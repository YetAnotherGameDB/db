#!/usr/bin/env python3
"""Crawl superfamicom.org for the Famicom / Super Famicom release catalogue.

This is the *catalogue* leg of the three-way cross-reference used to seed the
famicom / fds / snes(NTSC-J) platforms. It supplies the piece the No-Intro
dumps do not carry for cartridge systems: the printed catalogue number
(HVC-xx, SHVC-xx, and the western SNS-xx-USA / NES-xx-USA equivalents).

Two list roots, each paginated:
  /famicom/game-list[/N]   Famicom + Famicom Disk System
  /game-list[/N]           Super Famicom / SNES

Each list entry gives the slug, the English title, the Japanese title and one
`list-item-serial` per regional release. The per-game /info/<slug> page adds
the release date(s) and publisher, so the crawler follows it for every slug.

Output: <cache>/catalogue.json — a list of
  {slug, source, title_en, title_ja, serials[], release_dates[], publisher}

Usage:
  python fetch-nintendo-catalogue.py [--cache .cache/superfamicom] [--limit N]
                                     [--refresh]

Pages are cached on disk; re-runs only fetch what is missing unless
--refresh is given.
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://superfamicom.org"
UA = {"User-Agent": "Mozilla/5.0 (games-db catalogue crawl; papazark@gmail.com)"}
DELAY = 0.4

REPO = Path(__file__).resolve().parents[2]

# source -> (list root, /info root). The Famicom side of the site lives under
# a /famicom prefix and links to /famicom/info/<slug>.
LIST_ROOTS = {
    "famicom": ("/famicom/game-list", "/famicom/info"),
    "sfc": ("/game-list", "/info"),
}

ITEM_RE = re.compile(r'<div class="game-list-item.*?</div>\s*</div>', re.S)
SLUG_RE = re.compile(r'href="(?:/famicom)?/info/([^"/]+)"')
TITLE_RE = re.compile(r'<div class="list-item-title">\s*<a[^>]*>(.*?)</a>', re.S)
SERIAL_RE = re.compile(r'<div class="list-item-serial">\s*(.*?)\s*</div>', re.S)
PAGE_RE = re.compile(r'href="(?:/famicom)?/game-list/(\d+)"')

# /info/<slug> renders each field as a single element whose children are
# separated by " / " — one slot per regional release, in the same order for
# the serial and date rows (SHVC-AR / SNS-AR-USA <-> 1990-12-16 / 1991-11-01).
ORIGINAL_RE = re.compile(r'<h3 class="original">(.*?)</h3>', re.S)
PRODUCER_RE = re.compile(r'<h3 class="producer">(.*?)</h3>', re.S)
INFO_SERIAL_RE = re.compile(r'<h3 class="serial">(.*?)</h3>', re.S)
INFO_DATE_RE = re.compile(r'<h4 class="date">(.*?)</h4>', re.S)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def strip_tags(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


def fetch(url, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries:
                raise
        except Exception:
            if attempt == retries:
                raise
        time.sleep(2 * (attempt + 1))


def cached(cache_dir, key, url, refresh=False):
    path = cache_dir / f"{key}.html"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    body = fetch(url)
    time.sleep(DELAY)
    if body is None:
        return None
    path.write_text(body, encoding="utf-8")
    return body


def parse_list_page(body):
    """Pull one catalogue row per game-list item."""
    entries = []
    for chunk in ITEM_RE.findall(body):
        slug = SLUG_RE.search(chunk)
        if not slug:
            continue
        titles = [strip_tags(t) for t in TITLE_RE.findall(chunk)]
        titles = [t for t in titles if t]
        serials = [strip_tags(s) for s in SERIAL_RE.findall(chunk)]
        entries.append(
            {
                "slug": slug.group(1),
                "title_en": titles[0] if titles else "",
                # Only present when the site has a Japanese title on file.
                "title_ja": titles[1] if len(titles) > 1 else "",
                "serials": [s for s in serials if s],
            }
        )
    return entries


def split_slots(fragment):
    """Split a ' / '-separated field into one value per regional release."""
    return [p for p in (x.strip() for x in strip_tags(fragment).split("/")) if p]


def parse_info_page(body):
    """Serials, dates, JP title and publisher from a /info/<slug> page.

    Returns serials and dates as parallel lists so a JP serial can be paired
    with the JP release date. The second `h4.date` on the page is the genre,
    not a date, so only the first is read.
    """
    serials = split_slots(INFO_SERIAL_RE.search(body).group(1)) if INFO_SERIAL_RE.search(body) else []
    dates = []
    date_match = INFO_DATE_RE.search(body)
    if date_match:
        dates = [d for d in split_slots(date_match.group(1)) if DATE_RE.match(d)]

    original = ORIGINAL_RE.search(body)
    title_ja = strip_tags(original.group(1)) if original else ""

    producer = PRODUCER_RE.search(body)
    publishers = split_slots(producer.group(1)) if producer else []

    return {
        "info_serials": serials,
        "release_dates": dates,
        "info_title_ja": title_ja,
        "publishers": publishers,
    }


def crawl_list(cache_dir, source, root, refresh):  # noqa: D103
    first = cached(cache_dir, f"list-{source}-1", BASE + root, refresh)
    if first is None:
        sys.exit(f"could not fetch {BASE + root}")
    last = max((int(n) for n in PAGE_RE.findall(first)), default=1)
    entries = parse_list_page(first)
    for page in range(2, last + 1):
        body = cached(cache_dir, f"list-{source}-{page}", f"{BASE}{root}/{page}", refresh)
        if body is None:
            break
        entries.extend(parse_list_page(body))
    print(f"{source}: {last} list pages, {len(entries)} entries")
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(REPO / ".cache" / "superfamicom"))
    ap.add_argument("--limit", type=int, help="stop after N /info pages (smoke test)")
    ap.add_argument("--refresh", action="store_true", help="re-fetch cached pages")
    args = ap.parse_args()

    cache_dir = Path(args.cache)
    cache_dir.mkdir(parents=True, exist_ok=True)

    catalogue, seen = [], {}
    for source, (list_root, info_root) in LIST_ROOTS.items():
        for entry in crawl_list(cache_dir, source, list_root, args.refresh):
            # Slugs are only unique per source; the two lists are separate
            # sites and can reuse a slug for different games.
            key = (source, entry["slug"])
            if key in seen:
                for s in entry["serials"]:
                    if s not in seen[key]["serials"]:
                        seen[key]["serials"].append(s)
                continue
            entry["source"] = source
            entry["info_root"] = info_root
            seen[key] = entry
            catalogue.append(entry)

    todo = catalogue[: args.limit] if args.limit else catalogue
    for n, entry in enumerate(todo, 1):
        key = f"info-{entry['source']}-{entry['slug']}"
        url = f"{BASE}{entry['info_root']}/{entry['slug']}"
        body = cached(cache_dir, key, url, args.refresh)
        entry.update(parse_info_page(body) if body else
                     {"info_serials": [], "release_dates": [], "info_title_ja": "", "publishers": []})
        # The list page can omit a serial slot (blank div) while the info page
        # carries the full set; prefer whichever is longer.
        if len(entry["info_serials"]) > len(entry["serials"]):
            entry["serials"] = entry["info_serials"]
        if not entry["title_ja"]:
            entry["title_ja"] = entry["info_title_ja"]
        if n % 250 == 0:
            print(f"  ...{n}/{len(todo)} info pages")

    out = cache_dir / "catalogue.json"
    out.write_text(json.dumps(catalogue, ensure_ascii=False, indent=1), encoding="utf-8")

    with_serial = sum(1 for e in catalogue if e["serials"])
    with_ja = sum(1 for e in catalogue if e["title_ja"])
    with_date = sum(1 for e in catalogue if e.get("release_dates"))
    print(f"\n{len(catalogue)} catalogue entries -> {out}")
    print(f"  with serial: {with_serial}   with JP title: {with_ja}   with date: {with_date}")


if __name__ == "__main__":
    main()
