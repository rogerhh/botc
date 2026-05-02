"""Pacifist.

    "Executed good players might not die."

The Pacifist's ability is a Storyteller-discretion save on execution
deaths. Whenever a *good* player is about to die from an
:class:`engine.enums.DeathCause.EXECUTION` death, the Storyteller is
asked whether the Pacifist's ability should trigger; on yes, the
death is cancelled by setting ``event.data["cancelled"] = True`` (the
same channel the Mayor's PRE_DEATH redirect uses).

Per the project rule on drunk/poisoned info: this ability has no info
component, so the Pacifist sees no prompt at all. The Storyteller
prompt is a binary yes/no whose default is *no* (let the execution
land) — that matches the wiki tip "Triggering the Pacifist ability
once per game is usually about right" so a passive ST hitting Next
through the day still gets the canonical behaviour. The ST may
trigger more often (or never) by saying yes / leaving the default.

The :class:`engine.engine.Engine.execute_player` method dispatches
:class:`EventType.PRE_DEATH` *before* killing the player, so this
reaction (and Tea Lady / Sailor / Fool) can cancel the death cleanly
without going through a transient ``alive=False`` round-trip. If the
death is cancelled here, the engine's execute path also skips the
``EXECUTION`` and ``DEATH`` event dispatches: the player did not die,
so reactions tied to death (Saint loss, Undertaker info, Scarlet
Woman promotion) correctly do not fire.

Drunkenness / poisoning
-----------------------
A drunk or poisoned Pacifist does not save anyone. Gated on
``self.player.has_ability`` (alive + sober + healthy).

Scoping note
------------
The wiki spells out that Pacifist saves *good* executed players. We
read the target's actual ``alignment`` here. A Recluse executed today
is alignment=GOOD even though they may register as evil for other
detection abilities — the Pacifist save is the rulebook's "is this
player on the good team?" check, not a registers_as misregistration.
A Spy is alignment=EVIL and is not eligible for the save.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import Alignment, CharType, DeathCause
from engine.event import Event, EventType
from engine.prompt import YesNoPrompt

if TYPE_CHECKING:
    from engine.engine import Engine


class Pacifist(Character):
    name = "Pacifist"
    char_type = CharType.TOWNSFOLK
    ability_text = "Executed good players might not die."
    first_night_order = 0
    other_night_order = 0
    reminder_tokens: list = []

    def reaction(self, event: Event, engine: "Engine") -> None:
        if (
            event.type is EventType.PRE_DEATH
            and self.player is not None
            and self.player.has_ability
            and event.data.get("cause") is DeathCause.EXECUTION
            and not event.data.get("cancelled")
            and not event.data.get("force")
            and event.targets
        ):
            target = event.targets[0]
            if target.alignment is Alignment.GOOD:
                ask = YesNoPrompt(
                    text=(
                        f"Save {target.name} from execution (Pacifist)?"
                    ),
                    meta={
                        "character": self.name,
                        "step": "save_yes_no",
                        "stage": "st_post",
                        "default": False,
                        "saved_player_id": target.id,
                        "saved_player_name": target.name,
                    },
                )
                save = engine.send_prompt(ask)
                if isinstance(save, bool) and save:
                    event.data["cancelled"] = True
                    event.data["cancelled_by_character"] = "Pacifist"
                    event.data["cancelled_reason"] = (
                        "Pacifist saves an executed good player"
                    )
                    engine.log_reaction(
                        "Pacifist",
                        (
                            f"{target.name} executed but remains alive "
                            f"(Pacifist)."
                        ),
                        target=target,
                        trigger="execution",
                    )
        return super().reaction(event, engine)
