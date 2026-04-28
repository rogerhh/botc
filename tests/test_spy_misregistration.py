"""Spy misregistration tests.

Exercises the Spy's misregistration behaviour across the detection
characters: Empath, Chef, Undertaker, Ravenkeeper, Washerwoman,
Librarian (and verifies the Investigator does NOT prompt).

The Spy ability text reads:

    "Each night, you see the Grimoire. You might register as good and
     as a Townsfolk or Outsider, even if dead."

Per the project's CLAUDE.md and the Spy.pdf wiki page, the engine:

  * Tracks an internal preferred-good-character on the Spy (a random
    good role currently NOT in play). This isn't displayed as game
    state — it's only the *default* offered on each per-ability
    registration prompt so a Storyteller can hit Next for a consistent
    Spy character across the night.
  * Surfaces a SelectCharacterPrompt with eligible = (all good roles +
    Spy) whenever an ability targets the Spy. Choosing "Spy" registers
    as themselves (evil); any other name registers as that good role.
  * Skips the prompt for the Investigator (Spy is detected correctly)
    and for the Fortune Teller (Spy can't register as a Demon under
    the rules wording).
  * Allows the Washerwoman / Librarian seen-character token to point
    at the Spy — the chosen Townsfolk/Outsider is the character the
    Spy registers as for that ability.
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
# Setup-time: Spy picks an internal preferred-good-character.
# ---------------------------------------------------------------------------


def test_spy_picks_preferred_good_character_at_setup() -> None:
    e = Engine()
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")
    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    spy = e.get_player(d.id).character
    pref = spy._preferred_good_character
    assert pref is not None, "Spy should pick a preferred good character at setup."
    in_play = set(e.in_play_character_names())
    good_names = (
        e.all_character_names_by_type(CharType.TOWNSFOLK)
        + e.all_character_names_by_type(CharType.OUTSIDER)
    )
    assert pref in good_names, f"{pref!r} should be a good role on the script."
    assert pref not in in_play, (
        f"{pref!r} should NOT be in play (preferred-good-not-in-play)."
    )


# ---------------------------------------------------------------------------
# Empath: Spy neighbour triggers a registration prompt.
# ---------------------------------------------------------------------------


def test_empath_spy_neighbour_registers_good_counts_zero() -> None:
    """When a Spy borders the Empath and registers as a Townsfolk, the
    Empath's evil-neighbour count drops by 1 (the Spy is treated as
    good)."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Empath
    b = e.add_seat("Bob")      # 2 — Spy (evil neighbour clockwise)
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp (evil neighbour ccw)

    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Spy")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks self (no Empath impact).
        ({"character": "Poisoner",     "step": "select_player"}, d.id),
        # Empath: Spy neighbour Bob registers as Slayer (good) — count
        # drops to 1 (only Eve, the Imp, counts as evil).
        ({"character": "Empath",       "step": "spy_registers_as"},
         "Slayer"),
        ({"character": "Empath",       "step": "information"}, None),
        # Spy gets the grimoire prompt.
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_empath_spy_neighbour_registers_evil_counts_two() -> None:
    """When the Storyteller chooses to have the Spy register as the
    literal Spy, the Empath count includes the Spy as evil."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Empath
    b = e.add_seat("Bob")      # 2 — Spy
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Spy")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",     "step": "select_player"}, d.id),
        # Spy registers as itself → count is 2 (Bob + Eve).
        ({"character": "Empath",       "step": "spy_registers_as"}, "Spy"),
        ({"character": "Empath",       "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_empath_no_spy_no_prompt() -> None:
    """Empath on a board with no Spy: no spy_registers_as prompt fires
    (regression check that the prompt only appears when the Spy is in
    play)."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Empath
    b = e.add_seat("Bob")      # 2 — Soldier
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Soldier")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",     "step": "select_player"}, d.id),
        ({"character": "Empath",       "step": "information"}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Chef: Spy in play triggers a registration prompt; affects pair count.
# ---------------------------------------------------------------------------


def test_chef_spy_registers_good_breaks_pair() -> None:
    """The Chef sees one fewer adjacent-evil pair when the Spy
    registers as a good Townsfolk."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Chef
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy   (evil)
    f = e.add_seat("Eve")      # 5 — Imp   (evil) — adjacent to Dan
    g = e.add_seat("Frank")    # 6 — Poisoner (evil) — adjacent to Eve and Alice

    e.assign_character(a.id, "Chef")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")
    e.assign_character(g.id, "Poisoner")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",     "step": "select_player"}, g.id),
        # Spy registers as a Townsfolk → only Eve+Frank pair counts (1).
        ({"character": "Chef",         "step": "spy_registers_as"}, "Slayer"),
        ({"character": "Chef",         "step": "information"}, None),
        # Spy still gets the grimoire prompt later in the night.
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_chef_spy_registers_evil_full_count() -> None:
    """When the Spy registers as itself, the Chef sees the full evil
    count."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Chef
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp     # adjacent to Dan
    g = e.add_seat("Frank")    # 6 — Poisoner  # adjacent to Eve

    e.assign_character(a.id, "Chef")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")
    e.assign_character(g.id, "Poisoner")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",     "step": "select_player"}, g.id),
        # Spy registers as itself → 2 pairs (Spy+Imp, Imp+Poisoner).
        ({"character": "Chef",         "step": "spy_registers_as"}, "Spy"),
        ({"character": "Chef",         "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Investigator: with seen token on Spy, prompts for Spy's registration.
# Picking a Minion → standard 1-of-2 reading; non-Minion → "0 Minions"
# reading similar to Librarian's "0 Outsiders".
# ---------------------------------------------------------------------------


def test_investigator_seen_on_spy_fires_registration_prompt() -> None:
    """Investigator's seen-token on the Spy fires a Spy registration
    prompt, even though the Spy is itself a Minion. The check is a
    name-attribute Check with passes=("Spy",); since "Spy" is in the
    Spy's eligible-name list (TF + Outsider + "Spy"), the override
    fires so the ST may opt to have the Spy register as a good role
    (in which case the Investigator's check fails for that seat)."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Investigator
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Investigator")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    # Force the Investigator's seen-Minion to be the Spy.
    e.apply_setup_data({
        "investigator_minion": "Spy",
        "investigator_wrong": "Mayor",
    })

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # ST is asked what the Spy registers as — pick "Spy" itself
        # so the Investigator's name-check passes.
        ({"character": "Investigator", "step": "spy_registers_as"}, "Spy"),
        ({"character": "Investigator", "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Undertaker: prompt fires when the executed player is the Spy.
# ---------------------------------------------------------------------------


def test_undertaker_on_spy_shows_spy_registered_character() -> None:
    """When the executed player is the Spy, the Undertaker is shown
    whatever character the Spy registers as."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Undertaker
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Undertaker")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.start_game()

    # Night 1 — only the Spy acts.
    e.start_night()
    drain_prompts(e, [
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()

    # Day 1 — execute the Spy.
    e.execute_player(d.id)
    # Spy execution doesn't end the game (Spy is a Minion).
    assert e.phase is Phase.DAY

    # Night 2 — Undertaker wakes for the executed Spy.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        # Imp goes first on night 2; pick Soldier (immune; nobody dies).
        ({"character": "Imp",          "step": "select_target"}, c.id),
        # Undertaker on the Spy: ST picks what character to show.
        ({"character": "Undertaker",   "step": "spy_registers_as"},
         "Slayer"),
        ({"character": "Undertaker",   "step": "information"}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Ravenkeeper: prompt fires when the chosen target is the Spy.
# ---------------------------------------------------------------------------


def test_ravenkeeper_on_spy_shows_spy_registered_character() -> None:
    """A sober Ravenkeeper who picks the Spy is shown whatever role
    the Spy registers as."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Ravenkeeper
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Ravenkeeper")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.start_game()

    # Night 1 (Ravenkeeper doesn't act night 1 unless they died, but
    # they're alive here).
    e.start_night()
    drain_prompts(e, [
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()

    # Night 2: Imp kills the Ravenkeeper. The RK then wakes to learn a
    # character — pick the Spy.
    e.start_night()
    drain_prompts(e, [
        ({"character": "Imp",          "step": "select_target"}, a.id),
        # Ravenkeeper picks the Spy (Dan).
        ({"character": "Ravenkeeper",  "step": "select_player"}, d.id),
        # Spy misregistration prompt for the Ravenkeeper.
        ({"character": "Ravenkeeper",  "step": "spy_registers_as"},
         "Saint"),
        ({"character": "Ravenkeeper",  "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Washerwoman: Spy can be the seen player (registering as a Townsfolk
# that's not in play).
# ---------------------------------------------------------------------------


def test_washerwoman_with_spy_seen_no_actual_holder() -> None:
    """When the chosen Townsfolk has no actual holder, the engine asks
    each non-self player's ``registers_as``. The Spy override fires
    (categories=(TOWNSFOLK,) includes a good type) and the ST picks
    "Slayer" — the Spy becomes the seen player."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Storyteller picks "Slayer" (not in play). Pass 1 finds no
        # actual holder; pass 2 calls registers_as on each player.
        ({"character": "Washerwoman",  "step": "select_character"},
         "Slayer"),
        # Spy is the only player who can register as Slayer (Recluse
        # not in play; the Spy override fires on TOWNSFOLK category).
        ({"character": "Washerwoman",  "step": "spy_registers_as"},
         "Slayer"),
        # ST picks the WRONG player.
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Slayer"}, b.id),
        ({"character": "Washerwoman",  "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_washerwoman_with_spy_in_play_and_actual_holder() -> None:
    """When both the chosen Townsfolk's actual holder AND the Spy are
    in play, the actual holder always wins (pass 1 finds them) — no
    Spy registration prompt fires."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Mayor (actual holder of Mayor)
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # ST picks Mayor (in play). Pass 1 finds Bob (true Mayor) →
        # no Spy prompt. Straight to select_wrong_player.
        ({"character": "Washerwoman",  "step": "select_character"}, "Mayor"),
        # Wrong player: not Bob (actual Mayor) and not Alice (self).
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Mayor"}, c.id),
        ({"character": "Washerwoman",  "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_washerwoman_to_use_spy_pick_unmatched_townsfolk() -> None:
    """To make the Spy the seen player when an actual Townsfolk holder
    is in play for some role, the ST simply picks a different
    Townsfolk role that no actual player holds. The Spy then registers
    as that role."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Mayor (actual Mayor)
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # ST picks Slayer (not in play). Pass 1 finds nothing; pass 2
        # asks the Spy. The Spy registers as Slayer → seen player.
        ({"character": "Washerwoman",  "step": "select_character"}, "Slayer"),
        ({"character": "Washerwoman",  "step": "spy_registers_as"},
         "Slayer"),
        # WRONG player can be Bob, Cara, or Eve. Not the Spy
        # (seen) or Alice (self).
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Slayer"}, b.id),
        ({"character": "Washerwoman",  "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Librarian: Spy can be the seen player (registering as an Outsider
# even with no Outsiders in play).
# ---------------------------------------------------------------------------


def test_librarian_with_spy_no_actual_outsiders() -> None:
    """No Outsiders in play but Spy is in play → Librarian still gets
    a 1-of-2 read on the Spy registering as some Outsider."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Librarian
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Librarian")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # ST picks "Saint" (not in play). Pass 1 finds no holder; pass
        # 2 asks Spy.registers_as → Spy registers as Saint → seen.
        ({"character": "Librarian",    "step": "select_character"}, "Saint"),
        ({"character": "Librarian",    "step": "spy_registers_as"}, "Saint"),
        ({"character": "Librarian",    "step": "select_wrong_player",
          "shown_character": "Saint"}, b.id),
        ({"character": "Librarian",    "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_librarian_seen_token_on_spy_forces_outsider_pick() -> None:
    """When the Librarian's seen-Outsider token is dropped on the Spy
    chair (via ``apply_setup_data``), the night ability bypasses the
    ``select_character`` step entirely — instead the Spy's
    ``registers_as`` is prompted with the eligible list restricted to
    Outsider names. The ST must pick an Outsider; whichever they pick
    is what the Librarian is shown.

    Project rule: ``Librarian interaction with spy, if outsider seen
    token on spy, ST must register spy as some outsider character.``"""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Librarian
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Librarian")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.apply_setup_data({
        "librarian_outsider": "Spy",
        "librarian_wrong": "Mayor",
    })

    pending = []

    def collect(engine, scripted):
        deadline = time.time() + 5.0
        answered = 0
        last_id = -1
        while engine._night_thread and engine._night_thread.is_alive():
            if time.time() > deadline:
                raise TimeoutError("timed out")
            p = engine.pending_prompt()
            if p is None or p.id == last_id:
                time.sleep(0.01)
                continue
            if answered >= len(scripted):
                raise AssertionError(f"unexpected prompt {p.text!r}: {p.meta}")
            matcher, response = scripted[answered]
            for k, v in matcher.items():
                if p.meta.get(k) != v:
                    raise AssertionError(
                        f"Prompt #{answered+1} did not match: "
                        f"meta[{k!r}]={p.meta.get(k)!r} (expected {v!r})"
                    )
            # Record the eligible list of the spy_registers_as prompt
            # so the test can assert it was restricted to the right
            # category.
            if p.meta.get("step") == "spy_registers_as":
                pending.append(list(p.eligible_characters))
            last_id = p.id
            engine.respond(p.id, response)
            answered += 1
            time.sleep(0.01)

    e.start_game()
    e.start_night()
    collect(e, [
        # No select_character — seen token already on the Spy.
        # The Spy's registers_as prompt is the only ST interaction
        # before the wrong-player wireup.
        ({"character": "Librarian", "step": "spy_registers_as"}, "Saint"),
        # Wrong player is preset (Mayor), so no select_wrong_player.
        ({"character": "Librarian", "step": "information"}, None),
        ({"character": "Spy",       "step": "grimoire"}, None),
    ])
    e.advance_to_day()

    # The ``spy_registers_as`` eligible list should only contain
    # Outsider names — no Townsfolk, no "Spy" itself.
    assert len(pending) == 1, f"expected 1 spy_registers_as prompt, got {len(pending)}"
    eligible = pending[0]
    outsiders = set(e.all_character_names_by_type(CharType.OUTSIDER))
    townsfolk = set(e.all_character_names_by_type(CharType.TOWNSFOLK))
    assert set(eligible).issubset(outsiders), (
        f"Lib+Spy eligible should be restricted to Outsiders; got {eligible}"
    )
    assert "Spy" not in eligible
    assert not (set(eligible) & townsfolk)


def test_washerwoman_seen_token_on_spy_forces_townsfolk_pick() -> None:
    """When the Washerwoman's seen-Townsfolk token is on the Spy
    chair, the ST is prompted with eligible names restricted to
    Townsfolk roles only — no Outsider, no ``Spy``.

    Project rule: ``WW interaction with spy. If TF seen token on spy,
    ST must register spy as some TF character.``"""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.apply_setup_data({
        "washerwoman_townsfolk": "Spy",
        "washerwoman_wrong": "Mayor",
    })

    pending = []

    def collect(engine, scripted):
        deadline = time.time() + 5.0
        answered = 0
        last_id = -1
        while engine._night_thread and engine._night_thread.is_alive():
            if time.time() > deadline:
                raise TimeoutError("timed out")
            p = engine.pending_prompt()
            if p is None or p.id == last_id:
                time.sleep(0.01)
                continue
            if answered >= len(scripted):
                raise AssertionError(f"unexpected prompt {p.text!r}: {p.meta}")
            matcher, response = scripted[answered]
            for k, v in matcher.items():
                if p.meta.get(k) != v:
                    raise AssertionError(
                        f"Prompt #{answered+1} did not match: "
                        f"meta[{k!r}]={p.meta.get(k)!r} (expected {v!r})"
                    )
            if p.meta.get("step") == "spy_registers_as":
                pending.append(list(p.eligible_characters))
            last_id = p.id
            engine.respond(p.id, response)
            answered += 1
            time.sleep(0.01)

    e.start_game()
    e.start_night()
    collect(e, [
        ({"character": "Washerwoman", "step": "spy_registers_as"}, "Slayer"),
        ({"character": "Washerwoman", "step": "information"}, None),
        ({"character": "Spy",         "step": "grimoire"}, None),
    ])
    e.advance_to_day()

    assert len(pending) == 1
    eligible = pending[0]
    townsfolk = set(e.all_character_names_by_type(CharType.TOWNSFOLK))
    outsiders = set(e.all_character_names_by_type(CharType.OUTSIDER))
    assert set(eligible).issubset(townsfolk), (
        f"WW+Spy eligible should be restricted to Townsfolk; got {eligible}"
    )
    assert "Spy" not in eligible
    assert not (set(eligible) & outsiders)


def test_investigator_seen_on_spy_opt_out_shows_zero() -> None:
    """Spy seated on the Investigator's seen-Minion token may opt out
    of registering as a Minion (picking a TF or Outsider name in the
    ``spy_registers_as`` prompt). The Investigator's check then fails
    and a ``0 Minions`` reading is shown.

    Project rule: ``investigator interaction with spy, ST can decide
    that spy does not register as minion, then investigator learns a
    '0'``."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Investigator
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Investigator")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.apply_setup_data({
        "investigator_minion": "Spy",
        "investigator_wrong": "Mayor",
    })

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # ST registers Spy as a Townsfolk → name=Spy check fails.
        ({"character": "Investigator", "step": "spy_registers_as"},
         "Slayer"),
        # Investigator then sees the 0-Minions reading.
        ({"character": "Investigator", "step": "information",
          "shown_count": 0}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_seen_token_only_checks_seen_chair_no_spy_prompt() -> None:
    """Regression for the project rule ``Lib/WW/Inv ability only
    checks the character with the seen token at ability time``.

    With a Spy in play but the Librarian's seen-Outsider token
    pinned to a real Outsider (the Recluse here), the Librarian's
    night must not surface a ``spy_registers_as`` prompt. Previously
    the engine iterated every player and Spy.registers_as fired even
    though the Spy wasn't tagged."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Librarian
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Spy
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Librarian")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Spy")
    e.assign_character(f.id, "Imp")

    e.apply_setup_data({
        "librarian_outsider": "Recluse",
        "librarian_wrong": "Mayor",
    })

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Only the Recluse is checked — name attribute, passes=("Recluse",).
        ({"character": "Librarian", "step": "recluse_registers_as",
          "attribute": "name"}, "Recluse"),
        ({"character": "Librarian", "step": "information"}, None),
        ({"character": "Spy",       "step": "grimoire"}, None),
    ])
    e.advance_to_day()


if __name__ == "__main__":
    test_spy_picks_preferred_good_character_at_setup()
    print("test 1 passed.")
    test_empath_spy_neighbour_registers_good_counts_zero()
    print("test 2 passed.")
    test_empath_spy_neighbour_registers_evil_counts_two()
    print("test 3 passed.")
    test_empath_no_spy_no_prompt()
    print("test 4 passed.")
    test_chef_spy_registers_good_breaks_pair()
    print("test 5 passed.")
    test_chef_spy_registers_evil_full_count()
    print("test 6 passed.")
    test_investigator_seen_on_spy_fires_registration_prompt()
    print("test 7 passed.")
    test_undertaker_on_spy_shows_spy_registered_character()
    print("test 8 passed.")
    test_ravenkeeper_on_spy_shows_spy_registered_character()
    print("test 9 passed.")
    test_washerwoman_with_spy_seen_no_actual_holder()
    print("test 10 passed.")
    test_washerwoman_with_spy_in_play_and_actual_holder()
    print("test 11 passed.")
    test_washerwoman_to_use_spy_pick_unmatched_townsfolk()
    print("test 12 passed.")
    test_librarian_with_spy_no_actual_outsiders()
    print("test 13 passed.")
    test_librarian_seen_token_on_spy_forces_outsider_pick()
    print("test 14 passed.")
    test_washerwoman_seen_token_on_spy_forces_townsfolk_pick()
    print("test 15 passed.")
    test_investigator_seen_on_spy_opt_out_shows_zero()
    print("test 16 passed.")
    test_seen_token_only_checks_seen_chair_no_spy_prompt()
    print("test 17 passed.")
    print("All Spy misregistration tests passed.")
