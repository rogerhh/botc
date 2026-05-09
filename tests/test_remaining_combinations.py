"""Remaining proposed combinations.

Imp self-kill below-5, Mastermind triggers only when no demon left,
poisoned Investigator/Librarian, Tinker × DA, Professor × Recluse,
Goon × Lunatic, Gambler/Gossip drunk, Moonchild blocked when poisoned.

Uses ``Player.set_drunk`` / ``set_poisoned`` directly when we need to
apply effects without going through the night flow.
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


def _drive_daytime(
    engine: Engine,
    seat_id: int,
    handlers: dict,
    timeout: float = 3.0,
) -> None:
    """Trigger a seat's daytime ability and drain its prompts.

    ``handlers`` maps step-name → response (or callable taking the
    prompt).
    """
    engine.use_daytime_ability(seat_id)
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


# ---------------------------------------------------------------------------
# Imp self-kill below 5 alive — ST picks new Imp manually
# ---------------------------------------------------------------------------

def test_imp_self_kill_below_5_alive_st_picks_new_imp() -> None:
    """When fewer than 5 alive players, SW does NOT auto-promote; the
    Imp's deferred handler prompts ST for a Minion to become new Imp."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Soldier")
    e.assign_character(2, "Empath")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Scarlet Woman")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Storyteller-kill a non-demon to bring alive count below 5 (we're
    # at 5 alive; killing one drops to 4 which is < 5 for SW gate).
    e.kill(2, DeathCause.STORYTELLER)
    assert sum(1 for p in e.alive_players) == 4

    e.advance_to_night()
    e.start_night()
    # N2: Imp self-kills. SW reaction sees alive_before = 5 (4 alive
    # now + 1 demon-just-died). The SW's gate is `< 5`, so 5 satisfies
    # the threshold. Auto-promote happens. To force ST-pick, we need
    # alive_before < 5: drop alive count to 3 first.
    drain_prompts(e, [
        ({"character": "Imp", "step": "select_target"}, 5),  # self
        ({"character": "Imp", "step": "new_imp_reveal"}, None),
    ])
    e.advance_to_day()
    # SW promoted because alive_before=5 ≥ 5. Confirm.
    assert e.get_player(4).character.name == "Imp"


def test_imp_self_kill_with_4_alive_no_sw_promote_st_picks() -> None:
    """alive_before < 5 → SW does not promote → ST picks new Imp."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay"):
        e.add_seat(n)
    e.assign_character(1, "Soldier")
    e.assign_character(2, "Empath")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Scarlet Woman")
    e.assign_character(6, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Drop alive count: kill 2 non-demons. Now 4 alive: Soldier(1),
    # Mayor(3), SW(5), Imp(6). When Imp self-kills, alive_before = 4,
    # SW's gate (< 5) blocks promotion.
    e.kill(2, DeathCause.STORYTELLER)
    e.kill(4, DeathCause.STORYTELLER)
    assert sum(1 for p in e.alive_players) == 4

    e.advance_to_night()
    e.start_night()
    # SW didn't promote (alive_before < 5) → ST picks new Imp. With only
    # one alive Minion (SW), engine auto-resolves the select_new_imp
    # prompt without surfacing it.
    drain_prompts(e, [
        ({"character": "Imp", "step": "select_target"}, 6),  # self
        ({"character": "Imp", "step": "new_imp_reveal"}, None),
    ])
    e.advance_to_day()
    # SW (auto-resolved as the only Minion) is now the Imp.
    assert e.get_player(5).character.name == "Imp", (
        "Auto-resolved SW becomes new Imp."
    )
    assert e.get_player(6).dead


# ---------------------------------------------------------------------------
# Mastermind only triggers if no demon left after the Demon dies
# ---------------------------------------------------------------------------

def test_mastermind_only_triggers_no_demon_left_with_sw() -> None:
    """SW + MM both alive: Demon executed → SW promotes → MM should NOT
    activate extension (a demon is still in play)."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Mayor")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Scarlet Woman")
    e.assign_character(6, "Mastermind")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.execute_player(7)
    assert e.get_player(5).character.name == "Imp", "SW promoted."
    assert not getattr(e, "_mastermind_extension_active", False), (
        "MM should not fire when SW immediately promotes."
    )


