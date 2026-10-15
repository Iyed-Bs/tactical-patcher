"""Profiling a player the way a tactical analyst would.

Three lenses:
  * Roles: 25 modern roles, each a weighted attribute signature plus the
    PlayStyles that define it; a player's suitability is the weighted mean
    of his attributes, plus PlayStyle evidence, ranked as a percentile
    among players of his line.
  * Shape: each attribute relative to what his overall level predicts
    (residuals within his line), so a profile describes *how* he plays,
    not how good he is. Gaussian-mixture archetypes per line, the number
    chosen by BIC, give the data's own families of players.
  * References: nearest shapes among today's players and among elite
    peaks of the last decade (EA ratings 84+ at their best season).

Build:  python -m patcher.profile
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture

from . import growth2
from .paths import DATA

OUT = DATA / "scout"
# EA ratings site names -> FIFA history column names.
ATTR = {
    "crossing": "attacking_crossing", "finishing": "attacking_finishing", "headingAccuracy": "attacking_heading_accuracy",
    "shortPassing": "attacking_short_passing", "volleys": "attacking_volleys", "dribbling": "skill_dribbling", "curve": "skill_curve",
    "freeKickAccuracy": "skill_fk_accuracy", "longPassing": "skill_long_passing", "ballControl": "skill_ball_control",
    "acceleration": "movement_acceleration", "sprintSpeed": "movement_sprint_speed", "agility": "movement_agility",
    "reactions": "movement_reactions", "balance": "movement_balance", "shotPower": "power_shot_power", "jumping": "power_jumping",
    "stamina": "power_stamina", "strength": "power_strength", "longShots": "power_long_shots", "aggression": "mentality_aggression",
    "interceptions": "mentality_interceptions", "positioning": "mentality_positioning", "vision": "mentality_vision",
    "penalties": "mentality_penalties", "composure": "mentality_composure", "defensiveAwareness": "defending_marking_awareness",
    "standingTackle": "defending_standing_tackle", "slidingTackle": "defending_sliding_tackle",
}
GK_ATTR = {"gkDiving": "goalkeeping_diving", "gkHandling": "goalkeeping_handling", "gkKicking": "goalkeeping_kicking",
           "gkPositioning": "goalkeeping_positioning", "gkReflexes": "goalkeeping_reflexes"}
LINE_OF = {"GK": "GK", "CB": "DEF", "LB": "DEF", "RB": "DEF", "LWB": "DEF", "RWB": "DEF", "CDM": "MID", "CM": "MID", "CAM": "MID",
           "LM": "WIDE", "RM": "WIDE", "LW": "WIDE", "RW": "WIDE", "ST": "ATT", "CF": "ATT"}

# Role -> (lines it belongs to, attribute weights, PlayStyles that signal it).
ROLES = {
    "Shot-stopper": ("GK", {"gkReflexes": 3, "gkDiving": 3, "gkPositioning": 2, "gkHandling": 2, "reactions": 1}, ["Far Reach", "Footwork", "Cross Claimer"]),
    "Sweeper keeper": ("GK", {"gkKicking": 3, "gkPositioning": 2, "reactions": 2, "composure": 2, "gkReflexes": 1}, ["Rush Out", "Deflector", "Quick Reflexes"]),
    "Stopper": ("DEF", {"standingTackle": 3, "strength": 3, "aggression": 2, "jumping": 2, "headingAccuracy": 2, "defensiveAwareness": 1}, ["Bruiser", "Aerial Fortress", "Slide Tackle"]),
    "Covering centre-back": ("DEF", {"defensiveAwareness": 3, "interceptions": 3, "sprintSpeed": 2, "acceleration": 1, "reactions": 2}, ["Anticipate", "Intercept", "Jockey"]),
    "Ball-playing centre-back": ("DEF", {"shortPassing": 3, "longPassing": 3, "composure": 2, "ballControl": 2, "vision": 1, "defensiveAwareness": 2}, ["Long Ball Pass", "Pinged Pass", "Press Proven"]),
    "Defensive full-back": ("DEF", {"standingTackle": 3, "defensiveAwareness": 3, "interceptions": 2, "strength": 1, "sprintSpeed": 1}, ["Jockey", "Anticipate", "Block"]),
    "Overlapping wing-back": ("DEF", {"sprintSpeed": 3, "stamina": 3, "crossing": 3, "acceleration": 2, "dribbling": 1}, ["Whipped Pass", "Relentless", "Quick Step"]),
    "Inverted full-back": ("DEF", {"shortPassing": 3, "composure": 2, "ballControl": 2, "interceptions": 2, "vision": 2, "agility": 1}, ["Tiki Taka", "Press Proven", "Intercept"]),
    "Anchor": ("MID", {"interceptions": 3, "defensiveAwareness": 3, "standingTackle": 2, "strength": 2, "composure": 1}, ["Intercept", "Anticipate", "Block"]),
    "Regista": ("MID", {"longPassing": 3, "vision": 3, "shortPassing": 3, "composure": 2, "curve": 1}, ["Long Ball Pass", "Pinged Pass", "Incisive Pass"]),
    "Ball-winning midfielder": ("MID", {"aggression": 3, "standingTackle": 3, "stamina": 2, "interceptions": 2, "strength": 1}, ["Relentless", "Bruiser", "Intercept"]),
    "Box-to-box": ("MID", {"stamina": 3, "shortPassing": 2, "interceptions": 2, "longShots": 2, "reactions": 2, "strength": 1}, ["Relentless", "Power Shot", "Intercept"]),
    "Mezzala": ("MID", {"dribbling": 3, "ballControl": 2, "vision": 2, "longShots": 2, "acceleration": 2, "shortPassing": 1}, ["Technical", "Finesse Shot", "Incisive Pass"]),
    "Tempo controller": ("MID", {"shortPassing": 3, "composure": 3, "ballControl": 2, "reactions": 2, "vision": 2}, ["Tiki Taka", "Press Proven", "First Touch"]),
    "Classic 10": ("MID", {"vision": 3, "shortPassing": 2, "ballControl": 3, "dribbling": 2, "curve": 2, "freeKickAccuracy": 1}, ["Incisive Pass", "Dead Ball", "Technical"]),
    "Shadow striker": ("MID", {"finishing": 3, "positioning": 3, "acceleration": 2, "reactions": 2, "composure": 2}, ["Finesse Shot", "Low Driven Shot", "Quick Step"]),
    "Inside forward": ("WIDE", {"finishing": 3, "dribbling": 3, "acceleration": 2, "agility": 2, "curve": 2, "longShots": 1}, ["Finesse Shot", "Trickster", "Quick Step"]),
    "Classic winger": ("WIDE", {"crossing": 3, "sprintSpeed": 3, "acceleration": 2, "dribbling": 2, "stamina": 1}, ["Whipped Pass", "Rapid", "Quick Step"]),
    "Wide playmaker": ("WIDE", {"vision": 3, "curve": 2, "shortPassing": 3, "dribbling": 2, "ballControl": 2}, ["Incisive Pass", "Technical", "Tiki Taka"]),
    "Two-way winger": ("WIDE", {"stamina": 3, "interceptions": 2, "standingTackle": 2, "aggression": 2, "dribbling": 2, "sprintSpeed": 1}, ["Relentless", "Intercept", "Press Proven"]),
    "Wide target": ("WIDE", {"headingAccuracy": 3, "strength": 3, "jumping": 2, "crossing": 1, "finishing": 1}, ["Aerial", "Power Header", "Bruiser"]),
    "Channel runner": ("ATT", {"sprintSpeed": 3, "acceleration": 3, "positioning": 2, "finishing": 2, "agility": 1}, ["Rapid", "Quick Step", "Low Driven Shot"]),
    "Finisher": ("ATT", {"finishing": 3, "shotPower": 2, "volleys": 2, "composure": 2, "penalties": 1, "ballControl": 1}, ["Power Shot", "Finesse Shot", "Acrobatic"]),
    "Poacher": ("ATT", {"finishing": 3, "positioning": 3, "reactions": 2, "composure": 2, "acceleration": 1}, ["Finesse Shot", "Low Driven Shot", "Acrobatic"]),
    "Target man": ("ATT", {"headingAccuracy": 3, "strength": 3, "jumping": 2, "finishing": 2, "balance": 1}, ["Aerial", "Power Header", "Bruiser"]),
    "Pressing forward": ("ATT", {"stamina": 3, "aggression": 2, "acceleration": 2, "finishing": 2, "standingTackle": 1}, ["Relentless", "Rapid", "Press Proven"]),
    "False 9": ("ATT", {"vision": 3, "shortPassing": 3, "ballControl": 2, "dribbling": 2, "finishing": 2}, ["Incisive Pass", "Tiki Taka", "Technical"]),
    "Complete forward": ("ATT", {"finishing": 2, "shotPower": 2, "strength": 2, "dribbling": 2, "headingAccuracy": 2, "vision": 1}, ["Power Shot", "Finesse Shot", "Aerial"]),
}
LABEL = {"crossing": "crossing", "finishing": "finishing", "headingAccuracy": "heading", "shortPassing": "short passing",
         "volleys": "volleys", "dribbling": "dribbling", "curve": "curve", "freeKickAccuracy": "free kicks", "longPassing": "long passing",
         "ballControl": "ball control", "acceleration": "acceleration", "sprintSpeed": "top speed", "agility": "agility",
         "reactions": "reactions", "balance": "balance", "shotPower": "shot power", "jumping": "jumping", "stamina": "stamina",
         "strength": "strength", "longShots": "long shots", "aggression": "aggression", "interceptions": "interceptions",
         "positioning": "attacking runs", "vision": "vision", "penalties": "penalties", "composure": "composure",
         "defensiveAwareness": "defensive reading", "standingTackle": "tackling", "slidingTackle": "sliding", "gkDiving": "diving",
         "gkHandling": "handling", "gkKicking": "distribution", "gkPositioning": "positioning", "gkReflexes": "reflexes"}
ROLE_LINE_OF = {"GK": "GK", "DEF": "DEF", "MID": "MID", "WIDE": "WIDE", "ATT": "ATT"}


def role_vector(role: str, attrs: list[str]) -> np.ndarray:
    """A role's signature in shape space: its weights over the line's attributes, centred."""
    w = ROLES[role][1]
    v = np.array([w.get(a, 0.0) for a in attrs], dtype=float)
    return v - v.mean()


def role_fit(shape_z: np.ndarray, attrs: list[str], roles: list[str]) -> np.ndarray:
    """Cosine between each player's standardised shape and each role signature: level-free fit."""
    R = np.stack([role_vector(r, attrs) for r in roles])
    num = shape_z @ R.T
    return num / (np.linalg.norm(shape_z, axis=1, keepdims=True) * np.linalg.norm(R, axis=1) + 1e-9)


