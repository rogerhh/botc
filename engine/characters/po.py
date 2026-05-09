"""Po.

    "Each night*, you may choose a player: they die. If your last
     choice was no-one, choose 3 players tonight."

The Po acts on every night except the first. Each night the Po may
either pick a single player or shake their head no. If they shook
their head the previous night, they must pick three players this
night (and may not skip again).

Implementation
--------------
* ``_charged`` tracks "did the Po choose no-one last night?". Set
  on a "no" pick; cleared on a real pick (1 or 3).
* The wiki notes: "If the Exorcist selects the Po, the Po does not
  act, but this night does not count as a night where the Po 'chose
  no one'." The Exorcist gate therefore short-circuits *without*
  modifying ``_charged``.
* "Po doesn't act on the first night, but this night does not count
  as a night where the Po 'chose no one'." First-night skip leaves
  ``_charged = False`` so a possibly-Po who is being Exorcism-blocked
  on night 2 doesn't accidentally enter charged mode.
* Drunk/poisoned Po: the picks happen but no kills land. If the Po
  was drunk/poisoned when choosing nobody, they still owe 3 picks
  the next night (per the wiki).
* When charged, the engine forces 3 picks (no skip allowed); we
  enforce by passing only the 3-pick prompt without a skip option.
* All kills use ``cause=DEMON_KILL`` and ``source=self``; standard
  protections fire per character.

Reminder tokens
---------------
``3 ATTACKS`` is surfaced on the Po's seat while charged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class Po3AttacksEffect(Effect):
    """3 ATTACKS marker on the Po's seat when charged from a skip."""

    kind = "po_3_attacks"
    contributes_to_state = None
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


