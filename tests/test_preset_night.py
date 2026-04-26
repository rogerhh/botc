"""Preset-driven night ordering + auto-advance to day.

Boots a 5-player engine with the trouble_brewing preset and walks the
engine's night sheet. Confirms that:

  * The engine emits one preset_step prompt per character or
    Dusk/Dawn entry.
  * Characters in the preset that aren't seated are skipped silently.
  * After the night sheet is exhausted, the engine auto-advances to
    DAY (because ``set_auto_advance_to_day(True)`` was called).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Phase
from engine import preset as preset_module


def make_engine() -> Engine:
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None, "trouble_brewing preset should load"
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp
    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    return e


def drain(engine: Engine, scripted: List[Tuple[dict, Any]],
          timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    answered = 0
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night didn't finish; answered={answered}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected extra prompt: {p.text!r} meta={p.meta}"
            )
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"Prompt #{answered+1} did not match: "
                    f"expected meta[{k!r}]={v!r}, got meta={p.meta}, "
                    f"text={p.text!r}"
                )
        engine.respond(p.id, response)
        answered += 1
        time.sleep(0.01)
    if answered != len(scripted):
        raise AssertionError(
            f"Night ended with {answered} answered, expected {len(scripted)}."
        )


def test_first_night_preset_order_and_auto_dawn() -> None:
    e = make_engine()
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    # In the trouble_brewing first_night.txt, 5-player non-traveler
    # count means Minion Info / Demon Info don't fire (need 7+). The
    # preset order in this game becomes:
    #
    #   Dusk → Poisoner (preset_step + character ability) → Washerwoman
    #   (preset_step + ability) → Empath (preset_step + ability) → Dawn
    #
    # Characters that aren't seated (Librarian, Investigator, Chef,
    # Fortune Teller, Butler, Spy) are silently skipped.

    e.start_night()
    drain(e, [
        # Dusk announce — storyteller-only InformationPrompt (Dawn/Dusk
        # still emit prompts because there's no follow-up ability to
        # absorb the rulebook line).
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Poisoner: no announce prompt — character steps roll the
        # rulebook description into the first ability prompt's meta
        # (engine._announce_step now dispatches a STEP_START event
        # instead of blocking the storyteller).
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Washerwoman: same — sober WW only needs select_character +
        # select_wrong_player + information.
        ({"character": "Washerwoman", "step": "select_character"}, "Empath"),
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"}, None),
        # Empath: sober + healthy → no ST count prompt; just the
        # information prompt fires.
        ({"character": "Empath", "step": "information"}, None),
        # Dawn announce.
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])

    # Auto-advance should have happened — engine is in DAY now.
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY, f"expected DAY, got {e.phase.value}"
    assert e.day_number == 1


if __name__ == "__main__":
    test_first_night_preset_order_and_auto_dawn()
    print("preset-night test passed.")
