"""Recluse misregistration tests.

Exercises the Recluse's misregistration behaviour across the detection
characters: Slayer, Librarian, and other ability checks where the
Recluse may register as a Minion or Demon.

The Recluse ability text reads:

    "You might register as evil and as a Minion or Demon, even if
     dead."

The engine implements this as an override of
:meth:`Character.registers_as`: every detection-side ability calls
``self.check(engine, target, the_check)``, which dispatches into
``target.character.registers_as(engine, the_check)``. The Recluse's
override fires whenever the check's outcome could depend on the
registration choice — using the type-keyed stubs (MinionStub,
DemonStub) for char_type checks so the ST prompt stays small.
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
# Slayer → Recluse: char_type=DEMON check; Recluse may register as Demon.
# ---------------------------------------------------------------------------


def test_slayer_on_recluse_offers_demon_stub() -> None:
    """The Slayer's daytime ability runs ``Check(char_type, (DEMON,))``
    on its target. When the target is the Recluse, the Recluse's
    char_type override fires with eligible = ``[MinionStub, DemonStub,
    "Recluse"]``. Picking DemonStub passes the check → Recluse dies."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Slayer
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Slayer")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, d.id),
    ])
    e.advance_to_day()

    # Day 1 — Slayer slays the Recluse.
    # ``use_daytime_ability`` spawns its own worker thread (reusing the
    # _night_thread slot), so we call it directly and drain prompts.
    e.use_daytime_ability(a.id)
    drain_prompts(e, [
        # Slayer picks the Recluse as the target.
        ({"character": "Slayer", "step": "select_target"}, c.id),
        # Recluse's char_type override fires — ST picks "Demon" (the
        # DemonStub display label).
        ({"character": "Slayer", "step": "recluse_registers_as",
          "attribute": "char_type"}, "Demon"),
    ])

    # Recluse should be dead.
    assert e.get_player(c.id).dead, (
        "Recluse should have died from the Slayer shot when registering "
        "as Demon."
    )


def test_slayer_on_recluse_recluse_stays_alive_when_registering_self() -> None:
    """When the ST picks ``"Recluse"`` (register as themselves) on the
    Slayer's char_type check, the Recluse's char_type is OUTSIDER —
    the check's pass=DEMON fails, so no kill happens."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Slayer
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Slayer")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, d.id),
    ])
    e.advance_to_day()

    e.use_daytime_ability(a.id)
    drain_prompts(e, [
        ({"character": "Slayer", "step": "select_target"}, c.id),
        # ST chooses to have the Recluse register as themselves —
        # check fails, no kill.
        ({"character": "Slayer", "step": "recluse_registers_as",
          "attribute": "char_type"}, "Recluse"),
    ])

    # Recluse should be alive.
    assert e.get_player(c.id).alive, (
        "Recluse should have stayed alive when registering as themselves."
    )


# ---------------------------------------------------------------------------
# Librarian → Recluse: name=Recluse check fires the registration prompt.
# ---------------------------------------------------------------------------


def test_librarian_seen_recluse_fires_registration_prompt() -> None:
    """When the Librarian's seen-Outsider token is on the Recluse, a
    Recluse registration prompt fires (name attribute, passes=("Recluse",)).
    The default — ``"Recluse"`` — passes the check, so the Librarian
    sees the Recluse player."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Librarian
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Librarian")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    # Force the Librarian's seen-Outsider to be the Recluse.
    e.apply_setup_data({
        "librarian_outsider": "Recluse",
        "librarian_wrong": "Mayor",
    })

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",  "step": "select_player"}, d.id),
        # Recluse is the iterated target — registration prompt fires.
        # Eligible = Minion + Demon + "Recluse" (name attribute).
        # ST picks "Recluse" (default) → matches Lib's name=Recluse check.
        ({"character": "Librarian", "step": "recluse_registers_as",
          "attribute": "name"}, "Recluse"),
        ({"character": "Librarian", "step": "information"}, None),
    ])
    e.advance_to_day()


def test_librarian_seen_recluse_opt_out_shows_zero() -> None:
    """When the Librarian's seen-Outsider token is on the Recluse and
    the ST opts the Recluse out of registering as an Outsider (picking
    a Minion / Demon name), the Librarian's check fails and the
    Librarian learns ``0 Outsiders in play``.

    This exercises the project rule: ``Librarian interaction with
    recluse. At ability time, the check function checks recluse
    registering_as function. If ST decides recluse is not Outsider,
    the librarian learns a '0'``."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Librarian
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Librarian")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.apply_setup_data({
        "librarian_outsider": "Recluse",
        "librarian_wrong": "Mayor",
    })

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",  "step": "select_player"}, d.id),
        # ST registers Recluse as a Minion → name=Recluse check fails.
        ({"character": "Librarian", "step": "recluse_registers_as",
          "attribute": "name"}, "Poisoner"),
        # Librarian then sees the 0-Outsiders reading.
        ({"character": "Librarian", "step": "information",
          "shown_count": 0}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Empath: alignment check uses small stub-eligible list.
# ---------------------------------------------------------------------------


def test_empath_on_recluse_neighbour_uses_alignment_stubs() -> None:
    """The Empath's alignment check on a Recluse neighbour offers a
    two-button choice: ``[EvilStub, "Recluse"]``. The ST may have the
    Recluse register as evil (count goes up) or as themselves (count
    stays)."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Empath
    b = e.add_seat("Bob")      # 2 — Recluse
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Recluse")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, d.id),
        # Recluse neighbour (Bob): alignment override fires — small
        # 2-button list. ST picks "Evil" (the EvilStub display label) →
        # counts as evil.
        ({"character": "Empath",   "step": "recluse_registers_as",
          "attribute": "alignment"}, "Evil"),
        ({"character": "Empath",   "step": "information"}, None),
    ])
    e.advance_to_day()