# Lines a player may be judged in, from his main position (wide players can be strikers, etc.).
ELIGIBLE = {"GK": ["GK"], "DEF": ["DEF"], "MID": ["MID"], "WIDE": ["WIDE", "ATT", "MID"], "ATT": ["ATT", "WIDE"]}


def role_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Raw suitability (0-99 scale) for every role, NaN where the role isn't in his lines."""
    out = pd.DataFrame(index=df.index)
    styles = df.get("playstyles", pd.Series("", index=df.index)).fillna("") + "," + df.get("playstyles_plus", pd.Series("", index=df.index)).fillna("")
    lines = df.position.map(LINE_OF).fillna("MID")
    alt = df.get("alt_positions", pd.Series("", index=df.index)).fillna("").map(lambda s: {LINE_OF.get(p.strip()) for p in s.split(",") if p.strip()})
    for role, (line, w, ps) in ROLES.items():
        cols = list(w)
        vals = df[cols].astype(float)
        score = (vals * pd.Series(w)).sum(1) / sum(w.values())
        bonus = sum(styles.str.contains(p, regex=False).astype(float) * 1.5 for p in ps)
        ok = lines.map(lambda l: line in ELIGIBLE.get(l, [l])) | alt.map(lambda a: line in a)
        out[role] = (score + bonus).where(ok)
    return out


