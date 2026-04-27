"""Undertaker.

    "Each night except the first, you learn which character died by
     execution today."

Information ability that fires from night 2 onward, but only if the
day immediately before today *had* an execution. Otherwise the
Undertaker is not woken.

Implementation
--------------
The Undertaker tracks the most recently executed player by reacting
to ``EXECUTION`` events during the day. At dusk (NIGHT_START), if no
execution has been recorded for today, ``ability`` short-circuits.

Drunkenness / poisoning (per CLAUDE.md): storyteller may show any
character. Range of options, so the engine pre-picks a *random
wrong* character (anything other than the executed player's true
character) and surfaces it to the ST with a Next button. The ST may
change the pick before it goes to the player.
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

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Most recent player executed during the day. Cleared at the
        # next DAY_START so an Undertaker who was woken last night and
        # had no execution today doesn't see stale info.
        self._last_executed: Optional["Player"] = None
        self._last_executed_character: Optional[str] = None

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        # Undertaker only acts if there was an execution today.
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
            # Capture the character name *now* — the player's character
            # might be swapped before the Undertaker wakes (e.g.
            # Scarlet Woman becoming the Demon).
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
            # No execution today — Undertaker doesn't wake.
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        # Spy misregistration: if the executed player is the Spy, ask
        # the Storyteller what character the Spy registers as for the
        # Undertaker. The default is the Spy's internally-tracked
        # preferred good character (or "Spy" itself, which would show
        # the Undertaker the Spy token). This takes precedence over the
        # default "show the actual character" path; if the Undertaker
        # is also drunk/poisoned, the drunk/poisoned override below
        # still gets a chance to change the answer.
        spy_register_as: Optional[str] = None
        if (
            self._last_executed is not None
            and self._last_executed.character is not None
            and self._last_executed.character.name == "Spy"
        ):
            from engine.characters.spy import (
                prompt_spy_register_as as _prompt_spy_register_as,
            )
            spy_register_as = _prompt_spy_register_as(
                engine,
                self._last_executed,
                detector_name=self.name,
                detector_player_id=self.player.id,
                text="Spy registers as (Undertaker)",
                extra_meta={"step_for": "undertaker_executed"},
            )

        # Sober + healthy: trust the executed player's actual character,
        # no ST prompt. Drunk/poisoned: range of options — pre-pick a
        # random *wrong* character and surface to ST with a Next button.
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
        elif spy_register_as is not None:
            # Sober+healthy Undertaker on the Spy: show the Spy's
            # ST-chosen registration character.
            shown = spy_register_as
        else:
            shown = self._last_executed_character

        # WAKEUP — pre-wake pick locked in; physically wake the Undertaker.
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
