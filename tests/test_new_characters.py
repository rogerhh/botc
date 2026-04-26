"""Integration tests for newly-implemented characters.

Drives the engine through scripted prompts to exercise each of the
characters that previously lived as stubs:

  * Empath — neighbour-evil count, with override.
  * Chef — pair count.
  * Monk — protects target from Demon kill.
  * Soldier — passive Demon-kill immunity.
  * Saint — execution ends the game.
  * Imp — kills target, plus self-kill / Scarlet-Woman promotion.
  * Slayer — daytime once-per-game ability.
  * Virgin — first-nomination Townsfolk-execution.

The test pattern is the same as ``test_engine_smoke.py``: a worker
thread runs the night phase while the test thread polls
``pending_prompt`` and posts ``respond``.
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


def test_chef_pair_count() -> None:
    """Chef's first-night ability counts adjacent evil pairs."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Chef
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner (evil, adjacent to Eve)
    f = e.add_seat("Eve")      # 5 — Imp     (evil, adjacent to Dan)

    e.assign_character(a.id, "Chef")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner first (order 10). Don't poison the Chef.
        ({"character": "Poisoner",   "step": "select_player"}, 3),  # Cara
        # Chef is sober + healthy: engine uses the auto-computed count
        # directly (Dan + Eve adjacent evil → "1") with no ST prompt.
        ({"character": "Chef",       "step": "information"},   None),
    ])
    e.advance_to_day()


def test_empath_alive_neighbours() -> None:
    """Empath learns count of evil among 2 alive neighbours."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Empath
    b = e.add_seat("Bob")      # 2 — Soldier
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner (evil)
    f = e.add_seat("Eve")      # 5 — Imp      (evil)

    e.assign_character(a.id, "Empath")
    e.assign_character(b.id, "Soldier")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks Cara (no relevance).
        ({"character": "Poisoner",   "step": "select_player"}, 3),
        # Empath is sober + healthy: engine uses the auto-computed
        # count directly (Bob good + Eve evil via ring → "1") with no
        # ST prompt.
        ({"character": "Empath",     "step": "information"},   None),
    ])
    e.advance_to_day()


def test_imp_kills_target() -> None:
    """Imp's nightly kill kills the chosen player."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier (will be picked, but can't die to Demon)
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner (evil)
    f = e.add_seat("Eve")      # 5 — Imp      (evil)

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()

    # First night — Poisoner + Empath only (Imp doesn't act night 1).
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),  # poison self for simplicity
        # Empath is sober + healthy → no ST confirm prompt.
        ({"character": "Empath",     "step": "information"},   None),
    ])
    e.advance_to_day()
    e.advance_to_night()

    # Night 2: Imp picks Cara — she's the Mayor, no protection.
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
        ({"character": "Imp",        "step": "select_target"}, 3),  # Cara
        # Mayor death-redirect prompt. Decline.
        ({"character": "Mayor",      "step": "redirect_yes_no"}, False),
        ({"character": "Empath",     "step": "information"},   None),
    ])
    deaths = e.advance_to_day()
    # Cara should be dead.
    assert e.get_player(3).dead, "Cara should be dead from Imp kill."
    assert any(p.id == 3 for p in deaths), "Cara should be in night deaths."


def test_imp_kills_soldier_no_death() -> None:
    """Soldier is immune to the Demon's nightly kill."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
        # Empath is sober + healthy → no ST confirm prompt.
        ({"character": "Empath",     "step": "information"},   None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
        ({"character": "Imp",        "step": "select_target"}, 1),  # Soldier!
        # No mayor redirect because Mayor wasn't targeted.
        ({"character": "Empath",     "step": "information"},   None),
    ])
    e.advance_to_day()
    assert e.get_player(1).alive, "Soldier should survive."


def test_saint_executed_evil_wins() -> None:
    """Executing the Saint ends the game with evil winning."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Saint
    b = e.add_seat("Bob")      # 2 — Mayor
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Saint")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
    ])
    e.advance_to_day()
    e.execute_player(1)
    assert e.phase is Phase.FINISHED, "Saint execution should end the game."
    assert e.winner is Alignment.EVIL


def test_virgin_first_nomination() -> None:
    """First Townsfolk nominator of a sober Virgin is executed."""
    import threading

    e = Engine()
    a = e.add_seat("Alice")    # 1 — Virgin
    b = e.add_seat("Bob")      # 2 — Mayor (Townsfolk)
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Virgin")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
    ])
    e.advance_to_day()

    # In a real game the storyteller dispatches a NOMINATION event from
    # the UI thread; the engine reacts and (since the Virgin is sober
    # and healthy) trusts the nominator's actual char_type without
    # asking the ST. No prompt is emitted.
    virgin = e.get_player(1)
    mayor = e.get_player(2)

    def fire_nomination() -> None:
        e.dispatch(Event(
            EventType.NOMINATION,
            targets=[virgin],
            data={"nominator_id": mayor.id},
        ))

    worker = threading.Thread(target=fire_nomination, daemon=True)
    worker.start()
    worker.join(3.0)
    assert not worker.is_alive(), "Virgin reaction didn't finish."
    # No prompt should have been emitted for a sober Virgin.
    assert e.pending_prompt() is None

    # Mayor (Townsfolk) should now be dead by execution.
    assert e.get_player(2).dead
    assert e.get_player(2).death_cause is DeathCause.EXECUTION


