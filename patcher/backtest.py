"""Time machine: would the engine have seen it coming?

Train the growth engine only on what was known in summer 2018 (season pairs
FIFA 15 -> 16, 16 -> 17, 17 -> 18), start from every young player's FIFA 19
card (2018/19), roll four seasons forward exactly as the app does, and
compare with where they really were in FIFA 23 (2022/23). Two variants:
  * known minutes: the minutes each player really got (the engine's ceiling);
  * club role only: minutes from his club's depth chart, as the app does today.
Reports calibration (does the 10-90 % band hold 80 %?) by talent tier, and
case files for named players. Nothing here can see the future it predicts.

Run:  python -m patcher.backtest
"""
from __future__ import annotations

import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from . import growth2
from .paths import DATA, TRANSFERMARKT

OUT = DATA / "backtest"
START, SEASONS = 19, 4                     # FIFA 19 card -> FIFA 23
CASES = ["P. Foden", "J. Sancho", "Vinícius Jr.", "João Félix", "M. de Ligt", "K. Havertz", "M. Mount", "E. Haaland",
         "A. Davies", "S. Tonali", "D. Kulusevski", "M. Ødegaard", "Rodrygo", "A. Tchouaméni", "J. Koundé", "Rafael Leão",
         "M. Kean", "R. Sanches", "J. Kluivert", "Brahim", "P. Cutrone", "C. Pulisic", "M. Greenwood", "E. Camavinga",
         "F. Chiesa", "D. Upamecano", "A. Isak", "T. Hernández", "Pedro Neto", "Rúben Dias"]


def fit(train: pd.DataFrame) -> dict:
    params = dict(n_estimators=700, learning_rate=0.03, num_leaves=63, min_child_samples=60, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.8, verbose=-1)
    X, y = train[growth2.FEATURES_CORE].astype(float), train.target.astype(float)
    return {q: lgb.LGBMRegressor(objective="quantile", alpha=q / 100, **params).fit(X, y) for q in (10, 50, 90)}


def attribute_slopes(fifa_path: str, upto: int) -> dict:
    """How much each attribute moves per point of overall change, season to season (data known by `upto`)."""
    cols = growth2.FACE + growth2.MENTAL
    sel = ", ".join(f"any_value({c}) AS {c}" for c in cols)
    d = duckdb.sql(f"""SELECT player_id, fifa_version, any_value(overall) AS overall, {sel}
                       FROM read_parquet('{fifa_path}') WHERE fifa_update = 1 AND fifa_version <= {upto} GROUP BY 1, 2""").df()
    n = d.merge(d.assign(fifa_version=d.fifa_version - 1), on=["player_id", "fifa_version"], suffixes=("", "_n"))
    dov = (n.overall_n - n.overall).astype(float)
    out = {}
    for c in cols:
        da = (n[f"{c}_n"] - n[c]).astype(float)
        ok = da.notna() & dov.notna()
        out[c] = float(np.cov(da[ok], dov[ok])[0, 1] / np.var(dov[ok]))
    return out


def roll(models: dict, start: pd.DataFrame, minutes_by_step: list[np.ndarray], runs: int = 200, seed: int = 5,
         slopes: dict | None = None) -> np.ndarray:
    """Monte Carlo of `SEASONS` steps for many players at once: returns (players, runs) final ratings."""
    rng = np.random.default_rng(seed)
    n = len(start)
    X = pd.concat([start[growth2.FEATURES_CORE].astype(float)] * runs, ignore_index=True)
    for s in range(SEASONS):
        X["minutes_share"] = np.tile(minutes_by_step[s], runs)
        X["minutes_known"] = (~np.isnan(X.minutes_share)).astype(float)
        lo, mid, hi = (models[q].predict(X[growth2.FEATURES_CORE]) for q in (10, 50, 90))
        u = rng.uniform(0, 1, len(X))
        delta = np.where(u < 0.5, mid + (u - 0.5) / 0.4 * (mid - lo), mid + (u - 0.5) / 0.4 * (hi - mid))
        X["overall"] = np.clip(X.overall + delta, 40, 99)
        for c, b in (slopes or {}).items():            # the profile grows with the player, not just the headline number
            X[c] = np.clip(X[c] + b * delta, 1, 99)
        X["delta_prev"], X["age"] = delta, X.age + 1
        X["rel_to_team"] = X.overall - X.team_overall
    return X.overall.to_numpy().reshape(runs, n).T


