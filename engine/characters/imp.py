"""Imp.

    "Each night except the first, choose a player: they die. If you
     choose yourself, you die and a Minion becomes the Imp."

The Demon's nightly kill, plus the self-kill / Minion-promotion
mechanic ("starpassing").

Implementation
--------------
* On every night after the first, the Imp wakes, picks any player
  (alive or dead — picking a dead player simply wastes the kill, but
  is allowed by the rules), and that player dies via
  :meth:`engine.engine.Engine.kill` with ``DEMON_KILL`` cause and
  ``source=self`` so the kill carries an attribution back to the
  Imp's own ability.
* Soldier protection, Monk protection, Mayor death-redirect are
  enforced by :meth:`Engine.kill` and the Mayor's reaction; the Imp's
  ability doesn't need to know about them.
* Self-kill detection is **reactive**, not based on whether the Imp
  picked themselves at SELECT time. The Imp listens on ``DEATH`` for
  itself with ``event.source is self`` (i.e., the kill ultimately
  originated from this Imp's own ability, regardless of redirects).
  This means picking yourself directly *and* having the Mayor
  redirect a kill back to you both trigger the self-kill / promote-a-
  Minion flow — without either character knowing about the other.
* The self-kill **handler is deferred** to the post-DEATH callback
  queue rather than running synchronously inside the reaction. That
  way every other reaction listening on the same DEATH event — most
  importantly the Scarlet Woman's "Demon dies → you become the
  Demon" promotion — has already settled before the handler observes
  engine state. This sidesteps reaction-order fragility (the SW seat
  may sit before *or* after the Imp seat in seating order) and
  removes any need for special-case flags between the two roles.
* On a self-attributed Imp death the deferred handler:
    - Detects whether a new Demon is *already* in play (e.g. the
      Scarlet Woman has just auto-promoted). If so, no ST prompt
      and no character change — the new Demon's seat is simply
      adopted as the new Imp; the next-night reveal queued by the
      SW reaction is dequeued because we will run the reveal inline
      this same night.
    - Otherwise, prompts the Storyteller to pick any alive Minion
      to become the new Imp (single-eligible auto-resolves), then
      :meth:`Engine.change_character`'s the picked seat to a fresh
      Imp instance (alignment preserved, once-per-game flags reset).
  Either way — per the rulebook — the new Imp is woken THIS NIGHT
  and shown "YOU ARE the Imp." (No DEMON INFO is given.) The new
  Imp does not act on this same night; their first kill is on the
  next night.
* The Scarlet Woman's reaction has **no Imp-specific code**. It
  fires uniformly on every Demon death (subject to its own 5+ alive
  / healthy / alive checks). The Imp's deferred handler is what
  reconciles the two so we never end up with two new Demons.

Drunkenness / poisoning: a drunk or poisoned Imp picks a target but
no kill happens (the storyteller still walks them through wakeup).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import Alignment, CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectPlayerPrompt

if TYPE_CHECKING:
    from engine.engine import Engine

class Imp(Character):
    name = "Imp"
    char_type = CharType.DEMON
    ability_text = (
        "Each night except the first, choose a player: they die. "
        "If you choose yourself, you die and a Minion becomes the Imp."
    )
    first_night_order = 0
    other_night_order = 25
    reminder_tokens: list = [
        {"name": 'DEAD', "icon": 'imp_dead.png'},
    ]

    def compute_reminder_tokens(self, engine: "Engine") -> "dict[str, list[int]]":
        """Demon-class reminder tokens.

          * ``imp_dead``: every seat the Demon killed since dawn.
            Uses the engine's transient ``_demon_killed_player_ids``,
            populated when a DEMON_KILL lands and cleared at dawn.
            Contributed once per Demon seat — even if multiple Demons
            are seated (defensive), the merge is a set-union.
          * ``scarlet_woman_is_demon``: any seat that was promoted
            from Scarlet Woman to this Demon class. The Imp adopts
            the marker so the UI grimoire knows the seat used to be
            the SW. If this Imp's seat isn't in the promoted list
            the contribution is empty for the marker.
        """
        out: "dict[str, list[int]]" = {}
        ids = list(getattr(engine, "_demon_killed_player_ids", []) or [])
        if ids:
            out["imp_dead"] = list(ids)
        sw_ids = list(getattr(engine, "_sw_promoted_player_ids", []) or [])
        # Only contribute the SW marker for *this* seat — otherwise a
        # script with multiple demon classes would have every seated
        # demon claim every promoted seat. The merge below in the
        # engine is a union so it's still safe, but the per-seat call
        # is the right shape.
        if self.player is not None and self.player.id in sw_ids:
            out["scarlet_woman_is_demon"] = [self.player.id]
        return out

    def ability(self, engine: "Engine", night_number: int) -> None:
        if night_number == 1 or self.player is None or self.player.dead:
            return
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: any player (per the rules — including dead ones, see
        # the Imp PDF). The Imp can also pick themselves.
        eligible = [p.id for p in engine.players]
        sel = SelectPlayerPrompt(
            text="Imp kills a player",
            count=1,
            eligible_player_ids=eligible,
            allow_self=True,
            allow_randomize=False,  # player decision (Imp picks)
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_target",
                "stage": "player",
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

        # Drunk/poisoned Imp: still selects but no real kill.
        if not self.player.has_ability:
            engine.log(
                f"Imp {self.player.name} (drunk/poisoned) tried to kill "
                f"{target.name} — no effect."
            )
            engine.dispatch(
                Event(EventType.RESOLUTION, source=self, targets=[target])
            )
            return

        # RESOLUTION: kill the chosen player with this Imp as the
        # ``source`` so the dispatch carries the attribution. Engine.kill
        # handles Soldier protection, Monk protection, Mayor redirect,
        # etc. If a redirect bounces the kill back to us, the Mayor
        # forwards ``source=self`` along, and our PRE_DEATH/DEATH
        # reaction below picks up the self-attributed kill and runs
        # the starpassing flow — no special-case branching at this
        # call site.
        engine.kill(target.id, DeathCause.DEMON_KILL, source=self)

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[target])
        )

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        """Detect a self-attributed Imp death and queue the starpass flow.

        ``event.source is self`` means the kill ultimately originated
        from this Imp's ability — directly (Imp picked themselves) or
        indirectly (Mayor redirected an Imp kill back to the Imp). In
        both cases the rulebook says a Minion becomes the new Imp.

        We hook ``DEATH`` rather than ``PRE_DEATH`` so the death has
        already landed (the Imp's seat shows DEAD before the new Imp
        is promoted, matching the rulebook flow). The actual
        starpass handler is deferred onto
        ``engine._post_death_callbacks`` so it runs *after* every
        other reaction on this DEATH has fired — most importantly,
        after the Scarlet Woman's "Demon dies → you become the
        Demon" reaction has had its turn. The handler then observes
        the settled state and decides whether a new Demon is already
        in play (skip the ST prompt) or whether the ST needs to pick
        a Minion (no demon promoted by anyone else). ``Engine.kill``
        drains the queue between the DEATH dispatch and
        ``_check_win_conditions``, so by the time the win check runs
        the new Imp is in place and a "Demon is dead" win does not
        fire.
        """
        if (
            event.type is EventType.DEATH
            and self.player is not None
            and event.source is self
            and any(t.id == self.player.id for t in event.targets)
            and event.data.get("cause") is DeathCause.DEMON_KILL
        ):
            engine._post_death_callbacks.append(
                lambda: self._handle_self_kill(engine)
            )
        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _handle_self_kill(self, engine: "Engine") -> None:
        """The Imp killed themselves — make sure a new Imp is in play.

        Runs as a post-DEATH deferred callback (see :meth:`reaction`)
        so every other reaction on the same DEATH event has already
        settled. In particular the Scarlet Woman's "Demon dies → you
        become the Demon" reaction may have already promoted the SW
        to the new Imp. Behaviour:

        1. If a Demon is **already alive** (the SW reaction promoted
           someone), adopt that seat as the new Imp. No Storyteller
           prompt; no ``change_character`` (already done by the SW
           reaction). The next-night reveal the SW reaction queued
           is dequeued — we run the reveal inline this same night.
        2. Otherwise, prompt the Storyteller to pick any alive
           Minion to be the new Imp. Single-eligible auto-resolves.
           The picked seat's character is changed to "Imp" via
           :meth:`Engine.change_character` (fresh Imp instance,
           once-per-game reset, alignment preserved). If the picked
           seat's pre-promotion role was "Scarlet Woman", the seat
           is added to ``_sw_promoted_player_ids`` so the UI
           grimoire shows the "Scarlet Woman IS THE DEMON" reminder.
        3. Either way the new Imp is woken THIS NIGHT and shown the
           "YOU ARE the Imp." reveal — a single InformationPrompt
           preceded by a WAKEUP event. No DEMON INFO is run. The new
           Imp does not act on this night; their first kill is on
           the following night.

        If no alive Minion exists and no Demon was promoted, the
        Imp's self-kill leaves no Demon in play and the engine's
        pending "Demon is dead" good win stands.
        """
        # Did some other reaction already promote a Demon (typically
        # the Scarlet Woman)? If so, that seat is the new Imp — we
        # don't prompt and we don't ``change_character`` again.
        existing_new_demons = [
            p for p in engine.alive_players
            if p.char_type is CharType.DEMON
        ]
        if existing_new_demons:
            new_imp = existing_new_demons[0]
            engine.log(
                f"Imp self-kill: {new_imp.name} is already the new "
                f"Demon (promoted by another ability) — adopting them "
                f"as the new Imp."
            )
            # The SW reaction queues a next-night reveal; we're going
            # to do the reveal inline this same night, so dequeue.
            if new_imp.id in engine._sw_pending_demon_reveal:
                engine._sw_pending_demon_reveal.remove(new_imp.id)
        else:
            alive_minions = [
                p for p in engine.alive_players
                if p.char_type is CharType.MINION
            ]
            if not alive_minions:
                engine.log(
                    "Imp self-kill: no alive Minion to promote — game "
                    "ends."
                )
                return
            sel = SelectPlayerPrompt(
                text="New Imp",
                count=1,
                eligible_player_ids=[p.id for p in alive_minions],
                allow_self=False,
                target_player_id=self.player.id if self.player else None,
                meta={
                    "character": self.name,
                    "step": "select_new_imp",
                    "stage": "st_post",
                },
            )
            chosen_id = engine.send_prompt(sel)
            if isinstance(chosen_id, list):
                chosen_id = chosen_id[0] if chosen_id else None
            if chosen_id is None:
                return
            try:
                new_imp = engine.get_player(int(chosen_id))
            except (KeyError, ValueError, TypeError):
                return
            engine.log(f"{new_imp.name} becomes the new Imp.")

            # Capture the seat's pre-promotion role BEFORE we swap it
            # to Imp — we need this to decide whether to mark the
            # grimoire "Scarlet Woman IS THE DEMON" reminder. Read
            # from the Player's current character (the in-engine
            # state), not the chair.character (which is a
            # visual-layout field set via the UI and not always
            # populated in pure-engine flows).
            prev_char_name = (
                new_imp.character.name
                if new_imp.character is not None
                else None
            )

            # Promote: build a fresh Imp instance, swap it onto the
            # seat, alignment preserved.
            engine.change_character(new_imp.id, "Imp")

            # Grimoire reminder: only mark "Scarlet Woman IS THE DEMON"
            # when the picked seat was actually the Scarlet Woman.
            if prev_char_name == "Scarlet Woman":
                if new_imp.id not in engine._sw_promoted_player_ids:
                    engine._sw_promoted_player_ids.append(new_imp.id)

        # Wake the new Imp THIS night and show "YOU ARE the Imp."
        # (per the Imp PDF: "Wake the new Imp, show them the YOU ARE
        # info token, then show them the Imp token."). Collapsed into
        # a single consolidated reveal — no DEMON INFO.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[new_imp])
        )
        engine.send_prompt(InformationPrompt(
            text="YOU ARE THE IMP.",
            target_player_id=new_imp.id,
            shown_to_player=True,
            highlight_characters=["Imp"],
            meta={
                "step_kind": "imp_self_kill_reveal",
                "character": self.name,
                "step": "new_imp_reveal",
                "stage": "info",
                "target_player_name": new_imp.name,
                "reveal": "demon_role",
                "demon_character": "Imp",
                "render": {
                    "tokens": [{"label": "YOU ARE", "body": "THE IMP"}],
                },
            },
        ))

        # ``_handle_self_kill`` runs *before* ``Engine.kill``'s
        # ``_check_win_conditions`` (it's called from the post-DEATH
        # callback drain), but the engine may already have a pending
        # good win ("The Demon is dead.") registered from the death
        # itself. Now that a new Demon is in play, retract that
        # pending win and re-evaluate so any other condition that
        # legitimately fires (two players left, etc.) is still
        # recorded.
        if (
            engine._pending_winner is Alignment.GOOD
            and engine._pending_win_reason == "The Demon is dead."
        ):
            engine._pending_winner = None
            engine._pending_win_reason = None
            engine.log(
                "Imp self-kill: clearing pending good win — a new Demon is "
                "in play."
            )
            engine._check_win_conditions()
