"""Demon Info ST input stage 1.

Verifies the new prompt flow on the first night, 7+ players:

  1. The engine emits a ST input stage 1 (``stage="st_pre"``) prompt
     of type ``select_character`` with ``count=3`` carrying three
     pre-filled "not in play" good roles to bluff as.
  2. The Storyteller may swap any of the picks (or just hit Next).
  3. The engine then dispatches a WAKEUP event and emits the
     ``InformationPrompt`` (``stage="info"``, ``shown_to_player``)
     carrying the (possibly Storyteller-edited) bluffs.

This exercises the full "ST input stage 1 → wake up → show this to
player" flow that all six-section panels now follow.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import CharType, Phase
from engine import preset as preset_module


def _make_engine() -> Engine:
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)

    # 7-player Trouble Brewing setup so Demon Info actually fires
    # (the threshold is 7+ non-traveler players).
    a = e.add_seat("Alice")     # Washerwoman
    b = e.add_seat("Bob")       # Librarian
    c = e.add_seat("Cara")      # Investigator
    d = e.add_seat("Dan")       # Chef
    f = e.add_seat("Eve")       # Empath
    g = e.add_seat("Finn")      # Poisoner
    h = e.add_seat("Gail")      # Imp
    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Librarian")
    e.assign_character(c.id, "Investigator")
    e.assign_character(d.id, "Chef")
    e.assign_character(f.id, "Empath")
    e.assign_character(g.id, "Poisoner")
    e.assign_character(h.id, "Imp")
    return e


def _respond_and_settle(engine: Engine, prompt_id: int, response: Any,
                        timeout: float = 2.0) -> None:
    """Send a response and wait for the engine to consume it.

    Calling ``engine.respond`` only signals the night thread; the
    pending prompt isn't cleared until the night thread wakes up,
    reads the response, and (typically) blocks on the *next* prompt.
    Without an explicit wait, a follow-up call to
    ``engine.pending_prompt()`` can race and observe the *just-
    answered* prompt rather than the next one. This helper waits
    until the pending prompt id changes (or goes None) so the caller
    sees a clean handoff.
    """
    assert engine.respond(prompt_id, response), (
        f"engine.respond rejected prompt id {prompt_id}"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = engine.pending_prompt()
        if cur is None or cur.id != prompt_id:
            return
        time.sleep(0.005)
    raise TimeoutError(
        f"Engine did not move past prompt id {prompt_id} within {timeout}s"
    )


def _drain_until(
    engine: Engine,
    matcher: dict,
    timeout: float = 5.0,
) -> Any:
    """Answer prompts in sequence with ``None`` until one matches.

    Returns the matched prompt (without responding to it). Raises if
    the night thread dies before the prompt arrives.
    """
    deadline = time.time() + timeout
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Timed out waiting for prompt matching {matcher!r}; "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        # Check match.
        ok = True
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                ok = False
                break
        if ok:
            return p
        # Otherwise advance with a sensible default.
        if p.type == "select_player" or p.type == "select_players":
            eligible = list(p.eligible_player_ids or [])
            count = p.count or 1
            response: Any = (
                eligible[0] if count == 1 and eligible else eligible[:count]
            )
        elif p.type == "select_character":
            count = getattr(p, "count", 1) or 1
            default = (p.meta or {}).get("default")
            if count > 1:
                response = list(default) if isinstance(default, list) else (
                    list(p.eligible_characters)[:count]
                )
            else:
                response = (
                    default
                    if isinstance(default, str) and default
                    else (
                        p.eligible_characters[0]
                        if p.eligible_characters else None
                    )
                )
        elif p.type == "yes_no":
            response = bool((p.meta or {}).get("default", False))
        else:
            response = None
        # Use the settle helper so the pending-prompt observed on the
        # next loop iteration is genuinely the *next* prompt — without
        # this, the engine has only just been signalled and the same
        # prompt id can show up again.
        _respond_and_settle(engine, p.id, response)
    raise AssertionError(
        f"Night ended before a prompt matched {matcher!r}."
    )


def test_demon_info_emits_st_pre_then_info() -> None:
    e = _make_engine()
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    # Find the Demon (Imp) seat — the bluff-info prompt is targeted at
    # that player.
    demon = next(p for p in e.players if p.char_type is CharType.DEMON)

    e.start_night()

    # First: ST input stage 1 — the multi-character bluff selection.
    p1 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "st_pre",
        "character": "Demon",
    })
    assert p1.type == "select_character", (
        f"expected select_character, got {p1.type}"
    )
    assert getattr(p1, "count", 1) == 3, (
        f"expected count=3, got count={getattr(p1, 'count', 1)}"
    )
    assert p1.target_player_id == demon.id
    default = (p1.meta or {}).get("default")
    assert isinstance(default, list) and len(default) == 3, (
        f"expected 3 pre-filled bluffs, got default={default!r}"
    )
    # All three pre-filled bluffs should be non-in-play roles, i.e.
    # not held by any seated character.
    in_play = {
        pl.character.name for pl in e.players if pl.character is not None
    }
    for name in default:
        assert name not in in_play, (
            f"pre-filled bluff {name!r} is actually in play"
        )

    # Storyteller hits Next without changing the picks: respond with
    # the default list.
    _respond_and_settle(e, p1.id, list(default))

    # Next: the InformationPrompt (stage="info") with the chosen
    # bluffs. ``_drain_until`` advances past any other prompts (none
    # are expected between st_pre and info for the Demon).
    p2 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "info",
        "character": "Demon",
    })
    assert p2.type == "information"
    assert p2.shown_to_player is True
    assert p2.target_player_id == demon.id
    # The info prompt's bluff_characters meta should reflect the ST's
    # confirmed picks (which here equal the engine's defaults).
    assert list(p2.meta.get("bluff_characters", [])) == list(default)
    # Highlighted character tokens on the player's display match.
    assert list(p2.highlight_characters) == list(default)
    # Acknowledge the info to let the night unwind.
    _respond_and_settle(e, p2.id, None)


def test_demon_info_st_can_change_bluffs() -> None:
    """Storyteller swaps one of the pre-filled bluffs; the new pick
    flows through to the information prompt the Demon sees."""
    e = _make_engine()
    e.start_game()
    e.start_night()

    p1 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "st_pre",
        "character": "Demon",
    })
    default = list((p1.meta or {}).get("default") or [])
    assert len(default) == 3

    # Build a new triple by swapping one slot for a different
    # not-in-play eligible character.
    eligible = list(p1.eligible_characters)
    in_play = {
        pl.character.name for pl in e.players if pl.character is not None
    }
    swap_in = next(
        (c for c in eligible if c not in default and c not in in_play),
        None,
    )
    assert swap_in is not None, (
        "test setup expected at least 4 not-in-play roles available"
    )
    new_picks = [swap_in] + default[1:]
    _respond_and_settle(e, p1.id, new_picks)

    p2 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "info",
        "character": "Demon",
    })
    assert list(p2.meta.get("bluff_characters", [])) == new_picks
    assert list(p2.highlight_characters) == new_picks
    e.respond(p2.id, None)


def _make_engine_no_greater_joy() -> Engine:
    """7-player ``no_greater_joy`` setup so Demon Info fires.

    No Greater Joy is a deliberately *different* roster from Trouble
    Brewing — Clockmaker / Artist / Sage / Empath / Chambermaid /
    Investigator / Drunk / Klutz / Scarlet Woman / Baron / Imp — so
    asserting the Demon Info bluff pool against this script's
    townsfolk + outsider names catches the regression where the
    engine fell back to the hard-coded Trouble Brewing list (in which
    case the eligible pool would still contain Washerwoman, Slayer,
    Saint, Recluse, etc. — none of which are on this script).
    """
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "no_greater_joy"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)

    # Use 4 Townsfolk + 2 Minions + 1 Demon so the not-in-play good
    # pool is 8 - 4 = 4 roles. The bluff prompt has count=3 against
    # 4 eligible options, which side-steps the engine's "single
    # forced answer" auto-resolve and lets the test inspect the
    # prompt's eligible_characters directly.
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")
    g = e.add_seat("Finn")
    h = e.add_seat("Gail")
    e.assign_character(a.id, "Clockmaker")
    e.assign_character(b.id, "Investigator")
    e.assign_character(c.id, "Empath")
    e.assign_character(d.id, "Chambermaid")
    e.assign_character(f.id, "Scarlet Woman")
    e.assign_character(g.id, "Baron")
    e.assign_character(h.id, "Imp")
    return e


def test_demon_info_bluff_pool_is_scoped_to_preset_roster() -> None:
    """Regression: the Demon Info bluff pool must come from the active
    preset's ``characters.csv`` roster, not from the global Trouble
    Brewing list. On No Greater Joy, the eligible bluffs are exactly
    the script's not-in-play Townsfolk + Outsiders, and the pre-filled
    defaults are drawn from that same pool.
    """
    e = _make_engine_no_greater_joy()
    e.start_game()
    e.start_night()

    p1 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "st_pre",
        "character": "Demon",
    })

    # Build the *expected* eligible pool straight from the preset's
    # roster: Townsfolk + Outsiders minus everyone seated.
    in_play = {
        pl.character.name for pl in e.players if pl.character is not None
    }
    expected_pool = {
        n for n in (
            e.preset.names_by_type(CharType.TOWNSFOLK)
            + e.preset.names_by_type(CharType.OUTSIDER)
        )
        if n not in in_play
    }
    eligible = set(p1.eligible_characters)

    assert eligible == expected_pool, (
        f"bluff pool {eligible!r} != preset-derived pool {expected_pool!r}"
    )

    # And explicitly: every Trouble Brewing-only role that is *not*
    # on the No Greater Joy script must be absent from the pool.
    tb_only = {
        "Washerwoman", "Librarian", "Chef", "Fortune Teller",
        "Undertaker", "Monk", "Ravenkeeper", "Virgin", "Slayer",
        "Soldier", "Mayor", "Butler", "Recluse", "Saint",
        "Poisoner", "Spy",
    }
    leaks = eligible & tb_only
    assert not leaks, (
        f"bluff pool leaked Trouble-Brewing-only roles not on this "
        f"script: {sorted(leaks)!r}"
    )

    # Defaults must also live inside the preset-derived pool.
    default = list((p1.meta or {}).get("default") or [])
    assert default, "expected pre-filled defaults"
    for name in default:
        assert name in expected_pool, (
            f"default bluff {name!r} is not in the preset bluff pool"
        )

    # Hit Next without changing the picks; the info prompt should
    # carry exactly the same names.
    _respond_and_settle(e, p1.id, list(default))
    p2 = _drain_until(e, {
        "step_kind": "demon_info",
        "stage": "info",
        "character": "Demon",
    })
    assert list(p2.meta.get("bluff_characters", [])) == list(default)
    _respond_and_settle(e, p2.id, None)


def _make_small_engine(player_count: int) -> Engine:
    """Trouble Brewing setup at 5 or 6 non-traveler players.

    The roster matches the canonical recommended counts (5p: 3T/0O/1M/1D,
    6p: 3T/1O/1M/1D). Both sit below the 7-player threshold at which
    Minion Info and Demon Info fire, so this engine should walk the
    night without surfacing either of those steps.
    """
    assert player_count in (5, 6)
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)

    seats = []
    for nm in ("Alice", "Bob", "Cara", "Dan", "Eve", "Finn")[:player_count]:
        seats.append(e.add_seat(nm))
    if player_count == 5:
        # 3 TF + 1 Minion + 1 Demon
        chars = ["Washerwoman", "Investigator", "Chef", "Poisoner", "Imp"]
    else:
        # 3 TF + 1 Outsider + 1 Minion + 1 Demon
        chars = ["Washerwoman", "Investigator", "Chef",
                 "Saint", "Poisoner", "Imp"]
    for seat, ch in zip(seats, chars):
        e.assign_character(seat.id, ch)
    return e


def _drain_to_dawn_collecting_step_kinds(
    engine: Engine,
    timeout: float = 5.0,
) -> List[str]:
    """Walk the night to dawn, recording every prompt's ``step_kind``.

    Answers each prompt with a sensible default (mirroring
    :func:`_drain_until`) so the night thread runs to completion.
    Returns the list of step_kinds observed across the whole night.
    """
    seen: List[str] = []
    deadline = time.time() + timeout
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night didn't finish in {timeout}s; "
                f"pending={engine.pending_prompt()}, seen={seen!r}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        kind = (p.meta or {}).get("step_kind")
        if isinstance(kind, str):
            seen.append(kind)
        # Pick a plausible response so the engine moves forward.
        if p.type == "select_player" or p.type == "select_players":
            eligible = list(p.eligible_player_ids or [])
            count = p.count or 1
            response: Any = (
                eligible[0] if count == 1 and eligible else eligible[:count]
            )
        elif p.type == "select_character":
            count = getattr(p, "count", 1) or 1
            default = (p.meta or {}).get("default")
            if count > 1:
                response = list(default) if isinstance(default, list) else (
                    list(p.eligible_characters)[:count]
                )
            else:
                response = (
                    default
                    if isinstance(default, str) and default
                    else (
                        p.eligible_characters[0]
                        if p.eligible_characters else None
                    )
                )
        elif p.type == "yes_no":
            response = bool((p.meta or {}).get("default", False))
        else:
            response = None
        _respond_and_settle(engine, p.id, response)
    return seen


def test_minion_and_demon_info_fire_at_five_players() -> None:
    """5-player Trouble Brewing: BOTH Minion Info and Demon Info
    must fire on the first night.

    Project rule (deliberately diverging from the canonical Trouble
    Brewing rulebook, which gates these steps at 7+): Minion Info
    and Demon Info always run, regardless of player count, so
    Teensyville games still get the reveal and the bluff list. This
    test guards that contract.
    """
    e = _make_small_engine(5)
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT
    e.start_night()
    seen = _drain_to_dawn_collecting_step_kinds(e)
    assert "minion_info" in seen, (
        f"Minion Info did not fire in a 5-player game; "
        f"saw step_kinds={seen!r}"
    )
    assert "demon_info" in seen, (
        f"Demon Info did not fire in a 5-player game; "
        f"saw step_kinds={seen!r}"
    )


def test_minion_and_demon_info_fire_at_six_players() -> None:
    """Same contract at 6 players (still Teensyville)."""
    e = _make_small_engine(6)
    e.start_game()
    e.start_night()
    seen = _drain_to_dawn_collecting_step_kinds(e)
    assert "minion_info" in seen, (
        f"Minion Info did not fire in a 6-player game; "
        f"saw step_kinds={seen!r}"
    )
    assert "demon_info" in seen, (
        f"Demon Info did not fire in a 6-player game; "
        f"saw step_kinds={seen!r}"
    )


if __name__ == "__main__":
    test_demon_info_emits_st_pre_then_info()
    print("test_demon_info_emits_st_pre_then_info passed.")
    test_demon_info_st_can_change_bluffs()
    print("test_demon_info_st_can_change_bluffs passed.")
    test_demon_info_bluff_pool_is_scoped_to_preset_roster()
    print("test_demon_info_bluff_pool_is_scoped_to_preset_roster passed.")
    test_minion_and_demon_info_fire_at_five_players()
    print("test_minion_and_demon_info_fire_at_five_players passed.")
    test_minion_and_demon_info_fire_at_six_players()
    print("test_minion_and_demon_info_fire_at_six_players passed.")
