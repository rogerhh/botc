"""Exorcist.

    "Each night*, choose a player (different to last night): the
     Demon, if chosen, learns who you are then doesn't wake tonight."

The Exorcist names a player each night except the first; if the
named player is the Demon, the Demon does not wake to attack
tonight (but other reaction-based Demon abilities — Zombuul stays
alive when killed, Pukka kills the previously poisoned player —
still function because they are *not* the Demon's nightly attack
ability).

Implementation (registry-managed)
---------------------------------
* Each night except the first, the Exorcist picks any player.
* The picked seat gets an :class:`ExorcistChosenEffect` token marker
  for storyteller bookkeeping. Purges at the next dusk (so the
  marker stays visible through the day after, then clears at the
  transition into the next night).
* The "Demon doesn't wake tonight" rule is operationalized via
  ``engine._exorcism_blocked_id`` — set when the chosen seat is the
  Demon and the Exorcist has ability. Demons consult this flag at
  the start of their nightly ability and short-circuit. Cleared at
  the next NIGHT_START via the Exorcist's reaction (or whenever a
  fresh exorcism overwrites it).

Drunkenness / poisoning
-----------------------
A drunk/poisoned Exorcist still picks (the storyteller still walks
them through), but neither the registry effect nor the engine block
flag are set, so the Demon acts normally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class ExorcistChosenEffect(Effect):
    """Visual marker on the seat the Exorcist picked tonight.

    Persists through the day after for storyteller bookkeeping;
    purges at the next dusk."""

    kind = "exorcist_chosen"
    contributes_to_state = None

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dusk":
            engine.purge_effect(self)


class Exorcist(Character):
    name = "Exorcist"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night*, choose a player (different to last night): the "
        "Demon, if chosen, learns who you are then doesn't wake tonight."
    )
    first_night_order = 0
    other_night_order = 21
    reminder_tokens: list = [
        {"name": "CHOSEN", "icon": "exorcist_chosen.png"},
    ]

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # The seat picked on the previous night (so we can forbid
        # picking the same seat two nights in a row). Reset on revive.
        self._previous_pick_id: Optional[int] = None

    def reaction(self, event: Event, engine: "Engine") -> None:
        if event.type is EventType.NIGHT_START:
            # Clear the per-night exorcism block flag.
            if hasattr(engine, "_exorcism_blocked_id"):
                engine._exorcism_blocked_id = None
        return super().reaction(event, engine)

    def on_revive(self, engine: "Engine") -> None:
        super().on_revive(engine)
        self._previous_pick_id = None

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Eligible: any player (alive or dead — the wiki tip says you
        # can pick dead players to test for a Zombuul). Exclude only
        # the seat picked last night.
        eligible = [
            p.id for p in engine.players
            if p.id != self._previous_pick_id
        ]
        if not eligible:
            return
        sel = SelectPlayerPrompt(
            text="Exorcist exorcises a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_player",
                "stage": "player",
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0] if target_id else None
        if target_id is None:
            return
        try:
            target = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        self._previous_pick_id = target.id

        # RESOLUTION: emit the chosen-marker effect via the registry,
        # AND set the engine-level Demon block flag if applicable.
        # A drunk/poisoned Exorcist emits no effect and no block.
        if self.player.has_ability:
            engine.add_effect(ExorcistChosenEffect(
                source=self, targets=[target.id],
            ))
            if (
                target.character is not None
                and target.char_type is CharType.DEMON
                and target.alive
            ):
                if not hasattr(engine, "_exorcism_blocked_id"):
                    engine._exorcism_blocked_id = None
                engine._exorcism_blocked_id = target.id
                engine.log(
                    f"Exorcist {self.player.name} blocks the Demon "
                    f"{target.name} from acting tonight."
                )
                # Reveal to the Demon: wake them and show the
                # Exorcist token. We do *not* run the Demon's
                # nightly ability after.
                engine.dispatch(
                    Event(EventType.WAKEUP, source=self, targets=[target])
                )
                engine.send_prompt(
                    InformationPrompt(
                        text=(
                            f"This character selected you — Exorcist "
                            f"({self.player.name})."
                        ),
                        target_player_id=target.id,
                        shown_to_player=True,
                        highlight_player_ids=[self.player.id],
                        highlight_characters=[self.name],
                        meta={
                            "character": self.name,
                            "step": "demon_reveal",
                            "stage": "info",
                            "target_player_name": target.name,
                            "render": {
                                "tokens": [{
                                    "label": "EXORCIST",
                                    "body": self.player.name,
                                }],
                            },
                        },
                    )
                )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
