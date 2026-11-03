"""
NBA player-prop projections and over picks (experimental). See MODEL.md section 8.

Projections are free (ESPN season averages). DraftKings lines cost odds-API credits, so
they're only fetched for games tipping within LINES_WINDOW_HOURS, once per game, and only
out of credits left over after reserving enough for the moneyline odds the rest of the
month. Games without lines still show projections, just no picks.
"""
import calendar
import logging
import math
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from src import elo, espn, odds_api

log = logging.getLogger(__name__)

MARKETS = {"player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST"}
LINES_WINDOW_HOURS = 6
# Moneyline odds cost ~1 credit per league per run (2 leagues x 3-4 runs/day) -- this much
# per remaining day of the month is never spent on props.
ODDS_RESERVE_PER_DAY = 10

# Projection: per-minute rate = this season blended with last season worth K_MIN minutes;
# minutes = this season's average blended with last season's worth K_GAMES games.
K_MIN = 400
K_GAMES = 1
# Game-to-game spread: rebounds/assists negative binomial with variance = ratio * mean,
# points normal with variance = ratio * mean (sd ~7 for a 20-ppg scorer).
VAR_RATIO = {"PTS": 2.4, "REB": 1.35, "AST": 1.35}

# A pick is an over whose probability (model blended 50/50 with DK's no-vig price) is at
# least MIN_BLEND, where model and DK disagree by at most MAX_DISAGREEMENT (bigger gaps are
# almost always news the model doesn't have, like a role change).
MIN_BLEND = 0.56
MAX_DISAGREEMENT = 0.20
MAX_PICKS_PER_GAME = 4
PROJECTED_PLAYERS_PER_GAME = 6
GRADE_LOOKBACK_DAYS = 4
EXCLUDED_STATUSES = {"out", "injured reserve", "doubtful", "questionable", "day-to-day"}


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS prop_lines (
        event_id TEXT NOT NULL, game_date TEXT NOT NULL, player TEXT NOT NULL,
        stat TEXT NOT NULL, line REAL NOT NULL, over_price INTEGER, under_price INTEGER,
        fetched_at TEXT, PRIMARY KEY (event_id, player, stat))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS prop_picks (
        event_id TEXT NOT NULL, game_date TEXT NOT NULL, matchup TEXT, athlete_id TEXT NOT NULL,
        player TEXT, team TEXT, stat TEXT NOT NULL, line REAL, price INTEGER, proj REAL,
        prob REAL, market_prob REAL, graded INTEGER NOT NULL DEFAULT 0, result TEXT,
        actual REAL, created_at TEXT, PRIMARY KEY (event_id, athlete_id, stat))""")
    conn.commit()


def norm_name(name):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    n = n.replace(".", "").replace("'", "")
    for suffix in (" jr", " sr", " iii", " ii", " iv"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return " ".join(n.split())


def american_profit(price):
    """Profit on a 1-unit stake if the bet wins."""
    return price / 100.0 if price > 0 else 100.0 / -price


def project(avgs, season_year):
    """(minutes, {stat: projection}) from ESPN season averages, or None if the player
    hasn't played this season."""
    cur, last = avgs.get(season_year), avgs.get(season_year - 1)
    if not cur or not cur["gp"] or not cur["min"]:
        return None
    n, cur_min_total = cur["gp"], cur["gp"] * cur["min"]
    if last and last["gp"] and last["min"]:
        mins = (n * cur["min"] + K_GAMES * last["min"]) / (n + K_GAMES)
    else:
        mins, last = cur["min"], None
    out = {}
    for stat, key in (("PTS", "pts"), ("REB", "reb"), ("AST", "ast")):
        if last:
            last_rate = last[key] / last["min"]
            rate = (n * cur[key] + last_rate * K_MIN) / (cur_min_total + K_MIN)
        else:
            rate = cur[key] / cur["min"]
        out[stat] = rate * mins
    return mins, out


