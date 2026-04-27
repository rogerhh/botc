"""Imp.

    "Each night except the first, choose a player: they die. If you
     choose yourself, you die and a Minion becomes the Imp."

The Demon's nightly kill, plus the self-kill / Minion-promotion
mechanic ("starpassing").

Implementation
--------------
* On every night after the first, the Imp wakes, picks any player
  (alive or dead — picking a dead player simply wastes the kill, but
  is allowed by the rules), and that player dies via
  :meth:`engine.engine.Engine.kill` with ``DEMON_KILL`` cause.
* Soldier protection, Monk protection, Mayor death-redirect are
  enforced by :meth:`Engine.kill` and the Mayor's reaction; the Imp's
  ability doesn't need to know about them.
* If the Imp picks themselves, they die first, and then the
  storyteller is asked to pick which alive Minion becomes the new
  Imp. If the Scarlet Woman's reaction has already triggered
  (alive_before >= 5), the Scarlet Woman has *already* become the
  Imp — but the rules say the Demonhood passes to the Scarlet Woman
  in priority, then to other Minions, so we still ask the
  storyteller (defaulting to the Scarlet Woman if she's now the Imp).

Drunkenness / poisoning: a drunk or poisoned Imp picks a target but
no kill happens (the storyteller still walks them through wakeup).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine

class Imp(Character):
    name = "Imp"
    char_type = CharType.DEMON
    ability_text = (
        "Each night except the first, choose a player: they die. "
        "If you choose yourself, you die and a Minion becomes the Imp."
    )
    first_night_order = 0
    other_night_order = 25
    reminder_tokens: list = [
        {"name": 'DEAD', "icon": 'imp_dead.png'},
    ]

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: any player (per the rules — including dead ones, see
        # the Imp PDF). The Imp can also pick themselves.
        eligible = [p.id for p in engine.players]
        sel = SelectPlayerPrompt(
            text="Imp kills a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,  # player decision (Imp picks)
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

        # Drunk/poisoned Imp: still selects but no real kill.
        if not self.player.has_ability:
            engine.log(
                f"Imp {self.player.name} (drunk/poisoned) tried to kill "
                f"{target.name} — no effect."
            )
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[target])
            )
            return

        # RESOLUTION: kill the chosen player. Engine.kill handles
        # Soldier protection, Monk protection, Mayor redirect, etc.
        is_self_kill = (target.id == self.player.id)
        engine.kill(target.id, DeathCause.DEMON_KILL)

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )

        # Self-kill: the Demonhood must pass to a Minion. Scarlet
        # Woman's reaction may have already promoted her to the Imp;
        # that case shows up as the *current* Imp player no longer
        # being us. Otherwise, the storyteller picks a Minion.
        if is_self_kill:
            self._handle_self_kill(engine)

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _handle_self_kill(self, engine: "Engine") -> None:
        """The Imp killed themselves — pick a Minion to take over.

        If the Scarlet Woman has already taken over (her reaction ran
        synchronously inside the DEATH event dispatch), we don't need
        to do anything. Otherwise, prompt the storyteller to pick an
        alive Minion to become the new Imp.
        """
        # Detect SW takeover: any player whose character is now Imp and
        # who was previously a Minion. The simplest signal: there is an
        # alive Imp that isn't the dead self.
        alive_imps = [
            p for p in engine.alive_players
            if p.character is not None and p.character.name == "Imp"
        ]
        if alive_imps:
            engine.log(
                "Imp self-kill: Demonhood already passed (Scarlet Woman)."
            )
            return

        # Pick a Minion to promote.
        alive_minions = [
            p for p in engine.alive_players
            if p.char_type is CharType.MINION
        ]
        if not alive_minions:
            engine.log(
                "Imp self-kill: no alive Minion to promote — game ends."
            )
            return
        sel = SelectPlayerPrompt(
            text="New Imp",
            count=1,
            eligible_player_ids=[p.id for p in alive_minions],
            allow_self=False,
            target_player_id=self.player.id if self.player else None,
            meta={
                "character": self.name,
                "step": "select_new_imp",
                "stage": "st_post",
            },
        )
        chosen_id = engine.send_prompt(sel)
        if isinstance(chosen_id, list):
            chosen_id = chosen_id[0] if chosen_id else None
        if chosen_id is None:
            return
        try:
            new_imp = engine.get_player(int(chosen_id))
        except (KeyError, ValueError, TypeError):
            return
        engine.log(f"{new_imp.name} becomes the new Imp.")
        engine.change_character(new_imp.id, "Imp")
