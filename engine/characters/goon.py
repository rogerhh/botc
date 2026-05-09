"""Goon.

    "Each night, the 1st player to choose you with their ability is
     drunk until dusk. You become their alignment."

The Goon punishes the first player who actively chooses them at
night by making that player drunk. The Goon's own alignment flips to
match the targeting player's alignment.

Implementation (registry-managed)
---------------------------------
* The trigger logic lives in :meth:`Goon.choose_me`. It is the
  canonical entry point for "a player has actively chosen the Goon
  with their night ability" — called either:

    - From the legacy SELECT-listener :meth:`reaction` (still in
      place during the migration; will be removed in PR 2 once every
      caller has been switched over).
    - From :meth:`engine.engine.Engine.notify_goon_chosen`, which a
      character's ability code invokes per actively-chosen target
      (added in PR 1, called in PR 2+).

  Keeping the gate-and-apply logic in one method means the legacy
  reaction and the new explicit notification path produce identical
  effects.

* The "first per night" gate is structural: query the registry for
  any active :class:`GoonDrunkEffect` already sourced by this Goon.
  If present, the trigger has already fired this night. No private
  per-night latch needed.
* When called inside a SELECT reaction (legacy path), making the
  source drunk during the SELECT reaction means the source's own
  resolution code reads ``has_ability=False`` and performs no real
  effect. ``add_effect`` triggers immediate
  :meth:`Engine.resolve_droison_state` so the registry-derived
  ``Player.drunk`` flips True synchronously inside the SELECT
  reaction, before the source's RESOLUTION runs.
* Alignment flips immediately on the same call. The Goon's actual
  ``Player.alignment`` mutates so future reads see the new alignment.
* At dusk, :meth:`GoonDrunkEffect.on_phase_boundary` purges itself
  and the resolver clears the registry-managed drunk.

Drunkenness / poisoning
-----------------------
Per the wiki: "The Goon still changes alignment, and makes the
player drunk, if the player choosing the Goon was already drunk or
poisoned." So the trigger fires regardless of the source's state —
only the Goon's *own* ``has_ability`` matters. A drunk/poisoned Goon
does not retort; :meth:`choose_me` short-circuits with no effect.

Assassin override
-----------------
Per the wiki: "If chosen by the Assassin, the Goon dies but still
turns evil." Handled by the Assassin's per-character override (PR 6):
the Assassin's once-per-game force-kill is fired *before*
``notify_goon_chosen``, so the kill carries ``force=True`` and
bypasses every PRE_DEATH canceller. The notify call then flips the
Goon's alignment and drunkens the (now spent) Assassin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.character import Character
from engine.effect import Effect
from engine.enums import Alignment, CharType
from engine.event import Event, EventType
from engine.prompt import InformationPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class GoonDrunkEffect(Effect):
    """The Goon's reciprocal drunkening, lasting until dusk.

    Lifecycle deviates from the registry default in one place:

      * ``deactivate_on_source_droisoned = False``. Per the wiki, once
        the Goon drunkens a player it lasts "until dusk" — a fixed
        duration, not conditional on the Goon's intermediate state.
        If the Goon is later drunkened/poisoned by another character
        (e.g. a Courtier picks the Goon's role on the same night
        after the Goon has already drunkened the Poisoner), the
        Poisoner stays drunk per the wiki's flat duration. The
        Courtier's analogous "if I become drunk, my victim sobers"
        is a *Courtier-specific* clause, not a generic source-state
        rule.

      * ``purge_on_source_death = True`` (registry default). If the
        Goon dies, the drunkening goes with them — see the user's
        case 6 (Goon dies → Innkeeper sobers → first-target
        protection reactivates). The cascade falls out of the
        existing resolver.

      * ``on_phase_boundary("dusk") -> purge``. Dusk in the engine is
        DAY → NIGHT (start of the following night). "Drunk until
        dusk" means "drunk for the rest of this night and the
        following day"; the effect purges before the next night
        runs.
    """

    kind = "goon_drunk"
    contributes_to_state = "drunk"
    # Override default — see class docstring.
    deactivate_on_source_droisoned = False

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        if phase == "dusk":
            engine.purge_effect(self)


class Goon(Character):
    name = "Goon"
    char_type = CharType.OUTSIDER
    ability_text = (
        "Each night, the 1st player to choose you with their ability is "
        "drunk until dusk. You become their alignment."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = [
        {"name": "DRUNK", "icon": "goon_drunk.png"},
    ]

    def choose_me(
        self,
        source: "Character",
        source_player: "Player",
        engine: "Engine",
    ) -> bool:
        """``source`` actively chose this Goon's seat with a night ability.

        Apply the first-per-night gate. If open, insert
        :class:`GoonDrunkEffect` on ``source_player`` and flip the
        Goon's alignment to match ``source_player``'s.

        Returns ``True`` if the drunkening fired this call, ``False``
        if any gate short-circuited (already fired tonight, Goon
        drunk/poisoned/dead, source is the Goon themselves, not
        night, or no source player). Caller doesn't need the return —
        it's purely informational for tests / logs.

        Caller is responsible for ensuring this *is* an active player
        selection of the Goon's seat (i.e. the engine's
        ``notify_goon_chosen`` does the routing). Storyteller-driven
        targeting (Grandmother's grandchild, Sage's two-of-list)
        must NOT call this.
        """
        # Goon must actually be in play, alive, sober, healthy.
        if self.player is None or not self.player.has_ability:
            return False
        # Self-pick (Goon picking themselves via Imp self-kill, Sailor
        # picking self, etc.) doesn't fire — "first PLAYER to choose
        # YOU" implies a different player.
        if source_player is None or source_player.id == self.player.id:
            return False
        # Goon's text says "Each night..." — daytime selections do
        # not fire (Slayer / Gossip / Klutz / Moonchild).
        if not engine.phase.is_night:
            return False
        # First-per-night gate: any active GoonDrunkEffect we've
        # already emitted closes the door for the rest of the night.
        already = engine.effects_sourced_by(self)
        if any(isinstance(e, GoonDrunkEffect) for e in already):
            return False

        engine.add_effect(GoonDrunkEffect(
            source=self, targets=[source_player.id],
        ))

        # Flip alignment to match the chooser's. Pure state mutation
        # on the Goon's own seat (not an effect — intrinsic to being
        # chosen first this night, no token, no purge lifecycle).
        new_alignment = source_player.alignment
        if (
            new_alignment is not None
            and self.player.alignment != new_alignment
        ):
            old = self.player.alignment
            self.player.alignment = new_alignment
            engine.log_reaction(
                "Goon",
                (
                    f"{self.player.name}: alignment flips "
                    f"{(old.value if old else '?')} -> "
                    f"{new_alignment.value} (chosen by "
                    f"{source_player.name})."
                ),
                target=self.player,
                trigger="goon_select",
            )
            # Per the wiki: "rotate the Goon's character token… then
            # wake the Goon, give them a thumbs-up or a thumbs-down
            # (indicating their new alignment), then put the Goon to
            # sleep." Dispatch the Goon's own WAKEUP and surface an
            # alignment-reveal InformationPrompt the ST can forward
            # to the Goon player.
            #
            # ``source=self`` matches the canonical "woken due to own
            # ability" shape, so any wake-counting detector
            # (Chambermaid, future BMR roles) sees this wake-up the
            # same way it would see e.g. an Empath's nightly wake.
            engine.dispatch(
                Event(
                    EventType.WAKEUP,
                    source=self,
                    targets=[self.player],
                )
            )
            body = (
                "GOOD" if new_alignment is Alignment.GOOD else "EVIL"
            )
            thumbs = (
                "thumbs up" if new_alignment is Alignment.GOOD
                else "thumbs down"
            )
            engine.send_prompt(InformationPrompt(
                text=(
                    f"You are now {body.lower()} — "
                    f"the Storyteller will give you a {thumbs}."
                ),
                target_player_id=self.player.id,
                shown_to_player=True,
                meta={
                    "character": self.name,
                    "step": "alignment_reveal",
                    "stage": "info",
                    "new_alignment": new_alignment.value,
                    "previous_alignment": (
                        old.value if old is not None else None
                    ),
                    "chosen_by_player_id": source_player.id,
                    "render": {
                        "tokens": [{
                            "label": "YOU ARE NOW",
                            "body": body,
                        }],
                    },
                },
            ))
        else:
            engine.log_reaction(
                "Goon",
                f"{self.player.name}: drunkens {source_player.name}.",
                target=self.player,
                trigger="goon_select",
            )
        return True

    # No SELECT listener. The Goon's ability is driven exclusively by
    # ``engine.notify_goon_chosen`` calls inserted into each
    # actively-selecting character's ability code. The legacy
    # global-SELECT-listener branch was removed in PR 2 of the Goon
    # migration once every Group A single-target call site had been
    # switched to the explicit notification path. ``Character.reaction``
    # remains the inherited no-op default.
