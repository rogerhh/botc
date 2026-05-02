"""Gossip.

    "Each day, you may make a public statement. Tonight, if it was
     true, a player dies."

Two-phase ability:

  * **Daytime** — the Gossip declares a public statement. The
    storyteller is asked yes/no whether the statement was true. If
    yes, the engine arms ``self._truth_pending`` for tonight.
  * **Nightly** (every night except the first) — if the truth flag
    is armed, the storyteller picks any alive player to die. The
    flag is cleared every dawn (DAY_START reaction) so a still-
    pending true statement that didn't trigger by night doesn't
    leak into the next day.

Drunkenness / poisoning
-----------------------
Per the wiki: "If the Gossip made a true statement during the day
while drunk or poisoned, but is sober and healthy when their ability
triggers that night, the Storyteller still kills a player." We capture
the truth flag at daytime regardless of has_ability; the kill at
night gates on the Gossip's *current* has_ability.

The Gossip does not learn whether the kill landed; the storyteller
picks the victim privately at night.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt, YesNoPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class Gossip(Character):
    name = "Gossip"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each day, you may make a public statement. Tonight, if it "
        "was true, a player dies."
    )
    first_night_order = 0
    other_night_order = 47
    reminder_tokens: list = [
        {"name": "DEAD", "icon": "gossip_dead.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # True iff the Gossip made a definite, true public statement
        # this day. Cleared every DAY_START so a pending flag that
        # didn't trigger doesn't leak across days.
        self._truth_pending: bool = False

    def reaction(self, event: Event, engine: "Engine") -> None:
        if event.type is EventType.DAY_START:
            self._truth_pending = False
        return super().reaction(event, engine)

    def daytime_ability(self, engine: "Engine") -> None:
        """Storyteller-driven daytime trigger.

        Asks the storyteller "is the Gossip's statement true?" — on
        yes, the truth flag is armed for tonight. The Gossip player
        does not see this prompt; it's a Storyteller adjudication.
        """
        if self.player is None or self.player.dead:
            return
        ask = YesNoPrompt(
            text=(
                f"Was {self.player.name}'s public statement (Gossip) true?"
            ),
            meta={
                "character": self.name,
                "step": "truth_yes_no",
                "stage": "st_post",
                "default": False,
            },
        )
        ans = engine.send_prompt(ask)
        if isinstance(ans, bool) and ans:
            self._truth_pending = True
            engine.log(
                f"Gossip {self.player.name} made a true statement — "
                f"a player will die tonight."
            )

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        if night_number == 1:
            return False
        if not super().would_act_tonight(engine, night_number):
            return False
        return self._truth_pending

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        if not self._truth_pending:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT — any alive player. Storyteller picks.
        eligible = [p.id for p in engine.players if p.alive]
        if not eligible:
            self._truth_pending = False
            return
        sel = SelectPlayerPrompt(
            text="Gossip kills a player (true-statement payoff)",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=True,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_victim",
                "stage": "st_post",
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0] if target_id else None
        if target_id is None:
            self._truth_pending = False
            return
        try:
            target = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            self._truth_pending = False
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        # RESOLUTION — kill the picked player if Gossip has its
        # ability. The pending flag clears either way.
        if self.player.has_ability:
            engine.kill(target.id, DeathCause.ABILITY, source=self)
        else:
            engine.log(
                f"Gossip {self.player.name} is drunk/poisoned — "
                f"no real death."
            )
        self._truth_pending = False
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
