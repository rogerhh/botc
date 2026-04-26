"""Scarlet Woman.

    "If there are 5 or more players alive and the Demon dies, you
     become the Demon."

Reaction-based promotion. The Scarlet Woman watches every ``DEATH``
event; if it kills the Demon AND there were 5+ non-Traveler players
alive *just before* the death, the Scarlet Woman becomes the Demon.

The "alive count just before the death" is reconstructable: at the
time the reaction fires the Demon is already dead, so we add 1 to
the current alive count. Travelers do not count toward the threshold.

If the Demon kills themself at night (Imp self-kill), the Scarlet
Woman *must* take over as the new Imp.

Implementation
--------------
We become the Demon by mutating our character via
:meth:`engine.engine.Engine.change_character`, which builds a fresh
:class:`Imp` instance and rewires our :class:`Player`'s reference.
After this point, the engine's normal flow continues — including the
post-DEATH win check, which will now see the new Demon alive and
*not* declare a good win.

Caveat: change_character has to happen *before* the engine's
``_check_win_conditions`` runs (which is invoked synchronously after
the DEATH event is dispatched). Since reactions are executed as part
of the dispatch loop *before* control returns to ``Engine.kill``, we
satisfy that ordering.

Drunkenness / poisoning: a drunk or poisoned Scarlet Woman does NOT
take over.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine

class ScarletWoman(Character):
    name = "Scarlet Woman"
    char_type = CharType.MINION
    ability_text = (
        "If there are 5 or more players alive and the Demon dies, you "
        "become the Demon."
    )
    first_night_order = 0
    other_night_order = 15
    reminder_tokens: list = [
        {"name": 'IS THE DEMON', "icon": 'scarlet_woman_is_the_demon.png'},
    ]

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        # The Scarlet Woman never *takes a night action* — her promotion
        # to Demon happens via reaction the moment the Demon dies. The
        # entry in ``night.txt`` exists so the storyteller can show
        # "YOU ARE the new Imp" on the night she just promoted, but at
        # that point the seated player's character has already been
        # changed to Imp (see ScarletWoman.reaction). So the SW step
        # in the preset never matches an in-play character anyway —
        # we still return False here for clarity and so a stale
        # SW player (e.g. resurrected) doesn't trigger a phantom step.
        return False

    def reaction(self, event: Event, engine: "Engine") -> None:
        if self.player is None or self.player.dead:
            return super().reaction(event, engine)
        if not self.player.has_ability:
            return super().reaction(event, engine)
        if event.type is not EventType.DEATH:
            return super().reaction(event, engine)
        if not event.targets:
            return super().reaction(event, engine)

        target = event.targets[0]
        # Only react when the dying player WAS the Demon.
        if target.char_type is not CharType.DEMON:
            return super().reaction(event, engine)
        # Don't react to the Scarlet Woman's own death (defensive).
        if target.id == self.player.id:
            return super().reaction(event, engine)

        # Alive count just before the death = current alive + 1
        # (since the target has just been flipped to dead, and the
        # Scarlet Woman herself is still alive).
        alive_now = [
            p for p in engine.alive_players
            if p.char_type not in (CharType.TRAVELER, CharType.FABLED)
        ]
        # The just-killed Demon would have been counted before death.
        alive_before = len(alive_now) + 1
        if alive_before < 5:
            engine.log(
                f"Demon died with only {alive_before} players alive — "
                f"Scarlet Woman does not become the Demon."
            )
            return super().reaction(event, engine)

        # Promote: become the same Demon that just died.
        new_demon = target.character.name if target.character else "Imp"
        engine.log(
            f"Scarlet Woman {self.player.name} becomes the {new_demon}."
        )
        engine.change_character(self.player.id, new_demon)
        # Persist evil alignment — change_character preserves alignment.
        return super().reaction(event, engine)
