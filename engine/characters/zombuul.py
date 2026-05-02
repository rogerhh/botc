"""Zombuul.

    "Each night*, if no-one died today, choose a player: they die.
     The 1st time you die, you live but register as dead."

The Zombuul has two abilities glued together:

  1. **Survival** — the first time the Zombuul would die, the death
     is cancelled. They are then "seemingly dead" and the wiki says
     they "register as dead" thereafter.
  2. **Attack** — each night except the first, if nobody died during
     the most-recent day, the Zombuul wakes and kills any player.

Implementation faithfulness
---------------------------
The "register as dead" facade is the trickiest part of this role —
the wiki says the seemingly-dead Zombuul "counts as a dead player in
almost every way" (no nominations, no Tea Lady neighbour, life token
flipped). Achieving every one of those behaviours in the engine
without polluting the player model is more invasive than the rest of
this character set warrants. This implementation therefore takes the
**simpler Fool-style approach**: the first death is cancelled via the
``PRE_DEATH_LAST_RESORT`` channel (the deferred last-resort save
pass — see ``engine/event.py``), the Zombuul stays alive in the
engine, and the second death (the one that actually kills them)
lands normally.

Other consequences of "register as dead":

  * **Tea Lady neighbour** — Zombuul still counts as alive; the Tea
    Lady's neighbour-good check sees them. The wiki rule that the
    Zombuul shouldn't be a Tea Lady neighbour is not enforced here.
  * **Two-alive win** — Zombuul still counts toward the alive total;
    the wiki's "game continues if just two other players are alive"
    is not enforced.

Both edge cases are noted for future refinement; the core
"first-death immunity + wake-when-nobody-died-today" mechanic IS
faithfully implemented.

Drunkenness / poisoning
-----------------------
A drunk/poisoned Zombuul does not get the survival ability — the
PRE_DEATH cancellation is gated on ``has_ability``. A drunk Zombuul
nightly attack still wakes them but no real kill lands.

Suppression by the Exorcist
---------------------------
At the start of the nightly ability, the Zombuul checks
``engine._exorcism_blocked_id``. If it matches their seat, they
short-circuit (the Exorcist already showed them the reveal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class ZombuulDeadEffect(Effect):
    """Marker on a seat the Zombuul killed last night."""

    kind = "zombuul_dead"
    contributes_to_state = None
    purge_on_source_death = True
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class ZombuulDiedTodayEffect(Effect):
    """Marker on a seat that died during the current day (any cause).

    Used by the Zombuul's wake-gate ("don't wake if anyone died
    today") and by the storyteller's grimoire bookkeeping. Cleared
    at next dawn."""

    kind = "zombuul_died_today"
    contributes_to_state = None
    purge_on_source_death = True
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class ZombuulLifeTokenBackEffect(Effect):
    """FLIPPED marker on the Zombuul's own seat after the first-death
    save fires. Persistent for the rest of the game."""

    kind = "life_token_back"
    contributes_to_state = None
    purge_on_source_death = False
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


class Zombuul(Character):
    name = "Zombuul"
    char_type = CharType.DEMON
    ability_text = (
        "Each night*, if no-one died today, choose a player: they die. "
        "The 1st time you die, you live but register as dead."
    )
    first_night_order = 0
    other_night_order = 26
    reminder_tokens: list = [
        {"name": "DEAD", "icon": "zombuul_dead.png"},
        {"name": "DIED TODAY", "icon": "zombuul_died_today.png"},
        # Per the wiki: "The first time the Zombuul would die, they
        # remain alive. Declare that they died, but do not add a shroud
        # to the Zombuul. (Flip the life token on the Town Square, as
        # normal.)" We literally model that physical flip — the back
        # of the life token sits on the Zombuul's seat from the moment
        # the first-death save fires until the game ends.
        {"name": "FLIPPED", "icon": "life_token_back.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # True iff the survival save has already been spent.
        self._first_death_used: bool = False
        # Per-seat list of players who died during the most-recent day
        # (cleared at every DAY_START). Drives the DIED TODAY reminder
        # and gates the Zombuul's nightly wake. Per the wiki, the
        # Zombuul's own first cancelled death also lands here because
        # they "register as dead".
        self._died_today_ids: List[int] = []
        # Player IDs the Zombuul killed this night (DEAD reminder).
        # Cleared at every DAY_START.
        self._killed_tonight_ids: List[int] = []

    def _emit_dead_marker(self, engine: "Engine", target_id: int) -> None:
        """Emit a ZombuulDeadEffect on a fresh kill victim."""
        engine.add_effect(ZombuulDeadEffect(
            source=self, targets=[target_id],
        ))

    def _emit_died_today_marker(self, engine: "Engine", target_id: int) -> None:
        """Emit a ZombuulDiedTodayEffect on a day-death."""
        # Avoid duplicates.
        existing = engine.effects_targeting(
            target_id, kind="zombuul_died_today", active_only=False,
        )
        if existing:
            return
        engine.add_effect(ZombuulDiedTodayEffect(
            source=self, targets=[target_id],
        ))

    def _emit_life_token_back(self, engine: "Engine") -> None:
        """Emit the FLIPPED marker on the Zombuul's own seat (persistent)."""
        if self.player is None:
            return
        existing = engine.effects_targeting(
            self.player.id, kind="life_token_back", active_only=False,
        )
        if existing:
            return
        engine.add_effect(ZombuulLifeTokenBackEffect(
            source=self, targets=[self.player.id],
        ))

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Surface the DIED TODAY, DEAD, and FLIPPED reminders.

          * ``zombuul_died_today``: every seat that died during the
            most-recent day phase (any cause). Cleared at DAY_START.
          * ``zombuul_dead``: every seat the Zombuul has killed this
            night. Cleared at DAY_START.
          * ``life_token_back``: the Zombuul's own seat, persistent
            from the moment the first-death save fires (the wiki's
            "flip the life token on the Town Square" cue) until the
            end of the game.
        """
        out: "dict[str, list[int]]" = {}
        if self._died_today_ids:
            out["zombuul_died_today"] = list(self._died_today_ids)
        if self._killed_tonight_ids:
            out["zombuul_dead"] = list(self._killed_tonight_ids)
        if self._first_death_used and self.player is not None:
            out["life_token_back"] = [self.player.id]
        return out

    def reaction(self, event: Event, engine: "Engine") -> None:
        if event.type is EventType.DAY_START:
            # New day → clear yesterday's DIED TODAY tokens and last
            # night's DEAD reminders.
            self._died_today_ids = []
            self._killed_tonight_ids = []
        elif (
            event.type is EventType.DEATH
            and engine.phase.value == "day"
            and event.targets
        ):
            # Day-time death of any seat → mark them DIED TODAY. The
            # Zombuul's own cancelled "first death" never reaches DEATH
            # (PRE_DEATH cancels it), so that case is handled below in
            # the PRE_DEATH_LAST_RESORT branch which appends the
            # Zombuul's own id explicitly.
            tid = event.targets[0].id
            if tid not in self._died_today_ids:
                self._died_today_ids.append(tid)
                self._emit_died_today_marker(engine, tid)
        # First-death survival. Listens on ``PRE_DEATH_LAST_RESORT``
        # rather than the standard ``PRE_DEATH`` so the save fires
        # *after* every other protector has had a chance to cancel
        # (Innkeeper SAFE, Soldier, Mayor redirect, Tea Lady, Sailor,
        # Fool, Pacifist, Devil's Advocate). The engine only fires
        # the last-resort event when the standard PRE_DEATH pass did
        # *not* cancel the kill, so by the time we arrive here the
        # Zombuul is genuinely about to die — the first life is
        # spent only on actual deaths, never wasted on a death
        # somebody else was already preventing. The wiki explicitly
        # endorses this ordering: "If another character's ability
        # protects the Zombuul from death, the Zombuul does not use
        # their ability." Concretely fixes the Innkeeper-saves-Zombuul
        # collision: Innkeeper cancels in the standard pass, the
        # last-resort pass never fires, and the Zombuul's first life
        # stays available — even if the Zombuul self-targeted with
        # their nightly attack and Innkeeper marked them SAFE.
        if (
            event.type is EventType.PRE_DEATH_LAST_RESORT
            and self.player is not None
            and not self._first_death_used
            and self.player.has_ability
            and event.targets
            and any(t.id == self.player.id for t in event.targets)
            and not event.data.get("cancelled")
            and not event.data.get("force")
        ):
            event.data["cancelled"] = True
            # Stamp the canceller so engine.kill / engine.execute_player
            # always have a *why* string — every other PRE_DEATH
            # canceller (Soldier, Mayor, Pacifist, Tea Lady, Sailor,
            # Fool, Innkeeper, Devil's Advocate) does the same.
            event.data["cancelled_by_character"] = "Zombuul"
            event.data["cancelled_reason"] = (
                "Zombuul's first-death save (registers as dead)"
            )
            self._first_death_used = True
            engine.log_reaction(
                "Zombuul",
                (
                    f"{self.player.name} survives "
                    f"({event.data.get('cause').value if event.data.get('cause') else '?'}) "
                    f"— Zombuul's first-death save (registers as dead)."
                ),
                target=self.player,
                trigger="pre_death",
            )
            # Per wiki: "If the Zombuul 'dies' by execution, they
            # register as dead, so mark the Zombuul with the DIED
            # TODAY reminder." More generally: any time the survival
            # save fires during the day, the Zombuul registers as dead
            # for today, which means the Zombuul should NOT wake the
            # following night.
            if engine.phase.value == "day":
                if self.player.id not in self._died_today_ids:
                    self._died_today_ids.append(self.player.id)
                    self._emit_died_today_marker(engine, self.player.id)
            self._emit_life_token_back(engine)
            return
        return super().reaction(event, engine)

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if not super().would_act_tonight(engine, night_number):
            return False
        if night_number == 1:
            return False
        return not self._died_today_ids

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        if self._died_today_ids:
            return
        # Exorcist block: short-circuit before any wake.
        if (
            getattr(engine, "_exorcism_blocked_id", None) == self.player.id
        ):
            engine.log(
                f"Zombuul {self.player.name}: blocked by the Exorcist."
            )
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        eligible = [p.id for p in engine.players if p.alive]
        sel = SelectPlayerPrompt(
            text="Zombuul kills a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_target",
                "stage": "player",
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0] if target_id else None
        if target_id is None:
            return
        try:
            target = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )
        if self.player.has_ability:
            engine.kill(target.id, DeathCause.DEMON_KILL, source=self)
            # Place the Zombuul-specific DEAD reminder on the victim
            # (cleared at DAY_START). Note: the Zombuul's own DEATH
            # reaction also appends this to ``_died_today_ids`` only
            # when ``engine.phase.value == "day"``, so a night kill
            # does NOT count as "died today" for the Zombuul's wake
            # gating — exactly per the wiki rule.
            if target.id not in self._killed_tonight_ids:
                self._killed_tonight_ids.append(target.id)
                if not target.alive:
                    self._emit_dead_marker(engine, target.id)
        else:
            engine.log(
                f"Zombuul {self.player.name} is drunk/poisoned — no kill."
            )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
