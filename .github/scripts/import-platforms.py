#!/usr/bin/env python3
"""Import more platforms into games.json from the Redump and No-Intro DATs.

Redump (redump.org, `Games` category only): saturn, dreamcast, gamecube, wii, xbox, xbox360.
No-Intro (libretro mirror): gb, gbc, gba, nds, 3ds, genesis, sms, gamegear, virtualboy.

Region scheme follows the hardware (see CLAUDE.md): region-locked systems (saturn, dreamcast, gamecube,
wii, xbox, genesis) use the NTSC-J/NTSC-U/PAL lockout buckets; region-free ones (gb, gbc, gba, nds, 3ds,
xbox360, sms, gamegear, virtualboy) use the market regions Japan/North America/Europe/Asia/Korea. Where
the serial encodes the region (GBA/NDS/3DS/GBC game codes, Wii/GC suffixes) that wins over the DAT's
region tag, because "USA, Europe" tags are ambiguous.

One instance per serial (revisions and multi-disc sets sharing a serial collapse). Serial-less dumps
(most GB/SMS/GG, some GBC) are keyed on region + title. Idempotent: serials already present for the
platform are skipped. An existing game is reused only if it already has an instance of the platform.

Usage: python import-platforms.py PLATFORM[,PLATFORM...]|all [--apply]
"""
import argparse, io, json, re, sys, urllib.parse, urllib.request, xml.etree.ElementTree as ET, zipfile
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from match_nintendo import tight, unswap_article  # noqa: E402

GAMES = REPO / "games.json"
REDUMP = REPO / ".cache" / "redump"
NOINTRO = REPO / ".cache" / "nointro"
UA = {"User-Agent": "Mozilla/5.0 (games-db import; papazark@gmail.com)"}
NI_BASE = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro/"

LOCKOUT = ["NTSC-J", "NTSC-U", "PAL"]
MARKET = ["Japan", "North America", "Europe", "Asia", "Korea"]
# platform -> (source, source key, region scheme)
PLATFORMS = {
    "saturn": ("redump", "ss", LOCKOUT), "dreamcast": ("redump", "dc", LOCKOUT),
    "gamecube": ("redump", "gc", LOCKOUT), "wii": ("redump", "wii", LOCKOUT),
    "xbox": ("redump", "xbox", LOCKOUT), "xbox360": ("redump", "xbox360", MARKET),
    "gb": ("nointro", "Nintendo - Game Boy", MARKET), "gbc": ("nointro", "Nintendo - Game Boy Color", MARKET),
    "gba": ("nointro", "Nintendo - Game Boy Advance", MARKET), "nds": ("nointro", "Nintendo - Nintendo DS", MARKET),
    "3ds": ("nointro", "Nintendo - Nintendo 3DS", MARKET),
    "genesis": ("nointro", "Sega - Mega Drive - Genesis", LOCKOUT),
    "sms": ("nointro", "Sega - Master System - Mark III", MARKET),
    "gamegear": ("nointro", "Sega - Game Gear", MARKET),
    "virtualboy": ("nointro", "Nintendo - Virtual Boy", MARKET),
}

EXCLUDE = re.compile(r"\((?:Beta|Proto|Demo|Sample|Unl|Pirate|Kiosk|Promo|Preview|Not for Resale|Aftermarket|"
                     r"Homebrew|Test|Debug|Seed|Competition|Rental|Trial|Taikenban|Bonus|Possible)[^)]*\)|\[BIOS\]", re.I)
ANY_TAG = re.compile(r"\s*\([^)]*\)")
DISC_TAG = re.compile(r"\(Disc (\d+)[^)]*\)", re.I)
LANGS = "En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Zh|Ko|Pl|Ru|Cs|Hu|Tr|El"
LANG_TAG = re.compile(rf"\(((?:{LANGS})(?:,(?:{LANGS}))*)\)")

