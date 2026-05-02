"""Grandmother on-revive behavior.

Exercises the new revive flow added on top of the existing setup-time
GRANDCHILD pick: when the Grandmother is revived during a night
ability (e.g., the Professor), the engine immediately

  * prompts the Storyteller to (re-)pick the grandchild — defaulting
    to the current grandchild but allowing any seated good
    (Townsfolk/Outsider) seat other than the Grandmother, dead or
    alive;
  * wakes the Grandmother and re-shows her grandchild + character,
    with the usual drunk/poisoned wrong-character pre-pick;
  * marks her first-night ability slot as spent so the night loop
    doesn't re-fire the ability later that night or on a future
    night.

Day-phase revives (e.g., a Storyteller-driven manual revive on the
day phase, or any future day-revive ability) are not yet supported —
the Grandmother's :meth:`on_revive` raises ``NotImplementedError``,
which :meth:`Engine.revive` catches and logs.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import DeathCause, Phase


def _drain(engine: Engine, scripted: List[Tuple[dict, Any]],
           timeout: float = 5.0) -> None:
    """Walk the night thread by answering each pending prompt in turn.

    Each ``(matcher, response)`` pair is matched against the prompt's
    ``meta`` dict (every key in ``matcher`` must equal the prompt's
    value for that key) before responding. Matchers are checked in
    order — extra prompts the test didn't list are an error.
    """
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


# ---------------------------------------------------------------------------
# Shared setup.
# ---------------------------------------------------------------------------


def _make_gm_with_professor(*, with_poisoner: bool = False
                            ) -> Tuple[Engine, dict]:
    """Build a small game with Grandmother + Professor + Imp.

    Without poisoner (5 seats):
      1 Alice  Grandmother
      2 Bob    Slayer         (alt. grandchild candidate; no night ability,
                          and not protected from demon kills)
      3 Cara   Mayor          (original grandchild)
      4 Dan    Professor      (revives the GM)
      5 Eve    Imp

    With poisoner (6 seats — Bob is replaced by a Soldier+Poisoner
    pair so the second-night flow can poison the GM):
      1 Alice  Grandmother
      2 Bob    Slayer         (alt. grandchild candidate; no night ability,
                          and not protected from demon kills)
      3 Cara   Mayor          (original grandchild)
      4 Dan    Professor
      5 Eve    Poisoner
      6 Faye   Imp
    """
    e = Engine()
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    if with_poisoner:
        ev = e.add_seat("Eve")     # Poisoner
        f = e.add_seat("Faye")     # Imp
        e.assign_character(a.id, "Grandmother")
        e.assign_character(b.id, "Slayer")
        e.assign_character(c.id, "Mayor")
        e.assign_character(d.id, "Professor")
        e.assign_character(ev.id, "Poisoner")
        e.assign_character(f.id, "Imp")
        e.pool.set_many([
            "Grandmother", "Slayer", "Mayor", "Professor", "Poisoner", "Imp",
        ])
        ids = {
            "alice": a.id, "bob": b.id, "cara": c.id,
            "dan": d.id, "eve": ev.id, "faye": f.id,
        }
    else:
        ev = e.add_seat("Eve")     # Imp
        e.assign_character(a.id, "Grandmother")
        e.assign_character(b.id, "Slayer")
        e.assign_character(c.id, "Mayor")
        e.assign_character(d.id, "Professor")
        e.assign_character(ev.id, "Imp")
        e.pool.set_many([
            "Grandmother", "Slayer", "Mayor", "Professor", "Imp",
        ])
        ids = {
            "alice": a.id, "bob": b.id, "cara": c.id,
            "dan": d.id, "eve": ev.id,
        }
    e.pool.set_grandmother_grandchild("Mayor")
    e.apply_setup_data({"grandmother_grandchild": "Mayor"})
    return e, ids


# ---------------------------------------------------------------------------
# Test 1 — revive, ST keeps the original grandchild (default-confirm).
# ---------------------------------------------------------------------------


def test_revive_keeps_original_grandchild() -> None:
    e, ids = _make_gm_with_professor()
    gm_char = e.get_player(ids["alice"]).character
    assert gm_char._grandchild_id == ids["cara"]

    e.start_game()
    e.start_night()
    _drain(e, [
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    _drain(e, [
        # Imp kills Alice (the Grandmother).
        ({"character": "Imp",         "step": "select_target"}, ids["alice"]),
        # Professor picks the now-dead Alice and revives her.
        ({"character": "Professor",   "step": "select_dead_target"}, ids["alice"]),
        # Revive prompt — ST keeps the original grandchild (Cara/Mayor)
        # by selecting the default.
        ({"character": "Grandmother", "step": "select_grandchild_on_revive"},
         ids["cara"]),
        # Re-show: the GM sees her grandchild + character again.
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()

    alice = e.get_player(ids["alice"])
    assert alice.alive, "Grandmother should be revived."
    assert gm_char._grandchild_id == ids["cara"], (
        f"Grandchild stays as Cara; got id={gm_char._grandchild_id}"
    )
    assert e.pool.grandmother_grandchild() == "Mayor", (
        "Pool slot stays as Mayor when ST confirms the default."
    )


# ---------------------------------------------------------------------------
# Test 2 — revive, ST picks a NEW grandchild.
# ---------------------------------------------------------------------------


def test_revive_picks_new_grandchild() -> None:
    e, ids = _make_gm_with_professor()
    gm_char = e.get_player(ids["alice"]).character
    assert gm_char._grandchild_id == ids["cara"]

    e.start_game()
    e.start_night()
    _drain(e, [
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    _drain(e, [
        ({"character": "Imp",         "step": "select_target"}, ids["alice"]),
        ({"character": "Professor",   "step": "select_dead_target"}, ids["alice"]),
        # Revive prompt — ST swaps to Bob (Soldier).
        ({"character": "Grandmother", "step": "select_grandchild_on_revive"},
         ids["bob"]),
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()

    assert e.get_player(ids["alice"]).alive
    assert gm_char._grandchild_id == ids["bob"], (
        f"Grandchild should now be Bob; got id={gm_char._grandchild_id}"
    )
    assert e.pool.grandmother_grandchild() == "Slayer", (
        "Pool slot should reflect the new grandchild role."
    )


# ---------------------------------------------------------------------------
# Test 3 — drunk/poisoned on revive: wrong-character pre-pick fires.
# ---------------------------------------------------------------------------


def test_revive_while_poisoned_shows_wrong_character() -> None:
    e, ids = _make_gm_with_professor(with_poisoner=True)

    e.start_game()
    e.start_night()
    # Night 1: poisoner poisons themself (sober GM info show).
    _drain(e, [
        ({"character": "Poisoner",    "step": "select_player"}, ids["eve"]),
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    _drain(e, [
        # Poisoner poisons the Grandmother (Alice) on night 2.
        ({"character": "Poisoner",    "step": "select_player"}, ids["alice"]),
        # Imp kills the (poisoned) Grandmother.
        ({"character": "Imp",         "step": "select_target"}, ids["alice"]),
        # Professor revives her — she's still poisoned.
        ({"character": "Professor",   "step": "select_dead_target"}, ids["alice"]),
        # Revive grandchild prompt — keep the default (Cara/Mayor).
        ({"character": "Grandmother", "step": "select_grandchild_on_revive"},
         ids["cara"]),
        # Drunk/poisoned: wrong-character pre-pick. ST overrides the
        # default to "Slayer" so the GM is told the wrong role.
        ({"character": "Grandmother", "step": "select_shown_character"},
         "Slayer"),
        # Information panel.
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()

    assert e.get_player(ids["alice"]).alive
    assert e.get_player(ids["alice"]).poisoned, (
        "Poison persists across kill/revive."
    )


# ---------------------------------------------------------------------------
# Test 4 — revived GM dies again if her NEW grandchild is demon-killed.
# ---------------------------------------------------------------------------


def test_revive_then_new_grandchild_demon_kill_kills_gm() -> None:
    e, ids = _make_gm_with_professor()

    e.start_game()
    e.start_night()
    _drain(e, [
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    e.advance_to_night()
    e.start_night()
    _drain(e, [
        # Night 2: Imp kills Alice; Professor revives her; ST picks Bob
        # (Soldier) as the new grandchild.
        ({"character": "Imp",         "step": "select_target"}, ids["alice"]),
        ({"character": "Professor",   "step": "select_dead_target"}, ids["alice"]),
        ({"character": "Grandmother", "step": "select_grandchild_on_revive"},
         ids["bob"]),
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    assert e.get_player(ids["alice"]).alive
    assert e.get_player(ids["alice"]).character._grandchild_id == ids["bob"]

    e.advance_to_night()
    e.start_night()
    _drain(e, [
        # Night 3: Imp kills Bob (the new grandchild). Professor's slot
        # is spent — he doesn't wake. Grandmother's first-night slot is
        # spent — she doesn't wake either. Only the Imp prompt fires.
        ({"character": "Imp", "step": "select_target"}, ids["bob"]),
    ])
    e.advance_to_day()

    assert e.get_player(ids["bob"]).dead, "Bob (new grandchild) is dead."
    assert e.get_player(ids["alice"]).dead, (
        "Grandmother dies with her new grandchild — the death-reaction "
        "wiring re-points at the new grandchild after revive."
    )
    # The reaction console entry should attribute the death to the new
    # grandchild.
    gm_reactions = [
        entry for entry in e.console
        if entry.get("kind") == "reaction"
        and entry.get("details", {}).get("character") == "Grandmother"
        and entry.get("details", {}).get("trigger") == "grandchild_demon_death"
    ]
    assert gm_reactions, (
        f"Expected a Grandmother grandchild_demon_death reaction; "
        f"console entries: {[c for c in e.console if 'Grandmother' in str(c)]}"
    )


# ---------------------------------------------------------------------------
# Test 5 — day-phase revive raises NotImplementedError (caught + logged).
# ---------------------------------------------------------------------------


def test_day_revive_raises_unimplemented() -> None:
    e, ids = _make_gm_with_professor()

    e.start_game()
    e.start_night()
    _drain(e, [
        ({"character": "Grandmother", "step": "information"}, None),
    ])
    e.advance_to_day()
    # Manual ST kill on the Grandmother during the day.
    e.kill(ids["alice"], DeathCause.STORYTELLER)
    assert e.get_player(ids["alice"]).dead
    assert e.phase is Phase.DAY

    # Manual ST revive during the day. Engine.revive catches the
    # character's NotImplementedError and just logs it — the player is
    # still revived.
    e.revive(ids["alice"])

    assert e.get_player(ids["alice"]).alive, (
        "Engine.revive still flips the player to alive even though "
        "Grandmother.on_revive raised."
    )
    matched = [
        line for line in e.event_log
        if "Grandmother" in line and "on_revive" in line
        and "NotImplementedError" in line
    ]
    assert matched, (
        f"Expected a logged NotImplementedError from Grandmother.on_revive "
        f"on day revive. Recent log: {e.event_log[-15:]!r}"
    )


if __name__ == "__main__":
    test_revive_keeps_original_grandchild()
    print("revive keeps original OK")
    test_revive_picks_new_grandchild()
    print("revive picks new OK")
    test_revive_while_poisoned_shows_wrong_character()
    print("revive poisoned wrong-char OK")
    test_revive_then_new_grandchild_demon_kill_kills_gm()
    print("revive then new-grandchild demon-death OK")
    test_day_revive_raises_unimplemented()
    print("day revive raises OK")
