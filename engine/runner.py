"""Standalone engine runner — spawns the game engine as its own process.

This module is the subprocess entry-point used by the UI's "Start Game"
button. It runs an :class:`engine.engine.Engine` instance in a brand
new Python process, decoupled from the UI's HTTP server. Communication
is line-delimited JSON over stdin/stdout (a tiny JSON-RPC dialect):

  Inbound (stdin) — one command per line:
    {"cmd": "init",       "seats": [{"id": 1, "name": "Alice", ...}]}
    {"cmd": "start_game"}
    {"cmd": "start_night"}
    {"cmd": "advance_to_day"}
    {"cmd": "advance_to_night"}
    {"cmd": "respond",    "prompt_id": 17, "response": [2, 3]}
    {"cmd": "snapshot"}
    {"cmd": "kill",       "player_id": 4, "cause": "demon_kill"}
    {"cmd": "execute",    "player_id": 4}
    {"cmd": "shutdown"}

  Outbound (stdout) — one JSON object per line:
    {"ok": true, "snapshot": {...}}
    {"ok": false, "error": "..."}
    {"event": "prompt", "prompt": {...}}      # asynchronous prompt push
    {"event": "phase",  "phase": "first_night"}

The runner intentionally has *no* knowledge of the UI's chair layout or
character-pool widgets. It receives a seat list (name + character) and
just runs the game.

Why a separate process?
-----------------------
* Faults in a character implementation can't crash the UI — the runner
  exits and the UI shows a readable error.
* The engine's threading is fully isolated from the HTTP server's
  request threads, so a long-running ability prompt never blocks an
  HTTP worker.
* Replay / debugging: the runner can be driven from a recorded
  transcript file just as easily as from the live UI, so a captured
  game can be replayed deterministically.

Usage from the shell:
    python3 -m engine.runner

The UI's :func:`ui.ui.spawn_engine_runner` wraps this in a
:class:`subprocess.Popen` and proxies commands to it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

from engine import preset as preset_module
from engine.engine import Engine
from engine.enums import DeathCause


def _emit(obj: dict) -> None:
    """Write a single JSON object to stdout (line-delimited)."""
    sys.stdout.write(json.dumps(obj, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _debug(msg: str) -> None:
    """Print a human-readable debug line to stderr.

    These lines surface in the parent UI process's terminal — they're
    the operator-facing "the engine is alive" signal that distinguishes
    a healthy runner from a hung one.
    """
    sys.stderr.write(f"[engine-runner pid={os.getpid()}] {msg}\n")
    sys.stderr.flush()


def _ok(payload: Optional[dict] = None) -> None:
    out = {"ok": True}
    if payload is not None:
        out.update(payload)
    _emit(out)


def _err(msg: str) -> None:
    sys.stderr.write(f"[engine-runner pid={os.getpid()}] ERROR: {msg}\n")
    sys.stderr.flush()
    _emit({"ok": False, "error": msg})


class Runner:
    """Wraps an :class:`Engine` and executes commands serially.

    The runner serves a single game and exits when ``shutdown`` is
    received or when stdin is closed.
    """

    def __init__(self) -> None:
        self.engine = Engine()
        self._stop = threading.Event()
        self._prompt_watcher: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Setup commands.
    # ------------------------------------------------------------------

    def cmd_init(self, msg: dict) -> None:
        seats = msg.get("seats") or []
        if not isinstance(seats, list):
            return _err("seats must be a list")
        _debug(f"init: {len(seats)} seats")
        for s in seats:
            name = (s.get("name") or "").strip() or "?"
            char = (s.get("character") or "").strip()
            player = self.engine.add_seat(name)
            if char:
                try:
                    self.engine.assign_character(player.id, char)
                    _debug(f"  + seat {player.id}: {name} = {char}")
                except Exception as exc:
                    return _err(f"assign_character {name}: {exc!r}")
            else:
                _debug(f"  + seat {player.id}: {name} (no character)")

        # Optional: load preset script for night ordering.
        preset_name = (msg.get("preset") or "").strip()
        if preset_name:
            root = preset_module.default_presets_root()
            p = preset_module.load_preset(root, preset_name)
            if p is None:
                _debug(f"WARNING: preset {preset_name!r} not found; "
                       f"falling back to character night_order.")
            else:
                self.engine.set_preset(p)
                _debug(
                    f"loaded preset {p.name!r}: "
                    f"{len(p.first_night)} first-night steps, "
                    f"{len(p.other_nights)} other-night steps."
                )

        # Apply the UI's pre-game setup picks (Drunk fake, FT red
        # herring, Washerwoman seen TF) so the engine doesn't ask the
        # storyteller again at the start of the first night.
        setup_data = msg.get("setup_data") or {}
        if setup_data:
            self.engine.apply_setup_data(setup_data)
            _debug(f"applied setup_data: {setup_data}")

        _ok({"snapshot": self.engine.snapshot()})

    def cmd_start_game(self, msg: dict) -> None:
        _debug("start_game: validating setup and entering FIRST_NIGHT")
        try:
            self.engine.start_game()
        except Exception as exc:
            return _err(str(exc))
        # Spawn the prompt watcher: pushes prompt events to stdout
        # whenever the engine has one pending.
        if self._prompt_watcher is None:
            self._prompt_watcher = threading.Thread(
                target=self._watch_prompts, daemon=True
            )
            self._prompt_watcher.start()
        # Auto-start the first night so the storyteller sees prompts
        # immediately without an extra click. The engine's night thread
        # runs the preset sheet, blocking on send_prompt for each step.
        self.engine.set_auto_advance_to_day(True)
        try:
            self.engine.start_night()
            _debug(f"first night started; phase={self.engine.phase.value}")
        except Exception as exc:  # pragma: no cover
            _debug(f"start_night failed: {exc!r}")
        _ok({"snapshot": self.engine.snapshot()})

    # ------------------------------------------------------------------
    # Phase commands.
    # ------------------------------------------------------------------

    def cmd_start_night(self, msg: dict) -> None:
        _debug(f"start_night ({self.engine.phase.value})")
        try:
            self.engine.start_night()
        except Exception as exc:
            return _err(str(exc))
        _ok({"snapshot": self.engine.snapshot()})

    def cmd_advance_to_day(self, msg: dict) -> None:
        _debug("advance_to_day")
        try:
            deaths = self.engine.advance_to_day()
        except Exception as exc:
            return _err(str(exc))
        _debug(f"  night deaths: {[p.name for p in deaths]}")
        _ok({
            "snapshot": self.engine.snapshot(),
            "deaths": [p.id for p in deaths],
        })

    def cmd_advance_to_night(self, msg: dict) -> None:
        _debug("advance_to_night")
        try:
            self.engine.advance_to_night()
        except Exception as exc:
            return _err(str(exc))
        _ok({"snapshot": self.engine.snapshot()})

    # ------------------------------------------------------------------
    # Prompt / response.
    # ------------------------------------------------------------------

    def cmd_respond(self, msg: dict) -> None:
        try:
            prompt_id = int(msg["prompt_id"])
        except (KeyError, ValueError, TypeError):
            return _err("prompt_id must be an int")
        accepted = self.engine.respond(prompt_id, msg.get("response"))
        _debug(f"respond prompt_id={prompt_id} accepted={accepted}")
        _ok({"accepted": accepted})

    # ------------------------------------------------------------------
    # State / mutators.
    # ------------------------------------------------------------------

    def cmd_snapshot(self, msg: dict) -> None:
        _ok({"snapshot": self.engine.snapshot()})

    def cmd_kill(self, msg: dict) -> None:
        try:
            pid = int(msg["player_id"])
        except (KeyError, ValueError, TypeError):
            return _err("player_id must be an int")
        cause = DeathCause(msg.get("cause", DeathCause.STORYTELLER.value))
        self.engine.kill(pid, cause)
        _ok({"snapshot": self.engine.snapshot()})

    def cmd_execute(self, msg: dict) -> None:
        try:
            pid = int(msg["player_id"])
        except (KeyError, ValueError, TypeError):
            return _err("player_id must be an int")
        try:
            self.engine.execute_player(pid)
        except Exception as exc:
            return _err(str(exc))
        _ok({"snapshot": self.engine.snapshot()})

    def cmd_shutdown(self, msg: dict) -> None:
        _ok({"shutting_down": True})
        self._stop.set()

    # ------------------------------------------------------------------
    # Prompt push.
    # ------------------------------------------------------------------

    def _watch_prompts(self) -> None:
        """Push every newly-pending prompt to stdout as an event.

        Polls roughly 50 ms to catch new prompts. The prompt id is
        stable per-prompt, so we only emit each prompt once.
        """
        last_id: Optional[int] = None
        while not self._stop.is_set():
            p = self.engine.pending_prompt()
            if p is not None and p.id != last_id:
                last_id = p.id
                # Snapshot current phase too — useful for debugging the
                # transitions the storyteller will see in the UI.
                _debug(
                    f"PROMPT id={p.id} type={p.type.value} "
                    f"meta={p.meta} text={p.text!r}"
                )
                _emit({"event": "prompt", "prompt": p.to_dict()})
            elif p is None and last_id is not None:
                last_id = None
            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Main loop.
    # ------------------------------------------------------------------

    HANDLERS = {
        "init": cmd_init,
        "start_game": cmd_start_game,
        "start_night": cmd_start_night,
        "advance_to_day": cmd_advance_to_day,
        "advance_to_night": cmd_advance_to_night,
        "respond": cmd_respond,
        "snapshot": cmd_snapshot,
        "kill": cmd_kill,
        "execute": cmd_execute,
        "shutdown": cmd_shutdown,
    }

    def run(self) -> None:
        # Hello message so the parent knows we're up.
        _debug("engine runner subprocess starting up.")
        _emit({"event": "ready", "pid": _self_pid()})
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                _err(f"invalid JSON: {exc}")
                continue
            cmd = msg.get("cmd")
            _debug(f"<- cmd={cmd!r}")
            handler = self.HANDLERS.get(cmd)
            if handler is None:
                _err(f"unknown cmd: {cmd!r}")
                continue
            try:
                handler(self, msg)
            except Exception as exc:  # pragma: no cover
                _err(f"handler crashed: {exc!r}")
            if self._stop.is_set():
                break
        _debug("engine runner shutting down.")


def _self_pid() -> int:
    import os
    return os.getpid()


def main() -> None:
    Runner().run()


if __name__ == "__main__":
    main()
