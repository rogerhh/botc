"""Integration tests for the full BMR character roster.

Covers the second batch of BMR characters (everyone beyond the
initial six implemented in test_bmr_new_characters.py): Grandmother,
Exorcist, Gambler, Gossip, Professor, Minstrel, Goon, Lunatic,
Tinker, Moonchild, Godfather, Devil's Advocate, Assassin,
Mastermind, Zombuul, Pukka, Shabaloth, Po.

The test pattern is the same as ``tests/test_new_characters.py``: a
worker thread runs the night phase while the test thread polls
``pending_prompt`` and posts ``respond``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Alignment, CharType, DeathCause, Phase


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
# Grandmother
# ---------------------------------------------------------------------------

def test_grandmother_dies_when_grandchild_demon_killed() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Grandmother
    b = e.add_seat("Bob")      # 2 — Soldier (good)
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Grandmother")
    e.assign_character(b.id, "Soldier")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    # Pick the grandchild during setup — Cara (Mayor). This mirrors the
    # ST dragging the GRANDCHILD token onto Cara's chair before
    # clicking Start Game; the engine resolves the role to Cara's seat
    # and the first-night ability runs with no select_grandchild prompt.
    e.pool.set_many(["Grandmother", "Soldier", "Mayor", "Poisoner", "Imp"])
    e.pool.set_grandmother_grandchild("Mayor")
    e.apply_setup_data({"grandmother_grandchild": "Mayor"})
    assert e.get_player(a.id).character._grandchild_id == c.id

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",    "step": "select_player"}, 4),
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Imp",      "step": "select_target"}, 3),  # kill Cara (grandchild)
        ({"character": "Mayor",    "step": "redirect_yes_no"}, False),
    ])
    e.advance_to_day()
    assert e.get_player(3).dead, "Cara (grandchild) should be dead."
    assert e.get_player(1).dead, "Grandmother dies with grandchild."


def test_drunk_grandmother_survives_grandchild_demon_death_and_logs() -> None:
    """A drunk/poisoned Grandmother whose grandchild is demon-killed
    does NOT die — and the suppressed-reaction is logged on the
    storyteller console so the interaction is observable."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Grandmother
    b = e.add_seat("Bob")      # 2 — Soldier
    c = e.add_seat("Cara")     # 3 — Mayor (the grandchild)
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Grandmother")
    e.assign_character(b.id, "Soldier")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.pool.set_many(["Grandmother", "Soldier", "Mayor", "Poisoner", "Imp"])
    e.pool.set_grandmother_grandchild("Mayor")
    e.apply_setup_data({"grandmother_grandchild": "Mayor"})
    assert e.get_player(a.id).character._grandchild_id == c.id

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner poisons himself on night 1 (no real effect for the
        # Grandmother — her first-night info still reaches her, the
        # interaction we care about lives on night 2).
        ({"character": "Poisoner",    "step": "select_player"}, 4),
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        # Night 2: Poisoner poisons the Grandmother (Alice, seat 1).
        ({"character": "Poisoner", "step": "select_player"}, 1),
        # Imp kills Cara (the grandchild, seat 3).
        ({"character": "Imp",      "step": "select_target"}, 3),
        # Mayor declines to redirect.
        ({"character": "Mayor",    "step": "redirect_yes_no"}, False),
    ])
    e.advance_to_day()
    assert e.get_player(3).dead, "Cara (grandchild) should be dead."
    assert e.get_player(1).alive, (
        "Drunk/poisoned Grandmother must NOT die when the grandchild is "
        "demon-killed — her ability is suppressed."
    )

    # The interaction must be visible in the console log.
    suppressions = [
        entry for entry in e.console
        if entry.get("kind") == "reaction"
        and entry.get("details", {}).get("character") == "Grandmother"
        and entry.get("details", {}).get("suppressed") is True
    ]
    grandmother_entries = [
        entry for entry in e.console
        if entry.get("details", {}).get("character") == "Grandmother"
    ]
    assert suppressions, (
        f"Expected a Grandmother suppressed-reaction console entry; "
        f"got {grandmother_entries!r}"
    )
    summary = suppressions[-1].get("summary", "")
    assert "does NOT die" in summary, (
        f"Expected 'does NOT die' in the console summary; got {summary!r}"
    )
    assert "poisoned" in summary or "drunk" in summary, (
        f"Expected the drunk/poisoned state in the console summary; "
        f"got {summary!r}"
    )


# ---------------------------------------------------------------------------
# Exorcist (block demon kill)
# ---------------------------------------------------------------------------

def test_exorcist_blocks_imp_attack() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Exorcist
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Exorcist")
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
        # Exorcist picks Eve (Imp) — blocks the Imp tonight.
        ({"character": "Exorcist", "step": "select_player"}, 5),
        # Demon-reveal info prompt for the Imp.
        ({"character": "Exorcist", "step": "demon_reveal"}, None),
    ])
    deaths = e.advance_to_day()
    assert all(p.alive for p in (e.get_player(2), e.get_player(3))), (
        "Imp was blocked by the Exorcist — no kill should land."
    )
    assert not deaths, "No night deaths expected."


# ---------------------------------------------------------------------------
# Gambler
# ---------------------------------------------------------------------------

def test_gambler_correct_guess_lives() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Gambler
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Gambler")
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
        ({"character": "Imp",      "step": "select_target"}, 3),       # Cara
        ({"character": "Gambler",  "step": "select_player"}, 2),       # Bob
        ({"character": "Gambler",  "step": "select_character"}, "Mayor"),  # correct
    ])
    e.advance_to_day()
    assert e.get_player(1).alive, "Correct guess — Gambler lives."


def test_gambler_wrong_guess_dies() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Gambler
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Gambler")
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
        ({"character": "Imp",      "step": "select_target"}, 3),       # Cara
        ({"character": "Gambler",  "step": "select_player"}, 2),       # Bob
        ({"character": "Gambler",  "step": "select_character"}, "Imp"),  # wrong
    ])
    e.advance_to_day()
    assert e.get_player(1).dead, "Wrong guess — Gambler dies."


# ---------------------------------------------------------------------------
# Devil's Advocate (cancellation on execution)
# ---------------------------------------------------------------------------

def test_devils_advocate_protects_executed_player() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Devil's Advocate
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Imp (evil)
    f = e.add_seat("Eve")      # 5 — Empath

    e.assign_character(a.id, "Devil's Advocate")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Imp")
    e.assign_character(f.id, "Empath")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Devil's Advocate first night — pick Bob.
        ({"character": "Devil's Advocate", "step": "select_protect"}, 2),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.execute_player(2)  # try to kill Bob
    assert e.get_player(2).alive, "Bob saved by Devil's Advocate."


# ---------------------------------------------------------------------------
# Assassin (force-kill)
# ---------------------------------------------------------------------------

