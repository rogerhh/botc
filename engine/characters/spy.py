"""Spy.

    "Each night, you see the Grimoire. You might register as good and
     as a Townsfolk or Outsider, even if dead."

Two pieces:

  * **Grimoire reveal.** The Spy is woken every night and shown the
    full grimoire. We model this as a ``ShowInformation``-style prompt
    whose payload is the engine's storyteller-view snapshot. The Spy's
    phone renders the grimoire so they can study it.

  * **Misregistration.** The Spy may register as good and as any
    Townsfolk or Outsider, even when dead. This is implemented as an
    override of :meth:`Character.registers_as` — every detection-side
    ability calls ``target.character.registers_as(...)`` at check time;
    the Spy's override fires whenever the detector's ``categories``
    list includes ``TOWNSFOLK`` or ``OUTSIDER``, prompting the
    Storyteller for the Spy's registration for that specific detection.

    Per the project's Spy.pdf wiki page and ``CLAUDE.md``: the engine
    tracks an internal "preferred good character" — a random good role
    currently NOT in play — refreshed lazily each night. This is never
    displayed as game state; it is only the *default* offered on each
    registration prompt, so a Storyteller who hits Next on every
    registration prompt produces a consistent across-the-night Spy
    character.

    Per the project rule, when ``categories`` contains only ``MINION``
    / ``DEMON`` (the Investigator / Fortune Teller checks) the Spy
    override does NOT fire — the default ``self.name == "Spy"`` is
    itself a Minion, which is the correct registration for those
    checks. The Investigator therefore always sees the Spy as the Spy,
    and the Fortune Teller's NO-on-Spy reading is preserved.

Drunkenness / poisoning: a drunk or poisoned Spy still sees the
grimoire (the rule isn't really an "ability", it's just exposure).
We pass through the prompt regardless. Misregistration is always a
Storyteller call regardless of the Spy's drunk/poisoned state — the
override fires the same way (``has_ability`` is irrelevant to
registration).
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Optional, Sequence

from engine.character import Character
from engine.enums import CharType, SetupMode
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Spy(Character):
    name = "Spy"
    char_type = CharType.MINION
    ability_text = (
        "Each night, you see the Grimoire. You might register as good "
        "and as a Townsfolk or Outsider, even if dead."
    )
    first_night_order = 40
    other_night_order = 60
    reminder_tokens: list = []

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Internally-tracked default registration for the per-ability
        # misregistration prompt. Picked once at setup (a random good
        # character currently NOT in play) and refreshed lazily if it
        # ever turns out to be in play. Never displayed to the
        # storyteller as a state — it only seeds the *default* of each
        # registration prompt so a storyteller can hit Next for a
        # consistent across-the-night Spy character. Per project rules
        # this is not part of the official game state.
        self._preferred_good_character: Optional[str] = None

    # ------------------------------------------------------------------
    # Setup.
    # ------------------------------------------------------------------

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        """Mode-aware on-setup ability.

        Both ``SETUP_PHASE`` and ``IN_GAME`` modes do the same thing:
        seed the Spy's ``_preferred_good_character`` with a random
        good role that isn't currently in play. No Storyteller prompt;
        the field is internal.
        """
        if self.player is None:
            return
        self._refresh_preferred_good_character(engine)

    def _refresh_preferred_good_character(self, engine: "Engine") -> None:
        """Re-roll the preferred good character if needed.

        Picks a random Townsfolk-or-Outsider role on the script that
        is *not* currently in play. If every good role is in play
        (degenerate large-table case), falls back to any good role on
        the script.

        Cheaply idempotent: keeps the existing pick if it is still a
        good role not in play. Otherwise re-rolls.
        """
        in_play = set(engine.in_play_character_names())
        good_names = (
            engine.all_character_names_by_type(CharType.TOWNSFOLK)
            + engine.all_character_names_by_type(CharType.OUTSIDER)
        )
        # If the existing pick is still good and still not-in-play,
        # keep it.
        if (
            self._preferred_good_character
            and self._preferred_good_character in good_names
            and self._preferred_good_character not in in_play
        ):
            return
        not_in_play = [n for n in good_names if n not in in_play]
        pool = not_in_play or list(good_names)
        if pool:
            self._preferred_good_character = _rand.choice(pool)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return
        # Refresh the default registration each night in case roles
        # have moved in / out of play.
        self._refresh_preferred_good_character(engine)
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Show the Spy the grimoire. We pack the full snapshot into the
        # prompt's meta so the Spy's phone can render it. This is the
        # *one* place where the mobile UI legitimately sees other
        # players' character tokens — see ui/README.md "Information
        # hiding rules".
        snapshot = engine.snapshot()
        engine.send_prompt(
            InformationPrompt(
                text="GRIMOIRE",
                target_player_id=self.player.id,
                shown_to_player=True,
                meta={
                    "character": self.name,
                    "step": "grimoire",
                    "stage": "info",
                    "grimoire": snapshot,
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[self.player],
                data={"info": "Spy saw the grimoire.", "grimoire": snapshot},
            )
        )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )

    # ------------------------------------------------------------------
    # Registration override.
    # ------------------------------------------------------------------

    # Spy can fake Townsfolk or Outsider (good roles) in addition to
    # being itself (a Minion). Used by setup-time eligibility helpers
    # that need to know what registrations the Spy could plausibly
    # produce without running the override at all.
    @classmethod
    def registration_categories(cls) -> "tuple[CharType, ...]":
        return (CharType.TOWNSFOLK, CharType.OUTSIDER, cls.char_type)

    def registers_as(self, engine: "Engine", the_check) -> str:
        """Spy registration override.

        Fires only when ``the_check`` could be passed by a TF/Outsider
        registration. Otherwise returns ``self.name`` (Spy is a
        Minion — the correct registration for Investigator / Fortune
        Teller checks).

        Eligible options offered to the Storyteller depend on
        ``the_check.attribute``:

          * ``"alignment"`` — eligible = ``[GoodStub, "Spy"]``. A
            two-button choice: register as good or as evil (Spy).
          * ``"char_type"`` — eligible = the relevant stub(s) for the
            check's ``passes`` plus ``"Spy"`` itself.
          * ``"name"`` — eligible = full TF + Outsider names + "Spy".

        Default is the Spy's preferred-good-character (if it's in the
        eligible list) so a Storyteller hitting Next on every check
        produces a consistent across-the-night reading.

        The check may pass an ``extra_meta["restrict_categories"]`` hint
        — a tuple of :class:`CharType` values. When present, the ST's
        eligible list is restricted to names registering as one of
        those categories (and ``"Spy"`` is suppressed). This is used by
        the Librarian / Washerwoman seen-token-on-Spy paths to force
        the ST to pick a specific Outsider / Townsfolk role for the
        Spy to register as. Without the hint the full eligible list is
        offered — the standard Investigator / Empath / Chef / etc.
        flow.
        """
        from engine.characters.stubs import GoodStub, TownsfolkStub, OutsiderStub

        # Pull the per-check restriction (if any). Used by the
        # Lib/WW seen-on-Spy paths to pin the registration to a
        # specific char_type, so the ST must pick a real Outsider /
        # Townsfolk name (not the Spy itself or a different category).
        restrict = ()
        if the_check.extra_meta:
            restrict = tuple(
                the_check.extra_meta.get("restrict_categories") or ()
            )

        if not the_check.registration_matters(
            self.registration_categories()
        ):
            # The check's outcome doesn't depend on registration
            # choice — every category the Spy could fake produces the
            # same pass/fail result. Default Spy registration is the
            # correct answer; no ST prompt needed.
            return self.name

        # Refresh the preferred default in case the in-play set has
        # shifted since setup.
        self._refresh_preferred_good_character(engine)

        # Build the eligible list from the Spy's registration
        # categories — ALL of them, not just those passing the check.
        # The ST is choosing which way to misregister; both pass and
        # fail options must be available so the choice is meaningful.
        eligible: list = []
        attribute = the_check.attribute
        if attribute == "alignment":
            # Two-option list: GoodStub ("register as good") and
            # Spy itself ("register as evil"). Each Spy registration
            # category maps to one of these via its default alignment.
            eligible.append(GoodStub.name)
            eligible.append(self.name)
        elif attribute == "char_type":
            # One stub per char_type the Spy could fake, plus Spy.
            eligible.append(TownsfolkStub.name)
            eligible.append(OutsiderStub.name)
            eligible.append(self.name)
        else:  # attribute == "name"
            if restrict:
                # Caller (Lib / WW seen-on-Spy) wants the prompt
                # narrowed to a specific category — no Spy-itself
                # option, no Townsfolk if asking for an Outsider, etc.
                for ct in restrict:
                    eligible.extend(
                        engine.all_character_names_by_type(ct)
                    )
            else:
                eligible.extend(
                    engine.all_character_names_by_type(CharType.TOWNSFOLK)
                )
                eligible.extend(
                    engine.all_character_names_by_type(CharType.OUTSIDER)
                )
                if self.name not in eligible:
                    eligible.append(self.name)

        # De-duplicate while preserving stable order.
        seen: set = set()
        deduped: list = []
        for n in eligible:
            if n not in seen:
                seen.add(n)
                deduped.append(n)
        eligible = deduped

        # Smart skip for name attribute: if no value in the check's
        # passes is in our eligible set, the override has no choice
        # that would change the result. Stay silent. (E.g. WW asking
        # "is this Mayor?" of a Recluse — Recluse can't fake TF; so
        # the equivalent intersect-check on Spy keeps the WW from
        # firing a no-op prompt for any name not on the Spy's TF/
        # Outsider list.)
        if attribute == "name" and not any(
            p in eligible for p in the_check.passes
        ):
            return self.name

        # Default: preferred good character if it's eligible (only
        # possible on attribute="name"); otherwise the first
        # non-Spy eligible (so the ST sees a "register good"
        # default), falling back to Spy.
        default: Optional[str] = None
        pref = self._preferred_good_character
        if pref and pref in eligible:
            default = pref
        if default is None:
            non_self = [n for n in eligible if n != self.name]
            if non_self:
                default = non_self[0]
            else:
                default = self.name

        detector_name = the_check.detector_name or "?"
        detector_player_id = (
            the_check.detector_player_id
            if the_check.detector_player_id != -1 else None
        )
        meta: dict = {
            "character": detector_name,
            "step": "spy_registers_as",
            "stage": "st_pre",
            "default": default,
            "spy_player_id": self.player.id if self.player else None,
            "spy_player_name": self.player.name if self.player else None,
            "attribute": attribute,
        }
        if the_check.extra_meta:
            meta.update(the_check.extra_meta)

        target_id = (
            detector_player_id if detector_player_id is not None
            else (self.player.id if self.player else None)
        )
        # Prompt text doesn't include the detector name — the Storyteller
        # already sees the panel header ("Show the Townsfolk token to
        # the Washerwoman", etc.) and the duplicate is noise.
        prompt = SelectCharacterPrompt(
            text="Spy registers as",
            eligible_characters=eligible,
            target_player_id=target_id,
            meta=meta,
        )
        chosen = engine.send_prompt(prompt)
        if not isinstance(chosen, str) or chosen not in eligible:
            chosen = default
        return chosen