def shapes(df: pd.DataFrame, attrs: list[str]) -> np.ndarray:
    """Attribute residuals after the overall level, per line: the profile's shape."""
    X = df[attrs].astype(float).to_numpy()
    o = df.overall.astype(float).to_numpy()
    res = np.zeros_like(X)
    for j in range(X.shape[1]):
        ok = ~np.isnan(X[:, j])
        b = np.polyfit(o[ok], X[ok, j], 1)
        res[:, j] = X[:, j] - np.polyval(b, o)
    return np.nan_to_num(res)


def build() -> None:
    p = pd.read_parquet(OUT / "players.parquet")
    p["line"] = p.position.map(LINE_OF).fillna("MID")
    roles = role_scores(p)
    # Percentile among players of the role's line.
    pct = roles.rank(pct=True) * 100
    best = roles.apply(lambda r: r.dropna().sort_values(ascending=False).index[:3].tolist(), axis=1)
    prof = pd.DataFrame({"ea_id": p.ea_id, "best_roles": best})
    for role in ROLES:
        prof[f"role_{role}"] = roles[role].round(1)
        prof[f"pct_{role}"] = pct[role].round(0)

    # Archetypes per line on shape vectors; role fit from the same shapes.
    arche = pd.Series(index=p.index, dtype=object)
    arche_p = pd.Series(index=p.index, dtype=float)
    fit = pd.DataFrame(index=p.index, columns=list(ROLES), dtype=float)
    centroids = []
    for line, g in p.groupby("line"):
        attrs = list(GK_ATTR) if line == "GK" else list(ATTR)
        S = shapes(g, attrs)
        S = (S - S.mean(0)) / (S.std(0) + 1e-9)
        line_roles = [r for r, (l, _, _) in ROLES.items() if l in ELIGIBLE.get(line, [line])]
        fit.loc[g.index, line_roles] = role_fit(S, attrs, line_roles)
        best_k, best_bic, best_m = None, np.inf, None
        for k in range(3, 9):
            m = GaussianMixture(k, covariance_type="diag", random_state=7, n_init=3).fit(S)
            bic = m.bic(S)
            if bic < best_bic:
                best_k, best_bic, best_m = k, bic, m
        lab = best_m.predict(S)
        prob = best_m.predict_proba(S).max(1)
        # Name each archetype by the role signature its centroid matches best (roles of its own line),
        # then by its two most distinctive attributes.
        own_roles = [r for r, (l, _, _) in ROLES.items() if l == line] or line_roles
        used = {}
        for c in np.argsort(-np.bincount(lab, minlength=best_k)):
            mean = best_m.means_[c]
            top = [LABEL.get(attrs[i], attrs[i]) for i in np.argsort(-mean)[:2]]
            members = g.index[lab == c]
            sims = role_fit(mean[None, :], attrs, own_roles)[0]
            role = own_roles[int(np.argmax(sims))]
            used[role] = used.get(role, 0) + 1
            name = f"{role} ({' and '.join(top)})"
            arche.loc[members] = name
            centroids.append({"line": line, "archetype": name, "size": int(len(members)), "top": top,
                              "examples": p.loc[members].nlargest(5, "overall").name.tolist()})
        arche_p.loc[g.index] = prob
    prof["archetype"], prof["archetype_confidence"] = arche.values, arche_p.round(2).values
    for role in ROLES:
        prof[f"fit_{role}"] = fit[role].astype(float).round(3).values
    prof.to_parquet(OUT / "profiles.parquet")
    pd.DataFrame(centroids).to_parquet(OUT / "archetypes.parquet")

    # Reference library: every player's peak season in EA history (84+), as a shape vector.
    fifa_cols = ", ".join(f"any_value({c}) AS {k}" for k, c in {**ATTR, **GK_ATTR}.items())
    hist = duckdb.sql(f"""
        WITH s AS (SELECT player_id, fifa_version, any_value(short_name) AS name, any_value(age) AS age, any_value(overall) AS overall,
                          any_value(player_positions) AS positions, any_value(club_name) AS club, {fifa_cols}
                   FROM read_parquet('{growth2.FIFA}') WHERE fifa_update = 1 GROUP BY 1, 2)
        SELECT * FROM s QUALIFY row_number() OVER (PARTITION BY player_id ORDER BY overall DESC, fifa_version) = 1""").df()
    hist = hist[hist.overall >= 84].copy()
    hist["position"] = hist.positions.str.split(",").str[0].str.strip()
    hist["season"] = (2000 + hist.fifa_version - 1).astype(str) + "/" + (hist.fifa_version).astype(str)
    hist.to_parquet(OUT / "references.parquet")
    print(f"profiles {len(prof):,} | archetypes {len(centroids)} | elite references {len(hist):,}")


