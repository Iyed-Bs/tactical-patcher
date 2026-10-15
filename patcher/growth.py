"""The growth engine (Tactical Patcher's core): how a player's level changes
over one season.

Learned from real rating changes: FIFA version v to v+1 (FIFA 15-23), with
the minutes he actually played that season taken from Transfermarkt
(FIFA 18 -> 19 reflects the 2017/18 season, and so on).

Inputs: age, current level (overall), EA potential and the gap to it,
position, share of his team's league minutes, league quality, team strength.
Output: quantiles (P10, P50, P90) of the change in overall, by gradient
boosting with a quantile loss, so a season can be simulated with real
uncertainty instead of a fixed growth curve.

Validation: train on versions 15-20, test on 21-22, against the baseline
"average change for his age".
Run:  python -m patcher.growth
"""
from __future__ import annotations

import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from .paths import DATA, WAREHOUSE

MODEL_DIR = DATA / "growth_model"  # one LightGBM text file per quantile + metrics.json
FEATURES = ["age", "overall", "potential", "gap", "pos_gk", "pos_def", "pos_mid", "pos_att",
            "share", "log_lq", "team_strength", "league_level"]
QUANTILES = (0.1, 0.5, 0.9)


def season_table(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
    WITH f AS (
      SELECT l.player_id, f.fifa_version AS v, f.age, f.overall, f.potential, f.player_positions, f.league_level
      FROM link_fifa l JOIN fifa f ON f.player_id = l.fifa_id
    ),
    pair AS (
      SELECT a.*, b.overall AS overall_next
      FROM f a JOIN f b ON b.player_id = a.player_id AND b.v = a.v + 1
    ),
    season AS (   -- minutes share, league quality and team strength in season (2000 + v - 1)
      SELECT a.player_id, a.season,
             sum(a.mins) / nullif(max(tg.n_games) * 90.0, 0) AS share,
             sum(a.mins * lq.lq) / nullif(sum(CASE WHEN lq.lq IS NOT NULL THEN a.mins END), 0) AS lq,
             avg(CASE WHEN a.own_position IS NOT NULL AND a.total_clubs > 1
                      THEN 1 - (a.own_position - 1) / (a.total_clubs - 1.0) END) AS team_strength
      FROM app_ctx a
      LEFT JOIN team_games tg ON tg.club_id = a.club_id AND tg.season = a.season AND tg.competition_id = a.competition_id
      LEFT JOIN league_quality lq ON lq.competition_id = a.competition_id AND lq.season = a.season
      WHERE a.comp_type = 'domestic_league'
      GROUP BY ALL
    )
    SELECT pair.*, s.share, s.lq, s.team_strength
    FROM pair LEFT JOIN season s ON s.player_id = pair.player_id AND s.season = CAST(2000 + pair.v - 1 AS VARCHAR)
    """).df()


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["gap"] = df.potential - df.overall
    first = df.player_positions.fillna("").str.split(",").str[0].str.strip()
    df["pos_gk"] = (first == "GK").astype(int)
    df["pos_def"] = first.isin(["CB", "LB", "RB", "LWB", "RWB"]).astype(int)
    df["pos_mid"] = first.isin(["CDM", "CM", "CAM", "LM", "RM"]).astype(int)
    df["pos_att"] = first.isin(["ST", "CF", "LW", "RW"]).astype(int)
    df["share"] = df.share.fillna(0).clip(0, 1)
    df["log_lq"] = np.log1p(df.lq.fillna(0).astype(float))
    return df


def fit(train: pd.DataFrame) -> dict[float, lgb.LGBMRegressor]:
    models = {}
    for q in QUANTILES:
        models[q] = lgb.LGBMRegressor(objective="quantile", alpha=q, n_estimators=500, learning_rate=0.04,
                                      num_leaves=31, min_child_samples=40, subsample=0.8, subsample_freq=1,
                                      colsample_bytree=0.9, verbose=-1).fit(train[FEATURES], train.delta)
    return models


def build() -> None:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    df = prepare(season_table(con))
    df["delta"] = df.overall_next - df.overall
    print("season pairs:", len(df), "| with Transfermarkt minutes:", int((df.share > 0).sum()))

    train, test = df[df.v <= 20], df[df.v >= 21]
    models = fit(train)
    pred = {q: m.predict(test[FEATURES]) for q, m in models.items()}
    base = train.groupby("age").delta.mean()
    base_pred = test.age.map(base).fillna(train.delta.mean())
    mae_model = np.mean(np.abs(test.delta - pred[0.5]))
    mae_base = np.mean(np.abs(test.delta - base_pred))
    cover = np.mean((test.delta >= pred[0.1]) & (test.delta <= pred[0.9]))
    print(f"test (FIFA 21-22): median change MAE {mae_model:.2f} vs age-average baseline {mae_base:.2f}; "
          f"P10-P90 coverage {cover:.0%}")
    # What minutes do to a 19-year-old (all else at median): the patch's headline.
    probe = test[(test.age == 19)].copy()
    for share in (0.0, 0.25, 0.5, 0.8):
        probe["share"] = share
        print(f"age 19, share {share:.0%}: median change {np.median(models[0.5].predict(probe[FEATURES])):+.2f}")

    final = fit(df)
    MODEL_DIR.mkdir(exist_ok=True)
    for q, m in final.items():
        m.booster_.save_model(str(MODEL_DIR / f"q{int(q * 100)}.txt"))
    (MODEL_DIR / "metrics.json").write_text(json.dumps(
        {"mae": float(mae_model), "mae_baseline": float(mae_base), "coverage": float(cover)}), encoding="utf-8")
    print("saved", MODEL_DIR)


def load() -> dict[float, lgb.Booster]:
    """The saved quantile models (LightGBM's own text format, no pickle)."""
    return {q: lgb.Booster(model_file=str(MODEL_DIR / f"q{int(q * 100)}.txt")) for q in QUANTILES}


if __name__ == "__main__":
    build()
