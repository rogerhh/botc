"""Gambler.

    "Each night*, choose a player & guess their character: if you
     guess wrong, you die."

The Gambler picks a player and a character. If the chosen player's
seated role does not match the chosen character, the Gambler dies.
The Gambler does not learn whether the guess was correct.

Implementation
--------------
* Two-step nightly prompt: ``SelectPlayerPrompt`` (any player, alive
  or dead, including self) followed by ``SelectCharacterPrompt``
  over every role on the active script.
* If the picked player's actual ``character.name`` does not match
  the chosen character name AND the Gambler has its ability,
  :meth:`Engine.kill` is called on the Gambler with
  ``DeathCause.ABILITY``. Standard protections apply.
* A drunk/poisoned Gambler can guess but never dies as a result.

The Gambler does not learn the result. There is no
``InformationPrompt``; the storyteller silently knows whether the
guess was right.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectCharacterPrompt, SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class Gambler(Character):
    name = "Gambler"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night*, choose a player & guess their character: if you "
        "guess wrong, you die."
    )
    first_night_order = 0
    other_night_order = 41
    reminder_tokens: list = []

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT player.
        eligible = [p.id for p in engine.players]
        sel_p = SelectPlayerPrompt(
            text="Gambler picks a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_player",
                "stage": "player",
            },
        )
        target_id = engine.send_prompt(sel_p)
        if isinstance(target_id, list):
            target_id = target_id[0] if target_id else None
        if target_id is None:
            return
        try:
            target = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            return

        # SELECT character.
        sel_c = SelectCharacterPrompt(
            text=f"Gambler guesses the character of {target.name}",
            eligible_characters=engine.all_character_names(),
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_character",
                "stage": "player",
                "selected_player_id": target.id,
            },
        )
        guessed = engine.send_prompt(sel_c)
        if not isinstance(guessed, str) or not guessed:
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )
        # Goon notify: if the Gambler picked the Goon's seat, the
        # Goon drunkens the Gambler. The has_ability gate below then
        # short-circuits — a drunk Gambler doesn't actually evaluate
        # the guess, so the self-kill on a wrong guess does not fire
        # (matches the rulebook: drunk Gambler is immune).
        engine.notify_goon_chosen(self, target)

        # RESOLUTION: kill self on a wrong guess. Drunk/poisoned
        # Gambler is immune (no real ability).
        actual = target.character.name if target.character else None
        if (
            self.player.has_ability
            and actual is not None
            and guessed != actual
        ):
            engine.log(
                f"Gambler {self.player.name} guessed {target.name} as "
                f"{guessed} (actually {actual}) — dies."
            )
            engine.kill(
                self.player.id, DeathCause.ABILITY, source=self
            )
        else:
            engine.log(
                f"Gambler {self.player.name} guessed {target.name} as "
                f"{guessed} — no result published."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
