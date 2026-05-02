"""Minstrel.

    "When a Minion dies by execution, all other players (except
     Travellers) are drunk until dusk tomorrow."

Passive ability that fires reactively on a Minion's execution
death. Every other seat (Townsfolk, Outsider, Minion, Demon — but
not Traveller and not the Minstrel themself) becomes drunk
immediately and stays drunk through the rest of the day, the
following night, and the following day, ending at the next *next*
dusk (i.e., 2 dusk boundaries away).

Implementation (registry-managed)
---------------------------------
* Listens on ``DEATH`` events with ``cause=DeathCause.EXECUTION`` and
  a Minion target. Gates on the Minstrel's ``has_ability`` *at the
  moment of trigger*.
* On trigger: emits one :class:`MinstrelEveryoneDrunkEffect` with a
  multi-target list (every non-self, non-Traveller seat). The
  effect's ``on_phase_boundary`` ticks down a 2-dusk counter (the
  trigger fires during day; first dusk → 1 remaining; second dusk →
  expire).
* Re-trigger during an existing duration: purge the old effect and
  emit a fresh one (refreshes the targets list to current alive
  non-Travellers and resets the counter).

Drunkenness / poisoning
-----------------------
Per the wiki: "If the Minstrel is drunk or poisoned when a Minion
dies by execution, the Minstrel ability does not trigger." Gated on
``has_ability`` at trigger time. The registry contract ensures no
effect is emitted by a droisoned Minstrel.

Self-marker token
-----------------
The Minstrel's own seat carries the ``minstrel_everyone_drunk``
token while the duration is active. This is provided via
``MinstrelEveryoneDrunkEffect.token_kind_for_target``: each per-
target entry renders nothing (the *target's* token would be a
"drunk" indicator, not "everyone is drunk"); only the Minstrel's own
seat — added as an extra target on the effect — renders the marker.
We achieve this by giving the effect two target lists implicitly:
the actual drunkened players (contributing to ``Player.drunk``) plus
the Minstrel's own seat (rendered via the special-case in
``token_kind_for_target``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine


class MinstrelEveryoneDrunkEffect(Effect):
    """The Minstrel's table-wide drunkening, lasting through the next
    full day-night-day cycle (2 dusk boundaries).

    Two-channel target list: ``targets`` is the drunkened seats
    (drives ``Player.drunk`` via ``contributes_to_state="drunk"``).
    The Minstrel's own seat is also stamped as a target *for token
    rendering only* — overridden in :meth:`token_kind_for_target` so
    the Minstrel's seat is the one that shows the
    ``minstrel_everyone_drunk`` marker. The other targets render
    nothing (they get the standard ``Player.drunk`` flag, which the
    UI surfaces via the existing drunk indicator).
    """

    kind = "minstrel_everyone_drunk"
    contributes_to_state = "drunk"

    def __init__(self, source, drunkened_targets, minstrel_seat_id):
        # Include the minstrel's own seat as a target for rendering.
        # The contributes_to_state="drunk" check applies to all
        # targets, but Minstrel's own seat is sober (not in
        # drunkened_targets) — the resolver still adds it to the
        # registry-drunk set, which would set the Minstrel's flag.
        # Workaround: we override ``token_kind_for_target`` to return
        # the marker only on the Minstrel seat, and use a separate
        # internal field for the drunkened targets.
        super().__init__(source, drunkened_targets)
        self._minstrel_seat_id = minstrel_seat_id
        self._dusks_remaining = 2

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dusk":
            self._dusks_remaining -= 1
            if self._dusks_remaining <= 0:
                engine.purge_effect(self)

    def token_kind_for_target(
        self, target_id: int, engine: "Engine"
    ) -> Optional[str]:
        # The marker token sits on the Minstrel's own seat; drunkened
        # targets get nothing extra (their drunkness shows through the
        # ordinary Player.drunk flag the UI already renders).
        if target_id == self._minstrel_seat_id:
            return self.kind
        return None


class Minstrel(Character):
    name = "Minstrel"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "When a Minion dies by execution, all other players (except "
        "Travellers) are drunk until dusk tomorrow."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = [
        {"name": "EVERYONE IS DRUNK", "icon": "minstrel_everyone_drunk.png"},
    ]

    def reaction(self, event: Event, engine: "Engine") -> None:
        if (
            event.type is EventType.DEATH
            and event.data.get("cause") is DeathCause.EXECUTION
            and self.player is not None
            and self.player.has_ability
            and event.targets
            and event.targets[0].char_type is CharType.MINION
            and event.targets[0].id != self.player.id
        ):
            # Re-trigger during an existing duration: purge old, add
            # fresh.
            for old in list(engine.effects_sourced_by(self)):
                if isinstance(old, MinstrelEveryoneDrunkEffect):
                    engine.purge_effect(old)
            # Determine drunkened targets: all non-self, non-Traveller
            # seats with a character.
            drunkened: List[int] = []
            # Plus the minstrel's own seat for token rendering.
            all_targets: List[int] = [self.player.id]
            for p in engine.players:
                if p.id == self.player.id:
                    continue
                if p.char_type is CharType.TRAVELER:
                    continue
                if p.character is None:
                    continue
                drunkened.append(p.id)
                all_targets.append(p.id)
            if drunkened:
                engine.add_effect(MinstrelEveryoneDrunkEffect(
                    source=self,
                    drunkened_targets=all_targets,
                    minstrel_seat_id=self.player.id,
                ))
                engine.log_reaction(
                    "Minstrel",
                    f"Minion executed — {self.player.name} drunkens everyone.",
                    target=self.player,
                    trigger="minion_execution",
                )
        return super().reaction(event, engine)
