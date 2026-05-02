"""Drunk-as-Sage regression tests.

When a seat is the Drunk and has picked the Sage as the Townsfolk
they think they are, killing them at night by a Demon's ability must:

* Wake the perceived Sage just like a real Sage.
* Run the Sage's drunk/poisoned branch — i.e. the Storyteller is
  prompted with a 2-player select (``select_players``), pre-filled
  with a random *wrong* default (two non-Demon, non-Sage players),
  and the prompt's ``meta`` carries ``due_to_drunk_poison=True`` and
  a non-empty ``drunk_poison_state`` so the UI can append "(drunk)"
  to the prompt title.

Pre-fix this regressed: the Drunk's permanent ``Player.drunk`` flag
was getting cleared by ``Engine.resolve_droison_state`` the moment
the Drunk's seat died (the resolver flat-deactivated the
``DrunkSelfDrunkEffect`` because its source was dead), so the Sage's
ability saw a non-drunk seat and ran its sober 1-player branch
instead. Fix: ``DrunkSelfDrunkEffect`` opts out of the resolver's
source-death deactivation via ``survives_source_death = True``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine


def _make_drunk_as_sage_game() -> Engine:
    """5-seat game where Bob is the Drunk thinking he's the Sage."""
    e = Engine()
    e.add_seat("Alice")    # id 1 — Empath
    e.add_seat("Bob")      # id 2 — Drunk-as-Sage
    e.add_seat("Cara")     # id 3 — Soldier
    e.add_seat("Dan")      # id 4 — Poisoner
    e.add_seat("Eve")      # id 5 — Imp

    e.assign_character(1, "Empath")
    e.assign_character(2, "Drunk")
    e.assign_character(3, "Soldier")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")

    # Wire the Drunk's fake to "Sage" the way the UI's setup data
    # would. Skip the pool plumbing for testing and directly seed
    # ``members`` plus the perceived-character-name tag the engine
    # consults; ``Drunk.acting_perceived_character`` returns this
    # instance from then on.
    drunk = e.get_player(2).character
    sage_inst = e.build_character("Sage")
    drunk.members.clear()
    drunk.members.append(sage_inst)
    e.get_player(2).perceived_character_name = "Sage"
    return e


def _drain(
    e: Engine,
    *,
    timeout: float = 5.0,
    capture: dict | None = None,
    poisoner_target: int = 3,
    imp_target: int = 2,
) -> None:
    """Drive the night thread, capturing the Sage select prompt meta.

    Auto-responds to:
      * Poisoner ``select_player`` with ``poisoner_target``.
      * Imp ``select_target`` with ``imp_target``.
      * Sage ``select_other_player`` (sober branch — buggy) with
        the engine-provided default.
      * Sage ``select_players`` (drunk branch — fixed) with the
        engine-provided default pair.
      * Any ``information`` prompt with ``None``.
    Other prompts are responded to with ``None``.
    """
    deadline = time.time() + timeout
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"timeout — pending="
                f"{e.pending_prompt() and e.pending_prompt().meta}"
            )
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        meta = p.meta or {}
        char = meta.get("character")
        step = meta.get("step")
        if char == "Sage" and step in {"select_other_player", "select_players"}:
            if capture is not None:
                capture.setdefault("step", step)
                capture.setdefault("meta", dict(meta))
                capture.setdefault("text", p.text)
        if char == "Poisoner" and step == "select_player":
            e.respond(p.id, poisoner_target)
        elif char == "Imp" and step == "select_target":
            e.respond(p.id, imp_target)
        elif step == "select_other_player":
            elig = meta.get("eligible_player_ids") or []
            other = (meta.get("default")
                     if meta.get("default") is not None
                     else (elig[0] if elig else None))
            e.respond(p.id, other)
        elif step == "select_players":
            default = meta.get("default") or []
            e.respond(p.id, list(default))
        elif step == "information":
            e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.01)


def test_drunk_as_sage_killed_runs_drunk_branch() -> None:
    """Drunk-as-Sage killed by the Imp on night 2 → drunk branch fires."""
    e = _make_drunk_as_sage_game()
    e.start_game()

    bob = e.get_player(2)
    assert bob.drunk is True, "Drunk seat should be drunk after assignment."

    # Night 1 — Poisoner wastes pick on Cara, Empath info, no kill yet.
    e.start_night()
    _drain(e)
    e.advance_to_day()

    # Bob is still drunk (alive Drunk).
    assert bob.drunk is True

    # Night 2 — Imp kills the Drunk-as-Sage.
    e.advance_to_night()
    e.start_night()
    capture: dict = {}
    _drain(e, capture=capture)
    e.advance_to_day()

    assert bob.dead, "Bob should have been killed by the Imp."

    # **Bug-pre-fix would clear ``bob.drunk`` here.** With the fix the
    # ``DrunkSelfDrunkEffect`` survives source death so the seat stays
    # marked drunk for the rest of the game.
    assert bob.drunk is True, (
        "Drunk seat must remain drunk after death so impersonated "
        "abilities run their drunk/poisoned branch."
    )

    # The Sage's ability fired and took the drunk branch — 2-player
    # select_players, not 1-player select_other_player.
    assert capture, (
        "No Sage select prompt observed — the perceived Sage's "
        "death-trigger ability didn't fire at all."
    )
    assert capture.get("step") == "select_players", (
        f"Expected the drunk branch (`select_players`, 2-player "
        f"prompt) but got step={capture.get('step')!r} "
        f"text={capture.get('text')!r}"
    )

    meta = capture["meta"]
    assert meta.get("due_to_drunk_poison") is True, meta
    assert meta.get("drunk_poison_state") in {
        "drunk", "drunk and poisoned"
    }, meta

    # Wrong default is two non-Demon, non-Sage players.
    default = meta.get("default") or []
    assert isinstance(default, list) and len(default) == 2, default
    imp_id = 5
    sage_seat_id = 2
    assert imp_id not in default, default
    assert sage_seat_id not in default, default
    # Correct (Demon) pids surfaced for ST reference.
    correct = meta.get("correct") or []
    assert imp_id in correct, correct


if __name__ == "__main__":
    test_drunk_as_sage_killed_runs_drunk_branch()
    print("OK")
