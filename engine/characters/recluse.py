"""Recluse.

    "You might register as evil and as a Minion or Demon, even if dead."

The Recluse is a misregistration character: every time some other
ability *detects* alignment or character type, the storyteller may
choose to have the Recluse register as evil (or as a particular Minion
or Demon). This is true even when the Recluse is dead.

Implementation note
-------------------
We don't try to centralise registration in the engine. Each
information-receiving character (Empath, Investigator, Fortune Teller,
Chef, Undertaker, Ravenkeeper, ...) presents the storyteller with a
*confirm-or-override* prompt that already allows them to ask
"does the Recluse register evil here?" by overriding the default
answer. That keeps each ability self-contained and matches how a
human storyteller plays the game (Recluse misregistration is a
storyteller call, not a mechanical one).

So the Recluse class itself has no nightly action and no reactions to
override. We inherit from :class:`Character` (rather than
:class:`StubCharacter`) so no placeholder prompt is emitted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType

if TYPE_CHECKING:
    from engine.engine import Engine  # noqa: F401

class Recluse(Character):
    name = "Recluse"
    char_type = CharType.OUTSIDER
    ability_text = (
        "You might register as evil and as a Minion or Demon, even if dead."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    # Passive — registration is at the storyteller's discretion and
    # surfaces through the override step on each detecting ability's
    # confirm-or-override prompt.
