"""Droisoned info-character sweep.

Each sober info character behaves correctly when its source is poisoned
mid-game (not via Drunk impersonation — that's covered in
test_drunk_perceived.py and test_drunk_as_sage_repro.py). The
Poisoner targets the info character, and the engine should drive the
wrong-default flow documented in CLAUDE.md.

Existing tests already cover Empath (drunk-as), FT (drunk-as),
Sage (drunk-as), Chambermaid (drunk), Clockmaker (poisoned). This
file fills in the gaps:

  * Chef poisoned wrong-default
  * Washerwoman poisoned wrong-default
  * Empath poisoned wrong-default (sober Empath, poisoned by Poisoner)
  * Fortune Teller poisoned wrong-default (with pre-set red herring)
  * Ravenkeeper poisoned + killed → wrong-default character
  * Undertaker poisoned wrong-default after execution
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
                    f"Prompt #{answered+1} did not match: "
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


def test_poisoned_empath_wrong_default() -> None:
    """Poisoned Empath gets wrong-default count prompt with flag."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Empath")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Empath", "step": "select_count",
          "due_to_drunk_poison": True}, "2"),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Poison persists through the day; clears at dusk.
    assert e.get_player(1).poisoned


def test_poisoned_chef_wrong_default() -> None:
    """Poisoned Chef gets wrong-default count prompt with flag."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Chef")
    e.assign_character(2, "Empath")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Chef", "step": "select_count",
          "due_to_drunk_poison": True}, "2"),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()


def test_poisoned_washerwoman_wrong_default() -> None:
    """Poisoned Washerwoman: wrong-default character + select 2 players."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Washerwoman")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Washerwoman", "step": "select_character",
          "due_to_drunk_poison": True}, "Soldier"),
        ({"character": "Washerwoman", "step": "select_players"}, [2, 3]),
        ({"character": "Washerwoman", "step": "information"}, None),
    ])
    e.advance_to_day()


def test_poisoned_fortune_teller_wrong_default() -> None:
    """Poisoned FT: select 2 players, then wrong-default yes/no with flag."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve"):
        e.add_seat(n)
    e.assign_character(1, "Fortune Teller")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Mayor")
    e.assign_character(4, "Poisoner")
    e.assign_character(5, "Imp")
    # Pre-set red herring so the setup_select_red_herring prompt
    # doesn't fire during start_game.
    e.apply_setup_data({"ft_red_herring": "Soldier"})
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),
        ({"character": "Fortune Teller", "step": "select_players"},
         [2, 3]),
        ({"character": "Fortune Teller", "step": "select_yes_no",
          "due_to_drunk_poison": True}, True),
        ({"character": "Fortune Teller", "step": "information"}, None),
    ])
    e.advance_to_day()


def test_poisoned_ravenkeeper_wrong_default_on_death() -> None:
    """Poisoned RK killed by Imp gets wrong-default character pick.

    N2 order: Poisoner(10), Imp(25), Ravenkeeper(45 — wakes only on
    death), Empath(50).
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Ravenkeeper")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Mayor")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # poison RK
        ({"character": "Imp", "step": "select_target"}, 1),       # kill RK
        # RK reaction fires: pick a player to spy on, then wrong-default
        # character (since RK is poisoned).
        ({"character": "Ravenkeeper", "step": "select_player"}, 5),
        ({"character": "Ravenkeeper", "step": "select_shown_character",
          "due_to_drunk_poison": True}, "Soldier"),
        ({"character": "Ravenkeeper", "step": "information"}, None),
        # Empath fires after RK on N2 (order 50 vs RK reactive).
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(1).dead, "Ravenkeeper should be dead."


def test_poisoned_undertaker_wrong_default() -> None:
    """Poisoned Undertaker after a D2 execution: wrong-default character.

    N2 order: Poisoner(10), Imp(25), Empath(50), FT(51), Undertaker(52).
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Undertaker")
    e.assign_character(2, "Soldier")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Mayor")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Imp")
    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),  # self
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.execute_player(5)  # Mayor executed

    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 1),  # Undertaker
        # Imp targets Soldier (immune) so Poisoner stays alive
        # — poison effect persists and Undertaker still poisoned at
        # order 52.
        ({"character": "Imp", "step": "select_target"}, 2),
        ({"character": "Empath", "step": "information"}, None),
        ({"character": "Undertaker", "step": "select_shown_character",
          "due_to_drunk_poison": True}, "Soldier"),
        ({"character": "Undertaker", "step": "information"}, None),
    ])
    e.advance_to_day()


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
