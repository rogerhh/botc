"""Soldier.

    "You are safe from the Demon."

Passive ability — the Demon's nightly kill on a sober & healthy Soldier
fails. Implemented as a PRE_DEATH reaction on the Soldier itself: the
engine never has to know the Soldier exists. When a DEMON_KILL targets
this Soldier and the Soldier has its ability, the reaction cancels the
death by setting ``event.data["cancelled"] = True`` — same channel the
Mayor's night-redirect uses.

Drunkenness / poisoning: a drunk or poisoned Soldier loses protection.
The reaction gates on ``self.player.has_ability``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine


class Soldier(Character):
    name = "Soldier"
    char_type = CharType.TOWNSFOLK
    ability_text = "You are safe from the Demon."
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    def reaction(self, event: Event, engine: "Engine") -> None:
        if (
            event.type is EventType.PRE_DEATH
            and self.player is not None
            and any(t.id == self.player.id for t in event.targets)
            and event.data.get("cause") is DeathCause.DEMON_KILL
            and self.player.has_ability
            and not event.data.get("cancelled")
        ):
            event.data["cancelled"] = True
            engine.log_reaction(
                "Soldier",
                f"{self.player.name} cannot be killed by the Demon.",
                target=self.player,
                trigger="demon_kill",
            )
            return
        return super().reaction(event, engine)
