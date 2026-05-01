"""Clockmaker tests.

The Clockmaker (Sects & Violets Townsfolk) reads:

    "You start knowing how many steps from the Demon to its nearest
     Minion."

The reading is a single integer — the smaller of the clockwise and
counter-clockwise walking distances from the Demon to any Minion.
First night only.

These tests cover:

  * Basic distance readings (1, larger).
  * Counter-clockwise shorter than clockwise (and vice versa).
  * Multi-Minion: the *nearest* one wins.
  * Spy near the Demon: the Spy may register as Townsfolk and is
    skipped, so the next-closest registered Minion is the answer.
  * Recluse near the Demon: the Recluse may register as a Minion and
    becomes the nearest-minion target.
  * Drunk / poisoned: the engine pre-picks a wrong default and the
    Storyteller can hit Next or change it.
  * Second-night silence: the ability is first-night-only, so on
    subsequent nights the Clockmaker doesn't act.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine


def drain_prompts(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 5.0,
    captured_meta: List[dict] | None = None,
) -> None:
    """Walk the night thread, matching each pending prompt against
    ``scripted`` and posting the response.

    When ``captured_meta`` is provided, every prompt's meta dict is
    appended to it (a copy) before responding. Tests use this to
    inspect the Clockmaker's reported distance after the fact.
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
        if captured_meta is not None:
            captured_meta.append(dict(p.meta))
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected extra prompt: {p.text!r} meta={p.meta}"
            )
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"Prompt #{answered + 1} did not match: "
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


def _clockmaker_distance(captured: List[dict]) -> int:
    """Return the distance reported by the Clockmaker's information
    prompt. Raises if no Clockmaker information prompt was captured."""
    for meta in captured:
        if (
            meta.get("character") == "Clockmaker"
            and meta.get("step") == "information"
        ):
            d = meta.get("distance")
            if d is not None:
                return int(d)
            raise AssertionError(
                f"Clockmaker information prompt missing 'distance': {meta}"
            )
    raise AssertionError(
        "No Clockmaker information prompt captured. "
        f"Captured prompts: {captured}"
    )


# ---------------------------------------------------------------------------
# Basic readings: clockwise vs. counter-clockwise.
# ---------------------------------------------------------------------------


def test_clockmaker_demon_minion_adjacent_distance_one() -> None:
    """Imp + Poisoner sit next to each other → Clockmaker learns 1."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Clockmaker
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner (Minion)
    f = e.add_seat("Eve")      # 5 — Imp      (Demon, adjacent to Dan)

    e.assign_character(a.id, "Clockmaker")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    captured: List[dict] = []

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks itself for simplicity.
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Sober Clockmaker — engine uses computed distance directly.
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=captured)
    e.advance_to_day()

    assert _clockmaker_distance(captured) == 1


def test_clockmaker_counter_clockwise_shorter() -> None:
    """Demon at seat 1, Minion at seat 5 in a 7-seat ring (CW=4, CCW=3) →
    learns 3."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Imp        (Demon)
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Empath
    f = e.add_seat("Eve")      # 5 — Poisoner   (Minion, CCW=3 from Imp)
    g = e.add_seat("Fay")      # 6 — Saint
    h = e.add_seat("Gus")      # 7 — Clockmaker

    e.assign_character(a.id, "Imp")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Empath")
    e.assign_character(f.id, "Poisoner")
    e.assign_character(g.id, "Saint")
    e.assign_character(h.id, "Clockmaker")

    captured: List[dict] = []

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks itself.
        ({"character": "Poisoner",   "step": "select_player"}, f.id),
        # Empath sober → no prompt to the ST.
        ({"character": "Empath",     "step": "information"},   None),
        # Clockmaker: Imp at seat 1, Poisoner at seat 5. CW distance =
        # 4, CCW distance = 3 → answer is 3.
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=captured)
    e.advance_to_day()

    assert _clockmaker_distance(captured) == 3


