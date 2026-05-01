"""Chambermaid tests.

The Chambermaid (Bad Moon Rising Townsfolk) reads:

    "Each night, choose 2 alive players (not yourself): you learn how
     many woke tonight due to their ability."

The reading is one of {0, 1, 2}: how many of the chosen seats woke
tonight to use their *own* ability. Wakes for Demon Info / Minion Info
(engine-level dispatches with ``source=None``) and cross-player wakes
(another character's ability waking somebody else, e.g. Imp self-kill
promoting a Minion) do NOT count.

These tests cover:

  * Sober reading: 0 / 1 / 2 cases, with and without first-night
    Demon-Info & Minion-Info noise present.
  * Drunk / poisoned: the engine pre-picks a *wrong* count and
    surfaces it to the Storyteller; the ST may change it.
  * Self exclusion + alive-only eligibility on the SelectPlayerPrompt.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.event import Event, EventType


def drain_prompts(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 5.0,
    captured_meta: List[dict] | None = None,
) -> None:
    """Walk the night thread, matching each pending prompt against
    ``scripted`` and posting the response. Optionally captures every
    prompt's meta dict for after-the-fact assertions."""
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


def _chambermaid_count(captured: List[dict]) -> int:
    """Return the count reported by the Chambermaid's information prompt.

    The player phone renders just the explanation sentence (no big
    digit), so we recover the count from the leading number of that
    sentence.
    """
    for meta in captured:
        if (
            meta.get("character") == "Chambermaid"
            and meta.get("step") == "information"
        ):
            tokens = (meta.get("render") or {}).get("tokens") or []
            if not tokens:
                continue
            body = tokens[0].get("body", "")
            # info_text starts with the count: "0 of your..." / "1 of your...".
            head = body.split(" ", 1)[0]
            try:
                return int(head)
            except ValueError:
                continue
    raise AssertionError(
        f"No Chambermaid information prompt captured: {captured}"
    )


# ---------------------------------------------------------------------------
# Wake-up detection — the discriminator that powers the count.
# ---------------------------------------------------------------------------


def test_wake_detector_excludes_engine_dispatched_wakes() -> None:
    """Engine-level wakes (Demon Info, Minion Info) and cross-player
    wakes (one character's ability waking somebody else) must NOT count
    as "woke due to own ability".

    This is the rulebook discriminator the Chambermaid relies on, so
    it's worth a unit test that doesn't go through the night loop.
    """
    e = Engine()
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")

    e.assign_character(a.id, "Chambermaid")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    cm = a.character

    # 1. Engine-dispatched wakes (Demon Info / Minion Info pattern):
    #    source is None → does NOT count.
    e.dispatch(Event(EventType.WAKEUP, source=None, targets=[f]))
    e.dispatch(Event(EventType.WAKEUP, source=None, targets=[d]))

    # 2. Cross-player wake (Imp self-kill style: source=Imp, target is
    #    a different player). Does NOT count.
    e.dispatch(Event(EventType.WAKEUP, source=f.character, targets=[d]))

    # 3. Own-ability wake (the canonical pattern every concrete
    #    character uses): source.player == target → DOES count.
    e.dispatch(Event(EventType.WAKEUP, source=b.character, targets=[b]))
    e.dispatch(Event(EventType.WAKEUP, source=d.character, targets=[d]))

    assert cm._woke_tonight == {b.id, d.id}, (
        f"Expected only own-ability wakes to be tracked, "
        f"got {cm._woke_tonight}"
    )


def test_wake_tracker_resets_at_night_start() -> None:
    """The per-night set is cleared on NIGHT_START so each night is a
    clean slate."""
    e = Engine()
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")
    e.assign_character(a.id, "Chambermaid")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    cm = a.character

    # Pretend an earlier night already populated the set.
    cm._woke_tonight = {b.id, d.id}

    e.dispatch(Event(EventType.NIGHT_START))

    assert cm._woke_tonight == set(), (
        f"NIGHT_START should clear the tracker, got {cm._woke_tonight}"
    )


# ---------------------------------------------------------------------------
# End-to-end: nightly ability over the engine night loop.
# ---------------------------------------------------------------------------


