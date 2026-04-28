"""Investigator.

    "You start knowing that 1 of 2 players is a particular Minion."

First-night-only information ability. Same shape as the Washerwoman /
Librarian: the storyteller picks a Minion role, the engine identifies
who registers as that role at ability time (via ``registers_as``), the
storyteller then picks one "wrong" player, and the Investigator's
phone is shown both players plus the chosen Minion character token.

Per the project rule "Investigator always sees the Spy as the Spy",
the Spy's ``registers_as`` override does not fire for the
Investigator — its categories list is ``(MINION,)``, which doesn't
include Townsfolk or Outsider, so the Spy returns its true name
("Spy") which IS itself a Minion. The Recluse, by contrast, *can*
register as a Minion to the Investigator (categories includes Minion
→ Recluse override fires).

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


class Investigator(Character):
    name = "Investigator"
    char_type = CharType.TOWNSFOLK
    ability_text = "You start knowing that 1 of 2 players is a particular Minion."
    first_night_order = 32
    other_night_order = 0
    reminder_tokens: list = [
        # MINION is placed by the engine to help the ST run the
        # first-night "you start knowing" ability. The slot is cleared
        # once the ability resolves, so the grimoire stops rendering
        # it automatically (no separate "first night only" gate).
        {
            "name": 'MINION',
            "icon": 'investigator_minion.png',
        },
    ]

    setup_picks = (
        {
            "kind":         "investigator_minion",
            "slot":         "minion",
            "getter":       "investigator_minion",
            "setter":       "set_investigator_minion",
            "autofill":     "_autofill_investigator_minion",
            "mutex_with":   ("investigator_wrong",),
            "check":        ("char_type", "MINION"),
            "forbid_self":  False,
            "is_typed":     True,
        },
        {
            "kind":         "investigator_wrong",
            "slot":         "wrong",
            "getter":       "investigator_wrong",
            "setter":       "set_investigator_wrong",
            "autofill":     "_autofill_investigator_wrong",
            "mutex_with":   ("investigator_minion",),
            "check":        None,
            "forbid_self":  True,
            "forbid_seen":  True,
        },
    )

    @classmethod
    def accepts_tokens(cls) -> "frozenset[str]":
        # The Investigator herself can't host the Investigator WRONG token.
        return super().accepts_tokens() - {"investigator_wrong"}

    DETECTION_CATEGORIES = (CharType.MINION,)

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        self._chosen_minion: Optional[str] = None
        self._chosen_wrong: Optional[str] = None

    def setup_blocker(self, engine: "Engine") -> "str | None":
        if self.player is None:
            return None
        pool_names = set(engine.pool.list())
        seen = engine.pool.investigator_minion()
        wrong = engine.pool.investigator_wrong()
        if not seen:
            return "Investigator minion unset."
        if seen not in pool_names:
            return "Investigator minion invalid."
        if not wrong:
            return "Investigator wrong unset."
        if (
            wrong not in pool_names
            or wrong == self.name
            or wrong == seen
        ):
            return "Investigator wrong invalid."
        return None

    def absorb_setup_data(self, engine: "Engine", data: dict) -> None:
        """Pre-set seen-Minion + WRONG from UI setup data."""
        super().absorb_setup_data(engine, data)
        if self.player is None:
            return
        investigator_minion = data.get("investigator_minion")
        investigator_wrong = data.get("investigator_wrong")
        if investigator_minion:
            self._chosen_minion = investigator_minion
            engine.log(
                f"{self.player.name} (Investigator) will be shown the "
                f"{investigator_minion} (pre-set)."
            )
        if investigator_wrong:
            self._chosen_wrong = investigator_wrong
            engine.log(
                f"{self.player.name} (Investigator) WRONG token "
                f"placed on the {investigator_wrong} (pre-set)."
            )

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        if self.player is None:
            return
        if mode is SetupMode.SETUP_PHASE:
            minion = engine.pool.investigator_minion()
            wrong = engine.pool.investigator_wrong()
            if minion:
                self._chosen_minion = minion
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

        Two-phase: literal-name match first, then misregistering
        classes only (Spy / Recluse). Skipping non-overriding classes
        is the fix for the ``Lib/WW/Inv ability checks Spy for some
        reason even though Spy is not tagged`` bug; only used when
        ``_chosen_minion`` is unset.
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
                "step_for": "investigator_seen",
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
        """Resolve the seen-token chair to the Minion to show.

        Returns the Minion / Recluse name to show on the Investigator's
        phone, or ``None`` if the chair's registration ends up not
        being a Minion (the Spy seated on the Investigator's
        seen-token may opt out of the Minion registration; the
        Investigator then learns ``0 Minions``, mirroring the
        Librarian's ``0 Outsiders`` reading).

        The Recluse seated as the seen-Minion still uses the standard
        ``recluse_registers_as`` flow (Minion / Demon / Recluse
        eligible list); picking a Minion / Demon name passes the
        check and the Recluse is shown as that role.
        """
        from engine.check import Check

        if seen_role == "Spy":
            # Use Spy's standard registers_as for ``"Spy"`` name —
            # eligible = TF + Outsider + Spy. Picking ``"Spy"`` (the
            # default for Investigator's seen-on-Spy case) passes the
            # check; any other pick fails → Investigator learns 0.
            the_check = Check(
                attribute="name",
                passes=("Spy",),
                detector_name=self.name,
                detector_player_id=self.player.id if self.player else -1,
                extra_meta={
                    "step_for": "investigator_seen",
                    "shown_character": "Spy",
                },
            )
            if self.check(engine, seen_player, the_check):
                return "Spy"
            return None

        if seen_role == "Recluse":
            # Recluse seated as Investigator's seen-Minion. Default
            # registration ``"Recluse"`` would NOT pass a Minion-name
            # check, so we use a list of all Minion names — the ST
            # picks which Minion the Recluse registers as. Picking
            # ``"Recluse"`` makes the check fail and the Investigator
            # learns 0; picking any Minion name passes and that
            # Minion is shown.
            all_minions = engine.all_character_names_by_type(CharType.MINION)
            the_check = Check(
                attribute="name",
                passes=tuple(all_minions),
                detector_name=self.name,
                detector_player_id=self.player.id if self.player else -1,
                extra_meta={
                    "step_for": "investigator_seen",
                },
            )
            registered = seen_player.character.registers_as(engine, the_check)
            if registered in all_minions:
                return registered
            return None

        # True Minion on the seen chair — no prompt.
        return seen_role

    def _show_zero(self, engine: "Engine") -> None:
        """Wake the Investigator and show 'There are no Minions in play.'.

        Used when the Spy is the Investigator's seen target and the
        Storyteller opts the Spy out of registering as a Minion (the
        only registration that would have passed the check). Mirrors
        the Librarian's 0-Outsiders reading. Note this is a *false*
        reading — every game has at least one Minion in play — but
        the Investigator only learns what registers, and a Spy
        registering good is the only seat that could pass the check.
        """
        if self.player is None:
            return
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )
        info_text = "There are no Minions in play."
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
                    "render": {
                        "tokens": [{"label": "0", "body": info_text}],
                    },
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
        engine.pool.clear_investigator_token_slots()

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        dp_label = self.player.drunk_poison_label()

        in_play_minions = engine.in_play_character_names_by_type(CharType.MINION)
        all_minions = engine.all_character_names_by_type(CharType.MINION)

        chosen_char_name: Optional[str] = None
        right_player: Optional["Player"] = None

        # Setup-time seen-token path: only checks the chair carrying
        # the token (no full-table iteration).
        if not is_drunk_or_poisoned and self._chosen_minion:
            seen_role = self._chosen_minion
            seen_player = self._find_player_with_role(engine, seen_role)
            if seen_player is None:
                engine.log(
                    f"Investigator {self.player.name}: pre-set seen "
                    f"role {seen_role!r} has no seated player; falling "
                    f"back to ST pick."
                )
            else:
                chosen_char_name = self._resolve_seen_player(
                    engine, seen_role, seen_player
                )
                if chosen_char_name is None:
                    # Spy seated on the Investigator's seen-Minion
                    # registered good (or Recluse opted out) — show 0.
                    self._show_zero(engine)
                    return
                right_player = seen_player
                engine.log(
                    f"Investigator {self.player.name}: seen-token on "
                    f"{seen_player.name} ({seen_role}) shown as "
                    f"{chosen_char_name}."
                )

        if chosen_char_name is None:
            # Legacy / drunk-poison flow.
            eligible_chars = sorted(set(all_minions))
            default_char = None
            if is_drunk_or_poisoned and eligible_chars:
                in_play_set = set(in_play_minions)
                not_in_play = [c for c in eligible_chars if c not in in_play_set]
                pool = not_in_play or list(eligible_chars)
                default_char = _rand.choice(pool)
            elif eligible_chars:
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
                correct_chars = [
                    c for c in eligible_chars if c in set(in_play_minions)
                ]
                if correct_chars:
                    char_meta["correct"] = correct_chars
            char_prompt = SelectCharacterPrompt(
                text="Minion to show",
                eligible_characters=eligible_chars,
                target_player_id=self.player.id,
                meta=char_meta,
            )
            chosen_char_name = engine.send_prompt(char_prompt)
            right_player = (
                None if is_drunk_or_poisoned
                else self._find_player_registering_as(engine, chosen_char_name)
            )

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
                        f"Investigator {self.player.name}: pre-set "
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

        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        names = [p.name for p in chosen_players]
        info_text = (
            f"One of {names[0]} and {names[1]} is the {chosen_char_name}."
            if len(names) == 2
            else f"You learn: {names[0]} is the {chosen_char_name}."
            if len(names) == 1
            else f"You learn: an Investigator ability was triggered for {self.player.name}."
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
                    "render": {
                        "tokens": [{
                            "label": "ONE OF THESE IS THE "
                                + chosen_char_name.upper(),
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
                data={"info": info_text, "shown_character": chosen_char_name},
            )
        )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )

        # Ability has ended. The MINION / WRONG token slots exist
        # purely to let the storyteller see who the ability would
        # point at while it was running; now that it has resolved,
        # the slots are dead state. Clear them so the grimoire stops
        # rendering the reminder tokens — display always matches
        # state, with no separate "first-night only" flag.
        engine.pool.clear_investigator_token_slots()
