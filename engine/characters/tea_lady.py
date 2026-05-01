"""Tea Lady.

    "If both your alive neighbors are good, they can't die."

Passive protection ability. The Tea Lady's *alive neighbours* are the
two alive players closest to her — one clockwise and one counter-
clockwise, skipping past dead seats. While both of those neighbours
are good, neither can die from any cause (Demon kill, Godfather,
Gossip, execution, etc.).

The implementation listens on :class:`EventType.PRE_DEATH` and
cancels the death whenever the dying seat is one of the Tea Lady's
two alive neighbours and both of those neighbours are currently good.
The cancellation channel is the same ``event.data["cancelled"]``
flag used by Mayor / Pacifist / Sailor / Fool.

The two CANNOT DIE reminder tokens surface continuously through
:meth:`compute_reminder_tokens` — visibility is purely a function of
state, so the moment a neighbour dies and a new neighbour cycles into
view, the tokens reposition automatically without any per-character
event reaction.

Drunkenness / poisoning
-----------------------
A drunk or poisoned Tea Lady does not protect her neighbours.
Reaction and reminder-token visibility both gate on
``self.player.has_ability``.

Alignment scoping
-----------------
We compare each alive neighbour's actual ``Player.alignment`` to
:class:`Alignment.GOOD`. The wiki says "currently good" — a Recluse
seated next to the Tea Lady is alignment=GOOD even if they may
register as evil for *detection* abilities elsewhere; the protection
follows the seat's true alignment. If the storyteller wants to flip
a Recluse's effective alignment for a specific ruling, the existing
:meth:`Engine.set_alignment` mutator handles it and the Tea Lady's
state-driven reminder updates accordingly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.enums import Alignment, CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class TeaLady(Character):
    name = "Tea Lady"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "If both your alive neighbors are good, they can't die."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = [
        {"name": "CANNOT DIE", "icon": "tea_lady_cannot_die.png"},
    ]

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _alive_neighbours(self, engine: "Engine") -> List["Player"]:
        """Return the Tea Lady's two alive neighbours, skipping dead seats.

        The seating ring is read off ``engine.players`` (already sorted
        by seat). Neighbours are computed dynamically every call so any
        seat-state change (someone dies, someone is revived, alignment
        flip) is immediately reflected without bookkeeping.
        """
        if self.player is None:
            return []
        ring = engine.players
        n = len(ring)
        if n == 0:
            return []
        try:
            idx = next(i for i, p in enumerate(ring) if p.id == self.player.id)
        except StopIteration:
            return []
        # Walk clockwise, then counter-clockwise, picking the first alive
        # seat we land on that isn't the Tea Lady herself.
        def walk(step: int) -> Optional["Player"]:
            for k in range(1, n):
                p = ring[(idx + step * k) % n]
                if p.id == self.player.id:
                    continue
                if p.alive:
                    return p
            return None

        cw = walk(+1)
        ccw = walk(-1)
        out: List["Player"] = []
        for p in (cw, ccw):
            if p is not None and p.id not in {q.id for q in out}:
                out.append(p)
        return out

    def _both_neighbours_good(self, engine: "Engine") -> bool:
        neighbours = self._alive_neighbours(engine)
        if len(neighbours) < 2:
            # With only 0 or 1 alive neighbour, the rulebook's "both
            # alive neighbours" condition cannot be met: in a 3-player
            # endgame this is the corner case where the Tea Lady has
            # only one distinct alive neighbour, so we treat it as not
            # protecting.
            return False
        return all(p.alignment is Alignment.GOOD for p in neighbours)

    # ------------------------------------------------------------------
    # Reminder tokens.
    # ------------------------------------------------------------------

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Place the CANNOT DIE token on each protected neighbour.

        Visibility is purely state-driven: the Tea Lady must be alive
        and have her ability, and both alive neighbours must currently
        be good. Otherwise no tokens.
        """
        if self.player is None or not self.player.has_ability:
            return {}
        if not self._both_neighbours_good(engine):
            return {}
        ids = [p.id for p in self._alive_neighbours(engine)]
        if not ids:
            return {}
        return {"tea_lady_cannot_die": ids}

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        if (
            event.type is EventType.PRE_DEATH
            and self.player is not None
            and self.player.has_ability
            and not event.data.get("cancelled")
            and event.targets
        ):
            target = event.targets[0]
            neighbours = self._alive_neighbours(engine)
            if any(p.id == target.id for p in neighbours):
                if self._both_neighbours_good(engine):
                    event.data["cancelled"] = True
                    engine.log_reaction(
                        "Tea Lady",
                        (
                            f"{target.name} cannot die "
                            f"(Tea Lady protects good neighbours)."
                        ),
                        target=target,
                        trigger="pre_death",
                        effect="tea_lady_neighbour_protected",
                    )
                    return
        return super().reaction(event, engine)
