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
    # Fired immediately after ``PRE_DEATH`` *only if* no standard
    # protection cancelled it. This is the "last-resort" save pass
    # reserved for self-save abilities that must defer to every other
    # protector — currently the Zombuul's first-life save and the
    # Fool's once-per-game save. It shares the same ``data`` dict as
    # the originating ``PRE_DEATH`` so a cancellation
    # (``data["cancelled"] = True``) propagates back and is observed
    # by ``Engine.kill`` / ``Engine.execute_player``. Standard
    # cancellers (Innkeeper SAFE, Soldier, Mayor redirect, Tea Lady,
    # Sailor, Pacifist, Devil's Advocate) only react to ``PRE_DEATH``;
    # this guarantees a Zombuul / Fool's slot is spent only when the
    # death would otherwise actually land.
    PRE_DEATH_LAST_RESORT = "pre_death_last_resort"
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
    # Fired by ``Engine.change_character`` *before* a player's
    # Character class is swapped. ``targets`` is the affected Player;
    # ``data["new_character"]`` is the incoming role's name. The OLD
    # character instance is still wired to the player at dispatch
    # time, so it gets a chance to clean up any persistent effects it
    # has placed (the Poisoner's poison, etc.) before being discarded.
    CHARACTER_CHANGE = "character_change"
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

    # Effect-registry lifecycle (Layer 1 foundation; emitted by
    # ``Engine.add_effect`` / ``purge_effect`` / state transitions in
    # the resolver). Useful for character reactions that want to react
    # to the moment a poison appears, not the underlying state flip.
    # Most characters won't subscribe to these.
    EFFECT_ADDED = "effect_added"
    EFFECT_PURGED = "effect_purged"
    EFFECT_ACTIVATED = "effect_activated"
    EFFECT_DEACTIVATED = "effect_deactivated"

    # Alignment changes (Goon allegiance flip, future characters that
    # rotate good/evil). Tea Lady's protection re-evaluates on this.
    ALIGNMENT_CHANGE = "alignment_change"


class EventOutcome(Enum):
    """Phase-1 (effect) resolution outcomes for ``Effect.resolve_event``.

    The engine's two-phase event dispatch walks every *active* effect
    targeting the event's subjects (phase 1) before running character
    reactions (phase 2). An effect's ``resolve_event`` returns one of
    these to indicate how the engine should treat the event.

    * ``CANCEL`` — event is cancelled. The dispatcher sets
      ``event.data["cancelled"] = True`` (matching the legacy
      reaction-side cancellation shape) and stops further resolution.
    * ``REDIRECT`` — event continues with new targets. The effect
      mutates ``event.targets`` *before* returning REDIRECT; the
      dispatcher restarts the phase-1 walk against the new targets.
    * Returning ``None`` (the default) abstains — the effect didn't
      have anything to say about this event.
    """

    CANCEL = "cancel"
    REDIRECT = "redirect"


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
