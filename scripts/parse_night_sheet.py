#!/usr/bin/env python3
"""Parse the night-sheet section of a script.pdf into plain-text files.

The official Pandemonium Institute "script" PDFs (one per edition, e.g.
``trouble_brewing`` or ``bad_moon_rising``) end with two pages titled
``FIRST NIGHT`` and ``OTHER NIGHTS``. Each page lists the wake-order
entries — a name (a character, or one of the special hooks like
``Dusk``, ``Minion Info``, ``Demon Info``, ``Dawn``) followed by the
storyteller instruction for that step.

This script extracts both sections and writes them next to the source
PDF as ``first_night.txt`` and ``night.txt``::

    Dusk
    Start the Night Phase.

    Minion Info
    If there are 7 or more players, wake all Minions: ...

    Poisoner
    The Poisoner chooses a player.
    ...

The wake order in the file matches the order on the official sheet —
the engine can read it line-by-line to drive the night phase.

Usage:
    python3 scripts/parse_night_sheet.py
    python3 scripts/parse_night_sheet.py --preset trouble_brewing
    python3 scripts/parse_night_sheet.py --pdf path/to/script.pdf \
        --characters path/to/characters.csv \
        --out-dir path/to/out

We shell out to ``pdftotext`` (from poppler-utils) for extraction. It's
available on virtually every Linux distro and macOS via Homebrew, and
its plain-mode output is laid out very conveniently for this format —
each entry name lands on its own line, separated from the next by the
description body. We just have to detect which lines are *names* (we
match them against the character roster + a small set of special
keywords) and group everything between them into the body.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PRESETS_DIR = os.path.join(_REPO_ROOT, "assets", "presets")

# Section markers as they appear on the night-sheet pages.
FIRST_NIGHT_HEADER = "FIRST NIGHT"
OTHER_NIGHTS_HEADER = "OTHER NIGHTS"

# Footer / page-end markers we use to bound a section.
_SECTION_END_PATTERNS = (
    "© Steven Medway",
    "(c) Steven Medway",
)

# Names that appear on the night sheet but aren't characters.
SPECIAL_KEYWORDS = ("Dusk", "Minion Info", "Demon Info", "Dawn")

# Poppler emits the ``ﬁ`` ligature as the U+FFFD replacement char (the
# font's CMap doesn't include a unicode mapping for the glyph). Across
# both shipped scripts the ligature is *always* "fi" — words like
# "first", "finger", "Pacifist". Other private-use icons (the night-
# order moon/sun glyphs printed next to character names on the front
# page) come through as U+F111 etc.; we drop them.
_LIGATURE_FIXUPS = {
    "\ufffd": "fi",
}
_PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def _run_pdftotext(pdf_path: str) -> str:
    """Return the plain-text extraction of ``pdf_path`` via ``pdftotext``."""
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext not found on PATH. Install poppler-utils "
            "(`apt install poppler-utils` / `brew install poppler`)."
        )
    # ``-`` writes to stdout. We pass ``-enc UTF-8`` explicitly so the
    # ligature replacement char is a stable U+FFFD across systems.
    result = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", pdf_path, "-"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8", errors="replace")


def _normalise(text: str) -> str:
    """Patch up ligatures and strip private-use glyphs from pdftotext output."""
    for bad, good in _LIGATURE_FIXUPS.items():
        text = text.replace(bad, good)
    text = _PRIVATE_USE_RE.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Character roster
# ---------------------------------------------------------------------------


def _load_characters(csv_path: str) -> List[str]:
    names: List[str] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n = (row.get("name") or "").strip()
            if n:
                names.append(n)
    return names


# ---------------------------------------------------------------------------
# Section + entry parsing
# ---------------------------------------------------------------------------


def _slice_section(lines: List[str], header: str) -> List[str]:
    """Return the lines between ``header`` and the next end-of-section marker.

    We accept any line that strips to exactly the header (case-sensitive,
    matching the PDF) and stop at the next header / copyright footer.
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        return []

    out: List[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped in (FIRST_NIGHT_HEADER, OTHER_NIGHTS_HEADER):
            break
        if any(stripped.startswith(p) for p in _SECTION_END_PATTERNS):
            break
        out.append(line)
    return out


def _is_name_line(stripped: str, name_set: set) -> bool:
    """Decide whether a non-empty stripped line is an entry-name line.

    A line is an entry name iff it's an *exact* match against either a
    known character or one of the special keywords. The descriptions
    themselves tend to be full sentences (``"The Imp chooses a
    player."``), so an exact-match check is plenty discriminating.
    """
    return stripped in name_set


def _parse_entries(
    section_lines: List[str], name_set: set
) -> List[Tuple[str, str]]:
    """Group section lines into ``(name, body)`` pairs in source order.

    pdftotext's plain mode emits each name on its own line and wraps the
    description body across one or more lines. We treat *any* line that
    isn't a name-line as part of the current entry's body, then collapse
    runs of whitespace at write time.
    """
    entries: List[Tuple[str, List[str]]] = []
    current_name: Optional[str] = None
    current_body: List[str] = []

    def flush():
        if current_name is not None:
            entries.append((current_name, list(current_body)))

    for raw in section_lines:
        stripped = raw.strip()
        if not stripped:
            # Blank lines just separate entries / wrap descriptions —
            # we don't need to keep them in the body.
            continue
        if _is_name_line(stripped, name_set):
            flush()
            current_name = stripped
            current_body = []
        else:
            if current_name is None:
                # Stray text before the first name-line (e.g. orphaned
                # header chrome). Skip it rather than misattribute.
                continue
            current_body.append(stripped)

    flush()

    # Collapse multi-line descriptions into a single space-joined line —
    # the pdftotext line wraps come from column width, not semantics.
    return [(n, " ".join(body).strip()) for n, body in entries]


def _format_entries(entries: Iterable[Tuple[str, str]]) -> str:
    chunks = []
    for name, body in entries:
        chunks.append(f"{name}\n{body}".rstrip())
    return "\n\n".join(chunks) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def parse_night_sheet(
    pdf_path: str,
    characters: List[str],
) -> Dict[str, List[Tuple[str, str]]]:
    """Return ``{"first_night": [...], "night": [...]}`` for one PDF."""
    text = _normalise(_run_pdftotext(pdf_path))
    lines = text.splitlines()

    name_set = set(characters) | set(SPECIAL_KEYWORDS)

    return {
        "first_night": _parse_entries(
            _slice_section(lines, FIRST_NIGHT_HEADER), name_set
        ),
        "night": _parse_entries(
            _slice_section(lines, OTHER_NIGHTS_HEADER), name_set
        ),
    }


def _process_preset(preset_dir: str) -> Tuple[int, int]:
    pdf = os.path.join(preset_dir, "script.pdf")
    csv_path = os.path.join(preset_dir, "characters.csv")
    if not os.path.isfile(pdf):
        print(f"  ! no script.pdf in {preset_dir}", file=sys.stderr)
        return 0, 0
    if not os.path.isfile(csv_path):
        print(f"  ! no characters.csv in {preset_dir}", file=sys.stderr)
        return 0, 0

    characters = _load_characters(csv_path)
    sections = parse_night_sheet(pdf, characters)

    first_path = os.path.join(preset_dir, "first_night.txt")
    night_path = os.path.join(preset_dir, "night.txt")

    with open(first_path, "w", encoding="utf-8") as f:
        f.write(_format_entries(sections["first_night"]))
    with open(night_path, "w", encoding="utf-8") as f:
        f.write(_format_entries(sections["night"]))

    n_first = len(sections["first_night"])
    n_night = len(sections["night"])
    print(
        f"  {os.path.basename(preset_dir)}: "
        f"first_night={n_first} entries, night={n_night} entries"
    )
    return n_first, n_night


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--preset",
        help=(
            "Specific preset directory under assets/presets/ "
            "(e.g. 'trouble_brewing'). Default: every preset present."
        ),
    )
    p.add_argument(
        "--presets-dir",
        default=DEFAULT_PRESETS_DIR,
        help=f"Preset root (default: {DEFAULT_PRESETS_DIR}).",
    )
    p.add_argument(
        "--pdf",
        help="Process a single script.pdf at this path (overrides --preset).",
    )
    p.add_argument(
        "--characters",
        help="Path to a characters.csv (required when --pdf is used).",
    )
    p.add_argument(
        "--out-dir",
        help=(
            "Where to write first_night.txt / night.txt when --pdf is used. "
            "Defaults to the directory containing the PDF."
        ),
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # Ad-hoc single-PDF mode.
    if args.pdf:
        if not args.characters:
            print("--characters is required when using --pdf", file=sys.stderr)
            return 2
        out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.pdf))
        os.makedirs(out_dir, exist_ok=True)
        characters = _load_characters(args.characters)
        sections = parse_night_sheet(args.pdf, characters)
        with open(os.path.join(out_dir, "first_night.txt"), "w", encoding="utf-8") as f:
            f.write(_format_entries(sections["first_night"]))
        with open(os.path.join(out_dir, "night.txt"), "w", encoding="utf-8") as f:
            f.write(_format_entries(sections["night"]))
        print(
            f"Wrote first_night.txt ({len(sections['first_night'])} entries) "
            f"and night.txt ({len(sections['night'])} entries) -> {out_dir}"
        )
        return 0

    # Preset-batch mode.
    presets_root = args.presets_dir
    if not os.path.isdir(presets_root):
        print(f"presets dir not found: {presets_root}", file=sys.stderr)
        return 1

    if args.preset:
        targets = [os.path.join(presets_root, args.preset)]
    else:
        targets = sorted(
            os.path.join(presets_root, e)
            for e in os.listdir(presets_root)
            if os.path.isdir(os.path.join(presets_root, e))
        )

    print(f"Processing {len(targets)} preset(s):")
    fail = 0
    for preset_dir in targets:
        try:
            _process_preset(preset_dir)
        except Exception as e:
            fail += 1
            print(f"  ! {os.path.basename(preset_dir)}: {e}", file=sys.stderr)
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
