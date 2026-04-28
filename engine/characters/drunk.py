"""Drunk.

    "You do not know you are the Drunk. You think you are a Townsfolk
     character, but you are not."

Setup-time choice: the Storyteller picks a Townsfolk character that the
Drunk will *believe* they are. The Drunk's nightly ability is, on
purpose, nothing — but the storyteller wakes them as the chosen
Townsfolk and feeds them whatever fake info is plausible. The Drunk's
``perceived_character_name`` is what their phone displays.

This character demonstrates the generic
:meth:`Character.setup_ability` hook: any role whose setup needs the
storyteller to pick something (a Character, a Player, etc.) overrides
that method instead of bolting per-character logic into the engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType, SetupMode

if TYPE_CHECKING:
    from engine.engine import Engine

class Drunk(Character):
    name = "Drunk"
    char_type = CharType.OUTSIDER
    ability_text = (
        "You do not know you are the Drunk. You think you are a "
        "Townsfolk character, but you are not."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = [
        {"name": 'IS THE DRUNK', "icon": 'drunk_is_the_drunk.png'},
    ]

    # ------------------------------------------------------------------
    # Seat-assignment hook.
    # ------------------------------------------------------------------

    def on_assign_to_seat(self, engine: "Engine") -> None:
        """Mark the Drunk seat drunk; seed the perceived-TF placeholder.

        Replaces the engine's old ``if character_name == "Drunk":``
        branch in :meth:`Engine.assign_character`. The engine no longer
        has Drunk-specific code; the Drunk class owns this rule.
        """
        if self.player is None:
            return
        self.player.set_drunk(True)
        if self.player.perceived_character_name is None:
            self.player.perceived_character_name = "Townsfolk"

    # ------------------------------------------------------------------
    # Convenience accessor.
    # ------------------------------------------------------------------

    @property
    def perceived_character(self) -> Optional["Character"]:
        """The Townsfolk Character instance the Drunk thinks they are.

        Set by :meth:`setup_ability`. ``None`` until the storyteller
        has picked the fake. Carries the impersonated role's metadata
        (night order, ability text, …) which is useful when the
        storyteller is walking the Drunk through a fake wakeup.
        """
        return self.members[0] if self.members else None

    # ------------------------------------------------------------------
    # Acting-as override.
    # ------------------------------------------------------------------

    def acting_perceived_character(self) -> Optional["Character"]:
        """Return the impersonated Townsfolk wired to the Drunk's seat.

        The engine walks every seated player through this hook each
        night and during setup so the impersonated Townsfolk can run
        its setup_ability / nightly ability / reactions on the Drunk's
        seated player. The Drunk's player has ``has_ability=False``,
        which steers each impersonated ability into its
        drunk/poisoned branch (wrong-info pre-fill, no real
        protection, etc.) per :doc:`CLAUDE.md`.
        """
        if not self.members:
            return None
        perceived = self.members[0]
        # Wire to the same seated player so the impersonated role's
        # ``self.player.id`` / ``self.player.name`` / ``self.player.dead``
        # all resolve to the Drunk's chair.
        perceived.player = self.player
        return perceived

    # ------------------------------------------------------------------
    # Setup.
    # ------------------------------------------------------------------

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        """Mode-aware on-setup ability.

        ``SETUP_PHASE``: silently absorb pool state. If the storyteller
        already set ``engine.pool.drunk_fake()`` (or the IS-THE-DRUNK
        token has been moved onto a chair), instantiate that Townsfolk
        on ``self.members`` and write the Drunk's
        ``perceived_character_name``. If nothing is set yet, leave the
        slot empty — a later token-drag or pool change will re-trigger.

        ``IN_GAME``: delegate to the legacy :meth:`setup_ability`,
        which prompts the Storyteller for a Townsfolk to fake.
        """
        if self.player is None:
            return
        # Mark the Drunk as drunk in either mode.
        self.player.set_drunk(True)
        if mode is SetupMode.SETUP_PHASE:
            # Already populated (e.g. by a previous token-drag) —
            # ensure perceived_character_name is in sync, then return.
            if self.members:
                existing = self.members[0]
                if self.player.perceived_character_name != existing.name:
                    self.player.perceived_character_name = existing.name
                return
            # Otherwise read engine.pool.drunk_fake; if set, build the
            # impersonated TF onto self.members.
            fake_name = engine.pool.drunk_fake()
            if fake_name:
                try:
                    fake = engine.build_character(fake_name)
                except KeyError:
                    return
                self.members.append(fake)
                self.player.perceived_character_name = fake.name
                engine.log(
                    f"{self.player.name} (Drunk) absorbs pool pick: "
                    f"believes they are the {fake.name}."
                )
            return
        # IN_GAME: prompt the storyteller (legacy path).
        self.setup_ability(engine)

    def setup_ability(self, engine: "Engine") -> None:
        """Storyteller picks the Townsfolk the Drunk thinks they are.

        The eligible list is "every Townsfolk that isn't already in
        play" by default — that's the canonical fake (a Townsfolk
        token swapped *into* the bag in place of the Drunk's own).
        Falls back to the full Townsfolk roster if every Townsfolk is
        already in the bag (very large setups), so the storyteller
        never sees an empty picker.

        The chosen role is instantiated as a real :class:`Character`
        and stored on ``self.members`` via the generic
        :meth:`Character.pick_character_at_setup` helper. The Drunk's
        ``player.perceived_character_name`` is updated to match, so
        the player's phone displays the fake role.

        If the engine's :meth:`Engine.apply_setup_data` already
        populated ``self.members`` (i.e. the storyteller picked the
        fake in the UI before clicking Start Game), this method is a
        no-op — no prompt is emitted and the Drunk just goes on with
        the pre-set role.
        """
        if self.player is None:
            return

        # Mark the Drunk as drunk. ``has_ability`` is False from the
        # very first night: the engine already does this in
        # ``assign_character`` for "Drunk", but doing it again here
        # makes the Drunk behave correctly even if the engine path
        # changes later.
        self.player.set_drunk(True)

        # Already pre-populated from the UI's setup data? Nothing to
        # ask the storyteller — fast-forward.
        if self.members:
            existing = self.members[0]
            if self.player.perceived_character_name != existing.name:
                self.player.perceived_character_name = existing.name
            engine.log(
                f"{self.player.name} (Drunk) believes they are the "
                f"{existing.name} (already set; skipping prompt)."
            )
            return

        in_play = set(engine.in_play_character_names_by_type(CharType.TOWNSFOLK))
        all_townsfolk = engine.all_character_names_by_type(CharType.TOWNSFOLK)
        candidates = [n for n in all_townsfolk if n not in in_play]
        eligible = candidates or list(all_townsfolk)

        # If the player view already has a meaningful perceived
        # character (e.g. propagated from the storyteller's pre-game
        # bag UI) advertise it as the default; the prompt's metadata
        # carries it so the UI can pre-select it.
        existing = self.player.perceived_character_name
        default = (
            existing
            if existing and existing in eligible
            else None
        )

        chosen = self.pick_character_at_setup(
            engine,
            eligible_characters=eligible,
            text="Townsfolk the Drunk thinks they are",
            meta={"step": "setup_select_fake", "default": default},
        )
        if chosen is None:
            # Defensive fallback: if the UI returned something unusable,
            # at least keep the Drunk's perceived role consistent.
            self.player.perceived_character_name = (
                default or (eligible[0] if eligible else "Townsfolk")
            )
            return

        self.player.perceived_character_name = chosen.name
        engine.log(
            f"{self.player.name} (Drunk) believes they are the "
            f"{chosen.name} (carried as a member)."
        )
