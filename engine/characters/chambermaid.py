"""Chambermaid.

    "Each night, choose 2 alive players (not yourself): you learn how
     many woke tonight due to their ability."

Bad Moon Rising info character. Each night, the Chambermaid picks two
*alive* players (not herself) and learns the number — 0, 1, or 2 — of
those two who woke tonight *to use their own ability*.

What counts as a wake-up
------------------------
Per the rulebook: only wake-ups that fired because the woken player's
own character ability triggered count. The official examples spell out
what is *excluded*:

  * Demon Info / Minion Info (storyteller wakes the Demon or Minions
    on the first night to deliver the team & bluff info) — the engine
    dispatches these ``WAKEUP`` events with ``source=None``.
  * "You are the new Demon" reveals on Imp self-kill — dispatched with
    ``source=<old Imp>`` but ``targets=[<new Imp player>]``, so the
    seated player is woken *due to a different character's ability*.
  * Any ability that wakes another player to deliver information.

The discriminator is therefore: the woken player's own ability fired
iff the ``WAKEUP`` event has ``source is not None`` AND
``source.player.id == woken_player.id``. Every concrete character in
the engine's character roster dispatches its own wake-up event with
that exact shape (``source=self, targets=[self.player]``); engine-
level wake-ups for Demon / Minion Info, and cross-player wake-ups,
fail this check naturally.

The Chambermaid's :meth:`reaction` watches every dispatched event and
records the seat IDs that pass the test on the current night. The set
is reset at ``NIGHT_START`` so each night is a clean slate.

Drunkenness / poisoning (per CLAUDE.md)
---------------------------------------
The shown count is a 3-option range (0/1/2), so when the Chambermaid
is drunk or poisoned the engine pre-picks a *random wrong* number and
surfaces it to the Storyteller with a Next button. The ST may change
the number before it goes to the player. No "confirm" / "override"
language anywhere.

Scalability
-----------
Nothing in this module hardcodes which characters wake. The wake-up
detector is a single rule on the ``Event``; any new character that
follows the engine's ``WAKEUP`` convention (``source=self,
targets=[self.player]``) is detected automatically. Engine-level
wake-ups (Demon / Minion Info) and cross-player wake-ups (Imp self-
kill, etc.) are excluded by the same rule, also without any character
name knowledge.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, List, Optional, Set

from engine.character import Character
from engine.enums import CharType
from engine.event import Event, EventType
from engine.prompt import (
    InformationPrompt,
    SelectCharacterPrompt,
    SelectPlayerPrompt,
)

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player


class Chambermaid(Character):
    name = "Chambermaid"
    char_type = CharType.TOWNSFOLK
    ability_text = (
        "Each night, choose 2 alive players (not yourself): you learn "
        "how many woke tonight due to their ability."
    )
    # Bad Moon Rising late-night info step — the Chambermaid is the
    # last character action before Dawn so she sees every wake-up that
    # has fired on the night. ``script.py`` is the canonical source.
    first_night_order = 38
    other_night_order = 55
    reminder_tokens: list = []

    def __init__(self, player: Optional["Player"] = None) -> None:
        super().__init__(player)
        # Seat IDs that have woken THIS night for their own ability.
        # Reset at every NIGHT_START.
        self._woke_tonight: Set[int] = set()

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    @staticmethod
    def _wake_is_due_to_own_ability(event: "Event") -> Optional[int]:
        """Return the woken seat's player ID iff this WAKEUP qualifies.

        A wake-up "counts" for the Chambermaid iff the woken player is
        being woken to use *their own* character ability. The engine's
        ``WAKEUP`` dispatch convention is ``source=self,
        targets=[self.player]`` — i.e. the source character's seated
        player matches the targeted player. Engine-level wake-ups
        (Demon / Minion Info) use ``source=None``; cross-player wakes
        (Imp self-kill promoting a Minion) use a source whose seated
        player differs from the target. Both fail this test and are
        excluded.

        Returns the qualifying player ID, or ``None`` if the event
        doesn't represent a "wake to use own ability".
        """
        if event.type is not EventType.WAKEUP:
            return None
        src = event.source
        if src is None:
            return None
        src_player = getattr(src, "player", None)
        if src_player is None:
            return None
        if not event.targets:
            return None
        target = event.targets[0]
        if target is None:
            return None
        if getattr(target, "id", None) != getattr(src_player, "id", None):
            return None
        return target.id

    def reaction(self, event: "Event", engine: "Engine") -> None:
        # Reset the per-night tracker the moment the night begins, so
        # any wake-ups dispatched during this night land in a clean
        # set. (The Chambermaid herself is normally the last actor of
        # the night, so she reads from this set when her own ability
        # fires later in the same dispatch sequence.)
        if event.type is EventType.NIGHT_START:
            self._woke_tonight = set()
            return super().reaction(event, engine)

        woke_id = self._wake_is_due_to_own_ability(event)
        if woke_id is not None:
            self._woke_tonight.add(woke_id)

        return super().reaction(event, engine)

    # ------------------------------------------------------------------
    # Activation gate.
    # ------------------------------------------------------------------

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        """Skip the wake-up entirely if there are not 2 valid targets.

        Per the rulebook: "Do not wake the Chambermaid if there are
        not two players alive to be chosen." The Chambermaid herself
        is excluded from the two — she cannot pick herself — so the
        gate requires *two other* alive players besides her.
        """
        if not super().would_act_tonight(engine, night_number):
            return False
        if self.player is None:
            return False
        other_alive = [
            p for p in engine.players
            if p.alive and p.id != self.player.id
        ]
        return len(other_alive) >= 2

    # ------------------------------------------------------------------
    # Nightly ability.
    # ------------------------------------------------------------------

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return

        # CHECK_CONDITION: announces that the ability is firing tonight.
        engine.dispatch(
            Event(
                EventType.CHECK_CONDITION,
                source=self,
                targets=[self.player],
            )
        )

        is_drunk_or_poisoned = self.player.drunk or self.player.poisoned

        # WAKEUP — engine-internal event, no separate ST prompt. The
        # storyteller-facing wake-up line is shown as part of the
        # SelectPlayerPrompt panel.
        engine.dispatch(
            Event(EventType.WAKEUP, source=self, targets=[self.player])
        )

        # SELECT: 2 alive players, not self. ``allow_self=False`` plus a
        # tightened ``eligible_player_ids`` matches the rulebook.
        eligible = [
            p.id for p in engine.players
            if p.alive and p.id != self.player.id
        ]
        # ``would_act_tonight`` already guards ``len(eligible) >= 2``;
        # this is a defensive guard for direct callers (tests, replays).
        if len(eligible) < 2:
            return

        sel = SelectPlayerPrompt(
            text="Chambermaid picks 2 alive players (not yourself)",
            count=2,
            eligible_player_ids=eligible,
            allow_self=False,
            allow_randomize=False,  # player decision (Chambermaid picks)
            target_player_id=self.player.id,
            meta={
                "character": self.name,
                "step": "select_players",
                "stage": "player",
            },
        )
        chosen_resp = engine.send_prompt(sel)
        if isinstance(chosen_resp, int):
            chosen_ids: List[int] = [chosen_resp]
        elif isinstance(chosen_resp, list):
            chosen_ids = [int(x) for x in chosen_resp]
        else:
            chosen_ids = []
        chosen_players: List["Player"] = []
        for pid in chosen_ids:
            try:
                chosen_players.append(engine.get_player(int(pid)))
            except (KeyError, ValueError, TypeError):
                continue

        engine.dispatch(
            Event(EventType.SELECT, source=self, targets=chosen_players)
        )

        # Compute the truthful count: how many of the picked seats woke
        # tonight to use their own ability. Reads from the tracker
        # populated by :meth:`reaction` since NIGHT_START.
        default_count = sum(
            1 for p in chosen_players
            if p is not None and p.id in self._woke_tonight
        )

        # Sober + healthy: trust the computed count, no ST prompt.
        # Drunk/poisoned: pre-pick a random wrong count from {0,1,2}\\{default}
        # and surface it to the ST with a Next button; the ST may change
        # the count before it goes to the player.
        if is_drunk_or_poisoned:
            wrong_options = [c for c in (0, 1, 2) if c != default_count]
            default_wrong = (
                _rand.choice(wrong_options)
                if wrong_options else default_count
            )
            wrong_prompt = SelectCharacterPrompt(
                text="Count to show",
                eligible_characters=["0", "1", "2"],
                target_player_id=self.player.id,
                meta={
                    "character": self.name,
                    "step": "select_count",
                    "stage": "st_post",
                    "due_to_drunk_poison": True,
                    "drunk_poison_state": self.player.drunk_poison_label(),
                    "default": str(default_wrong),
                    "correct": str(default_count),
                    "selected_player_ids": [p.id for p in chosen_players],
                },
            )
            chosen_count_resp = engine.send_prompt(wrong_prompt)
            try:
                shown = int(chosen_count_resp)
            except (TypeError, ValueError):
                shown = default_wrong
            # Clamp into the valid range — defensive against malformed
            # ST responses.
            shown = max(0, min(2, shown))
        else:
            shown = default_count

        # INFORMATION — what the player's phone displays.
        if shown == 1:
            info_text = (
                "1 of your chosen players woke tonight due to their ability."
            )
        else:
            info_text = (
                f"{shown} of your chosen players woke tonight due to "
                f"their ability."
            )
        engine.send_prompt(
            InformationPrompt(
                text=info_text,
                target_player_id=self.player.id,
                shown_to_player=True,
                highlight_player_ids=[p.id for p in chosen_players],
                meta={
                    "character": self.name,
                    "step": "information",
                    "stage": "info",
                    # Player phone shows just the explanation sentence —
                    # ``label=""`` tells the renderer to skip the
                    # big-digit slot. The Storyteller still shows
                    # fingers in person; the digit doesn't need to be
                    # repeated on the player's screen.
                    "render": {
                        "tokens": [{"label": "", "body": info_text}],
                    },
                },
            )
        )
        engine.dispatch(
            Event(
                EventType.INFORMATION,
                source=self,
                targets=chosen_players,
                data={"info": info_text, "count": shown},
            )
        )

        # Pure information ability — no game-state mutation.
        engine.dispatch(
            Event(EventType.RESOLUTION, source=self, targets=chosen_players)
        )