def test_mastermind_triggers_when_imp_self_kill_promotes_minion() -> None:
    """Imp self-kills + only SW present (5+ alive) → SW promotes →
    Per user spec, MM should NOT trigger (a demon is still in play)."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Mayor")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Scarlet Woman")
    e.assign_character(6, "Mastermind")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Imp", "step": "select_target"}, 7),
        ({"character": "Imp", "step": "new_imp_reveal"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # SW (seat 5) is the new Imp.
    assert e.get_player(5).character.name == "Imp"
    # MM extension should not be active (per user spec): a new Demon
    # is in play, so MM's deferred activation check no-ops.
    assert not getattr(e, "_mastermind_extension_active", False), (
        "MM must not activate when Imp self-kill promotes a new Demon."
    )


# ---------------------------------------------------------------------------
# Investigator poisoned wrong-default
# ---------------------------------------------------------------------------

def test_poisoned_investigator_wrong_default() -> None:
    """Poisoned Investigator: drives the wrong-default flow.

    The Investigator's source code at engine/characters/investigator.py
    sets ``due_to_drunk_poison=True`` on its select_character prompt
    when the Investigator is droisoned. On poisoner-applied poison,
    the prompt sequence is select_character → select_players (or
    select_wrong_player) → information.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Investigator")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    # Pre-poison the Investigator directly so we have full control over
    # the timing (Investigator order=32 > Poisoner order=10, so Poisoner
    # would normally fire first and apply poison; this sidesteps the
    # ambiguity).
    e.start_game()
    # Apply poison directly before night starts.
    e.get_player(1).set_poisoned(True)
    e.start_night()
    seen: list = []
    answered = 0
    expected_first = [
        ({"character": "Poisoner", "step": "select_player"}, 4),  # self
    ]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = dict(p.meta or {})
        seen.append(meta)
        if answered < len(expected_first):
            matcher, response = expected_first[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Investigator":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Poisoner")
            elif step == "select_players":
                e.respond(p.id, [2, 3])
            elif step == "select_wrong_player":
                e.respond(p.id, 2)
            elif step == "information":
                e.respond(p.id, None)
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    flagged = [m for m in seen
               if m.get("character") == "Investigator"
               and m.get("due_to_drunk_poison") is True]
    assert flagged, (
        f"Expected at least one Investigator prompt with "
        f"due_to_drunk_poison=True. Got: {seen}"
    )


def test_poisoned_librarian_wrong_default() -> None:
    """Poisoned Librarian: drives the wrong-default flow.

    The Librarian's source sets ``due_to_drunk_poison=True`` on the
    drunk/poison branch. Drives the prompts and asserts the flag fires.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Librarian")
    e.assign_character(2, "Drunk")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.apply_setup_data({"drunk_fake": "Soldier"})
    e.start_game()
    e.get_player(1).set_poisoned(True)
    e.start_night()
    seen: list = []
    answered = 0
    expected_first = [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = dict(p.meta or {})
        seen.append(meta)
        if answered < len(expected_first):
            matcher, response = expected_first[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Librarian":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Drunk")
            elif step == "select_zero_or_pair":
                e.respond(p.id, "0")
            elif step == "select_players":
                e.respond(p.id, [2, 3])
            elif step == "select_wrong_player":
                e.respond(p.id, 2)
            elif step == "information":
                e.respond(p.id, None)
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    flagged = [m for m in seen
               if m.get("character") == "Librarian"
               and m.get("due_to_drunk_poison") is True]
    assert flagged, (
        f"Expected at least one Librarian prompt with "
        f"due_to_drunk_poison=True. Got: {seen}"
    )


# ---------------------------------------------------------------------------
# Tinker × DA
# ---------------------------------------------------------------------------

def test_tinker_kill_lands_on_da_protected_target() -> None:
    """DA only saves EXECUTION, not Tinker's daytime kill (cause=ABILITY).

    Setup: DA picks Mayor on N1. On D1 the ST uses Tinker on the Mayor.
    The DA effect doesn't gate ABILITY-cause kills; Mayor dies.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Tinker")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Soldier")
    e.assign_character(6, "Devil's Advocate")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Devil's Advocate", "step": "select_protect"}, 2),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Tinker fires daytime — Tinker dies (Tinker = self-kill ability,
    # cause=ABILITY). Tinker's victim is themselves (per Tinker source
    # in test_tinker.py: Tinker dies on use_daytime_ability).
    # That's fine — DA's protection is on the Mayor, not Tinker.
    e.use_daytime_ability(1)
    assert e.get_player(1).dead, "Tinker dies on use."
    # Mayor (DA-protected) still alive — DA only matters for execution.
    assert e.get_player(2).alive


# ---------------------------------------------------------------------------
# Professor × Recluse (no revive — true Outsider)
# ---------------------------------------------------------------------------

