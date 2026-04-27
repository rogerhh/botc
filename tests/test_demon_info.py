"""Demon Info ST input stage 1.

Verifies the new prompt flow on the first night, 7+ players:

  1. The engine emits a ST input stage 1 (``stage="st_pre"``) prompt
     of type ``select_character`` with ``count=3`` carrying three
     pre-filled "not in play" good roles to bluff as.
  2. The Storyteller may swap any of the picks (or just hit Next).
  3. The engine then dispatches a WAKEUP event and emits the
     ``InformationPrompt`` (``stage="info"``, ``shown_to_player``)
     carrying the (possibly Storyteller-edited) bluffs.

This exercises the full "ST input stage 1 → wake up → show this to
player" flow that all six-section panels now follow.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import CharType, Phase
from engine import preset as preset_module


def _make_engine() -> Engine:
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)

    # 7-player Trouble Brewing setup so Demon Info actually fires
    # (the threshold is 7+ non-traveler players).
    a = e.add_seat("Alice")     # Washerwoman
    b = e.add_seat("Bob")       # Librarian
    c = e.add_seat("Cara")      # Investigator
    d = e.add_seat("Dan")       # Chef
    f = e.add_seat("Eve")       # Empath
    g = e.add_seat("Finn")      # Poisoner
    h = e.add_seat("Gail")      # Imp
    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Librarian")
    e.assign_character(c.id, "Investigator")
    e.assign_character(d.id, "Chef")
    e.assign_character(f.id, "Empath")
    e.assign_character(g.id, "Poisoner")
    e.assign_character(h.id, "Imp")
    return e


def _respond_and_settle(engine: Engine, prompt_id: int, response: Any,
                        timeout: float = 2.0) -> None:
    """Send a response and wait for the engine to consume it.

    Calling ``engine.respond`` only signals the night thread; the
    pending prompt isn't cleared until the night thread wakes up,
    reads the response, and (typically) blocks on the *next* prompt.
    Without an explicit wait, a follow-up call to
    ``engine.pending_prompt()`` can race and observe the *just-
    answered* prompt rather than the next one. This helper waits
    until the pending prompt id changes (or goes None) so the caller
    sees a clean handoff.
    """
    assert engine.respond(prompt_id, response), (
        f"engine.respond rejected prompt id {prompt_id}"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = engine.pending_prompt()
        if cur is None or cur.id != prompt_id:
            return
        time.sleep(0.005)
    raise TimeoutError(
        f"Engine did not move past prompt id {prompt_id} within {timeout}s"
    )


def _drain_until(
    engine: Engine,
    matcher: dict,
    timeout: float = 5.0,
) -> Any:
    """Answer prompts in sequence with ``None`` until one matches.

    Returns the matched prompt (without responding to it). Raises if
    the night thread dies before the prompt arrives.
    """
    deadline = time.time() + timeout
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Timed out waiting for prompt matching {matcher!r}; "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        # Check match.
        ok = True
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                ok = False
                break
        if ok:
            return p
        # Otherwise advance with a sensible default.
        if p.type == "select_player" or p.type == "select_players":
            eligible = list(p.eligible_player_ids or [])
            count = p.count or 1
            response: Any = (
                eligible[0] if count == 1 and eligible else eligible[:count]
            )
        elif p.type == "select_character":
            count = getattr(p, "count", 1) or 1
            default = (p.meta or {}).get("default")
            if count > 1:
                response = list(default) if isinstance(default, list) else (
                    list(p.eligible_characters)[:count]
                )
            else:
                response = (
                    default
                    if isinstance(default, str) and default
                    else (
                        p.eligible_characters[0]
                        if p.eligible_characters else None
                    )
                )
        elif p.type == "yes_no":
            response = bool((p.meta or {}).get("default", False))
        else:
            response = None
        # Use the settle helper so the pending-prompt observed on the
        # next loop iteration is genuinely the *next* prompt — without
        # this, the engine has only just been signalled and the same
        # prompt id can show up again.
        _respond_and_settle(engine, p.id, response)
    raise AssertionError(
        f"Night ended before a prompt matched {matcher!r}."
    )


def test_demon_info_emits_st_pre_then_info() -> None:
    e = _make_engine()
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    # Find the Demon (Imp) seat — the bluff-info prompt is targeted at
    # that player.
    demon = next(p for p in e.players if p.char_type is CharType.DEMON)

    e.start_night()

    # First: ST input stage 1 — the multi-character bluff selection.
    p1 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "st_pre",
        "character": "Demon",
    })
    assert p1.type == "select_character", (
        f"expected select_character, got {p1.type}"
    )
    assert getattr(p1, "count", 1) == 3, (
        f"expected count=3, got count={getattr(p1, 'count', 1)}"
    )
    assert p1.target_player_id == demon.id
    default = (p1.meta or {}).get("default")
    assert isinstance(default, list) and len(default) == 3, (
        f"expected 3 pre-filled bluffs, got default={default!r}"
    )
    # All three pre-filled bluffs should be non-in-play roles, i.e.
    # not held by any seated character.
    in_play = {
        pl.character.name for pl in e.players if pl.character is not None
    }
    for name in default:
        assert name not in in_play, (
            f"pre-filled bluff {name!r} is actually in play"
        )

    # Storyteller hits Next without changing the picks: respond with
    # the default list.
    _respond_and_settle(e, p1.id, list(default))

    # Next: the InformationPrompt (stage="info") with the chosen
    # bluffs. ``_drain_until`` advances past any other prompts (none
    # are expected between st_pre and info for the Demon).
    p2 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "info",
        "character": "Demon",
    })
    assert p2.type == "information"
    assert p2.shown_to_player is True
    assert p2.target_player_id == demon.id
    # The info prompt's bluff_characters meta should reflect the ST's
    # confirmed picks (which here equal the engine's defaults).
    assert list(p2.meta.get("bluff_characters", [])) == list(default)
    # Highlighted character tokens on the player's display match.
    assert list(p2.highlight_characters) == list(default)
    # Acknowledge the info to let the night unwind.
    _respond_and_settle(e, p2.id, None)


def test_demon_info_st_can_change_bluffs() -> None:
    """Storyteller swaps one of the pre-filled bluffs; the new pick
    flows through to the information prompt the Demon sees."""
    e = _make_engine()
    e.start_game()
    e.start_night()

    p1 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "st_pre",
        "character": "Demon",
    })
    default = list((p1.meta or {}).get("default") or [])
    assert len(default) == 3

    # Build a new triple by swapping one slot for a different
    # not-in-play eligible character.
    eligible = list(p1.eligible_characters)
    in_play = {
        pl.character.name for pl in e.players if pl.character is not None
    }
    swap_in = next(
        (c for c in eligible if c not in default and c not in in_play),
        None,
    )
    assert swap_in is not None, (
        "test setup expected at least 4 not-in-play roles available"
    )
    new_picks = [swap_in] + default[1:]
    _respond_and_settle(e, p1.id, new_picks)

    p2 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "info",
        "character": "Demon",
    })
    assert list(p2.meta.get("bluff_characters", [])) == new_picks
    assert list(p2.highlight_characters) == new_picks
    e.respond(p2.id, None)


if __name__ == "__main__":
    test_demon_info_emits_st_pre_then_info()
    print("test_demon_info_emits_st_pre_then_info passed.")
    test_demon_info_st_can_change_bluffs()
    print("test_demon_info_st_can_change_bluffs passed.")
