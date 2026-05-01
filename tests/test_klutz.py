"""Integration tests for the Klutz outsider.

The Klutz fires its ability post-death during the day:

    "When you learn that you died, publicly choose 1 alive player:
     if they are evil, your team loses."

We exercise:

* The "Use ability" gate is enabled once the Klutz is dead during day.
* Picking a good player consumes the slot and the game continues.
* Picking an evil player parks an EVIL pending win (good Klutz case),
  which is announced at the next dawn.
* Picking an evil player when the Klutz is *evil* parks a GOOD
  pending win (the rulebook's "strange situation").
* A drunk / poisoned Klutz still consumes the slot but does NOT
  trigger any win.
* The use-ability button is gated to once-per-game (cannot fire
  twice).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Alignment, DeathCause, Phase


def _drain_until_idle(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 5.0,
) -> None:
    """Drive a worker-thread prompt loop to completion.

    Mirrors the helper in ``test_new_characters.py`` but, since
    ``use_daytime_ability`` reuses the night-thread slot, this works
    for daytime worker threads too.
    """
    deadline = time.time() + timeout
    answered = 0
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Worker thread didn't finish; "
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
            f"Worker ended with {answered} answered, expected {len(scripted)}."
        )


def _basic_setup() -> Engine:
    """Five-seat board: Klutz / Mayor / Soldier / Poisoner / Imp."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Klutz   (good)
    b = e.add_seat("Bob")      # 2 — Mayor   (good)
    c = e.add_seat("Cara")     # 3 — Soldier (good)
    d = e.add_seat("Dan")      # 4 — Poisoner (evil)
    f = e.add_seat("Eve")      # 5 — Imp     (evil)
    e.assign_character(a.id, "Klutz")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    e.start_game()
    e.start_night()
    _drain_until_idle(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),  # poison self
    ])
    e.advance_to_day()
    return e


def test_klutz_daytime_button_enabled_when_dead() -> None:
    """The Klutz's snapshot reports the daytime button stays available
    after death — that's how the storyteller side panel knows to keep
    the "Use ability" button enabled."""
    e = _basic_setup()
    klutz = e.get_player(1)
    # Storyteller-kills the Klutz mid-day to trigger the post-death state.
    e.kill(klutz.id, DeathCause.STORYTELLER)
    snap = klutz.snapshot()
    assert snap["alive"] is False
    assert snap["has_daytime_ability"] is True
    assert snap["daytime_ability_active_when_dead"] is True
    assert snap["once_per_game"] is True
    assert snap["once_per_game_used"] is False


def test_klutz_picks_good_player_game_continues() -> None:
    """Klutz picks a good player → slot consumed, no win, game continues."""
    e = _basic_setup()
    klutz = e.get_player(1)
    e.kill(klutz.id, DeathCause.STORYTELLER)
    assert e.phase is Phase.DAY
    assert e.pending_winner is None

    e.use_daytime_ability(klutz.id)
    _drain_until_idle(e, [
        # Pick Bob (Mayor, good).
        ({"character": "Klutz", "step": "select_target"}, 2),
    ])

    assert e.pending_winner is None, (
        "Picking a good player must NOT register a pending win."
    )
    assert e.phase is Phase.DAY
    # The slot should be spent.
    assert e.get_player(klutz.id).once_per_game_used is True


def test_klutz_picks_evil_player_evil_wins_at_dawn() -> None:
    """Good Klutz pointing at an evil player parks evil's win; dawn finalizes."""
    e = _basic_setup()
    klutz = e.get_player(1)
    e.kill(klutz.id, DeathCause.STORYTELLER)

    e.use_daytime_ability(klutz.id)
    _drain_until_idle(e, [
        # Pick Eve (Imp, evil).
        ({"character": "Klutz", "step": "select_target"}, 5),
    ])

    # Pending win is parked at registration time; the day continues.
    assert e.pending_winner is Alignment.EVIL, (
        f"Expected EVIL pending win; got {e.pending_winner}"
    )
    assert e.phase is Phase.DAY
    assert e.winner is None
    assert e.pending_win_reason and "Klutz" in e.pending_win_reason

    # Walk through dusk → no-op night → dawn to finalize.
    e.advance_to_night()
    _drain_until_idle(e, [])  # legacy night with no preset, no abilities run
    e.advance_to_day()
    assert e.phase is Phase.FINISHED
    assert e.winner is Alignment.EVIL
    assert e.win_reason and "Klutz" in e.win_reason


def test_evil_klutz_picks_evil_player_good_wins_at_dawn() -> None:
    """The rulebook's "strange situation": an evil Klutz picking evil
    flips the loss back onto the evil team — good wins."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Klutz (forced evil for this test)
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner (evil)
    f = e.add_seat("Eve")      # 5 — Imp     (evil)

    e.assign_character(a.id, "Klutz")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    # Override the Klutz alignment — outsiders default good.
    e.get_player(a.id).alignment = Alignment.EVIL

    e.start_game()
    e.start_night()
    _drain_until_idle(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
    ])
    e.advance_to_day()

    e.kill(a.id, DeathCause.STORYTELLER)
    e.use_daytime_ability(a.id)
    _drain_until_idle(e, [
        # Evil Klutz also picks an evil player (Eve, Imp).
        ({"character": "Klutz", "step": "select_target"}, 5),
    ])

    assert e.pending_winner is Alignment.GOOD, (
        f"Evil Klutz picking evil should park a GOOD win; "
        f"got {e.pending_winner}"
    )

    # Finalize.
    e.advance_to_night()
    _drain_until_idle(e, [])
    e.advance_to_day()
    assert e.phase is Phase.FINISHED
    assert e.winner is Alignment.GOOD


def test_drunk_klutz_consumes_slot_but_no_win() -> None:
    """A drunk / poisoned Klutz spends the slot but doesn't trigger a loss."""
    e = _basic_setup()
    klutz = e.get_player(1)
    # Pre-poison the Klutz to disable their ability.
    klutz.set_poisoned(True)
    e.kill(klutz.id, DeathCause.STORYTELLER)

    e.use_daytime_ability(klutz.id)
    _drain_until_idle(e, [
        # Even with an evil pick, the ability shouldn't fire.
        ({"character": "Klutz", "step": "select_target"}, 5),
    ])

    assert e.pending_winner is None, (
        "Poisoned Klutz must not register any pending win."
    )
    # Slot still consumed.
    assert e.get_player(klutz.id).once_per_game_used is True


