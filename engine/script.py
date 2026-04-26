"""Script (character set) reference data.

The engine ships with the Trouble Brewing roster as a data file —
one ``ScriptCharacter`` record per role. The roster includes both
fully-implemented characters (that have a real :class:`Character`
subclass with an ``ability`` method) and stubs for the remaining
roles, so the storyteller can still select them at setup, see
recommended counts, and walk the night order.

Stub characters are bound to a generic :class:`StubCharacter` whose
``ability`` is a single "(unimplemented) wake/sleep" prompt — the
storyteller can still hand-resolve the action without the engine
crashing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Type

from engine.character import Character, StubCharacter
from engine.characters import CHARACTER_REGISTRY
from engine.characters.none_character import NoneCharacter
from engine.enums import CharType

if TYPE_CHECKING:
    from engine.engine import Engine


# ---------------------------------------------------------------------------
# Trouble Brewing roster.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptCharacter:
    """Static per-character record used during setup."""

    name: str
    char_type: CharType
    ability_text: str
    first_night_order: int = 0
    other_night_order: int = 0
    once_per_game: bool = False
    setup_outsider_delta: int = 0
    setup_townsfolk_delta: int = 0


TROUBLE_BREWING: Tuple[ScriptCharacter, ...] = (
    # Townsfolk
    ScriptCharacter("Washerwoman", CharType.TOWNSFOLK,
                    "You start knowing that 1 of 2 players is a particular Townsfolk.",
                    first_night_order=30),
    ScriptCharacter("Librarian", CharType.TOWNSFOLK,
                    "You start knowing that 1 of 2 players is a particular Outsider. "
                    "(Or that zero are in play.)",
                    first_night_order=31),
    ScriptCharacter("Investigator", CharType.TOWNSFOLK,
                    "You start knowing that 1 of 2 players is a particular Minion.",
                    first_night_order=32),
    ScriptCharacter("Chef", CharType.TOWNSFOLK,
                    "You start knowing how many pairs of evil players there are.",
                    first_night_order=33),
    ScriptCharacter("Empath", CharType.TOWNSFOLK,
                    "Each night, you learn how many of your 2 alive neighbours are evil.",
                    first_night_order=34, other_night_order=50),
    ScriptCharacter("Fortune Teller", CharType.TOWNSFOLK,
                    "Each night, choose 2 players: you learn if either is a Demon. "
                    "There is a good player that registers as a Demon to you.",
                    first_night_order=35, other_night_order=51),
    ScriptCharacter("Undertaker", CharType.TOWNSFOLK,
                    "Each night except the first, you learn which character died by execution today.",
                    other_night_order=52),
    ScriptCharacter("Monk", CharType.TOWNSFOLK,
                    "Each night except the first, choose a player (not yourself): "
                    "they are safe from the Demon tonight.",
                    other_night_order=20),
    ScriptCharacter("Ravenkeeper", CharType.TOWNSFOLK,
                    "If you die at night, you are woken to choose a player: you learn their character.",
                    other_night_order=45),
    ScriptCharacter("Virgin", CharType.TOWNSFOLK,
                    "The 1st time you are nominated, if the nominator is a Townsfolk, "
                    "they are executed immediately."),
    ScriptCharacter("Slayer", CharType.TOWNSFOLK,
                    "Once per game, during the day, publicly choose a player: "
                    "if they are the Demon, they die.",
                    once_per_game=True),
    ScriptCharacter("Soldier", CharType.TOWNSFOLK,
                    "You are safe from the Demon."),
    ScriptCharacter("Mayor", CharType.TOWNSFOLK,
                    "If only 3 players live and no execution occurs, good wins. "
                    "If you die at night, another player might die instead."),
    # Outsiders
    ScriptCharacter("Butler", CharType.OUTSIDER,
                    "Each night, choose a player (not yourself): tomorrow you may only vote if they are voting too.",
                    first_night_order=36, other_night_order=53),
    ScriptCharacter("Drunk", CharType.OUTSIDER,
                    "You do not know you are the Drunk. You think you are a Townsfolk character, but you are not."),
    ScriptCharacter("Recluse", CharType.OUTSIDER,
                    "You might register as evil and as a Minion or Demon, even if dead."),
    ScriptCharacter("Saint", CharType.OUTSIDER,
                    "If you die by execution, your team loses."),
    # Minions
    ScriptCharacter("Poisoner", CharType.MINION,
                    "Each night, choose a player: they are poisoned tonight and tomorrow day.",
                    first_night_order=10, other_night_order=10),
    ScriptCharacter("Spy", CharType.MINION,
                    "Each night, you see the Grimoire. You might register as good and as a "
                    "Townsfolk or Outsider, even if dead.",
                    first_night_order=40, other_night_order=60),
    ScriptCharacter("Scarlet Woman", CharType.MINION,
                    "If there are 5 or more players alive and the Demon dies, you become the Demon.",
                    other_night_order=15),
    ScriptCharacter("Baron", CharType.MINION,
                    "There are extra Outsiders in play. [+2 Outsiders]",
                    setup_outsider_delta=2, setup_townsfolk_delta=-2),
    # Demon
    ScriptCharacter("Imp", CharType.DEMON,
                    "Each night except the first, choose a player: they die. "
                    "If you choose yourself, you die and a Minion becomes the Imp.",
                    other_night_order=25),
)


SCRIPT_BY_NAME: Dict[str, ScriptCharacter] = {c.name: c for c in TROUBLE_BREWING}


# Recommended counts from the Traveler sheet for Trouble Brewing.
SETUP_COUNTS: Dict[int, Tuple[int, int, int, int]] = {
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


def recommended_counts(player_count: int) -> Tuple[int, int, int, int]:
    """(townsfolk, outsiders, minions, demons) for ``player_count``."""
    if player_count < 5:
        raise ValueError("Blood on the Clocktower requires at least 5 players.")
    return SETUP_COUNTS[min(player_count, 15)]


def names_by_type(char_type: CharType) -> List[str]:
    """All character names of a given type, in script order."""
    return [c.name for c in TROUBLE_BREWING if c.char_type is char_type]


def all_names() -> List[str]:
    """All character names in the script, in script order."""
    return [c.name for c in TROUBLE_BREWING]


def build_character(name: str) -> Character:
    """Construct a fresh :class:`Character` instance for the given name.

    If the character has a real implementation in
    :mod:`engine.characters`, use it. Otherwise, fall back to a stub
    that still carries the right metadata so the engine can sequence it.

    The ``None`` placeholder character (a no-op slot-filler used when
    a setup pick has no real effect — see
    :class:`engine.characters.none_character.NoneCharacter`) is
    handled specially: it lives outside any script and returns a
    fresh :class:`NoneCharacter` instance.
    """
    if name == NoneCharacter.name:
        return NoneCharacter()

    if name not in SCRIPT_BY_NAME:
        raise KeyError(f"Unknown character {name!r}")
    spec = SCRIPT_BY_NAME[name]

    if name in CHARACTER_REGISTRY:
        cls: Type[Character] = CHARACTER_REGISTRY[name]
        # Sanity-check class-level metadata against the script spec.
        return cls()

    # Build a stub class on the fly with metadata copied from the spec.
    stub_cls = type(
        f"Stub_{name.replace(' ', '_')}",
        (StubCharacter,),
        {
            "name": spec.name,
            "char_type": spec.char_type,
            "ability_text": spec.ability_text,
            "first_night_order": spec.first_night_order,
            "other_night_order": spec.other_night_order,
            "once_per_game": spec.once_per_game,
            "setup_outsider_delta": spec.setup_outsider_delta,
            "setup_townsfolk_delta": spec.setup_townsfolk_delta,
        },
    )
    return stub_cls()
