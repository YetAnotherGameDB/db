#!/usr/bin/env python3
"""Backfill famicom / fds / snes and seed n64 from the No-Intro DATs.

Unlike the first seeding pass (add-nintendo-games.py, which demanded dump +
catalogue + mediaworld JAN to agree), a release is written here on the strength
of the No-Intro dump alone; the superfamicom.org catalogue and the N64 game
code only *add* a serial and release date when they are available.

  famicom, fds, snes   dumps in every region. The catalogue supplies the printed
                       number (SHVC-ZL / SNS-ZL-USA) and the release date where
                       it knows the game; western dumps without a catalogue
                       match are written without a serial.
  n64                  the DAT carries the 4-letter cartridge game code
                       (NZLE); the first three letters identify the game across
                       regions, the last letter the region, so the regional
                       releases of one game are grouped on that.

Already-present releases are detected by serial, then by (platform, region,
title key); a release whose catalogue entry has a serial already under a game
is attached to that game rather than becoming a new one.

Usage:
  python import-nintendo.py [--apply] [--only famicom,fds,snes,n64]
"""

import argparse
import difflib
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from match_nintendo import (  # noqa: E402
    WESTERN_PREFIX, WESTERN_SUFFIX, build_index, clean_serial, jp_serial_index,
    match_dump, numeral, tight, unswap_article)

GAMES = REPO / "games.json"
NOINTRO = REPO / ".cache" / "nointro"
CATALOGUE = REPO / ".cache" / "superfamicom" / "catalogue.json"
MIRROR = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro"
UA = {"User-Agent": "Mozilla/5.0 (games-db nointro import; papazark@gmail.com)"}

DATS = {
    "Nintendo - Nintendo Entertainment System": "famicom",
    "Nintendo - Family Computer Disk System": "fds",
    "Nintendo - Super Nintendo Entertainment System": "snes",
    "Nintendo - Nintendo 64": "n64",
}

GAME_RE = re.compile(r"^game \(\s*$")
FIELD_RE = re.compile(r'^\s*(\w+) "?([^"\n]*)"?\s*$')
ROM_RE = re.compile(r"^\s*rom \( (.*) \)\s*$")
ATTR_RE = re.compile(r'(\w+) (?:"([^"]*)"|(\S+))')

# Anything that is not an original boxed retail release: prototypes and
# samples, bootlegs, and the many later re-releases and re-dumps.
EXCLUDE = re.compile(
    r"\((?:Beta|Proto|Possible Proto|Sample|Demo|Debug|Program|Test Program|Unl|Pirate|"
    r"Aftermarket|Hack|Competition Cart|Kiosk|FamicomBox|Disk Writer|NP|e-Reader|GameCube|"
    r"Evercade|Piko Interactive|Limited Run Games|Retro-Bit|QUByte|Steam|SNESDEV|Digital|"
    r"Arcade|NES Conversion|Disney Classic|Mini|Rev-Mini|Switch|Auto Demo)\b[^)]*\)"
    r"|\([^)]*(?:Virtual Console|Collection|Classic Mini|Switch Online|Classics|Legacy)[^)]*\)",
    re.I,
)
BAD_NAME = re.compile(
    r"^BS |\[BIOS\]|Dev Disk|Disk Lot|Checker|Aging Ver|Parameter Disk|^ST0\d\d$|Test Cart|"
    r"Service Disk|Debug|Dumper", re.I)
ANY_TAG = re.compile(r"\s*\([^)]*\)")
LANGS = "En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Zh|Ko|Pl|Ru"
LANG_TAG = re.compile(rf"\(((?:{LANGS})(?:,(?:{LANGS}))*)\)")

# Asia / Korea / Taiwan / China dumps are, on these systems, overwhelmingly
# unlicensed or bootleg carts (Subor, Youxi Baobei ...), so they are dropped
# rather than filed under NTSC-J.
J = {"Japan"}
U = {"USA", "Canada"}
PAL = {"Europe", "Australia", "Germany", "France", "Spain", "Italy", "UK", "Netherlands",
       "Sweden", "Scandinavia", "Russia", "Denmark", "Finland", "Norway", "Poland",
       "Portugal", "Greece", "Austria", "Switzerland", "Ireland"}
ORDER = {"NTSC-J": 0, "NTSC-U": 1, "PAL": 2}


def buckets(region_field):
    out = set()
    for part in (p.strip() for p in region_field.split(",")):
        if part in J:
            out.add("NTSC-J")
        elif part in U:
            out.add("NTSC-U")
        elif part == "World":
            out.update(("NTSC-U", "PAL"))
        elif part in PAL:
            out.add("PAL")
    return out


def fetch_text(name):
    path = NOINTRO / f"{name}.dat"
    if path.exists():
        return path.read_text(encoding="utf-8")
    NOINTRO.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f"{MIRROR}/{urllib.parse.quote(name)}.dat", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8", "replace")
    path.write_text(text, encoding="utf-8")
    return text


