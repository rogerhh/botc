"""Blood on the Clocktower — core game engine.

This package provides a state machine that models a Blood on the Clocktower
game and exposes a Storyteller-facing API to drive it. The engine is
deliberately unopinionated about transport: a web server, CLI, or test
harness can all drive the same ``Game`` instance.

The ``Game`` class is the main entry point. See its docstring for an
overview of the storyteller workflow.
"""

from clocktower.characters import Character, CHARACTERS, get_character
from clocktower.enums import (
    Alignment,
    CharType,
    DeathCause,
    Phase,
)
from clocktower.exceptions import (
    ClocktowerError,
    InvalidActionError,
    InvalidPhaseError,
    PlayerNotFoundError,
    RuleViolationError,
)
from clocktower.game import Game, Nomination
from clocktower.player import Player

__all__ = [
    "Alignment",
    "CharType",
    "Character",
    "CHARACTERS",
    "ClocktowerError",
    "DeathCause",
    "Game",
    "InvalidActionError",
    "InvalidPhaseError",
    "Nomination",
    "Phase",
    "Player",
    "PlayerNotFoundError",
    "RuleViolationError",
    "get_character",
]
