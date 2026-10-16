"""Build one row per new GitHub account: what it did in its first week, and whether it came back.

    python src/build.py

Reads the GH Archive days in data/raw (one Parquet file per day) and writes
data/newcomers.parquet. Takes about a minute.

Who counts as new: an account registered in one of four weeks in June 2025. GH
Archive has no sign-up date, but GitHub hands out account ids in order, so each
sign-up week is a range of ids (src/signup_weeks.csv, found by checking ids
against the GitHub API).
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WEEKS = ROOT / "src" / "signup_weeks.csv"
OUT = ROOT / "data" / "newcomers.parquet"

FIRST_DAY, LAST_DAY = "2025-06-02", "2025-09-05"   # every day the analysis reads

# The same mapping as GitHub's own product areas. Event types that are not a
# feature someone chooses to use (PublicEvent: a repo was made public) are left out.
FEATURE = """
CASE event_type
    WHEN 'PushEvent'                     THEN 'Push code'
    WHEN 'PullRequestEvent'              THEN 'Pull requests'
    WHEN 'PullRequestReviewEvent'        THEN 'Code review'
    WHEN 'PullRequestReviewCommentEvent' THEN 'Code review'
    WHEN 'IssuesEvent'                   THEN 'Issues'
    WHEN 'IssueCommentEvent'             THEN 'Comments'
    WHEN 'CommitCommentEvent'            THEN 'Comments'
    WHEN 'WatchEvent'                    THEN 'Stars'
    WHEN 'ForkEvent'                     THEN 'Forks'
    WHEN 'CreateEvent'                   THEN 'Branches and repos'
    WHEN 'DeleteEvent'                   THEN 'Branches and repos'
    WHEN 'ReleaseEvent'                  THEN 'Releases'
    WHEN 'GollumEvent'                   THEN 'Wiki'
    WHEN 'MemberEvent'                   THEN 'Collaborators'
END
"""


def main():
    days = sorted(p for p in RAW.glob("events_2025-*.parquet")
                  if FIRST_DAY <= p.stem.removeprefix("events_") <= LAST_DAY)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")          # a day is a UTC day, as GH Archive publishes it
    con.execute("SET memory_limit = '4GB'")
    con.execute(f"CREATE TABLE weeks AS SELECT * FROM read_csv('{WEEKS}')")
    lo, hi = con.sql("SELECT min(first_id), max(last_id) FROM weeks").fetchone()
    con.execute(f"""
        CREATE TABLE events AS
        SELECT w.signup_week, e.actor_id, e.actor_login, e.created_at::DATE AS day,
               {FEATURE} AS feature
        FROM read_parquet({[str(d) for d in days]}, union_by_name = true) AS e
        JOIN weeks AS w ON e.actor_id BETWEEN w.first_id AND w.last_id
        WHERE e.actor_id BETWEEN {lo} AND {hi}
    """)
    con.execute(f"""
        COPY (
            WITH first_action AS (
                SELECT actor_id, signup_week, min(day) AS day0
                FROM events GROUP BY ALL
            ),
            newcomers AS (
                -- did something public within two weeks of the sign-up week starting
                SELECT * FROM first_action WHERE day0 <= signup_week + 13
            ),
            busiest AS (
                -- most events on any one day of week one; people rarely pass 200
                SELECT d.actor_id, max(d.events) AS busiest_day
                FROM (SELECT actor_id, day, count(*) AS events FROM events GROUP BY ALL) AS d
                JOIN newcomers AS n USING (actor_id)
                WHERE d.day <= n.day0 + 6
                GROUP BY ALL
            )
            SELECT
                n.actor_id,
                n.signup_week,
                n.day0                                                          AS first_action,
                bool_or(e.actor_login LIKE '%[bot]')                            AS declared_bot,
                count(*) FILTER (WHERE e.day <= n.day0 + 6)                     AS events_week1,
                any_value(b.busiest_day)                                        AS busiest_day_week1,
                count(DISTINCT e.day) FILTER (WHERE e.day <= n.day0 + 6)        AS active_days_week1,
                count(DISTINCT e.feature) FILTER (WHERE e.day <= n.day0 + 6)    AS features_week1,
                list(DISTINCT e.feature ORDER BY e.feature)
                    FILTER (WHERE e.day <= n.day0 + 6 AND e.feature IS NOT NULL) AS feature_list,
                bool_or(e.day BETWEEN n.day0 + 28 AND n.day0 + 55)              AS came_back
            FROM newcomers AS n
            JOIN events AS e USING (actor_id)
            LEFT JOIN busiest AS b USING (actor_id)
            GROUP BY ALL
        ) TO '{OUT}' (FORMAT parquet)
    """)
    n, back = con.sql(f"SELECT count(*), avg(came_back::INT) FROM '{OUT}'").fetchone()
    print(f"wrote {OUT.name}: {n:,} new accounts, {back:.1%} came back in days 28-55")


if __name__ == "__main__":
    main()
