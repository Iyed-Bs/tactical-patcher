"""The first Patcher: a Master League season engine on real growth (FIFA 23 squads, revealed youth trust).

Starts a club from its real FIFA 23 squad (2022/23), then plays seasons
forward. Each season:
1. the manager picks who plays: players are ranked by level within their
   line, with a youth-trust bonus for under-21s (the manager archetypes
   found in the data: selective vs volume developers), and given a share
   of the minutes (starter, rotation, bench);
2. every player's level changes by a draw from the growth engine's learned
   distribution (P10/P50/P90 for his age, level, potential, position and
   minutes), the same engine that beat the age-average baseline by 17 %;
3. everyone gets a year older.

Run many times (Monte Carlo) to turn one story into a range. The backtest
compares the minutes the simulated manager hands out in 2022/23-2024/25 with
what really happened (Transfermarkt), so the patch can be judged against
reality.
"""
from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from .growth import FEATURES, QUANTILES, load
from .paths import TRANSFERMARKT, WAREHOUSE

LINES = {"GK": 1, "DEF": 4, "MID": 3, "ATT": 3}   # starters per line in a 4-3-3
SHARES = {"starter": 0.82, "rotation": 0.35, "fringe": 0.10, "bench": 0.02}


@dataclass
class Squad:
    club: str
    players: pd.DataFrame   # fifa_id, player_id, name, age, overall, potential, line, league_level, lq


def line_of(positions: str) -> str:
    first = (positions or "").split(",")[0].strip()
    if first == "GK":
        return "GK"
    if first in {"CB", "LB", "RB", "LWB", "RWB"}:
        return "DEF"
    if first in {"CDM", "CM", "CAM", "LM", "RM"}:
        return "MID"
    return "ATT"


def load_squad(club: str, version: int = 23) -> Squad:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    df = con.execute("""
    SELECT f.player_id AS fifa_id, l.player_id, f.short_name AS name, f.age, f.overall, f.potential,
           f.player_positions, f.league_level, f.league_name, f.club_name
    FROM fifa f LEFT JOIN link_fifa l ON l.fifa_id = f.player_id
    WHERE f.fifa_version = ? AND f.club_name = ?
    """, [version, club]).df()
    lq = con.execute("""
    SELECT avg(lq) FROM league_quality lq JOIN app_ctx a USING (competition_id, season)
    WHERE a.season = '2022' AND a.club_id IN (
      SELECT DISTINCT a2.club_id FROM app_ctx a2 JOIN link_fifa l ON l.player_id = a2.player_id
      JOIN fifa f ON f.player_id = l.fifa_id AND f.fifa_version = ? AND f.club_name = ?)
    """, [version, club]).fetchone()[0]
    con.close()
    df["line"] = df.player_positions.map(line_of)
    df["lq"] = float(lq or 0)
    return Squad(club, df)


