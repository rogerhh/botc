"""Character definitions for the Trouble Brewing edition.

Each :class:`Character` is reference data: the state machine cares about
its ``type`` (which affects the setup counts on the Traveler sheet), its
night order (so the storyteller knows when to wake which player), and a
few setup-time modifiers (e.g., the Baron adds outsiders). The *effect*
of a character's ability is still adjudicated by the storyteller; the
engine simply provides the scaffolding to record and announce it.

Night-order values are copied from the Trouble Brewing night sheet.
``first_night_order = 0`` or ``other_night_order = 0`` means the
character does not act in that phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from clocktower.enums import Alignment, CharType


@dataclass(frozen=True)
class Character:
    """Static data for a single character.

    Attributes
    ----------
    name:
        Display name (e.g., ``"Fortune Teller"``).
    type:
        The character's type — determines default alignment and counts.
    ability:
        Short plain-language description of the character's ability.
    first_night_order:
        Position in the first-night wake order. ``0`` means skip.
    other_night_order:
        Position in the other-nights wake order. ``0`` means skip.
    once_per_game:
        ``True`` for abilities usable a single time total (e.g. Slayer).
    setup_outsider_delta:
        Net change to the number of outsiders at setup (e.g. Baron = +2).
    setup_townsfolk_delta:
        Net change to the number of townsfolk at setup (e.g. Baron = -2).
    notes:
        Extra notes relevant to the storyteller (e.g., "Drunk thinks
        they are a Townsfolk").
    """

    name: str
    type: CharType
    ability: str
    first_night_order: int = 0
    other_night_order: int = 0
    once_per_game: bool = False
    setup_outsider_delta: int = 0
    setup_townsfolk_delta: int = 0
    notes: str = ""

    @property
    def default_alignment(self) -> Alignment:
        return self.type.default_alignment

    @property
    def acts_first_night(self) -> bool:
        return self.first_night_order > 0

    @property
    def acts_other_nights(self) -> bool:
        return self.other_night_order > 0


# ---------------------------------------------------------------------------
# Trouble Brewing roster.
# ---------------------------------------------------------------------------
# The night-order numbers below are relative rankings (lower = earlier).
# They are used only for sorting; they need not be contiguous.

TROUBLE_BREWING: tuple[Character, ...] = (
    # --- Townsfolk (13) ---
    Character(
        name="Washerwoman",
        type=CharType.TOWNSFOLK,
        ability="You start knowing that 1 of 2 players is a particular Townsfolk.",
        first_night_order=30,
    ),
    Character(
        name="Librarian",
        type=CharType.TOWNSFOLK,
        ability="You start knowing that 1 of 2 players is a particular Outsider. "
                "(Or that zero are in play.)",
        first_night_order=31,
    ),
    Character(
        name="Investigator",
        type=CharType.TOWNSFOLK,
        ability="You start knowing that 1 of 2 players is a particular Minion.",
        first_night_order=32,
    ),
    Character(
        name="Chef",
        type=CharType.TOWNSFOLK,
        ability="You start knowing how many pairs of evil players there are.",
        first_night_order=33,
    ),
    Character(
        name="Empath",
        type=CharType.TOWNSFOLK,
        ability="Each night, you learn how many of your 2 alive neighbors are evil.",
        first_night_order=34,
        other_night_order=50,
    ),
    Character(
        name="Fortune Teller",
        type=CharType.TOWNSFOLK,
        ability="Each night, choose 2 players: you learn if either is a Demon. "
                "There is a good player that registers as a Demon to you.",
        first_night_order=35,
        other_night_order=51,
    ),
    Character(
        name="Undertaker",
        type=CharType.TOWNSFOLK,
        ability="Each night* (except the first), you learn which character "
                "died by execution today.",
        other_night_order=52,
    ),
    Character(
        name="Monk",
        type=CharType.TOWNSFOLK,
        ability="Each night* (except the first), choose a player (not yourself): "
                "they are safe from the Demon tonight.",
        other_night_order=20,
    ),
    Character(
        name="Ravenkeeper",
        type=CharType.TOWNSFOLK,
        ability="If you die at night, you are woken to choose a player: "
                "you learn their character.",
        other_night_order=45,
    ),
    Character(
        name="Virgin",
        type=CharType.TOWNSFOLK,
        ability="The 1st time you are nominated, if the nominator is a "
                "Townsfolk, they are executed immediately.",
    ),
    Character(
        name="Slayer",
        type=CharType.TOWNSFOLK,
        ability="Once per game, during the day, publicly choose a player: "
                "if they are the Demon, they die.",
        once_per_game=True,
    ),
    Character(
        name="Soldier",
        type=CharType.TOWNSFOLK,
        ability="You are safe from the Demon.",
    ),
    Character(
        name="Mayor",
        type=CharType.TOWNSFOLK,
        ability="If only 3 players live and no execution occurs, good wins. "
                "If you die at night, another player might die instead.",
    ),

    # --- Outsiders (4) ---
    Character(
        name="Butler",
        type=CharType.OUTSIDER,
        ability="Each night, choose a player (not yourself): tomorrow, "
                "you may only vote if they are voting too.",
        first_night_order=36,
        other_night_order=53,
    ),
    Character(
        name="Drunk",
        type=CharType.OUTSIDER,
        ability="You do not know you are the Drunk. You think you are a "
                "Townsfolk character, but you are not.",
        notes="Given to a player in place of a Townsfolk; they believe they are that Townsfolk.",
    ),
    Character(
        name="Recluse",
        type=CharType.OUTSIDER,
        ability="You might register as evil and as a Minion or Demon, "
                "even if dead.",
    ),
    Character(
        name="Saint",
        type=CharType.OUTSIDER,
        ability="If you die by execution, your team loses.",
    ),

    # --- Minions (4) ---
    Character(
        name="Poisoner",
        type=CharType.MINION,
        ability="Each night, choose a player: they are poisoned tonight "
                "and tomorrow day.",
        first_night_order=10,
        other_night_order=10,
    ),
    Character(
        name="Spy",
        type=CharType.MINION,
        ability="Each night, you see the Grimoire. You might register as "
                "good and as a Townsfolk or Outsider, even if dead.",
        first_night_order=40,
        other_night_order=60,
    ),
    Character(
        name="Scarlet Woman",
        type=CharType.MINION,
        ability="If there are 5 or more players alive and the Demon dies, "
                "you become the Demon.",
        other_night_order=15,
    ),
    Character(
        name="Baron",
        type=CharType.MINION,
        ability="There are extra Outsiders in play. [+2 Outsiders]",
        setup_outsider_delta=2,
        setup_townsfolk_delta=-2,
    ),

    # --- Demon (1) ---
    Character(
        name="Imp",
        type=CharType.DEMON,
        ability="Each night*, choose a player: they die. If you choose "
                "yourself, you die and a Minion becomes the Imp.",
        other_night_order=25,
    ),
)


CHARACTERS: Dict[str, Character] = {c.name: c for c in TROUBLE_BREWING}


def get_character(name: str) -> Character:
    """Return a character by name; raise ``KeyError`` if unknown."""
    return CHARACTERS[name]


def characters_by_type(type_: CharType) -> list[Character]:
    """Return all characters of the given type."""
    return [c for c in TROUBLE_BREWING if c.type is type_]


# Recommended character counts from the Traveler sheet for Trouble Brewing.
# Key is the player count (5..15+); value is (townsfolk, outsiders, minions, demons).
SETUP_COUNTS: Dict[int, tuple[int, int, int, int]] = {
    5:  (3, 0, 1, 1),
    6:  (3, 1, 1, 1),
    7:  (5, 0, 1, 1),
    8:  (5, 1, 1, 1),
    9:  (5, 2, 1, 1),
    10: (7, 0, 2, 1),
    11: (7, 1, 2, 1),
    12: (7, 2, 2, 1),
    13: (9, 0, 3, 1),
    14: (9, 1, 3, 1),
    15: (9, 2, 3, 1),
}


def recommended_counts(player_count: int) -> tuple[int, int, int, int]:
    """Return the (townsfolk, outsiders, minions, demons) count for ``player_count``.

    For 15 or more players, the 15-player counts are used (the engine does
    not attempt to scale Travelers automatically).
    """
    if player_count < 5:
        raise ValueError("Blood on the Clocktower requires at least 5 players.")
    return SETUP_COUNTS[min(player_count, 15)]
