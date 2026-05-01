"""Artist.

    "Once per game, during the day, privately ask the Storyteller any
     yes/no question."

Daytime once-per-game ability. The Artist's "ability" is a private
verbal exchange between the player and the Storyteller — the player
asks any yes/no question and the Storyteller honestly answers "yes,"
"no," or "I don't know." None of that conversation is mediated by the
engine; it happens in the room.

The engine's only job is to surface the ability button on the Artist's
side panel during the day, and — when the storyteller clicks it —
mark the ability as spent so the Artist can't ask a second question.
The grimoire shows the ``NO ABILITY`` reminder token
(``artist_no_ability.png``) on the Artist's seat once the ability has
been used.

Drunkenness / poisoning: the slot is still consumed (per the
rulebook). The Storyteller is responsible for giving wrong information
in the private exchange, so the engine has nothing to fake — clicking
"use ability" just marks the slot spent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType

if TYPE_CHECKING:
    from engine.engine import Engine


class Artist(Character):
    name = "Artist"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Once per game, during the day, privately ask the Storyteller "
        "any yes/no question."
    )
    first_night_order = 0
    other_night_order = 0
    once_per_game = True
    reminder_tokens: list = [
        {"name": 'NO ABILITY', "icon": 'artist_no_ability.png'},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._used: bool = False

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Persistent NO ABILITY marker once the Artist has asked their question.

        Survives the Artist's own death — the marker tracks the seat,
        not the source's current ability state, so a revived Artist
        can't ask a second question.
        """
        if self.player is None or not self._used:
            return {}
        return {"artist_no_ability": [self.player.id]}

    def daytime_ability(self, engine: "Engine") -> None:
        """Mark the Artist's once-per-game question as spent.

        The actual yes/no exchange happens privately between the player
        and the Storyteller in the room — the engine only flips the
        ability-spent flag and lets the grimoire render the
        ``artist_no_ability`` reminder token on the Artist's seat.
        """
        if self.player is None or self.player.dead:
            return
        if self._used:
            engine.log(
                f"Artist {self.player.name} tried to ask but ability "
                f"is already spent."
            )
            return

        # Consume the slot. Per the rulebook the question is spent
        # whether or not the Artist's ability actually works (drunk /
        # poisoned Artists still get one question with the ST giving
        # wrong info verbally).
        self._used = True
        if self.player is not None:
            self.player.once_per_game_used = True

        engine.log(
            f"Artist {self.player.name} asked their once-per-game "
            f"question — ability marked spent."
        )
