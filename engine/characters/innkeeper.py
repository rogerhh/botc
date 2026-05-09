"""Innkeeper.

    "Each night*, choose 2 players: they can't die tonight, but 1 is
     drunk until dusk."

The Innkeeper acts every night except the first. They pick two
players (which may include themself); both are protected from death
for the rest of the night, and the Storyteller picks one of the two
to be drunk for tonight and the next day.

Effect model (registry-managed)
-------------------------------
Two distinct effects, both added at the same ability resolution:

  * :class:`InnkeeperSafeEffect` — targets the two protected seats.
    Cancels any PRE_DEATH on either seat at night (the
    ``not event.data.get("force")`` check still honours Assassin's
    once-per-game force-kill, per the design doc Q-batch4-1). Purges
    itself at the next dawn (the wiki's "only protects at night,
    not the day").

  * :class:`InnkeeperDrunkEffect` — targets the one chosen-drunk seat.
    ``contributes_to_state = "drunk"`` so the resolver writes
    ``Player.drunk``. Purges at the next dusk (the wiki's "drunk
    until dusk").

Both inherit the standard lifecycle defaults: purge on Innkeeper
death or character change, deactivate when Innkeeper droisoned. So
all the cleanup paths the legacy implementation hand-coded fall out
for free.

Drunkenness / poisoning
-----------------------
A drunk or poisoned Innkeeper goes through the motions (storyteller
still wakes them, picks two, picks who's drunk) but no effects are
added — gated on ``self.player.has_ability`` at resolution time.
This satisfies the registry contract: only add effects when the
source has ability at application time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType
from engine.event import Event, EventOutcome, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class InnkeeperSafeEffect(Effect):
    """Two-seat 'cannot die at night' protection.

    Cancels any PRE_DEATH on its targets at night, except force-kills
    (Assassin). Purges at dawn — Innkeeper-safe is night-only.
    """

    kind = "innkeeper_safe"
    contributes_to_state = None  # event-resolving, not a droison state
    # Lifecycle defaults: purge on source death/character-change,
    # deactivate on source droisoned.

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)

    def resolve_event(
        self, engine: "Engine", event: "Event"
    ) -> Optional[EventOutcome]:
        if (
            event.type is EventType.PRE_DEATH
            and engine.phase.is_night
            and not event.data.get("cancelled")
            and not event.data.get("force")
        ):
            # Stamp the canceller for the storyteller console.
            event.data["cancelled_by_character"] = "Innkeeper"
            event.data["cancelled_reason"] = "Innkeeper protects (SAFE)"
            try:
                tgt_name = (
                    event.targets[0].name if event.targets else "?"
                )
                engine.log_reaction(
                    "Innkeeper",
                    (
                        f"{tgt_name} cannot die tonight "
                        f"(Innkeeper protects)."
                    ),
                    target=event.targets[0] if event.targets else None,
                    trigger="pre_death",
                    effect="innkeeper_safe",
                )
            except Exception:  # pragma: no cover (defensive)
                pass
            return EventOutcome.CANCEL
        return None


class InnkeeperDrunkEffect(Effect):
    """One-seat drunkening, lasting until dusk."""

    kind = "innkeeper_drunk"
    contributes_to_state = "drunk"

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dusk":
            engine.purge_effect(self)


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

    # ------------------------------------------------------------------
    # Reactions — all behavior moved to the two effect classes above.
    # No intrinsic reactions remain.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT — 2 players (alive or dead per the wiki rule). Self
        # is allowed. Dead seats can be safe AND drunk; if revived
        # later, the safe and drunk effects persist for their
        # documented durations.
        eligible = [p.id for p in engine.players]
        if len(eligible) < 2:
            return
        sel = SelectPlayerPrompt(
            text="Innkeeper picks 2 players to protect",
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

        # RESOLUTION: Goon-aware notify-all-then-emit.
        #
        # Innkeeper deliberately differs from Shabaloth / Po
        # (which use ``process_targets_with_goon_break`` and care
        # about pick ordering — kills before the Goon land, kills
        # after don't). For the Innkeeper, **pick order does not
        # matter**: if the Goon is among the picks, the retort
        # drunkens the Innkeeper before any of the Innkeeper's own
        # effects can land — neither SAFE nor DRUNK emits, in either
        # ordering. The Innkeeper's ability is conceptually a single
        # "fire," not a per-target loop.
        #
        # Implementation: notify every picked target up-front so the
        # Goon's first-per-night gate fires synchronously if any
        # pick is the Goon. The has_ability gate that follows then
        # blocks all effect emission. This keeps registry state
        # order-independent — there are no inactive Innkeeper-sourced
        # effects to reactivate later if the Goon dies, because we
        # never emit them in the first place.
        for tp in chosen_players[:2]:
            engine.notify_goon_chosen(self, tp)

        if self.player.has_ability:
            engine.add_effect(InnkeeperSafeEffect(
                source=self,
                targets=[p.id for p in chosen_players[:2]],
            ))
            engine.add_effect(InnkeeperDrunkEffect(
                source=self,
                targets=[drunk_player.id],
            ))
            engine.log(
                f"Innkeeper {self.player.name} protects "
                f"{', '.join(p.name for p in chosen_players[:2])} and "
                f"drunkens {drunk_player.name}."
            )
        else:
            engine.log(
                f"Innkeeper {self.player.name} is drunk/poisoned "
                f"(or Goon-retorted) — no real protection or "
                f"drunkening tonight."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
