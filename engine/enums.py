"""Enumerations used throughout the engine."""

from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    """Top-level phase of the game."""

    SETUP = "setup"
    FIRST_NIGHT = "first_night"
    DAY = "day"
    NIGHT = "night"
    FINISHED = "finished"

    @property
    def is_night(self) -> bool:
        return self in (Phase.FIRST_NIGHT, Phase.NIGHT)


class Alignment(str, Enum):
    """The team a player is on."""

    GOOD = "good"
    EVIL = "evil"


class CharType(str, Enum):
    """The category a character belongs to."""

    TOWNSFOLK = "townsfolk"
    OUTSIDER = "outsider"
    MINION = "minion"
    DEMON = "demon"
    TRAVELER = "traveler"
    FABLED = "fabled"

    @property
    def default_alignment(self) -> Alignment:
        if self in (CharType.TOWNSFOLK, CharType.OUTSIDER):
            return Alignment.GOOD
        if self in (CharType.MINION, CharType.DEMON):
            return Alignment.EVIL
        return Alignment.GOOD


class DeathCause(str, Enum):
    """Why a player died. Useful for abilities that care about cause."""

    EXECUTION = "execution"
    DEMON_KILL = "demon_kill"
    ABILITY = "ability"
    STORYTELLER = "storyteller"
