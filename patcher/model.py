"""The promise engine.

For each age (18-21) it learns, from players born 1995-1999, how a state at
that age turned into a career at 23-26.5, then scores today's under-21s:

- p_established: chance of becoming a regular in a top flight
  (4,500+ league minutes at 23-26.5), penalised logistic regression;
- p_big5: same, in a big-five league;
- p_objective: the same model without the market's valuation, to compare
  what the data sees with what the market prices ("undervalued" gap);
- comparables: the 25 most similar historical players at the same age
  (PECOTA style), whose careers give the range (P10 / P50 / P90 of league
  minutes and peak value) and named examples;
- drivers: each variable's contribution to his log-odds.

Validation is leave-one-cohort-out; results go to the model_report table.
Run:  python -m patcher.model
"""
from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from .paths import WAREHOUSE

OBJECTIVE = [
    "debut_age", "log_min_total", "log_min_last_year", "log_min_prev_year", "log_min_big5", "log_min_europe",
    "max_share", "mean_share", "log_league_quality", "log_quality_minutes", "team_strength", "ga_per90",
    "mgr_trust", "n_clubs", "height_in_cm", "q1_born", "pos_defender", "pos_midfield", "pos_attack", "pos_goalkeeper",
    "cutoff_age",
]
CROWD = ["log_mv_now", "mv_growth_2y"]
SIMILARITY = ["log_quality_minutes", "log_min_last_year", "max_share", "log_league_quality", "team_strength",
              "log_mv_now", "height_in_cm", "ga_per90"]
LABELS = {  # plain words for the drivers shown in the app
    "debut_age": "age at debut", "log_min_total": "senior minutes", "log_min_last_year": "minutes this past year",
    "log_min_prev_year": "minutes the year before", "log_min_big5": "big-five minutes", "log_min_europe": "European minutes",
    "max_share": "best share of team minutes", "mean_share": "usual share of team minutes",
    "log_league_quality": "league level", "log_quality_minutes": "minutes at level", "team_strength": "team strength",
    "ga_per90": "goals + assists per 90", "mgr_trust": "managers' trust in youth", "n_clubs": "clubs so far",
    "height_in_cm": "height", "q1_born": "born in Q1", "pos_defender": "defender", "pos_midfield": "midfielder",
    "pos_attack": "forward", "pos_goalkeeper": "goalkeeper", "cutoff_age": "age", "log_mv_now": "market value", "mv_growth_2y": "value trend (2 yrs)",
}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["min_total", "min_last_year", "min_prev_year", "min_big5", "min_europe", "league_quality",
                "quality_minutes", "mv_now"]:
        df[f"log_{col}"] = np.log1p(df[col].fillna(0).astype(float))
    now, before = df.mv_now.astype(float), df.mv_2y_before.astype(float)
    ok = now.notna() & before.notna()
    growth = np.log(now.clip(lower=1).fillna(1)) - np.log(before.clip(lower=1).fillna(1))
    df["mv_growth_2y"] = np.where(ok, growth.clip(-3, 4), np.nan)  # capped: a jump from 50k matters, not its exact size
    df["q1_born"] = (pd.to_datetime(df.dob).dt.month <= 3).astype(int)
    for pos in ["Defender", "Midfield", "Attack", "Goalkeeper"]:
        df[f"pos_{'midfield' if pos == 'Midfield' else pos.lower()}"] = (df.position == pos).astype(int)
    df["cohort"] = pd.to_datetime(df.dob).dt.year
    df["y_top"] = (df.out_min_league.fillna(0) >= 4500).astype(int)
    df["y_big5"] = (df.out_min_big5.fillna(0) >= 4500).astype(int)
    return df


def logit() -> Pipeline:
    return make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                         LogisticRegression(C=0.3, max_iter=4000))


def validate(hist: pd.DataFrame, cols: list[str], target: str) -> dict[str, float]:  # noqa: D103
    p = np.zeros(len(hist))
    for c in sorted(hist.cohort.unique()):
        tr, te = hist.cohort != c, hist.cohort == c
        p[te.values] = logit().fit(hist.loc[tr, cols], hist.loc[tr, target]).predict_proba(hist.loc[te, cols])[:, 1]
    y = hist[target].values
    return {"auc": float(roc_auc_score(y, p)), "brier": float(brier_score_loss(y, p)), "base": float(y.mean()), "n": float(len(y))}


def range_coverage(hist: pd.DataFrame, k: int = 25) -> float:
    """Share of held-out players whose real league minutes fell inside their comparables' P10-P90."""
    hits = []
    for c in sorted(hist.cohort.unique()):
        for pos in hist.position.dropna().unique():
            tr = hist[(hist.cohort != c) & (hist.position == pos)]
            te = hist[(hist.cohort == c) & (hist.position == pos)]
            if te.empty or len(tr) < k:
                continue
            sc = make_pipeline(SimpleImputer(strategy="median"), StandardScaler()).fit(tr[SIMILARITY])
            nn = NearestNeighbors(n_neighbors=k).fit(sc.transform(tr[SIMILARITY]))
            _, idx = nn.kneighbors(sc.transform(te[SIMILARITY]))
            out = tr.out_min_league.fillna(0).values[idx]
            lo, hi = np.percentile(out, 10, axis=1), np.percentile(out, 90, axis=1)
            y = te.out_min_league.fillna(0).values
            hits += list((y >= lo) & (y <= hi))
    return float(np.mean(hits))


