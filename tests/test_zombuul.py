"""Zombuul DEAD / DIED_TODAY state and wake-gating tests.

Per the wiki:

  * "Each night*, if no-one died today, choose a player: they die.
     The 1st time you die, you live but register as dead."
  * "Each day, if a player dies, mark them with the DIED TODAY
     reminder. (If the Zombuul "dies" by execution, they register as
     dead, so mark the Zombuul with the DIED TODAY reminder.)"
  * "Each night except the first, if any player is marked DIED TODAY,
     do not wake the Zombuul."

These tests drive the engine state directly (without spinning up the
night-thread) so the focus is on the Zombuul's reaction / would_act_tonight
logic and the DIED TODAY / DEAD reminder bookkeeping.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import DeathCause, Phase
from engine.event import Event, EventType


def _make_zombuul_game() -> tuple:
    """5-seat BMR-style game with the Zombuul on Eve."""
    e = Engine()
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Saint")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Zombuul")
    e.start_game()
    return e, (a, b, c, d, f)


def _begin_day(e: Engine, day_number: int) -> None:
    e._phase = Phase.DAY
    e._day_number = day_number
    e._dispatch(Event(EventType.DAY_START))


def _begin_night(e: Engine, night_number: int) -> None:
    e._phase = Phase.NIGHT
    e._night_number = night_number


# ---------------------------------------------------------------------------
# Wake-gating
# ---------------------------------------------------------------------------

def test_zombuul_skips_wake_when_player_executed_today() -> None:
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    e.execute_player(b.id)  # Mayor executed during day 1

    # DIED TODAY tracking
    assert b.id in zomb._died_today_ids

    # Zombuul does not wake on the following night.
    _begin_night(e, 2)
    assert zomb.would_act_tonight(e, 2) is False


def test_zombuul_skips_wake_when_player_dies_during_day_via_ability() -> None:
    """Slayer-style day kill (ABILITY cause) also marks DIED TODAY."""
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    e.kill(a.id, DeathCause.ABILITY)  # any day-time kill

    assert a.id in zomb._died_today_ids
    _begin_night(e, 2)
    assert zomb.would_act_tonight(e, 2) is False


def test_zombuul_wakes_when_no_one_died_during_day() -> None:
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    # No deaths during day 1.
    _begin_night(e, 2)
    assert zomb.would_act_tonight(e, 2) is True


def test_zombuul_does_not_wake_on_first_night() -> None:
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character
    # The Zombuul has no first-night ability.
    assert zomb.would_act_tonight(e, 1) is False


# ---------------------------------------------------------------------------
# DIED TODAY tracking is reset each new day
# ---------------------------------------------------------------------------

def test_day_start_clears_died_today() -> None:
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    e.execute_player(b.id)
    assert zomb._died_today_ids

    _begin_night(e, 2)
    # Roll forward to day 2.
    _begin_day(e, 2)
    assert zomb._died_today_ids == []


def test_zombuul_night_kill_does_not_count_as_died_today() -> None:
    """A kill that lands at NIGHT must not flag DIED TODAY for the
    following night's wake gate.

    The wiki rule is "if no-one died TODAY" — today means the most
    recent day phase. A night kill belongs to the night, not the day.
    """
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    # Drop straight into night and kill someone via the demon.
    e._phase = Phase.NIGHT
    e._night_number = 2
    e.kill(a.id, DeathCause.DEMON_KILL, source=zomb)

    # The DIED TODAY pool is by design empty — the death happened at
    # night, not during the day.
    assert zomb._died_today_ids == []


# ---------------------------------------------------------------------------
# Survival save + DIED TODAY (the wiki's parenthetical clarification)
# ---------------------------------------------------------------------------

def test_zombuul_executed_first_time_registers_as_died_today() -> None:
    """Per wiki: 'If the Zombuul dies by execution, they register as
    dead, so mark the Zombuul with the DIED TODAY reminder.'

    The engine fires PRE_DEATH (Zombuul cancels), no DEATH event is
    dispatched — so the Zombuul reaction has to append its own seat to
    ``_died_today_ids`` from the PRE_DEATH branch.
    """
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    e.execute_player(f.id)  # Zombuul executed; survival saves

    assert f.alive is True  # survival save kept them alive
    assert zomb._first_death_used is True
    assert f.id in zomb._died_today_ids  # but registers as dead today

    # Therefore Zombuul also does NOT wake the next night.
    _begin_night(e, 2)
    assert zomb.would_act_tonight(e, 2) is False


# ---------------------------------------------------------------------------
# compute_reminder_tokens surfaces the right markers
# ---------------------------------------------------------------------------

def test_compute_reminder_tokens_for_died_today() -> None:
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    e.execute_player(a.id)

    tokens = zomb.compute_reminder_tokens(e)
    assert tokens.get("zombuul_died_today") == [a.id]


def test_compute_reminder_tokens_empty_when_clean() -> None:
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character
    _begin_day(e, 1)
    assert zomb.compute_reminder_tokens(e) == {}


def test_compute_reminder_tokens_lists_died_today_reminder_metadata() -> None:
    """The class-level reminder_tokens metadata advertises DEAD,
    DIED TODAY, and the FLIPPED life-token reminder so the UI can
    render all three."""
    from engine.characters.zombuul import Zombuul

    names = {entry["name"] for entry in Zombuul.reminder_tokens}
    assert "DEAD" in names
    assert "DIED TODAY" in names
    assert "FLIPPED" in names

    icons = {entry["name"]: entry["icon"] for entry in Zombuul.reminder_tokens}
    assert icons["FLIPPED"] == "life_token_back.png"


# ---------------------------------------------------------------------------
# FLIPPED reminder — the life-token "flip" the wiki calls for whenever
# the Zombuul's first-death save fires.
# ---------------------------------------------------------------------------

def test_flipped_reminder_absent_before_first_death() -> None:
    """No first-death save has fired → no FLIPPED token on the seat."""
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    tokens = zomb.compute_reminder_tokens(e)
    assert "life_token_back" not in tokens


def test_flipped_reminder_set_after_first_death_save() -> None:
    """The first-death save fires (executed Zombuul) → FLIPPED on
    the Zombuul's seat. Mirrors the wiki's 'Flip the life token on the
    Town Square, as normal.'"""
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    e.execute_player(f.id)

    assert zomb._first_death_used is True
    tokens = zomb.compute_reminder_tokens(e)
    assert tokens.get("life_token_back") == [f.id]


