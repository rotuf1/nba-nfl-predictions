"""
ESPN public (undocumented) scoreboard/injury endpoints. No API key required.

These are unofficial endpoints -- every call here is wrapped so a failure or an
unexpected shape returns None/[] rather than raising, and the caller is expected to
log that and fall back (e.g. to a web search) rather than guess.
"""
import logging
import re

import requests

log = logging.getLogger(__name__)

SPORT_PATH = {
    "NBA": "basketball/nba",
    "NFL": "football/nfl",
}

TIMEOUT = 15

# ESPN's per-team "/teams/{id}/injuries" endpoint (site.api.espn.com) returns an empty
# {} for every team as of 2026-09 -- looks dead/deprecated. The league-wide feed below
# is what actually backs espn.com's injuries pages and is confirmed live.
_ATHLETE_ID_RE = re.compile(r"/id/(\d+)/")

# One league-wide injuries fetch is reused for every team lookup within a single
# daily_run.py process (it already covers all 32/30 teams in one response), instead of
# re-fetching per team per game.
_league_injuries_cache = {}


def get_scoreboard(league, date_yyyymmdd):
    """Returns the raw ESPN scoreboard JSON for a league on a given YYYYMMDD date, or None."""
    url = f"http://site.api.espn.com/apis/site/v2/sports/{SPORT_PATH[league]}/scoreboard"
    try:
        resp = requests.get(url, params={"dates": date_yyyymmdd}, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("ESPN scoreboard fetch failed for %s %s: %s", league, date_yyyymmdd, e)
        return None


def parse_events(league, scoreboard_json):
    """
    Normalize ESPN scoreboard JSON into a list of dicts:
      {event_id, date_utc, home_abbr, away_abbr, home_score, away_score,
       state ('pre'|'in'|'post'), home_name, away_name}
    Returns [] (not None) if the shape is unexpected, so callers can safely iterate.
    """
    if not scoreboard_json:
        return []
    events = scoreboard_json.get("events", [])
    out = []
    for ev in events:
        try:
            comp = ev["competitions"][0]
            competitors = comp["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            status = comp.get("status", {}).get("type", {}).get("state", "pre")
            # ESPN season.type: 1=preseason, 2=regular season, 3=postseason.
            season_type = (ev.get("season") or {}).get("type")
            out.append({
                "event_id": ev["id"],
                "date_utc": comp.get("date"),
                "home_abbr": home["team"]["abbreviation"],
                "away_abbr": away["team"]["abbreviation"],
                "home_name": home["team"].get("displayName"),
                "away_name": away["team"].get("displayName"),
                "home_score": int(home["score"]) if home.get("score") not in (None, "") else None,
                "away_score": int(away["score"]) if away.get("score") not in (None, "") else None,
                "state": status,  # 'pre', 'in', 'post'
                "season_type": season_type,  # 1=preseason, 2=regular, 3=postseason
            })
        except (KeyError, IndexError, StopIteration, TypeError, ValueError) as e:
            log.error("Failed to parse ESPN event for %s: %s (%s)", league, e, ev.get("id"))
            continue
    return out


def _extract_athlete_id(athlete):
    """The league-wide injuries feed doesn't include a bare athlete id field -- pull it
    out of the athlete's ESPN player-card link (.../player/_/id/<id>/<slug>) instead."""
    for link in athlete.get("links", []):
        m = _ATHLETE_ID_RE.search(link.get("href") or "")
        if m:
            return m.group(1)
    return None


def get_league_injuries(league):
    """
    Fetches ESPN's league-wide injuries feed and returns {espn_team_id: [{name,
    position, status, athlete_id, return_date}, ...]}, or None on failure. Cached per
    league for the life of the process (the feed already covers every team in one
    response, so callers should not re-fetch it per team). Entries with status "Active"
    are dropped -- that's ESPN's tag for a roster/news note about a player who is not
    currently on the injury report, not an actual designation like Questionable/
    Doubtful/Out/Injured Reserve.

    return_date is ESPN's expected-return date (YYYY-MM-DD string) if they have one for
    that entry, else None -- see daily_run.py's short-term-IR handling for why this
    matters (a real near-term date vs. their season-ending placeholder date).
    """
    if league in _league_injuries_cache:
        return _league_injuries_cache[league]

    url = f"http://site.api.espn.com/apis/site/v2/sports/{SPORT_PATH[league]}/injuries"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("ESPN league injuries fetch failed for %s: %s", league, e)
        return None

    by_team = {}
    try:
        for team in data.get("injuries", []):
            team_id = team.get("id")
            entries = []
            for item in team.get("injuries", []):
                status = item.get("status") or (item.get("type") or {}).get("description")
                if (status or "").lower() == "active":
                    continue
                athlete = item.get("athlete", {})
                entries.append({
                    "name": athlete.get("displayName", "unknown"),
                    "position": (athlete.get("position") or {}).get("abbreviation"),
                    "status": status,
                    "athlete_id": _extract_athlete_id(athlete),
                    "return_date": (item.get("details") or {}).get("returnDate"),
                })
            by_team[team_id] = entries
    except (KeyError, TypeError) as e:
        log.error("Failed to parse ESPN league injuries for %s: %s", league, e)
        return None

    _league_injuries_cache[league] = by_team
    return by_team


def get_team_injuries(league, espn_team_id):
    """
    Returns a list of {name, position, status, athlete_id} for a team, or None on
    failure. espn_team_id is ESPN's numeric team id (not the abbreviation) -- pass the
    id from the scoreboard team object's "id" field. athlete_id may be None if ESPN's
    payload doesn't carry a resolvable id for a given entry -- callers must handle that
    (it just means per-player stats can't be looked up for that entry).
    """
    if espn_team_id is None:
        return None
    by_team = get_league_injuries(league)
    if by_team is None:
        return None
    return by_team.get(str(espn_team_id), [])


def _parse_num(value):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _latest_season_row(category):
    """Most recent season's row from a stats category, or None. ESPN includes a full
    row for the most recently *completed* season until the new season has games in
    the books, which is exactly the fallback behavior wanted here."""
    rows = category.get("statistics") or []
    if not rows:
        return None
    return max(rows, key=lambda r: (r.get("season") or {}).get("year", 0))


def _parse_row(names, raw_values):
    """Zip a category's field names against one stat row's values, splitting any
    hyphenated combined field (NBA's "made-attempted" columns) into two entries."""
    parsed = {}
    for name, value in zip(names, raw_values):
        value = str(value)
        if "-" in name and "-" in value:
            n1, n2 = name.split("-", 1)
            v1, v2 = value.split("-", 1)
            parsed[n1] = _parse_num(v1)
            parsed[n2] = _parse_num(v2)
        else:
            parsed[name] = _parse_num(value)
    return parsed


def get_athlete_season_stats(league, athlete_id):
    """
    Per-game season stats for one player, for use in src/player_value.py, or None on
    failure. Falls back automatically to the most recently completed season if the
    current one has no games played yet.

    NBA: {"pts","fgm","fga","ftm","fta","oreb","dreb","ast","blk","stl","pf","tov"}
    NFL: {"pass_yds","pass_td","pass_int","rush_yds","rush_td","rec","rec_yds","rec_td"}
         (only the categories that had data for this player are present)
    """
    if athlete_id is None:
        return None
    url = f"http://site.web.api.espn.com/apis/common/v3/sports/{SPORT_PATH[league]}/athletes/{athlete_id}/stats"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("ESPN athlete stats fetch failed for %s athlete %s: %s", league, athlete_id, e)
        return None

    categories = {c["name"]: c for c in data.get("categories", [])}

    try:
        if league == "NBA":
            avg = categories.get("averages")
            if not avg:
                return None
            row = _latest_season_row(avg)
            if not row:
                return None
            parsed = _parse_row(avg.get("names", []), row.get("stats", []))
            return {
                "pts": parsed.get("avgPoints"),
                "fgm": parsed.get("avgFieldGoalsMade"),
                "fga": parsed.get("avgFieldGoalsAttempted"),
                "ftm": parsed.get("avgFreeThrowsMade"),
                "fta": parsed.get("avgFreeThrowsAttempted"),
                "oreb": parsed.get("avgOffensiveRebounds"),
                "dreb": parsed.get("avgDefensiveRebounds"),
                "ast": parsed.get("avgAssists"),
                "blk": parsed.get("avgBlocks"),
                "stl": parsed.get("avgSteals"),
                "pf": parsed.get("avgFouls"),
                "tov": parsed.get("avgTurnovers"),
            }

        # NFL: categories report season totals, not per-game -- divide by gamesPlayed.
        result = {}
        passing = categories.get("passing")
        if passing:
            row = _latest_season_row(passing)
            if row:
                p = _parse_row(passing.get("names", []), row.get("stats", []))
                gp = p.get("gamesPlayed")
                if gp:
                    result["pass_yds"] = (p.get("passingYards") or 0) / gp
                    result["pass_td"] = (p.get("passingTouchdowns") or 0) / gp
                    result["pass_int"] = (p.get("interceptions") or 0) / gp
        rushing = categories.get("rushing")
        if rushing:
            row = _latest_season_row(rushing)
            if row:
                p = _parse_row(rushing.get("names", []), row.get("stats", []))
                gp = p.get("gamesPlayed")
                if gp:
                    result["rush_yds"] = (p.get("rushingYards") or 0) / gp
                    result["rush_td"] = (p.get("rushingTouchdowns") or 0) / gp
        receiving = categories.get("receiving")
        if receiving:
            row = _latest_season_row(receiving)
            if row:
                p = _parse_row(receiving.get("names", []), row.get("stats", []))
                gp = p.get("gamesPlayed")
                if gp:
                    result["rec"] = (p.get("receptions") or 0) / gp
                    result["rec_yds"] = (p.get("receivingYards") or 0) / gp
                    result["rec_td"] = (p.get("receivingTouchdowns") or 0) / gp
        return result or None
    except (KeyError, TypeError, ZeroDivisionError) as e:
        log.warning("Failed to parse ESPN athlete stats for %s athlete %s: %s", league, athlete_id, e)
        return None


def get_scoreboard_team_ids(league, scoreboard_json):
    """Map abbreviation -> ESPN numeric team id from a scoreboard payload, for injury lookups."""
    mapping = {}
    if not scoreboard_json:
        return mapping
    for ev in scoreboard_json.get("events", []):
        try:
            for c in ev["competitions"][0]["competitors"]:
                mapping[c["team"]["abbreviation"]] = c["team"]["id"]
        except (KeyError, IndexError):
            continue
    return mapping
