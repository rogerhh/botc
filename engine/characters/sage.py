"""Sage.

    "If the Demon kills you, you learn that 1 of 2 players is the
     Demon."

The Sage acts only on a night they died **due to a Demon's ability**.
The trigger is reaction-driven: when a ``DEATH`` event fires for the
Sage's seat with ``cause == DeathCause.DEMON_KILL``, the Sage arms
itself for tonight's wake-up. The arming flag is consumed by
``would_act_tonight`` so the action loop only schedules the Sage when
the trigger actually fired.

The single positive ``cause is DEMON_KILL`` check is enough — every
Demon class (Imp, Zombuul, Pukka, Shabaloth, Po, …) routes its
nightly kill through ``engine.kill(target, DeathCause.DEMON_KILL,
source=self)``. Mayor's night-kill redirect carries that cause
through to the redirected target's ``Engine.kill`` call, so a Demon
kill bounced onto the Sage still arms the trigger. Other death
causes (``ABILITY`` from Slayer / Tinker / etc., ``STORYTELLER``,
``EXECUTION``) do not.

How to run (from the rulebook)
------------------------------
* Wake the Sage.
* Point at two players, one of whom is the Demon that killed the
  Sage.
* Put the Sage to sleep.

The two split paths the Storyteller sees mirror the Washerwoman's
"engine fixes the truth, ST picks the misdirection" pattern:

* **Sober & healthy:** the engine fixes one of the pair as the
  *actual* Demon (read off seated state, so a starpassed Imp from
  the Scarlet Woman is picked up correctly). The Storyteller is
  prompted for **only the other (non-Demon) player** — a single-
  player select. A random non-Demon default is pre-filled so a
  Next-only flow yields a valid pair (e.g. the rulebook's "two
  alive players" early-game default, or "1 alive + 1 dead" on the
  final night).
* **Drunk / poisoned:** per CLAUDE.md, the Sage's info is fake.
  The engine pre-fills a *random wrong* default — two non-Demon
  players (avoiding the Demon and the Sage themselves) — and the
  ST is prompted for the full pair so they can change either pick
  before sending. UI language never says "confirm" or "override";
  the prompt is sent with Next.

Drunk/poisoned still produces an INFORMATION shown to the Sage's
phone — the player believes their info is real, only the ST knows it
is fake.

Revives
-------
A revived Sage gets their trigger back. ``on_revive`` resets
``_triggered`` and ``_died_to_demon`` so a subsequent demon kill on
the revived Sage re-arms the ability cleanly.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Sage(Character):
    name = "Sage"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "If the Demon kills you, you learn that 1 of 2 players is the "
        "Demon."
    )
    first_night_order = 0
    other_night_order = 30
    reminder_tokens: list = []

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Set in :meth:`reaction` when the Sage dies and the death's
        # source is a Demon-class character. Cleared on revive.
        # ``would_act_tonight`` consults this flag to decide whether
        # the Sage's action slot should fire tonight.
        self._died_to_demon: bool = False
        # Once-per-death guard: the rulebook trigger fires once per
        # demon-kill death. The flag is reset on revive (see
        # :meth:`Character.on_revive`).
        self._triggered: bool = False

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        """Arm the trigger when the Sage dies due to a Demon's ability.

        Listens on ``DEATH`` events targeting the Sage's seat. The
        single trigger gate is ``cause is DeathCause.DEMON_KILL`` —
        every Demon class routes its nightly kill through
        ``engine.kill(..., DeathCause.DEMON_KILL, source=self)`` and
        the Mayor's redirect carries that cause through, so a
        redirected Demon kill that lands on the Sage still arms the
        trigger. ``DeathCause.ABILITY`` (Slayer, Tinker, Virgin),
        ``DeathCause.STORYTELLER`` and ``DeathCause.EXECUTION`` do
        not.
        """
        if self.player is None:
            return super().reaction(event, engine)
        if event.type is not EventType.DEATH:
            return super().reaction(event, engine)
        if not any(t.id == self.player.id for t in event.targets):
            return super().reaction(event, engine)

        cause = event.data.get("cause") if event.data else None
        if cause is not DeathCause.DEMON_KILL:
            return super().reaction(event, engine)

        source = event.source
        source_name = (
            source.name if source is not None and hasattr(source, "name")
            else "the Demon"
        )
        self._died_to_demon = True
        engine.log_reaction(
            self.name,
            (
                f"{self.player.name} died to {source_name} — Sage "
                f"trigger armed for tonight's wake-up."
            ),
            target=self.player,
            trigger="death",
            effect="armed",
            demon_source=source_name,
        )
        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Night order gating.
    # ------------------------------------------------------------------

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        """The Sage acts iff they died tonight to a Demon's ability.

        Sober/healthy is **not** a precondition. A drunk or poisoned
        Sage still wakes — the rulebook walkthrough explicitly covers
        a Sweetheart-drunk Sage being shown two arbitrary players.
        The drunk/poisoned branch in :meth:`ability` then pre-fills a
        wrong default per CLAUDE.md.
        """
        if self.player is None:
            return False
        if not self._died_to_demon or self._triggered:
            return False
        if self.player not in engine.pending_night_deaths:
            return False
        return True

    # ------------------------------------------------------------------
    # Ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None:
            return
        if not self._died_to_demon or self._triggered:
            return
        if self.player not in engine.pending_night_deaths:
            return

        # Mark spent up-front so a re-entrant call (defensive) doesn't
        # fire twice.
        self._triggered = True

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        dp_label = self.player.drunk_poison_label()

        all_player_ids = [p.id for p in engine.players]
        # The Sage is dead at this point — picking the dead Sage's own
        # seat as one of the two pointed-at players would be silly and
        # the rulebook's worked example never does it. Exclude self.
        eligible_ids = [pid for pid in all_player_ids if pid != self.player.id]

        # ----------------------------------------------------------
        # Identify the Demon seat(s) the engine knows about.
        # ----------------------------------------------------------
        # The "demon player" is whichever currently-seated player is
        # the Demon class. We look at engine state so a starpassed
        # demon (Scarlet Woman → Imp) is read correctly. There is at
        # most one alive Demon in standard scripts, but the lookup
        # is over ALL players (alive or dead) for defence in depth —
        # the source that just killed the Sage may itself have died
        # mid-night (e.g. an Imp self-kill that bounced).
        demon_players: List["Player"] = [
            p for p in engine.players
            if p.character is not None and p.char_type is CharType.DEMON
        ]
        alive_demons = [p for p in demon_players if p.alive]
        candidate_demons = alive_demons or demon_players
        demon_pids = {p.id for p in candidate_demons}

        chosen_players: List["Player"] = []

        if not is_drunk_or_poisoned and candidate_demons:
            # ------------------------------------------------------
            # Sober + healthy: the engine fixes one of the two as the
            # actual Demon (chosen at random among alive Demons for
            # multi-Demon scripts) and the Storyteller only picks the
            # *other* (non-Demon) player. Mirrors the Washerwoman's
            # sober-path "wrong only" prompt.
            # ------------------------------------------------------
            demon_player = _rand.choice(candidate_demons)
            other_eligible = [
                pid for pid in eligible_ids if pid not in demon_pids
            ]
            default_other: Optional[int] = (
                _rand.choice(other_eligible) if other_eligible else None
            )
            meta: dict = {
                "character": self.name,
                "step": "select_other_player",
                "stage": "st_pre",
                "demon_player_id": demon_player.id,
                "demon_player_name": demon_player.name,
            }
            if default_other is not None:
                meta["default"] = default_other
            sel = SelectPlayerPrompt(
                text=f"Other player (one besides the {demon_player.name})",
                count=1,
                eligible_player_ids=other_eligible,
                allow_self=False,
                allow_randomize=False,
                target_player_id=self.player.id,
                meta=meta,
            )
            response = engine.send_prompt(sel)
            if isinstance(response, list):
                response = response[0] if response else None
            other_id: Optional[int] = None
            if response is not None:
                try:
                    other_id = int(response)
                except (TypeError, ValueError):
                    other_id = None
            if other_id is None:
                other_id = default_other
            other_player: Optional["Player"] = None
            if other_id is not None:
                try:
                    other_player = engine.get_player(int(other_id))
                except (KeyError, ValueError, TypeError):
                    other_player = None
            chosen_players = [demon_player]
            if other_player is not None:
                chosen_players.append(other_player)
            # Randomize display order so the Demon isn't always shown
            # first on the Sage's phone.
            _rand.shuffle(chosen_players)
        else:
            # ------------------------------------------------------
            # Drunk / poisoned (or, defensively, no Demon seat found):
            # the Storyteller picks both players. Per CLAUDE.md the
            # engine pre-fills a *random wrong* default — two non-
            # Demon, non-Sage players — so a Next-only flow yields
            # bad info.
            # ------------------------------------------------------
            wrong_pool = [
                pid for pid in eligible_ids if pid not in demon_pids
            ]
            default_pair: List[int] = []
            if len(wrong_pool) >= 2:
                default_pair = _rand.sample(wrong_pool, 2)
            elif wrong_pool:
                default_pair = list(wrong_pool[:2])

            meta = {
                "character": self.name,
                "step": "select_players",
                "stage": "st_post" if is_drunk_or_poisoned else "st_pre",
            }
            if default_pair:
                meta["default"] = default_pair
            if is_drunk_or_poisoned:
                meta["due_to_drunk_poison"] = True
                if dp_label:
                    meta["drunk_poison_state"] = dp_label
                if demon_pids:
                    meta["correct"] = sorted(demon_pids)

            sel = SelectPlayerPrompt(
                text="Two players (one is the Demon)",
                count=2,
                eligible_player_ids=eligible_ids,
                allow_self=False,
                allow_randomize=False,
                target_player_id=self.player.id,
                meta=meta,
            )
            chosen_ids = engine.send_prompt(sel)
            if isinstance(chosen_ids, int):
                chosen_ids = [chosen_ids]
            if not chosen_ids:
                chosen_ids = list(default_pair)
            for pid in chosen_ids:
                try:
                    chosen_players.append(engine.get_player(int(pid)))
                except (KeyError, ValueError, TypeError):
                    continue

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=chosen_players)
        )

        names = [p.name for p in chosen_players]
        if len(names) >= 2:
            names_text = f"{names[0]} and {names[1]}"
        elif len(names) == 1:
            names_text = names[0]
        else:
            names_text = "(no one)"
        info_text = f"One of {names_text} is the Demon."

        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[p.id for p in chosen_players],
                highlight_characters=["Demon"],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    "render": {
                        "tokens": [{
                            "label": "ONE OF THESE IS THE DEMON",
                            "body": ", ".join(p.name for p in chosen_players),
                        }],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=chosen_players,
                data={"info": info_text},
            )
        )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------

    def on_revive(self, engine: "Engine") -> None:
        """Re-arm the Sage on revive.

        :meth:`Character.on_revive` already clears the conventional
        ``_triggered`` flag, so we only need to clear our own
        :attr:`_died_to_demon` arming flag here. After revive, a fresh
        demon kill on the Sage's seat will re-arm the trigger via the
        DEATH-event reaction.
        """
        super().on_revive(engine)
        self._died_to_demon = False
