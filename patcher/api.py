"""Tactical Patcher API (FastAPI) and the static app.

Run:  uvicorn patcher.api:app --port 8790
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .paths import TRANSFERMARKT, WAREHOUSE
from .first_patcher import load_squad, reality, simulate

WEB = Path(__file__).resolve().parents[1] / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _warm()                       # load the models and tables once, before the first request
    yield


app = FastAPI(title="Tactical Patcher", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

LEAGUES = {"FR1": "Ligue 1", "GB1": "Premier League", "ES1": "LaLiga", "IT1": "Serie A", "L1": "Bundesliga",
           "PO1": "Liga Portugal", "NL1": "Eredivisie", "BE1": "Pro League", "TR1": "Süper Lig"}


def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(str(WAREHOUSE), read_only=True)
    c.execute(f"ATTACH IF NOT EXISTS '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    return c


def records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.replace({np.nan: None}).to_json(orient="records", date_format="iso"))


# ── Analysis pages: the same clubs and squads as the scout desk ───────────────
@lru_cache(maxsize=1)
def _window() -> pd.DataFrame:
    """The summer 2026 window (Transfermarkt pages, 64 leagues), every move in and out, the other club by its full name."""
    from .names import tm_name
    from .paths import DATA
    tr = pd.read_parquet(DATA / "tm_transfers_2026.parquet")
    tr["other_club"] = [tm_name(i, o) for i, o in zip(tr.other_club_id, tr.other_club)]
    return tr


@lru_cache(maxsize=1)
def _arrivals() -> pd.DataFrame:
    """Each player's arrival this summer (loan returns aside), latest first: the rule the scout desk uses for 'club now'."""
    tr = _window()
    a = tr[(tr.direction == "in") & ~tr.fee_text.fillna("").str.startswith("End of loan")].drop_duplicates("player_id", keep="last")
    return a.rename(columns={"club": "to_club", "club_id": "to_club_id", "competition": "to_league", "other_club": "from_club",
                             "market_value": "value_window"})[["player_id", "to_club", "to_club_id", "to_league", "from_club", "fee",
                                                               "fee_text", "loan", "value_window"]]


@lru_cache(maxsize=1)
def _squads() -> pd.DataFrame:
    """FC 27 players with their Transfermarkt club now (this summer's club if they moved) and their projected peaks."""
    sc = _scout()
    p = sc.players[["ea_id", "tm_id", "name", "age", "position", "overall", "club_now", "club_tm_id", "current_club_id"]].copy()
    p["tm_club"] = pd.to_numeric(p.club_tm_id.fillna(p.current_club_id), errors="coerce").astype("Int64")
    m = _market().set_index("ea_id")[["peak_p50", "peak_p90", "best_role", "value_eur"]]
    return p.join(m, on="ea_id")


@lru_cache(maxsize=1)
def _tm_to_ea() -> dict:
    """Transfermarkt club id -> the club's name in the scout desk (the most common FC 27 name among its players)."""
    s = _squads().dropna(subset=["tm_club"])
    known = s[s.club_now.isin(_scout().clubs.index)]           # prefer names the scout desk knows, then any
    out = {int(k): v for k, v in s.groupby("tm_club").club_now.agg(lambda x: x.mode().iat[0]).items()}
    out.update({int(k): v for k, v in known.groupby("tm_club").club_now.agg(lambda x: x.mode().iat[0]).items()})
    return out


@lru_cache(maxsize=1)
def _coaches() -> pd.DataFrame:
    """Every coach with 30+ league games since 2021/22: youth trust, the youngsters he relied on, his last club."""
    with con() as c:
        return c.execute("""
        WITH g AS (SELECT manager, club_id, season, game_id, player_id, age, mins, date FROM app_ctx
                   WHERE comp_type = 'domestic_league' AND season >= '2021' AND manager IS NOT NULL),
        tot AS (SELECT manager, count(DISTINCT game_id) AS games, count(DISTINCT season) AS seasons,
                       sum(mins) AS all_min, sum(CASE WHEN age < 21 THEN mins ELSE 0 END) AS u21_min FROM g GROUP BY 1),
        young AS (SELECT manager, player_id, sum(mins) AS mins FROM g WHERE age < 21 GROUP BY 1, 2),
        top AS (SELECT y.manager, sum(CASE WHEN y.mins >= 900 THEN 1 ELSE 0 END) AS trusted,
                       list_slice(list(p.name ORDER BY y.mins DESC), 1, 3) AS names
                FROM young y JOIN tm.players p USING (player_id) GROUP BY 1),
        last AS (SELECT manager, arg_max(club_id, date) AS club_id, max(date) AS last_game FROM g GROUP BY 1)
        SELECT t.manager, t.games, t.seasons, t.u21_min / t.all_min AS trust, coalesce(tp.trusted, 0) AS trusted, tp.names,
               l.club_id, cl.name AS club, cl.domestic_competition_id AS league, l.last_game
        FROM tot t LEFT JOIN top tp USING (manager) LEFT JOIN last l USING (manager) LEFT JOIN tm.clubs cl ON cl.club_id = l.club_id
        WHERE t.games >= 30
        """).df()


