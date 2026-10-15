"""Real club names.

EA FC 27 carries placeholders where it lacks a licence ("Lombardia FC" is
Inter, "Milano FC" is AC Milan, "Bergamo Calcio" is Atalanta) and short
forms elsewhere ("OL", "Paris SG"). Every EA team gets its Transfermarkt
name instead: each Transfermarkt club votes for the EA team most of its own
players are registered at (3+ players, half of them at least), and each EA
team takes the club with the most votes. Transfermarkt's own short names
for the other side of a transfer ("Man City", "Nott'm Forest") are replaced
the same way, by club id.

Build:  python -m patcher.names     (after patcher.scout; writes scout/club_names.json)
"""
from __future__ import annotations

import json
from functools import lru_cache

import duckdb
import pandas as pd

from .paths import DATA, TRANSFERMARKT

OUT = DATA / "scout" / "club_names.json"


def tm_names() -> pd.Series:
    """Transfermarkt club id -> name: the summer window's page names first (short, current), the open dataset's otherwise."""
    tr = pd.read_parquet(DATA / "tm_transfers_2026.parquet")
    window = tr.drop_duplicates("club_id").set_index("club_id").club
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    cc0 = con.execute("SELECT club_id::BIGINT AS club_id, name FROM tm.clubs").df().set_index("club_id").name
    return window.combine_first(cc0)


def build() -> dict:
    p = pd.read_parquet(DATA / "scout" / "players.parquet")
    p["tm_club"] = pd.to_numeric(p.current_club_id, errors="coerce")
    p = p.dropna(subset=["tm_club", "team"])
    votes = p.groupby(["tm_club", "team"]).size().rename("n").reset_index()
    votes["share"] = votes.n / votes.groupby("tm_club").n.transform("sum")
    votes = votes[(votes.n >= 3) & (votes.share >= 0.5)].sort_values("n", ascending=False)
    best = votes.drop_duplicates("team").copy()                # each EA team: the Transfermarkt club with most votes
    names = tm_names()
    best["name"] = best.tm_club.astype(int).map(names)
    best = best.dropna(subset=["name"])
    best = best[~best.name.duplicated(keep="first")]           # two EA teams never share one real name
    from unidecode import unidecode
    same = lambda a, b: unidecode(a).lower().replace("-", " ") == unidecode(b).lower().replace("-", " ")
    ea_to_real = {t: n for t, n in zip(best.team, best.name) if t != n and not same(t, n)}   # keep EA's accents when that's the only difference
    out = {"ea_to_real": ea_to_real, "tm_names": {str(int(k)): v for k, v in names.items()}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    return out


@lru_cache(maxsize=1)
def table() -> dict:
    if not OUT.exists():
        return build()
    return json.loads(OUT.read_text(encoding="utf-8"))


def real(name):
    """An EA team name as the club is really called (unchanged when it already is)."""
    return table()["ea_to_real"].get(name, name) if isinstance(name, str) else name


def realise(s: pd.Series) -> pd.Series:
    return s.map(real)


def tm_name(club_id, fallback=None):
    try:
        return table()["tm_names"].get(str(int(club_id)), fallback)
    except (TypeError, ValueError):
        return fallback


if __name__ == "__main__":
    t = build()
    m = t["ea_to_real"]
    print(f"{len(m):,} EA names replaced")
    for k in ("Lombardia FC", "Milano FC", "Bergamo Calcio", "OL", "OM", "Paris SG", "Spurs", "Man Utd", "Athletic Club"):
        print(f"  {k} -> {m.get(k, '(unchanged)')}")
