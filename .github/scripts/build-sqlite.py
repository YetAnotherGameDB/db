#!/usr/bin/env python3
"""Build a SQLite database from games.json.

Usage: python3 build-sqlite.py [source.json] [dest.db]
"""
import json
import os
import sqlite3
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "games.json"
DST = sys.argv[2] if len(sys.argv) > 2 else "games.db"

# Instance keys appear in the JSON with inconsistent casing and the odd typo;
# map lowercased JSON key -> column name.
INSTANCE_KEY_MAP = {
    "name": "name",
    "regionalname": "regional_name",
    "regioonalname": "regional_name",
    "platform": "platform",
    "language": "language",
    "serial": "serial",
    "region": "region",
    "releasedate": "release_date",
    "tgdbid": "tgdb_id",
    "gameyeid": "gameye_id",
    "pricecharting": "pricecharting_id",
    "pricechartingid": "pricecharting_id",
    "upc": "upc",
    "redumpid": "redump_id",
}

INSTANCE_COLS = [
    "id", "game_id", "name", "regional_name", "platform", "language",
    "serial", "region", "release_date", "tgdb_id", "gameye_id",
    "pricecharting_id", "upc", "redump_id",
]

SCHEMA = """
CREATE TABLE platforms (name TEXT PRIMARY KEY);
CREATE TABLE regions (name TEXT PRIMARY KEY);
CREATE TABLE games (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE localized_names (
    game_id INTEGER NOT NULL REFERENCES games(id),
    lang    TEXT NOT NULL,
    name    TEXT NOT NULL,
    PRIMARY KEY (game_id, lang)
);
CREATE TABLE game_instances (
    id               TEXT PRIMARY KEY,
    game_id          INTEGER NOT NULL REFERENCES games(id),
    name             TEXT,
    regional_name    TEXT,
    platform         TEXT,
    language         TEXT,
    serial           TEXT,
    region           TEXT,
    release_date     TEXT,
    tgdb_id          INTEGER,
    gameye_id        INTEGER,
    pricecharting_id TEXT,
    upc              TEXT,
    redump_id        INTEGER
);
CREATE INDEX idx_instances_game_id ON game_instances(game_id);
CREATE INDEX idx_instances_serial  ON game_instances(serial);
CREATE INDEX idx_games_name        ON games(name);
"""


def main():
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)

    if os.path.exists(DST):
        os.remove(DST)

    con = sqlite3.connect(DST)
    cur = con.cursor()
    cur.executescript(SCHEMA)

    enums = data.get("enums", {})
    cur.executemany("INSERT INTO platforms VALUES (?)",
                    [(p,) for p in enums.get("platforms", [])])
    cur.executemany("INSERT INTO regions VALUES (?)",
                    [(r,) for r in enums.get("regions", [])])

    inst_sql = "INSERT INTO game_instances ({}) VALUES ({})".format(
        ",".join(INSTANCE_COLS), ",".join("?" * len(INSTANCE_COLS)))

    unknown_keys = set()
    n_games = n_instances = 0

    for game in data["games"]:
        cur.execute("INSERT INTO games (id, name) VALUES (?, ?)",
                    (game["id"], game["name"]))
        n_games += 1

        for lang, lname in (game.get("localizedNames") or {}).items():
            cur.execute("INSERT INTO localized_names VALUES (?, ?, ?)",
                        (game["id"], lang, lname))

        for inst in game.get("gameInstances", []):
            row = dict.fromkeys(INSTANCE_COLS)
            row["id"] = inst["id"]
            row["game_id"] = game["id"]
            for key, value in inst.items():
                lk = key.lower()
                if lk in ("id", "localizednames"):
                    continue
                col = INSTANCE_KEY_MAP.get(lk)
                if col is None:
                    unknown_keys.add(key)
                elif row[col] is None:
                    row[col] = value
            cur.execute(inst_sql, [row[c] for c in INSTANCE_COLS])
            n_instances += 1

    con.commit()
    con.close()

    if unknown_keys:
        print(f"WARNING: skipped unknown instance keys: {sorted(unknown_keys)}",
              file=sys.stderr)
    print(f"Wrote {DST}: {n_games} games, {n_instances} instances")


if __name__ == "__main__":
    main()
