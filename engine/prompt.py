"""Prompt classes.

A :class:`Prompt` represents a question the engine asks the
Storyteller (or, for INFORMATION prompts, a piece of info the engine
asks the Storyteller to show on a player's phone).

The engine creates a Prompt and hands it to the UI; the UI displays it
and, when the Storyteller answers, sends the response back. The engine
blocks until that happens.

Prompt subclasses
-----------------
* :class:`YesNoPrompt`         — the player has nodded/shaken their head.
* :class:`SelectPlayerPrompt`  — pick one (or several) players.
* :class:`SelectCharacterPrompt` — pick a character from a list.
* :class:`InformationPrompt`   — display info to the storyteller (and,
  if ``shown_to_player_id`` is set, to a player's phone). The only
  response is "Next".

Drunkenness / poisoning
-----------------------
For info-receiving abilities (Washerwoman, Empath, ...), if the source
player is drunk or poisoned the engine should first emit a
:class:`SelectCharacterPrompt` (or similar) asking the storyteller for
the false info to feed the player, and only then emit the
:class:`InformationPrompt` shown to the player.

For Player or Character prompts, the engine should populate
``eligible_*`` so the UI can highlight valid selections; the prompt
also exposes a "Randomize" affordance for the storyteller.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from engine.player import Player


class PromptType(str, Enum):
    YES_NO = "yes_no"
    SELECT_PLAYER = "select_player"
    SELECT_PLAYERS = "select_players"
    SELECT_CHARACTER = "select_character"
    INFORMATION = "information"


_prompt_id_counter = itertools.count(1)


@dataclass
class Prompt:
    """Base class for all prompts.

    All fields have defaults so subclasses (which inherit and add their
    own fields) can stay subscriptable; in particular this avoids the
    "non-default arg follows default arg" issue when a subclass overrides
    ``type`` with a default value.
    """

    type: PromptType = PromptType.INFORMATION
    text: str = ""
    # Whose ability triggered this prompt (so the UI can highlight the
    # active player's chair, or — for INFORMATION shown to a player —
    # decide whose phone to display the info on).
    target_player_id: Optional[int] = None
    # Free-form metadata (character name, ability name, night number, ...).
    meta: Dict[str, Any] = field(default_factory=dict)
    # Whether this prompt's text should be shown to the player on their
    # phone. When False, only the storyteller's local UI sees it.
    shown_to_player: bool = False
    # Whether the UI should offer a "Randomize" button for selection prompts.
    allow_randomize: bool = True

    id: int = field(default_factory=lambda: next(_prompt_id_counter))
    response: Any = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "text": self.text,
            "target_player_id": self.target_player_id,
            "meta": self.meta,
            "shown_to_player": self.shown_to_player,
            "allow_randomize": self.allow_randomize,
        }


@dataclass
class YesNoPrompt(Prompt):
    """A yes/no question. Response: True (yes) or False (no)."""

    type: PromptType = PromptType.YES_NO

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["allow_randomize"] = False
        return d


@dataclass
class SelectPlayerPrompt(Prompt):
    """Pick one or more players.

    ``eligible_player_ids`` lists the players the UI should highlight
    as valid selections (typically every player, but the Monk can't
    select themselves, the Fortune Teller picks two, etc.).

    Response: a single player id (when ``count == 1``) or a list of
    player ids.
    """

    type: PromptType = PromptType.SELECT_PLAYER
    eligible_player_ids: List[int] = field(default_factory=list)
    count: int = 1
    allow_self: bool = True

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["eligible_player_ids"] = list(self.eligible_player_ids)
        d["count"] = self.count
        d["allow_self"] = self.allow_self
        if self.count > 1:
            d["type"] = PromptType.SELECT_PLAYERS.value
        return d


@dataclass
class SelectCharacterPrompt(Prompt):
    """Pick a character from a list. Response: character name (str)."""

    type: PromptType = PromptType.SELECT_CHARACTER
    eligible_characters: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["eligible_characters"] = list(self.eligible_characters)
        return d


@dataclass
class InformationPrompt(Prompt):
    """Show information to the storyteller (and optionally a player).

    Response: any (the UI just needs the storyteller to click "Next").
    """

    type: PromptType = PromptType.INFORMATION
    # Players to highlight on the UI (e.g., the two players the
    # Washerwoman sees). Empty list means no highlight.
    highlight_player_ids: List[int] = field(default_factory=list)
    # Character names to display alongside the info (e.g. the
    # character token the Washerwoman is shown).
    highlight_characters: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["highlight_player_ids"] = list(self.highlight_player_ids)
        d["highlight_characters"] = list(self.highlight_characters)
        d["allow_randomize"] = False
        return d
