"""Growth engine v2: how a player's ratings move over one season.

Trained on EA's weekly ratings history (FIFA 15-23, sofifa ids = EA ids),
one pair per player per season: ratings at the start of edition v against
the start of v+1. Fixes v1's flaws:
  * missing minutes stay missing (v1 stored them as 0 and learned that
    unknown youngsters stall: Doku 75 -> 74 in three seasons; he is 84);
  * trajectory: last season's change and the in-season movement of the
    weekly updates (EA re-rates form during the season);
  * context: EA's depth-chart marker (starter / sub / reserve), team level
    and tactics, league level, real minutes share where Transfermarkt has it;
  * profile: face stats, mental attributes, work rates, body, market signals.
Outputs P10/P50/P90 of the overall change, and P50 of each face stat change.
Tested on the most recent season it never saw (FIFA 22 -> 23).

Run:  python -m patcher.growth2
"""
from __future__ import annotations

import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from .paths import DATA, TRANSFERMARKT, WAREHOUSE

FIFA = (DATA / "fifa_all_updates.parquet").as_posix()
TEAMS = (DATA / "raw" / "fifa" / "male_teams.csv").as_posix()
MODEL_DIR = DATA / "growth2"
QUANTILES = (0.1, 0.5, 0.9)
FACE = ["pace", "shooting", "passing", "dribbling", "defending", "physic"]
MENTAL = ["movement_reactions", "mentality_composure", "mentality_vision", "mentality_positioning", "mentality_interceptions"]
FEATURES = (["age", "overall", "potential", "gap", "pos_gk", "pos_def", "pos_mid", "pos_att", "starter", "sub", "reserve",
             "international_reputation", "skill_moves", "weak_foot", "wr_att", "wr_def", "height_cm", "weight_kg",
             "log_value", "log_wage", "contract_years", "league_level", "team_overall", "team_prestige", "team_def_pressure",
             "rel_to_team", "delta_prev", "in_season_prev", "minutes_share", "minutes_known"]
            + FACE + MENTAL)
# The core set: only what a scout has on any current player (EA's live ratings
# publish no potential, value, wage, work rate or contract). The Patcher's own
# potential comes from this model and the comparables, not from EA's guess.
FEATURES_CORE = (["age", "overall", "pos_gk", "pos_def", "pos_mid", "pos_att", "starter", "skill_moves", "weak_foot",
                  "height_cm", "weight_kg", "league_level", "team_overall", "rel_to_team", "delta_prev", "minutes_share",
                  "minutes_known"] + FACE + MENTAL)


