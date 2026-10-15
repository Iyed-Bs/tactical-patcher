"""Model lab: which algorithm best predicts a player's next season?

One fixed benchmark, every candidate judged the same way:
  train on season pairs from FIFA 15-21, test on 22 -> 23 (never seen);
  score the 10/50/90 % quantiles with the pinball loss (the proper score for
  quantiles), plus median error, band coverage and width, and how many real
  +5 jumps fall inside the band.
Candidates: age curve; LightGBM (current); LightGBM tuned; CatBoost
multi-quantile; an MLP with quantile heads; a GRU reading the last three
seasons of weekly EA updates (12 points a season) next to the tabular
features; and blends of the best.

Run:  python -m patcher.lab
"""
from __future__ import annotations

import json
import time

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import torch
from torch import nn

from . import growth2
from .paths import DATA, TRANSFERMARKT

Q = np.array([0.1, 0.5, 0.9])
SEQ_COLS = ["overall"] + growth2.FACE
STEPS = 12                      # points per season after resampling the weekly updates
SEASONS_BACK = 3
OUT = DATA / "lab"


# ── Data ─────────────────────────────────────────────────────────────────────
def sequences() -> tuple[pd.DataFrame, np.ndarray]:
    """For each player and edition, his weekly updates resampled to STEPS points: (player_id, version) -> (STEPS, 7)."""
    cols = ", ".join(SEQ_COLS)
    w = duckdb.sql(f"""SELECT player_id, fifa_version, fifa_update, {cols}
                       FROM read_parquet('{growth2.FIFA}') ORDER BY player_id, fifa_version, fifa_update""").df()
    w[SEQ_COLS] = w[SEQ_COLS].astype(float)
    keys, arrs = [], []
    grid = np.linspace(0, 1, STEPS)
    for (pid, v), g in w.groupby(["player_id", "fifa_version"], sort=False):
        x = g[SEQ_COLS].to_numpy()
        t = np.linspace(0, 1, len(x)) if len(x) > 1 else np.zeros(1)
        arrs.append(np.column_stack([np.interp(grid, t, x[:, j]) if len(x) > 1 else np.full(STEPS, x[0, j]) for j in range(x.shape[1])]))
        keys.append((pid, v))
    idx = pd.DataFrame(keys, columns=["player_id", "fifa_version"])
    idx["row"] = np.arange(len(idx))
    return idx, np.stack(arrs).astype(np.float32)


def dataset() -> tuple[pd.DataFrame, np.ndarray]:
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    df = growth2.prepare(growth2.pairs(con), growth2.minutes(con))
    df = df[df.target.notna()].reset_index(drop=True)
    idx, seq = sequences()
    # Seasons v-3 .. v-1 before the prediction point (start of edition v), plus edition v's own first point is in the tabular row.
    X = np.zeros((len(df), SEASONS_BACK * STEPS, len(SEQ_COLS) + 1), dtype=np.float32)   # last channel: mask
    for k in range(SEASONS_BACK):
        back = SEASONS_BACK - k            # 3, 2, 1 seasons back
        m = df[["player_id", "fifa_version"]].assign(fifa_version=df.fifa_version - back).merge(idx, on=["player_id", "fifa_version"], how="left")
        ok = m.row.notna().values
        X[ok, k * STEPS:(k + 1) * STEPS, :-1] = seq[m.row[ok].astype(int).values]
        X[ok, k * STEPS:(k + 1) * STEPS, -1] = 1.0
    # Express the sequence relative to the current rating, so the network reads trajectory, not level.
    cur = df.overall.values.astype(np.float32)[:, None]
    X[:, :, 0] = np.where(X[:, :, -1] > 0, X[:, :, 0] - cur, 0)
    for j in range(1, len(SEQ_COLS)):
        base = df[SEQ_COLS[j]].fillna(0).values.astype(np.float32)[:, None]
        X[:, :, j] = np.where(X[:, :, -1] > 0, X[:, :, j] - base, 0)
    return df, X


# ── Scores ───────────────────────────────────────────────────────────────────
def pinball(y: np.ndarray, q: np.ndarray) -> float:
    d = y[:, None] - q
    return float(np.mean(np.maximum(Q * d, (Q - 1) * d)))


def evaluate(y: np.ndarray, q: np.ndarray, age: np.ndarray) -> dict:
    q = np.sort(q, axis=1)
    young = age <= 21
    big = y >= 5
    return {"pinball": pinball(y, q), "pinball_young": pinball(y[young], q[young]),
            "mae": float(np.mean(np.abs(y - q[:, 1]))), "mae_young": float(np.mean(np.abs(y[young] - q[young, 1]))),
            "coverage_80": float(np.mean((y >= q[:, 0]) & (y <= q[:, 2]))), "width_80": float(np.mean(q[:, 2] - q[:, 0])),
            "big_jumps_in_band": float(np.mean(q[big, 2] >= 5)) if big.any() else None}