def run() -> dict:
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    mins = growth2.minutes(con)
    df = growth2.prepare(growth2.pairs(con), mins)
    train = df[(df.fifa_version < START) & df.target.notna()]
    models = fit(train)
    # Every young player on a FIFA 19 card who still has a FIFA 23 card.
    start = df[(df.fifa_version == START) & (df.age <= 21)].copy()
    final = df[df.fifa_version == START + SEASONS][["player_id", "overall", "club_name"]].rename(columns={"overall": "real", "club_name": "club_real"})
    start = start.merge(final, on="player_id").reset_index(drop=True)
    names = duckdb.sql(f"SELECT player_id, any_value(short_name) AS short_name FROM read_parquet('{growth2.FIFA}') "
                       f"WHERE fifa_version = {START} GROUP BY 1").df()
    start = start.merge(names, on="player_id", how="left")
    # Known minutes: what he really played each season (NaN where the league isn't covered).
    known = []
    for s in range(SEASONS):
        season = 2000 + START - 1 + s
        m = start[["player_id"]].merge(mins[mins.season == season].groupby("player_id").minutes_share.sum().reset_index(), on="player_id", how="left")
        known.append(m.minutes_share.to_numpy(dtype=float))
    # Club role only: the first season's role held constant (starter 0.72, sub 0.25, reserve 0.08).
    role = np.where(start.starter == 1, 0.72, np.where(start["sub"] == 1, 0.25, 0.08)).astype(float)
    slopes = attribute_slopes(growth2.FIFA, START - 1)
    out = {"attribute_slopes": slopes}
    for label, mb, sl in [("known minutes, frozen attributes (old)", known, None), ("known minutes", known, slopes),
                          ("club role only", [role] * SEASONS, slopes)]:
        sims = roll(models, start, mb, slopes=sl)
        p10, p50, p90 = (np.percentile(sims, q, axis=1) for q in (10, 50, 90))
        start[f"p10_{label}"], start[f"p50_{label}"], start[f"p90_{label}"] = p10, p50, p90
        tiers = pd.cut(start.overall, [0, 64, 71, 77, 99], labels=["under 65", "65-71", "72-77", "78+"])
        cal = []
        for t, g in start.groupby(tiers, observed=True):
            r, lo, mid, hi = g.real.values, g[f"p10_{label}"].values, g[f"p50_{label}"].values, g[f"p90_{label}"].values
            cal.append({"tier": str(t), "n": len(g), "inside_band": float(np.mean((r >= lo) & (r <= hi))),
                        "above_p90": float(np.mean(r > hi)), "below_p10": float(np.mean(r < lo)),
                        "median_error": float(np.median(r - mid)), "mae": float(np.mean(np.abs(r - mid)))})
        out[label] = cal
    OUT.mkdir(parents=True, exist_ok=True)
    start.to_parquet(OUT / "fifa19_to_23.parquet")
    (OUT / "calibration.json").write_text(json.dumps(out, indent=2))
    return {"players": len(start), "train_pairs": len(train), "calibration": out}


