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
   so we lean toward it ("there is at least an Outsider").

2. **If 0:** show "There are no Outsiders in play." straight away.

3. **If 1-of-2:** ST picks an Outsider on the script (default: a
   *correct* in-play Outsider — the wrongness comes from the player
   picks below, not the named role; if no Outsiders are in play, fall
   back to any Outsider on the script) and then picks **two players**
   to point at (default: two non-self players whose true roles are NOT
   the chosen Outsider, so the info is actually wrong). Any two
   non-self players are eligible — the ST can change either pick.

Every storyteller prompt that exists *because* the Librarian is
drunk/poisoned carries ``meta["due_to_drunk_poison"] = True`` so the
UI can flag the prompt accordingly. The only control on these prompts
is **Next**; the default is pre-filled.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.enums import CharType, SetupMode
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
        # First-night-only: the OUTSIDER token exists to help the ST
        # run the night-1 "you start knowing" ability and may be
        # removed from the grimoire display once night 1 ends. It
        # affects no game state. See ``Character.reminder_tokens`` for
        # the flag.
        {
            "name": 'OUTSIDER',
            "icon": 'librarian_outsider.png',
            "first_night_only": True,
        },
    ]

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Pre-set during setup (Engine.apply_setup_data). When non-None
        # (and the Librarian is sober + healthy), the ability uses this
        # Outsider role and skips the SelectCharacterPrompt — the
        # storyteller only picks the WRONG player.
        self._chosen_outsider: Optional[str] = None
        # Pre-set during setup. Names the *role* of the WRONG player
        # the Librarian will be pointed at. Resolved to a player at
        # ability time. When both ``_chosen_outsider`` and
        # ``_chosen_wrong`` are set (and the Librarian is sober +
        # healthy), the first-night ability skips every storyteller
        # prompt — both tokens were placed during setup.
        self._chosen_wrong: Optional[str] = None

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        """Mode-aware on-setup ability.

        ``SETUP_PHASE``: absorb the pool's seen-Outsider and WRONG
        slots into ``self._chosen_outsider`` / ``self._chosen_wrong``
        so the first-night ability can skip prompts. Pure
        read-and-copy; no Storyteller prompts.

        ``IN_GAME``: legacy delegation. The Librarian's prompt-emitting
        work happens inside its first-night ability(), so this is a
        no-op for the legacy path too.
        """
        if self.player is None:
            return
        if mode is SetupMode.SETUP_PHASE:
            outsider = engine.pool.librarian_outsider()
            wrong = engine.pool.librarian_wrong()
            if outsider:
                self._chosen_outsider = outsider
            if wrong:
                self._chosen_wrong = wrong
            return
        self.setup_ability(engine)

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

        # Spy support: when a Spy is in play, the Spy can register as
        # any Outsider — even if no real Outsider is in play. So the
        # canonical "0 Outsiders" sober shortcut doesn't fire when a
        # Spy is in play; the Storyteller still gets a chance to point
        # the Librarian at the Spy registering as some Outsider.
        from engine.characters.spy import find_spy_player as _find_spy_player
        spy_player = _find_spy_player(engine)

        # Sober + no Outsiders in play AND no Spy: canonical "0"
        # reading, no ST picks needed. With a Spy in play the
        # Storyteller still gets the choice of pointing at the Spy.
        if (
            not in_play_outsiders
            and not is_drunk_or_poisoned
            and spy_player is None
        ):
            self._show_zero(engine)
            return

        # Drunk/poisoned: ST first decides whether to give the "0
        # Outsiders" fake reading or the "1 of 2 players" fake reading.
        # Default = 1 of 2 (more interesting — points at specific
        # players). The only control is Next.
        dp_label = self.player.drunk_poison_label()
        if is_drunk_or_poisoned:
            zero_prompt = YesNoPrompt(
                text="Show 0 to Librarian?",
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

        # SELECT (character): pick the Outsider character to show. If
        # the storyteller pre-picked the Outsider in the UI (and the
        # Librarian is sober + healthy) we skip the
        # SelectCharacterPrompt entirely. When drunk/poisoned, the
        # engine pre-picks a *correct* default (a real in-play
        # Outsider) — the wrongness comes from the WRONG-two-players
        # default below, not from the named role. If no Outsider is in
        # play, fall back to any Outsider on the script.
        chosen_char_name: str
        if self._chosen_outsider and not is_drunk_or_poisoned:
            chosen_char_name = self._chosen_outsider
            engine.log(
                f"Librarian {self.player.name}: pre-set seen role = "
                f"{chosen_char_name}."
            )
        else:
            eligible_chars = (
                sorted(set(all_outsiders))
                if (is_drunk_or_poisoned or spy_player is not None)
                else sorted(set(in_play_outsiders))
                or sorted(set(all_outsiders))
            )
            default_char = None
            if is_drunk_or_poisoned and eligible_chars:
                in_play_set = set(in_play_outsiders)
                in_play_pool = [c for c in eligible_chars if c in in_play_set]
                # Prefer a *correct* in-play Outsider; fall back to any
                # Outsider on the script if none are in play.
                pool = in_play_pool or list(eligible_chars)
                default_char = _rand.choice(pool)
            elif eligible_chars:
                # Sober + eligible candidates available: pre-fill a
                # random non-self default. (The Librarian is a
                # Townsfolk and can never literally be an Outsider, so
                # ``self.name`` is never in ``eligible_chars`` — but
                # the self-avoidance filter is harmless and keeps the
                # pattern consistent with the WW / FT.)
                non_self = [c for c in eligible_chars if c != self.name]
                pool = non_self or list(eligible_chars)
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
                # Highlight the *correct* options (in-play Outsiders) so
                # the ST can see at a glance which characters would be
                # truthful. The prefilled default is one of these — the
                # wrongness for the drunk/poisoned read comes from the
                # two-players pick below, not from the named role.
                correct_chars = [
                    c for c in eligible_chars if c in set(in_play_outsiders)
                ]
                if correct_chars:
                    char_meta["correct"] = correct_chars
            char_prompt = SelectCharacterPrompt(
                text="Outsider to show",
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

        # Spy-as-seen-player: same logic as the Washerwoman. If the
        # chosen Outsider has no actual holder and the Spy is in play,
        # the Spy IS the seen player (registering as the chosen
        # Outsider). With both an actual holder and a Spy, ask the
        # Storyteller — default to the actual holder.
        if (
            spy_player is not None
            and not is_drunk_or_poisoned
            and spy_player.id != self.player.id
        ):
            if right_player is None:
                right_player = spy_player
                engine.log(
                    f"Librarian {self.player.name}: Spy "
                    f"({spy_player.name}) is the seen player, "
                    f"registering as {chosen_char_name}."
                )
            else:
                use_spy_prompt = YesNoPrompt(
                    text="Use Spy as seen Librarian target?",
                    target_player_id=self.player.id,
                    meta={
                        "character": self.name,
                        "step": "use_spy_as_seen",
                        "stage": "st_pre",
                        "default": False,
                        "shown_character": chosen_char_name,
                        "actual_holder_id": right_player.id,
                        "actual_holder_name": right_player.name,
                        "spy_player_id": spy_player.id,
                        "spy_player_name": spy_player.name,
                    },
                )
                use_spy = engine.send_prompt(use_spy_prompt)
                if isinstance(use_spy, bool) and use_spy:
                    right_player = spy_player
                    engine.log(
                        f"Librarian {self.player.name}: Spy "
                        f"({spy_player.name}) overrides actual holder; "
                        f"Spy registers as {chosen_char_name}."
                    )

        # SELECT (players to point at): if we know the right player,
        # only ask the storyteller for the *wrong* player; otherwise
        # (drunk/poisoned, or no actual holder) fall back to picking
        # two players.
        if right_player is not None and not is_drunk_or_poisoned:
            wrong_eligible = [
                p.id for p in engine.players
                if p.id != self.player.id and p.id != right_player.id
            ]
            # Pre-resolve the wrong player from ``_chosen_wrong`` if
            # it was set during setup. Same shape as the WW: the
            # setup pick is a *role name* — we look up the seated
            # player who currently holds that role, and only fall
            # through to the SelectPlayerPrompt if no such player
            # exists (defensive — the UI should have validated
            # already).
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
                        f"Librarian {self.player.name}: pre-set "
                        f"WRONG player = {preset_wrong_player.name} "
                        f"({self._chosen_wrong})."
                    )

            if preset_wrong_player is not None:
                wrong_player: Optional["Player"] = preset_wrong_player
            else:
                wrong_prompt = SelectPlayerPrompt(
                    text="Wrong player",
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
                # Highlight the player(s) whose true role matches the
                # chosen Outsider — these are the picks that would make
                # the info actually true.
                correct_pids = [
                    pid for pid in other_player_ids
                    if (engine.get_player(pid).character is not None
                        and engine.get_player(pid).character.name
                            == chosen_char_name)
                ]
                if correct_pids:
                    player_meta["correct"] = correct_pids
            player_prompt = SelectPlayerPrompt(
                text=f"Two players (one is the {chosen_char_name})",
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
