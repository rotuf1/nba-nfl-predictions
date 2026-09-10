"""
Elo rating engine shared by NBA and NFL. See MODEL.md for the exact formula and constants.

The SQLite database (team_ratings.db, path given by caller) holds three tables:

  teams(league, team_id, name, rating)         -- current rating per team
  games(game_id, league, season, date, home_team, away_team,
        home_score, away_score, home_rating_pre, away_rating_pre, processed_at)
        -- one row per game that has been replayed into the ratings, used both as a
        -- log and to make replays idempotent (a game_id is only ever applied once).
  meta(key, value)                              -- small key/value store, e.g. last season seen
"""
import sqlite3
from datetime import datetime

HOME_ADV = {"NBA": 100, "NFL": 48}
K_FACTOR = {"NBA": 20, "NFL": 20}
SEASON_REGRESSION = 0.75  # new = REGRESSION * old + (1 - REGRESSION) * 1500
START_RATING = 1500.0


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS teams (
        league TEXT NOT NULL,
        team_id TEXT NOT NULL,
        name TEXT,
        rating REAL NOT NULL,
        last_season_regressed TEXT,
        PRIMARY KEY (league, team_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS games (
        game_id TEXT PRIMARY KEY,
        league TEXT NOT NULL,
        season TEXT,
        date TEXT NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_score INTEGER,
        away_score INTEGER,
        home_rating_pre REAL,
        away_rating_pre REAL,
        home_rating_post REAL,
        away_rating_post REAL,
        processed_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.commit()
    return conn


def get_rating(conn, league, team_id, name=None):
    row = conn.execute(
        "SELECT rating FROM teams WHERE league=? AND team_id=?", (league, team_id)
    ).fetchone()
    if row:
        return row[0]
    conn.execute(
        "INSERT INTO teams (league, team_id, name, rating) VALUES (?,?,?,?)",
        (league, team_id, name, START_RATING),
    )
    return START_RATING


def _maybe_regress_for_new_season(conn, league, team_id, season):
    row = conn.execute(
        "SELECT rating, last_season_regressed FROM teams WHERE league=? AND team_id=?",
        (league, team_id),
    ).fetchone()
    if row is None:
        return
    rating, last_regressed = row
    if season and last_regressed != season:
        new_rating = SEASON_REGRESSION * rating + (1 - SEASON_REGRESSION) * START_RATING
        conn.execute(
            "UPDATE teams SET rating=?, last_season_regressed=? WHERE league=? AND team_id=?",
            (new_rating, season, league, team_id),
        )


def expected_score(rating_a, rating_b):
    """Probability that side A beats side B given their (already home-adjusted) ratings."""
    return 1.0 / (1.0 + 10 ** (-(rating_a - rating_b) / 400.0))


def apply_game(conn, league, game_id, season, date, home_team, away_team,
                home_score, away_score, home_name=None, away_name=None):
    """
    Replay one final game's result into the Elo ratings. Idempotent: if game_id has
    already been processed, this is a no-op. Returns True if the game was newly applied.
    """
    existing = conn.execute("SELECT 1 FROM games WHERE game_id=?", (game_id,)).fetchone()
    if existing:
        return False

    _maybe_regress_for_new_season(conn, league, home_team, season)
    _maybe_regress_for_new_season(conn, league, away_team, season)

    rating_home = get_rating(conn, league, home_team, home_name)
    rating_away = get_rating(conn, league, away_team, away_name)

    home_adv = HOME_ADV[league]
    k = K_FACTOR[league]

    expected_home = expected_score(rating_home + home_adv, rating_away)
    expected_away = 1.0 - expected_home

    if home_score == away_score:
        actual_home, actual_away = 0.5, 0.5
    else:
        actual_home = 1.0 if home_score > away_score else 0.0
        actual_away = 1.0 - actual_home

    new_rating_home = rating_home + k * (actual_home - expected_home)
    new_rating_away = rating_away + k * (actual_away - expected_away)

    conn.execute(
        "UPDATE teams SET rating=? WHERE league=? AND team_id=?",
        (new_rating_home, league, home_team),
    )
    conn.execute(
        "UPDATE teams SET rating=? WHERE league=? AND team_id=?",
        (new_rating_away, league, away_team),
    )
    conn.execute(
        """INSERT INTO games (game_id, league, season, date, home_team, away_team,
           home_score, away_score, home_rating_pre, away_rating_pre,
           home_rating_post, away_rating_post, processed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (game_id, league, season, date, home_team, away_team, home_score, away_score,
         rating_home, rating_away, new_rating_home, new_rating_away,
         datetime.utcnow().isoformat()),
    )
    conn.commit()
    return True


def last_game_date(conn, league, team_id, before_date=None):
    """Most recent processed game date for a team, optionally strictly before before_date."""
    q = """SELECT date FROM games
           WHERE league=? AND (home_team=? OR away_team=?)"""
    params = [league, team_id, team_id]
    if before_date:
        q += " AND date < ?"
        params.append(before_date)
    q += " ORDER BY date DESC LIMIT 1"
    row = conn.execute(q, params).fetchone()
    return row[0] if row else None


def recent_games(conn, league, team_id, before_date, limit=10):
    """Last `limit` processed games for a team strictly before before_date, most recent first."""
    rows = conn.execute(
        """SELECT date, home_team, away_team, home_score, away_score FROM games
           WHERE league=? AND (home_team=? OR away_team=?) AND date < ?
           ORDER BY date DESC LIMIT ?""",
        (league, team_id, team_id, before_date, limit),
    ).fetchall()
    return rows


def head_to_head(conn, league, team_a, team_b, before_date, limit=10):
    rows = conn.execute(
        """SELECT date, home_team, away_team, home_score, away_score FROM games
           WHERE league=? AND date < ?
             AND ((home_team=? AND away_team=?) OR (home_team=? AND away_team=?))
           ORDER BY date DESC LIMIT ?""",
        (league, before_date, team_a, team_b, team_b, team_a, limit),
    ).fetchall()
    return rows


def season_home_away_record(conn, league, team_id, season, home, before_date):
    """
    Win-loss record for a team in the given season, restricted to games where it
    played home (home=True) or away (home=False), strictly before before_date.
    """
    col = "home_team" if home else "away_team"
    rows = conn.execute(
        f"""SELECT home_score, away_score FROM games
            WHERE league=? AND season=? AND {col}=? AND date < ?""",
        (league, season, team_id, before_date),
    ).fetchall()
    wins = losses = 0
    for home_score, away_score in rows:
        team_score = home_score if home else away_score
        opp_score = away_score if home else home_score
        if team_score > opp_score:
            wins += 1
        elif team_score < opp_score:
            losses += 1
    return wins, losses


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
