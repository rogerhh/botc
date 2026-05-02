"""Grandmother GRANDCHILD setup-time token.

Exercises the new flow where the Grandmother's grandchild is picked
during game setup — the storyteller drags the GRANDCHILD reminder
token onto a good (Townsfolk or Outsider) chair before clicking Start
Game. The pool stores the grandchild as a role name (matching the
FT/WW conventions); the engine resolves the role to a seated player
during ``apply_setup_data`` and stores the player id on the
Grandmother's ``_grandchild_id`` so the death reaction wires up.

Also covers:

  * The pool auto-fills ``grandmother_grandchild`` whenever the
    Grandmother is in the bag — never picks the Grandmother herself.
  * ``move_grandmother_grandchild_token`` validates the destination
    chair (must hold a TF/Outsider in the pool, can't be the
    Grandmother's own seat).
  * The first-night ability runs with no ``select_grandchild`` ST
    prompt — only the information prompt fires.
  * The GRANDCHILD reminder token surfaces in ``chair_views``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Phase


def drain(engine: Engine, scripted: List[Tuple[dict, Any]],
          timeout: float = 5.0) -> None:
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


def _make_engine_with_gm() -> Tuple[Engine, dict]:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Grandmother
    b = e.add_seat("Bob")      # 2 — Soldier (good)
    c = e.add_seat("Cara")     # 3 — Mayor   (good)
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp
    e.assign_character(a.id, "Grandmother")
    e.assign_character(b.id, "Soldier")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    e.pool.set_many(["Grandmother", "Soldier", "Mayor", "Poisoner", "Imp"])
    # Bind chairs to player ids and characters (the test bypasses the UI's
    # ``_sync_chairs_to_engine`` so we mirror its effect by hand for the
    # token-drag handlers, which key off ``chair.character``).
    chairs = e.chairs.list()
    bindings = [
        (chairs[0]["id"], a.id, "Alice", "Grandmother"),
        (chairs[1]["id"], b.id, "Bob", "Soldier"),
        (chairs[2]["id"], c.id, "Cara", "Mayor"),
        (chairs[3]["id"], d.id, "Dan", "Poisoner"),
        (chairs[4]["id"], f.id, "Eve", "Imp"),
    ]
    for cid, pid, name, char in bindings:
        e.chairs.update(cid, player_id=pid, name=name, character=char)
    chair_ids = {key: cid for (cid, pid, _, _), key in zip(bindings,
                                                          ("a", "b", "c", "d", "f"))}
    return e, {
        "a": a.id, "b": b.id, "c": c.id, "d": d.id, "f": f.id,
        "chair_a": chair_ids["a"], "chair_b": chair_ids["b"],
        "chair_c": chair_ids["c"], "chair_d": chair_ids["d"],
        "chair_f": chair_ids["f"],
    }


# ---------------------------------------------------------------------------
# Pool autofill / setter rules.
# ---------------------------------------------------------------------------


def test_pool_autofills_grandmother_grandchild() -> None:
    e, ids = _make_engine_with_gm()
    role = e.pool.grandmother_grandchild()
    # Auto-fill picks a good role that isn't the Grandmother herself.
    assert role in {"Soldier", "Mayor"}, (
        f"autofill picked {role!r}; expected Soldier or Mayor"
    )


def test_pool_setter_rejects_self_and_evil() -> None:
    e, _ = _make_engine_with_gm()
    # Self-pick is forbidden.
    try:
        e.pool.set_grandmother_grandchild("Grandmother")
    except ValueError:
        pass
    else:
        raise AssertionError("Grandmother as own grandchild must be rejected.")
    # Evil role rejected (Imp is in pool; not TF/Outsider).
    try:
        e.pool.set_grandmother_grandchild("Imp")
    except ValueError:
        pass
    else:
        raise AssertionError("Demon as grandchild must be rejected.")
    # Out-of-pool role rejected.
    try:
        e.pool.set_grandmother_grandchild("Empath")
    except ValueError:
        pass
    else:
        raise AssertionError("Out-of-pool role as grandchild must be rejected.")


# ---------------------------------------------------------------------------
# move_grandmother_grandchild_token validation.
# ---------------------------------------------------------------------------


def test_move_token_to_evil_chair_rejected() -> None:
    e, ids = _make_engine_with_gm()
    err = e.move_grandmother_grandchild_token(ids["chair_d"])  # Poisoner
    assert err is not None and "Townsfolk or Outsider" in err, (
        f"expected an error about TF/Outsider; got {err!r}"
    )


def test_move_token_to_self_rejected() -> None:
    e, ids = _make_engine_with_gm()
    err = e.move_grandmother_grandchild_token(ids["chair_a"])  # Grandmother
    assert err is not None and "Grandmother" in err


def test_move_token_to_good_chair_resolves_grandchild() -> None:
    e, ids = _make_engine_with_gm()
    err = e.move_grandmother_grandchild_token(ids["chair_c"])  # Cara (Mayor)
    assert err is None, f"unexpected error: {err!r}"
    assert e.pool.grandmother_grandchild() == "Mayor"
    # The Grandmother's _grandchild_id has been re-resolved to Cara.
    gm_char = e.get_player(ids["a"]).character
    assert gm_char._grandchild_id == ids["c"]


# ---------------------------------------------------------------------------
# apply_setup_data + first-night flow.
# ---------------------------------------------------------------------------


def test_apply_setup_data_pre_resolves_grandchild() -> None:
    e, ids = _make_engine_with_gm()
    e.pool.set_grandmother_grandchild("Mayor")
    e.apply_setup_data({"grandmother_grandchild": "Mayor"})
    gm = e.get_player(ids["a"]).character
    assert gm._grandchild_id == ids["c"]


def test_first_night_skips_select_grandchild_prompt() -> None:
    """When the grandchild is set during setup, the first-night
    ability runs with no ``select_grandchild`` prompt — only the
    information prompt fires."""
    e, ids = _make_engine_with_gm()
    e.pool.set_grandmother_grandchild("Mayor")
    e.apply_setup_data({"grandmother_grandchild": "Mayor"})

    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT
    e.start_night()
    drain(e, [
        ({"character": "Poisoner",    "step": "select_player"}, ids["d"]),
        # No select_grandchild prompt — just the information.
        ({"character": "Grandmother", "step": "information"},   None),
    ])
    e.advance_to_day()
    assert e.phase is Phase.DAY


def test_grandchild_token_surfaces_in_chair_views() -> None:
    """The GRANDCHILD reminder-token kind appears on the grandchild's
    chair in ``chair_views`` after the token is set, both at setup
    time and after the first night fires."""
    e, ids = _make_engine_with_gm()
    e.pool.set_grandmother_grandchild("Mayor")
    e.apply_setup_data({"grandmother_grandchild": "Mayor"})

    # Setup phase: token sits on Cara's chair (Mayor).
    cara = next(c for c in e.chair_views() if c["id"] == ids["chair_c"])
    kinds = [t["kind"] for t in cara["tokens"]]
    assert "grandmother_grandchild" in kinds, (
        f"expected grandmother_grandchild on Cara's chair; got {kinds!r}"
    )

    # And not on any other chair.
    for c in e.chair_views():
        if c["id"] == ids["chair_c"]:
            continue
        assert "grandmother_grandchild" not in [t["kind"] for t in c["tokens"]], (
            f"unexpected grandmother_grandchild on chair id={c['id']}: {c}"
        )


if __name__ == "__main__":
    test_pool_autofills_grandmother_grandchild()
    print("pool autofill OK")
    test_pool_setter_rejects_self_and_evil()
    print("pool setter rejection OK")
    test_move_token_to_evil_chair_rejected()
    print("move token evil rejected OK")
    test_move_token_to_self_rejected()
    print("move token self rejected OK")
    test_move_token_to_good_chair_resolves_grandchild()
    print("move token good chair OK")
    test_apply_setup_data_pre_resolves_grandchild()
    print("apply_setup_data OK")
    test_first_night_skips_select_grandchild_prompt()
    print("first-night skip OK")
    test_grandchild_token_surfaces_in_chair_views()
    print("chair_views token OK")