# ── Candidates ───────────────────────────────────────────────────────────────
def age_curve(tr, te):
    g = tr.assign(target=tr.target.astype(float)).groupby("age").target.quantile([0.1, 0.5, 0.9]).unstack()
    return np.column_stack([te.age.map(g[c]).fillna(0).values for c in g.columns])


def lgbm(tr, te, features, **kw):
    params = dict(n_estimators=900, learning_rate=0.03, num_leaves=63, min_child_samples=60, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.8, verbose=-1) | kw
    return np.column_stack([lgb.LGBMRegressor(objective="quantile", alpha=a, **params).fit(tr[features].astype(float), tr.target.astype(float)).predict(te[features].astype(float)) for a in Q])


def catboost(tr, te, features):
    from catboost import CatBoostRegressor
    m = CatBoostRegressor(loss_function="MultiQuantile:alpha=0.1,0.5,0.9", iterations=1500, learning_rate=0.05, depth=7,
                          verbose=0, thread_count=16, random_seed=7, allow_writing_files=False)
    m.fit(tr[features].astype(float), tr.target.astype(float))
    return m.predict(te[features].astype(float))


class QuantileNet(nn.Module):
    """Tabular MLP, optionally with a GRU over the weekly rating sequence; three quantile heads."""

    def __init__(self, n_tab: int, n_seq: int | None, hidden: int = 128):
        super().__init__()
        self.gru = nn.GRU(n_seq, 64, batch_first=True, num_layers=1) if n_seq else None
        width = n_tab + (64 if n_seq else 0)
        self.mlp = nn.Sequential(nn.Linear(width, hidden), nn.GELU(), nn.Dropout(0.1), nn.Linear(hidden, hidden), nn.GELU(),
                                 nn.Dropout(0.1), nn.Linear(hidden, 3))

    def forward(self, tab, seq=None):
        h = tab
        if self.gru is not None:
            _, last = self.gru(seq)
            h = torch.cat([tab, last[-1]], dim=1)
        out = self.mlp(h)
        # Monotone quantiles: q10 = mid - softplus, q90 = mid + softplus.
        mid = out[:, 1:2]
        return torch.cat([mid - nn.functional.softplus(out[:, 0:1]), mid, mid + nn.functional.softplus(out[:, 2:3])], dim=1)


def neural(tr, te, features, seq_tr=None, seq_te=None, epochs: int = 25, seed: int = 7):
    torch.manual_seed(seed); np.random.seed(seed)
    F = tr[features].astype(float)
    mu, sd = F.mean(), F.std().replace(0, 1)
    def tab(df):
        f = df[features].astype(float)
        z = ((f - mu) / sd).fillna(0).to_numpy(np.float32)
        miss = f.isna().to_numpy(np.float32)
        return torch.tensor(np.hstack([z, miss]), dtype=torch.float32)
    Xtr, Xte = tab(tr), tab(te)
    ytr = torch.tensor(tr.target.to_numpy(dtype=np.float32))
    Str = torch.tensor(seq_tr / 10.0) if seq_tr is not None else None
    Ste = torch.tensor(seq_te / 10.0) if seq_te is not None else None
    net = QuantileNet(Xtr.shape[1], Str.shape[2] if Str is not None else None)
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=epochs * (len(Xtr) // 512 + 1))
    qs = torch.tensor(Q, dtype=torch.float32)
    for _ in range(epochs):
        net.train()
        perm = torch.randperm(len(Xtr))
        for i in range(0, len(Xtr), 512):
            b = perm[i:i + 512]
            pred = net(Xtr[b], Str[b] if Str is not None else None)
            d = ytr[b, None] - pred
            loss = torch.maximum(qs * d, (qs - 1) * d).mean()
            opt.zero_grad(); loss.backward(); opt.step(); sched.step()
    net.eval()
    with torch.no_grad():
        return net(Xte, Ste).numpy()


def run() -> pd.DataFrame:
    t0 = time.time()
    df, X = dataset()
    tr_mask, te_mask = (df.fifa_version <= 21).values, (df.fifa_version == 22).values
    tr, te = df[tr_mask], df[te_mask]
    y, age = te.target.to_numpy(dtype=float), te.age.to_numpy(dtype=float)
    core = growth2.FEATURES_CORE
    rich = core + ["potential", "gap", "international_reputation", "wr_att", "wr_def", "log_value", "log_wage", "contract_years",
                   "in_season_prev", "team_prestige", "team_def_pressure"]
    preds = {}
    print(f"data ready: {tr_mask.sum():,} train / {te_mask.sum():,} test pairs ({time.time() - t0:.0f}s)", flush=True)
    for name, fn in [
        ("age curve", lambda: age_curve(tr, te)),
        ("LightGBM core (current)", lambda: lgbm(tr, te, core)),
        ("LightGBM core, tuned", lambda: lgbm(tr, te, core, n_estimators=2000, learning_rate=0.02, num_leaves=127, min_child_samples=40,
                                                  reg_lambda=1.0)),
        ("LightGBM rich", lambda: lgbm(tr, te, rich)),
        ("CatBoost core", lambda: catboost(tr, te, core)),
        ("MLP core", lambda: neural(tr, te, core)),
        ("GRU sequence + core", lambda: neural(tr, te, core, X[tr_mask], X[te_mask])),
    ]:
        t = time.time()
        preds[name] = np.sort(fn(), axis=1)
        print(f"  {name:28s} pinball {pinball(y, preds[name]):.4f}  ({time.time() - t:.0f}s)", flush=True)
    ranked = sorted((k for k in preds if k != "age curve"), key=lambda k: pinball(y, preds[k]))
    preds["blend of best two"] = np.mean([preds[k] for k in ranked[:2]], axis=0)
    preds["blend of best three"] = np.mean([preds[k] for k in ranked[:3]], axis=0)
    rows = [{"model": k, **evaluate(y, v, age)} for k, v in preds.items()]
    res = pd.DataFrame(rows).sort_values("pinball")
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "round1.csv", index=False)
    np.save(OUT / "round1_test_target.npy", y)
    for k, v in preds.items():
        np.save(OUT / f"round1_{k.replace(' ', '_').replace(',', '').replace('(', '').replace(')', '').replace('+', 'plus')}.npy", v)
    return res





