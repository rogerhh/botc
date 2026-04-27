"""Self-avoidance for the Washerwoman and Fortune Teller setup picks.

The Washerwoman's seen-Townsfolk slot and the Fortune Teller's
red-herring slot can both legally point at the WW / FT herself per
the rules — but it makes for a degenerate game (the WW knowing she
herself is the Townsfolk she was shown, the FT knowing she herself is
the red herring). The pool's auto-fill therefore avoids picking self
when *any* non-self candidate is available, and switches to a
non-self pick the moment one becomes available.

These tests guard that behaviour as a recurring regression check —
they're run by the rest of the test suite alongside the
washerwoman / librarian / investigator end-to-end tests.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.ui import CharacterPool


# ---------------------------------------------------------------------------
# Washerwoman: seen-Townsfolk slot.
# ---------------------------------------------------------------------------


def test_ww_self_picked_only_when_no_other_townsfolk() -> None:
    """If the WW is the only Townsfolk in the pool, the autofill
    falls back to the WW herself — there is literally no other
    candidate."""
    pool = CharacterPool()
    pool.add("Imp")
    pool.add("Poisoner")
    pool.add("Washerwoman")
    # Only Townsfolk in the pool is the Washerwoman herself.
    assert pool.washerwoman_townsfolk() == "Washerwoman"


def test_ww_switches_off_self_when_another_townsfolk_added() -> None:
    """As soon as a non-self Townsfolk joins the pool, the WW's
    seen-role slot switches off the WW herself.

    This is the recurring guard: a degenerate self-pick should never
    survive once a real candidate exists.
    """
    pool = CharacterPool()
    pool.add("Imp")
    pool.add("Poisoner")
    pool.add("Washerwoman")
    assert pool.washerwoman_townsfolk() == "Washerwoman"

    pool.add("Empath")
    assert pool.washerwoman_townsfolk() == "Empath"
    assert pool.washerwoman_townsfolk() != "Washerwoman"


def test_ww_self_avoidance_via_set_many() -> None:
    """``set_many`` is the path the UI uses on randomize; it must apply
    the same self-avoidance rule."""
    # Build a pool where the only Townsfolk is the WW herself.
    pool = CharacterPool()
    pool.set_many(["Washerwoman", "Imp", "Poisoner"])
    assert pool.washerwoman_townsfolk() == "Washerwoman"

    # Now add a real Townsfolk; the slot should snap to it.
    pool.set_many(["Washerwoman", "Empath", "Imp", "Poisoner"])
    assert pool.washerwoman_townsfolk() == "Empath"


# ---------------------------------------------------------------------------
# Fortune Teller: red-herring slot.
# ---------------------------------------------------------------------------


def test_ft_self_picked_only_when_no_other_good() -> None:
    """If the FT is the only Good role in the pool, the autofill
    falls back to the FT herself."""
    pool = CharacterPool()
    pool.add("Imp")
    pool.add("Poisoner")
    pool.add("Fortune Teller")
    assert pool.ft_red_herring() == "Fortune Teller"


def test_ft_switches_off_self_when_another_good_added() -> None:
    """As soon as a non-self Good role joins the pool, the FT's red
    herring slot switches off the FT herself.

    Recurring guard: same shape as the WW test above.
    """
    pool = CharacterPool()
    pool.add("Imp")
    pool.add("Poisoner")
    pool.add("Fortune Teller")
    assert pool.ft_red_herring() == "Fortune Teller"

    # Adding a Townsfolk switches the slot.
    pool.add("Empath")
    assert pool.ft_red_herring() == "Empath"
    assert pool.ft_red_herring() != "Fortune Teller"


def test_ft_self_avoidance_with_outsider_added() -> None:
    """An Outsider is also a Good role — adding one to a pool whose
    FT is currently self-pointing should also flip the slot."""
    pool = CharacterPool()
    pool.set_many(["Fortune Teller", "Imp", "Poisoner"])
    assert pool.ft_red_herring() == "Fortune Teller"

    pool.add("Saint")
    assert pool.ft_red_herring() == "Saint"


def test_ft_self_avoidance_via_set_many() -> None:
    pool = CharacterPool()
    pool.set_many(["Fortune Teller", "Imp", "Poisoner"])
    assert pool.ft_red_herring() == "Fortune Teller"

    pool.set_many(["Fortune Teller", "Empath", "Imp", "Poisoner"])
    assert pool.ft_red_herring() == "Empath"


# ---------------------------------------------------------------------------
# Storyteller override: dragging the token back onto self is still
# allowed (the rules permit it; only auto-fill avoids it).
# ---------------------------------------------------------------------------


def test_ww_explicit_self_pick_is_allowed() -> None:
    """``set_washerwoman_townsfolk`` accepts the WW herself — the rule
    is auto-fill-only avoidance, not a hard prohibition."""
    pool = CharacterPool()
    pool.set_many(["Washerwoman", "Empath", "Imp", "Poisoner"])
    assert pool.washerwoman_townsfolk() == "Empath"
    # Storyteller drags the seen-TF token back onto the WW: explicit
    # set must succeed (the rules permit it).
    pool.set_washerwoman_townsfolk("Washerwoman")
    assert pool.washerwoman_townsfolk() == "Washerwoman"


def test_ft_explicit_self_pick_is_allowed() -> None:
    pool = CharacterPool()
    pool.set_many(["Fortune Teller", "Empath", "Imp", "Poisoner"])
    assert pool.ft_red_herring() == "Empath"
    pool.set_ft_red_herring("Fortune Teller")
    assert pool.ft_red_herring() == "Fortune Teller"


# ---------------------------------------------------------------------------
# Storyteller-facing prompts: the prompt's ``meta["default"]`` always
# holds a non-self eligible candidate when one is available. This is
# the IN_GAME path — what fires when the UI's pool-driven setup didn't
# pre-populate the slot.
# ---------------------------------------------------------------------------


import time
from engine.engine import Engine


def _wait_for_prompt(engine: Engine, matcher: dict, timeout: float = 3.0):
    """Poll until a pending prompt matches ``matcher``, then return it.

    ``matcher`` is a subset of the prompt's ``meta``; every key/value
    must appear. Times out with a TimeoutError.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = engine.pending_prompt()
        if p is not None:
            ok = True
            for k, v in matcher.items():
                if p.meta.get(k) != v:
                    ok = False
                    break
            if ok:
                return p
        time.sleep(0.01)
    raise TimeoutError(f"no prompt matched {matcher} within {timeout}s")


