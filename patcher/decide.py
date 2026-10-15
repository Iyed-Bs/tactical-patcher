"""When to buy: optimal transfer timing by least-squares Monte Carlo.

Buying a young player is an option you can exercise in any window: now, or
later at a price that moves with his development. This is the American
option problem, and least-squares Monte Carlo (Longstaff & Schwartz, 2001)
is the standard way to learn its exercise policy: an approximate dynamic
programme that fits, at every window, a regression of the value of waiting
on the state, backwards in time. (It belongs to the fitted value iteration
family of reinforcement learning.)

Environment, one season per step, from his current state:
  * where he is: his rating moves with the growth engine at his club;
  * if bought at window t: from then on it moves with the growth engine in
    the role our depth chart gives him;
  * price at t: the market value model at his state, plus the selling
    club's premium;
  * reward: buying at market value is neutral, so what counts are our
    edges: each season, his level above our current starters in his line
    (priced at what the market charges per rating point at our tier,
    spread over three seasons of use), and his value at the horizon as he
    develops with us; minus the fee (market value plus the seller's
    premium). Wages are part of a point's market price, not counted twice.
The policy says, in every simulated future, whether to buy at that window
or keep the option; the answer is the share of futures where "now" wins,
the best window, and the value of each choice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import growth2, market
from .scout import ROLE_SHARE, STARTERS, Scout, line_of

PREMIUM = 1.15            # fee over market value a selling club usually asks
DISCOUNT = 0.92           # one season ahead is worth 8 % less


def simulate_paths(sc: Scout, row: pd.Series, club: str, role: str, seasons: int, runs: int, rng, start=None) -> np.ndarray:
    """Rating paths (runs x seasons+1) under a club and role, optionally from given starting ratings."""
    base = sc.state(row, club, role)
    X = pd.DataFrame([base] * runs)
    if start is not None:
        X["overall"] = start
        X["rel_to_team"] = X.overall - X.team_overall
    out = np.zeros((runs, seasons + 1)); out[:, 0] = X.overall
    for s in range(seasons):
        F = X[growth2.FEATURES_CORE]
        lo, mid, hi = (sc.models[f"core_q{q}"].predict(F) for q in (10, 50, 90))
        u = rng.uniform(0, 1, runs)
        delta = np.where(u < 0.5, mid + (u - 0.5) / 0.4 * (mid - lo), mid + (u - 0.5) / 0.4 * (hi - mid))
        X["overall"] = np.clip(X.overall + delta, 40, 99)
        X["delta_prev"], X["age"] = delta, X.age + 1
        X["rel_to_team"] = X.overall - X.team_overall
        out[:, s + 1] = X.overall
    return out


def solve(sc: Scout, ea_id: int, our_club: str, horizon: int = 4, runs: int = 600, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    row = sc.players.set_index("ea_id").loc[ea_id].copy(); row["ea_id"] = ea_id
    line = line_of(row.position)
    vm = market.load()
    clubs = sc.clubs
    lvl_ours = int(clubs.loc[our_club].league_level) if our_club in clubs.index else 1
    lvl_his = int(clubs.loc[row.club_now].league_level) if row.club_now in clubs.index else 1
    mates = sc.squads[(sc.squads.club_now == our_club) & (sc.squads.line == line) & (sc.squads.ea_id != ea_id)]
    bar = float(mates.overall.nlargest(STARTERS[line]).min()) if len(mates) >= STARTERS[line] else 60.0
    role_now = sc.role_at(row, our_club)
    # Euros per rating point of contribution at our tier: what the market pays for a point at our starters' level.
    v_bar = market.value(vm, [bar, bar + 1], 25, line, lvl_ours, ROLE_SHARE["starter"], 0.0)
    eur_per_point = float(v_bar[1] - v_bar[0]) / 3              # a season's use of one rating point

    # 1. Futures where he stays: the state at each window.
    stay = simulate_paths(sc, row, row.club_now, sc.role_at(row, row.club_now), horizon, runs, rng)
    ages = row.age + np.arange(horizon + 1)
    # 2. Value of buying at window t from state r: simulate the rest of the horizon with us (role re-judged at that level).
    def exercise_value(t: int, r: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        left = horizon - t
        rr = row.copy(); rr["age"] = row.age + t
        role = "starter" if np.median(r) >= bar else role_now if role_now != "bench" else "rotation"
        ours = simulate_paths(sc, rr, our_club, role, left, len(r), rng, start=r) if left > 0 else r[:, None]
        share = ROLE_SHARE[role]
        sporting = sum(DISCOUNT ** s * share * np.clip(ours[:, s] - bar, 0, None) * eur_per_point for s in range(ours.shape[1]))
        resale = DISCOUNT ** left * market.value(vm, ours[:, -1], ages[-1], line, lvl_ours, share, 0.0)
        price = PREMIUM * market.value(vm, r, ages[t], line, lvl_his, row.minutes_last if pd.notna(row.minutes_last) else 0.4, 0.0)
        return DISCOUNT ** t * (sporting + resale - price), price, DISCOUNT ** t * (sporting + resale)

    ex = np.zeros((runs, horizon + 1)); prices = np.zeros((runs, horizon + 1)); worth = np.zeros((runs, horizon + 1))
    for t in range(horizon + 1):
        ex[:, t], prices[:, t], worth[:, t] = exercise_value(t, stay[:, t])
    # 3. Backward induction with regression for the value of waiting (Longstaff-Schwartz).
    cash = np.maximum(ex[:, horizon], 0.0)                      # last window: buy if it pays, else walk away
    when = np.where(ex[:, horizon] > 0, horizon, -1)
    for t in range(horizon - 1, -1, -1):
        x = stay[:, t]
        A = np.column_stack([np.ones(runs), x, x ** 2])
        cont = A @ np.linalg.lstsq(A, cash, rcond=None)[0]       # expected value of keeping the option
        buy = (ex[:, t] > cont) & (ex[:, t] > 0)
        cash = np.where(buy, ex[:, t], cash)
        when = np.where(buy, t, when)
    windows = ["this season (January window)"] + [f"summer {2026 + t}" for t in range(1, horizon + 1)]
    share = {windows[t]: float(np.mean(when == t)) for t in range(horizon + 1)}
    share["never"] = float(np.mean(when == -1))
    best = max(share, key=share.get)
    return {"our_club": our_club, "starter_bar": bar, "windows": windows, "policy": share, "best": best,
            "value_buy_now": float(np.mean(ex[:, 0])), "value_with_option": float(np.mean(cash)),
            "worth_to_us_now": float(np.mean(worth[:, 0])),
            "price_now": float(np.median(prices[:, 0])), "price_path_p50": np.median(prices, axis=0).round(-5).tolist(),
            "rating_path_p50": np.median(stay, axis=0).round(1).tolist()}


if __name__ == "__main__":
    sc = Scout()
    for name, club in [("Bouaddi", "Paris SG"), ("Bouaddi", "Strasbourg"), ("Ethan Mbapp", "Strasbourg"), ("Doku", "Paris SG"),
                       ("Doku", "Strasbourg"), ("Yamal", "Paris SG")]:
        eid = int(sc.search(name).iloc[0].ea_id)
        r = solve(sc, eid, club)
        print(name, "->", club, "| best:", r["best"], "| policy", {k: round(v, 2) for k, v in r["policy"].items()},
              f"| price now €{r['price_now'] / 1e6:.0f}m | value buy-now €{r['value_buy_now'] / 1e6:.1f}m vs with option €{r['value_with_option'] / 1e6:.1f}m")


def final_call(v: dict, t: dict) -> dict:
    """Combine the sporting verdict with the economics: the fee he's worth to us now."""
    call, reasons = v["call"], list(v["reasons"])
    club = v.get("our_club", "the club")
    worth, price = t["worth_to_us_now"], t["price_now"]
    if call in ("Buy now", "Buy, then loan"):
        if worth >= price:
            reasons.append(f"The numbers agree: worth about €{worth / 1e6:.0f}m to {club} now, against a fee near €{price / 1e6:.0f}m.")
        else:
            call = f"Buy below €{worth / 1e6:.0f}m"
            reasons.append(f"Sporting yes, price no: he is worth about €{worth / 1e6:.0f}m to {club} now (what he adds above its starters "
                           f"plus his value as he develops there), against a fee near €{price / 1e6:.0f}m. "
                           f"Negotiate down, or {('wait: ' + t['best']) if t['best'] != 'never' else 'look elsewhere'} is the better bet.")
    elif call in ("Wait a season", "Monitor") and t["best"] not in ("never",):
        reasons.append(f"On price and development, the best moment in most futures is {t['best']}.")
    elif call in ("Wait a season", "Monitor"):
        # Waiting is the sporting answer; the price decides whether the wait ends in a bid.
        reasons.append(f"At a fee near €{price / 1e6:.0f}m most futures never pay back (worth about €{worth / 1e6:.0f}m to {club} now): "
                       f"a bid makes sense only if the price falls toward that.")
    return {**v, "call": call, "reasons": reasons}
