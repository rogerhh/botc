"""Event objects.

An ``Event`` is anything that can trigger a character's reaction. The
engine dispatches events to every Character's ``reaction(event)`` method.

A character's ability is implemented as a sequence of events, conceptually
of five types:

    1. CHECK_CONDITION  — does the character's ability trigger this night?
    2. WAKEUP           — the storyteller wakes the player.
    3. SELECT           — the player makes a selection (or the ST does).
    4. INFORMATION      — the storyteller gives information to the player.
    5. RESOLUTION       — the ability's effect lands (poisoning, killing, etc).

Not every ability uses all five (e.g. the Poisoner has no INFORMATION;
day-time abilities have no WAKEUP). Other event types describe non-ability
state changes (death, execution, nomination, etc.) so any character can
react to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from engine.character import Character
    from engine.player import Player


class EventType(str, Enum):
    """Types of events that can be dispatched."""

    # Ability lifecycle.
    CHECK_CONDITION = "check_condition"
    WAKEUP = "wakeup"
    SELECT = "select"
    INFORMATION = "information"
    RESOLUTION = "resolution"

    # Game-state changes other characters may want to react to.
    # Fired *before* the death actually lands (after Soldier/Monk
    # protection, but before ``player.kill()`` flips ``alive`` to
    # False). A reaction may set ``event.data["cancelled"] = True``
    # to abort the kill — used by the Mayor's night-death redirect
    # so the Mayor never transiently appears dead. ``data["cause"]``
    # carries the original ``DeathCause``.
    PRE_DEATH = "pre_death"
    DEATH = "death"
    EXECUTION = "execution"
    NOMINATION = "nomination"
    POISON = "poison"
    DRUNK = "drunk"
    REVIVE = "revive"
    NIGHT_START = "night_start"
    NIGHT_END = "night_end"
    DAY_START = "day_start"
    DAY_END = "day_end"
    # Fired by the night loop right before a character's ability runs.
    # Carries the preset step's name + description in ``data`` so the
    # UI can highlight the character about to act and surface the
    # rulebook line, without the engine having to emit a separate
    # storyteller-facing prompt.
    STEP_START = "step_start"

    # Pre-game-start setup.
    SETUP_START = "setup_start"
    SETUP_END = "setup_end"
    SETUP_PICK = "setup_pick"


@dataclass
class Event:
    """An action/state-change in the game.

    Attributes
    ----------
    type:
        The kind of event (see :class:`EventType`).
    source:
        The Character whose ability produced this event (``None`` for
        engine-level events like ``NIGHT_START``).
    targets:
        The Player(s) the event acts on (e.g. the player being woken or
        selected). May be empty.
    data:
        Free-form data the event carries (e.g. ``{"info": "..."}`` for
        INFORMATION events, or ``{"cause": DeathCause.DEMON_KILL}`` for
        DEATH events).
    """

    type: EventType
    source: Optional["Character"] = None
    targets: List["Player"] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover  (debug output)
        src = self.source.name if self.source else "—"
        tgt = ", ".join(p.name for p in self.targets) or "—"
        return f"<Event {self.type.value} src={src} tgt={tgt} data={self.data}>"
