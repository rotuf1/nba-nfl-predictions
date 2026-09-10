"""
Kalshi public market data (docs.kalshi.com) -- no API key needed for reading prices.

Game-winner markets live under series KXNFLGAME (NFL) / KXNBAGAME (NBA), one event per
game with a ticker like "KXNFLGAME-26SEP10SFLAR" (away+home, 2-digit year + 3-letter
month + 2-digit day), and one binary yes/no market per team under that event
(e.g. "...-SF" / "...-LAR"). This module is best-effort: if a matching market can't be
found, callers get None and should show "no market", never a fabricated number.
"""
import logging

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT = 15

SERIES = {"NFL": "KXNFLGAME", "NBA": "KXNBAGAME"}

# Kalshi's team abbreviations mostly match ESPN's, but not always. Confirmed by hand
# for NFL (JAC, WAS); NBA entries beyond NYK/SAS are a best-effort guess from common
# naming conventions and unverified -- if wrong, the lookup below just fails safely
# and the site shows "no market" rather than a wrong number.
KALSHI_TO_ESPN = {
    "NFL": {"JAC": "JAX", "WAS": "WSH"},
    "NBA": {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"},
}


def _espn_to_kalshi_abbr(league, espn_abbr):
    reverse = {v: k for k, v in KALSHI_TO_ESPN[league].items()}
    return reverse.get(espn_abbr, espn_abbr)


def _date_ticker_component(date_et):
    """date_et: a date object -> e.g. '26SEP10'."""
    return date_et.strftime("%y%b%d").upper()


def _price_to_prob(market):
    bid = float(market.get("yes_bid_dollars") or 0)
    ask = float(market.get("yes_ask_dollars") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    last = market.get("last_price_dollars")
    if last is not None:
        try:
            v = float(last)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return None


def _fetch_markets_for_event(event_ticker):
    try:
        resp = requests.get(
            f"{BASE_URL}/markets", params={"event_ticker": event_ticker}, timeout=TIMEOUT
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("markets", [])
    except (requests.RequestException, ValueError) as e:
        log.warning("Kalshi markets fetch failed for %s: %s", event_ticker, e)
        return None


def _extract_probs(markets, home_kalshi, away_kalshi):
    home_prob = away_prob = None
    for m in markets:
        ticker = m.get("ticker", "")
        suffix = ticker.rsplit("-", 1)[-1]
        prob = _price_to_prob(m)
        if suffix == home_kalshi:
            home_prob = prob
        elif suffix == away_kalshi:
            away_prob = prob
    if home_prob is None or away_prob is None:
        return None
    return {"home_prob": home_prob, "away_prob": away_prob}


def get_game_market(league, date_et, home_abbr, away_abbr):
    """
    Returns {"home_prob": float, "away_prob": float, "event_ticker": str} or None if no
    matching Kalshi market could be found for this game.
    """
    series = SERIES.get(league)
    if not series:
        return None

    home_k = _espn_to_kalshi_abbr(league, home_abbr)
    away_k = _espn_to_kalshi_abbr(league, away_abbr)
    date_component = _date_ticker_component(date_et)

    # Fast path: guess the ticker directly (this is the observed convention: away+home).
    guess_ticker = f"{series}-{date_component}{away_k}{home_k}"
    markets = _fetch_markets_for_event(guess_ticker)
    if markets:
        result = _extract_probs(markets, home_k, away_k)
        if result:
            result["event_ticker"] = guess_ticker
            return result

    # Fallback: list open events for the series and search for a ticker containing
    # both team codes and the date component, in case the away/home order differs.
    try:
        resp = requests.get(
            f"{BASE_URL}/events",
            params={"series_ticker": series, "status": "open", "limit": 200},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except (requests.RequestException, ValueError) as e:
        log.warning("Kalshi events list failed for %s: %s", series, e)
        return None

    for ev in events:
        ticker = ev.get("event_ticker", "")
        if date_component in ticker and home_k in ticker and away_k in ticker:
            markets = _fetch_markets_for_event(ticker)
            if markets:
                result = _extract_probs(markets, home_k, away_k)
                if result:
                    result["event_ticker"] = ticker
                    return result

    return None
