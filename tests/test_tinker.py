"""Integration tests for the Tinker outsider.

The Tinker has no nightly action and no information for the
storyteller to set:

    "You might die at any time."

Instead the Storyteller may, at any time (day or night), fire the
Tinker's ability — the engine surfaces this through the standard
"Use ability" path with ``daytime_ability_active_at_night = True``.
Hitting the button kills the Tinker via ``Engine.kill`` with
``cause=DeathCause.ABILITY`` and no ``force=True``, so every standard
protection (Tea Lady neighbour, Innkeeper SAFE, Sailor sober immunity,
Mayor redirect, Fool first-death, etc.) still fires — exactly as the
wiki rule "The Tinker cannot die from their ability while protected
from death, as normal" requires.

We exercise:

* The Tinker's snapshot advertises the at-night ability flag so the
  side-panel "Use ability" button is enabled day or night.
* Firing the ability during the day kills the Tinker immediately.
* Firing the ability during the night kills the Tinker and lands the
  death in ``pending_night_deaths`` so it is announced at dawn.
* The standard Tea Lady protection still cancels the Tinker's death
  when both Tea Lady neighbours (one of which is the Tinker) are good.
* Firing the ability when the Tinker is already dead is a no-op.
* Drunk / poisoned has no effect — the ability still works.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import DeathCause, Phase


def _drain_night(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 5.0,
) -> None:
    """Drive the night-thread prompt loop until the night order finishes.

    Mirrors the helper in ``test_bmr_new_characters.py``. Returns once
    the night thread exits; phase is still NIGHT until the caller
    advances explicitly.
    """
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


def _basic_setup() -> Engine:
    """Five-seat board: Tinker / Mayor / Soldier / Poisoner / Imp.

    Tinker is at seat 0, Imp at seat 4.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Tinker  (good)
    b = e.add_seat("Bob")      # 2 — Mayor   (good)
    c = e.add_seat("Cara")     # 3 — Soldier (good)
    d = e.add_seat("Dan")      # 4 — Poisoner (evil)
    f = e.add_seat("Eve")      # 5 — Imp     (evil)
    e.assign_character(a.id, "Tinker")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    e.start_game()
    return e


def test_tinker_snapshot_advertises_at_night_ability() -> None:
    """The side panel needs ``daytime_ability_active_at_night`` true so
    the 'Use ability' button stays enabled during the night."""
    e = _basic_setup()
    snap = e.get_player(1).snapshot()
    assert snap["character"] == "Tinker"
    assert snap["has_daytime_ability"] is True, (
        "Tinker has a daytime_ability override — snapshot must say so."
    )
    assert snap["daytime_ability_active_at_night"] is True, (
        "Tinker's ability fires day or night."
    )
    assert snap["daytime_ability_active_when_dead"] is False, (
        "Once dead, the Tinker's ability is no longer relevant."
    )
    assert snap["once_per_game"] is False, (
        "The Storyteller may revisit the choice; the slot is implicit "
        "in 'is the Tinker still alive?'."
    )


def test_tinker_kill_during_day() -> None:
    """Hitting the ability during the day kills the Tinker immediately."""
    e = _basic_setup()
    e.start_night()
    _drain_night(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    assert e.phase is Phase.DAY
    assert e.get_player(1).alive

    e.use_daytime_ability(1)

    assert e.get_player(1).dead, (
        "Tinker dies when the Storyteller fires the ability during day."
    )
    assert e.get_player(1).death_cause is DeathCause.ABILITY, (
        "Tinker death is attributed to their own ability."
    )


def test_tinker_kill_during_night_lands_in_pending_deaths() -> None:
    """At night the Tinker dies and is added to ``pending_night_deaths``
    so the dawn announcement rolls the death into the night kills."""
    e = _basic_setup()
    e.start_night()
    _drain_night(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    # We're still in NIGHT phase (just past the night order) — exactly
    # the moment the Storyteller might decide to off the Tinker.
    assert e.phase is Phase.FIRST_NIGHT or e.phase is Phase.NIGHT
    assert e.get_player(1).alive

    e.use_daytime_ability(1)

    assert e.get_player(1).dead, "Tinker dies on Storyteller's whim at night."
    pending_ids = [p.id for p in e.pending_night_deaths]
    assert 1 in pending_ids, (
        "Night deaths must land in pending_night_deaths so dawn "
        "announces them with the rest of the night's casualties."
    )

    # Walk dawn — pending deaths are drained and the night-deaths slot
    # clears.
    e.advance_to_day()
    assert e.phase is Phase.DAY
    assert e.get_player(1).dead


def test_tinker_kill_blocked_while_protected_by_tea_lady() -> None:
    """"The Tinker cannot die from their ability while protected from
    death, as normal." A Tea Lady seated between two good players
    (Tinker is one of them, Mayor is the other) cancels the kill via
    her standard PRE_DEATH reaction. The Tinker stays alive.
    """
    e = Engine()
    a = e.add_seat("Alice")    # seat 0 — Tea Lady
    b = e.add_seat("Bob")      # seat 1 — Tinker  (TL CW neighbour)
    c = e.add_seat("Cara")     # seat 2 — Poisoner (evil)
    d = e.add_seat("Dan")      # seat 3 — Imp      (evil)
    f = e.add_seat("Eve")      # seat 4 — Mayor    (TL CCW neighbour)

    e.assign_character(a.id, "Tea Lady")
    e.assign_character(b.id, "Tinker")
    e.assign_character(c.id, "Poisoner")
    e.assign_character(d.id, "Imp")
    e.assign_character(f.id, "Mayor")
    e.start_game()
    e.start_night()
    _drain_night(e, [
        ({"character": "Poisoner", "step": "select_player"}, 3),  # poison Imp (no-op)
    ])
    e.advance_to_day()
    assert e.phase is Phase.DAY
    tinker_id = b.id
    assert e.get_player(tinker_id).alive

    e.use_daytime_ability(tinker_id)

    assert e.get_player(tinker_id).alive, (
        "Tea Lady's neighbour-protection cancels the Tinker's "
        "ability-kill, exactly per the wiki rule."
    )


def test_tinker_kill_when_already_dead_is_a_noop() -> None:
    """Once the Tinker is dead, firing the ability again is harmless.

    The Storyteller's button on the side panel will already be
    disabled (snapshot reports ``alive=False`` and
    ``daytime_ability_active_when_dead=False``), but if it's invoked
    anyway via the engine API, the engine should refuse.
    """
    e = _basic_setup()
    e.start_night()
    _drain_night(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    e.use_daytime_ability(1)
    assert e.get_player(1).dead

    # Engine refuses a second fire while the seat is dead.
    raised = False
    try:
        e.use_daytime_ability(1)
    except RuntimeError:
        raised = True
    assert raised, "Dead Tinker's ability button must not re-fire."


def test_poisoned_tinker_can_still_be_killed_by_the_storyteller() -> None:
    """The Tinker has no ``has_ability`` dependency — drunk or poisoned
    has no effect on whether the Storyteller can kill them."""
    e = _basic_setup()
    e.start_night()
    _drain_night(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # poison Tinker
    ])
    e.advance_to_day()
    assert e.get_player(1).poisoned is True
    assert e.get_player(1).alive

    e.use_daytime_ability(1)

    assert e.get_player(1).dead, (
        "Poisoned Tinker can still be killed by the Storyteller."
    )
