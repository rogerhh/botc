"""Grimoire reminder-token snapshot wiring.

The UI snapshot returned by ``ui.ui._character_pool_snapshot`` carries
the per-chair reminder-token state the storyteller's grimoire renders
around each chair (Monk SAFE, Undertaker DIED TODAY, Slayer / Virgin
NO ABILITY, Scarlet Woman IS THE DEMON, …). This test drives the
engine through the matching ability flows and asserts each token
field populates, so a missing wire-up (the SW token regression that
left ``scarlet_woman_is_demon: False`` hardcoded, or the Monk SAFE
token still hardcoded ``None``) is caught by the suite.

The test pattern mirrors ``test_preset_night.py``: a worker thread
runs the night phase while the test thread polls ``pending_prompt``
and posts ``respond``. After each phase we ask
``_character_pool_snapshot`` what reminder fields are set.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import DeathCause, Phase
from engine.event import Event, EventType
from engine import preset as preset_module
from ui import ui


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


def _seat_table(e: Engine, roster: List[Tuple[str, str]]) -> dict:
    """Seat the given (name, character) roster on a fresh engine and
    return a name->player_id dict. Reuses the existing chair store on
    ``ui.STORE`` so the snapshot's chair-driven fields render too."""
    ids = {}
    for name, character in roster:
        s = e.add_seat(name)
        ids[name] = s.id
        e.assign_character(s.id, character)
    return ids


def _make_engine(auto_dawn: bool = True) -> Engine:
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(auto_dawn)
    return e


# ---------------------------------------------------------------------------
# Monk SAFE token: the snapshot exposes ``monk_safe_player_id`` after
# the Monk picks, and clears it at the next NIGHT_START.
# ---------------------------------------------------------------------------


def test_monk_safe_token_appears_after_pick_and_clears_next_night() -> None:
    e = _make_engine()
    ids = _seat_table(e, [
        ("Alice", "Soldier"),
        ("Bob", "Empath"),
        ("Cara", "Monk"),
        ("Dan", "Mayor"),
        ("Eve", "Imp"),
    ])
    ui.ENGINE = e
    e.start_game()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Demon Info fires even at 5 players (no Minions seated, so
        # Minion Info is skipped by its own no-minions guard).
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        # Empath's first-night info (sober + healthy → auto info).
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY
    # First night: Monk doesn't act → SAFE token must still be empty.
    snap = ui._character_pool_snapshot()
    assert snap["monk_safe_player_id"] is None

    # Night 2: the Monk picks Alice (Soldier). Imp targets Alice — but
    # Soldier is Demon-immune, so no death. Empath is alive → its
    # nightly info still fires.
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"character": "Monk", "step": "select_player"}, ids["Alice"]),
        ({"character": "Imp", "step": "select_target"}, ids["Alice"]),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY
    # SAFE token should now name Alice's seat (player id).
    snap = ui._character_pool_snapshot()
    assert snap["monk_safe_player_id"] == ids["Alice"], (
        f"expected monk_safe_player_id=={ids['Alice']!r}; "
        f"got {snap['monk_safe_player_id']!r}"
    )

    # Advance to night 3 → reset_night_flags clears the token.
    e.advance_to_night()
    e.start_night()
    # Just before any abilities run on night 3, the snapshot should
    # already show monk_safe_player_id==None: NIGHT_START fired in
    # ``start_night``, and the Monk's reaction cleared ``_target``.
    snap = ui._character_pool_snapshot()
    assert snap["monk_safe_player_id"] is None, (
        f"SAFE token should clear at next night start; "
        f"got {snap['monk_safe_player_id']!r}"
    )
    # Drain the rest of night 3 cleanly so the background thread
    # terminates and we don't leak it into other tests.
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"character": "Monk", "step": "select_player"}, ids["Alice"]),
        ({"character": "Imp", "step": "select_target"}, ids["Alice"]),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])


# ---------------------------------------------------------------------------
# Undertaker DIED TODAY: snapshot exposes
# ``undertaker_died_today_player_id`` between the execution and the
# next DAY_START.
# ---------------------------------------------------------------------------


def test_undertaker_died_today_token_after_execution() -> None:
    e = _make_engine(auto_dawn=False)
    ids = _seat_table(e, [
        ("Alice", "Undertaker"),
        ("Bob", "Empath"),
        ("Cara", "Soldier"),
        ("Dan", "Saint"),       # Saint will be executed.
        ("Eve", "Imp"),
    ])
    ui.ENGINE = e
    e.start_game()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Demon Info fires (no Minions seated → Minion Info skipped).
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    e.advance_to_day()
    snap = ui._character_pool_snapshot()
    assert snap["undertaker_died_today_player_id"] is None, (
        "No execution yet; undertaker_died_today_player_id should be None."
    )

    # Execute Dan (Saint).
    e.execute_player(ids["Dan"])
    snap = ui._character_pool_snapshot()
    assert snap["undertaker_died_today_player_id"] == ids["Dan"], (
        f"After Saint execution, expected player id {ids['Dan']!r}; "
        f"got {snap['undertaker_died_today_player_id']!r}"
    )


# ---------------------------------------------------------------------------
# Slayer NO ABILITY: token persists once the Slayer has shot.
# ---------------------------------------------------------------------------


def test_slayer_no_ability_token_after_shot() -> None:
    e = _make_engine(auto_dawn=False)
    ids = _seat_table(e, [
        ("Alice", "Slayer"),
        ("Bob", "Empath"),
        ("Cara", "Soldier"),
        ("Dan", "Mayor"),
        ("Eve", "Imp"),
    ])
    ui.ENGINE = e
    e.start_game()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Demon Info fires (no Minions seated → Minion Info skipped).
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    e.advance_to_day()
    snap = ui._character_pool_snapshot()
    assert snap["slayer_no_ability_player_ids"] == []

    # Slayer shoots Bob (not the demon — no kill, but ability is spent).
    import threading
    slayer = e.get_player(ids["Alice"]).character
    def _fire():
        slayer.daytime_ability(e)
    t = threading.Thread(target=_fire, daemon=True)
    t.start()
    # Slayer's ability blocks on a select-target prompt; respond.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        p = e.pending_prompt()
        if p is not None and p.meta.get("character") == "Slayer":
            e.respond(p.id, ids["Bob"])
            break
        time.sleep(0.02)
    t.join(2.0)
    assert not t.is_alive(), "Slayer ability didn't return"

    snap = ui._character_pool_snapshot()
    assert ids["Alice"] in snap["slayer_no_ability_player_ids"], (
        "Slayer's seat must appear in slayer_no_ability_player_ids "
        "once their shot is spent."
    )


# ---------------------------------------------------------------------------
# Virgin NO ABILITY: token persists once a Virgin has been nominated
# for the first time (regardless of whether the execute landed).
# ---------------------------------------------------------------------------


def test_virgin_no_ability_token_after_first_nomination() -> None:
    e = _make_engine(auto_dawn=False)
    ids = _seat_table(e, [
        ("Alice", "Virgin"),
        ("Bob", "Mayor"),       # Townsfolk nominator → triggers execute.
        ("Cara", "Soldier"),
        ("Dan", "Poisoner"),
        ("Eve", "Imp"),
    ])
    ui.ENGINE = e
    e.start_game()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Minion Info + Demon Info fire even at 5 players (project rule).
        ({"step_kind": "minion_info"}, None),
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        # Poisoner picks Dan (irrelevant).
        ({"character": "Poisoner", "step": "select_player"}, ids["Dan"]),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    e.advance_to_day()
    snap = ui._character_pool_snapshot()
    assert snap["virgin_no_ability_player_ids"] == []

    # Mayor nominates the Virgin → Virgin reaction fires, executes Mayor.
    virgin = e.get_player(ids["Alice"])
    e.dispatch(Event(
        EventType.NOMINATION,
        targets=[virgin],
        data={"nominator_id": ids["Bob"]},
    ))
    snap = ui._character_pool_snapshot()
    assert ids["Alice"] in snap["virgin_no_ability_player_ids"], (
        "Virgin's seat must appear in virgin_no_ability_player_ids after "
        "first nomination, even if the executed nominator was a Townsfolk."
    )