@app.get("/api/overview")
def overview() -> dict:
    with con() as c:
        report = records(c.execute("SELECT * FROM model_report").df())
        n_current = c.execute("SELECT count(*) FROM promise").fetchone()[0]
        n_history = c.execute("SELECT count(*) FROM states WHERE kind = 'history'").fetchone()[0]
    growth = json.loads((Path(WAREHOUSE).parent / "growth_model" / "metrics.json").read_text())
    return {"report": report, "n_current": n_current, "n_history": n_history, "growth": growth, "leagues": LEAGUES}


@app.get("/api/promises")
def promises(league: str = "FR1", position: str = "", q: str = "", limit: int = 60) -> list[dict]:
    sql = """
    SELECT s.player_id, s.name, coalesce(w.to_club, s.current_club_name) AS club, coalesce(w.to_league, s.current_league) AS league,
           s.position, s.sub_position, round(s.cutoff_age, 1) AS age, s.min_total::INT AS minutes,
           coalesce(w.value_window, s.mv_now) AS value, w.from_club AS moved_from, w.fee_text AS move_fee, w.loan AS move_loan,
           p.p_established, p.p_objective, p.p_big5, p.peak_p10, p.peak_p50, p.peak_p90,
           p.min_p10, p.min_p50, p.min_p90
    FROM promise p JOIN states s ON s.player_id = p.player_id AND s.kind = 'current'
    LEFT JOIN win w ON w.player_id = s.player_id
    WHERE (? = '' OR coalesce(w.to_league, s.current_league) = ?) AND (? = '' OR s.position = ?)
      AND (? = '' OR s.name ILIKE '%' || ? || '%')
    ORDER BY p.p_established DESC LIMIT ?
    """
    with con() as c:
        c.register("win", _arrivals())
        return records(c.execute(sql, [league, league, position, position, q, q, limit]).df())


@app.get("/api/player/{player_id}")
def player(player_id: int) -> dict:
    with con() as c:
        st = c.execute("SELECT * FROM states WHERE player_id = ? AND kind = 'current'", [player_id]).df()
        if st.empty:
            raise HTTPException(404, "not a current promise")
        pr = c.execute("SELECT * FROM promise WHERE player_id = ?", [player_id]).df()
        seasons = c.execute("""
            SELECT a.season, any_value(cl.name) AS club, a.competition_id AS league,
                   sum(a.mins)::INT AS minutes, round(sum(a.mins) / nullif(max(tg.n_games) * 90.0, 0), 3) AS share
            FROM app_ctx a LEFT JOIN team_games tg ON tg.club_id = a.club_id AND tg.season = a.season
                 AND tg.competition_id = a.competition_id
            LEFT JOIN tm.clubs cl ON cl.club_id = a.club_id
            WHERE a.player_id = ? AND a.comp_type = 'domestic_league' GROUP BY a.season, a.competition_id ORDER BY a.season
        """, [player_id]).df()
        values = c.execute("SELECT date, market_value_in_eur AS value FROM tm.player_valuations WHERE player_id = ? ORDER BY date",
                           [player_id]).df()
    p = records(pr)[0]
    p["drivers"], p["comparables"] = json.loads(p["drivers"]), json.loads(p["comparables"])
    w = _arrivals()[_arrivals().player_id == player_id]
    ea = _squads()[_squads().tm_id == player_id]
    return {"state": records(st)[0], "promise": p, "seasons": records(seasons), "values": records(values),
            "moved": records(w)[0] if len(w) else None, "ea_id": int(ea.ea_id.iat[0]) if len(ea) else None}


