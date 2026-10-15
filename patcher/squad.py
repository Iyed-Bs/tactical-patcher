"""Squad lab: a coach's choices, played forward.

You set the club, a formation, a coach's style or your own game plan (PES's
instructions and advanced instructions), the role each slot plays, who
starts where, how much the coach trusts youth, how much he rotates, and who
comes in or leaves. Each season the coach picks his eleven slot by slot: the
best available player for the role that slot asks for (role level from the
player's attribute signature, a youth bonus scaled by his trust), unless you
pinned one. The tactics layer (patcher.tactics) reads the plan against that
eleven: shapes in and out of possession, demands against capability, and how
hard the plan runs the starters, which caps the minutes they can carry.
Minutes follow, growth follows minutes through the calibrated growth engine,
and next season he picks again from the grown squad.

The plan changes who plays, which role, and how the minutes spread. It does
not add growth by decree: the data supports minutes, level and age as
drivers of development, not a coach's label.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import growth2, tactics
from .rollout import SLOPES
from .scout import FACE_EA, MENTAL_EA, OUT, SEASON, Scout, line_of

# Detailed EA attributes the tactics layer reads; they move with the player's rating as he grows.
DETAIL = ["shortPassing", "longPassing", "ballControl", "dribbling", "crossing", "sprintSpeed", "acceleration", "stamina",
          "finishing", "shotPower", "aggression", "defensiveAwareness", "standingTackle", "headingAccuracy", "jumping",
          "strength", "agility", "curve", "gkKicking"]


def _positions(r) -> set[str]:
    return {r.position} | {p.strip() for p in str(r.alt_positions or "").split(",") if p.strip()}


class SquadLab:
    def __init__(self, sc: Scout | None = None):
        self.sc = sc or Scout()
        from .names import realise
        self.market = pd.read_parquet(OUT / "market.parquet").set_index("ea_id")
        self.market["club_now"] = realise(self.market.club_now)
        detail = self.sc.players.set_index("ea_id")[[c for c in DETAIL if c in self.sc.players]]
        self.market = self.market.join(detail[[c for c in detail.columns if c not in self.market]], how="left")

    # ── Fixes the board can suggest ──────────────────────────────────────────
    def _swaps(self, s: pd.DataFrame, xi: list, rows: list[dict]) -> list[dict]:
        """For each shortfall, the squad player who would lift it most in one slot (and what he gives up in the slot's role)."""
        out = []
        in_xi = {x["ea_id"] for x in xi if x}
        pos = [_positions(r) for _, r in s.iterrows()]
        for r in rows:
            if r["gap"] >= -1.5:
                continue
            key, slots = r["key"], tactics.INDICATORS[r["key"]][1]
            score = tactics.player_scores(s, key)
            W = sum(c["weight"] for c in r["who"])
            total = sum(c["weight"] * c["score"] for c in r["who"])
            best = None
            for k, x in enumerate(xi):
                if not x or x["slot"] not in slots:
                    continue
                w = slots[x["slot"]]
                old = next(c["score"] for c in r["who"] if c["ea_id"] == x["ea_id"])
                for j, row in s.iterrows():
                    if row.ea_id in in_xi or not (pos[j] & tactics.ELIGIBLE[x["slot"]]):
                        continue
                    after = (total - w * old + w * float(score[j])) / W
                    if after - r["value"] >= 1 and (best is None or after > best["after"]):
                        lvl = row.get(f"level_{x['role']}")
                        best = {"target": key, "slot": k, "slot_type": x["slot"], "role": x["role"], "out": x["name"], "out_score": old,
                                "in": row["name"], "in_id": int(row.ea_id), "in_score": round(float(score[j]), 1),
                                "before": r["value"], "after": round(after, 1), "out_level": x["role_level"],
                                "in_level": None if pd.isna(lvl) else round(float(lvl), 1)}
            if best:
                out.append(best)
        return out

    def _targets(self, s: pd.DataFrame, xi: list, rows: list[dict]) -> list[dict]:
        """For the two biggest shortfalls, players on the market who would lift the weakest slot, within the club's means."""
        out = []
        m = self.market
        if not hasattr(self, "_mpos"):
            self._mpos = pd.Series([_positions(r) for _, r in m.reset_index().iterrows()], index=m.index)
            self._mscore = {}
        budget = float(np.nanmax(s.value_eur.values)) if s.value_eur.notna().any() else 5e6
        mine = set(s.ea_id)
        # Who would really come: not a player well above the club's level, nor one leaving a clearly stronger club.
        level = self.sc.clubs.team_overall
        ours = float(np.mean([x["rating"] for x in xi if x]))
        reachable = (m.overall <= ours + 4) & (m.club_now.map(level).fillna(0) <= ours + 2)
        for r in sorted([r for r in rows if r["gap"] < -1.5], key=lambda r: r["gap"])[:2]:
            key, slots = r["key"], tactics.INDICATORS[r["key"]][1]
            heavy = [c for c in r["who"] if c["weight"] >= 0.8] or r["who"]
            weak = min(heavy, key=lambda c: c["score"])
            x = next(x for x in xi if x and x["ea_id"] == weak["ea_id"])
            if key not in self._mscore:
                self._mscore[key] = tactics.player_scores(m, key)
            sc = self._mscore[key]
            ok = reachable & (m.age <= 29) & (m.overall >= x["rating"] - 1) & (m.value_eur.fillna(0) <= budget) & ~m.index.isin(mine) \
                & self._mpos.map(lambda ps: bool(ps & tactics.ELIGIBLE[x["slot"]])) & (sc >= weak["score"] + 4)
            pick = m[ok].assign(score=sc[ok]).sort_values(["score", "overall"], ascending=False).head(3)
            if len(pick):
                out.append({"target": key, "slot": xi.index(x), "slot_type": x["slot"], "replace": x["name"], "replace_score": weak["score"],
                            "budget": budget, "players": [{"ea_id": int(i), "name": q["name"], "age": int(q.age), "club": q.club_now,
                                                           "overall": int(q.overall), "score": round(float(q.score), 1),
                                                           "value": None if pd.isna(q.value_eur) else float(q.value_eur)}
                                                          for i, q in pick.iterrows()]})
        return out

    def squad(self, club: str, add: list[int], remove: list[int]) -> pd.DataFrame:
        ids = list(self.market.index[self.market.club_now == club])
        ids = [i for i in ids if i not in set(remove)] + [i for i in add if i in self.market.index and i not in ids]
        return self.market.loc[ids].reset_index()

    def _state(self, s: pd.DataFrame, team_overall: float, minutes: np.ndarray, league_level: int) -> pd.DataFrame:
        p = self.sc.players.set_index("ea_id").loc[s.ea_id]
        line = p.position.map(line_of).values
        X = pd.DataFrame({
            "age": s.age.values.astype(float), "overall": s.overall.values.astype(float),
            "pos_gk": (line == "GK").astype(float), "pos_def": (line == "DEF").astype(float),
            "pos_mid": (line == "MID").astype(float), "pos_att": (line == "ATT").astype(float),
            "starter": (minutes >= 0.55).astype(float), "skill_moves": p.skill_moves.values.astype(float),
            "weak_foot": p.weak_foot.values.astype(float), "height_cm": p.height_cm.values.astype(float),
            "weight_kg": p.weight_kg.values.astype(float), "league_level": float(league_level), "team_overall": team_overall,
            "delta_prev": s.delta_prev.values.astype(float), "minutes_share": minutes, "minutes_known": 1.0,
        })
        X["rel_to_team"] = X.overall - X.team_overall
        for ours, ea in {**FACE_EA, **MENTAL_EA}.items():
            X[ours] = s[ea].values.astype(float)
        return X

    def pick(self, s: pd.DataFrame, formation: str, roles: list[str], pins: dict[int, int], youth_trust: float,
             rotation: float, plan: dict, adv: set[str]) -> tuple[list, np.ndarray, np.ndarray]:
        """The coach's eleven, slot by slot (pinned players first), then minutes for everyone."""
        slots = tactics.FORMATIONS[formation]
        used, xi = set(), [None] * len(slots)
        by_id = {int(e): i for i, e in zip(s.index, s.ea_id)}
        order = sorted(range(len(slots)), key=lambda k: (k not in pins, k))
        for k in order:
            slot, x, y = slots[k]
            role = roles[k]
            if k in pins and by_id.get(pins[k]) is not None and by_id[pins[k]] not in used:
                i = by_id[pins[k]]
                r = s.loc[i]
            else:
                cand = [(i, r) for i, r in s.iterrows() if i not in used and _positions(r) & tactics.ELIGIBLE[slot]]
                if not cand:                                    # nobody natural: the best remaining outfielder plays out of position
                    cand = [(i, r) for i, r in s.iterrows() if i not in used and r.position != "GK" and slot != "GK"]
                if not cand:
                    continue
                score = lambda r: (r.get(f"level_{role}") if pd.notna(r.get(f"level_{role}")) else r.overall - 8) + youth_trust * 4 * (r.age <= 21)
                i, r = max(cand, key=lambda c: score(c[1]))
            used.add(i)
            fit = r.get(f"fit_{role}")
            lvl = r.get(f"level_{role}")
            xi[k] = {"slot": slot, "role": role, "ea_id": int(r.ea_id), "name": r["name"], "age": int(r.age),
                     "rating": round(float(r.overall), 1), "role_level": round(float(lvl if pd.notna(lvl) else r.overall - 8), 1),
                     "fit": None if pd.isna(fit) else round(float(fit), 2), "natural": bool(_positions(r) & tactics.ELIGIBLE[slot]),
                     "pinned": k in pins, "x": x, "y": y, "_i": i}
        # Minutes: starters carry what the plan's load lets them; the next-best in each line rotate in and take the rest.
        mins = np.full(len(s), 0.06)
        avail = np.ones(len(s))
        start_share, rot_share = 0.78 - 0.18 * rotation, 0.22 + 0.25 * rotation
        stamina = s.stamina.values.astype(float) if "stamina" in s else np.full(len(s), 65.0)
        lost = {}
        lines_of = s.position.map(line_of)
        for spot in xi:
            if spot:
                j = s.index.get_loc(spot["_i"])
                a = float(tactics.availability(stamina[j:j + 1], [spot["slot"]], plan, adv)[0])
                avail[j] = a
                mins[j] = start_share * a
                lost[lines_of.iloc[j]] = lost.get(lines_of.iloc[j], 0) + start_share * (1 - a)
                spot["availability"] = round(a, 2)
        for line in ("GK", "DEF", "MID", "ATT"):
            rest = s[(lines_of == line) & (mins < 0.3)].sort_values("overall", ascending=False)
            n_rot = {"GK": 0, "DEF": 2, "MID": 2, "ATT": 2}[line] + int(round(rotation * 2)) + (1 if lost.get(line, 0) > 0.25 else 0)
            take = rest.index[:n_rot]
            if not len(take):
                continue
            boost = youth_trust * 0.12 * (rest.age.loc[take] <= 21).values
            extra = lost.get(line, 0) / len(take)
            pos = [s.index.get_loc(t) for t in take]
            mins[pos] = np.clip(rot_share + boost + extra, 0, start_share)
        for spot in xi:
            if spot:
                spot.pop("_i")
        return xi, mins, avail

    def run(self, club: str, formation: str = "4-3-3", style: str = "positional", plan: dict | None = None,
            advanced: list[str] | None = None, roles: list[str] | None = None, pins: dict[int, int] | None = None,
            youth_trust: float = 0.3, rotation: float = 0.3, seasons: int = 3, add: list[int] | None = None,
            remove: list[int] | None = None) -> dict:
        add, remove, pins = add or [], remove or [], {int(k): int(v) for k, v in (pins or {}).items()}
        st = tactics.STYLES[style]
        plan = {**st["plan"], **(plan or {})}
        adv = set(st["advanced"] if advanced is None else advanced)
        default = tactics.default_roles(formation, style)
        roles = [r if r and r in tactics.SLOT_ROLES[slot] else d
                 for (slot, _, _), r, d in zip(tactics.FORMATIONS[formation], (roles or [None] * 11) + [None] * 11, default)]
        clubs = self.sc.clubs
        level = int(clubs.loc[club].league_level) if club in clubs.index else 1
        s = self.squad(club, add, remove)
        s["delta_prev"] = (s.overall - s.overall_fc26).fillna(0)
        history = {int(e): [round(float(o), 1)] for e, o in zip(s.ea_id, s.overall)}
        minutes_hist = {int(e): [] for e in s.ea_id}
        plan_role = {}
        out_seasons = []
        for t in range(seasons):
            xi, mins, _ = self.pick(s, formation, roles, pins, youth_trust, rotation, plan, adv)
            team = float(np.mean([x["rating"] for x in xi if x])) if any(xi) else 70.0
            fit = [x["fit"] for x in xi if x and x["fit"] is not None]
            xi_rows = pd.DataFrame([{**{k: x[k] for k in ("slot", "name", "ea_id")}} for x in xi if x])
            xi_rows = xi_rows.merge(s, on="ea_id", how="left", suffixes=("", "_s"))
            assessment = tactics.assess(xi_rows, plan, adv)
            board = tactics.board(xi, plan, adv)
            X = self._state(s, team, mins, level)
            mid = self.sc.models["core_q50"].predict(X[growth2.FEATURES_CORE].astype(float))
            out_seasons.append({"season": f"{SEASON + t}/{str(SEASON + t + 1)[-2:]}", "xi": xi, "team_level": round(team, 1),
                                "role_fit": round(float(np.mean(fit)), 2) if fit else 0.0, **board, **assessment,
                                "youth_minutes": round(float(mins[s.age.values <= 21].sum() / max(mins.sum(), 1e-9)), 3),
                                "out_of_position": [x["name"] for x in xi if x and not x["natural"]]})
            if t == 0:
                plan_role = {x["ea_id"]: x["role"] for x in xi if x}
                rows = assessment["indicators"]
                fixes = {"tactical": tactics.tactical_fixes(plan, adv, rows), "personnel": self._swaps(s, xi, rows),
                         "market": self._targets(s, xi, rows), "styles": tactics.style_fits({r["key"]: r["value"] for r in rows})}
            for j, e in enumerate(s.ea_id):
                minutes_hist[int(e)].append(round(float(mins[j]), 2))
            # Grow everyone by the engine's median for the minutes they got; attributes and role levels move with them.
            s["overall"] = np.clip(s.overall + mid, 40, 99)
            s["age"] = s.age + 1
            s["delta_prev"] = mid
            for c in [c for c in s.columns if c.startswith("level_")]:
                s[c] = s[c] + 0.85 * mid
            for ours, ea in {**FACE_EA, **MENTAL_EA}.items():
                s[ea] = np.clip(s[ea] + SLOPES.get(ours, 0.7) * mid, 1, 99)
            for c in DETAIL + [c for c in s.columns if c.startswith("fl_") and s[c].dtype.kind == "f"]:
                if c in s:
                    s[c] = np.clip(s[c].astype(float) + 0.8 * mid, 1, 99)
            for j, e in enumerate(s.ea_id):
                history[int(e)].append(round(float(s.overall.iloc[j]), 1))
        players = [{"ea_id": int(r.ea_id), "name": r["name"], "age": int(r.age) - seasons, "position": r.position,
                    "ratings": history[int(r.ea_id)], "minutes": minutes_hist[int(r.ea_id)], "added": int(r.ea_id) in add,
                    "plan_role": plan_role.get(int(r.ea_id)), "stamina": None if pd.isna(r.get("stamina")) else int(r.stamina)}
                   for _, r in s.iterrows()]
        players.sort(key=lambda p: -sum(p["minutes"]))
        squad_now = [{"ea_id": p["ea_id"], "name": p["name"], "position": p["position"], "rating": p["ratings"][0]} for p in players]
        return {"club": club, "formation": formation, "style": style, "style_label": st["label"], "style_ref": st["ref"],
                "idea": st["idea"], "plan": plan, "advanced": sorted(adv), "roles": roles, "pins": pins,
                "load": round(tactics.load(plan, adv), 2), "demands": tactics.demands(plan, adv),
                "seasons": out_seasons, "players": players, "squad": squad_now, "fixes": fixes}


def calibrate(lab: SquadLab | None = None) -> dict:
    """Each indicator's P10/P50/P90 across the big five's first elevens (4-3-3, balanced roles): the scale demands are set on."""
    import json
    lab = lab or SquadLab()
    big5 = ["Premier League", "LALIGA EA SPORTS", "Serie A Enilive", "Bundesliga", "Ligue 1 McDonald's"]
    clubs = lab.sc.clubs[lab.sc.clubs.league.isin(big5)].index
    roles = tactics.default_roles("4-3-3", "gegenpress")
    vals = {k: [] for k in tactics.INDICATORS}
    for club in clubs:
        s = lab.squad(club, [], [])
        if len(s) < 14:
            continue
        xi, _, _ = lab.pick(s, "4-3-3", roles, {}, 0.0, 0.3, tactics.DEFAULT_PLAN, set())
        rows = pd.DataFrame([{k: x[k] for k in ("slot", "name", "ea_id")} for x in xi if x]).merge(s, on="ea_id", how="left", suffixes=("", "_s"))
        for k in tactics.INDICATORS:
            v, _ = tactics.capability(rows, k)
            if not np.isnan(v):
                vals[k].append(v)
    out = {k: [round(float(np.percentile(v, q)), 1) for q in (10, 50, 90)] for k, v in vals.items() if v}
    tactics.CALIBRATION_FILE.write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(calibrate(), indent=1))