def pairs(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per player per season: start-of-season state, last season's movement, next season's change."""
    face = ", ".join(f"f.{c}" for c in FACE + MENTAL)
    return con.execute(f"""
    WITH f AS (SELECT * FROM read_parquet('{FIFA}')),
    bounds AS (SELECT fifa_version, min(fifa_update) AS u0, max(fifa_update) AS u1 FROM f GROUP BY 1),
    start AS (SELECT f.* FROM f JOIN bounds b ON f.fifa_version = b.fifa_version AND f.fifa_update = b.u0),
    finish AS (SELECT f.player_id, f.fifa_version, f.overall AS overall_end FROM f JOIN bounds b ON f.fifa_version = b.fifa_version AND f.fifa_update = b.u1),
    teams AS (
      SELECT team_id, fifa_version, any_value(overall) AS team_overall, any_value(international_prestige) AS team_prestige,
             any_value(def_defence_pressure) AS team_def_pressure
      FROM read_csv_auto('{TEAMS}', sample_size=100000) GROUP BY 1, 2)
    SELECT s.player_id, s.fifa_version, s.age, s.overall, s.potential, s.potential - s.overall AS gap,
           s.player_positions, s.club_position, s.international_reputation, s.skill_moves, s.weak_foot, s.work_rate,
           s.height_cm, s.weight_kg, s.value_eur, s.wage_eur, s.club_contract_valid_until_year, s.league_level, s.league_name,
           s.club_team_id, s.club_name, {face.replace('f.', 's.')},
           t.team_overall, t.team_prestige, t.team_def_pressure,
           e.overall_end - s.overall AS in_season,
           n.overall AS overall_next, {', '.join(f'n.{c} AS {c}_next' for c in FACE)},
           p.overall AS overall_prev, pe.overall_end - p.overall AS in_season_prev
    FROM start s
    LEFT JOIN teams t ON t.team_id = s.club_team_id AND t.fifa_version = s.fifa_version
    LEFT JOIN finish e ON e.player_id = s.player_id AND e.fifa_version = s.fifa_version
    LEFT JOIN start n ON n.player_id = s.player_id AND n.fifa_version = s.fifa_version + 1
    LEFT JOIN start p ON p.player_id = s.player_id AND p.fifa_version = s.fifa_version - 1
    LEFT JOIN finish pe ON pe.player_id = s.player_id AND pe.fifa_version = s.fifa_version - 1
    """).df()


def minutes(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Real league minutes share per EA id and season (Transfermarkt), where the league is covered."""
    ident = pd.read_parquet(DATA / "identity_v2.parquet")[["ea_id", "tm_id"]].dropna()
    con.register("ident", ident)
    con.execute(f"ATTACH IF NOT EXISTS '{WAREHOUSE.as_posix()}' AS wh (READ_ONLY)")
    return con.execute("""
    SELECT i.ea_id AS player_id, CAST(a.season AS INT) AS season,
           sum(a.mins) / nullif(max(tg.n_games) * 90.0, 0) AS minutes_share
    FROM wh.app_ctx a JOIN ident i ON i.tm_id = a.player_id
    LEFT JOIN wh.team_games tg ON tg.club_id = a.club_id AND tg.season = a.season AND tg.competition_id = a.competition_id
    WHERE a.comp_type = 'domestic_league'
    GROUP BY ALL
    """).df()


def prepare(df: pd.DataFrame, mins: pd.DataFrame | None = None) -> pd.DataFrame:
    df = df.copy()
    first = df.player_positions.fillna("").str.split(",").str[0].str.strip()
    df["pos_gk"] = (first == "GK").astype(int)
    df["pos_def"] = first.isin(["CB", "LB", "RB", "LWB", "RWB"]).astype(int)
    df["pos_mid"] = first.isin(["CDM", "CM", "CAM", "LM", "RM"]).astype(int)
    df["pos_att"] = first.isin(["ST", "CF", "LW", "RW"]).astype(int)
    cp = df.club_position.fillna("")
    df["sub"], df["reserve"] = (cp == "SUB").astype(int), (cp == "RES").astype(int)
    df["starter"] = ((cp != "") & (cp != "SUB") & (cp != "RES")).astype(int)
    wr = df.work_rate.fillna("Medium/Medium").str.split("/")
    lvl = {"Low": 0, "Medium": 1, "High": 2}
    df["wr_att"] = wr.str[0].map(lvl)
    df["wr_def"] = wr.str[1].map(lvl)
    df["log_value"], df["log_wage"] = np.log1p(df.value_eur.fillna(0)), np.log1p(df.wage_eur.fillna(0))
    df["contract_years"] = df.club_contract_valid_until_year - (2000 + df.fifa_version - 1)
    for c in ("team_overall", "team_prestige", "team_def_pressure", "league_level", "height_cm", "weight_kg"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["rel_to_team"] = df.overall - df.team_overall
    df["delta_prev"] = df.overall - df.overall_prev
    df["in_season_prev"] = df.in_season_prev
    df["target"] = df.overall_next - df.overall
    for c in FACE:
        df[f"target_{c}"] = df[f"{c}_next"] - df[c]
    if mins is not None:
        # Edition v starts in season 2000+v-1; its minutes drive the change to v+1.
        df["season"] = 2000 + df.fifa_version - 1
        df = df.merge(mins, on=["player_id", "season"], how="left")
    df["minutes_known"] = df.minutes_share.notna().astype(int) if "minutes_share" in df else 0
    if "minutes_share" not in df:
        df["minutes_share"] = np.nan
    return df


def fit(train: pd.DataFrame, features: list[str], prefix: str = "") -> dict:
    params = dict(n_estimators=900, learning_rate=0.03, num_leaves=63, min_child_samples=60, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.8, verbose=-1)
    models = {f"{prefix}q{int(q * 100)}": lgb.LGBMRegressor(objective="quantile", alpha=q, **params).fit(train[features], train.target)
              for q in QUANTILES}
    for c in FACE:
        ok = train[f"target_{c}"].notna() & (train.pos_gk == 0)
        models[f"{prefix}face_{c}"] = lgb.LGBMRegressor(objective="quantile", alpha=0.5, **params).fit(
            train.loc[ok, features], train.loc[ok, f"target_{c}"])
    return models


def evaluate(models: dict, test: pd.DataFrame, features: list[str], prefix: str = "") -> dict:
    p50 = models[f"{prefix}q50"].predict(test[features])
    lo, hi = models[f"{prefix}q10"].predict(test[features]), models[f"{prefix}q90"].predict(test[features])
    base = test.groupby("age").target.transform("median")
    young = (test.age <= 21).values
    return {
        "pairs_test": int(len(test)),
        "mae": float(np.mean(np.abs(test.target - p50))), "mae_age_baseline": float(np.mean(np.abs(test.target - base))),
        "mae_young": float(np.mean(np.abs(test.target[young] - p50[young]))),
        "mae_young_baseline": float(np.mean(np.abs(test.target[young] - base[young]))),
        "coverage_p10_p90": float(np.mean((test.target >= lo) & (test.target <= hi))),
        "big_jumps_caught": float(np.mean(hi[(test.target >= 5).values] >= 5)),   # real +5s inside the band
    }


def build() -> dict:
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    df = prepare(pairs(con), minutes(con))
    df = df[df.target.notna()]
    train, test = df[df.fifa_version <= 21], df[df.fifa_version == 22]
    full = fit(train, FEATURES)
    core = fit(train, FEATURES_CORE, prefix="core_")
    metrics = {"pairs_train": int(len(train)), "minutes_known_share": float(df.minutes_known.mean()),
               "full": evaluate(full, test, FEATURES), "core": evaluate(core, test, FEATURES_CORE, "core_")}
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, m in {**full, **core}.items():
        m.booster_.save_model(str(MODEL_DIR / f"{name}.txt"))
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
    imp = pd.Series(core["core_q50"].booster_.feature_importance("gain"), index=FEATURES_CORE).sort_values(ascending=False)
    (MODEL_DIR / "importance_core.json").write_text(imp.round(1).to_json(indent=2))
    return metrics


def load() -> dict:
    return {p.stem: lgb.Booster(model_file=str(p)) for p in MODEL_DIR.glob("*.txt")}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
    print(pd.read_json(MODEL_DIR / "importance_core.json", typ="series").head(15))
