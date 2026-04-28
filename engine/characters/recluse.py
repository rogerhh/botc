"""Recluse.

    "You might register as evil and as a Minion or Demon, even if dead."

The Recluse is a misregistration character. Every time some other
ability *detects* alignment or character type, the Storyteller may
choose to have the Recluse register as evil (or as a particular Minion
or Demon). This is true even when the Recluse is dead.

We implement the rule via :meth:`Character.registers_as`: every
detection-style ability calls :meth:`Character.check`, which dispatches
to ``target.character.registers_as(engine, the_check)``. The Recluse's
override fires only when ``the_check`` could be passed by an evil
registration (Minion / Demon). The override's eligible list depends on
the check's ``attribute``:

  * ``"alignment"`` — eligible = ``[EvilStub, "Recluse"]``. A
    two-button choice: register as evil or as self (good).
  * ``"char_type"`` — eligible = ``[MinionStub, DemonStub, "Recluse"]``.
    A three-button choice. Type-keyed stubs keep the prompt small;
    no specific Demon role needs to be picked.
  * ``"name"`` — eligible = full Minion + Demon roster + ``"Recluse"``.

When the check only inspects good types (Washerwoman / Librarian /
Investigator's Townsfolk / Outsider checks), the override does NOT
fire — the default ``self.name == "Recluse"`` (an Outsider) is the
correct answer.

When the override does fire, the prompt's *default* is the
**misleading** registration (evil / Minion / Demon), in line with
the project's wrong-default rule: a Storyteller who simply hits Next
gets the misregistration the Recluse exists to produce, and the
good-team detector gets bad info. The Storyteller may still pick
``"Recluse"`` (the truthful self) before dispatching. Per attribute:

  * ``"alignment"`` (2 options) — flipped/wrong = ``Evil``.
  * ``"char_type"`` (3 options) — random wrong (``Minion`` or
    ``Demon``).
  * ``"name"`` (many) — random wrong (any Minion/Demon name in the
    eligible list).

So the Recluse class itself has no nightly action and no other
reactions to override. We inherit from :class:`Character` (rather than
:class:`StubCharacter`) so no placeholder prompt is emitted.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import Alignment, CharType
from engine.prompt import SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine  # noqa: F401
    from engine.check import Check


class Recluse(Character):
    name = "Recluse"
    char_type = CharType.OUTSIDER
    ability_text = (
        "You might register as evil and as a Minion or Demon, even if dead."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    # Recluse can fake Minion or Demon (evil roles) in addition to
    # being itself (an Outsider). Used by setup-time eligibility
    # helpers.
    @classmethod
    def registration_categories(cls) -> "tuple[CharType, ...]":
        return (CharType.MINION, CharType.DEMON, cls.char_type)

    # ------------------------------------------------------------------
    # Registration override.
    # ------------------------------------------------------------------

    def registers_as(self, engine: "Engine", the_check: "Check") -> str:
        """Recluse registration override.

        Fires only when the check's ``passes`` could be matched by a
        Minion / Demon registration. Otherwise ``self.name``
        ("Recluse") is returned — which is itself an Outsider, the
        correct registration for a Librarian check that picked the
        Recluse role.

        On firing the eligible list is narrowed for alignment and
        char_type checks (using the appropriate stub), and full for
        name checks. The prompt's *default* is the misleading
        registration (evil / Minion / Demon) — a Storyteller who hits
        Next produces a misregistration, in line with the project's
        wrong-default rule. The ST may pick ``"Recluse"`` to register
        as themselves (the truthful, good-team-friendly answer)
        instead.
        """
        from engine.characters.stubs import DemonStub, EvilStub, MinionStub

        if not the_check.registration_matters(
            self.registration_categories()
        ):
            # The check's outcome doesn't depend on registration
            # choice. Default Recluse registration is correct.
            return self.name

        # Build the eligible list from ALL of the Recluse's
        # registration categories — both pass and fail options must
        # be available so the ST has a real choice.
        eligible: list = []
        attribute = the_check.attribute
        if attribute == "alignment":
            eligible.append(EvilStub.name)
            eligible.append(self.name)
        elif attribute == "char_type":
            # Use the type-keyed stubs so the prompt stays small —
            # no specific Demon role needs to be named.
            eligible.append(MinionStub.name)
            eligible.append(DemonStub.name)
            eligible.append(self.name)
        else:  # attribute == "name"
            eligible.extend(
                engine.all_character_names_by_type(CharType.MINION)
            )
            eligible.extend(
                engine.all_character_names_by_type(CharType.DEMON)
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
        # that would change the result. Stay silent.
        if attribute == "name" and not any(
            p in eligible for p in the_check.passes
        ):
            return self.name

        # Default: register as evil / Minion / Demon — produces a
        # *misleading* reading for the good team. Per the project rule
        # ("the engine pre-fills a wrong default so the Storyteller can
        # simply hit Next and the player gets bad info"), the Recluse
        # leans into its misregistration ability by default; the ST may
        # still pick "Recluse" (the truthful self) before dispatching.
        #
        # Per attribute:
        #   * alignment (2 options) — flipped/wrong = Evil.
        #   * char_type (3 options) — random wrong (Minion or Demon).
        #   * name      (many)      — random wrong (any Minion/Demon
        #                             name in the eligible list).
        wrong_options = [n for n in eligible if n != self.name]
        if attribute == "alignment":
            from engine.characters.stubs import EvilStub
            default = (
                EvilStub.name if EvilStub.name in eligible
                else (wrong_options[0] if wrong_options else self.name)
            )
        else:
            default = (
                _rand.choice(wrong_options)
                if wrong_options
                else (self.name if self.name in eligible else (
                    eligible[0] if eligible else self.name
                ))
            )

        detector_name = the_check.detector_name or "?"
        detector_player_id = (
            the_check.detector_player_id
            if the_check.detector_player_id != -1 else None
        )
        meta: dict = {
            "character": detector_name,
            "step": "recluse_registers_as",
            "stage": "st_pre",
            "default": default,
            "recluse_player_id": self.player.id if self.player else None,
            "recluse_player_name": (
                self.player.name if self.player else None
            ),
            "attribute": attribute,
        }
        if the_check.extra_meta:
            meta.update(the_check.extra_meta)

        target_id = (
            detector_player_id if detector_player_id is not None
            else (self.player.id if self.player else None)
        )
        # Prompt text doesn't include the detector name — the
        # Storyteller already sees the panel header ("Show the Outsider
        # token to the Librarian", etc.) and the duplicate is noise.
        prompt = SelectCharacterPrompt(
            text="Recluse registers as",
            eligible_characters=eligible,
            target_player_id=target_id,
            meta=meta,
        )
        chosen = engine.send_prompt(prompt)
        if not isinstance(chosen, str) or chosen not in eligible:
            chosen = default
        return chosen
