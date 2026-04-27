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


class SetupMode(str, Enum):
    """Context in which a character's on-setup ability is running.

    The Storyteller's interaction surface differs between phase=SETUP
    (before the game starts) and the running game (after start_game):

    * ``SETUP_PHASE`` — phase=SETUP. The UI is in control. The on-setup
      ability *absorbs* whatever pool / chair / token state the
      storyteller has currently set, writing it onto Player + Character
      members. **No prompts are emitted.** A storyteller drag/drop on
      a token re-triggers the on-setup ability with this mode so the
      change is immediately reflected.

    * ``IN_GAME`` — phase has advanced past SETUP. The character is
      being instantiated (or re-instantiated mid-game, e.g. Scarlet
      Woman -> Imp). The on-setup ability **prompts the storyteller**
      for any picks that aren't already nailed down by pool / member
      state.
    """

    SETUP_PHASE = "setup_phase"
    IN_GAME = "in_game"
