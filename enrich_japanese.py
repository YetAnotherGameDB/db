#!/usr/bin/env python3
"""
enrich_japanese.py
==================
Fills in missing Japanese names and UPC codes in games.json.

Data sources
------------
  PSXDATACENTER  – individual game pages scraped by serial number.
                   Used for: UPC / EAN barcode only.
                   (The site does not carry Japanese-script titles.)

  TheGamesDB     – REST API.  Requires --tgdb-key KEY (free sign-up at
                   https://thegamesdb.net/).
                   Used for: Japanese titles (game_title or alternates).
                   PS1 platform ID = 7.

  IGDB           – Twitch-authenticated REST API. Requires --igdb-client-id
                   and --igdb-client-secret (free Twitch developer account at
                   https://dev.twitch.tv/).
                   Used for: Japanese titles (alternative_names).

Priority order
--------------
  Japanese name : TheGamesDB (by tgdbId)  →  TheGamesDB (search)  →  IGDB
  UPC           : PSXDATACENTER game page

Usage
-----
  # dry run – print what would change, write nothing
  python enrich_japanese.py --dry-run

  # full run with TheGamesDB
  python enrich_japanese.py --tgdb-key YOUR_KEY

  # full run with IGDB
  python enrich_japanese.py --igdb-client-id ID --igdb-client-secret SECRET

  # both sources + limit to first 50 missing entries
  python enrich_japanese.py --tgdb-key KEY --igdb-client-id ID --igdb-client-secret SECRET --limit 50

Requirements
------------
  pip install requests beautifulsoup4
"""

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GAMES_JSON = Path("games.json")
CACHE_DIR = Path("cache")

PSXDC_JLIST_URL = "http://psxdatacenter.com/jlist.html"
PSXDC_BASE_URL = "http://psxdatacenter.com/"

TGDB_BY_ID_URL = "https://api.thegamesdb.net/v1/Games/ByGameID"
TGDB_SEARCH_URL = "https://api.thegamesdb.net/v1/Games/ByGameName"
TGDB_PS1_PLATFORM_ID = 7

IGDB_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
IGDB_ALT_URL = "https://api.igdb.com/v4/alternative_names"

