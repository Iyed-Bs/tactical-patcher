"""Identity resolution v2: the same footballer across Football Life, EA and
Transfermarkt, decided on all the evidence the sources share.

Exact-name matching dropped players who were obviously the same person
("Vinícius Júnior" / "Vinícius José Paixão de Oliveira Júnior", one birth
year apart in a game's rounding). This is classic probabilistic record
linkage (Fellegi-Sunter):
  1. block: candidate pairs share the birth year (+-1) and a name token,
     or the birth year and the club;
  2. compare: each pair gets an agreement level on name similarity, birth
     year (exact date when both sides have one), height, stronger foot,
     nationality, club and line;
  3. weigh: each level's weight is log2(m/u), m = how often it occurs among
     sure matches (same full name, birth year, height and club or nation),
     u = how often among candidate pairs that are not;
  4. decide: best candidate per player, one-to-one, above a threshold set
     so the expected false matches stay under 2 %.
Transfermarkt's exact birth dates bridge what the games round off.

Run:  python -m patcher.resolve
"""
from __future__ import annotations

import json
import re

import duckdb
import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from unidecode import unidecode

from .paths import DATA, TRANSFERMARKT
from .sources import fl as flsrc

STOP = {"de", "da", "do", "dos", "das", "del", "van", "von", "der", "den", "le", "la", "el", "al", "bin", "ben", "jr", "junior", "filho", "neto"}
CLUB_STOP = {"fc", "cf", "sc", "ac", "as", "afc", "cd", "rc", "sv", "fk", "nk", "sk", "club", "de", "futbol", "football", "calcio", "sporting",
             "real", "associazione", "sportiva", "societa", "the", "1", "1.", "us", "ssc", "vfl", "vfb", "tsg", "bsc", "if", "ik", "bk", "aik"}
NATION_ALIAS = {"korea republic": "south korea", "republic of korea": "south korea", "korea, south": "south korea",
                "cote d'ivoire": "ivory coast", "turkiye": "turkey", "united states": "usa", "united states of america": "usa",
                "czechia": "czech republic", "china pr": "china", "ir iran": "iran", "republic of ireland": "ireland",
                "cape verde islands": "cape verde", "bosnia-herzegovina": "bosnia and herzegovina", "dr congo": "congo dr",
                "democratic republic of the congo": "congo dr", "north macedonia": "macedonia", "curacao": "curacao"}
LINE = {"GK": "GK", "CB": "DEF", "LB": "DEF", "RB": "DEF", "LWB": "DEF", "RWB": "DEF", "DMF": "MID", "CDM": "MID", "CMF": "MID",
        "CM": "MID", "AMF": "MID", "CAM": "MID", "LMF": "MID", "RMF": "MID", "LM": "MID", "RM": "MID", "LWF": "ATT", "RWF": "ATT",
        "LW": "ATT", "RW": "ATT", "SS": "ATT", "CF": "ATT", "ST": "ATT", "Goalkeeper": "GK", "Defender": "DEF", "Midfield": "MID",
        "Attack": "ATT"}


def norm(s) -> str:
    return " ".join(re.split(r"[^a-z]+", unidecode(str(s or "")).lower())).strip()


def name_tokens(*names) -> set[str]:
    return {t for n in names for t in norm(n).split() if len(t) >= 3 and t not in STOP}


def club_norm(s) -> str:
    return " ".join(t for t in norm(s).split() if t not in CLUB_STOP)


def nation_norm(s) -> str:
    n = norm(s)
    return NATION_ALIAS.get(n, n)


# ── Sources in one schema ────────────────────────────────────────────────────
def football_life() -> pd.DataFrame:
    p = pd.read_parquet(flsrc.OUT / "fl26_players.parquet")
    raw = np.frombuffer(open(DATA / "pes" / "fl26" / "data_s25262b" / "Player.bin", "rb").read(), dtype=np.uint8).reshape(-1, 312)
    bits = np.unpackbits(raw[:, 0x1D:0x1F], axis=1, bitorder="little").astype(np.int64)
    nation_id = pd.Series((bits[:, 0:9] * (1 << np.arange(9))).sum(1), index=raw[:, 8:12].copy().view("<u4").ravel())
    p["nation_id"] = p.fl_id.map(nation_id)
    return pd.DataFrame({"fl_id": p.fl_id, "names": list(zip(p.name, p.shirt_name)), "full": p.name.map(norm),
                         "birth_year": 2026 - p.age, "dob": pd.NaT, "height": p.height.astype(float),
                         "foot": np.where(p.left_footed == 1, "left", "right"), "nation_id": p.nation_id,
                         "club": p.team.map(club_norm), "line": p.registered_position.map(LINE)})


def ea() -> pd.DataFrame:
    rows = []
    for ed in ("fc27", "fc26"):
        e = pd.read_parquet(DATA / f"ea_{ed}.parquet")
        e = e[e.gender == "Men's Football"]
        rows.append(pd.DataFrame({"ea_id": e.ea_id, "names": list(zip(e.first_name, e.last_name, e.common_name)),
                                  "dob": pd.to_datetime(e.dob, errors="coerce"), "height": pd.to_numeric(e.height, errors="coerce"),
                                  "foot": e.foot, "nation": e.nationality, "club": e.team, "pos": e.position, "src": ed}))
    hist = duckdb.sql(f"""SELECT player_id AS ea_id, arg_max(short_name, fifa_version) s, arg_max(long_name, fifa_version) l,
                                 any_value(dob) dob, arg_max(height_cm, fifa_version) h, any_value(preferred_foot) foot,
                                 arg_max(nationality_name, fifa_version) nation, arg_max(club_name, fifa_version) club,
                                 arg_max(player_positions, fifa_version) pos
                          FROM read_parquet('{(DATA / 'fifa_all_updates.parquet').as_posix()}') GROUP BY 1""").df()
    rows.append(pd.DataFrame({"ea_id": hist.ea_id, "names": list(zip(hist.s, hist.l)), "dob": pd.to_datetime(hist.dob, errors="coerce"),
                              "height": hist.h.astype(float), "foot": hist.foot.str.lower(), "nation": hist.nation, "club": hist.club,
                              "pos": hist.pos.str.split(",").str[0].str.strip(), "src": "fifa"}))
    df = pd.concat(rows, ignore_index=True)
    # One row per id: the most recent source wins for club and body, names are pooled.
    df["rank"] = df.src.map({"fc27": 0, "fc26": 1, "fifa": 2})
    df = df.sort_values("rank")
    agg = df.groupby("ea_id").agg(names=("names", lambda ns: tuple(x for n in ns for x in n if isinstance(x, str))),
                                  dob=("dob", "first"), height=("height", "first"), foot=("foot", "first"), nation=("nation", "first"),
                                  club=("club", "first"), pos=("pos", "first")).reset_index()
    return pd.DataFrame({"ea_id": agg.ea_id, "names": agg.names, "full": [norm(n[0] + " " + n[1]) if len(n) > 1 else norm(n[0]) for n in agg.names],
                         "birth_year": agg.dob.dt.year, "dob": agg.dob, "height": agg.height, "foot": agg.foot,
                         "nation": agg.nation.map(nation_norm), "club": agg.club.map(club_norm), "line": agg.pos.map(LINE)})


def transfermarkt() -> pd.DataFrame:
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    t = con.execute("""SELECT player_id AS tm_id, name, first_name, last_name, date_of_birth::DATE AS dob, height_in_cm, foot,
                              country_of_citizenship, current_club_name, position FROM tm.players""").df()
    return pd.DataFrame({"tm_id": t.tm_id, "names": list(zip(t.name, t.first_name, t.last_name)), "full": t.name.map(norm),
                         "birth_year": pd.to_datetime(t.dob).dt.year, "dob": pd.to_datetime(t.dob), "height": t.height_in_cm.astype(float),
                         "foot": t.foot.str.lower().replace({"both": None}), "nation": t.country_of_citizenship.map(nation_norm),
                         "club": t.current_club_name.map(club_norm), "line": t.position.map(LINE)})


