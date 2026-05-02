"""Courtier.

    "Once per game, at night, choose a character: they are drunk for
     3 nights & 3 days."

The Courtier wakes every night and either shakes their head (declines)
or points at any character icon — *not* a player. If the chosen
character is in play, the seat playing that role is drunk for the
next three nights and three days, starting immediately. After the
Courtier triggers the ability (even if drunk/poisoned, even if the
chosen character isn't in play), they don't wake again.

Implementation (registry-managed)
---------------------------------
The wiki's "3 nights & 3 days" duration becomes a phase-tick counter
on the :class:`CourtierDrunkEffect`. The effect ticks down on every
dusk/dawn boundary and self-purges when the counter reaches zero.

The phase-tick semantics:

  * Pick on night N → counter = 6
  * Dawn into day N+1     → 5 (drunk during day N+1)
  * Dusk into night N+1   → 4 (drunk during night N+1)
  * Dawn into day N+2     → 3 (drunk during day N+2)
  * Dusk into night N+2   → 2 (drunk during night N+2)
  * Dawn into day N+3     → 1 (drunk during day N+3)
  * Dusk into night N+3   → 0 (effect purged before the new night runs)

Conditional re-evaluation
-------------------------
Per the wiki: "If the Courtier made a character drunk, but the
Courtier becomes drunk or poisoned, the player they made drunk
becomes sober again. If the Courtier becomes sober and healthy again
before the three nights and three days have ended, that player
becomes drunk yet again."

This falls out of the registry resolver for free: when the Courtier
is droisoned, the resolver sees source's drunk/poisoned status and
deactivates the effect (target sobers). When the Courtier sobers,
the resolver reactivates (target re-drunks). The phase counter
ticks regardless of activation status — per Q9, inactive effects
still tick, so a 1-night-poisoned Courtier doesn't get bonus time.

Drunk pre-pick at choose time
-----------------------------
Per the wiki: "If the drunk or poisoned Courtier chooses a character,
that character is not drunk, even if the Courtier later becomes
sober and healthy." This is the registry contract — we simply don't
call ``add_effect`` if the Courtier doesn't have ability at pick
time. The slot is still consumed.

Reminder tokens
---------------
The wiki rotates a numbered DRUNK marker on the target each night of
the duration:

    Tonight (pick night N)        → DRUNK 1
    Next night (N+1)              → DRUNK 2
    Next night (N+2)              → DRUNK 3
    At dusk into night N+3        → DRUNK 3 removed, NO ABILITY added

:meth:`CourtierDrunkEffect.token_kind_for_target` maps the phase-tick
counter onto the three icons:

    ticks 6 / 5  → courtier_drunk_1   (pick night + day after)
    ticks 4 / 3  → courtier_drunk_2   (second night + second day)
    ticks 2 / 1  → courtier_drunk_3   (third night + third day)
    tick  0      → no DRUNK token; NO ABILITY appears

NO ABILITY is emitted as a separate self-source effect
(:class:`CourtierNoAbilityEffect`) once the slot is spent and the
duration has finished — or immediately for picks that had no in-play
target / where the Courtier was drunk at pick time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class CourtierDrunkEffect(Effect):
    """The Courtier's 3n3d drunkening, with a per-tick numbered token."""

    kind = "courtier_drunk"
    contributes_to_state = "drunk"
    # Lifecycle defaults — purge on source death/character-change,
    # deactivate-on-source-droisoned (re-activates if source sobers).

    def __init__(self, source: "Character", targets: list) -> None:
        super().__init__(source, targets)
        self.phases_remaining = 6

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        # Per Q9: inactive effects still tick. So this runs regardless
        # of activation status. When the duration ends we add the
        # NO ABILITY marker on the source's seat.
        self.phases_remaining -= 1
        if self.phases_remaining <= 0:
            engine.purge_effect(self)
            # Slot was already consumed at pick time; surface NO
            # ABILITY now that the duration has run out.
            if self.source.player is not None:
                # Avoid duplicates if for some reason the marker is
                # already there.
                existing = [
                    e for e in engine.effects_sourced_by(self.source)
                    if isinstance(e, CourtierNoAbilityEffect)
                ]
                if not existing:
                    engine.add_effect(CourtierNoAbilityEffect(
                        source=self.source,
                        targets=[self.source.player.id],
                    ))

    def token_kind_for_target(
        self, target_id: int, engine: "Engine"
    ) -> Optional[str]:
        if self.phases_remaining >= 5:
            return "courtier_drunk_1"
        if self.phases_remaining >= 3:
            return "courtier_drunk_2"
        return "courtier_drunk_3"


class CourtierNoAbilityEffect(Effect):
    """Visual NO ABILITY marker on the Courtier's own seat once the
    once-per-game slot is spent and the drunkening duration is over.

    Persists post-mortem and through droison; only purged on
    character change."""

    kind = "courtier_no_ability"
    contributes_to_state = None
    purge_on_source_death = False
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


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
        {"name": "DRUNK 1", "icon": "courtier_drunk_1.png"},
        {"name": "DRUNK 2", "icon": "courtier_drunk_2.png"},
        {"name": "DRUNK 3", "icon": "courtier_drunk_3.png"},
        {"name": "NO ABILITY", "icon": "courtier_no_ability.png"},
    ]

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Once-per-game slot indicator. State on the character (the
        # underlying truth); the visual NO ABILITY token is emitted
        # via the registry as a separate effect.
        self._used: bool = False

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
        # active script. The Courtier may decline.
        eligible_chars = engine.all_character_names()
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
            chosen_resp = decline

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[self.player])
        )

        if chosen_resp == decline:
            engine.log(
                f"Courtier {self.player.name} declined to use ability tonight."
            )
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[self.player])
            )
            return

        # Slot is now consumed regardless of in-play / drunk state.
        self._used = True
        if self.player is not None:
            self.player.once_per_game_used = True

        # Find an in-play player carrying that role (if any). Prefer
        # alive seats.
        target: Optional["Player"] = None
        for p in engine.players:
            if p.character is None:
                continue
            if p.character.name != chosen_resp:
                continue
            if target is None or (target.dead and not p.dead):
                target = p

        # RESOLUTION: emit the CourtierDrunkEffect via the registry if
        # the Courtier has ability AND the chosen role is in play. If
        # either condition fails, surface the NO ABILITY marker
        # immediately (the slot is spent with no duration to track).
        if target is None:
            engine.log(
                f"Courtier {self.player.name} chose {chosen_resp} — "
                f"not in play, no effect."
            )
            engine.add_effect(CourtierNoAbilityEffect(
                source=self, targets=[self.player.id],
            ))
        elif not self.player.has_ability:
            engine.log(
                f"Courtier {self.player.name} is drunk/poisoned — "
                f"no real drunkening lands on the {chosen_resp}."
            )
            engine.add_effect(CourtierNoAbilityEffect(
                source=self, targets=[self.player.id],
            ))
        else:
            engine.add_effect(CourtierDrunkEffect(
                source=self, targets=[target.id],
            ))
            engine.log(
                f"Courtier {self.player.name} drunkens "
                f"{target.name} (the {chosen_resp})."
            )
            # NO ABILITY will be added by the CourtierDrunkEffect
            # itself when its duration expires.

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )
