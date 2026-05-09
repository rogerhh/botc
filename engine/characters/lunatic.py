"""Lunatic.

    "You think you are a Demon, but you are not. The Demon knows
     who you are & who you choose at night."

The Lunatic is a perceived-Demon impersonator: their seated player
believes they are the Demon and goes through every motion of being
that Demon — first-night Demon Info, the per-night attack pick,
Po's charging clock, Zombuul's "did anyone die today?" wake gate,
Shabaloth's two-target select, Pukka's poison cycle. Each of those
flows runs by *reusing the in-play Demon's class verbatim* via
:meth:`acting_perceived_character`; the Lunatic class itself owns
no demon logic of its own.

The "no real effect" property comes from the
:meth:`Character.is_authentic` gate, not from drunkenness. The
Lunatic player is a sober Outsider and ``has_ability`` stays True;
each demon's resolution path checks ``can_produce_real_effect``
(``is_authentic and has_ability``) before firing kills, poisons, or
regurgitations. A perceived Demon running on the Lunatic's chair
fails the authenticity check (``self.player.character`` is the
Lunatic, not the perceived role), so prompts and picks proceed
faithfully but no in-game effect lands.

Implementation overview
-----------------------
* ``perceived_character_name`` is set to the in-play Demon's class
  name on the Lunatic's seat so the Lunatic's phone displays that
  Demon (matching the wiki's "swap positions" trick).
* ``acting_perceived_character`` returns a fresh instance of the
  in-play Demon's class so the engine wakes the Lunatic at that
  Demon's night-order slot. The engine's ``_run_preset_night`` lists
  the perceived shadow *before* the real Demon at each shared step,
  so the Lunatic acts before the real Demon — matching the wiki's
  "before the Demon wakes to attack, wake the Lunatic".
* The perceived Demon is auto-derived from whichever Demon is
  currently seated. There is no Storyteller picker; the Lunatic
  always shadows the in-play Demon. If the bag changes during
  setup, ``Engine.assign_character`` re-runs this character's
  ``on_setup_ability`` so the perceived Demon stays current.
* During the game, a Scarlet Woman promotion creates a Demon of the
  *same class* as the dying Demon (e.g. Pukka → new Pukka), so the
  Lunatic's perceived Demon doesn't need to change. There's no
  in-game branch to handle here; the perceived Demon class stays
  whatever it was when setup ended.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType, SetupMode

if TYPE_CHECKING:
    from engine.engine import Engine


# Fallback demon when no Demon is seated yet (e.g. during very early
# setup, before the bag has been populated). Falls in line with the
# user's "all scripts should have a demon, falling back to Imp is
# fine" guidance.
LUNATIC_DEFAULT_DEMON = "Imp"


def _find_in_play_demon_name(engine: "Engine", exclude_player_id: int) -> Optional[str]:
    """Return the class name of the alive seated Demon (if any).

    ``exclude_player_id`` is the Lunatic's own seat id so a future
    pathological setup that somehow seated the Lunatic *as* a Demon
    can't loop back on itself. The first alive Demon seat wins;
    duplicates (post-Scarlet-Woman dead-and-promoted pair) sort
    naturally because we prefer alive matches first.
    """
    alive_demon: Optional[str] = None
    dead_demon: Optional[str] = None
    for p in engine.players:
        if p.character is None:
            continue
        if p.id == exclude_player_id:
            continue
        if p.char_type is not CharType.DEMON:
            continue
        if p.alive:
            return p.character.name
        if dead_demon is None:
            dead_demon = p.character.name
    return alive_demon or dead_demon


class Lunatic(Character):
    name = "Lunatic"
    char_type = CharType.OUTSIDER
    ability_text = (
        "You think you are a Demon, but you are not. The Demon knows "
        "who you are & who you choose at night."
    )
    # Night-order slots are 0 because the Lunatic itself doesn't act —
    # the perceived Demon's slots drive the wakes via
    # ``acting_perceived_character`` / the engine's
    # ``_build_action_order`` and ``_run_preset_night`` walks.
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = [
        {"name": "CHOSEN", "icon": "lunatic_chosen.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # The in-play Demon's class name the Lunatic is currently
        # shadowing. Auto-derived; mirrors ``player.perceived_character_name``.
        self._perceived_demon_name: Optional[str] = None

    # ------------------------------------------------------------------
    # Setup hooks.
    # ------------------------------------------------------------------

    def on_assign_to_seat(self, engine: "Engine") -> None:
        """Stamp a placeholder ``perceived_character_name`` on assign.

        The actual demon class is resolved by :meth:`on_setup_ability`
        once the bag has stabilised; until then we label the seat
        with a generic ``"Demon"`` so the Lunatic's phone doesn't
        briefly flash ``"Lunatic"`` between assign and the first
        setup pass.
        """
        if self.player is None:
            return
        if self.player.perceived_character_name is None:
            self.player.perceived_character_name = "Demon"

    def on_setup_ability(
        self, engine: "Engine", mode: SetupMode = SetupMode.IN_GAME
    ) -> None:
        """Auto-derive the perceived Demon from the in-play Demon.

        Called by :meth:`Engine.assign_character` whenever a seat
        changes — including when *another* seat's character changes,
        thanks to the explicit ``_retrigger_setup_for_role("Lunatic")``
        the engine fires after every assignment. So the Lunatic's
        perceived Demon stays current during setup as the ST shuffles
        the bag.

        The mode argument is ignored: there's no Storyteller prompt
        either way, so SETUP_PHASE and IN_GAME do the same thing here.
        """
        if self.player is None:
            return

        target_demon = _find_in_play_demon_name(engine, self.player.id)
        if target_demon is None:
            target_demon = LUNATIC_DEFAULT_DEMON

        # Idempotency: if we already shadow this exact demon class,
        # don't rebuild ``self.members[0]`` — that would discard any
        # in-flight per-instance state (e.g. Po's ``_charged`` clock,
        # Pukka's last-poison record). Per the user, the perceived
        # demon class doesn't change mid-game (SW becomes the same
        # type), so this protects the Lunatic's running state from
        # spurious setup re-triggers.
        if (
            self._perceived_demon_name == target_demon
            and self.members
            and self.members[0].name == target_demon
        ):
            # Re-sync the perceived role's player wiring defensively;
            # everything else is already correct.
            self.members[0].player = self.player
            self.player.perceived_character_name = target_demon
            return

        self._perceived_demon_name = target_demon
        self.player.perceived_character_name = target_demon

        # Rebuild the perceived demon instance.
        try:
            perceived = engine.build_character(target_demon)
        except KeyError:
            engine.log(
                f"Lunatic {self.player.name}: cannot build {target_demon!r} "
                f"(unknown character)."
            )
            self.members = []
            return
        perceived.player = self.player
        self.members = [perceived]
        engine.log(
            f"Lunatic {self.player.name} believes they are the {target_demon}."
        )

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Surface the ``lunatic_chosen`` reminder for tonight's picks.

        Reads ``engine._lunatic_picks_tonight`` directly. For a sober
        Lunatic this list reflects the actual picks; for a droisoned
        Lunatic the engine's droison interlude has already replaced
        the list with the ST-selected wrong players, so the tokens
        land on the *wrong* players (per the user's spec).
        """
        if self.player is None:
            return {}
        picks = list(getattr(engine, "_lunatic_picks_tonight", []) or [])
        if not picks:
            return {}
        return {"lunatic_chosen": list(picks)}

    def acting_perceived_character(self) -> Optional["Character"]:
        """Return the perceived-Demon shadow wired to this seat.

        The engine consumes this in ``_run_preset_night`` /
        ``_build_action_order`` to schedule the Lunatic's wake at
        the perceived Demon's night-order slot, and in the chair
        snapshot / token-rendering paths so any per-Demon state that
        renders on the chair (Po's 3-ATTACKS reminder is gated to
        authentic Po only; other reminders inherit normally) is
        sourced consistently.
        """
        if not self.members:
            return None
        perceived = self.members[0]
        if self.player is not None and perceived.player is not self.player:
            perceived.player = self.player
        return perceived
