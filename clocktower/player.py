"""Player model.

A :class:`Player` tracks the state of one seated participant. The game
state machine owns a list of players (ordered clockwise by seat) and
mutates them through its public API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from clocktower.characters import Character
from clocktower.enums import Alignment, DeathCause


@dataclass
class Player:
    """A seated player.

    A player has two kinds of state: *identity* state (character,
    alignment, what they believe they are) and *condition* state (alive,
    drunk, poisoned, used-once-per-game ability, has-vote-token).

    Notes
    -----
    * A dead player keeps their ``character`` — death does not reveal it
      to other players, but the storyteller still tracks it.
    * Alignment is independent of character. A Scarlet Woman who becomes
      the Imp keeps her evil alignment; a Goon who changes alignment
      keeps their character.
    * ``perceived_character`` holds what a player *thinks* they are —
      used by the Drunk and (in other editions) the Lunatic.
    """

    id: int
    name: str
    seat: int  # 0-indexed clockwise position.

    # --- Identity ---
    character: Optional[Character] = None
    alignment: Optional[Alignment] = None
    perceived_character: Optional[Character] = None

    # --- Life / death ---
    alive: bool = True
    death_cause: Optional[DeathCause] = None
    has_vote_token: bool = True  # Dead players lose this after voting once.

    # --- Condition flags ---
    drunk: bool = False
    poisoned: bool = False
    # Whether the player has used their once-per-game ability.
    used_once_per_game: bool = False

    # --- Day-scoped flags, reset by Game at the start of each day ---
    has_nominated_today: bool = False
    has_been_nominated_today: bool = False

    # --- Night-scoped flags, reset by Game at the start of each night ---
    awake: bool = False
    protected_from_demon: bool = False  # Set when the Monk protects.

    # --- Free-form notes kept by the storyteller ---
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Derived properties.
    # ------------------------------------------------------------------

    @property
    def dead(self) -> bool:
        return not self.alive

    @property
    def has_ability(self) -> bool:
        """A player has a working ability iff alive, sober, and healthy."""
        return self.alive and not self.drunk and not self.poisoned

    @property
    def can_nominate(self) -> bool:
        """Alive players who haven't nominated today may nominate."""
        return self.alive and not self.has_nominated_today

    @property
    def can_be_nominated(self) -> bool:
        """Both alive and dead players can be nominated, but only once per day each."""
        return not self.has_been_nominated_today

    @property
    def can_vote(self) -> bool:
        """Alive players always can; dead players may only if they have a vote token."""
        if self.alive:
            return True
        return self.has_vote_token

    # ------------------------------------------------------------------
    # Mutators (kept here so Game never reaches directly into the dataclass).
    # ------------------------------------------------------------------

    def kill(self, cause: DeathCause) -> None:
        """Mark the player as dead. Idempotent: does nothing if already dead."""
        if not self.alive:
            return
        self.alive = False
        self.death_cause = cause
        # Dead players keep one vote (the vote token) until they use it.

    def cast_vote_token(self) -> None:
        """A dead player spends their one remaining vote."""
        self.has_vote_token = False

    def reset_day_flags(self) -> None:
        self.has_nominated_today = False
        self.has_been_nominated_today = False

    def reset_night_flags(self) -> None:
        self.awake = False
        self.protected_from_demon = False

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    # ------------------------------------------------------------------
    # Debug / serialization.
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """A JSON-serializable view of the player (for UIs / logging)."""
        return {
            "id": self.id,
            "name": self.name,
            "seat": self.seat,
            "character": self.character.name if self.character else None,
            "perceived_character": (
                self.perceived_character.name if self.perceived_character else None
            ),
            "alignment": self.alignment.value if self.alignment else None,
            "alive": self.alive,
            "death_cause": self.death_cause.value if self.death_cause else None,
            "has_vote_token": self.has_vote_token,
            "drunk": self.drunk,
            "poisoned": self.poisoned,
            "used_once_per_game": self.used_once_per_game,
        }
