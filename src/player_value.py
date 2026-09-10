"""
Per-player "value score" used to weight the injury adjustment by how much a specific
player's absence actually matters, instead of counting every Out/Doubtful player the
same. See MODEL.md section 3 for the full writeup and the reasoning behind it.

NBA uses John Hollinger's published Game Score formula -- a well-known, fully public
per-game box-score formula, not something invented for this project.

NFL doesn't have an equivalent free, live, per-game, cross-position value stat.
Pro-Football-Reference's real Approximate Value is genuinely complex (different
formulas per position group, uses team context, computed from full-season data) and
isn't available via any free live API mid-season -- it can't be faithfully replicated
here. Instead, skill positions (QB/RB/WR/TE) use a production score built from
standard half-PPR fantasy-football scoring conventions (yards, touchdowns,
interceptions) -- itself a long-established, publicly documented scoring convention,
not invented for this project, just repurposed as a value proxy. Other positions (line,
defense, specialists) have no comparably clean free per-game production stat, so
nfl_production_score returns None for them and the caller falls back to a flat
per-status penalty instead.
"""

NFL_SKILL_POSITIONS = {"QB", "RB", "FB", "WR", "TE"}


def nba_game_score(stats):
    """
    Hollinger Game Score from per-game averages: pts, fgm, fga, ftm, fta, oreb, dreb,
    ast, blk, stl, pf, tov. Returns None if required fields are missing.
    """
    if not stats:
        return None
    try:
        return (
            stats["pts"]
            + 0.4 * stats["fgm"]
            - 0.7 * stats["fga"]
            - 0.4 * (stats["fta"] - stats["ftm"])
            + 0.7 * stats["oreb"]
            + 0.3 * stats["dreb"]
            + stats["stl"]
            + 0.7 * stats["ast"]
            + 0.7 * stats["blk"]
            - 0.4 * stats["pf"]
            - stats["tov"]
        )
    except (KeyError, TypeError):
        return None


def nfl_production_score(stats, position):
    """
    Half-PPR-style production score from per-game averages: pass_yds, pass_td,
    pass_int, rush_yds, rush_td, rec, rec_yds, rec_td (any subset). Weights: 0.04
    pt/passing yard, 4 pt/passing TD, -2 pt/INT, 0.1 pt/rushing or receiving yard, 6
    pt/rushing or receiving TD, 0.5 pt/reception.

    Returns None for a non-skill position, or if no relevant stats are available --
    the caller should fall back to a flat penalty in that case.
    """
    if position not in NFL_SKILL_POSITIONS or not stats:
        return None

    score = 0.0
    found_any = False

    if stats.get("pass_yds") is not None:
        score += stats["pass_yds"] * 0.04
        score += (stats.get("pass_td") or 0) * 4
        score -= (stats.get("pass_int") or 0) * 2
        found_any = True

    if stats.get("rush_yds") is not None:
        score += stats["rush_yds"] * 0.1
        score += (stats.get("rush_td") or 0) * 6
        found_any = True

    if stats.get("rec_yds") is not None:
        score += stats["rec_yds"] * 0.1
        score += (stats.get("rec_td") or 0) * 6
        score += (stats.get("rec") or 0) * 0.5
        found_any = True

    return score if found_any else None
