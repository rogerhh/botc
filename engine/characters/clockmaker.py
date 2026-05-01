"""Clockmaker.

    "You start knowing how many steps from the Demon to its nearest
     Minion."

First-night-only information ability from the Sects & Violets script.
The Clockmaker learns a single integer: the smallest number of seats
between the Demon and any Minion, walking either clockwise or
counter-clockwise around the seating ring.

Distance semantics (per the wiki):

    "The distance is the number of seated players, starting from the
     player next to the Demon and ending at the nearest Minion, either
     clockwise or counter-clockwise."

So if the Demon and a Minion are sitting next to each other, the
Clockmaker learns ``1``. If a Minion is two seats away (with one
non-Minion in between), the answer is ``2``. The smaller of the two
walking directions wins — the engine takes ``min(cw_distance,
ccw_distance)`` for each Demon, and ``min`` again across Demons (a
nicety for multi-Demon scripts like Legion).

Registration handling. Both detection passes — "is this seat the
Demon?" and "is this seat a Minion?" — are routed through
:class:`engine.check.Check` with ``attribute="char_type"``. That hands
control to ``registers_as`` on the target's character so:

    * a Spy may register as Townsfolk / Outsider and is therefore not
      counted as a Minion,
    * a Recluse may register as a Minion or Demon (Storyteller's
      pick), and
    * Travellers are never Minions / Demons (their char_type is
      ``TRAVELER``), matching the wiki example.

To keep the Storyteller from being prompted twice for the same seat we
cache each seat's pass/fail per check kind — a Recluse override (for
example) fires at most once per check kind no matter how many demons
walk past it.

Drunkenness / poisoning (per CLAUDE.md). The shown answer is a range
of options (``1..floor(N/2)`` for an ``N``-seat table), so the engine
pre-picks a *random wrong* number and surfaces it to the Storyteller
with a Next button. The Storyteller may change the value before it
goes to the player.

Scalability notes. The Clockmaker's logic is character-name-free past
this module: it asks the engine for the seating ring, builds two
char_type Checks, and lets the existing detection / registration
machinery do the rest. Adding a new Minion / Demon role to the script
needs no touch-ups here. Same for new misregistering roles — they
plug in through the Check / registers_as contract just like the Spy
and Recluse already do. The night-order constant lives at the top of
the class as a single source of truth.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from engine.character import Character
from engine.check import Check
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Clockmaker(Character):
    name = "Clockmaker"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "You start knowing how many steps from the Demon to its nearest "
        "Minion."
    )
    # Sects & Violets info character. Slots in after the Trouble
    # Brewing first-night info block (Chef 33 ... Spy 40); the official
    # combined night order puts the Clockmaker shortly after the Spy.
    first_night_order = 41
    other_night_order = 0
    reminder_tokens: list = []

    # Categories the Clockmaker cares about for misregistration
    # purposes: every type whose registration could change a Minion or
    # Demon answer. Used by setup-time eligibility helpers and by
    # Spy / Recluse overrides to size their prompt.
    DETECTION_CATEGORIES = (
        CharType.MINION,
        CharType.DEMON,
    )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _seat_passes(
        self,
        engine: "Engine",
        target: "Player",
        the_check: Check,
        cache: Dict[tuple, bool],
    ) -> bool:
        """Cached :meth:`Character.check`.

        Re-asking ``registers_as`` would re-prompt the Storyteller for
        misregistering targets (Spy, Recluse, ...). The Clockmaker
        walks the ring multiple times (once for the demon scan, once
        per demon for the minion scan), so we cache the answer per
        ``(seat_id, check_attribute, check_passes)`` and reuse.
        """
        if target is None or target.character is None:
            return False
        cache_key = (target.id, the_check.attribute, the_check.passes)
        if cache_key in cache:
            return cache[cache_key]
        result = self.check(engine, target, the_check)
        cache[cache_key] = result
        return result

    def _compute_default_distance(
        self,
        engine: "Engine",
        ordered: List["Player"],
    ) -> Tuple[Optional[int], List[int], List[int]]:
        """Walk the seating ring and compute the Clockmaker's reading.

        Returns ``(distance, demon_seats, nearest_minion_seats)``.
        ``distance`` is ``None`` when no demon / no minion is in play
        (degenerate scripts); callers default to a sensible fallback in
        that case. ``demon_seats`` and ``nearest_minion_seats`` are
        lists of player IDs and are used by the storyteller-facing
        prompt for highlighting and audit metadata.
        """
        if self.player is None:
            return None, [], []

        n = len(ordered)
        if n < 2:
            return None, [], []

        cache: Dict[tuple, bool] = {}

        demon_check = Check(
            attribute="char_type",
            passes=(CharType.DEMON,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={"step_for": "clockmaker_demon"},
        )
        minion_check = Check(
            attribute="char_type",
            passes=(CharType.MINION,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={"step_for": "clockmaker_minion"},
        )

        # Phase 1: identify demon seats. There is normally exactly one
        # demon, but this loop also handles 0-demon (degenerate) and
        # multi-demon (Legion / unusual scripts) without special cases.
        demon_indices: List[int] = []
        for i, p in enumerate(ordered):
            if p.character is None:
                continue
            if self._seat_passes(engine, p, demon_check, cache):
                demon_indices.append(i)

        if not demon_indices:
            return None, [], []

        # Phase 2: per demon, walk outward and find the nearest minion
        # in either direction. The first offset where any candidate
        # passes the minion check is this demon's reading.
        best_distance: Optional[int] = None
        nearest_minion_player_ids: List[int] = []
        for di in demon_indices:
            for offset in range(1, n):
                cw_idx = (di + offset) % n
                ccw_idx = (di - offset) % n
                # Same seat at offset == n/2 (even n) — dedupe so we
                # don't double-prompt.
                cand_indices: List[int] = []
                for idx in (cw_idx, ccw_idx):
                    if idx == di:
                        continue
                    if idx in cand_indices:
                        continue
                    cand_indices.append(idx)
                hit_ids: List[int] = []
                for idx in cand_indices:
                    target = ordered[idx]
                    if target.character is None:
                        continue
                    if self._seat_passes(
                        engine, target, minion_check, cache
                    ):
                        hit_ids.append(target.id)
                if hit_ids:
                    if best_distance is None or offset < best_distance:
                        best_distance = offset
                        nearest_minion_player_ids = list(hit_ids)
                    elif offset == best_distance:
                        nearest_minion_player_ids.extend(hit_ids)
                    # No need to walk further from this demon.
                    break

        demon_player_ids = [ordered[i].id for i in demon_indices]
        # De-dupe nearest_minion_player_ids while preserving order.
        seen: set = set()
        nearest_unique: List[int] = []
        for pid in nearest_minion_player_ids:
            if pid in seen:
                continue
            seen.add(pid)
            nearest_unique.append(pid)
        return best_distance, demon_player_ids, nearest_unique

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        # First night only.
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        ordered = sorted(engine.players, key=lambda p: p.seat)
        n = len(ordered)

        default_distance, demon_ids, nearest_minion_ids = (
            self._compute_default_distance(engine, ordered)
        )

        # Possible reading range. The maximum useful distance on an
        # N-seat ring is ``floor(N / 2)`` — past that the other walking
        # direction is shorter. We surface the full ``1..max_dist``
        # set to the Storyteller for the drunk/poisoned prompt.
        max_dist = max(1, n // 2)

        # If there's no demon / no minion in play (degenerate script),
        # default to 1 — the engine still has to show *something* to
        # the seat. The Storyteller can override on the drunk/poisoned
        # prompt; for sober readings this just means the Clockmaker
        # learns "1" in the no-evil-or-no-minion edge case, which is
        # closest to the spirit of the ability.
        sober_default = default_distance if default_distance is not None else 1
        # Clamp into the valid display range.
        sober_default = min(max(sober_default, 1), max_dist)

        if is_drunk_or_poisoned:
            choices = [str(i) for i in range(1, max_dist + 1)]
            wrong_options = [
                i for i in range(1, max_dist + 1) if i != sober_default
            ]
            default_wrong = (
                _rand.choice(wrong_options) if wrong_options else sober_default
            )
            prompt = SelectCharacterPrompt(
                text="Distance to show",
                eligible_characters=choices,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_distance",
                    "stage": "st_pre",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": str(default_wrong),
                    "correct": str(sober_default),
                    "demon_player_ids": list(demon_ids),
                    "nearest_minion_player_ids": list(nearest_minion_ids),
                    "max_distance": max_dist,
                },
            )
            chosen = engine.send_prompt(prompt)
            try:
                shown = int(chosen)
            except (TypeError, ValueError):
                shown = default_wrong
            # Clamp the storyteller's answer back into a valid range.
            shown = min(max(shown, 1), max_dist)
        else:
            shown = sober_default

        # WAKEUP — answer locked in; physically wake the Clockmaker.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        if shown == 1:
            info_text = (
                "The Demon's nearest Minion is 1 step away."
            )
        else:
            info_text = (
                f"The Demon's nearest Minion is {shown} steps away."
            )

        # The Clockmaker's reading is just an integer — the player
        # doesn't pick anyone and isn't supposed to learn *which* seat
        # is the nearest Minion. Highlighting the minion seat(s) on the
        # storyteller's screen would leak that identity to the player
        # the moment the SHOW THIS TO PLAYER screen is rotated their
        # way (the chairs/circles render the same on both views). So
        # we send no player highlights here; the demon and nearest-
        # minion seat ids are still surfaced via ``meta`` for audit /
        # debugging on the storyteller side.
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    "distance": shown,
                    # Suppress all chair highlighting — including the
                    # default "light up the target's seat" behavior.
                    # The Clockmaker themselves shouldn't be highlighted
                    # on the SHOW THIS TO PLAYER screen, since any
                    # highlighted circle there is information the
                    # player can see.
                    "no_highlight": True,
                    # Player phone shows just the explanation sentence —
                    # ``label=""`` tells the renderer to skip the
                    # big-digit slot, matching the Empath / Chambermaid
                    # info-character convention.
                    "render": {
                        "tokens": [
                            {"label": "", "body": info_text},
                        ],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[self.player],
                data={"info": info_text, "distance": shown},
            )
        )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )
