"""Multi-night demon protection chain tests.

Pukka, Shabaloth, Po, Zombuul interactions with Monk, Soldier, etc.

Night orders for the demons under test:
  Pukka:     N1=19, N2+=27 (acts every night)
  Shabaloth: N1=0,  N2+=28
  Po:        N1=0,  N2+=29
  Zombuul:   N1=0,  N2+=26 (only if no day-death the prior day)
  Exorcist:  N1=0,  N2+=21

Standard N2+ pre-demon ordering reference: Poisoner(10), Sailor(14),
SW reaction-only(15), Innkeeper(18), Monk(20), Exorcist(21).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Alignment, CharType, DeathCause, Phase


def drain_prompts(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 5.0,
) -> None:
    deadline = time.time() + timeout
    answered = 0
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night thread timeout; answered={answered}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected extra prompt: {p.text!r} meta={p.meta}"
            )
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"Prompt #{answered+1} mismatch: "
                    f"expected meta[{k!r}]={v!r}, got meta={p.meta}, "
                    f"text={p.text!r}"
                )
        engine.respond(p.id, response)
        answered += 1
        time.sleep(0.005)
    if answered != len(scripted):
        raise AssertionError(
            f"Night ended with {answered}/{len(scripted)} prompts answered."
        )


# ---------------------------------------------------------------------------
# Pukka × protection
# ---------------------------------------------------------------------------

def test_pukka_kill_lands_on_soldier_because_pukka_poison_disables_soldier() -> None:
    """Pukka poisons N1 → Soldier loses ability → kill lands N2.

    Note: this differs from Imp × Soldier (where Soldier survives).
    Pukka's poison on the prev-night target disables their ability
    on the night they're killed. So a Soldier targeted by Pukka on N1
    dies on N2 because they have no ability anymore (still being
    poisoned by Pukka's effect).
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Soldier")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Investigator")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Pukka")
    e.start_game()
    e.start_night()
    answered = 0
    expected = [({"character": "Poisoner", "step": "select_player"}, 6)]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = p.meta or {}
        if answered < len(expected):
            matcher, response = expected[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Pukka" and meta.get("step") == "select_target":
            e.respond(p.id, 1)  # Soldier
        elif meta.get("character") == "Investigator":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Poisoner")
            elif step == "select_wrong_player":
                e.respond(p.id, 2)
            elif step == "select_players":
                e.respond(p.id, [6, 2])
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()

    # N2: Pukka kills the previous target. Pukka picks Soldier again
    # (or anyone) — but the previous-night-target is the Soldier, so
    # Pukka's kill on the Soldier is cancelled by Soldier's immunity.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        # Pukka picks self for new poison (immaterial); previous-target
        # kill on Soldier should be cancelled.
        ({"character": "Pukka", "step": "select_target"}, 7),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(1).dead, (
        "Soldier should die: Pukka's poison disables Soldier's ability."
    )


def test_pukka_kill_blocked_by_monk() -> None:
    """Pukka's prev-night target gets Monk protection on N2 — kill cancels."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Monk")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Investigator")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Pukka")
    e.start_game()
    e.start_night()
    answered = 0
    expected = [({"character": "Poisoner", "step": "select_player"}, 6)]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = p.meta or {}
        if answered < len(expected):
            matcher, response = expected[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Pukka" and meta.get("step") == "select_target":
            e.respond(p.id, 2)  # poison Mayor
        elif meta.get("character") == "Investigator":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Poisoner")
            elif step == "select_wrong_player":
                e.respond(p.id, 3)
            elif step == "select_players":
                e.respond(p.id, [6, 3])
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()

    # N2: Monk protects Mayor; Pukka's prev-night target was Mayor →
    # kill cancelled. Pukka picks new poison target.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Monk", "step": "select_player"}, 2),
        ({"character": "Pukka", "step": "select_target"}, 4),
        # Mayor would die from Pukka's prev-night kill but Monk blocks.
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(2).alive, "Monk-protected Mayor shouldn't die."


# ---------------------------------------------------------------------------
# Shabaloth × protection
# ---------------------------------------------------------------------------

def test_shabaloth_attack_blocked_on_one_target_other_dies() -> None:
    """Shabaloth attacks 2 targets; Monk-protected one survives, other dies."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Monk")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Investigator")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Shabaloth")
    e.start_game()
    e.start_night()
    # N1: Shabaloth doesn't act. Drive through info.
    answered = 0
    expected = [({"character": "Poisoner", "step": "select_player"}, 6)]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = p.meta or {}
        if answered < len(expected):
            matcher, response = expected[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Investigator":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Poisoner")
            elif step == "select_wrong_player":
                e.respond(p.id, 2)
            elif step == "select_players":
                e.respond(p.id, [6, 2])
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()

    # N2: Monk protects Mayor. Shabaloth attacks Mayor + Chef. Mayor
    # cancelled, Chef dies.
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Monk", "step": "select_player"}, 2),
        ({"character": "Shabaloth", "step": "select_targets"}, [2, 4]),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(2).alive, "Monk-protected Mayor survives."
    assert e.get_player(4).dead, "Chef should die to Shabaloth."


# ---------------------------------------------------------------------------
# Po × Soldier as one of three charged targets
# ---------------------------------------------------------------------------

def test_po_charged_three_kills_one_blocked_by_soldier() -> None:
    """Po skips N2, charged on N3 picks 3 targets — Soldier survives."""
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Soldier")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Investigator")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Po")
    e.start_game()
    e.start_night()
    answered = 0
    expected = [({"character": "Poisoner", "step": "select_player"}, 6)]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = p.meta or {}
        if answered < len(expected):
            matcher, response = expected[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Investigator":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Poisoner")
            elif step == "select_wrong_player":
                e.respond(p.id, 2)
            elif step == "select_players":
                e.respond(p.id, [6, 2])
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()

    # N2: Po skips (charged for next night).
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Po", "step": "select_target_or_skip"}, 0),  # skip
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()

    # N3: Po picks 3 — including Soldier (immune) and Mayor (redirect).
    e.advance_to_night()
    e.start_night()
    drain_prompts(e, [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Po", "step": "select_targets_charged"}, [1, 2, 4]),
        # Mayor's redirect fires; decline.
        ({"character": "Mayor", "step": "redirect_yes_no"}, False),
        ({"character": "Empath", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(1).alive, "Soldier survives (immune)."
    assert e.get_player(2).dead, "Mayor dies (declined redirect)."
    assert e.get_player(4).dead, "Chef dies."


# ---------------------------------------------------------------------------
# Zombuul × day-death
# ---------------------------------------------------------------------------

def test_zombuul_does_not_wake_after_day_death() -> None:
    """Zombuul only kills on a night where no day-death happened.

    D1 has no death (no execution), so on N2 Zombuul wakes and kills.
    But if we kill someone via Tinker on D2, Zombuul on N3 doesn't act.
    """
    e = Engine()
    for n in ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus"):
        e.add_seat(n)
    e.assign_character(1, "Tinker")
    e.assign_character(2, "Mayor")
    e.assign_character(3, "Empath")
    e.assign_character(4, "Chef")
    e.assign_character(5, "Investigator")
    e.assign_character(6, "Poisoner")
    e.assign_character(7, "Zombuul")
    e.start_game()
    e.start_night()
    answered = 0
    expected = [({"character": "Poisoner", "step": "select_player"}, 6)]
    deadline = time.time() + 5.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        meta = p.meta or {}
        if answered < len(expected):
            matcher, response = expected[answered]
            for k, v in matcher.items():
                assert meta.get(k) == v, f"#{answered+1} {matcher} vs {meta}"
            e.respond(p.id, response)
            answered += 1
            continue
        if meta.get("character") == "Investigator":
            step = meta.get("step")
            if step == "select_character":
                e.respond(p.id, "Poisoner")
            elif step == "select_wrong_player":
                e.respond(p.id, 2)
            elif step == "select_players":
                e.respond(p.id, [6, 2])
            else:
                e.respond(p.id, None)
        else:
            e.respond(p.id, None)
        time.sleep(0.005)
    e.advance_to_day()
    # Tinker kills Mayor on D1 — that's a day-death.
    e.use_daytime_ability(1)  # not Tinker; we have to fix
    # Actually Tinker is seat 1 and use_daytime_ability(1) makes the
    # Tinker die. That's fine — Mayor died is wrong; the Tinker
    # death is the day-death.

    e.advance_to_night()
    e.start_night()
    # N2: Zombuul should NOT act because there was a day-death.
    seen: list = []
    expected = [
        ({"character": "Poisoner", "step": "select_player"}, 6),
        ({"character": "Empath", "step": "information"}, None),
    ]
    answered = 0
    deadline = time.time() + 3.0
    while e._night_thread and e._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError("timeout")
        p = e.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        seen.append(dict(p.meta))
        matcher, response = expected[answered]
        for k, v in matcher.items():
            assert p.meta.get(k) == v, f"#{answered+1} {matcher} vs {p.meta}"
        e.respond(p.id, response)
        answered += 1
        time.sleep(0.005)
    deaths = e.advance_to_day()
    assert deaths == [], "Zombuul shouldn't kill after a day-death."
    # Positive: Zombuul's select_target never fired.
    zombuul_prompts = [m for m in seen
                       if m.get("character") == "Zombuul"
                       and m.get("step") == "select_target"]
    assert not zombuul_prompts, (
        f"Zombuul gate should suppress select_target. Saw: {zombuul_prompts}"
    )


if __name__ == "__main__":
    import sys as _sys
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    p = f = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            p += 1
        except BaseException as exc:
            print(f"FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
            f += 1
    print(f"\n{p} passed, {f} failed")
    _sys.exit(0 if f == 0 else 1)
