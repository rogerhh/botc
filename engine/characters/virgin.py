"""Virgin.

    "The 1st time you are nominated, if the nominator is a Townsfolk,
     they are executed immediately."

Reaction-based daytime ability. The Virgin's "trigger" is the first
nomination targeted at them. We listen for ``NOMINATION`` events on
ourselves; on the first hit, if the nominator registers as a Townsfolk
(via :meth:`Character.registers_as` with categories=(TOWNSFOLK,)) the
nominator is killed by the Virgin's ability. So a Spy nominator may
register as a Townsfolk and trigger the Virgin's kill; the Recluse
override does not fire for a TOWNSFOLK-only check. Once consumed
(success or not), the ability is spent.

The flavour text says "executed", but mechanically this kill uses
``DeathCause.ABILITY``: the engine reserves ``DeathCause.EXECUTION``
for deaths produced by the Storyteller's Execute button
(``Engine.execute_player``). The Virgin therefore does NOT dispatch
an ``EXECUTION`` event and does NOT set ``Engine._executed_today``.
Saint loss, Mastermind extension, Pacifist / Devil's Advocate saves,
Undertaker info, Minstrel drunkness, etc. all key off the EXECUTION
event or ``DeathCause.EXECUTION`` and so do not fire on the Virgin's
kill.

Drunkenness / poisoning: a drunk or poisoned Virgin still consumes
their trigger but does NOT kill the nominator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine


class VirginNoAbilityEffect(Effect):
    """NO ABILITY marker on the Virgin's seat once the trigger fires."""

    kind = "virgin_no_ability"
    contributes_to_state = None
    purge_on_source_death = False
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


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

    # NO ABILITY marker emitted via VirginNoAbilityEffect when the
    # trigger fires; rendered via the registry.

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
        engine.add_effect(VirginNoAbilityEffect(
            source=self, targets=[self.player.id],
        ))

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

        # Kill the nominator on the spot. This is an ability-driven
        # death, not an execution: ``DeathCause.EXECUTION`` is reserved
        # for deaths that originate from the Storyteller's Execute
        # button (``Engine.execute_player``). The Virgin's own kill
        # therefore uses ``DeathCause.ABILITY`` and does NOT dispatch
        # an ``EXECUTION`` event — only ``Engine.kill``'s standard
        # ``PRE_DEATH`` / ``DEATH`` flow runs.
        engine.log_reaction(
            "Virgin",
            (
                f"{self.player.name}'s ability kills {nominator.name} "
                f"(first-nomination ability)."
            ),
            target=self.player,
            trigger="nomination",
            effect="kill_nominator",
            nominator_id=nominator.id,
            nominator_name=nominator.name,
        )
        engine.kill(nominator.id, DeathCause.ABILITY, source=self)
        return super().reaction(event, engine)
