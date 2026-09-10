#!/usr/bin/env python3
"""
The daily script. Meant to run once (or twice, see README) a day via cron.

Core rule: if there are zero NBA games and zero NFL games today, this does nothing
at all -- no /docs change, no commit, no push, no output beyond a log line. See
handle-zero-games logic in main().
"""
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from src import elo, espn, kalshi, odds_api, player_value, polymarket, seasons, site

ET = ZoneInfo("America/New_York")
DB_PATH = "team_ratings.db"
DOCS_DIR = "docs"
LOG_DIR = "logs"

REST_BACK_TO_BACK_NBA = -25
REST_SHORT_WEEK_NFL = -25
REST_SHORT_WEEK_NFL_MAX_DAYS = 5
REST_EXTRA_NFL = 15
REST_EXTRA_NFL_MIN_DAYS = 10
REST_EXTRA_NFL_MAX_DAYS = 21  # beyond this it's an offseason gap (e.g. season opener), not a bye week

# Injury adjustment: each Out/Doubtful player is weighted by their own recent-season
# production (see src/player_value.py + MODEL.md) rather than counted flatly. VALUE_SCALE
# converts a player's value score into Elo points; FALLBACK_OUT applies when a value
# score can't be computed (stats fetch failed, or -- NFL only -- a non-skill position
# with no clean free per-game production stat). Doubtful counts at DOUBTFUL_WEIGHT of
# the same player's Out-equivalent penalty. CAP bounds the total per team.
NBA_VALUE_SCALE = 1.5
NBA_INJURY_FALLBACK_OUT = -10
NBA_INJURY_CAP = -70

NFL_VALUE_SCALE = 1.5
NFL_INJURY_FALLBACK_OUT = -6
NFL_INJURY_CAP = -50

DOUBTFUL_WEIGHT = 0.5

RECENT_FORM_GAMES = 10
SCOREBOARD_RETRY_ATTEMPTS = 3
SCOREBOARD_RETRY_DELAY_SECONDS = 5

log = logging.getLogger("daily_run")


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def fetch_schedule_with_retry(league, date_str):
    for attempt in range(1, SCOREBOARD_RETRY_ATTEMPTS + 1):
        data = espn.get_scoreboard(league, date_str)
        if data is not None:
            return data
        log.warning("ESPN scoreboard fetch failed for %s %s (attempt %d/%d)",
                    league, date_str, attempt, SCOREBOARD_RETRY_ATTEMPTS)
        if attempt < SCOREBOARD_RETRY_ATTEMPTS:
            time.sleep(SCOREBOARD_RETRY_DELAY_SECONDS)
    return None


def replay_recent_results(conn, league, today_et, lookback_days=4):
    """Pull final scores from the last few days (idempotent) so ratings are current
    before predicting today's games. Returns False if any day's fetch outright failed
    (caller should treat this league's data as untrustworthy today), True otherwise."""
    ok_overall = True
    applied = 0
    for d in range(lookback_days, -1, -1):  # oldest to newest, includes today
        day = today_et - timedelta(days=d)
        data = fetch_schedule_with_retry(league, day.strftime("%Y%m%d"))
        if data is None:
            log.error("Could not fetch %s scoreboard for %s after retries", league, day)
            ok_overall = False
            continue
        for ev in espn.parse_events(league, data):
            if ev.get("season_type") == 1:
                continue  # preseason: rosters/results aren't representative, keep out of Elo
            if ev["state"] != "post" or ev["home_score"] is None or ev["away_score"] is None:
                continue
            game_id = f"{league}_{day.isoformat()}_{ev['away_abbr']}_{ev['home_abbr']}"
            season = str(seasons.season_for(league, day))
            applied_this = elo.apply_game(
                conn, league, game_id, season=season, date=day.isoformat(),
                home_team=ev["home_abbr"], away_team=ev["away_abbr"],
                home_score=ev["home_score"], away_score=ev["away_score"],
                home_name=ev["home_name"], away_name=ev["away_name"],
            )
            if applied_this:
                applied += 1
    if applied:
        log.info("Replayed %d newly completed %s game(s) into Elo", applied, league)
    return ok_overall


