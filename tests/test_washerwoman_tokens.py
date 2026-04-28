"""Washerwoman TOWNSFOLK + WRONG token setup picks.

Exercises the new flow where *both* of the Washerwoman's reminder
tokens — TOWNSFOLK and WRONG — are pre-picked at game setup (the
storyteller places them in the UI by drag-drop). When both picks are
present and the WW is sober + healthy, the first-night ability runs
without any storyteller prompts: the engine has already resolved the
two players the WW will be pointed at, so it goes straight to the
information stage.

Also covers:

  * The UI's :class:`CharacterPool` auto-fills WRONG on add — never
    picks the WW herself or the same role as the seen-TF.
  * Removing the WRONG role re-rolls the slot.
  * Setting the seen-TF to the same role as the existing WRONG slot
    re-rolls WRONG so the two tokens never collide.
  * A test where only TOWNSFOLK is pre-picked still asks the
    storyteller for the WRONG player (the legacy fallback).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Phase
from ui.ui import CharacterPool


# ---------------------------------------------------------------------------
# Engine-side: apply_setup_data with both tokens => no ST prompts on WW.
# ---------------------------------------------------------------------------


def drain(engine: Engine, scripted: List[Tuple[dict, Any]],
          timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    answered = 0
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night didn't finish; answered={answered}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected extra prompt: {p.text!r} meta={p.meta}"
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


def _make_engine_with_ww() -> Tuple[Engine, dict]:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Empath  (WW seen-TF)
    c = e.add_seat("Cara")     # 3 — Soldier (WW WRONG)
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp
    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    return e, {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "f": f.id}


def test_both_tokens_preset_skips_ww_prompts() -> None:
    """When both TOWNSFOLK and WRONG are set during setup, the WW
    ability runs with no select_* prompts — only the information
    prompt fires."""
    e, ids = _make_engine_with_ww()

    e.apply_setup_data({
        "washerwoman_townsfolk": "Empath",   # seen role -> Bob
        "washerwoman_wrong": "Soldier",      # WRONG role -> Cara
    })

    alice = e.get_player(ids["a"])
    ww_char = alice.character
    assert ww_char._chosen_townsfolk == "Empath"
    assert ww_char._chosen_wrong == "Soldier"

    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT
    e.start_night()
    drain(e, [
        # Poisoner (order 10): poison self for simplicity.
        ({"character": "Poisoner",     "step": "select_player"}, ids["d"]),
        # Washerwoman: NO select_character prompt, NO select_wrong_player
        # prompt — both pre-set. Just the information prompt.
        ({"character": "Washerwoman",  "step": "information"},   None),
        # Empath wakes after the WW (sober + healthy → no ST count
        # prompt; just information).
        ({"character": "Empath",       "step": "information"},   None),
    ])
    e.advance_to_day()
    assert e.phase is Phase.DAY


def test_ww_token_slots_cleared_after_ability() -> None:
    """Once the Washerwoman's first-night ability resolves, the pool's
    seen-TF and WRONG slots are cleared. Token display always matches
    state, so the UI naturally stops rendering the TOWNSFOLK / WRONG
    tokens — there is no separate phase-based "first-night only" gate.
    """
    e, ids = _make_engine_with_ww()
    # Mirror the real ST flow: pool tracks the seated roles + the WW
    # picks the storyteller dragged on at setup. The engine clears
    # these slots when the ability resolves.
    e.pool.set_many(["Washerwoman", "Empath", "Soldier", "Poisoner", "Imp"])
    e.pool.set_washerwoman_townsfolk("Empath")
    e.pool.set_washerwoman_wrong("Soldier")
    e.apply_setup_data({
        "washerwoman_townsfolk": "Empath",
        "washerwoman_wrong": "Soldier",
    })
    # Pool slots populated during setup; tokens visible to the ST.
    assert e.pool.washerwoman_townsfolk() == "Empath"
    assert e.pool.washerwoman_wrong() == "Soldier"

    e.start_game()
    e.start_night()
    drain(e, [
        ({"character": "Poisoner",    "step": "select_player"}, ids["d"]),
        ({"character": "Washerwoman", "step": "information"},   None),
        ({"character": "Empath",      "step": "information"},   None),
    ])

    # Ability has fired; pool slots are now cleared so the grimoire
    # stops showing the tokens automatically.
    assert e.pool.washerwoman_townsfolk() is None
    assert e.pool.washerwoman_wrong() is None

    # Cached picks on the character itself remain — they are part of
    # the ability's audit trail, not a display source.
    alice = e.get_player(ids["a"])
    assert alice.character._chosen_townsfolk == "Empath"
    assert alice.character._chosen_wrong == "Soldier"

    e.advance_to_day()


def test_only_townsfolk_preset_still_asks_for_wrong() -> None:
    """Backwards-compatibility: if only the seen TF is pre-set (the
    UI's previous behavior), the engine still asks the storyteller for
    the WRONG player at night.
    """
    e, ids = _make_engine_with_ww()
    e.apply_setup_data({"washerwoman_townsfolk": "Empath"})
    alice = e.get_player(ids["a"])
    assert alice.character._chosen_townsfolk == "Empath"
    assert alice.character._chosen_wrong is None

    e.start_game()
    e.start_night()
    drain(e, [
        ({"character": "Poisoner",     "step": "select_player"}, ids["d"]),
        # Still prompts for the wrong player.
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Empath"}, ids["c"]),
        ({"character": "Washerwoman",  "step": "information"},   None),
        ({"character": "Empath",       "step": "information"},   None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# UI-side: CharacterPool auto-fills WW WRONG with valid candidates.
# ---------------------------------------------------------------------------


def test_pool_autofills_wrong_when_ww_added() -> None:
    """Adding the WW to a pool that already has other roles auto-picks
    a WRONG role that is neither the WW herself nor the seen-TF."""
    pool = CharacterPool()
    # Build a small pool, then add the Washerwoman.
    for n in ["Empath", "Soldier", "Poisoner", "Imp"]:
        pool.add(n)
    assert pool.washerwoman_townsfolk() is None  # no WW yet
    assert pool.washerwoman_wrong() is None

    pool.add("Washerwoman")
    seen = pool.washerwoman_townsfolk()
    wrong = pool.washerwoman_wrong()

    assert seen is not None
    assert wrong is not None
    assert wrong != "Washerwoman"
    assert wrong != seen
    assert wrong in pool.list()


def test_pool_wrong_rerolls_when_marked_role_leaves() -> None:
    """If the role currently carrying WRONG is removed, a fresh
    candidate is picked so the slot doesn't go stale."""
    pool = CharacterPool()
    pool.set_many(["Washerwoman", "Empath", "Soldier", "Poisoner", "Imp"])
    initial_wrong = pool.washerwoman_wrong()
    assert initial_wrong is not None

    # Remove the role carrying WRONG. The slot should refill (provided
    # there's still a non-WW, non-seen-TF candidate left in the pool).
    pool.remove(initial_wrong)
    refilled = pool.washerwoman_wrong()
    assert refilled is not None
    assert refilled != initial_wrong
    assert refilled != "Washerwoman"
    assert refilled != pool.washerwoman_townsfolk()


def test_pool_setting_seen_tf_to_wrong_role_rerolls_wrong() -> None:
    """If the storyteller drags the seen-TF token onto the role that
    currently carries WRONG, the pool rerolls WRONG so the two tokens
    don't collide."""
    pool = CharacterPool()
    pool.set_many(["Washerwoman", "Empath", "Investigator",
                   "Soldier", "Poisoner", "Imp"])
    # Force a known initial state.
    pool.set_washerwoman_townsfolk("Empath")
    pool.set_washerwoman_wrong("Soldier")
    assert pool.washerwoman_townsfolk() == "Empath"
    assert pool.washerwoman_wrong() == "Soldier"

    # Move seen-TF onto Soldier — but wait, Soldier isn't a Townsfolk?
    # It is in TB. So set_washerwoman_townsfolk("Soldier") is valid.
    pool.set_washerwoman_townsfolk("Soldier")
    # WRONG must no longer equal the new seen-TF.
    assert pool.washerwoman_townsfolk() == "Soldier"
    assert pool.washerwoman_wrong() != "Soldier"
    assert pool.washerwoman_wrong() != "Washerwoman"


def test_pool_set_wrong_validates_distinct_from_seen() -> None:
    """``set_washerwoman_wrong`` rejects the seen-TF role and the WW
    herself — these are nonsensical and the rules forbid them."""
    pool = CharacterPool()
    pool.set_many(["Washerwoman", "Empath", "Soldier", "Poisoner", "Imp"])
    pool.set_washerwoman_townsfolk("Empath")

    try:
        pool.set_washerwoman_wrong("Empath")
    except ValueError as exc:
        assert "differ from the seen Townsfolk" in str(exc)
    else:
        raise AssertionError("expected ValueError for WRONG=seen-TF")

    try:
        pool.set_washerwoman_wrong("Washerwoman")
    except ValueError as exc:
        assert "Washerwoman herself" in str(exc)
    else:
        raise AssertionError("expected ValueError for WRONG=Washerwoman")


if __name__ == "__main__":
    test_both_tokens_preset_skips_ww_prompts()
    print("test 1 passed.")
    test_only_townsfolk_preset_still_asks_for_wrong()
    print("test 2 passed.")
    test_pool_autofills_wrong_when_ww_added()
    print("test 3 passed.")
    test_pool_wrong_rerolls_when_marked_role_leaves()
    print("test 4 passed.")
    test_pool_setting_seen_tf_to_wrong_role_rerolls_wrong()
    print("test 5 passed.")
    test_pool_set_wrong_validates_distinct_from_seen()
    print("test 6 passed.")
    print("All washerwoman-token tests passed.")
