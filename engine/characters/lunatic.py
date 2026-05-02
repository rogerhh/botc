"""Lunatic.

    "You think you are a Demon, but you are not. The Demon knows
     who you are & who you choose at night."

The Lunatic is a Drunk-style impersonator: their seated player
believes they are a Demon and goes through the motions of waking
each night to "kill". The actual Demon player learns who the
Lunatic is and what they chose on each night.

Implementation overview
-----------------------
This implementation is intentionally lightweight — full impersonation
of every Demon role's idiosyncratic ability flow (Shabaloth's two
picks + regurgitation, Po's charge mechanic, Pukka's poisoning,
Zombuul's no-one-died gate) is a deep refactor that requires
shadowing the in-play Demon's class on the Lunatic's seat. For the
common case the rule book asks the Storyteller to drive the fake
demon-info pass and the per-night fake wakes manually — which the
Storyteller can already do via the existing ``send_prompt`` /
``advance_to_*`` helpers — so the Lunatic class itself stays small:

  * ``perceived_character_name`` is set to the active demon's class
    name on the Lunatic's player so the Lunatic's phone displays
    that demon (matching the wiki's "swap positions" trick).
  * ``acting_perceived_character`` returns a fresh instance of the
    in-play demon's class so the engine wakes the Lunatic at that
    demon's night slot. Because the Lunatic's player has
    ``has_ability=False`` (the seated player is an Outsider, not a
    Demon — they should never produce real effects), the
    impersonated demon's nightly ability takes its drunk-branch and
    no real kill / poison / regurgitation happens. The Storyteller
    walks them through whatever fake feedback the rules require.

The Lunatic's setup picks let the Storyteller choose which Demon
the Lunatic believes they are (defaults to the Demon currently
seated on the table; falls back to "Imp" if no demon is yet seated
during setup).

Drunkenness / poisoning has no special meaning here — the Lunatic
*is always* "drunk" in effect (no real ability), so the ST can
freely lie to them about anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType, SetupMode

if TYPE_CHECKING:
    from engine.engine import Engine


class Lunatic(Character):
    name = "Lunatic"
    char_type = CharType.OUTSIDER
    ability_text = (
        "You think you are a Demon, but you are not. The Demon knows "
        "who you are & who you choose at night."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    # The Lunatic's perceived demon — populated on setup. The seated
    # player believes they are this role.
    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._perceived_demon_name: Optional[str] = None

    # ------------------------------------------------------------------
    # Setup hooks.
    # ------------------------------------------------------------------

    def on_assign_to_seat(self, engine: "Engine") -> None:
        """Default the perceived role to a demon stand-in.

        The actual demon class is resolved via :meth:`on_setup_ability`
        once the bag has stabilised; until then we just label the seat
        with a generic "Demon" placeholder so the player's phone
        doesn't reveal them as a Lunatic before setup completes.
        """
        if self.player is None:
            return
        if self.player.perceived_character_name is None:
            self.player.perceived_character_name = "Demon"

    def on_setup_ability(
        self, engine: "Engine", mode: SetupMode = SetupMode.IN_GAME
    ) -> None:
        """Pick the demon the Lunatic believes they are.

        Defaults to the demon class actually seated on the table.
        Falls back to "Imp" if no demon is seated yet (e.g. during
        SETUP_PHASE before the storyteller has finished the bag).
        """
        if self.player is None:
            return
        # Find an alive seated demon.
        demon_name: Optional[str] = None
        for p in engine.players:
            if p.character is None:
                continue
            if p.id == self.player.id:
                continue
            if p.char_type is CharType.DEMON:
                demon_name = p.character.name
                break
        if demon_name is None:
            demon_name = "Imp"

        self._perceived_demon_name = demon_name
        self.player.perceived_character_name = demon_name

        # Build a perceived instance and wire to this seat so the
        # engine wakes us at that demon's night slot.
        try:
            perceived = engine.build_character(demon_name)
        except KeyError:
            return
        perceived.player = self.player
        self.members = [perceived]
        engine.log(
            f"Lunatic {self.player.name} believes they are the {demon_name}."
        )

    def acting_perceived_character(self) -> Optional["Character"]:
        if not self.members:
            return None
        perceived = self.members[0]
        # Wire to this seat.
        if self.player is not None and perceived.player is not self.player:
            perceived.player = self.player
        return perceived
