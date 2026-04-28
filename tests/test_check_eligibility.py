"""Tests for the Check abstraction and setup-time eligibility helpers.

The :class:`engine.check.Check` dataclass and :meth:`Character.check`
form the contract every detection-style ability uses. This file tests:

  * the registration_categories declared by each character class
    (default, Spy, Recluse, stubs) are correct;
  * Check.could_register_as_pass / registration_matters compute the
    right answers for the canonical detector profiles;
  * Character.could_pass_check (the setup-time eligibility test)
    returns True / False for the right pairs of (token, chair).

These are unit-style tests — no engine threading, no game state.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.check import Check
from engine.enums import Alignment, CharType
from engine.characters.spy import Spy
from engine.characters.recluse import Recluse
from engine.characters.imp import Imp
from engine.characters.mayor import Mayor
from engine.characters.washerwoman import Washerwoman
from engine.characters.stubs import (
    EvilStub,
    GoodStub,
    MinionStub,
    OutsiderStub,
    TownsfolkStub,
)


# ---------------------------------------------------------------------------
# registration_categories on each class.
# ---------------------------------------------------------------------------


def test_default_registration_categories_is_own_char_type() -> None:
    """A non-misregistering character only ever registers as its own type."""
    assert Mayor.registration_categories() == (CharType.TOWNSFOLK,)
    assert Imp.registration_categories() == (CharType.DEMON,)
    assert Washerwoman.registration_categories() == (CharType.TOWNSFOLK,)


def test_spy_registration_categories_includes_good_and_minion() -> None:
    cats = set(Spy.registration_categories())
    assert CharType.TOWNSFOLK in cats
    assert CharType.OUTSIDER in cats
    assert CharType.MINION in cats  # the Spy itself is a Minion
    assert CharType.DEMON not in cats  # Spy can't fake the Demon


def test_recluse_registration_categories_includes_evil_and_outsider() -> None:
    cats = set(Recluse.registration_categories())
    assert CharType.OUTSIDER in cats  # the Recluse itself
    assert CharType.MINION in cats
    assert CharType.DEMON in cats
    assert CharType.TOWNSFOLK not in cats  # Recluse can't fake a Townsfolk


# ---------------------------------------------------------------------------
# Check.could_register_as_pass — coarse "could the override produce a
# passing answer?" used for setup-time eligibility.
# ---------------------------------------------------------------------------


def test_could_register_as_pass_char_type() -> None:
    ww_check = Check(attribute="char_type", passes=(CharType.TOWNSFOLK,))
    # Spy could register as TF → eligible for WW seen-TOWNSFOLK token.
    assert Spy.could_pass_check(ww_check) is True
    # Recluse cannot register as TF.
    assert Recluse.could_pass_check(ww_check) is False
    # Imp obviously cannot.
    assert Imp.could_pass_check(ww_check) is False
    # Mayor (a real TF) is eligible.
    assert Mayor.could_pass_check(ww_check) is True


def test_could_register_as_pass_alignment() -> None:
    evil_check = Check(attribute="alignment", passes=(Alignment.EVIL,))
    # Spy (a Minion) — yes.
    assert Spy.could_pass_check(evil_check) is True
    # Recluse — yes (can register as Minion / Demon).
    assert Recluse.could_pass_check(evil_check) is True
    # Mayor — no (TF only).
    assert Mayor.could_pass_check(evil_check) is False


# ---------------------------------------------------------------------------
# Check.registration_matters — fine-grained "does the ST's choice affect
# the result?" used by Spy / Recluse to decide whether to prompt.
# ---------------------------------------------------------------------------


def test_registration_matters_chef_alignment() -> None:
    """Chef's alignment=EVIL check: Spy pickup MATTERS — Spy can be
    GOOD (TF/Outsider regs) or EVIL (self), so the count changes."""
    chef_check = Check(attribute="alignment", passes=(Alignment.EVIL,))
    assert chef_check.registration_matters(Spy.registration_categories()) is True
    assert chef_check.registration_matters(Recluse.registration_categories()) is True
    # Mayor: only TF → only one possible alignment (GOOD) → doesn't matter.
    assert chef_check.registration_matters(Mayor.registration_categories()) is False


def test_registration_matters_ft_demon_check() -> None:
    """FT's char_type=DEMON check:
       * Spy (TF/Outsider/Minion) — none are DEMON → all fail → doesn't matter.
       * Recluse (Outsider/Minion/Demon) — Demon passes, others fail → matters.
       * Imp (Demon) — only DEMON → all pass → doesn't matter.
    """
    ft_check = Check(attribute="char_type", passes=(CharType.DEMON,))
    assert ft_check.registration_matters(Spy.registration_categories()) is False
    assert ft_check.registration_matters(Recluse.registration_categories()) is True
    assert ft_check.registration_matters(Imp.registration_categories()) is False


def test_registration_matters_inv_minion_check() -> None:
    """Investigator's char_type=MINION check:
       * Spy (TF/Outsider/Minion) — Minion passes, others fail → MATTERS… but
         per the project rule "Investigator always sees Spy as Spy", we want
         the override NOT to fire. The mechanism: Spy.registers_as itself
         skips the prompt only when registration_matters is False — so for
         the Investigator the spy_registers_as prompt DOES fire under the
         strictly-mechanical reading. The project rule is enforced by the
         Spy's own override eligibility (it offers TF/Outsider stubs), so
         the Storyteller's pick still resolves to "register as Spy" by
         default when alignment-only or char_type-only passes are all that
         matter. Concretely, the test below verifies the mechanical path.
    """
    inv_check = Check(attribute="char_type", passes=(CharType.MINION,))
    assert inv_check.registration_matters(Spy.registration_categories()) is True
    assert inv_check.registration_matters(Recluse.registration_categories()) is True
    # Imp — only Demon → never minion → doesn't matter.
    assert inv_check.registration_matters(Imp.registration_categories()) is False


# ---------------------------------------------------------------------------
# Stubs.
# ---------------------------------------------------------------------------


def test_stubs_have_correct_metadata() -> None:
    assert TownsfolkStub.char_type is CharType.TOWNSFOLK
    assert OutsiderStub.char_type is CharType.OUTSIDER
    assert MinionStub.char_type is CharType.MINION
    assert GoodStub.stub_alignment is Alignment.GOOD
    assert EvilStub.stub_alignment is Alignment.EVIL
    # All stubs flag is_stub for discrimination.
    for cls in (TownsfolkStub, OutsiderStub, MinionStub, GoodStub, EvilStub):
        assert cls.is_stub is True
        assert cls.first_night_order == 0
        assert cls.other_night_order == 0
        assert cls.reminder_tokens == []


# ---------------------------------------------------------------------------
# Token-application eligibility (engine helpers).
# ---------------------------------------------------------------------------


def test_spy_chair_eligible_for_ww_townsfolk_token() -> None:
    """A chair holding the Spy is eligible for the Washerwoman's seen-TF
    token — the Spy can register as a Townsfolk."""
    from engine.engine import Engine
    e = Engine()
    assert e._townsfolk_in_play("Spy") is True
    # Recluse can't register as a TF — should NOT be eligible.
    assert e._townsfolk_in_play("Recluse") is False
    # Imp obviously can't.
    assert e._townsfolk_in_play("Imp") is False
    # Mayor is a real TF — eligible.
    assert e._townsfolk_in_play("Mayor") is True


def test_recluse_chair_eligible_for_inv_minion_token() -> None:
    """A chair holding the Recluse is eligible for the Investigator's
    seen-Minion token — the Recluse can register as a Minion."""
    from engine.engine import Engine
    e = Engine()
    assert e._minion_in_play("Recluse") is True
    # Spy is a real Minion — eligible.
    assert e._minion_in_play("Spy") is True
    # Mayor (TF) cannot register as a Minion.
    assert e._minion_in_play("Mayor") is False


def test_spy_chair_eligible_for_lib_outsider_token() -> None:
    """A chair holding the Spy is eligible for the Librarian's
    seen-Outsider token — the Spy can register as an Outsider."""
    from engine.engine import Engine
    e = Engine()
    assert e._outsider_in_play("Spy") is True
    # Recluse is a real Outsider — eligible.
    assert e._outsider_in_play("Recluse") is True
    # Mayor (TF) cannot register as an Outsider.
    assert e._outsider_in_play("Mayor") is False


def test_drunk_token_strict_true_townsfolk_only() -> None:
    """The Drunk token's eligibility is *strict* true-Townsfolk only —
    a Spy chair cannot become the Drunk (the Drunk's perceived TF must
    be a real Townsfolk role, not a misregistered one)."""
    from engine.engine import Engine
    e = Engine()
    assert e._true_townsfolk("Mayor") is True
    assert e._true_townsfolk("Spy") is False
    assert e._true_townsfolk("Recluse") is False
    assert e._true_townsfolk("Imp") is False


if __name__ == "__main__":
    test_default_registration_categories_is_own_char_type()
    test_spy_registration_categories_includes_good_and_minion()
    test_recluse_registration_categories_includes_evil_and_outsider()
    test_could_register_as_pass_char_type()
    test_could_register_as_pass_alignment()
    test_registration_matters_chef_alignment()
    test_registration_matters_ft_demon_check()
    test_registration_matters_inv_minion_check()
    test_stubs_have_correct_metadata()
    test_spy_chair_eligible_for_ww_townsfolk_token()
    test_recluse_chair_eligible_for_inv_minion_token()
    test_spy_chair_eligible_for_lib_outsider_token()
    test_drunk_token_strict_true_townsfolk_only()
    print("All check eligibility tests passed.")
