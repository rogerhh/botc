"""Scarlet Woman.

    "If there are 5 or more players alive and the Demon dies, you
     become the Demon."

Reaction-based promotion. The Scarlet Woman watches every ``DEATH``
event; if it kills the Demon AND there were 5+ non-Traveler players
alive *just before* the death, the Scarlet Woman becomes the Demon.

The "alive count just before the death" is reconstructable: at the
time the reaction fires the Demon is already dead, so we add 1 to
the current alive count. Travelers do not count toward the threshold.

The trigger is uniform: **any** Demon death qualifies — execution,
Slayer, Imp self-kill, Storyteller-attributed kill, etc. The Scarlet
Woman's reaction does not special-case how the Demon died. For an
Imp self-kill specifically, the Imp's own ability runs a deferred
post-DEATH handler that observes whether this reaction has already
promoted a new Demon and adapts accordingly (no double-promotion,
inline same-night reveal instead of next-night queue).

Implementation
--------------
We become the Demon by mutating our character via
:meth:`engine.engine.Engine.change_character`, which builds a fresh
:class:`Imp` instance and rewires our :class:`Player`'s reference.
After this point, the engine's normal flow continues — including the
post-DEATH win check, which will now see the new Demon alive and
*not* declare a good win.

Caveat: change_character has to happen *before* the engine's
``_check_win_conditions`` runs (which is invoked synchronously after
the DEATH event is dispatched). Since reactions are executed as part
of the dispatch loop *before* control returns to ``Engine.kill``, we
satisfy that ordering.

The "YOU ARE the <Demon>" reveal does NOT happen synchronously here.
The promoted player is enqueued onto
``engine._sw_pending_demon_reveal``; the next night's "Scarlet
Woman" preset step (``_run_scarlet_woman_step``) drains the queue
and runs the wakeup + InformationPrompt then. Per the rules, no
DEMON INFO is given. The Imp self-kill flow is the one exception —
the reveal happens inline that same night, and the Imp's
post-DEATH handler removes the promoted seat from the queue so the
preset step doesn't re-reveal next night.

Drunkenness / poisoning: a drunk or poisoned Scarlet Woman does NOT
take over.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine

class ScarletWoman(Character):
    name = "Scarlet Woman"
    char_type = CharType.MINION
    ability_text = (
        "If there are 5 or more players alive and the Demon dies, you "
        "become the Demon."
    )
    first_night_order = 0
    other_night_order = 15
    reminder_tokens: list = [
        {"name": 'IS THE DEMON', "icon": 'scarlet_woman_is_the_demon.png'},
    ]

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        # The Scarlet Woman never *takes a night action* — her promotion
        # to Demon happens via reaction the moment the Demon dies. The
        # entry in ``night.txt`` exists so the storyteller can show
        # "YOU ARE the new Imp" on the night she just promoted, but at
        # that point the seated player's character has already been
        # changed to Imp (see ScarletWoman.reaction). So the SW step
        # in the preset never matches an in-play character anyway —
        # we still return False here for clarity and so a stale
        # SW player (e.g. resurrected) doesn't trigger a phantom step.
        return False

    def reaction(self, event: Event, engine: "Engine") -> None:
        if self.player is None or self.player.dead:
            return super().reaction(event, engine)
        if not self.player.has_ability:
            return super().reaction(event, engine)
        if event.type is not EventType.DEATH:
            return super().reaction(event, engine)
        if not event.targets:
            return super().reaction(event, engine)

        target = event.targets[0]
        # Only react when the dying player WAS the Demon.
        if target.char_type is not CharType.DEMON:
            return super().reaction(event, engine)
        # Don't react to the Scarlet Woman's own death (defensive).
        if target.id == self.player.id:
            return super().reaction(event, engine)

        # Alive count just before the death = current alive + 1
        # (since the target has just been flipped to dead, and the
        # Scarlet Woman herself is still alive).
        alive_now = [
            p for p in engine.alive_players
            if p.char_type not in (CharType.TRAVELER, CharType.FABLED)
        ]
        # The just-killed Demon would have been counted before death.
        alive_before = len(alive_now) + 1
        if alive_before < 5:
            engine.log_reaction(
                "Scarlet Woman",
                (
                    f"Demon died with only {alive_before} players alive — "
                    f"{self.player.name} does not become the Demon."
                ),
                target=self.player,
                trigger="demon_death",
                effect="no_promote_below_5",
                alive_before=alive_before,
            )
            return super().reaction(event, engine)

        # Promote: become the same Demon that just died (NOT necessarily
        # Imp — on a non-trouble-brewing script the dying demon could be
        # Pukka, Vortox, …; we instantiate a fresh instance of *that*
        # demon's class via ``change_character``, which builds the
        # character via ``script_data.build_character`` and resets
        # ``once_per_game_used`` so any once-per-game / first-night
        # ability the new Demon has is available to the new holder).
        new_demon = target.character.name if target.character else "Imp"
        engine.log_reaction(
            "Scarlet Woman",
            f"{self.player.name} becomes the {new_demon}.",
            target=self.player,
            trigger="demon_death",
            effect="promote_to_demon",
            new_demon=new_demon,
        )
        promoted_pid = self.player.id
        engine.change_character(promoted_pid, new_demon)
        # Persist evil alignment — change_character preserves alignment.

        # Bookkeeping: record the promotion so the UI grimoire can render
        # the "Scarlet Woman IS THE DEMON" reminder token on this seat
        # for the rest of the game, and queue the seat for the
        # night-time "YOU ARE the <Demon>" reveal at the preset's
        # "Scarlet Woman" step. We do NOT run DEMON INFO — the rules
        # only call for showing the new demon role.
        if promoted_pid not in engine._sw_promoted_player_ids:
            engine._sw_promoted_player_ids.append(promoted_pid)
        if promoted_pid not in engine._sw_pending_demon_reveal:
            engine._sw_pending_demon_reveal.append(promoted_pid)
        return super().reaction(event, engine)
