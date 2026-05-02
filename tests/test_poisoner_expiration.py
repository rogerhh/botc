"""Tests for the Poisoner's poison expiration at dusk.

Per project rule, persistent effects (poison, drunk-from-Drunk, ...) are
re-evaluated at the start of each dawn and each dusk. The Poisoner's
"tonight and tomorrow day" duration ends at the next dusk. The
Storyteller-facing bug this guards against: if the Poisoner dies
mid-day, their previous target should still be unpoisoned at the next
dusk; without the recheck pass, the poison would persist forever (the
Poisoner won't act again to clear it).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.enums import DeathCause, Phase
from engine.engine import Engine


def drain_prompts(engine: Engine, scripted: list, timeout: float = 5.0) -> None:
    """Answer prompts until the night thread finishes (copied from smoke test)."""
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
                    f"expected meta[{k!r}]={v!r}, got meta={p.meta}, text={p.text!r}"
                )
        engine.respond(p.id, response)
        answered += 1
        time.sleep(0.01)
    if answered != len(scripted):
        raise AssertionError(
            f"Night ended with {answered} answered, expected {len(scripted)}."
        )


def make_game() -> Engine:
    """5-player game with a Poisoner. Same shape as the smoke test so we
    don't need to retype Washerwoman/Ravenkeeper plumbing.
    """
    e = Engine()
    e.add_seat("Alice")    # id 1 — Washerwoman
    e.add_seat("Bob")      # id 2 — Ravenkeeper
    e.add_seat("Cara")     # id 3 — Soldier
    e.add_seat("Dan")      # id 4 — Poisoner
    e.add_seat("Eve")      # id 5 — Imp
    e.assign_character(1, "Washerwoman")
    e.assign_character(2, "Ravenkeeper")
    e.assign_character(3, "Soldier")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    return e


def _run_first_night_poisoning(e: Engine, target_id: int) -> None:
    """Drive the first night with the Poisoner picking ``target_id``.

    The Washerwoman is sober (not yet poisoned) so she gets the standard
    first-night flow. We pick characters/players that don't really matter
    for this test — only the Poisoner pick + the resulting state matters.
    """
    e.start_game()
    e.start_night()
    # Order: Poisoner (10), Washerwoman (30). Imp doesn't act on night 1.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, target_id),
        ({"character": "Washerwoman", "step": "select_character"}, "Ravenkeeper"),
        # If Washerwoman is sober, the engine asks for the wrong player only
        # (right player is auto-derived). If she's poisoned, both seen
        # players are picked by the storyteller. We only run the sober
        # path here (target_id != Alice's id 1).
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"}, None),
    ])


def test_poison_clears_at_dusk_when_poisoner_alive() -> None:
    """Poison applied on Night 1 expires at Dusk Day 1 by natural duration.

    Per the rulebook: "...poisoned tonight and tomorrow day." The
    duration ends at the next dusk regardless of whether the Poisoner
    is still around to maintain it.
    """
    e = make_game()
    # Poison Cara (id 3) so the Washerwoman path stays sober.
    _run_first_night_poisoning(e, target_id=3)

    e.advance_to_day()
    assert e.phase is Phase.DAY
    # Mid-day: Cara is still poisoned.
    assert e.get_player(3).poisoned is True

    # Dusk into Night 2 — the poison should clear here, BEFORE the
    # Poisoner gets a chance to act on Night 2.
    e.advance_to_night()
    assert e.phase is Phase.NIGHT
    assert e.get_player(3).poisoned is False, (
        "Cara should be unpoisoned at dusk per the Poisoner's natural "
        "duration ('tonight and tomorrow day')."
    )


def test_poison_clears_when_poisoner_dies() -> None:
    """When the Poisoner dies, their poison clears immediately.

    The POISONED token visibility and the ``Player.poisoned`` flag
    must stay in sync — both are driven by ``Poisoner._last_target``
    plus that target's flag, and a dead Poisoner can no longer
    maintain the poison. Without this reactive cleanup the flag would
    linger after the token disappeared.
    """
    e = make_game()
    # Poison Cara (id 3).
    _run_first_night_poisoning(e, target_id=3)

    # The storyteller kills the Poisoner overnight (e.g. demon-killed,
    # storyteller-arbitrated death — doesn't matter how).
    assert e.get_player(3).poisoned is True
    e.kill(4, cause=DeathCause.DEMON_KILL)
    assert e.get_player(4).dead
    # Immediate: the DEATH reaction on the Poisoner has already cleared
    # Cara's poison. No advance_to_day required.
    assert e.get_player(3).poisoned is False, (
        "Cara should be unpoisoned the moment the Poisoner dies — the "
        "DEATH event reaction must clear ``_last_target.poisoned`` so "
        "the flag and the POISONED token stay in sync."
    )


def test_poison_clears_immediately_when_poisoner_killed_mid_day() -> None:
    """Mid-day: Poisoner is killed (e.g. executed) → poison clears immediately."""
    e = make_game()
    _run_first_night_poisoning(e, target_id=3)
    e.advance_to_day()

    # Cara is poisoned at the start of the day.
    assert e.get_player(3).poisoned is True

    # Poisoner is killed during the day. The DEATH reaction fires on
    # the Poisoner's own seat → poison clears immediately, no need to
    # wait for dusk.
    e.kill(4, cause=DeathCause.STORYTELLER)
    assert e.get_player(3).poisoned is False, (
        "Cara should be unpoisoned the moment the Poisoner is killed "
        "mid-day — both the flag and the token are gated on the same "
        "underlying state."
    )


def test_poisoner_retarget_still_works_on_night_two() -> None:
    """Existing behaviour: a living Poisoner can pick a new target on Night 2.

    The dusk/dawn recheck shouldn't interfere with the Poisoner's
    normal flow when they're alive.
    """
    e = make_game()
    _run_first_night_poisoning(e, target_id=3)
    e.advance_to_day()
    e.advance_to_night()
    # Cara cleared at dusk:
    assert e.get_player(3).poisoned is False

    e.start_night()
    # Action order on night 2: Poisoner (10), Imp (25), Ravenkeeper
    # (45) — Ravenkeeper only acts on death and Cara (Soldier) is
    # demon-immune, so we have the Imp kill the Poisoner himself
    # (Dan, id 4). This also exercises the "Poisoner dies on the
    # same night they targeted" path: Alice is poisoned mid-night,
    # the Poisoner dies, and the dawn recheck must clear Alice's
    # poison (the Poisoner is dead and can't maintain it).
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # poison Alice
        ({"character": "Imp",      "step": "select_target"},  4),  # kills Dan
    ])
    # Dan is dead. The Imp's kill dispatched a DEATH event, which the
    # Poisoner's own seat reacted to: the poison on Alice (set
    # earlier in the same night) clears immediately.
    assert e.get_player(4).dead
    assert e.get_player(1).poisoned is False, (
        "Alice should be unpoisoned the moment the Poisoner dies — "
        "even mid-night, the cleanup is reactive."
    )

    # Sanity: dawn still completes cleanly with no leftover poison.
    e.advance_to_day()
    assert e.get_player(1).poisoned is False


def _poisoner_token_seats(e: Engine) -> list:
    """Return the player ids the Poisoner's POISONED reminder token is on.

    Post-Layer-2 the source of truth is the engine effect registry —
    walk active ``PoisonerPoisonEffect``s sourced by the Poisoner.
    """
    from engine.characters.poisoner import PoisonerPoisonEffect
    poisoner = e.get_player(4).character
    return [
        tgt
        for eff in e.effects_sourced_by(poisoner)
        if isinstance(eff, PoisonerPoisonEffect) and eff.is_active
        for tgt in eff.targets
    ]


def test_token_and_flag_clear_together_on_poisoner_death() -> None:
    """The POISONED token and the ``poisoned`` flag must always agree.

    Pre-fix: the token was hidden when ``has_ability`` flipped to
    False, but the flag persisted on the target seat. Post-fix:
    both are driven by ``_last_target.poisoned``, and the death of
    the Poisoner clears that one piece of state, so the token and
    the flag disappear together.
    """
    e = make_game()
    _run_first_night_poisoning(e, target_id=3)
    # While the Poisoner is alive and Cara is poisoned, the token is
    # placed on Cara's seat.
    assert e.get_player(3).poisoned is True
    assert _poisoner_token_seats(e) == [3]

    # Poisoner dies. Token and flag both vanish together.
    e.kill(4, cause=DeathCause.STORYTELLER)
    assert e.get_player(3).poisoned is False
    assert _poisoner_token_seats(e) == []


def test_token_and_flag_clear_together_when_poisoner_drunk() -> None:
    """When the Poisoner becomes drunk, both the token and the flag clear.

    The ``has_ability`` gate used to hide the token while leaving
    the flag set — the source of the desync the user reported. Now
    the DRUNK reaction clears the flag, and the token follows
    naturally.
    """
    e = make_game()
    _run_first_night_poisoning(e, target_id=3)
    assert e.get_player(3).poisoned is True
    assert _poisoner_token_seats(e) == [3]

    # Storyteller marks the Poisoner drunk.
    e.make_drunk(4)
    assert e.get_player(3).poisoned is False, (
        "Cara should be unpoisoned the moment the Poisoner becomes "
        "drunk — the DRUNK reaction clears the cleanup target."
    )
    assert _poisoner_token_seats(e) == []


def test_token_and_flag_clear_together_when_poisoner_poisoned() -> None:
    """When the Poisoner is themselves poisoned, both views clear."""
    e = make_game()
    _run_first_night_poisoning(e, target_id=3)
    assert e.get_player(3).poisoned is True
    assert _poisoner_token_seats(e) == [3]

    e.poison(4)
    assert e.get_player(3).poisoned is False
    assert _poisoner_token_seats(e) == []


def test_poison_clears_when_poisoner_changes_character() -> None:
    """Mid-game role swap → Poisoner instance is discarded → poison must clear.

    The canonical case is the Scarlet Woman promoting on Demon
    death: a Minion's Character class is swapped to the Demon's via
    ``Engine.change_character``, which builds a fresh instance and
    discards the old one. Without the CHARACTER_CHANGE pre-hook,
    the only handle on ``_last_target`` is gone with the discarded
    Poisoner instance — and the target's ``poisoned`` flag would
    leak forever.

    We don't need a full Scarlet-Woman scenario to test this; we
    just exercise ``Engine.change_character`` directly. The
    CHARACTER_CHANGE event fires before the swap, the OLD Poisoner
    instance reacts and clears its target.
    """
    e = make_game()
    _run_first_night_poisoning(e, target_id=3)
    assert e.get_player(3).poisoned is True

    # Storyteller-arbitrated role change. (In production the SW's
    # DEATH-reaction promotion path takes this code path.)
    e.change_character(4, "Imp")
    assert e.get_player(3).poisoned is False, (
        "Cara should be unpoisoned the moment the Poisoner is "
        "swapped to a different role — the CHARACTER_CHANGE event "
        "lets the outgoing Poisoner instance clean up before being "
        "discarded."
    )


if __name__ == "__main__":
    test_poison_clears_at_dusk_when_poisoner_alive()
    print("test 1 passed.")
    test_poison_clears_when_poisoner_dies()
    print("test 2 passed.")
    test_poison_clears_immediately_when_poisoner_killed_mid_day()
    print("test 3 passed.")
    test_poisoner_retarget_still_works_on_night_two()
    print("test 4 passed.")
    test_token_and_flag_clear_together_on_poisoner_death()
    print("test 5 passed.")
    test_token_and_flag_clear_together_when_poisoner_drunk()
    print("test 6 passed.")
    test_token_and_flag_clear_together_when_poisoner_poisoned()
    print("test 7 passed.")
    test_poison_clears_when_poisoner_changes_character()
    print("test 8 passed.")
    print("All Poisoner-expiration tests passed.")
