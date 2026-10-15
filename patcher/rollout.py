"""The season simulator shared by the app and the backtest.

Each simulated season:
  1. minutes: what he plays follows what he is: learned from real careers
     (next season's league minutes share given his level against his team,
     his age, last season's share), unless the scenario fixes his role;
  2. club: players who outgrow their club move up (and the reverse):
     the change in his team's level is learned from EA's club ratings,
     sampled, so some futures take him to a bigger club and others don't;
  3. growth: the change in his rating is drawn from the growth engine's
     quantiles with heavier tails than a straight line past P10/P90
     (rating changes have breakouts and collapses);
  4. profile: every attribute moves with the rating at the rate the data
     shows (composure 0.91 per point, pace 0.63...);
  5. calibration: the spread is widened per talent tier by the factor that
     makes the four-season band hold 80 % in the time-machine backtest.

Build:  python -m patcher.rollout     (fits the minutes and club-move models)
"""
from __future__ import annotations

import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from . import growth2
from .paths import DATA, TRANSFERMARKT

OUT = DATA / "rollout"
MIN_FEATURES = ["age", "overall", "rel_to_team", "team_overall", "minutes_prev", "minutes_prev_known", "starter", "pos_gk", "league_level"]
MOVE_FEATURES = ["age", "overall", "rel_to_team", "team_overall", "minutes_prev", "minutes_prev_known", "delta_prev"]
SLOPES = {"pace": 0.63, "shooting": 0.61, "passing": 0.76, "dribbling": 0.78, "defending": 0.66, "physic": 0.61,
          "movement_reactions": 0.84, "mentality_composure": 0.91, "mentality_vision": 0.67, "mentality_positioning": 0.65,
          "mentality_interceptions": 0.66}
TAIL = 2.2          # beyond P10/P90 the draw follows an exponential tail this many times the outer quantile gap (heavier than linear)


def training_table(upto: int | None = None) -> pd.DataFrame:
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    df = growth2.prepare(growth2.pairs(con), growth2.minutes(con))
    nxt = df[["player_id", "fifa_version", "minutes_share", "team_overall"]].assign(fifa_version=df.fifa_version - 1)
    nxt = nxt.rename(columns={"minutes_share": "minutes_next", "team_overall": "team_overall_next"})
    prev = df[["player_id", "fifa_version", "minutes_share"]].assign(fifa_version=df.fifa_version + 1).rename(columns={"minutes_share": "minutes_prev"})
    df = df.drop(columns=[c for c in ("minutes_prev",) if c in df]).merge(prev, on=["player_id", "fifa_version"], how="left")
    df = df.merge(nxt, on=["player_id", "fifa_version"], how="left")
    df["minutes_prev_known"] = df.minutes_prev.notna().astype(int)
    if upto is not None:
        df = df[df.fifa_version <= upto]
    return df


def fit(df: pd.DataFrame) -> dict:
    params = dict(n_estimators=500, learning_rate=0.04, num_leaves=63, min_child_samples=80, verbose=-1)
    m = df[df.minutes_next.notna()]
    minutes = lgb.LGBMRegressor(objective="regression", **params).fit(m[MIN_FEATURES].astype(float), m.minutes_next.clip(0, 1))
    c = df[df.team_overall_next.notna() & df.team_overall.notna()]
    dteam = (c.team_overall_next - c.team_overall).astype(float)
    move = {q: lgb.LGBMRegressor(objective="quantile", alpha=q / 100, **params).fit(c[MOVE_FEATURES].astype(float), dteam) for q in (10, 50, 90)}
    return {"minutes": minutes, **{f"move_q{q}": mm for q, mm in move.items()}}


def draw(lo: np.ndarray, mid: np.ndarray, hi: np.ndarray, u: np.ndarray, scale: np.ndarray | float = 1.0) -> np.ndarray:
    """Sample a change from three quantiles: linear between P10 and P90, exponential tails beyond, spread scaled."""
    left, right = np.maximum(mid - lo, 0.2), np.maximum(hi - mid, 0.2)
    inner = np.where(u < 0.5, (u - 0.5) / 0.4 * left, (u - 0.5) / 0.4 * right)
    lo_tail = -left + left / TAIL * np.log(np.clip(u / 0.1, 1e-6, 1))           # u < 0.1: log < 0, further down
    hi_tail = right + right / TAIL * -np.log(np.clip((1 - u) / 0.1, 1e-6, 1))    # u > 0.9
    dev = np.where(u < 0.1, lo_tail, np.where(u > 0.9, hi_tail, inner))
    return mid + dev * scale