# region tag word -> (lockout bucket, market region)
TAGS = {"Japan": ("NTSC-J", "Japan"), "Asia": ("NTSC-J", "Asia"), "Korea": ("NTSC-J", "Korea"),
        "Taiwan": ("NTSC-J", "Asia"), "China": ("NTSC-J", "Asia"), "Hong Kong": ("NTSC-J", "Asia"),
        "USA": ("NTSC-U", "North America"), "Canada": ("NTSC-U", "North America"),
        "Brazil": ("NTSC-U", "North America"), "Mexico": ("NTSC-U", "North America"),
        "Latin America": ("NTSC-U", "North America")}
# serial region letter (GBA/NDS/GBC/3DS game codes) -> market region; anything else is Europe
CART_PREFIX = {"gba": "AGB", "gbc": "CGB", "nds": "NTR"}
LETTER = {"J": "Japan", "E": "North America", "K": "Korea", "C": "Asia", "T": "Asia", "W": "Asia"}
SUFFIX = {"JPN": "NTSC-J", "ASI": "NTSC-J", "KOR": "NTSC-J", "USA": "NTSC-U", "CAN": "NTSC-U"}
ORDER_BEST = {"NTSC-U": 0, "PAL": 1, "NTSC-J": 2, "North America": 0, "Europe": 1, "Japan": 2, "Asia": 3, "Korea": 4}


def dat_root(platform):
    """Parsed Redump XML root, or the No-Intro clrmamepro text."""
    source, key, _ = PLATFORMS[platform]
    if source == "redump":
        REDUMP.mkdir(parents=True, exist_ok=True)
        cached = REDUMP / f"{key}.dat"
        if not cached.exists():
            req = urllib.request.Request(f"http://redump.org/datfile/{key}/serial,versions", headers=UA)
            z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=120).read()))
            cached.write_bytes(z.read(z.namelist()[0]))
        return ET.parse(cached).getroot()
    NOINTRO.mkdir(parents=True, exist_ok=True)
    cached = NOINTRO / f"{key}.dat"
    if not cached.exists():
        req = urllib.request.Request(NI_BASE + urllib.parse.quote(key) + ".dat", headers=UA)
        cached.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    return cached.read_text(encoding="utf-8")


def records(platform):
    """Yield (name, raw serial, rom file names) for each retail entry."""
    root = dat_root(platform)
    if isinstance(root, str):
        for block in root.split("\ngame (")[1:]:
            name = re.search(r'name "([^"]*)"', block)[1]
            s = re.search(r'serial "([^"]*)"', block)
            yield name, (s[1] if s else ""), "".join(re.findall(r'rom \( name "([^"]*)"', block))
    else:
        for g in root.findall("game"):
            if g.findtext("category") == "Games":
                yield g.get("name"), g.findtext("serial") or "", ""


def norm_serial(platform, raw):
    first = raw.split(",")[0].strip()
    if platform == "gamecube":
        m = re.search(r"DOL-([A-Z0-9]{4})-(?:\d-)?([A-Z]{3})", first)
        return f"DOL-{m[1]}-{m[2]}" if m else first
    if platform == "wii":
        m = re.search(r"RVL-([A-Z0-9]{4})-([A-Z]{3})", first)
        return f"RVL-{m[1]}-{m[2]}" if m else first
    if platform in CART_PREFIX and re.fullmatch(r"[A-Z0-9]{4}", first):
        return f"{CART_PREFIX[platform]}-{first}"  # as printed on the cart; keeps GBA/GBC/NDS codes from colliding
    return re.sub(r"\s+", " ", first)


def region_of(platform, name, serial):
    scheme = PLATFORMS[platform][2]
    market = scheme is MARKET
    if platform in ("gba", "nds", "gbc", "3ds") and serial:
        return LETTER.get(serial[-1], "Europe")
    if platform in ("gamecube", "wii") and serial:
        suffix = serial.rsplit("-", 1)[1]
        return SUFFIX.get(suffix, "PAL")
    for tag in re.findall(r"\(([^)]*)\)", name):
        for word in tag.split(","):
            if word.strip() in TAGS:
                return TAGS[word.strip()][1 if market else 0]
        if re.fullmatch(r"(Europe|Australia|France|Germany|Italy|Spain|UK|Netherlands|Sweden|Russia|Scandinavia|"
                        r"Poland|Portugal|Greece|Denmark|Finland|Norway)(, [A-Za-z ]+)*", tag):
            return "Europe" if market else "PAL"
    return None  # World / untagged: not safely placed


