"""Undertaker.

    "Each night except the first, you learn which character died by
     execution today."

Information ability that fires from night 2 onward, but only if the
day immediately before today *had* an execution. Otherwise the
Undertaker is not woken.

The Undertaker uses :meth:`Character.registers_as` (with all four
character-type categories) to learn the executed player's
*registered* role. So a Spy executed today registers as a TF or
Outsider for the Undertaker (or as the Spy itself — ST's call), and a
Recluse executed today may register as a Minion or Demon.

Drunkenness / poisoning (per CLAUDE.md): storyteller may show any
character. Range of options, so the engine pre-picks a *random
wrong* character and surfaces it to the ST with a Next button.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Undertaker(Character):
    name = "Undertaker"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night except the first, you learn which character died by "
        "execution today."
    )
    first_night_order = 0
    other_night_order = 52
    reminder_tokens: list = [
        {"name": 'DIED TODAY', "icon": 'undertaker_died_today.png'},
    ]

    DETECTION_CATEGORIES = (
        CharType.TOWNSFOLK,
        CharType.OUTSIDER,
        CharType.MINION,
        CharType.DEMON,
    )

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._last_executed: Optional["Player"] = None
        self._last_executed_character: Optional[str] = None

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Mark the today-executed seat with the DIED TODAY token.

        Persists past the Undertaker's own ability state — the marker
        tracks the executed seat regardless of whether the Undertaker
        is still alive / sober.
        """
        if (
            self._last_executed is None
            or getattr(self._last_executed, "character", None) is None
        ):
            return {}
        return {"undertaker_died_today": [self._last_executed.id]}

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if not super().would_act_tonight(engine, night_number):
            return False
        return (
            self._last_executed is not None
            and self._last_executed_character is not None
        )

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        if event.type is EventType.DAY_START:
            self._last_executed = None
            self._last_executed_character = None
        elif event.type is EventType.EXECUTION and event.targets:
            target = event.targets[0]
            self._last_executed = target
            self._last_executed_character = (
                target.character.name if target.character else None
            )
        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        if self._last_executed is None or self._last_executed_character is None:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        # Ask the executed player's character what they register as
        # via a name-attribute check whose passes is the full character
        # roster (so any registration is a "pass"). The override on
        # Spy / Recluse may prompt the Storyteller; the resulting name
        # is what we display.
        from engine.check import Check
        registered_char: Optional[str] = None
        if (
            self._last_executed is not None
            and self._last_executed.character is not None
        ):
            name_check = Check(
                attribute="name",
                passes=tuple(engine.all_character_names()),
                detector_name=self.name,
                detector_player_id=self.player.id,
                extra_meta={"step_for": "undertaker_executed"},
            )
            registered_char = self._last_executed.character.registers_as(
                engine, name_check
            )

        # Sober + healthy: trust the registered character, no extra ST
        # prompt. Drunk/poisoned: range of options — pre-pick a random
        # *wrong* character and surface to ST with a Next button.
        if is_drunk_or_poisoned:
            all_chars = engine.all_character_names()
            wrong_options = [
                c for c in all_chars if c != self._last_executed_character
            ]
            default_wrong = (
                _rand.choice(wrong_options)
                if wrong_options else self._last_executed_character
            )
            char_prompt = SelectCharacterPrompt(
                text="Character to show",
                eligible_characters=all_chars,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_shown_character",
                    "stage": "st_pre",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": default_wrong,
                    **(
                        {"correct": self._last_executed_character}
                        if self._last_executed_character else {}
                    ),
                    "executed_player_id": self._last_executed.id,
                },
            )
            shown = engine.send_prompt(char_prompt)
            if not isinstance(shown, str) or not shown:
                shown = default_wrong
        elif registered_char is not None:
            shown = registered_char
        else:
            shown = self._last_executed_character

        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        info_text = (
            f"{self._last_executed.name} was executed today; "
            f"they were the {shown}."
        )
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[self._last_executed.id],
                highlight_characters=[shown],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    "render": {
                        "tokens": [{
                            "label": shown.upper(),
                            "body": self._last_executed.name,
                        }],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[self._last_executed],
                data={"info": info_text, "shown_character": shown},
            )
        )
        engine.dispatch(
            Event(
                EventType.RESOLUTION,
                source=self,
                targets=[self._last_executed],
            )
        )
