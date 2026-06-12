# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Game Meta Database — a JSON-based database that links game entries to external database sources (TheGamesDB, GameEye, PriceCharting, Redump). The primary data file is `games.json`.

## Validation

Run locally with PowerShell:
```powershell
# Validate JSON structure and unique IDs
pwsh .github/scripts/validate-games.ps1

# JSON lint (requires jsonlint installed)
jsonlint -q games.json
```

CI runs both checks automatically on every push via GitHub Actions (`.github/workflows/validation.yml`).

## Data Structure

### `games.json`
The main database. Top-level structure:
```json
{
  "enums": {
    "platforms": ["ps1", "saturn", "snes"],
    "regions": ["NTSC-J", "NTSC-U", "PAL"]
  },
  "games": [ ... ]
}
```

Each game entry:
```json
{
  "id": 1,
  "name": "GAME TITLE",
  "releaseDate": "1994-12-03",
  "gameInstances": [
    {
      "id": "1-1",
      "name": "...",
      "regionalName": "...",
      "platform": "ps1",
      "language": "[J]",
      "serial": "SLPS-XXXXX",
      "region": "NTSC-J",
      "tgdbId": 0,
      "GameyeID": 0,
      "PriceCharting": "jp-playstation/game-slug",
      "UPC": "..."
    }
  ]
}
```

- `id` must be a unique integer across all entries — enforced by the validation script.
- Each instance has its own `id`: a string of the form `"{gameId}-{n}"` (1-based per game, e.g. `"3-2"`). Uniqueness and the game-id prefix are enforced by the validation script.
- A game can have multiple `gameInstances` for different regional releases.
- `tgdbId: 0` means the TheGamesDB ID has not been looked up yet.

## Key Conventions

- Game names in `games.json` are uppercase.