class PoDeadEffect(Effect):
    """Marker on a seat the Po killed last night. Cleared at next dawn."""

    kind = "po_dead"
    contributes_to_state = None
    # Survives source death: a self-kill (Po picks self) must still
    # leave the DEAD marker on the victim's seat. Dawn cleanup
    # (on_phase_boundary) remains responsible for removing the marker.
    purge_on_source_death = False
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class Po(Character):
    name = "Po"
    char_type = CharType.DEMON
    ability_text = (
        "Each night*, you may choose a player: they die. If your last "
        "choice was no-one, choose 3 players tonight."
    )
    first_night_order = 0
    other_night_order = 29
    reminder_tokens: list = [
        {"name": "3 ATTACKS", "icon": "po_3_attacks.png"},
    ]

    def __init__(self, player=None) -> None:
        super().__init__(player)
        self._charged: bool = False

    # 3 ATTACKS / kill markers rendered via Po3AttacksEffect /
    # PoDeadEffect emitted from ``ability()``.

    def _set_charged(self, engine: "Engine", charged: bool) -> None:
        """Sync ``_charged`` with the registry's Po3AttacksEffect.

        The ``Po3AttacksEffect`` renders the visible ``3 ATTACKS``
        reminder token on the Po's chair. A Lunatic-as-Po (or
        Drunk-as-Po, hypothetically) also tracks a charge clock
        independently of any real Po's, but the impersonator-side
        state is kept *silent and internal* — no chair-side reminder,
        just the in-memory ``self._charged`` flag. The flag still
        drives the impersonator's own pick count on the next night so
        the demon-info card reads out the correct number of picks.

        The impersonator suppression is handled by the engine-level
        gate in :meth:`Engine.add_effect`: emissions whose source is
        a perceived character (``is_authentic`` False) are silently
        suppressed for the registry, and ``effects_sourced_by`` for
        the same source returns an empty list, so the purge branch
        below is also a no-op for impersonators. No per-character
        check needed here.
        """
        self._charged = charged
        existing = [
            e for e in engine.effects_sourced_by(self)
            if isinstance(e, Po3AttacksEffect)
        ]
        if charged and not existing and self.player is not None:
            engine.add_effect(Po3AttacksEffect(
                source=self, targets=[self.player.id],
            ))
        elif not charged and existing:
            for old in existing:
                engine.purge_effect(old)

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        if getattr(engine, "_exorcism_blocked_id", None) == self.player.id:
            # Exorcism blocks this night entirely; charged state
            # untouched per wiki.
            engine.log(
                f"Po {self.player.name}: blocked by the Exorcist."
            )
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Per wiki rule, "choose a player" allows alive or dead picks.
        # A dead pick wastes that attack (engine.kill no-ops on dead)
        # but is legal — and useful for an evil Po that wants to hide
        # its kill count.
        eligible = [p.id for p in engine.players]
        decline_id = 0

        if self._charged:
            # Must pick 3 — no skip allowed.
            if len(eligible) < 3:
                # Pick as many as possible (defensive — a Po with
                # fewer alive targets than 3 picks all available).
                count = max(1, len(eligible))
            else:
                count = 3
            sel = SelectPlayerPrompt(
                text=f"Po unleashes {count} kills (charged)",
                count=count,
                eligible_player_ids=eligible,
                allow_self=False,
                allow_randomize=False,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_targets_charged",
                    "stage": "player",
                    "is_demon_attack": True,
                },
            )
        else:
            sel = SelectPlayerPrompt(
                text="Po picks a player (or shakes head no)",
                count=1,
                eligible_player_ids=eligible + [decline_id],
                allow_self=False,
                allow_randomize=False,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_target_or_skip",
                    "stage": "player",
                    "decline_id": decline_id,
                    "is_demon_attack": True,
                    # The decline sentinel doesn't correspond to a
                    # real player — the engine's pick recorder strips
                    # it before writing to ``_lunatic_picks_tonight``
                    # so a Lunatic-as-Po who shakes their head reads
                    # out as "did not pick anyone tonight".
                    "lunatic_filter_id": decline_id,
                },
            )

        chosen = engine.send_prompt(sel)
        if isinstance(chosen, int):
            chosen_ids: List[int] = [chosen]
        elif isinstance(chosen, list):
            chosen_ids = [int(x) for x in chosen]
        else:
            chosen_ids = []

        # Did the Po skip?
        is_skip = (
            not self._charged
            and len(chosen_ids) == 1
            and chosen_ids[0] == decline_id
        )

        if is_skip:
            self._set_charged(engine, True)
            engine.log(
                f"Po {self.player.name} chose no-one — charging for 3 "
                f"kills next night."
            )
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[self.player])
            )
            return

        chosen_players = []
        for pid in chosen_ids:
            if pid == decline_id:
                continue
            try:
                chosen_players.append(engine.get_player(int(pid)))
            except (KeyError, ValueError, TypeError):
                continue

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=chosen_players)
        )

        # Goon-aware per-target loop. When charged, Po picks 3 (or
        # fewer if not enough alive); the base helper notifies the
        # Goon BEFORE each per-target kill, so:
        #   * picks=[Goon, A, B]   → drunkens Po immediately,
        #                            no kills land.
        #   * picks=[A, Goon, B]   → A dies, then drunken on Goon,
        #                            B is skipped.
        #   * picks=[A, B, Goon]   → A and B die, then drunken on Goon.
        # A drunk/poisoned Po at SELECT time is gated by the helper's
        # pre-iteration has_ability check.
        def _kill_one(tp: "Player") -> None:
            engine.kill(tp.id, DeathCause.DEMON_KILL, source=self)
            if not tp.alive:
                engine.add_effect(PoDeadEffect(
                    source=self, targets=[tp.id],
                ))

        if not self.can_produce_real_effect:
            engine.log(
                f"Po {self.player.name} (authentic={self.is_authentic}, "
                f"has_ability={self.player.has_ability}) — no real kills."
            )
        # The Goon-break helper internally gates on
        # ``can_produce_real_effect`` so a Lunatic-shadowed or
        # drunk/poisoned Po runs through the picks (recorded above)
        # but ``_kill_one`` never executes.
        self.process_targets_with_goon_break(
            engine, chosen_players, _kill_one,
        )

        # Pick happened: clear charged state. This runs even on a
        # Lunatic-shadow seat — the Lunatic-as-Po has its own internal
        # charge clock (independent from the real Po's), tracked
        # silently via ``_charged`` / Po3AttacksEffect on this instance.
        self._set_charged(engine, False)

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
