"""Player model.

A :class:`Player` tracks the state of one seated participant. The Player
*owns* their Character (the Character can change mid-game, e.g. when the
Scarlet Woman becomes the Imp, but the Player object stays the same).

States that live on the Player (not the Character) include:

    * alignment              (good / evil)
    * char_type              (mirror of character.type, but kept on the
                              Player so it's preserved if the character
                              is swapped or revealed late)
    * alive / death_cause
    * drunk
    * poisoned
    * has_dead_vote          (a dead player gets one final vote token)
    * is_first_night         (true for the first night)
    * once_per_game_used     (resets if the player changes character)
    * protected_from_demon   (per-night flag, e.g. set by the Monk)

State transitions are reachable through the methods on Player so the
engine never reaches into the dataclass directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from engine.enums import Alignment, CharType, DeathCause

if TYPE_CHECKING:
    from engine.character import Character


@dataclass
class Player:
    id: int
    name: str
    seat: int = 0  # 0-indexed clockwise seat in the town square.

    # --- Identity ---
    # The Player owns its Character. Use change_character() to swap.
    character: Optional["Character"] = None
    # What this player believes they are (Drunk: thinks they're a TF).
    perceived_character_name: Optional[str] = None
    alignment: Optional[Alignment] = None
    char_type: Optional[CharType] = None

    # --- Life / death ---
    alive: bool = True
    death_cause: Optional[DeathCause] = None
    has_dead_vote: bool = True  # dead players keep one vote token

    # --- Condition flags ---
    drunk: bool = False
    poisoned: bool = False
    once_per_game_used: bool = False
    mad_about: List[str] = field(default_factory=list)  # things they must claim

    # --- Per-night flags (cleared by Engine each night) ---
    awake: bool = False
    protected_from_demon: bool = False

    # --- Per-day flags (cleared by Engine each day) ---
    has_nominated_today: bool = False
    has_been_nominated_today: bool = False

    # --- Free-form storyteller notes ---
    notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Derived properties.
    # ------------------------------------------------------------------

    @property
    def dead(self) -> bool:
        return not self.alive

    @property
    def has_ability(self) -> bool:
        """A player's ability functions iff alive, sober, and healthy."""
        return self.alive and not self.drunk and not self.poisoned

    @property
    def is_evil(self) -> bool:
        return self.alignment is Alignment.EVIL

    @property
    def is_good(self) -> bool:
        return self.alignment is Alignment.GOOD

    @property
    def can_nominate(self) -> bool:
        return self.alive and not self.has_nominated_today

    @property
    def can_be_nominated(self) -> bool:
        return not self.has_been_nominated_today

    @property
    def can_vote(self) -> bool:
        if self.alive:
            return True
        return self.has_dead_vote

    # ------------------------------------------------------------------
    # State mutators.
    # ------------------------------------------------------------------

    def assign_character(self, character: "Character") -> None:
        """Wire a freshly-built Character to this Player."""
        self.character = character
        self.char_type = character.char_type
        if self.alignment is None:
            self.alignment = character.char_type.default_alignment
        # Reset once-per-game on character change.
        self.once_per_game_used = False
        character.player = self

    def change_character(self, character: "Character") -> None:
        """Change to a different character mid-game (e.g., SW -> Imp).

        Resets once_per_game_used so the new character can use its
        own ability afresh. Alignment is preserved.
        """
        self.character = character
        self.char_type = character.char_type
        self.once_per_game_used = False
        character.player = self

    def kill(self, cause: DeathCause = DeathCause.STORYTELLER) -> None:
        if not self.alive:
            return
        self.alive = False
        self.death_cause = cause

    def revive(self) -> None:
        self.alive = True
        self.death_cause = None
        self.has_dead_vote = True
        # Once-per-game refreshes on revive (per engine README).
        self.once_per_game_used = False

    def use_dead_vote(self) -> None:
        self.has_dead_vote = False

    def set_drunk(self, drunk: bool = True) -> None:
        self.drunk = drunk

    def set_poisoned(self, poisoned: bool = True) -> None:
        self.poisoned = poisoned

    def drunk_poison_label(self) -> Optional[str]:
        """Return a short label for this player's drunk/poisoned state.

        ``"drunk"``, ``"poisoned"``, ``"drunk and poisoned"``, or
        ``None`` if neither flag is set. Used by info-character prompts
        to surface the state in the storyteller UI title.
        """
        parts = []
        if self.drunk:
            parts.append("drunk")
        if self.poisoned:
            parts.append("poisoned")
        if not parts:
            return None
        return " and ".join(parts)

    def reset_night_flags(self) -> None:
        self.awake = False
        self.protected_from_demon = False

    def reset_day_flags(self) -> None:
        self.has_nominated_today = False
        self.has_been_nominated_today = False

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    # ------------------------------------------------------------------
    # Serialization.
    # ------------------------------------------------------------------

    def snapshot(self, *, hide_character: bool = False) -> dict:
        """JSON-serializable view of the player.

        ``hide_character`` is used when serializing to a player's phone
        (the player should only see their *perceived* character).

        The full snapshot is what powers the Storyteller's per-player
        side panel: every state the engine tracks on a player surfaces
        here so the panel can be a pure read of engine state with no
        UI-side bookkeeping (see ``ui/README.md`` "Player side panel").
        """
        char_name = self.character.name if self.character else None
        if hide_character:
            char_name = self.perceived_character_name or char_name

        # Detect a real (overridden) daytime_ability so the side panel
        # can decide whether to surface the "Use ability" button. The
        # base ``Character.daytime_ability`` is a no-op; a subclass
        # bound method that points at the base function means the
        # character has no day action (Empath, Soldier, ...).
        has_day_ability = False
        once_per_game = False
        if self.character is not None:
            from engine.character import Character as _Char
            try:
                bound = self.character.daytime_ability
                base = _Char.daytime_ability
                # ``__func__`` strips the ``self`` binding so this
                # comparison is "is this the unmodified base method?".
                has_day_ability = (
                    getattr(bound, "__func__", bound) is not base
                )
            except Exception:  # pragma: no cover (defensive)
                has_day_ability = False
            once_per_game = bool(getattr(self.character, "once_per_game", False))

        return {
            "id": self.id,
            "name": self.name,
            "seat": self.seat,
            "character": char_name,
            "perceived_character": self.perceived_character_name,
            "alignment": self.alignment.value if self.alignment else None,
            "char_type": self.char_type.value if self.char_type else None,
            "alive": self.alive,
            "death_cause": self.death_cause.value if self.death_cause else None,
            "has_dead_vote": self.has_dead_vote,
            "drunk": self.drunk,
            "poisoned": self.poisoned,
            "once_per_game_used": self.once_per_game_used,
            "protected_from_demon": self.protected_from_demon,
            "has_nominated_today": self.has_nominated_today,
            "has_been_nominated_today": self.has_been_nominated_today,
            "mad_about": list(self.mad_about),
            "notes": list(self.notes),
            # Computed convenience flags so the UI doesn't have to
            # duplicate the rule (dead -> can_vote iff has_dead_vote).
            "can_nominate": self.can_nominate,
            "can_be_nominated": self.can_be_nominated,
            "can_vote": self.can_vote,
            # Day-action affordances for the side panel.
            "has_daytime_ability": has_day_ability,
            "once_per_game": once_per_game,
        }
