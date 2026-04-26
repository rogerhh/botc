"""Soldier.

    "You are safe from the Demon."

Passive ability — the Demon's nightly kill on a sober & healthy Soldier
fails. The actual protection check lives on the engine's kill helper
(:meth:`engine.engine.Engine.kill`), which inspects
``player.character.name == "Soldier"`` and ``player.has_ability`` before
applying the death. The Soldier itself has no nightly action and emits
no prompts; we inherit from :class:`Character` (not
:class:`StubCharacter`) so it doesn't produce a placeholder
"(unimplemented) wake/sleep" prompt.

Drunkenness / poisoning: a drunk or poisoned Soldier loses protection.
This too is enforced by ``Engine.kill`` via the ``has_ability`` check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType

if TYPE_CHECKING:
    from engine.engine import Engine  # noqa: F401

class Soldier(Character):
    name = "Soldier"
    char_type = CharType.TOWNSFOLK
    ability_text = "You are safe from the Demon."
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    # Passive — handled by Engine.kill().