def test_assassin_force_kills_through_protection() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier (immune to demon)
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Empath
    d = e.add_seat("Dan")      # 4 — Assassin (evil)
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Empath")
    e.assign_character(d.id, "Assassin")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        # Imp acts first (order 25) — kill Bob (Mayor); decline redirect.
        ({"character": "Imp",      "step": "select_target"}, 2),
        ({"character": "Mayor",    "step": "redirect_yes_no"}, False),
        # Assassin (order 43) force-kills Soldier, bypassing protection.
        ({"character": "Assassin", "step": "select_target"}, 1),
        ({"character": "Empath",   "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(1).dead, "Assassin force-kills through Soldier protection."


# ---------------------------------------------------------------------------
# Pukka
# ---------------------------------------------------------------------------

def test_pukka_poisons_then_kills_next_night() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Mayor
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Pukka

    e.assign_character(a.id, "Mayor")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Pukka")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),  # poison self
        ({"character": "Pukka",    "step": "select_target"}, 1),  # poison Mayor
        # Empath info (Mayor poisoned now → could affect — but reads alignments).
        ({"character": "Empath",   "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(1).poisoned, "Mayor poisoned by Pukka."
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Pukka kills Mayor (previously poisoned), then poisons Bob.
        ({"character": "Pukka",    "step": "select_target"}, 2),  # Bob
        # Empath is now poisoned (Pukka just poisoned them) — drunk
        # info path requires a Storyteller pre-pick.
        ({"character": "Empath",   "step": "select_count"},   "0"),
        ({"character": "Empath",   "step": "information"},    None),
    ])
    e.advance_to_day()
    assert e.get_player(1).dead, "Mayor dies from Pukka's previous poison."
    assert e.get_player(2).poisoned, "Bob is now poisoned by Pukka."


# ---------------------------------------------------------------------------
# Po
# ---------------------------------------------------------------------------

def test_po_charges_then_kills_three() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Mayor
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Po

    e.assign_character(a.id, "Mayor")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Po")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),  # poison self
        ({"character": "Empath",   "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Po skips (decline_id = 0).
        ({"character": "Po",       "step": "select_target_or_skip"}, 0),
        ({"character": "Empath",   "step": "information"}, None),
    ])
    e.advance_to_day()
    assert all(p.alive for p in e.players if p.id != 4 or p.alive), (
        "No deaths expected after Po skips (Poisoner is alive too)."
    )
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Charged: Po picks 3 — Mayor, Empath, Soldier.
        ({"character": "Po", "step": "select_targets_charged"}, [1, 2, 3]),
        # Mayor declines redirect.
        ({"character": "Mayor", "step": "redirect_yes_no"}, False),
        # No Empath info — Empath (Bob) was just killed by Po.
    ])
    e.advance_to_day()
    assert e.get_player(2).dead, "Bob dies from Po's charged attack."
    # Mayor declined redirect; she should be dead.
    assert e.get_player(1).dead, "Mayor dies from Po."
    # Soldier: immune to Demon kill.
    assert e.get_player(3).alive, "Soldier immune to Po's demon kill."


# ---------------------------------------------------------------------------
# Tinker (manual ST kill)
# ---------------------------------------------------------------------------

def test_tinker_storyteller_kill() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Tinker
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Tinker")
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
    # Storyteller kills the Tinker via the standard mutator.
    e.kill(1, DeathCause.ABILITY)
    assert e.get_player(1).dead, "Tinker dies on Storyteller's whim."


# ---------------------------------------------------------------------------
# Mastermind (extension day)
# ---------------------------------------------------------------------------

def test_mastermind_extension_evil_wins_when_good_executed() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Mayor
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Mastermind (evil)
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Mayor")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Mastermind")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Execute the Imp — Mastermind extension kicks in, game continues.
    e.execute_player(5)
    assert e.get_player(5).dead, "Imp executed."
    assert e.winner is None, "Game must not have ended (Mastermind extension)."
    assert e.pending_winner is None, "No pending win during extension."
    # Next day: execute a good player → evil wins.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        # Imp is dead, doesn't act. Empath still acts.
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.execute_player(1)  # execute Mayor (good)
    # Pending win should now be evil. Finalize at next dawn.
    assert e.pending_winner is Alignment.EVIL, (
        "Evil pending win after good execution."
    )
    e.advance_to_night()
    e.start_night()
    if e._night_thread:
        e._night_thread.join(timeout=2.0)
    e.advance_to_day()
    assert e.winner is Alignment.EVIL, "Evil wins via Mastermind extension."


# ---------------------------------------------------------------------------
# Goon (drunkens first ability target, alignment flip)
# ---------------------------------------------------------------------------

def test_goon_drunkens_first_targeter_and_flips_alignment() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Goon
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner (evil)
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Goon")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    # Poisoner picks Goon — Goon flips to evil, Poisoner becomes drunk.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
    ])
    e.advance_to_day()
    assert e.get_player(1).alignment is Alignment.EVIL, (
        "Goon flipped to evil after being targeted by Poisoner."
    )
    assert e.get_player(4).drunk, "Poisoner drunkened by Goon's retort."
