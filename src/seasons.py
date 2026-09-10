"""Season-label conventions, shared by the historical seeders and the daily run."""


def nfl_season_for_date(d):
    """NFL season is labeled by its starting year (e.g. Feb 2027 Super Bowl -> season 2026)."""
    return d.year - 1 if d.month <= 2 else d.year


def nba_season_for_date(d):
    """NBA season is labeled by its ending year (e.g. Oct 2026 tip-off -> season 2027)."""
    return d.year + 1 if d.month >= 8 else d.year


def season_for(league, d):
    return nfl_season_for_date(d) if league == "NFL" else nba_season_for_date(d)
