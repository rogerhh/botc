"""Virgin.

    "The 1st time you are nominated, if the nominator is a Townsfolk,
     they are executed immediately."

Reaction-based daytime ability. The Virgin's "trigger" is the first
nomination targeted at them. We listen for ``NOMINATION`` events on
ourselves; on the first hit, if the nominator registers as a Townsfolk
(via :meth:`Character.registers_as` with categories=(TOWNSFOLK,)) the
nominator is executed. So a Spy nominator may register as a Townsfolk
and trigger the Virgin's execute; the Recluse override does not fire
for a TOWNSFOLK-only check. Once consumed (success or not), the
ability is spent.

Drunkenness / poisoning: a drunk or poisoned Virgin still consumes
their trigger but does NOT execute the nominator.
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

    DETECTION_CATEGORIES = (CharType.TOWNSFOLK,)

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Tracks whether the first-nomination trigger has been spent.
        self._triggered: bool = False

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Persistent NO ABILITY marker once the trigger has fired."""
        if self.player is None or not self._triggered:
            return {}
        return {"virgin_no_ability": [self.player.id]}

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
            engine.log_reaction(
                "Virgin",
                (
                    f"{self.player.name} nominated by "
                    f"{nominator.name if nominator else '?'} — "
                    f"ability spent (Virgin drunk/poisoned)."
                ),
                target=self.player,
                trigger="nomination",
                effect="no_execute_drunk_or_poisoned",
            )
            return super().reaction(event, engine)

        # Determine whether the nominator registers as a Townsfolk.
        # Spy override may fire (its registration_categories include
        # TOWNSFOLK) and register the Spy as some Townsfolk, triggering
        # the Virgin's execute. Recluse override is silent.
        if nominator is None:
            return super().reaction(event, engine)
        from engine.check import Check
        tf_check = Check(
            attribute="char_type",
            passes=(CharType.TOWNSFOLK,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={"step_for": "virgin_nominator"},
        )
        is_townsfolk = self.check(engine, nominator, tf_check)
        if not is_townsfolk:
            engine.log_reaction(
                "Virgin",
                (
                    f"{self.player.name} nominated by {nominator.name} "
                    f"— not a Townsfolk; ability spent without effect."
                ),
                target=self.player,
                trigger="nomination",
                effect="no_execute_not_townsfolk",
                nominator_id=nominator.id,
                nominator_name=nominator.name,
            )
            return super().reaction(event, engine)

        # Execute the nominator on the spot.
        engine.log_reaction(
            "Virgin",
            (
                f"{self.player.name} executes {nominator.name} "
                f"(first-nomination ability)."
            ),
            target=self.player,
            trigger="nomination",
            effect="execute_nominator",
            nominator_id=nominator.id,
            nominator_name=nominator.name,
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
