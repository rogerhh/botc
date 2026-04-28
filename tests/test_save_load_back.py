"""Save / load engine state + Back button.

The engine can serialize its full game state to a string and reload it.
After every preset-night step the engine pushes a checkpoint onto its
``_history`` list; pressing Back pops the most recent checkpoint and
restores it (interrupting a running ability if necessary).

Tests in this module:

  * ``test_save_load_roundtrip`` — serialize, mutate, restore; confirm
    state matches the pre-mutate snapshot.
  * ``test_history_grows_during_night`` — every step records a
    checkpoint as the night runs.
  * ``test_back_within_ability_redoes_selections`` — pressing Back
    while an ability is mid-prompt restarts the same ability so the ST
    can re-make their selections.
  * ``test_back_across_abilities_walks_history`` — multiple Back
    presses walk further back through completed abilities.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Phase
from engine import preset as preset_module


def _make_engine() -> Engine:
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")
    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    return e


def _wait_for_prompt(
    engine: Engine,
    *,
    timeout: float = 2.0,
    different_from: Optional[int] = None,
) -> Optional[Any]:
    """Block until a fresh prompt arrives.

    ``different_from`` should be the prompt id we just responded to;
    the helper will skip past it so callers always see the next prompt
    (no races with the night thread's own bookkeeping in
    ``send_prompt``).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = engine.pending_prompt()
        if p is not None and (different_from is None or p.id != different_from):
            return p
        if (
            (not engine._night_thread or not engine._night_thread.is_alive())
            and p is None
        ):
            return None
        time.sleep(0.005)
    return None


def _wait_for_thread_done(
    engine: Engine, *, timeout: float = 3.0
) -> None:
    if engine._night_thread is None:
        return
    engine._night_thread.join(timeout=timeout)


# ---------------------------------------------------------------------------
# 1. save / load roundtrip
# ---------------------------------------------------------------------------


def test_save_load_roundtrip_during_setup() -> None:
    """save_state -> mutate -> load_state restores the prior snapshot."""
    e = _make_engine()
    blob = e.save_state()
    assert isinstance(blob, str) and blob

    # Mutate: add a player. Should differ before/after.
    extra = e.add_seat("Faye")
    assert any(p.name == "Faye" for p in e.players)

    e.load_state(blob)
    assert not any(p.name == "Faye" for p in e.players)
    # Original players survive.
    assert {p.name for p in e.players} == {
        "Alice", "Bob", "Cara", "Dan", "Eve",
    }


def test_save_load_preserves_phase_and_night_number() -> None:
    e = _make_engine()
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT
    assert e.night_number == 1
    blob = e.save_state()

    # Now bash on the engine: kill someone, reload, confirm restore.
    alice_id = e.players[0].id
    e.kill(alice_id)
    assert not e.get_player(alice_id).alive

    e.load_state(blob)
    assert e.phase is Phase.FIRST_NIGHT
    assert e.night_number == 1
    assert e.get_player(alice_id).alive


# ---------------------------------------------------------------------------
# 2. history grows during night
# ---------------------------------------------------------------------------


def _drain_night(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    *,
    timeout: float = 4.0,
    skip_prompt_id: Optional[int] = None,
) -> None:
    """Answer scripted prompts until the night thread exits.

    ``skip_prompt_id`` lets the caller pass in the id of a prompt
    that's already been responded to but may still briefly be visible
    via ``pending_prompt`` while the night thread clears it. We skip
    that id so the next match is against the *next* prompt only.
    """
    deadline = time.time() + timeout
    answered = 0
    last_id = skip_prompt_id
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night didn't finish; answered={answered}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None or (last_id is not None and p.id == last_id):
            time.sleep(0.005)
            continue
        if answered >= len(scripted):
            raise AssertionError(f"unexpected extra prompt: {p.text!r}")
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            assert p.meta.get(k) == v, (
                f"prompt #{answered+1}: meta[{k!r}] expected {v!r}, "
                f"got {p.meta.get(k)!r} (full meta={p.meta})"
            )
        engine.respond(p.id, response)
        last_id = p.id
        answered += 1
        time.sleep(0.005)
    assert answered == len(scripted), (
        f"night ended after answering {answered}/{len(scripted)} prompts"
    )


def test_history_grows_during_night() -> None:
    e = _make_engine()
    e.start_game()
    assert e.history_size() == 0

    e.start_night()
    # After start_night, the night-start checkpoint is recorded.
    assert e.history_size() >= 1

    _drain_night(e, [
        # Dusk announce.
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Poisoner picks a player.
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Washerwoman: pick the seen TF, then the WRONG player, then info.
        ({"character": "Washerwoman", "step": "select_character"}, "Empath"),
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"}, None),
        # Empath: sober, no override prompt.
        ({"character": "Empath", "step": "information"}, None),
        # Dawn announce.
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])

    # Wait for auto-advance to day.
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.01)
    assert e.phase is Phase.DAY

    # We should have many checkpoints — one per step (Dusk, Poisoner,
    # WW, Empath, Dawn) plus the night-start checkpoint, plus skipped
    # un-seated character steps. The exact count depends on the
    # preset, so we just assert there are several.
    assert e.history_size() >= 5