def test_ww_in_game_prompt_default_is_non_self() -> None:
    """When no UI pool data is supplied, the WW's first-night
    SelectCharacterPrompt pre-fills its ``meta["default"]`` with a
    non-self in-play Townsfolk."""
    e = Engine()
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
    e.start_game()
    e.start_night()

    try:
        # Drain the Poisoner first.
        p_pois = _wait_for_prompt(e, {"character": "Poisoner",
                                      "step": "select_player"})
        e.respond(p_pois.id, d.id)
        # Now wait for the WW's character-select prompt.
        p_ww = _wait_for_prompt(e, {"character": "Washerwoman",
                                    "step": "select_character"})
        default = p_ww.meta.get("default")
        eligibles = list(p_ww.eligible_characters)
        assert default is not None, (
            f"WW prompt missing default; meta={p_ww.meta}"
        )
        assert default != "Washerwoman", (
            f"WW prompt defaulted to self ({default}); should be non-self"
        )
        assert default in eligibles, (
            f"WW default {default!r} not in eligibles {eligibles!r}"
        )
        # Respond with the default to let the night thread terminate.
        e.respond(p_ww.id, default)
    finally:
        # Drain any remaining prompts so the night thread exits.
        deadline = time.time() + 3.0
        while (
            e._night_thread
            and e._night_thread.is_alive()
            and time.time() < deadline
        ):
            p = e.pending_prompt()
            if p is not None:
                # Default-respond: pick the first eligible / target.
                resp = None
                if hasattr(p, "eligible_player_ids") and p.eligible_player_ids:
                    resp = p.eligible_player_ids[0]
                elif (hasattr(p, "eligible_characters")
                      and p.eligible_characters):
                    resp = p.eligible_characters[0]
                e.respond(p.id, resp)
            else:
                time.sleep(0.01)


