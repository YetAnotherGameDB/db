#!/usr/bin/env python3
"""Second pass: resolve leftover Japanese Nintendo `releaseDate` values via superfamicom.org.

`fetch-nintendo-release-dates.py` refuses any mediaworld product whose two date
fields disagree (its `発売日` line vs the YYYYMMDD stamp in the product title).
That is the right call — for Balloon Fight the line says 1985/11/22 and only the
title stamp (1985-01-22) is correct — but it leaves those instances unfilled.

This pass consults superfamicom.org, an independent catalogue, for whatever the
first pass could not confirm. The lookup is self-verifying: we guess the slug
from the game name, then only trust the page if the catalogue number printed on
it equals the instance's `serial`. A wrong guess lands on a different game and
is rejected, so a bad slug can never produce a date.

Usage:
  python fetch-nintendo-release-dates-sfcorg.py --cache <cache.json> [--limit N] [--apply]
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://superfamicom.org"
UA = {"User-Agent": "Mozilla/5.0 (games-db release-date backfill; papazark@gmail.com)"}
DELAY = 0.5

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<script.*?</script>", re.S)
# catalogue number followed by the release date(s); JP catalogue/date come first
ENTRY_RE = re.compile(r"((?:SHVC|HVC|SNS)-[A-Z0-9]+)((?:\s*/\s*(?:SHVC|HVC|SNS)-[A-Z0-9]+)*)"
                      r"\s+((?:19|20)\d\d-\d\d-\d\d)")

# famicom and fds discs both live under the NES catalogue on superfamicom.org
PATH = {"famicom": "/famicom/info/", "fds": "/famicom/info/", "snes": "/info/"}

REPO = Path(__file__).resolve().parents[2]


def fetch(url, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
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


def norm_serial(s):
    return re.sub(r"[\s\-–—]", "", (s or "")).upper()


def slugs_for(name):
    """Candidate superfamicom.org slugs for a games.json title, best guess first."""
    s = name.lower()
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    out = [s]
    # games.json uses "NAME - SUBTITLE"; the site sometimes drops the subtitle
    head = re.split(r"-+", s)
    if len(head) > 1:
        out.append(head[0])
    # "THE LEGEND OF X" is often filed as "legend-of-x"
    if s.startswith("the-"):
        out.append(s[4:])
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def lookup(name, serial, platform):
    """Try each candidate slug; accept only a page whose catalogue matches `serial`."""
    tried = []
    for slug in slugs_for(name):
        url = BASE + PATH[platform] + slug
        html = fetch(url)
        time.sleep(DELAY)
        if html is None:
            tried.append({"slug": slug, "result": "404"})
            continue
        text = re.sub(r"\s+", " ", TAG_RE.sub(" ", SCRIPT_RE.sub("", html)))
        entries = ENTRY_RE.findall(text)
        if not entries:
            tried.append({"slug": slug, "result": "no catalogue on page"})
            continue
        for first, rest, date in entries:
            cats = [first] + re.findall(r"(?:SHVC|HVC|SNS)-[A-Z0-9]+", rest)
            if any(norm_serial(c) == norm_serial(serial) for c in cats):
                return {"status": "found", "date": date, "slug": slug,
                        "catalogue": cats, "tried": tried}
        tried.append({"slug": slug, "result": "catalogue mismatch",
                      "saw": [e[0] for e in entries][:4]})
    return {"status": "not_found", "tried": tried}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    games_path = REPO / "games.json"
    raw = games_path.read_bytes()
    data = json.loads(raw.decode("utf-8"))

    def dump(d):
        return (json.dumps(d, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n").encode("utf-8")

    if dump(data) != raw:
        sys.exit("games.json does not round-trip byte-identically; refusing to continue")

    targets = []
    for g in data["games"]:
        for inst in g["gameInstances"]:
            if inst.get("releaseDate") or not inst.get("serial"):
                continue
            if inst.get("platform") in PATH and inst.get("region") == "NTSC-J":
                targets.append((inst, g))
    print(f"{len(targets)} JP Nintendo instances still missing releaseDate")

    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    todo = [(g["name"], i["serial"], i["platform"]) for i, g in targets
            if f"{i['platform']}:{i['serial']}" not in cache]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} to look up ({len(cache)} already cached)")

    for n, (name, serial, platform) in enumerate(todo, 1):
        key = f"{platform}:{serial}"
        try:
            cache[key] = lookup(name, serial, platform)
        except Exception as e:
            print(f"  ! {serial}: {e}", flush=True)
            continue
        st = cache[key]["status"]
        print(f"  [{n}/{len(todo)}] {serial} {name[:40]}: {st}"
              + (f" -> {cache[key]['date']}" if st == "found" else ""), flush=True)
        if n % 20 == 0:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    found = [(i, g, cache[f"{i['platform']}:{i['serial']}"]["date"]) for i, g in targets
             if cache.get(f"{i['platform']}:{i['serial']}", {}).get("status") == "found"]
    print(f"\nfound: {len(found)}  still unresolved: {len(targets) - len(found)}")

    if not args.apply:
        return
    for inst, _, date in found:
        inst["releaseDate"] = date
    games_path.write_bytes(dump(data))
    print(f"wrote {len(found)} releaseDate values to games.json")


if __name__ == "__main__":
    main()