def prob_over(stat, mean, line):
    if mean <= 0:
        return 0.0
    var = VAR_RATIO[stat] * mean
    if stat == "PTS":
        return 0.5 * math.erfc((line - mean) / math.sqrt(2 * var))
    # negative binomial with the given mean/variance
    p = mean / var
    r = mean * p / (1 - p)
    pmf, cdf = p ** r, 0.0
    for x in range(int(math.floor(line)) + 1):
        if x > 0:
            pmf *= (x - 1 + r) / x * (1 - p)
        cdf += pmf
    return 1 - cdf


def _match_event(odds_events, home_name, away_name):
    nick = lambda s: (s or "").split()[-1].lower() if s else ""
    for ev in odds_events or []:
        if nick(ev.get("home_team")) == nick(home_name) and nick(ev.get("away_team")) == nick(away_name):
            return ev
    return None


def _days_left_in_month(today):
    return calendar.monthrange(today.year, today.month)[1] - today.day + 1


def grade_pending(conn, today_iso):
    """Grade ungraded picks whose games are final; void ones too old to grade or where the
    player didn't play."""
    cutoff = (datetime.fromisoformat(today_iso) - timedelta(days=GRADE_LOOKBACK_DAYS)).date().isoformat()
    conn.execute("UPDATE prop_picks SET graded=1, result='void' WHERE graded=0 AND game_date < ?", (cutoff,))
    events = [r[0] for r in conn.execute("SELECT DISTINCT event_id FROM prop_picks WHERE graded=0")]
    for event_id in events:
        box, state = espn.get_nba_boxscore(event_id)
        if state != "post" or box is None:
            continue
        for athlete_id, stat, line in conn.execute(
                "SELECT athlete_id, stat, line FROM prop_picks WHERE event_id=? AND graded=0",
                (event_id,)).fetchall():
            row = box.get(athlete_id)
            actual = row.get(stat.lower()) if row else None
            if actual is None or not row.get("min"):
                result = "void"
            else:
                result = "win" if actual > line else "loss"
            conn.execute("UPDATE prop_picks SET graded=1, result=?, actual=? "
                         "WHERE event_id=? AND athlete_id=? AND stat=?",
                         (result, actual, event_id, athlete_id, stat))
    conn.commit()


def get_record(conn):
    rows = conn.execute("SELECT result, price FROM prop_picks WHERE graded=1 AND result IN ('win','loss')").fetchall()
    wins = sum(1 for r, _ in rows if r == "win")
    units = sum(american_profit(p) if r == "win" else -1.0 for r, p in rows)
    return {"wins": wins, "losses": len(rows) - wins, "units": units}


def _fetch_lines(conn, games, now_et, odds_key, warnings):
    """Fetch DK prop lines for pre-tip games inside the window that don't have them yet,
    within today's credit allowance. Lines are stored in prop_lines."""
    today_iso = now_et.date().isoformat()
    due = []
    for g in games:
        if g["state"] != "pre" or not g.get("date_utc"):
            continue
        tip = datetime.fromisoformat(g["date_utc"].replace("Z", "+00:00"))
        if tip - now_et > timedelta(hours=LINES_WINDOW_HOURS):
            continue
        if conn.execute("SELECT 1 FROM prop_lines WHERE event_id=? LIMIT 1", (g["event_id"],)).fetchone():
            continue
        due.append((tip, g))
    if not due or not odds_key:
        return
    odds_events, remaining = odds_api.get_events("NBA", odds_key)
    if odds_events is None or remaining is None:
        warnings.append("NBA props: couldn't check odds-API credits, so no prop lines this run.")
        return
    spent_key = f"prop_credits_spent_{today_iso}"
    spent = int(elo.get_meta(conn, spent_key, "0"))
    days_left = _days_left_in_month(now_et.date())
    allowance = (remaining + spent - ODDS_RESERVE_PER_DAY * days_left) / days_left - spent
    cost = len(MARKETS)
    skipped = 0
    for _, g in sorted(due, key=lambda t: t[0]):
        if allowance < cost:
            skipped += 1
            continue
        ev = _match_event(odds_events, g["home_name"], g["away_name"])
        if not ev:
            continue
        props, rem = odds_api.get_player_props("NBA", odds_key, ev["id"], list(MARKETS))
        if props is None:
            continue
        allowance -= cost
        spent += cost
        for p in props:
            conn.execute("INSERT OR REPLACE INTO prop_lines VALUES (?,?,?,?,?,?,?,?)",
                         (g["event_id"], today_iso, p["player"], MARKETS[p["market"]], p["line"],
                          p["over"], p["under"], datetime.utcnow().isoformat()))
    elo.set_meta(conn, spent_key, str(spent))
    conn.commit()
    if skipped:
        warnings.append(f"NBA props: no lines for {skipped} game(s) -- this month's odds-API "
                        f"credits are reserved for moneyline odds.")


def run(conn, games, season_year, now_et, odds_key, warnings):
    """
    games: today's NBA games as dicts with event_id, date_utc, state, home/away abbr,
    name and ESPN team id, and home/away injuries (already including manual overrides).
    Returns the data src/site.py's render_props needs.
    """
    ensure_tables(conn)
    today_iso = now_et.date().isoformat()
    grade_pending(conn, today_iso)
    _fetch_lines(conn, games, now_et, odds_key, warnings)

    projections = []
    for g in games:
        if g["state"] != "pre":
            continue
        injured = {norm_name(i["name"]) for i in g["home_injuries"] + g["away_injuries"]
                   if (i.get("status") or "").lower() in EXCLUDED_STATUSES}
        roster = []
        for side in ("home", "away"):
            for p in espn.get_team_roster("NBA", g[f"{side}_team_id"]) or []:
                if norm_name(p["name"]) not in injured:
                    roster.append({**p, "team": g[f"{side}_abbr"]})
        with ThreadPoolExecutor(8) as ex:
            avgs = list(ex.map(lambda p: espn.get_nba_season_averages(p["athlete_id"]), roster))
        players = []
        for p, a in zip(roster, avgs):
            proj = project(a, season_year) if a else None
            if proj:
                players.append({**p, "min": proj[0], "proj": proj[1]})
        players.sort(key=lambda p: -p["min"])
        matchup = f"{g['away_abbr']} @ {g['home_abbr']}"

        lines = conn.execute("SELECT player, stat, line, over_price, under_price FROM prop_lines "
                             "WHERE event_id=?", (g["event_id"],)).fetchall()
        if not lines:
            projections.append({"matchup": matchup, "tipoff": g["tipoff_et_str"],
                                "players": players[:PROJECTED_PLAYERS_PER_GAME]})
            continue

        by_name = {norm_name(p["name"]): p for p in players}
        candidates = []
        for name, stat, line, over, under in lines:
            p = by_name.get(norm_name(name))
            if not p:
                continue
            model = prob_over(stat, p["proj"][stat], line)
            o, u = odds_api.american_to_implied_prob(over), odds_api.american_to_implied_prob(under)
            market = o / (o + u)
            blend = (model + market) / 2
            if model < 0.5 or abs(model - market) > MAX_DISAGREEMENT or blend < MIN_BLEND:
                continue
            candidates.append((blend, p, stat, line, over, model, market))
        candidates.sort(key=lambda c: -c[0])
        # Re-pick from scratch each pre-tip run, like the game picks.
        conn.execute("DELETE FROM prop_picks WHERE event_id=? AND graded=0", (g["event_id"],))
        for blend, p, stat, line, over, model, market in candidates[:MAX_PICKS_PER_GAME]:
            conn.execute(
                """INSERT OR REPLACE INTO prop_picks (event_id, game_date, matchup, athlete_id,
                   player, team, stat, line, price, proj, prob, market_prob, graded, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
                (g["event_id"], today_iso, f"{matchup} {g['tipoff_et_str']}", p["athlete_id"],
                 p["name"], p["team"], stat, line, over, round(p["proj"][stat], 1), blend, market,
                 datetime.utcnow().isoformat()))
    conn.commit()

    picks = [dict(zip(("matchup", "player", "team", "stat", "line", "price", "proj", "prob",
                       "result", "actual"), r))
             for r in conn.execute("SELECT matchup, player, team, stat, line, price, proj, prob, "
                                   "result, actual FROM prop_picks WHERE game_date=? "
                                   "ORDER BY prob DESC", (today_iso,))]
    return {"picks": picks, "projections": projections, "record": get_record(conn)}
