"""Courtier.

    "Once per game, at night, choose a character: they are drunk for
     3 nights & 3 days."

The Courtier wakes every night and either shakes their head (declines)
or points at any character icon — *not* a player. If the chosen
character is in play, the seat playing that role is drunk for the
next three nights and three days, starting immediately. After the
Courtier triggers the ability (even if drunk/poisoned, even if the
chosen character isn't in play), they don't wake again.

Phase counting
--------------
The "3 nights & 3 days" duration is implemented as a phase tick
counter that decrements at every dawn (NIGHT->DAY) and dusk
(DAY->NIGHT). Because the Courtier picks during a night, the
starting tick covers the rest of the picking night and we need 6
subsequent phase boundaries to traverse 3 days + 3 nights ending at
the dusk into the *fourth* night, exactly as the wiki describes.

  * Pick on night N → counter = 6
  * Dawn into day N+1     → 5 (drunk during day N+1)
  * Dusk into night N+1   → 4 (drunk during night N+1)
  * Dawn into day N+2     → 3 (drunk during day N+2)
  * Dusk into night N+2   → 2 (drunk during night N+2)
  * Dawn into day N+3     → 1 (drunk during day N+3)
  * Dusk into night N+3   → 0 (drunk lifted before the new night runs)

Conditional re-evaluation
-------------------------
Per the wiki: "If the Courtier made a character drunk, but the
Courtier becomes drunk or poisoned, the player they made drunk
becomes sober again. If the Courtier becomes sober and healthy again
before the three nights and three days have ended, that player
becomes drunk yet again."

We honour this by re-applying the drunk state at every phase
boundary based on the Courtier's *current* ``has_ability``. The
``_we_made_drunk`` flag tracks whether the current ``target.drunk``
state was placed by us — so we never undo a drunk that some other
character (Sailor, Innkeeper, …) is also imposing.

Drunk pre-pick at choose time
-----------------------------
Per the wiki: "If the drunk or poisoned Courtier chooses a character,
that character is not drunk, even if the Courtier later becomes
sober and healthy." This is captured by checking ``has_ability`` at
resolution time *and* at every subsequent phase boundary — a
drunk-at-pick-time Courtier never sets ``_we_made_drunk = True`` so
no re-application can happen on later sobering.

"NO ABILITY" reminder
---------------------
The wiki's "NO ABILITY" marker after the slot is consumed is a single
state-driven token surfaced through :meth:`compute_reminder_tokens`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Courtier(Character):
    name = "Courtier"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Once per game, at night, choose a character: they are drunk "
        "for 3 nights & 3 days."
    )
    first_night_order = 15
    other_night_order = 15
    once_per_game = True
    reminder_tokens: list = [
        {"name": "DRUNK", "icon": "courtier_drunk.png"},
        {"name": "NO ABILITY", "icon": "courtier_no_ability.png"},
    ]

    # Total phase ticks from "pick on night N" through "drunk lifted at
    # dusk into night N+3". See module docstring.
    _DURATION_PHASE_TICKS = 6

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        self._used: bool = False
        self._target_player_id: Optional[int] = None
        self._picked_character: Optional[str] = None
        self._phases_remaining: int = 0
        # True iff *we* currently hold ``target.drunk = True``. Lets
        # us re-apply / retract our contribution without trampling a
        # drunk state placed by another character (Sailor / Innkeeper).
        self._we_made_drunk: bool = False

    # ------------------------------------------------------------------
    # Reminder tokens.
    # ------------------------------------------------------------------

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        out: "dict[str, list[int]]" = {}
        # NO ABILITY on the Courtier's own seat once spent.
        if self._used and self.player is not None and self.player.character is not None:
            out["courtier_no_ability"] = [self.player.id]
        # DRUNK on the target while our contribution is active.
        if (
            self._we_made_drunk
            and self._target_player_id is not None
            and self._phases_remaining > 0
        ):
            try:
                tgt = engine.get_player(self._target_player_id)
                if tgt.character is not None and tgt.drunk:
                    out["courtier_drunk"] = [tgt.id]
            except KeyError:
                pass
        return out

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _resolve_target(self, engine: "Engine") -> Optional["Player"]:
        if self._target_player_id is None:
            return None
        try:
            return engine.get_player(self._target_player_id)
        except KeyError:
            return None

    def _set_drunk(self, engine: "Engine", target: "Player") -> None:
        """Apply our drunk to ``target`` if not already drunk from us."""
        if not target.drunk:
            target.set_drunk(True)
            engine.log(
                f"Courtier drunkens {target.name} ({self._phases_remaining} "
                f"phase ticks remaining)."
            )
        self._we_made_drunk = True

    def _retract_drunk(self, engine: "Engine", reason: str) -> None:
        """Lift our drunk contribution from the target, if active."""
        if not self._we_made_drunk:
            return
        target = self._resolve_target(engine)
        self._we_made_drunk = False
        if target is not None and target.drunk:
            target.set_drunk(False)
            engine.log(
                f"{target.name} is no longer drunk ({reason})."
            )

    def _reapply_or_retract(self, engine: "Engine") -> None:
        """Reconcile ``target.drunk`` with the Courtier's current ability.

        Called at every phase boundary while the duration is active.
        Reads ``self.player.has_ability`` *now* so a Courtier who
        slipped in/out of poisoning during the duration toggles the
        target's drunk state to match.
        """
        if self._target_player_id is None or self._phases_remaining <= 0:
            return
        target = self._resolve_target(engine)
        if target is None:
            return
        courtier_alive_with_ability = (
            self.player is not None and self.player.has_ability
        )
        if courtier_alive_with_ability:
            if not self._we_made_drunk:
                # We don't currently own a drunk on this seat. Apply
                # ours unless someone else has them drunk already (we
                # then take ownership only if the target is currently
                # not drunk — leaving foreign drunks alone).
                if not target.drunk:
                    self._set_drunk(engine, target)
        else:
            # Courtier can no longer maintain. If we set the drunk,
            # clear our contribution.
            self._retract_drunk(engine, "Courtier lost ability")

    def _expire(self, engine: "Engine") -> None:
        """Wind down the Courtier's effect at end of duration."""
        self._retract_drunk(engine, "Courtier's drunkening duration ended")
        self._target_player_id = None
        self._picked_character = None
        self._phases_remaining = 0

    # ------------------------------------------------------------------
    # Persistent-effect recheck — phase tick counter + re-application.
    # ------------------------------------------------------------------

    def recheck_persistent_effects(
        self, engine: "Engine", phase: str
    ) -> None:
        if self._target_player_id is None or self._phases_remaining <= 0:
            return
        # One phase elapsed. Decrement first so a 1-remaining counter
        # naturally winds down to 0 on the next boundary.
        self._phases_remaining -= 1
        if self._phases_remaining <= 0:
            self._expire(engine)
            return
        # Mid-duration: reconcile based on the Courtier's current
        # ability state.
        self._reapply_or_retract(engine)

    # ------------------------------------------------------------------
    # Reactions — early-cleanup paths + ability-state changes.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        if self.player is not None and event.targets:
            target_self = event.targets[0].id == self.player.id
            if target_self:
                if event.type is EventType.DEATH:
                    # Per the wiki the Courtier doesn't need to be alive
                    # to maintain the drunk; the duration is fixed at
                    # 3n3d. We still react to ``DEATH`` only to log the
                    # state — DO NOT retract.
                    pass
                elif event.type in (EventType.POISON, EventType.DRUNK):
                    # Courtier just lost ability — pull our drunk.
                    self._reapply_or_retract(engine)
                elif event.type is EventType.CHARACTER_CHANGE:
                    new_name = (
                        event.data.get("new_character") if event.data else "?"
                    )
                    self._retract_drunk(
                        engine, f"Courtier became the {new_name}"
                    )
                    self._target_player_id = None
                    self._picked_character = None
                    self._phases_remaining = 0
        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Activation gate — once-per-game, no more wakes after firing.
    # ------------------------------------------------------------------

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if self._used:
            return False
        return super().would_act_tonight(engine, night_number)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead or self._used:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT — character (not player). Eligible: every role on the
        # active script (preset roster preferred, falls back to global
        # script). The Courtier may decline — they're not forced to
        # spend the slot tonight.
        eligible_chars = engine.all_character_names()
        # Use a sentinel "—" to represent "do not use ability tonight"
        # so the prompt is a clean SelectCharacterPrompt with one extra
        # decline option appended at the end.
        decline = "(decline)"
        prompt_options = list(eligible_chars) + [decline]
        sel = SelectCharacterPrompt(
            text=(
                "Courtier may choose a character to drunken (or decline)"
            ),
            eligible_characters=prompt_options,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_character",
                "stage": "player",
                "decline_option": decline,
            },
        )
        chosen_resp = engine.send_prompt(sel)
        if not isinstance(chosen_resp, str) or not chosen_resp:
            # Treat a malformed response as a decline.
            chosen_resp = decline

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[self.player])
        )

        if chosen_resp == decline:
            # Courtier passed tonight. Slot stays available for a
            # future night — do *not* mark _used.
            engine.log(f"Courtier {self.player.name} declined to use ability tonight.")
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[self.player])
            )
            return

        # Slot is now consumed regardless of in-play / drunk state.
        self._used = True
        self._picked_character = chosen_resp
        if self.player is not None:
            self.player.once_per_game_used = True

        # Find an in-play player carrying that role (if any). Prefer
        # alive seats — matches the engine's _run_preset_night
        # tiebreak when two seats share a name.
        target: Optional["Player"] = None
        for p in engine.players:
            if p.character is None:
                continue
            if p.character.name != chosen_resp:
                continue
            if target is None or (target.dead and not p.dead):
                target = p

        # RESOLUTION: apply the drunk if the Courtier has ability AND
        # the chosen role is in play. The Courtier never learns whether
        # the pick succeeded — no INFORMATION step, no result feedback.
        if target is None:
            engine.log(
                f"Courtier {self.player.name} chose {chosen_resp} — "
                f"not in play, no effect."
            )
        elif not self.player.has_ability:
            engine.log(
                f"Courtier {self.player.name} is drunk/poisoned — "
                f"no real drunkening lands on the {chosen_resp}."
            )
        else:
            self._target_player_id = target.id
            self._phases_remaining = self._DURATION_PHASE_TICKS
            self._set_drunk(engine, target)

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )
