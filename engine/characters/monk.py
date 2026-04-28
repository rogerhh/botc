"""Monk.

    "Each night except the first, choose a player (not yourself): they
     are safe from the Demon tonight."

Protection ability. The Monk picks a target each night (other nights),
and that player gets ``protected_from_demon = True`` for the rest of
the night. The flag is cleared on the next NIGHT_START via
``Player.reset_night_flags`` (called by the engine).

The Demon's kill resolution checks ``protected_from_demon`` (see
``Engine.kill``) and skips the death if set, so the Monk's ability
is realised with a single state mutation rather than a reaction.

Drunkenness / poisoning: a drunk or poisoned Monk goes through the
motions (wake, pick) but no actual protection is applied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine

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

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Most-recent player chosen by the Monk this night. Surfaced
        # to the UI grimoire so the SAFE reminder token can be drawn
        # on the chosen seat. Cleared at NIGHT_START (see ``reaction``)
        # so a stale pick from a previous night doesn't leak into the
        # snapshot before the Monk has chosen tonight.
        self._target = None

    def reaction(self, event: "Event", engine: "Engine") -> None:
        # Reset the surfaced target at the start of every night so the
        # SAFE token disappears alongside the engine's reset of
        # ``protected_from_demon``. The Monk's ability re-populates
        # ``_target`` once the storyteller picks tonight.
        if event.type is EventType.NIGHT_START:
            self._target = None
        return super().reaction(event, engine)

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event so other abilities can react,
        # but no separate ST-facing prompt: the wake-up line is shown
        # as part of the next prompt's panel.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: any other alive player. Monk can't pick themselves.
        eligible = [
            p.id for p in engine.players
            if p.alive and p.id != self.player.id
        ]
        sel = SelectPlayerPrompt(
            text="Monk protects a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,  # player decision (Monk picks)
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

        # RESOLUTION: set the protected flag — but only if the Monk is
        # sober and healthy. A drunk/poisoned Monk goes through the
        # motions for the player's sake (the storyteller still wakes
        # them, etc.) but no real protection lands.
        if self.player.has_ability:
            target.protected_from_demon = True
            self._target = target
            engine.log(
                f"Monk {self.player.name} protects {target.name} tonight."
            )
        else:
            # Drunk/poisoned Monk still records the picked target so
            # storytellers can audit what was chosen, but the SAFE
            # token is gated on ``protected_from_demon`` in the UI
            # snapshot — so it won't render when the flag isn't set.
            self._target = target
            engine.log(
                f"Monk {self.player.name} is drunk/poisoned — "
                f"{target.name} is NOT actually protected."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
