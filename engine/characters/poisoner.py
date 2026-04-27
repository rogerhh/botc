"""Poisoner.

    "Each night, choose a player: they are poisoned tonight and tomorrow day."

A poisoned player has no ability — their abilities are simulated by the
storyteller (woken at the right time, given false info if applicable),
but no game state is altered.

Implementation
--------------
The Poisoner acts every night (first night and beyond). Before picking
their new target, the Poisoner's previous target — if any — is
unpoisoned, since "tonight and tomorrow day" expires at the next dusk.

If the Poisoner is themselves drunk or poisoned, they go through the
motions but no poisoning takes effect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

class Poisoner(Character):
    name = "Poisoner"
    char_type = CharType.MINION
    ability_text = (
        "Each night, choose a player: they are poisoned tonight and tomorrow day."
    )
    first_night_order = 10
    other_night_order = 10
    reminder_tokens: list = [
        {"name": 'POISONED', "icon": 'poisoner_poisoned.png'},
    ]

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Track the previously-poisoned player so we can unpoison them
        # at the next dusk (== before the Poisoner picks a new target).
        self._last_target: Optional["Player"] = None

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        # Clean up last night's poisoning before applying tonight's.
        if self._last_target is not None and self._last_target.poisoned:
            self._last_target.set_poisoned(False)
            engine.log(
                f"{self._last_target.name} is no longer poisoned (Poisoner expired)."
            )
        self._last_target = None

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event so other abilities can react,
        # but no separate ST-facing prompt: the wake-up line is shown
        # as part of the next prompt's panel.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: pick a player to poison. Storyteller may pick any
        # alive player. The Poisoner can poison themselves.
        eligible = [p.id for p in engine.players if p.alive]
        sel = SelectPlayerPrompt(
            text="Poisoner poisons a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,  # player decision (Poisoner picks)
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_player",
                "stage": "player",
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0]
        target = engine.get_player(target_id)

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        # No INFORMATION step — the Poisoner does not learn anything.

        # RESOLUTION: poison the target, but only if the Poisoner has
        # their ability working (sober, healthy, alive).
        if self.player.has_ability:
            target.set_poisoned(True)
            self._last_target = target
            engine.log(f"{target.name} is poisoned by the Poisoner.")
        else:
            engine.log(
                f"Poisoner {self.player.name} is drunk/poisoned; "
                f"no real poisoning happens."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
