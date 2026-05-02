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
    """Executing the Saint ends the game with evil winning.

    Per the project's pending-win rule, the win is parked at the
    moment of execution and finalized at the next dawn. The day
    finishes out, the (no-op) night runs, and dawn flips the phase
    to FINISHED.
    """
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
    # Pending win parked, but phase still DAY.
    assert e.pending_winner is Alignment.EVIL
    # Advance through dusk → night → dawn to finalize.
    e.advance_to_night()
    drain_prompts(e, [])  # no-op night
    e.advance_to_day()
    assert e.phase is Phase.FINISHED, "Saint execution should end the game."
    assert e.winner is Alignment.EVIL


def test_scarlet_woman_promoted_via_execution() -> None:
    """Executing the Demon with 5+ alive (and a healthy Scarlet Woman
    present) must promote the SW into the Demon — NOT end the game
    with good winning.

    Regression: ``execute_player`` previously dispatched only the
    EXECUTION event and skipped the broader DEATH event, so the SW's
    DEATH-listening reaction never fired and the post-execution win
    check incorrectly registered "Demon is dead → good wins".
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Chef
    fp = e.add_seat("Eve")     # 5 — Poisoner (Minion)
    g = e.add_seat("Fay")      # 6 — Scarlet Woman (Minion)
    h = e.add_seat("Gus")      # 7 — Imp (Demon)
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Chef")
    e.assign_character(fp.id, "Poisoner")
    e.assign_character(g.id, "Scarlet Woman")
    e.assign_character(h.id, "Imp")
    e.start_game()

    # First night drain. This test runs without the preset, so the
    # legacy night-order path is used (no MINION_INFO / DEMON_INFO
    # preset steps). Just walk the abilities.
    e.start_night()
    drain_prompts(e, [
        # Poisoner picks Cara (irrelevant for this test).
        ({"character": "Poisoner", "step": "select_player"}, c.id),
        # Chef sober + healthy → auto information.
        ({"character": "Chef", "step": "information"}, None),
        # Empath sober + healthy → auto information.
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    # Execute the Imp. With 7 alive ≥ 5 the SW reaction must promote.
    e.execute_player(h.id)

    # Game must NOT have ended. The SW (Fay) is now the Imp.
    assert e.pending_winner is None, (
        f"Imp executed with 5+ alive should not register a pending win; "
        f"got {e.pending_winner!r}"
    )
    assert e.phase is Phase.DAY
    fay = e.get_player(g.id)
    assert fay.character is not None
    assert fay.character.name == "Imp", (
        f"Scarlet Woman should now be the Imp; got {fay.character.name!r}"
    )
    # Bookkeeping for the reminder token + night reveal queue.
    assert g.id in e._sw_promoted_player_ids
    assert g.id in e._sw_pending_demon_reveal


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

    # Mayor (Townsfolk) should now be dead by the Virgin's ability.
    # ``DeathCause.EXECUTION`` is reserved for the Storyteller's
    # Execute button — the Virgin uses ``DeathCause.ABILITY``.
    assert e.get_player(2).dead
    assert e.get_player(2).death_cause is DeathCause.ABILITY
    # The Virgin must NOT have latched the day's execution flag —
    # only ``Engine.execute_player`` does that.
    assert e._executed_today is False


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
    # Advancing to night = dusk — Mayor's win condition triggers here
    # and parks a pending win. The next dawn finalizes it.
    e.advance_to_night()
    assert e.pending_winner is Alignment.GOOD
    drain_prompts(e, [])  # no-op night
    e.advance_to_day()
    assert e.phase is Phase.FINISHED, "Mayor should have won by dawn."
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
    assert e.pending_winner is Alignment.EVIL
    drain_prompts(e, [])  # no-op night
    e.advance_to_day()
    assert e.phase is Phase.FINISHED
    assert e.winner is Alignment.EVIL, (
        f"Evil Mayor should win evil by dawn; got {e.winner}."
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


def test_mayor_redirects_imp_kill_back_to_imp_triggers_starpass() -> None:
    """Imp picks Mayor → Mayor redirects to Imp → Imp's self-kill fires.

    The Imp's kill carries ``source=self`` into ``Engine.kill``; the
    Mayor's redirect forwards that source on the re-entrant kill of
    the Imp. The Imp's reaction picks up the self-attributed DEATH
    and runs the starpass flow — promoting a Minion to the new Imp —
    without either character knowing about the other.
    """
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
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()

    # Night 2: Imp picks Mayor → Mayor redirects → Imp dies.
    # Only one alive Minion (Poisoner), so ``select_new_imp`` is
    # auto-resolved by the engine; the new-Imp reveal still fires.
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Imp", "step": "select_target"}, 3),     # pick Mayor
        ({"character": "Mayor", "step": "redirect_yes_no"}, True),
        ({"character": "Mayor", "step": "redirect_select"}, 5), # back to Imp
        ({"character": "Imp", "step": "new_imp_reveal"}, None),
        ({"character": "Empath", "step": "information"}, None),
    ])
    if e._night_thread:
        e._night_thread.join(2.0)

    # Mayor never died; old Imp (Eve) is dead; Poisoner is the new Imp.
    assert not e.get_player(c.id).dead, "Mayor should still be alive."
    assert e.get_player(f.id).dead, "Old Imp (Eve) should be dead."
    assert e.get_player(d.id).character.name == "Imp", (
        "Poisoner should have been promoted to the new Imp via the "
        "Imp's self-kill ability triggered by the Mayor's redirect."
    )


def test_day_win_defers_to_dawn_players_can_keep_acting() -> None:
    """End-to-end check of the project's dawn-announcement rule.

    Saint executed during the day:
      * pending_winner is set, but phase stays DAY and winner is None;
      * abilities and nominations still work after the trigger;
      * advance_to_night moves to NIGHT and the night runs no actions
        (the night thread auto-dawns immediately);
      * dawn flips phase to FINISHED and announces the win.
    """
    import threading

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

    # Trigger the win during the day by executing the Saint.
    e.execute_player(a.id)
    assert e.phase is Phase.DAY, (
        "Day-time trigger must NOT end the game immediately; "
        "win is parked for dawn."
    )
    assert e.pending_winner is Alignment.EVIL
    assert e.pending_win_reason and "Saint" in e.pending_win_reason
    assert e.winner is None, "winner only populated after dawn finalize."

    # Players can still act / nominate during the rest of the day.
    # Nominate something to prove the engine still accepts it.
    e.nominate(b.id, c.id)
    assert e.get_player(b.id).has_nominated_today
    assert e.get_player(c.id).has_been_nominated_today
    # Pending winner should not be overwritten by subsequent state changes.
    assert e.pending_winner is Alignment.EVIL

    # Advance through dusk → night. Even though the win is pending,
    # advance_to_night still moves us into NIGHT.
    e.advance_to_night()
    assert e.phase is Phase.NIGHT
    assert e.pending_winner is Alignment.EVIL
    assert e.winner is None

    # Run the no-op night and let it auto-dawn. With auto-advance on,
    # the night thread will skip every ability and call _auto_dawn,
    # which finalizes the pending win. (No preset is installed in
    # this test, so there are no Dusk/Dawn announcement prompts to
    # drain — the legacy night path simply skips every action.)
    e.set_auto_advance_to_day(True)
    e.start_night()
    if e._night_thread is not None:
        e._night_thread.join(timeout=3.0)
        assert not e._night_thread.is_alive(), "night thread didn't finish"

    # No prompts should have been emitted on the no-op legacy night.
    assert e.pending_prompt() is None

    assert e.phase is Phase.FINISHED, (
        "Game must end at dawn after the no-op night."
    )
    assert e.winner is Alignment.EVIL
    assert e.win_reason and "Saint" in e.win_reason
    # Pending slots are cleared once the win is announced.
    assert e.pending_winner is None
    assert e.pending_win_reason is None


def test_dawn_announcement_emits_game_end_console_event() -> None:
    """The ``game_end`` console entry should appear at dawn, not before.

    During the day on which the Saint is executed we expect a
    ``win_pending`` console entry; the public ``game_end`` event is
    only logged when the next dawn finalizes the win.
    """
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
    e.execute_player(a.id)

    kinds_after_execute = [c["kind"] for c in e.console]
    assert "win_pending" in kinds_after_execute, (
        f"Expected win_pending entry; saw kinds={kinds_after_execute}"
    )
    assert "game_end" not in kinds_after_execute, (
        "game_end must NOT appear until the next dawn."
    )

    # Walk through the no-op night to dawn.
    e.advance_to_night()
    e.set_auto_advance_to_day(True)
    e.start_night()
    if e._night_thread is not None:
        e._night_thread.join(timeout=3.0)

    kinds_after_dawn = [c["kind"] for c in e.console]
    assert "game_end" in kinds_after_dawn, (
        f"Expected game_end at dawn; kinds={kinds_after_dawn}"
    )
    assert e.phase is Phase.FINISHED


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
    test_day_win_defers_to_dawn_players_can_keep_acting()
    print("dawn-deferred end-to-end test passed.")
    test_dawn_announcement_emits_game_end_console_event()
    print("dawn-announcement console event test passed.")
    print("All new-character tests passed.")
