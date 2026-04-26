"""Baron.

    "There are extra Outsiders in play. [+2 Outsiders]"

The Baron's "ability" is purely a setup-distribution effect: when the
Baron is in play, two Townsfolk slots are converted into Outsider slots.

The shift is read off the class attributes ``setup_outsider_delta`` and
``setup_townsfolk_delta`` by the bag/pool builder before the game
starts; the Baron has nothing to do at night and no setup_ability of
its own. We inherit from :class:`Character` (not :class:`StubCharacter`)
so it doesn't emit a placeholder "(unimplemented) wake/sleep" prompt
for non-existent night actions — its ``night_order`` is 0 on every
night, which is enough on its own, but the explicit ``Character`` base
makes the "no nightly behaviour" intent obvious to readers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType

if TYPE_CHECKING:
    from engine.engine import Engine  # noqa: F401

class Baron(Character):
    name = "Baron"
    char_type = CharType.MINION
    ability_text = "There are extra Outsiders in play. [+2 Outsiders]"
    first_night_order = 0
    other_night_order = 0
    setup_outsider_delta = 2
    setup_townsfolk_delta = -2
    reminder_tokens: list = []

    # No nightly ability and no setup_ability — the +2 outsiders / -2
    # townsfolk redistribution is applied at bag-build time by the
    # storyteller UI / pool helper, both of which read the deltas above
    # off the class.
