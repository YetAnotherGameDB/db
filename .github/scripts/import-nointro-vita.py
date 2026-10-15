#!/usr/bin/env python3
"""Import PlayStation Vita retail cartridges from the No-Intro DAT (libretro mirror) into games.json.

Vita is region-free like PSP/PS3, so instances use market regions (Japan / North America / Europe /
Asia / Korea), taken from the DAT's region tag (the PCSx serial prefix is not reliable: a Korean
release carries PCSG, a European one can carry PCSE). Idempotent: serials already present are skipped.
Digital (PSN) releases are not imported: that DAT carries no serials.

Usage: python import-nointro-vita.py [--apply]
"""
import argparse, json, re, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from match_nintendo import tight, unswap_article  # noqa: E402

GAMES = REPO / "games.json"
CACHE = REPO / ".cache" / "nointro" / "Sony - PlayStation Vita.dat"
URL = ("https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro/"
       "Sony%20-%20PlayStation%20Vita.dat")
PLATFORM = "psvita"
REGIONS = ["Japan", "North America", "Europe", "Asia", "Korea"]
TAG_REGION = {"Japan": "Japan", "USA": "North America", "Canada": "North America", "Europe": "Europe",
              "France": "Europe", "Germany": "Europe", "Italy": "Europe", "Spain": "Europe", "UK": "Europe",
              "Australia": "Europe", "Asia": "Asia", "Korea": "Korea"}
ORDER = {r: n for n, r in enumerate(REGIONS)}
BEST = {"North America": 0, "Europe": 1, "Japan": 2, "Asia": 3, "Korea": 4}
LANGS = "En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Zh|Ko|Pl|Ru|Cs|Hu|Tr|El"
LANG_TAG = re.compile(rf"\(((?:{LANGS})(?:,(?:{LANGS}))*)\)")
EXCLUDE = re.compile(r"\((?:Beta|Proto|Demo|Sample|Unl|Pirate|Kiosk|Promo|Preview|Not for Resale)[^)]*\)", re.I)
ANY_TAG = re.compile(r"\s*\([^)]*\)")
SERIAL = re.compile(r"serial \"(P[A-Z]{3}-\d{5})\"")


def dat_text():
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(subprocess.run(["curl", "-s", "-m", "90", URL], capture_output=True, check=True).stdout)
    return CACHE.read_text(encoding="utf-8")


def harvest():
    rows, seen = [], set()
    for block in dat_text().split("\ngame (")[1:]:
        name = re.search(r'name "([^"]*)"', block)[1]
        s = SERIAL.search(block)
        if EXCLUDE.search(name) or not s or s[1] in seen:
            continue
        seen.add(s[1])
        tags = re.findall(r"\(([^)]*)\)", name)
        region = next((TAG_REGION[t.strip()] for tag in tags for t in tag.split(",") if t.strip() in TAG_REGION), None)
        if region is None:
            continue
        lang = LANG_TAG.search(name)
        title = re.sub(r"\s{2,}", " ", ANY_TAG.sub("", name)).strip()
        rows.append({"title": title, "key": tight(unswap_article(title)), "serial": s[1], "region": region,
                     "language": f"[{lang[1]}]" if lang else {"Japan": "[Ja]", "Korea": "[Ko]"}.get(region, "[En]")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    data = json.loads(GAMES.read_text(encoding="utf-8"))
    games = data["games"]
    have = {i["serial"] for g in games for i in g["gameInstances"] if i.get("platform") == PLATFORM and i.get("serial")}
    rows = [r for r in harvest() if r["serial"] not in have]
    groups = defaultdict(list)
    for r in rows:
        groups[r["key"]].append(r)
    by_key = {tight(unswap_article(g["name"])): g for g in games
              if any(i.get("platform") == PLATFORM for i in g["gameInstances"])}
    next_id = max(g["id"] for g in games) + 1
    created = 0
    for key, members in sorted(groups.items()):
        game = by_key.get(key)
        if game is None:
            game = {"name": min(members, key=lambda r: BEST[r["region"]])["title"].upper(),
                    "gameInstances": [], "id": next_id}
            next_id += 1
            created += 1
            games.append(game)
        n = max((int(i["id"].rsplit("-", 1)[1]) for i in game["gameInstances"]), default=0)
        for r in sorted(members, key=lambda x: (ORDER[x["region"]], x["serial"])):
            n += 1
            game["gameInstances"].append({"name": r["title"], "platform": PLATFORM, "language": r["language"],
                                          "serial": r["serial"], "region": r["region"], "tgdbId": 0,
                                          "id": f"{game['id']}-{n}"})
    print(f"new instances {len(rows)}  new games {created}  by region {dict(Counter(r['region'] for r in rows))}")
    if args.apply:
        enums = data["enums"]
        if PLATFORM not in enums["platforms"]:
            enums["platforms"].append(PLATFORM)
        enums["regions"].setdefault(PLATFORM, REGIONS)
        GAMES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("written")
    else:
        print("(dry run)")


main()
