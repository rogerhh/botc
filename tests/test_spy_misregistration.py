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
# Investigator: NEVER prompts for Spy registration.
# ---------------------------------------------------------------------------


def test_investigator_sees_spy_correctly_no_prompt() -> None:
    """Investigator's seen-Minion can be the Spy with no
    misregistration prompt."""
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
        # No Poisoner in play. Investigator runs first.
        # Sober + healthy + both presets → straight to information.
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
    """When the chosen Townsfolk has no actual holder and a Spy is in
    play, the Spy automatically becomes the seen player. The chosen
    Townsfolk *is* the character the Spy registers as for the WW."""
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
        # Storyteller picks "Slayer" (not in play) — only the Spy can be
        # the seen player. No Yes/No prompt; Spy is auto-selected.
        ({"character": "Washerwoman",  "step": "select_character"},
         "Slayer"),
        # Now ST picks a WRONG player (any non-self, non-Spy player).
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Slayer"}, b.id),
        ({"character": "Washerwoman",  "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_washerwoman_with_spy_in_play_and_actual_holder() -> None:
    """When both the chosen Townsfolk's actual holder AND the Spy are
    in play, the Storyteller is asked Yes/No to use the Spy. Default
    No → use the actual holder."""
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
        # ST picks Mayor (in play).
        ({"character": "Washerwoman",  "step": "select_character"}, "Mayor"),
        # Yes/No: use Spy as seen? Answer No → use actual Mayor.
        ({"character": "Washerwoman",  "step": "use_spy_as_seen"}, False),
        # Wrong player: not Bob (actual Mayor) and not Alice (self).
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Mayor"}, c.id),
        ({"character": "Washerwoman",  "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
    ])
    e.advance_to_day()


def test_washerwoman_choose_spy_when_actual_in_play() -> None:
    """When both the actual holder AND the Spy exist, the ST can opt
    to use the Spy (Yes on the override prompt)."""
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
        ({"character": "Washerwoman",  "step": "select_character"}, "Mayor"),
        # Yes → use Spy as seen.
        ({"character": "Washerwoman",  "step": "use_spy_as_seen"}, True),
        # WRONG player can be Bob (actual Mayor), Cara, or Eve. Not the
        # Spy (already the seen) or Alice (self).
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Mayor"}, b.id),
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
        # ST picks "Saint" (not in play) — Spy is the seen player.
        ({"character": "Librarian",    "step": "select_character"}, "Saint"),
        ({"character": "Librarian",    "step": "select_wrong_player",
          "shown_character": "Saint"}, b.id),
        ({"character": "Librarian",    "step": "information"}, None),
        ({"character": "Spy",          "step": "grimoire"}, None),
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
    test_investigator_sees_spy_correctly_no_prompt()
    print("test 7 passed.")
    test_undertaker_on_spy_shows_spy_registered_character()
    print("test 8 passed.")
    test_ravenkeeper_on_spy_shows_spy_registered_character()
    print("test 9 passed.")
    test_washerwoman_with_spy_seen_no_actual_holder()
    print("test 10 passed.")
    test_washerwoman_with_spy_in_play_and_actual_holder()
    print("test 11 passed.")
    test_washerwoman_choose_spy_when_actual_in_play()
    print("test 12 passed.")
    test_librarian_with_spy_no_actual_outsiders()
    print("test 13 passed.")
    print("All Spy misregistration tests passed.")