def rest_adjustment(league, conn, team_abbr, game_date_iso):
    """Returns (adjustment_points, note_str_or_None)."""
    last_date = elo.last_game_date(conn, league, team_abbr, before_date=game_date_iso)
    if not last_date:
        return 0.0, None
    days_rest = (datetime.fromisoformat(game_date_iso).date()
                 - datetime.fromisoformat(last_date).date()).days
    if league == "NBA" and days_rest <= 1:
        return REST_BACK_TO_BACK_NBA, "on a back-to-back (0 days rest)"
    if league == "NFL":
        if days_rest <= REST_SHORT_WEEK_NFL_MAX_DAYS:
            return REST_SHORT_WEEK_NFL, f"on a short week ({days_rest} days rest)"
        if REST_EXTRA_NFL_MIN_DAYS <= days_rest <= REST_EXTRA_NFL_MAX_DAYS:
            return REST_EXTRA_NFL, f"coming off extra rest ({days_rest} days)"
    return 0.0, None


def player_out_penalty(league, athlete_id, position):
    """
    Elo points lost (a negative number) if this specific player is Out, based on their
    own recent-season production -- not a flat per-player count. Falls back to a flat
    per-league penalty if a value score can't be computed (stats fetch failed, missing
    athlete id, or -- NFL only -- a non-skill position). Never raises.
    """
    stats = espn.get_athlete_season_stats(league, athlete_id) if athlete_id else None
    if league == "NBA":
        score = player_value.nba_game_score(stats)
        if score is None:
            return NBA_INJURY_FALLBACK_OUT
        return -max(score, 0.0) * NBA_VALUE_SCALE
    score = player_value.nfl_production_score(stats, position)
    if score is None:
        return NFL_INJURY_FALLBACK_OUT
    return -max(score, 0.0) * NFL_VALUE_SCALE


def injury_adjustment(league, espn_team_id):
    """
    Returns (adjustment_points, injuries, ok_bool).
    `injuries` is every reported injury (any status) as {name, position, status}, for
    display on the card. The numeric adjustment only counts Out/Doubtful (Doubtful at
    DOUBTFUL_WEIGHT), each weighted by that specific player's own production -- see
    player_out_penalty() and MODEL.md section 3.
    """
    if espn_team_id is None:
        return 0.0, [], False
    injuries = espn.get_team_injuries(league, espn_team_id)
    if injuries is None:
        return 0.0, [], False

    cap = NBA_INJURY_CAP if league == "NBA" else NFL_INJURY_CAP
    total = 0.0
    for i in injuries:
        status = (i.get("status") or "").lower()
        if status not in ("out", "doubtful"):
            continue
        penalty = player_out_penalty(league, i.get("athlete_id"), i.get("position"))
        if status == "doubtful":
            penalty *= DOUBTFUL_WEIGHT
        total += penalty
    total = max(total, cap)
    return total, injuries, True


def form_record(conn, league, team_abbr, before_date_iso, limit=RECENT_FORM_GAMES):
    games = elo.recent_games(conn, league, team_abbr, before_date_iso, limit=limit)
    wins = losses = 0
    for date_, home, away, hscore, ascore in games:
        is_home = home == team_abbr
        team_score = hscore if is_home else ascore
        opp_score = ascore if is_home else hscore
        if team_score is None or opp_score is None:
            continue
        if team_score > opp_score:
            wins += 1
        elif team_score < opp_score:
            losses += 1
    return wins, losses, len(games)


