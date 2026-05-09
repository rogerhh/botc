"""Monk.

    "Each night except the first, choose a player (not yourself): they
     are safe from the Demon tonight."

Protection ability. The Monk picks a target each night (other nights),
and that player is safe from the Demon for the rest of the night.

Implementation (registry-managed)
---------------------------------
The Monk emits a :class:`MonkSafeEffect` on the chosen target. The
effect's ``resolve_event`` cancels :class:`EventType.PRE_DEATH` events
whose ``cause`` is :class:`DeathCause.DEMON_KILL`. The effect is purged
at the next dawn — Monk's protection is night-only.

Drunkenness / poisoning: a drunk or poisoned Monk goes through the
motions (wake, pick) but the registry contract requires
``has_ability`` at application time, so no effect is emitted.
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


class MonkSafeEffect(Effect):
    """Monk's nightly demon-kill protection.

    Cancels PRE_DEATH events whose cause is DEMON_KILL on the
    protected target. Force-kills (Assassin) bypass via the
    ``not event.data.get("force")`` check.

    Lifecycle: ``resolve_event`` only cancels demon kills (which only
    happen at night, so no phase gate needed). The token marker
    persists through the day after for storyteller bookkeeping, and
    purges at the next dusk (transition into the next night).
    """

    kind = "monk_safe"
    contributes_to_state = None

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        # Purge at next dusk so the SAFE marker stays visible through
        # the day after the protection night.
        if phase == "dusk":
            engine.purge_effect(self)

    def resolve_event(
        self, engine: "Engine", event: Event
    ) -> Optional[EventOutcome]:
        if (
            event.type is EventType.PRE_DEATH
            and event.data.get("cause") is DeathCause.DEMON_KILL
            and not event.data.get("cancelled")
            and not event.data.get("force")
        ):
            event.data["cancelled_by_character"] = "Monk"
            event.data["cancelled_reason"] = "Monk protects from demon"
            try:
                tgt = event.targets[0] if event.targets else None
                tgt_name = tgt.name if tgt else "?"
                engine.log_reaction(
                    "Monk",
                    f"{tgt_name} is protected from the Demon — no death.",
                    target=tgt,
                    trigger="demon_kill",
                    effect="monk_safe",
                )
            except Exception:  # pragma: no cover (defensive)
                pass
            return EventOutcome.CANCEL
        return None


class Monk(Character):
    name = "Monk"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night except the first, choose a player (not yourself): "
        "they are safe from the Demon tonight."
    )
    first_night_order = 0
    other_night_order = 20
    reminder_tokens: list = [
        {"name": 'SAFE', "icon": 'monk_safe.png'},
    ]

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: any other player (alive or dead, per the wiki rule).
        # Monk can't pick themselves. A dead pick wastes the protection
        # but is legal — engine.kill no-ops on dead targets, so the
        # Monk's safe-effect is harmless on a dead seat.
        eligible = [
            p.id for p in engine.players
            if p.id != self.player.id
        ]
        sel = SelectPlayerPrompt(
            text="Monk protects a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_player",
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
        # Goon notify: if the Monk picked the Goon's seat, the Goon
        # drunkens the Monk so the protection below doesn't land.
        engine.notify_goon_chosen(self, target)

        # RESOLUTION: emit MonkSafeEffect via the registry. A drunk/
        # poisoned Monk goes through the motions but no effect lands.
        if self.player.has_ability:
            engine.add_effect(MonkSafeEffect(
                source=self, targets=[target.id],
            ))
            engine.log(
                f"Monk {self.player.name} protects {target.name} tonight."
            )
        else:
            engine.log(
                f"Monk {self.player.name} is drunk/poisoned — "
                f"{target.name} is NOT actually protected."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
