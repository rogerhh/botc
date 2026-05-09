"""Win-race tests: which condition fires when multiple could.

Per CLAUDE.md, "the first win condition to fire wins; subsequent
triggers don't overwrite it." This file pins that ordering for the
trickiest interactions.

Includes:
  * Saint executed + Mayor 3-alive same day
  * Saint executed during Mastermind extension
  * Mastermind + Demon-execute → extension activates (no good win)
  * Mastermind + Scarlet Woman promotion → no extension (SW pre-empts)
  * Klutz curse + Demon dead same day (first-fire wins)
"""

from __future__ import annotations

import os
import sys
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


# ---------------------------------------------------------------------------
# Saint × Mayor same day
# ---------------------------------------------------------------------------

def test_saint_executed_locks_in_evil_win_first() -> None:
    """Saint executed first; Mayor's 3-alive window can't win after."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Saint")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),  # self
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    # Execute the Saint.
    e.execute_player(1)
    assert e.pending_winner is Alignment.EVIL

    # Now the engine state has Saint dead but game continues. Even if
    # we kill Empath to bring count to 3 alive (Mayor + Poisoner + Imp),
    # Mayor's win shouldn't pre-empt Saint's pending evil win — first
    # to fire wins.
    e.kill(3, DeathCause.STORYTELLER)
    assert e.pending_winner is Alignment.EVIL, (
        f"Saint's evil-win must hold; pending_winner={e.pending_winner!r}"
    )


# ---------------------------------------------------------------------------
# Mastermind extension scenarios
# ---------------------------------------------------------------------------

def test_mastermind_extension_saint_executed_during_extension() -> None:
    """During Mastermind extension, Saint executed → Saint loss fires.

    Per Saint's reaction, an executed Saint registers an evil-win
    pending — but we already have Mastermind's _extension_active flag
    set. The Saint's reaction fires before Mastermind's EXECUTION
    reaction (they share the EXECUTION event); behavior depends on
    seat order. This test pins the engine's actual outcome.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Saint")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Soldier")
    e.assign_character(5, "Chef")
    e.assign_character(6, "Mastermind")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
    ] if False else [  # no Poisoner in this game
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    # Execute the Imp → extension activates.
    e.execute_player(7)
    assert getattr(e, "_mastermind_extension_active", False), (
        "Extension should activate."
    )
    assert e.pending_winner is None

    # During the extension day, the Saint is also executed.
    e.execute_player(1)
    # First win condition to fire wins. Either Saint's evil win or
    # Mastermind's "good player executed → evil wins" — both register
    # evil wins, so the result should be evil regardless.
    assert e.pending_winner is Alignment.EVIL, (
        f"Saint executed during extension → evil wins. "
        f"pending_winner={e.pending_winner!r}"
    )


def test_mastermind_extension_no_execution_dawn_good_wins() -> None:
    """Extension day with no execution → DAY_END → good wins."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Mayor")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Sage")
    e.assign_character(6, "Mastermind")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    e.execute_player(7)  # Imp → extension active
    assert e._mastermind_extension_active

    # D1 already had an execution (the Imp), so the "extension day"
    # is actually D2. Walk through dusk → N2 → dawn → D2.
    e.advance_to_night()
    e.start_night()
    # Drain whatever prompts fire on the no-demon N2 (likely Empath).
    deadline = time.time() + 3.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("n2 timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()
    assert e.phase is Phase.DAY, "Should be back in day (the extension day)."
    # No execution on D2; advance to dusk → DAY_END → good wins.
    e.advance_to_night()
    assert e.pending_winner is Alignment.GOOD, (
        f"No execution on extension day → good wins; got "
        f"{e.pending_winner!r}"
    )


def test_mastermind_extension_does_not_activate_when_sw_promotes() -> None:
    """Demon executed + SW promotes → no extension (SW pre-empts).

    Per user spec: Mastermind only triggers if no demons are left.
    SW promotion means a new Demon IS in play, so MM should not fire.
    """
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

    e.execute_player(7)  # 7 alive, SW promotes
    assert e.get_player(5).character.name == "Imp", "SW promoted."
    assert e.pending_winner is None, "Game continues — new Demon in play."
    # Per user intent: MM should not activate when a new Demon exists.
    assert not getattr(e, "_mastermind_extension_active", False), (
        "Mastermind must not activate the extension when SW promotes."
    )


# ---------------------------------------------------------------------------
# Klutz × Demon dead
# ---------------------------------------------------------------------------

def test_klutz_curse_evil_first_then_demon_dead_no_overwrite() -> None:
    """Klutz curses an evil player → good loses pending. Then Demon
    dies → good-wins-via-demon-death must NOT overwrite the loss."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Klutz")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Storyteller-kill the Klutz (so they may use the curse).
    e.kill(1, DeathCause.STORYTELLER)
    # Klutz curses an evil player (the Imp, seat 5) — good loses.
    e.use_daytime_ability(1)
    # Drain the prompt.
    deadline = time.time() + 3.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("klutz timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if p.meta.get("character") == "Klutz":
            e.respond(p.id, 5)  # curse Imp
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    # Klutz pointed at evil → good loses pending (per Klutz rule:
    # if pointed at non-good, good team loses).
    assert e.pending_winner is Alignment.EVIL, (
        f"Klutz curse on evil should make good lose; "
        f"pending_winner={e.pending_winner!r}"
    )

    # Now execute the Imp — Demon dies — good would normally win.
    # But the Klutz's evil-win is already pending; engine should NOT
    # overwrite it.
    e.execute_player(5)
    assert e.pending_winner is Alignment.EVIL, (
        f"Klutz's evil-win must not be overwritten by Demon-dead. "
        f"pending_winner={e.pending_winner!r}"
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
