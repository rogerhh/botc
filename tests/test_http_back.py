"""HTTP smoke test for the Back button and save_state / load_state endpoints.

Boots the same UI server that the Storyteller's browser would talk to,
then drives a real night through the JSON API and exercises:

  * ``POST /api/engine/back`` — interrupts a running ability and
    re-runs the same step so the Storyteller can redo their selections.
  * ``POST /api/engine/save_state`` / ``POST /api/engine/load_state``
    — round-trip the engine's state through opaque blobs.
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

from engine.engine import Engine
from ui import ui


def _get(port: int, path: str):
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3)
    return r.status, json.loads(r.read())


def _post(port: int, path: str, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=3)
    return r.status, json.loads(r.read())


def _put(port: int, path: str, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=3)
    return r.status, json.loads(r.read())


def _delete(port: int, path: str):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError:
        pass


def _wait_for_prompt(
    port: int,
    *,
    character: str,
    step: str,
    timeout: float = 3.0,
    skip_id: int = None,
):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, data = _get(port, "/api/prompt")
        p = data.get("prompt")
        if p is None:
            time.sleep(0.02)
            continue
        if skip_id is not None and p["id"] == skip_id:
            time.sleep(0.02)
            continue
        if (
            p["meta"].get("character") == character
            and p["meta"].get("step") == step
        ):
            return p
        time.sleep(0.02)
    raise TimeoutError(
        f"Timed out waiting for {character}/{step} prompt"
    )


def _setup_server():
    """Bring up a fresh server on an ephemeral port. Returns (srv, port)."""
    ui.ENGINE = Engine()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    ui.SERVER_PORT = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def _seat_default_table(port: int) -> dict:
    """Seat a 5-player table and start the game.

    Returns a dict mapping player names to player ids.
    """
    chairs = sorted(ui.STORE.list(), key=lambda c: c["id"])[:5]
    names = ["Alice", "Bob", "Cara", "Dan", "Eve"]
    chars = ["Washerwoman", "Ravenkeeper", "Soldier", "Poisoner", "Imp"]
    for c, n, ch in zip(chairs, names, chars):
        _put(port, f"/api/chairs/{c['id']}", {"name": n, "character": ch})
    for cid in (6, 7, 8):
        _delete(port, f"/api/chairs/{cid}")
    _post(port, "/api/engine/start_game")
    _, snap = _get(port, "/api/engine")
    return {p["name"]: p["id"] for p in snap["players"]}


def test_save_state_load_state_endpoints() -> None:
    srv, port = _setup_server()
    try:
        ids = _seat_default_table(port)

        # Save the engine state right after start_game.
        s, payload = _post(port, "/api/engine/save_state")
        assert s == 200
        blob = payload["state"]
        assert isinstance(blob, str) and len(blob) > 50

        # Mutate via API: kill Alice.
        _post(port, "/api/engine/kill",
              {"player_id": ids["Alice"], "cause": "storyteller"})
        _, snap = _get(port, "/api/engine")
        alice = next(p for p in snap["players"] if p["id"] == ids["Alice"])
        assert alice["alive"] is False

        # Restore.
        s, payload = _post(port, "/api/engine/load_state", {"state": blob})
        assert s == 200, payload
        _, snap = _get(port, "/api/engine")
        alice = next(p for p in snap["players"] if p["id"] == ids["Alice"])
        assert alice["alive"] is True
    finally:
        srv.shutdown()


def test_back_endpoint_re_runs_current_ability() -> None:
    srv, port = _setup_server()
    try:
        ids = _seat_default_table(port)

        # Drive past Poisoner so Washerwoman's first prompt is up.
        p = _wait_for_prompt(port, character="Poisoner", step="select_player")
        _post(port, "/api/engine/respond",
              {"prompt_id": p["id"], "response": ids["Alice"]})
        last_id = p["id"]

        ww1 = _wait_for_prompt(
            port, character="Washerwoman", step="select_character",
            skip_id=last_id,
        )
        # Original answer.
        _post(port, "/api/engine/respond",
              {"prompt_id": ww1["id"], "response": "Ravenkeeper"})
        last_id = ww1["id"]

        # Inside the Washerwoman ability, second prompt up.
        ww2 = _wait_for_prompt(
            port, character="Washerwoman", step="select_players",
            skip_id=last_id,
        )

        # Press Back. Endpoint should report the restore happened, and
        # the snapshot's history shrinks by one.
        _, before_snap = _get(port, "/api/engine")
        h_before = before_snap.get("history_size", 0)
        s, payload = _post(port, "/api/engine/back")
        assert s == 200
        assert payload.get("restored") is True
        assert payload["engine"].get("history_size", 0) == h_before - 1

        # The Washerwoman's first prompt should fire again, with a new
        # id (new prompt object). We re-answer with a different role.
        ww1_redo = _wait_for_prompt(
            port, character="Washerwoman", step="select_character",
            skip_id=ww2["id"],
        )
        assert ww1_redo["id"] != ww1["id"]
        _post(port, "/api/engine/respond",
              {"prompt_id": ww1_redo["id"], "response": "Soldier"})

        # Drive the rest of the night (select_players + information).
        # Either character can be Soldier here; just need to drive
        # through. After this, engine auto-advances to DAY.
        ww2b = _wait_for_prompt(
            port, character="Washerwoman", step="select_players",
            skip_id=ww1_redo["id"],
        )
        _post(port, "/api/engine/respond",
              {"prompt_id": ww2b["id"],
               "response": [ids["Bob"], ids["Cara"]]})
        info = _wait_for_prompt(
            port, character="Washerwoman", step="information",
            skip_id=ww2b["id"],
        )
        _post(port, "/api/engine/respond",
              {"prompt_id": info["id"], "response": None})

        # Wait for auto-advance to day.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            _, snap = _get(port, "/api/engine")
            if snap["phase"] == "day":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                f"engine did not advance to day; phase={snap['phase']!r}"
            )
    finally:
        srv.shutdown()


def test_back_endpoint_returns_false_when_history_empty() -> None:
    srv, port = _setup_server()
    try:
        # Reset the engine to a known (empty-history) state.
        ui.ENGINE = Engine()
        s, payload = _post(port, "/api/engine/back")
        assert s == 200
        assert payload.get("restored") is False
    finally:
        srv.shutdown()


if __name__ == "__main__":
    test_save_state_load_state_endpoints()
    test_back_endpoint_re_runs_current_ability()
    test_back_endpoint_returns_false_when_history_empty()
    print("HTTP back tests passed.")
