"""Undertaker.

    "Each night except the first, you learn which character died by
     execution today."

Information ability that fires from night 2 onward, but only if the
day immediately before today *had* an execution. Otherwise the
Undertaker is not woken.

Implementation (registry-managed)
---------------------------------
The Undertaker's reaction listens for ``EXECUTION`` events and emits
an :class:`UndertakerDiedTodayEffect` on the executed seat. The
effect is a visual marker only (``contributes_to_state = None``) and
purges at the next dusk — so the marker stays through the night the
Undertaker learns about it, then clears at the dusk into the next
day.

The ``would_act_tonight`` gate and the ``ability`` consumption both
query the registry: an active UndertakerDiedTodayEffect sourced by
this Undertaker means there's something to learn tonight.

The Undertaker uses :meth:`Character.registers_as` (with all four
character-type categories) to learn the executed player's
*registered* role. Spy / Recluse misregistration applies normally.

Drunkenness / poisoning (per CLAUDE.md): storyteller may show any
character. Range of options, so the engine pre-picks a *random
wrong* character and surfaces it to the ST with a Next button.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class UndertakerDiedTodayEffect(Effect):
    """Marker on the seat that died by execution today.

    Lifetime: from execution through next night's Undertaker wake-up,
    purged at the dusk after that. Visual-only — does not gate
    anything except the Undertaker's own ability."""

    kind = "undertaker_died_today"
    contributes_to_state = None
    # The marker is bookkeeping; should persist even if Undertaker
    # gets droisoned (the wrong-info handling lives in the Undertaker's
    # ability path, not here).
    deactivate_on_source_droisoned = False

    def __init__(self, source, targets, executed_character_name: str) -> None:
        super().__init__(source, targets)
        # The character name of the executed seat at the moment of
        # execution (in case the seat changes character afterwards).
        self.executed_character_name = executed_character_name

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        # Purge at dawn — the marker is born during day-N execution,
        # persists through night-N+1 (when the Undertaker consumes
        # it), and clears at dawn into day-N+2.
        if phase == "dawn":
            engine.purge_effect(self)


class Undertaker(Character):
    name = "Undertaker"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night except the first, you learn which character died by "
        "execution today."
    )
    first_night_order = 0
    other_night_order = 52
    reminder_tokens: list = [
        {"name": 'DIED TODAY', "icon": 'undertaker_died_today.png'},
    ]

    DETECTION_CATEGORIES = (
        CharType.TOWNSFOLK,
        CharType.OUTSIDER,
        CharType.MINION,
        CharType.DEMON,
    )

    def _current_died_today_effect(
        self, engine: "Engine"
    ) -> Optional[UndertakerDiedTodayEffect]:
        """Return today's UndertakerDiedTodayEffect sourced by self,
        or None if no execution happened today."""
        for eff in engine.effects_sourced_by(self):
            if isinstance(eff, UndertakerDiedTodayEffect):
                return eff
        return None

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if not super().would_act_tonight(engine, night_number):
            return False
        # Wake only if there's an active died-today effect this night.
        return self._current_died_today_effect(engine) is not None

    def reaction(self, event: Event, engine: "Engine") -> None:
        # On every execution today, emit the died-today marker. If
        # there are multiple executions in a single day (rare but
        # possible — Virgin trigger then a separate vote), the
        # latest execution overwrites.
        if event.type is EventType.EXECUTION and event.targets:
            target = event.targets[0]
            char_name = (
                target.character.name if target.character else None
            )
            # Purge any prior today's effect (only one per day).
            for old in list(engine.effects_sourced_by(self)):
                if isinstance(old, UndertakerDiedTodayEffect):
                    engine.purge_effect(old)
            if char_name is not None:
                engine.add_effect(UndertakerDiedTodayEffect(
                    source=self,
                    targets=[target.id],
                    executed_character_name=char_name,
                ))
        return super().reaction(event, engine)

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        eff = self._current_died_today_effect(engine)
        if eff is None or not eff.targets:
            return
        try:
            executed_player = engine.get_player(eff.targets[0])
        except KeyError:
            return
        executed_character = eff.executed_character_name

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        # Ask the executed player's character what they register as
        # via a name-attribute check.
        from engine.check import Check
        registered_char: Optional[str] = None
        if executed_player.character is not None:
            name_check = Check(
                attribute="name",
                passes=tuple(engine.all_character_names()),
                detector_name=self.name,
                detector_player_id=self.player.id,
                extra_meta={"step_for": "undertaker_executed"},
            )
            registered_char = executed_player.character.registers_as(
                engine, name_check
            )

        # Sober + healthy: trust the registered character. Drunk/
        # poisoned: range of options — pre-pick a random wrong.
        if is_drunk_or_poisoned:
            all_chars = engine.all_character_names()
            wrong_options = [
                c for c in all_chars if c != executed_character
            ]
            default_wrong = (
                _rand.choice(wrong_options)
                if wrong_options else executed_character
            )
            char_prompt = SelectCharacterPrompt(
                text="Character to show",
                eligible_characters=all_chars,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_shown_character",
                    "stage": "st_pre",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": default_wrong,
                    **(
                        {"correct": executed_character}
                        if executed_character else {}
                    ),
                    "executed_player_id": executed_player.id,
                },
            )
            shown = engine.send_prompt(char_prompt)
            if not isinstance(shown, str) or not shown:
                shown = default_wrong
        elif registered_char is not None:
            shown = registered_char
        else:
            shown = executed_character

        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        info_text = (
            f"{executed_player.name} was executed today; "
            f"they were the {shown}."
        )
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[executed_player.id],
                highlight_characters=[shown],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    "render": {
                        "tokens": [{
                            "label": shown.upper(),
                            "body": executed_player.name,
                        }],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[executed_player],
                data={"info": info_text, "shown_character": shown},
            )
        )
        engine.dispatch(
            Event(
                EventType.RESOLUTION,
                source=self,
                targets=[executed_player],
            )
        )
