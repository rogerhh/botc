"""The game engine.

The Engine is a turn-based action resolver. It owns the list of seated
players, drives the night/day phase loop, and brokers the conversation
between character abilities and the storyteller's UI.

Threading model
---------------
Character abilities are written as straight-line code that calls
``engine.send_prompt(prompt)`` and gets a response back. To keep that
interface simple, the night-phase loop runs in its own worker thread.
The main (HTTP) thread reads ``engine.pending_prompt()`` and posts
``engine.respond(prompt_id, value)`` when the storyteller answers.

Public API
----------
* setup-related: ``add_seat``, ``rename_seat``, ``assign_character``,
  ``remove_seat``, ``start_game``.
* Phase: ``start_night``, ``advance_to_day``, ``advance_to_night``,
  ``end_game``.
* Storyteller mutators (used during day too): ``kill``, ``revive``,
  ``poison``, ``cure_poison``, ``make_drunk``, ``sober_up``,
  ``change_character``, ``set_alignment``.
* UI broker: ``pending_prompt``, ``respond``.
* Snapshot: ``snapshot``, ``player_view``.

The engine does not adjudicate character-specific rules itself — that's
delegated to the :class:`Character` subclasses, which call into the
engine for prompts and state changes.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine import preset as preset_module
from engine import script as script_data
from engine.chairs import ChairStore
from engine.character import Character
from engine.pool import CharacterPool
from engine.enums import Alignment, CharType, DeathCause, Phase, SetupMode
from engine.event import Event, EventType
from engine.player import Player
from engine.prompt import (
    InformationPrompt,
    Prompt,
    SelectCharacterPrompt,
    SelectPlayerPrompt,
)


# Sentinel used by ``Engine._auto_resolve`` to distinguish "no auto-resolve
# possible" from a legitimately ``None`` storyteller answer.
_NO_AUTO_RESOLVE = object()


class Engine:
    """Authoritative state for a single Clocktower game."""

    # ------------------------------------------------------------------
    # Construction.
    # ------------------------------------------------------------------

    def __init__(
        self,
        preset: Optional["preset_module.Preset"] = None,
        *,
        default_seats: int = 8,
    ) -> None:
        self._phase: Phase = Phase.SETUP
        self._night_number: int = 0
        self._day_number: int = 0

        self._players: List[Player] = []
        self._next_player_id = itertools.count(1)

        # Default number of chair slots seeded into ``self.chairs`` on
        # construction; also used by the entry-point CLI so the operator
        # can run e.g. ``python3 botc.py --players 12``.
        self._default_seats = default_seats

        # The town-square layout (chair positions, names, typed-in
        # character roles, the chair -> Player binding once the game
        # starts). Owned by the engine so any copy of the engine
        # snapshots-and-renders identically — see ``Engine.snapshot``.
        self.chairs = ChairStore(default_seats=default_seats)

        # The character pool ("the bag") plus the four setup-time
        # picks (Drunk fake, FT red herring, WW seen-Townsfolk, WW
        # wrong). Auto-fills dependent slots when the relevant owner
        # role enters the pool.
        self.pool = CharacterPool()

        # Selected preset name (e.g. "trouble_brewing"). Stored on the
        # engine so resets / reloads don't lose the operator's choice.
        # Drives the night order via ``set_preset`` once the game starts.
        self.selected_preset_name: Optional[str] = None

        # Players killed overnight, pending announcement at dawn.
        self._pending_night_deaths: List[Player] = []

        # Latched once the pre-first-night setup_abilities have all run,
        # so a re-entrant start_night doesn't ask the Drunk to pick a
        # fake Townsfolk twice.
        self._setup_actions_done: bool = False

        # Win state.
        self._winner: Optional[Alignment] = None
        self._win_reason: Optional[str] = None

        # Set when ANY execution has happened today. Cleared at dawn.
        # Read by ``_check_win_conditions`` for the Mayor's
        # "3-alive-no-execution" win at dusk.
        self._executed_today: bool = False

        # Storyteller-readable event log.
        self._log: List[str] = []

        # The preset (script) drives the night order and storyteller-
        # facing descriptions. ``None`` means fall back to the legacy
        # Character.first_night_order / other_night_order ordering.
        self._preset: Optional["preset_module.Preset"] = preset

        # ---- UI / prompt brokerage ----
        self._lock = threading.Lock()
        self._pending_prompt: Optional[Prompt] = None
        self._prompt_response: Any = None
        self._response_ready = threading.Event()
        # Set when the worker thread (night loop) is alive.
        self._night_thread: Optional[threading.Thread] = None
        # When set, ``_run_night`` automatically calls ``advance_to_day``
        # at end of night so the storyteller doesn't have to click
        # twice. Toggleable so tests / replays can stop at NIGHT_END.
        self._auto_advance_to_day: bool = False
        # Step context stashed by ``_announce_step`` for character
        # abilities. Each prompt that flows through ``send_prompt``
        # picks up the step's name / description / character /
        # target_player_name from this dict (consumed once, then
        # cleared) so the UI can render the panel header without the
        # engine having to emit a redundant announce-prompt up front.
        self._current_step_meta: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Read-only state.
    # ------------------------------------------------------------------

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def night_number(self) -> int:
        return self._night_number

    @property
    def day_number(self) -> int:
        return self._day_number

    @property
    def players(self) -> List[Player]:
        return sorted(self._players, key=lambda p: p.seat)

    @property
    def alive_players(self) -> List[Player]:
        return [p for p in self.players if p.alive]

    @property
    def pending_night_deaths(self) -> List[Player]:
        return list(self._pending_night_deaths)

    @property
    def event_log(self) -> List[str]:
        return list(self._log)

    @property
    def winner(self) -> Optional[Alignment]:
        return self._winner

    @property
    def win_reason(self) -> Optional[str]:
        return self._win_reason

    @property
    def preset(self) -> Optional["preset_module.Preset"]:
        return self._preset

    def set_preset(self, preset: Optional["preset_module.Preset"]) -> None:
        """Install (or clear) the preset that drives night ordering."""
        self._preset = preset
        if preset is not None:
            self.log(f"Using preset script: {preset.name}.")

    def set_auto_advance_to_day(self, enabled: bool) -> None:
        """When True, the night loop ends with an automatic dawn.

        The UI flips this on so the storyteller doesn't have to click
        "Advance to Day" — once Dawn is announced the engine flips the
        phase itself.
        """
        self._auto_advance_to_day = bool(enabled)

    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")
        # Mirror to stderr so the operator can see live engine activity
        # in the terminal where the UI server is running. Prefixed so
        # it's clearly distinct from HTTP request logs.
        try:
            import sys
            sys.stderr.write(f"[engine] {msg}\n")
            sys.stderr.flush()
        except Exception:  # pragma: no cover (defensive)
            pass

    # ------------------------------------------------------------------
    # Player lookup helpers.
    # ------------------------------------------------------------------

    def get_player(self, player_id: int) -> Player:
        for p in self._players:
            if p.id == player_id:
                return p
        raise KeyError(f"No player with id {player_id}")

    def get_player_by_seat(self, seat: int) -> Optional[Player]:
        for p in self._players:
            if p.seat == seat:
                return p
        return None

    # ------------------------------------------------------------------
    # Character / script helpers.
    # ------------------------------------------------------------------

    def all_character_names(self) -> List[str]:
        return script_data.all_names()

    def all_character_names_by_type(self, char_type: CharType) -> List[str]:
        return script_data.names_by_type(char_type)

    def build_character(self, name: str) -> Character:
        """Construct a fresh, unseated :class:`Character` by name.

        Thin wrapper over :func:`engine.script.build_character` so
        :class:`Character` subclasses (e.g. the Drunk picking its
        impersonated Townsfolk) can instantiate other roles via the
        engine without importing the script module — that import would
        be circular for any character-side code that lives below
        :mod:`engine.character`.

        The returned instance has no ``player`` set; it is meant to be
        held on another character (e.g. as a "member") rather than
        added to the seating.
        """
        return script_data.build_character(name)

    def in_play_characters(self) -> List[Character]:
        return [p.character for p in self._players if p.character is not None]

    def in_play_character_names(self) -> List[str]:
        return [c.name for c in self.in_play_characters()]

    def in_play_character_names_by_type(self, char_type: CharType) -> List[str]:
        return [
            c.name
            for c in self.in_play_characters()
            if c.char_type is char_type
        ]

    # ==================================================================
    #                            SETUP
    # ==================================================================

    def add_seat(self, name: str = "") -> Player:
        """Add a new seat (clockwise from the previous last seat)."""
        if self._phase is not Phase.SETUP:
            raise RuntimeError("Players can only be added during setup.")
        pid = next(self._next_player_id)
        seat = len(self._players)
        player = Player(id=pid, name=name, seat=seat)
        self._players.append(player)
        self.log(f"Added seat #{seat} for {name!r} (id={pid}).")
        return player

    def remove_seat(self, player_id: int) -> None:
        if self._phase is not Phase.SETUP:
            raise RuntimeError("Seats can only be removed during setup.")
        player = self.get_player(player_id)
        self._players.remove(player)
        # Compact seat numbers.
        for p in self._players:
            if p.seat > player.seat:
                p.seat -= 1
        self.log(f"Removed seat for {player.name!r} (id={player_id}).")

    def rename_seat(self, player_id: int, name: str) -> None:
        player = self.get_player(player_id)
        player.name = name

    def assign_character(self, player_id: int, character_name: str) -> None:
        """Assign a character to a player (by name).

        After wiring up the new Character, the engine immediately runs
        its on-setup ability in ``SETUP_PHASE`` mode (when the engine
        is in ``Phase.SETUP``) or ``IN_GAME`` mode (otherwise). The
        SETUP_PHASE pass silently absorbs whatever pool / token state
        is currently set; the IN_GAME pass prompts the storyteller.
        """
        player = self.get_player(player_id)
        char = script_data.build_character(character_name)
        player.assign_character(char)
        # Drunk: by default, perceived character is "Townsfolk" placeholder
        # until the storyteller sets a specific one.
        if character_name == "Drunk":
            player.set_drunk(True)
            if player.perceived_character_name is None:
                player.perceived_character_name = "Townsfolk"
        self.log(f"Assigned {character_name} to {player.name!r} (id={player_id}).")
        # Trigger the on-setup ability in the appropriate mode. During
        # phase=SETUP the UI is in control, so SETUP_PHASE is silent
        # absorption; mid-game (e.g. Scarlet Woman -> Imp via assign)
        # uses IN_GAME so the ST is prompted for any picks.
        mode = SetupMode.SETUP_PHASE if self._phase is Phase.SETUP else SetupMode.IN_GAME
        try:
            char.on_setup_ability(self, mode)
        except Exception as exc:  # pragma: no cover (defensive)
            self.log(f"Error in {char.name} on_setup_ability ({mode}): {exc!r}")

    def apply_setup_data(self, data: dict) -> None:
        """Pre-populate setup-time picks chosen in the UI.

        The local UI tracks three setup picks before the game starts:

          * ``drunk_fake`` — the Townsfolk role the Drunk *thinks* they
            are. We instantiate that role as a real Character on the
            Drunk's ``members[0]`` and write the role name onto
            ``player.perceived_character_name`` so the player's phone
            shows it. The Drunk's :meth:`setup_ability` will see the
            pre-populated member and skip its prompt.
          * ``ft_red_herring`` — a *role* (Townsfolk or Outsider) in
            play that the Fortune Teller will see as a Demon. We
            instantiate that role on the Fortune Teller's
            ``members[0]`` and resolve the seated red-herring player on
            ``self._red_herring``. The setup_ability skips its prompt.
          * ``washerwoman_townsfolk`` — the Townsfolk role the
            Washerwoman is shown. We pre-set
            ``self._chosen_townsfolk`` on the Washerwoman so its
            nightly ability skips the "pick a Townsfolk" prompt.
          * ``washerwoman_wrong`` — the role name of the WRONG player
            the Washerwoman will be pointed at. We pre-set
            ``self._chosen_wrong`` on the Washerwoman so its nightly
            ability skips the "pick the wrong player" prompt too.
            When both ``washerwoman_townsfolk`` and ``washerwoman_wrong``
            are present, the WW's first-night ability runs entirely
            without storyteller prompts (information only).

        Idempotent: passing the same ``data`` twice is fine. Missing
        keys leave the existing pick (if any) untouched.
        """
        drunk_fake = data.get("drunk_fake")
        ft_red_herring = data.get("ft_red_herring")
        ww_townsfolk = data.get("washerwoman_townsfolk")
        ww_wrong = data.get("washerwoman_wrong")

        for player in self._players:
            char = player.character
            if char is None:
                continue
            if char.name == "Drunk" and drunk_fake:
                tf_char = script_data.build_character(drunk_fake)
                char.members.clear()
                char.members.append(tf_char)
                player.perceived_character_name = drunk_fake
                self.log(
                    f"{player.name} (Drunk) believes they are the "
                    f"{drunk_fake} (pre-set from setup)."
                )
            elif char.name == "Fortune Teller" and ft_red_herring:
                rh_char = script_data.build_character(ft_red_herring)
                char.members.clear()
                char.members.append(rh_char)
                # Resolve the seated red-herring player.
                for p in self._players:
                    if p.character is not None and p.character.name == ft_red_herring:
                        char._red_herring = p
                        self.log(
                            f"{p.name} ({ft_red_herring}) is the red "
                            f"herring for {player.name} (pre-set)."
                        )
                        break
            elif char.name == "Washerwoman":
                if ww_townsfolk:
                    char._chosen_townsfolk = ww_townsfolk
                    self.log(
                        f"{player.name} (Washerwoman) will be shown the "
                        f"{ww_townsfolk} (pre-set)."
                    )
                if ww_wrong:
                    char._chosen_wrong = ww_wrong
                    self.log(
                        f"{player.name} (Washerwoman) WRONG token "
                        f"placed on the {ww_wrong} (pre-set)."
                    )

    def set_perceived_character(self, player_id: int, character_name: str) -> None:
        player = self.get_player(player_id)
        player.perceived_character_name = character_name
        self.log(f"{player.name!r} believes they are the {character_name}.")

    def set_alignment(self, player_id: int, alignment: Alignment) -> None:
        player = self.get_player(player_id)
        player.alignment = alignment
        self.log(f"{player.name!r} alignment set to {alignment.value}.")

    def start_game(self) -> None:
        """Leave SETUP and begin the first night.

        Validates: at least 5 players, all have a character, exactly one Demon.
        """
        if self._phase is not Phase.SETUP:
            raise RuntimeError("start_game called outside SETUP.")
        if len(self._players) < 5:
            raise RuntimeError(
                f"Blood on the Clocktower needs at least 5 players "
                f"(have {len(self._players)})."
            )
        for p in self._players:
            if p.character is None:
                raise RuntimeError(f"Player {p.name!r} has no character assigned.")
            if p.alignment is None:
                p.alignment = p.char_type.default_alignment

        demons = [p for p in self._players if p.char_type is CharType.DEMON]
        if len(demons) != 1:
            raise RuntimeError(
                f"Exactly one Demon is required (have {len(demons)})."
            )

        self._phase = Phase.FIRST_NIGHT
        self._night_number = 1
        self.log("Game started; entering the first night.")

    def recommended_counts(self) -> Tuple[int, int, int, int]:
        return script_data.recommended_counts(len(self._players))

    # ------------------------------------------------------------------
    # Setup-token drag/drop operations.
    #
    # These mutate ``self.chairs`` and ``self.pool`` together. They
    # exist on the engine because the rules they enforce span both
    # stores and need to stay consistent. Each returns ``None`` on
    # success or a human-readable error string on rejection.
    # ------------------------------------------------------------------

    def _townsfolk_in_play(self, name: str) -> bool:
        spec = script_data.SCRIPT_BY_NAME.get(name)
        return spec is not None and spec.char_type is CharType.TOWNSFOLK

    def _good_in_play(self, name: str) -> bool:
        spec = script_data.SCRIPT_BY_NAME.get(name)
        return spec is not None and spec.char_type in (
            CharType.TOWNSFOLK, CharType.OUTSIDER,
        )

    def move_drunk_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the IS-THE-DRUNK reminder onto ``dest_chair_id``.

        See ``ui.README`` / the engine README for the swap semantics.
        Briefly: the destination chair becomes the Drunk; the role it
        used to hold becomes the Drunk's new pretend role; the
        previously-Drunk chair (if any) inherits the *previous*
        pretend role as its actual character.
        """
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        pool_names = self.pool.list()
        if "Drunk" not in pool_names:
            return "Drunk is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        source: Optional[Dict[str, Any]] = None
        for c in self.chairs.list():
            if (c.get("character") or "").strip() == "Drunk":
                source = c
                break
        if source is not None and source["id"] == dest_chair_id:
            return None  # no-op
        if not self._townsfolk_in_play(dest_char):
            return "destination chair must hold a Townsfolk role"
        if dest_char not in pool_names:
            return f"{dest_char!r} is not in the pool"

        new_fake = dest_char
        prev_fake = self.pool.drunk_fake()

        if source is not None:
            self.chairs.update(source["id"], character=(prev_fake or ""))
        self.chairs.update(dest_chair_id, character="Drunk")

        new_pool: List[str] = []
        inserted_prev_fake = False
        for n in pool_names:
            if n == new_fake:
                if prev_fake is not None and not inserted_prev_fake:
                    new_pool.append(prev_fake)
                    inserted_prev_fake = True
                continue
            new_pool.append(n)
        self.pool.set_many(new_pool)
        try:
            self.pool.set_drunk_fake(new_fake)
        except ValueError:
            pass
        # Re-trigger SETUP_PHASE absorption on any seated Drunk so the
        # Player's perceived_character_name and the Drunk's members
        # both reflect the new fake.
        self._retrigger_setup_for_role("Drunk")
        return None

    def move_ft_red_herring_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the FT RED HERRING reminder onto ``dest_chair_id``."""
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        if "Fortune Teller" not in self.pool.list():
            return "Fortune Teller is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        if not self._good_in_play(dest_char):
            return "destination chair must hold a Townsfolk or Outsider role"
        if dest_char not in self.pool.list():
            return f"{dest_char!r} is not in the pool"
        try:
            self.pool.set_ft_red_herring(dest_char)
        except ValueError as exc:
            return str(exc)
        # Re-absorb on the FT so its members[0] / _red_herring update.
        self._retrigger_setup_for_role("Fortune Teller", reset_first=True)
        return None

    def move_washerwoman_townsfolk_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the WW TOWNSFOLK reminder onto ``dest_chair_id``."""
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        if "Washerwoman" not in self.pool.list():
            return "Washerwoman is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        if not self._townsfolk_in_play(dest_char):
            return "destination chair must hold a Townsfolk role"
        if dest_char not in self.pool.list():
            return f"{dest_char!r} is not in the pool"
        try:
            self.pool.set_washerwoman_townsfolk(dest_char)
        except ValueError as exc:
            return str(exc)
        # Re-absorb so the WW's _chosen_townsfolk reflects the move.
        self._retrigger_setup_for_role("Washerwoman")
        return None

    def move_washerwoman_wrong_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the WW WRONG reminder onto ``dest_chair_id``."""
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        if "Washerwoman" not in self.pool.list():
            return "Washerwoman is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        if dest_char not in self.pool.list():
            return f"{dest_char!r} is not in the pool"
        try:
            self.pool.set_washerwoman_wrong(dest_char)
        except ValueError as exc:
            return str(exc)
        # Re-absorb so the WW's _chosen_wrong reflects the move.
        self._retrigger_setup_for_role("Washerwoman")
        return None

    def _retrigger_setup_for_role(
        self, role_name: str, *, reset_first: bool = False
    ) -> None:
        """Re-run on_setup_ability(SETUP_PHASE) for any seated player
        whose character matches ``role_name``.

        Used by token-drag handlers so a UI mutation (e.g. moving the
        FT RED HERRING token) immediately re-absorbs the new pool /
        chair state into the affected character's internals.

        ``reset_first=True`` clears ``character.members`` and (for the
        Fortune Teller) ``_red_herring`` before the absorption pass
        so a new pool pick replaces the previous one rather than
        sticking with the first set of members. SETUP_PHASE is a
        no-op outside ``Phase.SETUP`` so this is safe to call
        unconditionally.
        """
        if self._phase is not Phase.SETUP:
            return
        for p in self._players:
            char = p.character
            if char is None or char.name != role_name:
                continue
            if reset_first:
                char.members.clear()
                if hasattr(char, "_red_herring"):
                    char._red_herring = None
            try:
                char.on_setup_ability(self, SetupMode.SETUP_PHASE)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(
                    f"Error in {char.name} on_setup_ability "
                    f"(SETUP_PHASE re-trigger): {exc!r}"
                )

    # ==================================================================
    #                       NIGHT PHASE
    # ==================================================================

    def start_night(self) -> None:
        """Kick off the night phase in a worker thread.

        After this returns, the UI should poll :meth:`pending_prompt` for
        the storyteller's next question and post answers via
        :meth:`respond`. When the night completes, the phase advances to
        DAY automatically.
        """
        if self._phase not in (Phase.FIRST_NIGHT, Phase.NIGHT):
            raise RuntimeError(
                f"start_night called in phase {self._phase.value}; "
                f"call advance_to_night() first."
            )
        if self._night_thread and self._night_thread.is_alive():
            raise RuntimeError("Night phase is already running.")

        # Reset per-night flags on every player. (We deliberately do NOT
        # clear ``_pending_night_deaths`` here — it's cleared at dawn,
        # in ``advance_to_day``, after the storyteller has been told who
        # died. This way any kills that landed *between* phase transitions
        # aren't lost, and the Ravenkeeper can still see "I died this
        # night" when its turn comes up.)
        for p in self._players:
            p.reset_night_flags()

        self.log(f"Night {self._night_number} begins.")

        self._night_thread = threading.Thread(
            target=self._run_night, name="botc-night", daemon=True
        )
        self._night_thread.start()

    def _run_night(self) -> None:
        try:
            # Pre-first-night setup actions: each character that
            # overrides Character.setup_ability gets a chance to ask the
            # storyteller a question (Drunk's fake Townsfolk, Fortune
            # Teller's red herring, etc.). Runs once, latched by
            # ``_setup_actions_done`` so a re-entrant start_night doesn't
            # repeat the prompts.
            if self._night_number == 1 and not self._setup_actions_done:
                self._run_setup_actions()
                self._setup_actions_done = True

            self._dispatch(Event(EventType.NIGHT_START))

            if self._preset is not None:
                self._run_preset_night(self._night_number)
            else:
                # Legacy path: fall back to Character.night_order if no
                # preset is installed (used by tests that don't set one).
                order = self._build_action_order(self._night_number)
                self.log(
                    f"Action order ({self._night_number}): "
                    + ", ".join(
                        f"{c.name}({c.player.name if c.player else '—'})"
                        for c in order
                    )
                )
                for char in order:
                    if self._phase is Phase.FINISHED:
                        break
                    try:
                        char.ability(self, self._night_number)
                    except Exception as exc:  # pragma: no cover (defensive)
                        self.log(f"Error in {char.name} ability: {exc!r}")

            self._dispatch(Event(EventType.NIGHT_END))

            if self._auto_advance_to_day and self._phase.is_night:
                # Drop into day automatically. The engine.advance_to_day
                # path expects to be called from the UI thread; we're
                # already on the night thread, so bypass the
                # join-on-self by inlining the state transition.
                self._auto_dawn()
        finally:
            with self._lock:
                self._pending_prompt = None

    # ------------------------------------------------------------------
    # Preset-driven night order.
    # ------------------------------------------------------------------

    def _run_preset_night(self, night_number: int) -> None:
        """Walk the preset's night sheet, prompting the storyteller for
        each step in turn.

        For non-character steps (Dusk, Dawn, Minion Info, Demon Info),
        the engine emits a storyteller-facing :class:`InformationPrompt`
        with the rulebook description. For character steps, the engine
        finds the in-play character (if any) and runs its ability().
        Each character ability already emits its own prompts; this
        wrapper simply orchestrates the order from the preset.

        Drunk-style impersonators: the impersonated role is registered
        in ``in_play`` under its own name (e.g. a Drunk-as-Empath
        registers an Empath instance) so when the preset reaches that
        role's slot the impersonator is woken and walked through the
        impersonated role's ability — with ``player.has_ability``
        false, so the ability takes its drunk/poisoned branch.
        """
        steps = self._preset.order_for_night(night_number)
        in_play: Dict[str, Character] = {}
        for p in self._players:
            if p.character is None:
                continue
            in_play[p.character.name] = p.character
            perceived = p.character.acting_perceived_character()
            if perceived is not None:
                # Don't shadow a real seated holder of that role: only
                # register the perceived role if no one is genuinely
                # playing it. (A Drunk's perceived role is normally
                # picked from "Townsfolk not in play" so this is a
                # defensive no-op in the canonical case.)
                in_play.setdefault(perceived.name, perceived)

        for step in steps:
            if self._phase is Phase.FINISHED:
                break

            if step.name in (preset_module.DUSK, preset_module.DAWN):
                self._announce_step(step)
                continue
            if step.name == preset_module.MINION_INFO:
                self._run_minion_info(step)
                continue
            if step.name == preset_module.DEMON_INFO:
                self._run_demon_info(step)
                continue

            char = in_play.get(step.name)
            if char is None:
                # That character isn't in this game — skip silently.
                continue
            # Trigger condition gating: if the character won't actually
            # do anything tonight (Ravenkeeper still alive, Undertaker
            # on a no-execution day, …) we skip the storyteller-facing
            # announcement *and* the ability call, so the storyteller
            # doesn't see a wake-up prompt for nothing.
            if not char.would_act_tonight(self, night_number):
                self.log(
                    f"Skipping {char.name}: trigger condition not met "
                    f"tonight."
                )
                continue
            self._announce_step(step, character=char)
            try:
                char.ability(self, night_number)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(f"Error in {char.name} ability: {exc!r}")

    def _announce_step(
        self,
        step: "preset_module.NightStep",
        character: Optional[Character] = None,
    ) -> None:
        """Mark the start of a preset step.

        For *character* steps, this **doesn't** emit a storyteller
        prompt — that would force the storyteller to click "Next" once
        before any actual ability prompts arrive. Instead the engine:

        * stashes the step's name + description on
          ``self._current_step_meta`` so the next prompt the character
          sends (or, for an ability with no prompts, the next prompt
          from the following step) can be auto-decorated with the
          rulebook line for the UI's panel header;

        * dispatches a ``STEP_START`` event so other characters can
          react and any UI tooling that wants to highlight the player
          about to act can do so.

        For non-character preset steps (Dusk, Dawn) we still emit an
        :class:`InformationPrompt` because there are no follow-up
        ability prompts to absorb the description — without the
        prompt the storyteller would never see the rulebook line.
        """
        # Always clear any stale step context first. If the previous
        # character's ability returned without sending a prompt, the
        # stash from *its* announce_step would otherwise leak into the
        # next prompt — which could be Dawn/Dusk or the next ability.
        self._current_step_meta = None

        if not step.description:
            return

        meta = {
            "step_kind": "preset_step",
            "step_name": step.name,
            "description": step.description,
        }
        target_pid: Optional[int] = None
        if character is not None and character.player is not None:
            meta["character"] = character.name
            meta["target_player_name"] = character.player.name
            target_pid = character.player.id

        # Always announce the step via an internal event so other
        # parts of the engine / UI tooling can hook in.
        self._dispatch(
            Event(
                EventType.STEP_START,
                source=character,
                targets=(
                    [character.player]
                    if character is not None and character.player is not None
                    else []
                ),
                data=dict(meta),
            )
        )

        # Character steps: stash the step context so the next prompt
        # picks it up; no blocking storyteller prompt.
        if character is not None and character.player is not None:
            self._current_step_meta = dict(meta)
            return

        # Non-character steps (Dusk / Dawn): keep emitting the prompt
        # — there's no follow-up ability to fold the description into.
        text = f"{step.name}: {step.description}"
        self.send_prompt(
            InformationPrompt(
                text=text,
                meta=meta,
                target_player_id=target_pid,
                shown_to_player=False,
            )
        )

    def _run_minion_info(self, step: "preset_module.NightStep") -> None:
        """Show each evil Minion who their Demon is and who else is on
        the evil team. Only fires in games of 7+ players (per the rule).
        """
        non_traveler_count = len([
            p for p in self._players
            if p.char_type not in (CharType.TRAVELER, CharType.FABLED)
        ])
        if non_traveler_count < 7:
            return
        minions = [p for p in self._players if p.char_type is CharType.MINION]
        demons = [p for p in self._players if p.char_type is CharType.DEMON]
        if not minions or not demons:
            return
        demon_names = ", ".join(p.name for p in demons)
        minion_names = ", ".join(p.name for p in minions)
        # The TARGET of minion-info is the Demon (and any fellow Minions)
        # the receiving Minion needs to learn about — those are the chairs
        # we want bright on the board, with everyone else dampened.
        demon_char_names = sorted({
            p.character.name for p in demons if p.character is not None
        })
        for minion in minions:
            text = (
                f"Wake {minion.name} (Minion). Show: the Demon is {demon_names}. "
                f"Other Minions: {minion_names}."
            )
            highlight_ids = [p.id for p in demons] + [
                p.id for p in minions if p.id != minion.id
            ]
            self.send_prompt(InformationPrompt(
                text=text,
                target_player_id=minion.id,
                shown_to_player=True,
                highlight_player_ids=highlight_ids,
                highlight_characters=demon_char_names,
                meta={
                    "step_kind": "minion_info",
                    "step_name": step.name,
                    "description": step.description,
                    # ``character`` and ``target_player_name`` let the
                    # storyteller UI synthesize the standard
                    # "Wake up <Role> (<Player>)" line above this
                    # info — the same 6-section layout used for ordinary
                    # ability prompts.
                    "character": "Minion",
                    "target_player_name": minion.name,
                    "stage": "info",
                    "demon_player_names": [p.name for p in demons],
                    "minion_player_names": [p.name for p in minions],
                },
            ))

    def _run_demon_info(self, step: "preset_module.NightStep") -> None:
        """Show the Demon their Minions and 3 not-in-play good roles to
        bluff as. Only fires in games of 7+ players.
        """
        non_traveler_count = len([
            p for p in self._players
            if p.char_type not in (CharType.TRAVELER, CharType.FABLED)
        ])
        if non_traveler_count < 7:
            return
        minions = [p for p in self._players if p.char_type is CharType.MINION]
        demons = [p for p in self._players if p.char_type is CharType.DEMON]
        if not demons:
            return

        # Pick three good (Townsfolk/Outsider) characters that are NOT in
        # play, to give the demon as bluff candidates.
        in_play_names = {
            p.character.name for p in self._players if p.character is not None
        }
        all_good_names = (
            script_data.names_by_type(CharType.TOWNSFOLK)
            + script_data.names_by_type(CharType.OUTSIDER)
        )
        bluff_pool = [n for n in all_good_names if n not in in_play_names]
        import random as _rand
        bluffs = _rand.sample(bluff_pool, min(3, len(bluff_pool)))

        minion_names = ", ".join(p.name for p in minions) or "(none)"
        for demon in demons:
            text = (
                f"Wake {demon.name} (Demon). Your Minions: {minion_names}. "
                f"Three not-in-play good roles to bluff as: "
                f"{', '.join(bluffs) if bluffs else '(none)'}."
            )
            # The TARGET of demon-info is the Demon's Minions plus the
            # 3 bluff roles. Highlight those chairs/character tokens so
            # the Demon's eye snaps to them; dampen the rest of the board.
            self.send_prompt(InformationPrompt(
                text=text,
                target_player_id=demon.id,
                shown_to_player=True,
                highlight_player_ids=[p.id for p in minions],
                highlight_characters=list(bluffs),
                meta={
                    "step_kind": "demon_info",
                    "step_name": step.name,
                    "description": step.description,
                    # ``character`` and ``target_player_name`` let the
                    # storyteller UI synthesize the standard
                    # "Wake up <Role> (<Player>)" line above this
                    # info — the same 6-section layout used for ordinary
                    # ability prompts.
                    "character": "Demon",
                    "target_player_name": demon.name,
                    "stage": "info",
                    "minion_player_names": [p.name for p in minions],
                    "bluff_characters": list(bluffs),
                },
            ))

    def _auto_dawn(self) -> None:
        """Internal version of advance_to_day for the night-thread.

        ``advance_to_day`` joins the night thread; we can't call that
        from the night thread itself or it'd deadlock. So we replicate
        the state-transition steps without the join.
        """
        deaths = list(self._pending_night_deaths)
        self._pending_night_deaths.clear()
        self._phase = Phase.DAY
        self._day_number += 1
        self._executed_today = False
        for p in self._players:
            p.reset_day_flags()
        self.log(
            f"Dawn (auto): day {self._day_number} begins. "
            f"Night deaths: {[p.name for p in deaths]}."
        )
        self._check_win_conditions()
        if self._phase is not Phase.FINISHED:
            self._dispatch(Event(EventType.DAY_START))

    def _run_setup_actions(self) -> None:
        """Drive every in-play character's on-setup ability in IN_GAME mode.

        At this point the engine has left ``Phase.SETUP`` and is in
        ``Phase.FIRST_NIGHT``. ``SETUP_PHASE`` absorption already ran
        on each ``assign_character`` call during setup; this pass uses
        ``SetupMode.IN_GAME`` so any character whose setup pick is
        still un-resolved (e.g. the storyteller didn't drag the Drunk
        token, didn't pick an FT red herring) will prompt the
        storyteller now.

        After each character's own on_setup_ability fires, the engine
        also runs the *perceived* character's on_setup_ability, if any
        (the Drunk-as-FT picks a red herring, etc.).
        """
        self._dispatch(Event(EventType.SETUP_START))
        for p in self._players:
            char = p.character
            if char is None:
                continue
            try:
                char.on_setup_ability(self, SetupMode.IN_GAME)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(f"Error in {char.name} on_setup_ability: {exc!r}")
            perceived = char.acting_perceived_character()
            if perceived is not None:
                try:
                    perceived.on_setup_ability(self, SetupMode.IN_GAME)
                except Exception as exc:  # pragma: no cover (defensive)
                    self.log(
                        f"Error in {char.name} (perceived "
                        f"{perceived.name}) on_setup_ability: {exc!r}"
                    )
        self._dispatch(Event(EventType.SETUP_END))

    def _build_action_order(self, night_number: int) -> List[Character]:
        """Order of characters acting this night.

        Sorted by their first/other_night_order. Characters with order
        0 are skipped. Dead players' characters are normally skipped,
        unless their ability is "if you die at night..." (Ravenkeeper)
        — handled by the character's own check inside ``ability``.

        Drunk-style impersonators contribute the impersonated role's
        slot too, so the Drunk-as-Empath wakes up at the Empath's
        order and walks through the (drunk) Empath ability.
        """
        chars: List[Character] = []
        for p in self._players:
            if p.character is None:
                continue
            if p.character.acts_on_night(night_number):
                chars.append(p.character)
            elif p.character.name == "Ravenkeeper" and night_number >= 2:
                # Ravenkeeper has other_night_order=45 already, so it's
                # included by the check above.
                pass
            perceived = p.character.acting_perceived_character()
            if perceived is not None and perceived.acts_on_night(night_number):
                chars.append(perceived)
        chars.sort(key=lambda c: c.night_order(night_number))
        return chars

    def advance_to_day(self) -> List[Player]:
        """End the night, move to day. Returns players who died this night."""
        if self._phase not in (Phase.FIRST_NIGHT, Phase.NIGHT):
            raise RuntimeError("advance_to_day requires NIGHT phase.")
        # Wait for the night thread to finish, if any.
        if self._night_thread and self._night_thread.is_alive():
            self._night_thread.join(timeout=1.0)

        deaths = list(self._pending_night_deaths)
        self._pending_night_deaths.clear()

        self._phase = Phase.DAY
        self._day_number += 1
        self._executed_today = False
        for p in self._players:
            p.reset_day_flags()
        self.log(f"Dawn: day {self._day_number} begins. "
                 f"Night deaths: {[p.name for p in deaths]}.")
        self._check_win_conditions()
        return deaths

    def advance_to_night(self) -> None:
        if self._phase is not Phase.DAY:
            raise RuntimeError("advance_to_night requires DAY phase.")
        # Dusk — fire DAY_END and run the dusk win check (Mayor's
        # 3-alive-no-execution condition activates here). If the game
        # ends, don't bother advancing the phase.
        self._dispatch(Event(EventType.DAY_END))
        self._check_win_conditions(at_dusk=True)
        if self._phase is Phase.FINISHED:
            return
        # Cure any one-day-only poisoning here; the Poisoner unpoisons
        # its previous target on its next ability, which serves the
        # same purpose, so this is a no-op for now.
        self._phase = Phase.NIGHT
        self._night_number += 1
        for p in self._players:
            p.reset_night_flags()
        self.log(f"Night {self._night_number} begins.")

    # ==================================================================
    #                        STORYTELLER MUTATORS
    # ==================================================================

    def kill(
        self,
        player_id: int,
        cause: DeathCause = DeathCause.STORYTELLER,
    ) -> Player:
        player = self.get_player(player_id)
        if player.dead:
            return player

        # Demon-kill protection (Soldier, Monk).
        if cause is DeathCause.DEMON_KILL:
            if player.protected_from_demon:
                self.log(f"{player.name!r} is protected from the Demon; no death.")
                return player
            if (
                player.character is not None
                and player.character.name == "Soldier"
                and player.has_ability
            ):
                self.log(f"{player.name!r} is the Soldier (safe from Demon).")
                return player

        player.kill(cause)
        self.log(f"{player.name!r} dies ({cause.value}).")

        if self._phase.is_night and cause is not DeathCause.EXECUTION:
            self._pending_night_deaths.append(player)

        self._dispatch(
            Event(EventType.DEATH, targets=[player], data={"cause": cause})
        )
        self._check_win_conditions()
        return player

    def revive(self, player_id: int) -> Player:
        player = self.get_player(player_id)
        player.revive()
        self.log(f"{player.name!r} is revived.")
        self._dispatch(Event(EventType.REVIVE, targets=[player]))
        return player

    def poison(self, player_id: int) -> None:
        player = self.get_player(player_id)
        player.set_poisoned(True)
        self.log(f"{player.name!r} is poisoned.")
        self._dispatch(Event(EventType.POISON, targets=[player]))

    def cure_poison(self, player_id: int) -> None:
        self.get_player(player_id).set_poisoned(False)
        self.log(f"{self.get_player(player_id).name!r} is no longer poisoned.")

    def make_drunk(self, player_id: int) -> None:
        self.get_player(player_id).set_drunk(True)
        self.log(f"{self.get_player(player_id).name!r} is drunk.")
        self._dispatch(Event(EventType.DRUNK, targets=[self.get_player(player_id)]))

    def sober_up(self, player_id: int) -> None:
        self.get_player(player_id).set_drunk(False)

    def change_character(self, player_id: int, character_name: str) -> None:
        player = self.get_player(player_id)
        char = script_data.build_character(character_name)
        player.change_character(char)
        self.log(f"{player.name!r} is now the {character_name}.")

    def execute_player(self, player_id: int) -> Player:
        if self._phase is not Phase.DAY:
            raise RuntimeError("Executions only happen during day.")
        player = self.get_player(player_id)
        player.kill(DeathCause.EXECUTION)
        # Latch "an execution happened today" — read by the Mayor's
        # 3-alive-no-execution win check at dusk.
        self._executed_today = True
        self.log(f"{player.name!r} is executed.")
        self._dispatch(
            Event(EventType.EXECUTION, targets=[player],
                  data={"cause": DeathCause.EXECUTION})
        )
        self._check_win_conditions()
        return player

    # ==================================================================
    #                       EVENT DISPATCH
    # ==================================================================

    def dispatch(self, event: Event) -> None:
        """Public alias used by character abilities."""
        self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        """Run reaction(event) on every player's character.

        Drunk-style impersonators also get reactions delivered to the
        impersonated role, so role-specific bookkeeping (Undertaker
        tracking executions, Virgin tracking nominations, …) keeps
        running on the Drunk's chair. The impersonated role's
        ``self.player.has_ability`` is False, so any state mutation
        gated on ``has_ability`` (Virgin's execute-the-nominator,
        Monk-style protection, …) takes the drunk/poisoned branch
        and resolves to no real game-state change — exactly as the
        rulebook requires.
        """
        for p in self._players:
            char = p.character
            if char is None:
                continue
            try:
                char.reaction(event, self)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(f"Reaction in {char.name} crashed: {exc!r}")
            perceived = char.acting_perceived_character()
            if perceived is not None:
                try:
                    perceived.reaction(event, self)
                except Exception as exc:  # pragma: no cover (defensive)
                    self.log(
                        f"Reaction in {char.name} (perceived "
                        f"{perceived.name}) crashed: {exc!r}"
                    )

    # ==================================================================
    #                       PROMPT BROKERAGE
    # ==================================================================

    def send_prompt(self, prompt: Prompt) -> Any:
        """Emit a prompt and block until the storyteller answers.

        Called from the night thread (or any thread that *isn't* the
        HTTP thread). Returns the storyteller's answer.

        Single-option short-circuit: if this is a storyteller-input
        select prompt (``meta["stage"]`` is ``"st_pre"`` or ``"st_post"``)
        whose eligible set has exactly one possible answer, we resolve
        it automatically without bothering the storyteller — there is
        nothing for them to choose between, so showing the prompt is
        just busywork. Logged so the action is still auditable.

        Step-meta inheritance: if there's a stashed
        ``_current_step_meta`` (set by ``_announce_step`` for character
        steps), its ``step_name`` / ``description`` / ``character`` /
        ``target_player_name`` are folded into this prompt's ``meta``
        so the UI can show the rulebook line and wake-up name without a
        separate announce prompt. Pre-existing keys on the prompt's
        ``meta`` win — abilities can override (e.g. a Spy info prompt
        keeping its own description). The stash is consumed on the
        first prompt of the step and cleared, so later prompts in the
        same ability still inherit through their own ``meta`` chain
        via the panel session in the UI.
        """
        self._merge_step_meta_into(prompt)
        auto = self._auto_resolve(prompt)
        if auto is not _NO_AUTO_RESOLVE:
            self.log(
                f"Auto-resolved {type(prompt).__name__} "
                f"(single eligible option): {auto!r}."
            )
            return auto
        with self._lock:
            self._pending_prompt = prompt
            self._prompt_response = None
            self._response_ready.clear()
        self.log(f"Prompt: {prompt.text}")
        self._response_ready.wait()
        with self._lock:
            response = self._prompt_response
            self._prompt_response = None
            self._pending_prompt = None
        return response

    def _merge_step_meta_into(self, prompt: Prompt) -> None:
        """Fold the current preset-step context into ``prompt.meta``.

        Called from ``send_prompt``. Only fills in keys the prompt
        didn't already specify so a character ability can still
        override (e.g. set its own ``description`` for a one-off
        info prompt). The stash is cleared on the first prompt of the
        step so later prompts in the same ability don't keep inheriting
        — by then the UI's panel session already has the header info.

        Also annotates the prompt with the target player's
        drunk/poisoned state (regardless of whether there's a stashed
        step meta) so the storyteller UI can italicize "(drunk)" /
        "(poisoned)" / "(drunk and poisoned)" next to the role name.
        """
        if prompt.meta is None:
            prompt.meta = {}
        meta = self._current_step_meta
        if meta:
            for key in (
                "step_kind",
                "step_name",
                "description",
                "character",
                "target_player_name",
            ):
                if key in meta and key not in prompt.meta:
                    prompt.meta[key] = meta[key]
            # Inherit target_player_id when the prompt didn't set its own
            # — without this, the panel-session key in the UI changes
            # between the announce-derived first prompt and later
            # prompts that DO set target_player_id, splitting one
            # ability across two panels.
            if prompt.target_player_id is None and meta.get("target_player_name"):
                # Look up the seated player by name (the only thing we
                # stored in step meta is the name; the id was on the
                # original announce-prompt's target_player_id field).
                for p in self._players:
                    if p.name == meta["target_player_name"]:
                        prompt.target_player_id = p.id
                        break
            self._current_step_meta = None
        # Drunk/poisoned annotation. Done after the step-meta merge
        # (which may have just filled in target_player_id) and outside
        # the `if meta` guard so prompts built directly with their own
        # meta — minion_info / demon_info / one-off info prompts — also
        # get labeled without each call site having to remember.
        if (
            prompt.target_player_id is not None
            and "drunk_poison_state" not in prompt.meta
        ):
            for p in self._players:
                if p.id == prompt.target_player_id:
                    label = p.drunk_poison_label()
                    if label:
                        prompt.meta["drunk_poison_state"] = label
                    break

    def _auto_resolve(self, prompt: Prompt) -> Any:
        """Return the storyteller's only possible answer, or ``_NO_AUTO_RESOLVE``.

        Applies only to storyteller-input stages (``st_pre``/``st_post``);
        player-decision and info stages are passed through untouched
        because the storyteller still needs to wait on the player /
        click "Next" themselves.
        """
        stage = None
        if isinstance(prompt.meta, dict):
            stage = prompt.meta.get("stage")
        if stage not in ("st_pre", "st_post"):
            return _NO_AUTO_RESOLVE

        if isinstance(prompt, SelectPlayerPrompt):
            eligible = list(prompt.eligible_player_ids)
            count = prompt.count
            if count == 1 and len(eligible) == 1:
                return eligible[0]
            if count > 1 and len(eligible) == count:
                # Forced multi-selection: every eligible player must be
                # picked — there is no other valid response.
                return list(eligible)
            return _NO_AUTO_RESOLVE

        if isinstance(prompt, SelectCharacterPrompt):
            if len(prompt.eligible_characters) == 1:
                return prompt.eligible_characters[0]
            return _NO_AUTO_RESOLVE

        return _NO_AUTO_RESOLVE

    def pending_prompt(self) -> Optional[Prompt]:
        """The prompt currently waiting for a response, or None."""
        with self._lock:
            return self._pending_prompt

    def respond(self, prompt_id: int, response: Any) -> bool:
        """Hand a storyteller response to the night thread.

        Returns True if the response was accepted, False if there was
        no pending prompt or the id didn't match (stale response).
        """
        with self._lock:
            if self._pending_prompt is None:
                return False
            if self._pending_prompt.id != prompt_id:
                return False
            self._prompt_response = response
            self._response_ready.set()
        return True

    # ==================================================================
    #                       WIN CONDITIONS
    # ==================================================================

    def _check_win_conditions(self, at_dusk: bool = False) -> None:
        if self._phase is Phase.FINISHED:
            return
        alive = self.alive_players
        alive_demons = [
            p for p in alive
            if p.char_type is CharType.DEMON
        ]
        if not alive_demons:
            self._end_game(Alignment.GOOD, "The Demon is dead.")
            return
        counted = [
            p for p in alive
            if p.char_type not in (CharType.TRAVELER, CharType.FABLED)
        ]
        if len(counted) <= 2:
            self._end_game(Alignment.EVIL, "Only two players remain.")
            return

        # Mayor: at dusk, if exactly 3 non-Traveler/Fabled players are
        # alive and no execution happened today, the Mayor's team wins.
        # The Mayor's alignment is read from the player (could be evil
        # in non-Trouble-Brewing scripts).
        if at_dusk and not self._executed_today and len(counted) == 3:
            mayor_player = next(
                (
                    p for p in alive
                    if p.character is not None
                    and p.character.name == "Mayor"
                    and p.has_ability
                ),
                None,
            )
            if mayor_player is not None:
                winner = mayor_player.alignment or Alignment.GOOD
                self.log(
                    f"Mayor {mayor_player.name} triggers win — "
                    f"3 alive, no execution; "
                    f"{winner.value} wins."
                )
                self._end_game(
                    winner,
                    "Mayor: 3 alive players and no execution today.",
                )

    def _end_game(self, winner: Alignment, reason: str) -> None:
        self._phase = Phase.FINISHED
        self._winner = winner
        self._win_reason = reason
        self.log(f"Game over: {winner.value} wins — {reason}")

    # ==================================================================
    #                       SNAPSHOTS
    # ==================================================================

    def snapshot(self) -> dict:
        """A storyteller-view JSON-serializable snapshot of the game.

        Includes the town-square layout (``chairs`` + ``storyteller``)
        so any consumer of the snapshot can reconstruct the entire UI
        without external state.
        """
        return {
            "phase": self._phase.value,
            "night_number": self._night_number,
            "day_number": self._day_number,
            "players": [p.snapshot() for p in self.players],
            "pending_night_deaths": [
                p.id for p in self._pending_night_deaths
            ],
            "winner": self._winner.value if self._winner else None,
            "win_reason": self._win_reason,
            "log_tail": self._log[-50:],
            "chairs": self.chairs.list(),
            "storyteller": self.chairs.get_storyteller(),
            "pool": self.pool.list(),
            "drunk_fake": self.pool.drunk_fake(),
            "ft_red_herring": self.pool.ft_red_herring(),
            "washerwoman_townsfolk": self.pool.washerwoman_townsfolk(),
            "washerwoman_wrong": self.pool.washerwoman_wrong(),
            "selected_preset": self.selected_preset_name,
        }

    def player_view(self, player_id: int) -> dict:
        """What ONE player sees on their phone (no other characters revealed)."""
        me = self.get_player(player_id)
        char_name = me.perceived_character_name or (
            me.character.name if me.character else None
        )
        return {
            "me": {
                "id": me.id,
                "name": me.name,
                "seat": me.seat,
                "character": char_name,
                "alignment": me.alignment.value if me.alignment else None,
                "alive": me.alive,
            },
            "others": [
                {
                    "id": p.id,
                    "name": p.name,
                    "seat": p.seat,
                    "alive": p.alive,
                    "has_dead_vote": p.has_dead_vote,
                }
                for p in self.players if p.id != player_id
            ],
            "phase": self._phase.value,
            "night_number": self._night_number,
            "day_number": self._day_number,
        }
