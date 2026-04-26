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
import itertools
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
# In-memory chair state (visual layout of the town square).
# ---------------------------------------------------------------------------


class ChairStore:
    """Holds the visual chair arrangement.

    Each chair is a dict::

        {"id": int, "x": float, "y": float, "name": str, "character": str,
         "player_id": Optional[int]}

    Chairs are *purely visual* until the storyteller clicks "Start
    Game", at which point each chair with a non-empty name is mapped to
    an engine Player (and the chair's ``player_id`` records the link).
    """

    def __init__(self) -> None:
        self._chairs: Dict[int, Dict[str, Any]] = {}
        self._next_id = itertools.count(1)
        self._lock = threading.Lock()
        # Storyteller default position sits at the bottom-center of the
        # would-be full circle, closing the 270-degree arc of chairs
        # seeded below.
        self._storyteller: Dict[str, float] = {
            "x": 0.5, "y": 0.88, "w": 0.175, "h": 0.08,
        }
        self._seed_default_ring(count=8)

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
        90 degrees open for the storyteller (bottom-center, see
        ``__init__``) to close the ring. The arc runs clockwise (in
        screen coords, where +y is down) from the lower-left at
        theta = 3pi/4, through the top, around to the lower-right at
        theta = pi/4. Endpoints are included so the outer chairs sit
        flush with where the storyteller's gap begins. Chairs are
        ordered by id, so adding a new chair always appends to the
        right end of the arc and shifts every existing chair slightly
        to make room — exactly the "reset and re-spread" behaviour the
        UI wants when a player joins.
        """
        import math
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
        # Adding a chair re-spreads every chair around the 270-degree
        # arc so the layout always looks intentional. The optional x/y
        # arguments are accepted for API compatibility but ignored when
        # the layout reset takes over.
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
        """Compact chair ids to 1..N preserving their existing order.

        Chair ids double as the seat numbers shown in the UI. After a
        removal we renumber so the surviving chairs read 1..N again with
        no gaps. The relative order is preserved (chairs are reassigned
        in ascending order of their old ids), and ``_next_id`` is reset
        so the next added chair picks up at N+1.
        """
        ordered = sorted(self._chairs.values(), key=lambda c: c["id"])
        new_chairs: Dict[int, Dict[str, Any]] = {}
        for new_id, chair in enumerate(ordered, start=1):
            chair["id"] = new_id
            new_chairs[new_id] = chair
        self._chairs = new_chairs
        # Reset the id allocator so subsequent adds continue from N+1.
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
        import math
        with self._lock:
            chairs = list(self._chairs.values())
        # Sort by angle from center, starting at the top (12 o'clock).
        def angle(c):
            dx, dy = c["x"] - 0.5, c["y"] - 0.5
            # atan2 is counter-clockwise; flip sign so we go clockwise
            # and offset so 12 o'clock is the start.
            a = math.atan2(dy, dx)  # -pi (left) .. pi
            # 12 o'clock = -pi/2; want it to be 0.
            a = (a + math.pi / 2) % (2 * math.pi)
            return a
        return sorted(chairs, key=angle)


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


# ---------------------------------------------------------------------------
# Character pool (the bag of characters chosen for this game).
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


class CharacterPool:
    """The set of characters chosen by the storyteller for the game.

    Each entry is the character name. Order is preserved (storyteller
    insertion order). The same character is never present twice.

    The pool also tracks the *Drunk's fake townsfolk role* — a Townsfolk
    name the Drunk player will be told they are. The fake role is NOT
    stored in ``_names`` (it does not occupy a Townsfolk slot in the
    bag); it is shown alongside the Drunk in the UI and used by the
    storyteller during setup. The fake is automatically cleared if the
    Drunk leaves the pool.

    The pool also tracks the *Fortune Teller's red-herring role* — a
    good role (Townsfolk or Outsider) that is *already in the pool*.
    The seated player who ends up holding this role is the FT's red
    herring. Unlike the Drunk's fake, this role does live in
    ``_names``; the red-herring slot just records *which* of the
    in-pool good roles the storyteller has marked. Auto-picked at
    random when the FT enters the pool; the storyteller can re-roll
    later by dragging the FT's red-herring token on the grimoire.

    The pool also tracks the *Washerwoman's seen Townsfolk role* — the
    Townsfolk character whose token the WW is shown on the first
    night. Like the FT's red herring, this role lives in ``_names``;
    the WW slot records which of the in-pool Townsfolk the storyteller
    has marked. Auto-picked at random when the WW enters the pool.

    The pool also tracks the *Washerwoman's WRONG role* — the role of
    the *other* player the WW is pointed at on the first night (the
    one who is *not* the seen Townsfolk). The WRONG role can be any
    role in the pool *other than* the WW herself and the seen
    Townsfolk role. Like the seen-Townsfolk slot, it lives in
    ``_names``; the WRONG slot records which of the in-pool roles the
    storyteller has marked. Auto-picked at random when the WW enters
    the pool, and re-rolled if its role leaves the pool.

    All three slots are cleared when the role they depend on (Drunk,
    Fortune Teller, Washerwoman) leaves the pool or when the marked
    role itself is removed.
    """

    def __init__(self) -> None:
        self._names: List[str] = []
        self._drunk_fake: Optional[str] = None
        self._ft_red_herring: Optional[str] = None
        self._washerwoman_townsfolk: Optional[str] = None
        self._washerwoman_wrong: Optional[str] = None
        self._lock = threading.Lock()

    def list(self) -> List[str]:
        with self._lock:
            return list(self._names)

    def drunk_fake(self) -> Optional[str]:
        with self._lock:
            return self._drunk_fake

    def ft_red_herring(self) -> Optional[str]:
        with self._lock:
            return self._ft_red_herring

    def washerwoman_townsfolk(self) -> Optional[str]:
        with self._lock:
            return self._washerwoman_townsfolk

    def washerwoman_wrong(self) -> Optional[str]:
        with self._lock:
            return self._washerwoman_wrong

    # ------------------------------------------------------------------
    # Internal helpers (assume self._lock is held).
    # ------------------------------------------------------------------

    def _good_in_pool(self) -> List[str]:
        """Names in the pool that are Townsfolk or Outsider."""
        good: List[str] = []
        for n in self._names:
            spec = script_data.SCRIPT_BY_NAME.get(n)
            if spec is None:
                continue
            if spec.char_type in (CharType.TOWNSFOLK, CharType.OUTSIDER):
                good.append(n)
        return good

    def _townsfolk_in_pool(self) -> List[str]:
        return [
            n for n in self._names
            if (script_data.SCRIPT_BY_NAME.get(n)
                and script_data.SCRIPT_BY_NAME[n].char_type
                is CharType.TOWNSFOLK)
        ]

    def _autofill_ft_red_herring(self) -> None:
        """If the FT is in the pool but no red herring is set, pick one
        uniformly at random from the Good roles in the pool. Caller
        must already hold self._lock.

        The FT *itself* is a Good role, and the rules allow the FT to
        be its own red herring — but it makes for a degenerate game
        ("which good player registers as a Demon to me?" "...me, I
        guess"). So we prefer roles other than the FT, falling back to
        the FT only if there's literally nothing else to pick.
        """
        if "Fortune Teller" not in self._names:
            self._ft_red_herring = None
            return
        if self._ft_red_herring in self._names:
            return  # already valid
        good = self._good_in_pool()
        non_self = [n for n in good if n != "Fortune Teller"]
        candidates = non_self or good
        self._ft_red_herring = random.choice(candidates) if candidates else None

    def _autofill_washerwoman_townsfolk(self) -> None:
        """If the WW is in the pool but no seen-role is set, pick one
        uniformly at random from the Townsfolk in the pool. Caller
        must already hold self._lock.

        Same self-avoidance rule as the FT: prefer any other Townsfolk
        over picking the WW herself, but fall back to self if no other
        Townsfolk exists in the pool yet (e.g. the storyteller added
        the WW first and hasn't filled the bag).
        """
        if "Washerwoman" not in self._names:
            self._washerwoman_townsfolk = None
            return
        if self._washerwoman_townsfolk in self._names:
            return  # already valid
        townsfolk = self._townsfolk_in_pool()
        non_self = [n for n in townsfolk if n != "Washerwoman"]
        candidates = non_self or townsfolk
        self._washerwoman_townsfolk = (
            random.choice(candidates) if candidates else None
        )

    def _autofill_washerwoman_wrong(self) -> None:
        """If the WW is in the pool but no WRONG-role is set, pick one
        uniformly at random from the in-pool roles that are *neither*
        the Washerwoman herself *nor* the currently-set seen-Townsfolk
        role. Caller must already hold self._lock.

        Per the rulebook the WRONG token goes "by any *other* character
        token", i.e. anyone who is not the seen Townsfolk. Outsiders,
        Minions, and Demons are all valid WRONG candidates. We exclude
        the WW herself because the storyteller doesn't point at the
        WW's own seat — the WW is the one being woken.
        """
        if "Washerwoman" not in self._names:
            self._washerwoman_wrong = None
            return
        # If the currently-set wrong role is still valid (in pool, not
        # WW, not the seen-TF), keep it.
        if (
            self._washerwoman_wrong in self._names
            and self._washerwoman_wrong != "Washerwoman"
            and self._washerwoman_wrong != self._washerwoman_townsfolk
        ):
            return
        candidates = [
            n for n in self._names
            if n != "Washerwoman" and n != self._washerwoman_townsfolk
        ]
        self._washerwoman_wrong = (
            random.choice(candidates) if candidates else None
        )

    # ------------------------------------------------------------------

    def add(self, name: str) -> bool:
        with self._lock:
            if name in self._names:
                return False
            self._names.append(name)
            # If by some race the new name was being tracked as the
            # Drunk's fake, drop the fake (it can't be both).
            if self._drunk_fake == name:
                self._drunk_fake = None
            # Auto-fill dependent slots so the UX doesn't require an
            # explicit "pick the red herring" / "pick the WW townsfolk"
            # step. Adding the FT picks a random Good role; adding the
            # WW picks a random Townsfolk; adding any Good role while
            # the FT/WW is already present is a chance to retroactively
            # fill in a previously-empty slot (e.g. FT was added when
            # the pool was empty).
            self._autofill_ft_red_herring()
            self._autofill_washerwoman_townsfolk()
            self._autofill_washerwoman_wrong()
            return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self._names:
                return False
            self._names.remove(name)
            # Removing the Drunk also drops their pretend role.
            if name == "Drunk":
                self._drunk_fake = None
            # Removing the Fortune Teller drops the red herring.
            if name == "Fortune Teller":
                self._ft_red_herring = None
            # Removing the Washerwoman drops their seen Townsfolk
            # *and* their WRONG role.
            if name == "Washerwoman":
                self._washerwoman_townsfolk = None
                self._washerwoman_wrong = None
            # Removing the marked role itself: re-pick a replacement
            # so the slot doesn't go stale (rules say there must always
            # be a red herring, a WW seen-Townsfolk, and a WW wrong
            # while the relevant roles are in play).
            if name == self._ft_red_herring:
                self._ft_red_herring = None
                self._autofill_ft_red_herring()
            if name == self._washerwoman_townsfolk:
                self._washerwoman_townsfolk = None
                self._autofill_washerwoman_townsfolk()
                # The WRONG role excludes the seen-TF, so a freshly
                # auto-picked TF can render the previous WRONG choice
                # invalid (if WRONG happened to be the new TF). Re-roll.
                self._autofill_washerwoman_wrong()
            if name == self._washerwoman_wrong:
                self._washerwoman_wrong = None
                self._autofill_washerwoman_wrong()
            return True

    def clear(self) -> None:
        with self._lock:
            self._names.clear()
            self._drunk_fake = None
            self._ft_red_herring = None
            self._washerwoman_townsfolk = None
            self._washerwoman_wrong = None

    def set_many(self, names: List[str]) -> List[str]:
        # Replace the pool with the given list, deduplicated and order-preserved.
        seen: set = set()
        deduped: List[str] = []
        for n in names:
            if isinstance(n, str) and n and n not in seen:
                seen.add(n)
                deduped.append(n)
        with self._lock:
            self._names = deduped
            # Drop the fake if the Drunk isn't in the new pool, or if
            # the fake's name happened to be promoted into the bag.
            if "Drunk" not in deduped or (self._drunk_fake in deduped):
                self._drunk_fake = None
            # Red herring / WW townsfolk: drop if their owner role left
            # the pool or if the marked role itself is gone, then
            # auto-refill so the slots are never left stale.
            if (
                "Fortune Teller" not in deduped
                or self._ft_red_herring not in deduped
            ):
                self._ft_red_herring = None
            if (
                "Washerwoman" not in deduped
                or self._washerwoman_townsfolk not in deduped
            ):
                self._washerwoman_townsfolk = None
            if (
                "Washerwoman" not in deduped
                or self._washerwoman_wrong not in deduped
                or self._washerwoman_wrong == "Washerwoman"
            ):
                self._washerwoman_wrong = None
            self._autofill_ft_red_herring()
            self._autofill_washerwoman_townsfolk()
            # Re-validate WRONG after seen-TF was (re)autofilled —
            # the WRONG slot must not equal the (possibly new) TF.
            if (
                self._washerwoman_wrong is not None
                and self._washerwoman_wrong == self._washerwoman_townsfolk
            ):
                self._washerwoman_wrong = None
            self._autofill_washerwoman_wrong()
            return list(self._names)

    def set_drunk_fake(self, name: Optional[str]) -> Optional[str]:
        """Set the Drunk's fake Townsfolk, or pass None to clear it.

        Raises ValueError if the pool doesn't contain the Drunk, or if
        ``name`` is already a real role in the pool.
        """
        with self._lock:
            if name is None:
                self._drunk_fake = None
                return None
            if "Drunk" not in self._names:
                raise ValueError("Drunk is not in the pool")
            if name in self._names:
                raise ValueError(
                    "Drunk's pretend role can't be a real role in play")
            self._drunk_fake = name
            return self._drunk_fake

    def set_ft_red_herring(self, name: Optional[str]) -> Optional[str]:
        """Set the FT's red-herring role, or pass None to clear it.

        Raises ValueError if the pool doesn't contain the Fortune
        Teller, if ``name`` isn't already in the pool, or if ``name``
        isn't a Good role (Townsfolk / Outsider). The FT may pick its
        own role as the red herring (per the rules).
        """
        with self._lock:
            if name is None:
                self._ft_red_herring = None
                return None
            if "Fortune Teller" not in self._names:
                raise ValueError("Fortune Teller is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "FT's red-herring role must already be in the pool")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            if spec.char_type not in (CharType.TOWNSFOLK, CharType.OUTSIDER):
                raise ValueError(
                    "FT's red-herring role must be a Townsfolk or Outsider")
            self._ft_red_herring = name
            return self._ft_red_herring

    def set_washerwoman_townsfolk(self, name: Optional[str]) -> Optional[str]:
        """Set the Washerwoman's seen Townsfolk role, or pass None to
        clear it.

        Raises ValueError if the pool doesn't contain the Washerwoman,
        if ``name`` isn't already in the pool, or if ``name`` isn't a
        Townsfolk. The WW *can* be the seen Townsfolk herself — the
        rules don't forbid it.

        If the new seen-TF equals the currently-set WRONG role (which
        the rules forbid — the WRONG slot is the *other* player), the
        WRONG slot is auto-rerolled to a still-valid candidate.
        """
        with self._lock:
            if name is None:
                self._washerwoman_townsfolk = None
                # The WRONG slot's eligibility expanded; re-validate.
                if self._washerwoman_wrong == name:
                    self._washerwoman_wrong = None
                self._autofill_washerwoman_wrong()
                return None
            if "Washerwoman" not in self._names:
                raise ValueError("Washerwoman is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "WW's seen Townsfolk must already be in the pool")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            if spec.char_type is not CharType.TOWNSFOLK:
                raise ValueError(
                    "WW's seen role must be a Townsfolk")
            self._washerwoman_townsfolk = name
            # If the new seen-TF collides with the WRONG slot, re-roll
            # WRONG so the two never point at the same role.
            if self._washerwoman_wrong == name:
                self._washerwoman_wrong = None
            self._autofill_washerwoman_wrong()
            return self._washerwoman_townsfolk

    def set_washerwoman_wrong(self, name: Optional[str]) -> Optional[str]:
        """Set the Washerwoman's WRONG role, or pass None to clear it.

        Raises ValueError if the pool doesn't contain the Washerwoman,
        if ``name`` isn't already in the pool, if ``name`` is the
        Washerwoman herself, or if ``name`` is the same as the WW's
        seen Townsfolk slot (the two tokens point at *different*
        players by definition).
        """
        with self._lock:
            if name is None:
                self._washerwoman_wrong = None
                return None
            if "Washerwoman" not in self._names:
                raise ValueError("Washerwoman is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "WW's WRONG role must already be in the pool")
            if name == "Washerwoman":
                raise ValueError(
                    "WW's WRONG token can't sit on the Washerwoman herself")
            if name == self._washerwoman_townsfolk:
                raise ValueError(
                    "WW's WRONG role must differ from the seen Townsfolk")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            self._washerwoman_wrong = name
            return self._washerwoman_wrong


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


def _townsfolk_in_play(name: str) -> bool:
    spec = script_data.SCRIPT_BY_NAME.get(name)
    return spec is not None and spec.char_type is CharType.TOWNSFOLK


def _good_in_play(name: str) -> bool:
    spec = script_data.SCRIPT_BY_NAME.get(name)
    return spec is not None and spec.char_type in (
        CharType.TOWNSFOLK, CharType.OUTSIDER,
    )


def _move_drunk_token(dest_chair_id: int) -> Optional[str]:
    """Drop the IS-THE-DRUNK reminder onto ``dest_chair_id``.

    The destination chair's character must be a Townsfolk *currently
    in the pool* (the rules say the IS THE DRUNK token goes by a
    Townsfolk character token in the grimoire). If a different chair
    is currently the Drunk, this performs a clean swap so the bag /
    chair-character invariants stay consistent:

      * The destination chair becomes the Drunk; the Townsfolk role
        it used to hold (``T``) becomes the Drunk's new pretend role.
      * The previously-Drunk chair (if any) inherits the *previous*
        pretend role (``F``) as its actual character. ``F`` was not in
        the pool list before; now it is, replacing ``T``.

    The pool's drunk_fake field updates from ``F`` to ``T``, and the
    pool list swaps ``T`` out for ``F``. Net effect: the chair that
    "is the Drunk" moves, and a different Townsfolk slot is now the
    one swapped out of the bag.
    """
    dest = STORE.get(dest_chair_id)
    if dest is None:
        return f"no chair with id {dest_chair_id}"
    pool_names = POOL.list()
    if "Drunk" not in pool_names:
        return "Drunk is not in the pool"
    dest_char = (dest.get("character") or "").strip()
    if not dest_char:
        return "destination chair has no character assigned"
    # Source chair = whichever chair currently holds the Drunk role.
    source: Optional[Dict[str, Any]] = None
    for c in STORE.list():
        if (c.get("character") or "").strip() == "Drunk":
            source = c
            break
    if source is not None and source["id"] == dest_chair_id:
        return None  # no-op: dropping the token where it already is
    if not _townsfolk_in_play(dest_char):
        return "destination chair must hold a Townsfolk role"
    if dest_char not in pool_names:
        return f"{dest_char!r} is not in the pool"

    new_fake = dest_char  # Townsfolk the Drunk now thinks they are.
    prev_fake = POOL.drunk_fake()  # may be None if first placement.

    # Swap the chair characters first so the chair store reflects the
    # new arrangement before we reshuffle the pool. The *previously
    # drunk* chair takes on the previous fake (which was off-bag and
    # now joins the bag); if there was no previous fake, the source
    # chair is left without a character — the storyteller can fill it
    # in later.
    if source is not None:
        STORE.update(source["id"], character=(prev_fake or ""))
    STORE.update(dest_chair_id, character="Drunk")

    # Pool list: drop ``new_fake`` (it's now off-bag, the Drunk's
    # pretend role) and insert ``prev_fake`` in its place if there
    # was one.
    new_pool: List[str] = []
    inserted_prev_fake = False
    for n in pool_names:
        if n == new_fake:
            if prev_fake is not None and not inserted_prev_fake:
                new_pool.append(prev_fake)
                inserted_prev_fake = True
            # else: just drop new_fake without replacing — the Drunk
            # keeps its slot ("Drunk" stays in the list), so the bag
            # just shrinks by one. This is the "first placement"
            # branch where the pool didn't have a swapped-in TF yet.
            continue
        new_pool.append(n)
    POOL.set_many(new_pool)
    try:
        POOL.set_drunk_fake(new_fake)
    except ValueError:
        # Shouldn't happen — set_many leaves "Drunk" in the pool and
        # ``new_fake`` is no longer in pool_names. But if it does,
        # leave the pool's drunk_fake unset; UI will show pending.
        pass
    return None


def _move_ft_red_herring(dest_chair_id: int) -> Optional[str]:
    """Drop the FT RED HERRING reminder onto ``dest_chair_id``.

    The destination chair's character must be a Good role (Townsfolk
    or Outsider) currently in the pool. The Drunk seat counts (its
    chair character is "Drunk", an Outsider) — the FT may land its
    red herring on the actual Drunk per the rules.
    """
    dest = STORE.get(dest_chair_id)
    if dest is None:
        return f"no chair with id {dest_chair_id}"
    if "Fortune Teller" not in POOL.list():
        return "Fortune Teller is not in the pool"
    dest_char = (dest.get("character") or "").strip()
    if not dest_char:
        return "destination chair has no character assigned"
    if not _good_in_play(dest_char):
        return "destination chair must hold a Townsfolk or Outsider role"
    if dest_char not in POOL.list():
        return f"{dest_char!r} is not in the pool"
    try:
        POOL.set_ft_red_herring(dest_char)
    except ValueError as exc:
        return str(exc)
    return None


def _move_washerwoman_token(dest_chair_id: int) -> Optional[str]:
    """Drop the WW TOWNSFOLK reminder onto ``dest_chair_id``.

    The destination chair's character must be a Townsfolk currently
    in the pool. The Drunk seat is *not* eligible — the Washerwoman
    is told the actual Townsfolk character of the seen player, and
    the rules say the WW knows the seen player is not the Drunk.
    """
    dest = STORE.get(dest_chair_id)
    if dest is None:
        return f"no chair with id {dest_chair_id}"
    if "Washerwoman" not in POOL.list():
        return "Washerwoman is not in the pool"
    dest_char = (dest.get("character") or "").strip()
    if not dest_char:
        return "destination chair has no character assigned"
    if not _townsfolk_in_play(dest_char):
        return "destination chair must hold a Townsfolk role"
    if dest_char not in POOL.list():
        return f"{dest_char!r} is not in the pool"
    try:
        POOL.set_washerwoman_townsfolk(dest_char)
    except ValueError as exc:
        return str(exc)
    return None


def _move_washerwoman_wrong_token(dest_chair_id: int) -> Optional[str]:
    """Drop the WW WRONG reminder onto ``dest_chair_id``.

    Per the rulebook the WRONG token goes "by any *other* character
    token" — meaning any seated character except the Washerwoman
    herself and the WW's seen Townsfolk. Any role type
    (Townsfolk / Outsider / Minion / Demon) qualifies.
    """
    dest = STORE.get(dest_chair_id)
    if dest is None:
        return f"no chair with id {dest_chair_id}"
    if "Washerwoman" not in POOL.list():
        return "Washerwoman is not in the pool"
    dest_char = (dest.get("character") or "").strip()
    if not dest_char:
        return "destination chair has no character assigned"
    if dest_char not in POOL.list():
        return f"{dest_char!r} is not in the pool"
    try:
        POOL.set_washerwoman_wrong(dest_char)
    except ValueError as exc:
        return str(exc)
    return None


# ---------------------------------------------------------------------------
# Globals (set up in main()).
# ---------------------------------------------------------------------------

STORE = ChairStore()
ENGINE = Engine()
POOL = CharacterPool()

# Selected preset name (e.g. "trouble_brewing"). Set when the
# storyteller randomizes the bag from a preset, or sent explicitly with
# /api/engine/start_game. Drives the engine's night order.
SELECTED_PRESET: Optional[str] = None


def SELECTED_PRESET_set(name: Optional[str]) -> None:
    """Set the selected preset name from any scope.

    Wrapper so handler code can update the global without needing a
    ``global`` declaration (which Python disallows mixing with a later
    second ``global`` in the same function).
    """
    global SELECTED_PRESET
    SELECTED_PRESET = name


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

        # Build the seat list from the current chairs in clockwise order
        # — same source of truth as ``_sync_chairs_to_engine``.
        seats: List[Dict[str, Any]] = []
        for chair in STORE.chairs_in_clockwise_order():
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
                "preset": SELECTED_PRESET or "",
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
            self._send_file(os.path.join(STATIC_DIR, "phone.html"),
                            "text/html; charset=utf-8")
            return

        if path == "/api/state":
            self._send_json(HTTPStatus.OK, {
                "chairs": STORE.list(),
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

        if path == "/api/character_pool/move_drunk_token":
            ok, data = self._read_json()
            if not ok or "chair_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need chair_id"})
                return
            try:
                err = _move_drunk_token(int(data["chair_id"]))
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "chair_id must be an int"})
                return
            if err is not None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": err})
                return
            self._send_json(HTTPStatus.OK, {
                "chairs": STORE.list(),
                **_character_pool_snapshot(),
            })
            return

        if path == "/api/character_pool/move_ft_red_herring":
            ok, data = self._read_json()
            if not ok or "chair_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need chair_id"})
                return
            try:
                err = _move_ft_red_herring(int(data["chair_id"]))
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "chair_id must be an int"})
                return
            if err is not None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": err})
                return
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
            return

        if path == "/api/character_pool/move_washerwoman_token":
            ok, data = self._read_json()
            if not ok or "chair_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need chair_id"})
                return
            try:
                err = _move_washerwoman_token(int(data["chair_id"]))
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "chair_id must be an int"})
                return
            if err is not None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": err})
                return
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
            return

        if path == "/api/character_pool/move_washerwoman_wrong_token":
            ok, data = self._read_json()
            if not ok or "chair_id" not in data:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "need chair_id"})
                return
            try:
                err = _move_washerwoman_wrong_token(int(data["chair_id"]))
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "chair_id must be an int"})
                return
            if err is not None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": err})
                return
            self._send_json(HTTPStatus.OK, _character_pool_snapshot())
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
                if SELECTED_PRESET:
                    p = preset_module.load_preset(_PRESETS_DIR, SELECTED_PRESET)
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
            #   2. Replace the in-process engine with a fresh instance —
            #      preserves chair layout / character pool / preset
            #      selection (those live on STORE / POOL /
            #      SELECTED_PRESET, not on ENGINE), but discards every
            #      Player, Character, and event log entry from the
            #      previous game.
            #   3. Clear the chair → engine player_id mappings so that
            #      a future Start Game re-creates engine players from
            #      the current chair list.
            killed = kill_engine_runner_subprocess()
            ENGINE_replace(Engine())
            # Each chair currently records the engine player_id it was
            # bound to last time we hit Start Game. After resetting,
            # those ids no longer exist, so wipe them.
            for chair in STORE.list():
                STORE.update(chair["id"], clear_player_id=True)
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
        chairs in clockwise order, creating one engine Player per
        non-empty-named chair, and assigning the typed character if the
        ``character`` field on the chair matches a known role.
        """
        # Only valid before start_game; otherwise just bail (engine
        # rejects setup mutations anyway).
        clockwise = STORE.chairs_in_clockwise_order()
        for chair in clockwise:
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
    poisoned_role = None
    butler_master_role = None
    try:
        engine_players = ENGINE.players
    except Exception:
        engine_players = []
    for p in engine_players:
        char = getattr(p, "character", None)
        if char is None:
            continue
        # Gate on the source player's state. ``has_ability`` is
        # ``alive and not drunk and not poisoned`` — exactly the
        # condition under which the rulebook says the ability has
        # an effect on the table.
        if not p.has_ability:
            continue
        if char.name == "Poisoner":
            target = getattr(char, "_last_target", None)
            if (target is not None
                    and getattr(target, "character", None) is not None
                    and target.poisoned):
                poisoned_role = target.character.name
        elif char.name == "Butler":
            master = getattr(char, "_master", None)
            if (master is not None
                    and getattr(master, "character", None) is not None):
                butler_master_role = master.character.name

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
        "investigator_minion_role": None,
        "librarian_outsider_role": None,
        "butler_master_role": butler_master_role,
        "monk_safe_role": None,
        "poisoned_role": poisoned_role,
        "imp_dead_role": None,
        "undertaker_died_today_role": None,
        "scarlet_woman_is_demon": False,
        "slayer_no_ability": False,
        "virgin_no_ability": False,
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
        help="Require an access code to visit the site.",
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
