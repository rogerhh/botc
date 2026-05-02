"""Moonchild.

    "When you learn that you died, publicly choose 1 alive player.
     Tonight, if it was a good player, they die."

Daytime ability triggered after the Moonchild dies. The pattern
mirrors :mod:`engine.characters.klutz` — the seat's "Use ability"
button stays available after death (``daytime_ability_active_when_dead
= True``), and the slot is consumed once-per-game.

Implementation
--------------
* The Moonchild tracks their death period (engine.night_number at
  the moment of death) so the slot is forfeit if the storyteller
  doesn't publicly point this same period.
* On ability fire, the Moonchild publicly picks an alive player. If
  the chosen player is *currently good* and the Moonchild has its
  ability at the moment of pick, the chosen player is queued to die
  the next night via :meth:`engine.engine.Engine.kill` dispatched
  inside the next ``NIGHT_START`` reaction.

Drunkenness / poisoning
-----------------------
Per the wiki: a drunk/poisoned Moonchild at the *moment of pick*
does not kill anyone, even if they later sober up. We capture
``has_ability`` once at pick time. If sober at pick but
drunk/poisoned at night, the queued kill still fires (the kill is
already armed by the pick).

Reminder tokens
---------------
``DEAD`` is surfaced on the chosen seat while the queued kill is
pending; cleared once it lands or expires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import Alignment, CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class MoonchildDeadEffect(Effect):
    """Marker on a seat the Moonchild's pending kill will hit at the
    next night transition (or that just got killed by the trigger).

    Mirrors the legacy ``_queued_kill_id`` storage; rendered via the
    registry so the storyteller's grimoire shows the Moonchild's
    pending pointed-at seat."""

    kind = "moonchild_dead"
    contributes_to_state = None
    purge_on_source_death = False
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


class Moonchild(Character):
    name = "Moonchild"
    char_type = CharType.OUTSIDER
    ability_text = (
        "When you learn that you died, publicly choose 1 alive player. "
        "Tonight, if it was a good player, they die."
    )
    first_night_order = 0
    other_night_order = 0
    once_per_game = True
    daytime_ability_active_when_dead = True
    reminder_tokens: list = [
        {"name": "DEAD", "icon": "moonchild_dead.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._used: bool = False
        # Seat queued to die at the next night transition (cleared
        # after firing or on expiry).
        self._queued_kill_id: Optional[int] = None
        # Engine night_number recorded at the moment of death; the
        # ability must fire on the same period.
        self._death_period: Optional[int] = None

    # DEAD marker rendered via MoonchildDeadEffect emitted when the
    # Moonchild points at a target during their daytime ability.

    def _set_queued_kill(self, engine: "Engine", target_id: Optional[int]) -> None:
        """Sync ``_queued_kill_id`` with the registry's
        :class:`MoonchildDeadEffect`."""
        self._queued_kill_id = target_id
        for old in list(engine.effects_sourced_by(self)):
            if isinstance(old, MoonchildDeadEffect):
                engine.purge_effect(old)
        if target_id is not None:
            engine.add_effect(MoonchildDeadEffect(
                source=self, targets=[target_id],
            ))

    def reaction(self, event: Event, engine: "Engine") -> None:
        if self.player is None:
            return super().reaction(event, engine)
        if event.type is EventType.DEATH and any(
            t.id == self.player.id for t in event.targets
        ):
            self._death_period = engine.night_number
        elif event.type is EventType.DAY_END:
            # Forfeit if the slot wasn't used on the death day.
            if (
                not self._used
                and self._death_period is not None
                and engine.day_number == self._death_period
            ):
                self._used = True
                self.player.once_per_game_used = True
                engine.log(
                    f"Moonchild {self.player.name} did not point — "
                    f"slot forfeit."
                )
        elif event.type is EventType.NIGHT_START:
            # Resolve the queued kill on the next night.
            if self._queued_kill_id is not None:
                try:
                    target = engine.get_player(self._queued_kill_id)
                except KeyError:
                    self._set_queued_kill(engine, None)
                    return super().reaction(event, engine)
                self._set_queued_kill(engine, None)
                if target.alive:
                    engine.log_reaction(
                        "Moonchild",
                        f"{target.name} dies — Moonchild's deathly pick.",
                        target=target,
                        trigger="moonchild_curse",
                    )
                    engine.kill(target.id, DeathCause.ABILITY, source=self)
        return super().reaction(event, engine)

    def on_revive(self, engine: "Engine") -> None:
        super().on_revive(engine)
        self._death_period = None
        self._set_queued_kill(engine, None)

    def daytime_ability(self, engine: "Engine") -> None:
        if self.player is None or self._used:
            return
        if (
            self._death_period is not None
            and engine.day_number != self._death_period
        ):
            self._used = True
            self.player.once_per_game_used = True
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        eligible = [
            p.id for p in engine.players
            if p.alive and p.id != self.player.id
        ]
        if not eligible:
            return
        sel = SelectPlayerPrompt(
            text="Moonchild publicly points at an alive player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,
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

        # Slot is consumed regardless.
        self._used = True
        if self.player is not None:
            self.player.once_per_game_used = True

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        # Effect lands only if Moonchild has ability AT PICK TIME and
        # the target is currently good.
        if (
            (self.player.drunk or self.player.poisoned)
            or target.alignment is not Alignment.GOOD
        ):
            engine.log(
                f"Moonchild {self.player.name} pointed at {target.name} "
                f"— no curse (drunk/poisoned or target not good)."
            )
        else:
            self._set_queued_kill(engine, target.id)
            engine.log(
                f"Moonchild {self.player.name} curses {target.name} — "
                f"will die at next night transition."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
