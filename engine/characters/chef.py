"""Chef.

    "You start knowing how many pairs of evil players there are."

First-night-only information ability. Counts the number of *pairs of
evil players sitting next to each other* in the seating order, treating
the table as a ring. A player is "evil" for this count if their
*registered* role (via :meth:`Character.registers_as`) is a Minion or
Demon — so a Spy registering as a Townsfolk counts as good for the
Chef, and a Recluse registering as a Minion counts as evil.

Drunkenness / poisoning (per CLAUDE.md): the count is a range, so the
engine pre-picks a *random wrong* count and surfaces it to the
storyteller with a Next button. The pickable range is
``0..(E+1)//2`` where ``E`` is the number of evil players seated.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
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

    # Categories the Chef cares about for registration: every type, so
    # the alignment of each player can be derived from their registered
    # role. Spy (override fires on TF/Outsider) and Recluse (override
    # fires on Minion/Demon) both prompt the ST when the Chef inspects
    # them.
    DETECTION_CATEGORIES = (
        CharType.TOWNSFOLK,
        CharType.OUTSIDER,
        CharType.MINION,
        CharType.DEMON,
    )

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        # Walk the seating ring. For each player, run an
        # alignment-attribute Check; an EVIL pass means that player
        # counts as evil for the pair count tonight.
        from engine.check import Check
        from engine.enums import Alignment
        evil_check = Check(
            attribute="alignment",
            passes=(Alignment.EVIL,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={"step_for": "chef_pair_count"},
        )

        ordered = sorted(engine.players, key=lambda p: p.seat)
        n = len(ordered)

        evil_flags: list = []
        for p in ordered:
            if p.character is None:
                evil_flags.append(False)
                continue
            evil_flags.append(self.check(engine, p, evil_check))

        default_count = 0
        if n >= 2:
            for i in range(n):
                if evil_flags[i] and evil_flags[(i + 1) % n]:
                    default_count += 1
        # Edge case: 2 players. The ring "wraps around" so both
        # adjacency edges describe the same pair; clamp to 1.
        if n == 2 and default_count > 1:
            default_count = 1

        # Sober + healthy: trust the computed count, no ST prompt.
        # Drunk/poisoned: range of options — pre-pick a random *wrong*
        # count, surface to ST with a Next button. ST may change.
        if is_drunk_or_poisoned:
            num_evil = sum(1 for f in evil_flags if f)
            max_pairs = (num_evil + 1) // 2
            choices = [str(i) for i in range(max_pairs + 1)]
            default_capped = min(default_count, max_pairs)
            wrong_options = [
                i for i in range(max_pairs + 1) if i != default_capped
            ]
            default_wrong = (
                _rand.choice(wrong_options)
                if wrong_options else default_capped
            )
            prompt = SelectCharacterPrompt(
                text="Count to show",
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

        if shown == 1:
            info_text = "There is 1 pair of evil players sitting next to each other."
        else:
            info_text = (
                f"There are {shown} pairs of evil players sitting next to each other."
            )
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
