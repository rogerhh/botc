"""Empath.

    "Each night, you learn how many of your 2 alive neighbours are evil."

Information ability that fires every night, including the first. The
two "alive neighbours" are the next alive player clockwise and the
next alive player counter-clockwise from the Empath's seat (skipping
dead players entirely).

The "is evil" check goes through :meth:`Character.registers_as`: a
neighbour counts as evil iff their *registered* role (per the
detector's categories — every type) has a char_type in
``{MINION, DEMON}``. So a Spy neighbour registering as a Townsfolk
counts as good, and a Recluse neighbour registering as a Minion
counts as evil. Both prompt the Storyteller as part of the
registration call.

Drunkenness / poisoning (per CLAUDE.md): the count has 3 options
(0/1/2), so the engine pre-picks a *random wrong* count and surfaces
it to the storyteller with a Next button.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Empath(Character):
    name = "Empath"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night, you learn how many of your 2 alive neighbours are evil."
    )
    first_night_order = 34
    other_night_order = 50
    reminder_tokens: list = []

    DETECTION_CATEGORIES = (
        CharType.TOWNSFOLK,
        CharType.OUTSIDER,
        CharType.MINION,
        CharType.DEMON,
    )

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _alive_neighbours(self, engine: "Engine") -> List["Player"]:
        """Return up to two alive neighbours in clockwise + ccw order."""
        if self.player is None:
            return []
        ordered = sorted(engine.players, key=lambda p: p.seat)
        n = len(ordered)
        if n <= 1:
            return []
        try:
            self_idx = next(
                i for i, p in enumerate(ordered) if p.id == self.player.id
            )
        except StopIteration:
            return []
        neighbours: List["Player"] = []
        for offset in range(1, n):
            cand = ordered[(self_idx + offset) % n]
            if cand.id == self.player.id:
                break
            if cand.alive:
                neighbours.append(cand)
                break
        for offset in range(1, n):
            cand = ordered[(self_idx - offset) % n]
            if cand.id == self.player.id:
                break
            if cand.alive:
                if not neighbours or cand.id != neighbours[0].id:
                    neighbours.append(cand)
                break
        return neighbours

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        neighbours = self._alive_neighbours(engine)

        # For each alive neighbour, run an alignment-attribute Check.
        # Spy / Recluse overrides may fire and prompt the Storyteller
        # (with reduced eligible options because attribute=alignment).
        from engine.check import Check
        from engine.enums import Alignment
        evil_check = Check(
            attribute="alignment",
            passes=(Alignment.EVIL,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={"step_for": "empath_neighbour"},
        )

        default_count = 0
        for p in neighbours:
            if p.character is None:
                continue
            if self.check(engine, p, evil_check):
                default_count += 1

        # Sober + healthy: trust the computed count, no ST prompt.
        # Drunk/poisoned: 3 options (0/1/2) — default to a random
        # *wrong* count and surface it to the ST.
        if is_drunk_or_poisoned:
            wrong_options = [c for c in (0, 1, 2) if c != default_count]
            default_wrong = (
                _rand.choice(wrong_options)
                if wrong_options else default_count
            )
            prompt = SelectCharacterPrompt(
                text="Count to show",
                eligible_characters=["0", "1", "2"],
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_count",
                    "stage": "st_pre",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": str(default_wrong),
                    "correct": str(default_count),
                    "neighbour_player_ids": [p.id for p in neighbours],
                },
            )
            chosen = engine.send_prompt(prompt)
            try:
                shown = int(chosen)
            except (TypeError, ValueError):
                shown = default_wrong
        else:
            shown = default_count

        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        if shown == 1:
            info_text = "1 of your alive neighbours is evil."
        else:
            info_text = f"{shown} of your alive neighbours are evil."
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[p.id for p in neighbours],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=neighbours,
                data={"info": info_text, "count": shown},
            )
        )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=neighbours)
        )
