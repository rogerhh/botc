"""Spy.

    "Each night, you see the Grimoire. You might register as good and
     as a Townsfolk or Outsider, even if dead."

Three pieces:

  * **Grimoire reveal.** The Spy is woken every night and shown the
    full grimoire. We model this as a ``ShowInformation``-style prompt
    whose payload is the engine's storyteller-view snapshot. The Spy's
    phone renders the grimoire so they can study it.

  * **Misregistration.** The Spy may register as good and as any
    Townsfolk or Outsider, even when dead. Whenever a detection-style
    ability would observe the Spy, the engine surfaces a
    :class:`SelectCharacterPrompt` to the Storyteller asking what the
    Spy registers as for that ability. The list is every good character
    on the script plus the literal ``Spy`` option (to register as
    themselves — evil). The default is the Spy's internally-tracked
    "preferred good character" (a random good role currently *not* in
    play, picked at setup), so a Storyteller who hits Next on every
    such prompt gets a consistent registration across the night and
    across nights — but can pick anything else for any given ability.

    The Investigator is the one detector that does NOT prompt: per the
    user's project rules the Investigator always sees the Spy as the
    Spy. The Fortune Teller likewise does not prompt because the Spy
    cannot register as a Demon under the rules wording (Townsfolk or
    Outsider only) — the FT result from a chosen Spy is always NO
    regardless of choice, so a prompt would be pure busywork.

  * **Preferred good character.** Picked once at setup (and refreshed
    if it ever lands on a now-in-play role). Never displayed to the
    Storyteller as a state value — it's only the *default* offered on
    each per-ability registration prompt. Per project rules, this is
    not an official game state; it's purely an ergonomic seed for the
    Storyteller.

Drunkenness / poisoning: a drunk or poisoned Spy still sees the
grimoire (the rule isn't really an "ability", it's just exposure).
We pass through the prompt regardless. Misregistration is always a
Storyteller call regardless of the Spy's drunk/poisoned state.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from engine.character import Character
from engine.enums import CharType, SetupMode
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player

class Spy(Character):
    name = "Spy"
    char_type = CharType.MINION
    ability_text = (
        "Each night, you see the Grimoire. You might register as good "
        "and as a Townsfolk or Outsider, even if dead."
    )
    first_night_order = 40
    other_night_order = 60
    reminder_tokens: list = []

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Internally-tracked default registration for the per-ability
        # misregistration prompt. Picked once at setup (a random good
        # character currently NOT in play) and refreshed lazily if it
        # ever turns out to be in play. Never displayed to the
        # storyteller as a state — it only seeds the *default* of each
        # registration prompt so a storyteller can hit Next for a
        # consistent across-the-night Spy character. Per project rules
        # this is not part of the official game state.
        self._preferred_good_character: Optional[str] = None

    # ------------------------------------------------------------------
    # Setup.
    # ------------------------------------------------------------------

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        """Mode-aware on-setup ability.

        Both ``SETUP_PHASE`` and ``IN_GAME`` modes do the same thing:
        seed the Spy's ``_preferred_good_character`` with a random
        good role that isn't currently in play. No Storyteller prompt;
        the field is internal.
        """
        if self.player is None:
            return
        self._refresh_preferred_good_character(engine)

    def _refresh_preferred_good_character(self, engine: "Engine") -> None:
        """Re-roll the preferred good character if needed.

        Picks a random Townsfolk-or-Outsider role on the script that
        is *not* currently in play. If every good role is in play
        (degenerate large-table case), falls back to any good role on
        the script.

        Cheaply idempotent: keeps the existing pick if it is still a
        good role not in play. Otherwise re-rolls.
        """
        in_play = set(engine.in_play_character_names())
        good_names = (
            engine.all_character_names_by_type(CharType.TOWNSFOLK)
            + engine.all_character_names_by_type(CharType.OUTSIDER)
        )
        # If the existing pick is still good and still not-in-play,
        # keep it.
        if (
            self._preferred_good_character
            and self._preferred_good_character in good_names
            and self._preferred_good_character not in in_play
        ):
            return
        not_in_play = [n for n in good_names if n not in in_play]
        pool = not_in_play or list(good_names)
        if pool:
            self._preferred_good_character = _rand.choice(pool)

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return
        # Refresh the default registration each night in case roles
        # have moved in / out of play.
        self._refresh_preferred_good_character(engine)
        engine.dispatch(
            Event(EventType.CHECK_CONDITION, source=self, targets=[self.player])
        )

        # WAKEUP — engine-internal event, no separate ST prompt.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # Show the Spy the grimoire. We pack the full snapshot into the
        # prompt's meta so the Spy's phone can render it. This is the
        # *one* place where the mobile UI legitimately sees other
        # players' character tokens — see ui/README.md "Information
        # hiding rules".
        snapshot = engine.snapshot()
        engine.send_prompt(
            InformationPrompt(
                text="GRIMOIRE",
                target_player_id=self.player.id,
                shown_to_player=True,
                meta={
                    "character": self.name,
                    "step": "grimoire",
                    "stage": "info",
                    "grimoire": snapshot,
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=[self.player],
                data={"info": "Spy saw the grimoire.", "grimoire": snapshot},
            )
        )

        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=[self.player])
        )


# ------------------------------------------------------------------
# Helpers for detection-side characters (Empath, Chef, Undertaker, …).
# ------------------------------------------------------------------

def find_spy_player(engine: "Engine") -> Optional["Player"]:
    """Return the seated player whose character is the Spy, if any.

    Returns ``None`` when no Spy is in play. Used by detection-side
    characters to decide whether a misregistration prompt is needed.
    """
    for p in engine.players:
        if p.character is not None and p.character.name == Spy.name:
            return p
    return None


def prompt_spy_register_as(
    engine: "Engine",
    spy_player: "Player",
    *,
    detector_name: str,
    detector_player_id: Optional[int] = None,
    categories: Sequence[str] = ("townsfolk", "outsider", "spy"),
    text: str = "Spy registers as",
    stage: str = "st_pre",
    extra_meta: Optional[dict] = None,
) -> str:
    """Ask the Storyteller what the Spy registers as for an ability.

    Eligible categories restrict the choices:

      * ``"townsfolk"`` / ``"outsider"`` — every role of that type on
        the script is offered (good registration).
      * ``"spy"`` — the literal ``Spy`` option, meaning "register as
        themselves" (evil, Minion).

    The Washerwoman uses ``("townsfolk", "spy")``; the Librarian uses
    ``("outsider", "spy")``; everything else uses the full set.

    The default is the Spy's internally-tracked
    ``_preferred_good_character`` (when a good category is allowed and
    the preferred role is in the eligible list); otherwise the literal
    ``Spy`` option; otherwise the first eligible character.

    Returns the chosen character name. ``"Spy"`` means the Spy
    registered as themselves (evil); any other name means good.
    """
    spy_char = spy_player.character if spy_player else None

    eligible: list = []
    if "townsfolk" in categories:
        eligible.extend(
            engine.all_character_names_by_type(CharType.TOWNSFOLK)
        )
    if "outsider" in categories:
        eligible.extend(
            engine.all_character_names_by_type(CharType.OUTSIDER)
        )
    if "spy" in categories:
        eligible.append(Spy.name)
    # De-duplicate while preserving a stable sorted-ish order: good
    # roles in script order, with Spy at the end.
    seen: set = set()
    deduped: list = []
    for n in eligible:
        if n not in seen:
            seen.add(n)
            deduped.append(n)
    eligible = deduped

    # Default: preferred good character (when a good category is
    # allowed and the preferred role is eligible). Else "Spy" if
    # offered. Else the first eligible name.
    default: Optional[str] = None
    if isinstance(spy_char, Spy):
        pref = spy_char._preferred_good_character
        if pref and pref in eligible:
            default = pref
    if default is None:
        if Spy.name in eligible:
            default = Spy.name
        elif eligible:
            default = eligible[0]

    meta: dict = {
        "character": detector_name,
        "step": "spy_registers_as",
        "stage": stage,
        "default": default,
        "spy_player_id": spy_player.id if spy_player else None,
        "spy_player_name": spy_player.name if spy_player else None,
    }
    if extra_meta:
        meta.update(extra_meta)

    target_id = (
        detector_player_id if detector_player_id is not None
        else (spy_player.id if spy_player else None)
    )
    prompt = SelectCharacterPrompt(
        text=text,
        eligible_characters=eligible,
        target_player_id=target_id,
        meta=meta,
    )
    chosen = engine.send_prompt(prompt)
    if not isinstance(chosen, str) or chosen not in eligible:
        chosen = default if default else Spy.name
    return chosen


def spy_registers_as_evil(register_as: str) -> bool:
    """True iff the Spy is registering as themselves (evil/Minion).

    Convenience predicate: anything other than the literal ``"Spy"``
    is a good (Townsfolk/Outsider) registration.
    """
    return register_as == Spy.name
