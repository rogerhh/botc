"""Tea Lady.

    "If both your alive neighbors are good, they can't die."

Passive protection ability. The Tea Lady's *alive neighbours* are the
two alive players closest to her — one clockwise and one counter-
clockwise, skipping past dead seats. While both of those neighbours
are good, neither can die from any cause (Demon kill, Godfather,
Gossip, execution, etc.).

Implementation (registry-managed)
---------------------------------
The Tea Lady emits one :class:`TeaLadyCannotDieEffect` per protected
neighbour (Option B from the design doc). The set of effects is
*refreshed* whenever the seating-ring composition could change:

  * SETUP_END — initial computation when the game starts.
  * DEATH / REVIVE on any seat — neighbours may shift.
  * ALIGNMENT_CHANGE on any seat — Goon flip, etc.

A refresh purges all currently-emitted Tea Lady effects and re-emits
fresh ones for the current good-neighbour pair. The effect's
``resolve_event`` cancels any PRE_DEATH on its target (any cause —
Tea Lady's protection is broad), respecting the ``force`` flag for
Assassin's bypass.

The standard Effect lifecycle handles the easy cases:

  * ``purge_on_source_death = True`` — dead Tea Lady purges all her
    effects, so subsequent kills on former neighbours land normally.
  * ``deactivate_on_source_droisoned = True`` — drunk/poisoned Tea
    Lady's effects deactivate, so neighbours lose protection.

Alignment scoping follows the seat's true ``Player.alignment`` —
Recluse misregistration doesn't apply (the wiki ruling).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import Alignment, CharType
from engine.event import Event, EventOutcome, EventType

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class TeaLadyCannotDieEffect(Effect):
    """Tea Lady's per-neighbour cannot-die protection.

    Cancels any PRE_DEATH on the target unless ``force=True``.
    Lifetime is until-source-death (no phase boundary expiry); the
    Tea Lady's ``reaction()`` refreshes the effect list when the
    seating ring changes."""

    kind = "tea_lady_cannot_die"
    contributes_to_state = None

    def resolve_event(
        self, engine: "Engine", event: Event
    ) -> Optional[EventOutcome]:
        if (
            event.type is EventType.PRE_DEATH
            and not event.data.get("cancelled")
            and not event.data.get("force")
        ):
            event.data["cancelled_by_character"] = "Tea Lady"
            event.data["cancelled_reason"] = (
                "Tea Lady protects good neighbours"
            )
            try:
                tgt = event.targets[0] if event.targets else None
                tgt_name = tgt.name if tgt else "?"
                engine.log_reaction(
                    "Tea Lady",
                    (
                        f"{tgt_name} cannot die "
                        f"(Tea Lady protects good neighbours)."
                    ),
                    target=tgt,
                    trigger="pre_death",
                    effect="tea_lady_neighbour_protected",
                )
            except Exception:  # pragma: no cover (defensive)
                pass
            return EventOutcome.CANCEL
        return None


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

        The seating ring is read off ``engine.players``. Neighbours
        are computed dynamically every call so any seat-state change
        is immediately reflected without bookkeeping.
        """
        if self.player is None:
            return []
        ring = engine.players
        n = len(ring)
        if n == 0:
            return []
        try:
            idx = next(
                i for i, p in enumerate(ring) if p.id == self.player.id
            )
        except StopIteration:
            return []

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

    def _refresh_effects(self, engine: "Engine") -> None:
        """Purge all Tea Lady-sourced effects and re-emit for the
        current good-neighbour pair (if both neighbours are good and
        the Tea Lady is alive + has-ability).

        Idempotent and cheap: called from every event that could
        change the relevant state (DEATH, REVIVE, ALIGNMENT_CHANGE,
        and at SETUP_END for the initial population).
        """
        # Purge any existing Tea Lady effects.
        for eff in list(engine.effects_sourced_by(self)):
            if isinstance(eff, TeaLadyCannotDieEffect):
                engine.purge_effect(eff)

        # Conditions for protection: Tea Lady alive + has-ability,
        # both alive neighbours good (i.e. exactly two of them and
        # both good).
        if self.player is None or not self.player.alive or not self.player.has_ability:
            return
        neighbours = self._alive_neighbours(engine)
        if len(neighbours) < 2:
            return
        if not all(p.alignment is Alignment.GOOD for p in neighbours):
            return

        for p in neighbours:
            engine.add_effect(TeaLadyCannotDieEffect(
                source=self, targets=[p.id],
            ))

    # ------------------------------------------------------------------
    # Reaction — refresh trigger.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        # Refresh on ring-shape or alignment changes. Cheap: O(N) per
        # event, called only on the relevant kinds.
        if event.type in (
            EventType.SETUP_END,
            EventType.DEATH,
            EventType.REVIVE,
            EventType.ALIGNMENT_CHANGE,
        ):
            self._refresh_effects(engine)
        return super().reaction(event, engine)
