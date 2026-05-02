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
    a redirect. We hook the engine's ``PRE_DEATH`` event so the
    decision is made *before* the Mayor's death lands: the Mayor
    never transiently appears dead, no DEATH event fires for the
    Mayor, and no ``revive`` is needed. We surface this as a
    ``YesNoPrompt`` (offer the redirect) and, on yes, a
    ``SelectPlayerPrompt`` (pick the new victim). On a yes, we kill
    the new target with the *same* cause and cancel the original
    kill via ``event.data["cancelled"] = True``.

Drunkenness / poisoning: a drunk or poisoned Mayor's redirect does
nothing. We gate the reaction on the Mayor not being drunk/poisoned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing import Optional, Tuple

from engine.character import Character
from engine.enums import Alignment, CharType
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
    # Win condition: 3 alive, no execution, at dusk.
    # ------------------------------------------------------------------

    def check_win_condition(
        self, engine: "Engine", *, at_dusk: bool
    ) -> "Optional[Tuple[Alignment, str]]":
        """Mayor's win: at dusk, exactly 3 alive non-Traveler/Fabled
        players remain and no execution happened today.

        The Mayor must still be alive and have their ability for the
        win to fire. Alignment comes from the Mayor's own player so
        non-TB scripts where a Mayor is evil work correctly.
        """
        if not at_dusk:
            return None
        if self.player is None or not self.player.has_ability:
            return None
        if engine._executed_today:
            return None
        counted = [
            p for p in engine.alive_players
            if p.char_type not in (CharType.TRAVELER, CharType.FABLED)
        ]
        if len(counted) != 3:
            return None
        winner = self.player.alignment or Alignment.GOOD
        return winner, "Mayor: 3 alive players and no execution today."

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        if self.player is None:
            return super().reaction(event, engine)

        # Night-kill redirect — listen on PRE_DEATH so the Mayor never
        # transiently appears dead. The engine has already cleared
        # Soldier/Monk demon protection by the time PRE_DEATH fires;
        # at this point the death is *about to* land and any reaction
        # that sets ``event.data["cancelled"] = True`` aborts it.
        # Trigger condition is "killed at night" regardless of cause
        # (Demon, ability, poison, Storyteller). The Mayor must still
        # be alive (``has_ability`` includes alive + sober + healthy).
        if (
            event.type is EventType.PRE_DEATH
            and not self._redirect_in_flight
            and self.player.has_ability
            and any(t.id == self.player.id for t in event.targets)
            and engine.phase.is_night
            # If a passive canceller earlier in the dispatch order has
            # already saved the Mayor (Tea Lady's good-neighbour
            # protection, Innkeeper's SAFE marker, Sailor's cannot-die,
            # Fool's first-death save, etc.), skip the redirect prompt
            # — the Mayor doesn't get an unnecessary ST decision and
            # the death is already prevented.
            and not event.data.get("cancelled")
            and not event.data.get("force")
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

        Called from a ``PRE_DEATH`` reaction *before* the kill lands.
        On a YES, we kill the storyteller-picked target with the same
        cause and cancel the original kill on the Mayor by setting
        ``event.data["cancelled"] = True``. The Mayor never enters a
        transient "dead" state — no ``Player.kill`` / ``revive`` round
        trip, no ``DEATH`` event fired for the Mayor, no
        ``_pending_night_deaths`` cleanup needed.

        The Mayor never wakes at night — the redirect is a pure
        Storyteller decision. We deliberately leave
        ``target_player_id`` *and* ``target_player_name`` off these
        prompts so the UI does **not** render a "Wake up Mayor (...)"
        banner: that banner is keyed on ``meta.character`` AND a
        resolvable player name, so withholding the name keeps the
        prompts as a Storyteller-only decision panel grouped under
        ``meta.character``. The Mayor's name is included in the
        prompt text so the Storyteller still knows whose death is
        being adjudicated.
        """
        self._redirect_in_flight = True
        try:
            cause = event.data.get("cause") if event.data else None
            # Preserve the original kill's source character so that a
            # redirect back to the originator (e.g. Imp picks Mayor →
            # Mayor redirects to Imp) still reads as a self-attributed
            # demon kill at the engine level. The Imp's self-kill
            # ability triggers off the DEATH event's ``source`` —
            # neither the Mayor nor the Imp needs to know about each
            # other for this to work.
            kill_source = event.source
            ask = YesNoPrompt(
                text=f"Redirect {self.player.name}'s death (Mayor)?",
                meta={
                    "character": self.name,
                    "step": "redirect_yes_no",
                },
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
                text=f"Player who dies instead of {self.player.name}",
                count=1,
                eligible_player_ids=eligible,
                allow_self=False,
                meta={
                    "character": self.name,
                    "step": "redirect_select",
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
            # Cancel the Mayor's pending death and kill the new target
            # with the same cause AND the same source. Order matters:
            # cancel BEFORE the re-entrant kill so any reactions that
            # fire on the redirected target's death see the Mayor as
            # alive (matters for win-condition checks — e.g. if the
            # redirected target is the last evil player, good wins
            # without ever counting the Mayor as dead).
            event.data["cancelled"] = True
            event.data["cancelled_by_character"] = "Mayor"
            event.data["cancelled_reason"] = (
                f"Mayor's death redirected to {target.name}"
            )
            engine.log_reaction(
                "Mayor",
                (
                    f"Mayor death redirected from {self.player.name} to "
                    f"{target.name}."
                ),
                target=self.player,
                trigger="pre_death",
                effect="redirect_death",
                redirected_to_id=target.id,
                redirected_to_name=target.name,
            )
            if cause is None:
                engine.kill(target.id, source=kill_source)
            else:
                engine.kill(target.id, cause, source=kill_source)
        finally:
            self._redirect_in_flight = False
