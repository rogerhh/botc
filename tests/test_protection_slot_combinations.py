"""Once-per-game slot preservation, day-mechanic combinations, and
Exorcist × each Demon.

Includes Pacifist saves, Devil's Advocate execution-protection,
Slayer × Spy/Recluse, Virgin × Drunk nominator, Fool slot preservation
when another protector saved them, Professor revive constraints, and
Exorcist's gate against each implemented Demon.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, List, Tuple

import pytest

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
                f"Night thread timeout; answered={answered}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected extra prompt: {p.text!r} meta={p.meta}"
            )
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"Prompt #{answered+1} mismatch: "
                    f"expected meta[{k!r}]={v!r}, got meta={p.meta}, "
                    f"text={p.text!r}"
                )
        engine.respond(p.id, response)
        answered += 1
        time.sleep(0.005)
    if answered != len(scripted):
        raise AssertionError(
            f"Night ended with {answered}/{len(scripted)} prompts answered."
        )


def execute_with_prompts(
    engine: Engine,
    seat_id: int,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 3.0,
) -> None:
    """Run execute_player in a worker thread, drain its prompts."""
    worker = threading.Thread(
        target=lambda: engine.execute_player(seat_id),
        daemon=True,
    )
    worker.start()
    deadline = time.time() + timeout
    answered = 0
    while worker.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"execute_player timeout; answered={answered}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected execute prompt: {p.text!r} meta={p.meta}"
            )
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"execute prompt #{answered+1} mismatch: "
                    f"expected meta[{k!r}]={v!r}, got meta={p.meta}, "
                    f"text={p.text!r}"
                )
        engine.respond(p.id, response)
        answered += 1
        time.sleep(0.005)
    worker.join(0.5)
    assert not worker.is_alive(), "execute_player didn't return"
    if answered != len(scripted):
        raise AssertionError(
            f"execute_player ended with {answered}/{len(scripted)} prompts."
        )


# ---------------------------------------------------------------------------
# Pacifist
# ---------------------------------------------------------------------------

def test_pacifist_saves_saint_no_evil_win() -> None:
    """Pacifist saves the Saint from execution; loss does NOT fire.

    Per engine/characters/pacifist.py:24, when Pacifist cancels the
    execution PRE_DEATH, the engine ALSO skips dispatching the
    EXECUTION event — so the Saint's reaction never observes the
    "executed" trigger and the loss condition does not register.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Saint")
    e.assign_character(2, "Pacifist")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Soldier")
    e.assign_character(5, "Empath")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    # Execute the Saint; Pacifist saves.
    execute_with_prompts(e, 1, [
        ({"character": "Pacifist", "step": "save_yes_no"}, True),
    ])
    assert e.get_player(1).alive, "Pacifist must save the Saint."
    assert e.pending_winner is None, (
        f"Saint not actually executed → no loss. "
        f"pending_winner={e.pending_winner!r}"
    )


def test_pacifist_decline_save_lets_saint_die() -> None:
    """Pacifist decline → Saint dies → loss triggers."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Saint")
    e.assign_character(2, "Pacifist")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Soldier")
    e.assign_character(5, "Empath")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    execute_with_prompts(e, 1, [
        ({"character": "Pacifist", "step": "save_yes_no"}, False),
    ])
    assert e.get_player(1).dead
    assert e.pending_winner is Alignment.EVIL


def test_pacifist_drunk_does_not_save() -> None:
    """Poisoned Pacifist: no save prompt, Saint dies normally."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Saint")
    e.assign_character(2, "Pacifist")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Soldier")
    e.assign_character(5, "Empath")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 2),  # poison Pac
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(2).poisoned

    # No Pacifist save prompt — execute proceeds.
    e.execute_player(1)
    assert e.get_player(1).dead
    assert e.pending_winner is Alignment.EVIL


def test_pacifist_does_not_save_evil_executed() -> None:
    """Pacifist only saves *good* executed players. No prompt for Imp."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Saint")
    e.assign_character(2, "Pacifist")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Soldier")
    e.assign_character(5, "Empath")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Execute the Imp — no Pacifist prompt should fire.
    e.execute_player(7)
    assert e.get_player(7).dead
    # Pacifist save prompt must not have fired.
    assert e.pending_prompt() is None, (
        f"No Pacifist prompt should fire on evil execution. "
        f"Got: {e.pending_prompt()}"
    )


# ---------------------------------------------------------------------------
# Devil's Advocate
# ---------------------------------------------------------------------------

def test_devils_advocate_protects_saint_from_execution() -> None:
    """DA protects Saint from execution; cancel fires; Saint loss not
    triggered."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Saint")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Soldier")
    e.assign_character(4, "Empath")
    e.assign_character(5, "Chef")
    e.assign_character(6, "Devil's Advocate")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Devil's Advocate", "step": "select_protect"}, 1),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.execute_player(1)
    assert e.get_player(1).alive, "DA-protected Saint should survive execution."
    assert e.pending_winner is None


