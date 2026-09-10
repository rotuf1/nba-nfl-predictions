"""
Polymarket's Gamma API (gamma-api.polymarket.com) -- public, no API key needed.

There's no clean "give me today's NFL games" endpoint, so this searches by team
nickname pair (e.g. "49ers Rams") and picks the event whose title is exactly
"{away nickname} vs. {home nickname}" (the plain moneyline-style market), close in
date to the game -- as opposed to unrelated matches like season-series or prop markets
that also mention both teams. Best-effort: returns None (not a guess) if nothing
matches closely enough.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
import json as jsonlib

import requests

log = logging.getLogger(__name__)

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
TIMEOUT = 15

# Most team nicknames are just the last word of the ESPN display name; these have a
# multi-word nickname instead.
NICKNAME_OVERRIDES = {
    "Portland Trail Blazers": "Trail Blazers",
}


def _nickname(display_name):
    if not display_name:
        return None
    return NICKNAME_OVERRIDES.get(display_name, display_name.split()[-1])


def get_game_market(league, date_et, home_name, away_name):
    """
    date_et: a date object for the game (America/New_York calendar date).
    home_name / away_name: ESPN team displayName, e.g. "Los Angeles Rams".

    Returns {"home_prob": float, "away_prob": float, "slug": str} or None.
    """
    home_nick = _nickname(home_name)
    away_nick = _nickname(away_name)
    if not home_nick or not away_nick:
        return None

    try:
        resp = requests.get(
            SEARCH_URL,
            params={"q": f"{away_nick} {home_nick}", "events_status": "active",
                    "limit_per_type": 20},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Polymarket search failed for %s @ %s: %s", away_name, home_name, e)
        return None

    title_re = re.compile(
        rf"^{re.escape(away_nick)}\s+vs\.?\s+{re.escape(home_nick)}$", re.IGNORECASE
    )

    candidates = []
    for ev in data.get("events", []):
        if ev.get("closed"):
            continue
        title = ev.get("title", "")
        if not title_re.match(title.strip()):
            continue
        end_date = ev.get("endDate")
        try:
            ev_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            continue
        ev_date_et = ev_dt.astimezone(timezone(timedelta(hours=-4))).date()
        if abs((ev_date_et - date_et).days) > 1:
            continue
        candidates.append(ev)

    if not candidates:
        return None

    ev = candidates[0]
    markets = ev.get("markets") or []
    if not markets:
        return None
    market = markets[0]
    try:
        outcomes = jsonlib.loads(market["outcomes"])
        prices = [float(p) for p in jsonlib.loads(market["outcomePrices"])]
    except (KeyError, ValueError, TypeError) as e:
        log.warning("Polymarket market parse failed for %s: %s", ev.get("slug"), e)
        return None

    prob_by_nick = dict(zip(outcomes, prices))
    home_prob = prob_by_nick.get(home_nick)
    away_prob = prob_by_nick.get(away_nick)
    if home_prob is None or away_prob is None:
        return None

    return {"home_prob": home_prob, "away_prob": away_prob, "slug": ev.get("slug")}
