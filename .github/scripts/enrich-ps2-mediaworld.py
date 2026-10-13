#!/usr/bin/env python3
"""Fill Japanese PS2 instances' regionalName / releaseDate / upc from mediaworld.co.jp.

For every NTSC-J ps2 instance missing any of those fields, search mediaworld
by the disc serial, fetch each candidate product's /products/<sku>.js and keep
one only when its structured ■メーカー品番 field equals the serial exactly -
a search hit that merely mentions the serial somewhere is not evidence.

  regionalName  ■タイトル minus the (SERIAL) and any ASCII alias in brackets
  upc           ■JAN/EAN, and only when it matches the variant barcode
  releaseDate   the (YYYYMMDD) stamp in the product title, accepted only when
                it agrees with ■発売日 (either field alone is sometimes wrong)

Existing values are never overwritten. Results (including misses) are cached
under .cache/mediaworld-ps2/, so re-runs only fetch what is new.

Usage:
  python enrich-ps2-mediaworld.py [--apply] [--limit N] [--workers 4]
"""

import argparse
import html
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GAMES = REPO / "games.json"
CACHE = REPO / ".cache" / "mediaworld-ps2"
UA = {"User-Agent": "Mozilla/5.0"}
BASE = "https://mediaworld.co.jp"
MAX_CANDIDATES = 8


_lock = threading.Lock()
_next_slot = [0.0]
MIN_GAP = 0.4   # seconds between any two requests, across all workers


def get(url, retries=6):
    """Body text, or None after repeated failure. Throttled; backs off on 429."""
    for attempt in range(retries):
        with _lock:
            wait = _next_slot[0] - time.time()
            _next_slot[0] = max(time.time(), _next_slot[0]) + MIN_GAP
        if wait > 0:
            time.sleep(wait)
        # mediaworld answers 429 to Python's TLS stack but not to curl's, so
        # fetch through curl.
        proc = subprocess.run(
            ["curl", "-s", "-m", "40", "-A", UA["User-Agent"], "-H", "Accept-Language: ja-JP,ja;q=0.9",
             "-w", "\n%{http_code}", url],
            capture_output=True)
        body, _, code = proc.stdout.rpartition(b"\n")
        code = code.decode().strip()
        if code == "200":
            return body.decode("utf-8", "replace")
        if code == "404":
            return ""
        time.sleep(30 * (attempt + 1) if code == "429" else 3 * (attempt + 1))
    return None


def field(description, label):
    m = re.search(rf"■{label}[:：]\s*(.*?)(?:<br|$)", description)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def norm(serial):
    return re.sub(r"[^A-Z0-9]", "", (serial or "").upper())


def clean_title(raw, serial):
    t = html.unescape(raw)
    t = re.sub(r"【[^】]*】", "", t)
    # condition / bundle notes the shop appends
    t = re.sub(r"\((?:ソフト単品|本体同梱ソフト単品|本体同梱版?|再販|初回限定版?|通常版)\)", "", t)
    t = re.sub(r"\[PS2[^\]]*\]", "", t)
    t = re.sub(r"\(\s*" + re.escape(serial) + r"[^)]*\)", "", t)
    # ASCII-only bracketed alias: (FINAL FANTASY X / FF10)
    t = re.sub(r"\([\x20-\x7e]*\)", "", t)
    t = re.sub(r"[\s　]+", " ", t).strip()
    return t


def lookup(serial):
    path = CACHE / f"{serial}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    result = {"status": "none"}
    page = get(f"{BASE}/search?q={serial}")
    if page is None:
        return {"status": "error"}
    skus = []
    for sku in re.findall(r'/products/(\d{6,})\b', page):
        if sku not in skus:
            skus.append(sku)
    for sku in skus[:MAX_CANDIDATES]:
        body = get(f"{BASE}/products/{sku}.js")
        if body is None:
            return {"status": "error"}      # not cached: retry on the next run
        if not body:
            continue
        try:
            prod = json.loads(body)
        except ValueError:
            continue
        desc = prod.get("description", "")
        if norm(field(desc, "メーカー品番")) != norm(serial) or "PS2" not in prod.get("title", "") + field(desc, "機種"):
            continue
        jan = field(desc, "JAN/EAN")
        barcode = (prod.get("variants") or [{}])[0].get("barcode") or ""
        stamp = re.search(r"\((\d{8})\)\s*$", prod.get("title", ""))
        listed = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", field(desc, "発売日"))
        date = ""
        if stamp and listed:
            s = stamp.group(1)
            if (s[:4], s[4:6], s[6:]) == (listed.group(1), f"{int(listed.group(2)):02d}", f"{int(listed.group(3)):02d}"):
                date = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        result = {
            "status": "ok", "sku": sku,
            "title_ja": clean_title(field(desc, "タイトル") or prod["title"], serial),
            "upc": jan if re.fullmatch(r"\d{12,13}", jan) and jan == barcode else "",
            "release_date": date,
        }
        break
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    data = json.loads(GAMES.read_text(encoding="utf-8"))
    todo = []
    for g in data["games"]:
        for i in g["gameInstances"]:
            if (i.get("platform") == "ps2" and i.get("region") == "NTSC-J" and i.get("serial")
                    and not (i.get("regionalName") and i.get("releaseDate") and i.get("upc"))):
                todo.append(i["serial"])
    todo = sorted(set(todo))[: args.limit] if args.limit else sorted(set(todo))
    print(f"{len(todo)} serials to look up")

    results = {}
    with ThreadPoolExecutor(args.workers) as pool:
        for n, (serial, res) in enumerate(zip(todo, pool.map(lookup, todo)), 1):
            results[serial] = res
            if n % 100 == 0:
                ok = sum(1 for r in results.values() if r["status"] == "ok")
                print(f"  {n}/{len(todo)}  matched {ok}", flush=True)

    ok = {s: r for s, r in results.items() if r["status"] == "ok"}
    print(f"matched {len(ok)}/{len(todo)}  errors {sum(1 for r in results.values() if r['status'] == 'error')}")

    changed = {"regionalName": 0, "releaseDate": 0, "upc": 0, "localizedNames": 0}
    for g in data["games"]:
        for i in g["gameInstances"]:
            r = ok.get(i.get("serial")) if i.get("platform") == "ps2" else None
            if not r:
                continue
            if r["title_ja"] and not i.get("regionalName"):
                i["regionalName"] = r["title_ja"]
                changed["regionalName"] += 1
                if not g.get("localizedNames", {}).get("ja"):
                    g.setdefault("localizedNames", {})["ja"] = r["title_ja"]
                    changed["localizedNames"] += 1
            if r["release_date"] and not i.get("releaseDate"):
                i["releaseDate"] = r["release_date"]
                changed["releaseDate"] += 1
            if r["upc"] and not i.get("upc"):
                i["upc"] = r["upc"]
                changed["upc"] += 1
    print(changed)
    if args.apply:
        GAMES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("written")


if __name__ == "__main__":
    main()