@app.get("/api/clubs")
def clubs(league: str = "FR1") -> list[dict]:
    """Clubs of a league: FC 27 level, under-21 minutes, the coach who finished 2025/26 and his youth trust, the summer's business."""
    with con() as c:
        c.register("win", _arrivals())
        df = c.execute("""
        WITH u21 AS (
          SELECT club_id, sum(CASE WHEN age < 21 THEN mins ELSE 0 END) / nullif(sum(mins), 0) AS u21_share,
                 arg_max(manager, date) AS coach
          FROM app_ctx WHERE comp_type = 'domestic_league' AND season = '2025' GROUP BY 1
        ),
        now AS (
          SELECT coalesce(w.to_club_id, s.last_club_id) AS club_id, count(*) AS promises,
                 sum(CASE WHEN p.p_established >= 0.6 THEN 1 ELSE 0 END) AS strong
          FROM states s JOIN promise p USING (player_id) LEFT JOIN win w ON w.player_id = s.player_id
          WHERE s.kind = 'current' GROUP BY 1
        )
        SELECT cl.club_id, cl.name, u.coach, u.u21_share, coalesce(n.promises, 0) AS promises, coalesce(n.strong, 0) AS strong
        FROM tm.clubs cl LEFT JOIN u21 u ON u.club_id = cl.club_id LEFT JOIN now n ON n.club_id = cl.club_id
        WHERE cl.domestic_competition_id = ? AND cl.last_season = '2025'
        """, [league]).df()
    df["club_id"] = df.club_id.astype(int)          # Transfermarkt's clubs table keeps ids as text; the window as numbers
    trust = _coaches().set_index("manager").trust
    df["coach_trust"] = df.coach.map(trust)
    tr = _window()
    money = tr.assign(fee=tr.fee.fillna(0)).groupby(["club_id", "direction"]).agg(n=("player_id", "size"), fee=("fee", "sum")).unstack(fill_value=0)
    for d in ("in", "out"):
        df[f"n_{d}"] = df.club_id.map(money[("n", d)]).fillna(0).astype(int) if ("n", d) in money else 0
        df[f"fee_{d}"] = df.club_id.map(money[("fee", d)]).fillna(0) if ("fee", d) in money else 0.0
    sc = _scout()
    ea = _tm_to_ea()
    df["ea_club"] = df.club_id.map(ea)
    df["level"] = df.ea_club.map(sc.clubs.team_overall)
    return records(df.sort_values("level", ascending=False, na_position="last"))


@app.get("/api/club/{club_id}")
def club(club_id: int) -> dict:
    club_id = int(club_id)
    with con() as c:
        info = c.execute("SELECT club_id, name, coach_name AS coach, domestic_competition_id AS league, stadium_name FROM tm.clubs WHERE club_id = ?",
                         [club_id]).df()
        if info.empty:
            raise HTTPException(404, "unknown club")
        trend = c.execute("""
            SELECT season, sum(CASE WHEN age < 21 THEN mins ELSE 0 END) / nullif(sum(mins), 0) AS u21_share,
                   mode(manager) AS manager
            FROM app_ctx WHERE club_id = ? AND comp_type = 'domestic_league' GROUP BY season ORDER BY season
        """, [club_id]).df()
        c.register("win", _arrivals())
        players = c.execute("""
            SELECT s.player_id, s.name, s.position, s.sub_position, round(s.cutoff_age, 1) AS age, s.min_total::INT AS minutes,
                   coalesce(w.value_window, s.mv_now) AS value, p.p_established, p.p_objective, p.p_big5, p.peak_p50,
                   CASE WHEN w.to_club_id IS NOT NULL AND w.to_club_id <> ? THEN w.to_club END AS left_for,
                   CASE WHEN w.to_club_id = ? AND coalesce(s.last_club_id, -1) <> ? THEN w.from_club END AS joined_from
            FROM states s JOIN promise p USING (player_id) LEFT JOIN win w ON w.player_id = s.player_id
            WHERE s.kind = 'current' AND (s.last_club_id = ? OR w.to_club_id = ?) ORDER BY p.p_established DESC
        """, [club_id] * 5).df()
        coach = c.execute("SELECT arg_max(manager, date) FROM app_ctx WHERE club_id = ? AND comp_type = 'domestic_league'", [club_id]).fetchone()[0]
    out = records(info)[0]
    out["club_id"] = club_id
    out["coach"] = coach or out["coach"]
    ct = _coaches().set_index("manager")
    out["coach_trust"] = _clean(ct.trust.get(out["coach"])) if out["coach"] in ct.index else None
    ea_name = _tm_to_ea().get(club_id)
    out["ea_club"] = ea_name
    out["level"] = _clean(_scout().clubs.team_overall.get(ea_name)) if ea_name else None
    sq = _squads()
    squad = sq[sq.tm_club == club_id].sort_values("overall", ascending=False)
    tr = _window()
    moves = tr[tr.club_id == club_id][["direction", "player", "age", "position", "other_club", "fee_text", "fee", "loan"]]
    return {"club": out, "trend": records(trend), "players": records(players),
            "squad": records(squad[["ea_id", "name", "age", "position", "overall", "peak_p50", "peak_p90", "best_role"]]),
            "window": {"in": records(moves[moves.direction == "in"].drop(columns="direction")),
                       "out": records(moves[moves.direction == "out"].drop(columns="direction"))}}


@lru_cache(maxsize=1)
def _actions() -> list[dict]:
    path = Path(WAREHOUSE).parent / "wy_player_season.parquet"
    if not path.exists():
        return []
    with con() as c:
        c.execute(f"CREATE TEMP VIEW vs AS SELECT * FROM read_parquet('{path.as_posix()}')")
        return records(c.execute("""
        SELECT p.name, p.date_of_birth::DATE AS dob, v.competition, v.minutes_played AS minutes,
               v.vaep_p90, v.off_p90, v.def_p90, v.x, v.y,
               date_diff('day', p.date_of_birth, DATE '2018-01-01') / 365.25 AS age_then,
               p.highest_market_value_in_eur AS peak_value, p.current_club_name AS club_now
        FROM vs v JOIN link_wyscout l ON l.wy_id = v.wy_id JOIN tm.players p ON p.player_id = l.player_id
        WHERE v.minutes_played >= 900 AND p.date_of_birth >= DATE '1996-07-01'
          AND v.competition IN ('English first division', 'Spanish first division', 'Italian first division',
                                'German first division', 'French first division')
        ORDER BY v.vaep_p90 DESC LIMIT 40
        """).df())


@app.get("/api/actions/young")
def actions_young() -> list[dict]:
    return _actions()


@lru_cache(maxsize=1)
def _managers() -> list[dict]:
    with con() as c:
        df = c.execute("""
        WITH grad AS (
          SELECT a.manager, s.player_id, any_value(s.out_min_league) AS later
          FROM app_ctx a JOIN states s ON s.player_id = a.player_id AND s.kind = 'history' AND s.cutoff_age = 21
          WHERE a.age < 21 AND a.comp_type = 'domestic_league'
          GROUP BY a.manager, s.player_id HAVING sum(a.mins) >= 900
        )
        SELECT m.manager, m.games, m.u21_min / m.all_min AS trust,
               count(g.player_id) AS developed, avg(CASE WHEN g.later >= 4500 THEN 1.0 ELSE 0 END) AS graduation
        FROM manager_trust m LEFT JOIN grad g USING (manager)
        WHERE m.games >= 80 GROUP BY ALL HAVING count(g.player_id) >= 5
        ORDER BY developed DESC
        """).df()
    return records(df)


@app.get("/api/managers")
def managers() -> list[dict]:
    return _managers()


@app.get("/api/coaches")
def coaches() -> list[dict]:
    df = _coaches().copy()
    grad = pd.DataFrame(_managers()).set_index("manager").graduation if _managers() else pd.Series(dtype=float)
    df["graduation"] = df.manager.map(grad)
    df["ea_club"] = df.club_id.map(_tm_to_ea())
    df["last_game"] = df.last_game.astype(str)
    return records(df.sort_values("trust", ascending=False))


@app.get("/api/patcher/clubs")
def patcher_clubs() -> list[dict]:
    with con() as c:
        return records(c.execute("""
            SELECT league_name AS league, club_name AS club, count(*) AS players, round(avg(overall), 1) AS level
            FROM fifa WHERE fifa_version = 23 AND league_name IN ('Ligue 1', 'Ligue 2', 'Premier League', 'La Liga',
                 'Serie A', 'Bundesliga', 'Eredivisie', 'Liga Portugal')
            GROUP BY ALL ORDER BY league, club""").df())


class PatcherRun(BaseModel):
    club: str
    youth_trust: float = Field(0.0, ge=-5, le=20)
    seasons: int = Field(3, ge=1, le=6)
    runs: int = Field(120, ge=20, le=400)


@app.post("/api/patcher/run")
def patcher_run(body: PatcherRun) -> dict:
    squad = load_squad(body.club)
    if squad.players.empty:
        raise HTTPException(404, "club not in FIFA 23")
    out = simulate(squad, seasons=body.seasons, youth_trust=body.youth_trust, runs=body.runs)
    real = reality(squad.players.player_id.dropna().tolist())
    real = real.rename(columns={"season": "played_season"})
    real["played_season"] = real.played_season.astype(float)
    out = out.merge(real, on=["player_id", "played_season"], how="left")
    with con() as c:
        has = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'revealed_trust'").fetchone()[0]
        rev = records(c.execute("SELECT * FROM revealed_trust WHERE club = ?", [body.club]).df()) if has else []
    return {"club": body.club, "rows": records(out), "revealed": rev[0] if rev else None}



# ── Tactical Patcher: the scout's app ─────────────────────────────────────────
@lru_cache(maxsize=1)
def _scout():
    from .profile import Profiler
    from .scout import Scout
    sc = Scout()
    sc.profiler = Profiler(sc.players)
    return sc