def simulate(growth: dict, side: dict, state: pd.DataFrame, seasons: int, runs: int, rng: np.random.Generator,
             fixed_minutes: list | None = None, scale: np.ndarray | float = 1.0, prefix: str = "core_",
             team_moves: bool | None = None) -> dict:
    """state: one row per player with growth2.FEATURES_CORE (+ minutes_prev). Returns arrays (players, runs, seasons+1)."""
    n = len(state)
    X = pd.concat([state] * runs, ignore_index=True)
    X["minutes_prev"] = X.get("minutes_prev", np.nan)
    X["minutes_prev_known"] = X.minutes_prev.notna().astype(float)
    sc = np.tile(np.broadcast_to(scale, n), runs) if np.ndim(scale) else scale
    # Players whose minutes are unknown at the start (leagues Transfermarkt doesn't cover) stay unknown:
    # the growth engine learned how such careers move without minutes, and inventing them biases it.
    known0 = X.minutes_prev.notna().to_numpy() | (X.get("minutes_known", 0) == 1).to_numpy()
    level = np.zeros((runs * n, seasons + 1)); level[:, 0] = X.overall
    minutes = np.zeros((runs * n, seasons)); team = np.zeros((runs * n, seasons + 1)); team[:, 0] = X.team_overall
    for s in range(seasons):
        if fixed_minutes is not None and s < len(fixed_minutes) and fixed_minutes[s] is not None:
            X["minutes_share"] = np.tile(np.broadcast_to(fixed_minutes[s], n), runs)
            X["minutes_known"] = 1.0
        else:
            m = side["minutes"].predict(X[MIN_FEATURES].astype(float))
            X["minutes_share"] = np.where(known0, np.clip(m + rng.normal(0, 0.12, len(X)), 0, 0.95), np.nan)
            X["minutes_known"] = known0.astype(float)
        X["starter"] = (X.minutes_share.fillna(0) >= 0.55).astype(float)
        F = X[growth2.FEATURES_CORE].astype(float)
        lo, mid, hi = (growth[f"{prefix}q{q}"].predict(F) for q in (10, 50, 90))
        delta = draw(lo, mid, hi, rng.uniform(0, 1, len(X)), sc)
        minutes[:, s] = X.minutes_share
        X["minutes_prev"], X["minutes_prev_known"] = X.minutes_share, X.minutes_known
        X["overall"] = np.clip(X.overall + delta, 40, 99)
        for c, b in SLOPES.items():
            if c in X:
                X[c] = np.clip(X[c] + b * delta, 1, 99)
        X["delta_prev"], X["age"] = delta, X.age + 1
        # Club: his team's level moves (a transfer up, a club in decline...) as careers like his did.
        M = X[MOVE_FEATURES].astype(float).assign(rel_to_team=X.overall - X.team_overall)
        mlo, mmid, mhi = (side[f"move_q{q}"].predict(M) for q in (10, 50, 90))
        moves = team_moves if team_moves is not None else (fixed_minutes is None or s >= len(fixed_minutes) or fixed_minutes[s] is None)
        if moves:
            X["team_overall"] = np.clip(X.team_overall + draw(mlo, mmid, mhi, rng.uniform(0, 1, len(X))), 50, 92)
        X["rel_to_team"] = X.overall - X.team_overall
        level[:, s + 1] = X.overall
        team[:, s + 1] = X.team_overall
    shape = lambda a: a.reshape(runs, n, -1).transpose(1, 0, 2)
    return {"level": shape(level), "minutes": shape(minutes), "team": shape(team)}


def save(side: dict, path=OUT) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name, m in side.items():
        m.booster_.save_model(str(path / f"{name}.txt"))


class _Booster:
    """LightGBM booster with the sklearn-style predict the simulator uses."""

    def __init__(self, path):
        self.b = lgb.Booster(model_file=str(path))

    def predict(self, X):
        return self.b.predict(X)


def load(path=OUT) -> dict:
    return {p.stem: _Booster(p) for p in path.glob("*.txt")}


def calibration(path=OUT) -> dict:
    f = path / "calibration.json"
    return json.loads(f.read_text()) if f.exists() else {}


def tier_scale(overall: np.ndarray, cal: dict) -> np.ndarray:
    """Spread multiplier per talent tier from the backtest calibration (1.0 if not calibrated)."""
    edges = [(0, 64, "under 65"), (65, 71, "65-71"), (72, 77, "72-77"), (78, 99, "78+")]
    out = np.ones(len(np.atleast_1d(overall)))
    for lo, hi, name in edges:
        k = (np.atleast_1d(overall) >= lo) & (np.atleast_1d(overall) <= hi)
        out[k] = cal.get("scale", {}).get(name, 1.0)
    return out


if __name__ == "__main__":
    df = training_table()
    side = fit(df)
    save(side)
    m = df[df.minutes_next.notna()]
    print(f"minutes model on {len(m):,} player-seasons; club-move model on {int(df.team_overall_next.notna().sum()):,}")
