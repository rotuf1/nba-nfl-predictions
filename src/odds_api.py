"""
the-odds-api.com moneyline odds, DraftKings only. Requires ODDS_API_KEY (free tier).
See MODEL.md section 5 for the de-vig formula.
"""
import logging

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports"
TIMEOUT = 15

SPORT_KEY = {"NBA": "basketball_nba", "NFL": "americanfootball_nfl"}


def american_to_implied_prob(odds):
    if odds is None:
        return None
    odds = float(odds)
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def devig(implied_a, implied_b):
    """Normalize two implied probabilities (which sum to >100% due to vig) to sum to 1."""
    total = implied_a + implied_b
    if total <= 0:
        return None, None
    return implied_a / total, implied_b / total


def get_draftkings_odds(league, api_key):
    """
    Fetch all upcoming DraftKings moneylines for a league.

    Returns a list of dicts: {home_team, away_team, commence_time, home_odds, away_odds}
    using the-odds-api's own team name strings (e.g. "Los Angeles Rams"), or None on
    total failure (bad key, network down, etc.) so the caller can mark odds
    "unavailable" rather than guess.
    """
    sport = SPORT_KEY.get(league)
    if not sport or not api_key:
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/{sport}/odds",
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
                "bookmakers": "draftkings",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        events = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("the-odds-api fetch failed for %s: %s", league, e)
        return None

    out = []
    for ev in events:
        home_team = ev.get("home_team")
        away_team = ev.get("away_team")
        home_odds = away_odds = None
        for bk in ev.get("bookmakers", []):
            if bk.get("key") != "draftkings":
                continue
            for market in bk.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") == home_team:
                        home_odds = outcome.get("price")
                    elif outcome.get("name") == away_team:
                        away_odds = outcome.get("price")
        out.append({
            "home_team": home_team,
            "away_team": away_team,
            "commence_time": ev.get("commence_time"),
            "home_odds": home_odds,
            "away_odds": away_odds,
        })
    return out


def match_game(odds_list, home_name, away_name):
    """Find the odds_list entry matching an ESPN home/away displayName pair, or None."""
    if not odds_list:
        return None
    for ev in odds_list:
        if ev["home_team"] == home_name and ev["away_team"] == away_name:
            return ev
    # Fall back to loose substring matching in case of minor naming differences.
    for ev in odds_list:
        h, a = ev.get("home_team") or "", ev.get("away_team") or ""
        if (h in home_name or home_name in h) and (a in away_name or away_name in a):
            return ev
    return None


def get_events(league, api_key):
    """
    (events, credits_remaining) -- upcoming events [{id, home_team, away_team,
    commence_time}] for a league. This endpoint costs no credits; the remaining-credit
    count comes from the response headers (None if absent). (None, None) on failure.
    """
    sport = SPORT_KEY.get(league)
    if not sport or not api_key:
        return None, None
    try:
        resp = requests.get(f"{BASE_URL}/{sport}/events", params={"apiKey": api_key}, timeout=TIMEOUT)
        resp.raise_for_status()
        events = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("the-odds-api events fetch failed for %s: %s", league, e)
        return None, None
    return events, _remaining(resp)


def get_player_props(league, api_key, event_id, markets):
    """
    (props, credits_remaining) for one event, DraftKings only. props is
    [{player, market, line, over, under}] with American prices. Costs one credit per
    market. (None, None) on failure.
    """
    sport = SPORT_KEY.get(league)
    try:
        resp = requests.get(
            f"{BASE_URL}/{sport}/events/{event_id}/odds",
            params={"apiKey": api_key, "bookmakers": "draftkings", "markets": ",".join(markets),
                    "oddsFormat": "american"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("the-odds-api player props fetch failed for %s event %s: %s", league, event_id, e)
        return None, None
    by_key = {}
    for bk in data.get("bookmakers", []):
        if bk.get("key") != "draftkings":
            continue
        for market in bk.get("markets", []):
            for o in market.get("outcomes", []):
                key = (o.get("description"), market.get("key"), o.get("point"))
                entry = by_key.setdefault(key, {"player": key[0], "market": key[1], "line": key[2]})
                entry[(o.get("name") or "").lower()] = o.get("price")
    props = [p for p in by_key.values() if p.get("over") is not None and p.get("under") is not None]
    return props, _remaining(resp)


def _remaining(resp):
    try:
        return int(float(resp.headers.get("x-requests-remaining")))
    except (TypeError, ValueError):
        return None