def test_devils_advocate_cannot_pick_same_target_twice() -> None:
    """DA's eligibility list excludes the previous-night pick."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Mayor")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Saint")
    e.assign_character(6, "Devil's Advocate")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Devil's Advocate", "step": "select_protect"}, 1),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    e.advance_to_night()
    # Capture the DA's prompt eligibility on N2.
    e.start_night()
    captured = []
    deadline = time.time() + 3.0
    answered = 0
    expected = [
        ({"character": "Devil's Advocate", "step": "select_protect"}, 2),
        ({"character": "Imp", "step": "select_target"}, 4),
        ({"character": "Empath", "step": "information"}, None),
    ]
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if (p.meta.get("character") == "Devil's Advocate"
                and p.meta.get("step") == "select_protect"):
            captured.append(dict(p.meta))
        matcher, response = expected[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"#{answered+1} {matcher} vs {p.meta}"
                )
        e.respond(p.id, response)
        answered += 1
        time.sleep(0.005)
    e.advance_to_day()
    assert captured, "DA prompt should have fired on N2."
    da_prompt = captured[0]
    eligible = da_prompt.get("eligible_player_ids", [])
    assert 1 not in eligible, (
        f"Last-night pick (seat 1) must not be eligible. "
        f"eligible={eligible}"
    )


# ---------------------------------------------------------------------------
# Slayer
# ---------------------------------------------------------------------------

def _drive_daytime_ability(
    engine: Engine,
    seat_id: int,
    handlers: dict,
    timeout: float = 3.0,
) -> None:
    """Trigger seat's daytime ability and drain prompts using handlers.

    `handlers` maps step-name → response (or callable that takes the
    prompt and returns a response).
    """
    engine.use_daytime_ability(seat_id)
    # Engine spawns its own worker on _night_thread; wait/poll on it.
    deadline = time.time() + timeout
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("daytime ability timeout")
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        step = p.meta.get("step")
        if step in handlers:
            h = handlers[step]
            response = h(p) if callable(h) else h
        else:
            response = None
        engine.respond(p.id, response)
        time.sleep(0.005)


def test_slayer_drunk_no_kill() -> None:
    """Drunk Slayer → no kill, slot consumed."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Slayer")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Soldier")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
    ])
    e.advance_to_day()
    assert e.get_player(1).poisoned
    _drive_daytime_ability(e, 1, {"select_target": 5})
    assert e.get_player(5).alive, "Drunk Slayer cannot kill the Imp."
    slayer_char = e.get_player(1).character
    assert slayer_char._used is True, "Slot still consumes on drunk fire."


def test_slayer_on_spy_no_kill() -> None:
    """Spy registers as Spy (Minion) for Slayer's DEMON check; no kill."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Slayer")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Soldier")
    e.assign_character(4, "Spy")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    answered = 0
    expected = [({"character": "Spy", "step": "information"}, None)]
    deadline = time.time() + 3.0
    drained: list = []
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = dict(p.meta or {})
        drained.append(meta)
        # Defensive: respond to any prompts during N1 (Spy info varies).
        if meta.get("character") == "Poisoner":
            e.respond(p.id, 5)
        elif meta.get("character") == "Spy":
            e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()
    # Slayer shoots the Spy. Spy's DEMON check should fail (Spy doesn't
    # override registers_as for DEMON-only checks per spy.py).
    _drive_daytime_ability(e, 1, {"select_target": 4})
    assert e.get_player(4).alive, "Spy is not the Demon — no kill."


# ---------------------------------------------------------------------------
# Virgin
# ---------------------------------------------------------------------------

def test_virgin_drunk_nominator_outsider_does_not_fire() -> None:
    """Drunk impersonating a Townsfolk: true type still Outsider; Virgin
    should NOT fire (Drunk's true char_type is OUTSIDER)."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Virgin")
    e.assign_character(2, "Drunk")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.apply_setup_data({"drunk_fake": "Soldier"})
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()

    worker = threading.Thread(target=lambda: e.dispatch(Event(
        EventType.NOMINATION,
        targets=[e.get_player(1)],
        data={"nominator_id": 2},  # Drunk nominates Virgin
    )), daemon=True)
    worker.start()
    worker.join(2.0)
    assert not worker.is_alive(), "nomination dispatch hung"
    # Drunk's true type is OUTSIDER, so Virgin should not fire.
    assert not e.get_player(2).dead, (
        "Drunk nominator (true Outsider) shouldn't trigger Virgin execution."
    )


# ---------------------------------------------------------------------------
# Fool slot preservation when another protector saves
# ---------------------------------------------------------------------------

def test_fool_soldier_save_preserves_fool_slot() -> None:
    """Imp picks Fool-who-is-also-Soldier? Not possible. Test:
    Imp picks Soldier separately so Fool's last-resort doesn't fire.

    Setup: Imp targets Soldier (immune); Fool is alive, untouched.
    Verifies Fool's slot is NOT spent for non-Fool deaths.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Soldier")
    e.assign_character(2, "Fool")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
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
        ({"character": "Imp", "step": "select_target"}, 1),  # Soldier
    ])
    e.advance_to_day()
    fool = e.get_player(2)
    assert fool.alive
    assert fool.character._used is False, "Fool slot must not consume."


# ---------------------------------------------------------------------------
# Professor revive constraints
# ---------------------------------------------------------------------------

def test_professor_cannot_revive_drunk_outsider() -> None:
    """Professor's revive only works on Townsfolk. Drunk is true
    Outsider → no revive."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Professor")
    e.assign_character(2, "Drunk")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Mayor")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.apply_setup_data({"drunk_fake": "Soldier"})
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Storyteller-kill the Drunk so Professor can attempt revive on N2.
    e.kill(2, DeathCause.STORYTELLER)
    assert e.get_player(2).dead

    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Imp", "step": "select_target"}, 4),  # Chef (filler)
        ({"character": "Professor", "step": "select_dead_target"}, 2),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(2).dead, (
        "Drunk's true type is OUTSIDER — Professor cannot revive."
    )


# ---------------------------------------------------------------------------
# Exorcist × each Demon — gate must short-circuit the demon's ability
# ---------------------------------------------------------------------------

def test_exorcist_gates_imp() -> None:
    """Exorcist picks the Imp; Imp doesn't even prompt for a target."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Exorcist")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Soldier")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    e.advance_to_night()
    e.start_night()
    # Capture every prompt to verify Imp's select_target NEVER fires.
    seen: list = []
    answered = 0
    expected = [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Exorcist", "step": "select_player"}, 7),
        ({"character": "Exorcist", "step": "demon_reveal"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ]
    deadline = time.time() + 3.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        seen.append(dict(p.meta))
        matcher, response = expected[answered]
        for k, v in matcher.items():
            assert p.meta.get(k) == v, f"#{answered+1} {matcher} vs {p.meta}"
        e.respond(p.id, response)
        answered += 1
        time.sleep(0.005)
    deaths = e.advance_to_day()
    assert deaths == [], "Imp blocked → no deaths."
    # Positive assertion: Imp's select_target prompt was never emitted.
    imp_prompts = [m for m in seen
                   if m.get("character") == "Imp"
                   and m.get("step") == "select_target"]
    assert not imp_prompts, (
        f"Exorcist gate should suppress Imp's select_target. "
        f"Saw: {imp_prompts}"
    )


def test_exorcist_gates_pukka() -> None:
    """Exorcist picks the Pukka — Pukka doesn't pick a NEW target,
    but the previously-poisoned player still dies and stops being
    poisoned (BMR ruling, BOTC quiz BMR Intermediate Q6).
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Exorcist")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Soldier")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Pukka")
    e.start_game()
    e.start_night()
    # N1: Pukka acts (poisons Chef). Exorcist doesn't act on N1.
    answered = 0
    expected_first = [
        ({"character": "Poisoner", "step": "select_player"}, 6),
    ]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if answered < len(expected_first):
            matcher, response = expected_first[answered]
            for k, v in matcher.items():
                assert p.meta.get(k) == v, f"#{answered+1} {matcher} vs {p.meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        # Defensive responses for the rest of N1.
        meta = p.meta or {}
        if meta.get("character") == "Pukka":
            e.respond(p.id, 4)  # Pukka poisons Chef
        elif meta.get("character") == "Exorcist":
            e.respond(p.id, 7)
        elif meta.get("step") == "demon_reveal":
            e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()
    assert e.get_player(4).poisoned, "Chef poisoned by Pukka on N1."

    e.advance_to_night()
    e.start_night()
    seen: list = []
    expected = [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Exorcist", "step": "select_player"}, 7),
        ({"character": "Exorcist", "step": "demon_reveal"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ]
    answered = 0
    deadline = time.time() + 3.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        seen.append(dict(p.meta))
        matcher, response = expected[answered]
        for k, v in matcher.items():
            assert p.meta.get(k) == v, f"#{answered+1} {matcher} vs {p.meta}"
        e.respond(p.id, response)
        answered += 1
        time.sleep(0.005)
    deaths = e.advance_to_day()
    death_ids = [d.id for d in deaths]
    # Per BMR ruling: Exorcist-blocked Pukka still resolves its
    # previous-night poison. Chef (poisoned on N1) dies tonight and
    # is no longer poisoned.
    assert 4 in death_ids, (
        f"Exorcist-gated Pukka must STILL kill the previously-poisoned "
        f"player (Chef = id 4). deaths={death_ids}"
    )
    assert e.get_player(4).dead, "Chef should be dead after N2."
    assert not e.get_player(4).poisoned, (
        "Chef should no longer be poisoned after Pukka resolves the "
        "previous-night poison."
    )
    # Negative: Pukka's select_target never fired this night — the
    # Exorcist gate suppresses the *new pick* but not the prior-night
    # follow-through.
    pukka_prompts = [m for m in seen
                     if m.get("character") == "Pukka"
                     and m.get("step") == "select_target"]
    assert not pukka_prompts, (
        f"Exorcist gate must suppress Pukka's select_target. "
        f"Saw: {pukka_prompts}"
    )


def test_drunk_pukka_does_not_kill_previous_target_and_poison_persists() -> None:
    """Pukka becomes drunk on N2 — previously-poisoned target survives
    and remains poisoned. On N3 the Pukka is sober again and finally
    kills that target (BMR ruling, BOTC quiz BMR Intermediate Q25 —
    Minstrel-drunk Pukka case modeled here with the Poisoner).
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Mayor")
    e.assign_character(2, "Empath")
    e.assign_character(3, "Chef")
    e.assign_character(4, "Investigator")
    e.assign_character(5, "Soldier")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Pukka")
    e.start_game()

    # N1: Poisoner self-poisons (irrelevant). Pukka poisons Mayor.
    e.start_night()
    answered = 0
    expected_n1 = [
        ({"character": "Poisoner", "step": "select_player"}, 6),
    ]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if answered < len(expected_n1):
            matcher, response = expected_n1[answered]
            for k, v in matcher.items():
                assert p.meta.get(k) == v, f"#{answered+1} {matcher} vs {p.meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        meta = p.meta or {}
        if meta.get("character") == "Pukka":
            e.respond(p.id, 1)  # Pukka poisons Mayor
        elif meta.get("character") == "Investigator":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Poisoner")
            elif step == "select_wrong_player":
                e.respond(p.id, 3)
            elif step == "select_players":
                e.respond(p.id, [6, 3])
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()
    assert e.get_player(1).poisoned, "Mayor poisoned by Pukka on N1."

    # N2: Poisoner picks the Pukka -> Pukka is droisoned tonight. The
    # Pukka must NOT kill Mayor and must NOT clear Mayor's poison.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 7),  # poison Pukka
        # Drunk Pukka still goes through the motions (picks anyone) —
        # but no real effect lands.
        ({"character": "Pukka",    "step": "select_target"}, 3),  # pick Chef
        ({"character": "Empath",   "step": "information"}, None),
    ])
    deaths_n2 = e.advance_to_day()
    death_ids_n2 = [d.id for d in deaths_n2]
    assert 1 not in death_ids_n2, (
        f"Drunk Pukka must NOT kill the previously-poisoned Mayor. "
        f"deaths={death_ids_n2}"
    )
    assert e.get_player(1).alive, "Mayor must survive a drunk Pukka night."
    assert e.get_player(1).poisoned, (
        "Mayor must REMAIN poisoned across the Pukka's drunk night — "
        "PukkaPoisonEffect persists when source is droisoned."
    )
    # Chef was the drunk-Pukka's pick; no real effect should have
    # landed on Chef.
    assert not e.get_player(3).poisoned, (
        "Drunk Pukka's new pick (Chef) must not actually be poisoned."
    )

    # N3: Poisoner picks someone else. Pukka is sober again and now
    # follows through on Mayor's still-active poison: Mayor dies.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),  # self
        ({"character": "Pukka",    "step": "select_target"}, 5),  # Soldier
        ({"character": "Empath",   "step": "information"}, None),
    ])
    deaths_n3 = e.advance_to_day()
    death_ids_n3 = [d.id for d in deaths_n3]
    assert 1 in death_ids_n3, (
        f"Sober Pukka on N3 must finally kill the still-poisoned "
        f"Mayor. deaths={death_ids_n3}"
    )
    assert e.get_player(1).dead, "Mayor should be dead after N3."
    assert not e.get_player(1).poisoned, (
        "Mayor's poison should be cleared after the Pukka follows "
        "through on N3."
    )


if __name__ == "__main__":
    import sys as _sys
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    p = f = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            p += 1
        except BaseException as exc:
            print(f"FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
            f += 1
    print(f"\n{p} passed, {f} failed")
    _sys.exit(0 if f == 0 else 1)
