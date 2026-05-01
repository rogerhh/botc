"""Fool.

    "The 1st time you die, you don't."

A passive once-per-game survival ability. The Fool watches every
:class:`engine.event.EventType.PRE_DEATH` event targeting their seat;
the *first* one that would actually land is cancelled, and the slot
is consumed (``self._used = True``). After that, the Fool dies
normally.

Per the wiki: "If another character's ability protects the Fool from
death, the Fool does not use their ability." This falls out of the
PRE_DEATH cancellation chain naturally — Monk-style demon protection
is checked *before* PRE_DEATH dispatch in :meth:`Engine.kill`, and
other PRE_DEATH-cancelling protectors (Tea Lady, Sailor, Innkeeper
SAFE marker) react first or are checked separately. By the time
control reaches the Fool's reaction, ``event.data["cancelled"]`` is
already True if any other protector saved them, and we skip without
spending the slot.

Drunkenness / poisoning
-----------------------
A drunk or poisoned Fool dies normally — the ability does not
trigger. Gated on ``self.player.has_ability`` at the moment of the
PRE_DEATH (which captures alive + sober + healthy). The Fool slot is
not consumed when the ability didn't fire.

Reset on revive
---------------
The base :meth:`Character.on_revive` already resets ``_used`` (it
follows the conventional flag name), so a revived Fool gets their
single-life back. ``_first_night_pending`` is also reset by the base
hook, harmlessly here since the Fool has no first-night ability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine


class Fool(Character):
    name = "Fool"
    char_type = CharType.TOWNSFOLK
    ability_text = "The 1st time you die, you don't."
    first_night_order = 0
    other_night_order = 0
    once_per_game = True
    reminder_tokens: list = [
        {"name": "NO ABILITY", "icon": "fool_no_ability.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # True once the Fool's "first death" save has been spent. Reset
        # by the base ``Character.on_revive`` hook (which clears any
        # ``_used`` attribute matching the conventional flag name).
        self._used: bool = False

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Surface the NO ABILITY token once the slot has been spent."""
        if self.player is None or self.player.character is None:
            return {}
        if not self._used:
            return {}
        return {"fool_no_ability": [self.player.id]}

    def reaction(self, event: Event, engine: "Engine") -> None:
        if (
            event.type is EventType.PRE_DEATH
            and self.player is not None
            and not self._used
            and self.player.has_ability
            and event.targets
            and any(t.id == self.player.id for t in event.targets)
            and not event.data.get("cancelled")
        ):
            event.data["cancelled"] = True
            self._used = True
            # Mirror onto Player.once_per_game_used so the side panel and
            # the engine's standard once-per-game machinery both see the
            # slot as consumed.
            self.player.once_per_game_used = True
            engine.log_reaction(
                "Fool",
                (
                    f"{self.player.name} would have died — Fool ability "
                    f"saves them once."
                ),
                target=self.player,
                trigger="pre_death",
                effect="fool_first_death_cancelled",
                cause=(
                    event.data.get("cause").value
                    if event.data.get("cause") is not None else None
                ),
            )
            return
        return super().reaction(event, engine)