def run2() -> pd.DataFrame:
    """Round 2: selection on a validation season (train <= 20, validate 21), then refit <= 21 and test once on 22."""
    df, X = dataset()
    X = np.nan_to_num(X)                                     # keepers have no face stats in EA's history
    core = growth2.FEATURES_CORE
    rich = core + ["potential", "gap", "international_reputation", "wr_att", "wr_def", "log_value", "log_wage", "contract_years",
                   "in_season_prev", "team_prestige", "team_def_pressure"]
    def candidates(tr_mask, te_mask):
        tr, te = df[tr_mask], df[te_mask]
        return {
            "LightGBM core": lambda: lgbm(tr, te, core),
            "LightGBM rich": lambda: lgbm(tr, te, rich),
            "CatBoost rich": lambda: catboost(tr, te, rich),
            "MLP core x5": lambda: np.mean([neural(tr, te, core, seed=s) for s in range(5)], axis=0),
            "MLP rich x5": lambda: np.mean([neural(tr, te, rich, seed=s) for s in range(5)], axis=0),
            "GRU sequence + rich x3": lambda: np.mean([neural(tr, te, rich, X[tr_mask], X[te_mask], seed=s) for s in range(3)], axis=0),
        }
    results = {}
    for stage, (tr_mask, te_mask) in {"validation (train <=20, test 21)": ((df.fifa_version <= 20).values, (df.fifa_version == 21).values),
                                      "test (train <=21, test 22)": ((df.fifa_version <= 21).values, (df.fifa_version == 22).values)}.items():
        te = df[te_mask]
        y, age = te.target.to_numpy(dtype=float), te.age.to_numpy(dtype=float)
        preds = {}
        for name, fn in candidates(tr_mask, te_mask).items():
            t = time.time()
            preds[name] = np.sort(fn(), axis=1)
            print(f"  [{stage}] {name:26s} pinball {pinball(y, preds[name]):.4f} ({time.time() - t:.0f}s)", flush=True)
        results[stage] = (preds, y, age)
    # Blend weights chosen on validation only (non-negative, sum to one, coarse grid over the best three).
    vpreds, vy, _ = results["validation (train <=20, test 21)"]
    top3 = sorted(vpreds, key=lambda k: pinball(vy, vpreds[k]))[:3]
    best_w, best_s = None, np.inf
    for a in np.linspace(0, 1, 11):
        for b in np.linspace(0, 1 - a, int(round((1 - a) * 10)) + 1):
            w = (a, b, 1 - a - b)
            s = pinball(vy, sum(wi * vpreds[k] for wi, k in zip(w, top3)))
            if s < best_s:
                best_w, best_s = w, s
    tpreds, ty, tage = results["test (train <=21, test 22)"]
    tpreds["blend (weights from validation)"] = sum(wi * tpreds[k] for wi, k in zip(best_w, top3))
    rows = [{"model": k, **evaluate(ty, v, tage)} for k, v in tpreds.items()]
    res = pd.DataFrame(rows).sort_values("pinball")
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "round2.csv", index=False)
    (OUT / "round2_blend.json").write_text(json.dumps({"members": top3, "weights": [float(w) for w in best_w], "validation_pinball": best_s}, indent=2))
    return res


if __name__ == "__main__":
    import sys
    pd.set_option("display.width", 200)
    print((run2() if "2" in sys.argv[1:] else run()).round(4).to_string(index=False))
