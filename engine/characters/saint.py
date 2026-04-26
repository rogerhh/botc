"""Saint.

    "If you die by execution, your team loses."

Reaction-based loss condition. The Saint has no nightly action; instead
we override :meth:`reaction` to listen for an
:class:`engine.event.EventType.EXECUTION` event whose target is the
Saint's own player. When that fires (and the Saint is sober &
healthy), the Saint's team loses — i.e. evil wins.

Drunkenness / poisoning: per the rulebook, a drunk or poisoned Saint
does NOT trigger their loss condition. This matches the engine's
``Player.has_ability`` semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import Alignment, CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine

class Saint(Character):
    name = "Saint"
    char_type = CharType.OUTSIDER
    ability_text = "If you die by execution, your team loses."
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    def reaction(self, event: Event, engine: "Engine") -> None:
        """Lose-condition: an executed Saint (with ability) ends the game.

        The Saint reaction fires *during* the execution event. The
        engine's ``execute_player`` already calls
        ``_check_win_conditions`` after dispatching the EXECUTION event,
        so by ending the game here we override the default
        "Demon-still-alive → no win yet" check.
        """
        if self.player is None:
            return super().reaction(event, engine)
        if event.type is not EventType.EXECUTION:
            return super().reaction(event, engine)
        # The execution event must target this Saint specifically.
        if not any(t.id == self.player.id for t in event.targets):
            return super().reaction(event, engine)
        # Ability gating: a drunk/poisoned Saint does NOT trigger.
        # Note: we check ``has_ability`` at the moment of the reaction;
        # the player is already dead at this point (kill happened
        # before the EXECUTION event was dispatched), so we read the
        # drunk/poisoned bits — alive-ness is NOT a precondition for
        # the Saint losing the game (the ability triggers *as* they
        # die).
        if self.player.drunk or self.player.poisoned:
            engine.log(
                f"Saint {self.player.name} executed while drunk/poisoned; "
                f"loss condition does not trigger."
            )
            return super().reaction(event, engine)
        engine.log(
            f"Saint {self.player.name} executed — evil wins."
        )
        # End-game directly. Engine state is already FINISHED-eligible.
        engine._end_game(Alignment.EVIL, "The Saint was executed.")
        return super().reaction(event, engine)
