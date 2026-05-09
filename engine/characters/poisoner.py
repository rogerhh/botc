"""Poisoner.

    "Each night, choose a player: they are poisoned tonight and tomorrow day."

A poisoned player has no ability — their abilities are simulated by the
storyteller (woken at the right time, given false info if applicable),
but no game state is altered.

Implementation (registry-managed)
---------------------------------
The Poisoner acts every night (first night and beyond). The natural
duration of the poison is "tonight and tomorrow day", which expires at
the next **dusk**.

The poison is emitted as a :class:`PoisonerPoisonEffect` in the engine
effect registry. All cleanup paths fall out of the standard Effect
lifecycle:

  * **Natural dusk boundary** —
    :meth:`PoisonerPoisonEffect.on_phase_boundary` purges at dusk.
  * **Poisoner dies** — ``purge_on_source_death=True`` (default) →
    purge.
  * **Poisoner becomes drunk or poisoned** — resolver deactivates
    the effect; the target sobers. (If the Poisoner sobers again
    before dusk, the effect re-activates.)
  * **Poisoner's character class changes** —
    ``purge_on_source_character_change=True`` (default) → purge.

If the Poisoner is themselves drunk or poisoned at the moment of the
night-time SELECT, they go through the motions but no
:class:`PoisonerPoisonEffect` is added (registry contract: source must
have ability at application time).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class PoisonerPoisonEffect(Effect):
    """Per-source poison effect, lasting tonight + tomorrow day.

    Distinct from :class:`PukkaPoisonEffect` — same
    ``contributes_to_state="poisoned"`` (so ``Player.poisoned`` is
    the union of any active poison effects regardless of source) but
    distinct ``kind`` for per-source token rendering.
    """

    kind = "poisoner_poisoned"
    contributes_to_state = "poisoned"

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dusk":
            engine.purge_effect(self)


class Poisoner(Character):
    name = "Poisoner"
    char_type = CharType.MINION
    ability_text = (
        "Each night, choose a player: they are poisoned tonight and tomorrow day."
    )
    first_night_order = 10
    other_night_order = 10
    reminder_tokens: list = [
        {"name": 'POISONED', "icon": 'poisoner_poisoned.png'},
    ]

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event so other abilities can react,
        # but no separate ST-facing prompt: the wake-up line is shown
        # as part of the next prompt's panel.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: pick a player to poison. Per the wiki rule, "choose
        # a player" means any player — alive or dead — can be chosen.
        # The Poisoner can poison themselves.
        eligible = [p.id for p in engine.players]
        sel = SelectPlayerPrompt(
            text="Poisoner poisons a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,  # player decision (Poisoner picks)
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_player",
                "stage": "player",
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0]
        target = engine.get_player(target_id)

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )
        # Goon notify: if the Poisoner picked the Goon's seat, the
        # Goon drunkens the Poisoner synchronously here.
        engine.notify_goon_chosen(self, target)

        # No INFORMATION step — the Poisoner does not learn anything.

        # RESOLUTION: emit the PoisonerPoisonEffect via the registry.
        # A drunk/poisoned Poisoner goes through the motions but no
        # effect lands (registry contract: source must have ability
        # at application time).
        if self.player.has_ability:
            engine.add_effect(PoisonerPoisonEffect(
                source=self, targets=[target.id],
            ))
            engine.log(f"{target.name} is poisoned by the Poisoner.")
        else:
            engine.log(
                f"Poisoner {self.player.name} is drunk/poisoned; "
                f"no real poisoning happens."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