def test_clockmaker_picks_nearest_of_multiple_minions() -> None:
    """Two Minions at different distances — the Clockmaker reads the
    smaller."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Imp        (Demon)
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Poisoner   (Minion, dist 2)
    d = e.add_seat("Dan")      # 4 — Soldier
    f = e.add_seat("Eve")      # 5 — Baron      (Minion, dist 3 ccw / 4 cw)
    g = e.add_seat("Fay")      # 6 — Saint
    h = e.add_seat("Gus")      # 7 — Clockmaker

    e.assign_character(a.id, "Imp")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Poisoner")
    e.assign_character(d.id, "Soldier")
    e.assign_character(f.id, "Baron")
    e.assign_character(g.id, "Saint")
    e.assign_character(h.id, "Clockmaker")

    captured: List[dict] = []

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks itself.
        ({"character": "Poisoner",   "step": "select_player"}, c.id),
        # Clockmaker: nearest Minion is Poisoner at distance 2.
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=captured)
    e.advance_to_day()

    assert _clockmaker_distance(captured) == 2


# ---------------------------------------------------------------------------
# Misregistration: Spy and Recluse.
# ---------------------------------------------------------------------------


def test_clockmaker_skips_spy_when_registering_townsfolk() -> None:
    """Spy adjacent to the Imp registers as Townsfolk for the
    Clockmaker's Minion check — so the Spy is *not* counted as a
    Minion and the next-closest real Minion is the answer."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Imp       (Demon)
    b = e.add_seat("Bob")      # 2 — Spy       (Minion, adjacent to Imp;
                               #                  registers as Townsfolk)
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner  (real Minion at distance 3)
    f = e.add_seat("Eve")      # 5 — Soldier
    g = e.add_seat("Fay")      # 6 — Clockmaker

    e.assign_character(a.id, "Imp")
    e.assign_character(b.id, "Spy")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Soldier")
    e.assign_character(g.id, "Clockmaker")

    captured: List[dict] = []

    e.start_game()
    e.start_night()
    # The Spy registration prompt fires for the Clockmaker's checks on
    # the Spy seat. We supply ``Townsfolk`` so the Spy is *not* a
    # Minion; the next-nearest Minion is Poisoner at distance 3.
    drain_prompts(e, [
        # Poisoner picks themselves first.
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Spy's first-night grimoire reveal fires between Poisoner and
        # the Clockmaker.
        ({"character": "Spy",        "step": "grimoire"},        None),
        # Clockmaker's Minion check on the Spy seat — ST picks the
        # ``Townsfolk`` stub so the Spy doesn't pass the Minion test.
        ({"character": "Clockmaker", "step": "spy_registers_as",
          "attribute": "char_type"}, "Townsfolk"),
        # Clockmaker reads distance 3 (Imp -> Spy/skip -> Mayor ->
        # Poisoner clockwise) — CCW from Imp goes Clockmaker (good) ->
        # Soldier (good) -> Poisoner: also distance 3.
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=captured)
    e.advance_to_day()

    assert _clockmaker_distance(captured) == 3


def test_clockmaker_recluse_registers_as_minion() -> None:
    """Recluse adjacent to the Imp registers as a Minion → the
    Clockmaker reads distance 1."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Imp       (Demon)
    b = e.add_seat("Bob")      # 2 — Recluse   (Outsider; registers as Minion)
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Soldier
    f = e.add_seat("Eve")      # 5 — Poisoner  (real Minion, distance 3 ccw)
    g = e.add_seat("Fay")      # 6 — Clockmaker

    e.assign_character(a.id, "Imp")
    e.assign_character(b.id, "Recluse")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Soldier")
    e.assign_character(f.id, "Poisoner")
    e.assign_character(g.id, "Clockmaker")

    captured: List[dict] = []

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks themselves.
        ({"character": "Poisoner",   "step": "select_player"}, f.id),
        # Clockmaker's Demon check on the Recluse — Recluse may register
        # as Demon, but we pick "Recluse" to keep its own role for that
        # check. (The Recluse override fires here because char_type with
        # passes=DEMON has multiple registration outcomes.)
        ({"character": "Clockmaker", "step": "recluse_registers_as",
          "attribute": "char_type"}, "Recluse"),
        # Clockmaker's Minion check on the Recluse — ST picks the
        # ``Minion`` stub so the Recluse counts as a Minion and the
        # nearest-minion distance becomes 1.
        ({"character": "Clockmaker", "step": "recluse_registers_as",
          "attribute": "char_type"}, "Minion"),
        # Clockmaker reads distance 1.
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=captured)
    e.advance_to_day()

    assert _clockmaker_distance(captured) == 1


# ---------------------------------------------------------------------------
# Drunk / poisoned readings.
# ---------------------------------------------------------------------------


def test_clockmaker_poisoned_storyteller_picks_value() -> None:
    """A poisoned Clockmaker is offered every distance in
    ``1..floor(N/2)``; the engine pre-fills a *random wrong* default and
    the Storyteller can change it. We verify the prompt fires with a
    ``due_to_drunk_poison`` flag and that the chosen value is what the
    player learns."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Imp       (Demon)
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner  (Minion)
    f = e.add_seat("Eve")      # 5 — Clockmaker (will be poisoned)

    e.assign_character(a.id, "Imp")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Clockmaker")

    captured: List[dict] = []

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks the Clockmaker — Clockmaker is now poisoned.
        ({"character": "Poisoner",   "step": "select_player"}, f.id),
        # Drunk/poisoned prompt — ST picks "1" deliberately.
        ({"character": "Clockmaker", "step": "select_distance",
          "due_to_drunk_poison": True}, "1"),
        # Information delivered to the player.
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=captured)
    e.advance_to_day()

    assert _clockmaker_distance(captured) == 1


