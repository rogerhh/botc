"""Game state machine.

This is the central object in the engine. The :class:`Game` class models
the full lifecycle of a Blood on the Clocktower session and exposes a
Storyteller-facing API.

Typical workflow
----------------

::

    g = Game()
    g.add_player("Alice")
    g.add_player("Bob")
    # ...
    g.assign_character(alice_id, "Fortune Teller")
    # ...
    g.start_game()                    # -> FIRST_NIGHT

    g.wake_player(alice_id)           # storyteller walks the night sheet
    g.put_to_sleep(alice_id)
    g.advance_to_day()                # -> DAY

    g.open_nominations()
    g.nominate(alice_id, bob_id)
    g.record_vote(alice_id, True)
    g.record_vote(bob_id, False)
    result = g.tally_nomination()
    g.execute_player()                # executes the about-to-die player
    g.advance_to_night()              # -> NIGHT
    ...

The engine enforces:

* Phase ordering (cannot ``nominate`` at night, cannot ``wake_player`` in
  day, etc.).
* Nomination rules (one per player per day; alive players only nominate;
  majority + plurality to become "about to die").
* Death bookkeeping (dead players keep a vote token for one vote, cannot
  nominate, lose their ability).
* Win condition checks (Demon dies -> good; only two alive players -> evil;
  ties go to good).

It does *not* adjudicate character abilities — that's the storyteller's
job. The engine provides helpers (``poison``, ``make_drunk``,
``protect_from_demon``, etc.) that the storyteller invokes.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from clocktower.characters import (
    CHARACTERS,
    Character,
    characters_by_type,
    recommended_counts,
)
from clocktower.enums import (
    Alignment,
    CharType,
    DayStage,
    DeathCause,
    Phase,
)
from clocktower.exceptions import (
    InvalidActionError,
    InvalidPhaseError,
    PlayerNotFoundError,
    RuleViolationError,
)
from clocktower.player import Player


# ---------------------------------------------------------------------------
# Nomination record.
# ---------------------------------------------------------------------------


@dataclass
class Nomination:
    """An in-progress or completed nomination.

    ``votes`` maps player_id -> True (yes) / False (no). A missing key
    means the player has not yet been polled (treated as a no vote once
    the nomination is tallied).
    """

    nominator_id: int
    nominee_id: int
    votes: Dict[int, bool] = field(default_factory=dict)
    tallied: bool = False
    yes_count: int = 0
    succeeded: bool = False  # Got majority AND more than previous top.

    def record(self, voter_id: int, choice: bool) -> None:
        self.votes[voter_id] = choice


# ---------------------------------------------------------------------------
# Game.
# ---------------------------------------------------------------------------


class Game:
    """Authoritative state for a single Clocktower game."""

    # ------------------------------------------------------------------
    # Construction.
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._phase: Phase = Phase.SETUP
        self._day_stage: DayStage = DayStage.DISCUSSION
        self._day_number: int = 0  # Incremented each time we enter DAY.
        self._night_number: int = 0  # 1 on first night, 2, 3, ...

        self._players: List[Player] = []
        self._next_player_id = itertools.count(1)

        # Active nomination and today's history.
        self._active_nomination: Optional[Nomination] = None
        self._day_nominations: List[Nomination] = []
        # The player currently leading in votes today, with their vote count.
        self._about_to_die: Optional[int] = None  # player id
        self._about_to_die_votes: int = 0
        self._execution_happened_today: bool = False

        # Players killed overnight, pending announcement at dawn.
        self._pending_night_deaths: List[int] = []

        # Winner once game ends.
        self._winner: Optional[Alignment] = None
        self._win_reason: Optional[str] = None

        # Human-readable event log for Storyteller debugging.
        self._log: List[str] = []

    # ------------------------------------------------------------------
    # Read-only state accessors.
    # ------------------------------------------------------------------

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def day_stage(self) -> DayStage:
        return self._day_stage

    @property
    def day_number(self) -> int:
        return self._day_number

    @property
    def night_number(self) -> int:
        return self._night_number

    @property
    def players(self) -> List[Player]:
        """All seated players, ordered by seat."""
        return sorted(self._players, key=lambda p: p.seat)

    @property
    def alive_players(self) -> List[Player]:
        return [p for p in self.players if p.alive]

    @property
    def dead_players(self) -> List[Player]:
        return [p for p in self.players if p.dead]

    @property
    def winner(self) -> Optional[Alignment]:
        return self._winner

    @property
    def win_reason(self) -> Optional[str]:
        return self._win_reason

    @property
    def event_log(self) -> List[str]:
        return list(self._log)

    @property
    def active_nomination(self) -> Optional[Nomination]:
        return self._active_nomination

    @property
    def about_to_die(self) -> Optional[Player]:
        if self._about_to_die is None:
            return None
        return self.get_player(self._about_to_die)

    @property
    def pending_night_deaths(self) -> List[Player]:
        return [self.get_player(pid) for pid in self._pending_night_deaths]

    # ------------------------------------------------------------------
    # Player lookup helpers.
    # ------------------------------------------------------------------

    def get_player(self, player_id: int) -> Player:
        for p in self._players:
            if p.id == player_id:
                return p
        raise PlayerNotFoundError(f"No player with id {player_id}")

    def _require_phase(self, *allowed: Phase) -> None:
        if self._phase not in allowed:
            raise InvalidPhaseError(
                f"Action not allowed in phase {self._phase.value}; "
                f"expected one of {[p.value for p in allowed]}"
            )

    def _require_day_stage(self, *allowed: DayStage) -> None:
        if self._day_stage not in allowed:
            raise InvalidActionError(
                f"Action not allowed in day stage {self._day_stage.value}; "
                f"expected one of {[s.value for s in allowed]}"
            )

    # ==================================================================
    #                           SETUP PHASE
    # ==================================================================

    def add_player(self, name: str, seat: Optional[int] = None) -> int:
        """Seat a new player. Returns their player id.

        Players must be added during ``SETUP``. New seats are appended
        clockwise; pass ``seat`` to insert at a specific position.
        """
        self._require_phase(Phase.SETUP)
        pid = next(self._next_player_id)
        if seat is None:
            seat = len(self._players)
        if seat < 0 or seat > len(self._players):
            raise InvalidActionError(f"Invalid seat {seat}.")
        # Shift existing seats at or after the insertion point.
        for p in self._players:
            if p.seat >= seat:
                p.seat += 1
        self._players.append(Player(id=pid, name=name, seat=seat))
        self._log.append(f"Added player {name!r} (id={pid}) at seat {seat}.")
        return pid

    def remove_player(self, player_id: int) -> None:
        """Un-seat a player. Only allowed during ``SETUP``."""
        self._require_phase(Phase.SETUP)
        player = self.get_player(player_id)
        self._players.remove(player)
        # Collapse seats clockwise.
        for p in self._players:
            if p.seat > player.seat:
                p.seat -= 1
        self._log.append(f"Removed player id={player_id} ({player.name!r}).")

    def assign_character(self, player_id: int, character_name: str) -> None:
        """Give a player their starting character (alignment follows type).

        Pass the storyteller's *true* assignment here. For a Drunk, use
        this method to set the character to ``"Drunk"`` and then use
        :meth:`set_perceived_character` to set what they believe.
        """
        self._require_phase(Phase.SETUP)
        char = self._lookup_character(character_name)
        player = self.get_player(player_id)
        player.character = char
        # Default alignment from type; storyteller can override afterwards.
        if player.alignment is None:
            player.alignment = char.default_alignment
        if char.name == "Drunk":
            player.drunk = True
        self._log.append(
            f"Assigned {char.name} to {player.name!r} (id={player_id})."
        )

    def set_perceived_character(self, player_id: int, character_name: str) -> None:
        """Tell a player they are a character other than their real one.

        Used for the Drunk (who thinks they are a Townsfolk) and other
        "think you are" abilities.
        """
        self._require_phase(Phase.SETUP, Phase.FIRST_NIGHT, Phase.DAY, Phase.NIGHT)
        char = self._lookup_character(character_name)
        player = self.get_player(player_id)
        player.perceived_character = char
        self._log.append(
            f"{player.name!r} believes they are the {char.name}."
        )

    def set_alignment(self, player_id: int, alignment: Alignment) -> None:
        """Override a player's alignment (e.g., to make a Townsfolk evil)."""
        player = self.get_player(player_id)
        player.alignment = alignment
        self._log.append(
            f"{player.name!r} alignment set to {alignment.value}."
        )

    def start_game(self) -> None:
        """Leave setup and begin the first night.

        Validates: at least 5 players, every player has a character, and
        there is exactly one Demon (for Trouble Brewing).
        """
        self._require_phase(Phase.SETUP)
        if len(self._players) < 5:
            raise RuleViolationError(
                f"Blood on the Clocktower needs at least 5 players "
                f"(have {len(self._players)})."
            )
        for p in self._players:
            if p.character is None:
                raise RuleViolationError(
                    f"Player {p.name!r} has no character assigned."
                )
            if p.alignment is None:
                p.alignment = p.character.default_alignment

        demons = [p for p in self._players if p.character.type is CharType.DEMON]
        if len(demons) != 1:
            raise RuleViolationError(
                f"Exactly one Demon required at game start (have {len(demons)})."
            )

        self._phase = Phase.FIRST_NIGHT
        self._night_number = 1
        self._log.append("Game started; entering the first night.")

    def recommended_counts_for_current(self) -> tuple[int, int, int, int]:
        """(townsfolk, outsiders, minions, demons) for current player count."""
        return recommended_counts(len(self._players))

    # ==================================================================
    #                            NIGHT PHASE
    # ==================================================================

    def wake_player(self, player_id: int) -> Player:
        """Mark a player as awake. Used while walking the night sheet."""
        self._require_phase(Phase.FIRST_NIGHT, Phase.NIGHT)
        player = self.get_player(player_id)
        player.awake = True
        self._log.append(f"Woke {player.name!r} (id={player_id}).")
        return player

    def put_to_sleep(self, player_id: int) -> None:
        """Mark a player as asleep again."""
        self._require_phase(Phase.FIRST_NIGHT, Phase.NIGHT)
        player = self.get_player(player_id)
        player.awake = False
        self._log.append(f"Put {player.name!r} back to sleep.")

    def advance_to_day(self) -> List[Player]:
        """End the night phase and move into the day.

        Returns the list of players who died overnight (in seat order).
        The caller should announce these deaths to the players.
        """
        self._require_phase(Phase.FIRST_NIGHT, Phase.NIGHT)

        # Everyone's eyes open.
        for p in self._players:
            p.reset_night_flags()

        deaths = self.pending_night_deaths
        self._pending_night_deaths.clear()

        self._phase = Phase.DAY
        self._day_number += 1
        self._day_stage = DayStage.DISCUSSION
        self._execution_happened_today = False
        self._about_to_die = None
        self._about_to_die_votes = 0
        self._day_nominations.clear()
        self._active_nomination = None

        # Reset per-day nomination flags.
        for p in self._players:
            p.reset_day_flags()

        self._log.append(
            f"Dawn: day {self._day_number} begins. "
            f"Night deaths: {[p.name for p in deaths]}"
        )
        # A check happens at dawn for completeness (some kills may push
        # the game past a win condition).
        self._check_win_conditions()
        return deaths

    # ==================================================================
    #                             DAY PHASE
    # ==================================================================

    def open_nominations(self) -> None:
        """Storyteller calls for nominations."""
        self._require_phase(Phase.DAY)
        self._require_day_stage(DayStage.DISCUSSION, DayStage.EXECUTION_PENDING)
        self._day_stage = DayStage.NOMINATIONS_OPEN
        self._log.append(f"Nominations are open (day {self._day_number}).")

    def nominate(self, nominator_id: int, nominee_id: int) -> Nomination:
        """Record a nomination. Starts an active vote on that nominee.

        Rules enforced:
          * Nominator must be alive.
          * Nominator may nominate at most once per day.
          * Nominee may be nominated at most once per day.
          * Dead players cannot nominate.

        Allowed after a previous nomination has made someone "about to die":
        the rulebook requires calling again for nominations, so a future
        nominee who gets more votes can replace the current leader.
        """
        self._require_phase(Phase.DAY)
        self._require_day_stage(
            DayStage.NOMINATIONS_OPEN, DayStage.EXECUTION_PENDING
        )

        nominator = self.get_player(nominator_id)
        nominee = self.get_player(nominee_id)

        if not nominator.can_nominate:
            raise RuleViolationError(
                f"{nominator.name!r} cannot nominate "
                f"(dead or already nominated today)."
            )
        if not nominee.can_be_nominated:
            raise RuleViolationError(
                f"{nominee.name!r} has already been nominated today."
            )

        nominator.has_nominated_today = True
        nominee.has_been_nominated_today = True

        nom = Nomination(nominator_id=nominator_id, nominee_id=nominee_id)
        self._active_nomination = nom
        self._day_stage = DayStage.NOMINATION_ACTIVE
        self._log.append(
            f"{nominator.name!r} nominates {nominee.name!r}."
        )
        return nom

    def record_vote(self, voter_id: int, yes: bool) -> None:
        """Record a single player's vote on the active nomination.

        Tallying is deferred until :meth:`tally_nomination` to let the
        storyteller drive the clockwise spin manually.
        """
        self._require_phase(Phase.DAY)
        self._require_day_stage(DayStage.NOMINATION_ACTIVE)
        voter = self.get_player(voter_id)
        if not voter.can_vote:
            raise RuleViolationError(
                f"{voter.name!r} cannot vote (dead with no vote token)."
            )
        assert self._active_nomination is not None
        self._active_nomination.record(voter_id, yes)

    def tally_nomination(self) -> Nomination:
        """Finalize the active nomination and apply its outcome.

        If the nomination succeeds, the nominee becomes "about to die"
        and the day stage moves to ``EXECUTION_PENDING``. Otherwise the
        day returns to ``NOMINATIONS_OPEN``.
        """
        self._require_phase(Phase.DAY)
        self._require_day_stage(DayStage.NOMINATION_ACTIVE)
        assert self._active_nomination is not None
        nom = self._active_nomination

        yes_count = 0
        for voter_id, choice in nom.votes.items():
            if not choice:
                continue
            voter = self.get_player(voter_id)
            if not voter.can_vote:
                # Should have been rejected on record, but be defensive.
                continue
            yes_count += 1
            # A dead player spends their vote token on a YES vote.
            if voter.dead and voter.has_vote_token:
                voter.cast_vote_token()

        nom.yes_count = yes_count
        nom.tallied = True

        alive = len(self.alive_players)
        threshold = math.ceil(alive / 2)
        has_majority = yes_count >= threshold
        beats_previous = yes_count > self._about_to_die_votes
        ties_previous = yes_count == self._about_to_die_votes and yes_count > 0

        if has_majority and beats_previous:
            nom.succeeded = True
            self._about_to_die = nom.nominee_id
            self._about_to_die_votes = yes_count
            self._day_stage = DayStage.EXECUTION_PENDING
            self._log.append(
                f"Nomination on {self.get_player(nom.nominee_id).name!r} "
                f"succeeded ({yes_count} yes of {alive} alive). "
                f"They are about to die."
            )
        else:
            if has_majority and ties_previous:
                # Tie: neither is about to die.
                self._log.append(
                    f"Tie at {yes_count} votes — "
                    f"{self.get_player(nom.nominee_id).name!r} is not about to die; "
                    f"neither is the previous leader."
                )
                self._about_to_die = None
                self._about_to_die_votes = yes_count
                self._day_stage = DayStage.NOMINATIONS_OPEN
            else:
                self._log.append(
                    f"Nomination on {self.get_player(nom.nominee_id).name!r} "
                    f"failed ({yes_count} yes, needed {threshold})."
                )
                self._day_stage = DayStage.NOMINATIONS_OPEN

        self._day_nominations.append(nom)
        self._active_nomination = None
        return nom

    def execute_player(self, player_id: Optional[int] = None) -> Player:
        """Execute the about-to-die player (or a specific player).

        Raises if no one is about to die and no ``player_id`` is given.
        The day does not automatically end; the storyteller calls
        :meth:`advance_to_night` when ready.
        """
        self._require_phase(Phase.DAY)
        if self._execution_happened_today:
            raise RuleViolationError("Only one execution is allowed per day.")

        if player_id is None:
            if self._about_to_die is None:
                raise InvalidActionError(
                    "No player is about to die; pass player_id to force."
                )
            player_id = self._about_to_die

        player = self.get_player(player_id)

        # Capture character/ability state BEFORE killing — some effects
        # (Saint, Scarlet Woman) depend on the dying player's pre-death state.
        was_saint_with_ability = (
            player.character is not None
            and player.character.name == "Saint"
            and player.has_ability
        )
        was_demon = (
            player.character is not None
            and player.character.type is CharType.DEMON
        )

        player.kill(DeathCause.EXECUTION)
        self._execution_happened_today = True
        self._day_stage = DayStage.CLOSED
        self._log.append(f"{player.name!r} is executed.")

        # Saint: executed -> their team loses (good).
        if was_saint_with_ability:
            self._end_game(
                Alignment.EVIL,
                f"Saint ({player.name!r}) was executed.",
            )
            return player

        # Executing the Demon may trigger Scarlet Woman takeover.
        if was_demon and len(self.alive_players) >= 5:
            self._try_scarlet_woman_takeover(player)

        self._check_win_conditions()
        return player

    def skip_execution(self) -> None:
        """End the day with no execution."""
        self._require_phase(Phase.DAY)
        self._day_stage = DayStage.CLOSED
        self._log.append(f"Day {self._day_number} ends with no execution.")

    def close_day_nominations(self) -> None:
        """Close nominations without (yet) executing."""
        self._require_phase(Phase.DAY)
        self._require_day_stage(
            DayStage.NOMINATIONS_OPEN, DayStage.EXECUTION_PENDING
        )
        # If someone is about to die, leave them there until execute_player
        # is called; otherwise mark the day closed.
        if self._about_to_die is None:
            self._day_stage = DayStage.CLOSED

    def advance_to_night(self) -> None:
        """End the day and begin the next night."""
        self._require_phase(Phase.DAY)
        # Allow ending from any day stage — storyteller's call.
        self._phase = Phase.NIGHT
        self._night_number += 1
        self._day_stage = DayStage.DISCUSSION  # irrelevant but tidy
        self._active_nomination = None
        self._about_to_die = None
        self._about_to_die_votes = 0

        for p in self._players:
            p.reset_night_flags()

        self._log.append(f"Night {self._night_number} begins.")

    # ==================================================================
    #                     CHARACTER / STATUS EFFECTS
    # ==================================================================

    def kill(self, player_id: int, cause: DeathCause = DeathCause.STORYTELLER) -> Player:
        """Kill a player outright.

        At night, use this for Demon kills and ability deaths; the death
        is queued for announcement at dawn (the rulebook does not reveal
        night deaths to players until then).
        """
        player = self.get_player(player_id)
        if player.dead:
            return player

        # Demon-kill protection (set by Monk, Soldier, etc).
        if (
            cause is DeathCause.DEMON_KILL
            and (player.protected_from_demon or self._is_soldier(player))
        ):
            self._log.append(
                f"{player.name!r} is safe from the Demon tonight; "
                f"no death occurs."
            )
            return player

        player.kill(cause)
        self._log.append(
            f"{player.name!r} dies ({cause.value})."
        )

        # Scarlet Woman: if the Demon dies and 5+ alive, SW becomes the Demon.
        if (
            player.character
            and player.character.type is CharType.DEMON
            and len(self.alive_players) >= 5
        ):
            self._try_scarlet_woman_takeover(player)

        if self._phase.is_night and cause is not DeathCause.EXECUTION:
            self._pending_night_deaths.append(player_id)

        self._check_win_conditions()
        return player

    def revive(self, player_id: int) -> Player:
        """Bring a player back (rare — storyteller override)."""
        player = self.get_player(player_id)
        player.alive = True
        player.death_cause = None
        player.has_vote_token = True
        self._log.append(f"{player.name!r} is revived.")
        return player

    def poison(self, player_id: int) -> None:
        self.get_player(player_id).poisoned = True
        self._log.append(f"{self.get_player(player_id).name!r} is poisoned.")

    def cure_poison(self, player_id: int) -> None:
        self.get_player(player_id).poisoned = False
        self._log.append(f"{self.get_player(player_id).name!r} is no longer poisoned.")

    def make_drunk(self, player_id: int) -> None:
        self.get_player(player_id).drunk = True
        self._log.append(f"{self.get_player(player_id).name!r} is drunk.")

    def sober_up(self, player_id: int) -> None:
        self.get_player(player_id).drunk = False
        self._log.append(f"{self.get_player(player_id).name!r} is sober.")

    def protect_from_demon(self, player_id: int) -> None:
        """Mark a player as safe from the Demon tonight (Monk effect)."""
        self._require_phase(Phase.FIRST_NIGHT, Phase.NIGHT)
        self.get_player(player_id).protected_from_demon = True
        self._log.append(
            f"{self.get_player(player_id).name!r} is protected from the Demon."
        )

    def change_character(self, player_id: int, character_name: str) -> None:
        """Change a player's character mid-game (e.g., Scarlet Woman -> Imp)."""
        char = self._lookup_character(character_name)
        player = self.get_player(player_id)
        player.character = char
        self._log.append(
            f"{player.name!r} is now the {char.name}."
        )

    def mark_once_per_game_used(self, player_id: int) -> None:
        """Record that this player has spent their once-per-game ability."""
        self.get_player(player_id).used_once_per_game = True

    # ==================================================================
    #                           WIN CONDITIONS
    # ==================================================================

    def declare_winner(self, alignment: Alignment, reason: str = "") -> None:
        """Forcibly end the game with the given alignment winning."""
        self._end_game(alignment, reason or "Storyteller declared a winner.")

    def _check_win_conditions(self) -> None:
        """Check for automatic win conditions; end the game if any apply."""
        if self._phase is Phase.FINISHED:
            return

        alive = self.alive_players
        # No non-traveler demon alive => good wins.
        alive_demons = [
            p for p in alive
            if p.character and p.character.type is CharType.DEMON
        ]
        if not alive_demons:
            self._end_game(Alignment.GOOD, "The Demon is dead.")
            return

        # Evil wins when only two players (excluding travelers/fabled) remain.
        counted = [
            p for p in alive
            if p.character and p.character.type not in
               (CharType.TRAVELER, CharType.FABLED)
        ]
        if len(counted) <= 2:
            # Good and evil would both 'win' -> good wins.
            if not alive_demons:
                self._end_game(Alignment.GOOD, "Only two players remain.")
            else:
                self._end_game(Alignment.EVIL, "Only two players remain.")
            return

    def _end_game(self, winner: Alignment, reason: str) -> None:
        self._phase = Phase.FINISHED
        self._winner = winner
        self._win_reason = reason
        self._log.append(f"Game over: {winner.value} wins — {reason}")

    # ==================================================================
    #                         INTERNAL HELPERS
    # ==================================================================

    def _lookup_character(self, name: str) -> Character:
        if name not in CHARACTERS:
            raise InvalidActionError(
                f"Unknown character {name!r}. "
                f"Known characters: {sorted(CHARACTERS)}"
            )
        return CHARACTERS[name]

    def _is_soldier(self, player: Player) -> bool:
        return (
            player.character is not None
            and player.character.name == "Soldier"
            and player.has_ability
        )

    def _try_scarlet_woman_takeover(self, dead_demon: Player) -> None:
        """If a Scarlet Woman is alive and healthy, promote her to Imp."""
        for candidate in self.alive_players:
            if (
                candidate.character is not None
                and candidate.character.name == "Scarlet Woman"
                and candidate.has_ability
            ):
                candidate.character = CHARACTERS["Imp"]
                self._log.append(
                    f"Scarlet Woman {candidate.name!r} becomes the Imp."
                )
                return

    # ==================================================================
    #                           SNAPSHOTS
    # ==================================================================

    def snapshot(self) -> dict:
        """A JSON-serializable view of the whole game (storyteller view)."""
        return {
            "phase": self._phase.value,
            "day_stage": self._day_stage.value,
            "day_number": self._day_number,
            "night_number": self._night_number,
            "players": [p.snapshot() for p in self.players],
            "about_to_die": self._about_to_die,
            "active_nomination": (
                {
                    "nominator_id": self._active_nomination.nominator_id,
                    "nominee_id": self._active_nomination.nominee_id,
                    "votes": dict(self._active_nomination.votes),
                    "tallied": self._active_nomination.tallied,
                    "yes_count": self._active_nomination.yes_count,
                    "succeeded": self._active_nomination.succeeded,
                }
                if self._active_nomination
                else None
            ),
            "pending_night_deaths": list(self._pending_night_deaths),
            "winner": self._winner.value if self._winner else None,
            "win_reason": self._win_reason,
        }

    def player_view(self, player_id: int) -> dict:
        """What one player should see (for a phone display).

        Each player sees only their own name, character (as *they
        perceive it*), alignment, and the public info of everyone else.
        """
        me = self.get_player(player_id)
        perceived = me.perceived_character or me.character
        return {
            "me": {
                "id": me.id,
                "name": me.name,
                "seat": me.seat,
                "character": perceived.name if perceived else None,
                "ability": perceived.ability if perceived else None,
                "alignment": me.alignment.value if me.alignment else None,
                "alive": me.alive,
            },
            "others": [
                {
                    "id": p.id,
                    "name": p.name,
                    "seat": p.seat,
                    "alive": p.alive,
                    "has_vote_token": p.has_vote_token,
                }
                for p in self.players
                if p.id != player_id
            ],
            "phase": self._phase.value,
            "day_number": self._day_number,
            "night_number": self._night_number,
        }
