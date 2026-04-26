"""End-to-end smoke test: Washerwoman + Poisoner + Ravenkeeper.

Drives a 5-player game through setup, the first night (Poisoner picks
the Washerwoman; Washerwoman gets [false] info), day, the second night
(Ravenkeeper dies and learns a character), and verifies state along the
way.

Runs the engine's night thread and pretends to be the storyteller by
polling ``engine.pending_prompt()`` and posting ``engine.respond()``.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.enums import CharType, DeathCause, Phase
from engine.engine import Engine


def drain_prompts(engine: Engine, scripted: list, timeout: float = 5.0) -> None:
    """Answer prompts until the night thread finishes.

    ``scripted`` is a list of (matcher_dict, response) tuples. The
    matcher_dict is checked against the pending prompt's meta — every
    key/value in matcher_dict must appear in the prompt's meta.
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
    e = Engine()
    # Five players around the table.
    alice = e.add_seat("Alice")    # id 1
    bob   = e.add_seat("Bob")      # id 2
    cara  = e.add_seat("Cara")     # id 3
    dan   = e.add_seat("Dan")      # id 4
    eve   = e.add_seat("Eve")      # id 5

    # Give them characters: 3 TF, 0 OUTSIDER, 1 MINION, 1 DEMON for 5 players.
    e.assign_character(alice.id, "Washerwoman")
    e.assign_character(bob.id,   "Ravenkeeper")
    e.assign_character(cara.id,  "Soldier")     # stub
    e.assign_character(dan.id,   "Poisoner")
    e.assign_character(eve.id,   "Imp")         # stub
    return e


def test_first_night_with_poisoner_and_washerwoman() -> None:
    e = make_game()
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    e.start_night()
    # Action order on night 1:
    #   Poisoner (10), Washerwoman (30), [Imp doesn't act first night]
    # Poisoner script: wakeup ack, pick Alice (Washerwoman) to poison.
    # Washerwoman script: wakeup ack, pick TF (Ravenkeeper), pick two
    # players (Bob and Cara), info ack.
    drain_prompts(e, [
        # ---- Poisoner ----
        ({"character": "Poisoner", "step": "select_player"},   1),     # poison Alice
        # ---- Washerwoman ----
        ({"character": "Washerwoman", "step": "select_character"},
                                                                "Ravenkeeper"),
        ({"character": "Washerwoman", "step": "select_players"},
                                                                [2, 3]),
        ({"character": "Washerwoman", "step": "information"},  None),
    ])

    # Alice should be poisoned now.
    assert e.get_player(1).poisoned is True
    assert e.get_player(1).has_ability is False  # poisoned -> no real ability

    deaths = e.advance_to_day()
    assert deaths == []
    assert e.phase is Phase.DAY


def test_second_night_with_ravenkeeper_dying() -> None:
    e = make_game()
    e.start_game()

    # First night — keep it minimal: Poisoner poisons Cara (Soldier),
    # Washerwoman is sober so the new flow auto-derives the right
    # player (Bob = Ravenkeeper) and only asks for the wrong one.
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"},   3),
        ({"character": "Washerwoman", "step": "select_character"}, "Ravenkeeper"),
        # Sober WW path: storyteller picks the WRONG player only.
        # Eligibles exclude Alice (self) and Bob (right player).
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"},  None),
    ])
    e.advance_to_day()

    # Day passes, transition to night 2.
    e.advance_to_night()
    assert e.phase is Phase.NIGHT
    assert e.night_number == 2

    e.start_night()
    # Action order on night 2:
    #   Poisoner (10), Imp (25), Ravenkeeper (45).
    # The Imp now has a real ability — it picks Bob (the Ravenkeeper)
    # to kill. That triggers the Ravenkeeper's death, which arms its
    # own night ability.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"},   3),     # poison Cara again
        # Imp picks the Ravenkeeper (Bob, id 2).
        ({"character": "Imp",      "step": "select_target"},   2),
        # Ravenkeeper triggers because they died this night. Sober +
        # healthy at time of death → no ST select_shown_character
        # prompt; the engine uses the target's actual character.
        ({"character": "Ravenkeeper", "step": "select_player"}, 5),    # pick Eve
        ({"character": "Ravenkeeper", "step": "information"},  None),
    ])

    # Bob should now be dead from the Imp's kill.
    assert e.get_player(2).dead


if __name__ == "__main__":
    test_first_night_with_poisoner_and_washerwoman()
    print("test 1 passed.")
    test_second_night_with_ravenkeeper_dying()
    print("test 2 passed.")
    print("All smoke tests passed.")
