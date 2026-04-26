"""Reset Game endpoint smoke test.

Boots the UI server on an ephemeral port, starts a 5-player game,
verifies the engine is in FIRST_NIGHT and the runner subprocess is
running, then POSTs /api/engine/reset and verifies:

  * engine phase flips back to setup
  * the engine has zero players (state was wiped)
  * the runner subprocess was killed
  * chair layout / chair names / chair characters are preserved
  * a subsequent /api/engine/start_game re-creates engine players
    from the still-intact chair list
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import ThreadingHTTPServer

from ui import ui


def get(port, path):
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3)
    return r.status, json.loads(r.read())


def post(port, path, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=3)
    return r.status, json.loads(r.read())


def put(port, path, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=3)
    return r.status, json.loads(r.read())


def main() -> None:
    # Fresh state.
    from engine.engine import Engine
    ui.ENGINE = Engine()
    ui.STORE = ui.ChairStore()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    ui.SERVER_PORT = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]

    try:
        # Configure 5 chairs with names + characters; delete the rest.
        chairs = sorted(ui.STORE.list(), key=lambda c: c["id"])[:5]
        names = ["Alice", "Bob", "Cara", "Dan", "Eve"]
        chars = ["Washerwoman", "Ravenkeeper", "Soldier", "Poisoner", "Imp"]
        for c, n, ch in zip(chairs, names, chars):
            put(port, f"/api/chairs/{c['id']}", {"name": n, "character": ch})
        for cid in (6, 7, 8):
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/chairs/{cid}",
                    method="DELETE",
                )
                urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError:
                pass

        # Start the game; verify it's running.
        s, _ = post(port, "/api/engine/start_game")
        assert s == 200
        _, snap = get(port, "/api/engine")
        assert snap["phase"] == "first_night", snap
        assert len(snap["players"]) == 5, snap

        # Subprocess should be alive.
        assert ui.ENGINE_PROCESS is not None, "expected runner subprocess"
        assert ui.ENGINE_PROCESS.poll() is None, "subprocess should be alive"

        # Reset the game.
        s, body = post(port, "/api/engine/reset")
        assert s == 200, body
        assert body["engine"]["phase"] == "setup", body
        assert body["engine"]["players"] == [], body
        assert body["subprocess_killed"] is True, body

        # The runner subprocess should be cleaned up.
        assert ui.ENGINE_PROCESS is None, "runner ref should be cleared"

        # Chairs are preserved (still 6 chairs with names/characters
        # on the first 5).
        _, state = get(port, "/api/state")
        assert len(state["chairs"]) == 6, state["chairs"]
        named_chairs = [c for c in state["chairs"] if c["name"]]
        assert len(named_chairs) == 5, named_chairs
        # Each named chair's player_id was cleared by reset.
        for chair in named_chairs:
            assert chair["player_id"] is None, chair

        # A subsequent Start Game succeeds and reseats everyone.
        s, _ = post(port, "/api/engine/start_game")
        assert s == 200
        _, snap = get(port, "/api/engine")
        assert snap["phase"] == "first_night", snap
        assert len(snap["players"]) == 5, snap

        # And reset again, confirming the cycle is repeatable.
        s, body = post(port, "/api/engine/reset")
        assert s == 200, body
        assert body["engine"]["phase"] == "setup", body

    finally:
        # Clean up any lingering subprocess so the test process exits.
        ui.kill_engine_runner_subprocess()
        srv.shutdown()
    print("Reset Game test PASSED.")


if __name__ == "__main__":
    main()
