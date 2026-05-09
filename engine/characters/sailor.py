"""Sailor.

    "Each night, choose an alive player: either you or they are drunk
     until dusk. You can't die."

Each night (first night included) the Sailor wakes and picks an alive
player. The Storyteller then chooses which of two players ends up
drunk — the Sailor themself or the chosen player — and that drunk
state expires at the next dusk. While the Sailor is sober (and
healthy and alive), they cannot die for any cause.

Cannot-die mechanic
-------------------
A passive PRE_DEATH cancellation. The reaction listens for any death
about to land on the Sailor's seat and, if the Sailor still has its
ability (alive + sober + healthy), cancels via
``event.data["cancelled"] = True``. Per the design doc this is an
*intrinsic reaction*, not an effect — there's no token, no source on
another seat, no duration. Stays as a ``reaction()``.

Drunk-target lifecycle (registry-managed)
-----------------------------------------
The Sailor's drunkening goes through the engine's effect registry.
:class:`SailorDrunkEffect` is added on resolution if the Sailor has
ability, and the effect's ``on_phase_boundary`` purges itself at the
next dusk. The lifecycle hooks the engine provides handle every
cleanup path automatically:

  * **Natural dusk expiry** — ``on_phase_boundary("dusk")`` purges.
  * **Sailor dies** — ``purge_on_source_death=True`` (default) → purge.
  * **Sailor becomes drunk/poisoned** — the resolver deactivates the
    effect; the target sobers. If the Sailor sobers again before
    dusk, the effect re-activates and the target is drunk again.
  * **Character change** — ``purge_on_source_character_change=True``
    (default) → purge before the swap.
  * **Self-target** — when the Sailor picks themself, the resolver's
    self-source rule (excluding the effect itself when checking the
    source's drunk status) keeps the effect active until *another*
    droison source droisons the Sailor.

A drunk-from-self Sailor goes through the motions of picking the next
night (the Storyteller still wakes them) but no real drunk is applied
— guarded by ``self.player.has_ability`` at resolution time, exactly
the contract the registry expects (don't add an effect when the
source is droisoned at application time).

Drunk pre-pick on the ST decision
---------------------------------
The "Sailor or chosen player" pick is a 2-option choice. Per the
project rule on drunk/poisoned info this is treated as binary, but
the ability is the Sailor's — not an info ability — so we don't
pre-pick a wrong default. The rulebook's heuristic is exposed via the
prompt's default: townsfolk-typed picks default to *the chosen
player* drunk, otherwise *the Sailor*. The Storyteller may override.
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


class SailorDrunkEffect(Effect):
    """The Sailor's nightly drunkening, lasting until dusk.

    Registry-managed — the engine resolver decides active/inactive
    based on the Sailor's current ``has_ability`` (alive + sober +
    healthy). No private bookkeeping on the character.
    """

    kind = "sailor_drunk"
    contributes_to_state = "drunk"
    # Lifecycle defaults are correct:
    # * ``purge_on_source_death = True`` — dead Sailor cannot maintain.
    # * ``purge_on_source_character_change = True`` — clear on swap.
    # * ``deactivate_on_source_droisoned = True`` — Sailor poisoned →
    #   target sobers (re-activates if Sailor sobers within the
    #   one-phase duration, though dusk would purge first in practice).

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        """Sailor's drunk lasts ``until dusk`` — purge at next dusk."""
        if phase == "dusk":
            engine.purge_effect(self)


