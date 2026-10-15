"""One key per footballer across every source.

EA's id is the hub (sofifa's FIFA history uses the same id, so FIFA 15-23
and FC 26-27 join on it directly). Transfermarkt joins on the exact birth
date and name tokens; Football Life only stores a birth year (2026 - age),
so it joins on the year with a stricter name match; Wyscout comes through
its existing Transfermarkt link. Ties are dropped, never guessed.

Run:  python -m patcher.identity
"""
from __future__ import annotations

import duckdb
import pandas as pd

from .link import link, tokens
from .paths import DATA, TRANSFERMARKT, WAREHOUSE


def ea_people() -> pd.DataFrame:
    """Every EA/FIFA player id with names and birth date (FC 27 first, then FC 26, then FIFA 15-23)."""
    frames = []
    for ed in ("fc27", "fc26"):
        e = pd.read_parquet(DATA / f"ea_{ed}.parquet")
        e = e[e.gender == "Men's Football"]
        frames.append(pd.DataFrame({"ea_id": e.ea_id, "dob": pd.to_datetime(e.dob, errors="coerce").dt.date,
                                    "names": list(zip(e.first_name, e.last_name, e.common_name))}))
    hist = duckdb.sql(f"""SELECT player_id AS ea_id, any_value(dob) AS dob, any_value(short_name) AS s, any_value(long_name) AS l
                          FROM read_parquet('{(DATA / 'fifa_all_updates.parquet').as_posix()}') GROUP BY 1""").df()
    frames.append(pd.DataFrame({"ea_id": hist.ea_id, "dob": pd.to_datetime(hist.dob, errors="coerce").dt.date,
                                "names": list(zip(hist.s, hist.l))}))
    df = pd.concat(frames, ignore_index=True)
    df["tok"] = [tokens(*n) for n in df.names]
    # One row per id, union of every spelling seen.
    df = df.groupby("ea_id").agg(dob=("dob", "first"), tok=("tok", lambda ts: frozenset().union(*ts))).reset_index()
    df["birth_year"] = pd.to_datetime(df.dob).dt.year
    return df


def build() -> pd.DataFrame:
    ea = ea_people()
    con = duckdb.connect()
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    tm = con.execute("SELECT player_id, date_of_birth::DATE AS dob, name, first_name, last_name FROM tm.players WHERE date_of_birth IS NOT NULL").df()
    tm["tok"] = [tokens(a, b, c) for a, b, c in zip(tm.name, tm.first_name, tm.last_name)]
    tm["dob"] = pd.to_datetime(tm.dob).dt.date
    ea_tm = link(tm[["player_id", "dob", "tok"]], ea.dropna(subset=["dob"])[["ea_id", "dob", "tok"]], "ea_id")
    ea_tm = ea_tm.rename(columns={"player_id": "tm_id", "score": "score_tm"})

    fl = pd.read_parquet(DATA / "fl" / "fl26_players.parquet")
    fl["birth_year"] = 2026 - fl.age
    fl["tok"] = [tokens(a, b) for a, b in zip(fl.name, fl.shirt_name)]
    ea_fl = link(ea.dropna(subset=["birth_year"]).rename(columns={"ea_id": "player_id"})[["player_id", "birth_year", "tok"]],
                 fl[["fl_id", "birth_year", "tok"]], "fl_id", on="birth_year", min_overlap=1, min_score=0.34)
    ea_fl = ea_fl.rename(columns={"player_id": "ea_id", "score": "score_fl"})

    wy = duckdb.connect(str(WAREHOUSE), read_only=True).execute("SELECT player_id AS tm_id, wy_id FROM link_wyscout").df()

    ident = ea[["ea_id", "dob", "birth_year"]].merge(ea_tm, on="ea_id", how="left").merge(ea_fl, on="ea_id", how="left")
    ident = ident.merge(wy, on="tm_id", how="left")
    # Football Life players with no EA counterpart still get a row of their own.
    orphans = fl[~fl.fl_id.isin(ident.fl_id)][["fl_id", "birth_year"]]
    ident = pd.concat([ident, orphans], ignore_index=True)
    for c in ("tm_id", "fl_id", "wy_id"):
        ident[c] = ident[c].astype("Int64")
    ident.to_parquet(DATA / "identity.parquet")
    return ident


if __name__ == "__main__":
    i = build()
    print(f"{len(i):,} people | EA {i.ea_id.notna().sum():,} | Transfermarkt {i.tm_id.notna().sum():,} | "
          f"Football Life {i.fl_id.notna().sum():,} (with EA {(i.fl_id.notna() & i.ea_id.notna()).sum():,}) | Wyscout {i.wy_id.notna().sum():,}")
