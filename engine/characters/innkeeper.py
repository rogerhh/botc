"""Innkeeper.

    "Each night*, choose 2 players: they can't die tonight, but 1 is
     drunk until dusk."

The Innkeeper acts every night except the first. They pick two
players (which may include themself); both are protected from death
for the rest of the night, and the Storyteller picks one of the two
to be drunk for tonight and the next day.

State model
-----------
The Innkeeper carries two pieces of state:

  * ``_safe_player_ids: Set[int]`` — the SAFE marker on the chosen
    pair *for tonight only*. Populated when the ability fires and
    cleared at the next ``NIGHT_START`` so a stale marker doesn't
    leak into the new night before the Innkeeper has chosen tonight.
  * ``_drunk_target: Optional[Player]`` — the seat the Innkeeper has
    currently drunkened. Mirrors the Sailor's / Poisoner's lifecycle:
    cleared at the next dusk via :meth:`recheck_persistent_effects`,
    or earlier on Innkeeper death / poison / character-change.

Cancellation channel
--------------------
A SAFE seat's death is cancelled at PRE_DEATH for any cause (Demon,
Godfather, Gossip, Storyteller, etc.) — the same
``event.data["cancelled"] = True`` channel used by Mayor / Pacifist /
Tea Lady / Sailor / Fool. The protection only spans the night, so
once dawn lands the SAFE set is cleared on the next NIGHT_START.

Drunkenness / poisoning
-----------------------
A drunk or poisoned Innkeeper goes through the motions (storyteller
still wakes them, picks two, picks who's drunk) but no real
protection or drunkening lands. Gated on ``self.player.has_ability``
at resolution time; reminder-token visibility is gated on the same
flag plus the actual player ``drunk`` flag for the drunk reminder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Set

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Innkeeper(Character):
    name = "Innkeeper"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night*, choose 2 players: they can't die tonight, but 1 "
        "is drunk until dusk."
    )
    first_night_order = 0
    other_night_order = 18
    reminder_tokens: list = [
        {"name": "SAFE", "icon": "innkeeper_safe.png"},
        {"name": "DRUNK", "icon": "innkeeper_drunk.png"},
    ]

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Seat ids the Innkeeper is currently keeping safe *tonight*.
        # Cleared at every NIGHT_START so a fresh night starts blank
        # before the Innkeeper has picked.
        self._safe_player_ids: Set[int] = set()
        # The seat the Innkeeper has currently drunkened. Cleared at
        # the next dusk (natural duration) or on the early-expiry
        # paths (Innkeeper dies, becomes poisoned, swaps role).
        self._drunk_target: Optional["Player"] = None

    # ------------------------------------------------------------------
    # Reminder tokens.
    # ------------------------------------------------------------------

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Place the SAFE token on protected seats and DRUNK on the drunk one.

        SAFE visibility is keyed off the per-night ``_safe_player_ids``
        set, gated on the Innkeeper having ability and on the engine
        being in a night phase. DRUNK visibility is keyed off the
        actual ``Player.drunk`` flag on ``_drunk_target`` (so cleanup
        paths automatically retract the token).
        """
        out: "dict[str, list[int]]" = {}
        if (
            self.player is not None
            and self.player.has_ability
            and engine.phase.is_night
            and self._safe_player_ids
        ):
            safe_ids = []
            for pid in self._safe_player_ids:
                try:
                    p = engine.get_player(pid)
                except KeyError:
                    continue
                if p.character is None:
                    continue
                safe_ids.append(p.id)
            if safe_ids:
                out["innkeeper_safe"] = safe_ids
        if (
            self._drunk_target is not None
            and getattr(self._drunk_target, "character", None) is not None
            and self._drunk_target.drunk
        ):
            out["innkeeper_drunk"] = [self._drunk_target.id]
        return out

    # ------------------------------------------------------------------
    # Cleanup helpers.
    # ------------------------------------------------------------------

    def _clear_drunk(self, engine: "Engine", reason: str) -> None:
        if self._drunk_target is None:
            return
        target = self._drunk_target
        self._drunk_target = None
        if target.drunk:
            target.set_drunk(False)
            engine.log(
                f"{target.name} is no longer drunk ({reason})."
            )

    def recheck_persistent_effects(
        self, engine: "Engine", phase: str
    ) -> None:
        if phase == "dusk":
            self._clear_drunk(engine, "Innkeeper's drunk expired at dusk")

    # ------------------------------------------------------------------
    # Reactions.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        # Reset the per-night SAFE set the moment a new night begins.
        if event.type is EventType.NIGHT_START:
            self._safe_player_ids = set()

        # Cancel deaths on SAFE seats. Any cause; the SAFE protection
        # is a generic "they can't die tonight".
        if (
            event.type is EventType.PRE_DEATH
            and self.player is not None
            and self.player.has_ability
            and self._safe_player_ids
            and event.targets
            and not event.data.get("cancelled")
        ):
            target = event.targets[0]
            if target.id in self._safe_player_ids:
                event.data["cancelled"] = True
                engine.log_reaction(
                    "Innkeeper",
                    (
                        f"{target.name} cannot die tonight "
                        f"(Innkeeper protects)."
                    ),
                    target=target,
                    trigger="pre_death",
                    effect="innkeeper_safe",
                )
                return

        # Drunk-target cleanup paths (mirror Sailor / Poisoner).
        if self.player is not None and event.targets:
            target_self = event.targets[0].id == self.player.id
            if target_self:
                if event.type is EventType.DEATH:
                    self._clear_drunk(engine, "Innkeeper died")
                elif event.type is EventType.POISON:
                    self._clear_drunk(engine, "Innkeeper is poisoned")
                elif event.type is EventType.CHARACTER_CHANGE:
                    new_name = (
                        event.data.get("new_character") if event.data else "?"
                    )
                    self._clear_drunk(
                        engine, f"Innkeeper became the {new_name}"
                    )

        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return

        # Defensive: clear any leftover drunk before tonight's pick.
        self._clear_drunk(engine, "Innkeeper's drunk expired before new pick")

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT — 2 alive players. Self is allowed.
        eligible = [p.id for p in engine.players if p.alive]
        if len(eligible) < 2:
            return
        sel = SelectPlayerPrompt(
            text="Innkeeper picks 2 alive players to protect",
            count=2,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_players",
                "stage": "player",
            },
        )
        chosen_resp = engine.send_prompt(sel)
        if isinstance(chosen_resp, int):
            chosen_ids: List[int] = [chosen_resp]
        elif isinstance(chosen_resp, list):
            chosen_ids = [int(x) for x in chosen_resp]
        else:
            chosen_ids = []
        chosen_players: List["Player"] = []
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

        # ST decision: which of the 2 is drunk.
        drunk_eligible = [p.id for p in chosen_players[:2]]
        # Heuristic default per the wiki tip: an Innkeeper protecting
        # themself is likely to drunken themself, so default to self
        # in that case. Otherwise default to the *first* picked seat
        # — the storyteller can change either way.
        if self.player.id in drunk_eligible:
            default_drunk = self.player.id
        else:
            default_drunk = drunk_eligible[0]

        drunk_sel = SelectPlayerPrompt(
            text=(
                f"Storyteller: which of {chosen_players[0].name} / "
                f"{chosen_players[1].name} is drunk tonight?"
            ),
            count=1,
            eligible_player_ids=drunk_eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_drunk",
                "stage": "st_post",
                "default": default_drunk,
                "chosen_player_ids": list(drunk_eligible),
            },
        )
        drunk_resp = engine.send_prompt(drunk_sel)
        if isinstance(drunk_resp, list):
            drunk_resp = drunk_resp[0] if drunk_resp else None
        try:
            drunk_id = (
                int(drunk_resp) if drunk_resp is not None else default_drunk
            )
        except (TypeError, ValueError):
            drunk_id = default_drunk
        if drunk_id not in drunk_eligible:
            drunk_id = default_drunk
        try:
            drunk_player = engine.get_player(drunk_id)
        except (KeyError, ValueError, TypeError):
            drunk_player = chosen_players[0]

        # RESOLUTION: apply the SAFE set + drunk. Drunk/poisoned
        # Innkeeper goes through the motions but no real effect.
        if self.player.has_ability:
            self._safe_player_ids = {p.id for p in chosen_players[:2]}
            drunk_player.set_drunk(True)
            self._drunk_target = drunk_player
            engine.log(
                f"Innkeeper {self.player.name} protects "
                f"{', '.join(p.name for p in chosen_players[:2])} and "
                f"drunkens {drunk_player.name}."
            )
        else:
            engine.log(
                f"Innkeeper {self.player.name} is drunk/poisoned — "
                f"no real protection or drunkening tonight."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
