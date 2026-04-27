"""Butler.

    "Each night, choose a player (not yourself): tomorrow you may only
     vote if they are voting too."

Voting-restriction ability. Each night (first and other) the Butler
picks a "Master". On the following day, the Butler may only vote when
their Master also votes.

Implementation
--------------
The voting restriction is enforced by the storyteller / vote-tally UI
when a vote is taken; the engine just tracks the chosen Master here.
We expose the Master via ``self._master`` and add the
``"is_master"`` note to the chosen player so the storyteller's
grimoire can highlight it.

Drunkenness / poisoning: a drunk or poisoned Butler goes through the
motions but the vote restriction does not apply (their vote always
counts). We don't enforce that here either — but the storyteller can
see the Butler is drunk in the grimoire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

class Butler(Character):
    name = "Butler"
    char_type = CharType.OUTSIDER
    ability_text = (
        "Each night, choose a player (not yourself): tomorrow you may "
        "only vote if they are voting too."
    )
    first_night_order = 36
    other_night_order = 53
    reminder_tokens: list = [
        {"name": 'MASTER', "icon": 'butler_master.png'},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Most recently chosen Master. Refreshed each night.
        self._master: Optional["Player"] = None

    @property
    def master(self) -> Optional["Player"]:
        return self._master

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: any other player (rule says "not yourself"; alive or
        # dead is allowed — picking a dead Master means tomorrow you
        # vote only if the dead Master uses their dead vote).
        eligible = [p.id for p in engine.players if p.id != self.player.id]
        sel = SelectPlayerPrompt(
            text="Butler's Master",
            count=1,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,  # player decision (Butler picks)
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_master",
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

        # RESOLUTION: record the Master. Restriction is enforced at
        # vote time by the storyteller / UI, not here.
        self._master = target
        if self.player.has_ability:
            engine.log(
                f"Butler {self.player.name} chose {target.name} as Master."
            )
        else:
            engine.log(
                f"Butler {self.player.name} (drunk/poisoned) picked "
                f"{target.name} but the restriction does not apply."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )

    def can_vote_today(self, master_voting: bool) -> bool:
        """Helper for the vote tally: can the Butler vote on the
        current nomination?

        ``master_voting`` is whether the chosen Master is currently
        raising their hand (or has decided to vote). A drunk/poisoned
        Butler may always vote.
        """
        if self.player is None or not self.player.has_ability:
            return True
        if self._master is None:
            return True
        return bool(master_voting)
