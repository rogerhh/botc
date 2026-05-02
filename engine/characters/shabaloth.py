"""Shabaloth.

    "Each night*, choose 2 players: they die. A dead player you chose
     last night might be regurgitated."

The Shabaloth attacks twice per night and may resurrect one of last
night's victims. Acts every night except the first.

Implementation
--------------
* Each night >= 2:
    1. (Regurgitation step.) If the Shabaloth had at least one
       seat marked from last night's attacks, the storyteller is
       offered a yes/no choice. On yes, they pick which previously-
       attacked seat to resurrect — limited to seats that ended last
       night dead (alive seats Shabaloth attacked but didn't die,
       e.g. an Innkeeper-protected one, are not regurgitated).
    2. (Attack step.) The Shabaloth picks two players and the
       storyteller orders them. Each is killed with cause
       ``DEMON_KILL`` and ``source=self``. Standard Tea Lady /
       Innkeeper / Sailor / Fool / Mayor protections apply per
       character.
* The seats picked tonight are remembered as ``_last_attacked_ids``
  for next night's regurgitation pool. Reset on the new attack pass.
* Drunk/poisoned Shabaloth still wakes and goes through the picks,
  but no kill / no regurgitation lands.
* Exorcist block: skip both steps tonight.

Reminder tokens
---------------
``ATTACKED`` is surfaced on each seat the Shabaloth attacked last
night that is still trackable for regurgitation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt, YesNoPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class ShabalothAttackedEffect(Effect):
    """Marker on a seat the Shabaloth attacked last night.

    The Shabaloth's regurgitation prompt the next night reads these
    effects to know who can be revived. Cleared at the next ability
    cycle (the new attacks reset the list)."""

    kind = "shabaloth_attacked"
    contributes_to_state = None
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


class Shabaloth(Character):
    name = "Shabaloth"
    char_type = CharType.DEMON
    ability_text = (
        "Each night*, choose 2 players: they die. A dead player you "
        "chose last night might be regurgitated."
    )
    first_night_order = 0
    other_night_order = 28
    reminder_tokens: list = [
        {"name": "ATTACKED", "icon": "shabaloth_attacked.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Seats attacked on the most-recent night. The next night's
        # regurgitation step pulls from this list.
        self._last_attacked_ids: List[int] = []

    # ATTACKED markers rendered via ShabalothAttackedEffect emitted
    # in ``ability()`` after each kill.

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        if getattr(engine, "_exorcism_blocked_id", None) == self.player.id:
            engine.log(
                f"Shabaloth {self.player.name}: blocked by the Exorcist."
            )
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Step 1: regurgitation. Only meaningful if some prior victim
        # is currently dead.
        prior_dead = []
        for pid in self._last_attacked_ids:
            try:
                p = engine.get_player(pid)
            except KeyError:
                continue
            if p.dead and p.character is not None:
                prior_dead.append(p)
        if prior_dead and self.player.has_ability:
            ask = YesNoPrompt(
                text="Shabaloth: regurgitate a previous victim?",
                meta={
                    "character": self.name,
                    "step": "regurgitate_yes_no",
                    "stage": "st_post",
                    "default": False,
                },
            )
            do_regurgitate = engine.send_prompt(ask)
            if isinstance(do_regurgitate, bool) and do_regurgitate:
                eligible = [p.id for p in prior_dead]
                sel = SelectPlayerPrompt(
                    text="Pick the player to regurgitate",
                    count=1,
                    eligible_player_ids=eligible,
                    allow_self=False,
                    allow_randomize=True,
                    target_player_id=self.player.id,
                    meta={
                        "character": self.name,
                        "step": "regurgitate_pick",
                        "stage": "st_post",
                    },
                )
                regur_id = engine.send_prompt(sel)
                if isinstance(regur_id, list):
                    regur_id = regur_id[0] if regur_id else None
                if regur_id is not None:
                    try:
                        rp = engine.get_player(int(regur_id))
                        if rp.dead:
                            engine.revive(rp.id)
                            engine.log_reaction(
                                "Shabaloth",
                                f"{rp.name} is regurgitated by the Shabaloth.",
                                target=rp,
                                trigger="regurgitate",
                            )
                    except (KeyError, ValueError, TypeError):
                        pass

        # Step 2: pick 2 to attack.
        eligible = [p.id for p in engine.players if p.alive]
        if len(eligible) < 2:
            return
        sel = SelectPlayerPrompt(
            text="Shabaloth picks 2 players to eat",
            count=2,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_targets",
                "stage": "player",
            },
        )
        chosen = engine.send_prompt(sel)
        if isinstance(chosen, int):
            chosen_ids: List[int] = [chosen]
        elif isinstance(chosen, list):
            chosen_ids = [int(x) for x in chosen]
        else:
            chosen_ids = []
        chosen_players = []
        for pid in chosen_ids:
            try:
                chosen_players.append(engine.get_player(int(pid)))
            except (KeyError, ValueError, TypeError):
                continue
        if len(chosen_players) < 2:
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=chosen_players)
        )

        # Purge previous attack markers (regurgitation already done above).
        for old in list(engine.effects_sourced_by(self)):
            if isinstance(old, ShabalothAttackedEffect):
                engine.purge_effect(old)
        new_attacked: List[int] = []
        if self.player.has_ability:
            for tp in chosen_players[:2]:
                engine.kill(tp.id, DeathCause.DEMON_KILL, source=self)
                new_attacked.append(tp.id)
                if not tp.alive:
                    engine.add_effect(ShabalothAttackedEffect(
                        source=self, targets=[tp.id],
                    ))
        else:
            engine.log(
                f"Shabaloth {self.player.name} is drunk/poisoned — no kills."
            )
        self._last_attacked_ids = new_attacked

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