def current_streak(conn, league, team_abbr, before_date_iso, limit=15):
    """(streak_length, 'W'|'L') from the most recent completed games, or None if unknown."""
    games = elo.recent_games(conn, league, team_abbr, before_date_iso, limit=limit)
    results = []
    for date_, home, away, hscore, ascore in games:
        if hscore is None or ascore is None:
            continue
        is_home = home == team_abbr
        team_score = hscore if is_home else ascore
        opp_score = ascore if is_home else hscore
        if team_score == opp_score:
            break  # a tie breaks the streak-counting cleanly; stop rather than guess
        results.append("W" if team_score > opp_score else "L")
    if not results:
        return None
    streak_type = results[0]
    count = 0
    for r in results:
        if r != streak_type:
            break
        count += 1
    return count, streak_type


def avg_margin(conn, league, team_abbr, before_date_iso, limit=RECENT_FORM_GAMES):
    """Average point/score margin (positive = outscoring opponents) over the last N games."""
    games = elo.recent_games(conn, league, team_abbr, before_date_iso, limit=limit)
    total = n = 0
    for date_, home, away, hscore, ascore in games:
        if hscore is None or ascore is None:
            continue
        is_home = home == team_abbr
        total += (hscore - ascore) if is_home else (ascore - hscore)
        n += 1
    return (total / n) if n else None


def build_why(conn, league, home_abbr, away_abbr, home_name, away_name, date_iso,
              season, home_rest_note, away_rest_note):
    bullets = []

    for abbr, name, rest_note in ((home_abbr, home_name, home_rest_note),
                                   (away_abbr, away_name, away_rest_note)):
        w, l, n = form_record(conn, league, abbr, date_iso)
        if not n:
            continue
        streak = current_streak(conn, league, abbr, date_iso)
        margin = avg_margin(conn, league, abbr, date_iso)
        streak_str = ""
        if streak:
            count, kind = streak
            if count > 1:
                streak_str = f", on a {count}-game {'winning' if kind == 'W' else 'losing'} streak"
        margin_str = ""
        if margin is not None:
            direction = "outscoring" if margin >= 0 else "getting outscored by"
            margin_str = f", {direction} opponents by {abs(margin):.1f} points/game on average"
        rest_str = f"; {name} are {rest_note}" if rest_note else ""
        bullets.append(f"{name} are {w}-{l} over their last {n} games{streak_str}{margin_str}{rest_str}")

    h2h = elo.head_to_head(conn, league, home_abbr, away_abbr, date_iso, limit=5)
    if h2h:
        home_h2h_wins = 0
        for _, h, a, hs, as_ in h2h:
            winner = h if hs > as_ else a
            if winner == home_abbr:
                home_h2h_wins += 1
        bullets.append(f"{home_name} have won {home_h2h_wins} of the last {len(h2h)} "
                        f"meetings with {away_name}")
    else:
        bullets.append(f"No recent head-to-head history found between {home_name} and {away_name}")

    hsw, hsl = elo.season_home_away_record(conn, league, home_abbr, season, True, date_iso)
    asw, asl = elo.season_home_away_record(conn, league, away_abbr, season, False, date_iso)
    if hsw + hsl > 0:
        bullets.append(f"{home_name} are {hsw}-{hsl} at home this season")
    if asw + asl > 0:
        bullets.append(f"{away_name} are {asw}-{asl} on the road this season")

    if not bullets:
        bullets = ["No notable recent-form, rest, streak, or head-to-head signals found for "
                   "this matchup -- this is an early-season or data-sparse game."]
    return bullets


def process_league(league, conn, today_et, odds_key, warnings):
    date_str = today_et.strftime("%Y%m%d")
    date_iso = today_et.isoformat()

    schedule_ok = replay_recent_results(conn, league, today_et)
    if not schedule_ok:
        warnings.append(f"{league}: could not fully refresh recent results before predicting "
                         f"(ESPN fetch issue) -- ratings may be slightly stale.")

    today_data = fetch_schedule_with_retry(league, date_str)
    if today_data is None:
        warnings.append(f"{league}: ESPN schedule for today is unavailable; {league} games "
                         f"could not be checked.")
        return None  # unknown, not "zero"

    events = [ev for ev in espn.parse_events(league, today_data) if ev.get("season_type") != 1]
    team_ids = espn.get_scoreboard_team_ids(league, today_data)

    if not events:
        return []  # confirmed zero games today

    dk_odds = odds_api.get_draftkings_odds(league, odds_key)
    if dk_odds is None:
        warnings.append(f"{league}: DraftKings odds unavailable today (the-odds-api fetch failed "
                         f"or no key set).")

    season = str(seasons.season_for(league, today_et))
    games = []
    for ev in events:
        home_abbr, away_abbr = ev["home_abbr"], ev["away_abbr"]
        home_name, away_name = ev["home_name"], ev["away_name"]

        home_rating = elo.get_rating(conn, league, home_abbr, home_name)
        away_rating = elo.get_rating(conn, league, away_abbr, away_name)

        home_rest_adj, home_rest_note = rest_adjustment(league, conn, home_abbr, date_iso)
        away_rest_adj, away_rest_note = rest_adjustment(league, conn, away_abbr, date_iso)

        home_inj_adj, home_injuries, home_inj_ok = injury_adjustment(league, team_ids.get(home_abbr))
        away_inj_adj, away_injuries, away_inj_ok = injury_adjustment(league, team_ids.get(away_abbr))
        if not (home_inj_ok and away_inj_ok):
            warnings.append(f"{league} {away_abbr}@{home_abbr}: injury report unavailable for "
                             f"one or both teams; injury adjustment skipped where missing.")

        eff_home = home_rating + home_rest_adj + home_inj_adj
        eff_away = away_rating + away_rest_adj + away_inj_adj
        model_home_prob = elo.expected_score(eff_home + elo.HOME_ADV[league], eff_away)

        dk_home_odds = dk_away_odds = None
        market_true_home_prob = None
        if dk_odds:
            match = odds_api.match_game(dk_odds, home_name, away_name)
            if match:
                dk_home_odds, dk_away_odds = match["home_odds"], match["away_odds"]
                if dk_home_odds is not None and dk_away_odds is not None:
                    ih = odds_api.american_to_implied_prob(dk_home_odds)
                    ia = odds_api.american_to_implied_prob(dk_away_odds)
                    market_true_home_prob, _ = odds_api.devig(ih, ia)

        kalshi_result = kalshi.get_game_market(league, today_et, home_abbr, away_abbr)
        poly_result = polymarket.get_game_market(league, today_et, home_name, away_name)

        # The model's own pick (not always the home team).
        pick_is_home = model_home_prob >= 0.5
        pick_team_name = home_name if pick_is_home else away_name
        model_pick_prob = model_home_prob if pick_is_home else 1 - model_home_prob

        # The market's own favorite -- independently computed, so it can (rarely) name a
        # different team than the model's pick; the two "pick" lines on the card make that
        # visible on their own without a separate disagreement callout.
        market_pick_team_name = None
        market_pick_prob = None
        market_prob_for_model_pick = None
        if market_true_home_prob is not None:
            market_favors_home = market_true_home_prob >= 0.5
            market_pick_team_name = home_name if market_favors_home else away_name
            market_pick_prob = market_true_home_prob if market_favors_home else 1 - market_true_home_prob
            market_prob_for_model_pick = (
                market_true_home_prob if pick_is_home else 1 - market_true_home_prob
            )

        # Edge stays anchored to the model's own pick (comparing the model's and market's
        # probability for the *same* team), regardless of which team is displayed as the
        # market's favorite above.
        edge = None
        if market_prob_for_model_pick is not None:
            edge = model_pick_prob - market_prob_for_model_pick

        why = build_why(conn, league, home_abbr, away_abbr, home_name, away_name, date_iso,
                         season, home_rest_note, away_rest_note)

        tipoff_str = "time TBD"
        if ev.get("date_utc"):
            try:
                dt_utc = datetime.fromisoformat(ev["date_utc"].replace("Z", "+00:00"))
                tipoff_str = dt_utc.astimezone(ET).strftime("%-I:%M %p")
            except ValueError:
                pass

        final_score = None
        if ev["state"] == "post" and ev["home_score"] is not None:
            final_score = f"{away_abbr} {ev['away_score']} - {home_abbr} {ev['home_score']}"

        games.append({
            "league": league,
            "tipoff_et_str": tipoff_str,
            "away_abbr": away_abbr, "away_name": away_name,
            "home_abbr": home_abbr, "home_name": home_name,
            "status": "final" if ev["state"] == "post" else "scheduled",
            "final_score": final_score,
            "model_home_prob": model_home_prob,
            "market_true_home_prob": market_true_home_prob,
            "pick_team_name": pick_team_name,
            "model_pick_prob": model_pick_prob,
            "market_pick_team_name": market_pick_team_name,
            "market_pick_prob": market_pick_prob,
            "dk_home_odds": dk_home_odds, "dk_away_odds": dk_away_odds,
            "kalshi_home_prob": kalshi_result["home_prob"] if kalshi_result else None,
            "kalshi_away_prob": kalshi_result["away_prob"] if kalshi_result else None,
            "polymarket_home_prob": poly_result["home_prob"] if poly_result else None,
            "polymarket_away_prob": poly_result["away_prob"] if poly_result else None,
            "edge": edge,
            "why": why,
            "home_injuries": home_injuries, "home_injuries_ok": home_inj_ok,
            "away_injuries": away_injuries, "away_injuries_ok": away_inj_ok,
        })

    return games


def git(*args):
    subprocess.run(["git", *args], check=True)


def main():
    setup_logging()
    load_dotenv()
    odds_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not odds_key:
        log.warning("ODDS_API_KEY is not set -- DraftKings odds will be unavailable today.")

    now_et = datetime.now(ET)
    today_et = now_et.date()
    log.info("Daily run starting for %s ET", today_et.isoformat())

    conn = elo.connect(DB_PATH)
    warnings = []

    nfl_games = process_league("NFL", conn, today_et, odds_key, warnings)
    nba_games = process_league("NBA", conn, today_et, odds_key, warnings)
    conn.close()

    if nfl_games is None or nba_games is None:
        log.error("Could not confirm today's schedule for one or both leagues -- "
                  "aborting without publishing (never guessing an off-day). "
                  "Check the warnings above and re-run, or investigate ESPN's endpoints.")
        sys.exit(1)

    if not nfl_games and not nba_games:
        log.info("No NBA or NFL games today. Off-day: doing nothing.")
        sys.exit(0)

    os.makedirs(DOCS_DIR, exist_ok=True)
    updated_str = now_et.strftime("%Y-%m-%d %-I:%M %p")
    html = site.render_page(nba_games, nfl_games, updated_str, warnings)
    with open(os.path.join(DOCS_DIR, "index.html"), "w") as f:
        f.write(html)
    log.info("Wrote %s with %d NFL and %d NBA game(s).",
              os.path.join(DOCS_DIR, "index.html"), len(nfl_games), len(nba_games))

    git("add", "docs/index.html", DB_PATH)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        log.info("No change to docs/index.html or %s since last commit -- nothing to commit.", DB_PATH)
        sys.exit(0)

    git("commit", "-m", f"Predictions for {today_et.isoformat()}")
    try:
        git("push")
        log.info("Pushed update for %s.", today_et.isoformat())
    except subprocess.CalledProcessError:
        log.error("git push failed -- commit was made locally but not pushed. "
                  "Check network/auth and push manually.")
        sys.exit(1)


if __name__ == "__main__":
    main()
