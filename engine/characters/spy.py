"""Spy.

    "Each night, you see the Grimoire. You might register as good and
     as a Townsfolk or Outsider, even if dead."

Two pieces:

  * **Grimoire reveal.** The Spy is woken every night and shown the
    full grimoire. We model this as a ``ShowInformation``-style prompt
    whose payload is the engine's storyteller-view snapshot. The Spy's
    phone renders the grimoire so they can study it.

  * **Misregistration.** The Spy may register as good and as any
    Townsfolk or Outsider, even when dead. As with the Recluse, this
    is a storyteller call surfaced via the override prompts on
    detection-style abilities (Empath, Investigator, Washerwoman,
    Fortune Teller, Chef, Undertaker, Ravenkeeper). The Spy class
    itself doesn't need a reaction — the override is built into each
    detector.

Drunkenness / poisoning: a drunk or poisoned Spy still sees the
grimoire (the rule isn't really an "ability", it's just exposure).
We pass through the prompt regardless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt

if TYPE_CHECKING:
    from engine.engine import Engine

class Spy(Character):
    name = "Spy"
    char_type = CharType.MINION
    ability_text = (
        "Each night, you see the Grimoire. You might register as good "
        "and as a Townsfolk or Outsider, even if dead."
    )
    first_night_order = 40
    other_night_order = 60
    reminder_tokens: list = []

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Show the Spy the grimoire. We pack the full snapshot into the
        # prompt's meta so the Spy's phone can render it. This is the
        # *one* place where the mobile UI legitimately sees other
        # players' character tokens — see ui/README.md "Information
        # hiding rules".
        snapshot = engine.snapshot()
        engine.send_prompt(
            InformationPrompt(
                text="Spy: study the grimoire.",
                target_player_id=self.player.id,
                shown_to_player=True,
                meta={
                    "character": self.name,
                    "step": "grimoire",
                    "stage": "info",
                    "grimoire": snapshot,
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[self.player],
                data={"info": "Spy saw the grimoire.", "grimoire": snapshot},
            )
        )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )
