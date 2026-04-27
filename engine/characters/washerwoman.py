"""Washerwoman.

    "You start knowing that 1 of 2 players is a particular Townsfolk."

First-night-only information ability. The storyteller picks a Townsfolk
character that's in play, then points to the player who is that
Townsfolk plus one "wrong" player. The Washerwoman is told that one of
those two players is the chosen Townsfolk character.

Drunkenness / poisoning (per CLAUDE.md): the engine pre-picks a
plausible-but-wrong default — a random Townsfolk on the script
(preferring one *not* in play) and two random non-self players whose
true roles are not the chosen Townsfolk. The storyteller sees the
defaults pre-filled and may change them; the only control is Next.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType, SetupMode
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectCharacterPrompt,
    SelectPlayerPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

class Washerwoman(Character):
    name = "Washerwoman"
    char_type = CharType.TOWNSFOLK
    ability_text = "You start knowing that 1 of 2 players is a particular Townsfolk."
    first_night_order = 30
    other_night_order = 0
    reminder_tokens: list = [
        {"name": 'TOWNSFOLK', "icon": 'washerwoman_townsfolk.png'},
        {"name": 'WRONG', "icon": 'washerwoman_wrong.png'},
    ]

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Pre-set during setup (Engine.apply_setup_data). When non-None,
        # the ability uses this Townsfolk role and skips the
        # SelectCharacterPrompt — the storyteller only picks the
        # "wrong" player (unless ``_chosen_wrong`` is also set, in
        # which case both the SelectCharacterPrompt *and* the
        # SelectPlayerPrompt are skipped).
        self._chosen_townsfolk: Optional[str] = None
        # Pre-set during setup (Engine.apply_setup_data). When non-None,
        # this names the *role* of the WRONG player the WW will be
        # pointed at. The role uniquely identifies a seated player in
        # the bag, so we resolve it to the player at ability time.
        # When both ``_chosen_townsfolk`` and ``_chosen_wrong`` are
        # set (and the WW is sober + healthy), the ability skips every
        # storyteller prompt — both tokens were placed during setup.
        self._chosen_wrong: Optional[str] = None

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        """Mode-aware on-setup ability.

        ``SETUP_PHASE``: absorb the pool's seen-Townsfolk and WRONG
        slots into ``self._chosen_townsfolk`` / ``self._chosen_wrong``
        so the first-night ability can skip prompts. Pure read-and-copy;
        no Storyteller prompts.

        ``IN_GAME``: delegate to legacy ``setup_ability`` (no-op for
        the Washerwoman — its real work runs in
        :meth:`ability(night_number=1)`). The first-night ability is
        what emits prompts when the slots aren't pre-filled.
        """
        if self.player is None:
            return
        if mode is SetupMode.SETUP_PHASE:
            tf = engine.pool.washerwoman_townsfolk()
            wrong = engine.pool.washerwoman_wrong()
            if tf:
                self._chosen_townsfolk = tf
            if wrong:
                self._chosen_wrong = wrong
            return
        # IN_GAME: legacy delegation. Washerwoman's prompt-emitting
        # work happens inside its first-night ability(), so this is
        # a no-op for the legacy path too.
        self.setup_ability(engine)

    def ability(self, engine: "Engine", night_number: int) -> None:
        # CHECK_CONDITION: only the first night, only if alive.
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        dp_label = self.player.drunk_poison_label()

        # SELECT (character): if the storyteller pre-picked the
        # Townsfolk in the UI we skip the SelectCharacterPrompt; the
        # WW's seen role is decided. Otherwise we ask the storyteller
        # at night. This is a pre-wake ST decision (``stage="st_pre"``)
        # — the storyteller fills it in before physically waking the
        # Washerwoman.
        in_play_townsfolk = engine.in_play_character_names_by_type(CharType.TOWNSFOLK)
        all_townsfolk = engine.all_character_names_by_type(CharType.TOWNSFOLK)
        chosen_char_name: str
        if self._chosen_townsfolk and not is_drunk_or_poisoned:
            chosen_char_name = self._chosen_townsfolk
            engine.log(
                f"Washerwoman {self.player.name}: pre-set seen role = "
                f"{chosen_char_name}."
            )
        else:
            eligible_chars = (
                sorted(set(all_townsfolk))
                if is_drunk_or_poisoned
                else sorted(set(in_play_townsfolk))
                or sorted(set(all_townsfolk))
            )
            # Pre-pick a random plausible-wrong Townsfolk for the
            # drunk/poisoned default. Prefer Townsfolk not in play —
            # that guarantees neither of the 2 players will actually be
            # the named role. Fall back to any Townsfolk if all are in
            # play.
            default_char = self._chosen_townsfolk
            if is_drunk_or_poisoned:
                in_play_set = set(in_play_townsfolk)
                not_in_play = [c for c in eligible_chars if c not in in_play_set]
                pool = not_in_play or list(eligible_chars)
                if pool:
                    default_char = _rand.choice(pool)
            char_meta = {
                "character": self.name,
                "step": "select_character",
                "stage": "st_pre",
                "default": default_char,
            }
            if is_drunk_or_poisoned:
                char_meta["due_to_drunk_poison"] = True
                char_meta["drunk_poison_state"] = dp_label
            char_prompt = SelectCharacterPrompt(
                text=(
                    "Pick the Townsfolk to show the Washerwoman (drunk/poisoned)."
                    if is_drunk_or_poisoned
                    else "Pick a Townsfolk in play to show the Washerwoman."
                ),
                eligible_characters=eligible_chars,
                target_player_id=self.player.id,
                meta=char_meta,
            )
            chosen_char_name = engine.send_prompt(char_prompt)

        # Identify the seated player who *holds* the chosen Townsfolk
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
        # fall back to the legacy "pick two players" prompt.
        if right_player is not None and not is_drunk_or_poisoned:
            wrong_eligible = [
                p.id for p in engine.players
                if p.id != self.player.id and p.id != right_player.id
            ]
            # Pre-resolve the wrong player from ``_chosen_wrong`` if it
            # was set during setup. The setup pick is a *role name* —
            # we look up the seated player who currently holds that
            # role, and only fall through to the SelectPlayerPrompt if
            # no such player exists (defensive — the UI should have
            # validated already).
            preset_wrong_player: Optional["Player"] = None
            if self._chosen_wrong:
                for p in engine.players:
                    if (
                        p.character is not None
                        and p.character.name == self._chosen_wrong
                        and p.id != self.player.id
                        and p.id != right_player.id
                    ):
                        preset_wrong_player = p
                        break
                if preset_wrong_player is not None:
                    engine.log(
                        f"Washerwoman {self.player.name}: pre-set "
                        f"WRONG player = {preset_wrong_player.name} "
                        f"({self._chosen_wrong})."
                    )

            if preset_wrong_player is not None:
                wrong_player: Optional["Player"] = preset_wrong_player
            else:
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
            # Randomise the visible order so the WW can't tell which
            # is which.
            _rand.shuffle(chosen_players)
        else:
            other_player_ids = [
                p.id for p in engine.players if p.id != self.player.id
            ]
            # Drunk/poisoned default: 2 random non-self players whose
            # true roles are NOT the chosen Townsfolk (so the info is
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
                    # Degenerate: fewer than 2 plausible-wrong players.
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
            # Normalize: server may send a single id or a list.
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

        # WAKEUP — dispatched here, AFTER the storyteller's pre-wake
        # picks have been recorded. Engine-internal event so other
        # abilities can react.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # INFORMATION: show on the Washerwoman's phone.
        names = [p.name for p in chosen_players]
        info_text = (
            f"One of {names[0]} and {names[1]} is the {chosen_char_name}."
            if len(names) == 2
            else f"You learn: a Washerwoman ability was triggered for {self.player.name}."
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

        # RESOLUTION: nothing to update on the Washerwoman's player —
        # the only effect was the information just shown.
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
