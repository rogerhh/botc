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
    # The Goon also wakes to be shown its new alignment per the wiki:
    # "wake the Goon, give them a thumbs-up or a thumbs-down."
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Goon", "step": "alignment_reveal"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(1).alignment is Alignment.EVIL, (
        "Goon flipped to evil after being targeted by Poisoner."
    )
    assert e.get_player(4).drunk, "Poisoner drunkened by Goon's retort."


def test_courtier_picks_goon_drunkens_courtier_no_effect_lands() -> None:
    """Courtier picks the Goon's character.

    Courtier picks a *role*, not a player seat. The engine resolves
    the chosen role to the seated Goon and routes that through
    ``Engine.notify_goon_chosen``, so:

      * The Goon's retort drunkens the Courtier *before* the
        Courtier's own ``CourtierDrunkEffect`` would land.
      * The Courtier's ``has_ability`` is False at effect-emit time,
        so the Goon never receives ``CourtierDrunkEffect`` —
        emits ``CourtierNoAbilityEffect`` on the Courtier's own seat
        instead.
      * Both seats are good (Courtier good, Goon starts good), so
        no alignment flip — but the slot is still spent.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Courtier
    b = e.add_seat("Bob")      # 2 — Goon
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Courtier")
    e.assign_character(b.id, "Goon")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    # Poisoner picks Imp (irrelevant target). Courtier picks "Goon"
    # — Goon's retort fires synchronously inside Courtier.ability,
    # drunkening the Courtier. Courtier's own DrunkEffect doesn't
    # land on the Goon.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 5),
        ({"character": "Courtier", "step": "select_character"}, "Goon"),
    ])
    e.advance_to_day()

    assert e.get_player(1).drunk, (
        "Courtier drunkened by Goon's retort after picking the Goon's role."
    )
    assert not e.get_player(2).drunk, (
        "Goon should NOT be drunkened — Courtier lost ability before "
        "the CourtierDrunkEffect could be emitted."
    )
    assert e.get_player(2).alignment is Alignment.GOOD, (
        "Goon stays good (Courtier is good, no flip needed)."
    )
    assert e.get_player(1).character._used, (
        "Courtier's once-per-game slot is consumed regardless."
    )


def test_poisoner_picks_goon_first_then_courtier_picks_goon_no_retort() -> None:
    """First-per-night gate: once the Goon has retorted on one
    targeter tonight, a *second* targeter (here the Courtier) gets
    no retort. The Courtier's normal drunkening lands on the Goon
    via ``CourtierDrunkEffect``.

    The Poisoner acts first (order 10) and picks the Goon — Goon
    flips evil, Poisoner becomes drunk, gate closes. Then the
    Courtier (order 15) picks "Goon" — notify_goon_chosen no-ops
    (gate closed), Courtier still has ability, the
    ``CourtierDrunkEffect`` is emitted onto the Goon's seat.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Courtier
    b = e.add_seat("Bob")      # 2 — Goon
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Courtier")
    e.assign_character(b.id, "Goon")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 2),  # picks Goon
        # Poisoner's pick flipped Goon to evil; the Goon wakes for
        # the alignment reveal. The later Courtier pick of "Goon"
        # finds the gate closed, so no second wake.
        ({"character": "Goon", "step": "alignment_reveal"}, None),
        ({"character": "Courtier", "step": "select_character"}, "Goon"),
    ])
    e.advance_to_day()

    # Goon's retort fired on the Poisoner only.
    assert e.get_player(4).drunk, (
        "Poisoner drunkened by Goon's retort (first targeter tonight)."
    )
    assert e.get_player(2).alignment is Alignment.EVIL, (
        "Goon flipped evil to match Poisoner."
    )
    # Courtier got no retort and the CourtierDrunkEffect lands on
    # the Goon's seat.
    assert not e.get_player(1).drunk, (
        "Courtier NOT drunkened — Goon's first-per-night gate had "
        "already closed when the Courtier picked them."
    )
    assert e.get_player(2).drunk, (
        "Goon picks up CourtierDrunkEffect (Courtier still has ability)."
    )


# ---------------------------------------------------------------------------
# Shabaloth regurgitation eligibility — sourced from DEAD effects, not
# from "every seat the Shabaloth pointed at last night".
# ---------------------------------------------------------------------------


def test_shabaloth_no_regurgitate_for_tea_lady_saved_then_assassin_killed() -> None:
    """Regression for the Tea Lady + Assassin scenario.

    Setup: Bob is the Tea Lady; both his alive neighbours (Alice and
    Cara) are good, so both are protected by ``CANNOT DIE``.

    Night 2:
      * Shabaloth picks Alice and Cara. Tea Lady cancels both
        PRE_DEATHs — neither dies and neither receives a Shabaloth
        DEAD marker.
      * Assassin force-kills Alice. Tea Lady can't save Alice from
        a force-kill, so Alice dies — but the cause is the Assassin,
        not the Shabaloth.

    Night 3:
      * The Shabaloth's regurgitation step must NOT fire — no DEAD
        markers landed last night, so there is nothing in the
        regurgitation pool. (Pre-fix, Alice would have been in
        ``_last_attacked_ids`` from the Shabaloth's pick and the
        Storyteller would have been offered to regurgitate her even
        though the Shabaloth never actually killed her.)

    The test scripts only the prompts that should fire — if the
    regurgitate prompt fires unexpectedly, ``drain_prompts`` raises
    "Unexpected extra prompt".
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Saint  (Tea Lady's good neighbour)
    b = e.add_seat("Bob")      # 2 — Tea Lady
    c = e.add_seat("Cara")     # 3 — Recluse (Tea Lady's other good neighbour)
    d = e.add_seat("Dan")      # 4 — Assassin
    f = e.add_seat("Eve")      # 5 — Shabaloth

    e.assign_character(a.id, "Saint")
    e.assign_character(b.id, "Tea Lady")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Assassin")
    e.assign_character(f.id, "Shabaloth")

    e.start_game()
    e.start_night()
    # Night 1: nobody acts (no Empath / Poisoner; Shabaloth doesn't
    # act on night 1).
    drain_prompts(e, [])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    # Night 2: Shabaloth (order 28) picks Alice + Cara. Tea Lady's
    # CANNOT DIE on each cancels both PRE_DEATHs — no DEAD markers
    # land. Then the Assassin (order 43) force-kills Alice;
    # Tea Lady's cancellation respects ``force=True`` so Alice dies.
    drain_prompts(e, [
        ({"character": "Shabaloth", "step": "select_targets"}, [1, 3]),
        ({"character": "Assassin",  "step": "select_target"},  1),
    ])
    e.advance_to_day()
    assert e.get_player(1).dead, (
        "Assassin's force-kill bypasses Tea Lady; Alice dies."
    )
    assert e.get_player(3).alive, (
        "Cara is Tea Lady-saved and never targeted by the Assassin."
    )
    # Sanity: no Shabaloth DEAD markers in the registry — both attacks
    # were cancelled by Tea Lady before the kill landed.
    from engine.characters.shabaloth import (
        Shabaloth, ShabalothDeadEffect, ShabalothAliveEffect,
    )
    shabaloth = e.get_player(f.id).character
    dead_effects = [
        eff for eff in e.effects_sourced_by(shabaloth)
        if isinstance(eff, ShabalothDeadEffect)
    ]
    assert dead_effects == [], (
        f"No Shabaloth DEAD markers should exist after Tea Lady "
        f"cancelled both kills; got {dead_effects}"
    )

    e.advance_to_night()
    e.start_night()
    # Night 3: Shabaloth wakes. The regurgitate prompt MUST NOT fire
    # (no DEAD-marked seats from last night), so the only Shabaloth
    # prompt is the standard select_targets. Picking [Cara, Dan]
    # exercises a normal kill so the night completes cleanly.
    drain_prompts(e, [
        ({"character": "Shabaloth", "step": "select_targets"}, [3, 4]),
    ])
    e.advance_to_day()

    # And no ALIVE marker should ever have been created — the
    # Storyteller was never offered to regurgitate.
    alive_effects = [
        eff for eff in e.effects_sourced_by(shabaloth)
        if isinstance(eff, ShabalothAliveEffect)
    ]
    assert alive_effects == [], (
        f"Alice must not be regurgitated; got ALIVE effects {alive_effects}"
    )


def test_shabaloth_dead_pool_clears_even_when_kill_step_bails_out() -> None:
    """Regression for the DEAD-purge timing.

    The DEAD-pool refresh must happen *after* Step 1 (regurgitation)
    and *before* Step 2's early-return paths. Otherwise, on a night
    where the ST flubs the picks (returns <2 player ids) the kill
    step bails out before the bulk-purge runs, leaving last night's
    DEAD markers in place. They would then re-qualify the same
    victim for regurgitation a night later — violating the rulebook
    "a dead player you chose **last night**" gate.

    Scenario:
      * Night 2: Shabaloth kills Alice + Bob → DEAD on both.
      * Night 3: ST declines regurgitation. Then ST returns a
        single-id pick on the kill prompt; ``len(chosen_players)
        < 2`` triggers the early return.
      * Night 4: regurgitation step must NOT offer Alice or Bob —
        they are no longer "last night's" victims.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Saint    (passive, dies cleanly)
    b = e.add_seat("Bob")      # 2 — Recluse  (passive)
    c = e.add_seat("Cara")     # 3 — Soldier  (passive at night vs Shabaloth)
    d = e.add_seat("Dan")      # 4 — Mastermind (no nightly action)
    f = e.add_seat("Eve")      # 5 — Shabaloth

    e.assign_character(a.id, "Saint")
    e.assign_character(b.id, "Recluse")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Mastermind")
    e.assign_character(f.id, "Shabaloth")

    e.start_game()
    # Night 1: nothing.
    e.start_night()
    drain_prompts(e, [])
    e.advance_to_day()
    # Night 2: Shabaloth eats Alice + Bob — both die, both get DEAD.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Shabaloth", "step": "select_targets"}, [1, 2]),
    ])
    e.advance_to_day()
    assert e.get_player(1).dead and e.get_player(2).dead

    from engine.characters.shabaloth import (
        Shabaloth, ShabalothDeadEffect,
    )
    shabaloth = e.get_player(f.id).character
    dead_after_n2 = [
        eff for eff in e.effects_sourced_by(shabaloth)
        if isinstance(eff, ShabalothDeadEffect)
    ]
    assert sorted(t for eff in dead_after_n2 for t in eff.targets) == [1, 2], (
        f"Expected DEAD on Alice + Bob; got {dead_after_n2}"
    )

    # Night 3: regurgitation declined; kill step bails out via the
    # <2-pick early return (we send a single-id response to the
    # count=2 SelectPlayerPrompt; the engine treats it as a no-pick
    # night via ``len(chosen_players) < 2: return``).
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        # Decline regurgitation.
        ({"character": "Shabaloth", "step": "regurgitate_pick"}, 0),
        # Single-id response → bails out the kill step early.
        ({"character": "Shabaloth", "step": "select_targets"}, 3),
    ])
    e.advance_to_day()

    # The DEAD pool must be empty: the regurgitation step consumed
    # last night's markers, and the bulk-purge cleared them before
    # the kill step's early return.
    dead_after_n3 = [
        eff for eff in e.effects_sourced_by(shabaloth)
        if isinstance(eff, ShabalothDeadEffect)
    ]
    assert dead_after_n3 == [], (
        f"DEAD markers must be cleared after Step 1 even when Step 2 "
        f"bails out; got {dead_after_n3}"
    )

    # Night 4: regurgitation prompt MUST NOT fire — there are no
    # DEAD-marked seats from "last night" (Night 3 emitted none).
    # Only the standard select_targets prompt should appear.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Shabaloth", "step": "select_targets"}, [3, 4]),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Multi-target action + Goon (user scenarios 2/3/5/6)
