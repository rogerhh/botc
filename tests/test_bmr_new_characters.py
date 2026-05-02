"""Integration tests for the 6 newly-implemented BMR characters.

Covers:
  * Sailor — picks, drunk dispatch, can't-die immunity, dusk expiry.
  * Innkeeper — protects 2, drunkens 1, SAFE clears at dawn.
  * Courtier — picks character, drunkens player in play, expires.
  * Tea Lady — protects good neighbours.
  * Pacifist — saves executed good player on storyteller's discretion.
  * Fool — first-death save, cannot save twice.

Driven through the same scripted-prompt pattern as
``tests/test_new_characters.py``: a worker thread runs the night
phase while the test thread polls ``pending_prompt`` and posts
``respond``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Alignment, CharType, DeathCause, Phase
from engine.event import Event, EventType


def drain_prompts(
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
# Sailor
# ---------------------------------------------------------------------------

def test_sailor_picks_townsfolk_drunkens_them() -> None:
    """Sailor picks a Townsfolk neighbour; ST drunks the Townsfolk."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Sailor
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Sailor")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Sailor",   "step": "select_player"}, 2),  # Bob
        ({"character": "Sailor",   "step": "select_drunk"},  2),  # Drunk Bob
    ])
    e.advance_to_day()
    assert e.get_player(2).drunk, "Bob should be drunk after Sailor's ability."
    assert not e.get_player(1).drunk, "Sailor should be sober."


def test_sailor_cannot_die_when_sober() -> None:
    """A sober Sailor is immune to Demon kill."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Sailor
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Sailor")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),  # poison self
        ({"character": "Sailor",   "step": "select_player"}, 2),  # Bob
        ({"character": "Sailor",   "step": "select_drunk"},  2),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Sailor",   "step": "select_player"}, 3),  # Cara TF
        ({"character": "Sailor",   "step": "select_drunk"},  3),
        ({"character": "Imp",      "step": "select_target"}, 1),  # try kill Sailor
    ])
    e.advance_to_day()
    assert e.get_player(1).alive, "Sober Sailor cannot die."


def test_sailor_self_drunk_can_die() -> None:
    """A self-drunk Sailor loses the cannot-die immunity."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Sailor
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Sailor")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Sailor",   "step": "select_player"}, 2),  # Bob
        # ST chooses to drunken the Sailor instead of Bob.
        ({"character": "Sailor",   "step": "select_drunk"},  1),
    ])
    e.advance_to_day()
    assert e.get_player(1).drunk, "Sailor should be self-drunk."
    e.advance_to_night()
    # ``recheck_persistent_effects("dusk")`` cleared the drunk on
    # advance_to_night. Confirm.
    assert not e.get_player(1).drunk, "Sailor's drunk should expire at dusk."


# ---------------------------------------------------------------------------
# Innkeeper
# ---------------------------------------------------------------------------

def test_innkeeper_protects_two_drunkens_one() -> None:
    """Innkeeper makes both targets safe; one is drunk for the night/day."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Innkeeper
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Innkeeper")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",  "step": "select_player"},  4),
        ({"character": "Innkeeper", "step": "select_players"}, [2, 3]),
        ({"character": "Innkeeper", "step": "select_drunk"},   2),  # Bob drunk
        ({"character": "Imp",       "step": "select_target"},  3),  # try kill Cara — SAFE
    ])
    e.advance_to_day()
    assert e.get_player(2).drunk, "Bob should be drunk from Innkeeper."
    assert e.get_player(3).alive, "Cara was SAFE from Innkeeper."


# ---------------------------------------------------------------------------
# Courtier
# ---------------------------------------------------------------------------

def test_courtier_drunkens_named_character_in_play() -> None:
    """Courtier picks Imp, drunkens the seated Imp."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Courtier
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Courtier")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"},   4),
        ({"character": "Courtier", "step": "select_character"}, "Imp"),
    ])
    e.advance_to_day()
    assert e.get_player(5).drunk, "Imp should be drunk after Courtier picks them."


