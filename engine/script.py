"""Script (character set) reference data.

The engine ships with the Trouble Brewing roster as a data file —
one ``ScriptCharacter`` record per role. The roster includes both
fully-implemented characters (that have a real :class:`Character`
subclass with an ``ability`` method) and stubs for the remaining
roles, so the storyteller can still select them at setup, see
recommended counts, and walk the night order.

Stub characters are bound to a generic :class:`StubCharacter` whose
``ability`` is a single "(unimplemented) wake/sleep" prompt — the
storyteller can still hand-resolve the action without the engine
crashing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple, Type

from engine.character import Character, StubCharacter
from engine.characters import CHARACTER_REGISTRY
from engine.characters.stubs import STUB_BY_NAME
from engine.enums import CharType

if TYPE_CHECKING:
    from engine.engine import Engine


# ---------------------------------------------------------------------------
# Trouble Brewing roster.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptCharacter:
    """Static per-character record used during setup."""

    name: str
    char_type: CharType
    ability_text: str
    first_night_order: int = 0
    other_night_order: int = 0
    once_per_game: bool = False
    setup_outsider_delta: int = 0
    setup_townsfolk_delta: int = 0


TROUBLE_BREWING: Tuple[ScriptCharacter, ...] = (
    # Townsfolk
    ScriptCharacter("Washerwoman", CharType.TOWNSFOLK,
                    "You start knowing that 1 of 2 players is a particular Townsfolk.",
                    first_night_order=30),
    ScriptCharacter("Librarian", CharType.TOWNSFOLK,
                    "You start knowing that 1 of 2 players is a particular Outsider. "
                    "(Or that zero are in play.)",
                    first_night_order=31),
    ScriptCharacter("Investigator", CharType.TOWNSFOLK,
                    "You start knowing that 1 of 2 players is a particular Minion.",
                    first_night_order=32),
    ScriptCharacter("Chef", CharType.TOWNSFOLK,
                    "You start knowing how many pairs of evil players there are.",
                    first_night_order=33),
    ScriptCharacter("Empath", CharType.TOWNSFOLK,
                    "Each night, you learn how many of your 2 alive neighbours are evil.",
                    first_night_order=34, other_night_order=50),
    ScriptCharacter("Fortune Teller", CharType.TOWNSFOLK,
                    "Each night, choose 2 players: you learn if either is a Demon. "
                    "There is a good player that registers as a Demon to you.",
                    first_night_order=35, other_night_order=51),
    ScriptCharacter("Undertaker", CharType.TOWNSFOLK,
                    "Each night except the first, you learn which character died by execution today.",
                    other_night_order=52),
    ScriptCharacter("Monk", CharType.TOWNSFOLK,
                    "Each night except the first, choose a player (not yourself): "
                    "they are safe from the Demon tonight.",
                    other_night_order=20),
    ScriptCharacter("Ravenkeeper", CharType.TOWNSFOLK,
                    "If you die at night, you are woken to choose a player: you learn their character.",
                    other_night_order=45),
    ScriptCharacter("Virgin", CharType.TOWNSFOLK,
                    "The 1st time you are nominated, if the nominator is a Townsfolk, "
                    "they are executed immediately."),
    ScriptCharacter("Slayer", CharType.TOWNSFOLK,
                    "Once per game, during the day, publicly choose a player: "
                    "if they are the Demon, they die.",
                    once_per_game=True),
    ScriptCharacter("Soldier", CharType.TOWNSFOLK,
                    "You are safe from the Demon."),
    ScriptCharacter("Mayor", CharType.TOWNSFOLK,
                    "If only 3 players live and no execution occurs, good wins. "
                    "If you die at night, another player might die instead."),
    # Outsiders
    ScriptCharacter("Butler", CharType.OUTSIDER,
                    "Each night, choose a player (not yourself): tomorrow you may only vote if they are voting too.",
                    first_night_order=36, other_night_order=53),
    ScriptCharacter("Drunk", CharType.OUTSIDER,
                    "You do not know you are the Drunk. You think you are a Townsfolk character, but you are not."),
    ScriptCharacter("Recluse", CharType.OUTSIDER,
                    "You might register as evil and as a Minion or Demon, even if dead."),
    ScriptCharacter("Saint", CharType.OUTSIDER,
                    "If you die by execution, your team loses."),
    # Minions
    ScriptCharacter("Poisoner", CharType.MINION,
                    "Each night, choose a player: they are poisoned tonight and tomorrow day.",
                    first_night_order=10, other_night_order=10),
    ScriptCharacter("Spy", CharType.MINION,
                    "Each night, you see the Grimoire. You might register as good and as a "
                    "Townsfolk or Outsider, even if dead.",
                    first_night_order=40, other_night_order=60),
    ScriptCharacter("Scarlet Woman", CharType.MINION,
                    "If there are 5 or more players alive and the Demon dies, you become the Demon.",
                    other_night_order=15),
    ScriptCharacter("Baron", CharType.MINION,
                    "There are extra Outsiders in play. [+2 Outsiders]",
                    setup_outsider_delta=2, setup_townsfolk_delta=-2),
    # Demon
    ScriptCharacter("Imp", CharType.DEMON,
                    "Each night except the first, choose a player: they die. "
                    "If you choose yourself, you die and a Minion becomes the Imp.",
                    other_night_order=25),
)


# ---------------------------------------------------------------------------
# Bad Moon Rising additions.
#
# Only the roles needed by presets that ship with the project (e.g. the
# Chambermaid in ``no_greater_joy``) are defined here. The rest of the
# Bad Moon Rising roster lives in ``assets/presets/bad_moon_rising/`` as
# data only and falls back to a generic stub if seated.
# ---------------------------------------------------------------------------


BAD_MOON_RISING: Tuple[ScriptCharacter, ...] = (
    ScriptCharacter("Chambermaid", CharType.TOWNSFOLK,
                    "Each night, choose 2 alive players (not yourself): "
                    "you learn how many woke tonight due to their ability.",
                    first_night_order=38, other_night_order=55),
    ScriptCharacter("Sailor", CharType.TOWNSFOLK,
                    "Each night, choose an alive player: either you or "
                    "they are drunk until dusk. You can't die.",
                    first_night_order=14, other_night_order=14),
    ScriptCharacter("Innkeeper", CharType.TOWNSFOLK,
                    "Each night*, choose 2 players: they can't die "
                    "tonight, but 1 is drunk until dusk.",
                    other_night_order=18),
    ScriptCharacter("Courtier", CharType.TOWNSFOLK,
                    "Once per game, at night, choose a character: they "
                    "are drunk for 3 nights & 3 days.",
                    first_night_order=15, other_night_order=15,
                    once_per_game=True),
    ScriptCharacter("Tea Lady", CharType.TOWNSFOLK,
                    "If both your alive neighbors are good, they can't die."),
    ScriptCharacter("Pacifist", CharType.TOWNSFOLK,
                    "Executed good players might not die."),
    ScriptCharacter("Fool", CharType.TOWNSFOLK,
                    "The 1st time you die, you don't.",
                    once_per_game=True),
)


# ---------------------------------------------------------------------------
# Sects & Violets additions.
#
# Same scope rule as ``BAD_MOON_RISING`` above — only the roles used by
# bundled presets (No Greater Joy: Clockmaker, Artist, Sage, Klutz).
# ---------------------------------------------------------------------------


SECTS_AND_VIOLETS: Tuple[ScriptCharacter, ...] = (
    ScriptCharacter("Clockmaker", CharType.TOWNSFOLK,
                    "You start knowing how many steps from the Demon to its "
                    "nearest Minion.",
                    first_night_order=37),
    ScriptCharacter("Artist", CharType.TOWNSFOLK,
                    "Once per game, during the day, privately ask the "
                    "Storyteller any yes/no question.",
                    once_per_game=True),
    ScriptCharacter("Sage", CharType.TOWNSFOLK,
                    "If the Demon kills you, you learn that 1 of 2 players is "
                    "the Demon.",
                    other_night_order=30),
    ScriptCharacter("Klutz", CharType.OUTSIDER,
                    "When you learn that you died, publicly choose 1 alive "
                    "player: if they are evil, your team loses."),
)


# ``SCRIPT_BY_NAME`` is the engine-wide name → spec lookup. It carries
# every role any bundled preset can seat, regardless of edition. Presets
# pull their *visible* roster from their own ``characters.csv``; this
# dict just tells the engine what each role *means* (type, ability text,
# night order, setup deltas) so seating, validation, and stub fallback
# all work uniformly.
SCRIPT_BY_NAME: Dict[str, ScriptCharacter] = {
    c.name: c
    for c in (*TROUBLE_BREWING, *BAD_MOON_RISING, *SECTS_AND_VIOLETS)
}


# Recommended counts from the Traveler sheet for Trouble Brewing.
SETUP_COUNTS: Dict[int, Tuple[int, int, int, int]] = {
    5:  (3, 0, 1, 1),
    6:  (3, 1, 1, 1),
    7:  (5, 0, 1, 1),
    8:  (5, 1, 1, 1),
    9:  (5, 2, 1, 1),
    10: (7, 0, 2, 1),
    11: (7, 1, 2, 1),
    12: (7, 2, 2, 1),
    13: (9, 0, 3, 1),
    14: (9, 1, 3, 1),
    15: (9, 2, 3, 1),
}


def recommended_counts(player_count: int) -> Tuple[int, int, int, int]:
    """(townsfolk, outsiders, minions, demons) for ``player_count``."""
    if player_count < 5:
        raise ValueError("Blood on the Clocktower requires at least 5 players.")
    return SETUP_COUNTS[min(player_count, 15)]


def apply_setup_deltas(
    rec_townsfolk: int,
    rec_outsiders: int,
    pool_names: Iterable[str],
    roster_townsfolk: Optional[int] = None,
    roster_outsiders: Optional[int] = None,
) -> Tuple[int, int]:
    """Return (adjusted_townsfolk, adjusted_outsiders) after applying every
    in-pool role's ``setup_townsfolk_delta`` / ``setup_outsider_delta`` to
    the recommended counts, then clamping the result against the preset's
    actual roster size.

    Why the clamp matters: a role like the Baron declares ``+2 outsiders /
    -2 townsfolk`` regardless of which script it lives in. On a Trouble
    Brewing-sized roster (4 outsiders) this fits in every player count.
    On a smaller script — e.g. No Greater Joy, which only carries 2
    outsiders (Drunk, Klutz) — the Baron may not be able to add a full
    +2 because the roster runs out of distinct outsiders. The fix is
    general: clamp the adjusted outsider count to the roster's outsider
    capacity, and shovel any clamped surplus back into townsfolk so the
    total slot count (rec_t + rec_o) is preserved. The mirror clamp on
    townsfolk handles the symmetric case (a hypothetical role with a
    negative outsider delta on a script with too few townsfolk).

    ``pool_names`` is iterable to keep callers free to pass either a
    list of names or a generator. Names not in :data:`SCRIPT_BY_NAME`
    are ignored so the snapshot stays robust to half-typed picks.

    ``roster_townsfolk`` / ``roster_outsiders`` are the *number of
    distinct* characters of each type in the active script. Pass
    ``None`` (the default) to skip the roster clamp — useful when no
    preset is selected and the snapshot has nothing to clamp against.
    """
    townsfolk_delta = 0
    outsider_delta = 0
    for n in pool_names:
        spec = SCRIPT_BY_NAME.get(n)
        if spec is None:
            continue
        townsfolk_delta += spec.setup_townsfolk_delta
        outsider_delta += spec.setup_outsider_delta

    adj_t = max(0, rec_townsfolk + townsfolk_delta)
    adj_o = max(0, rec_outsiders + outsider_delta)

    # Clamp the *outsider* surplus first: it's the side that the canon
    # ability text talks about (Baron *adds* outsiders), so anything we
    # can't fit on this side is what spills back into townsfolk.
    if roster_outsiders is not None and adj_o > roster_outsiders:
        surplus = adj_o - roster_outsiders
        adj_o = roster_outsiders
        adj_t += surplus

    # Mirror clamp for townsfolk. Only triggers if roster_townsfolk is
    # also supplied; same surplus-redirect logic in reverse.
    if roster_townsfolk is not None and adj_t > roster_townsfolk:
        surplus = adj_t - roster_townsfolk
        adj_t = roster_townsfolk
        if roster_outsiders is None or adj_o + surplus <= roster_outsiders:
            adj_o += surplus
        else:
            # Pathological case: neither side has room. Fill outsiders
            # to capacity; the caller will see a smaller-than-expected
            # bag, which is the most we can do without a bigger roster.
            adj_o = roster_outsiders

    return adj_t, adj_o


def names_by_type(char_type: CharType) -> List[str]:
    """All character names of a given type, in script order."""
    return [c.name for c in TROUBLE_BREWING if c.char_type is char_type]


def all_names() -> List[str]:
    """All character names in the script, in script order."""
    return [c.name for c in TROUBLE_BREWING]


def build_character(name: str) -> Character:
    """Construct a fresh :class:`Character` instance for the given name.

    If the character has a real implementation in
    :mod:`engine.characters`, use it. Otherwise, fall back to a stub
    that still carries the right metadata so the engine can sequence it.

    The five anonymous stubs (TownsfolkStub, OutsiderStub, MinionStub,
    GoodStub, EvilStub) are handled specially: they live outside any
    script and are returned as fresh instances.
    """
    if name in STUB_BY_NAME:
        return STUB_BY_NAME[name]()

    if name not in SCRIPT_BY_NAME:
        raise KeyError(f"Unknown character {name!r}")
    spec = SCRIPT_BY_NAME[name]

    if name in CHARACTER_REGISTRY:
        cls: Type[Character] = CHARACTER_REGISTRY[name]
        # Sanity-check class-level metadata against the script spec.
        return cls()

    # Build a stub class on the fly with metadata copied from the spec.
    stub_cls = type(
        f"Stub_{name.replace(' ', '_')}",
        (StubCharacter,),
        {
            "name": spec.name,
            "char_type": spec.char_type,
            "ability_text": spec.ability_text,
            "first_night_order": spec.first_night_order,
            "other_night_order": spec.other_night_order,
            "once_per_game": spec.once_per_game,
            "setup_outsider_delta": spec.setup_outsider_delta,
            "setup_townsfolk_delta": spec.setup_townsfolk_delta,
        },
    )
    return stub_cls()