# ---------------------------------------------------------------------------


def test_shabaloth_picks_goon_first_neither_dies() -> None:
    """User scenario 2.

    Shabaloth picks ``[Goon, A]`` — the first selection is the Goon,
    so the Goon's retort drunkens the Shabaloth *before* any
    per-target kill runs. ``process_targets_with_goon_break`` notifies
    first; the post-notify ``has_ability`` guard stops the loop, and
    ``action_fn`` never runs. Neither target dies.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Goon
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Shabaloth

    e.assign_character(a.id, "Goon")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Shabaloth")

    e.start_game()
    e.start_night()
    # Night 1: Poisoner picks self, Shabaloth doesn't act on N1.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    # Night 2: Poisoner picks self again. Shabaloth picks
    # [Goon=1, Mayor=2] — Goon FIRST. Notify drunkens Shabaloth
    # immediately; loop breaks with no kills. The Goon flips to
    # evil and wakes for the alignment reveal.
    drain_prompts(e, [
        ({"character": "Poisoner",  "step": "select_player"},   4),
        ({"character": "Shabaloth", "step": "select_targets"},  [1, 2]),
        ({"character": "Goon",      "step": "alignment_reveal"}, None),
    ])
    e.advance_to_day()

    assert e.get_player(1).alive, "Goon survives — no kill on the Goon."
    assert e.get_player(2).alive, (
        "Mayor survives — Shabaloth was drunkened on the Goon "
        "before the Mayor's kill could run."
    )
    assert e.get_player(5).drunk, (
        "Shabaloth drunkened by Goon's retort."
    )
    assert e.get_player(1).alignment is Alignment.EVIL, (
        "Goon flips evil to match Shabaloth."
    )


def test_shabaloth_picks_other_first_then_goon_first_dies_goon_survives() -> None:
    """User scenario 3.

    Shabaloth picks ``[A, Goon]`` — A is killed first (the per-target
    notify is a no-op for A because A is not the Goon, then
    action_fn runs and kills A). On the next iteration the Goon's
    retort fires; the post-notify ``has_ability`` guard stops the
    loop and the kill on the Goon never lands.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Goon
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Shabaloth

    e.assign_character(a.id, "Goon")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Shabaloth")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    # Night 2: Shabaloth picks [Mayor=2, Goon=1] — Mayor FIRST.
    # Mayor's redirect prompt fires on the Mayor's PRE_DEATH; we
    # decline. Mayor dies. Then notify(Goon) drunkens Shabaloth and
    # the loop breaks. The Goon flips evil and wakes for the
    # alignment reveal. The Goon's kill never lands.
    drain_prompts(e, [
        ({"character": "Poisoner",  "step": "select_player"},   4),
        ({"character": "Shabaloth", "step": "select_targets"},  [2, 1]),
        ({"character": "Mayor",     "step": "redirect_yes_no"}, False),
        ({"character": "Goon",      "step": "alignment_reveal"}, None),
    ])
    e.advance_to_day()

    assert e.get_player(2).dead, "Mayor dies (Shabaloth picked first)."
    assert e.get_player(1).alive, "Goon survives the second-pick retort."
    assert e.get_player(5).drunk, "Shabaloth drunkened by Goon's retort."
    assert e.get_player(1).alignment is Alignment.EVIL, (
        "Goon flips evil to match Shabaloth."
    )


