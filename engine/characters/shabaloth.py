"""Shabaloth.

    "Each night*, choose 2 players: they die. A dead player you chose
     last night might be regurgitated."

The Shabaloth attacks twice per night and may resurrect one of last
night's victims. Acts every night except the first.

Implementation
--------------
* Each night >= 2:
    1. (Regurgitation step.) If the Shabaloth had at least one
       seat carrying a DEAD marker from last night's attacks, the
       storyteller is shown a single picker listing those seats
       plus a "No regurgitation" sentinel. The eligibility list
       only contains seats where the Shabaloth's attack actually
       *killed* the player; seats the Shabaloth pointed at but
       couldn't kill (Tea Lady / Innkeeper / Sailor / Mayor / …)
       never carry DEAD and are never regurgitatable, even if they
       die later from another cause.
    2. (Attack step.) The Shabaloth picks two players and the
       storyteller orders them. Each is killed with cause
       ``DEMON_KILL`` and ``source=self``. Standard Tea Lady /
       Innkeeper / Sailor / Fool / Mayor protections apply per
       character.
* The next night's regurgitation pool is sourced from the live
  ``ShabalothDeadEffect`` registry (i.e. seats where the kill
  *landed*). Picks that the Shabaloth pointed at but didn't kill —
  Tea Lady-protected, Innkeeper-protected, Sailor-protected,
  Mayor-redirected — are never marked DEAD and therefore are never
  regurgitatable, even if they later die from another cause
  (Assassin, execution, …).
* Drunk/poisoned Shabaloth still wakes and goes through the picks,
  but no kill / no regurgitation lands.
* Exorcist block: skip both steps tonight.

Reminder tokens
---------------
Two reminder tokens, mirroring the Shabaloth's rulebook page:

* ``DEAD`` — placed on every seat the Shabaloth killed last night
  (i.e. the kill stuck — Innkeeper-protected / Tea Lady-saved
  targets are not marked). The next night's regurgitation step
  reads these to decide who is eligible to come back. Cleared at
  the start of every new attack pass: only *last night's* victims
  are eligible per the rulebook.

* ``ALIVE`` — placed on the seat the Shabaloth regurgitated. Like
  every BMR dawn-announcement reminder, ALIVE expires at the next
  dawn (via ``on_phase_boundary``); its purpose is to remind the
  storyteller to declare *"this player is alive again"* at dawn.
  May also be purged earlier if the same seat is re-killed by the
  Shabaloth before dawn (DEAD supersedes ALIVE on a target).

DEAD and ALIVE are mutex on the same seat by construction: emitting
one purges the other on that target. They share an angle in the
chair-token layout per the project's mutex-share-an-angle rule.
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


class ShabalothDeadEffect(Effect):
    """DEAD reminder — marker on a seat the Shabaloth killed last night.

    The Shabaloth's regurgitation prompt the next night reads these
    effects to know who can be revived. Cleared at the next ability
    cycle: only *last night's* DEAD-marked seats are eligible per
    the rulebook ("a dead player you chose last night")."""

    kind = "shabaloth_dead"
    contributes_to_state = None
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False


class ShabalothAliveEffect(Effect):
    """ALIVE reminder — placed on the seat the Shabaloth regurgitated.

    Like every dawn-announcement reminder in BMR (``imp_dead``,
    ``po_dead``, ``pukka_dead``, …), this marker exists so the
    storyteller can announce *"this player is alive again"* at the
    next dawn. It is purged at that dawn via
    ``on_phase_boundary("dawn")`` — same lifetime contract as the
    DEAD markers across BMR demons.

    May also be purged earlier if the seat is re-killed by the
    Shabaloth before dawn (the inline mutex purge in ``_kill_one``
    swaps ALIVE → DEAD on the same target)."""

    kind = "shabaloth_alive"
    contributes_to_state = None
    purge_on_source_death = True
    purge_on_source_character_change = True
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dawn":
            engine.purge_effect(self)


class Shabaloth(Character):
    name = "Shabaloth"
    char_type = CharType.DEMON
    ability_text = (
        "Each night*, choose 2 players: they die. A dead player you "
        "chose last night might be regurgitated."
    )
    first_night_order = 0
    other_night_order = 28
    reminder_tokens: list = [
        {"name": "DEAD",  "icon": "shabaloth_dead.png"},
        {"name": "ALIVE", "icon": "shabaloth_alive.png"},
    ]

    # DEAD markers rendered via ShabalothDeadEffect emitted in
    # ``ability()`` after each kill that lands. ALIVE markers are
    # emitted in the regurgitation step on the resurrected seat.
    # The DEAD effect registry is the single source of truth for
    # regurgitation eligibility — see ability() below.

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        if getattr(engine, "_exorcism_blocked_id", None) == self.player.id:
            engine.log(
                f"Shabaloth {self.player.name}: blocked by the Exorcist."
            )
            return

        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Step 1: regurgitation. Eligibility comes from last night's
        # DEAD effects in the registry — only seats the Shabaloth's
        # attack actually killed are regurgitatable. A seat the
        # Shabaloth pointed at but couldn't kill (Tea Lady-protected,
        # Innkeeper-protected, Sailor-protected, Mayor-redirected,
        # Soldier when the demon is the Imp, etc.) never received a
        # DEAD marker, so it stays out of this pool even if it later
        # dies from another cause (Assassin, execution, …).
        prior_dead = []
        seen_pids: set = set()
        for eff in engine.effects_sourced_by(self):
            if not isinstance(eff, ShabalothDeadEffect):
                continue
            for tid in eff.targets:
                if tid in seen_pids:
                    continue
                seen_pids.add(tid)
                try:
                    p = engine.get_player(tid)
                except KeyError:
                    continue
                if p.dead and p.character is not None:
                    prior_dead.append(p)
        # Regurgitation only runs on the *authentic* Shabaloth seat.
        # A Lunatic-/Drunk-shadowed Shabaloth never accumulated real
        # ShabalothDeadEffects last night (the kill gate below was
        # closed), so ``prior_dead`` would be empty anyway — but we
        # guard explicitly on ``can_produce_real_effect`` here so a
        # future change to effect emission can't accidentally open
        # the regurgitation prompt on a shadow seat.
        if prior_dead and self.can_produce_real_effect:
            # Single-step picker: the storyteller sees last night's
            # Shabaloth-killed seats plus a "No regurgitation" sentinel.
            # Picking the sentinel is the same as declining; no separate
            # Yes/No prompt fires beforehand.
            decline_id = 0
            eligible_ids = [p.id for p in prior_dead]
            sel = SelectPlayerPrompt(
                text="Shabaloth: regurgitate a previous victim?",
                count=1,
                eligible_player_ids=eligible_ids + [decline_id],
                allow_self=False,
                allow_randomize=True,
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "regurgitate_pick",
                    "stage": "st_post",
                    "decline_id": decline_id,
                    "decline_label": "No regurgitation",
                    # Default to declining: the Shabaloth's regurgitation
                    # is a per-game rare event, so the safe ST-presses-Next
                    # outcome is "no regurgitation tonight."
                    "default": decline_id,
                },
            )
            regur_id = engine.send_prompt(sel)
            if isinstance(regur_id, list):
                regur_id = regur_id[0] if regur_id else None
            # Decline sentinel (or no answer) → no regurgitation.
            if regur_id is not None and regur_id != decline_id:
                try:
                    rp = engine.get_player(int(regur_id))
                    if rp.dead:
                        engine.revive(rp.id)
                        # Per rulebook: "replace the DEAD reminder
                        # with the Shabaloth's ALIVE reminder."
                        # Drop our DEAD marker on this seat (the
                        # bulk DEAD purge below would also cover
                        # it, but doing it inline keeps the swap
                        # local and explicit) and emit ALIVE.
                        for old in list(
                            engine.effects_targeting(
                                rp.id, kind="shabaloth_dead",
                                active_only=False,
                            )
                        ):
                            if old.source is self:
                                engine.purge_effect(old)
                        engine.add_effect(ShabalothAliveEffect(
                            source=self, targets=[rp.id],
                        ))
                        engine.log_reaction(
                            "Shabaloth",
                            f"{rp.name} is regurgitated by the Shabaloth.",
                            target=rp,
                            trigger="regurgitate",
                        )
                except (KeyError, ValueError, TypeError):
                    pass

        # The regurgitation step has now consumed last night's DEAD
        # pool — clear it before Step 2 so the markers don't carry
        # over and re-qualify on a *future* night. We do this before
        # Step 2's early-return paths (fewer than 2 alive players,
        # or the ST returning <2 picks) so the rulebook's "a dead
        # player you chose **last night**" gate holds even when the
        # kill step bails out. ALIVE markers from earlier
        # regurgitations are deliberately left intact — they are
        # the rulebook's record-of-resurrection for the rest of the
        # game.
        for old in list(engine.effects_sourced_by(self)):
            if isinstance(old, ShabalothDeadEffect):
                engine.purge_effect(old)

        # Step 2: pick 2 to attack. Per the wiki rule, "choose a
        # player" allows alive or dead picks. A dead pick still places
        # the ShabalothDeadEffect on the seat (via _kill_one's
        # `if not tp.alive` branch — engine.kill no-ops, tp.alive
        # stays False, the marker lands), so the seat becomes
        # regurgitate-eligible next round.
        eligible = [p.id for p in engine.players]
        if len(eligible) < 2:
            return
        sel = SelectPlayerPrompt(
            text="Shabaloth picks 2 players to eat",
            count=2,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_targets",
                "stage": "player",
                "is_demon_attack": True,
            },
        )
        chosen = engine.send_prompt(sel)
        if isinstance(chosen, int):
            chosen_ids: List[int] = [chosen]
        elif isinstance(chosen, list):
            chosen_ids = [int(x) for x in chosen]
        else:
            chosen_ids = []
        chosen_players = []
        for pid in chosen_ids:
            try:
                chosen_players.append(engine.get_player(int(pid)))
            except (KeyError, ValueError, TypeError):
                continue
        if len(chosen_players) < 2:
            return

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=chosen_players)
        )

        # Goon-aware per-target loop. The base helper notifies the
        # Goon BEFORE running ``action_fn`` for each target, so:
        #   * picks=[Goon, A]   → notify drunkens Shabaloth on Goon,
        #                         action_fn never runs, neither dies.
        #   * picks=[A, Goon]   → action_fn kills A, notify drunkens
        #                         Shabaloth on Goon, loop breaks,
        #                         Goon doesn't die.
        # A drunk/poisoned Shabaloth at SELECT time is gated by the
        # helper's pre-iteration has_ability check; no kills land.
        def _kill_one(tp: "Player") -> None:
            engine.kill(tp.id, DeathCause.DEMON_KILL, source=self)
            if not tp.alive:
                # If this seat had a prior ALIVE record (it was
                # regurgitated earlier in the game and is now being
                # eaten again), DEAD supersedes ALIVE on the same
                # target — they're mutex by construction.
                for old in list(
                    engine.effects_targeting(
                        tp.id, kind="shabaloth_alive",
                        active_only=False,
                    )
                ):
                    if old.source is self:
                        engine.purge_effect(old)
                engine.add_effect(ShabalothDeadEffect(
                    source=self, targets=[tp.id],
                ))

        if not self.can_produce_real_effect:
            engine.log(
                f"Shabaloth {self.player.name} "
                f"(authentic={self.is_authentic}, "
                f"has_ability={self.player.has_ability}) — no kills."
            )
        # Goon-break helper internally gates on ``can_produce_real_effect``,
        # so a Lunatic-shadow / drunk Shabaloth runs through the
        # SELECT prompt above (driving the demon-info card) but
        # ``_kill_one`` never executes.
        self.process_targets_with_goon_break(
            engine, chosen_players[:2], _kill_one,
        )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
