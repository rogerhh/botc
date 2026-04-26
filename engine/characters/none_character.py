"""None character — a no-op placeholder.

A :class:`NoneCharacter` is an inert :class:`Character` that does
nothing: no nightly action, no first-night ability, no setup
ability, no reminder tokens, no setup deltas. It is *not* part of
any script's bag — it is never seated and never appears in setup
counts. It exists purely as a typed Character instance you can hold
in a slot when a setup-time pick needs to resolve to *something*
but the pick has no meaningful real-world effect.

The canonical use-case is the Drunk impersonating a Townsfolk that
itself has a setup-time character pick (e.g. the Fortune Teller's
red herring): the impersonated role still walks through its setup
pipeline so its data structures stay consistent, but its picked
"dummy" role is None — the storyteller does not need to be asked,
and no physical reminder token is placed. Per the project rules in
``CLAUDE.md`` and engine README, asking the storyteller about an
unused setup pick is exactly the kind of busy-work we avoid.

Other roles that need a dummy stand-in for a Character slot should
likewise use :class:`NoneCharacter` rather than picking a random
real role.
"""

from __future__ import annotations

from engine.character import Character
from engine.enums import CharType


class NoneCharacter(Character):
    """No-op placeholder Character.

    Never seated, never acts, never reminders. Identifiable by
    :attr:`name` ``"None"`` so callers can detect "this slot was
    filled with a placeholder".
    """

    name = "None"
    # ``char_type`` is arbitrary because the None character is never
    # seated and so its type is never consulted by setup-count logic
    # or alignment defaults. Townsfolk is chosen as the most innocuous
    # placeholder.
    char_type = CharType.TOWNSFOLK
    ability_text = ""
    first_night_order = 0
    other_night_order = 0
    once_per_game = False
    setup_outsider_delta = 0
    setup_townsfolk_delta = 0
    reminder_tokens: list = []
