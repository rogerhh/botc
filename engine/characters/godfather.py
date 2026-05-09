"""Godfather.

    "You start knowing which Outsiders are in play. If 1 died today,
     choose a player tonight: they die. [-1 or +1 Outsider]"

The Godfather learns the Outsider list on the first night, gets a
nightly kill whenever an Outsider dies during the day, and shifts
the Outsider count by +/-1 at setup.

Implementation
--------------
* ``setup_outsider_delta`` is set to ``+1`` by default — a deliberate
  simplification of the rulebook's "+1 or -1 Outsider" choice (the
  Storyteller can manually swap a TF for an Outsider in the bag if
  they want the -1 direction; the engine's symmetric clamp already
  handles small rosters).
* First-night info pass shows the in-play Outsiders' character
  tokens. Drunk/poisoned Godfather sees a random *wrong* Outsider
  set per the project's drunk/info rule — implemented here as a
  Storyteller pre-pick over Outsiders not in play (the wiki's
  "Storyteller may show wrong Outsiders" allowance).
* ``would_act_tonight`` returns True only when an Outsider has died
  during the immediately-preceding day. The day-death tracker is
  updated reactively on ``DEATH`` (not ``EXECUTION``) — the wiki
  says "if 1 died today" without restricting the cause, so Tinker
  daytime deaths and any other daytime Outsider death qualify too.
* Reset the tracker at every ``DAY_START``.

Drunkenness / poisoning
-----------------------
A drunk/poisoned Godfather does not gain the bonus kill (gated on
``has_ability`` at the moment of resolution).
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class GodfatherDiedTodayEffect(Effect):
    """DIED TODAY marker on each Outsider seat that died during the
    current day. Cleared at the next dawn (after the Godfather's
    night kill triggered)."""

    kind = "godfather_died_today"
    contributes_to_state = None
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class GodfatherDeadEffect(Effect):
    """Marker on a seat the Godfather killed last night."""

    kind = "godfather_dead"
    contributes_to_state = None
    # Survives source death: a self-kill must still leave the DEAD
    # marker on the victim's seat. Dawn cleanup (on_phase_boundary)
    # remains responsible for removing the marker.
    purge_on_source_death = False
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class Godfather(Character):
    name = "Godfather"
    char_type = CharType.MINION
    ability_text = (
        "You start knowing which Outsiders are in play. If 1 died today, "
        "choose a player tonight: they die. [-1 or +1 Outsider]"
    )
    first_night_order = 18
    other_night_order = 46
    # Default to +1 Outsider; the storyteller may manually swap to
    # -1 by editing the bag.
    setup_outsider_delta = 1
    setup_townsfolk_delta = -1
    reminder_tokens: list = [
        {"name": "DIED TODAY", "icon": "godfather_died_today.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # IDs of Outsiders that died during the most-recent day.
        # Cleared at every DAY_START.
        self._outsider_died_today_ids: List[int] = []

    # DIED TODAY rendered via GodfatherDiedTodayEffect emitted on
    # day Outsider deaths in ``reaction``.

    def reaction(self, event: Event, engine: "Engine") -> None:
        if event.type is EventType.DAY_START:
            self._outsider_died_today_ids = []
        elif (
            event.type is EventType.DEATH
            and event.targets
            and event.targets[0].char_type is CharType.OUTSIDER
            and engine.phase.value == "day"
        ):
            tid = event.targets[0].id
            if tid not in self._outsider_died_today_ids:
                self._outsider_died_today_ids.append(tid)
                engine.add_effect(GodfatherDiedTodayEffect(
                    source=self, targets=[tid],
                ))
        return super().reaction(event, engine)

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if self.player is None or self.player.dead:
            return False
        if night_number == 1:
            # First night: info pass.
            return True
        # Other nights: only act if an Outsider died yesterday.
        return bool(self._outsider_died_today_ids)

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        if night_number == 1:
            self._first_night_info(engine)
            return

        if not self._outsider_died_today_ids:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Per wiki rule, "choose a player" allows alive or dead picks.
        # A dead pick is wasteful (engine.kill no-ops) but legal.
        eligible = [p.id for p in engine.players]
        sel = SelectPlayerPrompt(
            text="Godfather kills a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_target",
                "stage": "player",
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

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )
        # Goon notify: if the Godfather picked the Goon's seat, the
        # Goon drunkens the Godfather and the kill below is skipped.
        engine.notify_goon_chosen(self, target)

        if self.player.has_ability:
            engine.kill(target.id, DeathCause.ABILITY, source=self)
            if not target.alive:
                engine.add_effect(GodfatherDeadEffect(
                    source=self, targets=[target.id],
                ))
        else:
            engine.log(
                f"Godfather {self.player.name} is drunk/poisoned — no kill."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )

    def _first_night_info(self, engine: "Engine") -> None:
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        in_play_outsiders = [
            p for p in engine.players
            if p.character is not None and p.char_type is CharType.OUTSIDER
        ]
        true_names = sorted(p.character.name for p in in_play_outsiders if p.character)

        # Drunk/poisoned: pre-pick a random wrong set (random subset of
        # Outsiders not in play). Same length as truth.
        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        if is_drunk_or_poisoned:
            all_outsiders = engine.all_character_names_by_type(CharType.OUTSIDER)
            not_in_play = [n for n in all_outsiders if n not in true_names]
            wrong = _rand.sample(
                not_in_play, k=min(len(true_names), len(not_in_play))
            ) if not_in_play else true_names
            shown = wrong if wrong else true_names
        else:
            shown = true_names

        info_text = (
            f"Outsiders in play: {', '.join(shown)}"
            if shown else "No Outsiders in play."
        )
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_characters=list(shown),
                meta={
                    "character": self.name,
                    "step": "outsiders_info",
                    "stage": "info",
                    "render": {
                        "tokens": [{"label": "OUTSIDERS", "body": ", ".join(shown) or "—"}],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[self.player],
                data={"info": info_text, "outsiders": list(shown)},
            )
        )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )
