"""Preset (script) parser.

Each preset under ``assets/presets/<name>/`` ships with two text files
that describe the *order in which the storyteller wakes characters* on
the first night and on subsequent nights:

  * ``first_night.txt`` — the first-night order (Dusk, Minion Info,
    Demon Info, Poisoner, Washerwoman, …, Dawn).
  * ``night.txt`` — every-other-night order (Dusk, Poisoner, Monk, …,
    Dawn).

Each entry occupies two lines (a name, then its storyteller-facing
description) separated from the next entry by a blank line. Example::

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

This module is the canonical source of *night order*. The Character
class still carries ``first_night_order`` and ``other_night_order`` for
backwards compatibility (and for editions whose preset files are
missing), but when a preset is supplied it overrides those numbers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


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


@dataclass
class Preset:
    """A loaded preset (Trouble Brewing, Bad Moon Rising, …)."""

    name: str
    first_night: List[NightStep]
    other_nights: List[NightStep]

    def order_for_night(self, night_number: int) -> List[NightStep]:
        return self.first_night if night_number == 1 else self.other_nights


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
    return Preset(name=name, first_night=first, other_nights=other)


def default_presets_root() -> str:
    """Resolve the repo's assets/presets directory."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    return os.path.join(repo_root, "assets", "presets")