# ── Blocking, comparison, weights ────────────────────────────────────────────
def candidates(a: pd.DataFrame, b: pd.DataFrame, ka: str, kb: str, max_block: int = 400) -> pd.DataFrame:
    """Pairs sharing birth year (+-1) and a name token, or birth year (+-1) and a club."""
    def tok_table(df, key):
        t = df[[key, "birth_year", "names"]].dropna(subset=["birth_year"]).copy()
        t["tok"] = [list(name_tokens(*n)) for n in t.names]
        return t.drop(columns="names").explode("tok").dropna(subset=["tok"])
    ta, tb = tok_table(a, ka), tok_table(b, kb)
    tb = tb[tb.groupby(["tok", "birth_year"])[kb].transform("size") <= max_block]   # skip over-common tokens
    shifted = pd.concat([ta.assign(birth_year=ta.birth_year + d) for d in (-1, 0, 1)])
    pairs = shifted.merge(tb, on=["tok", "birth_year"])[[ka, kb]]
    ca = a[[ka, "birth_year", "club"]].dropna(); ca = ca[ca.club != ""]
    cb = b[[kb, "birth_year", "club"]].dropna(); cb = cb[cb.club != ""]
    by_club = pd.concat([ca.assign(birth_year=ca.birth_year + d) for d in (-1, 0, 1)]).merge(cb, on=["club", "birth_year"])[[ka, kb]]
    return pd.concat([pairs, by_club]).drop_duplicates()


def compare(pairs: pd.DataFrame, a: pd.DataFrame, b: pd.DataFrame, ka: str, kb: str) -> pd.DataFrame:
    x = pairs.merge(a.add_suffix("_a").rename(columns={f"{ka}_a": ka}), on=ka).merge(b.add_suffix("_b").rename(columns={f"{kb}_b": kb}), on=kb)
    def best_ratio(na, nb):
        fa = {norm(s) for s in na if isinstance(s, str) and s}
        fb = {norm(s) for s in nb if isinstance(s, str) and s}
        return max((fuzz.token_set_ratio(p, q) for p in fa for q in fb), default=0)
    ratio = np.array([best_ratio(na, nb) for na, nb in zip(x.names_a, x.names_b)])
    x["name_lvl"] = np.select([x.full_a == x.full_b, ratio >= 95, ratio >= 85, ratio >= 70], [4, 3, 2, 1], 0)
    both_dob = x.dob_a.notna() & x.dob_b.notna()
    x["dob_lvl"] = np.select([both_dob & (x.dob_a == x.dob_b), both_dob, x.birth_year_a == x.birth_year_b], [3, 0, 2], 1)
    hd = (x.height_a - x.height_b).abs()
    x["height_lvl"] = np.select([hd.isna(), hd <= 1, hd <= 3], [-1, 2, 1], 0)
    x["foot_lvl"] = np.select([x.foot_a.isna() | x.foot_b.isna(), x.foot_a == x.foot_b], [-1, 1], 0)
    x["nation_lvl"] = np.select([x.nation_a.isna() | x.nation_b.isna() | (x.nation_a == "") | (x.nation_b == ""), x.nation_a == x.nation_b], [-1, 1], 0)
    clubsim = np.array([fuzz.token_set_ratio(p or "", q or "") if p and q else -1 for p, q in zip(x.club_a, x.club_b)])
    x["club_lvl"] = np.select([clubsim < 0, clubsim >= 90], [-1, 1], 0)
    x["line_lvl"] = np.select([x.line_a.isna() | x.line_b.isna(), x.line_a == x.line_b], [-1, 1], 0)
    if "ovr_a" in x and "ovr_b" in x:
        # The player himself as evidence: does his Football Life ability profile predict his EA rating?
        gap = (x.ovr_a - x.ovr_b).abs()
        x["rating_lvl"] = np.select([gap.isna(), gap <= 3, gap <= 6], [-1, 2, 1], 0)
    else:
        x["rating_lvl"] = -1
    return x


LEVELS = ["name_lvl", "dob_lvl", "height_lvl", "foot_lvl", "nation_lvl", "club_lvl", "line_lvl", "rating_lvl"]


