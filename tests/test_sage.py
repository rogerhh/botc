"""Integration tests for the Sage townsfolk.

The Sage's ability fires only when they die at night to a *Demon's*
ability:

    "If the Demon kills you, you learn that 1 of 2 players is the Demon."

We exercise:

* Sage killed directly by the Imp on a non-first night → the Sage
  wakes, the Storyteller picks the demon-containing pair, the Sage is
  shown the info on their phone.
* Mayor-redirected demon kill that lands on the Sage → still demon-
  attributed, Sage triggers normally.
* Sage killed by execution → never triggers.
* Sage drunk via the storyteller (proxy for Sweetheart drunk) when
  killed at night → still wakes; engine pre-fills two non-Demon
  players as the wrong default per CLAUDE.md.
* No-Demon-kill night → no Sage prompt at all.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import CharType, DeathCause, Phase


def _drain(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 5.0,
) -> None:
    deadline = time.time() + timeout
    answered = 0
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night thread didn't finish; "
                f"answered={answered}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected extra prompt: meta={p.meta} text={p.text!r}"
            )
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"Prompt #{answered+1} did not match: "
                    f"expected meta[{k!r}]={v!r}, got meta={p.meta}, "
                    f"text={p.text!r}"
                )
        engine.respond(p.id, response)
        answered += 1
        time.sleep(0.01)
    if answered != len(scripted):
        raise AssertionError(
            f"Night ended with {answered} answered, expected {len(scripted)}."
        )


def _make_game(*, sage_seat: str = "Bob", with_mayor: bool = False) -> Engine:
    """Build a 5-player game where Bob is the Sage by default."""
    e = Engine()
    alice = e.add_seat("Alice")    # id 1 — Empath
    bob   = e.add_seat("Bob")      # id 2 — Sage
    cara  = e.add_seat("Cara")     # id 3 — Soldier or Mayor
    dan   = e.add_seat("Dan")      # id 4 — Poisoner
    eve   = e.add_seat("Eve")      # id 5 — Imp

    e.assign_character(alice.id, "Empath")
    e.assign_character(bob.id, "Sage")
    e.assign_character(cara.id, "Mayor" if with_mayor else "Soldier")
    e.assign_character(dan.id, "Poisoner")
    e.assign_character(eve.id, "Imp")
    return e


# ----------------------------------------------------------------------
# Demon kills the Sage on night 2 → Sage triggers.
# ----------------------------------------------------------------------


def test_sage_demon_kill_triggers_wake_and_info() -> None:
    e = _make_game()
    e.start_game()

    # First night — Poisoner wastes their pick on Cara (not the Sage),
    # Empath is sober so the engine computes the count automatically.
    e.start_night()
    _drain(e, [
        ({"character": "Poisoner", "step": "select_player"}, 3),  # Cara
        # Empath sober + healthy: just an info prompt.
        ({"character": "Empath",   "step": "information"},  None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    assert e.phase is Phase.NIGHT

    # Night 2: Imp kills the Sage. Sage triggers at order 30.
    e.start_night()
    _drain(e, [
        # Poisoner — leave the Sage sober.
        ({"character": "Poisoner", "step": "select_player"}, 3),
        # Imp kills the Sage (Bob, id 2).
        ({"character": "Imp",      "step": "select_target"}, 2),
        # Sober Sage wakes; the Demon (Eve) is fixed by the engine —
        # the Storyteller only picks the *other* player (Dan, id 4).
        ({"character": "Sage",     "step": "select_other_player"}, 4),
        # Info ack.
        ({"character": "Sage",     "step": "information"},   None),
        # Empath wakes after — sober, info auto-computes.
        ({"character": "Empath",   "step": "information"},   None),
    ])

    sage = e.get_player(2)
    assert sage.dead
    assert sage.death_cause is DeathCause.DEMON_KILL
    assert sage.character is not None
    # The trigger-spent flag should now be set.
    assert sage.character._triggered is True
    assert sage.character._died_to_demon is True


# ----------------------------------------------------------------------
# Mayor-redirected demon kill — Sage still triggers.
# ----------------------------------------------------------------------


def test_sage_triggers_on_mayor_redirected_demon_kill() -> None:
    e = _make_game(with_mayor=True)
    e.start_game()

    e.start_night()
    _drain(e, [
        # Poisoner targets Alice (Empath) — leaves Mayor + Sage sober.
        ({"character": "Poisoner", "step": "select_player"}, 1),
        # Empath is poisoned — engine pre-fills wrong default; ST sends.
        ({"character": "Empath",   "step": "select_count"}, 0),
        ({"character": "Empath",   "step": "information"},  None),
    ])
    e.advance_to_day()
    e.advance_to_night()

    e.start_night()
    # Imp picks Mayor (Cara, id 3); Mayor redirects death onto the Sage
    # (Bob, id 2). Source=Imp is preserved through the redirect, so the
    # Sage still sees a demon-attributed death and arms.
    _drain(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # poison Alice
        ({"character": "Imp",      "step": "select_target"}, 3),  # kill Mayor
        # Mayor redirect prompts (no character field collision — the
        # Mayor's redirect prompts include ``character: "Mayor"``).
        ({"character": "Mayor", "step": "redirect_yes_no"},   True),
        ({"character": "Mayor", "step": "redirect_select"},   2),  # → Sage
        # Sage now wakes — sober Sage, ST picks only the *other*
        # (non-Demon) player.
        ({"character": "Sage",  "step": "select_other_player"}, 4),
        ({"character": "Sage",  "step": "information"},      None),
        # Empath (Alice) is poisoned this night → ST picks the count.
        ({"character": "Empath","step": "select_count"},     0),
        ({"character": "Empath","step": "information"},      None),
    ])

    sage = e.get_player(2)
    assert sage.dead
    assert sage.death_cause is DeathCause.DEMON_KILL
    assert sage.character is not None
    assert sage.character._triggered is True


# ----------------------------------------------------------------------
# Execution: Sage does NOT trigger.
# ----------------------------------------------------------------------


def test_sage_execution_does_not_trigger() -> None:
    e = _make_game()
    e.start_game()
    e.start_night()
    _drain(e, [
        ({"character": "Poisoner", "step": "select_player"}, 3),
        ({"character": "Empath",   "step": "information"},  None),
    ])
    e.advance_to_day()

    # Execute the Sage during day 1.
    sage = e.get_player(2)
    e.execute_player(sage.id)
    assert sage.dead
    assert sage.death_cause is DeathCause.EXECUTION
    # Trigger flag must remain False — execution is not a demon kill.
    assert sage.character is not None
    assert sage.character._died_to_demon is False
    assert sage.character._triggered is False


# ----------------------------------------------------------------------
# Drunk Sage still wakes; the engine pre-fills 2 non-Demon defaults.
# ----------------------------------------------------------------------


def test_sage_drunk_wakes_with_wrong_default() -> None:
    e = _make_game()
    e.start_game()
    e.start_night()
    _drain(e, [
        ({"character": "Poisoner", "step": "select_player"}, 3),
        ({"character": "Empath",   "step": "information"},  None),
    ])
    e.advance_to_day()

    # Make the Sage drunk via the storyteller (proxy for Sweetheart).
    sage = e.get_player(2)
    sage.set_drunk(True)
    assert sage.has_ability is False

    e.advance_to_night()
    e.start_night()

    # Capture the Sage's pending prompt to verify the default & meta.
    captured: dict = {}

    def script_with_capture(p_meta: dict) -> bool:
        if (
            p_meta.get("character") == "Sage"
            and p_meta.get("step") == "select_players"
        ):
            captured.update(p_meta)
            return True
        return False

    # We script the prompts but also capture the Sage's select prompt
    # meta to inspect its defaults.
    deadline = time.time() + 5.0
    scripted = [
        ({"character": "Poisoner", "step": "select_player"}, 3),
        ({"character": "Imp",      "step": "select_target"}, 2),  # kill Sage
        # Drunk Sage: send the engine-provided default by responding
        # with whatever's in meta["default"]. We'll fill this in below.
        ({"character": "Sage",     "step": "select_players"}, "_DEFAULT_"),
        ({"character": "Sage",     "step": "information"},   None),
        ({"character": "Empath",   "step": "information"},   None),
    ]
    answered = 0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("drunk-sage drain timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        if answered >= len(scripted):
            raise AssertionError(f"unexpected extra prompt: {p.meta}")
        matcher, response = scripted[answered]
        ok = all(p.meta.get(k) == v for k, v in matcher.items())
        if not ok:
            raise AssertionError(f"prompt mismatch: {p.meta}")
        if response == "_DEFAULT_":
            # This is the Sage select_players prompt — capture it.
            captured.update(p.meta)
            response = list(p.meta.get("default") or [])
        e.respond(p.id, response)
        answered += 1
        time.sleep(0.01)
    assert answered == len(scripted)

    # Verify drunk-poison metadata is correctly attached.
    assert captured.get("due_to_drunk_poison") is True
    assert captured.get("drunk_poison_state") in {"drunk", "drunk and poisoned"}
    # Default is a 2-player wrong pair — neither player is the Imp.
    default_pair = captured.get("default") or []
    imp_id = 5
    assert imp_id not in default_pair
    # Correct pids should include the Imp seat for ST reference.
    correct = captured.get("correct") or []
    assert imp_id in correct

    sage = e.get_player(2)
    assert sage.dead
    assert sage.character._triggered is True


# ----------------------------------------------------------------------
# Non-demon-kill night: no Sage prompt fires.
# ----------------------------------------------------------------------


def test_sage_no_prompt_when_not_demon_killed() -> None:
    e = _make_game()
    e.start_game()
    e.start_night()
    _drain(e, [
        ({"character": "Poisoner", "step": "select_player"}, 3),
        ({"character": "Empath",   "step": "information"},  None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()

    # Imp doesn't kill the Sage — picks Dan instead. Sage should not
    # wake at all this night.
    _drain(e, [
        ({"character": "Poisoner", "step": "select_player"}, 3),
        ({"character": "Imp",      "step": "select_target"}, 4),  # Dan
        ({"character": "Empath",   "step": "information"},  None),
    ])

    sage = e.get_player(2)
    assert sage.alive
    assert sage.character._died_to_demon is False
    assert sage.character._triggered is False


if __name__ == "__main__":
    test_sage_demon_kill_triggers_wake_and_info()
    test_sage_triggers_on_mayor_redirected_demon_kill()
    test_sage_execution_does_not_trigger()
    test_sage_drunk_wakes_with_wrong_default()
    test_sage_no_prompt_when_not_demon_killed()
    print("All sage tests passed.")
