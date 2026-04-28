"""Fortune Teller.

    "Each night, choose 2 players: you learn if either is a Demon. There
     is a good player that registers as a Demon to you."

Two interesting pieces here:

* a *setup-time* pick — the storyteller chooses a "red herring", a
  good player who will always register as the Demon when the FT
  checks them. This is wired through the generic
  :meth:`Character.setup_ability` hook so the engine doesn't need to
  know a thing about FT specifically.

* a *nightly* information ability that fires on the first night and
  every other night. The storyteller picks two players for the FT to
  point at; for each picked player the engine asks
  :meth:`Character.registers_as` (with categories=(DEMON,)) — YES iff
  any picked player registers as the Demon, or is the red herring.
  Spy never registers as a Demon (categories doesn't include
  TF/Outsider, so its override is silent — and the rules disallow Spy
  registering as Demon anyway). Recluse's override fires and may pick
  a Demon role.
  When the FT is drunk/poisoned the engine pre-fills the *flipped*
  (wrong) answer on a YesNoPrompt; the ST may change it before sending.
  See CLAUDE.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.enums import CharType, SetupMode
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectPlayerPrompt,
    YesNoPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

class FortuneTeller(Character):
    name = "Fortune Teller"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night, choose 2 players: you learn if either is a Demon. "
        "There is a good player that registers as a Demon to you."
    )
    first_night_order = 35
    other_night_order = 51
    reminder_tokens: list = [
        {"name": 'RED HERRING', "icon": 'fortune_teller_red_herring.png'},
    ]

    setup_picks = (
        {
            "kind":         "ft_red_herring",
            "slot":         "red_herring",
            "getter":       "ft_red_herring",
            "setter":       "set_ft_red_herring",
            "autofill":     "_autofill_ft_red_herring",
            "mutex_with":   (),
            "check":        ("char_type", "GOOD"),  # TF or Outsider
            "forbid_self":  False,
            "reset_first":  True,
        },
    )

    DETECTION_CATEGORIES = (CharType.DEMON,)

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Resolved by ``setup_ability`` from the picked red-herring
        # role. Refreshed every time the nightly ability runs so a
        # mid-game character change on the herring's seat (Scarlet
        # Woman → Imp, etc.) can be picked up if needed.
        self._red_herring: Optional["Player"] = None

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    @property
    def red_herring_role(self) -> Optional["Character"]:
        """The good Character role the storyteller picked at setup.

        ``None`` until :meth:`setup_ability` has run. The actual
        red-herring *player* is whichever seated player holds that
        role; see :attr:`_red_herring` for the resolved Player.
        """
        return self.members[0] if self.members else None

    def absorb_setup_data(self, engine: "Engine", data: dict) -> None:
        """Pre-set the red-herring role from the UI's setup data."""
        super().absorb_setup_data(engine, data)
        if self.player is None:
            return
        rh_name = data.get("ft_red_herring")
        if not rh_name:
            return
        try:
            rh_char = engine.build_character(rh_name)
        except KeyError:
            return
        self.members.clear()
        self.members.append(rh_char)
        for p in engine.players:
            if p.character is not None and p.character.name == rh_name:
                self._red_herring = p
                engine.log(
                    f"{p.name} ({rh_name}) is the red herring for "
                    f"{self.player.name} (pre-set)."
                )
                break

    def _resolve_red_herring_player(
        self, engine: "Engine"
    ) -> Optional["Player"]:
        """Find the seated player whose character matches the picked role."""
        role = self.red_herring_role
        if role is None:
            return None
        for p in engine.players:
            if p.character is not None and p.character.name == role.name:
                return p
        return None

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
        already set ``engine.pool.ft_red_herring()`` (or moved the
        RED HERRING token onto a chair), instantiate that role on
        ``self.members`` and resolve ``self._red_herring`` to the
        seated player. Leaves the slot empty if nothing is set yet.

        ``IN_GAME``: prompt the storyteller (legacy
        :meth:`setup_ability`).
        """
        if self.player is None:
            return
        if mode is SetupMode.SETUP_PHASE:
            if self.members:
                # Already populated; just refresh the resolved player.
                if self._red_herring is None:
                    self._red_herring = self._resolve_red_herring_player(engine)
                return
            rh_name = engine.pool.ft_red_herring()
            if rh_name:
                try:
                    rh = engine.build_character(rh_name)
                except KeyError:
                    return
                self.members.append(rh)
                self._red_herring = self._resolve_red_herring_player(engine)
                if self._red_herring is not None:
                    engine.log(
                        f"{self._red_herring.name} ({rh_name}) absorbed as "
                        f"red herring for {self.player.name} (Fortune Teller)."
                    )
            return
        # IN_GAME: legacy prompt path.
        self.setup_ability(engine)

    def setup_ability(self, engine: "Engine") -> None:
        """Storyteller picks a good *role* to be the red herring.

        Per the rules the red herring is a good *player*; we model it
        as a good *role* selected at setup time, so the chosen
        Character is instantiated and held on ``self.members`` via the
        generic :meth:`Character.pick_character_at_setup` helper. The
        red-herring player is then whichever seated player holds that
        role — looked up at night, so a mid-game role change (e.g.
        Scarlet Woman becoming the Imp) is reflected automatically.

        Eligible roles are every Townsfolk or Outsider currently in
        play. Demons and Minions are excluded — only a good player can
        be the red herring per the rules.

        If the engine's :meth:`Engine.apply_setup_data` already
        populated ``self.members`` (i.e. the storyteller picked the
        red herring in the UI before clicking Start Game), this method
        is a no-op — the pre-set role and resolved red-herring player
        are used as-is.
        """
        if self.player is None:
            return

        # Already pre-populated from the UI's setup data? Make sure
        # the red-herring player resolution is fresh, then skip the
        # prompt.
        if self.members:
            existing = self.members[0]
            if self._red_herring is None:
                self._red_herring = self._resolve_red_herring_player(engine)
            engine.log(
                f"Fortune Teller {self.player.name}: red herring role is "
                f"{existing.name}"
                + (f" → {self._red_herring.name}"
                   if self._red_herring is not None else "")
                + " (already set; skipping prompt)."
            )
            return

        in_play = engine.in_play_characters()
        good_types = (CharType.TOWNSFOLK, CharType.OUTSIDER)
        eligible_names = [c.name for c in in_play if c.char_type in good_types]
        # Defensive: at least one good role must exist (the FT itself
        # is one). This list is never empty in a sane game.
        if not eligible_names:
            return

        chosen = self.pick_character_at_setup(
            engine,
            eligible_characters=eligible_names,
            text="Red herring",
            meta={"step": "setup_select_red_herring"},
        )
        if chosen is None:
            return

        self._red_herring = self._resolve_red_herring_player(engine)
        if self._red_herring is not None:
            engine.log(
                f"{self._red_herring.name} ({chosen.name}) is the red "
                f"herring for {self.player.name} (Fortune Teller)."
            )

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        # Re-resolve the red-herring player every night so a mid-game
        # character swap on that seat (Scarlet Woman becomes the Imp,
        # etc.) is reflected in tonight's read.
        self._red_herring = self._resolve_red_herring_player(engine)

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: pick 2 players (FT is allowed to pick themselves).
        all_player_ids = [p.id for p in engine.players]
        sel = SelectPlayerPrompt(
            text="Fortune Teller picks 2 players",
            count=2,
            eligible_player_ids=all_player_ids,
            allow_self=True,
            allow_randomize=False,  # player decision (FT picks 2)
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_players",
                "stage": "player",
            },
        )
        chosen_ids = engine.send_prompt(sel)
        if isinstance(chosen_ids, int):
            chosen_ids = [chosen_ids]
        chosen_players = [engine.get_player(pid) for pid in chosen_ids]
        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=chosen_players)
        )

        # Compute the default answer. YES iff any chosen player passes
        # a char_type=DEMON check (Recluse may misregister) OR is the
        # red herring.
        from engine.check import Check
        demon_check = Check(
            attribute="char_type",
            passes=(CharType.DEMON,),
            detector_name=self.name,
            detector_player_id=self.player.id,
            extra_meta={"step_for": "fortune_teller_pick"},
        )
        rh = self._red_herring
        auto_yes = False
        for p in chosen_players:
            if rh is not None and p.id == rh.id:
                auto_yes = True
                # Don't break — still run the check on the other
                # picked player so a Recluse / misregister prompt
                # fires consistently regardless of pick order.
                continue
            if self.check(engine, p, demon_check):
                auto_yes = True

        # Sober + healthy: trust the auto-computed answer, no ST prompt.
        # Drunk/poisoned: binary info — pre-fill the *flipped* (wrong)
        # answer on a YesNoPrompt and let the ST send it (or change it)
        # via Yes/No. The default-highlighted button is the wrong one.
        if is_drunk_or_poisoned:
            default_wrong = not bool(auto_yes)
            yn = YesNoPrompt(
                text="Show YES or NO?",
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_yes_no",
                    "stage": "st_post",
                    "due_to_drunk_poison": True,
                    "default": bool(default_wrong),
                    "correct": bool(auto_yes),
                    "selected_player_ids": [p.id for p in chosen_players],
                },
            )
            resp = engine.send_prompt(yn)
            ans = bool(resp) if isinstance(resp, bool) else default_wrong
        else:
            ans = bool(auto_yes)

        # INFORMATION.
        names = [p.name for p in chosen_players]
        names_text = (
            f"{names[0]} and {names[1]}" if len(names) >= 2
            else (names[0] if names else "(no one)")
        )
        info_text = (
            f"Yes — one of {names_text} registers as a Demon."
            if ans
            else f"No — neither {names_text} registers as a Demon."
        )
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[p.id for p in chosen_players],
                # The info is "is one of these two the Demon?" — show the
                # Demon token alongside the two highlighted chairs.
                highlight_characters=["Demon"],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    # YES / NO label, with the picked players' names
                    # alongside as the body. Render directive avoids
                    # the UI's character-name switch.
                    "render": {
                        "tokens": [{
                            "label": "YES" if ans else "NO",
                            "body": names_text,
                        }],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=chosen_players,
                data={"info": info_text, "answer": bool(ans)},
            )
        )

        # No state to mutate — pure information ability.
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