def test_flipped_reminder_persists_across_days() -> None:
    """FLIPPED is a permanent state token — once the save has fired,
    it stays on the Zombuul for the rest of the game (unlike DIED TODAY
    which clears at DAY_START)."""
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    e.execute_player(f.id)
    assert zomb.compute_reminder_tokens(e).get("life_token_back") == [f.id]

    # Roll forward a full day/night cycle. DIED TODAY should clear,
    # FLIPPED should not.
    _begin_night(e, 2)
    _begin_day(e, 2)
    tokens = zomb.compute_reminder_tokens(e)
    assert tokens.get("life_token_back") == [f.id]
    assert "zombuul_died_today" not in tokens  # cleared on DAY_START


def test_flipped_reminder_only_on_zombuul_seat() -> None:
    """A night-time kill of another seat must not place FLIPPED on
    the victim — FLIPPED is the Zombuul's own life-token state."""
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    e._phase = Phase.NIGHT
    e._night_number = 2
    e.kill(a.id, DeathCause.DEMON_KILL, source=zomb)

    tokens = zomb.compute_reminder_tokens(e)
    assert "life_token_back" not in tokens


# ---------------------------------------------------------------------------
# Sanity: the survival save still cancels the death
# ---------------------------------------------------------------------------

def test_zombuul_survives_first_death() -> None:
    e, (a, b, c, d, f) = _make_zombuul_game()
    zomb = f.character

    _begin_day(e, 1)
    assert f.alive is True
    e.execute_player(f.id)
    assert f.alive is True
    assert zomb._first_death_used is True

    # On a second execution the Zombuul dies for real.
    _begin_day(e, 2)
    e.execute_player(f.id)
    assert f.alive is False


def test_zombuul_cancellation_stamps_reason() -> None:
    """Zombuul's first-death save must stamp ``cancelled_by_character``
    and ``cancelled_reason`` on the PRE_DEATH event.

    Without these fields the storyteller console reports the cancelled
    death as ``(reason not recorded)`` — the engine contract is that
    every PRE_DEATH canceller stamps both fields so the why-string is
    always available.

    Note: the Zombuul listens on ``PRE_DEATH_LAST_RESORT`` (a deferred
    pass the engine only fires when no standard protector cancelled
    the kill — see ``engine/event.py``). The contract being tested is
    on the *shared* data dict between PRE_DEATH and the last-resort
    event, so we hand-dispatch the last-resort event directly here.
    """
    from engine.event import Event, EventType

    e, (a, b, c, d, f) = _make_zombuul_game()
    _begin_day(e, 1)

    # Hand-dispatch the last-resort pass on the Zombuul and inspect
    # the event. (See module docstring above — Zombuul's first-life
    # save reacts to PRE_DEATH_LAST_RESORT, not raw PRE_DEATH.)
    pre_event = Event(
        EventType.PRE_DEATH_LAST_RESORT,
        targets=[f],
        data={"cause": None, "cancelled": False, "force": False},
    )
    e._dispatch(pre_event)

    assert pre_event.data.get("cancelled") is True, (
        "Zombuul should cancel its first death."
    )
    assert pre_event.data.get("cancelled_by_character") == "Zombuul", (
        "Zombuul must stamp itself as the canceller."
    )
    reason = pre_event.data.get("cancelled_reason")
    assert reason and "Zombuul" in reason, (
        f"Zombuul must stamp a non-empty cancellation reason; got {reason!r}."
    )


# ---------------------------------------------------------------------------
# Innkeeper + Zombuul collision — first-life MUST NOT be spent when the
# Innkeeper has marked the Zombuul SAFE.
# ---------------------------------------------------------------------------

