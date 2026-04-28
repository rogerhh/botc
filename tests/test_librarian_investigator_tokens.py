"""Librarian and Investigator setup picks (seen-Outsider / seen-Minion).

The UI picks a random in-pool Outsider / Minion at game setup, rendered
as the librarian_outsider / investigator_minion reminder token. When
the engine starts the first night, the absorbed pick lets the
character ability skip its SelectCharacterPrompt — the storyteller
only confirms the WRONG player.
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


# ---------------------------------------------------------------------------
# Pool-side: auto-fill on add, validation on set.
# ---------------------------------------------------------------------------


def test_pool_autofills_librarian_outsider_when_added() -> None:
    """Adding the Librarian to a pool with at least one Outsider
    auto-picks an Outsider to be the seen role."""
    pool = CharacterPool()
    for n in ["Empath", "Soldier", "Drunk", "Poisoner", "Imp"]:
        pool.add(n)
    assert pool.librarian_outsider() is None  # no Librarian yet

    pool.add("Librarian")
    seen = pool.librarian_outsider()
    assert seen == "Drunk"  # only Outsider in this pool
    assert seen in pool.list()


def test_pool_no_autofill_when_no_outsiders() -> None:
    """If there are no Outsiders in the pool, the Librarian's seen
    slot stays None — that's the rules-correct '0 Outsiders' state."""
    pool = CharacterPool()
    for n in ["Empath", "Soldier", "Investigator", "Imp"]:
        pool.add(n)
    pool.add("Librarian")
    assert pool.librarian_outsider() is None


def test_pool_autofills_investigator_minion_when_added() -> None:
    pool = CharacterPool()
    for n in ["Empath", "Soldier", "Poisoner", "Imp"]:
        pool.add(n)
    assert pool.investigator_minion() is None

    pool.add("Investigator")
    seen = pool.investigator_minion()
    assert seen == "Poisoner"
    assert seen in pool.list()


def test_pool_set_librarian_outsider_validates_type() -> None:
    pool = CharacterPool()
    pool.set_many(["Librarian", "Empath", "Drunk", "Poisoner", "Imp"])

    # Setting to a Townsfolk role is rejected.
    try:
        pool.set_librarian_outsider("Empath")
    except ValueError as exc:
        assert "Outsider" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-Outsider pick")

    # Setting to None clears.
    assert pool.set_librarian_outsider(None) is None
    assert pool.librarian_outsider() is None


def test_pool_set_investigator_minion_validates_type() -> None:
    pool = CharacterPool()
    pool.set_many(["Investigator", "Empath", "Drunk", "Poisoner", "Imp"])

    # Setting to a Demon is rejected.
    try:
        pool.set_investigator_minion("Imp")
    except ValueError as exc:
        assert "Minion" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-Minion pick")


def test_pool_remove_marked_role_rerolls() -> None:
    pool = CharacterPool()
    pool.set_many(["Librarian", "Investigator",
                   "Drunk", "Saint",
                   "Poisoner", "Spy",
                   "Imp"])
    initial_lib = pool.librarian_outsider()
    initial_inv = pool.investigator_minion()
    assert initial_lib in ("Drunk", "Saint")
    assert initial_inv in ("Poisoner", "Spy")

    # Remove the chosen Outsider — the slot should refill with the
    # remaining Outsider.
    pool.remove(initial_lib)
    refilled_lib = pool.librarian_outsider()
    assert refilled_lib is not None
    assert refilled_lib != initial_lib

    # Same for the chosen Minion.
    pool.remove(initial_inv)
    refilled_inv = pool.investigator_minion()
    assert refilled_inv is not None
    assert refilled_inv != initial_inv


# ---------------------------------------------------------------------------
# Engine-side: apply_setup_data sets _chosen_outsider / _chosen_minion.
# ---------------------------------------------------------------------------


