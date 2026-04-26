"""Blood on the Clocktower — Storyteller GUI server.

A tiny HTTP server (stdlib only) that serves a single-page browser GUI
for arranging the "town square". The GUI shows a square playing area
containing circles (chairs). The Storyteller can:

    * add a chair
    * remove a chair
    * drag any chair to a new position

The authoritative chair state lives on the server so multiple browsers
(Storyteller laptop, phone display) see the same layout.

Run:
    python3 server/server.py [--host 0.0.0.0] [--port 8000]
                             [--access-code [CODE]]

Then open http://localhost:8000 in a browser.

Exposing the server on the public internet
------------------------------------------
Combine an outbound tunnel with ``--access-code`` so only people you've
told the code to can view the game. Two easy tunnel options:

    cloudflared tunnel --url http://localhost:8000
    ngrok http 8000

Either gives back a public ``https://…`` URL. Share ``<URL>/phone``
and the access code with the players.

The board is a unit square; each chair position is stored as two floats
in [0, 1] so the layout is independent of the actual pixel size of any
given client's viewport.
"""

from __future__ import annotations

import argparse
import http.cookies
import itertools
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")


# ---------------------------------------------------------------------------
# In-memory chair state. Thread-safe via a single coarse lock.
# ---------------------------------------------------------------------------


class ChairStore:
    """Holds the current chair arrangement.

    Each chair is a dict::

        {"id": int, "x": float, "y": float, "name": str, "character": str}

    ``x`` and ``y`` are fractions in [0, 1] relative to the board.
    ``name`` is the seated player's display name and ``character`` is
    their assigned character (both free-form strings set by the
    Storyteller).
    """

    def __init__(self) -> None:
        self._chairs: Dict[int, Dict[str, float]] = {}
        self._next_id = itertools.count(1)
        self._lock = threading.Lock()
        # The single Storyteller marker. Sized as a fraction of the
        # board. The default position sits at the bottom-center of the
        # would-be full circle so the chairs (a semi-circle along the
        # top half) and the storyteller together complete the ring.
        self._storyteller: Dict[str, float] = {
            "x": 0.5,
            "y": 0.88,
            "w": 0.14,
            "h": 0.08,
        }
        # Seed with a semi-circle of 8 chairs so the first page load
        # isn't empty.
        self._seed_default_ring(count=8)

    def _seed_default_ring(self, count: int) -> None:
        import math

        cx, cy, r = 0.5, 0.5, 0.38
        # Spread chairs evenly along a 270-degree arc that sits over
        # the top half of the board, leaving the bottom 90 degrees
        # open for the storyteller (bottom-center, see __init__) to
        # close the ring. The arc runs clockwise (in screen coords,
        # where +y is down) from the lower-left at theta = 3pi/4,
        # through the top at theta = 3pi/2, around to the lower-right
        # at theta = pi/4 + 2pi. Endpoints are included so the outer
        # chairs sit flush with where the storyteller's gap begins.
        arc = 1.5 * math.pi  # 270 degrees
        start = 0.75 * math.pi  # lower-left
        for i in range(count):
            if count > 1:
                theta = start + arc * i / (count - 1)
            else:
                theta = start + arc / 2
            x = cx + r * math.cos(theta)
            y = cy + r * math.sin(theta)
            cid = next(self._next_id)
            self._chairs[cid] = {
                "id": cid, "x": x, "y": y,
                "name": "", "character": "",
            }

    # --- reads ---
    def list(self) -> list:
        with self._lock:
            return sorted(self._chairs.values(), key=lambda c: c["id"])

    # --- writes ---
    def add(self, x: Optional[float] = None, y: Optional[float] = None) -> dict:
        with self._lock:
            cid = next(self._next_id)
            # Default: drop the new chair in the middle, nudged so
            # multiple rapid adds don't overlap exactly.
            if x is None or y is None:
                offset = (cid % 8) * 0.02
                x = 0.5 + offset - 0.08
                y = 0.5 + offset - 0.08
            chair = {
                "id": cid,
                "x": _clamp01(x), "y": _clamp01(y),
                "name": "", "character": "",
            }
            self._chairs[cid] = chair
            return chair

    def update(
        self,
        cid: int,
        x: Optional[float] = None,
        y: Optional[float] = None,
        name: Optional[str] = None,
        character: Optional[str] = None,
    ) -> Optional[dict]:
        """Partial-update a chair. Unspecified fields are left unchanged."""
        with self._lock:
            chair = self._chairs.get(cid)
            if chair is None:
                return None
            if x is not None:
                chair["x"] = _clamp01(x)
            if y is not None:
                chair["y"] = _clamp01(y)
            if name is not None:
                chair["name"] = str(name)[:64]
            if character is not None:
                chair["character"] = str(character)[:64]
            return chair

    def remove(self, cid: int) -> bool:
        with self._lock:
            return self._chairs.pop(cid, None) is not None

    def remove_last(self) -> Optional[int]:
        """Pop the highest-id chair; convenience for a "Remove Chair" button."""
        with self._lock:
            if not self._chairs:
                return None
            cid = max(self._chairs)
            del self._chairs[cid]
            return cid

    # --- storyteller ---
    def get_storyteller(self) -> dict:
        with self._lock:
            return dict(self._storyteller)

    def move_storyteller(self, x: float, y: float) -> dict:
        with self._lock:
            self._storyteller["x"] = _clamp01(x)
            self._storyteller["y"] = _clamp01(y)
            return dict(self._storyteller)


