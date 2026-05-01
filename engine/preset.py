"""Preset (script) parser.

Each preset under ``assets/presets/<name>/`` ships with three data
files:

  * ``first_night.txt`` — the first-night order (Dusk, Minion Info,
    Demon Info, Poisoner, Washerwoman, …, Dawn).
  * ``night.txt`` — every-other-night order (Dusk, Poisoner, Monk, …,
    Dawn).
  * ``characters.csv`` — the script's *roster*: every character that
    can legally be in play on this script, with its team. Drives
    information abilities that need to enumerate "every Townsfolk on
    the script" (Demon-Info bluff pool, Washerwoman/Librarian/
    Investigator misregistration candidates, the Drunk's pretend role
    pool, etc.) so they don't leak roles from a different edition.

The two ``.txt`` files use the same format: each entry occupies two
lines (a name, then its storyteller-facing description) separated
from the next entry by a blank line. Example::

    Dusk
    Start the Night Phase.

    Poisoner
    The Poisoner chooses a player.

The engine treats each entry as a single *step* in the night phase.
Steps whose name matches an in-play character drive that character's
ability(); other steps (Dusk, Dawn, Minion Info, Demon Info, …) are
handled by the engine itself. The storyteller-facing description is
piped through the prompt so the local UI can display the literal
rulebook instruction.

This module is the canonical source of *night order* and (when a
preset is installed) the canonical source of the *script roster*.
The Character class still carries ``first_night_order`` and
``other_night_order`` for backwards compatibility (and for editions
whose preset files are missing), but when a preset is supplied it
overrides those numbers and its roster overrides the global
:mod:`engine.script` lookup for "what's on the script".
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.enums import CharType


# Built-in step names that aren't tied to a single character.
DUSK = "Dusk"
DAWN = "Dawn"
MINION_INFO = "Minion Info"
DEMON_INFO = "Demon Info"

# Steps the engine handles directly (not via Character.ability).
NON_CHARACTER_STEPS = frozenset({DUSK, DAWN, MINION_INFO, DEMON_INFO})


@dataclass(frozen=True)
class NightStep:
    """One ordered step in a night sheet."""

    name: str            # e.g. "Poisoner", "Dusk"
    description: str     # storyteller-facing text from the preset

    @property
    def is_character_step(self) -> bool:
        """True if this step drives a character's ability() rather than
        a built-in engine action."""
        return self.name not in NON_CHARACTER_STEPS


def _parse_text(text: str) -> List[NightStep]:
    """Parse a night-sheet ``text`` into a list of :class:`NightStep`.

    Splits on blank lines; each block's first non-blank line is the
    name and the rest of the block (joined with spaces) is the
    description. Blank trailing lines are ignored.
    """
    steps: List[NightStep] = []
    current_name: Optional[str] = None
    current_desc_lines: List[str] = []

    def flush() -> None:
        nonlocal current_name, current_desc_lines
        if current_name is None:
            return
        desc = " ".join(line.strip() for line in current_desc_lines).strip()
        steps.append(NightStep(name=current_name, description=desc))
        current_name = None
        current_desc_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        if current_name is None:
            current_name = line.strip()
        else:
            current_desc_lines.append(line)
    flush()
    return steps


def parse_file(path: str) -> List[NightStep]:
    """Load and parse a preset night-sheet file."""
    with open(path, "r", encoding="utf-8") as f:
        return _parse_text(f.read())


# ``characters.csv`` uses the team labels in column 2. Map them onto
# ``CharType`` so the loader can bin each row. Both singular and plural
# spellings show up in real CSVs; both are accepted, case-insensitive.
_TEAM_TO_CHAR_TYPE: Dict[str, CharType] = {
    "townsfolk": CharType.TOWNSFOLK,
    "outsider": CharType.OUTSIDER,
    "outsiders": CharType.OUTSIDER,
    "minion": CharType.MINION,
    "minions": CharType.MINION,
    "demon": CharType.DEMON,
    "demons": CharType.DEMON,
    "traveler": CharType.TRAVELER,
    "travelers": CharType.TRAVELER,
    "fabled": CharType.FABLED,
}


def _parse_roster(path: str) -> Dict[CharType, List[str]]:
    """Parse a preset's ``characters.csv`` into ``{CharType: [names]}``.

    Names are kept in CSV order (so script-order survives the roundtrip).
    Unknown teams are silently dropped — the CSV is the authoritative
    list of *what's on the script*; teams the engine doesn't model are
    not part of any caller's enumeration.
    """
    roster: Dict[CharType, List[str]] = {ct: [] for ct in CharType}
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return roster
            field_map = {
                (k or "").strip().lower(): k for k in reader.fieldnames
            }
            name_key = field_map.get("name")
            team_key = field_map.get("team")
            if name_key is None or team_key is None:
                return roster
            for row in reader:
                char_name = (row.get(name_key) or "").strip()
                team = (row.get(team_key) or "").strip().lower()
                if not char_name or not team:
                    continue
                ct = _TEAM_TO_CHAR_TYPE.get(team)
                if ct is None:
                    continue
                # Preserve order; skip exact duplicates.
                if char_name not in roster[ct]:
                    roster[ct].append(char_name)
    except OSError:
        pass
    return roster


@dataclass
class Preset:
    """A loaded preset (Trouble Brewing, Bad Moon Rising, …)."""

    name: str
    first_night: List[NightStep]
    other_nights: List[NightStep]
    # Roster from ``characters.csv``. Empty lists are treated as
    # "this preset doesn't ship a roster" by Engine helpers, which then
    # fall back to the global ``engine.script`` lookup.
    roster: Dict[CharType, List[str]] = field(
        default_factory=lambda: {ct: [] for ct in CharType}
    )

    def order_for_night(self, night_number: int) -> List[NightStep]:
        return self.first_night if night_number == 1 else self.other_nights

    def has_roster(self) -> bool:
        """True if the preset's ``characters.csv`` produced any names."""
        return any(self.roster.get(ct) for ct in CharType)

    def names_by_type(self, char_type: CharType) -> List[str]:
        """Roster names of ``char_type`` in script order. May be empty."""
        return list(self.roster.get(char_type, ()))

    def all_names(self) -> List[str]:
        """Every name on the preset's roster, in script order across types."""
        out: List[str] = []
        for ct in CharType:
            out.extend(self.roster.get(ct, ()))
        return out


def load_preset(presets_root: str, name: str) -> Optional[Preset]:
    """Load a preset by directory name. Returns ``None`` if not found.

    ``presets_root`` is the path to ``assets/presets/``; ``name`` is
    the directory name (e.g. ``"trouble_brewing"``).
    """
    safe = os.path.normpath(name)
    if safe.startswith("..") or os.path.isabs(safe) or "/" in safe:
        return None
    base = os.path.join(presets_root, safe)
    first_path = os.path.join(base, "first_night.txt")
    other_path = os.path.join(base, "night.txt")
    if not (os.path.isfile(first_path) and os.path.isfile(other_path)):
        return None
    first = parse_file(first_path)
    other = parse_file(other_path)
    # ``characters.csv`` is optional — older presets, hand-rolled test
    # fixtures, etc. may omit it. Engine helpers fall back to the
    # global script_data lookup when the roster is empty.
    roster_path = os.path.join(base, "characters.csv")
    roster = (
        _parse_roster(roster_path)
        if os.path.isfile(roster_path)
        else {ct: [] for ct in CharType}
    )
    return Preset(
        name=name,
        first_night=first,
        other_nights=other,
        roster=roster,
    )


def default_presets_root() -> str:
    """Resolve the repo's assets/presets directory."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    return os.path.join(repo_root, "assets", "presets")