def _make_engine() -> Tuple[Engine, dict]:
    """5-player game with Librarian + Investigator + Drunk + Poisoner + Imp."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Librarian
    b = e.add_seat("Bob")      # 2 — Investigator
    c = e.add_seat("Cara")     # 3 — Drunk (Outsider, Lib seen role)
    d = e.add_seat("Dan")      # 4 — Poisoner (Minion, Inv seen role)
    f = e.add_seat("Eve")      # 5 — Imp
    e.assign_character(a.id, "Librarian")
    e.assign_character(b.id, "Investigator")
    e.assign_character(c.id, "Drunk")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    return e, {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "f": f.id}


def test_apply_setup_data_pre_populates_lib_inv() -> None:
    e, ids = _make_engine()
    e.apply_setup_data({
        "librarian_outsider": "Drunk",
        "investigator_minion": "Poisoner",
        # The Drunk also needs a fake Townsfolk (otherwise its
        # setup_ability prompts), but that's tested elsewhere — we
        # provide one here so this test focuses on Librarian /
        # Investigator.
        "drunk_fake": "Empath",
    })
    alice = e.get_player(ids["a"])
    bob = e.get_player(ids["b"])
    assert alice.character._chosen_outsider == "Drunk"
    assert bob.character._chosen_minion == "Poisoner"

    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT
    e.start_night()
    drain(e, [
        # Poisoner first (order 10). Poison self.
        ({"character": "Poisoner",     "step": "select_player"}, ids["d"]),
        # Librarian (order 31): seen-Outsider pre-set → no
        # SelectCharacterPrompt. ST only picks WRONG player. Eligibles
        # exclude Alice (self) and Cara (right player).
        ({"character": "Librarian",    "step": "select_wrong_player",
          "shown_character": "Drunk"}, ids["b"]),
        ({"character": "Librarian",    "step": "information"},   None),
        # Investigator (order 32): seen-Minion pre-set → no
        # SelectCharacterPrompt. ST only picks WRONG player. Eligibles
        # exclude Bob (self) and Dan (right player).
        ({"character": "Investigator", "step": "select_wrong_player",
          "shown_character": "Poisoner"}, ids["c"]),
        ({"character": "Investigator", "step": "information"},   None),
        # Drunk-as-Empath wakes at the Empath slot. Drunk → wrong
        # info. ST sends "0".
        ({"character": "Empath",       "step": "select_count",
          "due_to_drunk_poison": True}, "0"),
        ({"character": "Empath",       "step": "information"},   None),
    ])
    e.advance_to_day()
    assert e.phase is Phase.DAY


def test_apply_setup_data_with_wrong_skips_st_prompts() -> None:
    """When the Librarian's and Investigator's *both* seen and WRONG
    slots are pre-set (and the character is sober + healthy), the
    first-night ability skips every storyteller prompt."""
    e, ids = _make_engine()
    e.apply_setup_data({
        "librarian_outsider": "Drunk",
        "librarian_wrong": "Investigator",
        "investigator_minion": "Poisoner",
        "investigator_wrong": "Librarian",
        "drunk_fake": "Empath",
    })
    alice = e.get_player(ids["a"])
    bob = e.get_player(ids["b"])
    assert alice.character._chosen_outsider == "Drunk"
    assert alice.character._chosen_wrong == "Investigator"
    assert bob.character._chosen_minion == "Poisoner"
    assert bob.character._chosen_wrong == "Librarian"

    e.start_game()
    e.start_night()
    drain(e, [
        ({"character": "Poisoner",     "step": "select_player"}, ids["d"]),
        # Librarian: NO select_character, NO select_wrong_player —
        # both pre-set. Just information.
        ({"character": "Librarian",    "step": "information"},   None),
        # Investigator: same.
        ({"character": "Investigator", "step": "information"},   None),
        # Drunk-as-Empath wakes drunk → still emits select_count.
        ({"character": "Empath",       "step": "select_count",
          "due_to_drunk_poison": True}, "0"),
        ({"character": "Empath",       "step": "information"},   None),
    ])
    e.advance_to_day()


def test_lib_inv_token_slots_cleared_after_ability() -> None:
    """Once the Librarian / Investigator first-night abilities
    resolve, their pool slots are cleared. Token display always
    matches state, so the UI naturally stops rendering the OUTSIDER /
    MINION / WRONG tokens — there is no separate phase-based
    "first-night only" gate.
    """
    e, ids = _make_engine()
    # Mirror the real ST flow: pool tracks the seated roles + the
    # Librarian / Investigator picks the storyteller dragged on at
    # setup. The engine clears these slots when each ability resolves.
    e.pool.set_many([
        "Librarian", "Investigator", "Drunk", "Poisoner", "Imp"
    ])
    e.pool.set_librarian_outsider("Drunk")
    e.pool.set_librarian_wrong("Investigator")
    e.pool.set_investigator_minion("Poisoner")
    e.pool.set_investigator_wrong("Librarian")
    e.apply_setup_data({
        "librarian_outsider": "Drunk",
        "librarian_wrong": "Investigator",
        "investigator_minion": "Poisoner",
        "investigator_wrong": "Librarian",
        "drunk_fake": "Empath",
    })
    # Pool slots populated during setup.
    assert e.pool.librarian_outsider() == "Drunk"
    assert e.pool.librarian_wrong() == "Investigator"
    assert e.pool.investigator_minion() == "Poisoner"
    assert e.pool.investigator_wrong() == "Librarian"

    e.start_game()
    e.start_night()
    drain(e, [
        ({"character": "Poisoner",     "step": "select_player"}, ids["d"]),
        ({"character": "Librarian",    "step": "information"},   None),
        ({"character": "Investigator", "step": "information"},   None),
        ({"character": "Empath",       "step": "select_count",
          "due_to_drunk_poison": True}, "0"),
        ({"character": "Empath",       "step": "information"},   None),
    ])

    # Both abilities have fired — pool slots are cleared and the
    # grimoire stops showing the tokens automatically.
    assert e.pool.librarian_outsider() is None
    assert e.pool.librarian_wrong() is None
    assert e.pool.investigator_minion() is None
    assert e.pool.investigator_wrong() is None

    e.advance_to_day()


def test_pool_autofills_lib_wrong_and_inv_wrong() -> None:
    """Adding the Librarian / Investigator to a pool with valid
    seen-role + at least one other role auto-fills the WRONG slot
    too — distinct from the seen-role and from self."""
    pool = CharacterPool()
    pool.set_many(["Librarian", "Investigator", "Drunk", "Empath",
                   "Soldier", "Poisoner", "Spy", "Imp"])
    lib_seen = pool.librarian_outsider()
    lib_wrong = pool.librarian_wrong()
    inv_seen = pool.investigator_minion()
    inv_wrong = pool.investigator_wrong()
    assert lib_seen == "Drunk"
    assert lib_wrong is not None
    assert lib_wrong != "Librarian"
    assert lib_wrong != lib_seen
    assert inv_seen in ("Poisoner", "Spy")
    assert inv_wrong is not None
    assert inv_wrong != "Investigator"
    assert inv_wrong != inv_seen


def test_pool_lib_wrong_clears_when_no_outsider_in_play() -> None:
    """If there are no Outsiders in the pool, the Librarian's WRONG
    slot stays None — same '0 Outsiders' rules-correct skip as the
    seen-Outsider slot."""
    pool = CharacterPool()
    pool.set_many(["Librarian", "Empath", "Soldier", "Poisoner", "Imp"])
    assert pool.librarian_outsider() is None
    assert pool.librarian_wrong() is None


if __name__ == "__main__":
    test_pool_autofills_librarian_outsider_when_added()
    print("test 1 passed.")
    test_pool_no_autofill_when_no_outsiders()
    print("test 2 passed.")
    test_pool_autofills_investigator_minion_when_added()
    print("test 3 passed.")
    test_pool_set_librarian_outsider_validates_type()
    print("test 4 passed.")
    test_pool_set_investigator_minion_validates_type()
    print("test 5 passed.")
    test_pool_remove_marked_role_rerolls()
    print("test 6 passed.")
    test_apply_setup_data_pre_populates_lib_inv()
    print("test 7 passed.")
    test_apply_setup_data_with_wrong_skips_st_prompts()
    print("test 8 passed.")
    test_pool_autofills_lib_wrong_and_inv_wrong()
    print("test 9 passed.")
    test_pool_lib_wrong_clears_when_no_outsider_in_play()
    print("test 10 passed.")
    print("All librarian/investigator-token tests passed.")