def test_innkeeper_picks_goon_first_neither_protected_nor_drunk() -> None:
    """User scenario 5.

    Innkeeper picks ``[Goon, Mayor]`` — Goon FIRST. The notify
    drunkens the Innkeeper immediately; the post-notify guard breaks
    the loop and ``action_fn`` never runs. Neither target receives
    SAFE or DRUNK; the demon's later kill on the Mayor lands normally.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Innkeeper
    b = e.add_seat("Bob")      # 2 — Goon
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Innkeeper")
    e.assign_character(b.id, "Goon")
    e.assign_character(c.id, "Mayor")
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
    # Night 2: Innkeeper picks [Goon=2, Mayor=3], chooses Mayor as
    # the drunk-target. Goon retort fires immediately on the first
    # iteration — neither effect lands. Imp then attacks Mayor; no
    # SAFE → Mayor dies (declines redirect).
    drain_prompts(e, [
        ({"character": "Poisoner",  "step": "select_player"},   4),
        ({"character": "Innkeeper", "step": "select_players"},  [2, 3]),
        ({"character": "Innkeeper", "step": "select_drunk"},    3),
        ({"character": "Imp",       "step": "select_target"},   3),
        ({"character": "Mayor",     "step": "redirect_yes_no"}, False),
    ])
    e.advance_to_day()

    assert e.get_player(1).drunk, (
        "Innkeeper drunkened by Goon's retort on the first pick."
    )
    assert not e.get_player(2).drunk, (
        "Goon NOT drunkened by Innkeeper — action_fn never ran."
    )
    assert not e.get_player(3).drunk, (
        "Mayor NOT drunkened — action_fn never ran for the Mayor."
    )
    assert e.get_player(3).dead, (
        "Mayor was not protected by the Innkeeper (action_fn skipped) "
        "so the Imp's kill landed."
    )


def test_innkeeper_picks_other_first_then_goon_no_effects_emit() -> None:
    """User scenario 6 (revised): pick order does NOT matter for the
    Innkeeper-vs-Goon interaction.

    The Innkeeper's ability is conceptually one fire. When the Goon
    is among the picks, the Goon's retort drunkens the Innkeeper
    before any of the Innkeeper's effects can land — so neither
    SAFE nor DRUNK is emitted, in either pick order. This test
    exercises ``[Mayor, Goon]`` (Mayor first); it must produce the
    same end state as ``[Goon, Mayor]`` (covered by
    ``test_innkeeper_picks_goon_first_neither_protected_nor_drunk``):
    Innkeeper drunk via Goon retort, Mayor not drunk, Mayor not
    protected, Imp's later kill on Mayor lands.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Innkeeper
    b = e.add_seat("Bob")      # 2 — Goon
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Innkeeper")
    e.assign_character(b.id, "Goon")
    e.assign_character(c.id, "Mayor")
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
    # Night 2: Innkeeper picks [Mayor=3, Goon=2] — Mayor FIRST.
    # The Innkeeper notifies all picks first; the Goon's retort
    # drunkens the Innkeeper *before* the has_ability-gated emit
    # block runs. Neither SAFE nor DRUNK lands on either target.
    # Imp attacks Mayor — no SAFE so Mayor dies (declines redirect).
    drain_prompts(e, [
        ({"character": "Poisoner",  "step": "select_player"},   4),
        ({"character": "Innkeeper", "step": "select_players"},  [3, 2]),
        ({"character": "Innkeeper", "step": "select_drunk"},    3),
        ({"character": "Imp",       "step": "select_target"},   3),
        ({"character": "Mayor",     "step": "redirect_yes_no"}, False),
    ])
    e.advance_to_day()

    assert e.get_player(1).drunk, (
        "Innkeeper drunkened by Goon's retort."
    )
    assert not e.get_player(3).drunk, (
        "Mayor NOT drunk — InnkeeperDrunkEffect was never emitted "
        "(notify-all-first preempts the has_ability-gated emit)."
    )
    assert e.get_player(3).dead, (
        "Mayor dies — no InnkeeperSafeEffect, Imp's kill landed."
    )
    assert not e.get_player(2).drunk, (
        "Goon NOT drunkened by the Innkeeper — emission was preempted."
    )
    assert e.get_player(2).alignment is Alignment.GOOD, (
        "Goon stays good — Innkeeper is good, no flip needed."
    )

    # Order-independence: the Innkeeper must not have left any
    # inactive ``InnkeeperSafeEffect`` / ``InnkeeperDrunkEffect`` in
    # the registry. (If we'd used the per-target loop helper, the
    # ``[Mayor, Goon]`` ordering would have emitted-then-deactivated
    # them — that's the asymmetry this fix removes.)
    from engine.characters.innkeeper import (
        InnkeeperSafeEffect,
        InnkeeperDrunkEffect,
    )
    inn_char = e.get_player(1).character
    sourced = list(e.effects_sourced_by(inn_char))
    assert not any(
        isinstance(eff, (InnkeeperSafeEffect, InnkeeperDrunkEffect))
        for eff in sourced
    ), (
        "No Innkeeper-sourced SAFE/DRUNK in the registry — the "
        "Goon retort preempted emission, regardless of order."
    )


