#!/usr/bin/env python3
"""Fill missing PSP instance releaseDate from a public PSP games list.

The table has one row per title with North America / Europe / Asia-Japan / Australia dates.
A row is used only when its normalized title equals exactly one database game's (or one of its
instances') normalized name. Existing dates are never overwritten. Mapping:
  North America -> region "North America",  Europe -> "Europe",  Asia/Japan -> "Japan" only
and only for a region holding a single PSP instance of that game (the Asia/Korea regions are skipped: the column does not say which of them the date is for).

Usage: python enrich-psp-dates.py [--apply]
"""
import argparse, json, re, subprocess, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GAMES = REPO / "games.json"
CACHE = REPO / ".cache" / "psp-list.json"
URL = ("https://en.wikipedia.org/w/api.php?action=parse&page=List_of_PlayStation_Portable_games"
       "&prop=wikitext&format=json&formatversion=2")
COLS = {1: "North America", 2: "Europe", 3: "Japan"}


def source_text():
    if not CACHE.exists():
        out = subprocess.run(["curl", "-s", "-m", "90", "-A", "Mozilla/5.0", URL], capture_output=True, check=True).stdout
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_bytes(out)
    return json.loads(CACHE.read_text(encoding="utf-8"))["parse"]["wikitext"]


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    s = s.replace("&", " AND ")
    s = re.sub(r"^(THE|A|AN)\s+", "", s.strip())
    return re.sub(r"[^A-Z0-9]", "", s)


def title_of(cell):
    cell = re.sub(r"<span[^>]*></span>", "", cell)
    cell = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", cell)
    return re.sub(r"'''?|<[^>]+>|\{\{[^}]*\}\}", "", cell).strip()


def rows():
    t = source_text()
    for block in t.split("\n|-")[1:]:
        cells = [c[1:] for c in block.split("\n") if c.startswith("|") and not c.startswith("|}")]
        if len(cells) < 4:
            continue
        title = title_of(cells[0])
        dates = {}
        for k, region in COLS.items():
            m = re.search(r"\{\{dts\|(\d{4})/(\d{1,2})/(\d{1,2})", cells[k])
            if m:
                dates[region] = f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
        if title and dates:
            yield title, dates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = json.loads(GAMES.read_text(encoding="utf-8"))
    index = defaultdict(set)   # normalized title -> {game index}
    for n, g in enumerate(db["games"]):
        psp = [i for i in g["gameInstances"] if i.get("platform") == "psp"]
        if not psp:
            continue
        for name in [g["name"]] + [i.get("name", "") for i in psp]:
            if norm(name):
                index[norm(name)].add(n)
    listing = defaultdict(list)
    for title, dates in rows():
        listing[norm(title)].append(dates)
    filled = ambiguous = 0
    for key, entries in listing.items():
        games = index.get(key)
        if not games:
            continue
        if len(games) > 1 or len(entries) > 1:
            ambiguous += 1
            continue
        dates = entries[0]
        psp = [i for i in db["games"][next(iter(games))]["gameInstances"] if i.get("platform") == "psp"]
        per_region = Counter(i.get("region") for i in psp)
        for i in psp:
            # the list dates the original release; with several instances in a region
            # (reissues, Best editions) we cannot tell which serial it belongs to
            d = dates.get(i.get("region")) if per_region[i.get("region")] == 1 else None
            if d and not i.get("releaseDate"):
                i["releaseDate"] = d
                filled += 1
    print(f"listing titles {len(listing)}, ambiguous skipped {ambiguous}, instances filled {filled}")
    if args.apply:
        GAMES.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("written")


main()
