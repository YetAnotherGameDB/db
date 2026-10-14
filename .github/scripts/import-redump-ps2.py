#!/usr/bin/env python3
"""Import PlayStation 2 / PSP releases from the Redump DAT into games.json.

Redump is the authority on which PS2 discs exist; its DAT carries the disc
serial (SLUS-20312) which is the key the rest of the database uses. Only
`Games` category discs are imported - demos, coverdiscs, applications and
preproduction dumps are skipped.

One instance is written per distinct *base* serial. Redump lists the printed
serial variants of one disc together ("SLUS 20312, SLUS 20312GH"); suffixes
(GH, -F, -P, #, /E ...) are stripped, so the Greatest Hits reprint does not
become a second release of the same SKU. Different base serials (reissues
under a new product number) do stay separate instances.

Instances are grouped into a game by title key within and across regions. An
existing game is reused only when it already has a PS2 instance and its
normalised name equals the title; everything else becomes a new game.

Usage:
  python import-redump-ps2.py [--platform ps2|psp] [--dat PATH] [--apply]
Without --apply nothing is written to games.json.
"""

import argparse
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from match_nintendo import tight, unswap_article  # noqa: E402

GAMES = REPO / "games.json"
CACHE = REPO / ".cache" / "redump"
DAT_URL = "http://redump.org/datfile/{platform}/serial,versions"
PLATFORM = "ps2"   # set from --platform
UA = {"User-Agent": "Mozilla/5.0 (games-db redump import; papazark@gmail.com)"}

EXCLUDE = re.compile(r"\((?:Beta|Proto|Demo|Sample|Unl|Pirate|Kiosk|Promo|Preview|Not for Resale)[^)]*\)", re.I)
REGION_TAG = re.compile(r"\(((?:Japan|USA|Europe|Asia|Korea|Australia|Canada|Brazil|France|Germany|Italy|Spain|"
                        r"Russia|Netherlands|Sweden|Taiwan|China|Hong Kong|World|UK|Scandinavia|Latin America|"
                        r"Mexico|Poland|Portugal|Greece|Denmark|Finland|Norway)[^)]*)\)")
LANGS = "En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Zh|Ko|Pl|Ru|Cs|Hu|Tr|El"
LANG_TAG = re.compile(rf"\(((?:{LANGS})(?:,(?:{LANGS}))*)\)")
ANY_TAG = re.compile(r"\s*\([^)]*\)")
DISC_TAG = re.compile(r"\(Disc (\d+)[^)]*\)", re.I)
SERIAL_RE = re.compile(r"\b([A-Z]{4})[ -](\d{5})")

J_PREFIX = {"SLPS", "SLPM", "SCPS", "SCPM", "SCAJ", "SLAJ", "SLKA", "SCKA", "PBPX", "SIPS", "PAPX"}
U_PREFIX = {"SLUS", "SCUS", "PSRM"}
E_PREFIX = {"SLES", "SCES", "SLED", "SCED"}
REGION_ORDER = {"NTSC-J": 0, "NTSC-U": 1, "PAL": 2,
                "Japan": 0, "North America": 1, "Europe": 2, "Asia": 3, "Korea": 4}
BEST_REGION = {"NTSC-U": 0, "PAL": 1, "NTSC-J": 2,
               "North America": 0, "Europe": 1, "Japan": 2, "Asia": 3, "Korea": 4}
# PSP is region-free like PS3, so it uses market regions keyed on the serial's
# 3rd letter (ULJM -> Japan, ULUS -> North America, ULES -> Europe, ULAS -> Asia,
# ULKS -> Korea). Prefixes outside U[CL][AEJKU]x are homebrew/ports, not retail.
PSP_SERIAL = re.compile(r"^U[CL]([AEJKU])[A-Z]$")
PSP_REGIONS = {"J": "Japan", "U": "North America", "E": "Europe", "A": "Asia", "K": "Korea"}