def pooled_validation(history: pd.DataFrame, cols: list[str], target: str) -> pd.DataFrame:
    """Leave-one-cohort-out on the pooled data, scored separately at each age."""
    p = np.zeros(len(history))
    for c in sorted(history.cohort.unique()):
        tr, te = history.cohort != c, history.cohort == c
        p[te.values] = logit().fit(history.loc[tr, cols], history.loc[tr, target]).predict_proba(history.loc[te, cols])[:, 1]
    out = []
    for age in (18, 19, 20, 21):
        m = (history.cutoff_age == age).values
        y = history[target].values[m]
        out.append({"age": age, "auc": float(roc_auc_score(y, p[m])), "brier": float(brier_score_loss(y, p[m])),
                    "base": float(y.mean()), "n": float(m.sum())})
    return pd.DataFrame(out)


def build() -> None:
    con = duckdb.connect(str(WAREHOUSE))
    states = prepare(con.execute("SELECT * FROM states").df())
    history = states[states.kind == "history"].reset_index(drop=True)
    report, scored = [], []
    for name, cols, target in [("established, objective", OBJECTIVE, "y_top"),
                               ("established, with market", OBJECTIVE + CROWD, "y_top"),
                               ("big five, with market", OBJECTIVE + CROWD, "y_big5")]:
        for r in pooled_validation(history, cols, target).to_dict("records"):
            report.append({"model": name, **r})
    m_obj = logit().fit(history[OBJECTIVE], history.y_top)
    m_all = logit().fit(history[OBJECTIVE + CROWD], history.y_top)
    m_b5 = logit().fit(history[OBJECTIVE + CROWD], history.y_big5)

    for age in (18, 19, 20, 21):
        hist = history[history.cutoff_age == age].reset_index(drop=True)
        cur = states[(states.kind == "current") & (states.cutoff_age.round().clip(18, 21) == age)].reset_index(drop=True)
        if cur.empty:
            continue
        report.append({"model": "comparables P10-P90 coverage (league minutes)", "age": age,
                       "auc": np.nan, "brier": np.nan, "base": range_coverage(hist), "n": float(len(hist))})
        cur["p_objective"] = m_obj.predict_proba(cur[OBJECTIVE])[:, 1]
        cur["p_established"] = m_all.predict_proba(cur[OBJECTIVE + CROWD])[:, 1]
        cur["p_big5"] = m_b5.predict_proba(cur[OBJECTIVE + CROWD])[:, 1]

        # Drivers: contribution of each variable to the log-odds, relative to the average player.
        imp, sc, lr = m_all[0], m_all[1], m_all[2]
        z = sc.transform(imp.transform(cur[OBJECTIVE + CROWD]))
        contrib = z[:, : len(OBJECTIVE + CROWD)] * lr.coef_[0][: len(OBJECTIVE + CROWD)]

        # Comparables: the 25 most similar players at the same age and in the same position.
        pools = {}
        for pos in hist.position.dropna().unique():
            ph = hist[hist.position == pos].reset_index(drop=True)
            simsc = make_pipeline(SimpleImputer(strategy="median"), StandardScaler()).fit(ph[SIMILARITY])
            pools[pos] = (ph, simsc, NearestNeighbors(n_neighbors=min(25, len(ph))).fit(simsc.transform(ph[SIMILARITY])))
        for i, row in cur.iterrows():
            ph, simsc, nn = pools.get(row.position, pools["Midfield"])
            _, nidx = nn.kneighbors(simsc.transform(cur.loc[[i], SIMILARITY]))
            neigh = ph.iloc[nidx[0]]
            mins = neigh.out_min_league.fillna(0).values
            peak = neigh.out_peak_mv.fillna(0).values
            drivers = sorted(zip(OBJECTIVE + CROWD, contrib[i]), key=lambda t: -abs(t[1]))[:6]
            comps = neigh.head(5)
            scored.append({
                "player_id": int(row.player_id), "age_model": age,
                "p_established": float(row.p_established), "p_objective": float(row.p_objective),
                "p_big5": float(row.p_big5),
                "min_p10": float(np.percentile(mins, 10)), "min_p50": float(np.percentile(mins, 50)),
                "min_p90": float(np.percentile(mins, 90)),
                "peak_p10": float(np.percentile(peak, 10)), "peak_p50": float(np.percentile(peak, 50)),
                "peak_p90": float(np.percentile(peak, 90)),
                "comparables_established": float((mins >= 4500).mean()),
                "drivers": json.dumps([{"name": LABELS[c], "value": round(float(v), 3)} for c, v in drivers]),
                "comparables": json.dumps([{
                    "name": r.name, "at_age": int(age), "then_club_league": r.last_league,
                    "later_minutes": 0 if pd.isna(r.out_min_league) else int(r.out_min_league),
                    "later_peak_value": 0.0 if pd.isna(r.out_peak_mv) else float(r.out_peak_mv),
                } for r in comps.itertuples()]),
            })
    con.register("rep", pd.DataFrame(report))
    con.execute("CREATE OR REPLACE TABLE model_report AS SELECT * FROM rep")
    con.register("sc", pd.DataFrame(scored))
    con.execute("CREATE OR REPLACE TABLE promise AS SELECT * FROM sc")
    print(pd.DataFrame(report).round(3).to_string(index=False))
    print("scored", len(scored))
    con.close()


if __name__ == "__main__":
    build()
