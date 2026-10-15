"""FotMob: match performance (Opta-derived) for current players, youth sides
and youth tournaments: what ratings don't see.

Per player: career (youth and B teams included), every season's
competitions, deep season stats per competition (per 90 and percentile
against his position: progressive passing, dribbles, duels, recoveries,
dispossessions...), match ratings, injuries, market value.

Polite by design: one request at a time with a pause, every response cached
under PATCHER_DATA/raw/fotmob/ and never fetched twice. Private research use.

Run:  python -m patcher.sources.fotmob young      (FC 27 players aged <= 23)
"""
from __future__ import annotations

import json
import re
import sys
import time

import pandas as pd
import requests
from rapidfuzz import fuzz
from unidecode import unidecode

from ..paths import DATA

API = "https://www.fotmob.com/api/data"
RAW = DATA / "raw" / "fotmob"
HEADERS = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
PAUSE = 1.2
SEASONS_KEEP = ("2026/2027", "2025/2026", "2024/2025", "2023/2024", "2026", "2025", "2024")

_session = requests.Session()
_session.headers.update(HEADERS)
_last = [0.0]


def _get(path: str, params: dict, cache: str) -> dict | list | None:
    out = RAW / cache
    if out.exists():
        txt = out.read_text(encoding="utf-8")
        return json.loads(txt) if txt else None
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = _session.get(f"{API}/{path}", params=params, timeout=60)
    _last[0] = time.time()
    out.parent.mkdir(parents=True, exist_ok=True)
    txt = r.text if r.ok and r.text.lstrip().startswith(("{", "[")) else ""
    out.write_text(txt, encoding="utf-8")
    return json.loads(txt) if txt else None


def _norm(s: str) -> str:
    return " ".join(re.split(r"[^a-z]+", unidecode(str(s or "")).lower())).strip()


def search(name: str) -> list[dict]:
    key = re.sub(r"[^a-z0-9]+", "_", _norm(name))[:80]
    res = _get("search/suggest", {"term": name}, f"search/{key}.json") or []
    out = []
    for block in res:
        for s in block.get("suggestions", []):
            if s.get("type") == "player" and s not in out:
                out.append(s)
    return out


def player(fid: int | str) -> dict | None:
    return _get("playerData", {"id": fid}, f"player/{fid}.json")


def season_stats(fid: int | str, entry: str) -> dict | None:
    return _get("playerStats", {"playerId": fid, "seasonId": entry, "isFirstSeason": "false"}, f"stats/{fid}_{entry}.json")


def match_player(name: str, dob, club: str | None) -> tuple[int | None, float]:
    """FotMob id for a player: best name match among search suggestions, confirmed by exact birth date."""
    best, best_score = None, 0.0
    for s in search(name)[:5]:
        score = fuzz.token_set_ratio(_norm(name), _norm(s.get("name"))) + (15 if club and fuzz.token_set_ratio(_norm(club), _norm(s.get("teamName"))) >= 80 else 0)
        if score > best_score:
            best, best_score = s, score
    if not best or best_score < 80:
        return None, best_score
    d = player(best["id"])
    born = (d or {}).get("birthDate", {}).get("utcTime", "")[:10]
    if dob is not None and pd.notna(dob) and born and born != str(pd.Timestamp(dob).date()):
        return None, best_score                                  # same name, different person
    return int(best["id"]), best_score


def collect(fid: int) -> None:
    """Every kept season's competitions with deep stats, cached."""
    d = player(fid)
    for season in (d or {}).get("statSeasons") or []:
        if season.get("seasonName") not in SEASONS_KEEP:
            continue
        for t in season.get("tournaments") or []:
            if t.get("hasDeepStats"):
                season_stats(fid, t["entryId"])


def flatten_stats(fid: int) -> pd.DataFrame:
    """One row per (season, competition, stat): value, per 90, percentile against his position."""
    d = player(fid) or {}
    rows = []
    for season in d.get("statSeasons") or []:
        for t in season.get("tournaments") or []:
            s = (RAW / "stats" / f"{fid}_{t['entryId']}.json")
            if not s.exists() or not s.read_text(encoding="utf-8"):
                continue
            j = json.loads(s.read_text(encoding="utf-8"))
            for group in (j.get("statsSection") or {}).get("items", []):
                for it in group.get("items", []):
                    rows.append({"fotmob_id": fid, "season": season.get("seasonName"), "competition": t.get("name"),
                                 "group": group.get("title"), "stat": it.get("title"), "value": it.get("statValue"),
                                 "per90": it.get("per90"), "percentile": it.get("percentileRank")})
            for it in (j.get("topStatCard") or {}).get("items", []):
                rows.append({"fotmob_id": fid, "season": season.get("seasonName"), "competition": t.get("name"),
                             "group": "Top", "stat": it.get("title"), "value": it.get("statValue"),
                             "per90": it.get("per90"), "percentile": it.get("percentileRank")})
    return pd.DataFrame(rows)


def run_young(max_age: int = 23) -> None:
    p = pd.read_parquet(DATA / "scout" / "players.parquet")
    p = p[p.age <= max_age].sort_values(["age", "overall"], ascending=[True, False])
    links = []
    out = DATA / "fotmob_links.parquet"
    done = set(pd.read_parquet(out).ea_id) if out.exists() else set()
    for k, r in enumerate(p.itertuples()):
        if r.ea_id in done:
            continue
        try:
            fid, score = match_player(r.name, r.dob, r.club_now)
            if fid:
                collect(fid)
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            print(f"skip {r.name}: {e}", flush=True)
            fid, score = None, 0.0
        links.append({"ea_id": r.ea_id, "fotmob_id": fid, "score": score})
        if k % 100 == 0:
            prev = pd.read_parquet(out) if out.exists() else pd.DataFrame()
            pd.concat([prev, pd.DataFrame(links)]).drop_duplicates("ea_id").to_parquet(out)
            links = []
            print(f"{k}/{len(p)} players", flush=True)
    prev = pd.read_parquet(out) if out.exists() else pd.DataFrame()
    pd.concat([prev, pd.DataFrame(links)]).drop_duplicates("ea_id").to_parquet(out)
    print("done", flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["young"]:
        run_young()