def _clean(v):
    """JSON-safe scalars: numpy numbers, NaN/NA/NaT, timestamps, arrays."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) else round(float(v), 3)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (str, int, list, dict)):
        return v
    return str(v)


@app.get("/api/scout/search")
def scout_search(q: str, limit: int = 25) -> list[dict]:
    if len(q.strip()) < 2:
        return []
    hits = _scout().search(q, limit)
    cols = ["ea_id", "name", "age", "position", "overall", "club_now", "league", "nationality"]
    return records(hits[cols])


@app.get("/api/scout/clubs")
def scout_clubs() -> list[dict]:
    c = _scout().clubs.reset_index()
    return records(c.sort_values(["league_level", "team_overall"], ascending=[True, False]))


@lru_cache(maxsize=256)
def _card(ea_id: int) -> dict:
    """Everything about the player that doesn't depend on your club: card, history, profile, comparables, Master League."""
    from .scout import ml_projection, youth_price
    sc = _scout()
    players = sc.players.set_index("ea_id")
    row = players.loc[ea_id].copy()
    row["ea_id"] = ea_id
    info = {k: _clean(v) for k, v in row.items() if not isinstance(v, (list, dict))}
    hist = sc.history[sc.history.ea_id == ea_id].sort_values("fifa_version")
    history = [{"season": f"{2000 + int(v) - 1}/{str(2000 + int(v))[-2:]}", "overall": int(o), "club": c}
               for v, o, c in zip(hist.fifa_version, hist.overall, hist.club)]
    if pd.notna(row.overall_fc26):
        history.append({"season": "2025/26", "overall": int(row.overall_fc26), "club": None})
    history.append({"season": "2026/27", "overall": int(row.overall), "club": row.club_now})
    comps = sc.comparables(ea_id)
    fc27 = players.overall
    comparables = [{"name": r["name"], "season": f"{2000 + int(r.fifa_version) - 1}/{str(2000 + int(r.fifa_version))[-2:]}",
                    "age": int(r.age), "overall": int(r.overall), "club": r.club,
                    "overall_3y": _clean(r.overall_3y), "club_3y": r.club_3y, "peak_ea_history": _clean(r.peak),
                    "now_fc27": _clean(fc27.get(r.player_id))} for _, r in comps.iterrows()]
    pr = sc.profiler
    out = {"player": info, "history": history, "youth": [{k: _clean(v) for k, v in y.items()} for y in youth_price(sc, ea_id)],
           "comparables": comparables, "master_league": ml_projection(row),
           "profile": {"archetype": pr.archetype(ea_id), "roles": pr.roles(ea_id, 5), "plays_like": pr.plays_like(ea_id, 5)}}
    return json.loads(json.dumps(out, default=_clean))


@lru_cache(maxsize=512)
def _judge(ea_id: int, our_club: str) -> dict:
    """What he means for your club: four seasons three ways, the call, when to buy (about a second; cached after)."""
    from .decide import final_call, solve
    from .scout import verdict
    sc = _scout()
    v, timing = verdict(sc, ea_id, our_club), solve(sc, ea_id, our_club, runs=300)
    face = v["ours"]["face"]
    grows = sc.profiler.roles_after(ea_id, {k: x[0] for k, x in face.items()}, {k: x[2] for k, x in face.items()})
    return json.loads(json.dumps({"verdict": final_call(v, timing), "timing": timing, "grows_into": grows}, default=_clean))


def _known(ea_id: int, our_club: str | None) -> None:
    sc = _scout()
    if ea_id not in _ids():
        raise HTTPException(404, "unknown player")
    if our_club and our_club not in sc.clubs.index:
        raise HTTPException(400, f"unknown club: {our_club}")


@lru_cache(maxsize=1)
def _ids() -> frozenset:
    return frozenset(int(e) for e in _scout().players.ea_id)


@app.get("/api/scout/player/{ea_id}")
def scout_player(ea_id: int, our_club: str | None = None) -> dict:
    """The card; with our_club, the judgement too (the app asks for the two separately so the card shows at once)."""
    _known(ea_id, our_club)
    card = _card(ea_id)
    if not our_club:
        return card
    j = _judge(ea_id, our_club)
    return {**card, "verdict": j["verdict"], "timing": j["timing"], "profile": {**card["profile"], "grows_into": j["grows_into"]}}


@app.get("/api/scout/player/{ea_id}/judge")
def scout_judge(ea_id: int, our_club: str) -> dict:
    _known(ea_id, our_club)
    return _judge(ea_id, our_club)


# ── Market search ────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _market() -> pd.DataFrame:
    from .names import realise
    from .scout import OUT
    m = pd.read_parquet(OUT / "market.parquet")
    m["club_now"] = realise(m.club_now)
    return m


MARKET_COLS = ["ea_id", "name", "age", "position", "overall", "peak_p50", "peak_p90", "growth_room", "club_now", "league",
               "nationality", "value_eur", "minutes_last", "best_role", "archetype", "fl_playing_style", "moved_this_summer"]
RANGES = {"age": "age", "overall": "overall", "peak": "peak_p50", "peak_high": "peak_p90", "room": "growth_room",
          "value": "value_eur", "minutes": "minutes_last", "height": "height_cm", "skill_moves": "skill_moves", "weak_foot": "weak_foot"}


