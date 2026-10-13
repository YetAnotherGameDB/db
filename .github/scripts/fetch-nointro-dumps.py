#!/usr/bin/env python3
"""Harvest the No-Intro dump lists for Famicom / FDS / Super Famicom.

This is the *dump* leg of the three-way cross-reference. No-Intro is the
authority on which cartridges and disks actually exist and how their titles
romanize; for the Family Computer Disk System it also carries the 3-letter
disk serial, which is the suffix of the printed catalogue code (Dead Zone =
SSD-DZN <-> No-Intro serial "DZN").

DATs come from the libretro mirror of DAT-o-MATIC, which publishes them as
plain files (datomatic.no-intro.org itself is behind a form POST).

Only Japanese retail releases are kept: `region "Japan"`, with prototypes,
betas, demos, samples, unlicensed and aftermarket dumps filtered out, and the
one-row-per-dump layout collapsed to one row per game (the NES and FDS DATs
list the same game once per file format, e.g. .fds and .qd).

Output: <cache>/dumps.json — a list of
  {platform, name, region, serial, crc, sha1}

Usage:
  python fetch-nointro-dumps.py [--cache .cache/nointro] [--refresh]
"""

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (games-db dump harvest; papazark@gmail.com)"}
MIRROR = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro"

REPO = Path(__file__).resolve().parents[2]

# DAT name -> platform key in games.json enums. Super Famicom rides on the
# existing `snes` platform (NTSC-J region); Famicom carts on `famicom`.
DATS = {
    "Nintendo - Nintendo Entertainment System": "famicom",
    "Nintendo - Family Computer Disk System": "fds",
    "Nintendo - Super Nintendo Entertainment System": "snes",
}

GAME_RE = re.compile(r"^game \(\s*$")
FIELD_RE = re.compile(r'^\s*(\w+) "?([^"\n]*)"?\s*$')
ROM_RE = re.compile(r"^\s*rom \( (.*) \)\s*$")
ATTR_RE = re.compile(r'(\w+) (?:"([^"]*)"|(\S+))')

# Anything in a No-Intro name that means "not an original boxed retail release":
# prototypes, plus the many later digital/compilation re-dumps of the same ROM.
EXCLUDE_TAGS = re.compile(
    r"\((?:Beta|Proto|Possible Proto|Sample|Demo|Debug|Program|Test Program|Unl|Pirate|"
    r"Aftermarket|Hack|Competition Cart|Kiosk|FamicomBox|Disk Writer|"
    r"[^)]*Virtual Console|Switch Online|Classic Mini|Mini|NP|e-Reader|GameCube|"
    r"Namcot Collection|Animal Crossing|Capcom Town|Retro-Bit Generations|"
    r"Columbus Circle|The Cowabunga Collection|[^)]*Collection)\b[^)]*\)",
    re.I,
)
BIOS_RE = re.compile(r"\[BIOS\]", re.I)

# Revisions of one physical release: same catalogue number, same barcode.
REVISION_RE = re.compile(r"\s*\((?:Rev [0-9A-Z]+|Alt|v[\d.]+|DV \d+)\)", re.I)
LANG_RE = re.compile(r"\((En|Ja|(?:En|Ja|Fr|De|Es|It|Zh|Ko)(?:,(?:En|Ja|Fr|De|Es|It|Zh|Ko))+)\)")
REGION_PAREN_RE = re.compile(r"\s*\((?:Japan|Japan, USA|Japan, Europe|World)\)")


def fetch(url, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))


def parse_dat(text):
    """Yield one dict per `game (...)` block in a clrmamepro DAT."""
    game = None
    for line in text.splitlines():
        if GAME_RE.match(line):
            game = {"roms": []}
            continue
        if game is None:
            continue
        if line.startswith(")"):
            yield game
            game = None
            continue
        rom = ROM_RE.match(line)
        if rom:
            attrs = {k: (q or u) for k, q, u in ATTR_RE.findall(rom.group(1))}
            game["roms"].append(attrs)
            continue
        field = FIELD_RE.match(line)
        if field:
            game[field.group(1)] = field.group(2)


def title_of(name):
    """The release title: No-Intro name minus region, language and revision tags."""
    stripped = REVISION_RE.sub("", name)
    stripped = LANG_RE.sub("", stripped)
    stripped = REGION_PAREN_RE.sub("", stripped)
    return re.sub(r"\s{2,}", " ", stripped).strip()


def harvest(text, platform):
    """Japanese retail releases only, one row per physical release.

    The DATs list a game once per file format (.fds/.qd, .nes/.unh) and once
    per revision; both collapse onto the title, since a revision ships under
    the same catalogue number and barcode.
    """
    by_title = {}
    for game in parse_dat(text):
        name = game.get("name", "")
        if not name or BIOS_RE.search(name) or EXCLUDE_TAGS.search(name):
            continue
        if not game.get("region", "").startswith("Japan"):
            continue

        title = title_of(name)
        lang = LANG_RE.search(name)
        rom = game["roms"][0] if game["roms"] else {}
        row = by_title.setdefault(
            title,
            {
                "platform": platform,
                "title": title,
                "name": name,
                "region": game["region"],
                "language": f"[{lang.group(1)}]" if lang else "[Ja]",
                "serial": game.get("serial", ""),
                "crc": rom.get("crc", ""),
                "sha1": rom.get("sha1", ""),
                "dumps": 0,
            },
        )
        row["dumps"] += 1
        if not row["serial"] and game.get("serial"):
            row["serial"] = game["serial"]
    return list(by_title.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(REPO / ".cache" / "nointro"))
    ap.add_argument("--refresh", action="store_true", help="re-download the DATs")
    args = ap.parse_args()

    cache_dir = Path(args.cache)
    cache_dir.mkdir(parents=True, exist_ok=True)

    dumps = []
    for dat_name, platform in DATS.items():
        path = cache_dir / f"{dat_name}.dat"
        if path.exists() and not args.refresh:
            text = path.read_text(encoding="utf-8")
        else:
            text = fetch(f"{MIRROR}/{urllib.parse.quote(dat_name)}.dat")
            path.write_text(text, encoding="utf-8")
        rows = harvest(text, platform)
        version = re.search(r'version "([^"]+)"', text)
        print(f"{platform:8s} {dat_name} (v{version.group(1) if version else '?'}): "
              f"{len(rows)} Japanese retail titles")
        dumps.extend(rows)

    out = cache_dir / "dumps.json"
    out.write_text(json.dumps(dumps, ensure_ascii=False, indent=1), encoding="utf-8")
    with_serial = sum(1 for d in dumps if d["serial"])
    print(f"\n{len(dumps)} dumps -> {out}  ({with_serial} carry a No-Intro serial)")


if __name__ == "__main__":
    main()
