"""Tactical Patcher, the scout's engine.

For any current player: who he is today (EA FC 27 and Football Life 26
ratings, club as of the 2026 summer window, minutes in 2025/26), where he
could be in one to four seasons under a scenario (stay, or join a club in a
given role), what Football Life's own Master League engine would make of
him, which past players looked like him at the same age and what they
became, and a plain recommendation.

Build:  python -m patcher.scout
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from . import growth2
from .paths import DATA, TRANSFERMARKT
from .sources.fl import ABILITIES as FL_ABILITIES

OUT = DATA / "scout"
SEASON = 2026                     # the season about to be played (2026/27)
LEVELS = {  # EA league name -> level (1 = top flight)
    "EFL Championship": 2, "LALIGA HYPERMOTION": 2, "Bundesliga 2": 2, "Serie BKT": 2, "Ligue 2 BKT": 2,
    "EFL League One": 3, "3. Liga": 3, "EFL League Two": 4,
}
ROLE_SHARE = {"starter": 0.72, "rotation": 0.40, "bench": 0.12, "loan_starter": 0.75}
LINES = {"GK": ["GK"], "DEF": ["CB", "LB", "RB", "LWB", "RWB"], "MID": ["CDM", "CM", "CAM", "LM", "RM"], "ATT": ["ST", "CF", "LW", "RW"]}
STARTERS = {"GK": 1, "DEF": 4, "MID": 3, "ATT": 3}
FACE_EA = {"pace": "pac", "shooting": "sho", "passing": "pas", "dribbling": "dri", "defending": "def", "physic": "phy"}
MENTAL_EA = {"movement_reactions": "reactions", "mentality_composure": "composure", "mentality_vision": "vision",
             "mentality_positioning": "positioning", "mentality_interceptions": "interceptions"}


def line_of(pos: str | None) -> str:
    for line, codes in LINES.items():
        if pos in codes:
            return line
    return "MID"


# ── Build ────────────────────────────────────────────────────────────────────
def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fc27 = pd.read_parquet(DATA / "ea_fc27.parquet")
    fc27 = fc27[fc27.gender == "Men's Football"].copy()
    fc26 = pd.read_parquet(DATA / "ea_fc26.parquet")
    fc26 = fc26[fc26.gender == "Men's Football"].set_index("ea_id")
    ident = pd.read_parquet(DATA / "identity_v2.parquet")
    fl = pd.read_parquet(DATA / "fl" / "fl26_players.parquet")

    # Clubs: strength = mean of the best eleven in FC 27.
    fc27["line"] = fc27.position.map(line_of)
    top11 = fc27.sort_values("overall", ascending=False).groupby("team").head(11)
    clubs = top11.groupby("team").overall.mean().round(1).rename("team_overall").reset_index()
    # A club can sit in two EA "leagues" (South American sides also appear under the Libertadores): keep its most common one.
    clubs["league"] = clubs.team.map(fc27.groupby("team").league.agg(lambda s: s.value_counts().index[0]))
    clubs["league_level"] = clubs.league.map(LEVELS).fillna(1).astype(int)
    clubs.to_parquet(OUT / "clubs.parquet")

    # Current club: the 2026 summer window first (Transfermarkt), then EA's September file.
    tr = pd.read_parquet(DATA / "tm_transfers_2026.parquet")
    arrivals = tr[(tr.direction == "in") & ~tr.fee_text.str.startswith("End of loan", na=False)]
    arrivals = arrivals.drop_duplicates("player_id", keep="last")[["player_id", "club_id", "club", "fee_text", "fee", "market_value", "loan"]]
    arrivals.columns = ["tm_id", "club_tm_id", "club_tm", "move_fee_text", "move_fee", "tm_value", "on_loan"]

    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    tmp = con.execute("""SELECT player_id AS tm_id, current_club_id, current_club_name, market_value_in_eur, highest_market_value_in_eur,
                                position AS tm_position, sub_position AS tm_sub_position, country_of_citizenship FROM tm.players""").df()
    mins = growth2.minutes(con)
    mins = mins[mins.season == SEASON - 1].groupby("player_id").minutes_share.sum().rename("minutes_last").reset_index()

    p = fc27.merge(ident[["ea_id", "tm_id", "fl_id"]].dropna(subset=["ea_id"]), on="ea_id", how="left")
    p = p.merge(arrivals, on="tm_id", how="left").merge(tmp, on="tm_id", how="left")
    p = p.merge(mins.rename(columns={"player_id": "ea_id"}), on="ea_id", how="left")
    # Movers carry Transfermarkt club names; translate Transfermarkt club ids to EA team names using
    # the players who did not move (their Transfermarkt club and EA team agree), majority vote.
    stayed = p[p.club_tm.isna() & p.current_club_id.notna()]
    votes = stayed.groupby(["current_club_id", "team"]).size().rename("n").reset_index().sort_values("n", ascending=False)
    tm_to_ea = votes[votes.n >= 3].drop_duplicates("current_club_id").set_index("current_club_id").team
    p["club_now"] = p.club_tm_id.map(tm_to_ea).fillna(p.club_tm).fillna(p.team)
    p["club_source"] = np.where(p.club_tm.notna(), "Transfermarkt, summer 2026", "EA FC 27, September 2026")
    p["league"] = p.club_now.map(clubs.set_index("team").league).where(p.club_now.isin(clubs.team), np.where(p.club_tm.notna(), None, p.league))
    p["value_eur"] = p.tm_value.fillna(p.market_value_in_eur)
    p["name"] = p.common_name.fillna((p.first_name.fillna("") + " " + p.last_name.fillna("")).str.strip())
    p["age"] = SEASON - pd.to_datetime(p.dob).dt.year            # age in the 2026/27 season, as games count it
    p["overall_fc26"] = p.ea_id.map(fc26.overall)
    p["height_cm"] = p.ea_id.map(pd.to_numeric(fc26.height, errors="coerce"))
    p["weight_kg"] = p.ea_id.map(pd.to_numeric(fc26.weight, errors="coerce"))
    flcols = ["fl_id", "registered_position", "playing_style", "form", "injury_resistance", "weak_foot_usage",
              "weak_foot_accuracy"] + FL_ABILITIES
    p = p.merge(fl[flcols].rename(columns={c: f"fl_{c}" for c in flcols if c != "fl_id"}), on="fl_id", how="left")
    p.to_parquet(OUT / "players.parquet")

    # History: EA overall at the start of every edition, plus FC 26 and FC 27.
    hist = con.execute(f"""
        SELECT player_id AS ea_id, fifa_version, any_value(overall) AS overall, any_value(club_name) AS club, any_value(age) AS age
        FROM read_parquet('{growth2.FIFA}') f
        WHERE fifa_update = (SELECT min(fifa_update) FROM read_parquet('{growth2.FIFA}') g WHERE g.fifa_version = f.fifa_version)
        GROUP BY 1, 2""").df()
    hist.to_parquet(OUT / "history.parquet")

    # Comparables pool: every player-season in history with what he became three seasons later and at his peak.
    pool = con.execute(f"""
        WITH s AS (
          SELECT player_id, fifa_version, any_value(short_name) AS name, any_value(age) AS age, any_value(overall) AS overall,
                 any_value(player_positions) AS positions, any_value(club_name) AS club,
                 {', '.join(f'any_value({c}) AS {c}' for c in growth2.FACE + growth2.MENTAL)}
          FROM read_parquet('{growth2.FIFA}') f
          WHERE fifa_update = (SELECT min(fifa_update) FROM read_parquet('{growth2.FIFA}') g WHERE g.fifa_version = f.fifa_version)
          GROUP BY 1, 2)
        SELECT s.*, s3.overall AS overall_3y, s3.club AS club_3y,
               (SELECT max(x.overall) FROM s x WHERE x.player_id = s.player_id AND x.fifa_version >= s.fifa_version) AS peak
        FROM s LEFT JOIN s s3 ON s3.player_id = s.player_id AND s3.fifa_version = s.fifa_version + 3
        WHERE s.age <= 24""").df()
    pool["line"] = pool.positions.fillna("").str.split(",").str[0].str.strip().map(line_of)
    pool.to_parquet(OUT / "comparables_pool.parquet")
    print(f"players {len(p):,} (moved this summer: {p.club_tm.notna().sum():,}) | clubs {len(clubs):,} | "
          f"history rows {len(hist):,} | comparables pool {len(pool):,}")


# ── Query ────────────────────────────────────────────────────────────────────
class Scout:
    def __init__(self):
        from .names import realise
        self.players = pd.read_parquet(OUT / "players.parquet")
        self.players["club_now"] = realise(self.players.club_now)        # real club names, not EA's placeholders
        clubs = pd.read_parquet(OUT / "clubs.parquet")
        clubs["team"] = realise(clubs.team)
        self.clubs = clubs.drop_duplicates("team").set_index("team")
        self.history = pd.read_parquet(OUT / "history.parquet")
        self.pool = pd.read_parquet(OUT / "comparables_pool.parquet")
        self.models = growth2.load()
        self._side, self._cal = None, None
        self.squads = self.players.assign(line=self.players.position.map(line_of))

    def search(self, q: str, limit: int = 20) -> pd.DataFrame:
        from unidecode import unidecode
        key = unidecode(q).lower().strip()
        names = self.players.name.fillna("").map(lambda s: unidecode(s).lower())
        hit = self.players[names.str.contains(key, regex=False)]
        return hit.sort_values("overall", ascending=False).head(limit)

    def role_at(self, row: pd.Series, club: str) -> str:
        """Where he would sit in a club's depth chart for his line (FC 27 squads, by club as of now)."""
        line = line_of(row.position)
        mates = self.squads[(self.squads.club_now == club) & (self.squads.line == line) & (self.squads.ea_id != row.ea_id)]
        rank = int((mates.overall > row.overall).sum())
        n = STARTERS[line]
        return "starter" if rank < n else "rotation" if rank < n + max(1, n // 2) + 1 else "bench"

    def state(self, row: pd.Series, club: str, role: str) -> pd.Series:
        c = self.clubs.loc[club] if club in self.clubs.index else None
        team_overall = float(c.team_overall) if c is not None else float(row.overall)
        s = pd.Series({
            "age": row.age, "overall": row.overall, "pos_gk": int(line_of(row.position) == "GK"),
            "pos_def": int(line_of(row.position) == "DEF"), "pos_mid": int(line_of(row.position) == "MID"),
            "pos_att": int(line_of(row.position) == "ATT"), "starter": int(role in ("starter", "loan_starter")),
            "skill_moves": row.skill_moves, "weak_foot": row.weak_foot, "height_cm": row.height_cm, "weight_kg": row.weight_kg,
            "league_level": int(c.league_level) if c is not None else 1, "team_overall": team_overall,
            "rel_to_team": row.overall - team_overall,
            "delta_prev": (row.overall - row.overall_fc26) if pd.notna(row.overall_fc26) else np.nan,
            "minutes_share": ROLE_SHARE[role], "minutes_known": 1,
        })
        for ours, ea in {**FACE_EA, **MENTAL_EA}.items():
            s[ours] = row.get(ea)
        return s.astype(float)

    def project(self, ea_id: int, club: str | None = None, role: str | None = None, seasons: int = 4,
                runs: int = 400, seed: int = 11) -> dict:
        row = self.players.set_index("ea_id").loc[ea_id]
        row = row.copy(); row["ea_id"] = ea_id
        club = club or row.club_now
        if club != row.club_now and club not in self.clubs.index:
            raise ValueError(f"unknown club: {club}")        # only his own club may fall outside EA's club list
        stays = club == row.club_now and role is None
        role = role or self.role_at(row, club)
        # The calibrated simulator (patcher.rollout): minutes follow his level after the first season,
        # his club can change if he stays where he is (he may be sold on), not if he joins a club in a set role.
        from . import rollout
        if self._side is None:
            self._side, self._cal = rollout.load(), rollout.calibration()
        base = self.state(row, club, role)
        if role == "loan_starter":
            # A loan goes to a club where he starts, one at his own level, not the parent club's: EA ratings rise with
            # the club (the target carries club bias), so starting "at Real Madrid's level" would flatter the loan.
            level = min(float(base.team_overall), float(row.overall))
            base["team_overall"], base["rel_to_team"] = level, float(row.overall) - level
        base["minutes_prev"] = row.minutes_last if pd.notna(row.minutes_last) else np.nan
        sims = rollout.simulate(self.models, self._side, pd.DataFrame([base]), seasons, runs, np.random.default_rng(seed),
                                fixed_minutes=None if stays else [ROLE_SHARE[role]], team_moves=stays,
                                scale=rollout.tier_scale(np.array([row.overall]), self._cal))
        levels, mins, team = sims["level"][0], sims["minutes"][0], sims["team"][0]
        q = lambda a, p: np.round(np.percentile(a, p, axis=0), 1).tolist()
        p50 = np.percentile(levels, 50, axis=0)
        face = {k: np.round(base[k] + rollout.SLOPES[k] * (p50 - row.overall), 1).tolist() for k in growth2.FACE}
        return {"club": club, "role": role, "minutes_share": ROLE_SHARE[role],
                "seasons": [f"{SEASON + s}/{str(SEASON + s + 1)[-2:]}" for s in range(seasons + 1)],
                "overall": {"p10": q(levels, 10), "p50": q(levels, 50), "p90": q(levels, 90)},
                "minutes_p50": np.round(np.nanpercentile(mins, 50, axis=0), 2).tolist() if np.isfinite(mins).any() else None,
                "team_p50": q(team, 50), "face": face,
                # Peaks: the high point of the median path and of the 90th-percentile path. (The median of each future's
                # best season would sit above today's rating even for a 35-year-old: noise alone lifts a maximum.)
                "peak_p50": float(np.percentile(levels, 50, axis=0).max()), "peak_p90": float(np.percentile(levels, 90, axis=0).max())}

    def comparables(self, ea_id: int, k: int = 6) -> pd.DataFrame:
        row = self.players.set_index("ea_id").loc[ea_id]
        line = line_of(row.position)
        pool = self.pool[(self.pool.age == row.age) & (self.pool.line == line) & (self.pool.player_id != ea_id)].copy()
        if pool.empty:
            return pool
        cols = list(FACE_EA) + list(MENTAL_EA)
        me = np.array([row.overall] + [row.get(FACE_EA.get(c, MENTAL_EA.get(c))) for c in cols], dtype=float)
        them = pool[["overall"] + cols].to_numpy(dtype=float)
        w = np.array([3.0] + [1.0] * len(cols))
        pool["distance"] = np.sqrt(np.nansum(w * (them - me) ** 2, axis=1))
        return pool.sort_values("distance").head(k)


# ── Judgement ────────────────────────────────────────────────────────────────
def _ml_curve() -> pd.Series:
    """Football Life's own Master League growth per season by age (from the user's careers, 2-season save)."""
    path = DATA / "fl" / "fl26_ml_growth.parquet"
    if not path.exists():
        return pd.Series(dtype=float)
    g = pd.read_parquet(path)
    g = g[(g.save == "ML00000001") & ~g.is_gk]
    return g.groupby("age").delta_per_year.mean().rolling(3, center=True, min_periods=1).mean()


ML_CURVE = None


def ml_projection(row: pd.Series, seasons: int = 4) -> dict | None:
    """What FL's Master League engine would do to his PES abilities, on average, at his age."""
    global ML_CURVE
    if ML_CURVE is None:
        ML_CURVE = _ml_curve()
    cols = [f"fl_{a}" for a in FL_ABILITIES if not a.startswith("gk_")]
    if pd.isna(row.get("fl_id")) or ML_CURVE.empty:
        return None
    level = float(np.nanmean([row[c] for c in cols]))
    path, age = [round(level, 1)], int(row.age) - 1                     # FL counts age from 2026; the season starts at age-1
    for _ in range(seasons):
        step = float(ML_CURVE.get(age, ML_CURVE.iloc[-1] if age > ML_CURVE.index.max() else ML_CURVE.iloc[0]))
        level += step; age += 1
        path.append(round(level, 1))
    return {"ability_mean": path, "abilities": {a: int(row[f"fl_{a}"]) for a in FL_ABILITIES if pd.notna(row.get(f"fl_{a}"))}}


def verdict(sc: "Scout", ea_id: int, our_club: str) -> dict:
    row = sc.players.set_index("ea_id").loc[ea_id].copy(); row["ea_id"] = ea_id
    stay = sc.project(ea_id)
    ours = sc.project(ea_id, our_club)
    loan = sc.project(ea_id, row.club_now, "loan_starter") if ours["role"] == "bench" else None
    line = line_of(row.position)
    mates = sc.squads[(sc.squads.club_now == our_club) & (sc.squads.line == line) & (sc.squads.ea_id != ea_id)]
    bar = float(mates.overall.nlargest(STARTERS[line]).min()) if len(mates) >= STARTERS[line] else float(mates.overall.min() if len(mates) else 0)
    p50_2 = ours["overall"]["p50"][2]
    reach = next((ours["seasons"][i] for i, v in enumerate(ours["overall"]["p50"]) if v >= bar), None)
    reach_hi = next((ours["seasons"][i] for i, v in enumerate(ours["overall"]["p90"]) if v >= bar), None)
    reasons = []
    if row.club_now == our_club:
        call = "Already in the squad"
        reasons.append(f"He is already at {our_club}; the projection shows what his current role gives him.")
    elif row.age >= 24:
        # Growth is mostly behind him: judge the level he brings now.
        if row.overall >= bar:
            call = "Buy now"
            reasons.append(f"A ready-made starter: {row.overall} today, against {our_club}'s starters at {bar:.0f} in his line.")
        elif row.overall >= bar - 2:
            call = "Squad option"
            reasons.append(f"Close to {our_club}'s starters ({row.overall} against {bar:.0f}) but not above them, and at {row.age} he is near his ceiling.")
        else:
            call = "Pass"
            reasons.append(f"At {row.age} he is near his ceiling ({ours['peak_p90']:.0f} at best) and below {our_club}'s starters ({bar:.0f}).")
    elif ours["peak_p90"] < bar - 3:
        call = "Pass"
        reasons.append(f"Even his optimistic path (peak {ours['peak_p90']:.0f}) stays below {our_club}'s starters in his line ({bar:.0f}).")
    elif ours["role"] in ("starter", "rotation") and p50_2 >= stay["overall"]["p50"][2] - 0.3:
        call = "Buy now"
        reasons.append(f"He joins {our_club}'s {ours['role']} group and grows at least as well there ({p50_2:.1f} in two seasons, "
                       f"against {stay['overall']['p50'][2]:.1f} where he is).")
    elif ours["role"] == "bench" and row.age <= 21 and loan and loan["peak_p50"] >= bar - 1:
        call = "Buy, then loan"
        reasons.append(f"Behind {our_club}'s starters he would sit on the bench ({ours['overall']['p50'][2]:.1f} in two seasons); "
                       f"a season as a starter elsewhere keeps him on track ({loan['overall']['p50'][2]:.1f}).")
    elif stay["overall"]["p50"][1] > ours["overall"]["p50"][1] + 0.5:
        call = "Wait a season"
        reasons.append(f"He develops better where he plays now ({stay['overall']['p50'][1]:.1f} next summer against "
                       f"{ours['overall']['p50'][1]:.1f} at {our_club}): his current club gives him the minutes first, the move comes after.")
    else:
        call = "Monitor"
        reasons.append("No clear edge either way: his next season's minutes and form will decide it.")
    if row.age < 24:
        if row.overall >= bar:
            reasons.append(f"Already at or above {our_club}'s starters in his line ({bar:.0f}).")
        elif reach:
            reasons.append(f"Projected to reach {our_club}'s starters' level ({bar:.0f}) by {reach}" + (f", {reach_hi} if he takes the high road." if reach_hi and reach_hi != reach else "."))
        elif reach_hi:
            reasons.append(f"Reaches {our_club}'s starters' level ({bar:.0f}) only on the high road, by {reach_hi}.")
    if pd.notna(row.get("value_eur")):
        reasons.append(f"Market value €{row.value_eur / 1e6:.1f}m ({'Transfermarkt' if pd.notna(row.get('tm_value')) or pd.notna(row.get('market_value_in_eur')) else 'n/a'}).")
    return {"call": call, "reasons": reasons, "our_club": our_club, "starter_bar": bar, "reach": reach, "reach_high": reach_hi,
            "stay": stay, "ours": ours, "loan": loan}


def youth_price(sc: "Scout", ea_id: int) -> list[dict]:
    """His EA rating and value at each earlier age: the price of waiting."""
    fifa = duckdb.sql(f"""SELECT fifa_version, any_value(age) AS age, any_value(overall) AS overall, any_value(potential) AS potential,
                                 any_value(value_eur) AS value_eur, any_value(club_name) AS club
                          FROM read_parquet('{growth2.FIFA}') WHERE player_id = {int(ea_id)} AND fifa_update = 1 GROUP BY 1 ORDER BY 1""").df()
    return fifa.to_dict("records")


# ── Market: every player, projected, filterable ──────────────────────────────
def market_table(runs: int = 60, seed: int = 21) -> pd.DataFrame:
    """One row per player with what a market search needs, including his projected path where he is
    (calibrated simulator: dynamic minutes, club moves), four seasons ahead."""
    from . import rollout
    from .profile import ROLES
    sc = Scout()
    p = sc.players.copy()
    prof = pd.read_parquet(OUT / "profiles.parquet").set_index("ea_id")
    clubs = sc.clubs
    line = p.position.map(line_of)
    state = pd.DataFrame({
        "age": p.age.astype(float), "overall": p.overall.astype(float),
        "pos_gk": (line == "GK").astype(float), "pos_def": (line == "DEF").astype(float),
        "pos_mid": (line == "MID").astype(float), "pos_att": (line == "ATT").astype(float),
        "starter": (p.minutes_last.fillna(0) >= 0.55).astype(float), "skill_moves": p.skill_moves.astype(float),
        "weak_foot": p.weak_foot.astype(float), "height_cm": p.height_cm.astype(float), "weight_kg": p.weight_kg.astype(float),
        "league_level": p.club_now.map(clubs.league_level).fillna(1).astype(float),
        "team_overall": p.club_now.map(clubs.team_overall).fillna(p.overall).astype(float),
        "delta_prev": (p.overall - p.overall_fc26).astype(float), "minutes_share": p.minutes_last.astype(float),
        "minutes_known": p.minutes_last.notna().astype(float), "minutes_prev": p.minutes_last.astype(float),
    })
    state["rel_to_team"] = state.overall - state.team_overall
    for ours, ea in {**FACE_EA, **MENTAL_EA}.items():
        state[ours] = p[ea].astype(float)
    sims = rollout.simulate(sc.models, rollout.load(), state, 4, runs, np.random.default_rng(seed),
                            scale=rollout.tier_scale(state.overall.to_numpy(), rollout.calibration()))
    lv = sims["level"]
    fl_cols = [c for c in p.columns if c.startswith("fl_") and c != "fl_playing_style"]
    m = p[["ea_id", "name", "age", "position", "alt_positions", "overall", "overall_fc26", "club_now", "league", "nationality",
           "foot", "height_cm", "value_eur", "minutes_last", "club_tm", "playstyles", "playstyles_plus", "fl_playing_style",
           "skill_moves", "weak_foot"] + fl_cols + list(FACE_EA.values()) + list(MENTAL_EA.values())].copy()
    m["moved_this_summer"] = m.club_tm.notna()
    # Likely peak: the high point of his median path; high road: of his 90th-percentile path (see Scout.project).
    m["peak_p50"] = np.percentile(lv, 50, axis=1).max(axis=1).round(1)
    m["peak_p90"] = np.percentile(lv, 90, axis=1).max(axis=1).round(1)
    m["in_two_p50"] = np.percentile(lv[:, :, 2], 50, axis=1).round(1)
    m["growth_room"] = (m.peak_p50 - m.overall).round(1)
    m["peak_per_million"] = (m.peak_p50 / (m.value_eur.fillna(m.value_eur.median()) / 1e6 + 1)).round(2)
    m["archetype"] = m.ea_id.map(prof.archetype)
    score = pd.DataFrame({r: m.ea_id.map(prof[f"pct_{r}"]) + 60 * m.ea_id.map(prof[f"fit_{r}"]) for r in ROLES})
    m["best_role"] = score.idxmax(axis=1)
    for r in ROLES:
        m[f"fit_{r}"] = m.ea_id.map(prof[f"fit_{r}"]).round(3)
        m[f"level_{r}"] = m.ea_id.map(prof[f"role_{r}"]).round(1)
    m.to_parquet(OUT / "market.parquet")
    return m


if __name__ == "__main__":
    import sys
    # `python -m patcher.scout` builds the tables; `... market` (after patcher.profile) plays every player forward for the market search.
    market_table() if sys.argv[1:] == ["market"] else build()
