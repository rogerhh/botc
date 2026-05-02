"""Tinker.

    "You might die at any time."

The Tinker has no nightly action and no information for the
storyteller to set. Instead the Storyteller may choose, *at any time*
(day or night), to kill the Tinker.

The engine surfaces this through :meth:`Character.daytime_ability`
combined with the ``daytime_ability_active_at_night`` flag, so the
"Use ability" button on the storyteller side panel is enabled day or
night. Hitting the button fires :meth:`daytime_ability`, which calls
:meth:`engine.engine.Engine.kill` with
``cause=DeathCause.ABILITY`` and ``source=self`` (no ``force=True``).

Per the wiki rule "The Tinker cannot die from their ability while
protected from death, as normal" — every standard pre-death canceller
(Tea Lady neighbour protection, Innkeeper SAFE marker, Sailor sober
immunity, Soldier vs Demon, Mayor redirect, Fool first-death, etc.)
fires unchanged because we go through the regular ``Engine.kill``
path without ``force``.

Per the wiki "How to run":

    "If this is during the day, immediately declare that the Tinker
    has died. If this is during the night, mark the Tinker with the
    DEAD reminder and wait until dawn to declare which players died
    during the night."

The day vs night announcement is handled by the engine itself. During
night, ``Engine.kill`` appends to ``_pending_night_deaths`` so the
death rolls into the dawn announcement; during day the death is
declared immediately via the standard ``DEATH`` event / log path.

The Tinker ability is **not** once-per-game — the Storyteller may
have multiple opportunities to fire it, but at most one will land
(after which the Tinker is dead and the button becomes inert).

Drunkenness / poisoning has no effect — the Tinker has no
dependency on ``has_ability``.

Implementation note: ``daytime_ability_active_at_night = True``
forces the engine to run this ability synchronously (no worker
thread), so it must not call ``send_prompt``. The whole ability is a
single ``engine.kill`` call, which is safe to invoke inline alongside
a live night-order thread.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType, DeathCause

if TYPE_CHECKING:
    from engine.engine import Engine


class Tinker(Character):
    name = "Tinker"
    char_type = CharType.OUTSIDER
    ability_text = "You might die at any time."
    first_night_order = 0
    other_night_order = 0
    # The Storyteller may fire the ability at any time, day or night.
    daytime_ability_active_at_night = True
    reminder_tokens: list = []

    def daytime_ability(self, engine: "Engine") -> None:
        """The Storyteller decides the Tinker dies.

        Goes through the standard ``Engine.kill`` path so all normal
        protections (Tea Lady neighbour, Innkeeper SAFE, Sailor sober
        immunity, Mayor redirect, Fool first-death, etc.) still fire
        — "the Tinker cannot die from their ability while protected
        from death, as normal".
        """
        if self.player is None or self.player.dead:
            return
        engine.kill(
            self.player.id,
            cause=DeathCause.ABILITY,
            source=self,
        )