def machine(start_v: int, runs: int = 150, scale=1.0, seed: int = 9) -> pd.DataFrame:
    """Train everything on data known before `start_v`, simulate 4 seasons from its cards with dynamic minutes and club moves."""
    from . import rollout
    df = rollout.training_table()
    known = df[df.fifa_version < start_v]
    growth = {f"core_q{q}": m for q, m in fit(known[known.target.notna()]).items()}
    side = rollout.fit(known)
    start = df[(df.fifa_version == start_v) & (df.age <= 21)].copy()
    final = df[df.fifa_version == start_v + SEASONS][["player_id", "overall"]].rename(columns={"overall": "real"})
    start = start.merge(final, on="player_id").reset_index(drop=True)
    names = duckdb.sql(f"SELECT player_id, any_value(short_name) AS short_name, any_value(club_name) AS club FROM read_parquet('{growth2.FIFA}') "
                       f"WHERE fifa_version = {start_v} GROUP BY 1").df()
    start = start.merge(names, on="player_id", how="left")
    state = start[list(dict.fromkeys(growth2.FEATURES_CORE + ["minutes_prev"]))].astype(float)
    sc = rollout.tier_scale(start.overall.to_numpy(), {"scale": scale}) if isinstance(scale, dict) else scale
    sims = rollout.simulate(growth, side, state, SEASONS, runs, np.random.default_rng(seed), scale=sc)
    final_lv = sims["level"][:, :, -1]
    for q in (10, 50, 90):
        start[f"p{q}"] = np.percentile(final_lv, q, axis=1)
    start["tier"] = pd.cut(start.overall, [0, 64, 71, 77, 99], labels=["under 65", "65-71", "72-77", "78+"])
    start["sims"] = list(final_lv)
    return start


def calibrate_and_test() -> dict:
    """Choose the spread per tier on machine A (FIFA 18 -> 22), test it on machine B (FIFA 19 -> 23)."""
    from . import rollout
    a = machine(18)
    scale = {}
    for t, g in a.groupby("tier", observed=True):
        sims, real = np.stack(g.sims.values), g.real.values
        mid = np.median(sims, axis=1, keepdims=True)
        best = 1.0
        for s in np.arange(1.0, 3.01, 0.05):                     # widen around the median until the band holds 80 %
            wide = mid + (sims - mid) * s
            lo, hi = np.percentile(wide, 10, axis=1), np.percentile(wide, 90, axis=1)
            if np.mean((real >= lo) & (real <= hi)) >= 0.80:
                best = float(s); break
            best = float(s)
        scale[str(t)] = round(best, 2)
    b_raw = machine(19)
    b = machine(19, scale=scale)
    def report(df):
        rows = []
        for t, g in df.groupby("tier", observed=True):
            r = g.real.values
            rows.append({"tier": str(t), "n": len(g), "inside_band": float(np.mean((r >= g.p10) & (r <= g.p90))),
                         "above_p90": float(np.mean(r > g.p90)), "below_p10": float(np.mean(r < g.p10)),
                         "median_error": float(np.median(r - g.p50)), "mae": float(np.mean(np.abs(r - g.p50)))})
        return rows
    res = {"scale": scale, "test_before_calibration": report(b_raw), "test_after_calibration": report(b)}
    OUT.mkdir(parents=True, exist_ok=True)
    (rollout.OUT).mkdir(parents=True, exist_ok=True)
    (rollout.OUT / "calibration.json").write_text(json.dumps({"scale": scale}, indent=2))
    (OUT / "calibration_v2.json").write_text(json.dumps(res, indent=2))
    b.drop(columns="sims").to_parquet(OUT / "fifa19_to_23_v2.parquet")
    return res


if __name__ == "__main__" and __import__("sys").argv[1:] == ["v2"]:
    r = calibrate_and_test()
    print("spread per tier (chosen on FIFA 18 -> 22):", r["scale"])
    for k in ("test_before_calibration", "test_after_calibration"):
        print()
        print(f"FIFA 19 -> 23, dynamic minutes and club moves, {k.replace('_', ' ')}:")
        print(pd.DataFrame(r[k]).round(3).to_string(index=False))
elif __name__ == "__main__":
    r = run()
    print(f"{r['players']:,} young players (<= 21 on FIFA 19) | trained on {r['train_pairs']:,} pairs known in 2018")
    for label, rows in r["calibration"].items():
        if label == "attribute_slopes":
            continue
        print(f"\n{label}:")
        print(pd.DataFrame(rows).round(3).to_string(index=False))