def _clamp01(v: float) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.5
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


STORE = ChairStore()


# ---------------------------------------------------------------------------
# Access-code gate.
#
# When ACCESS_CODE is not None, every request must present the code as
# either a cookie (``botc_access=<code>``) or a query param
# (``?code=<code>``), with one exception: requests originating from
# localhost are always trusted (the Storyteller's own machine).
# ---------------------------------------------------------------------------

ACCESS_CODE: Optional[str] = None
COOKIE_NAME = "botc_access"
# Paths that are always reachable without the cookie.
OPEN_PATHS = ("/enter", "/enter/", "/static/")

# Set by main() so /api/host_info can hand the port back to the browser.
SERVER_PORT: Optional[int] = None


def _make_random_code() -> str:
    """Generate a 6-character code with no easily-confused glyphs."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
    return "".join(secrets.choice(alphabet) for _ in range(6))


# ---------------------------------------------------------------------------
# LAN IP detection.
#
# The Storyteller's laptop usually has several IPs (wifi, ethernet, VPN,
# docker bridges). We want the one a phone on the same wifi network can
# reach. We detect candidates by shelling out to ``ifconfig`` (or ``ip
# addr`` as a fallback) and then rank them by how LAN-ish they look.
# The UDP-socket trick is a final fallback when neither tool is
# available (rare on Linux/macOS; on Windows we'd need ``ipconfig`` —
# out of scope for now).
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
    # The default docker bridge is 172.17.0.0/16. A laptop using docker
    # often has this plus its real wifi IP; prefer the latter.
    return ip.startswith("172.17.") or ip.startswith("172.18.")


def _rank_ip(ip: str) -> tuple:
    # Lower is better. RFC1918 non-docker wins, then other LAN-ish
    # addresses, then anything else.
    return (
        0 if _is_rfc1918(ip) and not _looks_dockerish(ip) else
        1 if _is_rfc1918(ip) else
        2,
        ip,
    )


def _detect_lan_ips() -> list:
    """Best-effort list of this host's reachable IPv4 addresses, best
    candidate first."""
    # 1) ifconfig  (macOS, Linux with net-tools, many BSDs)
    # 2) ip addr   (modern Linux)
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

    # 3) UDP-connect trick: the OS picks the outbound interface's IP.
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


class Handler(BaseHTTPRequestHandler):
    # Quieter log — one line per request instead of the default two.
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        print(f"[{self.log_date_time_string()}] {self.address_string()} "
              f"{format % args}")

    # -- helpers --

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
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

    # -- auth helpers --

    def _is_localhost(self) -> bool:
        ip = self.client_address[0]
        return ip in ("127.0.0.1", "::1", "localhost")

    def _provided_code(self) -> Optional[str]:
        """Read the access code from cookie or ``?code=`` query param."""
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
        """Return True if the request may proceed. Otherwise writes a
        401 (for APIs) or 302 redirect to /enter (for everything else)
        and returns False."""
        path = self.path.split("?", 1)[0]
        if self._is_open_path(path):
            return True
        if self._authorized():
            return True

        if path.startswith("/api/"):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "access code required"})
            return False

        # Redirect browsers to the login page, preserving where they
        # wanted to go in a ``next`` query param.
        target = "/enter?next=" + urllib.parse.quote(self.path, safe="/?=&%")
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _set_access_cookie(self, code: str) -> str:
        """Build a Set-Cookie header value for the access cookie."""
        jar = http.cookies.SimpleCookie()
        jar[COOKIE_NAME] = code
        m = jar[COOKIE_NAME]
        m["path"] = "/"
        m["max-age"] = str(60 * 60 * 24 * 7)  # 1 week
        m["samesite"] = "Lax"
        # Note: no Secure flag, so cookie also works over plain LAN HTTP.
        return m.OutputString()

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

    # -- routing --

    def do_GET(self) -> None:  # noqa: N802  (stdlib API)
        path = self.path.split("?", 1)[0]

        # The login page is always reachable.
        if path in ("/enter", "/enter/"):
            self._serve_enter_page()
            return

        if not self._gate():
            return

        # If you visit a gated page with ?code=…, set the cookie and
        # strip the code from the URL so it doesn't linger in history.
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
            self._send_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            return

        # Read-only phone view. Any of /phone, /phone/, /phone.html
        # serves the same file.
        if path in ("/phone", "/phone/", "/phone.html"):
            self._send_file(os.path.join(STATIC_DIR, "phone.html"), "text/html; charset=utf-8")
            return

        if path == "/api/state":
            self._send_json(HTTPStatus.OK, {
                "chairs": STORE.list(),
                "storyteller": STORE.get_storyteller(),
            })
            return

        # Server-side info the Storyteller UI needs to build a QR code
        # that a phone on the same wifi network can reach.
        if path == "/api/host_info":
            ips = _detect_lan_ips()
            self._send_json(HTTPStatus.OK, {
                "lan_ip": ips[0] if ips else None,
                "candidates": ips,
                "port": SERVER_PORT,
            })
            return

        # Any other path under /static/ can be served directly.
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

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        # Login form POST is always reachable.
        if path in ("/enter", "/enter/"):
            self._handle_enter_submit()
            return

        if not self._gate():
            return

        if self.path == "/api/chairs":
            ok, data = self._read_json()
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            x = data.get("x")
            y = data.get("y")
            chair = STORE.add(x, y)
            self._send_json(HTTPStatus.CREATED, chair)
            return

        if self.path == "/api/chairs/remove_last":
            removed = STORE.remove_last()
            if removed is None:
                self._send_json(HTTPStatus.OK, {"removed": None})
            else:
                self._send_json(HTTPStatus.OK, {"removed": removed})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._gate():
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
        # Accept partial updates: any subset of x, y, name, character.
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

        m = CHAIR_ID_RE.match(self.path)
        if not m:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        cid = int(m.group(1))
        if STORE.remove(cid):
            self._send_json(HTTPStatus.OK, {"removed": cid})
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such chair"})

    # -- /enter (login page) --

    def _serve_enter_page(self, *, error: bool = False) -> None:
        qs = urllib.parse.urlparse(self.path).query
        params = dict(urllib.parse.parse_qsl(qs))
        next_url = params.get("next", "/")
        # If the server isn't gating access, skip straight through.
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
        # Minimal templating: replace a few placeholders. ``next`` is
        # HTML-escaped because it's inserted into a value attribute.
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
            # Success (or auth disabled) — set cookie and redirect.
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", next_url)
            if ACCESS_CODE is not None:
                self.send_header("Set-Cookie", self._set_access_cookie(ACCESS_CODE))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Failure: slow the caller down a little and re-render with an
        # inline error message.
        time.sleep(0.5)
        # Rewrite ``self.path`` so the template sees the correct ``next``.
        self.path = "/enter?next=" + urllib.parse.quote(next_url, safe="/?=&%")
        self._serve_enter_page(error=True)


def _safe_next(url: str) -> str:
    """Only allow same-site redirects, to avoid open-redirect abuse."""
    if not url or not url.startswith("/") or url.startswith("//"):
        return "/"
    return url


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


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


def main() -> None:
    global ACCESS_CODE, SERVER_PORT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0",
                        help="Interface to bind (default: all interfaces).")
    parser.add_argument("--port", type=int, default=8000,
                        help="TCP port (default: 8000).")
    parser.add_argument(
        "--access-code", nargs="?", const="__AUTO__", default=None,
        metavar="CODE",
        help="Require an access code to visit the site. Pass a specific "
             "value, or use --access-code with no argument to generate "
             "a random 6-character code. Without this flag, the server "
             "is open to anyone on the network.",
    )
    args = parser.parse_args()

    if args.access_code == "__AUTO__":
        ACCESS_CODE = _make_random_code()
    elif args.access_code is not None:
        ACCESS_CODE = args.access_code

    SERVER_PORT = args.port

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"BotC GUI server listening on http://{args.host}:{args.port}")
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


if __name__ == "__main__":
    main()