# ---------------------------------------------------------------------------
# Stubs: DemonStub exists and has the right metadata.
# ---------------------------------------------------------------------------


def test_demon_stub_exists() -> None:
    """The DemonStub stub exists alongside Townsfolk/Outsider/Minion."""
    from engine.characters.stubs import (
        DemonStub,
        STUB_BY_NAME,
        stub_for_char_type,
    )
    assert DemonStub.char_type is CharType.DEMON
    assert DemonStub.stub_alignment is Alignment.EVIL
    assert DemonStub.is_stub is True
    # The display label is "Demon" (no "Stub" suffix), so the ST
    # alignment / char_type pickers read as friendly category names.
    assert DemonStub.name == "Demon"
    assert "Demon" in STUB_BY_NAME
    assert stub_for_char_type(CharType.DEMON) is DemonStub


# ---------------------------------------------------------------------------
# Drunk / poisoned Recluse: misregistration ability fails — Recluse
# registers as themselves, no recluse_registers_as prompt fires.
# ---------------------------------------------------------------------------


def test_poisoned_recluse_does_not_misregister_no_prompt() -> None:
    """A poisoned Recluse's misregistration ability does not work.
    The detection-side check sees the Recluse as the Recluse (an
    Outsider → good), and the engine never emits a
    ``recluse_registers_as`` prompt.

    Scenario: Empath next to the Recluse. With the Recluse poisoned
    the Empath's alignment check on the Recluse falls through to
    the base ``Character.registers_as``, which returns ``"Recluse"``
    (Outsider → good). Combined with the Imp on the other side,
    the Empath sees count=1.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Empath
    b = e.add_seat("Bob")      # 2 — Recluse  (Empath's clockwise neighbour)
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Soldier
    f = e.add_seat("Eve")      # 5 — Imp      (Empath's ccw neighbour)

    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Recluse")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Soldier")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.poison(b.id)
    seen_texts: List[str] = []

    def collect(engine: Engine, scripted: List[Tuple[dict, Any]]) -> None:
        deadline = time.time() + 5.0
        answered = 0
        while engine._night_thread and engine._night_thread.is_alive():
            if time.time() > deadline:
                raise TimeoutError("night thread didn't finish")
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
                assert p.meta.get(k) == v, (
                    f"Prompt #{answered+1} mismatch: {p.meta!r}"
                )
            seen_texts.append(p.text)
            engine.respond(p.id, response)
            answered += 1
            time.sleep(0.01)
        assert answered == len(scripted)

    e.start_night()
    collect(e, [
        # NO recluse_registers_as prompt — the override no-ops on a
        # droisoned Recluse. Empath info: count=1 (only Eve the
        # Imp counts as evil; the Recluse registers as themselves
        # → Outsider → good).
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert any("1 of your alive neighbours is evil" in t for t in seen_texts), (
        f"Empath should see count=1 (Recluse registers as Recluse → "
        f"Outsider → good); texts={seen_texts!r}"
    )


def test_drunk_recluse_does_not_misregister_no_prompt() -> None:
    """Same as the poisoned case, but with a drunk Recluse."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Empath
    b = e.add_seat("Bob")      # 2 — Recluse
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Soldier
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Recluse")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Soldier")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.make_drunk(b.id)
    e.start_night()
    drain_prompts(e, [
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()


def test_poisoned_recluse_slayer_check_no_prompt() -> None:
    """A Slayer shoots a poisoned Recluse: no recluse_registers_as
    prompt fires (the Recluse can't misregister), the Recluse
    registers as themselves (Outsider, not Demon), and the shot
    fizzles — Recluse stays alive."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Slayer
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Recluse
    d = e.add_seat("Dan")      # 4 — Soldier
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Slayer")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Recluse")
    e.assign_character(d.id, "Soldier")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.poison(c.id)
    e.start_night()
    drain_prompts(e, [])
    e.advance_to_day()

    e.use_daytime_ability(a.id)
    drain_prompts(e, [
        # Slayer picks the Recluse. No recluse_registers_as prompt
        # follows — the Recluse is poisoned and the override
        # no-ops. The base check fails (Recluse char_type is
        # OUTSIDER, not DEMON).
        ({"character": "Slayer", "step": "select_target"}, c.id),
    ])

    assert e.get_player(c.id).alive, (
        "A poisoned Recluse can't misregister, so the Slayer's "
        "char_type=DEMON check fails and the Recluse survives."
    )


if __name__ == "__main__":
    test_slayer_on_recluse_offers_demon_stub()
    print("test 1 passed.")
    test_slayer_on_recluse_recluse_stays_alive_when_registering_self()
    print("test 2 passed.")
    test_librarian_seen_recluse_fires_registration_prompt()
    print("test 3 passed.")
    test_librarian_seen_recluse_opt_out_shows_zero()
    print("test 4 passed.")
    test_empath_on_recluse_neighbour_uses_alignment_stubs()
    print("test 5 passed.")
    test_demon_stub_exists()
    print("test 6 passed.")
    test_poisoned_recluse_does_not_misregister_no_prompt()
    print("test 7 passed.")
    test_drunk_recluse_does_not_misregister_no_prompt()
    print("test 8 passed.")
    test_poisoned_recluse_slayer_check_no_prompt()
    print("test 9 passed.")
    print("All Recluse misregistration tests passed.")
