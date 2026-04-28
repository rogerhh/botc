"""Chair / town-square layout state.

Owned by the engine so any copy of an :class:`engine.Engine` can be
rendered identically by the UI without help from external state.

Each chair is a dict::

    {"id": int, "x": float, "y": float,
     "name": str, "character": str,
     "player_id": Optional[int]}

Chair coordinates are normalized to ``[0, 1]`` on both axes so they map
cleanly into whatever board size the front-end picks. Chairs are
*purely visual* until ``Engine.start_game`` is called, at which point
each named chair is mapped to an :class:`engine.Player` and the chair's
``player_id`` records the link.
"""

from __future__ import annotations

import itertools
import math
import threading
from typing import Any, Dict, List, Optional


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


class ChairStore:
    """Holds the visual chair arrangement for one engine instance.

    The class is thread-safe; it is read by HTTP request handlers and
    mutated both by the engine and by Storyteller-driven UI calls.
    """

    def __init__(self, *, default_seats: int = 8) -> None:
        self._chairs: Dict[int, Dict[str, Any]] = {}
        self._next_id = itertools.count(1)
        self._lock = threading.Lock()
        # Storyteller default position sits at the bottom-center of the
        # would-be full circle, closing the 270-degree arc of chairs
        # seeded below.
        self._storyteller: Dict[str, float] = {
            "x": 0.5, "y": 0.88, "w": 0.175, "h": 0.08,
        }
        self._seed_default_ring(count=default_seats)

    # ------------------------------------------------------------------
    # Pickling support.
    #
    # ``threading.Lock`` and ``itertools.count`` aren't picklable, so we
    # strip them out on save and rebuild on restore. The ``next_id`` is
    # serialized as a plain int (the next id we'd hand out) so the
    # restored store keeps issuing fresh ids.
    # ------------------------------------------------------------------

    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()
        state.pop("_lock", None)
        # Capture the next id without consuming it from the live counter.
        max_id = max(self._chairs) if self._chairs else 0
        state["_next_id"] = max(max_id + 1, 1)
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        next_id_value = state.pop("_next_id", 1)
        self.__dict__.update(state)
        self._lock = threading.Lock()
        self._next_id = itertools.count(int(next_id_value))

    def reseed(self, count: int) -> None:
        """Replace the chair set with ``count`` freshly-seeded chairs."""
        with self._lock:
            self._chairs = {}
            self._next_id = itertools.count(1)
            self._seed_default_ring(count=count)

    def _seed_default_ring(self, count: int) -> None:
        # Create the chairs as placeholders, then call the shared
        # arc-layout helper so seeding and adding produce identical
        # geometry.
        for _ in range(count):
            cid = next(self._next_id)
            self._chairs[cid] = {
                "id": cid, "x": 0.5, "y": 0.5,
                "name": "", "character": "",
                "player_id": None,
            }
        self._relayout_arc()

    def _relayout_arc(self) -> None:
        """Spread every chair evenly along the 270-degree arc.

        The arc sits over the top half of the board, leaving the bottom
        90 degrees open for the storyteller to close the ring.
        """
        cx, cy, r = 0.5, 0.5, 0.38
        arc = 1.5 * math.pi  # 270 degrees
        start = 0.75 * math.pi  # lower-left
        chairs_in_order = sorted(self._chairs.values(), key=lambda c: c["id"])
        n = len(chairs_in_order)
        for i, chair in enumerate(chairs_in_order):
            if n > 1:
                theta = start + arc * i / (n - 1)
            else:
                theta = start + arc / 2
            chair["x"] = cx + r * math.cos(theta)
            chair["y"] = cy + r * math.sin(theta)

    def list(self) -> list:
        with self._lock:
            return sorted(self._chairs.values(), key=lambda c: c["id"])

    def add(self, x: Optional[float] = None, y: Optional[float] = None) -> dict:
        with self._lock:
            cid = next(self._next_id)
            chair = {
                "id": cid, "x": 0.5, "y": 0.5,
                "name": "", "character": "", "player_id": None,
            }
            self._chairs[cid] = chair
            self._relayout_arc()
            return chair

    def update(
        self,
        cid: int,
        x: Optional[float] = None,
        y: Optional[float] = None,
        name: Optional[str] = None,
        character: Optional[str] = None,
        player_id: Optional[int] = None,
        clear_player_id: bool = False,
    ) -> Optional[dict]:
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
            if player_id is not None:
                chair["player_id"] = int(player_id)
            elif clear_player_id:
                chair["player_id"] = None
            return chair

    def remove(self, cid: int) -> Optional[dict]:
        with self._lock:
            removed = self._chairs.pop(cid, None)
            if removed is not None:
                self._renumber()
            return removed

    def remove_last(self) -> Optional[int]:
        with self._lock:
            if not self._chairs:
                return None
            cid = max(self._chairs)
            del self._chairs[cid]
            self._renumber()
            return cid

    def _renumber(self) -> None:
        """Renumber chair ids to 1..N going clockwise from bottom-left.

        Chair number 1 is always the chair closest to the bottom-left
        (the start of the seeded 270° arc) and the numbering walks
        clockwise from there — up the left side, across the top, and
        down the right — to chair number N at the bottom-right. This
        is the single numbering system shared with the badge rendered
        on each chair and with ``engine.Player.id`` once the game
        starts (see ``ui._sync_chairs_to_engine``).

        Called after every ``add`` / ``remove`` so the invariant
        survives any sequence of chair mutations. Drags update only
        positions; if the storyteller wants the numbering to follow
        a manually-rearranged board, a re-spread via the Circle /
        Square arrangement buttons (or a subsequent add/remove) will
        refresh the numbering against the new positions.
        """
        # Sort key: clockwise angle measured from "lower-left" (math
        # angle 0.75π — the start of the seeded arc). For a chair at
        # the default seeded position, this is exactly its arc index,
        # so chair.id-1 == arc index == clockwise rank from bottom-left.
        # The wrap-around modulo ensures bottom-right (math angle
        # ~0.25π) sorts AFTER the top (math 1.5π), not before it.
        origin = 0.75 * math.pi  # bottom-left direction from board center

        def rank(c: Dict[str, Any]) -> float:
            dx, dy = c["x"] - 0.5, c["y"] - 0.5
            a = math.atan2(dy, dx)
            return (a - origin) % (2 * math.pi)

        ordered = sorted(self._chairs.values(), key=rank)
        new_chairs: Dict[int, Dict[str, Any]] = {}
        for new_id, chair in enumerate(ordered, start=1):
            chair["id"] = new_id
            new_chairs[new_id] = chair
        self._chairs = new_chairs
        self._next_id = itertools.count(len(self._chairs) + 1)

    def get(self, cid: int) -> Optional[dict]:
        with self._lock:
            return dict(self._chairs[cid]) if cid in self._chairs else None

    def get_storyteller(self) -> dict:
        with self._lock:
            return dict(self._storyteller)

    def move_storyteller(self, x: float, y: float) -> dict:
        with self._lock:
            self._storyteller["x"] = _clamp01(x)
            self._storyteller["y"] = _clamp01(y)
            return dict(self._storyteller)

    def chairs_in_clockwise_order(self) -> list:
        """Return chairs sorted by clockwise angle around the board center."""
        with self._lock:
            chairs = list(self._chairs.values())
        def angle(c):
            dx, dy = c["x"] - 0.5, c["y"] - 0.5
            a = math.atan2(dy, dx)
            a = (a + math.pi / 2) % (2 * math.pi)
            return a
        return sorted(chairs, key=angle)
