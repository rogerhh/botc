"""HTTP-driven smoke test.

Boots the ui.ui server on an ephemeral port and walks setup + the
first night through actual HTTP calls, the way the storyteller's
browser will.
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


def get(port: int, path: str):
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3)
    return r.status, json.loads(r.read())


def post(port: int, path: str, payload: dict | None = None):
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=3)
    return r.status, json.loads(r.read())


def put(port: int, path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=3)
    return r.status, json.loads(r.read())


def wait_for_prompt(port: int, character: str, step: str, timeout: float = 3.0):
    """Poll /api/prompt until a matching prompt is offered. Return the prompt dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, data = get(port, "/api/prompt")
        p = data.get("prompt")
        if p and p["meta"].get("character") == character and p["meta"].get("step") == step:
            return p
        time.sleep(0.02)
    raise TimeoutError(f"Timed out waiting for {character}/{step} prompt")


def respond(port: int, prompt_id: int, response):
    return post(port, "/api/engine/respond", {"prompt_id": prompt_id, "response": response})


def main() -> None:
    # Re-create a fresh chair store + engine so a previous run's state
    # doesn't leak.
    ui.STORE = ui.ChairStore()
    ui.ENGINE = ui.__dict__["Engine"]() if "Engine" in ui.__dict__ else __import__("engine.engine", fromlist=["Engine"]).Engine()
    # The above is awkward; just set fresh objects directly.
    from engine.engine import Engine
    ui.ENGINE = Engine()
    ui.STORE = ui.ChairStore()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    ui.SERVER_PORT = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]

    try:
        # Wipe the seeded chairs and add 5 fresh ones at predictable angles.
        # (We can't easily DELETE seeded chairs in bulk, so just configure them.)
        chairs = sorted(ui.STORE.list(), key=lambda c: c["id"])[:5]
        # Position them in a clockwise ring, names + characters via PUT.
        names = ["Alice", "Bob", "Cara", "Dan", "Eve"]
        chars = ["Washerwoman", "Ravenkeeper", "Soldier", "Poisoner", "Imp"]
        # Already seeded ring is clockwise; reuse first 5.
        for c, n, ch in zip(chairs, names, chars):
            put(port, f"/api/chairs/{c['id']}", {"name": n, "character": ch})
        # Remove the extras (chair 6, 7, 8) so the engine sees exactly 5 seats.
        for cid in (6, 7, 8):
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/chairs/{cid}",
                    method="DELETE",
                )
                urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError:
                pass

        s, _ = post(port, "/api/engine/start_game")
        assert s == 200, f"start_game failed: {s}"
        # Verify engine is in FIRST_NIGHT. start_game now auto-starts
        # the night thread, so we don't call /api/engine/start_night
        # explicitly.
        _, snap = get(port, "/api/engine")
        assert snap["phase"] == "first_night", snap
        assert len(snap["players"]) == 5, snap

        # Figure out which engine player IDs got assigned to which named
        # players. _sync_chairs_to_engine walks the chairs in clockwise
        # order from 12 o'clock — and the seeded ring starts at the
        # lower-left, so Eve actually gets player_id=1, Alice gets 2,
        # etc. We look up the IDs from the snapshot rather than guessing.
        id_by_name = {p["name"]: p["id"] for p in snap["players"]}
        alice_id = id_by_name["Alice"]
        bob_id = id_by_name["Bob"]
        cara_id = id_by_name["Cara"]

        # Walk through the night via prompts.
        # Poisoner picks Alice (wake-up no longer fires a separate
        # storyteller prompt; the panel shows it as part of the next
        # ability prompt).
        p = wait_for_prompt(port, "Poisoner", "select_player")
        respond(port, p["id"], alice_id)

        # Pick a townsfolk character
        p = wait_for_prompt(port, "Washerwoman", "select_character")
        # Verify eligible_characters present
        assert "Ravenkeeper" in p["eligible_characters"], p["eligible_characters"]
        respond(port, p["id"], "Ravenkeeper")
        # Pick two players: Bob + Cara
        p = wait_for_prompt(port, "Washerwoman", "select_players")
        assert p["count"] == 2
        respond(port, p["id"], [bob_id, cara_id])
        # Information prompt
        p = wait_for_prompt(port, "Washerwoman", "information")
        assert "Bob" in p["text"] and "Cara" in p["text"], p["text"]
        respond(port, p["id"], None)

        # The engine auto-advances to day once the night sheet finishes,
        # so we just poll until the phase flips. (No manual
        # /api/engine/advance_to_day call.)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            _, snap = get(port, "/api/engine")
            if snap["phase"] == "day":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                f"engine did not auto-advance to day; "
                f"phase is still {snap['phase']!r}"
            )

        # Verify Alice is poisoned.
        alice = next(p for p in snap["players"] if p["id"] == alice_id)
        assert alice["poisoned"] is True, alice

    finally:
        srv.shutdown()
    print("HTTP smoke test PASSED.")


if __name__ == "__main__":
    main()
