"""Exception hierarchy for the Clocktower engine."""

from __future__ import annotations


class ClocktowerError(Exception):
    """Base class for all engine errors."""


class InvalidPhaseError(ClocktowerError):
    """Raised when an action is attempted in the wrong phase.

    Example: calling ``nominate`` during the night phase.
    """


class InvalidActionError(ClocktowerError):
    """Raised when an action is structurally invalid.

    Example: trying to nominate before calling ``open_nominations``.
    """


class RuleViolationError(ClocktowerError):
    """Raised when an action violates a game rule.

    Example: a dead player attempting to nominate, or a player trying to
    nominate twice in one day.
    """


class PlayerNotFoundError(ClocktowerError):
    """Raised when a player ID does not match any seated player."""
