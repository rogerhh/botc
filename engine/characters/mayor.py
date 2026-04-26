"""Mayor.

    "If only 3 players live and no execution occurs, good wins. If you
     die at night, another player might die instead."

The Mayor never wakes at night (``first_night_order`` and
``other_night_order`` are both 0). Both abilities are passive and
adjudicated by the Storyteller:

  * **Win condition.** Owned by ``Engine._check_win_conditions``: at
    dusk, if exactly 3 non-Traveler/Fabled players are alive and no
    execution happened today and an alive Mayor still has its ability,
    the Mayor's team wins. The Mayor's *alignment* (read off the
    player) decides who wins — Mayor can be evil in non-Trouble
    Brewing scripts.

  * **Night-kill redirect.** When the Mayor would die at night — for
    *any* cause, not just ``DEMON_KILL`` — the Storyteller is offered
    a redirect. We surface this as a single ``YesNoPrompt`` (offer the
    redirect) and, on yes, a ``SelectPlayerPrompt`` (pick the new
    victim). The actual death has already been queued via
    ``Engine.kill`` by the time the reaction fires; if the Storyteller
    picks a different victim, the Mayor's death is rolled back
    (``Player.revive``) and the new target is killed instead with the
    *same* cause.

Drunkenness / poisoning: a drunk or poisoned Mayor's redirect does
nothing. We gate the reaction on the Mayor not being drunk/poisoned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt, YesNoPrompt

if TYPE_CHECKING:
    from engine.engine import Engine

class Mayor(Character):
    name = "Mayor"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "If only 3 players live and no execution occurs, good wins. "
        "If you die at night, another player might die instead."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Guard against re-entrant redirect handling on the same death.
        self._redirect_in_flight: bool = False

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        if self.player is None:
            return super().reaction(event, engine)

        # Night-kill redirect.
        # NOTE: by the time this DEATH event fires, the engine has
        # already flipped ``alive`` to False, so we explicitly check
        # ``drunk`` / ``poisoned`` instead of ``has_ability`` (which
        # also requires alive). Trigger condition is "killed at night"
        # regardless of cause (Demon, ability, poison, Storyteller).
        if (
            event.type is EventType.DEATH
            and not self._redirect_in_flight
            and not self.player.drunk
            and not self.player.poisoned
            and any(t.id == self.player.id for t in event.targets)
            and engine.phase.is_night
        ):
            self._maybe_redirect_night_kill(engine, event)

        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _maybe_redirect_night_kill(
        self, engine: "Engine", event: Event
    ) -> None:
        """Ask the Storyteller whether to redirect the Mayor's death.

        The Mayor is already dead by the time we get here (the engine
        has just dispatched the DEATH event). On a YES, we revive the
        Mayor and kill the storyteller-picked target with the same
        cause as the original death.
        """
        self._redirect_in_flight = True
        try:
            cause = event.data.get("cause") if event.data else None
            ask = YesNoPrompt(
                text=(
                    f"The Mayor ({self.player.name}) was killed at "
                    f"night. Redirect the death to another player?"
                ),
                target_player_id=self.player.id,
                meta={"character": self.name, "step": "redirect_yes_no"},
            )
            do_redirect = engine.send_prompt(ask)
            if not isinstance(do_redirect, bool) or not do_redirect:
                return
            # Pick the new victim. Eligible: any other alive player who
            # isn't the Mayor. (Killing an already-dead player would
            # be a no-op.)
            eligible = [
                p.id for p in engine.alive_players
                if p.id != self.player.id
            ]
            if not eligible:
                return
            sel = SelectPlayerPrompt(
                text="Pick the player who dies instead of the Mayor.",
                count=1,
                eligible_player_ids=eligible,
                allow_self=False,
                target_player_id=self.player.id,
                meta={"character": self.name, "step": "redirect_select"},
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
            # Roll back the Mayor's death and kill the new target with
            # the same cause as the original kill.
            engine.revive(self.player.id)
            # Engine.revive doesn't pop pending_night_deaths; do that
            # by hand so dawn doesn't announce the Mayor.
            try:
                engine._pending_night_deaths.remove(self.player)
            except ValueError:
                pass
            engine.log(
                f"Mayor death redirected from {self.player.name} to "
                f"{target.name}."
            )
            if cause is None:
                engine.kill(target.id)
            else:
                engine.kill(target.id, cause)
        finally:
            self._redirect_in_flight = False
