"""Klutz.

    "When you learn that you died, publicly choose 1 alive player:
     if they are evil, your team loses."

Outsider whose ability is a daytime, once-per-game choice triggered
*after* the Klutz has died — they wake up dead in the morning (or are
executed during the day) and then point at any alive player.

Engine wiring:

* ``daytime_ability_active_when_dead = True`` keeps the seat's "Use
  ability" button enabled in the storyteller side panel even after
  the Klutz has been killed. The base Engine.use_daytime_ability gate
  honours the same flag so the button can actually be clicked while
  the player is dead.
* ``once_per_game = True`` so the choice is consumed after a single
  use. The base ``Player.once_per_game_used`` machinery already grays
  the button out after the slot is spent.
* :meth:`daytime_ability` opens a :class:`SelectPlayerPrompt`, asks
  the storyteller to record the Klutz's pointed-at player, and (per
  the project's pending-win rule) registers a *pending* win on the
  engine if the picked player is evil. The day plays on; the next
  night runs no abilities; dawn announces the result.

Death-window rule (project house rule):

  * The Klutz can **only** use the ability on the same night/day
    that they die. If they die during a day (e.g. executed), they
    must point that day; if they die during a night, they must
    point on the immediately-following day (the day they "wake up
    dead"). After that day passes, the slot is forfeit.
  * :meth:`reaction` tags the Klutz with the engine's
    ``night_number`` at the moment of death — during day N this
    equals ``day_number`` and during night N it is the upcoming
    day's number, so a single anchor covers both cases. Both
    :meth:`daytime_ability` and the dusk hook on
    :meth:`reaction` consume the slot if the window is missed.

Win bookkeeping:

  * Klutz is on the **good** team and points at an **evil** player →
    the good team loses (evil wins).
  * Klutz is on the **evil** team and points at an **evil** player →
    the evil team loses (good wins). This is the "strange situation"
    called out in the rulebook.
  * Klutz points at a **good** player → nothing happens, the game
    continues. The slot is still consumed — picking a safe target is
    one of the canonical Klutz plays.

Drunk / poisoned: per the standard rulebook treatment, the slot is
spent but no loss is registered. The storyteller can still walk
through the public pointing without ending the game.

The Klutz never registers a *new* win once one is already pending
(the engine guards against double-registration in
``_register_pending_win``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import Alignment, CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class Klutz(Character):
    name = "Klutz"
    char_type = CharType.OUTSIDER
    ability_text = (
        "When you learn that you died, publicly choose 1 alive player: "
        "if they are evil, your team loses."
    )
    first_night_order = 0
    other_night_order = 0
    once_per_game = True
    # The Klutz's ability fires *after* they have died, so the seat's
    # "Use ability" button must remain available while the Klutz is
    # dead.
    daytime_ability_active_when_dead = True
    reminder_tokens: list = []

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._used: bool = False
        # The engine's ``night_number`` recorded at the moment the
        # Klutz dies. Equal to the ``day_number`` of the day on which
        # the Klutz must use their ability (during day N both numbers
        # are N; during night N the upcoming day will be N). ``None``
        # while the Klutz is alive, and reset by :meth:`on_revive`.
        self._death_period: int | None = None

    def reaction(self, event: Event, engine: "Engine") -> None:
        """Track the death window and forfeit the slot at dusk.

        Two cases land here:

        * ``DEATH`` targeting this seat — record the period that the
          ability is allowed to fire (engine ``night_number`` at the
          time of death; see class docstring for why this is the
          correct anchor for both day and night deaths).
        * ``DAY_END`` — if the death window matches the day that's
          ending and the Klutz never fired, consume the slot so the
          storyteller's "Use ability" button grays out on subsequent
          days. The ability is forfeit per the house rule.
        """
        if self.player is not None:
            if event.type is EventType.DEATH and any(
                t.id == self.player.id for t in event.targets
            ):
                self._death_period = engine.night_number
                engine.log(
                    f"Klutz {self.player.name} died — ability window "
                    f"opens for day {self._death_period}."
                )
            elif event.type is EventType.DAY_END:
                if (
                    not self._used
                    and self._death_period is not None
                    and engine.day_number == self._death_period
                ):
                    self._used = True
                    self.player.once_per_game_used = True
                    engine.log(
                        f"Klutz {self.player.name} did not point this "
                        f"day — ability forfeit (must fire same "
                        f"night/day as death)."
                    )
        return super().reaction(event, engine)

    def on_revive(self, engine: "Engine") -> None:
        """Reset the death window if the Klutz is brought back."""
        super().on_revive(engine)
        self._death_period = None

    def daytime_ability(self, engine: "Engine") -> None:
        """Klutz publicly points at an alive player.

        The storyteller drives the public pointing through a
        :class:`SelectPlayerPrompt` over the alive players. Whether
        the chosen player is evil determines whether a pending win is
        parked (announced at the next dawn).
        """
        if self.player is None:
            return
        if self._used:
            engine.log(
                f"Klutz {self.player.name} tried to point but ability "
                f"is already spent."
            )
            return

        # House rule: the Klutz can only use the ability on the same
        # night/day they die. If a death has been recorded and the
        # current day is past it, the slot is forfeit. We tolerate the
        # storyteller firing while the Klutz is still alive (no death
        # period yet) — the existing eligible-list logic handles that
        # corner case.
        if (
            self._death_period is not None
            and engine.day_number != self._death_period
        ):
            engine.log(
                f"Klutz {self.player.name} cannot point — outside "
                f"the death window (died: day {self._death_period}, "
                f"now: day {engine.day_number}). Slot forfeit."
            )
            self._used = True
            self.player.once_per_game_used = True
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # Eligible targets: every currently alive player. The Klutz
        # themselves is dead by the time the ability normally fires,
        # but in case the storyteller fires the button while the
        # Klutz is still alive, exclude the Klutz from their own
        # eligible list — the rule is "1 alive player" with the
        # implicit "other than yourself" carried by the public
        # pointing convention.
        eligible = [
            p.id for p in engine.players
            if p.alive and p.id != self.player.id
        ]
        if not eligible:
            engine.log(
                f"Klutz {self.player.name}: no alive players to point "
                f"at; ability not triggered."
            )
            return

        sel = SelectPlayerPrompt(
            text="Klutz publicly points at a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,  # the Klutz's pick is a public, deliberate decision
            target_player_id=self.player.id,
            meta={"character": self.name, "step": "select_target"},
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

        # Always consume the slot, even if drunk/poisoned or wrong.
        # Per the rulebook the public pointing is a one-shot regardless
        # of effect.
        self._used = True
        if self.player is not None:
            self.player.once_per_game_used = True

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        # Drunk / poisoned Klutz: slot consumed, nothing happens.
        # Note: ``Player.has_ability`` also gates on ``alive``, but the
        # Klutz fires *while dead* by design — so we only check the
        # drunk / poisoned bits here.
        if self.player.drunk or self.player.poisoned:
            engine.log(
                f"Klutz {self.player.name} pointed at {target.name} "
                f"— but ability did not work (drunk/poisoned)."
            )
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[target])
            )
            return

        # Resolve the picked player's effective alignment. We use the
        # standard Check pathway so misregistration overrides (Spy
        # registers as Good, Recluse may register as Evil) flow
        # through the storyteller in the usual way.
        from engine.check import Check
        evil_check = Check(
            attribute="alignment",
            passes=(Alignment.EVIL,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={"step_for": "klutz_target"},
        )
        target_is_evil = self.check(engine, target, evil_check)

        if not target_is_evil:
            engine.log(
                f"Klutz {self.player.name} pointed at {target.name} "
                f"— good player, game continues."
            )
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[target])
            )
            return

        # The chosen player is evil — the Klutz's *own team* loses.
        # If the Klutz is good (the standard case) the good team loses,
        # so evil wins. If the Klutz is evil (the rulebook's "strange
        # situation"), the evil team loses, so good wins. We compute
        # the winner as the opposite of the Klutz's alignment.
        klutz_alignment = self.player.alignment or Alignment.GOOD
        winner = (
            Alignment.EVIL
            if klutz_alignment is Alignment.GOOD
            else Alignment.GOOD
        )
        reason = (
            f"The Klutz ({self.player.name}) publicly chose {target.name}, "
            f"who is evil."
        )
        engine.log(
            f"Klutz {self.player.name} pointed at {target.name} (evil) "
            f"— {winner.value} wins (pending)."
        )
        engine._register_pending_win(winner, reason)

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