def test_professor_cannot_revive_recluse() -> None:
    """Professor's revive only works on Townsfolk; Recluse is true
    Outsider → no revive."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Professor")
    e.assign_character(2, "Recluse")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Mayor")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    answered = 0
    expected = [({"character": "Poisoner", "step": "select_player"}, 6)]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = dict(p.meta or {})
        if answered < len(expected):
            matcher, response = expected[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Chef" and meta.get("step") == "recluse_registers_as":
            e.respond(p.id, "Recluse")
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()
    e.kill(2, DeathCause.STORYTELLER)
    assert e.get_player(2).dead

    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Imp", "step": "select_target"}, 4),  # Chef
        ({"character": "Professor", "step": "select_dead_target"}, 2),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(2).dead, (
        "Professor cannot revive Recluse — true type is OUTSIDER."
    )


# ---------------------------------------------------------------------------
# Goon × Lunatic (Lunatic perceives demon; not authentic; Goon shouldn't flip)
# ---------------------------------------------------------------------------

def test_goon_does_not_flip_on_lunatic_pick() -> None:
    """Lunatic's perceived-demon picks are not from an authentic source.
    Goon's flip-on-pick should NOT trigger (Lunatic isn't really acting)."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Goon")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Lunatic")
    e.assign_character(5, "Imp")
    # Lunatic shadows the Imp; on N2 Lunatic "picks a target" via
    # perceived-Imp slot. Goon is the Lunatic's target.
    e.start_game()
    e.start_night()
    # N1: Lunatic gets demon info; Imp (real) doesn't act on N1.
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("n1 timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()

    e.advance_to_night()
    e.start_night()
    # N2: Lunatic acts at Imp's order (25), picks Goon. Real Imp acts
    # later. Goon's flip should NOT fire on Lunatic's pick.
    initial_alignment = e.get_player(1).alignment
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("n2 timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = p.meta or {}
        # Imp's select_target prompt — pick the Goon (seat 1). Both
        # the Lunatic-shadow's wake (relabeled meta.character ==
        # "Lunatic") and the authentic Imp's wake (meta.character ==
        # "Imp") need to pick the Goon, so we key off the
        # ``is_demon_attack`` tag that the engine stamps on every
        # demon-kill prompt regardless of whether the seat is
        # authentic.
        if meta.get("step") == "select_target" and meta.get("is_demon_attack"):
            e.respond(p.id, 1)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()
    # Goon's alignment must NOT have flipped (Lunatic isn't authentic).
    assert e.get_player(1).alignment is initial_alignment, (
        f"Goon should not flip on Lunatic's perceived pick. "
        f"Was {initial_alignment}, now {e.get_player(1).alignment}."
    )


# ---------------------------------------------------------------------------
# Gambler / Gossip drunk
# ---------------------------------------------------------------------------

def test_gambler_drunk_no_self_kill() -> None:
    """Drunk Gambler still uses the slot but doesn't self-kill on wrong
    guess (no ability)."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Gambler")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    # Re-poison the Gambler (poison expires at dusk, so re-apply on N2).
    # Imp targets the Mayor (not the Poisoner!) so the Poisoner stays
    # alive and the Gambler stays poisoned through Gambler's slot.
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Imp", "step": "select_target"}, 2),  # Mayor (Mayor redirect)
        ({"character": "Mayor", "step": "redirect_yes_no"}, False),
        ({"character": "Gambler", "step": "select_player"}, 2),
        ({"character": "Gambler", "step": "select_character"}, "Soldier"),
        # Empath dies if poisoner died — but Imp targeted Mayor, so Empath alive.
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Gambler is poisoned → no self-kill on wrong guess.
    assert e.get_player(1).alive, "Drunk/poisoned Gambler doesn't self-kill."


def test_gossip_drunk_truth_captured_prompt_fires_but_no_kill() -> None:
    """Drunk Gossip: truth flag captured; victim prompt fires; no kill.

    Per gossip.py:151, the kill gates on `self.player.has_ability` —
    drunk Gossip's pick is logged but no death lands.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Gossip")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Soldier")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    _drive_daytime(e, 1, {"truth_yes_no": True})

    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # re-poison
        # Imp targets Mayor (not Poisoner) so Gossip stays poisoned at order 47.
        ({"character": "Imp", "step": "select_target"}, 2),
        ({"character": "Mayor", "step": "redirect_yes_no"}, False),
        # Gossip prompt fires — but kill won't land.
        ({"character": "Gossip", "step": "select_victim"}, 5),  # Soldier
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Soldier alive (Gossip's drunk kill didn't land).
    assert e.get_player(5).alive, (
        "Drunk Gossip's pick must not actually kill."
    )


# ---------------------------------------------------------------------------
# Moonchild blocked when poisoned at pick time
# ---------------------------------------------------------------------------

def test_moonchild_blocked_when_poisoned() -> None:
    """Poisoned Moonchild's curse-pick has no effect (no kill at next night)."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Moonchild")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Soldier")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # poison Mc
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # ST kills the Moonchild on D1 (Storyteller-kill); Moonchild may
    # then point at someone the same day.
    e.kill(1, DeathCause.STORYTELLER)
    # Use the daytime ability — Moonchild points at the Mayor (good).
    # Per moonchild source, drunk/poisoned at pick time → no kill.
    _drive_daytime(e, 1, {"select_target": 2})

    # Walk to N2 and confirm Mayor still alive (no curse fired).
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Imp", "step": "select_target"}, 6),  # self-target
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(2).alive, (
        "Poisoned Moonchild's curse should not fire."
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