def minutes_shares(players: pd.DataFrame, youth_trust: float, rng: np.random.Generator) -> np.ndarray:
    """Rank within each line by level (+ youth bonus + a little noise) and hand out shares."""
    score = players.overall + youth_trust * (players.age < 21) + rng.normal(0, 1.0, len(players))
    shares = np.full(len(players), SHARES["bench"])
    for line, n_start in LINES.items():
        idx = np.where(players.line.values == line)[0]
        order = idx[np.argsort(-score.values[idx])]
        for rank, i in enumerate(order):
            if rank < n_start:
                shares[i] = SHARES["starter"]
            elif rank < n_start + max(1, n_start // 2):
                shares[i] = SHARES["rotation"]
            elif rank < 2 * n_start + 1:
                shares[i] = SHARES["fringe"]
    return shares


def simulate(squad: Squad, seasons: int = 3, youth_trust: float = 0.0, runs: int = 200, seed: int = 7) -> pd.DataFrame:
    """Monte Carlo seasons. Returns one row per player per season with level and minutes quantiles."""
    models, qs = load(), QUANTILES
    rng = np.random.default_rng(seed)
    base = squad.players.reset_index(drop=True)
    first = base.player_positions.fillna("").str.split(",").str[0].str.strip()
    pos = {"pos_gk": first == "GK", "pos_def": first.isin(["CB", "LB", "RB", "LWB", "RWB"]),
           "pos_mid": first.isin(["CDM", "CM", "CAM", "LM", "RM"]), "pos_att": first.isin(["ST", "CF", "LW", "RW"])}
    levels = np.zeros((runs, seasons + 1, len(base)))
    shares_all = np.zeros((runs, seasons, len(base)))
    for r in range(runs):
        p = base.copy()
        levels[r, 0] = p.overall
        for s in range(seasons):
            share = minutes_shares(p, youth_trust, rng)
            X = pd.DataFrame({
                "age": p.age, "overall": p.overall, "potential": p.potential, "gap": p.potential - p.overall,
                **{k: v.astype(int) for k, v in pos.items()}, "share": share, "log_lq": np.log1p(p.lq),
                "team_strength": 0.5, "league_level": p.league_level.fillna(1),
            })[FEATURES]
            q = np.column_stack([models[x].predict(X) for x in qs])        # P10, P50, P90
            u = rng.uniform(0, 1, len(p))
            # Piecewise-linear draw through the three quantiles (tails extended symmetrically).
            lo, mid, hi = q[:, 0], q[:, 1], q[:, 2]
            delta = np.where(u < 0.5, mid + (u - 0.5) / 0.4 * (mid - lo), mid + (u - 0.5) / 0.4 * (hi - mid))
            p["overall"] = np.clip(p.overall + np.round(delta), 30, 99)
            p["age"] = p.age + 1
            levels[r, s + 1] = p.overall
            shares_all[r, s] = share
    rows = []
    for i, pl in base.iterrows():
        for s in range(seasons + 1):
            lv = levels[:, s, i]
            rows.append({
                "fifa_id": pl.fifa_id, "player_id": pl.player_id, "name": pl["name"], "line": pl.line,
                "season": 2022 + s, "age": pl.age + s,
                "level_p10": float(np.percentile(lv, 10)), "level_p50": float(np.percentile(lv, 50)),
                "level_p90": float(np.percentile(lv, 90)),
                "share_mean": float(shares_all[:, s - 1, i].mean()) if s > 0 else np.nan,
                # minutes are handed out during the season that starts in played_season
                # (row 2023 = level after 2022/23, minutes of 2022/23)
                "played_season": 2022 + s - 1 if s > 0 else np.nan,
            })
    return pd.DataFrame(rows)


def club_of(squad: Squad) -> int | None:
    """Transfermarkt id of the squad's club: where the whole squad played most in 2022/23."""
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.register("ids", pd.DataFrame({"player_id": [int(x) for x in squad.players.player_id.dropna()]}))
    row = con.execute("""
        SELECT club_id FROM app_ctx JOIN ids USING (player_id)
        WHERE season = '2022' AND comp_type = 'domestic_league' GROUP BY club_id ORDER BY sum(mins) DESC LIMIT 1
    """).fetchone()
    con.close()
    return int(row[0]) if row else None


def reality(player_ids: list[int], club_id: int | None = None) -> pd.DataFrame:
    """What really happened: league minutes share in 2022/23, 2023/24 and 2024/25 (Transfermarkt).

    Without club_id: minutes wherever the player played (seasons with no
    league appearance are missing, since the data only records games played).
    With club_id: minutes at that club only, in every season the player was
    on its books (a market valuation between September and April lists him
    there), so a season spent unused counts as 0 instead of vanishing.
    """
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.execute(f"ATTACH IF NOT EXISTS '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    con.register("ids", pd.DataFrame({"player_id": [int(x) for x in player_ids if pd.notna(x)]}))
    played = f"""
    SELECT a.player_id, CAST(a.season AS INT) AS season,
           sum(a.mins) / nullif(max(tg.n_games) * 90.0, 0) AS real_share
    FROM app_ctx a JOIN ids USING (player_id)
    LEFT JOIN team_games tg ON tg.club_id = a.club_id AND tg.season = a.season AND tg.competition_id = a.competition_id
    WHERE a.comp_type = 'domestic_league' AND a.season IN ('2022', '2023', '2024')
      {f"AND a.club_id = {int(club_id)}" if club_id is not None else ""}
    GROUP BY ALL"""
    if club_id is None:
        df = con.execute(played).df()
    else:
        # A September-April valuation belongs to the season that started that summer.
        df = con.execute(f"""
        WITH books AS (
          SELECT DISTINCT v.player_id, CAST(year(v.date - INTERVAL 8 MONTH) AS INT) AS season
          FROM tm.player_valuations v JOIN ids USING (player_id)
          WHERE v.current_club_id = {int(club_id)} AND month(v.date) NOT IN (5, 6, 7, 8)
        ), played AS ({played})
        SELECT player_id, season, coalesce(p.real_share, 0) AS real_share
        FROM (SELECT player_id, season FROM books WHERE season BETWEEN 2022 AND 2024
              UNION SELECT player_id, season FROM played) s
        LEFT JOIN played p USING (player_id, season)
        """).df()
    con.close()
    return df


def reveal(league: str = "Ligue 1", trusts: range = range(0, 17, 2), runs: int = 40) -> pd.DataFrame:
    """Each club's revealed youth trust: the setting whose simulated minutes for
    its under-21s best match what really happened in 2022/23-2024/25 (mean
    absolute error of the minutes share, seasons spent unused counting as 0)."""
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    clubs = [r[0] for r in con.execute(
        "SELECT DISTINCT club_name FROM fifa WHERE fifa_version = 23 AND league_name = ?", [league]).fetchall()]
    con.close()
    rows = []
    for club in clubs:
        squad = load_squad(club)
        young = squad.players[squad.players.age <= 20]   # under 21 in 2022/23, as the trust bonus
        real = reality(young.player_id.dropna().tolist(), club_id=club_of(squad))
        if real.empty:
            continue
        real_mean = real.groupby("player_id").real_share.mean()
        errors = {}
        for t in trusts:
            sim = simulate(squad, seasons=3, youth_trust=float(t), runs=runs)
            sim = sim[sim.season > 2022].merge(real.rename(columns={"season": "played_season"}),
                                               on=["player_id", "played_season"])   # only seasons he was still there
            both = sim.groupby("player_id")[["share_mean", "real_share"]].mean()
            errors[t] = float((both.iloc[:, 0] - both.iloc[:, 1]).abs().mean())
        best = min(errors, key=errors.get)
        rows.append({"club": club, "league": league, "revealed_trust": best, "error": errors[best],
                     "error_level_only": errors[0], "young_players": len(real_mean)})
        print(f"{club}: revealed trust {best} (error {errors[best]:.3f} vs {errors[0]:.3f} at 0)", flush=True)
    out = pd.DataFrame(rows)
    con = duckdb.connect(str(WAREHOUSE))
    con.register("rt", out)
    con.execute("CREATE OR REPLACE TABLE revealed_trust AS SELECT * FROM rt")
    con.close()
    return out


if __name__ == "__main__":
    # Builds the revealed_trust table the app reads.
    print(reveal().sort_values("revealed_trust", ascending=False).to_string(index=False))
