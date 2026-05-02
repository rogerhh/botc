"""Devil's Advocate.

    "Each night, choose a living player (different to last night):
     if executed tomorrow, they don't die."

The Devil's Advocate marks a living player each night for execution
immunity the next day. Like the Exorcist, the same player may not be
picked two nights in a row.

Implementation (registry-managed)
---------------------------------
Each night the DA picks a player and emits a
:class:`DevilsAdvocateSurvivesEffect` on that seat. The effect's
``resolve_event`` cancels :class:`EventType.PRE_DEATH` whose cause
is :class:`DeathCause.EXECUTION` (force-kill bypass via the standard
``not event.data.get("force")`` check). The effect purges at the
next dusk — protection is for the day after the pick only.

The "different to last night" rule stays as character-side state
(``_previous_pick_id``) — it's a per-character pick constraint, not
an emitted effect.

Drunk/poisoned DA goes through the motions but no effect is emitted
(registry contract: source must have ability at application time).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventOutcome, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class DevilsAdvocateSurvivesEffect(Effect):
    """Devil's Advocate's one-day execution immunity."""

    kind = "devils_advocate_survives"
    contributes_to_state = None

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dusk":
            engine.purge_effect(self)

    def resolve_event(
        self, engine: "Engine", event: Event
    ) -> Optional[EventOutcome]:
        if (
            event.type is EventType.PRE_DEATH
            and event.data.get("cause") is DeathCause.EXECUTION
            and not event.data.get("cancelled")
            and not event.data.get("force")
        ):
            event.data["cancelled_by_character"] = "Devil's Advocate"
            event.data["cancelled_reason"] = (
                "Devil's Advocate protects the previously-chosen player"
            )
            try:
                tgt = event.targets[0] if event.targets else None
                tgt_name = tgt.name if tgt else "?"
                engine.log_reaction(
                    "Devil's Advocate",
                    (
                        f"{tgt_name} executed but remains alive "
                        f"(Devil's Advocate)."
                    ),
                    target=tgt,
                    trigger="execution",
                )
            except Exception:  # pragma: no cover (defensive)
                pass
            return EventOutcome.CANCEL
        return None


class DevilsAdvocate(Character):
    name = "Devil's Advocate"
    char_type = CharType.MINION
    ability_text = (
        "Each night, choose a living player (different to last night): "
        "if executed tomorrow, they don't die."
    )
    first_night_order = 16
    other_night_order = 19
    reminder_tokens: list = [
        {"name": "SURVIVES EXECUTION", "icon": "devils_advocate_survives.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Same-pick-twice-in-a-row prevention. Per-character state,
        # not an emitted effect.
        self._previous_pick_id: Optional[int] = None

    def on_revive(self, engine: "Engine") -> None:
        super().on_revive(engine)
        self._previous_pick_id = None

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        eligible = [
            p.id for p in engine.players
            if p.alive and p.id != self._previous_pick_id
        ]
        if not eligible:
            return
        sel = SelectPlayerPrompt(
            text="Devil's Advocate protects a living player from execution",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_protect",
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

        # RESOLUTION: emit DevilsAdvocateSurvivesEffect via the
        # registry. A drunk/poisoned DA goes through the motions but
        # no effect lands. Either way, ``_previous_pick_id`` is
        # updated so the same-pick-twice rule binds.
        self._previous_pick_id = target.id
        if self.player.has_ability:
            engine.add_effect(DevilsAdvocateSurvivesEffect(
                source=self, targets=[target.id],
            ))
            engine.log(
                f"Devil's Advocate {self.player.name} protects {target.name}."
            )
        else:
            engine.log(
                f"Devil's Advocate {self.player.name} is drunk/poisoned — "
                f"no real protection."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
