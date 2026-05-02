"""Mastermind.

    "If the Demon dies (ending the game), play for 1 more day. If a
     player is then executed, their team loses."

When the Demon dies, the game continues for one extra day instead
of ending. The next day's outcome is driven by who (if anyone) is
executed:

  * Good player executed → evil wins (the Mastermind's victory).
  * Evil player executed → good wins.
  * Nobody executed       → good wins.

Implementation
--------------
* Listens on ``DEATH`` of a Demon while the Mastermind is alive and
  has its ability — *any* cause counts (execution, Slayer kill,
  storyteller kill, ability kill, etc.). The "by execution" wording
  in the canonical rule book is dropped here: ``DeathCause.EXECUTION``
  is reserved in this engine for kills produced by the Storyteller's
  Execute button, but the Mastermind's extension is logically about
  "the Demon would otherwise have ended the game", so any death
  qualifies. On trigger, sets the engine flag
  ``_mastermind_extension_active = True``. The engine's
  ``_check_builtin_win_conditions`` short-circuits when this flag is
  set, so the standard "Demon is dead → good wins" check does not
  fire and the day continues normally.
* On the *next* day's ``EXECUTION`` event, registers a pending win
  according to the executed player's alignment and clears the
  extension flag. Per the wiki ("whether or not they die from it"),
  we react on the EXECUTION event (which fires regardless of
  Pacifist/Tea Lady/Sailor cancellations of the actual death).
* On ``DAY_END`` during an extension day with no execution, registers
  a pending good win and clears the flag.
* Drunk/poisoned Mastermind at the moment of demon death does not
  trigger the extension — the engine's standard "demon dead → good
  wins" check fires immediately as usual.

Reminder tokens
---------------
None — the extension state is engine-internal and surfaces via the
storyteller's pending-win banner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import Alignment, CharType
from engine.event import Event, EventType

if TYPE_CHECKING:
    from engine.engine import Engine


class Mastermind(Character):
    name = "Mastermind"
    char_type = CharType.MINION
    ability_text = (
        "If the Demon dies by execution (ending the game), play for 1 "
        "more day. If a player is then executed, their team loses."
    )
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    def __init__(self, player=None) -> None:
        super().__init__(player)
        # Local mirror of engine flag — kept on the Mastermind so the
        # reaction can distinguish "extension started by *me*" from
        # "extension reset by another character_change" cleanup path.
        self._extension_active: bool = False

    def reaction(self, event: Event, engine: "Engine") -> None:
        # Demon dies (any cause) → start the extension.
        if (
            event.type is EventType.DEATH
            and event.targets
            and event.targets[0].char_type is CharType.DEMON
            and self.player is not None
            and self.player.has_ability
            and not self._extension_active
        ):
            self._extension_active = True
            engine._mastermind_extension_active = True
            engine.log_reaction(
                "Mastermind",
                (
                    f"Demon ({event.targets[0].name}) died — game "
                    f"continues 1 more day."
                ),
                target=event.targets[0],
                trigger="demon_death",
            )
            return super().reaction(event, engine)

        # During the extension day: an execution registers the win.
        if (
            event.type is EventType.EXECUTION
            and self._extension_active
            and event.targets
        ):
            target = event.targets[0]
            if target.alignment is Alignment.GOOD:
                engine._register_pending_win(
                    Alignment.EVIL,
                    "Mastermind: a good player was executed."
                )
            else:
                engine._register_pending_win(
                    Alignment.GOOD,
                    "Mastermind: an evil player was executed."
                )
            self._extension_active = False
            engine._mastermind_extension_active = False
            return super().reaction(event, engine)

        # During the extension day: dusk with no execution → good wins.
        if event.type is EventType.DAY_END and self._extension_active:
            if not engine._executed_today:
                engine._register_pending_win(
                    Alignment.GOOD,
                    "Mastermind: no execution on the extension day."
                )
                self._extension_active = False
                engine._mastermind_extension_active = False

        return super().reaction(event, engine)
