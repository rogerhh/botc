"""Professor.

    "Once per game, at night*, choose a dead player: if they are a
     Townsfolk, they are resurrected."

The Professor picks any dead seat from night 2 onward. If that seat
is a Townsfolk, it's revived. Either way the Professor's
once-per-game slot is consumed.

Implementation
--------------
* ``would_act_tonight`` skips the wake once the slot is spent.
* The ability prompt is a ``SelectPlayerPrompt`` over dead seats.
  An additional decline option lets the Professor pass for tonight
  (the slot is *not* consumed on a decline — passing is allowed
  every night until the Professor finally fires).
* Revive uses :meth:`Engine.revive`, which already resets the
  revived character's once-per-game flags and refreshes its
  first-night ability slot via :meth:`Character.on_revive`. So a
  revived character with a "you start knowing" or "first night
  only" ability has it available again on the next nightly walk.
* A drunk/poisoned Professor still consumes the slot but no revive
  occurs.

Reminder tokens
---------------
``NO ABILITY`` surfaces on the Professor's seat once spent;
``ALIVE`` surfaces on the resurrected seat (the revive removes the
DEAD shroud automatically; the ALIVE marker is just a per-game
ledger so the Storyteller can announce who is alive again at the
next dawn).
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


class ProfessorNoAbilityEffect(Effect):
    """NO ABILITY marker on the Professor's seat once spent."""

    kind = "professor_no_ability"
    contributes_to_state = None
    purge_on_source_death = False
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


class ProfessorAliveEffect(Effect):
    """ALIVE marker on a seat the Professor revived.

    Like every BMR ALIVE/DEAD-style ST-announcement reminder, this
    expires at the next dawn — its purpose is to remind the
    storyteller to declare *"this player is alive again"* at dawn.
    Purged via ``on_phase_boundary("dawn")``."""

    kind = "professor_alive"
    contributes_to_state = None
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class Professor(Character):
    name = "Professor"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Once per game, at night*, choose a dead player: if they are "
        "a Townsfolk, they are resurrected."
    )
    first_night_order = 0
    other_night_order = 49
    once_per_game = True
    reminder_tokens: list = [
        {"name": "ALIVE", "icon": "professor_alive.png"},
        {"name": "NO ABILITY", "icon": "professor_no_ability.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._used: bool = False
        self._revived_player_id: Optional[int] = None

    # NO ABILITY / ALIVE rendered via the registry effects emitted
    # in ``ability()``.

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

        # Eligible: any dead player. The Professor may also decline.
        dead_ids = [p.id for p in engine.players if p.dead]
        if not dead_ids:
            return

        # Use a sentinel id 0 for decline (engine ids start at 1).
        decline_id = 0
        eligible = list(dead_ids)
        sel = SelectPlayerPrompt(
            text="Professor may resurrect a dead player (or decline)",
            count=1,
            eligible_player_ids=eligible + [decline_id],
            allow_self=False,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_dead_target",
                "stage": "player",
                "decline_id": decline_id,
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0] if target_id else None
        if target_id is None or int(target_id) == decline_id:
            engine.log(f"Professor {self.player.name} declined tonight.")
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[self.player])
            )
            return
        try:
            target = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            return
        if not target.dead:
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )
        # Goon notify: if the Professor picked a Goon seat, route to
        # ``choose_me``. In practice this no-ops because the Professor
        # only ever picks dead players (gated above with
        # ``if not target.dead: return``), and a dead Goon has no
        # ability so the choose_me gate returns False without firing
        # the drunkening. Kept for consistency with the Group A
        # template.
        engine.notify_goon_chosen(self, target)

        # Slot consumed regardless of result.
        self._used = True
        if self.player is not None:
            self.player.once_per_game_used = True
            engine.add_effect(ProfessorNoAbilityEffect(
                source=self, targets=[self.player.id],
            ))

        # Resurrect iff the target is a Townsfolk and the Professor
        # has its ability.
        if (
            self.player.has_ability
            and target.character is not None
            and target.char_type is CharType.TOWNSFOLK
        ):
            engine.revive(target.id)
            self._revived_player_id = target.id
            engine.add_effect(ProfessorAliveEffect(
                source=self, targets=[target.id],
            ))
            engine.log_reaction(
                "Professor",
                f"{target.name} is resurrected (Professor).",
                target=target,
                trigger="professor_revive",
            )
        else:
            engine.log(
                f"Professor {self.player.name} chose {target.name} — "
                f"no revive (not a Townsfolk or Professor lacks ability)."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