class Sailor(Character):
    name = "Sailor"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night, choose an alive player: either you or they are "
        "drunk until dusk. You can't die."
    )
    first_night_order = 14
    other_night_order = 14
    reminder_tokens: list = [
        {"name": "DRUNK", "icon": "sailor_drunk.png"},
    ]

    # ------------------------------------------------------------------
    # Reactions — intrinsic Sailor cannot die.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        # Cannot-die: a sober Sailor's death is cancelled at PRE_DEATH.
        # This is intrinsic to the role — not an effect emitted on
        # another seat — so it stays in ``reaction()``.
        if (
            event.type is EventType.PRE_DEATH
            and self.player is not None
            and self.player.has_ability
            and event.targets
            and any(t.id == self.player.id for t in event.targets)
            and not event.data.get("cancelled")
            and not event.data.get("force")
        ):
            event.data["cancelled"] = True
            event.data["cancelled_by_character"] = "Sailor"
            event.data["cancelled_reason"] = (
                "Sailor is sober (Sailor cannot die)"
            )
            engine.log_reaction(
                "Sailor",
                f"{self.player.name} cannot die — the Sailor is sober.",
                target=self.player,
                trigger="pre_death",
                effect="sailor_cannot_die",
            )
            return

        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT — the Sailor's pick. Any alive player, including self.
        eligible = [p.id for p in engine.players if p.alive]
        sel = SelectPlayerPrompt(
            text="Sailor chooses an alive player",
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
            chosen = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[chosen])
        )
        # Goon notify: if the Sailor picked the Goon's seat (not
        # themself — choose_me self-guards anyway), the Goon
        # drunkens the Sailor. The has_ability check at effect-emit
        # time below picks up the new state and skips the protection
        # / drunkening Sailor would otherwise apply.
        engine.notify_goon_chosen(self, chosen)

        # ST decision: which of {Sailor, chosen} ends up drunk. If the
        # Sailor picked themself, the only valid pick is the Sailor —
        # we still ask so the resolution flow stays consistent (the
        # engine's auto-resolve will short-circuit the prompt when
        # there's exactly one eligible answer).
        if chosen.id == self.player.id:
            drunk_eligible = [self.player.id]
            default_drunk = self.player.id
        else:
            drunk_eligible = [self.player.id, chosen.id]
            # Heuristic default per the rulebook tip: if the chosen
            # player is a Townsfolk, default to drunkening *them*; if
            # they're an Outsider/Minion/Demon, default to drunkening
            # the *Sailor*.
            chosen_type = (
                chosen.character.char_type if chosen.character else None
            )
            if chosen_type is CharType.TOWNSFOLK:
                default_drunk = chosen.id
            else:
                default_drunk = self.player.id

        drunk_sel = SelectPlayerPrompt(
            text=(
                f"Storyteller: who is drunk tonight — "
                f"{self.player.name} (Sailor) or {chosen.name}?"
            ),
            count=1,
            eligible_player_ids=drunk_eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_drunk",
                "stage": "st_post",
                "default": default_drunk,
                "chosen_player_id": chosen.id,
            },
        )
        drunk_resp = engine.send_prompt(drunk_sel)
        if isinstance(drunk_resp, list):
            drunk_resp = drunk_resp[0] if drunk_resp else None
        try:
            drunk_id = int(drunk_resp) if drunk_resp is not None else default_drunk
        except (TypeError, ValueError):
            drunk_id = default_drunk
        if drunk_id not in drunk_eligible:
            drunk_id = default_drunk
        try:
            drunk_player = engine.get_player(drunk_id)
        except (KeyError, ValueError, TypeError):
            drunk_player = self.player

        # RESOLUTION: emit the SailorDrunkEffect via the registry. A
        # drunk/poisoned Sailor goes through the motions but the
        # effect contract requires has_ability at application time —
        # we skip add_effect entirely in that case (the slot still
        # "fires" from the storyteller's perspective; nothing lands).
        if self.player.has_ability:
            engine.add_effect(SailorDrunkEffect(
                source=self, targets=[drunk_player.id],
            ))
            engine.log(
                f"Sailor {self.player.name} drunkens {drunk_player.name}."
            )
        else:
            engine.log(
                f"Sailor {self.player.name} is drunk/poisoned — "
                f"no real drunkening tonight."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[drunk_player])
        )
