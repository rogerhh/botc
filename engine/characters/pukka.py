"""Pukka.

    "Each night, choose a player: they are poisoned. The previously
     poisoned player dies then becomes healthy."

The Pukka acts on every night (including the first — unique among
BMR demons). Each night they pick a player; that player is poisoned
immediately. On every night except the first, the *previously*
poisoned player (from the prior night) dies and becomes sober.

Implementation (registry-managed)
---------------------------------
* The previously-poisoned target is tracked by querying the registry
  for active :class:`PukkaPoisonEffect` instances sourced by this
  Pukka. No private bookkeeping needed.
* On night N>=2, before picking, the Pukka kills its previous target
  via :meth:`Engine.kill` with cause ``DEMON_KILL``, then purges the
  old PukkaPoisonEffect (whether or not the kill landed — per wiki:
  "Innkeeper prevents the Pukka from killing a poisoned player, then
  that player is no longer poisoned").
* The new pick is poisoned via ``engine.add_effect(PukkaPoisonEffect
  (...))``. The Pukka's lifecycle hooks (purge_on_source_death,
  deactivate_on_source_droisoned) handle every cleanup path.
* The Exorcist gate (``engine._exorcism_blocked_id``) suppresses
  *waking and acting tonight*: no kill of the previous target, no
  new poison.
* Drunk/poisoned Pukka: no new effect emitted (registry contract).
  The previous-night's effect remains in the registry but is
  deactivated by the resolver because the Pukka can't maintain it,
  so the previously-poisoned target sobers immediately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class PukkaPoisonEffect(Effect):
    """Pukka's per-source poison effect.

    Distinct ``kind`` from :class:`PoisonerPoisonEffect` for per-source
    token rendering. Both contribute to the same derived
    ``Player.poisoned`` state.

    The Pukka's poison has no automatic expiry — it lasts until the
    Pukka kills the target on the next night (and the kill lands /
    is cancelled, after which the Pukka's ``ability()`` purges this
    effect explicitly). If the Pukka loses ability or dies, the
    standard lifecycle hooks (purge on source death, deactivate on
    source droisoned) handle cleanup.
    """

    kind = "pukka_poisoned"
    contributes_to_state = "poisoned"
    # No on_phase_boundary — Pukka's effect doesn't expire on its
    # own. The Pukka's next ability() purges the previous one.


class PukkaDeadEffect(Effect):
    """Marker on a seat the Pukka killed last night."""

    kind = "pukka_dead"
    contributes_to_state = None
    purge_on_source_death = True
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class Pukka(Character):
    name = "Pukka"
    char_type = CharType.DEMON
    ability_text = (
        "Each night, choose a player: they are poisoned. The previously "
        "poisoned player dies then becomes healthy."
    )
    first_night_order = 19
    other_night_order = 27
    reminder_tokens: list = [
        {"name": "POISONED", "icon": "pukka_poisoned.png"},
    ]

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return
        # Exorcist block.
        if getattr(engine, "_exorcism_blocked_id", None) == self.player.id:
            engine.log(
                f"Pukka {self.player.name}: blocked by the Exorcist."
            )
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Step 1: kill the previously-poisoned player (other nights).
        # Find the previous PukkaPoisonEffect via the registry.
        previous = next(
            (
                e for e in engine.effects_sourced_by(self)
                if isinstance(e, PukkaPoisonEffect)
            ),
            None,
        )
        if night_number > 1 and previous is not None:
            try:
                prev_target = engine.get_player(previous.targets[0])
            except (KeyError, IndexError):
                prev_target = None
            if prev_target is not None and prev_target.alive:
                engine.kill(prev_target.id, DeathCause.DEMON_KILL, source=self)
                if not prev_target.alive:
                    engine.add_effect(PukkaDeadEffect(
                        source=self, targets=[prev_target.id],
                    ))
            # Purge the old poison whether or not the kill landed.
            engine.purge_effect(previous)

        # Step 2: pick tonight's target.
        eligible = [p.id for p in engine.players if p.alive]
        sel = SelectPlayerPrompt(
            text="Pukka poisons a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_target",
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

        # RESOLUTION: emit PukkaPoisonEffect via the registry. A
        # drunk/poisoned Pukka goes through the motions but no effect
        # lands.
        if self.player.has_ability:
            engine.add_effect(PukkaPoisonEffect(
                source=self, targets=[target.id],
            ))
        else:
            engine.log(
                f"Pukka {self.player.name} is drunk/poisoned — no real "
                f"poisoning."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
