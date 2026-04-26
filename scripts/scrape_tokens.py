#!/usr/bin/env python3
"""Scrape Blood on the Clocktower token images and clean them up.

Given one or more bloodontheclocktower.com collection URLs (e.g. character
or reminder token pages), this:

  1. Downloads every Shopify CDN image referenced from the page(s).
  2. Removes the white page background by mapping near-white pixels to
     fully transparent (with a soft alpha ramp for anti-aliased edges).
  3. Crops each image to the bounding box of its non-transparent pixels.
  4. Writes the result as a PNG in the chosen output directory.

The defaults target the Trouble Brewing reminder + character token
collections and write to ``assets/tokens/``.

Usage:
    python3 scripts/scrape_tokens.py
    python3 scripts/scrape_tokens.py --url https://... --url https://...
    python3 scripts/scrape_tokens.py --out path/to/dir
    python3 scripts/scrape_tokens.py --keep-jpgs    # don't delete originals

Requires: Pillow, numpy. (`pip install Pillow numpy`)

Note: token artwork is © The Pandemonium Institute. Use responsibly —
fine for personal/local tooling, not for redistribution.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Iterable, List, Tuple

try:
    import numpy as np
    from PIL import Image
except ImportError as e:  # pragma: no cover - friendly error path
    sys.stderr.write(
        f"Missing dependency: {e.name}. Run: pip install Pillow numpy\n"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_URLS = [
    # Reminder tokens (2 pages)
    "https://bloodontheclocktower.com/collections/trouble-brewing-reminder-tokens?page=1",
    "https://bloodontheclocktower.com/collections/trouble-brewing-reminder-tokens?page=2",
    # Character tokens (2 pages)
    "https://bloodontheclocktower.com/collections/trouble-brewing-character-tokens?page=1",
    "https://bloodontheclocktower.com/collections/trouble-brewing-character-tokens?page=2",
]

# Default destination relative to repo root (parent of this script's dir).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(_REPO_ROOT, "assets", "tokens")

USER_AGENT = (
    "Mozilla/5.0 (compatible; botc-token-scraper/1.0; "
    "+https://github.com/)"
)

# Shopify CDN serves a tiny default when no ``width=`` is set on the
# query string. Force a width so we get sharp source art to crop from.
# 533 ≈ the per-product detail size on the store; gives ~220px reminder
# tokens and ~380px character tokens after crop — small enough to ship
# to a phone, sharp enough at typical UI sizes. Pass --width 1500 (or
# higher) for source-resolution art.
DEFAULT_WIDTH = 533

# Whiteness thresholds for the soft-alpha ramp. Pixels with min(R,G,B)
# above HIGH become fully transparent; below LOW stay opaque; in between
# we ramp linearly so the cropped circle has a smooth edge.
WHITE_HIGH = 245
WHITE_LOW = 220

# Only consider images on the official CDN to avoid grabbing analytics
# pixels or theme assets.
CDN_HOST = "bloodontheclocktower.com"

# All Shopify product images for these tokens are uploaded as
# ``<id>-<Token_Name>.jpg`` (or .png). Restricting to that prefix is the
# cleanest way to ignore site chrome (logos, banners, backgrounds, the
# F1 grimoire cover, favicons, etc.) without an ever-growing blacklist.
_TOKEN_NAME_RE = re.compile(r"^\d+[-_].+\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Page scraping
# ---------------------------------------------------------------------------

# Match Shopify CDN refs whether they appear as absolute URLs, protocol-
# relative ("//host/..."), or just paths ("cdn/shop/files/...").
_IMG_RE = re.compile(
    r'(?:https?://' + re.escape(CDN_HOST) + r'|//' + re.escape(CDN_HOST)
    + r'|(?<![\w/]))'
    r'/?cdn/shop/files/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|webp)'
    r'(?:\?[^\s"\')<>]*)?',
    re.IGNORECASE,
)


def _normalize_image_url(raw: str) -> str:
    """Force the matched ref to a fully-qualified https URL."""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("/"):
        return "https://" + CDN_HOST + raw
    return "https://" + CDN_HOST + "/" + raw


def _with_width(url: str, width: int) -> str:
    """Set/override the Shopify ``width=`` query parameter."""
    parts = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for (k, v) in query if k.lower() != "width"]
    query.append(("width", str(width)))
    return urllib.parse.urlunparse(
        parts._replace(query=urllib.parse.urlencode(query))
    )


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _scrape_image_urls(page_urls: Iterable[str]) -> List[str]:
    """Find token-art image URLs across one or more collection pages."""
    seen = set()
    ordered: List[str] = []
    for page_url in page_urls:
        try:
            html = _http_get(page_url).decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"  ! failed to fetch {page_url}: {e}", file=sys.stderr)
            continue
        for m in _IMG_RE.finditer(html):
            url = _normalize_image_url(m.group(0))
            # Filename without query string for filtering / deduping.
            path = urllib.parse.urlparse(url).path
            base = os.path.basename(path)
            if not _TOKEN_NAME_RE.match(base):
                continue
            # Dedupe by filename (strip the ?v=... cache-buster).
            if base in seen:
                continue
            seen.add(base)
            ordered.append(_with_width(url, DEFAULT_WIDTH))
    return ordered


# ---------------------------------------------------------------------------
# Filename normalisation
# ---------------------------------------------------------------------------

# Strip the leading "<num>-" Shopify product code, lowercase, and turn
# spaces / underscores into a clean snake_case name.
_NUM_PREFIX_RE = re.compile(r"^\d+[-_]")

# Newer Shopify uploads append a UUID (e.g. "Po_16d323e6-5f8e-4164-b686-
# fffc06b8612e") for cache-busting. Strip it so different copies of the
# same token collapse to one filename.
_UUID_SUFFIX_RE = re.compile(
    r"[_-][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# Apostrophes in the source filenames (e.g. "Devil's_Advocate") get
# escaped/encoded to underscores (e.g. "Devil_s_Advocate"), which we
# then want to flatten back to "devils_advocate".
_POSSESSIVE_RE = re.compile(r"_s_", re.IGNORECASE)


def _output_name(url: str) -> str:
    base = os.path.basename(urllib.parse.urlparse(url).path)
    stem, _ext = os.path.splitext(base)
    stem = _NUM_PREFIX_RE.sub("", stem)
    stem = _UUID_SUFFIX_RE.sub("", stem)
    # "Butler_Reminder_Master" -> "butler_master"
    # "Fortune_Teller_Reminder_Red_Herring" -> "fortune_teller_red_herring"
    stem = re.sub(r"_Reminder_", "_", stem, flags=re.IGNORECASE)
    # "Devil_s_Advocate" -> "Devils_Advocate"
    stem = _POSSESSIVE_RE.sub("s_", stem)
    stem = stem.lower().replace(" ", "_")
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem + ".png"


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------


def _process_image(raw: bytes, high: int, low: int) -> Image.Image:
    """White-to-transparent + crop. Returns a new RGBA PIL image."""
    from io import BytesIO
    img = Image.open(BytesIO(raw)).convert("RGBA")
    arr = np.array(img)
    rgb = arr[:, :, :3].astype(np.int32)
    # "Whiteness" = the dimmest channel — only fully white when *all*
    # channels are bright, which avoids eating saturated colours.
    whiteness = rgb.min(axis=2)
    span = max(high - low, 1)
    alpha = np.clip((high - whiteness) * 255.0 / span, 0, 255).astype(np.uint8)
    arr[:, :, 3] = alpha

    mask = alpha > 0
    if not mask.any():
        return Image.fromarray(arr, mode="RGBA")
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    top, bottom = rows[0], rows[-1] + 1
    left, right = cols[0], cols[-1] + 1
    return Image.fromarray(arr[top:bottom, left:right], mode="RGBA")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run(
    page_urls: List[str],
    out_dir: str,
    *,
    high: int = WHITE_HIGH,
    low: int = WHITE_LOW,
    overwrite: bool = True,
    sleep_between: float = 0.0,
    width: int = DEFAULT_WIDTH,
) -> Tuple[int, int]:
    """Scrape, process, write. Returns ``(downloaded, skipped)`` counts."""
    # Threaded through the module-level constant via _with_width; rebuild
    # the URL list with the requested width if the caller overrode it.
    os.makedirs(out_dir, exist_ok=True)
    print(f"Scraping {len(page_urls)} page(s)…")
    image_urls = _scrape_image_urls(page_urls)
    if width != DEFAULT_WIDTH:
        image_urls = [_with_width(u, width) for u in image_urls]
    print(f"Found {len(image_urls)} candidate image(s).")

    downloaded = 0
    skipped = 0
    for url in image_urls:
        out_name = _output_name(url)
        out_path = os.path.join(out_dir, out_name)
        if os.path.exists(out_path) and not overwrite:
            print(f"  skip (exists) {out_name}")
            skipped += 1
            continue
        try:
            raw = _http_get(url)
            img = _process_image(raw, high=high, low=low)
            img.save(out_path, "PNG", optimize=True)
        except Exception as e:
            print(f"  ! failed {out_name}: {e}", file=sys.stderr)
            skipped += 1
            continue
        print(f"  {out_name}  {img.size[0]}x{img.size[1]}")
        downloaded += 1
        if sleep_between > 0:
            time.sleep(sleep_between)
    return downloaded, skipped


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape & clean Blood on the Clocktower token images.",
    )
    p.add_argument(
        "--url",
        action="append",
        dest="urls",
        metavar="URL",
        help=(
            "Collection page URL to scrape. May be passed multiple times. "
            "Defaults to the four Trouble Brewing token pages."
        ),
    )
    p.add_argument(
        "--out",
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    p.add_argument(
        "--white-high",
        type=int,
        default=WHITE_HIGH,
        help="Pixels with min channel above this go fully transparent.",
    )
    p.add_argument(
        "--white-low",
        type=int,
        default=WHITE_LOW,
        help="Pixels with min channel below this stay fully opaque.",
    )
    p.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Skip any output file that already exists.",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep between downloads (be polite).",
    )
    p.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=(
            "Shopify width param for downloaded source art. Higher = "
            "sharper input to crop from. Default: %(default)s."
        ),
    )
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    page_urls = args.urls or list(DEFAULT_URLS)
    if args.white_low >= args.white_high:
        print("--white-low must be < --white-high", file=sys.stderr)
        return 2
    downloaded, skipped = run(
        page_urls,
        args.out,
        high=args.white_high,
        low=args.white_low,
        overwrite=not args.no_overwrite,
        sleep_between=args.sleep,
        width=args.width,
    )
    print(f"Done. Wrote {downloaded}, skipped {skipped}. -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
