"""Virgin.

    "The 1st time you are nominated, if the nominator is a Townsfolk,
     they are executed immediately."

Reaction-based daytime ability. The Virgin's "trigger" is the first
nomination targeted at them. We listen for ``NOMINATION`` events on
ourselves; on the first hit, if the nominator is a Townsfolk, the
nominator is executed. Once consumed (success or not), the ability
is spent.

Drunkenness / poisoning: a drunk or poisoned Virgin still consumes
their trigger but does NOT execute the nominator. When sober and
healthy the engine acts directly on the nominator's actual
``char_type`` — no ST verify/override prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine

class Virgin(Character):
    name = "Virgin"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "The 1st time you are nominated, if the nominator is a Townsfolk, "
        "they are executed immediately."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = [
        {"name": 'NO ABILITY', "icon": 'virgin_no_ability.png'},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Tracks whether the first-nomination trigger has been spent.
        self._triggered: bool = False

    def reaction(self, event: Event, engine: "Engine") -> None:
        if self.player is None:
            return super().reaction(event, engine)
        if event.type is not EventType.NOMINATION:
            return super().reaction(event, engine)
        # The nomination must target this Virgin.
        if not any(t.id == self.player.id for t in event.targets):
            return super().reaction(event, engine)
        if self._triggered:
            return super().reaction(event, engine)

        # Always consume the trigger on first nomination, even when the
        # ability has no effect (drunk/poisoned, evil nominator, …).
        self._triggered = True

        nominator_id = event.data.get("nominator_id")
        nominator = None
        if nominator_id is not None:
            try:
                nominator = engine.get_player(int(nominator_id))
            except (KeyError, ValueError, TypeError):
                nominator = None

        # Drunk/poisoned Virgin: trigger is spent, but no execution.
        if not self.player.has_ability:
            engine.log(
                f"Virgin {self.player.name} nominated by "
                f"{nominator.name if nominator else '?'} — "
                f"ability spent (Virgin drunk/poisoned)."
            )
            return super().reaction(event, engine)

        # Determine whether the nominator counts as a Townsfolk. The
        # Virgin is sober + healthy at this point (drunk/poisoned was
        # handled above), so we trust the nominator's actual char_type
        # — no ST verify/override prompt.
        if nominator is None:
            return super().reaction(event, engine)
        is_townsfolk = nominator.char_type is CharType.TOWNSFOLK
        if not is_townsfolk:
            engine.log(
                f"Virgin {self.player.name} nominated by "
                f"{nominator.name} — not a Townsfolk; ability "
                f"spent without effect."
            )
            return super().reaction(event, engine)

        # Execute the nominator on the spot.
        engine.log(
            f"Virgin {self.player.name} executes {nominator.name} "
            f"(first-nomination ability)."
        )
        engine.kill(nominator.id, DeathCause.EXECUTION)
        engine.dispatch(
            Event(
                EventType.EXECUTION,
                source=self,
                targets=[nominator],
                data={"cause": DeathCause.EXECUTION},
            )
        )
        return super().reaction(event, engine)
