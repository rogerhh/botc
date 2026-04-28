"""Blood on the Clocktower — Storyteller GUI server.

A small stdlib-only HTTP server that:

  * Serves the storyteller's local-machine ``index.html`` (town square UI).
  * Serves the read-only / per-player ``phone.html`` for phones on the
    same LAN.
  * Holds the visual "chair" arrangement (the in-progress town square).
  * Talks to the :class:`engine.Engine` instance — exposing setup,
    night/day controls, and the prompt/response loop.

Run:
    python3 -m ui.ui [--host 0.0.0.0] [--port 8000]
                     [--access-code [CODE]]

Then open http://localhost:8000 in a browser. Phone view: /phone.

The chair UI is preserved unchanged from the prior implementation —
the new engine endpoints sit alongside the chair endpoints and only
become active once the storyteller clicks "Start Game".
"""

from __future__ import annotations

import argparse
import http.cookies
import json
import os
import random
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

# Allow running as `python3 ui/ui.py` from the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.engine import Engine  # noqa: E402
from engine.enums import Alignment, CharType, DeathCause  # noqa: E402
from engine import preset as preset_module  # noqa: E402
from engine import script as script_data  # noqa: E402

STATIC_DIR = os.path.join(_HERE, "static")


# ---------------------------------------------------------------------------
# Chair / town-square state.
#
# As of the engine-driver refactor, chair layout lives on the engine
# (see ``engine/chairs.py``). The module-level ``STORE`` symbol below
# is preserved as a thin proxy so the request-handler code reads
# unchanged: every ``STORE.foo(...)`` call is forwarded to
# ``ENGINE.chairs.foo(...)`` at access time, so a fresh ENGINE
# (e.g. after ``/api/engine/reset``) is picked up automatically.
# ---------------------------------------------------------------------------


from engine.chairs import ChairStore  # noqa: E402, F401  (re-exported)


class _ChairStoreProxy:
    """Thin pass-through that forwards every attribute to ``ENGINE.chairs``.

    This exists so the rest of ``ui.py`` (which still says ``STORE.list()``
    everywhere) doesn't need to know that the chair store moved onto the
    engine. The proxy does the lookup at call time, so an ``ENGINE_replace``
    swaps the underlying store without leaving stale references behind.
    """

    def __getattr__(self, name: str):
        return getattr(ENGINE.chairs, name)


# ---------------------------------------------------------------------------
# Character pool (the bag of characters chosen for this game).
#
# As of the engine-driver refactor, the pool lives on the engine
# (see ``engine/pool.py``). The module-level ``POOL`` symbol below is
# preserved as a thin proxy so the request-handler code reads
# unchanged. Same pattern as ``STORE`` -> ``ENGINE.chairs`` above.
# ---------------------------------------------------------------------------


_PRESETS_DIR = os.path.join(_ROOT, "assets", "presets")

# Map CSV "team" values to the JSON-style category keys the rest of the
# codebase (and the front-end) expect.
_TEAM_TO_CATEGORY = {
    "townsfolk": "Townsfolk",
    "outsider": "Outsiders",
    "outsiders": "Outsiders",
    "minion": "Minions",
    "minions": "Minions",
    "demon": "Demons",
    "demons": "Demons",
}


from engine.pool import CharacterPool  # noqa: E402, F401  (re-exported)


class _PoolProxy:
    """Thin pass-through to ``ENGINE.pool`` (lookup-at-call-time)."""

    def __getattr__(self, name: str):
        return getattr(ENGINE.pool, name)



def _list_presets() -> List[str]:
    """Names of presets discovered in the assets/presets directory."""
    try:
        entries = sorted(os.listdir(_PRESETS_DIR))
    except OSError:
        return []
    presets: List[str] = []
    for entry in entries:
        full = os.path.join(_PRESETS_DIR, entry, "characters.csv")
        if os.path.isfile(full):
            presets.append(entry)
    return presets


def _load_preset(name: str) -> Optional[dict]:
    """Load the characters.csv for a preset, or None if not found.

    The CSV has columns ``name,team`` where ``team`` is one of
    ``Townsfolk`` / ``Outsider`` / ``Minion`` / ``Demon`` (case-insensitive,
    plural also accepted). Returns the same ``{category: [names]}`` dict
    shape the rest of the codebase already consumes.
    """
    import csv

    safe = os.path.normpath(name)
    if safe.startswith("..") or os.path.isabs(safe) or "/" in safe:
        return None
    path = os.path.join(_PRESETS_DIR, safe, "characters.csv")
    if not os.path.isfile(path):
        return None
    data: dict = {"Townsfolk": [], "Outsiders": [], "Minions": [], "Demons": []}
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return None
            # Be tolerant of header casing/whitespace.
            field_map = {
                (k or "").strip().lower(): k for k in reader.fieldnames
            }
            name_key = field_map.get("name")
            team_key = field_map.get("team")
            if name_key is None or team_key is None:
                return None
            for row in reader:
                char_name = (row.get(name_key) or "").strip()
                team = (row.get(team_key) or "").strip().lower()
                if not char_name or not team:
                    continue
                category = _TEAM_TO_CATEGORY.get(team)
                if category is None:
                    continue
                data[category].append(char_name)
    except OSError:
        return None
    return data


