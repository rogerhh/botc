"""Librarian.

    "You start knowing that 1 of 2 players is a particular Outsider.
     (Or that zero are in play.)"

First-night-only information ability. Two flavours:

  * **No players register as any Outsider:** Librarian is shown
    "There are no Outsiders in play." with no further player picks.
    Drunk/poisoned mode lets the ST opt into / out of this reading.

  * **At least one player registers as an Outsider:** ST picks the
    Outsider role to show, the engine identifies which player(s)
    register as that role, the ST picks a "wrong" player → Librarian
    sees both players highlighted with the Outsider token.

Setup picks (the seen-Outsider and WRONG slots) are *defaults*, not
authoritative. They tell the engine which Outsider to test for and
which player to point at as the WRONG; the actual "right" player is
discovered at ability time via ``registers_as`` (so a Spy can register
as the chosen Outsider, and the Recluse always registers as itself —
an Outsider — to a Librarian check).

Drunkenness / poisoning (per CLAUDE.md): two possible fake readings —
"0 Outsiders" or "1 of 2 players is the {Outsider}". The Storyteller
picks which to feed the player.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, List, Optional

from engine.character import Character
from engine.effect import SetupEffect
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


class LibrarianOutsiderEffect(SetupEffect):
    """Marker on the seat the Librarian's first-night info will
    point at as the Outsider-of-interest.

    Setup-only, mutex with the WRONG marker. Higher autofill
    priority than WRONG."""

    kind = "librarian_outsider"
    contributes_to_state = None
    setup_only = True
    mutex_kinds = ("librarian_wrong",)
    autofill_priority = 10
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False

    @classmethod
    def can_target(cls, engine: "Engine", chair_id: int) -> bool:
        try:
            p = engine.get_player(chair_id)
        except KeyError:
            return False
        if p.character is None:
            return False
        # Outsider role (Spy can also register).
        return p.character.char_type is CharType.OUTSIDER


class LibrarianWrongEffect(SetupEffect):
    """Marker on the WRONG seat for the Librarian's pair info."""

    kind = "librarian_wrong"
    contributes_to_state = None
    setup_only = True
    mutex_kinds = ("librarian_outsider",)
    autofill_priority = 5
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False

    @classmethod
    def can_target(cls, engine: "Engine", chair_id: int) -> bool:
        try:
            p = engine.get_player(chair_id)
        except KeyError:
            return False
        return p.character is not None


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
        # OUTSIDER is placed by the engine to help the ST run the
        # first-night "you start knowing" ability. The slot is cleared
        # once the ability resolves, so the grimoire stops rendering
        # it automatically (no separate "first night only" gate).
        {
            "name": 'OUTSIDER',
            "icon": 'librarian_outsider.png',
        },
    ]

    setup_picks = (
        {
            "kind":         "librarian_outsider",
            "slot":         "outsider",
            "getter":       "librarian_outsider",
            "setter":       "set_librarian_outsider",
            "autofill":     "_autofill_librarian_outsider",
            "mutex_with":   ("librarian_wrong",),
            "check":        ("char_type", "OUTSIDER"),
            "forbid_self":  False,
            "is_typed":     True,
        },
        {
            "kind":         "librarian_wrong",
            "slot":         "wrong",
            "getter":       "librarian_wrong",
            "setter":       "set_librarian_wrong",
            "autofill":     "_autofill_librarian_wrong",
            "mutex_with":   ("librarian_outsider",),
            "check":        None,
            "forbid_self":  True,
            "forbid_seen":  True,
        },
    )

    @classmethod
    def accepts_tokens(cls) -> "frozenset[str]":
        # The Librarian herself can't host the Librarian WRONG token.
        return super().accepts_tokens() - {"librarian_wrong"}

    DETECTION_CATEGORIES = (CharType.OUTSIDER,)

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        self._chosen_outsider: Optional[str] = None
        self._chosen_wrong: Optional[str] = None

    def setup_blocker(self, engine: "Engine") -> "str | None":
        """The "0 Outsiders" reading is allowed: when no Outsider is
        in the pool, the seen slot is permitted to be empty (and so is
        WRONG). Otherwise both slots must be set and valid.
        """
        from engine import script as script_data
        from engine.enums import CharType
        if self.player is None:
            return None
        pool_names = set(engine.pool.list())
        seen = engine.pool.librarian_outsider()
        wrong = engine.pool.librarian_wrong()
        any_outsider_in_pool = any(
            (spec := script_data.SCRIPT_BY_NAME.get(n)) is not None
            and spec.char_type is CharType.OUTSIDER
            for n in pool_names
        )
        if not seen:
            if any_outsider_in_pool:
                return "Librarian outsider unset."
            return None  # 0-Outsiders reading; both slots empty by design
        if seen not in pool_names:
            return "Librarian outsider invalid."
        if not wrong:
            return "Librarian wrong unset."
        if (
            wrong not in pool_names
            or wrong == self.name
            or wrong == seen
        ):
            return "Librarian wrong invalid."
        return None

    def absorb_setup_data(self, engine: "Engine", data: dict) -> None:
        """Pre-set seen-Outsider + WRONG from UI setup data."""
        super().absorb_setup_data(engine, data)
        if self.player is None:
            return
        librarian_outsider = data.get("librarian_outsider")
        librarian_wrong = data.get("librarian_wrong")
        if librarian_outsider:
            self._chosen_outsider = librarian_outsider
            engine.log(
                f"{self.player.name} (Librarian) will be shown the "
                f"{librarian_outsider} (pre-set)."
            )
        if librarian_wrong:
            self._chosen_wrong = librarian_wrong
            engine.log(
                f"{self.player.name} (Librarian) WRONG token "
                f"placed on the {librarian_wrong} (pre-set)."
            )
        self._refresh_registry_effects(engine)

    def _refresh_registry_effects(self, engine: "Engine") -> None:
        """Synchronise the registry's Librarian seen + wrong effects
        with the current pool/character state. Bridges legacy pool
        storage with the registry-effect model. Idempotent."""
        if self.player is None:
            return
        seen_chair: Optional[int] = None
        wrong_chair: Optional[int] = None
        if self._chosen_outsider:
            for p in engine.players:
                if (
                    p.character is not None
                    and p.character.name == self._chosen_outsider
                ):
                    seen_chair = p.id
                    break
        if self._chosen_wrong:
            for p in engine.players:
                if (
                    p.character is not None
                    and p.character.name == self._chosen_wrong
                ):
                    wrong_chair = p.id
                    break
        for old in list(engine.effects_sourced_by(self)):
            if isinstance(old, (LibrarianOutsiderEffect, LibrarianWrongEffect)):
                engine.purge_effect(old)
        if seen_chair is not None:
            engine.add_effect(LibrarianOutsiderEffect(
                source=self, targets=[seen_chair],
            ))
        if wrong_chair is not None:
            engine.add_effect(LibrarianWrongEffect(
                source=self, targets=[wrong_chair],
            ))

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        if self.player is None:
            return
        if mode is SetupMode.SETUP_PHASE:
            outsider = engine.pool.librarian_outsider()
            wrong = engine.pool.librarian_wrong()
            if outsider:
                self._chosen_outsider = outsider
            if wrong:
                self._chosen_wrong = wrong
            self._refresh_registry_effects(engine)
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
                    "render": {
                        "tokens": [{
                            "label": "0",
                            "body": info_text,
                        }],
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
        # Ability has ended (the "0 Outsiders" reading). Clear pool
        # slots so the grimoire stops rendering the OUTSIDER / WRONG
        # tokens — display always matches state.
        engine.pool.clear_librarian_token_slots()
        for old in list(engine.effects_sourced_by(self)):
            if isinstance(old, (LibrarianOutsiderEffect, LibrarianWrongEffect)):
                engine.purge_effect(old)

    def _find_player_with_role(
        self,
        engine: "Engine",
        role_name: str,
    ) -> Optional["Player"]:
        """Return the first non-self player whose true character is
        ``role_name``, without calling ``registers_as``.

        Used to locate the chair carrying a setup-time pool slot — the
        slot stores a literal role name on a chair (Drunk, Recluse,
        Spy, …), not a target of misregistration. No ST prompts.
        """
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
        """Legacy fallback: find a player who registers as ``role_name``.

        Used when ``_chosen_outsider`` is unset (no setup-time seen
        token), e.g. in the drunk/poisoned ST-pick flow or in
        stripped-down test setups that bypass the pool. Two phases:

          1. True-name match — no ``registers_as`` calls, no prompts.
          2. Override-bearing classes only (Spy / Recluse). Skipping
             non-overriding classes here is the fix for the bug
             described in the project notes: previously every player
             was iterated, so a Spy seated unrelated to the Librarian
             still surfaced a registration prompt.
        """
        from engine.character import Character as _BaseCharacter
        from engine.check import Check

        if self.player is None:
            return None
        # Phase 1: literal role match.
        match = self._find_player_with_role(engine, role_name)
        if match is not None:
            return match
        # Phase 2: misregistering classes only.
        the_check = Check(
            attribute="name",
            passes=(role_name,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={
                "step_for": "librarian_seen",
                "shown_character": role_name,
            },
        )
        for p in engine.players:
            if p.id == self.player.id or p.character is None:
                continue
            cls = type(p.character)
            if cls.registers_as is _BaseCharacter.registers_as:
                # Class doesn't override registers_as — phase 1 already
                # ruled this player out. Skip the no-op check call so
                # we don't fire a spurious Storyteller prompt.
                continue
            if self.check(engine, p, the_check):
                return p
        return None

    def _could_register_as_outsider(self, engine: "Engine") -> bool:
        """Could any non-self player register as some Outsider?

        Used for the sober "0 Outsiders" shortcut: when this returns
        False, no player can possibly register as an Outsider, so we
        skip the role-picking flow and show 0 directly.

        We answer cheaply *without* calling ``registers_as`` (which
        would surface a premature Storyteller prompt to the Spy /
        Recluse). A player can register as an Outsider iff
        ``registration_categories()`` on their class includes Outsider.
        """
        if self.player is None:
            return False
        for p in engine.players:
            if p.id == self.player.id or p.character is None:
                continue
            cls = type(p.character)
            if CharType.OUTSIDER in cls.registration_categories():
                return True
        return False

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def _resolve_seen_player(
        self,
        engine: "Engine",
        seen_role: str,
        seen_player: "Player",
    ) -> Optional[str]:
        """Resolve the seen-token chair to the role to show on the
        Librarian's phone.

        Returns the Outsider name to show, or ``None`` if the chair's
        registration ends up not being an Outsider (the Recluse case
        where the ST opts the Recluse out — the Librarian then learns
        ``0 Outsiders``).

        Three branches keyed by the chair's literal role:

          * **True Outsider** (Saint, Drunk, Butler, …): no
            ``registers_as`` prompt — the chair already registers as
            its own role. Returns ``seen_role``.

          * **Spy**: ``registers_as`` fires with
            ``extra_meta["restrict_categories"] = (OUTSIDER,)`` so the
            ST sees only Outsider names in the eligible list (no Spy,
            no Townsfolk). The Librarian shows whichever Outsider the
            ST picked — no opt-out at this seat.

          * **Recluse**: ``registers_as`` fires with the standard
            Minion / Demon / Recluse eligible list. The default
            (``"Recluse"``) makes the Librarian see the Recluse; if
            the ST opts the Recluse into a Minion / Demon
            registration, the Librarian's check fails and the engine
            shows ``0 Outsiders``.
        """
        from engine.check import Check

        all_outsiders = engine.all_character_names_by_type(CharType.OUTSIDER)

        if seen_role == "Spy":
            the_check = Check(
                attribute="name",
                passes=tuple(all_outsiders),
                detector_name=self.name,
                detector_player_id=self.player.id if self.player else -1,
                extra_meta={
                    "step_for": "librarian_seen",
                    "restrict_categories": (CharType.OUTSIDER,),
                },
            )
            registered = seen_player.character.registers_as(engine, the_check)
            if registered in all_outsiders:
                return registered
            # Spy's restricted prompt should only return an Outsider
            # name; defensive fallback to the first eligible Outsider.
            engine.log(
                f"Librarian {self.player.name}: Spy registered as "
                f"{registered!r} — falling back to first eligible "
                f"Outsider."
            )
            return all_outsiders[0] if all_outsiders else None

        if seen_role == "Recluse":
            the_check = Check(
                attribute="name",
                passes=("Recluse",),
                detector_name=self.name,
                detector_player_id=self.player.id if self.player else -1,
                extra_meta={
                    "step_for": "librarian_seen",
                    "shown_character": "Recluse",
                },
            )
            if self.check(engine, seen_player, the_check):
                return "Recluse"
            return None

        # True Outsider on the seen chair — no prompt.
        return seen_role

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number != 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        dp_label = self.player.drunk_poison_label()

        in_play_outsiders: List[str] = engine.in_play_character_names_by_type(
            CharType.OUTSIDER
        )
        all_outsiders: List[str] = engine.all_character_names_by_type(
            CharType.OUTSIDER
        )

        # Sober "0 Outsiders" shortcut: only if no player could
        # register as any Outsider (no true Outsider, no Spy who could
        # fake one). When a Spy is in play, we skip the shortcut so
        # the ST can still point the Librarian at a Spy registering as
        # some Outsider.
        if (
            not is_drunk_or_poisoned
            and not self._could_register_as_outsider(engine)
        ):
            self._show_zero(engine)
            return

        # Drunk/poisoned: ST first decides whether to show "0
        # Outsiders" or "1 of 2 players". Default = 1 of 2 (more
        # interesting). The only control is Next.
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

        # ------------------------------------------------------------------
        # Sober resolution. Two paths:
        #
        #   * Setup-time seen token placed (``_chosen_outsider`` is set).
        #     The token sits on a specific chair — we look up that chair
        #     and resolve via :meth:`_resolve_seen_player`. The
        #     misregistration prompt (if any) fires only on that chair,
        #     not on every player. This is the fix for the project
        #     note "ability only checks the character with the seen
        #     token at ability time".
        #
        #   * No setup-time token (legacy / stripped-down setups). The
        #     ST is asked which Outsider to show, and we use the
        #     two-phase fallback in
        #     :meth:`_find_player_registering_as` (true holder first,
        #     then any Spy / Recluse misregistering classes).
        #
        # The drunk/poisoned branch always uses the legacy ST-pick
        # path because the seen pair is fabricated.
        # ------------------------------------------------------------------
        chosen_char_name: Optional[str] = None
        right_player: Optional["Player"] = None

        if not is_drunk_or_poisoned and self._chosen_outsider:
            seen_role = self._chosen_outsider
            seen_player = self._find_player_with_role(engine, seen_role)
            if seen_player is None:
                engine.log(
                    f"Librarian {self.player.name}: pre-set seen role "
                    f"{seen_role!r} has no seated player; falling back "
                    f"to ST pick."
                )
            else:
                chosen_char_name = self._resolve_seen_player(
                    engine, seen_role, seen_player
                )
                if chosen_char_name is None:
                    # Recluse opted out / Spy fell through — show 0.
                    self._show_zero(engine)
                    return
                right_player = seen_player
                engine.log(
                    f"Librarian {self.player.name}: seen-token on "
                    f"{seen_player.name} ({seen_role}) shown as "
                    f"{chosen_char_name}."
                )

        if chosen_char_name is None:
            # No setup-time token (or pre-set role has no seated
            # player). Fall back to the legacy "ST picks character"
            # flow.
            eligible_chars = sorted(set(all_outsiders))
            default_char = None
            if is_drunk_or_poisoned and eligible_chars:
                in_play_set = set(in_play_outsiders)
                in_play_pool = [c for c in eligible_chars if c in in_play_set]
                pool = in_play_pool or list(eligible_chars)
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
            right_player = (
                None if is_drunk_or_poisoned
                else self._find_player_registering_as(engine, chosen_char_name)
            )

        # SELECT (players to point at).
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

        # Ability has ended. The OUTSIDER / WRONG token slots exist
        # purely to let the storyteller see who the ability would
        # point at while it was running; now that it has resolved,
        # the slots are dead state. Clear them so the grimoire stops
        # rendering the reminder tokens — display always matches
        # state, with no separate "first-night only" flag.
        engine.pool.clear_librarian_token_slots()
        for old in list(engine.effects_sourced_by(self)):
            if isinstance(old, (LibrarianOutsiderEffect, LibrarianWrongEffect)):
                engine.purge_effect(old)
