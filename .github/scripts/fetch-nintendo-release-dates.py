#!/usr/bin/env python3
"""Backfill missing `releaseDate` on Japanese Famicom / FDS / Super Famicom instances.

Targets famicom/fds/snes instances that have a `upc` (JAN) but no `releaseDate`,
searches mediaworld.co.jp by JAN, and only accepts a date after the product page
agrees with itself and with games.json on every field we can check:

  1. the variant `barcode` must equal the instance's `upc`,
  2. the description's `JAN/EAN` field must equal the instance's `upc`,
  3. the description's maker part number must equal the instance's `serial`,
  4. the product title must carry the right platform tag ([FC] / [SFC]),
  5. the description's release date must match the YYYYMMDD stamp mediaworld
     puts in the product title.

Rule 5 matters: mediaworld's two date fields sometimes disagree (Balloon Fight
is listed as both 1985/11/22 and 19850122), and only the title stamp is right.
When they disagree we write nothing and report the instance instead.

Candidates that survive all five checks but disagree with each other on the date
are reported as conflicts, never written.

Usage:
  python fetch-nintendo-release-dates.py --cache <cache.json> [--limit N] [--apply]

Without --apply it only fills the cache and prints a summary. With --apply,
verified dates are written into games.json and everything unresolved goes to
nintendo-release-dates-review.md.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://mediaworld.co.jp"
UA = {"User-Agent": "Mozilla/5.0 (games-db release-date backfill; papazark@gmail.com)"}
DELAY = 0.35

HANDLE_RE = re.compile(r"/products/(\d{11})")
TAG_RE = re.compile(r"<[^>]+>")
# structured fields mediaworld puts in every product description
DESC_JAN_RE = re.compile(r"JAN(?:/|\\/)EAN[:：]\s*(\d{8,13})")
DESC_PARTNO_RE = re.compile(r"メーカー品番[:：]\s*([^\s<]+)")
DESC_DATE_RE = re.compile(r"発売日[:：]\s*(\d{4})[/年](\d{1,2})[/月](\d{1,2})")
# mediaworld stamps the release date into the product title as (YYYYMMDD)
TITLE_DATE_RE = re.compile(r"\((\d{4})(\d{2})(\d{2})\)")

PLATFORM_TAG = {"famicom": "[FC]", "fds": "[FC]", "snes": "[SFC]"}

REPO = Path(__file__).resolve().parents[2]


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


def norm_serial(s):
    """Compare serials ignoring case, spaces and hyphens."""
    return re.sub(r"[\s\-–—]", "", (s or "")).upper()


def lookup_jan(jan, serial, platform):
    """Search mediaworld for one JAN, verify candidates, return outcome dict."""
    html = fetch(BASE + "/search?q=" + urllib.parse.quote(jan))
    time.sleep(DELAY)
    if html is None:
        return {"status": "not_found", "candidates": []}

    # sku = 8-digit product base + 3-digit condition suffix; dedupe on the base
    bases = {}
    for sku in HANDLE_RE.findall(html):
        base = sku[:8]
        if base not in bases or sku < bases[base]:
            bases[base] = sku

    tag = PLATFORM_TAG[platform]
    candidates = []
    for sku in sorted(bases.values()):
        body = fetch(f"{BASE}/products/{sku}.js")
        time.sleep(DELAY)
        if body is None:
            continue
        prod = json.loads(body)
        title = prod.get("title") or ""
        desc = TAG_RE.sub("\n", prod.get("description") or "")
        variant_jans = {v.get("barcode") for v in prod.get("variants", []) if v.get("barcode")}
        desc_jans = set(DESC_JAN_RE.findall(desc))

        m = DESC_DATE_RE.search(desc)
        desc_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
        m = TITLE_DATE_RE.search(title)
        title_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

        partno = DESC_PARTNO_RE.search(desc)
        partno = partno.group(1) if partno else None

        candidates.append({
            "sku": sku,
            "title": title[:140],
            "desc_date": desc_date,
            "title_date": title_date,
            "partno": partno,
            "barcode_ok": jan in variant_jans,
            "desc_jan_ok": jan in desc_jans,
            "serial_ok": bool(partno) and norm_serial(partno) == norm_serial(serial),
            "platform_ok": tag in title,
            # the page must agree with itself on the date
            "date_ok": bool(desc_date) and desc_date == title_date,
        })

    verified = [c for c in candidates
                if c["barcode_ok"] and c["desc_jan_ok"] and c["serial_ok"]
                and c["platform_ok"] and c["date_ok"]]
    dates = {c["desc_date"] for c in verified}
    if len(dates) == 1:
        return {"status": "found", "date": dates.pop(), "candidates": candidates}
    if len(dates) > 1:
        return {"status": "conflict", "dates": sorted(dates), "candidates": candidates}
    return {"status": "not_found", "candidates": candidates}


def reject_reason(res):
    """Why did every candidate fail? Used only for the review report."""
    if not res.get("candidates"):
        return "no product on mediaworld"
    order = [("barcode_ok", "variant barcode mismatch"),
             ("desc_jan_ok", "description JAN mismatch"),
             ("platform_ok", "wrong platform tag"),
             ("serial_ok", "maker part number mismatch"),
             ("date_ok", "page disagrees with itself on the date")]
    for key, label in order:
        if not any(c[key] for c in res["candidates"]):
            return label
    return "no candidate passed every check"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, help="JSON cache of per-JAN lookups (resumable)")
    ap.add_argument("--limit", type=int, default=0, help="max new lookups this run (0 = all)")
    ap.add_argument("--apply", action="store_true", help="write verified dates into games.json")
    args = ap.parse_args()

    games_path = REPO / "games.json"
    raw = games_path.read_bytes()
    data = json.loads(raw.decode("utf-8"))

    def dump(d):
        return (json.dumps(d, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n").encode("utf-8")

    if dump(data) != raw:
        sys.exit("games.json does not round-trip byte-identically; refusing to continue")

    targets = []  # (instance, game) pairs: JP Nintendo releases with a upc but no date
    for g in data["games"]:
        for inst in g["gameInstances"]:
            if inst.get("releaseDate") or not inst.get("upc") or not inst.get("serial"):
                continue
            if inst.get("platform") in PLATFORM_TAG and inst.get("region") == "NTSC-J":
                targets.append((inst, g))
    by_plat = {}
    for inst, _ in targets:
        by_plat[inst["platform"]] = by_plat.get(inst["platform"], 0) + 1
    print(f"{len(targets)} JP Nintendo instances missing releaseDate ({by_plat})")

    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    todo, seen = [], set()
    for inst, _ in targets:
        key = f"{inst['platform']}:{inst['upc']}"
        if key not in cache and key not in seen:
            seen.add(key)
            todo.append((inst["platform"], inst["upc"], inst["serial"]))
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} JANs to look up ({len(cache)} already cached)")

    for n, (platform, jan, serial) in enumerate(todo, 1):
        key = f"{platform}:{jan}"
        try:
            cache[key] = lookup_jan(jan, serial, platform)
        except Exception as e:
            print(f"  ! {jan}: {e}", flush=True)
            continue
        st = cache[key]["status"]
        print(f"  [{n}/{len(todo)}] {jan} {serial}: {st}"
              + (f" -> {cache[key]['date']}" if st == "found" else ""), flush=True)
        if n % 20 == 0:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    found, conflicts, missing = [], [], []
    for inst, g in targets:
        res = cache.get(f"{inst['platform']}:{inst['upc']}")
        if not res:
            continue
        if res["status"] == "found":
            found.append((inst, g, res["date"]))
        elif res["status"] == "conflict":
            conflicts.append((inst, g, res))
        else:
            missing.append((inst, g, res))
    print(f"\nfound: {len(found)}  conflict: {len(conflicts)}  unverified: {len(missing)}")

    if not args.apply:
        return

    for inst, _, date in found:
        inst["releaseDate"] = date
    games_path.write_bytes(dump(data))
    print(f"wrote {len(found)} releaseDate values to games.json")

    review = REPO / "nintendo-release-dates-review.md"
    lines = ["# Famicom / FDS / Super Famicom — release dates still unresolved", "",
             "Japanese instances with a JAN but no `releaseDate` that mediaworld could not",
             "confirm, and that the superfamicom.org second pass",
             "(`fetch-nintendo-release-dates-sfcorg.py`) could not resolve either.",
             "Nothing here was written to `games.json`.", "",
             f"- unverified — {len(missing)}", f"- conflict — {len(conflicts)}", "",
             "| instance | title | platform | serial | jan | reason | detail |",
             "|---|---|---|---|---|---|---|"]
    for inst, g, res in conflicts:
        lines.append(f"| {inst['id']} | {g['name']} | {inst['platform']} | {inst['serial']} "
                     f"| {inst['upc']} | conflict | {', '.join(res['dates'])} |")
    for inst, g, res in missing:
        detail = ""
        for c in res.get("candidates", []):
            if c["desc_date"] or c["title_date"]:
                detail = f"desc {c['desc_date']} vs title {c['title_date']}"
                break
        lines.append(f"| {inst['id']} | {g['name']} | {inst['platform']} | {inst['serial']} "
                     f"| {inst['upc']} | {reject_reason(res)} | {detail} |")
    review.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {review.name} ({len(conflicts) + len(missing)} unresolved)")


if __name__ == "__main__":
    main()
