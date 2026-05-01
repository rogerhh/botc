"""Roster-aware clamping of setup-time count adjustments.

Covers :func:`engine.script.apply_setup_deltas`, the helper used by
both ``_character_pool_snapshot`` and ``_randomize_pool_from_preset``
to translate a recommended (T, O) pair plus a list of in-pool roles
into the actual (adjusted_T, adjusted_O) the storyteller should fill.

The clamp is general — every preset feeds its roster sizes through
``apply_setup_deltas`` — but the test cases below were inspired by the
Baron, whose canonical "+2 Outsiders / -2 Townsfolk" can't always be
honored on smaller scripts. No Greater Joy ships only two outsiders
(Drunk, Klutz), so a Baron's 9-player game must cap at +0 effective.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import script as s


def test_no_pool_returns_recommended_unchanged() -> None:
    # Empty pool → adjusted == recommended; clamps are inert.
    assert s.apply_setup_deltas(5, 0, []) == (5, 0)
    assert s.apply_setup_deltas(5, 0, [], roster_townsfolk=13,
                                roster_outsiders=4) == (5, 0)


def test_baron_adds_two_outsiders_when_roster_has_room() -> None:
    # Trouble Brewing has 4 outsiders; +2 fits at every player count.
    assert s.apply_setup_deltas(5, 0, ["Baron"],
                                roster_townsfolk=13,
                                roster_outsiders=4) == (3, 2)
    assert s.apply_setup_deltas(5, 1, ["Baron"],
                                roster_townsfolk=13,
                                roster_outsiders=4) == (3, 3)


def test_baron_caps_at_roster_outsider_max_full_block() -> None:
    # No Greater Joy has 2 outsiders. At a 9-player game (rec 5T/2O)
    # the Baron's +2 has nowhere to go — outsiders are already at 2.
    # Surplus (2) flows back into townsfolk, so the bag size is
    # preserved (5T + 2O = 7) and the adjustment is a no-op.
    assert s.apply_setup_deltas(5, 2, ["Baron"],
                                roster_townsfolk=6,
                                roster_outsiders=2) == (5, 2)


def test_baron_caps_at_roster_outsider_max_partial_block() -> None:
    # NGJ 6 players: rec 3T/1O. Baron wants +2 outsiders but only 1
    # extra slot exists (cap=2). +1 effective; the other +1 goes
    # back to townsfolk → bag becomes 2T/2O (= same total 4).
    assert s.apply_setup_deltas(3, 1, ["Baron"],
                                roster_townsfolk=6,
                                roster_outsiders=2) == (2, 2)


def test_baron_no_cap_without_roster_information() -> None:
    # Without ``roster_*`` the helper applies the raw deltas.
    # (Used as a fallback when no preset is selected.)
    assert s.apply_setup_deltas(5, 0, ["Baron"]) == (3, 2)


def test_unknown_pool_names_are_ignored() -> None:
    # Names not in SCRIPT_BY_NAME silently skip; they have no
    # delta, so the result is the same as an empty pool.
    assert s.apply_setup_deltas(5, 0, ["NotARealCharacter"]) == (5, 0)
    assert s.apply_setup_deltas(5, 0, ["Baron", "NotARealCharacter"],
                                roster_townsfolk=13,
                                roster_outsiders=4) == (3, 2)


def test_recommended_floor_at_zero() -> None:
    # If the deltas would drive a count negative we floor at 0
    # rather than carry the negative further.
    # (No real character does this today, but the clamp is defensive
    # so the snapshot UI never shows "townsfolk: -1".)
    assert s.apply_setup_deltas(0, 0, ["Baron"],
                                roster_townsfolk=13,
                                roster_outsiders=4) == (0, 2)