def test_chambermaid_other_night_counts_two_wakers() -> None:
    """Pick 2 players who both woke tonight → Chambermaid learns 2.

    Setup: Chambermaid + Empath + Monk + Poisoner + Imp. On night 2
    the Empath, Monk, Poisoner, and Imp all wake to use their own
    abilities. The Chambermaid picks the Empath and the Monk → 2.
    """
    e = Engine()
    a = e.add_seat("Alice")    # Chambermaid
    b = e.add_seat("Bob")      # Empath
    c = e.add_seat("Cara")     # Monk
    d = e.add_seat("Dan")      # Poisoner
    f = e.add_seat("Eve")      # Imp

    e.assign_character(a.id, "Chambermaid")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Monk")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    captured: List[dict] = []

    e.start_game()
    # Night 1: Empath + Poisoner act (Monk and Imp don't on night 1).
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks itself (no real effect — sober/alive doesn't
        # matter for this count test).
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Empath sober on night 1.
        ({"character": "Empath",     "step": "information"},   None),
        # Chambermaid acts last on night 1.
        ({"character": "Chambermaid", "step": "select_players"},
         [b.id, d.id]),
        ({"character": "Chambermaid", "step": "information"},   None),
    ], captured_meta=captured)
    # Night-1 reading: Empath woke, Poisoner woke → 2.
    assert _chambermaid_count(captured) == 2

    e.advance_to_day()
    e.advance_to_night()

    captured2: List[dict] = []
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks themselves.
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Monk picks themselves (allowed targets exclude self in real
        # rules, so pick someone else).
        ({"character": "Monk",       "step": "select_player"}, b.id),
        # Imp kills nobody dangerous — pick Cara.
        ({"character": "Imp",        "step": "select_target"}, c.id),
        # Empath sober.
        ({"character": "Empath",     "step": "information"},   None),
        # Chambermaid picks Empath + Monk → both woke → 2.
        ({"character": "Chambermaid", "step": "select_players"},
         [b.id, c.id]),
        ({"character": "Chambermaid", "step": "information"},   None),
    ], captured_meta=captured2)
    assert _chambermaid_count(captured2) == 2


def test_chambermaid_zero_when_picks_dont_wake() -> None:
    """Pick 2 non-waking characters → Chambermaid learns 0.

    Soldier and Saint have no night actions, so picking them returns 0.
    """
    e = Engine()
    a = e.add_seat("Alice")    # Chambermaid
    b = e.add_seat("Bob")      # Saint  (no night action; safe to ignore)
    c = e.add_seat("Cara")     # Soldier
    d = e.add_seat("Dan")      # Poisoner
    f = e.add_seat("Eve")      # Imp

    e.assign_character(a.id, "Chambermaid")
    e.assign_character(b.id, "Saint")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    captured: List[dict] = []
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Pick the two non-acting players: Saint + Soldier.
        ({"character": "Chambermaid", "step": "select_players"},
         [b.id, c.id]),
        ({"character": "Chambermaid", "step": "information"},   None),
    ], captured_meta=captured)
    assert _chambermaid_count(captured) == 0

    e.advance_to_day()
    e.advance_to_night()

    captured2: List[dict] = []
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Imp picks the Soldier — Soldier protection cancels the kill,
        # so no death event / no redirect prompt fires.
        ({"character": "Imp",        "step": "select_target"}, c.id),
        # Saint + Soldier picked again — neither woke this night.
        ({"character": "Chambermaid", "step": "select_players"},
         [b.id, c.id]),
        ({"character": "Chambermaid", "step": "information"},   None),
    ], captured_meta=captured2)
    assert _chambermaid_count(captured2) == 0


def test_chambermaid_one_when_only_one_picked_woke() -> None:
    """Pick a waker + a non-waker → Chambermaid learns 1."""
    e = Engine()
    a = e.add_seat("Alice")    # Chambermaid
    b = e.add_seat("Bob")      # Saint (no night action)
    c = e.add_seat("Cara")     # Soldier
    d = e.add_seat("Dan")      # Poisoner (acts every night)
    f = e.add_seat("Eve")      # Imp

    e.assign_character(a.id, "Chambermaid")
    e.assign_character(b.id, "Saint")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    captured: List[dict] = []
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, d.id),
        # Saint (didn't wake) + Poisoner (woke) → 1.
        ({"character": "Chambermaid", "step": "select_players"},
         [b.id, d.id]),
        ({"character": "Chambermaid", "step": "information"},   None),
    ], captured_meta=captured)
    assert _chambermaid_count(captured) == 1


# ---------------------------------------------------------------------------
# Drunk / poisoned: pre-picked wrong default surfaced to the Storyteller.
# ---------------------------------------------------------------------------


def test_chambermaid_drunk_prompts_for_wrong_default() -> None:
    """When the Chambermaid is poisoned, the engine pre-picks a wrong
    default count and surfaces it to the ST with a Next button.

    We force-poison the Chambermaid mid-night and verify a select_count
    prompt appears with ``due_to_drunk_poison=True`` and a default that
    differs from the truthful count.
    """
    e = Engine()
    a = e.add_seat("Alice")    # Chambermaid
    b = e.add_seat("Bob")      # Mayor
    c = e.add_seat("Cara")     # Soldier
    d = e.add_seat("Dan")      # Poisoner — will poison the Chambermaid
    f = e.add_seat("Eve")      # Imp

    e.assign_character(a.id, "Chambermaid")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    captured: List[dict] = []
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner poisons Alice (the Chambermaid).
        ({"character": "Poisoner",   "step": "select_player"}, a.id),
        # Chambermaid picks Mayor + Soldier (truthful count = 0).
        ({"character": "Chambermaid", "step": "select_players"},
         [b.id, c.id]),
        # Drunk/poisoned wrong-default prompt — ST sends 2.
        ({"character": "Chambermaid", "step": "select_count",
          "due_to_drunk_poison": True}, "2"),
        # Information prompt to the player; ST hits Next.
        ({"character": "Chambermaid", "step": "information"},   None),
    ], captured_meta=captured)
    # The shown count is 2 (what the ST sent), not the truthful 0.
    assert _chambermaid_count(captured) == 2

    # The wrong-default prompt should also have surfaced ``correct=0``
    # as metadata so the ST can see what they're overriding.
    drunk_prompt = next(
        m for m in captured
        if m.get("character") == "Chambermaid"
        and m.get("step") == "select_count"
    )
    assert drunk_prompt.get("correct") == "0"
    assert drunk_prompt.get("default") in {"1", "2"}  # any wrong value


# ---------------------------------------------------------------------------
# Eligibility: alive non-self only. Skipped entirely when fewer than 2
# valid targets exist.
# ---------------------------------------------------------------------------


def test_chambermaid_select_prompt_excludes_self_and_dead() -> None:
    """Eligible IDs sent to the SelectPlayerPrompt must be alive and
    not the Chambermaid herself.

    Drives a small game where one player dies via execution between
    nights, then asserts the SelectPlayerPrompt's ``eligible_player_ids``
    excludes the Chambermaid and the dead player.
    """
    e = Engine()
    a = e.add_seat("Alice")    # Chambermaid
    b = e.add_seat("Bob")      # Saint  (no special death prompts)
    c = e.add_seat("Cara")     # Soldier
    d = e.add_seat("Dan")      # Poisoner
    f = e.add_seat("Eve")      # Imp

    e.assign_character(a.id, "Chambermaid")
    e.assign_character(b.id, "Saint")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    # Pull the Chambermaid's pending SelectPlayerPrompt directly from
    # the engine so we can read its ``eligible_player_ids``. The meta
    # dict alone doesn't carry the eligibility list — that's a top-
    # level prompt field.
    seen_eligible: List[List[int]] = []

    def _capture_select_eligibility(engine: Engine) -> None:
        p = engine.pending_prompt()
        if p is None:
            return
        if (
            p.meta.get("character") == "Chambermaid"
            and p.meta.get("step") == "select_players"
        ):
            seen_eligible.append(list(getattr(p, "eligible_player_ids", [])))

    # Custom drain that snapshots eligibility before responding.
    def _drain_with_capture(scripted: list) -> None:
        deadline = time.time() + 5.0
        answered = 0
        while e._night_thread and e._night_thread.is_alive():
            if time.time() > deadline:
                raise TimeoutError(f"answered={answered}")
            p = e.pending_prompt()
            if p is None:
                time.sleep(0.01)
                continue
            _capture_select_eligibility(e)
            matcher, response = scripted[answered]
            for k, v in matcher.items():
                if p.meta.get(k) != v:
                    raise AssertionError(
                        f"Prompt #{answered + 1} did not match: "
                        f"expected meta[{k!r}]={v!r}, got {p.meta}"
                    )
            e.respond(p.id, response)
            answered += 1
            time.sleep(0.01)

    e.start_game()
    e.start_night()
    _drain_with_capture([
        ({"character": "Poisoner",    "step": "select_player"}, d.id),
        ({"character": "Chambermaid", "step": "select_players"},
         [b.id, c.id]),
        ({"character": "Chambermaid", "step": "information"},   None),
    ])
    # Night-1 eligibility: alive non-self = {Bob, Cara, Dan, Eve}.
    assert sorted(seen_eligible[-1]) == sorted([b.id, c.id, d.id, f.id])

    # Day 1: nominate + execute Bob (the Saint) — but executing a
    # Saint ends the game. Instead, kill Bob "off-camera" by directly
    # marking him dead via the engine's kill helper so we don't trip
    # any other ability flows.
    e.advance_to_day()
    from engine.enums import DeathCause
    e.kill(b.id, cause=DeathCause.STORYTELLER)
    e.advance_to_night()

    e.start_night()
    _drain_with_capture([
        ({"character": "Poisoner",    "step": "select_player"}, d.id),
        # Imp picks the Soldier — protected, no kill resolves.
        ({"character": "Imp",         "step": "select_target"},  c.id),
        ({"character": "Chambermaid", "step": "select_players"},
         [c.id, d.id]),
        ({"character": "Chambermaid", "step": "information"},   None),
    ])
    # Night-2 eligibility: alive non-self = {Cara, Dan, Eve} (Bob dead).
    assert a.id not in seen_eligible[-1], "Chambermaid in own eligibility"
    assert b.id not in seen_eligible[-1], "Dead Bob in eligibility"
    assert sorted(seen_eligible[-1]) == sorted([c.id, d.id, f.id])


if __name__ == "__main__":
    test_wake_detector_excludes_engine_dispatched_wakes()
    test_wake_tracker_resets_at_night_start()
    test_chambermaid_other_night_counts_two_wakers()
    test_chambermaid_zero_when_picks_dont_wake()
    test_chambermaid_one_when_only_one_picked_woke()
    test_chambermaid_drunk_prompts_for_wrong_default()
    test_chambermaid_select_prompt_excludes_self_and_dead()
    print("All Chambermaid tests passed.")