def test_ft_in_game_prompt_default_is_non_self() -> None:
    """When no UI pool data is supplied, the FT's red-herring
    setup prompt pre-fills its ``meta["default"]`` with a non-self
    in-play Good role.
    """
    e = Engine()
    alice = e.add_seat("Alice")    # 1 — Drunk
    bob   = e.add_seat("Bob")      # 2 — Fortune Teller
    cara  = e.add_seat("Cara")     # 3 — Soldier
    dan   = e.add_seat("Dan")      # 4 — Mayor
    eve   = e.add_seat("Eve")      # 5 — Chef
    finn  = e.add_seat("Finn")     # 6 — Baron
    gabe  = e.add_seat("Gabe")     # 7 — Imp
    e.assign_character(alice.id, "Drunk")
    e.assign_character(bob.id,   "Fortune Teller")
    e.assign_character(cara.id,  "Soldier")
    e.assign_character(dan.id,   "Mayor")
    e.assign_character(eve.id,   "Chef")
    e.assign_character(finn.id,  "Baron")
    e.assign_character(gabe.id,  "Imp")
    e.start_game()
    e.start_night()

    try:
        # The Drunk's setup prompt fires first (setup_select_fake);
        # respond to it before the FT's prompt becomes pending.
        p_drunk = _wait_for_prompt(e, {"character": "Drunk",
                                       "step": "setup_select_fake"})
        e.respond(p_drunk.id, "Empath")
        # Now check the FT's red-herring prompt.
        p_ft = _wait_for_prompt(e, {"character": "Fortune Teller",
                                    "step": "setup_select_red_herring"})
        default = p_ft.meta.get("default")
        eligibles = list(p_ft.eligible_characters)
        assert default is not None, (
            f"FT prompt missing default; meta={p_ft.meta}"
        )
        assert default != "Fortune Teller", (
            f"FT prompt defaulted to self ({default}); should be non-self"
        )
        assert default in eligibles, (
            f"FT default {default!r} not in eligibles {eligibles!r}"
        )
        e.respond(p_ft.id, default)
    finally:
        deadline = time.time() + 3.0
        while (
            e._night_thread
            and e._night_thread.is_alive()
            and time.time() < deadline
        ):
            p = e.pending_prompt()
            if p is not None:
                resp = None
                if hasattr(p, "eligible_player_ids") and p.eligible_player_ids:
                    resp = p.eligible_player_ids[0]
                elif (hasattr(p, "eligible_characters")
                      and p.eligible_characters):
                    resp = p.eligible_characters[0]
                e.respond(p.id, resp)
            else:
                time.sleep(0.01)


if __name__ == "__main__":
    test_ww_self_picked_only_when_no_other_townsfolk()
    print("test 1 passed.")
    test_ww_switches_off_self_when_another_townsfolk_added()
    print("test 2 passed.")
    test_ww_self_avoidance_via_set_many()
    print("test 3 passed.")
    test_ft_self_picked_only_when_no_other_good()
    print("test 4 passed.")
    test_ft_switches_off_self_when_another_good_added()
    print("test 5 passed.")
    test_ft_self_avoidance_with_outsider_added()
    print("test 6 passed.")
    test_ft_self_avoidance_via_set_many()
    print("test 7 passed.")
    test_ww_explicit_self_pick_is_allowed()
    print("test 8 passed.")
    test_ft_explicit_self_pick_is_allowed()
    print("test 9 passed.")
    test_ww_in_game_prompt_default_is_non_self()
    print("test 10 passed.")
    test_ft_in_game_prompt_default_is_non_self()
    print("test 11 passed.")
    print("All self-avoidance tests passed.")