@app.get("/api/scout/market/options")
def market_options() -> dict:
    from .profile import ROLES
    from .sources.fl import ABILITIES
    m = _market()
    styles = {s for col in ("playstyles", "playstyles_plus") for v in m[col].dropna() for s in v.split(",") if s}
    return {"positions": sorted(m.position.dropna().unique().tolist()), "leagues": sorted(m.league.dropna().unique().tolist()),
            "nations": sorted(m.nationality.dropna().unique().tolist()), "roles": list(ROLES),
            "archetypes": sorted(m.archetype.dropna().unique().tolist()), "fl_styles": sorted(m.fl_playing_style.dropna().unique().tolist()),
            "playstyles": sorted(styles),
            "ea_attributes": ["pac", "sho", "pas", "dri", "def", "phy", "reactions", "composure", "vision", "positioning", "interceptions"],
            "fl_abilities": [f"fl_{a}" for a in ABILITIES]}


@app.get("/api/scout/market")
def market(request: Request) -> dict:
    """Master League-style market search, plus role fit, archetype and projections.
    Ranges as <name>_min / <name>_max (age, overall, peak, peak_high, room, value, minutes, height, skill_moves, weak_foot);
    lists as comma-separated values (position, league, nation, fl_style, foot, playstyle); role + role_fit;
    attrs as name:min pairs over EA attributes (pac, composure...) and Football Life abilities (fl_tight_possession...)."""
    q = dict(request.query_params)
    m = _market()
    k = pd.Series(True, index=m.index)
    for key, col in RANGES.items():
        if q.get(f"{key}_min"):
            k &= m[col] >= float(q[f"{key}_min"])
        if q.get(f"{key}_max"):
            k &= m[col] <= float(q[f"{key}_max"])
    if q.get("position"):
        pos = q["position"].split(",")
        k &= m.position.isin(pos) | m.alt_positions.fillna("").map(lambda a: any(x in a.split(",") for x in pos))
    for key, col in (("league", "league"), ("nation", "nationality"), ("fl_style", "fl_playing_style"), ("foot", "foot")):
        if q.get(key):
            k &= m[col].fillna("").str.lower().isin([v.lower() for v in q[key].split(",")])
    if q.get("club"):
        k &= m.club_now.fillna("").str.contains(q["club"], case=False, regex=False)
    if q.get("archetype"):
        k &= m.archetype.fillna("").str.contains(q["archetype"], case=False, regex=False)
    if q.get("playstyle"):
        styles = (m.playstyles.fillna("") + "," + m.playstyles_plus.fillna("")).str.lower()
        for ps in q["playstyle"].lower().split(","):
            k &= styles.str.contains(ps.strip(), regex=False)
    role = q.get("role")
    if role and f"fit_{role}" in m:
        k &= m[f"fit_{role}"].fillna(-1) >= float(q.get("role_fit") or 0)
    if q.get("moved") in ("true", "false"):
        k &= m.moved_this_summer == (q["moved"] == "true")
    for part in (q.get("attrs") or "").split(","):
        name, _, lo = part.partition(":")
        if name in m and lo:
            k &= m[name] >= float(lo)
    sort = q.get("sort", "peak_p50")
    sort_col = f"level_{role}" if sort == "role" and role else sort if sort in m else "peak_p50"
    hits = m[k].sort_values(sort_col, ascending=q.get("dir") == "asc", na_position="last")
    cols = MARKET_COLS + ([f"fit_{role}", f"level_{role}"] if role and f"fit_{role}" in m else [])
    offset, limit = int(q.get("offset", 0)), min(int(q.get("limit", 100)), 500)
    return {"total": int(k.sum()), "rows": records(hits[cols].iloc[offset:offset + limit])}


# ── Squad lab ────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _lab():
    from .squad import SquadLab
    return SquadLab(_scout())


class LabRun(BaseModel):
    club: str
    formation: str = "4-3-3"
    style: str = "positional"
    plan: dict = {}
    advanced: list[str] | None = None
    roles: list[str | None] | None = None
    pins: dict[str, int] = {}
    youth_trust: float = Field(default=0.3, ge=0, le=1)
    rotation: float = Field(default=0.3, ge=0, le=1)
    seasons: int = Field(default=3, ge=1, le=4)
    add: list[int] = []
    remove: list[int] = []


@app.get("/api/patcher/lab/options")
def patcher_lab_options() -> dict:
    from . import tactics
    return tactics.options()