def parse_dat(text):
    game = None
    for line in text.splitlines():
        if GAME_RE.match(line):
            game = {"roms": []}
        elif game is None:
            continue
        elif line.startswith(")"):
            yield game
            game = None
        elif ROM_RE.match(line):
            game["roms"].append({k: (q or u) for k, q, u in ATTR_RE.findall(ROM_RE.match(line).group(1))})
        elif FIELD_RE.match(line):
            m = FIELD_RE.match(line)
            game[m.group(1)] = m.group(2)


def harvest(text, platform):
    """One row per (bucket, title) - revisions and file formats collapse."""
    rows = {}
    for g in parse_dat(text):
        name = g.get("name", "")
        if not name or BAD_NAME.search(name) or EXCLUDE.search(name):
            continue
        title = re.sub(r"\s{2,}", " ", ANY_TAG.sub("", name)).strip()
        title = unswap_article(title)
        lang = LANG_TAG.search(name)
        serial = g.get("serial") or next((r["serial"] for r in g["roms"] if r.get("serial")), "")
        for bucket in buckets(g.get("region", "")):
            key = (bucket, serial) if platform == "n64" and serial else (bucket, tight(title))
            row = rows.setdefault(key, {
                "platform": platform, "title": title, "bucket": bucket,
                "language": f"[{lang.group(1)}]" if lang else ("[Ja]" if bucket == "NTSC-J" else "[En]"),
                "nointro_serial": serial,
            })
            if lang and row["language"] in ("[Ja]", "[En]"):
                row["language"] = f"[{lang.group(1)}]"
    return list(rows.values())


def near_duplicate(title, candidates):
    """An existing name that is the same game spelled differently, or ''.

    Loose matching is where the first backfill went wrong ("Super Mario Bros."
    swallowing "Super Mario Bros. 3"), so a fuzzy hit must also agree on every
    number in the title and on the subtitle after " - ".
    """
    key = tight(title)
    nums = re.findall(r"\d+", numeral_text(title))
    sub = tight(title.split(" - ", 1)[1]) if " - " in title else ""
    for cand_title, cand_key in candidates:
        if abs(len(cand_key) - len(key)) > 6:
            continue
        m = difflib.SequenceMatcher(None, key, cand_key)
        if m.quick_ratio() < 0.82 or m.ratio() < 0.82:
            continue
        if re.findall(r"\d+", numeral_text(cand_title)) != nums:
            continue
        cand_sub = tight(cand_title.split(" - ", 1)[1]) if " - " in cand_title else ""
        if bool(sub) != bool(cand_sub) or (sub and difflib.SequenceMatcher(None, sub, cand_sub).ratio() < 0.8):
            continue
        return cand_title
    return ""


def numeral_text(text):
    from match_nintendo import ROMAN, ROMAN_RE, slugify
    return ROMAN_RE.sub(lambda m: ROMAN[m.group(1)], slugify(text))


