"""Player states: a player as he stood on a given day, with everything known
up to that day and nothing after.

Two kinds of rows in the `states` table:
- history: players born 1995-1999 at their 18th, 19th, 20th and 21st
  birthdays, with outcomes at 23-26.5 (what the models learn from);
- current: every player born mid-2005 to mid-2009 as of 1 July 2026, the
  promises the scored promises (no outcome yet).

Features follow the research loops (dossier §13): minutes and their share of
the team's time, minutes weighted by league quality, team strength, the
managers' youth trust, debut age, output, body and birth data, and the
crowd's valuation and its trajectory (kept separate so models can be built
with or without it).
Run:  python -m patcher.features
"""
from __future__ import annotations

import duckdb

from .paths import TRANSFERMARKT, WAREHOUSE

AS_OF = "2026-07-01"
BIG5 = "('GB1','ES1','IT1','L1','FR1')"
EUROPE = "('CL','EL','UCOL','CLQ','ELQ','ECLQ')"


def build() -> None:
    con = duckdb.connect(str(WAREHOUSE))
    con.execute(f"ATTACH IF NOT EXISTS '{TRANSFERMARKT.as_posix()}' AS tm (READ_ONLY)")

    # Every appearance of players born 1995-2009, with its context.
    con.execute("""
    CREATE OR REPLACE TABLE app_ctx AS
    WITH a AS (
      SELECT a.player_id, a.game_id, a.date, a.player_club_id AS club_id, a.competition_id,
             a.minutes_played AS mins, a.goals, a.assists, p.date_of_birth::DATE AS dob,
             date_diff('day', p.date_of_birth, a.date) / 365.25 AS age,
             c.type AS comp_type, g.season, cg.own_manager_name AS manager, cg.own_position, c.total_clubs
      FROM tm.appearances a
      JOIN tm.players p USING (player_id)
      JOIN tm.games g ON g.game_id = a.game_id
      LEFT JOIN tm.competitions c ON c.competition_id = a.competition_id
      LEFT JOIN tm.club_games cg ON cg.game_id = a.game_id AND cg.club_id = a.player_club_id
      WHERE p.date_of_birth IS NOT NULL
    )
    SELECT a.*, v.market_value_in_eur AS mv_then
    FROM a ASOF LEFT JOIN tm.player_valuations v ON a.player_id = v.player_id AND a.date >= v.date
    """)
    con.execute("""
    CREATE OR REPLACE TABLE league_quality AS
    SELECT competition_id, season, avg(mv_then) AS lq FROM app_ctx
    WHERE comp_type = 'domestic_league' AND mv_then IS NOT NULL GROUP BY ALL
    """)
    con.execute("""
    CREATE OR REPLACE TABLE team_games AS
    SELECT cg.club_id, g.season, g.competition_id, count(*) AS n_games
    FROM tm.club_games cg JOIN tm.games g USING (game_id) JOIN tm.competitions c ON c.competition_id = g.competition_id
    WHERE c.type = 'domestic_league' GROUP BY ALL
    """)
    con.execute("""
    CREATE OR REPLACE TABLE manager_trust AS
    SELECT manager, sum(mins) AS all_min, sum(CASE WHEN age < 21 THEN mins ELSE 0 END) AS u21_min,
           count(DISTINCT game_id) AS games
    FROM app_ctx WHERE comp_type = 'domestic_league' AND manager IS NOT NULL GROUP BY manager
    """)

    # The rows to describe: (player, cutoff date).
    con.execute(f"""
    CREATE OR REPLACE TABLE targets AS
    SELECT player_id, date_of_birth::DATE AS dob, c AS cutoff_age,
           (date_of_birth + to_years(c))::DATE AS cutoff_date, 'history' AS kind
    FROM tm.players, (SELECT unnest([18, 19, 20, 21]) AS c)
    WHERE date_of_birth BETWEEN DATE '1995-01-01' AND DATE '1999-12-31'
    UNION ALL
    SELECT player_id, date_of_birth::DATE, date_diff('day', date_of_birth, DATE '{AS_OF}') / 365.25,
           DATE '{AS_OF}', 'current'
    FROM tm.players
    WHERE date_of_birth BETWEEN DATE '2005-07-01' AND DATE '2009-06-30'
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE states AS
    WITH w AS (
      SELECT t.player_id, t.cutoff_date, t.cutoff_age, a.*, lq.lq, tg.n_games
      FROM targets t
      JOIN app_ctx a ON a.player_id = t.player_id AND a.date < t.cutoff_date AND a.age >= 15
      LEFT JOIN league_quality lq ON lq.competition_id = a.competition_id AND lq.season = a.season
      LEFT JOIN team_games tg ON tg.club_id = a.club_id AND tg.season = a.season AND tg.competition_id = a.competition_id
    ),
    season_share AS (
      SELECT player_id, cutoff_date, season, competition_id, sum(mins) / max(n_games * 90.0) AS share
      FROM w WHERE comp_type = 'domestic_league' AND n_games > 0 GROUP BY ALL
    ),
    own AS (
      SELECT player_id, manager, sum(mins) AS all_own, sum(CASE WHEN age < 21 THEN mins ELSE 0 END) AS u21_own
      FROM app_ctx WHERE comp_type = 'domestic_league' GROUP BY ALL
    ),
    mgr AS (
      SELECT w.player_id, w.cutoff_date,
             sum(w.mins * (m.u21_min - o.u21_own) / nullif(m.all_min - o.all_own, 0)) / nullif(sum(w.mins), 0) AS mgr_trust,
             count(DISTINCT w.manager) AS n_managers
      FROM w JOIN manager_trust m USING (manager) JOIN own o ON o.player_id = w.player_id AND o.manager = w.manager
      WHERE w.comp_type = 'domestic_league' GROUP BY ALL
    ),
    agg AS (
      SELECT player_id, cutoff_date,
        min(age) AS debut_age,
        sum(mins) AS min_total,
        sum(CASE WHEN comp_type = 'domestic_league' THEN mins ELSE 0 END) AS min_league,
        sum(CASE WHEN competition_id IN {BIG5} THEN mins ELSE 0 END) AS min_big5,
        sum(CASE WHEN competition_id IN {EUROPE} THEN mins ELSE 0 END) AS min_europe,
        sum(CASE WHEN date >= cutoff_date - INTERVAL 1 YEAR THEN mins ELSE 0 END) AS min_last_year,
        sum(CASE WHEN date >= cutoff_date - INTERVAL 2 YEAR AND date < cutoff_date - INTERVAL 1 YEAR THEN mins ELSE 0 END) AS min_prev_year,
        sum(mins * lq) / nullif(sum(CASE WHEN lq IS NOT NULL THEN mins END), 0) AS league_quality,
        sum(mins * lq) / 1e9 AS quality_minutes,
        avg(CASE WHEN own_position IS NOT NULL AND total_clubs > 1 THEN 1 - (own_position - 1) / (total_clubs - 1.0) END) AS team_strength,
        (sum(goals) + sum(assists)) * 90.0 / nullif(sum(mins), 0) AS ga_per90,
        count(DISTINCT club_id) AS n_clubs,
        max(CASE WHEN competition_id IN {BIG5} THEN 1 ELSE 0 END) AS played_big5,
        arg_max(club_id, date) AS last_club_id,
        arg_max(competition_id, CASE WHEN comp_type = 'domestic_league' THEN date END) AS last_league
      FROM w GROUP BY ALL
    ),
    mv AS (
      SELECT t.player_id, t.cutoff_date,
        arg_max(v.market_value_in_eur, v.date) FILTER (WHERE v.date < t.cutoff_date) AS mv_now,
        arg_max(v.market_value_in_eur, v.date) FILTER (WHERE v.date < t.cutoff_date - INTERVAL 2 YEAR) AS mv_2y_before,
        max(v.market_value_in_eur) FILTER (WHERE v.date >= t.dob + INTERVAL 23 YEAR AND v.date < t.dob + INTERVAL 318 MONTH) AS out_peak_mv
      FROM targets t JOIN tm.player_valuations v USING (player_id) GROUP BY ALL
    ),
    outcome AS (
      SELECT t.player_id, t.cutoff_date,
        sum(CASE WHEN a.competition_id IN {BIG5} THEN a.mins ELSE 0 END) AS out_min_big5,
        sum(CASE WHEN a.comp_type = 'domestic_league' THEN a.mins ELSE 0 END) AS out_min_league
      FROM targets t JOIN app_ctx a ON a.player_id = t.player_id AND a.age >= 23 AND a.age < 26.5
      WHERE t.kind = 'history' GROUP BY ALL
    )
    SELECT t.player_id, t.kind, t.dob, t.cutoff_age, t.cutoff_date,
           p.name, p.position, p.sub_position, p.foot, p.height_in_cm, p.country_of_citizenship,
           p.current_club_name, p.current_club_domestic_competition_id AS current_league,
           agg.* EXCLUDE (player_id, cutoff_date),
           s.max_share, s.mean_share, mgr.mgr_trust, mgr.n_managers,
           mv.mv_now, mv.mv_2y_before, mv.out_peak_mv,
           o.out_min_big5, o.out_min_league
    FROM targets t
    JOIN agg USING (player_id, cutoff_date)
    JOIN tm.players p USING (player_id)
    LEFT JOIN (SELECT player_id, cutoff_date, max(share) AS max_share, avg(share) AS mean_share
               FROM season_share GROUP BY ALL) s USING (player_id, cutoff_date)
    LEFT JOIN mgr USING (player_id, cutoff_date)
    LEFT JOIN mv USING (player_id, cutoff_date)
    LEFT JOIN outcome o USING (player_id, cutoff_date)
    """)
    for kind, n in con.execute("SELECT kind, count(*) FROM states GROUP BY kind").fetchall():
        print(kind, n)
    print(con.execute("SELECT cutoff_age, count(*) FROM states WHERE kind='history' GROUP BY 1 ORDER BY 1").fetchall())
    con.close()


if __name__ == "__main__":
    build()
