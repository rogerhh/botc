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
* Day-time per-player actions (driven by the Local UI side panel):
  ``nominate``, ``record_vote``, ``execute_player``,
  ``use_daytime_ability``.
* UI broker: ``pending_prompt``, ``respond``.
* Snapshot: ``snapshot``, ``player_view``.

The engine does not adjudicate character-specific rules itself — that's
delegated to the :class:`Character` subclasses, which call into the
engine for prompts and state changes.
"""

from __future__ import annotations

import base64
import itertools
import pickle
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


class _AbortAbility(BaseException):
    """Raised inside ``Engine.send_prompt`` when the Storyteller hits Back.

    Inherits from :class:`BaseException` (not :class:`Exception`) so the
    ``except Exception`` wrappers in :meth:`Engine._run_preset_night`
    don't swallow the abort — it has to bubble all the way up to
    :meth:`Engine._run_night`, which discards the half-run ability and
    lets :meth:`Engine.back` resume the night from a checkpoint.
    """

    pass


# Save-string envelope. The first byte of the base64-decoded payload is
# a version tag so future formats can be added without breaking saves.
_SAVE_STATE_VERSION = 1


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
        #
        # ``_winner`` / ``_win_reason`` are only set once the game has
        # actually ended (phase == FINISHED). Per the project rule
        # *Game should only end after a night*: when a win condition is
        # detected at any other time we record the result on the
        # ``_pending_winner`` / ``_pending_win_reason`` slots and let
        # the day or current night play out. The next dawn calls
        # :meth:`_finalize_pending_win`, which copies the pending slots
        # onto the live ``_winner`` slots, flips the phase to FINISHED
        # and emits the public ``game_end`` console event.
        self._winner: Optional[Alignment] = None
        self._win_reason: Optional[str] = None
        self._pending_winner: Optional[Alignment] = None
        self._pending_win_reason: Optional[str] = None

        # Set when ANY execution has happened today. Cleared at dawn.
        # Read by ``_check_win_conditions`` for the Mayor's
        # "3-alive-no-execution" win at dusk.
        self._executed_today: bool = False

        # Scarlet Woman bookkeeping.
        # ``_sw_promoted_player_ids`` is the persistent set of player ids
        # whose seat began the game as the Scarlet Woman and have been
        # promoted to the Demon by the SW reaction. The UI grimoire keys
        # off this list to render the "Scarlet Woman IS THE DEMON"
        # reminder token on the SW's seat for the rest of the game.
        # ``_sw_pending_demon_reveal`` is the queue of those player ids
        # still awaiting the night-time "YOU ARE the <Demon>" reveal at
        # the preset's "Scarlet Woman" step (per the trouble-brewing
        # night sheet). Drained by ``_run_scarlet_woman_step``.
        self._sw_promoted_player_ids: List[int] = []
        self._sw_pending_demon_reveal: List[int] = []

        # Callbacks to run after a DEATH dispatch completes, before
        # ``_check_win_conditions`` runs. Lets a character's reaction
        # detect a death of interest (e.g. the Imp's self-kill flow)
        # and *defer* the actual handling until every other reaction
        # has had a chance to fire — so reactions like the Scarlet
        # Woman's "if the Demon dies, you become the Demon" promotion
        # have already taken effect by the time the deferred callback
        # observes engine state. Drained by ``Engine.kill`` after each
        # DEATH dispatch.
        self._post_death_callbacks: List[Callable[[], None]] = []

        # Player ids that the Demon has killed since the last dawn.
        # Drives the grimoire DEAD reminder token (Imp/Pukka/etc.):
        # the token is placed when the Demon's nightly kill lands and
        # cleared at the end of the night, when ``advance_to_day`` /
        # ``_auto_dawn`` runs. The persistent ``Player.death_cause``
        # is left untouched so post-mortem rulings (Undertaker info,
        # win-condition reads, etc.) can still tell *how* the player
        # died — only the visual reminder is transient.
        self._demon_killed_player_ids: List[int] = []

        # Storyteller-readable event log.
        self._log: List[str] = []

        # Structured "console" log surfaced live to the storyteller and
        # replayed verbatim as the end-of-game report. Each entry is
        # a typed, curated event (an ability firing, info shown to a
        # player, a kill, a revive, a nomination, a selection, a phase
        # transition, etc.) — distinct from the noisy free-form
        # ``_log`` which mirrors every internal happening.
        #
        # Each entry is a dict with at least ``ts``, ``phase``,
        # ``night_number``, ``day_number``, ``kind``, ``summary``; the
        # ``details`` sub-dict is kind-specific (see ``_console_log``).
        self._console: List[Dict[str, Any]] = []

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

        # ---- Save / load + Back-button history ----
        # Index of the next preset step to run within the current
        # night. Reset to 0 by ``start_night``; advanced by
        # ``_run_preset_night`` after each step completes. Persisted in
        # save_state so restoring mid-night resumes from the correct
        # step without re-running already-completed abilities.
        self._completed_step_index: int = 0
        # History of pickled engine snapshots, taken after each
        # preset-night step completes (and after each setup-action
        # character runs). The Back button pops the latest entry and
        # restores it; consecutive Back presses walk further back.
        self._history: List[str] = []
        self._history_labels: List[str] = []
        # Set by :meth:`back` to interrupt a blocked ``send_prompt``.
        # The waiting character ability sees the flag, raises
        # ``_AbortAbility``, and propagates out of ``_run_night``.
        self._abort_requested: bool = False

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
    def console(self) -> List[Dict[str, Any]]:
        """Structured live-console / end-of-game report log.

        Read-only copy; the engine owns the underlying list and appends
        to it from ``_console_log`` (called by ``send_prompt``,
        ``_announce_step``, ``kill``, ``revive``, ``poison`` /
        ``make_drunk`` / ``sober_up`` / ``cure_poison``,
        ``execute_player``, ``nominate``, phase-transition helpers,
        and ``_end_game``).
        """
        return [dict(e) for e in self._console]

    @property
    def winner(self) -> Optional[Alignment]:
        """Final winner, set only once the game has actually ended.

        While a win has been triggered but the game is still waiting
        for dawn to announce, this remains ``None``. Use
        :attr:`pending_winner` to inspect a triggered-but-not-yet-
        announced win.
        """
        return self._winner

    @property
    def win_reason(self) -> Optional[str]:
        return self._win_reason

    @property
    def pending_winner(self) -> Optional[Alignment]:
        """A win condition has triggered but the game is waiting for dawn.

        Per the project rule: end-of-game announcements always happen
        at dawn. When the engine detects a winning condition during
        the day or mid-night, the alignment is parked here and players
        keep going (use abilities, nominate, etc.); the next dawn
        copies it onto :attr:`winner` and flips the phase to FINISHED.
        """
        return self._pending_winner

    @property
    def pending_win_reason(self) -> Optional[str]:
        return self._pending_win_reason

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
    # Console log (drives both the live storyteller console panel and
    # the end-of-game report — they are the same data, rendered twice).
    # ------------------------------------------------------------------

    def log_reaction(
        self,
        character: Optional[str],
        summary: str,
        *,
        target: Optional["Player"] = None,
        target_player_id: Optional[int] = None,
        target_player_name: Optional[str] = None,
        trigger: Optional[str] = None,
        **details: Any,
    ) -> None:
        """Log an observable character reaction to the console feed.

        ``character`` is the role whose ability fired (e.g. "Soldier",
        "Monk", "Virgin", "Mayor", "Saint", "Scarlet Woman"). The
        ``summary`` is a one-line, storyteller-readable statement of
        what observably happened (e.g. "Soldier — Bob cannot be
        killed by the Demon.").

        ``target`` / ``target_player_id`` / ``target_player_name`` is
        whoever the reaction acted on (the player saved, the new
        Demon, the executed nominator, …) — any one is fine; we
        normalize. ``trigger`` is the EventType (or free-form label)
        that caused the reaction to fire, captured for the report
        details so you can later reconstruct the chain.

        Also mirrors to the human-readable ``_log`` so the operator
        terminal still sees the line.
        """
        # Normalize target details from whichever form the caller used.
        if target is not None:
            try:
                target_player_id = int(target.id)
                target_player_name = target.name
            except Exception:  # pragma: no cover (defensive)
                pass

        char_label = character or "?"
        # Mirror to the noisy log as well so the operator can grep for
        # reactions in the terminal even if they aren't watching the
        # console panel.
        self.log(f"[reaction] {char_label}: {summary}")
        self._console_log(
            "reaction",
            summary,
            character=char_label,
            target_player_id=target_player_id,
            target_player_name=target_player_name,
            trigger=trigger,
            **details,
        )

    def _console_log(self, kind: str, summary: str, **details: Any) -> None:
        """Append a structured entry to the console log.

        ``kind`` is one of: ``ability``, ``info``, ``selection``,
        ``kill``, ``revive``, ``execution``, ``nomination``,
        ``state``, ``phase``, ``game_end``. The entry is timestamped
        and stamped with the current phase / night / day numbers so
        the renderer can group entries by phase. Defensive: any
        exception is swallowed so logging never breaks gameplay.
        """
        try:
            entry: Dict[str, Any] = {
                "ts": time.strftime("%H:%M:%S"),
                "phase": self._phase.value,
                "night_number": self._night_number,
                "day_number": self._day_number,
                "kind": kind,
                "summary": summary,
                "details": dict(details),
            }
            self._console.append(entry)
        except Exception:  # pragma: no cover (defensive)
            pass

    def _record_prompt_response(self, prompt: Prompt, response: Any) -> None:
        """Log an interesting prompt+response pair to the console.

        Called from ``send_prompt`` once the storyteller has answered.
        Filters out announce-style information prompts that are not
        shown to a player (e.g. Dusk / Dawn banners).

        ``info`` covers any InformationPrompt that targets a specific
        player and was visible to them on their phone (Empath /
        Washerwoman / Fortune Teller / Undertaker / Investigator,
        minion- and demon-info, ...).

        ``selection`` covers SelectPlayer / SelectCharacter / YesNo
        prompts. Whether the choice is the storyteller's (drunk /
        poisoned overrides, demon bluffs) or the player's own decision
        (Fortune Teller picks 2 players, Monk picks a target, Butler
        picks a master, ...) is recorded under ``details.actor``.
        """
        meta = prompt.meta if isinstance(prompt.meta, dict) else {}
        character = meta.get("character") or ""
        target_pid = prompt.target_player_id
        target_name = meta.get("target_player_name") or (
            self._safe_player_name(target_pid) if target_pid is not None else ""
        )
        stage = meta.get("stage")
        is_st_stage = stage in ("st_pre", "st_post")

        # InformationPrompt: only record the ones a player was meant to see.
        if isinstance(prompt, InformationPrompt):
            if not getattr(prompt, "shown_to_player", False):
                return
            if target_pid is None:
                return
            highlight_ids = list(getattr(prompt, "highlight_player_ids", []) or [])
            highlight_chars = list(getattr(prompt, "highlight_characters", []) or [])
            highlight_names = [
                self._safe_player_name(pid) for pid in highlight_ids
            ]
            label = character or "Info"
            who = target_name or f"player {target_pid}"
            extras: List[str] = []
            if highlight_chars:
                extras.append("characters: " + ", ".join(highlight_chars))
            if highlight_names:
                extras.append("players: " + ", ".join(highlight_names))
            tail = (" — " + "; ".join(extras)) if extras else ""
            summary = f"Showed {label} ({who}){tail}"
            self._console_log(
                "info",
                summary,
                character=character or None,
                target_player_id=target_pid,
                target_player_name=target_name or None,
                text=prompt.text,
                highlight_player_ids=highlight_ids,
                highlight_player_names=highlight_names,
                highlight_characters=highlight_chars,
                drunk_poison_state=meta.get("drunk_poison_state"),
                step_kind=meta.get("step_kind"),
                step_name=meta.get("step_name"),
            )
            return

        # Select / yes-no prompts → "selection". Whether the choice is
        # the storyteller's or the player's lives in details.actor.
        actor = "Storyteller" if is_st_stage else (
            target_name or (character or "Player")
        )
        char_label = character or "?"

        if isinstance(response, list):
            value_str = ", ".join(str(x) for x in response) or "(none)"
        elif response is True:
            value_str = "Yes"
        elif response is False:
            value_str = "No"
        elif response is None:
            value_str = "(none)"
        else:
            value_str = str(response)

        # Translate player-id responses into names for readability.
        selected_player_ids: List[int] = []
        selected_player_names: List[str] = []
        if isinstance(prompt, SelectPlayerPrompt):
            ids = response if isinstance(response, list) else (
                [] if response is None else [response]
            )
            for pid in ids:
                try:
                    selected_player_ids.append(int(pid))
                    selected_player_names.append(self._safe_player_name(int(pid)))
                except (TypeError, ValueError):
                    continue
            value_str = ", ".join(selected_player_names) or value_str

        summary = (
            f"{actor} → {char_label}"
            + (f" ({target_name})" if target_name and not is_st_stage else "")
            + f": {value_str}"
        )
        self._console_log(
            "selection",
            summary,
            actor=actor,
            character=character or None,
            target_player_id=target_pid,
            target_player_name=target_name or None,
            stage=stage,
            is_storyteller_pick=bool(is_st_stage),
            step=meta.get("step"),
            step_kind=meta.get("step_kind"),
            step_name=meta.get("step_name"),
            response=response,
            selected_player_ids=selected_player_ids,
            selected_player_names=selected_player_names,
            prompt_text=prompt.text,
        )

    def _safe_player_name(self, player_id: Optional[int]) -> str:
        """Best-effort player name lookup that never raises."""
        if player_id is None:
            return ""
        try:
            return self.get_player(int(player_id)).name
        except (KeyError, TypeError, ValueError):
            return f"player {player_id}"

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

    def char_type_of(self, name: str) -> CharType:
        """Return the :class:`CharType` of a role given its name.

        Used by detection-side abilities that look at a player's
        *registered* role name (returned from
        :meth:`Character.registers_as`) and need the corresponding
        char_type to count alignment, check Demon status, etc.

        Raises :class:`KeyError` if the name is not on the script.
        """
        return script_data.SCRIPT_BY_NAME[name].char_type

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
        # Per-character seed: each Character class declares any
        # seat-bound side effect via ``on_assign_to_seat`` (the Drunk
        # marks itself drunk, etc.). The engine has no character-name
        # knowledge here.
        try:
            char.on_assign_to_seat(self)
        except Exception as exc:  # pragma: no cover (defensive)
            self.log(f"Error in {char.name} on_assign_to_seat: {exc!r}")
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
          * ``librarian_outsider`` — the Outsider role the Librarian is
            shown. We pre-set ``self._chosen_outsider`` on the Librarian
            so its nightly ability skips the "pick an Outsider" prompt.
          * ``librarian_wrong`` — the WRONG role for the Librarian.
            When both ``librarian_outsider`` and ``librarian_wrong``
            are set, the Librarian's first-night ability skips every
            storyteller prompt.
          * ``investigator_minion`` — the Minion role the Investigator
            is shown. We pre-set ``self._chosen_minion`` on the
            Investigator so its nightly ability skips the
            "pick a Minion" prompt.
          * ``investigator_wrong`` — the WRONG role for the
            Investigator. When both ``investigator_minion`` and
            ``investigator_wrong`` are set, the Investigator's
            first-night ability skips every storyteller prompt.

        Idempotent: passing the same ``data`` twice is fine. Missing
        keys leave the existing pick (if any) untouched.
        """
        # Generic dispatch — every seated character absorbs the setup
        # data its class declares. The engine has no character-name
        # knowledge here; new roles wire themselves in by overriding
        # :meth:`Character.absorb_setup_data`.
        for player in self._players:
            char = player.character
            if char is None:
                continue
            try:
                char.absorb_setup_data(self, data)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(
                    f"absorb_setup_data crashed in {char.name}: {exc!r}"
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
        # Fresh game → blank slate for the Back-button history. The
        # storyteller can still rewind to the start of night 1 once
        # the night begins (start_night pushes its own checkpoint).
        self.reset_history()
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

    def _role_could_pass(self, name: str, the_check) -> bool:
        """Setup-time eligibility test: could a chair holding ``name``
        pass ``the_check`` at run time?

        Looks up the character class for ``name`` and delegates to
        :meth:`Character.could_pass_check`. Used by the token-application
        helpers below: a Spy chair is eligible for a WW TOWNSFOLK
        token (Spy can register as TF), a Recluse chair is eligible
        for an INV MINION token (Recluse can register as Minion), etc.

        Returns False for unknown names.
        """
        spec = script_data.SCRIPT_BY_NAME.get(name)
        if spec is None:
            return False
        from engine.characters import CHARACTER_REGISTRY
        from engine.character import Character
        cls = CHARACTER_REGISTRY.get(name)
        if cls is None:
            # Stub-class character (no registered subclass): use the
            # script's char_type as its registration_categories so the
            # default-Character path still works.
            class _Spec(Character):
                pass
            _Spec.char_type = spec.char_type
            cls = _Spec
        return cls.could_pass_check(the_check)

    def _townsfolk_in_play(self, name: str) -> bool:
        """True iff a chair holding ``name`` could register as a Townsfolk.

        Used by token-application helpers (WW TOWNSFOLK seen-token,
        Drunk token). With registration semantics, a Spy chair is also
        eligible (Spy may register as a Townsfolk for the WW). The
        Drunk token's caller still semantically wants a *true* TF role,
        but at the seat level "could register as TF" is a strict superset
        and the Drunk's swap logic guards on the actual role anyway.
        """
        from engine.check import Check
        return self._role_could_pass(
            name, Check(attribute="char_type", passes=(CharType.TOWNSFOLK,))
        )

    def _good_in_play(self, name: str) -> bool:
        """True iff a chair holding ``name`` could register as good.

        Good = Townsfolk or Outsider. Used by the FT red-herring token
        (the herring is "a good player").
        """
        from engine.check import Check
        return self._role_could_pass(
            name,
            Check(
                attribute="char_type",
                passes=(CharType.TOWNSFOLK, CharType.OUTSIDER),
            ),
        )

    def _outsider_in_play(self, name: str) -> bool:
        """True iff a chair holding ``name`` could register as an Outsider."""
        from engine.check import Check
        return self._role_could_pass(
            name, Check(attribute="char_type", passes=(CharType.OUTSIDER,))
        )

    def _minion_in_play(self, name: str) -> bool:
        """True iff a chair holding ``name`` could register as a Minion."""
        from engine.check import Check
        return self._role_could_pass(
            name, Check(attribute="char_type", passes=(CharType.MINION,))
        )

    def _true_townsfolk(self, name: str) -> bool:
        """True iff ``name`` is *literally* a Townsfolk role on the script.

        Used by the Drunk-token swap (the chair's current role becomes
        the Drunk's perceived TF — and the perceived TF must be a real
        Townsfolk, not a misregistered one).
        """
        spec = script_data.SCRIPT_BY_NAME.get(name)
        return spec is not None and spec.char_type is CharType.TOWNSFOLK

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
        # Drunk token requires the chair to literally hold a Townsfolk
        # role — the perceived TF the Drunk thinks they are has to be a
        # real Townsfolk on the script, not a misregistered one. So we
        # use the strict ``_true_townsfolk`` test here, NOT the
        # registration-based ``_townsfolk_in_play``.
        if not self._true_townsfolk(dest_char):
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

    def move_librarian_outsider_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the LIBRARIAN OUTSIDER reminder onto ``dest_chair_id``.

        The destination chair's character must be an Outsider currently
        in the pool.
        """
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        if "Librarian" not in self.pool.list():
            return "Librarian is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        if not self._outsider_in_play(dest_char):
            return "destination chair must hold an Outsider role"
        if dest_char not in self.pool.list():
            return f"{dest_char!r} is not in the pool"
        try:
            self.pool.set_librarian_outsider(dest_char)
        except ValueError as exc:
            return str(exc)
        # Re-absorb so the Librarian's _chosen_outsider reflects the
        # move.
        self._retrigger_setup_for_role("Librarian")
        return None

    def move_investigator_minion_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the INVESTIGATOR MINION reminder onto ``dest_chair_id``.

        The destination chair's character must be a Minion currently
        in the pool.
        """
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        if "Investigator" not in self.pool.list():
            return "Investigator is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        if not self._minion_in_play(dest_char):
            return "destination chair must hold a Minion role"
        if dest_char not in self.pool.list():
            return f"{dest_char!r} is not in the pool"
        try:
            self.pool.set_investigator_minion(dest_char)
        except ValueError as exc:
            return str(exc)
        # Re-absorb so the Investigator's _chosen_minion reflects the
        # move.
        self._retrigger_setup_for_role("Investigator")
        return None

    def move_librarian_wrong_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the LIBRARIAN WRONG reminder onto ``dest_chair_id``.

        Per the rulebook the WRONG token goes "by any *other*
        character token" — meaning any seated character except the
        Librarian herself and the seen-Outsider.
        """
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        if "Librarian" not in self.pool.list():
            return "Librarian is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        if dest_char not in self.pool.list():
            return f"{dest_char!r} is not in the pool"
        try:
            self.pool.set_librarian_wrong(dest_char)
        except ValueError as exc:
            return str(exc)
        self._retrigger_setup_for_role("Librarian")
        return None

    def move_investigator_wrong_token(self, dest_chair_id: int) -> Optional[str]:
        """Drop the INVESTIGATOR WRONG reminder onto ``dest_chair_id``.

        Per the rulebook the WRONG token goes "by any *other*
        character token" — meaning any seated character except the
        Investigator herself and the seen-Minion.
        """
        dest = self.chairs.get(dest_chair_id)
        if dest is None:
            return f"no chair with id {dest_chair_id}"
        if "Investigator" not in self.pool.list():
            return "Investigator is not in the pool"
        dest_char = (dest.get("character") or "").strip()
        if not dest_char:
            return "destination chair has no character assigned"
        if dest_char not in self.pool.list():
            return f"{dest_char!r} is not in the pool"
        try:
            self.pool.set_investigator_wrong(dest_char)
        except ValueError as exc:
            return str(exc)
        self._retrigger_setup_for_role("Investigator")
        return None

    # ------------------------------------------------------------------
    # Unified token-apply with swap semantics.
    #
    # Each ``move_*_token`` method above handles one token at a time and
    # is the right primitive for tests + the engine. The UI, however,
    # benefits from one entry point that knows which mutex pairs swap
    # rather than overwrite: dragging the WW TOWNSFOLK token onto a
    # chair already carrying the WW WRONG token swaps the two; ditto
    # Librarian and Investigator pairs. The non-paired tokens (Drunk,
    # FT red herring) just delegate to the underlying ``move_*``.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Setup-pick registry, built lazily from every Character class'
    # ``setup_picks`` declaration. The engine's apply_token logic, the
    # snapshot's setup-token getters, and the pool-side dispatch
    # (autofill / clear / reroll) all read from this registry — there
    # are no character-name lookups in this engine file. Adding a new
    # role with a new setup pick is a class-level declaration plus
    # corresponding pool-slot methods.
    # ------------------------------------------------------------------

    _setup_pick_registry_cache: Optional[Dict[str, Dict[str, Any]]] = None

    @classmethod
    def _setup_pick_registry(cls) -> Dict[str, Dict[str, Any]]:
        """Return ``{kind: spec_dict}`` for every setup pick on every
        registered character class, plus owning-role metadata.

        Cached at the class level — registry contents are pure class
        attributes and never change at runtime.
        """
        if cls._setup_pick_registry_cache is not None:
            return cls._setup_pick_registry_cache
        from engine.characters import CHARACTER_REGISTRY
        registry: Dict[str, Dict[str, Any]] = {}
        for role_name, klass in CHARACTER_REGISTRY.items():
            for spec in getattr(klass, "setup_picks", ()) or ():
                kind = spec.get("kind")
                if not kind or kind in registry:
                    continue
                merged = dict(spec)
                merged["owner_role"] = role_name
                registry[kind] = merged
        cls._setup_pick_registry_cache = registry
        return registry

    def _setup_spec(self, kind: str) -> Optional[Dict[str, Any]]:
        return self._setup_pick_registry().get(kind)

    def _token_role_for_kind(self, kind: str) -> Optional[str]:
        """Return the *character name* the token of ``kind`` currently
        sits on, or ``None`` if no chair carries it.

        Generic dispatch — looks up the kind's ``getter`` in the
        registry and reads the pool. The Drunk's IS-THE-DRUNK kind
        is handled specially because it tracks a chair (the seat
        whose character is "Drunk") rather than a pool slot.
        """
        spec = self._setup_spec(kind)
        if spec is None:
            return None
        if spec.get("triggers_seat_swap"):
            owner = spec.get("owner_role")
            for c in self.chairs.list():
                if (c.get("character") or "").strip() == owner:
                    return owner
            return None
        getter_name = spec.get("getter")
        if not getter_name:
            return None
        getter = getattr(self.pool, getter_name, None)
        return getter() if getter else None

    def _token_typed_kinds(self) -> "frozenset[str]":
        """Token kinds that are the *typed* (seen) half of a mutex pair."""
        return frozenset(
            kind for kind, spec in self._setup_pick_registry().items()
            if spec.get("is_typed")
        )

    def _token_mutex_partner(self, kind: str) -> Optional[str]:
        spec = self._setup_spec(kind)
        if spec is None:
            return None
        partners = spec.get("mutex_with") or ()
        return partners[0] if partners else None

    def _token_move_method(self, kind: str):
        """Return the ``move_*`` method for ``kind`` (or None).

        Each setup-pick spec names its own per-slot validator on the
        Engine — the existing per-kind ``move_*_token`` methods stay
        as the per-slot validators (they encode rules like "WRONG
        must differ from seen"); ``apply_token`` dispatches to them
        through the registry.
        """
        spec = self._setup_spec(kind)
        if spec is None:
            return None
        # Convention: move_<kind>_token. The Drunk uses move_drunk_token
        # (no _<kind>_ infix since there's only the one Drunk slot).
        method_name = "move_" + kind + "_token"
        return getattr(self, method_name, None)

    def apply_token(self, kind: str, dest_chair_id: int) -> Optional[str]:
        """Apply token ``kind`` to ``dest_chair_id`` with swap semantics.

        If the destination chair currently carries the mutex partner of
        ``kind`` (e.g. dropping WW TOWNSFOLK on a chair carrying WW
        WRONG), the partner is moved to the chair that previously held
        ``kind`` so the two effectively swap places. For all other
        cases this delegates to the corresponding ``move_*_token``
        method, whose autofill rules handle invalidations (e.g. the
        Drunk taking over a chair the WW had pointed at).

        Returns ``None`` on success, or a human-readable error string
        on rejection (mirrors the underlying ``move_*`` methods).
        """
        move = self._token_move_method(kind)
        if move is None:
            return f"unknown token kind {kind!r}"

        partner_kind = self._token_mutex_partner(kind)
        if partner_kind is not None:
            dest = self.chairs.get(dest_chair_id)
            if dest is None:
                return f"no chair with id {dest_chair_id}"
            dest_char = (dest.get("character") or "").strip()
            partner_role = self._token_role_for_kind(partner_kind)
            if partner_role and dest_char and dest_char == partner_role:
                # Swap path. Capture the chair currently holding
                # ``kind`` *before* we overwrite anything, then apply
                # the two moves in the right order so the pool's
                # "WRONG must differ from seen" validation never trips:
                # set the typed/seen slot first (a Townsfolk/Outsider/
                # Minion role), then the WRONG slot. Each of the
                # individual ``set_*`` calls happens to also clear and
                # autofill the partner slot, but the second move
                # overwrites that autofilled value with the role we
                # actually want, so the end state matches the swap.
                source_role = self._token_role_for_kind(kind)
                source_chair_id: Optional[int] = None
                if source_role:
                    for c in self.chairs.list():
                        if (c.get("character") or "").strip() == source_role:
                            source_chair_id = c["id"]
                            break
                if (
                    source_chair_id is None
                    or source_chair_id == dest_chair_id
                ):
                    # No real swap to do — fall through to the regular
                    # move. (Either the kind isn't currently seated, or
                    # the user dropped it back where it started.)
                    return move(dest_chair_id)

                # Decide which of (kind, partner_kind) is the typed slot.
                typed_kinds = self._token_typed_kinds()
                if kind in typed_kinds:
                    typed_kind, typed_chair = kind, dest_chair_id
                    wrong_kind, wrong_chair = partner_kind, source_chair_id
                else:
                    typed_kind, typed_chair = partner_kind, source_chair_id
                    wrong_kind, wrong_chair = kind, dest_chair_id

                typed_move = self._token_move_method(typed_kind)
                wrong_move = self._token_move_method(wrong_kind)
                if typed_move is None or wrong_move is None:
                    # Defensive — shouldn't happen for the documented
                    # mutex pairs, but log + fall back to the basic
                    # move so the storyteller's drag still does
                    # *something* useful.
                    self.log(
                        f"apply_token swap: missing move method for "
                        f"{typed_kind!r} / {wrong_kind!r}"
                    )
                    return move(dest_chair_id)

                err = typed_move(typed_chair)
                if err is None:
                    err = wrong_move(wrong_chair)
                    if err is not None:
                        self.log(
                            f"apply_token swap fallback: "
                            f"{wrong_kind} → chair {wrong_chair} failed: {err}"
                        )
                    return None

                # The clean swap failed — typically because the source
                # chair's character isn't compatible with the seen
                # token's required type (e.g. user drags Lib WRONG
                # from a Townsfolk chair onto the Lib SEEN chair). If
                # there's *another* in-pool role of the right type the
                # seen token can move to, do a "displaced swap": land
                # WRONG on dest, autofill seen onto a different valid
                # chair. If there are no other candidates, refuse the
                # drop so the seen token doesn't disappear.
                alt_candidates = self._seen_candidates_excluding(
                    typed_kind, dest_char
                )
                if not alt_candidates:
                    self.log(
                        f"apply_token swap infeasible and no alternate "
                        f"seen candidate ({typed_kind}); refusing drop"
                    )
                    return f"swap infeasible: {err}"

                self.log(
                    f"apply_token swap infeasible "
                    f"({typed_kind} → chair {typed_chair}): {err}; "
                    f"performing displaced swap (seen rerolls to a "
                    f"different chair)"
                )
                self._clear_token_slot(typed_kind)
                err2 = wrong_move(wrong_chair)
                if err2 is not None:
                    return err2
                self._reroll_seen_excluding(typed_kind, dest_char)
                return None

        return move(dest_chair_id)

    def _clear_token_slot(self, kind: str) -> None:
        """Set the pool slot for ``kind`` to None via the registry.

        Used by :meth:`apply_token` when an in-progress swap turns
        out to be infeasible — we need to clear the typed slot so the
        partner WRONG can be set without tripping the "must differ
        from seen" validation, then trigger a fresh autofill pick.
        """
        spec = self._setup_spec(kind)
        if spec is None:
            return
        setter = getattr(self.pool, spec.get("setter") or "", None)
        if setter is None:
            return
        try:
            setter(None)
        except ValueError:
            pass

    def _seen_candidates_excluding(
        self, typed_kind: str, exclude_role: str
    ) -> List[str]:
        """Return all in-pool roles that could legitimately receive
        the typed seen token, *excluding* ``exclude_role``.

        Generic across token kinds: the registry's ``check`` value
        names a Check-style attribute/passes pair; we apply it through
        the existing :meth:`_role_could_pass` helper plus the
        ``forbid_self`` rule.
        """
        spec = self._setup_spec(typed_kind)
        if spec is None:
            return []
        owner = spec.get("owner_role")
        return [
            n for n in self.pool.list()
            if n != exclude_role
            and (not spec.get("forbid_self") or n != owner)
            and self._role_passes_setup_check(n, spec.get("check"))
        ]

    def _reroll_seen_excluding(
        self, typed_kind: str, exclude_role: str
    ) -> None:
        """Pick a random new role for the typed seen-slot that isn't
        ``exclude_role``.

        Used by :meth:`apply_token` after the WRONG slot has been
        forced to ``exclude_role`` — we need to re-pick the seen slot
        so the pool invariant "WRONG must differ from seen" stays
        valid. Uses the registry's ``setter`` so all side-effect
        bookkeeping (retriggering setup absorption) runs.
        """
        import random as _random
        candidates = self._seen_candidates_excluding(typed_kind, exclude_role)
        if not candidates:
            return
        spec = self._setup_spec(typed_kind)
        if spec is None:
            return
        setter = getattr(self.pool, spec.get("setter") or "", None)
        if setter is None:
            return
        try:
            setter(_random.choice(candidates))
        except ValueError:
            # Defensive — the candidate list satisfies the type check
            # by construction, but a stale pool race could drop it.
            pass

    def _autofill_token_slot(self, kind: str) -> None:
        """Re-run the autofill for ``kind``'s pool slot via the registry."""
        spec = self._setup_spec(kind)
        if spec is None or not spec.get("autofill"):
            return
        fn = getattr(self.pool, spec["autofill"], None)
        if fn is None:
            return
        with self.pool._lock:
            fn()

    def _role_passes_setup_check(self, name: str, check_decl) -> bool:
        """Apply a setup-pick ``check`` declaration to a role name.

        Accepts the small enumerated forms used in ``setup_picks``:
          * ``None`` — any role passes (used by WRONG slots).
          * ``("char_type", "TOWNSFOLK"|"OUTSIDER"|"MINION"|"GOOD")``
            — uses the existing Check abstraction so misregistering
            roles (Spy, Recluse) participate correctly.
          * ``"true_townsfolk"`` — strict-true Townsfolk (no Spy).
        """
        if check_decl is None:
            return True
        if check_decl == "true_townsfolk":
            return self._true_townsfolk(name)
        if (
            isinstance(check_decl, tuple)
            and len(check_decl) == 2
            and check_decl[0] == "char_type"
        ):
            ct = check_decl[1]
            if ct == "TOWNSFOLK":
                return self._townsfolk_in_play(name)
            if ct == "OUTSIDER":
                return self._outsider_in_play(name)
            if ct == "MINION":
                return self._minion_in_play(name)
            if ct == "GOOD":
                return self._good_in_play(name)
        return False

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

        # Fresh night → start over from step 0. (``back()`` later
        # restores a snapshot whose ``_completed_step_index`` points
        # at whichever step it was on.)
        self._completed_step_index = 0
        self._abort_requested = False

        self.log(f"Night {self._night_number} begins.")
        self._console_log(
            "phase",
            (
                "First night begins"
                if self._phase is Phase.FIRST_NIGHT
                else f"Night {self._night_number} begins"
            ),
            phase=self._phase.value,
            night_number=self._night_number,
        )

        # Initial checkpoint: state at night-start, before any ability
        # has run. Pressing Back during the very first ability of the
        # night restores this checkpoint.
        self._save_history_checkpoint(
            f"start of night {self._night_number}"
        )

        self._launch_night_thread(resume=False)

    def _run_night(self, resume: bool = False) -> None:
        try:
            # Per project rule, a night that follows a triggered win
            # runs **no abilities** — but the storyteller still sees
            # the start-of-night and end-of-night announcements
            # (Dusk / Dawn preset steps) so the night has its normal
            # rhythm. We skip the setup-action pass and the
            # NIGHT_START / NIGHT_END event dispatch (those drive
            # character reactions, which are abilities), and we walk
            # the preset which short-circuits every non-Dusk/Dawn step
            # internally. After the Dawn announcement we fall through
            # to the auto-dawn block which finalizes the pending win.
            pending = self._pending_winner is not None
            if pending:
                self.log(
                    f"Night {self._night_number}: win is pending "
                    f"({self._pending_winner.value}); abilities skipped, "
                    f"Dusk/Dawn announcements still fire."
                )

            if not resume and not pending:
                # Pre-first-night setup actions: each character that
                # overrides Character.setup_ability gets a chance to ask
                # the storyteller a question (Drunk's fake Townsfolk,
                # Fortune Teller's red herring, etc.). Runs once, latched
                # by ``_setup_actions_done`` so a re-entrant start_night
                # doesn't repeat the prompts.
                if self._night_number == 1 and not self._setup_actions_done:
                    self._run_setup_actions()
                    self._setup_actions_done = True

                self._dispatch(Event(EventType.NIGHT_START))

            if self._preset is not None:
                self._run_preset_night(self._night_number)
            else:
                # Legacy path: fall back to Character.night_order if no
                # preset is installed (used by tests that don't set one).
                # The legacy path is not back-aware; checkpoints just
                # mark each character's ability boundary.
                order = self._build_action_order(self._night_number)
                self.log(
                    f"Action order ({self._night_number}): "
                    + ", ".join(
                        f"{c.name}({c.player.name if c.player else '—'})"
                        for c in order
                    )
                )
                # ``_completed_step_index`` doubles as the legacy-path
                # cursor when no preset is installed.
                while self._completed_step_index < len(order):
                    if self._phase is Phase.FINISHED:
                        break
                    # If a win condition tripped during a previous
                    # ability this night, skip the rest of the night
                    # and let the dawn announce the result.
                    if self._pending_winner is not None:
                        self.log(
                            "Win pending mid-night — skipping remaining "
                            "abilities."
                        )
                        break
                    char = order[self._completed_step_index]
                    try:
                        char.ability(self, self._night_number)
                    except Exception as exc:  # pragma: no cover (defensive)
                        self.log(f"Error in {char.name} ability: {exc!r}")
                    self._completed_step_index += 1
                    self._save_history_checkpoint(
                        f"after {char.name} (night "
                        f"{self._night_number})"
                    )

            # NIGHT_END drives reactions (Undertaker bookkeeping,
            # etc.) — those are abilities, so on a pending-win night
            # we don't dispatch it.
            if not pending:
                self._dispatch(Event(EventType.NIGHT_END))

            if self._auto_advance_to_day and self._phase.is_night:
                # Drop into day automatically. The engine.advance_to_day
                # path expects to be called from the UI thread; we're
                # already on the night thread, so bypass the
                # join-on-self by inlining the state transition.
                self._auto_dawn()
        except _AbortAbility:
            # The Storyteller hit Back while a character ability was
            # waiting on a prompt. We swallow the exception here and
            # bail out of the night thread without advancing; the
            # ``back()`` call that triggered the abort is responsible
            # for restoring state and re-launching the thread.
            self.log("Night aborted (Back button).")
        finally:
            with self._lock:
                self._pending_prompt = None

    # ------------------------------------------------------------------
    # Preset-driven night order.
    # ------------------------------------------------------------------

    # System-step handlers: preset steps that aren't a single
    # character's ability. Each entry maps the step name to a
    # ``(method_attr, history_label)`` pair. The dispatch itself lives
    # in :meth:`_run_preset_night`. Extending the engine for a new
    # system-driven step (e.g. a ritual at start-of-night) is purely
    # additive — drop a new method on the engine and add an entry
    # here.
    _SYSTEM_STEP_HANDLERS: Dict[str, Tuple[str, str]] = {
        preset_module.MINION_INFO: ("_run_minion_info_step", "Minion Info"),
        preset_module.DEMON_INFO:  ("_run_demon_info_step",  "Demon Info"),
        "Scarlet Woman":           ("_run_scarlet_woman_demon_reveal_step",
                                    "Scarlet Woman"),
    }

    def _run_minion_info_step(
        self, step: "preset_module.NightStep", night_number: int
    ) -> None:
        self._run_minion_info(step)

    def _run_demon_info_step(
        self, step: "preset_module.NightStep", night_number: int
    ) -> None:
        self._run_demon_info(step)

    def _run_scarlet_woman_demon_reveal_step(
        self, step: "preset_module.NightStep", night_number: int
    ) -> None:
        """Drive the night-time "YOU ARE the <Demon>" reveal queue.

        The Scarlet Woman class' reaction queues a freshly promoted
        seat onto ``engine._sw_pending_demon_reveal`` when it inherits
        the Demon. The preset places a "Scarlet Woman" step at the
        right night-order slot for the reveal; this handler drains
        the queue if any reveal is pending. (Once the SW reaction has
        fired, the seat's ``character`` is now an Imp instance, so
        the generic ``in_play.get(step.name)`` path would silently
        skip — hence the dedicated step handler.)
        """
        if self._sw_pending_demon_reveal:
            self._announce_step(step)
            self._run_scarlet_woman_step(step)

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
            # Prefer alive instances over dead ones when two seated
            # players share a character name. Canonical example: after
            # a Scarlet Woman promotion, the (dead) original Demon and
            # the freshly-promoted Scarlet Woman both have
            # ``character.name`` equal to the demon class. Without this
            # guard the dead instance can win the in_play slot, and
            # ``would_act_tonight`` then short-circuits on the demon
            # step because ``self.player.dead`` is True.
            existing = in_play.get(p.character.name)
            if (
                existing is not None
                and existing.player is not None
                and not existing.player.dead
            ):
                # Already have an alive instance — don't overwrite with
                # a (possibly dead) duplicate.
                pass
            else:
                in_play[p.character.name] = p.character
            perceived = p.character.acting_perceived_character()
            if perceived is not None:
                # Don't shadow a real seated holder of that role: only
                # register the perceived role if no one is genuinely
                # playing it. (A Drunk's perceived role is normally
                # picked from "Townsfolk not in play" so this is a
                # defensive no-op in the canonical case.)
                in_play.setdefault(perceived.name, perceived)

        # Resume support: ``self._completed_step_index`` is 0 on a
        # fresh night and >0 after a Back-button restore. We iterate
        # by index (not by step value) so the index advances atomically
        # alongside the step it represents — this is what makes the
        # post-step checkpoint reflect "step N is done".
        while self._completed_step_index < len(steps):
            if self._phase is Phase.FINISHED:
                break
            step = steps[self._completed_step_index]

            # Dusk / Dawn announcements are ALWAYS run, including on a
            # pending-win night — the storyteller still sees "Dusk"
            # and "Dawn" so the night has its normal rhythm.
            if step.name in (preset_module.DUSK, preset_module.DAWN):
                self._announce_step(step)
                self._completed_step_index += 1
                self._save_history_checkpoint(
                    f"after {step.name} (night {night_number})"
                )
                continue

            # Project rule: when a win is pending, every NON-Dusk/Dawn
            # step is silently skipped — minion info, demon info, and
            # every character ability stay quiet. We don't break out
            # of the loop because Dawn (the closing announcement)
            # might still be ahead of us in the sheet.
            if self._pending_winner is not None:
                self._completed_step_index += 1
                continue

            # System-step registry — engine-driven steps that aren't a
            # single character's ability (Minion Info, Demon Info, the
            # Scarlet Woman demon-role reveal, …). Each step name is
            # mapped to a ``(handler, label)`` pair via
            # ``_SYSTEM_STEP_HANDLERS``. Adding a new system step is a
            # one-line registry entry plus a method on the engine.
            handler_entry = self._SYSTEM_STEP_HANDLERS.get(step.name)
            if handler_entry is not None:
                handler_attr, label = handler_entry
                handler = getattr(self, handler_attr)
                handler(step, night_number)
                self._completed_step_index += 1
                self._save_history_checkpoint(
                    f"after {label} (night {night_number})"
                )
                continue

            char = in_play.get(step.name)
            if char is None:
                # That character isn't in this game — skip silently.
                # Still take a checkpoint so the Back button has a
                # consistent "after each step" history.
                self._completed_step_index += 1
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
                self._completed_step_index += 1
                continue
            self._announce_step(step, character=char)
            try:
                char.ability(self, night_number)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(f"Error in {char.name} ability: {exc!r}")
            self._completed_step_index += 1
            # Per the project rule: after each ability, save the game
            # state. The checkpoint is what the Back button restores.
            self._save_history_checkpoint(
                f"after {char.name} (night {night_number})"
            )

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
            self._console_log(
                "ability",
                f"{character.name} ({character.player.name}) — {step.name}",
                character=character.name,
                target_player_id=character.player.id,
                target_player_name=character.player.name,
                step_name=step.name,
                step_description=step.description,
            )
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
        """Show the evil Minions who their Demon is. Only fires in
        games of 7+ players (per the rule).

        Project rule: all Minions are presumed to wake up at the same
        time, so the engine emits a single consolidated prompt that
        wakes every Minion together and shows them only the
        ``THIS IS THE DEMON`` token. (The Minions are awake in the
        same room and can already see each other, so the
        ``THESE ARE YOUR MINIONS`` token is redundant.)
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
        # ``THIS IS THE DEMON`` is the only token shown, so only the
        # Demon's chair is highlighted on the board. The Minions that
        # are being woken are conveyed via ``target_player_name`` (and
        # rendered as the wake-up line in the storyteller UI).
        demon_char_names = sorted({
            p.character.name for p in demons if p.character is not None
        })
        text = (
            f"Wake {minion_names} (Minions). Show: the Demon is {demon_names}."
        )
        self.send_prompt(InformationPrompt(
            text=text,
            target_player_id=None,
            shown_to_player=True,
            highlight_player_ids=[p.id for p in demons],
            highlight_characters=demon_char_names,
            meta={
                "step_kind": "minion_info",
                "step_name": step.name,
                "description": step.description,
                # ``character`` and ``target_player_name`` let the
                # storyteller UI synthesize the standard
                # "Wake up <Role> (<Player>)" line above this
                # info — the same 6-section layout used for ordinary
                # ability prompts. For the consolidated minion-info
                # prompt the "Role" is the plural ``Minions`` and the
                # "Player" slot lists every Minion's name.
                "character": "Minions",
                "target_player_name": minion_names,
                "stage": "info",
                "demon_player_names": [p.name for p in demons],
                "minion_player_names": [p.name for p in minions],
                "minion_player_ids": [p.id for p in minions],
            },
        ))

    def _run_demon_info(self, step: "preset_module.NightStep") -> None:
        """Show the Demon their Minions and 3 not-in-play good roles to
        bluff as. Only fires in games of 7+ players.

        Prompt flow (matches the standard 6-section panel layout used
        by every other character ability):

          1. Title / description: synthesized from the preset step.
          2. **ST input stage 1** — a ``SelectCharacterPrompt`` with
             ``count=3`` and ``stage="st_pre"``. The engine pre-picks
             3 random good (Townsfolk/Outsider) characters that are
             not in play and surfaces them as the default. The
             Storyteller may swap any of the picks before clicking
             Next; the picks land before the Demon physically wakes.
          3. **Wake up Demon (player)** — synthesized by the UI from
             ``meta.character`` / ``meta.target_player_name``.
             Internally we also dispatch ``EventType.WAKEUP`` so other
             abilities and any audit tooling see a real wakeup event.
          4. **Show this to player** — an ``InformationPrompt`` with
             ``stage="info"`` carrying the (possibly Storyteller-edited)
             bluffs plus the Demon's Minion list.
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

        # Pre-pick three good (Townsfolk/Outsider) characters that are
        # NOT in play, as the default bluff set the Storyteller will
        # confirm in ST input stage 1. The pool is the full script
        # minus everyone seated; if the script is unusually thin we
        # fall back to whatever is left.
        in_play_names = {
            p.character.name for p in self._players if p.character is not None
        }
        all_good_names = (
            script_data.names_by_type(CharType.TOWNSFOLK)
            + script_data.names_by_type(CharType.OUTSIDER)
        )
        bluff_pool = [n for n in all_good_names if n not in in_play_names]
        import random as _rand
        eligible_bluffs = sorted(set(bluff_pool))
        n_bluffs = min(3, len(eligible_bluffs))
        default_bluffs: List[str] = (
            _rand.sample(eligible_bluffs, n_bluffs) if n_bluffs else []
        )

        minion_names = ", ".join(p.name for p in minions) or "(none)"
        for demon in demons:
            # ----- ST input stage 1: confirm / change the 3 bluffs ----
            # Even though the engine already randomized the picks, we
            # surface them to the ST so they can swap any character
            # they don't like (e.g. one that conflicts with the table's
            # mood, or that a clever player would never bluff).
            bluff_prompt = SelectCharacterPrompt(
                text="THESE CHARACTERS ARE NOT IN PLAY",
                eligible_characters=eligible_bluffs,
                count=n_bluffs if n_bluffs > 0 else 0,
                target_player_id=demon.id,
                meta={
                    "character": "Demon",
                    "target_player_name": demon.name,
                    "step": "select_bluffs",
                    "stage": "st_pre",
                    "step_kind": "demon_info",
                    "step_name": step.name,
                    "description": step.description,
                    "default": list(default_bluffs),
                },
            )
            chosen_bluffs: List[str]
            if n_bluffs == 0:
                # Nothing for the ST to pick — skip the prompt entirely.
                chosen_bluffs = []
            else:
                resp = self.send_prompt(bluff_prompt)
                if isinstance(resp, list):
                    chosen_bluffs = [str(x) for x in resp if x]
                elif isinstance(resp, str) and resp:
                    chosen_bluffs = [resp]
                else:
                    chosen_bluffs = list(default_bluffs)
                # Defensive: if the ST somehow returned fewer bluffs
                # than expected, top up from the random default so the
                # Demon still sees three roles when possible.
                if len(chosen_bluffs) < n_bluffs:
                    for d in default_bluffs:
                        if d not in chosen_bluffs:
                            chosen_bluffs.append(d)
                            if len(chosen_bluffs) >= n_bluffs:
                                break

            # ----- WAKEUP — picks are locked in; wake the Demon ----
            # Engine-internal event so other abilities / audit tools
            # see a real wakeup. The UI synthesizes the visible
            # "Wake up Demon (player)" line from the prompt meta.
            self._dispatch(
                Event(EventType.WAKEUP, source=None, targets=[demon])
            )

            # ----- Show this to player (auto info; ST clicks Next) ----
            text = (
                f"Wake {demon.name} (Demon). Your Minions: {minion_names}. "
                f"Three not-in-play good roles to bluff as: "
                f"{', '.join(chosen_bluffs) if chosen_bluffs else '(none)'}."
            )
            # The TARGET of demon-info is the Demon's Minions plus the
            # 3 bluff roles. Highlight those chairs/character tokens so
            # the Demon's eye snaps to them; dampen the rest of the board.
            self.send_prompt(InformationPrompt(
                text=text,
                target_player_id=demon.id,
                shown_to_player=True,
                highlight_player_ids=[p.id for p in minions],
                highlight_characters=list(chosen_bluffs),
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
                    "bluff_characters": list(chosen_bluffs),
                },
            ))

    def _run_scarlet_woman_step(self, step: "preset_module.NightStep") -> None:
        """Walk a freshly-promoted Scarlet Woman through the demon reveal.

        The trouble-brewing night sheet's "Scarlet Woman" line tells the
        storyteller to inform the freshly-promoted Scarlet Woman of her
        new demon role. We collapse this into a single prompt: wake the
        player up and show them the "YOU ARE the <Demon>" text in one
        go (no separate YOU ARE token reveal).

        We deliberately do NOT run DEMON INFO here — the rules only call
        for the role reveal (no minion list, no bluffs). Per the project
        rule, no ``confirm`` / ``override`` language: the storyteller
        clicks Next on the reveal.

        Drains ``self._sw_pending_demon_reveal``; the persistent
        ``_sw_promoted_player_ids`` list is left untouched so the
        grimoire reminder ("Scarlet Woman IS THE DEMON") keeps showing
        for the rest of the game.
        """
        if not self._sw_pending_demon_reveal:
            return
        # Drain the queue defensively — pop ids one at a time so a Back
        # button mid-reveal restores the queue alongside the pickled
        # engine snapshot.
        pending = list(self._sw_pending_demon_reveal)
        self._sw_pending_demon_reveal = []
        for pid in pending:
            try:
                player = self.get_player(pid)
            except KeyError:
                continue
            if player.character is None:
                continue
            demon_name = player.character.name
            # WAKEUP — engine-internal event so audit tooling sees a
            # real wakeup; the UI synthesizes the "Wake up <player>"
            # line from the prompt meta.
            self._dispatch(
                Event(EventType.WAKEUP, source=None, targets=[player])
            )
            # Single consolidated reveal — "YOU ARE THE <DEMON>".
            # Uppercase demon name matches the all-caps token style
            # ("YOU ARE THE IMP" / "YOU ARE THE PUKKA" / …) the
            # storyteller shows the freshly-promoted player.
            self.send_prompt(InformationPrompt(
                text=f"YOU ARE THE {demon_name.upper()}.",
                target_player_id=player.id,
                shown_to_player=True,
                highlight_characters=[demon_name],
                meta={
                    "step_kind": "scarlet_woman_reveal",
                    "step_name": step.name,
                    "description": step.description,
                    "character": "Scarlet Woman",
                    "target_player_name": player.name,
                    "stage": "info",
                    "reveal": "demon_role",
                    "demon_character": demon_name,
                },
            ))

    def _auto_dawn(self) -> None:
        """Internal version of advance_to_day for the night-thread.

        ``advance_to_day`` joins the night thread; we can't call that
        from the night thread itself or it'd deadlock. So we replicate
        the state-transition steps without the join.

        Per project rule, this is also where any pending win condition
        is finalized — game-end announcements happen at dawn.
        """
        deaths = list(self._pending_night_deaths)
        self._pending_night_deaths.clear()
        # End-of-night cleanup — see ``advance_to_day``.
        self._demon_killed_player_ids.clear()
        self._phase = Phase.DAY
        self._day_number += 1
        self._executed_today = False
        for p in self._players:
            p.reset_day_flags()
        self.log(
            f"Dawn (auto): day {self._day_number} begins. "
            f"Night deaths: {[p.name for p in deaths]}."
        )
        # Re-run win-condition detection now that we've stepped into
        # day; any newly-tripped condition becomes pending and is
        # finalized below alongside any pending win carried over from
        # the previous day or night.
        self._check_win_conditions()
        if self._pending_winner is not None:
            self._finalize_pending_win()
            return
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
            perceived = p.character.acting_perceived_character()
            if perceived is not None and perceived.acts_on_night(night_number):
                chars.append(perceived)
        chars.sort(key=lambda c: c.night_order(night_number))
        return chars

    def advance_to_day(self) -> List[Player]:
        """End the night, move to day. Returns players who died this night.

        Per project rule, this is also a dawn — a pending win
        condition (set during the day before, at dusk, or mid-night)
        is finalized here and the game ends with the standard
        ``game_end`` announcement.
        """
        if self._phase not in (Phase.FIRST_NIGHT, Phase.NIGHT):
            raise RuntimeError("advance_to_day requires NIGHT phase.")
        # Wait for the night thread to finish, if any.
        if self._night_thread and self._night_thread.is_alive():
            self._night_thread.join(timeout=1.0)

        deaths = list(self._pending_night_deaths)
        self._pending_night_deaths.clear()

        # End-of-night cleanup: the DEAD reminder marker for the
        # Demon's nightly kill is dropped at dawn. Per project rule,
        # this marker exists only for the night the kill landed.
        self._demon_killed_player_ids.clear()

        self._phase = Phase.DAY
        self._day_number += 1
        self._executed_today = False
        for p in self._players:
            p.reset_day_flags()
        self.log(f"Dawn: day {self._day_number} begins. "
                 f"Night deaths: {[p.name for p in deaths]}.")
        death_names = [p.name for p in deaths]
        death_summary = (
            f"Night deaths: {', '.join(death_names)}"
            if death_names else "No deaths overnight"
        )
        self._console_log(
            "phase",
            f"Day {self._day_number} begins — {death_summary}",
            phase=self._phase.value,
            day_number=self._day_number,
            night_deaths=death_names,
        )
        self._check_win_conditions()
        # Dawn announcement: if any win condition is pending (from
        # this dawn's check, or carried over from earlier), finalize
        # it now.
        if self._pending_winner is not None:
            self._finalize_pending_win()
        return deaths

    def advance_to_night(self) -> None:
        if self._phase is not Phase.DAY:
            raise RuntimeError("advance_to_night requires DAY phase.")
        # Dusk — fire DAY_END and run the dusk win check (Mayor's
        # 3-alive-no-execution condition activates here).
        #
        # Per project rule, a triggered win condition no longer ends
        # the game immediately — it's parked as a pending win and the
        # phase still advances to NIGHT. The night thread will run no
        # actions (see :meth:`_run_night`) and the next dawn will
        # finalize the win.
        self._dispatch(Event(EventType.DAY_END))
        self._check_win_conditions(at_dusk=True)
        # Defensive: storyteller-driven _end_game could already have
        # marked the game finished. Don't try to advance past it.
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
        source: Optional["Character"] = None,
    ) -> Player:
        """Kill ``player_id`` with the given cause.

        ``source`` is the :class:`Character` whose ability initiated
        this kill (e.g. the Imp passes ``source=self`` from its
        nightly demon kill). The source flows through the dispatched
        ``PRE_DEATH`` and ``DEATH`` events so reactions can attribute
        the kill — most importantly, redirect abilities like the
        Mayor's preserve the original source when re-entering
        :meth:`kill`, so a kill the Imp originated and the Mayor
        bounces back to the Imp still reads as a self-attributed
        demon kill at the dispatch level (without any
        character-specific knowledge in either ability). Default is
        ``None`` (engine/Storyteller-attributed).
        """
        player = self.get_player(player_id)
        if player.dead:
            return player

        # Demon-kill protection (Monk only here; the Soldier's
        # protection rule lives on the Soldier class as a PRE_DEATH
        # reaction — see :mod:`engine.characters.soldier`).
        if cause is DeathCause.DEMON_KILL:
            if player.protected_from_demon:
                # Monk-style protection: someone is shielding ``player``
                # from the demon for this night. Log the reaction with
                # the protector's role so the report is unambiguous;
                # ``protected_from_demon`` is set by the Monk's ability
                # path, so attribute it to the Monk here.
                self.log_reaction(
                    "Monk",
                    f"{player.name} is protected from the Demon — no death.",
                    target=player,
                    trigger="demon_kill",
                )
                return player

        # PRE_DEATH hook — fires after protection checks but BEFORE the
        # death actually lands. A reaction may cancel the kill by
        # setting ``event.data["cancelled"] = True`` (Mayor's night
        # redirect uses this so the Mayor never transiently appears
        # dead). If cancelled, the death never lands: no
        # ``_pending_night_deaths`` entry, no DEMON_KILL marker, no
        # ``DEATH`` event, no ``_check_win_conditions``. The
        # reaction is responsible for any replacement effect (e.g.
        # killing the redirected target via a re-entrant
        # ``Engine.kill``).
        pre_event = Event(
            EventType.PRE_DEATH,
            source=source,
            targets=[player],
            data={"cause": cause, "cancelled": False},
        )
        self._dispatch(pre_event)
        if pre_event.data.get("cancelled"):
            return player

        player.kill(cause)
        self.log(f"{player.name!r} dies ({cause.value}).")

        if self._phase.is_night and cause is not DeathCause.EXECUTION:
            self._pending_night_deaths.append(player)

        # Demon kill lands → place the DEAD reminder marker on the
        # seat. Cleared at the end of the night by ``advance_to_day``
        # / ``_auto_dawn``.
        if cause is DeathCause.DEMON_KILL:
            if player.id not in self._demon_killed_player_ids:
                self._demon_killed_player_ids.append(player.id)

        self._dispatch(
            Event(
                EventType.DEATH,
                source=source,
                targets=[player],
                data={"cause": cause},
            )
        )
        # Drain deferred post-DEATH callbacks. Reactions that need to
        # observe the *settled* state after every other reaction has
        # fired (e.g. Imp self-kill, which must let the Scarlet
        # Woman's "Demon dies → you become the Demon" reaction take
        # effect first before deciding whether to prompt the ST for a
        # replacement Minion) queue here during dispatch and run now.
        if self._post_death_callbacks:
            callbacks = list(self._post_death_callbacks)
            self._post_death_callbacks.clear()
            for cb in callbacks:
                try:
                    cb()
                except Exception as exc:  # pragma: no cover (defensive)
                    self.log(f"Post-DEATH callback crashed: {exc!r}")
        char_name = player.character.name if player.character else None
        self._console_log(
            "kill",
            f"{player.name} dies ({cause.value})",
            player_id=player.id,
            player_name=player.name,
            character=char_name,
            cause=cause.value,
        )
        self._check_win_conditions()
        return player

    def revive(self, player_id: int) -> Player:
        player = self.get_player(player_id)
        player.revive()
        # Revived seats lose any in-flight demon-kill marker — they
        # are alive again and the DEAD reminder no longer applies.
        if player.id in self._demon_killed_player_ids:
            self._demon_killed_player_ids.remove(player.id)
        self.log(f"{player.name!r} is revived.")
        self._dispatch(Event(EventType.REVIVE, targets=[player]))
        char_name = player.character.name if player.character else None
        self._console_log(
            "revive",
            f"{player.name} is revived",
            player_id=player.id,
            player_name=player.name,
            character=char_name,
        )
        return player

    def poison(self, player_id: int) -> None:
        player = self.get_player(player_id)
        player.set_poisoned(True)
        self.log(f"{player.name!r} is poisoned.")
        self._dispatch(Event(EventType.POISON, targets=[player]))
        self._console_log(
            "state",
            f"{player.name} is poisoned",
            player_id=player.id, player_name=player.name, change="poison_on",
        )

    def cure_poison(self, player_id: int) -> None:
        player = self.get_player(player_id)
        player.set_poisoned(False)
        self.log(f"{player.name!r} is no longer poisoned.")
        self._console_log(
            "state",
            f"{player.name} is no longer poisoned",
            player_id=player.id, player_name=player.name, change="poison_off",
        )

    def make_drunk(self, player_id: int) -> None:
        player = self.get_player(player_id)
        player.set_drunk(True)
        self.log(f"{player.name!r} is drunk.")
        self._dispatch(Event(EventType.DRUNK, targets=[player]))
        self._console_log(
            "state",
            f"{player.name} is drunk",
            player_id=player.id, player_name=player.name, change="drunk_on",
        )

    def sober_up(self, player_id: int) -> None:
        player = self.get_player(player_id)
        player.set_drunk(False)
        self._console_log(
            "state",
            f"{player.name} is no longer drunk",
            player_id=player.id, player_name=player.name, change="drunk_off",
        )

    def change_character(self, player_id: int, character_name: str) -> None:
        """Swap a player's character class mid-game.

        The new character is a *fresh instantiation*: every per-character
        flag (Slayer's spent-shot, Virgin's triggered nominator-execute,
        Butler's master, Fortune Teller's red-herring resolution, Mayor's
        redirect-in-flight, etc.) starts at its class default because
        ``script_data.build_character(name)`` calls ``cls()`` with no
        carry-over. On the Player side,
        :meth:`Player.change_character` resets the per-role flags
        (``once_per_game_used``, ``mad_about``, ``protected_from_demon``)
        so once-per-game and first-night abilities are available to
        the new role. Identity (alignment, alive, drunk, poisoned,
        per-day flags) belongs to the seat and is preserved.

        The chair store is also updated so ``chair.character`` tracks
        the engine truth — there is exactly one place that records
        "what role is this seat playing right now". Keeping
        ``chair.character == player.character.name`` avoids the "two
        copies of the player's role" problem the UI used to have
        (engine sees Imp, chair shows Scarlet Woman).

        Status reminders that depend on the seat's *original* role
        (e.g. "Scarlet Woman IS THE DEMON" — the SW promoted, so
        chair.character is now "Imp") use a separate per-seat list
        on the engine (``_sw_promoted_player_ids``); UI rendering
        keys off that, not chair.character.
        """
        player = self.get_player(player_id)
        # ``build_character`` returns a freshly-constructed instance —
        # the source of "all conditions reset" for the new role.
        char = script_data.build_character(character_name)
        player.change_character(char)
        # Mirror the change onto the chair store so chair.character is
        # the single source of truth visible to the UI.
        for chair in self.chairs.list():
            if chair.get("player_id") == player_id:
                self.chairs.update(chair["id"], character=character_name)
                break
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
        # EXECUTION is the execution-specific signal (Saint, Undertaker,
        # Mayor's no-execution latch). It fires first so an executed
        # Saint can register a pending evil win before the broader
        # DEATH reactions run.
        self._dispatch(
            Event(EventType.EXECUTION, targets=[player],
                  data={"cause": DeathCause.EXECUTION})
        )
        # An execution IS a death, so the broader DEATH event must
        # also fire — otherwise reactions that listen for DEATH
        # (notably the Scarlet Woman's Demon takeover, which only
        # promotes when the Demon dies) silently miss executed-Demon
        # kills, and a 5+-alive table that executes the Demon would
        # incorrectly end the game with good winning. Dispatched after
        # EXECUTION so per-event ordering matches engine.kill (kill →
        # dispatch DEATH) and so any pending win Saint already
        # registered isn't reordered.
        self._dispatch(
            Event(EventType.DEATH, targets=[player],
                  data={"cause": DeathCause.EXECUTION})
        )
        char_name = player.character.name if player.character else None
        self._console_log(
            "execution",
            f"{player.name} is executed",
            player_id=player.id,
            player_name=player.name,
            character=char_name,
        )
        self._console_log(
            "kill",
            f"{player.name} dies (execution)",
            player_id=player.id,
            player_name=player.name,
            character=char_name,
            cause=DeathCause.EXECUTION.value,
        )
        self._check_win_conditions()
        return player

    # ------------------------------------------------------------------
    # Daytime player actions (nominate / vote / use ability).
    #
    # These are user-facing helpers exposed by the side-panel UI that
    # opens when the Storyteller clicks a player circle during the day.
    # Each one updates per-player state, dispatches the appropriate
    # event so character reactions fire (e.g. Virgin on NOMINATION),
    # and runs end-game checks where relevant.
    # ------------------------------------------------------------------

    def nominate(self, nominator_id: int, nominee_id: int) -> Tuple[Player, Player]:
        """Record a nomination from ``nominator_id`` against ``nominee_id``.

        Sets ``has_nominated_today`` on the nominator and
        ``has_been_nominated_today`` on the nominee, then dispatches a
        ``NOMINATION`` event so role reactions fire (e.g. the Virgin
        executing the nominator). Dead players cannot nominate; dead
        players *can* be nominated (the rulebook allows nominations
        against the dead, even though they cannot be re-killed).
        """
        if self._phase is not Phase.DAY:
            raise RuntimeError("Nominations only happen during day.")
        nominator = self.get_player(nominator_id)
        nominee = self.get_player(nominee_id)
        if not nominator.alive:
            raise RuntimeError(
                f"{nominator.name!r} is dead; the dead cannot nominate."
            )
        if nominator.has_nominated_today:
            raise RuntimeError(
                f"{nominator.name!r} has already nominated today."
            )
        if nominee.has_been_nominated_today:
            raise RuntimeError(
                f"{nominee.name!r} has already been nominated today."
            )
        nominator.has_nominated_today = True
        nominee.has_been_nominated_today = True
        self.log(f"{nominator.name!r} nominates {nominee.name!r}.")
        self._dispatch(
            Event(
                EventType.NOMINATION,
                targets=[nominee],
                data={
                    "nominator_id": nominator.id,
                    "nominee_id": nominee.id,
                },
            )
        )
        self._console_log(
            "nomination",
            f"{nominator.name} nominates {nominee.name}",
            nominator_id=nominator.id,
            nominator_name=nominator.name,
            nominee_id=nominee.id,
            nominee_name=nominee.name,
        )
        # A reaction (e.g. Virgin) may have ended the game.
        self._check_win_conditions()
        return nominator, nominee

    def record_vote(self, player_id: int) -> Player:
        """Record a vote by ``player_id``.

        For a living player this is purely informational (logged so the
        end-of-game narration can replay the day). For a dead player it
        consumes their single dead-vote token. Calling on a dead player
        with no dead vote left raises — the side panel disables the
        button in that case so this should not normally happen.
        """
        if self._phase is not Phase.DAY:
            raise RuntimeError("Votes only happen during day.")
        player = self.get_player(player_id)
        if player.alive:
            self.log(f"{player.name!r} votes.")
            return player
        if not player.has_dead_vote:
            raise RuntimeError(
                f"{player.name!r} is dead and has no dead vote left."
            )
        player.use_dead_vote()
        self.log(f"{player.name!r} (dead) spends their dead vote.")
        return player

    def use_daytime_ability(self, player_id: int) -> None:
        """Trigger the player's character daytime ability.

        Character implementations of ``daytime_ability`` typically call
        ``send_prompt`` to ask the Storyteller for a target. Since
        ``send_prompt`` blocks waiting for an HTTP response on
        ``/api/engine/respond``, we must run the ability on a worker
        thread — calling it directly on the HTTP thread would deadlock.
        We refuse to start a new ability while another worker thread
        (e.g. the night loop) is still alive.
        """
        if self._phase is not Phase.DAY:
            raise RuntimeError("Daytime abilities only fire during day.")
        player = self.get_player(player_id)
        if player.character is None:
            raise RuntimeError(f"{player.name!r} has no character.")
        if not player.alive:
            raise RuntimeError(f"{player.name!r} is dead.")
        if self._night_thread and self._night_thread.is_alive():
            raise RuntimeError(
                "An ability worker is already running; wait for it to finish."
            )
        char = player.character
        self._console_log(
            "ability",
            f"{char.name} ({player.name}) — daytime ability",
            character=char.name,
            target_player_id=player.id,
            target_player_name=player.name,
            ability_kind="daytime",
        )
        thread = threading.Thread(
            target=self._run_daytime_ability,
            args=(char,),
            name=f"botc-daytime-{char.name}",
            daemon=True,
        )
        # Reuse the night-thread slot so subsequent calls (and the
        # next start_night) wait for this ability to complete.
        self._night_thread = thread
        thread.start()

    def _run_daytime_ability(self, char: Character) -> None:
        try:
            char.daytime_ability(self)
        except Exception as exc:  # pragma: no cover (defensive)
            self.log(f"Error in {char.name} daytime_ability: {exc!r}")
        finally:
            with self._lock:
                self._pending_prompt = None

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
        # An ability that pre-emptively checks the abort flag (e.g. the
        # Storyteller hit Back twice in quick succession) shouldn't end
        # up emitting another prompt to the UI; bail early.
        if self._abort_requested:
            raise _AbortAbility()
        auto = self._auto_resolve(prompt)
        if auto is not _NO_AUTO_RESOLVE:
            self.log(
                f"Auto-resolved {type(prompt).__name__} "
                f"(single eligible option): {auto!r}."
            )
            self._record_prompt_response(prompt, auto)
            return auto
        with self._lock:
            self._pending_prompt = prompt
            self._prompt_response = None
            self._response_ready.clear()
        self.log(f"Prompt: {prompt.text}")
        self._response_ready.wait()
        # If ``back()`` woke us up to abort, the response slot is still
        # ``None`` (we didn't get a real Storyteller answer). Raise
        # ``_AbortAbility`` so the character ability unwinds without
        # treating the missing response as a legitimate pick.
        if self._abort_requested:
            with self._lock:
                self._pending_prompt = None
                self._prompt_response = None
            raise _AbortAbility()
        with self._lock:
            response = self._prompt_response
            self._prompt_response = None
            self._pending_prompt = None
        self._record_prompt_response(prompt, response)
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
            count = getattr(prompt, "count", 1) or 1
            eligible = list(prompt.eligible_characters)
            if count == 1 and len(eligible) == 1:
                return eligible[0]
            if count > 1 and len(eligible) == count:
                # Forced multi-selection: every eligible character must
                # be picked — there is no other valid response.
                return list(eligible)
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
    #
    # Project rule: *Game should only end after a night*. When a win
    # condition is satisfied at any time other than a dawn transition
    # we record it on ``_pending_winner`` / ``_pending_win_reason`` and
    # let play continue:
    #
    #   * Win triggered DURING DAY — players keep using abilities and
    #     nominating; advance_to_night still works; the night that
    #     follows runs no actions; dawn finalizes the win.
    #   * Win triggered DURING NIGHT — remaining night abilities are
    #     skipped (the night thread fast-forwards to dawn); dawn
    #     finalizes the win.
    #   * Win triggered AT DUSK (Mayor) — recorded; advance_to_night
    #     still moves into NIGHT, that night is a no-op, dawn
    #     finalizes.
    #
    # The first win condition to trigger wins; subsequent triggers are
    # ignored so a chain reaction (e.g. Saint executed *and* the Demon
    # already dead) doesn't overwrite the original cause.

    def _check_win_conditions(self, at_dusk: bool = False) -> None:
        """Walk the win-condition registry; register the first triggered.

        The registry is composed of two layers:

          1. **Builtin** — script-agnostic conditions every game has:
             "all Demons dead → good wins", "two players left → evil
             wins". Lives on the engine so empty-roster tests still
             produce a sensible end state.
          2. **Per-character** — each seated character (and each
             impersonated perceived character) is asked, via
             :meth:`Character.check_win_condition`, whether *its*
             condition fires now. Adding a new role with a new win
             rule (Mayor today; Atheist, Engineer-redirect, …
             tomorrow) needs no engine edit.

        First one to fire wins; the rest are skipped. Saint's "evil
        wins on execution" stays a reaction-based pending-win because
        it has to fire *during* the execution event, before this
        check runs.
        """
        if self._phase is Phase.FINISHED:
            return
        if self._pending_winner is not None:
            return

        # 1) Builtin checks.
        result = self._check_builtin_win_conditions()
        if result is not None:
            winner, reason = result
            self._register_pending_win(winner, reason)
            return

        # 2) Per-character contributions.
        for p in self._players:
            char = getattr(p, "character", None)
            if char is None:
                continue
            try:
                contrib = char.check_win_condition(self, at_dusk=at_dusk)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(
                    f"check_win_condition crashed in "
                    f"{type(char).__name__}: {exc!r}"
                )
                contrib = None
            if contrib is not None:
                winner, reason = contrib
                self._register_pending_win(winner, reason)
                return
            perceived = char.acting_perceived_character()
            if perceived is None:
                continue
            try:
                contrib = perceived.check_win_condition(self, at_dusk=at_dusk)
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(
                    f"check_win_condition crashed in perceived "
                    f"{type(perceived).__name__}: {exc!r}"
                )
                contrib = None
            if contrib is not None:
                winner, reason = contrib
                self._register_pending_win(winner, reason)
                return

    def _check_builtin_win_conditions(
        self,
    ) -> "Optional[Tuple[Alignment, str]]":
        """Script-agnostic win checks: demon-dead, two-alive."""
        alive = self.alive_players
        alive_demons = [
            p for p in alive if p.char_type is CharType.DEMON
        ]
        if not alive_demons:
            return Alignment.GOOD, "The Demon is dead."
        counted = [
            p for p in alive
            if p.char_type not in (CharType.TRAVELER, CharType.FABLED)
        ]
        if len(counted) <= 2:
            return Alignment.EVIL, "Only two players remain."
        return None

    def _register_pending_win(self, winner: Alignment, reason: str) -> None:
        """Record a triggered win without ending the game yet.

        The actual phase flip and ``game_end`` console event are
        deferred to :meth:`_finalize_pending_win`, which is called by
        the dawn paths (:meth:`advance_to_day` and :meth:`_auto_dawn`).
        Per project rule, this lets the day finish out normally and
        makes the next night a no-op.
        """
        if self._phase is Phase.FINISHED:
            return
        if self._pending_winner is not None:
            return
        self._pending_winner = winner
        self._pending_win_reason = reason
        self.log(
            f"Win pending: {winner.value} — {reason}. "
            f"Will be announced at dawn."
        )
        self._console_log(
            "win_pending",
            f"{winner.value.capitalize()} win pending — {reason} "
            f"(announced at dawn)",
            winner=winner.value,
            reason=reason,
        )

    def _finalize_pending_win(self) -> None:
        """Promote the pending win to a real game-over.

        Called from the dawn transition. Idempotent — if there is no
        pending win or the game already finished, this is a no-op.
        """
        if self._phase is Phase.FINISHED:
            return
        if self._pending_winner is None:
            return
        winner = self._pending_winner
        reason = self._pending_win_reason or ""
        self._phase = Phase.FINISHED
        self._winner = winner
        self._win_reason = reason
        # Clear the pending slots so a snapshot taken after end-of-game
        # only shows the final winner, not the (now-stale) pending one.
        self._pending_winner = None
        self._pending_win_reason = None
        self.log(f"Game over: {winner.value} wins — {reason}")
        self._console_log(
            "game_end",
            f"{winner.value.capitalize()} wins — {reason}",
            winner=winner.value,
            reason=reason,
        )

    def _end_game(self, winner: Alignment, reason: str) -> None:
        """Storyteller-driven end-of-game.

        The ``/api/engine/end_game`` route calls this, as does any
        external caller that wants to override the dawn-deferred
        behavior. Internal win-condition detection should call
        :meth:`_register_pending_win` instead so the project rule
        (announcements only at dawn) is respected.
        """
        if self._phase is Phase.FINISHED:
            return
        # Route through the pending machinery so the bookkeeping is in
        # one place; immediately finalize since the storyteller asked
        # for an explicit end.
        self._pending_winner = winner
        self._pending_win_reason = reason
        self._finalize_pending_win()

    # ==================================================================
    #                  SAVE / LOAD / BACK-BUTTON HISTORY
    # ==================================================================
    #
    # The engine state is serializable to a string and reloadable. The
    # primary use case is the Back button: after every ability the
    # engine takes a snapshot and pushes it on ``self._history``;
    # pressing Back pops the most recent snapshot and restores it,
    # which (during a night) interrupts the running ability and
    # re-enters the preset loop at the same step so the Storyteller
    # can redo any selections in that ability.
    #
    # Threading state (the prompt lock, response event, and night
    # thread handle) is *not* part of a saved state — it's reconstructed
    # fresh on restore, since resuming requires a brand new thread.
    # ------------------------------------------------------------------

    # Attributes that don't survive a save/load round-trip. They are
    # transient brokerage state, not part of the game's logical state,
    # and we recreate them in :meth:`__setstate__`.
    _NON_PERSISTED_ATTRS: Tuple[str, ...] = (
        "_lock",
        "_response_ready",
        "_night_thread",
        "_pending_prompt",
        "_prompt_response",
        "_current_step_meta",
        "_abort_requested",
    )

    def __getstate__(self) -> Dict[str, Any]:
        """Drop non-picklable / transient attributes for serialization.

        The history list is also dropped — otherwise each new history
        entry would recursively contain every prior entry, blowing up
        in size after a few abilities.
        """
        state = self.__dict__.copy()
        for k in self._NON_PERSISTED_ATTRS:
            state.pop(k, None)
        # Don't recursively serialize the history list inside each
        # snapshot; the history is engine-instance-local and rebuilt
        # on the live engine, not part of the logical game state.
        state["_history"] = []
        state["_history_labels"] = []
        # The preset object is rebuilt by name on restore; saving it
        # keeps the snapshot self-contained but the on-disk preset
        # files are the authoritative source.
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        """Rehydrate from :meth:`__getstate__` and rebuild thread state.

        Resets all prompt-broker plumbing to a clean baseline so a
        :meth:`back` call (which restores then restarts the night
        thread) starts from a known state.
        """
        self.__dict__.update(state)
        self._lock = threading.Lock()
        self._response_ready = threading.Event()
        self._night_thread = None
        self._pending_prompt = None
        self._prompt_response = None
        self._current_step_meta = None
        self._abort_requested = False
        # Backwards-compat: older snapshots won't have the
        # pending-win fields; default them so a load doesn't crash on
        # older save states.
        if "_pending_winner" not in self.__dict__:
            self._pending_winner = None
        if "_pending_win_reason" not in self.__dict__:
            self._pending_win_reason = None
        if "_demon_killed_player_ids" not in self.__dict__:
            self._demon_killed_player_ids = []
        # The history list isn't persisted in the snapshot itself; the
        # caller (``load_state``) preserves the live engine's history.
        if "_history" not in self.__dict__ or self._history is None:
            self._history = []
        if (
            "_history_labels" not in self.__dict__
            or self._history_labels is None
        ):
            self._history_labels = []

    def save_state(self) -> str:
        """Return an opaque, base64-encoded pickle of the engine state.

        The string is stable across processes (subject to the engine's
        Python module layout matching) and small enough to store in
        memory or send over HTTP. ``load_state`` restores it back onto
        the same :class:`Engine` instance, preserving the engine's
        live history list.
        """
        payload = pickle.dumps(
            {"version": _SAVE_STATE_VERSION, "state": self.__getstate__()},
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        return base64.b64encode(payload).decode("ascii")

    def load_state(self, blob: str) -> None:
        """Restore engine state from a string produced by ``save_state``.

        The current ``_history`` / ``_history_labels`` lists are
        preserved across the restore so back-navigation can keep
        walking further back even after a load. If the caller
        explicitly wants to discard history they should call
        :meth:`reset_history` afterwards.
        """
        if not isinstance(blob, str) or not blob:
            raise ValueError("save-state blob must be a non-empty string")
        try:
            payload = pickle.loads(base64.b64decode(blob.encode("ascii")))
        except Exception as exc:
            raise ValueError(f"could not decode save-state blob: {exc!r}") from exc
        if not isinstance(payload, dict) or "state" not in payload:
            raise ValueError("save-state blob is not a recognized envelope")
        version = payload.get("version")
        if version != _SAVE_STATE_VERSION:
            raise ValueError(
                f"save-state version {version!r} not supported "
                f"(expected {_SAVE_STATE_VERSION!r})"
            )
        # Preserve the live engine's history across the restore.
        saved_history = list(self._history)
        saved_history_labels = list(self._history_labels)
        self.__setstate__(payload["state"])
        self._history = saved_history
        self._history_labels = saved_history_labels

    def reset_history(self) -> None:
        """Drop every saved Back-button checkpoint."""
        self._history = []
        self._history_labels = []

    def history_size(self) -> int:
        """Number of Back-button checkpoints currently held."""
        return len(self._history)

    def history_labels(self) -> List[str]:
        """Human-readable label for each checkpoint, oldest first.

        Mirrors the order of ``self._history``. Useful for UI tooling
        that wants to surface "you can rewind to: <label>".
        """
        return list(self._history_labels)

    def _save_history_checkpoint(self, label: str) -> None:
        """Push a fresh checkpoint onto the Back-button history.

        Called after each preset step completes (and after each
        first-night setup action). The label is human-readable and is
        only used by the UI / log; the engine never inspects it.
        """
        try:
            blob = self.save_state()
        except Exception as exc:  # pragma: no cover (defensive)
            self.log(f"history checkpoint save failed: {exc!r}")
            return
        self._history.append(blob)
        self._history_labels.append(label)

    def back(self) -> bool:
        """Pop the latest Back-button checkpoint and restore it.

        Behaviour:

          * If a night thread is running (the Storyteller is mid-night
            or mid-ability), this signals the thread to abort, joins
            it, restores the latest checkpoint, and restarts the night
            thread at the same step. This re-runs the ability the
            Storyteller was on, so any selections made during it can
            be redone.
          * If no thread is running, the latest checkpoint is restored
            in place (e.g. to step back across nights). Future Back
            presses keep walking further back.

        Returns ``True`` if a checkpoint was restored, ``False`` if
        the history is empty and there is nothing to revert to.
        """
        if not self._history:
            return False

        # Tell any blocked ability to abort. ``send_prompt`` re-raises
        # ``_AbortAbility`` once it sees the flag, which propagates up
        # to ``_run_night`` and tears the night thread down cleanly.
        was_running = bool(self._night_thread and self._night_thread.is_alive())
        if was_running:
            self._abort_requested = True
            with self._lock:
                # Wake the prompt-response wait without supplying a
                # real answer; ``send_prompt`` will see the abort flag
                # and raise rather than treat the (unset) response as
                # the Storyteller's choice.
                self._response_ready.set()
            try:
                self._night_thread.join(timeout=2.0)
            except Exception:  # pragma: no cover (defensive)
                pass

        blob = self._history.pop()
        try:
            self._history_labels.pop()
        except IndexError:  # pragma: no cover (defensive)
            pass
        try:
            self.load_state(blob)
        except Exception as exc:  # pragma: no cover (defensive)
            self.log(f"back() restore failed: {exc!r}")
            return False

        self.log(
            f"Back: restored checkpoint "
            f"({len(self._history)} earlier checkpoint(s) remaining)."
        )

        # If we were mid-night, re-launch the night thread so the
        # current step (the one the Storyteller wanted to redo) runs
        # again from scratch.
        if was_running and self._phase in (Phase.FIRST_NIGHT, Phase.NIGHT):
            self._launch_night_thread(resume=True)
        return True

    def _launch_night_thread(self, *, resume: bool) -> None:
        """Spawn the night worker thread.

        ``resume=False`` is the fresh-start path used by
        :meth:`start_night`. ``resume=True`` skips the
        once-per-night NIGHT_START / setup-action work and picks up
        from ``self._completed_step_index``.
        """
        self._night_thread = threading.Thread(
            target=self._run_night,
            kwargs={"resume": resume},
            name="botc-night",
            daemon=True,
        )
        self._night_thread.start()

    # ==================================================================
    #                       SNAPSHOTS
    # ==================================================================

    @classmethod
    def _setup_token_getters(cls) -> "tuple[tuple[str, str], ...]":
        """Return ``((kind, pool_getter_name), …)`` for every setup
        pick whose value lives in a pool slot.

        Sourced from the same registry the token-drag dispatch uses.
        Excludes the Drunk's IS-THE-DRUNK kind (the marker tracks the
        chair whose character is "Drunk", not a pool slot).
        """
        return tuple(
            (kind, spec["getter"])
            for kind, spec in cls._setup_pick_registry().items()
            if not spec.get("triggers_seat_swap") and spec.get("getter")
        )

    def _per_seat_tokens(self) -> Dict[str, List[int]]:
        """Per-seat (player_id) reminder-token presence.

        Generic collector: every seated character (and every
        impersonated perceived character) contributes via
        :meth:`Character.compute_reminder_tokens`. The engine has no
        character-name knowledge here — adding a new role with a new
        token kind requires no engine edit.

        Returns a dict ``{token_kind: [player_id, ...]}``. The merge
        across contributors is a deduped concatenation so multiple
        seats can hold the same kind of token.
        """
        merged: Dict[str, List[int]] = {}

        def _absorb(contributor: "Character") -> None:
            try:
                contrib = contributor.compute_reminder_tokens(self) or {}
            except Exception as exc:  # pragma: no cover (defensive)
                self.log(
                    f"compute_reminder_tokens crashed in "
                    f"{type(contributor).__name__}: {exc!r}"
                )
                return
            for kind, ids in contrib.items():
                if not ids:
                    continue
                bucket = merged.setdefault(kind, [])
                for pid in ids:
                    if pid not in bucket:
                        bucket.append(pid)

        for p in self.players:
            char = getattr(p, "character", None)
            if char is None:
                continue
            _absorb(char)
            perceived = char.acting_perceived_character()
            if perceived is not None:
                _absorb(perceived)

        return merged

    def chair_views(self) -> List[Dict[str, Any]]:
        """Enriched chair dicts for the UI.

        Each entry has every field from :meth:`ChairStore.list` plus:

          - ``display_character``: the role the seat *appears* to be
            (the Drunk's pretend Townsfolk if set; otherwise the real
            character).
          - ``tokens``: ``[{"kind": str}, ...]`` — every reminder token
            currently sitting on this chair. Token visibility is purely
            a function of state; the engine clears the underlying slot
            when the relevant ability resolves and the entry simply
            stops appearing here.
          - ``eligible_token_kinds``: list of setup-token kinds whose
            drag would land here. Sourced from the chair's character
            class' :meth:`Character.accepts_tokens`, gated on the
            character being in the current pool. The UI uses this to
            highlight valid drop targets.

        The UI is expected to render these directly without re-deriving
        eligibility or perceived character.
        """
        from engine.characters import CHARACTER_REGISTRY

        raw = self.chairs.list()
        seat = self._per_seat_tokens()
        # Resolve each setup token's current role-name once per call.
        setup_roles = {
            kind: getattr(self.pool, getter)()
            for kind, getter in self._setup_token_getters()
        }
        drunk_fake_role = self.pool.drunk_fake()
        pool_names = set(self.pool.list())

        out: List[Dict[str, Any]] = []
        for c in raw:
            char = (c.get("character") or "").strip()
            pid = c.get("player_id")
            kinds: List[str] = []

            # Setup tokens key off chair.character matching the slot.
            for kind, role in setup_roles.items():
                if role and char == role:
                    kinds.append(kind)

            # Runtime per-seat tokens key off chair.player_id. The
            # ``seat`` dict is now ``{kind: [player_id, ...]}`` for
            # every kind across every contributor.
            if pid is not None:
                for kind, holders in seat.items():
                    if pid in holders:
                        kinds.append(kind)

            display = char
            if char == "Drunk" and drunk_fake_role:
                display = drunk_fake_role

            # Drag eligibility — only meaningful when the chair holds an
            # in-pool role. The character class declares which token
            # kinds its chair can host.
            eligible: List[str] = []
            if char and char in pool_names:
                klass = CHARACTER_REGISTRY.get(char)
                if klass is not None:
                    eligible = sorted(klass.accepts_tokens())

            out.append({
                **c,
                "display_character": display,
                "tokens": [{"kind": k} for k in kinds],
                "eligible_token_kinds": eligible,
            })
        return out

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
            # A *pending* win has been triggered but the game hasn't
            # ended yet — players are still using abilities / nominating
            # during the day, or the next night is running as a no-op.
            # The UI can show this so the storyteller knows the next
            # dawn will finalize the result.
            "pending_winner": (
                self._pending_winner.value if self._pending_winner else None
            ),
            "pending_win_reason": self._pending_win_reason,
            "log_tail": self._log[-50:],
            "chairs": self.chair_views(),
            "storyteller": self.chairs.get_storyteller(),
            "pool": self.pool.list(),
            "drunk_fake": self.pool.drunk_fake(),
            "ft_red_herring": self.pool.ft_red_herring(),
            "washerwoman_townsfolk": self.pool.washerwoman_townsfolk(),
            "washerwoman_wrong": self.pool.washerwoman_wrong(),
            "selected_preset": self.selected_preset_name,
            # Setup-pick map: ``{owner_role: {slot: value}}`` derived
            # from the registry, so the UI can render parenthetical
            # annotations (e.g. "Drunk (Empath)", "Washerwoman
            # (Soldier)") generically without per-character branches.
            # Replaces the bespoke top-level keys above for new code;
            # the named keys are kept for backward compat.
            "setup_picks_by_role": self._setup_picks_by_role(),
            # Back-button affordance: the UI lights the button up only
            # while there is at least one checkpoint to return to.
            "history_size": self.history_size(),
            "completed_step_index": self._completed_step_index,
        }

    def _setup_picks_by_role(self) -> Dict[str, Dict[str, str]]:
        """Snapshot view of every setup pick currently on the pool,
        grouped by owner-role and indexed by slot name.

        Driven entirely by ``Character.setup_picks`` declarations —
        the engine has no character-name knowledge here.
        """
        out: Dict[str, Dict[str, str]] = {}
        for kind, spec in self._setup_pick_registry().items():
            owner = spec.get("owner_role")
            slot = spec.get("slot")
            getter_name = spec.get("getter")
            if not (owner and slot and getter_name):
                continue
            getter = getattr(self.pool, getter_name, None)
            value = getter() if getter else None
            if value is None:
                continue
            out.setdefault(owner, {})[slot] = value
        return out

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
