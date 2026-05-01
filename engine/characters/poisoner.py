"""Poisoner.

    "Each night, choose a player: they are poisoned tonight and tomorrow day."

A poisoned player has no ability — their abilities are simulated by the
storyteller (woken at the right time, given false info if applicable),
but no game state is altered.

Implementation
--------------
The Poisoner acts every night (first night and beyond). The natural
duration of the poison is "tonight and tomorrow day", so it expires at
the next **dusk**.

Single source of truth
~~~~~~~~~~~~~~~~~~~~~~
The POISONED reminder token's visibility and the target's actual
``Player.poisoned`` flag are both driven by exactly one piece of
state: ``self._last_target`` plus that target's ``poisoned`` flag.
:meth:`compute_reminder_tokens` reads the flag directly, and the
flag is cleared the moment the Poisoner can no longer maintain the
poison. So there is never a window where the token is gone but the
target is "secretly still poisoned" (or vice versa).

Cleanup paths
~~~~~~~~~~~~~
The poison is cleared in any of the following cases:

  * **Natural dusk boundary** (``recheck_persistent_effects``) — the
    "tonight and tomorrow day" duration ends.
  * **Poisoner dies** (DEATH reaction on self) — a dead Poisoner
    cannot maintain the poison.
  * **Poisoner becomes drunk or poisoned** (DRUNK / POISON reaction
    on self) — Poisoner without ability cannot maintain.
  * **Poisoner's character class changes** (CHARACTER_CHANGE reaction
    on self, fired by ``Engine.change_character`` *before* the swap)
    — e.g. the Scarlet Woman promoting to Demon and discarding the
    old Minion role; or storyteller-arbitrated role swaps.

If the Poisoner is themselves drunk or poisoned at the moment of the
night-time SELECT, they go through the motions but no poisoning takes
effect (so ``_last_target`` stays None and there's nothing to clean
up later).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

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

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # The seat this Poisoner has currently poisoned, or ``None``
        # if no live poisoning is being maintained. Both the
        # POISONED reminder token and the storyteller-visible
        # ``Player.poisoned`` flag key off this — they are two
        # views of the same state, and they go stale or fresh
        # together.
        self._last_target: Optional["Player"] = None

    # ------------------------------------------------------------------
    # Display.
    # ------------------------------------------------------------------

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Place the POISONED token on the currently-poisoned target.

        The token's visibility is driven *purely* by the actual
        ``poisoned`` flag on ``_last_target`` — the same flag the rest
        of the engine consults via ``Player.has_ability``. When the
        Poisoner can no longer maintain the poison, the cleanup paths
        below clear that flag (and ``_last_target``); both the token
        and the flag disappear at the same moment.

        Note: we deliberately do NOT gate on ``self.player.has_ability``
        here. The Poisoner's own state controls *whether the cleanup
        paths fire*, not *whether the existing poison shows up*.
        """
        if (
            self._last_target is None
            or getattr(self._last_target, "character", None) is None
            or not self._last_target.poisoned
        ):
            return {}
        return {"poisoned": [self._last_target.id]}

    # ------------------------------------------------------------------
    # Cleanup helper.
    # ------------------------------------------------------------------

    def _clear_poison(self, engine: "Engine", reason: str) -> None:
        """Clear the poison this Poisoner has placed, if any.

        Idempotent: safe to call when no target is currently set.
        """
        if self._last_target is None:
            return
        target = self._last_target
        self._last_target = None
        if target.poisoned:
            target.set_poisoned(False)
            engine.log(
                f"{target.name} is no longer poisoned ({reason})."
            )

    # ------------------------------------------------------------------
    # Cleanup path 1: natural dusk expiry.
    # ------------------------------------------------------------------

    def recheck_persistent_effects(
        self, engine: "Engine", phase: str
    ) -> None:
        """Expire the Poisoner's poison at the natural dusk boundary.

        The Poisoner's ability is "poisoned tonight and tomorrow day",
        so the poison ends at the next dusk regardless of the
        Poisoner's state. The reactive cleanup paths
        (:meth:`reaction`) handle the early-expiry cases; this just
        handles the natural duration end.
        """
        if phase == "dusk":
            self._clear_poison(engine, "Poisoner expired")

    # ------------------------------------------------------------------
    # Cleanup path 2: reactive — Poisoner can no longer maintain.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        """Clear the poison the moment the Poisoner can no longer maintain.

        Trigger conditions, all keyed on the **Poisoner's own seat**:

          * ``DEATH``           — Poisoner died.
          * ``POISON``          — Poisoner just became poisoned.
          * ``DRUNK``           — Poisoner just became drunk.
          * ``CHARACTER_CHANGE`` — Poisoner is being swapped to a
            different role (e.g. Scarlet Woman promotes a Minion to
            Demon, then this Poisoner instance is discarded). The
            engine fires this event *before* the swap so we still
            have access to ``self._last_target`` and the cleanup
            takes effect under the right name.

        After cleanup, defer to ``super().reaction`` so the standard
        drunk/poisoned RESOLUTION-blocking still applies to anything
        downstream.
        """
        if self.player is not None and event.targets:
            target_self = event.targets[0].id == self.player.id
            if target_self:
                if event.type is EventType.DEATH:
                    self._clear_poison(engine, "Poisoner died")
                elif event.type is EventType.POISON:
                    self._clear_poison(engine, "Poisoner is poisoned")
                elif event.type is EventType.DRUNK:
                    self._clear_poison(engine, "Poisoner is drunk")
                elif event.type is EventType.CHARACTER_CHANGE:
                    new_name = event.data.get("new_character") if event.data else "?"
                    self._clear_poison(
                        engine, f"Poisoner became the {new_name}"
                    )
        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        # Defensive belt-and-braces cleanup. The dusk recheck and
        # reactive cleanup paths should already have cleared
        # ``_last_target`` by the time the Poisoner acts again; if
        # something bypassed both (a save/load path, a manual
        # storyteller mutator), we still want to start the night with
        # a clean slate.
        self._clear_poison(engine, "Poisoner expired")

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event so other abilities can react,
        # but no separate ST-facing prompt: the wake-up line is shown
        # as part of the next prompt's panel.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: pick a player to poison. Storyteller may pick any
        # alive player. The Poisoner can poison themselves.
        eligible = [p.id for p in engine.players if p.alive]
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

        # No INFORMATION step — the Poisoner does not learn anything.

        # RESOLUTION: poison the target, but only if the Poisoner has
        # their ability working (sober, healthy, alive).
        if self.player.has_ability:
            target.set_poisoned(True)
            self._last_target = target
            engine.log(f"{target.name} is poisoned by the Poisoner.")
        else:
            engine.log(
                f"Poisoner {self.player.name} is drunk/poisoned; "
                f"no real poisoning happens."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