def fit_em(x: pd.DataFrame, sure: pd.Series, iters: int = 60) -> tuple[dict, dict, float]:
    """Fellegi-Sunter by EM: match (m) and non-match (u) level frequencies estimated jointly
    over all candidate pairs, assuming the comparisons are independent given the true status.
    The sure pairs only seed the first guess. Missing comparisons (-1) carry no evidence."""
    g = np.where(sure, 0.95, 0.05)
    for _ in range(iters):
        m, u = {}, {}
        for col in LEVELS:
            lv = x[col].values
            ok = lv >= 0
            m[col] = {l: (g[ok & (lv == l)].sum() + 1e-3) / (g[ok].sum() + 1e-3) for l in np.unique(lv[ok])}
            u[col] = {l: ((1 - g)[ok & (lv == l)].sum() + 1e-3) / ((1 - g)[ok].sum() + 1e-3) for l in np.unique(lv[ok])}
        prior = float(g.mean())
        llr = np.zeros(len(x))
        for col in LEVELS:
            lv = x[col].values
            w = np.array([np.log(m[col].get(l, 1e-6) / u[col].get(l, 1e-6)) if l >= 0 else 0.0 for l in lv])
            llr += w
        logit = llr + np.log(prior / (1 - prior))
        g_new = 1 / (1 + np.exp(-np.clip(logit, -50, 50)))
        if np.abs(g_new - g).max() < 1e-4:
            g = g_new
            break
        g = g_new
    weights = {col: {int(l): float(np.log2(m[col][l] / u[col].get(l, 1e-6))) for l in m[col]} for col in LEVELS}
    for col in LEVELS:
        weights[col][-1] = 0.0
    return weights, {"prior": prior}, g


def score(x: pd.DataFrame, w: dict) -> pd.Series:
    return sum(x[c].map(w[c]).fillna(0) for c in LEVELS)


def assign(x: pd.DataFrame, ka: str, kb: str, threshold: float) -> pd.DataFrame:
    """Greedy one-to-one by score, above the threshold."""
    x = x[x.score >= threshold].sort_values("score", ascending=False)
    used_a, used_b, keep = set(), set(), []
    for i, r in zip(x.index, x[[ka, kb]].itertuples(index=False)):
        if r[0] in used_a or r[1] in used_b:
            continue
        used_a.add(r[0]); used_b.add(r[1]); keep.append(i)
    return x.loc[keep]


def link_sources(a: pd.DataFrame, b: pd.DataFrame, ka: str, kb: str, label: str) -> tuple[pd.DataFrame, dict]:
    pairs = candidates(a, b, ka, kb)
    x = compare(pairs, a, b, ka, kb)
    sure = (x.name_lvl == 4) & (x.dob_lvl >= 2) & (x.height_lvl == 2) & ((x.club_lvl == 1) | (x.nation_lvl == 1))
    w, info, post = fit_em(x, sure)
    x["score"], x["p_match"] = score(x, w), post
    threshold = 0.98                                                      # posterior match probability
    linked = assign(x.assign(score=x.p_match), ka, kb, threshold)
    linked["score"] = x.loc[linked.index, "score"]
    linked["p_match"] = x.loc[linked.index, "p_match"]
    stats = {"pairs": len(x), "sure": int(sure.sum()), "threshold": threshold, "linked": len(linked), **info,
             "expected_false_matches": float((1 - linked.p_match).sum())}
    print(f"{label}: {len(x):,} candidate pairs, linked {len(linked):,} at p >= {threshold}, "
          f"expected false matches {stats['expected_false_matches']:.0f} ({stats['expected_false_matches'] / max(len(linked), 1):.2%})")
    return linked[[ka, kb, "score", "p_match"] + LEVELS], {"weights": w, **stats}


def fl_to_ea_scale(fl_players: pd.DataFrame, e: pd.DataFrame, seed_links: pd.DataFrame) -> pd.Series:
    """Each FL player's abilities on EA's overall scale (ridge per FL position, fitted on seed links)."""
    from sklearn.linear_model import RidgeCV
    ab = flsrc.ABILITIES
    t = seed_links.merge(fl_players[["fl_id", "registered_position"] + ab], on="fl_id").merge(e[["ea_id", "ovr"]], on="ea_id").dropna(subset=["ovr"])
    out = pd.Series(np.nan, index=fl_players.fl_id.values)
    for pos, g in t.groupby("registered_position"):
        if len(g) < 100:
            continue
        m = RidgeCV(alphas=[1, 10]).fit(g[ab], g.ovr)
        k = (fl_players.registered_position == pos).values
        out.iloc[np.where(k)[0]] = m.predict(fl_players.loc[k, ab])
    return out


