"""Investigator.

    "You start knowing that 1 of 2 players is a particular Minion."

First-night-only information ability. Same shape as the Washerwoman /
Librarian: the storyteller picks a Minion character that's in play,
the engine identifies the player holding that role, the storyteller
then picks one "wrong" player, and the Investigator's phone is shown
both players plus the chosen Minion character token.

There is always at least one Minion in play, so unlike the Librarian
we don't need a "0" branch.

Drunkenness / poisoning (per CLAUDE.md): the engine pre-picks a
plausible-wrong default — a random Minion on the script (preferring
one *not* in play) and two random non-self players whose true roles
are not the chosen Minion. The storyteller sees the defaults
pre-filled and may change them; the only control is Next.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectCharacterPrompt,
    SelectPlayerPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

class Investigator(Character):
    name = "Investigator"
    char_type = CharType.TOWNSFOLK
    ability_text = "You start knowing that 1 of 2 players is a particular Minion."
    first_night_order = 32
    other_night_order = 0
    reminder_tokens: list = [
        {"name": 'MINION', "icon": 'investigator_minion.png'},
    ]

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        dp_label = self.player.drunk_poison_label()

        # SELECT (character): pick the Minion character to show. When
        # drunk/poisoned, the engine pre-picks a plausible-wrong default
        # (a Minion on the script, preferring one not in play) and
        # surfaces it to the ST with a Next button.
        in_play_minions = engine.in_play_character_names_by_type(CharType.MINION)
        all_minions = engine.all_character_names_by_type(CharType.MINION)
        eligible_chars = (
            sorted(set(all_minions))
            if is_drunk_or_poisoned
            else sorted(set(in_play_minions) or set(all_minions))
        )
        default_char = None
        if is_drunk_or_poisoned and eligible_chars:
            in_play_set = set(in_play_minions)
            not_in_play = [c for c in eligible_chars if c not in in_play_set]
            pool = not_in_play or list(eligible_chars)
            default_char = _rand.choice(pool)
        char_meta = {
            "character": self.name,
            "step": "select_character",
            "stage": "st_pre",
            **({"default": default_char} if default_char else {}),
        }
        if is_drunk_or_poisoned:
            char_meta["due_to_drunk_poison"] = True
            char_meta["drunk_poison_state"] = dp_label
        char_prompt = SelectCharacterPrompt(
            text=(
                "Pick the Minion to show the Investigator (drunk/poisoned)."
                if is_drunk_or_poisoned
                else "Pick the Minion in play to show the Investigator."
            ),
            eligible_characters=eligible_chars,
            target_player_id=self.player.id,
            meta=char_meta,
        )
        chosen_char_name = engine.send_prompt(char_prompt)

        # Identify the seated player who *holds* the chosen Minion
        # role (when one exists). For the legitimate flow this is the
        # "right" player; for the drunk/poisoned flow there may be no
        # such player and we leave it None.
        right_player: Optional["Player"] = None
        for p in engine.players:
            if p.character is not None and p.character.name == chosen_char_name:
                if p.id != self.player.id:
                    right_player = p
                    break

        # SELECT (players to point at): if we know the right player,
        # only ask the storyteller for the *wrong* player; otherwise
        # (drunk/poisoned with no actual holder) fall back to picking
        # two players.
        if right_player is not None and not is_drunk_or_poisoned:
            wrong_eligible = [
                p.id for p in engine.players
                if p.id != self.player.id and p.id != right_player.id
            ]
            wrong_prompt = SelectPlayerPrompt(
                text=(
                    f"Pick the WRONG player to point at — the other player "
                    f"alongside {right_player.name} who is *not* the "
                    f"{chosen_char_name}."
                ),
                count=1,
                eligible_player_ids=wrong_eligible,
                allow_self=False,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_wrong_player",
                    "stage": "st_pre",
                    "shown_character": chosen_char_name,
                    "right_player_id": right_player.id,
                    "right_player_name": right_player.name,
                },
            )
            wrong_id = engine.send_prompt(wrong_prompt)
            if isinstance(wrong_id, list):
                wrong_id = wrong_id[0] if wrong_id else None
            try:
                wrong_player = (
                    engine.get_player(int(wrong_id))
                    if wrong_id is not None else None
                )
            except (KeyError, ValueError, TypeError):
                wrong_player = None
            if wrong_player is None:
                if wrong_eligible:
                    wrong_player = engine.get_player(wrong_eligible[0])
            chosen_players = (
                [right_player, wrong_player]
                if wrong_player is not None
                else [right_player]
            )
            # Randomise the visible order so the Investigator can't
            # tell which is which.
            _rand.shuffle(chosen_players)
        else:
            other_player_ids = [
                p.id for p in engine.players if p.id != self.player.id
            ]
            # Drunk/poisoned default: 2 random non-self players whose
            # true roles are NOT the chosen Minion. The ST may change
            # the picks.
            default_pids = None
            if is_drunk_or_poisoned:
                wrong_pool = [
                    pid for pid in other_player_ids
                    if (engine.get_player(pid).character is None
                        or engine.get_player(pid).character.name != chosen_char_name)
                ]
                if len(wrong_pool) >= 2:
                    default_pids = _rand.sample(wrong_pool, 2)
                elif wrong_pool:
                    default_pids = list(wrong_pool[:2])
            player_meta = {
                "character": self.name,
                "step": "select_players",
                "stage": "st_pre",
                "shown_character": chosen_char_name,
                **({"default": default_pids} if default_pids else {}),
            }
            if is_drunk_or_poisoned:
                player_meta["due_to_drunk_poison"] = True
                player_meta["drunk_poison_state"] = dp_label
            player_prompt = SelectPlayerPrompt(
                text=(
                    f"Pick the two players to point at — one is the "
                    f"{chosen_char_name}, one is wrong."
                ),
                count=2,
                eligible_player_ids=other_player_ids,
                allow_self=False,
                target_player_id=self.player.id,
                meta=player_meta,
            )
            chosen_player_ids = engine.send_prompt(player_prompt)
            if isinstance(chosen_player_ids, int):
                chosen_player_ids = [chosen_player_ids]
            chosen_players = [engine.get_player(pid) for pid in chosen_player_ids]

        engine.dispatch(
            Event(
                EventType.SELECT,
                source=self,
                targets=chosen_players,
                data={"shown_character": chosen_char_name},
            )
        )

        # WAKEUP — pre-wake picks complete; physically wake the
        # Investigator now.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        names = [p.name for p in chosen_players]
        info_text = (
            f"One of {names[0]} and {names[1]} is the {chosen_char_name}."
            if len(names) == 2
            else f"You learn: {names[0]} is the {chosen_char_name}."
            if len(names) == 1
            else f"You learn that one of these players is the {chosen_char_name}."
        )
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[p.id for p in chosen_players],
                highlight_characters=[chosen_char_name],
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
                targets=chosen_players,
                data={"info": info_text, "shown_character": chosen_char_name},
            )
        )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