def test_clockmaker_poisoned_default_is_wrong_answer() -> None:
    """The drunk/poisoned prompt's ``meta["default"]`` should differ
    from ``meta["correct"]`` — the engine pre-picks a *wrong* default
    per the project rule."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Imp       (Demon)
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Empath
    f = e.add_seat("Eve")      # 5 — Poisoner  (Minion)
    g = e.add_seat("Fay")      # 6 — Saint
    h = e.add_seat("Gus")      # 7 — Clockmaker (will be poisoned)

    e.assign_character(a.id, "Imp")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Empath")
    e.assign_character(f.id, "Poisoner")
    e.assign_character(g.id, "Saint")
    e.assign_character(h.id, "Clockmaker")

    captured: List[dict] = []

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner poisons the Clockmaker.
        ({"character": "Poisoner",   "step": "select_player"}, h.id),
        # Empath sober — auto info.
        ({"character": "Empath",     "step": "information"},   None),
        # Drunk/poisoned distance prompt fires; we hit Next (use the
        # pre-picked wrong default).
        ({"character": "Clockmaker", "step": "select_distance",
          "due_to_drunk_poison": True}, None),
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=captured)
    e.advance_to_day()

    cm_select = next(
        m for m in captured
        if m.get("character") == "Clockmaker"
        and m.get("step") == "select_distance"
    )
    assert cm_select.get("due_to_drunk_poison") is True
    assert cm_select.get("correct") == "3", (
        f"Sober reading on this 7-seat table is 3 (CW=4, CCW=3); "
        f"got correct={cm_select.get('correct')!r}"
    )
    assert cm_select.get("default") != cm_select.get("correct"), (
        f"Drunk/poisoned default should not equal correct; "
        f"meta={cm_select}"
    )
    # And the value the player ultimately receives is the wrong default.
    assert (
        _clockmaker_distance(captured) == int(cm_select["default"])
    ), (
        f"Player should learn the storyteller-selected wrong default; "
        f"got {captured}"
    )


# ---------------------------------------------------------------------------
# First-night-only.
# ---------------------------------------------------------------------------


def test_clockmaker_silent_on_subsequent_nights() -> None:
    """The Clockmaker's ability is first-night only. On night 2, no
    Clockmaker prompt fires."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Clockmaker
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner  (Minion)
    f = e.add_seat("Eve")      # 5 — Imp       (Demon)

    e.assign_character(a.id, "Clockmaker")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    night1: List[dict] = []
    night2: List[dict] = []

    # First night: Clockmaker fires.
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        ({"character": "Clockmaker", "step": "information"},   None),
    ], captured_meta=night1)
    e.advance_to_day()

    cm_n1 = [m for m in night1 if m.get("character") == "Clockmaker"]
    assert cm_n1, "Expected at least one Clockmaker prompt on night 1."

    # Day -> Night 2.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks themselves.
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Imp picks the Mayor; Mayor's redirect prompt also fires.
        ({"character": "Imp",        "step": "select_target"}, b.id),
        # Mayor death-redirect prompt — decline.
        ({"character": "Mayor",      "step": "redirect_yes_no"}, False),
    ], captured_meta=night2)
    e.advance_to_day()

    cm_n2 = [m for m in night2 if m.get("character") == "Clockmaker"]
    assert not cm_n2, (
        f"Clockmaker should NOT fire on night 2; got prompts: {cm_n2}"
    )