def build() -> pd.DataFrame:
    fl, e, t = football_life(), ea(), transfermarkt()
    # Football Life nation ids -> names, by majority vote over exact-name links to EA.
    fl_nat = fl.merge(e[["full", "birth_year", "nation"]], on=["full", "birth_year"]).dropna(subset=["nation"])
    nat_map = fl_nat.groupby("nation_id").nation.agg(lambda s: s.value_counts().index[0])
    fl["nation"] = fl.nation_id.map(nat_map)
    # Ratings on one scale for the profile comparison: EA's FC 26 (the season FL26 depicts), else FC 27.
    fc = {ed: pd.read_parquet(DATA / f"ea_{ed}.parquet").set_index("ea_id").overall for ed in ("fc26", "fc27")}
    e["ovr"] = e.ea_id.map(fc["fc26"]).fillna(e.ea_id.map(fc["fc27"]))
    fl_players = pd.read_parquet(flsrc.OUT / "fl26_players.parquet")
    seed = fl.merge(e[["ea_id", "full", "birth_year"]], on=["full", "birth_year"])[["fl_id", "ea_id"]].drop_duplicates("fl_id", keep=False)
    fl["ovr"] = fl.fl_id.map(fl_to_ea_scale(fl_players, e, seed))

    fl_ea, s1 = link_sources(fl, e, "fl_id", "ea_id", "FL <-> EA")
    fl_tm, s2 = link_sources(fl.drop(columns="ovr"), t, "fl_id", "tm_id", "FL <-> Transfermarkt")
    ea_tm, s3 = link_sources(e.drop(columns="ovr"), t, "ea_id", "tm_id", "EA <-> Transfermarkt")
    # EA <-> Transfermarkt: keep v1's exact-birth-date links wherever they don't contradict v2.
    v1 = pd.read_parquet(DATA / "identity.parquet")[["ea_id", "tm_id"]].dropna().astype({"tm_id": "int64"})
    v1 = v1[~v1.ea_id.isin(ea_tm.ea_id) & ~v1.tm_id.isin(ea_tm.tm_id)]
    ea_tm_all = pd.concat([ea_tm[["ea_id", "tm_id"]], v1], ignore_index=True)
    # FL <-> EA: direct links, plus the ones that chain through Transfermarkt.
    chain = fl_tm[["fl_id", "tm_id", "p_match"]].merge(ea_tm_all, on="tm_id")[["fl_id", "ea_id", "p_match"]]
    chain = chain[~chain.fl_id.isin(fl_ea.fl_id) & ~chain.ea_id.isin(fl_ea.ea_id)]
    chain = chain.sort_values("p_match", ascending=False).drop_duplicates("fl_id").drop_duplicates("ea_id")
    fl_ea_all = pd.concat([fl_ea[["fl_id", "ea_id"]].assign(how="direct"), chain[["fl_id", "ea_id"]].assign(how="via Transfermarkt")], ignore_index=True)

    ident = e[["ea_id"]].merge(ea_tm_all, on="ea_id", how="left").merge(fl_ea_all, on="ea_id", how="left")
    extra = fl_tm[~fl_tm.fl_id.isin(ident.fl_id)][["fl_id", "tm_id"]]
    ident = pd.concat([ident, extra], ignore_index=True)
    ident = pd.concat([ident, fl[~fl.fl_id.isin(ident.fl_id)][["fl_id"]]], ignore_index=True)
    for c in ("tm_id", "fl_id"):
        ident[c] = ident[c].astype("Int64")
    ident.to_parquet(DATA / "identity_v2.parquet")
    stats = {k: {**v, "weights": {c: {str(l): w for l, w in lv.items()} for c, lv in v["weights"].items()}}
             for k, v in {"fl_ea": s1, "fl_tm": s2, "ea_tm": s3}.items()}
    stats["fl_ea_via_transfermarkt"] = int(len(chain)); stats["ea_tm_kept_from_v1"] = int(len(v1))
    (DATA / "identity_v2_stats.json").write_text(json.dumps(stats, indent=1))
    fl_ea.to_parquet(DATA / "link_fl_ea_v2.parquet"); fl_tm.to_parquet(DATA / "link_fl_tm_v2.parquet"); ea_tm.to_parquet(DATA / "link_ea_tm_v2.parquet")
    return ident


if __name__ == "__main__":
    i = build()
    print(f"{len(i):,} people | FL with EA {(i.fl_id.notna() & i.ea_id.notna()).sum():,} | FL with Transfermarkt "
          f"{(i.fl_id.notna() & i.tm_id.notna()).sum():,} | EA with Transfermarkt {(i.ea_id.notna() & i.tm_id.notna()).sum():,}")