@app.post("/api/patcher/lab")
def patcher_lab(body: LabRun) -> dict:
    from . import tactics
    if body.club not in _scout().clubs.index:
        raise HTTPException(400, f"unknown club: {body.club}")
    if body.formation not in tactics.FORMATIONS or body.style not in tactics.STYLES:
        raise HTTPException(400, "unknown formation or style")
    plan = {k: v for k, v in body.plan.items() if k in tactics.DEFAULT_PLAN}
    for k, v in plan.items():
        ok = v in tactics.INSTRUCTIONS[k] if k in tactics.INSTRUCTIONS else isinstance(v, (int, float)) and 1 <= v <= 10
        if not ok:
            raise HTTPException(400, f"bad instruction: {k}={v}")
    known = {a for group in tactics.ADVANCED.values() for a in group}
    adv = None if body.advanced is None else [a for a in body.advanced if a in known]
    out = _lab().run(body.club, body.formation, body.style, plan, adv, body.roles, {int(k): v for k, v in body.pins.items()},
                     body.youth_trust, body.rotation, body.seasons, body.add, body.remove)
    return json.loads(json.dumps(out, default=_clean))


class Scenario(BaseModel):
    ea_id: int
    club: str | None = None
    role: str | None = Field(default=None, pattern="^(starter|rotation|bench|loan_starter)$")
    seasons: int = Field(default=4, ge=1, le=6)


@app.post("/api/scout/project")
def scout_project(body: Scenario) -> dict:
    try:
        return _scout().project(body.ea_id, body.club, body.role, body.seasons)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Scouting lists ───────────────────────────────────────────────────────────
# Two lists a scout keeps: the shortlist, and the players few are watching yet. A player sits in one list at a time.
# Stored next to the scout tables (PATCHER_DATA/scout/lists.json), so they outlive the browser.
LISTS = {"shortlist": "Shortlist", "radar": "Under the radar"}


def _lists_path() -> Path:
    from .scout import OUT
    return OUT / "lists.json"


def _read_lists() -> dict:
    p = _lists_path()
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {k: list(data.get(k, [])) for k in LISTS}


def _write_lists(data: dict) -> None:
    p = _lists_path()
    tmp = p.with_suffix(".tmp")                                       # write then swap: a crash never leaves half a file
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


class ListNote(BaseModel):
    note: str = Field(default="", max_length=500)


@app.get("/api/scout/lists")
def scout_lists() -> dict:
    """Both lists, each player joined to today's market row (rating, peaks, value), in the order they were added."""
    m = _market().set_index("ea_id")
    out = {"labels": LISTS}
    for k, items in _read_lists().items():
        rows = []
        for it in items:
            if it["ea_id"] not in m.index:
                continue
            r = m.loc[it["ea_id"]]
            rows.append({**it, "name": r["name"], "age": int(r.age), "position": r.position, "club_now": r.club_now,
                         "overall": int(r.overall), "peak_p50": _clean(r.peak_p50), "peak_p90": _clean(r.peak_p90),
                         "value_eur": _clean(r.value_eur), "best_role": _clean(r.best_role)})
        out[k] = rows
    return out


@app.put("/api/scout/lists/{name}/{ea_id}")
def scout_list_put(name: str, ea_id: int, body: ListNote) -> dict:
    """Add a player to a list (moving him out of the other one), or update his note."""
    if name not in LISTS:
        raise HTTPException(404, "unknown list")
    _known(ea_id, None)
    from datetime import date
    data = _read_lists()
    for k in LISTS:
        if k != name:
            data[k] = [x for x in data[k] if x["ea_id"] != ea_id]
    entry = next((x for x in data[name] if x["ea_id"] == ea_id), None)
    if entry:
        entry["note"] = body.note
    else:
        data[name].append({"ea_id": ea_id, "note": body.note, "added": date.today().isoformat()})
    _write_lists(data)
    return scout_lists()


@app.delete("/api/scout/lists/{name}/{ea_id}")
def scout_list_delete(name: str, ea_id: int) -> dict:
    if name not in LISTS:
        raise HTTPException(404, "unknown list")
    data = _read_lists()
    data[name] = [x for x in data[name] if x["ea_id"] != ea_id]
    _write_lists(data)
    return scout_lists()


# ── Squad and comparison ─────────────────────────────────────────────────────
LINE_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "ATT": 3}


@app.get("/api/scout/squad")
def scout_squad(club: str) -> list[dict]:
    """A club's FC 27 squad by line, best first: the players a scouted name gets compared with."""
    sc = _scout()
    if club not in sc.clubs.index:
        raise HTTPException(400, f"unknown club: {club}")
    s = sc.squads[sc.squads.club_now == club].copy()
    s["order"] = s.line.map(LINE_ORDER)
    s = s.sort_values(["order", "overall"], ascending=[True, False])
    return records(s[["ea_id", "name", "position", "overall", "age", "line"]])


@lru_cache(maxsize=512)
def _with_you(ea_id: int, our_club: str) -> dict:
    """Four seasons at your club, in the role your depth chart gives him (a squad member: in his own role)."""
    return json.loads(json.dumps(_scout().project(ea_id, our_club), default=_clean))


