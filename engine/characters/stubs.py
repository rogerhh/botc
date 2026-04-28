"""Stub characters used as anonymous registrations.

These five inert :class:`Character` subclasses carry only ``char_type``
and ``alignment`` metadata. They are never seated, never act, and
never appear on a script's roster. They exist so that:

  * a *registers_as* override can return a "I am some Townsfolk" /
    "I am some evil player" answer when the detector only cares
    about character type or alignment, not the specific role —
    avoiding a noisy ST prompt for a full character pick;

  * a setup-time *dummy* slot (e.g. the Drunk-impersonating-the-FT's
    red-herring pick) can be filled with a placeholder that names the
    relevant *kind* of player without the ST having to point at any
    real character.

Stubs replace the older ``NoneCharacter``: where ``NoneCharacter`` was
a single typeless slot-filler, the five stubs preserve the bit of
metadata that actually matters (the type/alignment).

The available stubs:

  * :class:`TownsfolkStub` — char_type=TOWNSFOLK
  * :class:`OutsiderStub`  — char_type=OUTSIDER
  * :class:`MinionStub`    — char_type=MINION
  * :class:`DemonStub`     — char_type=DEMON
  * :class:`GoodStub`      — alignment=GOOD (char_type left as TOWNSFOLK
                             so type-side queries get a sensible default)
  * :class:`EvilStub`      — alignment=EVIL  (char_type=MINION default)

Stubs are *recognised* by the engine (``engine.is_stub(name)``) but are
not part of any script roster — they don't appear in the bag, in
``in_play_character_names``, or anywhere setup-counts logic runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.character import Character
from engine.enums import Alignment, CharType

if TYPE_CHECKING:
    from engine.engine import Engine  # noqa: F401


class _Stub(Character):
    """Common base for the five stub roles.

    Stubs are inert: ``ability_text`` is empty, no night actions, no
    reminders, no setup deltas. Only the class-level metadata
    (``char_type``, ``stub_alignment``) carries information.
    """

    name: str = "Stub"
    ability_text: str = ""
    first_night_order: int = 0
    other_night_order: int = 0
    once_per_game: bool = False
    setup_outsider_delta: int = 0
    setup_townsfolk_delta: int = 0
    reminder_tokens: list = []

    # Some stubs (GoodStub / EvilStub) carry an explicit alignment
    # because they exist precisely to answer "what alignment is this?"
    # without committing to a specific role. Type-keyed stubs
    # (TownsfolkStub / OutsiderStub / MinionStub) inherit a default
    # alignment from their char_type — the cached alignment is exposed
    # here for callers that want a uniform interface.
    stub_alignment: Alignment = Alignment.GOOD

    # Marker for ``engine.is_stub(name)`` and any test that wants to
    # discriminate stubs from real characters without a hardcoded list.
    is_stub: bool = True


class TownsfolkStub(_Stub):
    # The ``.name`` is the *display* string used in ST prompts and
    # internal lookups; we use the friendly label ``"Townsfolk"`` here
    # instead of ``"TownsfolkStub"`` so the Storyteller's char-type
    # picker reads as a small set of category names rather than
    # implementation details. The Python class name keeps the ``Stub``
    # suffix for clarity in code.
    name = "Townsfolk"
    char_type = CharType.TOWNSFOLK
    stub_alignment = Alignment.GOOD


class OutsiderStub(_Stub):
    name = "Outsider"
    char_type = CharType.OUTSIDER
    stub_alignment = Alignment.GOOD


class MinionStub(_Stub):
    name = "Minion"
    char_type = CharType.MINION
    stub_alignment = Alignment.EVIL


class DemonStub(_Stub):
    name = "Demon"
    char_type = CharType.DEMON
    stub_alignment = Alignment.EVIL


class GoodStub(_Stub):
    """Alignment-only stub: 'some good player' (no committed type).

    ``char_type`` defaults to TOWNSFOLK so callers that read it get a
    sensible answer, but the *meaning* of GoodStub is alignment-only:
    it should be returned by ``registers_as`` when a detector cares
    about alignment but not character type. Asking for its type
    yields "Townsfolk" by convention.
    """

    name = "Good"
    char_type = CharType.TOWNSFOLK
    stub_alignment = Alignment.GOOD


class EvilStub(_Stub):
    """Alignment-only stub: 'some evil player' (no committed type)."""

    name = "Evil"
    char_type = CharType.MINION
    stub_alignment = Alignment.EVIL


# ---------------------------------------------------------------------------
# Module-level convenience predicates / lookups.
# ---------------------------------------------------------------------------

ALL_STUBS = (
    TownsfolkStub,
    OutsiderStub,
    MinionStub,
    DemonStub,
    GoodStub,
    EvilStub,
)
STUB_NAMES = frozenset(s.name for s in ALL_STUBS)
STUB_BY_NAME = {s.name: s for s in ALL_STUBS}


def is_stub_name(name: str) -> bool:
    """Return True iff ``name`` is one of the stubs."""
    return name in STUB_NAMES


def stub_for_char_type(char_type: CharType) -> "type[_Stub]":
    """Pick the right stub for a *type-only* registration.

    Used when a Spy / Recluse override is asked to "register as some
    char_type" without a specific role.
    """
    if char_type is CharType.TOWNSFOLK:
        return TownsfolkStub
    if char_type is CharType.OUTSIDER:
        return OutsiderStub
    if char_type is CharType.MINION:
        return MinionStub
    if char_type is CharType.DEMON:
        return DemonStub
    raise ValueError(f"No stub registered for char_type {char_type!r}")


def stub_for_alignment(alignment: Alignment) -> "type[_Stub]":
    """Pick the right stub for an *alignment-only* registration."""
    if alignment is Alignment.GOOD:
        return GoodStub
    if alignment is Alignment.EVIL:
        return EvilStub
    raise ValueError(f"No stub registered for alignment {alignment!r}")
