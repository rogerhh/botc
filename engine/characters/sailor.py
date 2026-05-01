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
``event.data["cancelled"] = True`` — the same channel used by Mayor /
Pacifist / Tea Lady / Fool. The "you can't die" works for *every*
cause: Demon kill (after Monk's pre-PRE_DEATH gate), execution,
ability kill, ST-attributed kill.

If the Sailor has been made drunk (typically by their own ability
choosing themself), ``has_ability`` returns False and the cancellation
no longer fires — so the self-drunk Sailor dies normally.

Drunk-target lifecycle
----------------------
The Sailor places the drunk on either themself or the chosen target.
Cleanup paths mirror the Poisoner's design:

  * **Natural dusk expiry** — the rulebook's "until dusk" duration is
    cleared in :meth:`recheck_persistent_effects` (phase=``"dusk"``).
  * **Sailor dies** — a dead Sailor cannot maintain the drunk; clear.
    Note that a sober Sailor cannot die at all, so reaching this path
    means the Sailor was already self-drunk.
  * **Sailor becomes poisoned** — Sailor without ability cannot
    maintain the drunk; clear.
  * **Character change** — the Poisoner's pattern: clear before the
    swap so the new role doesn't inherit the old role's effect.

Drunkenness / poisoning
-----------------------
A drunk-from-self Sailor goes through the motions of picking the
next night (the Storyteller still wakes them) but no real drunk is
applied — guarded by ``self.player.has_ability`` at resolution time.
A poisoned Sailor likewise loses both the cannot-die immunity and
the drunkening effect.

Drunk pre-pick on the ST decision
---------------------------------
The "Sailor or chosen player" pick is a 2-option choice. Per the
project rule on drunk/poisoned info this is treated as binary, but
the ability is the *Sailor's* — not an info ability — so we don't
pre-pick a wrong default. Instead, the rulebook's heuristic is
exposed via the prompt's default: townsfolk-typed picks default to
*the chosen player* drunk, and outsider/minion/demon picks default
to *the Sailor* drunk. The Storyteller may override either way.
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

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # The seat the Sailor is currently keeping drunk via their
        # nightly ability (could be themself). Cleared at dusk and on
        # any of the cleanup-path triggers in :meth:`reaction`.
        self._drunk_target: Optional["Player"] = None

    # ------------------------------------------------------------------
    # Reminder tokens.
    # ------------------------------------------------------------------

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Show the DRUNK token on whoever the Sailor's ability has drunk.

        Visibility is keyed off the actual ``Player.drunk`` flag on
        ``_drunk_target`` so the token disappears the moment the
        cleanup paths fire (or the engine's other state mutators
        clear the flag).
        """
        if (
            self._drunk_target is None
            or getattr(self._drunk_target, "character", None) is None
            or not self._drunk_target.drunk
        ):
            return {}
        return {"sailor_drunk": [self._drunk_target.id]}

    # ------------------------------------------------------------------
    # Cleanup helpers.
    # ------------------------------------------------------------------

    def _clear_drunk(self, engine: "Engine", reason: str) -> None:
        """Clear any drunk state placed by *this* Sailor.

        Idempotent: safe to call when no target is currently set.
        """
        if self._drunk_target is None:
            return
        target = self._drunk_target
        self._drunk_target = None
        if target.drunk:
            target.set_drunk(False)
            engine.log(
                f"{target.name} is no longer drunk ({reason})."
            )

    def recheck_persistent_effects(
        self, engine: "Engine", phase: str
    ) -> None:
        """Expire the Sailor's drunk at the natural dusk boundary."""
        if phase == "dusk":
            self._clear_drunk(engine, "Sailor's drunk expired at dusk")

    # ------------------------------------------------------------------
    # Reactions.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        # Cannot-die: a sober Sailor's death is cancelled at PRE_DEATH.
        if (
            event.type is EventType.PRE_DEATH
            and self.player is not None
            and self.player.has_ability
            and event.targets
            and any(t.id == self.player.id for t in event.targets)
            and not event.data.get("cancelled")
        ):
            event.data["cancelled"] = True
            engine.log_reaction(
                "Sailor",
                f"{self.player.name} cannot die — the Sailor is sober.",
                target=self.player,
                trigger="pre_death",
                effect="sailor_cannot_die",
            )
            return

        # Drunk-target cleanup paths (mirror Poisoner).
        if self.player is not None and event.targets:
            target_self = event.targets[0].id == self.player.id
            if target_self:
                if event.type is EventType.DEATH:
                    self._clear_drunk(engine, "Sailor died")
                elif event.type is EventType.POISON:
                    self._clear_drunk(engine, "Sailor is poisoned")
                elif event.type is EventType.CHARACTER_CHANGE:
                    new_name = (
                        event.data.get("new_character") if event.data else "?"
                    )
                    self._clear_drunk(
                        engine, f"Sailor became the {new_name}"
                    )

        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        # Defensive: clear any leftover drunk before the new pick.
        self._clear_drunk(engine, "Sailor's drunk expired before new pick")

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

        # RESOLUTION: apply the drunk. A drunk/poisoned Sailor goes
        # through the motions but no real drunk is applied.
        if self.player.has_ability:
            drunk_player.set_drunk(True)
            self._drunk_target = drunk_player
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