def load_dat(path):
    if path:
        return ET.parse(path).getroot()
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{PLATFORM}.dat"
    if not cached.exists():
        req = urllib.request.Request(DAT_URL.format(platform=PLATFORM), headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        cached.write_bytes(z.read(z.namelist()[0]))
    return ET.parse(cached).getroot()


def region_of(prefix, name):
    if PLATFORM == "psp":
        m = PSP_SERIAL.match(prefix)
        return PSP_REGIONS[m.group(1)] if m else None
    if prefix in J_PREFIX:
        return "NTSC-J"
    if prefix in U_PREFIX:
        return "NTSC-U"
    if prefix in E_PREFIX:
        return "PAL"
    m = REGION_TAG.search(name)
    first = (m.group(1) if m else "").split(",")[0].strip()
    if first in ("Japan", "Asia", "Korea", "Taiwan", "China", "Hong Kong"):
        return "NTSC-J"
    if first in ("USA", "Canada", "Brazil", "Mexico", "Latin America"):
        return "NTSC-U"
    return "PAL"


def languages(name, region):
    m = LANG_TAG.search(name)
    if m:
        return f"[{m.group(1)}]"
    return {"NTSC-J": "[Ja]", "Japan": "[Ja]", "Korea": "[Ko]"}.get(region, "[En]")


def clean_title(name):
    """Release title: the DAT name minus every parenthesised tag."""
    return re.sub(r"\s{2,}", " ", ANY_TAG.sub("", name)).strip()


def title_key(title):
    return tight(unswap_article(title))


def harvest(root):
    rows, seen = [], set()
    for g in root.findall("game"):
        if g.findtext("category") != "Games":
            continue
        name = g.get("name")
        if EXCLUDE.search(name):
            continue
        serials = []
        for m in SERIAL_RE.finditer(g.findtext("serial") or ""):
            s = f"{m.group(1)}-{m.group(2)}"
            if s not in serials:
                serials.append(s)
        title = clean_title(name)
        disc = DISC_TAG.search(name)
        for s in serials:
            if s in seen:
                continue
            seen.add(s)
            region = region_of(s[:4], name)
            if region is None:
                continue
            rows.append({
                "title": title,
                "key": title_key(title),
                "serial": s,
                "region": region,
                "language": languages(name, region),
                "disc": disc.group(1) if disc else "",
            })
    return rows


def base_serial(s):
    m = SERIAL_RE.search((s or "").replace("/", " "))
    return f"{m.group(1)}-{m.group(2)}" if m else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=("ps2", "psp"), default="ps2")
    ap.add_argument("--dat")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    global PLATFORM
    PLATFORM = args.platform

    rows = harvest(load_dat(args.dat))
    data = json.loads(GAMES.read_text(encoding="utf-8"))
    games = data["games"]

    have = {base_serial(i["serial"]) for g in games for i in g["gameInstances"]
            if i.get("platform") == PLATFORM and i.get("serial")}
    by_key = defaultdict(list)
    for g in games:
        by_key[title_key(g["name"])].append(g)

    new_rows = [r for r in rows if r["serial"] not in have]
    groups = defaultdict(list)
    for r in new_rows:
        groups[r["key"]].append(r)

    next_id = max(g["id"] for g in games) + 1
    created, merged, merge_log = 0, 0, []
    for key, members in sorted(groups.items()):
        best = min(members, key=lambda r: BEST_REGION[r["region"]])
        name = best["title"].upper()
        # Reuse an existing game only when it is already a PS2 game: the same
        # name on another platform is as often a different game (a 1987 NES
        # Castlevania vs a PS2 one) as a port, and a wrong merge is worse than
        # a second entry.
        targets = [g for g in by_key.get(key, [])
                   if any(i.get("platform") == PLATFORM for i in g["gameInstances"])]
        game = targets[0] if len(targets) == 1 else None
        if game is None:
            game = {"name": name, "gameInstances": [], "id": next_id}
            next_id += 1
            created += 1
            games.append(game)
            by_key[key].append(game)
        else:
            merged += 1
            merge_log.append(f"{name}\t-> {game['id']} {game['name']}\t{len(members)}")
        n = max((int(i["id"].rsplit("-", 1)[1]) for i in game["gameInstances"]), default=0)
        for r in sorted(members, key=lambda x: (REGION_ORDER[x["region"]], x["serial"])):
            n += 1
            title = r["title"] + (f" (Disc {r['disc']})" if r["disc"] else "")
            game["gameInstances"].append({
                "name": title,
                "platform": PLATFORM,
                "language": r["language"],
                "serial": r["serial"],
                "region": r["region"],
                "tgdbId": 0,
                "id": f"{game['id']}-{n}",
            })

    print(f"redump rows {len(rows)}  already in db {len(rows) - len(new_rows)}  "
          f"new instances {len(new_rows)}")
    print(f"new games {created}  merged into existing {merged}")
    print("by region:", dict(Counter(r["region"] for r in new_rows)))
    out = REPO / ".cache" / f"{PLATFORM}-merges.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(merge_log), encoding="utf-8")
    if args.apply:
        if PLATFORM == "psp":
            enums = data["enums"]
            if "psp" not in enums["platforms"]:
                enums["platforms"].append("psp")
            enums["regions"].setdefault("psp", ["Japan", "North America", "Europe", "Asia", "Korea"])
        GAMES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("written")
    else:
        print("(dry run)")


if __name__ == "__main__":
    main()