@app.get("/api/scout/compare")
def scout_compare(ids: str, our_club: str) -> dict:
    """Two to four players side by side, every one of them projected at your club, so the numbers compare like for like."""
    want = list(dict.fromkeys(int(x) for x in ids.split(",") if x.strip().isdigit()))[:4]
    if len(want) < 2:
        raise HTTPException(400, "pick two to four players")
    for i in want:
        _known(i, our_club)
    sc, m = _scout(), _market().set_index("ea_id")
    face = {"pace": "pac", "shooting": "sho", "passing": "pas", "dribbling": "dri", "defending": "def", "physical": "phy"}
    mental = ["reactions", "composure", "vision", "positioning", "interceptions"]
    out = []
    for i in want:
        p = _card(i)["player"]
        mk = m.loc[i] if i in m.index else None
        proj = _with_you(i, our_club)
        out.append({
            "ea_id": i, "name": p["name"], "age": p["age"], "position": p["position"], "club_now": p["club_now"],
            "nationality": p.get("nationality"), "overall": p["overall"], "value_eur": p.get("value_eur"),
            "minutes_last": p.get("minutes_last"), "fl_playing_style": p.get("fl_playing_style"),
            "in_squad": p["club_now"] == our_club,
            "growth_room": _clean(mk.growth_room) if mk is not None else None,
            "archetype": sc.profiler.archetype(i).get("name"),
            "roles": sc.profiler.roles(i, 3),
            "face": {k: p.get(ea) for k, ea in face.items()},
            "mental": {k: p.get(k) for k in mental},
            "with_you": {"role": proj["role"], "seasons": proj["seasons"], "overall": proj["overall"],
                         "peak_p50": proj["peak_p50"], "peak_p90": proj["peak_p90"]},
        })
    return {"our_club": our_club, "players": out}


# ── Home ─────────────────────────────────────────────────────────────────────
# Featured players on the title screen: (EA id, FotMob id for the portrait, kit). Left, centre, right.
FEATURED = [(277954, 1412132, "juventus"), (80376, 1597668, "liverpool"), (70497, 1536436, "dortmund")]


@lru_cache(maxsize=1)
def _home() -> dict:
    from .paths import DATA
    m = _market().set_index("ea_id")
    sc = _scout()
    feat = []
    for ea_id, fotmob_id, kit in FEATURED:
        r = m.loc[ea_id]
        like = sc.profiler.plays_like(ea_id, 1)["references"]
        render = (DATA / "home" / f"{ea_id}.webp").exists()
        feat.append({"ea_id": ea_id, "fotmob_id": fotmob_id, "kit": kit, "render": render, "name": r["name"], "age": int(r.age), "position": r.position,
                     "overall": int(r.overall), "club": r.club_now, "nationality": r.nationality, "peak_p50": _clean(r.peak_p50),
                     "peak_p90": _clean(r.peak_p90), "best_role": r.best_role, "value_eur": _clean(r.value_eur),
                     "plays_like": like[0]["name"] if like else None})
    tr = _window()
    moves = tr[(tr.direction == "in") & (tr.fee > 0)].sort_values("fee", ascending=False).drop_duplicates("player_id").head(12)
    ticker = [{"player": r.player, "to": r.club, "from": r.other_club, "fee": float(r.fee), "age": _clean(r.age)} for r in moves.itertuples()]
    credits_file = DATA / "home" / "credits.json"
    credits = json.loads(credits_file.read_text(encoding="utf-8")) if credits_file.exists() else {}
    return {"featured": feat, "credits": sorted(set(credits.values())), "ticker": ticker, "players": int(len(m)), "clubs": int(len(sc.clubs)),
            "leagues": int(sc.clubs.league.nunique()), "moves": int((tr.direction == "in").sum())}


@app.get("/api/home")
def home() -> dict:
    return _home()


RENDERS = None


@app.get("/media/player/{ea_id}.webp")
def player_render(ea_id: int) -> FileResponse:
    """A featured player's cut-out photo, kept in the data folder (club photos: not redistributed with the code)."""
    from .paths import DATA
    path = DATA / "home" / f"{ea_id}.webp"
    if not path.exists():
        raise HTTPException(404, "no render")
    return FileResponse(path, media_type="image/webp", headers={"cache-control": "public, max-age=86400"})


def _warm() -> None:
    sc = _scout()
    from . import rollout
    if sc._side is None:
        sc._side, sc._cal = rollout.load(), rollout.calibration()
    _ids()
    _market()
    _squads()
    _tm_to_ea()
    _coaches()
    _lab()
    _home()


@app.get("/analysis")
def analysis() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/scout")
def scout_app() -> FileResponse:
    return FileResponse(WEB / "scout.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "home.html")