# ---------------------------------------------------------------------------
# 3. Back within an ability re-runs the ability
# ---------------------------------------------------------------------------


def test_back_within_ability_redoes_selections() -> None:
    """Press Back mid-Washerwoman; the ST gets to redo their picks.

    Walks the night up to the Washerwoman's first prompt, answers it,
    then presses Back. The Washerwoman's first prompt should re-fire
    (the ST's old answer is discarded), and the storyteller then drives
    the night to completion with a *different* answer.
    """
    e = _make_engine()
    e.start_game()
    e.start_night()

    # Dusk
    p = _wait_for_prompt(e)
    assert p is not None
    assert p.meta.get("step_name") == "Dusk"
    last_id = p.id
    e.respond(p.id, None)

    # Poisoner picks Dan (seat 4)
    p = _wait_for_prompt(e, different_from=last_id)
    assert p is not None and p.meta.get("character") == "Poisoner"
    last_id = p.id
    e.respond(p.id, 4)

    # Washerwoman: first prompt is select_character (the seen TF role).
    p = _wait_for_prompt(e, different_from=last_id)
    assert p is not None
    assert p.meta.get("character") == "Washerwoman"
    assert p.meta.get("step") == "select_character"
    last_id = p.id
    # ST picks Empath, then realizes they wanted Soldier.
    e.respond(p.id, "Empath")

    # Washerwoman second prompt: select_wrong_player.
    p = _wait_for_prompt(e, different_from=last_id)
    assert p is not None and p.meta.get("step") == "select_wrong_player"

    # PRESS BACK. This aborts the running Washerwoman ability and
    # restores the post-Poisoner checkpoint, so when we resume the
    # Washerwoman's first prompt fires again.
    history_before = e.history_size()
    assert e.back() is True
    # back() pops one checkpoint when restoring.
    assert e.history_size() == history_before - 1

    # Now we should see select_character again — same ability, redone.
    p = _wait_for_prompt(e)
    assert p is not None, "expected the Washerwoman ability to restart"
    assert p.meta.get("character") == "Washerwoman"
    assert p.meta.get("step") == "select_character"
    last_id = p.id

    # This time the ST picks Soldier — different answer.
    e.respond(p.id, "Soldier")

    # Drive the rest of the night to completion.
    rest = [
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ]
    _drain_night(e, rest, skip_prompt_id=last_id)

    # Auto-advance should have flipped us into DAY.
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.01)
    assert e.phase is Phase.DAY


# ---------------------------------------------------------------------------
# 4. Back across abilities walks history
# ---------------------------------------------------------------------------


def test_back_across_abilities_walks_history() -> None:
    """Two consecutive Backs walk further back through the night."""
    e = _make_engine()
    e.start_game()
    e.start_night()

    # Dusk
    p = _wait_for_prompt(e)
    last_id = p.id
    e.respond(p.id, None)

    # Poisoner: pick Dan
    p = _wait_for_prompt(e, different_from=last_id)
    assert p is not None and p.meta.get("character") == "Poisoner"
    last_id = p.id
    e.respond(p.id, 4)

    # Washerwoman select_character
    p = _wait_for_prompt(e, different_from=last_id)
    assert p is not None and p.meta.get("character") == "Washerwoman"
    last_id = p.id
    e.respond(p.id, "Empath")

    # We've now completed Dusk + Poisoner. Currently inside
    # Washerwoman. Press Back — re-runs Washerwoman.
    assert e.back() is True
    p = _wait_for_prompt(e)
    assert p is not None
    assert p.meta.get("character") == "Washerwoman"
    assert p.meta.get("step") == "select_character"

    # Press Back AGAIN — should rewind to the Poisoner step.
    assert e.back() is True
    p = _wait_for_prompt(e)
    assert p is not None
    assert p.meta.get("character") == "Poisoner"
    last_id = p.id

    # Re-answer Poisoner with a different target this time.
    e.respond(p.id, 5)

    # Drive the rest of the night to completion with the redone state.
    rest = [
        ({"character": "Washerwoman", "step": "select_character"}, "Empath"),
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ]
    _drain_night(e, rest, skip_prompt_id=last_id)

    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.01)
    assert e.phase is Phase.DAY

    # Confirm the redone Poisoner targeted seat 5 (Eve / Imp), not 4.
    eve = next(p for p in e.players if p.name == "Eve")
    dan = next(p for p in e.players if p.name == "Dan")
    assert eve.poisoned, "Poisoner's redone target should be poisoned"
    assert not dan.poisoned, "old Poisoner target should no longer be poisoned"


def test_back_returns_false_when_history_empty() -> None:
    e = _make_engine()
    assert e.history_size() == 0
    assert e.back() is False


if __name__ == "__main__":
    test_save_load_roundtrip_during_setup()
    test_save_load_preserves_phase_and_night_number()
    test_history_grows_during_night()
    test_back_within_ability_redoes_selections()
    test_back_across_abilities_walks_history()
    test_back_returns_false_when_history_empty()
    print("save/load + back tests passed.")
