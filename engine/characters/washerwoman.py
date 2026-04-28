"""Washerwoman.

    "You start knowing that 1 of 2 players is a particular Townsfolk."

First-night-only information ability. The storyteller picks a Townsfolk
character that's in play (or, when drunk/poisoned, any Townsfolk on the
script) and one "wrong" player. At ability time the engine identifies
who registers as the chosen Townsfolk by calling ``registers_as`` on
each non-self player; the seen pair is that player plus the WRONG.

Setup picks (the seen-Townsfolk and WRONG slots) are *defaults*, not
authoritative. They tell the engine which Townsfolk to test for and
which player to point at as the WRONG; the actual "right" player is
discovered at ability time via ``registers_as`` (so a Spy can register
as the chosen Townsfolk, the Recluse cannot — Recluse never registers
as a Townsfolk).

Drunkenness / poisoning (per CLAUDE.md): the engine pre-picks a
plausible-but-wrong default — a random Townsfolk on the script
(preferring one *not* in play) and two random non-self players whose
true roles are not the chosen Townsfolk. The storyteller sees the
defaults pre-filled and may change them; the only control is Next.
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
        # The TOWNSFOLK / WRONG tokens are placed by the engine to help
        # the ST run the first-night "you start knowing" ability. They
        # are cleared from the pool once the ability resolves, so the
        # grimoire stops rendering them automatically (no separate
        # "first night only" gate).
        {
            "name": 'TOWNSFOLK',
            "icon": 'washerwoman_townsfolk.png',
        },
        {
            "name": 'WRONG',
            "icon": 'washerwoman_wrong.png',
        },
    ]

    @classmethod
    def accepts_tokens(cls) -> "frozenset[str]":
        # The WW herself can't host the WW WRONG token.
        return super().accepts_tokens() - {"washerwoman_wrong"}

    # Categories the WW cares about for registration. Only TOWNSFOLK —
    # so the Spy override may fire (Spy can register as a TF) but the
    # Recluse override does not (Recluse only fakes evil).
    DETECTION_CATEGORIES = (CharType.TOWNSFOLK,)

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Pre-set during setup (Engine.apply_setup_data). Names the
        # Townsfolk role the WW's seen-token is on. Resolved at ability
        # time by asking each player's ``registers_as`` whether they
        # register as this role.
        self._chosen_townsfolk: Optional[str] = None
        # Pre-set during setup (Engine.apply_setup_data). Names the
        # *role* of the WRONG player the WW will be pointed at.
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
        :meth:`ability(night_number=1)`).
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
        self.setup_ability(engine)

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _find_player_with_role(
        self,
        engine: "Engine",
        role_name: str,
    ) -> Optional["Player"]:
        """Return the first non-self player whose true character is
        ``role_name`` (no ``registers_as``, no ST prompts)."""
        if self.player is None:
            return None
        for p in engine.players:
            if p.id == self.player.id or p.character is None:
                continue
            if p.character.name == role_name:
                return p
        return None

    def _find_player_registering_as(
        self,
        engine: "Engine",
        role_name: str,
    ) -> Optional["Player"]:
        """Legacy fallback: identify who registers as ``role_name``.

        Two-phase: literal-name match first (cheap, no prompt), then
        only misregistering classes (Spy / Recluse). Skipping
        non-overriding classes here is the fix for the project's
        ``Lib/WW/Inv ability checks Spy for some reason even though
        Spy is not tagged`` bug — only used when the seen-token was
        not pre-set at setup.
        """
        from engine.character import Character as _BaseCharacter
        from engine.check import Check

        if self.player is None:
            return None
        match = self._find_player_with_role(engine, role_name)
        if match is not None:
            return match
        the_check = Check(
            attribute="name",
            passes=(role_name,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={
                "step_for": "washerwoman_seen",
                "shown_character": role_name,
            },
        )
        for p in engine.players:
            if p.id == self.player.id or p.character is None:
                continue
            cls = type(p.character)
            if cls.registers_as is _BaseCharacter.registers_as:
                continue
            if self.check(engine, p, the_check):
                return p
        return None

    def _resolve_seen_player(
        self,
        engine: "Engine",
        seen_role: str,
        seen_player: "Player",
    ) -> Optional[str]:
        """Resolve the seen-token chair to the Townsfolk to show.

        Returns the Townsfolk name to show on the WW's phone, or
        ``None`` if the chair's registration ends up not being a
        Townsfolk (the WW has no ``0`` reading; ``None`` should not
        normally happen — the seen token shouldn't be on a chair that
        cannot fake a Townsfolk).
        """
        from engine.check import Check

        all_townsfolk = engine.all_character_names_by_type(CharType.TOWNSFOLK)

        if seen_role == "Spy":
            the_check = Check(
                attribute="name",
                passes=tuple(all_townsfolk),
                detector_name=self.name,
                detector_player_id=self.player.id if self.player else -1,
                extra_meta={
                    "step_for": "washerwoman_seen",
                    "restrict_categories": (CharType.TOWNSFOLK,),
                },
            )
            registered = seen_player.character.registers_as(engine, the_check)
            if registered in all_townsfolk:
                return registered
            engine.log(
                f"Washerwoman {self.player.name}: Spy registered as "
                f"{registered!r} — falling back to first eligible TF."
            )
            return all_townsfolk[0] if all_townsfolk else None

        # True Townsfolk on the seen chair — no prompt.
        return seen_role

    def ability(self, engine: "Engine", night_number: int) -> None:
        # CHECK_CONDITION: only the first night, only if alive.
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        dp_label = self.player.drunk_poison_label()

        # SELECT (character): pick the Townsfolk to show. Pre-pick from
        # setup if available and the WW is sober + healthy. When
        # drunk/poisoned, the engine pre-picks a plausible-but-wrong
        # default (a Townsfolk on the script, preferring one NOT in
        # play) and surfaces it to the ST with a Next button.
        in_play_townsfolk = engine.in_play_character_names_by_type(CharType.TOWNSFOLK)
        all_townsfolk = engine.all_character_names_by_type(CharType.TOWNSFOLK)

        chosen_char_name: Optional[str] = None
        right_player: Optional["Player"] = None

        # Setup-time seen-token path: only checks the chair carrying
        # the token (no full-table iteration). The chair may carry
        # a true Townsfolk or the Spy.
        if not is_drunk_or_poisoned and self._chosen_townsfolk:
            seen_role = self._chosen_townsfolk
            seen_player = self._find_player_with_role(engine, seen_role)
            if seen_player is None:
                engine.log(
                    f"Washerwoman {self.player.name}: pre-set seen "
                    f"role {seen_role!r} has no seated player; falling "
                    f"back to ST pick."
                )
            else:
                chosen_char_name = self._resolve_seen_player(
                    engine, seen_role, seen_player
                )
                right_player = seen_player
                engine.log(
                    f"Washerwoman {self.player.name}: seen-token on "
                    f"{seen_player.name} ({seen_role}) shown as "
                    f"{chosen_char_name}."
                )

        if chosen_char_name is None:
            # Legacy / drunk-poison flow: ST picks the Townsfolk to
            # show, the engine then identifies the right player via
            # the two-phase fallback (true holder first, Spy/Recluse
            # only if none).
            eligible_chars = sorted(set(all_townsfolk))
            default_char = self._chosen_townsfolk
            if is_drunk_or_poisoned:
                in_play_set = set(in_play_townsfolk)
                not_in_play = [c for c in eligible_chars if c not in in_play_set]
                pool = not_in_play or list(eligible_chars)
                if pool:
                    default_char = _rand.choice(pool)
            elif default_char is None and eligible_chars:
                non_self = [c for c in eligible_chars if c != self.name]
                pool = non_self or list(eligible_chars)
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
                correct_chars = [
                    c for c in eligible_chars if c in set(in_play_townsfolk)
                ]
                if correct_chars:
                    char_meta["correct"] = correct_chars
            char_prompt = SelectCharacterPrompt(
                text="Townsfolk to show",
                eligible_characters=eligible_chars,
                target_player_id=self.player.id,
                meta=char_meta,
            )
            chosen_char_name = engine.send_prompt(char_prompt)
            right_player = (
                None if is_drunk_or_poisoned
                else self._find_player_registering_as(engine, chosen_char_name)
            )

        # SELECT (players to point at): if we know the right player,
        # only ask the storyteller for the *wrong* player; otherwise
        # fall back to the legacy "pick two players" prompt.
        if right_player is not None and not is_drunk_or_poisoned:
            wrong_eligible = [
                p.id for p in engine.players
                if p.id != self.player.id and p.id != right_player.id
            ]
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
                    if wrong_eligible:
                        wrong_player = engine.get_player(wrong_eligible[0])
            chosen_players = (
                [right_player, wrong_player]
                if wrong_player is not None
                else [right_player]
            )
            _rand.shuffle(chosen_players)
        else:
            other_player_ids = [
                p.id for p in engine.players if p.id != self.player.id
            ]
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

        # WAKEUP — pre-wake picks complete; physically wake the WW now.
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

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )

        # Ability has ended. The TOWNSFOLK / WRONG token slots exist
        # purely to let the storyteller see who the ability would
        # point at while it was running; now that it has resolved,
        # the slots are dead state. Clear them so the grimoire stops
        # rendering the reminder tokens — display always matches
        # state, with no separate "first-night only" flag.
        engine.pool.clear_washerwoman_token_slots()
