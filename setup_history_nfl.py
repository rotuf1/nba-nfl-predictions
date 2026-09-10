"""
One-time (re-runnable/idempotent) historical NFL ingestion.

Source: nflverse's community-maintained games.csv (free, no key, no scraping needed):
https://github.com/nflverse/nfldata

Pulls the last NFL_HISTORY_SEASONS completed+in-progress seasons, replays every
finished game chronologically into team_ratings.db via src/elo.py so current Elo
ratings reflect real history instead of starting cold.
"""
import sys
import pandas as pd
import requests

from src import elo

GAMES_CSV_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
NFL_HISTORY_SEASONS = 10  # how many most-recent seasons to seed with

# nflverse uses some historical/relocated abbreviations; normalize to ESPN's current ones
# so a franchise's rating carries over across a relocation instead of starting fresh.
TEAM_ALIAS = {
    "LA": "LAR",   # Rams (post-2016 move uses "LA" in nflverse)
    "STL": "LAR",  # Rams (pre-2016, St. Louis)
    "OAK": "LV",   # Raiders (pre-2020, Oakland)
    "SD": "LAC",   # Chargers (pre-2017, San Diego)
    "WAS": "WSH",  # Washington
}


def normalize_team(code):
    return TEAM_ALIAS.get(code, code)


def main():
    print(f"Fetching {GAMES_CSV_URL}")
    resp = requests.get(GAMES_CSV_URL, timeout=30)
    resp.raise_for_status()
    with open("/tmp/nflverse_games.csv", "wb") as f:
        f.write(resp.content)
    df = pd.read_csv("/tmp/nflverse_games.csv")

    current_season = int(df["season"].max())
    min_season = current_season - NFL_HISTORY_SEASONS + 1
    df = df[df["season"] >= min_season]

    # Only real, completed games (has both scores) and real game types (skip anything odd)
    df = df[df["home_score"].notna() & df["away_score"].notna()]
    df = df[df["game_type"].isin(["REG", "WC", "DIV", "CON", "SB"])]
    df = df.sort_values("gameday")

    conn = elo.connect("team_ratings.db")

    applied = 0
    for _, row in df.iterrows():
        home = normalize_team(row["home_team"])
        away = normalize_team(row["away_team"])
        # Keyed on date+matchup (not nflverse's own game_id) so this stays consistent
        # with the daily ESPN-based updates in daily_run.py, which use the same key
        # convention -- that's what keeps a game from ever being double-counted into
        # Elo if it shows up from both sources.
        ok = elo.apply_game(
            conn,
            league="NFL",
            game_id=f"NFL_{row['gameday']}_{away}_{home}",
            season=str(row["season"]),
            date=row["gameday"],
            home_team=home,
            away_team=away,
            home_score=int(row["home_score"]),
            away_score=int(row["away_score"]),
        )
        if ok:
            applied += 1

    elo.set_meta(conn, "nfl_last_season_seen", str(current_season))
    print(f"Applied {applied} new NFL games (seasons {min_season}-{current_season}).")

    print("\nCurrent NFL ratings:")
    for r in conn.execute(
        "SELECT team_id, rating FROM teams WHERE league='NFL' ORDER BY rating DESC"
    ):
        print(f"  {r[0]:>4}  {r[1]:.1f}")

    conn.close()


if __name__ == "__main__":
    sys.exit(main())