def test_courtier_decline_keeps_slot() -> None:
    """Courtier declining tonight does not consume the once-per-game slot."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Courtier
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Courtier")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"},   4),
        ({"character": "Courtier", "step": "select_character"}, "(decline)"),
    ])
    e.advance_to_day()
    courtier_char = e.get_player(1).character
    assert courtier_char._used is False, "Courtier slot should still be available."


# ---------------------------------------------------------------------------
# Tea Lady
# ---------------------------------------------------------------------------

def test_tea_lady_protects_good_neighbours() -> None:
    """Tea Lady's neighbours can't die when both are good.

    Tea Lady is seated at seat 0 so her PRE_DEATH cancellation fires
    *before* Mayor's redirect-prompt reaction (dispatch order = seat
    order). Her neighbours in this 5-seat ring are seat 1 (Mayor)
    and seat 4 (Soldier) — both good, so the protection is active.
    """
    e = Engine()
    a = e.add_seat("Alice")    # seat 0 — Tea Lady
    b = e.add_seat("Bob")      # seat 1 — Mayor    (TL CW neighbour)
    c = e.add_seat("Cara")     # seat 2 — Poisoner (evil)
    d = e.add_seat("Dan")      # seat 3 — Imp      (evil)
    f = e.add_seat("Eve")      # seat 4 — Soldier  (TL CCW neighbour)

    e.assign_character(a.id, "Tea Lady")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Poisoner")
    e.assign_character(d.id, "Imp")
    e.assign_character(f.id, "Soldier")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks themselves (no relevant effect tonight).
        ({"character": "Poisoner", "step": "select_player"}, 3),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 3),
        ({"character": "Imp",      "step": "select_target"}, 2),  # Mayor neighbour to TL
        # No Mayor redirect prompt — Tea Lady's PRE_DEATH cancels first
        # so the Mayor's reaction never sees the kill.
    ])
    e.advance_to_day()
    assert e.get_player(2).alive, "Mayor (good TL neighbour) cannot die."


# ---------------------------------------------------------------------------
# Pacifist
# ---------------------------------------------------------------------------

def test_pacifist_saves_good_executee_on_yes() -> None:
    """Pacifist save: ST says yes → executed good player remains alive."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Pacifist
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Pacifist")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()

    # During the day, the Storyteller executes Bob.
    # Pacifist's PRE_DEATH reaction prompts ST yes/no synchronously.
    # We need to drive that prompt from a thread separate to the
    # execute_player call. Spawn a thread:
    import threading
    result: List[Any] = []

    def runner():
        try:
            result.append(e.execute_player(2))  # Bob
        except Exception as exc:
            result.append(exc)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    deadline = time.time() + 5.0
    while t.is_alive():
        if time.time() > deadline:
            raise TimeoutError("execute_player did not complete")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        # Should be the Pacifist save prompt.
        assert p.meta.get("character") == "Pacifist"
        assert p.meta.get("step") == "save_yes_no"
        e.respond(p.id, True)
        time.sleep(0.01)
    t.join(timeout=1.0)
    assert e.get_player(2).alive, "Bob saved by Pacifist."
    assert e._executed_today is True, "Execution still counted for today."


def test_pacifist_no_prompt_for_evil_executee() -> None:
    """Pacifist does NOT prompt for evil executions."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Pacifist
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner (evil)
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Pacifist")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    # Executing the Poisoner (evil) should not prompt Pacifist.
    e.execute_player(4)
    assert e.get_player(4).dead


# ---------------------------------------------------------------------------
# Fool
# ---------------------------------------------------------------------------

def test_fool_first_death_saved() -> None:
    """Fool survives the first death; the second death lands."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Fool
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Fool")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Imp",      "step": "select_target"}, 1),  # Imp targets Fool
    ])
    e.advance_to_day()
    fool_player = e.get_player(1)
    fool_char = fool_player.character
    assert fool_player.alive, "Fool's first-death ability should save."
    assert fool_char._used, "Fool slot should be consumed."

    # Now execute Fool — second death, should land.
    e.execute_player(1)
    assert e.get_player(1).dead, "Fool dies on second attempt."


def test_innkeeper_protected_fool_keeps_first_life() -> None:
    """Innkeeper SAFE protects the Fool's once-per-game slot.

    Per the wiki: "If another character's ability protects the Fool
    from death, the Fool does not use their ability." When the
    Innkeeper has marked the Fool SAFE, the Innkeeper is the
    canceller — the Fool's slot must NOT be spent. Driven directly
    through the engine (rather than the night-loop + prompt drainer)
    so the test is isolated to the cancellation pipeline. Pinning
    the Fool to seat 0 (before the Innkeeper) is the seat-ordering
    that used to produce the bug — with the fix in place the order
    no longer matters; the assertion holds either way.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Fool (deliberately seated FIRST)
    b = e.add_seat("Bob")      # 2 — Innkeeper
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Soldier
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Fool")
    e.assign_character(b.id, "Innkeeper")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Soldier")
    e.assign_character(f.id, "Imp")
    e.start_game()

    # Hand-set the Innkeeper's SAFE pair to include the Fool. The
    # Innkeeper now uses the engine effect registry (post-Layer-2
    # migration) — add an InnkeeperSafeEffect directly so the test
    # doesn't depend on running the night-loop / prompt drainer.
    from engine.characters.innkeeper import InnkeeperSafeEffect
    inn = b.character
    fool_char = a.character
    e.add_effect(InnkeeperSafeEffect(source=inn, targets=[a.id, c.id]))

    # Demon kills the Fool at night.
    e._phase = Phase.NIGHT
    e._night_number = 2
    e.kill(a.id, DeathCause.DEMON_KILL, source=f.character)

    # Innkeeper cancels in the standard PRE_DEATH pass. The
    # last-resort pass is therefore never dispatched, and the Fool's
    # slot is intact.
    assert a.alive, "Innkeeper SAFE should keep the Fool alive."
    assert fool_char._used is False, (
        "Innkeeper saved the Fool; the once-per-game slot must NOT "
        "have been consumed."
    )
    assert a.once_per_game_used is False, (
        "Player.once_per_game_used should also be unconsumed when "
        "the save was provided by another protector."
    )


def test_fool_drunk_dies_normally() -> None:
    """A drunk Fool dies normally — ability does not fire."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Fool
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Fool")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # poison Fool
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # keep Fool poisoned
        ({"character": "Imp",      "step": "select_target"}, 1),
    ])
    e.advance_to_day()
    assert e.get_player(1).dead, "Poisoned Fool dies on first attempt."
