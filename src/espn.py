"""
ESPN public (undocumented) scoreboard/injury endpoints. No API key required.

These are unofficial endpoints -- every call here is wrapped so a failure or an
unexpected shape returns None/[] rather than raising, and the caller is expected to
log that and fall back (e.g. to a web search) rather than guess.
"""
import logging

import requests

log = logging.getLogger(__name__)

SPORT_PATH = {
    "NBA": "basketball/nba",
    "NFL": "football/nfl",
}

TIMEOUT = 15


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
            })
        except (KeyError, IndexError, StopIteration, TypeError, ValueError) as e:
            log.error("Failed to parse ESPN event for %s: %s (%s)", league, e, ev.get("id"))
            continue
    return out


def get_team_injuries(league, espn_team_id):
    """
    Returns a list of {name, position, status} for a team, or None on failure.
    espn_team_id is ESPN's numeric team id (not the abbreviation) -- pass the id from
    the scoreboard team object's "id" field.
    """
    url = (f"http://site.api.espn.com/apis/site/v2/sports/{SPORT_PATH[league]}"
           f"/teams/{espn_team_id}/injuries")
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("ESPN injuries fetch failed for %s team %s: %s", league, espn_team_id, e)
        return None

    out = []
    try:
        for item in data.get("injuries", []):
            athlete = item.get("athlete", {})
            out.append({
                "name": athlete.get("displayName", "unknown"),
                "position": (athlete.get("position") or {}).get("abbreviation"),
                "status": item.get("status") or (item.get("type") or {}).get("description"),
            })
    except (KeyError, TypeError) as e:
        log.error("Failed to parse ESPN injuries for %s team %s: %s", league, espn_team_id, e)
        return None
    return out


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