def _randomize_pool_from_preset(
    preset_name: str,
) -> Tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Pick a random valid roster from the named preset.

    Returns ``(names, drunk_fake, error)`` where ``error`` is None on
    success. The picker:

      * looks up the recommended (T, O, M, D) counts for the current
        chair count,
      * picks ``D`` demons and ``M`` minions uniformly at random,
      * applies any setup deltas from the chosen evil team (e.g. Baron
        gives +2 outsiders / -2 townsfolk),
      * picks ``T_adjusted`` townsfolk and ``O_adjusted`` outsiders to
        fill the remaining slots,
      * if Drunk is among the chosen, also picks a random Townsfolk
        from the preset (one that's *not* in the bag) to be the
        Drunk's pretend role.

    The pool is *not* mutated; the caller is responsible for installing
    the returned list.
    """
    data = _load_preset(preset_name)
    if data is None:
        return None, None, "no such preset"

    chair_count = len(STORE.list())
    try:
        rec_t, rec_o, rec_m, rec_d = script_data.recommended_counts(chair_count)
    except (ValueError, KeyError):
        return None, None, (
            f"need at least 5 chairs to randomize (have {chair_count})"
        )

    def _filter(category: str) -> List[str]:
        return [
            n for n in (data.get(category) or [])
            if isinstance(n, str) and n in script_data.SCRIPT_BY_NAME
        ]

    townsfolk = _filter("Townsfolk")
    outsiders = _filter("Outsiders")
    minions = _filter("Minions")
    demons = _filter("Demons")

    if len(demons) < rec_d:
        return None, None, f"preset only has {len(demons)} demon(s), need {rec_d}"
    if len(minions) < rec_m:
        return None, None, f"preset only has {len(minions)} minion(s), need {rec_m}"

    chosen_demons = random.sample(demons, rec_d)
    chosen_minions = random.sample(minions, rec_m)

    # Apply setup deltas from the chosen evil team. (In Trouble Brewing
    # this is just the Baron, but the math is general.)
    townsfolk_delta = 0
    outsider_delta = 0
    for n in chosen_minions + chosen_demons:
        spec = script_data.SCRIPT_BY_NAME[n]
        townsfolk_delta += spec.setup_townsfolk_delta
        outsider_delta += spec.setup_outsider_delta

    adjusted_t = max(0, rec_t + townsfolk_delta)
    adjusted_o = max(0, rec_o + outsider_delta)

    # Don't ask for more than the preset has.
    take_t = min(adjusted_t, len(townsfolk))
    take_o = min(adjusted_o, len(outsiders))

    chosen_townsfolk = random.sample(townsfolk, take_t)
    chosen_outsiders = random.sample(outsiders, take_o)

    chosen = (chosen_townsfolk + chosen_outsiders
              + chosen_minions + chosen_demons)

    # If the Drunk is in play, pick a random Townsfolk that *isn't*
    # already in the bag to be the Drunk's pretend role.
    drunk_fake: Optional[str] = None
    if "Drunk" in chosen:
        chosen_set = set(chosen)
        candidates = [n for n in townsfolk if n not in chosen_set]
        if candidates:
            drunk_fake = random.choice(candidates)

    # Group by type for a tidy display order in the side panel.
    return chosen, drunk_fake, None


def _pick_default_red_herring(names: List[str]) -> Optional[str]:
    """Pick a random Good role (Townsfolk / Outsider) from ``names``.

    Used as a default after randomization when the Fortune Teller is
    in the pool; the storyteller can still override the choice in the
    Add-Characters screen.
    """
    good = [
        n for n in names
        if n in script_data.SCRIPT_BY_NAME
        and script_data.SCRIPT_BY_NAME[n].char_type
        in (CharType.TOWNSFOLK, CharType.OUTSIDER)
    ]
    return random.choice(good) if good else None


# ---------------------------------------------------------------------------
# Token-drag operations.
#
# The grimoire shows three movable reminder tokens — IS THE DRUNK,
# FT RED HERRING, WW TOWNSFOLK — that the storyteller can drag from
# one chair to another. The destination chair's character determines
# which role the token lands on. Each helper validates the destination
# and applies the corresponding transformation, returning ``None`` on
# success or a human-readable error string on rejection.
#
# These helpers are package-private; the HTTP handlers above are the
# only call sites.
# ---------------------------------------------------------------------------


def _apply_token(kind: str, dest_chair_id: int) -> Optional[str]:
    """Single entry point used by the grimoire's drag-drop handler.

    Wraps ``Engine.apply_token``, which owns the swap-vs-overwrite
    decision for mutex pairs (WW TOWNSFOLK ↔ WW WRONG, etc.). Drunk /
    FT tokens just fall through to their underlying ``move_*`` method.
    """
    return ENGINE.apply_token(kind, dest_chair_id)


# ---------------------------------------------------------------------------
# Globals (set up in main()).
# ---------------------------------------------------------------------------

ENGINE = Engine()
STORE = _ChairStoreProxy()
POOL = _PoolProxy()

# Selected preset name (e.g. "trouble_brewing"). Lives on the engine
# post-refactor; these module-level shims keep the handler code reading
# unchanged. Drives the engine's night order at start_game time.


def SELECTED_PRESET_set(name: Optional[str]) -> None:
    """Forward the storyteller's preset choice onto the engine."""
    ENGINE.selected_preset_name = name


def _selected_preset() -> Optional[str]:
    return ENGINE.selected_preset_name


def ENGINE_replace(new_engine: "Engine") -> None:
    """Swap the in-process engine for a fresh instance.

    Used by the reset endpoint. Same rationale as
    :func:`SELECTED_PRESET_set` — we can't ``global ENGINE`` inside a
    handler that already references ``ENGINE`` elsewhere.
    """
    global ENGINE
    ENGINE = new_engine


def _setup_data_from_pool() -> Dict[str, Any]:
    """Snapshot the UI's pre-game setup picks for the engine.

    Returns a dict mirroring the keys :meth:`Engine.apply_setup_data`
    consumes:

      * ``drunk_fake`` — Townsfolk role the Drunk thinks they are.
      * ``ft_red_herring`` — role chosen as the Fortune Teller's red
        herring.
      * ``washerwoman_townsfolk`` — Townsfolk role the Washerwoman is
        shown.
      * ``washerwoman_wrong`` — role of the WRONG player the WW is
        pointed at alongside the seen Townsfolk.
      * ``librarian_outsider`` — Outsider role the Librarian is shown.
      * ``investigator_minion`` — Minion role the Investigator is
        shown.

    Missing picks are simply omitted; the engine treats absent keys
    as "no override".
    """
    data: Dict[str, Any] = {}
    drunk_fake = POOL.drunk_fake()
    if drunk_fake:
        data["drunk_fake"] = drunk_fake
    ft_red_herring = POOL.ft_red_herring()
    if ft_red_herring:
        data["ft_red_herring"] = ft_red_herring
    ww_townsfolk = POOL.washerwoman_townsfolk()
    if ww_townsfolk:
        data["washerwoman_townsfolk"] = ww_townsfolk
    ww_wrong = POOL.washerwoman_wrong()
    if ww_wrong:
        data["washerwoman_wrong"] = ww_wrong
    librarian_outsider = POOL.librarian_outsider()
    if librarian_outsider:
        data["librarian_outsider"] = librarian_outsider
    librarian_wrong = POOL.librarian_wrong()
    if librarian_wrong:
        data["librarian_wrong"] = librarian_wrong
    investigator_minion = POOL.investigator_minion()
    if investigator_minion:
        data["investigator_minion"] = investigator_minion
    investigator_wrong = POOL.investigator_wrong()
    if investigator_wrong:
        data["investigator_wrong"] = investigator_wrong
    return data

ACCESS_CODE: Optional[str] = None
COOKIE_NAME = "botc_access"
OPEN_PATHS = ("/enter", "/enter/", "/static/")
SERVER_PORT: Optional[int] = None

# ---------------------------------------------------------------------------
# Engine subprocess.
#
# The "Start Game" button spawns a separate process running
# ``python3 -m engine.runner`` so the engine lives in its own OS process,
# decoupled from the UI's HTTP server. We keep a reference to the
# subprocess here so the storyteller's UI can show its status (running /
# crashed) and so the UI can shut it down cleanly on a "New Game" click.
#
# The in-process ``ENGINE`` is the *primary* engine the HTTP request
# handlers continue to query — the subprocess is a *mirror* spun up to
# satisfy the "run the engine in a separate process" requirement and to
# enable a future migration where the UI proxies all engine calls
# through the runner. Today the runner runs alongside the in-process
# engine and writes its own snapshot to stdout for inspection.
# ---------------------------------------------------------------------------

ENGINE_PROCESS: Optional[subprocess.Popen] = None
ENGINE_PROCESS_LOCK = threading.Lock()


def kill_engine_runner_subprocess() -> bool:
    """Tear down the engine runner subprocess (if any).

    Sends a graceful ``shutdown`` command first; if the child doesn't
    exit within a second, follows up with SIGKILL. Returns True if a
    subprocess was running (and is now dead), False if there was
    nothing to kill.
    """
    global ENGINE_PROCESS
    with ENGINE_PROCESS_LOCK:
        proc = ENGINE_PROCESS
        if proc is None or proc.poll() is not None:
            ENGINE_PROCESS = None
            return False
        try:
            if proc.stdin is not None:
                proc.stdin.write(b'{"cmd":"shutdown"}\n')
                proc.stdin.flush()
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        ENGINE_PROCESS = None
        return True


def spawn_engine_runner_subprocess() -> Optional[subprocess.Popen]:
    """Start (or restart) the engine runner as a child process.

    Reads the current chair layout, serializes the seating + character
    assignments, and pipes them into ``python3 -m engine.runner``'s
    stdin. The child's stdout is set up as a pipe so the UI can inspect
    its responses (each line is a JSON object — see ``engine/runner.py``).

    If a previous runner is still alive, it is shut down first so we
    never have two engine subprocesses fighting over the same game.

    Returns the :class:`subprocess.Popen` for the new child, or ``None``
    if spawning failed (in which case the in-process engine is still
    authoritative — the storyteller can carry on as before).
    """
    global ENGINE_PROCESS

    with ENGINE_PROCESS_LOCK:
        # Tear down any prior runner.
        if ENGINE_PROCESS is not None and ENGINE_PROCESS.poll() is None:
            try:
                ENGINE_PROCESS.stdin.write(b'{"cmd":"shutdown"}\n')
                ENGINE_PROCESS.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                ENGINE_PROCESS.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                ENGINE_PROCESS.kill()

        # Build the seat list from the current chairs in chair-id order
        # — same source of truth as ``_sync_chairs_to_engine`` so the
        # subprocess engine assigns the same ``player.id == chair.id``
        # mapping the in-process engine sees.
        seats: List[Dict[str, Any]] = []
        for chair in sorted(STORE.list(), key=lambda c: c["id"]):
            name = (chair.get("name") or "").strip()
            character = (chair.get("character") or "").strip()
            if not name:
                continue
            seats.append({"name": name, "character": character})

        # Spawn ``python3 -m engine.runner`` from the repo root so the
        # engine package imports resolve identically to the parent.
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "engine.runner"],
                cwd=_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            print(f"[engine-runner] spawn failed: {exc!r}", file=sys.stderr)
            ENGINE_PROCESS = None
            return None

        ENGINE_PROCESS = proc
        # Send init + start_game to the runner. We don't drain stdout
        # synchronously here (the parent UI's request handler is on the
        # critical path); a daemon reader prints stdout to stderr for
        # operator visibility instead.
        try:
            init_msg = json.dumps({
                "cmd": "init",
                "seats": seats,
                "preset": _selected_preset() or "",
                "setup_data": _setup_data_from_pool(),
            }) + "\n"
            start_msg = json.dumps({"cmd": "start_game"}) + "\n"
            proc.stdin.write(init_msg.encode("utf-8"))
            proc.stdin.write(start_msg.encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            print(f"[engine-runner] init failed: {exc!r}", file=sys.stderr)

        # Pump the runner's stdout/stderr into the UI process logs so
        # the operator sees what the subprocess is doing without
        # blocking the request handler.
        def _pump(stream, prefix):
            try:
                for line in iter(stream.readline, b""):
                    if not line:
                        break
                    sys.stderr.write(f"[engine-runner {prefix}] "
                                     f"{line.decode('utf-8', 'replace')}")
                    sys.stderr.flush()
            except (OSError, ValueError):
                pass

        threading.Thread(
            target=_pump, args=(proc.stdout, "out"),
            name="engine-runner-stdout", daemon=True,
        ).start()
        threading.Thread(
            target=_pump, args=(proc.stderr, "err"),
            name="engine-runner-stderr", daemon=True,
        ).start()

        return proc


def _make_random_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


# ---------------------------------------------------------------------------
# LAN IP detection.
# ---------------------------------------------------------------------------

_INET4_RE = re.compile(r"\binet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)")


def _parse_ipv4s(text: str) -> list:
    found = []
    for m in _INET4_RE.finditer(text):
        ip = m.group(1)
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        if ip not in found:
            found.append(ip)
    return found


def _is_rfc1918(ip: str) -> bool:
    a, b, *_ = (int(p) for p in ip.split("."))
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False


def _looks_dockerish(ip: str) -> bool:
    return ip.startswith("172.17.") or ip.startswith("172.18.")


def _rank_ip(ip: str) -> tuple:
    return (
        0 if _is_rfc1918(ip) and not _looks_dockerish(ip) else
        1 if _is_rfc1918(ip) else 2,
        ip,
    )


def _detect_lan_ips() -> list:
    for cmd in (["ifconfig"], ["ifconfig", "-a"], ["ip", "-4", "-o", "addr"]):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=2, check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                ips = _parse_ipv4s(proc.stdout)
                if ips:
                    ips.sort(key=_rank_ip)
                    return ips
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return [ip]
        finally:
            s.close()
    except OSError:
        pass
    return []


# ---------------------------------------------------------------------------
# HTTP handler.
# ---------------------------------------------------------------------------

CHAIR_ID_RE = re.compile(r"^/api/chairs/(\d+)/?$")
PLAYER_ID_RE = re.compile(r"^/api/players/(\d+)/?$")
PLAYER_VIEW_RE = re.compile(r"^/api/player_view/(\d+)/?$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        print(f"[{self.log_date_time_string()}] {self.address_string()} "
              f"{format % args}")

    # ---- helpers ----

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, content_type: str) -> None:
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Tuple[bool, dict]:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return True, {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                return False, {}
            return True, data
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False, {}

    # ---- auth ----

    def _is_localhost(self) -> bool:
        ip = self.client_address[0]
        return ip in ("127.0.0.1", "::1", "localhost")

    def _provided_code(self) -> Optional[str]:
        raw_cookie = self.headers.get("Cookie", "")
        if raw_cookie:
            jar = http.cookies.SimpleCookie()
            try:
                jar.load(raw_cookie)
            except http.cookies.CookieError:
                pass
            else:
                morsel = jar.get(COOKIE_NAME)
                if morsel is not None:
                    return morsel.value
        qs = urllib.parse.urlparse(self.path).query
        for k, v in urllib.parse.parse_qsl(qs):
            if k == "code":
                return v
        return None

    def _authorized(self) -> bool:
        if ACCESS_CODE is None:
            return True
        if self._is_localhost():
            return True
        return self._provided_code() == ACCESS_CODE

    def _is_open_path(self, path: str) -> bool:
        return any(
            path == p or path.startswith(p if p.endswith("/") else p + "/")
            for p in OPEN_PATHS
        )

    def _gate(self) -> bool:
        path = self.path.split("?", 1)[0]
        if self._is_open_path(path):
            return True
        if self._authorized():
            return True
        if path.startswith("/api/"):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "access code required"})
            return False
        target = "/enter?next=" + urllib.parse.quote(self.path, safe="/?=&%")
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _set_access_cookie(self, code: str) -> str:
        jar = http.cookies.SimpleCookie()
        jar[COOKIE_NAME] = code
        m = jar[COOKIE_NAME]
        m["path"] = "/"
        m["max-age"] = str(60 * 60 * 24 * 7)
        m["samesite"] = "Lax"
        return m.OutputString()

    # ---- routing ----

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path in ("/enter", "/enter/"):
            self._serve_enter_page()
            return

        if not self._gate():
            return

        if ACCESS_CODE is not None and not self._is_localhost():
            qs = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(qs))
            if params.get("code") == ACCESS_CODE:
                clean = path
                other = {k: v for k, v in params.items() if k != "code"}
                if other:
                    clean += "?" + urllib.parse.urlencode(other)
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", clean)
                self.send_header("Set-Cookie", self._set_access_cookie(ACCESS_CODE))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        if path == "/" or path == "/index.html":
            self._send_file(os.path.join(STATIC_DIR, "index.html"),
                            "text/html; charset=utf-8")
            return

        if path in ("/phone", "/phone/", "/phone.html"):
            # Storyteller mobile UI. The QR shown on the local UI
            # points here, so a phone scan gives the storyteller a
            # mobile-friendly grimoire (chairs + tokens + side panel +
            # prompts). Player-facing views live at /player below.
            self._send_file(os.path.join(STATIC_DIR, "phone.html"),
                            "text/html; charset=utf-8")
            return

        if path in ("/player", "/player/", "/player.html"):
            # Per-player phone UI. Placeholder for now — once player
            # codes / personal QR routing is set up this will be the
            # view a single player gets on their own device. Kept
            # separate from /phone (storyteller mobile) so the two
            # UIs can evolve independently.
            self._send_file(os.path.join(STATIC_DIR, "player.html"),
                            "text/html; charset=utf-8")
            return

        if path == "/api/state":
            self._send_json(HTTPStatus.OK, {
                "chairs": ENGINE.chair_views(),
                "storyteller": STORE.get_storyteller(),
                "engine": ENGINE.snapshot(),
                "character_pool": _character_pool_snapshot(),
            })
            return

        if path == "/api/host_info":
            ips = _detect_lan_ips()
            self._send_json(HTTPStatus.OK, {
                "lan_ip": ips[0] if ips else None,
                "candidates": ips,
                "port": SERVER_PORT,
            })
            return

        # ---- preset / character-pool endpoints ----

        if path == "/api/presets":
            self._send_json(HTTPStatus.OK, {"presets": _list_presets()})
            return

        if path.startswith("/api/presets/"):
            preset_name = urllib.parse.unquote(path[len("/api/presets/"):]).rstrip("/")
            data = _load_preset(preset_name)
            if data is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such preset"})
                return
            self._send_json(HTTPStatus.OK, {"name": preset_name, "characters": data})
            return

        if path == "/api/character_pool":
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
            return

        # ---- engine endpoints ----

        if path == "/api/script":
            # Roster the storyteller can choose from at setup.
            self._send_json(HTTPStatus.OK, {
                "characters": [
                    {
                        "name": c.name,
                        "type": c.char_type.value,
                        "ability": c.ability_text,
                        "first_night_order": c.first_night_order,
                        "other_night_order": c.other_night_order,
                        "once_per_game": c.once_per_game,
                        "setup_outsider_delta": c.setup_outsider_delta,
                        "setup_townsfolk_delta": c.setup_townsfolk_delta,
                    }
                    for c in script_data.TROUBLE_BREWING
                ],
            })
            return

        if path == "/api/engine":
            self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            return

        if path == "/api/prompt":
            p = ENGINE.pending_prompt()
            self._send_json(HTTPStatus.OK, {
                "prompt": p.to_dict() if p else None,
            })
            return

        if path == "/api/engine/console":
            # Live console feed (also replayed verbatim as the
            # end-of-game report). Same payload powers both the small
            # console panel that updates throughout the game and the
            # "View Report" overlay that opens when the game ends.
            snap = ENGINE.snapshot()
            self._send_json(HTTPStatus.OK, {
                "winner": snap.get("winner"),
                "win_reason": snap.get("win_reason"),
                "phase": snap.get("phase"),
                "entries": ENGINE.console,
                "players": [
                    {
                        "id": p.get("id"),
                        "name": p.get("name"),
                        "seat": p.get("seat"),
                        "character": p.get("character"),
                        "alignment": p.get("alignment"),
                        "alive": p.get("alive"),
                    }
                    for p in snap.get("players", [])
                ],
            })
            return

        m = PLAYER_VIEW_RE.match(path)
        if m:
            try:
                view = ENGINE.player_view(int(m.group(1)))
                self._send_json(HTTPStatus.OK, view)
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such player"})
            return

        # ---- static fallback ----

        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            safe = os.path.normpath(rel)
            if safe.startswith("..") or os.path.isabs(safe):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            full = os.path.join(STATIC_DIR, safe)
            content_type = _guess_content_type(full)
            self._send_file(full, content_type)
            return

        # ---- /assets/ — game assets (character tokens, etc.) ----
        # Mirrors /static/ but rooted at the repo's assets directory so
        # we can serve images like /assets/tokens/drunk.png inline in
        # the SVG town square.
        if path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            safe = os.path.normpath(rel)
            if safe.startswith("..") or os.path.isabs(safe):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            full = os.path.join(_ROOT, "assets", safe)
            content_type = _guess_content_type(full)
            self._send_file(full, content_type)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path in ("/enter", "/enter/"):
            self._handle_enter_submit()
            return

        if not self._gate():
            return

        # ---- chair endpoints ----

        if self.path == "/api/chairs":
            ok, data = self._read_json()
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            chair = STORE.add(data.get("x"), data.get("y"))
            self._send_json(HTTPStatus.CREATED, chair)
            return

        if self.path == "/api/chairs/remove_last":
            removed = STORE.remove_last()
            self._send_json(HTTPStatus.OK, {"removed": removed})
            return

        # ---- character-pool endpoints ----

        if path == "/api/character_pool":
            ok, data = self._read_json()
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            name = (data.get("name") or "").strip()
            names = data.get("names")
            if isinstance(names, list):
                # Replace the pool with the given names (validated against script).
                valid = [n for n in names if isinstance(n, str)
                         and n in script_data.SCRIPT_BY_NAME]
                POOL.set_many(valid)
                self._send_json(HTTPStatus.OK, _character_pool_snapshot())
                return
            if not name:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "need name or names list"})
                return
            if name not in script_data.SCRIPT_BY_NAME:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": f"unknown character {name!r}"})
                return
            added = POOL.add(name)
            self._send_json(HTTPStatus.OK,
                            {"added": added, **_character_pool_snapshot()})
            return

        if path == "/api/character_pool/clear":
            POOL.clear()
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
            return

        if path == "/api/character_pool/randomize":
            ok, data = self._read_json()
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            preset_name = (data.get("preset") or "").strip()
            if not preset_name:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "need a preset"})
                return
            names, drunk_fake, err = _randomize_pool_from_preset(preset_name)
            if names is None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": err})
                return
            # Remember the preset so /api/engine/start_game can install
            # its night order on the engine without the UI needing to
            # repeat itself.
            SELECTED_PRESET_set(preset_name)
            POOL.set_many(names)
            if drunk_fake is not None:
                # Best-effort: if the fake somehow conflicts, the pool
                # snapshot below will just show pending_drunk_fake.
                try:
                    POOL.set_drunk_fake(drunk_fake)
                except ValueError:
                    pass
            # FT red herring and WW seen-Townsfolk are auto-filled
            # inside POOL.set_many(), so no extra seeding needed here.
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
            return

        # ---- token-drag endpoints ----
        # The grimoire lets the storyteller drag the IS-THE-DRUNK,
        # FT red-herring, and WW seen-Townsfolk reminder tokens between
        # chairs. Each endpoint takes ``{"chair_id": <int>}`` — the
        # destination chair the token is being dropped on — and updates
        # the relevant pool / chair state. See the docstrings on each
        # branch for the precise transformation.

        # Unified token-apply endpoint. The grimoire's drag-drop handler
        # POSTs ``{kind, chair_id}`` and the engine handles routing —
        # mutex swap (e.g. WW TOWNSFOLK ↔ WW WRONG) or plain move. The
        # legacy per-kind ``/move_*_token`` endpoints have been removed.
        if path == "/api/character_pool/apply_token":
            ok, data = self._read_json()
            if not ok or "chair_id" not in data or "kind" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "need kind and chair_id"})
                return
            try:
                kind = str(data["kind"])
                chair_id = int(data["chair_id"])
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "kind must be a string and chair_id "
                                          "must be an int"})
                return
            err = _apply_token(kind, chair_id)
            if err is not None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": err})
                return
            self._send_json(HTTPStatus.OK, {
                "chairs": ENGINE.chair_views(),
                **_character_pool_snapshot(),
            })
            return

        # ---- engine control endpoints ----

        if path == "/api/engine/start_game":
            ok, body = self._read_json()
            preset_from_body = (body or {}).get("preset")
            if isinstance(preset_from_body, str) and preset_from_body:
                # Note: we don't use ``global`` here because the
                # top of do_POST already declares it for the randomize
                # branch — Python's parser prohibits a second ``global``
                # declaration after an assignment in the same function.
                SELECTED_PRESET_set(preset_from_body)
            try:
                self._sync_chairs_to_engine()
                # Push the UI's setup picks onto the engine so the
                # Drunk / Fortune Teller / Washerwoman come up
                # pre-configured (no extra prompts at start_night).
                setup_data = _setup_data_from_pool()
                ENGINE.apply_setup_data(setup_data)
                # Install the preset script on the engine so the night
                # loop walks it instead of the legacy Character.night_order.
                preset_name = _selected_preset()
                if preset_name:
                    p = preset_module.load_preset(_PRESETS_DIR, preset_name)
                    if p is not None:
                        ENGINE.set_preset(p)
                ENGINE.set_auto_advance_to_day(True)
                ENGINE.start_game()
                # Spin up a separate-process mirror of the engine. The
                # in-process ENGINE remains the source of truth for HTTP
                # endpoints; the subprocess is a side-channel that
                # demonstrates the engine running in its own OS process
                # and is the foundation for a future proxy-based
                # architecture (see engine/runner.py).
                spawn_engine_runner_subprocess()
                # Auto-start the first night so the storyteller sees
                # the wake-up prompts immediately. The engine's night
                # thread blocks on send_prompt for each step in the
                # preset sheet.
                try:
                    ENGINE.start_night()
                except Exception as exc:  # pragma: no cover
                    print(f"[ui] auto-start_night failed: {exc!r}",
                          file=sys.stderr)
                self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/reset":
            # Tear down the running game and return to the town square.
            #
            # Steps:
            #   1. Kill the engine runner subprocess so it stops
            #      generating prompts on stderr and releases resources.
            #   2. Snapshot the existing chair layout (positions, names,
            #      typed-in characters) — chairs now live on the engine
            #      itself, so a fresh Engine() would otherwise wipe them.
            #   3. Replace the in-process engine with a fresh instance,
            #      then restore the snapshotted chair layout (with all
            #      ``player_id`` bindings cleared, since the old engine's
            #      Player ids no longer exist).
            #   4. POOL / SELECTED_PRESET still live on the UI side;
            #      they're untouched so the storyteller doesn't have to
            #      re-pick the bag after a reset.
            killed = kill_engine_runner_subprocess()
            saved_chairs = ENGINE.chairs.list()
            saved_storyteller = ENGINE.chairs.get_storyteller()
            saved_pool = ENGINE.pool.list()
            saved_drunk_fake = ENGINE.pool.drunk_fake()
            saved_ft_rh = ENGINE.pool.ft_red_herring()
            saved_ww_tf = ENGINE.pool.washerwoman_townsfolk()
            saved_ww_wrong = ENGINE.pool.washerwoman_wrong()
            saved_preset = ENGINE.selected_preset_name
            new_engine = Engine(default_seats=0)
            for chair in saved_chairs:
                new_chair = new_engine.chairs.add()
                new_engine.chairs.update(
                    new_chair["id"],
                    x=chair["x"], y=chair["y"],
                    name=chair["name"], character=chair["character"],
                    clear_player_id=True,
                )
            new_engine.chairs.move_storyteller(
                saved_storyteller["x"], saved_storyteller["y"],
            )
            new_engine.pool.set_many(saved_pool)
            try:
                if saved_drunk_fake:
                    new_engine.pool.set_drunk_fake(saved_drunk_fake)
                if saved_ft_rh:
                    new_engine.pool.set_ft_red_herring(saved_ft_rh)
                if saved_ww_tf:
                    new_engine.pool.set_washerwoman_townsfolk(saved_ww_tf)
                if saved_ww_wrong:
                    new_engine.pool.set_washerwoman_wrong(saved_ww_wrong)
            except ValueError:
                # If a saved pick happens to be invalid in the new
                # context (e.g. the role was removed earlier in this
                # session), let auto-fill repopulate it via set_many.
                pass
            new_engine.selected_preset_name = saved_preset
            ENGINE_replace(new_engine)
            self._send_json(HTTPStatus.OK, {
                "engine": ENGINE.snapshot(),
                "subprocess_killed": killed,
            })
            return

        if path == "/api/engine/start_night":
            try:
                ENGINE.start_night()
                self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/advance_to_day":
            try:
                deaths = ENGINE.advance_to_day()
                self._send_json(HTTPStatus.OK, {
                    "engine": ENGINE.snapshot(),
                    "deaths": [p.id for p in deaths],
                })
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/advance_to_night":
            try:
                ENGINE.advance_to_night()
                self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/respond":
            ok, data = self._read_json()
            if not ok or "prompt_id" not in data or "response" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "need prompt_id and response"})
                return
            accepted = ENGINE.respond(int(data["prompt_id"]), data["response"])
            self._send_json(HTTPStatus.OK, {
                "accepted": accepted,
                "engine": ENGINE.snapshot(),
            })
            return

        if path == "/api/engine/back":
            # Pop the most recent post-ability checkpoint and restore
            # it. If a night thread is running, it is interrupted and
            # re-launched at the same step so the Storyteller can redo
            # whichever ability they were on.
            try:
                restored = ENGINE.back()
                self._send_json(HTTPStatus.OK, {
                    "restored": bool(restored),
                    "engine": ENGINE.snapshot(),
                })
            except Exception as exc:  # pragma: no cover (defensive)
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/save_state":
            # Return the engine's current state as an opaque blob the
            # caller can later POST to /api/engine/load_state.
            try:
                blob = ENGINE.save_state()
                self._send_json(HTTPStatus.OK, {"state": blob})
            except Exception as exc:  # pragma: no cover (defensive)
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/load_state":
            ok, data = self._read_json()
            if not ok or "state" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "need state blob"})
                return
            try:
                ENGINE.load_state(str(data["state"]))
                self._send_json(HTTPStatus.OK, {"engine": ENGINE.snapshot()})
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/kill":
            ok, data = self._read_json()
            if not ok or "player_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need player_id"})
                return
            cause = DeathCause(data.get("cause", DeathCause.STORYTELLER.value))
            ENGINE.kill(int(data["player_id"]), cause)
            self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            return

        if path == "/api/engine/execute":
            ok, data = self._read_json()
            if not ok or "player_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need player_id"})
                return
            try:
                ENGINE.execute_player(int(data["player_id"]))
                self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/poison":
            ok, data = self._read_json()
            if not ok or "player_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need player_id"})
                return
            ENGINE.poison(int(data["player_id"]))
            self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            return

        if path == "/api/engine/end_game":
            ok, data = self._read_json()
            alignment = Alignment(data.get("winner", "good"))
            reason = data.get("reason", "Storyteller declared.")
            ENGINE._end_game(alignment, reason)  # internal helper
            self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            return

        # ---- Per-player day actions (side panel) ------------------------
        # These four endpoints back the per-seat side panel that opens
        # when the Storyteller clicks a player circle once the game has
        # started. See ui/README.md "Player side panel".
        if path == "/api/engine/nominate":
            ok, data = self._read_json()
            if (not ok
                or "nominator_id" not in data
                or "nominee_id" not in data):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "need nominator_id and nominee_id"})
                return
            try:
                ENGINE.nominate(
                    int(data["nominator_id"]),
                    int(data["nominee_id"]),
                )
                self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/vote":
            ok, data = self._read_json()
            if not ok or "player_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need player_id"})
                return
            try:
                ENGINE.record_vote(int(data["player_id"]))
                self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/engine/use_ability":
            ok, data = self._read_json()
            if not ok or "player_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need player_id"})
                return
            try:
                ENGINE.use_daytime_ability(int(data["player_id"]))
                self._send_json(HTTPStatus.OK, ENGINE.snapshot())
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._gate():
            return

        path = self.path.split("?", 1)[0]

        if path == "/api/character_pool/drunk_fake":
            ok, data = self._read_json()
            if not ok or "role" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need role"})
                return
            role = data.get("role")
            if role is not None and not isinstance(role, str):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "role must be a string or null"})
                return
            if role is not None:
                spec = script_data.SCRIPT_BY_NAME.get(role)
                if spec is None:
                    self._send_json(HTTPStatus.BAD_REQUEST,
                                    {"error": f"unknown character {role!r}"})
                    return
                if spec.char_type is not CharType.TOWNSFOLK:
                    self._send_json(HTTPStatus.BAD_REQUEST,
                                    {"error": "Drunk's pretend role must be a Townsfolk"})
                    return
            try:
                POOL.set_drunk_fake(role)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
            return

        if path == "/api/character_pool/ft_red_herring":
            ok, data = self._read_json()
            if not ok or "role" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need role"})
                return
            role = data.get("role")
            if role is not None and not isinstance(role, str):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "role must be a string or null"})
                return
            try:
                POOL.set_ft_red_herring(role)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
            return

        if self.path == "/api/storyteller":
            ok, data = self._read_json()
            if not ok or "x" not in data or "y" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need x and y"})
                return
            self._send_json(HTTPStatus.OK, STORE.move_storyteller(data["x"], data["y"]))
            return

        m = CHAIR_ID_RE.match(self.path)
        if not m:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        cid = int(m.group(1))
        ok, data = self._read_json()
        if not ok:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return
        if not any(k in data for k in ("x", "y", "name", "character")):
            self._send_json(HTTPStatus.BAD_REQUEST,
                            {"error": "need at least one of x, y, name, character"})
            return
        chair = STORE.update(
            cid,
            x=data.get("x"),
            y=data.get("y"),
            name=data.get("name"),
            character=data.get("character"),
        )
        if chair is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such chair"})
            return
        self._send_json(HTTPStatus.OK, chair)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._gate():
            return

        path = self.path.split("?", 1)[0]
        if path.startswith("/api/character_pool/"):
            name = urllib.parse.unquote(path[len("/api/character_pool/"):]).rstrip("/")
            if not name:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need a name"})
                return
            removed = POOL.remove(name)
            if not removed:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not in pool"})
                return
            self._send_json(HTTPStatus.OK,
                            {"removed": name, **_character_pool_snapshot()})
            return

        m = CHAIR_ID_RE.match(self.path)
        if not m:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        cid = int(m.group(1))
        if STORE.remove(cid):
            self._send_json(HTTPStatus.OK, {"removed": cid})
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such chair"})

    # ---- /enter (login page) ----

    def _serve_enter_page(self, *, error: bool = False) -> None:
        qs = urllib.parse.urlparse(self.path).query
        params = dict(urllib.parse.parse_qsl(qs))
        next_url = params.get("next", "/")
        if ACCESS_CODE is None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", _safe_next(next_url))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            with open(os.path.join(STATIC_DIR, "enter.html"), "rb") as f:
                template = f.read().decode("utf-8")
        except OSError:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        html = (template
                .replace("{{next}}", _html_escape(_safe_next(next_url)))
                .replace("{{error_style}}", "" if error else "display:none"))
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_enter_submit(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        form = dict(urllib.parse.parse_qsl(raw))
        code = (form.get("code") or "").strip()
        next_url = _safe_next(form.get("next") or "/")
        if ACCESS_CODE is None or code == ACCESS_CODE:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", next_url)
            if ACCESS_CODE is not None:
                self.send_header("Set-Cookie", self._set_access_cookie(ACCESS_CODE))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        time.sleep(0.5)
        self.path = "/enter?next=" + urllib.parse.quote(next_url, safe="/?=&%")
        self._serve_enter_page(error=True)

    # ---- engine / chair sync ----

    def _sync_chairs_to_engine(self) -> None:
        """Materialize the chair layout into engine players.

        Called when the storyteller clicks "Start Game". Iterates the
        chairs in chair-id order (which the chair store keeps compacted
        to 1..N through every add/remove), creating one engine Player
        per non-empty-named chair. Engine players are assigned ids in
        the same chair-id order so ``player.id == chair.id`` — there is
        a single numbering system shared between the chair store, the
        UI, and the engine.
        """
        # Only valid before start_game; otherwise just bail (engine
        # rejects setup mutations anyway).
        ordered = sorted(STORE.list(), key=lambda c: c["id"])
        for chair in ordered:
            name = (chair.get("name") or "").strip()
            character_name = (chair.get("character") or "").strip()
            if not name:
                continue
            existing_pid = chair.get("player_id")
            try:
                if existing_pid is not None:
                    player = ENGINE.get_player(existing_pid)
                    player.name = name
                    pid = existing_pid
                else:
                    player = ENGINE.add_seat(name)
                    pid = player.id
                    STORE.update(chair["id"], player_id=pid)
            except Exception:
                # Engine rejected the seat add (probably already started).
                continue
            if character_name and character_name in script_data.SCRIPT_BY_NAME:
                ENGINE.assign_character(pid, character_name)


# ---------------------------------------------------------------------------
# Misc helpers.
# ---------------------------------------------------------------------------


def _character_pool_snapshot() -> dict:
    """Build a snapshot of the character pool with helpful derived data.

    Includes:
      * ``names`` — the pool as a list of character names (insertion order).
      * ``counts`` — current counts per CharType (townsfolk/outsider/...).
      * ``recommended`` — recommended counts for the current chair count.
      * ``recommended_adjusted`` — recommended counts adjusted by any
        in-pool setup deltas (e.g. Baron: +2 outsiders / -2 townsfolk).
      * ``needed`` — adjusted_recommended - counts (positive = still need).
      * ``total_needed`` — sum of positive ``needed`` entries (the
        "X to add" badge on the storyteller UI).
      * ``chair_count`` — number of chairs currently on the board.
    """
    names = POOL.list()
    chair_count = len(STORE.list())

    # Per-type counts of the current pool.
    counts = {ct.value: 0 for ct in CharType}
    townsfolk_delta = 0
    outsider_delta = 0
    for n in names:
        spec = script_data.SCRIPT_BY_NAME.get(n)
        if spec is None:
            continue
        counts[spec.char_type.value] += 1
        townsfolk_delta += spec.setup_townsfolk_delta
        outsider_delta += spec.setup_outsider_delta

    # Recommended counts. We only have setup tables for >=5 players;
    # below that we fall back to zeros so the UI stays sensible.
    try:
        rec_t, rec_o, rec_m, rec_d = script_data.recommended_counts(chair_count)
        has_recommendation = True
    except (ValueError, KeyError):
        rec_t, rec_o, rec_m, rec_d = 0, 0, 0, 0
        has_recommendation = False

    recommended = {
        "townsfolk": rec_t,
        "outsider": rec_o,
        "minion": rec_m,
        "demon": rec_d,
    }
    adjusted = {
        "townsfolk": max(0, rec_t + townsfolk_delta),
        "outsider": max(0, rec_o + outsider_delta),
        "minion": rec_m,
        "demon": rec_d,
    }
    needed = {k: adjusted[k] - counts.get(k, 0) for k in adjusted}
    total_needed = sum(v for v in needed.values() if v > 0)

    drunk_fake = POOL.drunk_fake()
    ft_red_herring = POOL.ft_red_herring()
    ww_townsfolk = POOL.washerwoman_townsfolk()
    ww_wrong = POOL.washerwoman_wrong()
    librarian_outsider = POOL.librarian_outsider()
    librarian_wrong = POOL.librarian_wrong()
    investigator_minion = POOL.investigator_minion()
    investigator_wrong = POOL.investigator_wrong()

    # Live engine state for in-game reminder tokens. The chair UI
    # keys off ``character == <X>_role`` to render a token next to
    # the carrying chair, so we report the *role name* of whichever
    # player currently carries each token. ``None`` means no chair
    # carries it (or the engine has not been started yet).
    #
    # IMPORTANT: token application is gated on the *source player's*
    # state, not just on what the character object stored. By the
    # rulebook a drunk or poisoned source goes through the motions
    # but their ability has no effect, so no reminder token is
    # placed. We model that here by requiring
    # ``source.player.has_ability`` (== alive AND sober AND healthy)
    # before reporting the token. Same gate fires when the source
    # later dies or becomes drunk/poisoned: the snapshot recomputes
    # on every poll, so the badge disappears as soon as the Player
    # state changes.
    # All grimoire reminder tokens are tracked PER-SEAT (player id),
    # not per-role-name. Keying by role name is wrong as soon as a
    # player's role changes mid-game (Scarlet Woman -> Imp via
    # change_character): the new role's chair would otherwise
    # accidentally match a token that referred to the *old* role name
    # — e.g. the dead old-Imp leaving DEAD on imp_dead_roles=["Imp"]
    # and the new-Imp's chair (chair.character == "Imp" after
    # change_character) inheriting that DEAD token. Per-seat keying
    # makes the token follow the player, so role swaps don't leak
    # tokens onto the wrong chair.
    poisoned_player_id: Optional[int] = None
    butler_master_player_id: Optional[int] = None
    monk_safe_player_id: Optional[int] = None
    undertaker_died_today_player_id: Optional[int] = None
    slayer_no_ability_player_ids: list = []
    virgin_no_ability_player_ids: list = []
    try:
        engine_players = ENGINE.players
    except Exception:
        engine_players = []
    for p in engine_players:
        char = getattr(p, "character", None)
        if char is None:
            continue
        # Gate sourced-by-the-source-player tokens on the source's
        # ``has_ability`` (== alive AND sober AND healthy). Tokens
        # whose presence is *spent / persistent* (NO ABILITY tokens
        # for once-per-game roles, Undertaker's DIED TODAY which
        # tracks the executed seat regardless of Undertaker state)
        # are derived below outside this gate.
        if not p.has_ability:
            continue
        if char.name == "Poisoner":
            target = getattr(char, "_last_target", None)
            if (target is not None
                    and getattr(target, "character", None) is not None
                    and target.poisoned):
                poisoned_player_id = target.id
        elif char.name == "Butler":
            master = getattr(char, "_master", None)
            if (master is not None
                    and getattr(master, "character", None) is not None):
                butler_master_player_id = master.id
        elif char.name == "Monk":
            # The Monk's chosen target is the player whose
            # ``protected_from_demon`` flag is set. The Monk's ability
            # only sets the flag when the Monk has ability, so a
            # drunk/poisoned Monk never has a real SAFE chair (the
            # outer ``if not p.has_ability`` gate already filters that
            # case out, but be defensive).
            target = getattr(char, "_target", None)
            if (target is not None
                    and target.alive
                    and getattr(target, "protected_from_demon", False)
                    and getattr(target, "character", None) is not None):
                monk_safe_player_id = target.id

    # Once-per-game / spent-ability tokens. These persist regardless of
    # the source player's current state (a dead Slayer who already
    # slew still needs the NO ABILITY token to remind the storyteller
    # they can't slay again on revive). Walk every seated player and
    # check the per-character "spent" flag.
    for p in engine_players:
        char = getattr(p, "character", None)
        if char is None:
            continue
        if char.name == "Slayer" and getattr(char, "_used", False):
            slayer_no_ability_player_ids.append(p.id)
        elif char.name == "Virgin" and getattr(char, "_triggered", False):
            virgin_no_ability_player_ids.append(p.id)
        elif char.name == "Undertaker":
            # Undertaker's "DIED TODAY" reminder marks the seat of the
            # player executed today. The Undertaker character resets
            # ``_last_executed`` on DAY_START, so this naturally
            # disappears the next morning.
            executed = getattr(char, "_last_executed", None)
            if (executed is not None
                    and getattr(executed, "character", None) is not None):
                undertaker_died_today_player_id = executed.id

    # Imp's DEAD reminder tokens. Per the rulebook, the DEAD reminder
    # is placed on every player the Imp's nightly ability actually
    # killed AND is removed at the end of the night. We read the
    # transient ``Engine._demon_killed_player_ids`` list — populated
    # by ``Engine.kill`` when a DEMON_KILL lands and cleared by
    # ``advance_to_day`` / ``_auto_dawn``. (Mayor-redirected kills
    # still land with cause=DEMON_KILL on the new target so they
    # show up automatically; a Soldier/Monk-protected pick that
    # never killed anyone does NOT.) Tracked by player id so a *new*
    # demon (Scarlet Woman promoted to Imp; chair.character now
    # equals "Imp") doesn't spuriously inherit the dead old demon's
    # DEAD token.
    imp_dead_player_ids: list = list(
        getattr(ENGINE, "_demon_killed_player_ids", []) or []
    )

    # Scarlet Woman -> Demon promotion: the engine appends to
    # ``_sw_promoted_player_ids`` from inside the SW reaction. The
    # grimoire shows the "Scarlet Woman IS THE DEMON" reminder token
    # on each promoted seat. After promotion, ``Engine.change_character``
    # also rewrites ``chair.character`` to the new demon class (so
    # the player circle shows "Imp" rather than the stale "Scarlet
    # Woman" — single source of truth between engine and chair). The
    # reminder token is therefore gated on the per-seat list, NOT on
    # ``chair.character == "Scarlet Woman"``: that string is no
    # longer there once the swap has happened.
    sw_promoted_ids: list = list(getattr(ENGINE, "_sw_promoted_player_ids", []) or [])
    scarlet_woman_is_demon_flag = bool(sw_promoted_ids)

    return {
        "names": names,
        "counts": counts,
        "recommended": recommended,
        "recommended_adjusted": adjusted,
        "needed": needed,
        "total_needed": total_needed,
        "chair_count": chair_count,
        "has_recommendation": has_recommendation,
        "drunk_fake_role": drunk_fake,
        # Implicit: the storyteller has put the Drunk in the bag but
        # hasn't yet picked which Townsfolk the Drunk thinks they are.
        # (FT and WW slots auto-fill on add, so they are only "pending"
        # in pathological cases — e.g. FT was added when the pool had
        # no other Good roles yet.)
        "pending_drunk_fake": ("Drunk" in names) and (drunk_fake is None),
        # FT's red-herring role: a good role (Townsfolk / Outsider)
        # already in the pool. Auto-picked at random when the FT is
        # added; the storyteller can re-roll by dragging the FT
        # red-herring token on the grimoire.
        "ft_red_herring_role": ft_red_herring,
        "pending_ft_red_herring": (
            ("Fortune Teller" in names) and (ft_red_herring is None)
        ),
        # Washerwoman's seen Townsfolk role: a Townsfolk already in
        # the pool. Same auto-pick + grimoire-drag flow as the FT.
        "washerwoman_townsfolk_role": ww_townsfolk,
        "pending_washerwoman_townsfolk": (
            ("Washerwoman" in names) and (ww_townsfolk is None)
        ),
        # Washerwoman's WRONG role: any in-pool role *other than* the
        # WW herself and the seen-Townsfolk slot. Auto-picked at
        # random when the WW is added; the storyteller can re-roll by
        # dragging the WRONG token on the grimoire.
        "washerwoman_wrong_role": ww_wrong,
        "pending_washerwoman_wrong": (
            ("Washerwoman" in names) and (ww_wrong is None)
        ),
        # Reminder-token slots for the rest of Trouble Brewing. These
        # mirror the tokens that exist in ``assets/tokens/`` (one PNG
        # per slot). The chair UI keys off ``character ===
        # <role>_role`` to render the matching token next to the
        # carrying chair, or off the boolean self-flags
        # (Scarlet Woman / Slayer / Virgin "no ability") for tokens
        # that always sit on the originating role's chair.
        # The engine wiring that updates these as the game proceeds
        # is intentionally separate from this snapshot — the snapshot
        # currently emits ``None``/``False`` defaults so the UI
        # render code degrades cleanly until each setter is hooked up.
        # Librarian's seen Outsider role: an Outsider already in the
        # pool. Auto-picked at random when the Librarian is added; the
        # storyteller can re-roll by dragging the Librarian token on
        # the grimoire. ``None`` means no Outsiders are in the pool —
        # the Librarian's first-night ability shows "0 Outsiders".
        "librarian_outsider_role": librarian_outsider,
        "pending_librarian_outsider": (
            ("Librarian" in names) and (librarian_outsider is None)
            # Only "pending" if there's an Outsider in the pool to pick
            # from; otherwise the empty slot is the rules-correct "0
            # Outsiders" reading and not pending at all.
            and any(
                script_data.SCRIPT_BY_NAME.get(n) is not None
                and script_data.SCRIPT_BY_NAME[n].char_type
                is CharType.OUTSIDER
                for n in names
            )
        ),
        # Librarian's WRONG role: any in-pool role *other than* the
        # Librarian herself and the seen-Outsider slot. ``None`` when
        # there's no seen-Outsider (the "0 Outsiders" reading skips
        # both reminder tokens).
        "librarian_wrong_role": librarian_wrong,
        "pending_librarian_wrong": (
            ("Librarian" in names)
            and librarian_outsider is not None
            and librarian_wrong is None
        ),
        # Investigator's seen Minion role: a Minion already in the
        # pool. Same auto-pick + grimoire-drag flow as the Librarian.
        "investigator_minion_role": investigator_minion,
        "pending_investigator_minion": (
            ("Investigator" in names) and (investigator_minion is None)
        ),
        # Investigator's WRONG role: any in-pool role *other than* the
        # Investigator herself and the seen-Minion slot.
        "investigator_wrong_role": investigator_wrong,
        "pending_investigator_wrong": (
            ("Investigator" in names)
            and investigator_minion is not None
            and investigator_wrong is None
        ),
        # Per-seat reminder tokens (player-id keyed). Every grimoire
        # reminder that says "this seat is currently the something"
        # is now a player_id (or list of player ids) so the JS can
        # render the badge against ``chair.player_id`` instead of
        # ``chair.character``. This keeps the token attached to the
        # *seat* even when ``change_character`` rewrites the chair's
        # role mid-game (Scarlet Woman -> Imp): the new role can't
        # accidentally inherit a token that referred to the old role.
        "butler_master_player_id": butler_master_player_id,
        "monk_safe_player_id": monk_safe_player_id,
        "poisoned_player_id": poisoned_player_id,
        "imp_dead_player_ids": imp_dead_player_ids,
        "undertaker_died_today_player_id": undertaker_died_today_player_id,
        "scarlet_woman_is_demon": scarlet_woman_is_demon_flag,
        # Per-seat list of player ids whose seat originated as the
        # Scarlet Woman and has since been promoted to the Demon. The
        # JS keys the "Scarlet Woman IS THE DEMON" reminder token off
        # this list (matched against ``chair.player_id``) rather than
        # against ``chair.character``, since the chair character is
        # rewritten to the new demon class on promotion.
        "scarlet_woman_promoted_player_ids": list(sw_promoted_ids),
        "slayer_no_ability_player_ids": slayer_no_ability_player_ids,
        "virgin_no_ability_player_ids": virgin_no_ability_player_ids,
    }


def _safe_next(url: str) -> str:
    if not url or not url.startswith("/") or url.startswith("//"):
        return "/"
    return url


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _guess_content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def serve(
    engine: "Engine",
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    access_code: Optional[str] = None,
) -> None:
    """Start the HTTP server, binding ``engine`` as the source of truth.

    ``botc.py`` (the top-level entry point) builds the engine first and
    then calls this. The legacy ``python3 -m ui.ui`` path goes through
    :func:`main`, which builds a default engine and forwards here.
    """
    global ACCESS_CODE, SERVER_PORT
    ENGINE_replace(engine)
    ACCESS_CODE = access_code
    SERVER_PORT = port

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"BotC server listening on http://{host}:{port}")
    if ACCESS_CODE is None:
        print("  (no access code required — anyone on the network can connect)")
    else:
        print(f"  Access code: {ACCESS_CODE}")
        print( "  Players should visit  <your-url>/phone  and enter that code.")
        print( "  Requests from localhost bypass the code.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


def main() -> None:
    """Legacy entry point: ``python3 -m ui.ui``.

    Builds a default engine and calls :func:`serve`. New code should use
    ``python3 botc.py`` (see ``botc/botc.py``), which constructs the
    engine explicitly before handing it to the UI.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0",
                        help="Interface to bind (default: all interfaces).")
    parser.add_argument("--port", type=int, default=8000,
                        help="TCP port (default: 8000).")
    parser.add_argument(
        "--access-code", nargs="?", const="__AUTO__", default=None,
        metavar="CODE",
        help="Require an access code to visit the site.",
    )
    args = parser.parse_args()

    code: Optional[str] = None
    if args.access_code == "__AUTO__":
        code = _make_random_code()
    elif args.access_code is not None:
        code = args.access_code

    serve(Engine(), host=args.host, port=args.port, access_code=code)


if __name__ == "__main__":
    main()