# ---------------------------------------------------------------------------
# Assassin picks the Goon (user scenario 4)
# ---------------------------------------------------------------------------


def test_assassin_picks_goon_dies_and_turns_evil() -> None:
    """User scenario 4 — "If chosen by the Assassin, the Goon dies but
    still turns evil."

    The Assassin's force-kill bypasses every PRE_DEATH canceller, AND
    bypasses the Goon's own retort drunkening on the Assassin. The
    Assassin captures their sober state at SELECT time before the
    notify call, so the post-notify drunken state doesn't block the
    kill. The Goon's retort still fires synchronously inside
    ``notify_goon_chosen``, flipping alignment to evil while the Goon
    is still alive (the kill resolves *after* the notify).

    Result: Goon dies + Goon's alignment flipped to evil + Assassin
    is drunkened by the retort + Assassin's slot is consumed.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Goon
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Assassin
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Goon")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Assassin")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    # Night 1: nobody acts (no Empath / Poisoner in this seating; the
    # bare Engine() runs no preset Demon/Minion Info on first night).
    drain_prompts(e, [])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    # Night 2: Imp kills Soldier (immune), then Assassin force-kills
    # Goon. The Goon's retort flips alignment to evil and wakes the
    # Goon for the alignment reveal *before* the force-kill lands.
    drain_prompts(e, [
        ({"character": "Imp",      "step": "select_target"},   a.id),
        ({"character": "Assassin", "step": "select_target"},   b.id),
        ({"character": "Goon",     "step": "alignment_reveal"}, None),
    ])
    e.advance_to_day()

    # Goon dies — force-kill bypasses anything.
    assert e.get_player(b.id).dead, (
        "Goon dies from the Assassin's force-kill, even though the "
        "Goon's retort drunkened the Assassin moments earlier."
    )
    # Goon's alignment flipped to evil while still alive (the
    # alignment flip is a direct mutation to Player.alignment, not
    # an effect, so it persists post-mortem).
    assert e.get_player(b.id).alignment is Alignment.EVIL, (
        "Goon flips evil to match the Assassin (retort fires before kill)."
    )
    # The Assassin is drunkened transiently inside ``choose_me``,
    # but the immediate force-kill on the Goon purges
    # ``GoonDrunkEffect`` via the registry's
    # ``purge_on_source_death=True`` default — same cascade as user
    # scenario 6 (Goon dying sobers anyone they had drunkened). The
    # observable end-of-night state is therefore: Assassin SOBER.
    assert not e.get_player(d.id).drunk, (
        "Assassin sobered immediately when the Goon died — "
        "GoonDrunkEffect purged via purge_on_source_death=True."
    )
    # Slot is consumed.
    assert e.get_player(d.id).character._used, (
        "Assassin's once-per-game slot is spent."
    )


def test_drunk_assassin_picks_goon_no_kill_no_flip() -> None:
    """A drunk-at-activation Assassin (e.g. previously poisoned by a
    Poisoner) consumes their slot when they pick a target, but the
    kill does NOT land — that's the standard BotC rule for a drunk
    source. The Goon's retort gate is independent of the Assassin's
    state (the wiki: "The Goon still changes alignment, and makes the
    player drunk, if the player choosing the Goon was already drunk
    or poisoned."), so the Goon's alignment STILL flips to evil and
    the Assassin would be drunkened — but the Assassin was already
    drunken from the Poisoner so it's a no-op.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Mayor
    b = e.add_seat("Bob")      # 2 — Goon
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Assassin? — no, the demon must be Imp.

    # Different setup: we need the Poisoner to be a *separate* minion
    # from the Assassin so the Poisoner can poison the Assassin. But
    # a 5-player game is 1 demon, 1 minion at most. Use 6 seats instead.
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Goon
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Recluse
    g = e.add_seat("Greta")    # 5 — Poisoner
    f = e.add_seat("Eve")      # 6 — Imp ... wait, where's the Assassin?

    # We need the Assassin AND the Poisoner. Both Minions. So we need
    # a 7-player game (2 Minions). Setup:
    e = Engine()
    p1 = e.add_seat("Alice")   # 1 — Goon
    p2 = e.add_seat("Bob")     # 2 — Mayor
    p3 = e.add_seat("Cara")    # 3 — Soldier
    p4 = e.add_seat("Dan")     # 4 — Recluse
    p5 = e.add_seat("Eve")     # 5 — Saint
    p6 = e.add_seat("Frank")   # 6 — Poisoner
    p7 = e.add_seat("Greta")   # 7 — Assassin
    p8 = e.add_seat("Hank")    # 8 — Imp

    e.assign_character(p1.id, "Goon")
    e.assign_character(p2.id, "Mayor")
    e.assign_character(p3.id, "Soldier")
    e.assign_character(p4.id, "Recluse")
    e.assign_character(p5.id, "Saint")
    e.assign_character(p6.id, "Poisoner")
    e.assign_character(p7.id, "Assassin")
    e.assign_character(p8.id, "Imp")

    e.start_game()
    e.start_night()
    # Night 1: Poisoner poisons the Assassin.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, p7.id),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    # Night 2: Poisoner re-poisons the Assassin (so Assassin enters
    # SELECT already poisoned). Imp picks somebody irrelevant.
    # Assassin then picks the Goon.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, p7.id),
        # Imp picks Soldier (immune) — no death.
        ({"character": "Imp",      "step": "select_target"},  p3.id),
        # Assassin picks Goon — but Assassin is poisoned, so kill
        # does NOT land. The Goon's retort still fires (gate is
        # source-state-independent per the wiki) and the Goon
        # wakes for the alignment reveal.
        ({"character": "Assassin", "step": "select_target"},   p1.id),
        ({"character": "Goon",     "step": "alignment_reveal"}, None),
    ])
    e.advance_to_day()

    # Goon survives — the drunk Assassin's force-kill never fired.
    assert e.get_player(p1.id).alive, (
        "Drunk Assassin's force-kill does not land; Goon survives."
    )
    # The Goon's retort still fires regardless of the source's state
    # (per the wiki). Alignment flips to evil to match the Assassin;
    # the Goon's drunkening is added to the Assassin's seat (no-op
    # in practice — Assassin was already poisoned by the Poisoner —
    # but the alignment flip is the observable effect).
    assert e.get_player(p1.id).alignment is Alignment.EVIL, (
        "Goon flips evil — retort fires regardless of source's "
        "droison state."
    )
    # Slot is still consumed (BotC standard rule).
    assert e.get_player(p7.id).character._used, (
        "Assassin's slot is spent regardless of drunk state."
    )


# Note: the original PR 7 cascade-reactivation test
# (Innkeeper picks [Other, Goon] → Other gets SAFE+DRUNK
# (deactivated) → Goon dies → SAFE+DRUNK reactivate) was removed
# when the Innkeeper switched to a notify-all-then-emit shape.
# The Innkeeper's effects no longer emit at all when the Goon is
# among the picks — there's nothing to deactivate or reactivate.
# The general registry cascade behavior (effects deactivate on
# source droisoned, reactivate on source sober) is still exercised
# by other character tests.
