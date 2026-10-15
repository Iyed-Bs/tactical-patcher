"""Market value model: what a player costs given his level, age and context.

Trained on Transfermarkt's valuation history (one value every few months
since 2012, CC0 dataset), each valuation joined to the player's EA rating
at that date (FIFA 15-23 weekly history, FC 26, FC 27) and his league
minutes that season. Tested on the latest seasons it never saw.

Run:  python -m patcher.market
"""
from __future__ import annotations

import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from . import growth2
from .paths import DATA, TRANSFERMARKT

OUT = DATA / "market"
FEATURES = ["overall", "age", "pos_gk", "pos_def", "pos_mid", "pos_att", "league_level", "minutes_share", "minutes_known",
            "delta_prev", "season"]


def dataset() -> pd.DataFrame:
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    ident = pd.read_parquet(DATA / "identity_v2.parquet")[["ea_id", "tm_id"]].dropna()
    con.register("ident", ident)
    # EA rating at each edition start (one row per player-season).
    ea = con.execute(f"""
        SELECT player_id AS ea_id, fifa_version, any_value(overall) AS overall, any_value(age) AS age,
               any_value(player_positions) AS positions, any_value(league_level) AS league_level
        FROM read_parquet('{growth2.FIFA}') WHERE fifa_update = 1 GROUP BY 1, 2""").df()
    ea["season"] = 2000 + ea.fifa_version - 1
    prev = ea[["ea_id", "fifa_version", "overall"]].assign(fifa_version=ea.fifa_version + 1).rename(columns={"overall": "overall_prev"})
    ea = ea.merge(prev, on=["ea_id", "fifa_version"], how="left")
    # Valuations from September to the following June belong to that season.
    vals = con.execute("""
        SELECT i.ea_id, v.date, v.market_value_in_eur AS value,
               CAST(year(v.date - INTERVAL 8 MONTH) AS INT) AS season
        FROM tm.player_valuations v JOIN ident i ON i.tm_id = v.player_id
        WHERE v.market_value_in_eur > 0""").df()
    df = vals.merge(ea, on=["ea_id", "season"])
    mins = growth2.minutes(con).rename(columns={"player_id": "ea_id"})
    mins["season"] = mins.season + 1                      # last season's minutes are known when this season's value is set
    df = df.merge(mins, on=["ea_id", "season"], how="left")
    first = df.positions.fillna("").str.split(",").str[0].str.strip()
    df["pos_gk"], df["pos_def"] = (first == "GK").astype(int), first.isin(["CB", "LB", "RB", "LWB", "RWB"]).astype(int)
    df["pos_mid"], df["pos_att"] = first.isin(["CDM", "CM", "CAM", "LM", "RM"]).astype(int), first.isin(["ST", "CF", "LW", "RW"]).astype(int)
    df["minutes_known"] = df.minutes_share.notna().astype(int)
    df["delta_prev"] = df.overall - df.overall_prev
    df["league_level"] = pd.to_numeric(df.league_level, errors="coerce")
    df["log_value"] = np.log(df.value)
    return df


def build() -> dict:
    df = dataset()
    tr, te = df[df.season <= 2020], df[df.season >= 2021]
    m = lgb.LGBMRegressor(n_estimators=1200, learning_rate=0.03, num_leaves=63, min_child_samples=40, subsample=0.8,
                          subsample_freq=1, colsample_bytree=0.9, verbose=-1)
    m.fit(tr[FEATURES].astype(float), tr.log_value)
    pred = m.predict(te[FEATURES].astype(float))
    err = np.abs(np.exp(pred) / te.value - 1)
    metrics = {"rows_train": len(tr), "rows_test": len(te),
               "r2_log": float(1 - np.mean((pred - te.log_value) ** 2) / np.var(te.log_value)),
               "median_abs_pct_error": float(np.median(err)), "within_50pct": float(np.mean(err <= 0.5))}
    # Refit on everything for use.
    m.fit(df[FEATURES].astype(float), df.log_value)
    OUT.mkdir(parents=True, exist_ok=True)
    m.booster_.save_model(str(OUT / "value.txt"))
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


def load() -> lgb.Booster:
    return lgb.Booster(model_file=str(OUT / "value.txt"))


def value(model: lgb.Booster, overall, age, line: str, league_level: int, minutes_share, delta_prev, season: int = 2026) -> np.ndarray:
    """Market value in euros for arrays of states (season trend included, extrapolated to 2026+)."""
    overall = np.atleast_1d(np.asarray(overall, dtype=float))
    n = len(overall)
    X = pd.DataFrame({"overall": overall, "age": np.broadcast_to(age, n).astype(float),
                      "pos_gk": float(line == "GK"), "pos_def": float(line == "DEF"), "pos_mid": float(line == "MID"),
                      "pos_att": float(line == "ATT"), "league_level": float(league_level),
                      "minutes_share": np.broadcast_to(minutes_share, n).astype(float), "minutes_known": 1.0,
                      "delta_prev": np.broadcast_to(delta_prev, n).astype(float), "season": float(min(season, 2025))})
    return np.exp(model.predict(X[FEATURES]))


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
