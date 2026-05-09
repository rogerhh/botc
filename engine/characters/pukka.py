"""Pukka.

    "Each night, choose a player: they are poisoned. The previously
     poisoned player dies then becomes healthy."

The Pukka acts on every night (including the first — unique among
BMR demons). Each night they pick a player; that player is poisoned
immediately. On every night except the first, the *previously*
poisoned player (from the prior night) dies and becomes sober.

Implementation (registry-managed)
---------------------------------
* The previously-poisoned target is tracked by querying the registry
  for active :class:`PukkaPoisonEffect` instances sourced by this
  Pukka. No private bookkeeping needed.
* On night N>=2, before picking, the Pukka kills its previous target
  via :meth:`Engine.kill` with cause ``DEMON_KILL``, then purges the
  old PukkaPoisonEffect (whether or not the kill landed — per wiki:
  "Innkeeper prevents the Pukka from killing a poisoned player, then
  that player is no longer poisoned").
* The new pick is poisoned via ``engine.add_effect(PukkaPoisonEffect
  (...))``. The Pukka's lifecycle hooks (purge_on_source_death,
  deactivate_on_source_droisoned) handle every cleanup path.
* The Exorcist gate (``engine._exorcism_blocked_id``) suppresses the
  *new pick* only. Per the BMR ruling (BOTC quiz, BMR Intermediate
  Q6) and the Exorcist's own design note, an Exorcist-blocked Pukka
  still resolves the previous-night poison: the previously-poisoned
  player dies tonight and is no longer poisoned. The Pukka does not
  wake, but the prior-night's poison "matures" anyway.
* Drunk/poisoned Pukka: the Pukka's ability is suppressed entirely
  for the night — no kill of the previous target, no purge of the
  prior PukkaPoisonEffect, and no real effect on the new pick. Per
  the BMR ruling (BOTC quiz, BMR Intermediate Q25), the previously-
  poisoned player remains poisoned and survives; once the Pukka is
  sober again, that player still dies on the Pukka's next sober
  night. The persistence is implemented at the effect level via
  ``PukkaPoisonEffect.deactivate_on_source_droisoned = False``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class PukkaPoisonEffect(Effect):
    """Pukka's per-source poison effect.

    Distinct ``kind`` from :class:`PoisonerPoisonEffect` for per-source
    token rendering. Both contribute to the same derived
    ``Player.poisoned`` state.

    The Pukka's poison has no automatic expiry — it lasts until the
    Pukka kills the target on the next night (and the kill lands /
    is cancelled, after which the Pukka's ``ability()`` purges this
    effect explicitly). If the Pukka dies, the standard lifecycle
    hook (``purge_on_source_death``, default True) clears it.

    Persistence across the Pukka becoming drunk/poisoned
    -----------------------------------------------------
    Per the official BMR ruling (BOTC quiz, Bad Moon Rising
    Intermediate, Q25): a Pukka who is drunk on a given night does
    *not* kill the previously-poisoned player and does *not* clear
    the poison; the next time the Pukka is sober, that previously-
    poisoned player still dies. To support this, the effect overrides
    ``deactivate_on_source_droisoned`` to ``False`` — temporary
    drunkenness/poisoning of the Pukka does not suspend the poison
    on the target. Only an explicit purge (the Pukka's own next-night
    follow-through, the Pukka's death, etc.) clears it.
    """

    kind = "pukka_poisoned"
    contributes_to_state = "poisoned"
    # The poison was placed by a sober Pukka and persists across any
    # later drunk/poisoned nights of the Pukka — see class docstring.
    deactivate_on_source_droisoned = False
    # No on_phase_boundary — Pukka's effect doesn't expire on its
    # own. The Pukka's next ability() purges the previous one.


class PukkaDeadEffect(Effect):
    """Marker on a seat the Pukka killed last night."""

    kind = "pukka_dead"
    contributes_to_state = None
    # Survives source death: a self-kill (or chained Pukka death) must
    # still leave the DEAD marker on the victim's seat. Dawn cleanup
    # (on_phase_boundary) remains responsible for removing the marker.
    purge_on_source_death = False
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class Pukka(Character):
    name = "Pukka"
    char_type = CharType.DEMON
    ability_text = (
        "Each night, choose a player: they are poisoned. The previously "
        "poisoned player dies then becomes healthy."
    )
    first_night_order = 19
    other_night_order = 27
    reminder_tokens: list = [
        {"name": "POISONED", "icon": "pukka_poisoned.png"},
    ]

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        # Exorcist block: the Pukka does not WAKE and does not pick a
        # new poison target tonight, but the previous-night's poison
        # still resolves (kill + clear). Drunk/poisoned Pukka, by
        # contrast, suppresses the *whole* ability — handled below
        # via ``can_produce_real_effect``.
        is_exorcist_blocked = (
            getattr(engine, "_exorcism_blocked_id", None) == self.player.id
        )
        if is_exorcist_blocked:
            engine.log(
                f"Pukka {self.player.name}: blocked by the Exorcist; "
                f"does not wake to pick, but the previously-poisoned "
                f"player still dies and stops being poisoned."
            )
        else:
            engine.dispatch(
                Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
            )
            engine.dispatch(
                Event(EventType.WAKEUP, source=self, targets=[self.player])
            )

        # Step 1: resolve the previously-poisoned player.
        #
        # This step runs even when the Pukka is Exorcist-blocked, but
        # is gated by ``can_produce_real_effect`` so a drunk/poisoned
        # Pukka does NOT kill the previous target and does NOT purge
        # the prior poison (per BOTC quiz, BMR Intermediate Q25 — the
        # poison persists across the Pukka's drunk night, and the
        # target dies on the Pukka's next sober night instead).
        if night_number > 1 and self.can_produce_real_effect:
            previous = next(
                (
                    e for e in engine.effects_sourced_by(self)
                    if isinstance(e, PukkaPoisonEffect)
                ),
                None,
            )
            if previous is not None:
                try:
                    prev_target = engine.get_player(previous.targets[0])
                except (KeyError, IndexError):
                    prev_target = None
                if prev_target is not None and prev_target.alive:
                    engine.kill(prev_target.id, DeathCause.DEMON_KILL, source=self)
                    if not prev_target.alive:
                        engine.add_effect(PukkaDeadEffect(
                            source=self, targets=[prev_target.id],
                        ))
                # Purge the old poison whether or not the kill landed
                # (Innkeeper / Monk protection still removes poison —
                # see wiki).
                engine.purge_effect(previous)

        # Step 2: pick tonight's target. Skipped entirely on Exorcist
        # block (the Pukka doesn't wake to pick).
        if is_exorcist_blocked:
            return

        # Per the wiki rule, "choose a player" allows alive or dead
        # picks. A dead pick still places the poison effect on the
        # seat — if the seat is later revived (Professor), they wake
        # up poisoned for the duration.
        eligible = [p.id for p in engine.players]
        sel = SelectPlayerPrompt(
            text="Pukka poisons a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_target",
                "stage": "player",
                "is_demon_attack": True,
            },
        )
        target_id = engine.send_prompt(sel)
        if isinstance(target_id, list):
            target_id = target_id[0] if target_id else None
        if target_id is None:
            return
        try:
            target = engine.get_player(int(target_id))
        except (KeyError, ValueError, TypeError):
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=[target])
        )
        # Goon notify: if Pukka picked the Goon's seat, drunken Pukka
        # synchronously so the has_ability check below short-circuits.
        engine.notify_goon_chosen(self, target)

        # RESOLUTION: emit PukkaPoisonEffect via the registry. A
        # drunk/poisoned or Lunatic-shadowed Pukka goes through the
        # motions but no effect lands. ``can_produce_real_effect``
        # combines the authenticity gate (real Pukka on its own seat)
        # with the standard has_ability check.
        if self.can_produce_real_effect:
            engine.add_effect(PukkaPoisonEffect(
                source=self, targets=[target.id],
            ))
        else:
            engine.log(
                f"Pukka {self.player.name} (authentic={self.is_authentic}, "
                f"has_ability={self.player.has_ability}) — no real "
                f"poisoning."
            )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )
