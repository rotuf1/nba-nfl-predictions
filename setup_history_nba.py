"""
One-time (re-runnable/idempotent) historical NBA ingestion.

Source: basketball-reference.com season schedule pages, e.g.
https://www.basketball-reference.com/leagues/NBA_2025_games-october.html

There's no free bulk CSV equivalent to nflverse for NBA, so this scrapes the public
schedule/results tables directly. robots.txt for basketball-reference does not
disallow /leagues/*games-*.html and specifies "Crawl-delay: 3" — this script sleeps
3+ seconds between every request to respect that.
"""
import sys
import time
from io import StringIO

import pandas as pd
import requests

from src import elo, seasons

NBA_HISTORY_SEASONS = 10  # how many most-recent seasons to seed with
CRAWL_DELAY_SECONDS = 3.5
HEADERS = {
    "User-Agent": "Mozilla/5.0 (research script for a free personal predictions site; "
                  "contact: matthewliang26@gmail.com)"
}
MONTHS = ["october", "november", "december", "january", "february", "march",
          "april", "may", "june", "july", "august", "september"]

NAME_TO_ABBR = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GS",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NO",
    "New York Knicks": "NY",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SA",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTAH",
    "Washington Wizards": "WSH",
}


def fetch_month(season, month):
    url = f"https://www.basketball-reference.com/leagues/NBA_{season}_games-{month}.html"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"  {season} {month}: request failed ({e}), skipping")
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        print(f"  {season} {month}: HTTP {resp.status_code}, skipping")
        return None
    try:
        tables = pd.read_html(StringIO(resp.text), attrs={"id": "schedule"})
    except ValueError:
        return None
    if not tables:
        return None
    df = tables[0]
    df = df[df["Date"] != "Playoffs"]  # a stray header row bref inserts before playoffs
    return df


def main():
    conn = elo.connect("team_ratings.db")

    today = pd.Timestamp.now(tz="America/New_York").date()
    current_season_label = seasons.nba_season_for_date(today)
    season_list = list(range(current_season_label - NBA_HISTORY_SEASONS + 1, current_season_label + 1))

    total_applied = 0
    for season in season_list:
        season_applied = 0
        for month in MONTHS:
            df = fetch_month(season, month)
            time.sleep(CRAWL_DELAY_SECONDS)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                try:
                    date = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
                    visitor = row["Visitor/Neutral"]
                    home = row["Home/Neutral"]
                    vpts = row["PTS"]
                    hpts = row["PTS.1"]
                except (KeyError, ValueError):
                    continue
                if pd.isna(vpts) or pd.isna(hpts):
                    continue  # game not yet played
                away_abbr = NAME_TO_ABBR.get(visitor)
                home_abbr = NAME_TO_ABBR.get(home)
                if not away_abbr or not home_abbr:
                    print(f"  WARNING: unmapped team name '{visitor}' or '{home}', skipping game")
                    continue
                game_id = f"NBA_{date}_{away_abbr}_{home_abbr}"
                ok = elo.apply_game(
                    conn,
                    league="NBA",
                    game_id=game_id,
                    season=str(season),
                    date=date,
                    home_team=home_abbr,
                    away_team=away_abbr,
                    home_score=int(hpts),
                    away_score=int(vpts),
                )
                if ok:
                    season_applied += 1
        print(f"Season {season}: applied {season_applied} games")
        total_applied += season_applied

    elo.set_meta(conn, "nba_last_season_seen", str(current_season_label))
    print(f"\nApplied {total_applied} new NBA games total (seasons {season_list[0]}-{season_list[-1]}).")

    print("\nCurrent NBA ratings:")
    for r in conn.execute(
        "SELECT team_id, rating FROM teams WHERE league='NBA' ORDER BY rating DESC"
    ):
        print(f"  {r[0]:>4}  {r[1]:.1f}")

    conn.close()


if __name__ == "__main__":
    sys.exit(main())
