"""Slayer.

    "Once per game, during the day, publicly choose a player: if they
     are the Demon, they die."

Daytime once-per-game ability. The Slayer's trigger is a storyteller
action; the engine surfaces the ability through
:meth:`Character.daytime_ability`, which the UI calls when the
storyteller hits the "Slayer slays" button on a seat panel. The
storyteller picks the target via a ``SelectPlayerPrompt``; the ability
fires once and is then spent (whether or not the target was the Demon).

Drunkenness / poisoning: the slot is still consumed (per the
rulebook), but no kill happens.

When the Slayer is sober and healthy, the engine acts directly on the
target's actual ``char_type`` — no ST verify/override prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectPlayerPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine

class Slayer(Character):
    name = "Slayer"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Once per game, during the day, publicly choose a player: "
        "if they are the Demon, they die."
    )
    first_night_order = 0
    other_night_order = 0
    once_per_game = True
    reminder_tokens: list = [
        {"name": 'NO ABILITY', "icon": 'slayer_no_ability.png'},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._used: bool = False

    def daytime_ability(self, engine: "Engine") -> None:
        if self.player is None or self.player.dead:
            return
        if self._used:
            engine.log(
                f"Slayer {self.player.name} tried to slay but ability "
                f"is already spent."
            )
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # SELECT: any alive player.
        eligible = [p.id for p in engine.players if p.alive]
        sel = SelectPlayerPrompt(
            text="Slayer slays a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,  # player decision (Slayer picks publicly)
            target_player_id=self.player.id,
            meta={"character": self.name, "step": "select_target"},
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

        # Always consume the slot, even if drunk/poisoned or wrong.
        self._used = True
        if self.player is not None:
            self.player.once_per_game_used = True

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        # Drunk/poisoned: nothing happens.
        if not self.player.has_ability:
            info_text = (
                f"Slayer slew {target.name} — but ability did not work."
            )
            engine.send_prompt(
                InformationPrompt(
                    text=info_text,
                    target_player_id=self.player.id,
                    shown_to_player=False,
                    meta={"character": self.name, "step": "ineffective"},
                )
            )
            engine.log(
                f"Slayer (drunk/poisoned) shot {target.name} — no effect."
            )
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[target])
            )
            return

        # Sober + healthy Slayer: trust the target's actual char_type,
        # no ST verify/override prompt. The drunk/poisoned branch was
        # handled above.
        is_demon = target.char_type is CharType.DEMON

        if is_demon:
            engine.log(f"Slayer kills {target.name} (was a Demon).")
            engine.kill(target.id, DeathCause.ABILITY)
        else:
            engine.log(f"Slayer shot {target.name} — not a Demon.")

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
