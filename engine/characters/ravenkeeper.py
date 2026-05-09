"""Ravenkeeper.

    "If you die at night, you are woken to choose a player: you learn
     their character."

The Ravenkeeper acts in the other-nights order, but only triggers
when they died this night — i.e. the player is in the engine's
``pending_night_deaths`` list. Cause does not matter: a Demon kill,
an ability-cause death (Grandmother grief, Tinker, etc.), or a
storyteller-attributed night death all arm the ability.
``DeathCause.EXECUTION`` cannot reach this branch because executions
land during the day and are never appended to ``pending_night_deaths``.

The Ravenkeeper uses :meth:`Character.registers_as` (with all four
character-type categories) to learn the chosen player's *registered*
role. Spy / Recluse overrides may fire and prompt the Storyteller.

If the Ravenkeeper was sober and healthy at death, the registered
character is shown as-is. If drunk or poisoned at the time of death,
the engine pre-picks a *random wrong* character per CLAUDE.md and
surfaces it to the storyteller with a Next button.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
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

    DETECTION_CATEGORIES = (
        CharType.TOWNSFOLK,
        CharType.OUTSIDER,
        CharType.MINION,
        CharType.DEMON,
    )

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if night_number == 1:
            return False
        if self.player is None:
            return False
        # Any death at night arms the Ravenkeeper. The
        # ``pending_night_deaths`` membership is the canonical
        # "died this night" gate — executions never appear there
        # (engine.kill skips appending when cause is EXECUTION,
        # and engine.execute_player runs only during day anyway).
        if self.player not in engine.pending_night_deaths:
            return False
        return True

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1:
            return
        if self.player is None or self.player.alive:
            return
        if self.player not in engine.pending_night_deaths:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

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
            target_id = target_id[0]
        target = engine.get_player(target_id)

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )
        # Goon notify: if the Ravenkeeper picked the Goon's seat, the
        # Goon drunkens the Ravenkeeper. ``is_drunk_or_poisoned`` is
        # captured AFTER the notify so the wrong-info ST prompt path
        # below picks up the new state.
        engine.notify_goon_chosen(self, target)

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        actual_char = target.character.name if target.character else None

        # Ask the chosen target what they register as via a name
        # check. Spy / Recluse overrides may prompt the Storyteller.
        from engine.check import Check
        registered_char = actual_char
        if target.character is not None:
            name_check = Check(
                attribute="name",
                passes=tuple(engine.all_character_names()),
                detector_name=self.name,
                detector_player_id=self.player.id,
                extra_meta={"step_for": "ravenkeeper_target"},
            )
            registered_char = target.character.registers_as(
                engine, name_check
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
        else:
            shown_char = registered_char

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
                    "render": {
                        "tokens": [{
                            "label": shown_char.upper(),
                            "body": target.name,
                        }],
                    },
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
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
