"""Fool.

    "The 1st time you die, you don't."

A passive once-per-game survival ability. The Fool watches every
:class:`engine.event.EventType.PRE_DEATH_LAST_RESORT` event targeting
their seat; the *first* one that would actually land is cancelled,
and the slot is consumed (``self._used = True``). After that, the
Fool dies normally.

Per the wiki: "If another character's ability protects the Fool from
death, the Fool does not use their ability." The Fool listens on the
**last-resort** PRE_DEATH pass rather than the standard one so this
rule holds independent of seat order. The engine only fires
``PRE_DEATH_LAST_RESORT`` when no standard protector
(Innkeeper SAFE, Tea Lady, Sailor, Soldier, Mayor redirect, Pacifist,
Devil's Advocate, …) cancelled the kill on the first pass. By the
time the Fool's reaction is invoked here, the death is genuinely
about to land — so the once-per-game slot is spent only on actual
deaths, never wasted on a death somebody else was already preventing.

Earlier versions of this docstring claimed the standard PRE_DEATH
chain handled this "naturally" because the cancellation flag would
be set before the Fool's reaction fired. That was incorrect — the
engine dispatches reactions in seat order, so a Fool seated *before*
their protector would burn the slot first. The last-resort pass
removes that ordering hazard.

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
from engine.effect import Effect
from engine.enums import CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine


class FoolNoAbilityEffect(Effect):
    """NO ABILITY marker on the Fool's seat once their first-death
    save has been consumed."""

    kind = "fool_no_ability"
    contributes_to_state = None
    purge_on_source_death = False
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


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

    # NO ABILITY rendered via FoolNoAbilityEffect emitted when the
    # save fires (in ``reaction``). Per Q4: the marker persists
    # post-mortem.

    def reaction(self, event: Event, engine: "Engine") -> None:
        # Listens on ``PRE_DEATH_LAST_RESORT`` — see the module
        # docstring above for the full rationale. In short: the
        # engine only fires the last-resort pass when no standard
        # protector (Innkeeper SAFE, Tea Lady, Sailor, Soldier, Mayor,
        # Pacifist, Devil's Advocate, …) cancelled the kill, so the
        # Fool's once-per-game slot is spent only when the death
        # would otherwise actually land. This makes the wiki's "If
        # another character's ability protects the Fool from death,
        # the Fool does not use their ability" hold regardless of
        # seat order.
        if (
            event.type is EventType.PRE_DEATH_LAST_RESORT
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
            engine.add_effect(FoolNoAbilityEffect(
                source=self, targets=[self.player.id],
            ))
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
