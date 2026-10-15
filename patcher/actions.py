"""Match contribution from event data: every on-ball action valued with VAEP.

Source: Wyscout public dataset (Pappalardo et al., 2019, CC BY 4.0): the
2017/18 season of the five big leagues, World Cup 2018 and Euro 2016.

Pipeline, checkpointed (each stage saves to PATCHER_DATA and is skipped
when its file exists):
  1. convert:  Wyscout events -> SPADL actions (socceraction), plus minutes
  2. features: VAEP game-state features and labels (socceraction)
  3. value:    two LightGBM classifiers, P(scores) and P(concedes) within
               the next 10 actions, trained on one half of the matches and
               applied to the other (two folds), then socceraction's VAEP
               formula: value = dP(score) - dP(concede)
  4. aggregate per player and competition: minutes, VAEP per 90
               (offensive, defensive), action mix, mean position.

socceraction's own VAEP.fit passes an argument current LightGBM rejects,
so the two models are trained here directly.
Run:  python -m patcher.actions
"""
from __future__ import annotations

import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd
import socceraction.spadl as spadl
from socceraction.data.wyscout import PublicWyscoutLoader
from socceraction.vaep import VAEP
from socceraction.vaep import formula

from .paths import DATA, WYSCOUT

warnings.filterwarnings("ignore")
GAMES = DATA / "wy_games.parquet"
ACTIONS = DATA / "wy_actions.parquet"
MINUTES = DATA / "wy_minutes.parquet"
FEATURES = DATA / "wy_vaep_features.parquet"
LABELS = DATA / "wy_vaep_labels.parquet"
VALUED = DATA / "wy_actions_valued.parquet"
PLAYERS = DATA / "wy_player_season.parquet"


def convert() -> None:
    loader = PublicWyscoutLoader(root=str(WYSCOUT))
    games_all, actions_all, minutes_all = [], [], []
    for comp in loader.competitions().itertuples():
        games = loader.games(comp.competition_id, comp.season_id)
        games["competition"] = comp.competition_name
        games_all.append(games)
        for g in games.itertuples():
            acts = spadl.wyscout.convert_to_actions(loader.events(g.game_id), g.home_team_id)
            acts["competition"] = comp.competition_name
            actions_all.append(acts)
            pl = loader.players(g.game_id)
            pl["competition"] = comp.competition_name
            minutes_all.append(pl[["game_id", "team_id", "player_id", "minutes_played", "competition"]])
        print(f"{comp.competition_name}: {len(games)} games", flush=True)
    pd.concat(games_all).to_parquet(GAMES)
    pd.concat(actions_all, ignore_index=True).to_parquet(ACTIONS)
    pd.concat(minutes_all, ignore_index=True).to_parquet(MINUTES)


def features() -> None:
    games, actions = pd.read_parquet(GAMES), pd.read_parquet(ACTIONS)
    model = VAEP()
    X, y = [], []
    for n, g in enumerate(games.itertuples()):
        a = spadl.add_names(actions[actions.game_id == g.game_id].reset_index(drop=True))
        X.append(model.compute_features(g, a).assign(game_id=g.game_id))
        y.append(model.compute_labels(g, a))
        if n % 300 == 0:
            print(f"features: {n} games", flush=True)
    pd.concat(X, ignore_index=True).to_parquet(FEATURES)
    pd.concat(y, ignore_index=True).to_parquet(LABELS)


def value() -> None:
    games, actions = pd.read_parquet(GAMES), pd.read_parquet(ACTIONS)
    X, y = pd.read_parquet(FEATURES), pd.read_parquet(LABELS)
    # Same game order as the feature pass, so rows line up.
    order = pd.concat([actions[actions.game_id == gid] for gid in games.game_id], ignore_index=True)
    fold = (X.game_id % 2).values
    cols = [c for c in X.columns if c != "game_id"]
    p = {"scores": np.zeros(len(X)), "concedes": np.zeros(len(X))}
    for k in (0, 1):
        tr, te = fold != k, fold == k
        for target in p:
            clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=63, min_child_samples=100,
                                     subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1)
            clf.fit(X.loc[tr, cols], y.loc[tr, target])
            p[target][te] = clf.predict_proba(X.loc[te, cols])[:, 1]
        print(f"fold {k}: trained on {int(tr.sum())} actions, rated {int(te.sum())}", flush=True)
    v = formula.value(spadl.add_names(order), pd.Series(p["scores"]), pd.Series(p["concedes"]))
    order[["off_value", "def_value", "vaep"]] = v[["offensive_value", "defensive_value", "vaep_value"]].values
    order.to_parquet(VALUED)


def aggregate() -> None:
    a = spadl.add_names(pd.read_parquet(VALUED))
    minutes = pd.read_parquet(MINUTES)
    mins = minutes.groupby(["player_id", "competition"], as_index=False).minutes_played.sum()
    agg = a.groupby(["player_id", "competition"]).agg(
        actions=("vaep", "size"), vaep=("vaep", "sum"), off=("off_value", "sum"), dfn=("def_value", "sum"),
        x=("start_x", "mean"), y=("start_y", "mean")).reset_index()
    mix = a.pivot_table(index=["player_id", "competition"], columns="type_name", values="vaep",
                        aggfunc="size", fill_value=0).add_prefix("n_").reset_index()
    out = agg.merge(mix, on=["player_id", "competition"]).merge(mins, on=["player_id", "competition"])
    per90 = 90 / out.minutes_played.clip(lower=1)
    out["vaep_p90"], out["off_p90"], out["def_p90"] = out.vaep * per90, out.off * per90, out.dfn * per90
    out.rename(columns={"player_id": "wy_id"}).to_parquet(PLAYERS)
    print(out.shape)


if __name__ == "__main__":
    for path, stage in [(MINUTES, convert), (LABELS, features), (VALUED, value), (PLAYERS, aggregate)]:
        if not path.exists():
            stage()
        else:
            print("skip", stage.__name__, "(already done)")