def harvest(platform):
    rows, seen = [], {}
    for name, raw, roms in records(platform):
        if EXCLUDE.search(name) or EXCLUDE.search(roms):
            continue
        serial = norm_serial(platform, raw)
        region = region_of(platform, name, serial)
        if region is None:
            continue
        title = re.sub(r"\s{2,}", " ", ANY_TAG.sub("", name)).strip()
        disc = DISC_TAG.search(name)
        lang = LANG_TAG.search(name)
        key = serial or f"{region}|{tight(unswap_article(title))}"
        row = {"title": title, "key": tight(unswap_article(title)), "serial": serial, "region": region,
               "disc": disc[1] if disc else "", "rev": bool(re.search(r"\((?:Rev|v\d)", name)),
               "language": f"[{lang[1]}]" if lang else {"Japan": "[Ja]", "NTSC-J": "[Ja]", "Korea": "[Ko]"}.get(region, "[En]")}
        prev = seen.get(key)
        if prev is None:
            seen[key] = row
            rows.append(row)
        elif prev["rev"] and not row["rev"]:
            prev.update(row)
    # a disc suffix only where the discs carry different serials; same serial = one instance
    per_title = defaultdict(set)
    for r in rows:
        if r["disc"]:
            per_title[(r["title"], r["region"])].add(r["serial"] or r["disc"])
    for r in rows:
        if r["disc"] and len(per_title[(r["title"], r["region"])]) > 1:
            r["title"] += f" (Disc {r['disc']})"
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("platforms")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    wanted = list(PLATFORMS) if args.platforms == "all" else args.platforms.split(",")
    data = json.loads(GAMES.read_text(encoding="utf-8"))
    games, enums = data["games"], data["enums"]
    next_id = max(g["id"] for g in games) + 1
    for platform in wanted:
        scheme = PLATFORMS[platform][2]
        order = {r: n for n, r in enumerate(scheme)}
        have, have_key = set(), set()
        for g in games:
            for i in g["gameInstances"]:
                if i.get("platform") == platform:
                    if i.get("serial"):
                        have.add(i["serial"])
                    have_key.add((i.get("region"), tight(unswap_article(i.get("name", "")))))
        rows = [r for r in harvest(platform)
                if (r["serial"] and r["serial"] not in have)
                or (not r["serial"] and (r["region"], r["key"]) not in have_key)]
        by_key = {}
        for g in games:
            if any(i.get("platform") == platform for i in g["gameInstances"]):
                by_key.setdefault(tight(unswap_article(g["name"])), g)
        groups = defaultdict(list)
        for r in rows:
            groups[r["key"]].append(r)
        created = 0
        for key, members in sorted(groups.items()):
            game = by_key.get(key)
            if game is None:
                best = min(members, key=lambda r: ORDER_BEST[r["region"]])
                game = {"name": re.sub(r" \(Disc \d+\)$", "", best["title"]).upper(), "gameInstances": [], "id": next_id}
                next_id += 1
                created += 1
                games.append(game)
                by_key[key] = game
            n = max((int(i["id"].rsplit("-", 1)[1]) for i in game["gameInstances"]), default=0)
            for r in sorted(members, key=lambda x: (order[x["region"]], x["serial"], x["title"])):
                n += 1
                inst = {"name": r["title"], "platform": platform, "language": r["language"]}
                if r["serial"]:
                    inst["serial"] = r["serial"]
                inst.update({"region": r["region"], "tgdbId": 0, "id": f"{game['id']}-{n}"})
                game["gameInstances"].append(inst)
        print(f"{platform:10} new instances {len(rows):5}  new games {created:5}  {dict(Counter(r['region'] for r in rows))}")
        if args.apply and rows:
            if platform not in enums["platforms"]:
                enums["platforms"].append(platform)
            enums["regions"].setdefault(platform, scheme)
    if args.apply:
        GAMES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("written")
    else:
        print("(dry run)")


if __name__ == "__main__":
    main()
