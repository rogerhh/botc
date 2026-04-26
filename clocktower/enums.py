"""Enumerations used throughout the Clocktower engine."""

from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    """Top-level phase of the game.

    The game progresses ``SETUP`` -> ``FIRST_NIGHT`` -> ``DAY`` ->
    ``NIGHT`` -> ``DAY`` -> ... -> ``FINISHED``. Day/night alternate
    after the first night until a win condition is met.
    """

    SETUP = "setup"
    FIRST_NIGHT = "first_night"
    DAY = "day"
    NIGHT = "night"
    FINISHED = "finished"

    @property
    def is_night(self) -> bool:
        return self in (Phase.FIRST_NIGHT, Phase.NIGHT)


class DayStage(str, Enum):
    """Sub-state within the ``DAY`` phase.

    ``DISCUSSION``     — free chat, no open nomination.
    ``NOMINATIONS_OPEN`` — storyteller has called for nominations.
    ``NOMINATION_ACTIVE`` — a player has been nominated; votes are being tallied.
    ``EXECUTION_PENDING`` — a player is "about to die" but not executed yet.
    ``CLOSED``         — day is wrapping up (execution resolved or skipped).
    """

    DISCUSSION = "discussion"
    NOMINATIONS_OPEN = "nominations_open"
    NOMINATION_ACTIVE = "nomination_active"
    EXECUTION_PENDING = "execution_pending"
    CLOSED = "closed"


class Alignment(str, Enum):
    """The team a player is currently on.

    A player's alignment is mostly fixed by their starting character but
    can change during play (e.g., Scarlet Woman becoming the Imp).
    """

    GOOD = "good"
    EVIL = "evil"

    @property
    def opposite(self) -> "Alignment":
        return Alignment.EVIL if self is Alignment.GOOD else Alignment.GOOD


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
        # Travelers are chosen by the storyteller; fabled are neutral.
        return Alignment.GOOD


class DeathCause(str, Enum):
    """Why a player died. Useful for abilities that care about cause."""

    EXECUTION = "execution"
    DEMON_KILL = "demon_kill"
    ABILITY = "ability"  # Slayer, Virgin, etc.
    EXILE = "exile"
    STORYTELLER = "storyteller"  # Catch-all for anything else.


class VoteChoice(str, Enum):
    """How a player voted on a given nomination."""

    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"  # Player had no opportunity / declined.
