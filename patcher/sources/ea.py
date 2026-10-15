"""EA SPORTS FC ratings from EA's own ratings site (drop-api.ea.com).

The public ratings page serves the current edition when the request carries
the page's feature header; without it the API falls back to the previous
edition. Pages of 100 players are cached as raw JSON under
PATCHER_DATA/raw/ea/<edition>/ and flattened to one row per player.
Train on it, never redistribute it.

Run:  python -m patcher.sources.ea            (FC 27 and FC 26)
"""
from __future__ import annotations

import json
import time

import pandas as pd
import requests

from ..paths import DATA

API = "https://drop-api.ea.com/rating/ea-sports-fc"
RAW = DATA / "raw" / "ea"
# Edition -> extra headers. The ratings page sends the feature header for the
# current edition; the bare API answers with the previous one.
EDITIONS = {
    "fc27": {"x-feature": "9281653454195393000", "drop-referrer": "https://www.ea.com/games/ea-sports-fc/ratings",
             "referer": "https://www.ea.com/games/ea-sports-fc/ratings"},
    "fc26": {},
}
UA = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def download(edition: str, page_size: int = 100, pause: float = 1.0) -> int:
    """Fetch every page once; pages already on disk are skipped."""
    out = RAW / edition
    out.mkdir(parents=True, exist_ok=True)
    headers = {**UA, **EDITIONS[edition]}
    first = requests.get(API, params={"locale": "en", "limit": page_size, "offset": 0}, headers=headers, timeout=60).json()
    total = first["totalItems"]
    for offset in range(0, total, page_size):
        path = out / f"{offset:06d}.json"
        if path.exists():
            continue
        page = first if offset == 0 else requests.get(
            API, params={"locale": "en", "limit": page_size, "offset": offset}, headers=headers, timeout=60).json()
        path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        time.sleep(pause)
    return total


def flatten(edition: str) -> pd.DataFrame:
    """One row per player: identity, club, every face stat and sub-stat, PlayStyles."""
    rows = []
    for path in sorted((RAW / edition).glob("*.json")):
        for it in json.loads(path.read_text(encoding="utf-8"))["items"]:
            row = {
                "ea_id": it["id"], "edition": edition, "first_name": it.get("firstName"), "last_name": it.get("lastName"),
                "common_name": it.get("commonName"), "dob": pd.to_datetime(it.get("birthdate"), errors="coerce"),
                "height": it.get("height"), "weight": it.get("weight"), "overall": it.get("overallRating"),
                "skill_moves": it.get("skillMoves"), "weak_foot": it.get("weakFootAbility"),
                "foot": {1: "right", 2: "left"}.get(it.get("preferredFoot")),
                "gender": (it.get("gender") or {}).get("label"), "nationality": (it.get("nationality") or {}).get("label"),
                "team": (it.get("team") or {}).get("label"), "team_id": (it.get("team") or {}).get("id"),
                "league": it.get("leagueName"), "position": (it.get("position") or {}).get("shortLabel"),
                "alt_positions": ",".join(p["shortLabel"] for p in it.get("alternatePositions") or []),
                "playstyles": ",".join(a["label"] for a in it.get("playerAbilities") or [] if a["type"]["id"] == "playStyle"),
                "playstyles_plus": ",".join(a["label"] for a in it.get("playerAbilities") or [] if a["type"]["id"] != "playStyle"),
            }
            for stat, v in (it.get("stats") or {}).items():
                row[stat] = v.get("value") if isinstance(v, dict) else v
            rows.append(row)
    return pd.DataFrame(rows).drop_duplicates("ea_id")


if __name__ == "__main__":
    for ed in EDITIONS:
        n = download(ed)
        df = flatten(ed)
        df.to_parquet(DATA / f"ea_{ed}.parquet")
        print(ed, "api total", n, "| rows", len(df), "| columns", df.shape[1])
