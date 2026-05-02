"""Grandmother.

    "You start knowing a good player & their character. If the Demon
     kills them, you die too."

The Grandmother learns who their grandchild is on the first night —
a single good player and that player's character. If the demon kills
that grandchild on a later night, the Grandmother dies the same
night.

Implementation
--------------
* The grandchild is picked by the Storyteller during **game setup**
  via the GRANDCHILD reminder token (drag onto any good — Townsfolk
  or Outsider — chair other than the Grandmother's own). The pool
  stores the grandchild as a *role name* in ``pool.grandmother_grandchild``,
  matching the convention used by the FT red herring / WW seen tokens
  (each role appears at most once in a game, so a role name uniquely
  identifies the chair).
* The Grandmother's :meth:`absorb_setup_data` and
  :meth:`on_setup_ability` (``SETUP_PHASE`` mode) resolve the pool's
  role to the seated player's id and store it on
  ``self._grandchild_id`` so the death-reaction wiring keeps working.
* The first-night ``ability`` no longer prompts the ST for a player —
  it just shows the Grandmother who their grandchild is. A
  drunk/poisoned Grandmother is shown a random *wrong* character for
  the displayed role (per the project's drunk/poisoned info rule).
* The "you die too" effect is reactive on ``DEATH`` of the
  grandchild. Gated on the Grandmother's current ``has_ability``
  (alive + sober + healthy).

Reminder tokens
---------------
``GRANDCHILD`` is a setup-phase token whose pool slot
(``pool.grandmother_grandchild``) survives into play, so the
storyteller's grimoire keeps showing it on the grandchild's chair
for as long as the Grandmother is in the game.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import SetupEffect
from engine.enums import CharType, DeathCause, SetupMode
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectCharacterPrompt,
    SelectPlayerPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class GrandmotherGrandchildEffect(SetupEffect):
    """Setup-time marker on the Grandmother's grandchild seat.

    Persists from setup through the Grandmother's death (or
    character change). When the grandchild dies at night via the
    Demon, the Grandmother dies of grief — handled in
    :meth:`on_target_death`."""

    kind = "grandmother_grandchild"
    contributes_to_state = None
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = True   # poisoned Grandma doesn't grieve

    @classmethod
    def can_target(cls, engine: "Engine", chair_id: int) -> bool:
        try:
            p = engine.get_player(chair_id)
        except KeyError:
            return False
        if p.character is None:
            return False
        # Must be a good role (TF or Outsider). The Grandmother
        # cannot be her own grandchild.
        if p.character.char_type not in (
            CharType.TOWNSFOLK,
            CharType.OUTSIDER,
        ):
            return False
        # The Grandmother instance is the source of the effect; we
        # can't reach it cleanly from a class-method without the
        # effect instance. This case is rare — defer the self-check
        # to the source character's logic.
        return True

    def on_target_death(
        self, engine: "Engine", dead_target_id: int
    ) -> None:
        # Per wiki: grandchild dies AT NIGHT → Grandmother dies of
        # grief. Day execution does NOT trigger.
        if not engine.phase.is_night:
            return
        if self.source.player is None or self.source.player.dead:
            return
        # Only fires on demon kills. The DEATH event has already
        # fired by the time on_target_death is called; we read the
        # cause off the dying player's death_cause.
        try:
            tgt = engine.get_player(dead_target_id)
        except KeyError:
            return
        if tgt.death_cause is not DeathCause.DEMON_KILL:
            return
        engine.log_reaction(
            "Grandmother",
            f"{self.source.player.name} dies — grandchild was killed by the Demon.",
            target=self.source.player,
            trigger="grandchild_demon_death",
        )
        engine.kill(
            self.source.player.id,
            DeathCause.ABILITY,
            source=self.source,
        )


class Grandmother(Character):
    name = "Grandmother"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "You start knowing a good player & their character. If the Demon "
        "kills them, you die too."
    )
    first_night_order = 36
    other_night_order = 0
    reminder_tokens: list = [
        {"name": "GRANDCHILD", "icon": "grandmother_grandchild.png"},
    ]

    setup_picks = (
        {
            "kind":         "grandmother_grandchild",
            "slot":         "grandchild",
            "getter":       "grandmother_grandchild",
            "setter":       "set_grandmother_grandchild",
            "autofill":     "_autofill_grandmother_grandchild",
            "mutex_with":   (),
            "check":        ("char_type", "GOOD"),  # TF or Outsider
            "forbid_self":  True,
        },
    )

    @classmethod
    def accepts_tokens(cls) -> "frozenset[str]":
        # The Grandmother herself can't host her own GRANDCHILD token.
        return super().accepts_tokens() - {"grandmother_grandchild"}

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # The id of the grandchild seat. Resolved during setup from the
        # pool's ``grandmother_grandchild`` slot (a role name) — the
        # seated player whose character matches that role is the
        # grandchild.
        self._grandchild_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Setup.
    # ------------------------------------------------------------------

    def _resolve_grandchild_player(
        self, engine: "Engine", role: str
    ) -> Optional["Player"]:
        """Find the seated player whose character matches ``role``.

        Skips the Grandmother's own seat — by construction the pool's
        grandchild slot is forbidden from naming the Grandmother
        herself, but be defensive in case of stale data.
        """
        if self.player is None:
            return None
        for p in engine.players:
            if p.id == self.player.id:
                continue
            if p.character is not None and p.character.name == role:
                return p
        return None

    def absorb_setup_data(self, engine: "Engine", data: dict) -> None:
        """Pre-set the grandchild from the UI's setup data."""
        super().absorb_setup_data(engine, data)
        if self.player is None:
            return
        # Prefer the pool slot (post-set by the generic absorption in
        # super(); fall back to ``data`` for compat).
        role = engine.pool.grandmother_grandchild() or data.get(
            "grandmother_grandchild"
        )
        if not role:
            return
        target = self._resolve_grandchild_player(engine, role)
        if target is not None:
            self._grandchild_id = target.id
            engine.log(
                f"{target.name} ({role}) is the grandchild for "
                f"{self.player.name} (Grandmother)."
            )
            self._refresh_registry_effect(engine)

    def _refresh_registry_effect(self, engine: "Engine") -> None:
        """Synchronise the registry's :class:`GrandmotherGrandchildEffect`
        with this character's current ``_grandchild_id``.

        Bridges the legacy pool-based grandchild storage with the new
        registry-effect model: every time the pool slot resolves
        (absorb_setup_data, on_setup_ability SETUP_PHASE), call this
        to purge any stale effect and emit a fresh one targeting the
        current grandchild seat. Idempotent: repeated calls with the
        same target are no-ops once the effect exists.
        """
        if self.player is None:
            return
        # Purge any existing emission.
        existing = [
            e for e in engine.effects_sourced_by(self)
            if isinstance(e, GrandmotherGrandchildEffect)
        ]
        # If we already have the right target, leave it.
        if (
            len(existing) == 1
            and self._grandchild_id is not None
            and existing[0].targets == [self._grandchild_id]
        ):
            return
        for old in existing:
            engine.purge_effect(old)
        if self._grandchild_id is None:
            return
        # Don't emit if the grandchild isn't valid (e.g. resolved to
        # the Grandmother's own seat — defensive).
        if self._grandchild_id == self.player.id:
            return
        engine.add_effect(GrandmotherGrandchildEffect(
            source=self, targets=[self._grandchild_id],
        ))

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        """Mode-aware on-setup ability.

        ``SETUP_PHASE``: silently absorb the pool's grandchild slot
        into ``self._grandchild_id``. Pure read-and-resolve; no
        Storyteller prompts.

        ``IN_GAME``: delegate to legacy ``setup_ability`` (no-op for
        the Grandmother — the first-night ability handles the rest).
        """
        if self.player is None:
            return
        if mode is SetupMode.SETUP_PHASE:
            role = engine.pool.grandmother_grandchild()
            if role:
                target = self._resolve_grandchild_player(engine, role)
                if target is not None:
                    self._grandchild_id = target.id
                    engine.log(
                        f"{target.name} ({role}) absorbed as grandchild "
                        f"for {self.player.name} (Grandmother)."
                    )
                    self._refresh_registry_effect(engine)
            return
        self.setup_ability(engine)

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        if self._grandchild_id is None:
            return {}
        try:
            tgt = engine.get_player(self._grandchild_id)
        except KeyError:
            return {}
        if tgt.character is None:
            return {}
        return {"grandmother_grandchild": [tgt.id]}

    def reaction(self, event: Event, engine: "Engine") -> None:
        # The grief death is triggered by
        # :meth:`GrandmotherGrandchildEffect.on_target_death`, called
        # by the engine on active effects only. We retain a small
        # reaction here only to log the SUPPRESSED case (drunk/
        # poisoned Grandmother → effect inactive → no grief, but
        # storytellers want to see the interaction in the console).
        if (
            event.type is EventType.DEATH
            and self.player is not None
            and not self.player.dead
            and self._grandchild_id is not None
            and event.targets
            and event.targets[0].id == self._grandchild_id
            and event.data.get("cause") is DeathCause.DEMON_KILL
            and not self.player.has_ability
        ):
            state = self.player.drunk_poison_label() or "no ability"
            grandchild = event.targets[0]
            engine.log_reaction(
                "Grandmother",
                (
                    f"{self.player.name} ({state}) does NOT die — "
                    f"grandchild {grandchild.name} was killed by the "
                    f"Demon, but ability has no effect."
                ),
                target=self.player,
                trigger="grandchild_demon_death",
                suppressed=True,
                drunk_poison_state=state,
                grandchild_player_id=grandchild.id,
                grandchild_player_name=grandchild.name,
            )
        return super().reaction(event, engine)

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number != 1 or self.player is None or self.player.dead:
            return

        # Re-resolve the grandchild from the pool slot in case the
        # Storyteller dragged the GRANDCHILD token to a new chair after
        # the initial absorb (the token-drag handler retriggers the
        # SETUP_PHASE absorption, but be defensive).
        role = engine.pool.grandmother_grandchild()
        if role:
            target = self._resolve_grandchild_player(engine, role)
            if target is not None:
                self._grandchild_id = target.id

        self._show_grandchild_info(engine)

    # ------------------------------------------------------------------
    # Info show (shared between first-night ability and on-revive wake).
    # ------------------------------------------------------------------

    def _show_grandchild_info(self, engine: "Engine") -> None:
        """Wake the Grandmother and show her the grandchild + character.

        Used by the first-night ``ability`` and by the on-revive
        immediate-wake flow. Both paths share the same wake / select /
        information dispatch and the same drunk/poisoned wrong-character
        pre-pick.
        """
        if self.player is None or self.player.dead:
            return
        if self._grandchild_id is None:
            engine.log(
                f"Grandmother {self.player.name}: no grandchild set — "
                f"skipping info show."
            )
            return
        try:
            target = engine.get_player(self._grandchild_id)
        except KeyError:
            return
        if target.character is None:
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )

        # Show the Grandmother the grandchild's character. Drunk/poisoned
        # Grandmother gets a random wrong character pre-picked.
        true_char = target.character.name
        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned
        if is_drunk_or_poisoned:
            all_chars = engine.all_character_names()
            wrong_options = [c for c in all_chars if c != true_char]
            default_wrong = (
                _rand.choice(wrong_options) if wrong_options else true_char
            )
            char_prompt = SelectCharacterPrompt(
                text="Character to show",
                eligible_characters=all_chars,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_shown_character",
                    "stage": "st_pre",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": default_wrong,
                    "correct": true_char,
                    "grandchild_player_id": target.id,
                },
            )
            shown = engine.send_prompt(char_prompt)
            if not isinstance(shown, str) or not shown:
                shown = default_wrong
        else:
            shown = true_char

        info_text = f"Your grandchild is {target.name}, the {shown}."
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[target.id],
                highlight_characters=[shown],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    "render": {
                        "tokens": [{
                            "label": shown.upper(),
                            "body": target.name,
                        }],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[target],
                data={"info": info_text, "shown_character": shown},
            )
        )
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )

    # ------------------------------------------------------------------
    # Revive — re-pick grandchild and immediately wake the Grandmother.
    # ------------------------------------------------------------------

    def on_revive(self, engine: "Engine") -> None:
        """Re-arm and re-show the Grandmother's start-knowing info.

        On revive the Storyteller is asked to confirm/replace the
        grandchild (default-selected to the current grandchild, eligible
        seats are any seated Townsfolk/Outsider other than the
        Grandmother — dead or alive both count). The Grandmother is
        then immediately woken and shown the grandchild + character,
        with the usual drunk/poisoned wrong-character pre-pick.

        Day revives are not currently supported — the immediate-wake
        flow assumes a triggering night ability is holding the night
        thread. A storyteller-driven day revive raises
        :class:`NotImplementedError`; ``Engine.revive`` swallows the
        exception and logs it, and the existing ``_first_night_pending``
        re-fire path will still run the info show on the next night.
        """
        super().on_revive(engine)
        if self.player is None:
            return

        if not engine.phase.is_night:
            raise NotImplementedError(
                "Grandmother revive during day phase is not implemented."
            )

        # Re-pick the grandchild (ST may keep the original by hitting Next).
        self._prompt_revive_grandchild(engine)

        # Immediately wake the Grandmother and re-show the info. After
        # this, the slot has been spent — clear the first-night pending
        # flag so the night loop doesn't re-fire the ability later.
        self._show_grandchild_info(engine)
        self.mark_first_night_fired()

    def _prompt_revive_grandchild(self, engine: "Engine") -> None:
        """Ask the Storyteller to (re-)pick the grandchild on revive.

        The previous grandchild is pre-selected as the default — the
        Storyteller can keep them by hitting Next or pick a new seat.
        Eligible seats are any seated Townsfolk/Outsider other than
        the Grandmother herself (dead or alive both count, per the
        project rule).
        """
        if self.player is None:
            return
        eligible: list[int] = []
        for p in engine.players:
            if p.id == self.player.id:
                continue
            if p.character is None:
                continue
            if p.character.char_type not in (
                CharType.TOWNSFOLK,
                CharType.OUTSIDER,
            ):
                continue
            eligible.append(p.id)
        if not eligible:
            engine.log(
                f"Grandmother {self.player.name}: revive — no eligible "
                f"grandchild seats; keeping previous grandchild "
                f"({self._grandchild_id})."
            )
            return

        if self._grandchild_id in eligible:
            default_id = self._grandchild_id
        else:
            default_id = eligible[0]

        sel = SelectPlayerPrompt(
            text="Pick the Grandmother's grandchild",
            eligible_player_ids=eligible,
            count=1,
            allow_self=False,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_grandchild_on_revive",
                "stage": "st_pre",
                "default": default_id,
                "previous_grandchild_id": self._grandchild_id,
            },
        )
        chosen = engine.send_prompt(sel)
        if isinstance(chosen, list):
            chosen = chosen[0] if chosen else None
        if chosen is None:
            chosen = default_id
        try:
            target = engine.get_player(int(chosen))
        except (KeyError, ValueError, TypeError):
            engine.log(
                f"Grandmother revive: invalid grandchild pick {chosen!r}; "
                f"keeping previous grandchild."
            )
            return
        if target.character is None:
            engine.log(
                f"Grandmother revive: chosen seat {target.name} has no "
                f"character; keeping previous grandchild."
            )
            return

        role = target.character.name
        try:
            engine.pool.set_grandmother_grandchild(role)
        except ValueError as exc:
            engine.log(
                f"Grandmother revive re-pick rejected: {exc}. Keeping "
                f"previous grandchild."
            )
            return
        self._grandchild_id = target.id
        # Refresh the registry effect to reflect the new grandchild.
        # Note: the effect was purged on Grandmother's death (per
        # ``purge_on_source_death=True``), so on revive we always
        # need to re-emit.
        self._refresh_registry_effect(engine)
        engine.log(
            f"Grandmother {self.player.name}: revived — grandchild is "
            f"now {target.name} ({role})."
        )