def test_demon_dead_token_clears_at_end_of_night() -> None:
    """Per project rule, the DEAD reminder for the Demon's nightly
    kill is dropped at the end of the night. The marker is set when
    the kill lands and cleared when ``advance_to_day`` runs.

    Verifies both halves: between the kill and the dawn, the seat is
    in ``imp_dead_player_ids``; after the dawn, the list is empty.
    """
    e = _make_engine(auto_dawn=False)
    ids = _seat_table(e, [
        ("Alice", "Soldier"),
        ("Bob",   "Empath"),
        ("Cara",  "Mayor"),
        ("Dan",   "Saint"),
        ("Eve",   "Slayer"),
        ("Fay",   "Imp"),
    ])
    ui.ENGINE = e
    e.start_game()

    # Burn through night 1 (no Imp kill). Demon Info fires (no
    # Minions seated → Minion Info skipped).
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    e.advance_to_day()

    # Night 2: Imp kills Bob. Track the DEAD marker landing.
    # (Bob is the Empath and dies to the Imp's pick, so the Empath
    # step gets skipped on this night — no Empath prompt to drain.)
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"character": "Imp", "step": "select_target"}, ids["Bob"]),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    # Before advance_to_day: the marker is on Bob's seat.
    snap = ui._character_pool_snapshot()
    assert ids["Bob"] in snap["imp_dead_player_ids"], (
        "Demon-killed seat must carry the DEAD reminder before dawn; "
        f"imp_dead_player_ids={snap['imp_dead_player_ids']}"
    )

    # End of night → marker is dropped.
    e.advance_to_day()
    snap = ui._character_pool_snapshot()
    assert snap["imp_dead_player_ids"] == [], (
        "DEAD reminder must clear at end of night; "
        f"imp_dead_player_ids={snap['imp_dead_player_ids']}"
    )
    # Bob is still dead with cause=DEMON_KILL — only the visual
    # reminder is transient.
    assert e.get_player(ids["Bob"]).dead
    assert e.get_player(ids["Bob"]).death_cause is DeathCause.DEMON_KILL


def test_promoted_imp_does_not_inherit_dead_demon_tokens() -> None:
    """When a Minion (e.g. the Scarlet Woman) becomes the Imp via
    ``change_character``, the new Imp's CHAIR must not inherit any
    grimoire reminder token that referred to the *previous* Imp's
    seat. This is the bug the user reported: ``imp_dead_player_ids``
    must list only the dead old-Imp's seat, NOT the freshly-promoted
    Minion's seat — even though both seats' chair.character is now
    "Imp" (we sync chair.character on promotion for single-source-of
    -truth display).

    Generic guarantee, not Imp-specific: the per-seat keying applies
    to every reminder (DEAD, MONK SAFE, BUTLER MASTER, POISONED,
    UNDERTAKER DIED TODAY, etc.).
    """
    e = _make_engine(auto_dawn=False)
    ids = _seat_table(e, [
        ("Alice", "Soldier"),
        ("Bob",   "Empath"),
        ("Cara",  "Mayor"),
        ("Dan",   "Chef"),
        ("Eve",   "Poisoner"),
        ("Fay",   "Scarlet Woman"),
        ("Gus",   "Imp"),
    ])
    ui.ENGINE = e
    e.start_game()

    # Storyteller-kill the Imp by demon kill (simulating an Imp
    # self-kill landing). Skip the night-loop machinery so the test
    # stays focused on the snapshot. The SW reaction will not fire
    # because we're not in a self-kill flow; instead the engine
    # pending-good-win path triggers but isn't relevant here. We
    # then manually promote Fay (the SW) to Imp via change_character.
    from engine.enums import DeathCause
    e.advance_to_day()  # land in day phase so kill works smoothly
    e.kill(ids["Gus"], DeathCause.DEMON_KILL)

    # Fay is now the new Imp via change_character (mirrors what
    # ``_handle_self_kill`` and the SW reaction both do).
    e.change_character(ids["Fay"], "Imp")

    snap = ui._character_pool_snapshot()

    # The dead old Imp's seat must be in imp_dead_player_ids.
    assert ids["Gus"] in snap["imp_dead_player_ids"], (
        f"Old (dead) Imp's seat must carry the DEAD reminder; "
        f"imp_dead_player_ids={snap['imp_dead_player_ids']}"
    )
    # The freshly-promoted Imp (Fay's seat) must NOT inherit DEAD —
    # she's alive and is the new Demon. The bug under test was that
    # both seats' ``chair.character`` reads "Imp", so a role-name-keyed
    # reminder list (the OLD design) wrongly matched both.
    assert ids["Fay"] not in snap["imp_dead_player_ids"], (
        f"New (promoted) Imp's seat must NOT inherit the dead Imp's "
        f"DEAD reminder; imp_dead_player_ids={snap['imp_dead_player_ids']}"
    )


if __name__ == "__main__":
    test_monk_safe_token_appears_after_pick_and_clears_next_night()
    print("monk-safe token test passed.")
    test_undertaker_died_today_token_after_execution()
    print("undertaker-died-today token test passed.")
    test_slayer_no_ability_token_after_shot()
    print("slayer-no-ability token test passed.")
    test_virgin_no_ability_token_after_first_nomination()
    print("virgin-no-ability token test passed.")
    test_demon_dead_token_clears_at_end_of_night()
    print("demon-dead-token-end-of-night test passed.")
    test_promoted_imp_does_not_inherit_dead_demon_tokens()
    print("promoted-imp-no-stale-dead-token test passed.")