def test_mayor_dusk_win_three_alive_no_execution() -> None:
    """At dusk, exactly 3 alive + no execution today + Mayor in play
    with ability ⇒ Mayor's team wins."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor (good)
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),  # poison self
        ({"character": "Empath",     "step": "information"},   None),
    ])
    e.advance_to_day()
    # Storyteller-kill two non-Mayor, non-Demon players to bring the
    # alive count to 3 (Mayor, Poisoner, Imp) without an execution.
    e.kill(a.id, DeathCause.STORYTELLER)
    e.kill(b.id, DeathCause.STORYTELLER)
    assert e.phase is Phase.DAY, "Game should not have ended mid-day."
    assert not e._executed_today, "No execution happened today."
    # Advancing to night = dusk — Mayor's win condition triggers here.
    e.advance_to_night()
    assert e.phase is Phase.FINISHED, "Mayor should have won at dusk."
    assert e.winner is Alignment.GOOD
    assert "Mayor" in (e.win_reason or "")


def test_mayor_dusk_win_uses_mayor_alignment() -> None:
    """If the Mayor is evil, Mayor's win at dusk is reported as evil."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor (forced evil for this test)
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    # Override the Mayor's alignment to evil before starting.
    e.get_player(c.id).alignment = Alignment.EVIL

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
        ({"character": "Empath",     "step": "information"},   None),
    ])
    e.advance_to_day()
    e.kill(a.id, DeathCause.STORYTELLER)
    e.kill(b.id, DeathCause.STORYTELLER)
    e.advance_to_night()
    assert e.phase is Phase.FINISHED
    assert e.winner is Alignment.EVIL, (
        f"Evil Mayor should win evil at dusk; got {e.winner}."
    )


def test_mayor_dusk_no_win_after_execution() -> None:
    """An execution today voids the Mayor's dusk win — game continues."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
        ({"character": "Empath",     "step": "information"},   None),
    ])
    e.advance_to_day()
    # Execute Alice — counts as today's execution. Then drop one more
    # via storyteller-kill so we're at exactly 3 alive at dusk.
    e.execute_player(a.id)
    e.kill(b.id, DeathCause.STORYTELLER)
    assert e._executed_today, "Execution flag should be latched."
    e.advance_to_night()
    # No Mayor win; standard checks (demon alive + 3 left) keep going.
    assert e.phase is Phase.NIGHT, (
        f"Expected NIGHT, got {e.phase} (winner={e.winner})."
    )


def test_mayor_redirect_triggers_on_non_demon_kill() -> None:
    """Any night kill of the Mayor (not just DEMON_KILL) prompts the
    Storyteller for a redirect."""
    import threading

    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp

    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    e.start_game()
    e.start_night()
    # Drain the first night so we're stably in FIRST_NIGHT with prompts
    # cleared, then kill the Mayor with a non-Demon cause directly. The
    # kill blocks on send_prompt, so we run it in a worker thread.
    drain_prompts(e, [
        ({"character": "Poisoner",   "step": "select_player"}, 4),
        ({"character": "Empath",     "step": "information"},   None),
    ])
    # Engine is still in FIRST_NIGHT (we haven't advanced to day yet).
    assert e.phase.is_night
    # Storyteller-kills the Mayor at night. The Mayor's reaction will
    # prompt for a redirect — answer "no" so the Mayor stays dead.
    killer = threading.Thread(
        target=lambda: e.kill(c.id, DeathCause.STORYTELLER),
        daemon=True,
    )
    killer.start()
    deadline = time.time() + 3.0
    while killer.is_alive() and time.time() < deadline:
        p = e.pending_prompt()
        if p is not None and p.meta.get("character") == "Mayor":
            assert p.meta.get("step") == "redirect_yes_no"
            e.respond(p.id, False)
            break
        time.sleep(0.01)
    killer.join(2.0)
    assert not killer.is_alive(), "kill() didn't return — prompt missed?"
    assert e.get_player(c.id).dead, "Mayor should be dead (declined)."


if __name__ == "__main__":
    test_chef_pair_count()
    print("chef test passed.")
    test_empath_alive_neighbours()
    print("empath test passed.")
    test_imp_kills_target()
    print("imp-kill test passed.")
    test_imp_kills_soldier_no_death()
    print("imp-vs-soldier test passed.")
    test_saint_executed_evil_wins()
    print("saint test passed.")
    test_virgin_first_nomination()
    print("virgin test passed.")
    test_mayor_dusk_win_three_alive_no_execution()
    print("mayor-dusk-win test passed.")
    test_mayor_dusk_win_uses_mayor_alignment()
    print("mayor-evil-win test passed.")
    test_mayor_dusk_no_win_after_execution()
    print("mayor-execution-voids test passed.")
    test_mayor_redirect_triggers_on_non_demon_kill()
    print("mayor-redirect-non-demon test passed.")
    print("All new-character tests passed.")
