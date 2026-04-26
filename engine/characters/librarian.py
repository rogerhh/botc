"""Librarian.

    "You start knowing that 1 of 2 players is a particular Outsider.
     (Or that zero are in play.)"

First-night-only information ability. Two flavours:

  * **No Outsiders in play (sober):** the Librarian is shown
    "There are no Outsiders in play." with no storyteller prompts.
  * **At least one Outsider in play (sober):** ST picks the Outsider →
    engine finds the player who is that Outsider → ST picks a "wrong"
    player → Librarian sees both players highlighted with the Outsider
    token.

Drunkenness / poisoning (per CLAUDE.md)
---------------------------------------
The drunk/poisoned Librarian has *two* possible fake readings: a "0
Outsiders" reading or a "1 of 2 players is the {Outsider}" reading. We
let the storyteller choose which one to feed the player, because both
are interesting bluffs depending on the table state.

The flow is:

1. **Choose 0 vs 1-of-2.** A YesNo-style prompt asks the storyteller
   whether to show the "0 Outsiders" line. The default is **No** —
   the 1-of-2 path is more interesting (it points at specific players)
   so we lean toward it.

2. **If 0:** show "There are no Outsiders in play." straight away.

3. **If 1-of-2:** ST picks an Outsider on the script (default: an
   Outsider *not* in play, so neither pointed player is actually that
   Outsider) and then picks **two players** to point at. Any two
   non-self players are eligible — the info is fake either way.

Every storyteller prompt that exists *because* the Librarian is
drunk/poisoned carries ``meta["due_to_drunk_poison"] = True`` so the
UI can flag the prompt accordingly. The only control on these prompts
is **Next**; the default is pre-filled.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectCharacterPrompt,
    SelectPlayerPrompt,
    YesNoPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

class Librarian(Character):
    name = "Librarian"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "You start knowing that 1 of 2 players is a particular Outsider. "
        "(Or that zero are in play.)"
    )
    first_night_order = 31
    other_night_order = 0
    reminder_tokens: list = [
        {"name": 'OUTSIDER', "icon": 'librarian_outsider.png'},
    ]

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _show_zero(self, engine: "Engine") -> None:
        """Wake the Librarian and show 'There are no Outsiders in play.'."""
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )
        info_text = "There are no Outsiders in play."
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    "shown_count": 0,
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[],
                data={"info": info_text, "shown_count": 0},
            )
        )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[])
        )

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        in_play_outsiders: List[str] = engine.in_play_character_names_by_type(
            CharType.OUTSIDER
        )
        all_outsiders: List[str] = engine.all_character_names_by_type(
            CharType.OUTSIDER
        )

        # Sober + no Outsiders in play: canonical "0" reading, no ST
        # picks needed.
        if not in_play_outsiders and not is_drunk_or_poisoned:
            self._show_zero(engine)
            return

        # Drunk/poisoned: ST first decides whether to give the "0
        # Outsiders" fake reading or the "1 of 2 players" fake reading.
        # Default = 1 of 2 (more interesting — points at specific
        # players). The only control is Next.
        dp_label = self.player.drunk_poison_label()
        if is_drunk_or_poisoned:
            zero_prompt = YesNoPrompt(
                text="Show '0 Outsiders' to the Librarian?",
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_zero_or_pair",
                    "stage": "st_pre",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": dp_label,
                    "default": False,
                    "correct": (not bool(in_play_outsiders)),
                },
            )
            show_zero = engine.send_prompt(zero_prompt)
            if isinstance(show_zero, bool) and show_zero:
                self._show_zero(engine)
                return
            # Otherwise fall through to the "1 of 2" path with fake
            # data.

        # SELECT (character): pick the Outsider character to show. When
        # drunk/poisoned, the engine pre-picks a plausible-wrong default
        # (an Outsider on the script, preferring one not in play) and
        # surfaces it to the ST with a Next button.
        eligible_chars = (
            sorted(set(all_outsiders))
            if is_drunk_or_poisoned
            else sorted(set(in_play_outsiders))
            or sorted(set(all_outsiders))
        )
        default_char = None
        if is_drunk_or_poisoned and eligible_chars:
            in_play_set = set(in_play_outsiders)
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
                "Pick the Outsider to show the Librarian (drunk/poisoned)."
                if is_drunk_or_poisoned
                else "Pick the Outsider in play to show the Librarian."
            ),
            eligible_characters=eligible_chars,
            target_player_id=self.player.id,
            meta=char_meta,
        )
        chosen_char_name = engine.send_prompt(char_prompt)

        # Identify the seated player who *holds* the chosen Outsider
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
        # (drunk/poisoned, or no actual holder) fall back to picking
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
                # Defensive fallback: any other eligible player.
                if wrong_eligible:
                    wrong_player = engine.get_player(wrong_eligible[0])
            chosen_players = (
                [right_player, wrong_player]
                if wrong_player is not None
                else [right_player]
            )
            # Randomise the visible order so the Librarian can't tell
            # which is which.
            _rand.shuffle(chosen_players)
        else:
            other_player_ids = [
                p.id for p in engine.players if p.id != self.player.id
            ]
            # Drunk/poisoned default: 2 random non-self players whose
            # true roles are NOT the chosen Outsider (so the info is
            # actually wrong). The ST may change the picks.
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
        # Librarian now.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # INFORMATION: show on the Librarian's phone — both player
        # tokens highlighted alongside the Outsider character token.
        names = [p.name for p in chosen_players]
        info_text = (
            f"One of {names[0]} and {names[1]} is the {chosen_char_name}."
            if len(names) == 2
            else f"You learn: {names[0]} is the {chosen_char_name}."
            if len(names) == 1
            else f"You learn: a Librarian ability was triggered for {self.player.name}."
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