def catalogue_release(entry, bucket):
    """(serial, date) of the catalogue entry's release in a region bucket."""
    dates = entry.get("release_dates") or []
    for i, raw in enumerate(entry.get("serials", [])):
        raw = (raw or "").strip()
        western = bool(WESTERN_SUFFIX.search(raw) or WESTERN_PREFIX.match(raw))
        serial = re.sub(r"-JPN$", "", clean_serial(raw))
        if not serial:
            continue
        if bucket == "NTSC-J" and not western:
            return serial, (dates[i] if i < len(dates) else (dates[0] if len(dates) == 1 else ""))
        if bucket == "NTSC-U" and western and re.search(r"-USA$|^SNS-[A-Z0-9]{2,4}$|^NES-", serial, re.I):
            return serial, (dates[i] if i < len(dates) else "")
    return "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()
    wanted = set(args.only.split(",")) if args.only else set(DATS.values())

    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    cat_index = build_index(catalogue)
    data = json.loads(GAMES.read_text(encoding="utf-8"))
    games = data["games"]

    # --- what the database already holds ---------------------------------
    serial_game, name_slots = {}, set()
    for g in games:
        platforms = {i.get("platform") for i in g["gameInstances"]}
        for i in g["gameInstances"]:
            p, r = i.get("platform"), i.get("region")
            if i.get("serial"):
                serial_game[i["serial"]] = g
                serial_game[re.sub(r"-JPN$", "", i["serial"])] = g
            for n in (i.get("name"), ):
                if n:
                    name_slots.update({(p, r, tight(n)), (p, r, numeral(n))})
        for p in platforms:
            for r in ("NTSC-U", "PAL"):
                name_slots.update({(p, r, tight(g["name"])), (p, r, numeral(g["name"]))})
    games_by_name = defaultdict(list)
    for g in games:
        games_by_name[tight(g["name"])].append(g)

    by_slot = defaultdict(list)
    for g in games:
        for i in g["gameInstances"]:
            if i.get("name"):
                by_slot[(i.get("platform"), i.get("region"))].append((i["name"], tight(i["name"])))
    review = []

    rows = []
    for dat, platform in DATS.items():
        if platform in wanted:
            rows += harvest(fetch_text(dat), platform)

    next_id = max(g["id"] for g in games) + 1
    stats = Counter()
    pending = {}   # (platform, group key) -> game created in this run
    added = []

    def attach(game, platform, row, serial, date, ja_name):
        n = max((int(i["id"].rsplit("-", 1)[1]) for i in game["gameInstances"]), default=0) + 1
        inst = {"name": row["title"], "platform": platform, "language": row["language"]}
        if serial:
            inst["serial"] = serial
        inst["region"] = row["bucket"]
        inst["tgdbId"] = 0
        if ja_name and row["bucket"] == "NTSC-J":
            inst["regionalName"] = ja_name
        if date:
            inst["releaseDate"] = date
        inst["id"] = f"{game['id']}-{n}"
        game["gameInstances"].append(inst)
        if serial:
            serial_game[serial] = game
        stats[(platform, row["bucket"])] += 1
        added.append(inst)

    # western regions last so a catalogue-matched JP release has made its game
    rows.sort(key=lambda r: (r["platform"], ORDER[r["bucket"]], r["title"]))
    n64_groups = defaultdict(list)
    for row in rows:
        platform, bucket = row["platform"], row["bucket"]
        if platform == "n64":
            n64_groups[row["nointro_serial"][:3] or tight(row["title"])].append(row)
            continue
        key = (platform, bucket, tight(row["title"]))
        if key in name_slots or (platform, bucket, numeral(row["title"])) in name_slots:
            stats["already"] += 1
            continue
        dup = near_duplicate(row["title"], by_slot[(platform, bucket)])
        if dup:
            review.append(f"{platform}	{bucket}	{row['title']}	~ {dup}")
            stats["near-duplicate (skipped)"] += 1
            continue
        entry = match_dump({"platform": platform, "title": row["title"]}, cat_index)
        serial, date = ("", "")
        ja_name = ""
        game = None
        if entry:
            serial, date = catalogue_release(entry, bucket)
            ja_name = entry.get("title_ja", "")
            if serial and serial in serial_game:
                stats["already"] += 1
                continue
            for raw in entry.get("serials", []):
                other = serial_game.get(re.sub(r"-JPN$", "", clean_serial(raw)))
                if other is not None and any(i.get("platform") == platform for i in other["gameInstances"]):
                    game = other
                    break
        if game is None:
            gkey = (platform, tight(entry["title_en"]) if entry else tight(row["title"]))
            game = pending.get(gkey)
            if game is None:
                cands = [g for g in games_by_name.get(tight(row["title"]), [])
                         if any(i.get("platform") == platform for i in g["gameInstances"])]
                game = cands[0] if len(cands) == 1 else None
            if game is None:
                name = (entry["title_en"] if entry and bucket == "NTSC-J" else row["title"]).upper()
                game = {"name": name, "gameInstances": [], "id": next_id}
                if ja_name:
                    game["localizedNames"] = {"ja": ja_name}
                next_id += 1
                games.append(game)
                games_by_name[tight(name)].append(game)
                stats["new games"] += 1
            pending[gkey] = game
        attach(game, platform, row, serial, date, ja_name)
        name_slots.add(key)

    # --- n64: group on the 3-letter game code ------------------------------
    for code, members in sorted(n64_groups.items()):
        best = min(members, key=lambda r: {"NTSC-U": 0, "PAL": 1, "NTSC-J": 2}[r["bucket"]])
        game = {"name": best["title"].upper(), "gameInstances": [], "id": next_id}
        next_id += 1
        games.append(game)
        stats["new games"] += 1
        for row in sorted(members, key=lambda r: (ORDER[r["bucket"]], r["nointro_serial"])):
            attach(game, "n64", row, row["nointro_serial"], "", "")

    (REPO / ".cache" / "nintendo-review.txt").write_text("\n".join(review), encoding="utf-8")
    print(dict(stats))
    if args.apply:
        if "n64" in wanted:
            enums = data["enums"]
            if "n64" not in enums["platforms"]:
                enums["platforms"].insert(enums["platforms"].index("snes") + 1, "n64")
            enums["regions"].setdefault("n64", ["NTSC-J", "NTSC-U", "PAL"])
        GAMES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("written")
    else:
        (REPO / ".cache" / "nintendo-added.json").write_text(
            json.dumps(added, ensure_ascii=False, indent=1), encoding="utf-8")
        print("(dry run) preview -> .cache/nintendo-added.json")


if __name__ == "__main__":
    main()