def test_klutz_use_ability_fails_when_already_spent() -> None:
    """Once the Klutz has spent their ability the engine refuses
    a second activation (matching the once-per-game gate)."""
    e = _basic_setup()
    klutz = e.get_player(1)
    e.kill(klutz.id, DeathCause.STORYTELLER)

    e.use_daytime_ability(klutz.id)
    _drain_until_idle(e, [
        ({"character": "Klutz", "step": "select_target"}, 2),
    ])
    assert e.get_player(klutz.id).once_per_game_used is True

    # Second activation: the engine would still spawn a worker thread,
    # but the Klutz's daytime_ability is a no-op once ``self._used``
    # is True. No prompts should be queued.
    e.use_daytime_ability(klutz.id)
    if e._night_thread is not None:
        e._night_thread.join(timeout=2.0)
    assert e.pending_prompt() is None, (
        "Once-spent Klutz must not emit any further prompts."
    )
    assert e.pending_winner is None


def test_klutz_ability_locked_out_after_death_day_passes() -> None:
    """House rule: the Klutz can only use the ability on the same
    night/day they die. If the day they died ends without the
    ability firing, the slot is forfeit and a later attempt is a
    no-op (and the once-per-game flag is set so the UI grays out)."""
    e = _basic_setup()
    klutz = e.get_player(1)

    # Klutz dies during day 1; they DON'T point that day.
    e.kill(klutz.id, DeathCause.STORYTELLER)
    assert e.day_number == 1
    assert klutz.character._death_period == 1
    assert klutz.once_per_game_used is False

    # Day 1 ends without the Klutz firing. The DAY_END dispatch
    # forfeits the slot.
    e.advance_to_night()
    assert klutz.once_per_game_used is True, (
        "Missing the death-day window must forfeit the slot."
    )
    assert klutz.character._used is True

    # Walk to day 2 and make sure firing the ability now is a no-op.
    e.start_night()
    _drain_until_idle(e, [
        # Poisoner self-poisons again on night 2 (sober now since the
        # night-1 self-poison expired at dusk), Imp picks a target.
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Imp picks Dan (Poisoner) — no Mayor redirect, no Soldier
        # protection in the way.
        ({"character": "Imp", "step": "select_target"}, 4),
    ])
    e.advance_to_day()
    assert e.day_number == 2
    assert e.pending_winner is None

    # Try to fire the ability outside the window. The daytime hook
    # spawns a worker but the body short-circuits without prompting.
    e.use_daytime_ability(klutz.id)
    if e._night_thread is not None:
        e._night_thread.join(timeout=2.0)
    assert e.pending_prompt() is None, (
        "Out-of-window Klutz must not emit any prompts."
    )
    assert e.pending_winner is None, (
        "Out-of-window Klutz must not register any pending win."
    )


def test_klutz_killed_at_night_can_point_next_day() -> None:
    """Klutz killed during the night must use the ability on the
    immediately-following day (the day they wake up dead)."""
    e = _basic_setup()  # day 1, Klutz still alive
    klutz = e.get_player(1)
    assert klutz.alive is True

    # Walk to night 2 and have the Imp kill the Klutz there.
    e.advance_to_night()
    e.start_night()
    _drain_until_idle(e, [
        # Poisoner picks self again on night 2 (sober now).
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Imp picks the Klutz.
        ({"character": "Imp", "step": "select_target"}, 1),
    ])

    # During night 2 the death lands; night_number == 2,
    # day_number was still 1 at the moment of death. The death
    # window therefore opens for day 2 (the upcoming day).
    assert klutz.alive is False
    assert klutz.character._death_period == 2

    e.advance_to_day()
    assert e.day_number == 2

    # The Klutz fires their ability on day 2 — within the death
    # window — and the pick (Imp = evil) lands a pending evil win.
    e.use_daytime_ability(klutz.id)
    _drain_until_idle(e, [
        ({"character": "Klutz", "step": "select_target"}, 5),
    ])
    assert e.pending_winner is Alignment.EVIL


if __name__ == "__main__":
    test_klutz_daytime_button_enabled_when_dead()
    print("dead-button-enabled test passed.")
    test_klutz_picks_good_player_game_continues()
    print("good-pick test passed.")
    test_klutz_picks_evil_player_evil_wins_at_dawn()
    print("evil-pick test passed.")
    test_evil_klutz_picks_evil_player_good_wins_at_dawn()
    print("evil-klutz-picks-evil test passed.")
    test_drunk_klutz_consumes_slot_but_no_win()
    print("poisoned-klutz test passed.")
    test_klutz_use_ability_fails_when_already_spent()
    print("once-per-game test passed.")
    test_klutz_ability_locked_out_after_death_day_passes()
    print("locked-out-after-death-day test passed.")
    test_klutz_killed_at_night_can_point_next_day()
    print("night-kill-next-day test passed.")
    print("All Klutz tests passed.")
