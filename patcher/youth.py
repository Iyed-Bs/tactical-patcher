"""Youth radar, step one: what did U17 internationals become?

Every FIFA U-17 World Cup and UEFA U-17 European Championship squad on
Wikipedia (exact birth dates and clubs, CC BY-SA), linked to Transfermarkt
and EA by name and birth date, then followed: did he become a senior
professional, a top-flight regular, a big-five player, an elite one? Those
base rates, and the early signals that move them (age within the cohort,
club academy, nation, position, team's run), are the prior every U15/U17
assessment starts from.

Run:  python -m patcher.youth
"""
from __future__ import annotations

import re

import duckdb
import pandas as pd

from .link import link, tokens
from .paths import DATA, TRANSFERMARKT
from .sources import wikipedia as wp

TITLES = DATA / "pes" / "youth_titles.txt"           # squad pages found through Wikipedia search
PAGES = [t for t in TITLES.read_text(encoding="utf-8").splitlines() if t and "Women" not in t] if TITLES.exists() else []
PLAYER = re.compile(r"\{\{\s*(?:nat )?fs (?:g )?player(?: no caps)?\s*\|(.*?)\}\}(?=\s*(?:\n|\{\{|$))", re.S | re.I)
BIRTH = re.compile(r"birth date and age2?\s*\|\s*(?:\d{4}\s*\|\s*\d{1,2}\s*\|\s*\d{1,2}\s*\|\s*)?(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", re.I)


def wikitext(title: str) -> str | None:
    res = wp._get({"action": "parse", "page": title, "prop": "wikitext", "redirects": 1}, f"parse/{wp._key(title)}.json") or {}
    return (res.get("parse") or {}).get("wikitext")


def _field(body: str, name: str) -> str | None:
    m = re.search(rf"\|\s*{name}\s*=\s*(.*?)(?=\|\s*\w+\s*=|$)", "|" + body, re.S)
    return m.group(1).strip() if m else None


