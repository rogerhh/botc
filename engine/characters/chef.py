"""Chef.

    "You start knowing how many pairs of evil players there are."

First-night-only information ability. Counts the number of *pairs of
evil players sitting next to each other* in the seating order taken
from the chair list (clockwise), treating the table as a ring. Each
pair is counted once: in a row of three evil players you have two
pairs (per ``assets/characters/Chef.pdf``: two adjacent = 1 pair,
three adjacent = 2 pairs, four adjacent = 3 pairs, …). The head and
tail of the seating order are considered adjacent (ring topology).

Drunkenness / poisoning (per CLAUDE.md): the count is a range, so
the engine pre-picks a *random wrong* count and surfaces it to the
storyteller with a Next button. The pickable range is
``0..(E+1)//2`` where ``E`` is the number of evil players seated —
that's the largest plausible count of pairs (e.g. 4 evil players ⇒
max 2 pairs to hand the Chef). The ST may change the pick before it
goes to the player; the only control is Next.

When the Chef is sober and healthy the engine uses the auto-computed
count directly — no ST step.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import Alignment, CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine

class Chef(Character):
    name = "Chef"
    char_type = CharType.TOWNSFOLK
    ability_text = "You start knowing how many pairs of evil players there are."
    first_night_order = 33
    other_night_order = 0
    reminder_tokens: list = []

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        # Compute the count from the chair list's seating order. The
        # engine's ``Player.seat`` attribute mirrors the chair list's
        # clockwise order (set by ``_sync_chairs_to_engine`` when the
        # storyteller starts the game). Walk the ring and count every
        # adjacent (i, i+1 mod N) pair where both players are evil. Per
        # the Chef wiki page (assets/characters/Chef.pdf):
        #   * 2 adjacent evil players = 1 pair
        #   * 3 adjacent evil players = 2 pairs
        #   * 4 adjacent evil players = 3 pairs
        # Walking the ring once gives exactly this — each adjacency is
        # counted at most once.
        ordered = sorted(engine.players, key=lambda p: p.seat)
        n = len(ordered)
        default_count = 0
        if n >= 2:
            for i in range(n):
                a = ordered[i]
                b = ordered[(i + 1) % n]
                if a.alignment is Alignment.EVIL and b.alignment is Alignment.EVIL:
                    default_count += 1
        # Edge case: 2 players. The ring "wraps around" so both
        # adjacency edges describe the same pair; clamp to 1.
        if n == 2 and default_count > 1:
            default_count = 1

        # Sober + healthy: trust the computed count, no ST prompt.
        # Drunk/poisoned: range of options — pre-pick a random *wrong*
        # count, surface to ST with a Next button. ST may change.
        if is_drunk_or_poisoned:
            num_evil = sum(
                1 for p in ordered if p.alignment is Alignment.EVIL
            )
            max_pairs = (num_evil + 1) // 2
            choices = [str(i) for i in range(max_pairs + 1)]
            # Cap default into the allowed range too — the auto-computed
            # default could (in theory) exceed the plausible cap if a
            # long row of evil happened to sit together.
            default_capped = min(default_count, max_pairs)
            wrong_options = [
                i for i in range(max_pairs + 1) if i != default_capped
            ]
            default_wrong = (
                _rand.choice(wrong_options)
                if wrong_options else default_capped
            )
            prompt = SelectCharacterPrompt(
                text="Pick the count to show the Chef (drunk/poisoned).",
                eligible_characters=choices,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_count",
                    "stage": "st_pre",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": str(default_wrong),
                    "correct": str(default_capped),
                    "num_evil": num_evil,
                    "max_pairs": max_pairs,
                },
            )
            chosen = engine.send_prompt(prompt)
            try:
                shown = int(chosen)
            except (TypeError, ValueError):
                shown = default_wrong
        else:
            shown = default_count

        # WAKEUP — pre-wake count is locked in; physically wake the Chef.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        info_text = f"You learn: {shown} pair(s) of evil players are adjacent."
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
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
                targets=[self.player],
                data={"info": info_text, "count": shown},
            )
        )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )
