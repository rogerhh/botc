#!/usr/bin/env python3
"""Print Blood on the Clocktower wiki character pages to PDF.

Renders each character's wiki page with headless Chromium and saves
``<Wiki_Title>.pdf`` to ``assets/characters/`` — the goal is byte-for-
byte the same output you'd get by opening the page in Chrome and
pressing Ctrl+P with the default save-as-PDF settings.

We use headless Chromium (the one Playwright bundles) over
``--print-to-pdf`` because:

  1. It runs the page's actual print stylesheet (``hide-for-print``
     classes, etc.) instead of approximating it.
  2. It executes JavaScript before rendering, so deferred images and
     MediaWiki's ``mw-collapsible`` content land in the PDF.
  3. WeasyPrint (our previous approach) ignored several of the wiki's
     print rules and ended up baking the entire sidebar nav into every
     character sheet.

If Playwright/Chromium isn't installed, this auto-installs both into
the user's home dir on first run.

Usage:
    python3 scripts/print_character_pdfs.py
    python3 scripts/print_character_pdfs.py --preset bad_moon_rising
    python3 scripts/print_character_pdfs.py --char Grandmother --char Sailor
    python3 scripts/print_character_pdfs.py --no-overwrite

The default preset is the one passed via ``--preset``; without it the
script reads every directory under ``assets/presets/`` and prints the
union of their characters.csv entries.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Optional, Tuple


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PRESETS_DIR = os.path.join(_REPO_ROOT, "assets", "presets")
DEFAULT_OUT_DIR = os.path.join(_REPO_ROOT, "assets", "characters")
WIKI_BASE = "https://wiki.bloodontheclocktower.com"

# Playwright's bundled Chromium lives under ~/.cache/ms-playwright/.
# Pin to the build it ships with the version we install to keep the
# binary path predictable across machines.
_PLAYWRIGHT_CACHE = os.path.expanduser("~/.cache/ms-playwright")


# ---------------------------------------------------------------------------
# Character name -> wiki URL / output filename
# ---------------------------------------------------------------------------


def _wiki_title(name: str) -> str:
    """'Tea Lady' -> 'Tea_Lady', 'Devil's Advocate' -> 'Devil%27s_Advocate'."""
    # MediaWiki encodes spaces as underscores and percent-encodes the rest.
    return urllib.parse.quote(name.replace(" ", "_"), safe="_")


def _output_name(name: str) -> str:
    """'Tea Lady' -> 'Tea_Lady.pdf', 'Devil's Advocate' -> 'Devils_Advocate.pdf'.

    Apostrophes get stripped from the output filename so it stays
    filesystem-friendly across platforms.
    """
    stem = name.replace("'", "").replace(" ", "_")
    return stem + ".pdf"


def _load_preset(preset_dir: str) -> List[str]:
    """Read the names from a preset's characters.csv."""
    names: List[str] = []
    csv_path = os.path.join(preset_dir, "characters.csv")
    if not os.path.isfile(csv_path):
        return names
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n = (row.get("name") or "").strip()
            if n:
                names.append(n)
    return names


def _all_preset_characters(presets_root: str, preset: Optional[str]) -> List[str]:
    if preset:
        return _load_preset(os.path.join(presets_root, preset))
    seen = set()
    out: List[str] = []
    if not os.path.isdir(presets_root):
        return out
    for entry in sorted(os.listdir(presets_root)):
        sub = os.path.join(presets_root, entry)
        if not os.path.isdir(sub):
            continue
        for name in _load_preset(sub):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# Chromium discovery / install
# ---------------------------------------------------------------------------


def _find_chromium() -> Optional[str]:
    """Return a path to a usable Chromium/Chrome binary, or None."""
    # 1. Anything on PATH.
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        path = shutil.which(candidate)
        if path:
            return path
    # 2. A pre-extracted Chrome under <repo>/.cache/ (we ship one here in
    # ephemeral build environments to avoid re-downloading every run).
    workspace_chrome = os.path.join(
        _REPO_ROOT, ".cache", "chrome-extracted", "opt", "google", "chrome", "google-chrome"
    )
    if os.path.isfile(workspace_chrome):
        return workspace_chrome
    # 3. Playwright's bundled build.
    if os.path.isdir(_PLAYWRIGHT_CACHE):
        for entry in sorted(os.listdir(_PLAYWRIGHT_CACHE)):
            if not entry.startswith("chromium-"):
                continue
            for sub in (
                "chrome-linux/chrome",
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            ):
                p = os.path.join(_PLAYWRIGHT_CACHE, entry, sub)
                if os.path.isfile(p):
                    return p
    return None


def _ensure_chromium() -> str:
    """Locate Chromium, installing the Playwright build if needed."""
    found = _find_chromium()
    if found:
        return found
    print("Chromium not found — installing via Playwright (one-time, ~150MB)…")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--user", "--quiet", "playwright"]
    )
    # Use the user-installed playwright CLI to fetch the browser.
    pw = os.path.expanduser("~/.local/bin/playwright")
    if not os.path.isfile(pw):
        # Fall back to running it as a module.
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    else:
        subprocess.check_call([pw, "install", "chromium"])
    found = _find_chromium()
    if not found:
        raise RuntimeError("Chromium install reported success but binary not found.")
    return found


# ---------------------------------------------------------------------------
# Print one page
# ---------------------------------------------------------------------------


def _print_one(
    chrome: str,
    name: str,
    out_dir: str,
    *,
    overwrite: bool = True,
    timeout_s: float = 30.0,
) -> Tuple[str, bool, str]:
    """Render ``name``'s wiki page to PDF. Returns (name, ok, message)."""
    out_path = os.path.join(out_dir, _output_name(name))
    if os.path.exists(out_path) and not overwrite:
        return name, True, "skip (exists)"

    url = f"{WIKI_BASE}/{_wiki_title(name)}"
    args = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        # Keep network sane — some pages pull from cdn.* on first load.
        f"--virtual-time-budget={int(timeout_s * 1000)}",
        f"--print-to-pdf={out_path}",
        # Default Chrome Ctrl+P leaves "Background graphics" off; matching
        # that produces a cleaner sheet (white background, no banners).
        # Pass --no-pdf-header-footer if a future Chrome ever defaults it on.
        "--no-pdf-header-footer",
        url,
    ]
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_s + 15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return name, False, "timeout"
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1024:
        msg = (result.stderr.decode("utf-8", "ignore").strip().splitlines() or ["no pdf"])[-1]
        return name, False, msg[:120]
    return name, True, f"ok ({os.path.getsize(out_path) // 1024} KB)"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run(
    names: Iterable[str],
    out_dir: str,
    *,
    overwrite: bool = True,
    workers: int = 4,
) -> Tuple[int, int]:
    chrome = _ensure_chromium()
    os.makedirs(out_dir, exist_ok=True)
    names = list(names)
    print(f"Printing {len(names)} character page(s) -> {out_dir}")

    ok = 0
    fail = 0
    # Each chrome --print-to-pdf is a fresh process, so a small pool gives
    # us nice parallelism without risking the shared profile lock.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {
            pool.submit(_print_one, chrome, n, out_dir, overwrite=overwrite): n
            for n in names
        }
        for fut in as_completed(futs):
            name, success, msg = fut.result()
            tag = "ok " if success else "FAIL"
            print(f"  [{tag}] {name}: {msg}")
            if success:
                ok += 1
            else:
                fail += 1
    return ok, fail


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--preset",
        help=(
            "Name of a preset directory under assets/presets/ "
            "(e.g. 'bad_moon_rising'). Default: union of all presets."
        ),
    )
    p.add_argument(
        "--char",
        action="append",
        dest="chars",
        metavar="NAME",
        help="Specific character name (repeatable). Overrides --preset.",
    )
    p.add_argument("--out", default=DEFAULT_OUT_DIR, help="Output directory.")
    p.add_argument(
        "--presets-dir",
        default=DEFAULT_PRESETS_DIR,
        help="Where to look for preset directories.",
    )
    p.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Skip any output PDF that already exists.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel chromium processes (default: %(default)s).",
    )
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.chars:
        names = list(args.chars)
    else:
        names = _all_preset_characters(args.presets_dir, args.preset)
    if not names:
        print("No characters to print.", file=sys.stderr)
        return 1
    start = time.time()
    ok, fail = run(
        names,
        args.out,
        overwrite=not args.no_overwrite,
        workers=args.workers,
    )
    dur = time.time() - start
    print(f"Done. {ok} ok, {fail} fail in {dur:.1f}s -> {args.out}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