def _plain(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\{\{\s*(?:efn|refn|sfn|ref)[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}", "", s, flags=re.I)   # footnotes, nested ones too
    s = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    s = re.sub(r"'{2,}", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.split(r"Capped for|\(captain\)|\bcaptain\b", s)[0]
    return s.strip(" ,;*") or None


def _team(heading: str) -> str | None:
    """Nation from a squad heading: plain text, a link, or a flag template ({{fb|FRA}}, {{flagicon|FRA}} France)."""
    text = _plain(heading)
    if text:
        return text
    m = re.search(r"\{\{\s*(?:fb|fbu|flagicon|flag|flagcountry)\s*\|\s*([A-Za-z \-]+)", heading)
    return m.group(1).strip() if m else None


def squads() -> pd.DataFrame:
    rows = []
    for title in PAGES:
        text = wikitext(title)
        if not text:
            continue
        year = int(title[:4])
        age_group = "U16" if "U-16" in title else "U17" if ("U-17" in title or "Under-17" in title) else "U19" if "Under-19" in title else "U20"
        tournament = title[5:].replace(" squads", "")
        # Team headings (=== Nation ===) give the nation of the players listed under them.
        for block in re.split(r"\n===\s*", text)[1:]:
            team = _team(block.split("===")[0])
            for m in PLAYER.finditer(block):
                body = m.group(1)
                b = BIRTH.search(body)
                rows.append({"tournament": tournament, "age_group": age_group, "year": year, "team": team, "pos": _plain(_field(body, "pos")),
                             "name": _plain(_field(body, "name")), "club": _plain(_field(body, "club")),
                             "dob": pd.Timestamp(int(b.group(1)), int(b.group(2)), int(b.group(3))) if b else pd.NaT})
    df = pd.DataFrame(rows).dropna(subset=["name"])
    df["age_at_tournament"] = (pd.to_datetime(df.year.astype(str) + "-06-01") - df.dob).dt.days / 365.25
    return df


def outcomes(sq: pd.DataFrame) -> pd.DataFrame:
    """Link to Transfermarkt by birth date + name, then measure the senior career."""
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    tm = con.execute("SELECT player_id, date_of_birth::DATE AS dob, name, first_name, last_name, highest_market_value_in_eur AS peak_value FROM tm.players WHERE date_of_birth IS NOT NULL").df()
    tm["tok"] = [tokens(a, b, c) for a, b, c in zip(tm.name, tm.first_name, tm.last_name)]
    tm["dob"] = pd.to_datetime(tm.dob).dt.date
    sq = sq.reset_index(drop=True).copy()
    sq["sq_id"] = sq.index
    s = sq.dropna(subset=["dob"]).copy()
    s["dob"] = s.dob.dt.date
    s["tok"] = [tokens(n) for n in s.name]
    links = link(tm.rename(columns={"player_id": "player_id"})[["player_id", "dob", "tok"]], s[["sq_id", "dob", "tok"]], "sq_id")
    sq = sq.merge(links[["sq_id", "player_id"]], on="sq_id", how="left")
    # Senior career from Transfermarkt appearances (league minutes; the CC0 dataset covers top flights).
    con.register("ids", sq[["player_id"]].dropna().astype("int64").drop_duplicates())
    career = con.execute("""
        SELECT a.player_id, sum(a.minutes_played) AS top_flight_minutes,
               sum(CASE WHEN c.country_name IN ('England','Spain','Italy','Germany','France') THEN a.minutes_played ELSE 0 END) AS big5_minutes,
               count(DISTINCT a.player_club_id) AS clubs
        FROM tm.appearances a JOIN ids USING (player_id)
        LEFT JOIN tm.competitions c ON c.competition_id = a.competition_id
        WHERE c.type = 'domestic_league' GROUP BY 1""").df()
    sq = sq.merge(career, on="player_id", how="left").merge(tm[["player_id", "peak_value"]], on="player_id", how="left")
    for c in ("top_flight_minutes", "big5_minutes"):
        sq[c] = sq[c].fillna(0)
    sq["regular"] = sq.top_flight_minutes >= 4500                    # 50 full top-flight games
    sq["big5_regular"] = sq.big5_minutes >= 4500
    sq["elite"] = sq.peak_value.fillna(0) >= 30e6
    return sq


def base_rates(sq: pd.DataFrame) -> dict:
    # Only cohorts old enough to judge (U17 in 2017 or earlier: 26+ today).
    old = sq[sq.year <= 2017]
    out = {"players": int(len(old)), "linked_to_transfermarkt": float(old.player_id.notna().mean()),
           "became_top_flight_regular": float(old.regular.mean()), "became_big5_regular": float(old.big5_regular.mean()),
           "peak_value_30m_plus": float(old.elite.mean())}
    old = old.assign(birth_quarter=old.dob.dt.quarter, line=old.pos.fillna("").str[:2].str.upper())
    out["by_birth_quarter"] = old.groupby("birth_quarter")[["regular", "big5_regular", "elite"]].mean().round(3).to_dict("index")
    out["by_position"] = old.groupby("line")[["regular", "big5_regular", "elite"]].mean().round(3).to_dict("index")
    out["by_age_group"] = old.groupby("age_group")[["regular", "big5_regular", "elite"]].mean().round(3).to_dict("index")
    out["by_confederation"] = old.assign(conf=old.tournament.str.extract(r"(FIFA|UEFA|South American|Africa|CONCACAF)")[0]).groupby("conf")[["regular", "big5_regular", "elite"]].mean().round(3).to_dict("index")
    nat = old.groupby("team")[["regular", "big5_regular", "elite"]].agg(["mean", "size"])
    nat = nat[nat[("regular", "size")] >= 40]
    out["by_nation_top"] = nat[("big5_regular", "mean")].sort_values(ascending=False).head(12).round(3).to_dict()
    return out


if __name__ == "__main__":
    import json
    sq = squads()
    print(f"{len(sq):,} youth tournament players parsed from {sq.groupby(['tournament', 'year']).ngroups} tournaments")
    sq = outcomes(sq)
    sq.to_parquet(DATA / "youth_u17_tournaments.parquet")
    print(json.dumps(base_rates(sq), indent=1, default=str)[:3500])
