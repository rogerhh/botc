"""Goon.

    "Each night, the 1st player to choose you with their ability is
     drunk until dusk. You become their alignment."

The Goon punishes the first player who targets them at night by
making that player drunk. The Goon's own alignment flips to match
the targeting player's alignment.

Implementation (registry-managed)
---------------------------------
* The Goon's ``reaction()`` listens on every ``SELECT`` event during
  night phases. The first event whose ``source.player.id !=
  self.player.id`` and whose ``targets`` contain the Goon's seat
  triggers the drunkenization — operationalized as
  ``engine.add_effect(GoonDrunkEffect(...))``.
* The "first per night" gate is structural: query the registry for
  any active :class:`GoonDrunkEffect` already sourced by this Goon.
  If present, the trigger has already fired this night. No private
  per-night latch needed.
* Because SELECT is dispatched *before* the source's RESOLUTION (and
  before ability-internal mutations gated on ``has_ability``), making
  the source drunk during the SELECT reaction means the source's own
  resolution code reads ``has_ability=False`` and performs no real
  effect. ``add_effect`` triggers immediate
  :meth:`Engine.resolve_droison_state` so the registry-derived
  ``Player.drunk`` flips True synchronously inside the SELECT
  reaction, before the source's RESOLUTION runs.
* Alignment flips immediately on the same SELECT reaction. The
  Goon's actual ``Player.alignment`` mutates so future reads see
  the new alignment.
* At dusk, :meth:`GoonDrunkEffect.on_phase_boundary` purges itself
  and the resolver clears the registry-managed drunk.

Drunkenness / poisoning
-----------------------
Per the wiki: "The Goon still changes alignment, and makes the
player drunk, if the player choosing the Goon was already drunk or
poisoned." So the trigger fires regardless of the source's state —
only the Goon's *own* ``has_ability`` matters. A drunk/poisoned Goon
does not retort; the SELECT passes through unanswered.

Assassin override
-----------------
Per the wiki: "If chosen by the Assassin, the Goon dies but still
turns evil." This is the documented Assassin special-case (Q-batch4-1
in the design doc): Assassin's once-per-game force-kill bypasses the
Goon's drunkening on Assassin (Assassin doesn't re-check has_ability
between SELECT and kill dispatch). On the Goon's side: alignment
still flips, drunk effect still added — both happen during SELECT,
before the kill lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import Alignment, CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine


class GoonDrunkEffect(Effect):
    """The Goon's reciprocal drunkening, lasting until dusk."""

    kind = "goon_drunk"
    contributes_to_state = "drunk"

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dusk":
            engine.purge_effect(self)


class Goon(Character):
    name = "Goon"
    char_type = CharType.OUTSIDER
    ability_text = (
        "Each night, the 1st player to choose you with their ability is "
        "drunk until dusk. You become their alignment."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = [
        {"name": "DRUNK", "icon": "goon_drunk.png"},
    ]

    def reaction(self, event: Event, engine: "Engine") -> None:
        if (
            event.type is EventType.SELECT
            and self.player is not None
            and self.player.has_ability
            and event.source is not None
            and event.source.player is not None
            and event.source.player.id != self.player.id
            and event.targets
            and any(t.id == self.player.id for t in event.targets)
            and engine.phase.is_night
        ):
            # First-per-night gate: have we already added a
            # GoonDrunkEffect this night? If so, this is a subsequent
            # SELECT and the wiki says it has no effect.
            already = engine.effects_sourced_by(self)
            if any(isinstance(e, GoonDrunkEffect) for e in already):
                return super().reaction(event, engine)

            source_player = event.source.player
            engine.add_effect(GoonDrunkEffect(
                source=self, targets=[source_player.id],
            ))

            # Flip the Goon's alignment to match. Pure state mutation
            # on the Goon's own seat; not an effect (no token,
            # intrinsic to being chosen first this night).
            new_alignment = source_player.alignment
            if (
                new_alignment is not None
                and self.player.alignment != new_alignment
            ):
                old = self.player.alignment
                self.player.alignment = new_alignment
                engine.log_reaction(
                    "Goon",
                    (
                        f"{self.player.name}: alignment flips "
                        f"{(old.value if old else '?')} -> "
                        f"{new_alignment.value} (chosen by "
                        f"{source_player.name})."
                    ),
                    target=self.player,
                    trigger="goon_select",
                )
            else:
                engine.log_reaction(
                    "Goon",
                    f"{self.player.name}: drunkens {source_player.name}.",
                    target=self.player,
                    trigger="goon_select",
                )

        return super().reaction(event, engine)
