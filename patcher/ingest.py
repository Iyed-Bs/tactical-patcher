"""Build the warehouse: one DuckDB file holding the FIFA ratings, the Wyscout
reference tables and the crosswalks to Transfermarkt's player ids.

Transfermarkt itself is attached read-only at query time (tm.*).
Run:  python -m patcher.ingest
"""
from __future__ import annotations

import json
import re

import duckdb
import pandas as pd

from .link import link, tokens
from .paths import FIFA, TRANSFERMARKT, WAREHOUSE, WYSCOUT


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(WAREHOUSE), read_only=read_only)
    con.execute(f"ATTACH IF NOT EXISTS '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")
    return con


def _wyscout_json(name: str) -> pd.DataFrame:
    with open(WYSCOUT / name, encoding="utf-8") as f:
        return pd.json_normalize(json.load(f))


_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape(s):
    r"""The public Wyscout files keep some accents as literal text ("Konat\u00e9");
    decode them and drop soft hyphens."""
    if not isinstance(s, str):
        return s
    return _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), s).replace("\u00ad", "").strip()


def build() -> None:
    con = duckdb.connect(str(WAREHOUSE))
    con.execute(f"ATTACH '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")

    # FIFA 15-23: one row per player per game version.
    con.execute(f"CREATE OR REPLACE TABLE fifa AS SELECT * FROM read_csv_auto('{FIFA.as_posix()}', sample_size=-1)")

    # Wyscout reference tables.
    wp = _wyscout_json("players.json")
    wp = wp.rename(columns={"wyId": "wy_id", "birthDate": "dob", "firstName": "first_name",
                            "lastName": "last_name", "shortName": "short_name",
                            "currentTeamId": "current_team_id", "role.name": "role"})
    for col in ("first_name", "last_name", "short_name"):
        wp[col] = wp[col].map(_unescape)
    con.register("wp", wp[["wy_id", "first_name", "last_name", "short_name", "dob", "height", "weight",
                           "foot", "role", "current_team_id"]])
    con.execute("CREATE OR REPLACE TABLE wy_players AS SELECT * FROM wp")
    wt = _wyscout_json("teams.json").rename(columns={"wyId": "wy_team_id", "area.name": "country"})
    for col in ("name", "officialName"):
        wt[col] = wt[col].map(_unescape)
    con.register("wt", wt[["wy_team_id", "name", "officialName", "country", "type"]])
    con.execute("CREATE OR REPLACE TABLE wy_teams AS SELECT * FROM wt")

    # Crosswalks to Transfermarkt.
    tm = con.execute("SELECT player_id, date_of_birth::DATE AS dob, name, first_name, last_name FROM tm.players WHERE date_of_birth IS NOT NULL").df()
    tm["tok"] = [tokens(n, f, l) for n, f, l in zip(tm.name, tm.first_name, tm.last_name)]
    tm = tm[["player_id", "dob", "tok"]]

    fifa = con.execute("SELECT DISTINCT player_id AS fifa_id, dob::DATE AS dob, long_name, short_name FROM fifa").df()
    fifa["tok"] = [tokens(a, b) for a, b in zip(fifa.long_name, fifa.short_name)]
    x_fifa = link(tm, fifa[["fifa_id", "dob", "tok"]].drop_duplicates("fifa_id"), "fifa_id")
    con.register("x_fifa", x_fifa)
    con.execute("CREATE OR REPLACE TABLE link_fifa AS SELECT * FROM x_fifa")

    wyp = wp[["wy_id", "dob", "first_name", "last_name", "short_name"]].copy()
    wyp["dob"] = pd.to_datetime(wyp.dob, errors="coerce").dt.date
    wyp["tok"] = [tokens(a, b, c) for a, b, c in zip(wyp.first_name, wyp.last_name, wyp.short_name)]
    tm["dob"] = pd.to_datetime(tm.dob).dt.date
    x_wy = link(tm, wyp.dropna(subset=["dob"])[["wy_id", "dob", "tok"]], "wy_id")
    con.register("x_wy", x_wy)
    con.execute("CREATE OR REPLACE TABLE link_wyscout AS SELECT * FROM x_wy")

    n = lambda q: con.execute(q).fetchone()[0]  # noqa: E731
    print("fifa rows", n("SELECT count(*) FROM fifa"), "| distinct fifa players", n("SELECT count(DISTINCT player_id) FROM fifa"))
    print("linked fifa->tm", n("SELECT count(*) FROM link_fifa"))
    print("wyscout players", n("SELECT count(*) FROM wy_players"), "| linked wyscout->tm", n("SELECT count(*) FROM link_wyscout"))
    con.close()


if __name__ == "__main__":
    build()