# ── Query ────────────────────────────────────────────────────────────────────
class Profiler:
    def __init__(self, players: pd.DataFrame):
        self.players = players.set_index("ea_id")
        self.prof = pd.read_parquet(OUT / "profiles.parquet").set_index("ea_id")
        self.refs = pd.read_parquet(OUT / "references.parquet")
        self.arch = pd.read_parquet(OUT / "archetypes.parquet")

    def roles(self, ea_id: int, k: int = 5) -> list[dict]:
        """His roles ranked by fit (shape against the role's signature, level-free), with the level he'd bring."""
        r = self.prof.loc[ea_id]
        rows = [{"role": role, "line": ROLES[role][0], "fit": float(r[f"fit_{role}"]), "level": float(r[f"role_{role}"]),
                 "percentile": float(r[f"pct_{role}"])}
                for role in ROLES if pd.notna(r.get(f"fit_{role}")) and pd.notna(r[f"role_{role}"])]
        # Fit decides the style, level decides whether he can do it: rank on both.
        return sorted(rows, key=lambda x: -(x["percentile"] + 60 * x["fit"]))[:k]

    def roles_after(self, ea_id: int, face_now: dict, face_later: dict, k: int = 3) -> list[dict]:
        """Role suitability after the projected face-stat changes (each sub-attribute moves with its face stat)."""
        row = self.players.loc[[ea_id]].copy()
        group = {"pace": ["acceleration", "sprintSpeed"], "shooting": ["finishing", "shotPower", "longShots", "volleys", "positioning", "penalties"],
                 "passing": ["shortPassing", "longPassing", "vision", "crossing", "curve", "freeKickAccuracy"],
                 "dribbling": ["dribbling", "ballControl", "agility", "balance", "composure", "reactions"],
                 "defending": ["defensiveAwareness", "standingTackle", "slidingTackle", "interceptions", "headingAccuracy"],
                 "physic": ["strength", "stamina", "jumping", "aggression"]}
        for face, cols in group.items():
            if face in face_now and face in face_later and face_now[face] is not None and face_later[face] is not None:
                delta = face_later[face] - face_now[face]
                for c in cols:
                    row[c] = np.clip(row[c].astype(float) + delta, 1, 99)
        now = role_scores(self.players.loc[[ea_id]]).iloc[0]
        later = role_scores(row).iloc[0]
        mine = [r["role"] for r in self.roles(ea_id, 6)]            # his best-fitting roles
        best = later[mine].dropna().sort_values(ascending=False)
        return [{"role": r, "now": round(float(now[r]), 1), "later": round(float(later[r]), 1)} for r in best.index[:k]]

    def plays_like(self, ea_id: int, k: int = 5) -> dict:
        me = self.players.loc[ea_id]
        line = LINE_OF.get(me.position, "MID")
        attrs = list(GK_ATTR) if line == "GK" else list(ATTR)
        pool = self.players[self.players.position.map(LINE_OF).fillna("MID") == line].reset_index()
        S = shapes(pool, attrs)
        i = int(np.where(pool.ea_id.values == ea_id)[0][0])
        cos = (S @ S[i]) / (np.linalg.norm(S, axis=1) * np.linalg.norm(S[i]) + 1e-9)
        cos[i] = -1
        # Same shape at his level or above: a 62 in League Two can share the shape and still say nothing about how he plays at 80.
        cos[pool.overall.values < me.overall - 4] = -1
        today = pool.assign(sim=cos).nlargest(k, "sim")[["ea_id", "name", "age", "overall", "club_now", "sim"]]
        refs = self.refs[self.refs.position.map(LINE_OF).fillna("MID") == line].copy()
        both = pd.concat([pool[["overall"] + attrs], refs[["overall"] + attrs]], ignore_index=True)
        Sb = shapes(both, attrs)
        mine, theirs = Sb[i], Sb[len(pool):]
        rc = (theirs @ mine) / (np.linalg.norm(theirs, axis=1) * np.linalg.norm(mine) + 1e-9)
        ref = refs.assign(sim=rc).nlargest(k, "sim")[["name", "age", "overall", "club", "season", "sim"]]
        return {"today": today.round(3).to_dict("records"), "references": ref.round(3).to_dict("records")}

    def archetype(self, ea_id: int) -> dict:
        r = self.prof.loc[ea_id]
        a = self.arch[self.arch.archetype == r.archetype]
        return {"name": r.archetype, "confidence": float(r.archetype_confidence),
                "examples": a.examples.iloc[0] if len(a) else [], "size": int(a["size"].iloc[0]) if len(a) else 0}


if __name__ == "__main__":
    build()