REQUEST_DELAY = 1.0    # seconds between HTTP requests
REQUEST_TIMEOUT = 20   # seconds per request

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({"User-Agent": "GameDB-Enricher/1.0 (personal database project)"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _contains_japanese(text: str) -> bool:
    """True if text contains kanji, hiragana, katakana, or fullwidth chars."""
    return bool(re.search(r"[\u3000-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]", text))


def _cache_path(key: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", key)
    return CACHE_DIR / f"{safe}.cache"


def _fetch_raw(url: str, cache_key: str | None = None, extra_headers: dict | None = None) -> bytes | None:
    """Fetch URL with disk caching. Returns raw bytes or None on error."""
    CACHE_DIR.mkdir(exist_ok=True)
    key = cache_key or url
    path = _cache_path(key)

    if path.exists():
        log.debug("Cache hit: %s", key)
        return path.read_bytes()

    log.debug("Fetching: %s", url)
    time.sleep(REQUEST_DELAY)
    headers = {}
    if extra_headers:
        headers.update(extra_headers)
    try:
        r = _session.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        r.raise_for_status()
        path.write_bytes(r.content)
        return r.content
    except requests.RequestException as e:
        log.warning("Failed to fetch %s : %s", url, e)
        return None


def _decode_psxdc(raw: bytes) -> str:
    """PSXDATACENTER pages are delivered as UTF-16 LE with BOM."""
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le")
    # Fallback
    return raw.decode("windows-1252", errors="replace")


# ---------------------------------------------------------------------------
# PSXDATACENTER – build serial → page-URL map from J-list
# ---------------------------------------------------------------------------
_serial_to_psxdc_url: dict[str, str] | None = None


def _load_psxdc_serial_map() -> dict[str, str]:
    """
    Parse the PSXDATACENTER J-list and return {serial: relative_url}.
    The <a> tag href gives us the exact URL like games/J/0-9/SLPS-01986.html.
    """
    global _serial_to_psxdc_url
    if _serial_to_psxdc_url is not None:
        return _serial_to_psxdc_url

    log.info("Loading PSXDATACENTER J-list …")
    raw = _fetch_raw(PSXDC_JLIST_URL, "psxdc_jlist")
    if not raw:
        log.warning("Could not load PSXDATACENTER J-list")
        _serial_to_psxdc_url = {}
        return _serial_to_psxdc_url

    text = _decode_psxdc(raw)
    soup = BeautifulSoup(text, "html.parser")
    result: dict[str, str] = {}

    for row in soup.select("table.sectiontable tr"):
        link = row.find("a")
        serial_cell = row.find("td", class_="col2")
        if not link or not serial_cell:
            continue
        serial = serial_cell.get_text(strip=True).upper()
        href = link.get("href", "")
        if serial and href and re.match(r"[A-Z]{4}-\d{5}", serial):
            result[serial] = PSXDC_BASE_URL + href

    log.info("  → %d serials mapped from J-list", len(result))
    _serial_to_psxdc_url = result
    return _serial_to_psxdc_url


# ---------------------------------------------------------------------------
# PSXDATACENTER – individual game page → UPC
# ---------------------------------------------------------------------------
def lookup_psxdc_upc(serial: str) -> str | None:
    """Fetch PSXDATACENTER game page for this serial and return UPC/barcode."""
    smap = _load_psxdc_serial_map()
    url = smap.get(serial.upper())
    if not url:
        return None

    raw = _fetch_raw(url, f"psxdc_game_{serial}")
    if not raw:
        return None

    text = _decode_psxdc(raw)
    soup = BeautifulSoup(text, "html.parser")

    # Look for the "Barcode Number(s)( UPC / EAN )" row
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        if "barcode" in label.lower() or "upc" in label.lower() or "ean" in label.lower():
            value = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            # Extract numeric barcode (8–13 digits)
            candidates = re.findall(r"\b(\d{8,13})\b", value)
            if candidates:
                return candidates[0]

    return None


# ---------------------------------------------------------------------------
# Rakuten – Japanese title lookup via JAN/UPC
# ---------------------------------------------------------------------------
def lookup_rakuten_title(upc: str) -> str | None:
    """Search Rakuten by JAN/UPC and return the Japanese product title."""
    url = f"https://search.rakuten.co.jp/search/mall/{upc}/"
    raw = _fetch_raw(url, f"rakuten_{upc}", extra_headers={"Accept-Language": "ja-JP,ja;q=0.9"})
    if not raw:
        return None

    soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
    link = soup.select_one("a[class*='title-link']")
    if not link:
        return None

    title = link.get_text(strip=True)
    # Strip leading/trailing [...] and 【...】 tags (e.g. [PR], 【中古】)
    title = re.sub(r"^(\[[^\]]*\]|【[^】]*】)\s*", "", title)
    title = re.sub(r"\s*(\[[^\]]*\]|【[^】]*】)$", "", title)
    # Strip remaining 【...】 anywhere (e.g. trailing 【中古】 after above)
    title = re.sub(r"\s*【[^】]*】\s*", " ", title).strip()
    # Strip leading PS- or PS  prefix (e.g. "PS-アライド ジェネラル")
    title = re.sub(r"^PS[-\s]+", "", title).strip()
    # Strip platform/format noise after game title
    title = re.sub(r"\s+(プレイステーション|PlayStation|ゲームソフト|SONY|PS\d?)\b.*$", "", title, flags=re.IGNORECASE).strip()
    return title or None


# ---------------------------------------------------------------------------
# TheGamesDB – Japanese title lookup
# ---------------------------------------------------------------------------
_tgdb_key: str = ""


def _tgdb_fetch(url: str, params: dict, cache_key: str) -> dict | None:
    """Fetch a TheGamesDB endpoint with caching."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(cache_key)

    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    params["apikey"] = _tgdb_key
    time.sleep(REQUEST_DELAY)
    try:
        r = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        path.write_text(json.dumps(data), encoding="utf-8")
        return data
    except (requests.RequestException, json.JSONDecodeError) as e:
        log.warning("TGDB request failed (%s): %s", url, e)
        return None


def _extract_tgdb_ja_name(game_obj: dict) -> str | None:
    """
    TheGamesDB returns game objects that may have 'game_title' in Japanese
    (for games whose primary market is Japan) or 'alternates' with a Japanese
    entry.  Returns the first Japanese-script string found.
    """
    # Check primary title
    title = game_obj.get("game_title", "")
    if title and _contains_japanese(title):
        return title

    # Check alternate names
    for alt in game_obj.get("alternates", []) or []:
        name = alt.get("name", "") if isinstance(alt, dict) else str(alt)
        if _contains_japanese(name):
            return name

    return None


def lookup_tgdb_by_id(tgdb_id: int) -> str | None:
    if not _tgdb_key or tgdb_id <= 0:
        return None
    data = _tgdb_fetch(
        TGDB_BY_ID_URL,
        {"id": tgdb_id, "fields": "alternates"},
        f"tgdb_id_{tgdb_id}",
    )
    if not data:
        return None
    games = data.get("data", {}).get("games", [])
    if games:
        return _extract_tgdb_ja_name(games[0])
    return None


def lookup_tgdb_by_name(name: str) -> str | None:
    """Search TGDB for a game by (romanized) name on PS1, return Japanese title."""
    if not _tgdb_key:
        return None
    # Normalize: lowercase, strip bracketed suffixes like [RERELEASE]
    clean_name = re.sub(r"\s*\[.*?\]", "", name).strip()
    data = _tgdb_fetch(
        TGDB_SEARCH_URL,
        {"name": clean_name, "filter[platform]": TGDB_PS1_PLATFORM_ID, "fields": "alternates"},
        f"tgdb_search_{re.sub(r'[^a-z0-9]', '_', clean_name.lower()[:60])}",
    )
    if not data:
        return None
    games = data.get("data", {}).get("games", [])
    for game in games:
        ja = _extract_tgdb_ja_name(game)
        if ja:
            return ja
    return None


# ---------------------------------------------------------------------------
# IGDB – Japanese title lookup
# ---------------------------------------------------------------------------
_igdb_token: str | None = None
_igdb_client_id: str = ""
_igdb_client_secret: str = ""


def _get_igdb_token() -> str | None:
    global _igdb_token
    if _igdb_token:
        return _igdb_token
    if not _igdb_client_id or not _igdb_client_secret:
        return None

    try:
        r = _session.post(
            IGDB_TOKEN_URL,
            params={
                "client_id": _igdb_client_id,
                "client_secret": _igdb_client_secret,
                "grant_type": "client_credentials",
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        _igdb_token = r.json().get("access_token")
        log.info("IGDB token acquired.")
        return _igdb_token
    except (requests.RequestException, KeyError) as e:
        log.warning("Failed to get IGDB token: %s", e)
        return None


def _igdb_query(endpoint: str, body: str, cache_key: str) -> list | None:
    token = _get_igdb_token()
    if not token:
        return None

    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(cache_key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    time.sleep(REQUEST_DELAY)
    try:
        r = _session.post(
            endpoint,
            data=body,
            headers={
                "Client-ID": _igdb_client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        result = r.json()
        path.write_text(json.dumps(result), encoding="utf-8")
        return result
    except (requests.RequestException, json.JSONDecodeError) as e:
        log.warning("IGDB query failed: %s", e)
        return None


def lookup_igdb_by_name(name: str) -> str | None:
    """Search IGDB for a PS1 game by name, return Japanese alternative name."""
    if not _igdb_client_id or not _igdb_client_secret:
        return None

    clean = re.sub(r"\s*\[.*?\]", "", name).strip()
    safe = re.sub(r"[^a-z0-9]", "_", clean.lower())[:50]
    cache_key = f"igdb_search_{safe}"

    # PS1 platform ID on IGDB is 7
    query = (
        f'search "{clean}"; '
        f'fields id,name,alternative_names; '
        f'where platforms = (7); '
        f'limit 5;'
    )
    results = _igdb_query(IGDB_GAMES_URL, query, cache_key)
    if not results:
        return None

    # Collect game IDs with alternative_names
    game_ids_with_alts = [
        g["id"] for g in results
        if g.get("alternative_names")
    ]
    if not game_ids_with_alts:
        return None

    ids_str = ",".join(str(i) for i in game_ids_with_alts[:3])
    alt_cache_key = f"igdb_alts_{'_'.join(str(i) for i in game_ids_with_alts[:3])}"
    alt_query = f"fields game,name,comment; where game = ({ids_str});"
    alts = _igdb_query(IGDB_ALT_URL, alt_query, alt_cache_key)
    if not alts:
        return None

    for alt in alts:
        n = alt.get("name", "")
        if _contains_japanese(n):
            return n

    return None


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------
def enrich(
    games_json_path: Path,
    dry_run: bool,
    limit: int,
) -> None:
    log.info("Loading %s …", games_json_path)
    with open(games_json_path, encoding="utf-8") as f:
        data = json.load(f)

    games = data["games"]

    # Pre-load the J-list serial map once
    _load_psxdc_serial_map()

    stats = {"ja_name": 0, "regional_name": 0, "upc": 0, "processed": 0}

    for game in games:
        if limit and stats["processed"] >= limit:
            break

        ntscj = [gi for gi in game["gameInstances"] if gi.get("region") == "NTSC-J"]
        if not ntscj:
            continue

        game_needs_ja = not game.get("localizedNames", {}).get("ja")
        instances_need_regional = [gi for gi in ntscj if not gi.get("regionalName")]
        instances_need_upc = [gi for gi in ntscj if not gi.get("upc") and not gi.get("UPC")]

        if not game_needs_ja and not instances_need_regional and not instances_need_upc:
            continue

        stats["processed"] += 1
        game_changed = False

        for gi in ntscj:
            serial = gi.get("serial", "").strip().upper()
            needs_name = game_needs_ja or not gi.get("regionalName")
            needs_upc = not gi.get("upc") and not gi.get("UPC")

            if not needs_name and not needs_upc:
                continue

            # ---- Japanese name lookup ----
            found_ja: str | None = None
            if needs_name:
                # 0. Use shortest regionalName from any NTSC-J instance (no API needed)
                if game_needs_ja:
                    regional_names = [gi2.get("regionalName", "") for gi2 in ntscj if gi2.get("regionalName")]
                    if regional_names:
                        found_ja = min(regional_names, key=len)

                if not found_ja:
                    # 1. Rakuten by UPC/JAN (no API key needed)
                    upc = gi.get("upc") or gi.get("UPC", "")
                    if upc:
                        found_ja = lookup_rakuten_title(upc)
                    # 2. TheGamesDB by ID (fast, precise)
                    if not found_ja:
                        tgdb_id = gi.get("tgdbId", 0) or 0
                        found_ja = lookup_tgdb_by_id(tgdb_id)
                    # 3. TheGamesDB by name search
                    if not found_ja:
                        found_ja = lookup_tgdb_by_name(game["name"])
                    # 4. IGDB by name search
                    if not found_ja:
                        found_ja = lookup_igdb_by_name(game["name"])

            # ---- UPC lookup ----
            found_upc: str | None = None
            if needs_upc and serial:
                found_upc = lookup_psxdc_upc(serial)

            # ---- Apply changes ----
            if found_ja:
                if game_needs_ja:
                    log.info("[%d] %-50s  localizedNames.ja = %s", game["id"], game["name"][:50], found_ja)
                    if not dry_run:
                        if "localizedNames" not in game:
                            game["localizedNames"] = {}
                        game["localizedNames"]["ja"] = found_ja
                    stats["ja_name"] += 1
                    game_needs_ja = False
                    game_changed = True

                if not gi.get("regionalName"):
                    log.info("[%d] %-50s  regionalName = %s  (%s)", game["id"], game["name"][:50], found_ja, serial)
                    if not dry_run:
                        gi["regionalName"] = found_ja
                    stats["regional_name"] += 1
                    game_changed = True

            if found_upc:
                log.info("[%d] %-50s  upc = %s  (%s)", game["id"], game["name"][:50], found_upc, serial)
                if not dry_run:
                    gi["upc"] = found_upc
                stats["upc"] += 1
                game_changed = True

    suffix = " (DRY RUN — nothing written)" if dry_run else ""
    log.info(
        "\nDone%s.  Entries examined: %d  |  ja names: %d  |  regionalNames: %d  |  UPCs: %d",
        suffix,
        stats["processed"],
        stats["ja_name"],
        stats["regional_name"],
        stats["upc"],
    )

    if not dry_run and (stats["ja_name"] or stats["regional_name"] or stats["upc"]):
        with open(games_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("Saved → %s", games_json_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich games.json with Japanese names (TheGamesDB/IGDB) and UPCs (PSXDATACENTER).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing to disk")
    parser.add_argument("--tgdb-key", default="", metavar="KEY", help="TheGamesDB API key")
    parser.add_argument("--igdb-client-id", default="", metavar="ID", help="IGDB / Twitch client ID")
    parser.add_argument("--igdb-client-secret", default="", metavar="SECRET", help="IGDB / Twitch client secret")
    parser.add_argument("--limit", type=int, default=0, metavar="N", help="Only process first N missing entries (0 = all)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.tgdb_key and not (args.igdb_client_id and args.igdb_client_secret):
        log.warning(
            "No API keys provided — Japanese name lookup will be skipped.\n"
            "  TheGamesDB: get a free key at https://thegamesdb.net/\n"
            "  IGDB:       get free credentials at https://dev.twitch.tv/\n"
            "UPC lookup via PSXDATACENTER will still run."
        )

    # Inject keys into module-level variables
    global _tgdb_key, _igdb_client_id, _igdb_client_secret
    _tgdb_key = args.tgdb_key
    _igdb_client_id = args.igdb_client_id
    _igdb_client_secret = args.igdb_client_secret

    enrich(GAMES_JSON, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