def test_innkeeper_protected_zombuul_self_kill_keeps_first_life() -> None:
    """Innkeeper marks the Zombuul SAFE; Zombuul self-targets at night.

    The Zombuul's nightly attack lets them pick any seat including
    themself. If the Innkeeper has tagged that seat SAFE the Innkeeper
    is the one who cancels the death — the Zombuul's first-life save
    must NOT be spent.

    Per the project rule the wiki endorses: "If another character's
    ability protects the Zombuul from death, the Zombuul does not use
    their ability." Before the last-resort split this fix introduced,
    Zombuul and Innkeeper both tried to cancel via PRE_DEATH and seat
    order decided the winner — a Zombuul seated before the Innkeeper
    would burn its first life even though the Innkeeper would have
    saved them anyway. Now the Zombuul listens on PRE_DEATH_LAST_RESORT
    which only fires when no standard protector cancelled the kill, so
    the Innkeeper always wins the priority race.
    """
    from engine.engine import Engine
    from engine.enums import DeathCause, Phase

    e = Engine()
    # Seat order: Zombuul *first* — this is the seat-ordering that
    # used to produce the bug. With the fix in place the order no
    # longer matters; pinning Zombuul to seat 0 keeps the regression
    # explicit.
    z = e.add_seat("Zara")    # 1 — Zombuul
    i = e.add_seat("Iris")    # 2 — Innkeeper
    b = e.add_seat("Bob")     # 3 — Mayor
    c = e.add_seat("Cara")    # 4 — Soldier
    d = e.add_seat("Dan")     # 5 — Saint
    e.assign_character(z.id, "Zombuul")
    e.assign_character(i.id, "Innkeeper")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Saint")
    e.start_game()

    # Mark the Innkeeper's SAFE set to include the Zombuul. The
    # Innkeeper now uses the engine effect registry (post-Layer-2
    # migration) — add an InnkeeperSafeEffect directly so the test
    # is isolated to the cancellation pipeline and doesn't depend on
    # the night-loop or prompt drainer.
    from engine.characters.innkeeper import InnkeeperSafeEffect
    inn = i.character
    zomb = z.character
    e.add_effect(InnkeeperSafeEffect(source=inn, targets=[z.id, b.id]))

    # Drop into night and have the Zombuul self-kill.
    e._phase = Phase.NIGHT
    e._night_number = 2
    e.kill(z.id, DeathCause.DEMON_KILL, source=zomb)

    # Innkeeper should have cancelled the death; Zombuul's first life
    # is intact.
    assert z.alive, "Innkeeper SAFE should keep the Zombuul alive."
    assert zomb._first_death_used is False, (
        "Innkeeper saved the Zombuul; the first-life save must NOT have "
        "been consumed."
    )

    # And the storyteller console should attribute the save to the
    # Innkeeper, not the Zombuul. The console wraps reaction details
    # under ``ent["details"]`` (see Engine._console_log).
    cancelled_by_innkeeper = any(
        (ent.get("details") or {}).get("character") == "Innkeeper"
        and (ent.get("details") or {}).get("effect") == "innkeeper_safe"
        for ent in e.console
        if ent.get("kind") == "reaction"
    )
    assert cancelled_by_innkeeper, (
        "Expected the Innkeeper to be the canceller of record on the "
        "storyteller console."
    )
    # Mirror assertion: no Zombuul reaction should be on the feed.
    zombuul_reactions = [
        ent for ent in e.console
        if ent.get("kind") == "reaction"
        and (ent.get("details") or {}).get("character") == "Zombuul"
    ]
    assert not zombuul_reactions, (
        "Zombuul's first-life save must NOT have logged — Innkeeper "
        f"cancelled the death first. Got: {zombuul_reactions!r}"
    )


def test_zombuul_first_life_still_saves_when_no_other_protector() -> None:
    """Sanity check: with no Innkeeper / Soldier / etc. in play, the
    Zombuul's last-resort save still fires on a self-kill.

    Pairs with the Innkeeper test above to pin both sides of the
    last-resort dispatch — the Zombuul's first life is consumed iff
    the death would otherwise actually have landed.
    """
    from engine.engine import Engine
    from engine.enums import DeathCause, Phase

    e = Engine()
    z = e.add_seat("Zara")    # 1 — Zombuul
    b = e.add_seat("Bob")     # 2 — Mayor (no PRE_DEATH save here)
    c = e.add_seat("Cara")    # 3 — Saint
    d = e.add_seat("Dan")     # 4 — Poisoner
    f = e.add_seat("Eve")     # 5 — Recluse
    e.assign_character(z.id, "Zombuul")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Saint")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Recluse")
    e.start_game()

    zomb = z.character
    e._phase = Phase.NIGHT
    e._night_number = 2
    e.kill(z.id, DeathCause.DEMON_KILL, source=zomb)

    # Zombuul self-kill at night: no other protector applies (Mayor's
    # redirect requires an ST prompt and we're not driving prompts),
    # so the last-resort pass kicks in and Zombuul's first life saves.
    assert z.alive, "Zombuul should survive its first death."
    assert zomb._first_death_used is True, (
        "First life should be consumed when no other protector cancelled."
    )
