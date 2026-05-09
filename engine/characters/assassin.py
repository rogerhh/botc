"""Assassin.

    "Once per game, at night*, choose a player: they die, even if
     for some reason they could not."

The Assassin's once-per-game force-kill bypasses every PRE_DEATH
canceller and Monk-style protection by passing ``force=True`` to
:meth:`engine.engine.Engine.kill`. The flag flows into the
``PRE_DEATH`` event's ``data["force"]``, where every protection
reaction (Soldier, Mayor, Pacifist, Tea Lady, Sailor, Fool,
Innkeeper, Devil's Advocate) checks and steps aside.

Implementation
--------------
* From night 2 onward the Assassin wakes and may either decline
  (slot stays available next night) or pick any player.
* Slot consumption (``self._used``) and ``Player.once_per_game_used``
  are both set on a real fire — declining doesn't consume the slot.
* Drunk/poisoned Assassin: slot still consumed but no real kill.

Reminder tokens
---------------
``NO ABILITY`` surfaces on the Assassin's seat once spent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class AssassinNoAbilityEffect(Effect):
    """NO ABILITY marker on the Assassin's seat once spent."""

    kind = "assassin_no_ability"
    contributes_to_state = None
    purge_on_source_death = False
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


class AssassinDeadEffect(Effect):
    """Marker on the seat the Assassin killed (post-kill bookkeeping)."""

    kind = "assassin_dead"
    contributes_to_state = None
    # Survives source death: a self-kill (or Assassin force-kill that
    # bounces back) must still leave the DEAD marker on the victim's
    # seat. Dawn cleanup (on_phase_boundary) remains responsible for
    # removing the marker.
    purge_on_source_death = False
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class Assassin(Character):
    name = "Assassin"
    char_type = CharType.MINION
    ability_text = (
        "Once per game, at night*, choose a player: they die, even if "
        "for some reason they could not."
    )
    first_night_order = 0
    other_night_order = 43
    once_per_game = True
    reminder_tokens: list = [
        {"name": "NO ABILITY", "icon": "assassin_no_ability.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._used: bool = False

    # NO ABILITY rendered via AssassinNoAbilityEffect when ``_used``
    # is set; AssassinDeadEffect placed on the kill victim.

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if self._used:
            return False
        if night_number == 1:
            return False
        return super().would_act_tonight(engine, night_number)

    def ability(self, engine: "Engine", night_number: int) -> None:
        if (
            night_number == 1
            or self.player is None
            or self.player.dead
            or self._used
        ):
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Eligible: any player. Decline option appended as id 0.
        decline_id = 0
        eligible = [p.id for p in engine.players] + [decline_id]
        sel = SelectPlayerPrompt(
            text="Assassin may stab a player (or decline)",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_target",
                "stage": "player",
                "decline_id": decline_id,
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0] if target_id else None
        if target_id is None or int(target_id) == decline_id:
            engine.log(f"Assassin {self.player.name} declined tonight.")
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[self.player])
            )
            return
        try:
            target = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        # Capture the Assassin's pre-notify droison state. The
        # Assassin's force-kill is "stronger than" the Goon's retort:
        # per the wiki, "If chosen by the Assassin, the Goon dies but
        # still turns evil." Practically this means the kill must
        # land regardless of any drunkening that the Goon's retort
        # applies during ``notify_goon_chosen`` below — so we gate
        # the force-kill on the source's state *before* the notify
        # rather than after. A drunk-at-activation Assassin (e.g.
        # poisoned earlier in the night) still sees their slot spent
        # and no kill — that's the standard BotC rule for a drunk
        # source's ability.
        sober_at_select = self.player.has_ability

        # Goon notify: if the Assassin picked the Goon's seat, the
        # Goon's retort fires synchronously here — drunkens the
        # Assassin and flips the Goon's alignment to match the
        # Assassin (evil). We deliberately notify BEFORE the kill so
        # the alignment flip happens while the Goon is still alive
        # (the choose_me gate requires Goon ``has_ability``). After
        # the kill the Goon would be dead and the alignment flip
        # would no-op — that's the wrong order. The kill below uses
        # the captured ``sober_at_select`` instead of a fresh
        # ``has_ability`` read so the post-notify drunkening doesn't
        # block it.
        engine.notify_goon_chosen(self, target)

        # Slot consumed regardless of drunk state.
        self._used = True
        if self.player is not None:
            self.player.once_per_game_used = True
            engine.add_effect(AssassinNoAbilityEffect(
                source=self, targets=[self.player.id],
            ))

        if sober_at_select:
            # Force-kill bypasses every PRE_DEATH canceller via
            # ``force=True``. It also bypasses the Goon's retort
            # drunkening on the Assassin (we use ``sober_at_select``,
            # not a fresh ``has_ability`` read).
            engine.kill(target.id, DeathCause.ABILITY, source=self, force=True)
            if not target.alive:
                engine.add_effect(AssassinDeadEffect(
                    source=self, targets=[target.id],
                ))
        else:
            engine.log(
                f"Assassin {self.player.name} is drunk/poisoned — "
                f"no real kill."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
