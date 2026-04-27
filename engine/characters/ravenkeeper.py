"""Ravenkeeper.

    "If you die at night, you are woken to choose a player: you learn
     their character."

The Ravenkeeper acts in the other-nights order, but only triggers if
they died THIS night (Demon kill, ability, anything except execution).

If the Ravenkeeper was sober and healthy at death, they learn the true
character of the chosen player. If drunk or poisoned at the time of
death, the engine pre-picks a *random wrong* character (anything
other than the target's true character) per CLAUDE.md and surfaces it
to the storyteller with a Next button. The ST may change the pick.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectCharacterPrompt,
    SelectPlayerPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine

class Ravenkeeper(Character):
    name = "Ravenkeeper"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "If you die at night, you are woken to choose a player: "
        "you learn their character."
    )
    first_night_order = 0
    other_night_order = 45
    reminder_tokens: list = []

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        # Ravenkeeper acts only when they died this night (Demon kill,
        # ability, anything except execution). They are *dead* at the
        # time of the wake-up — exactly the opposite of the default
        # "is alive" precondition — so we skip the base check.
        if night_number == 1:
            return False
        if self.player is None:
            return False
        if self.player not in engine.pending_night_deaths:
            return False
        from engine.enums import DeathCause as _DC
        if self.player.death_cause is _DC.EXECUTION:
            return False
        return True

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1:
            return  # No first-night action.
        if self.player is None or self.player.alive:
            return  # Only triggers if Ravenkeeper is dead.
        # Specifically: died this night, not by execution.
        if self.player not in engine.pending_night_deaths:
            return
        if self.player.death_cause is DeathCause.EXECUTION:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: Ravenkeeper picks any player.
        all_player_ids = [p.id for p in engine.players]
        sel = SelectPlayerPrompt(
            text="Player to learn",
            count=1,
            eligible_player_ids=all_player_ids,
            allow_self=True,
            allow_randomize=False,  # player decision (Ravenkeeper picks)
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

        # Determine what character to show. Sober + healthy: trust
        # the target's actual character, no ST prompt. Drunk/poisoned:
        # range of options — pre-pick a random *wrong* character and
        # surface to ST with a Next button.
        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        actual_char = target.character.name if target.character else None

        # Spy misregistration: if the target is the Spy, ask the
        # Storyteller what character the Spy registers as. Default is
        # the Spy's internally-tracked preferred good character.
        spy_register_as = None
        if actual_char == "Spy":
            from engine.characters.spy import (
                prompt_spy_register_as as _prompt_spy_register_as,
            )
            spy_register_as = _prompt_spy_register_as(
                engine,
                target,
                detector_name=self.name,
                detector_player_id=self.player.id,
                text="Spy registers as (Ravenkeeper)",
                stage="st_post",
                extra_meta={"step_for": "ravenkeeper_target"},
            )

        if is_drunk_or_poisoned:
            all_chars = engine.all_character_names()
            wrong_options = [c for c in all_chars if c != actual_char]
            default_wrong = (
                _rand.choice(wrong_options) if wrong_options else actual_char
            )
            char_prompt = SelectCharacterPrompt(
                text="Character to show",
                eligible_characters=all_chars,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_shown_character",
                    "stage": "st_post",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": default_wrong,
                    **({"correct": actual_char} if actual_char else {}),
                    "target_player_id": target.id,
                },
            )
            shown_char = engine.send_prompt(char_prompt)
            if not isinstance(shown_char, str) or not shown_char:
                shown_char = default_wrong
        elif spy_register_as is not None:
            # Sober+healthy Ravenkeeper on the Spy: show the Spy's
            # ST-chosen registration character.
            shown_char = spy_register_as
        else:
            shown_char = actual_char

        # INFORMATION: show on the Ravenkeeper's phone.
        info_text = f"You learn that {target.name} is the {shown_char}."
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[target.id],
                highlight_characters=[shown_char],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[target],
                data={"info": info_text, "shown_character": shown_char},
            )
        )

        # RESOLUTION: information-only ability, no further state change.
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
