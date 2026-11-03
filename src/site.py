"""
Renders /docs/index.html: a single self-contained static page (inline CSS/JS, no
build step) with one card per game. See daily_run.py for how a "game" dict is built.
"""
import html as htmlmod

DISCLAIMER = (
    "This site presents the output of a statistical model (team Elo ratings plus "
    "documented adjustments) alongside public betting-market prices, purely for "
    "informational and entertainment purposes. It is not betting advice, and nothing "
    "here is a recommendation to wager on anything. See MODEL.md in the repo for the "
    "exact methodology."
)


def esc(s):
    return htmlmod.escape(str(s)) if s is not None else ""


def fmt_pct(p):
    return "unavailable" if p is None else f"{p * 100:.1f}%"


def fmt_odds(o):
    if o is None:
        return "unavailable"
    o = int(o)
    return f"+{o}" if o > 0 else str(o)


def render_injury_group(team_name, injuries, ok):
    if not ok:
        body = "unavailable"
    elif not injuries:
        body = "none reported"
    else:
        items = "".join(
            f"<li>{esc(i['name'])} ({esc(i.get('position') or '?')}) — {esc(i.get('status') or 'unknown')}</li>"
            for i in injuries
        )
        body = f"<ul>{items}</ul>"
    return f'<div class="injury-group"><span class="injury-team">{esc(team_name)}</span>{body}</div>'


def render_game_card(game):
    """
    game: {
      league, tipoff_et_str, away_abbr, away_name, home_abbr, home_name,
      status ('scheduled'|'live'|'final'), final_score (str or None),
      pick_team_name, model_pick_prob,
      market_pick_team_name, market_pick_prob, edge,
      dk_home_odds, dk_away_odds,
      kalshi_home_prob, kalshi_away_prob (or None each),
      polymarket_home_prob, polymarket_away_prob (or None each),
      why (list of str),
    }
    """
    g = game
    pick_team = g.get("pick_team_name")
    if pick_team is not None:
        pick_html = f'<span class="pick-team">{esc(pick_team)}</span> {fmt_pct(g["model_pick_prob"])}'
    else:
        pick_html = "unavailable"

    market_pick_team = g.get("market_pick_team_name")
    if market_pick_team is not None:
        market_pick_html = (f'<span class="pick-team">{esc(market_pick_team)}</span> '
                             f'{fmt_pct(g["market_pick_prob"])}')
    else:
        market_pick_html = "unavailable"

    edge_html = "unavailable"
    if g.get("edge") is not None:
        edge_pts = g["edge"] * 100
        if abs(edge_pts) < 0.5:
            edge_html = "~0 pts — model and market roughly agree"
        elif g["edge"] > 0:
            edge_html = f"+{edge_pts:.1f} pts — model is more confident than the market"
        else:
            edge_html = f"{edge_pts:.1f} pts — model is less confident than the market"

    status_badge = ""
    if g.get("status") == "final":
        status_badge = f'<span class="badge badge-final">FINAL {esc(g.get("final_score", ""))}</span>'
    elif g.get("status") == "live":
        status_badge = '<span class="badge badge-live">LIVE · pre-kickoff card</span>'

    dk_line = (
        f'DK: {esc(g["away_abbr"])} {fmt_odds(g.get("dk_away_odds"))} / '
        f'{esc(g["home_abbr"])} {fmt_odds(g.get("dk_home_odds"))}'
    )

    kalshi_html = "no market"
    if g.get("kalshi_home_prob") is not None:
        kalshi_html = (
            f'{esc(g["away_abbr"])} {fmt_pct(g["kalshi_away_prob"])} / '
            f'{esc(g["home_abbr"])} {fmt_pct(g["kalshi_home_prob"])}'
        )

    poly_html = "no market"
    if g.get("polymarket_home_prob") is not None:
        poly_html = (
            f'{esc(g["away_abbr"])} {fmt_pct(g["polymarket_away_prob"])} / '
            f'{esc(g["home_abbr"])} {fmt_pct(g["polymarket_home_prob"])}'
        )

    return f"""
    <div class="card">
      <div class="card-header">
        <span class="matchup">{esc(g['away_name'])} @ {esc(g['home_name'])}</span>
        {status_badge}
      </div>
      <div class="tipoff">{esc(g['tipoff_et_str'])} ET</div>

      <div class="section">
        <div class="section-title">Model pick</div>
        <div class="pick">{pick_html}</div>
      </div>

      <div class="section">
        <div class="section-title">Market pick (de-vigged)</div>
        <div class="pick">{market_pick_html}</div>
        <div class="odds-line">{dk_line}</div>
        <div class="odds-line">Kalshi: {kalshi_html}</div>
        <div class="odds-line">Polymarket: {poly_html}</div>
        <div class="edge">Edge: {edge_html}</div>
      </div>

      <div class="section">
        <div class="section-title">Why</div>
        <ul class="why">{"".join(f"<li>{esc(b)}</li>" for b in g.get('why') or [])}</ul>
      </div>

      <div class="section">
        <div class="section-title">Injury report</div>
        {render_injury_group(g['away_name'], g.get('away_injuries') or [], g.get('away_injuries_ok', False))}
        {render_injury_group(g['home_name'], g.get('home_injuries') or [], g.get('home_injuries_ok', False))}
      </div>
    </div>"""


def fmt_record(wins, losses):
    total = wins + losses
    pct = f" ({wins / total * 100:.1f}%)" if total else ""
    return f"{wins}-{losses}{pct}"


def render_record(record):
    """record: {'overall': (wins, losses), 'NFL': (wins, losses), 'NBA': (wins, losses)}"""
    if not record or record.get("overall", (0, 0)) == (0, 0):
        return ""
    parts = []
    for label in ("NFL", "NBA"):
        w, l = record.get(label, (0, 0))
        if w + l:
            parts.append(f"{label} {fmt_record(w, l)}")
    overall_w, overall_l = record["overall"]
    parts.append(f"Overall {fmt_record(overall_w, overall_l)}")
    return (f'<div class="record"><span class="section-title">Model record '
            f'(correct picks vs. incorrect, final games only)</span> {" · ".join(parts)}</div>')


def render_props(props_data):
    """NBA player-prop section (src/props.py's run() output), or "" if there's nothing."""
    if not props_data or not (props_data["picks"] or props_data["projections"]):
        return ""
    rec = props_data["record"]
    rec_html = ""
    if rec["wins"] + rec["losses"]:
        profit = rec["units"] * 100
        rec_html = (f'<div class="record"><span class="section-title">Prop record (graded overs, '
                    f'$100 per pick at DraftKings prices)</span> {fmt_record(rec["wins"], rec["losses"])} · '
                    f'{"+" if profit >= 0 else "-"}${abs(profit):,.0f}</div>')

    picks_html = ""
    if props_data["picks"]:
        result_badge = {"win": '<span class="res-win">✓</span>', "loss": '<span class="res-loss">✗</span>',
                        "void": '<span class="res-void">void</span>'}
        rows = "".join(
            f"<tr><td><strong>{esc(p['player'])}</strong> <span class=\"muted\">{esc(p['team'])}</span></td>"
            f"<td>Over {p['line']:g} {esc(p['stat'])}</td><td>{fmt_odds(p['price'])}</td>"
            f"<td>{p['proj']:.1f}</td><td>{p['prob'] * 100:.0f}%</td>"
            f"<td>{result_badge.get(p['result'], '')}"
            f"{'' if p['actual'] is None else ' ' + format(p['actual'], 'g')}</td>"
            f"<td class=\"muted\">{esc(p['matchup'])}</td></tr>"
            for p in props_data["picks"]
        )
        picks_html = (f'<div class="table-wrap"><table class="props"><thead><tr><th>Player</th><th>Pick</th>'
                      f'<th>DK</th><th>Proj</th><th>Chance</th><th>Result</th><th>Game</th></tr></thead>'
                      f'<tbody>{rows}</tbody></table></div>')

    proj_html = ""
    if props_data["projections"]:
        blocks = "".join(
            f'<div class="proj-game"><div class="section-title">{esc(g["matchup"])} · {esc(g["tipoff"])}</div>'
            f'<table class="props"><thead><tr><th>Player</th><th>Min</th><th>Pts</th><th>Reb</th><th>Ast</th>'
            f'</tr></thead><tbody>'
            + "".join(f"<tr><td>{esc(p['name'])} <span class=\"muted\">{esc(p['team'])}</span></td>"
                      f"<td>{p['min']:.0f}</td><td>{p['proj']['PTS']:.1f}</td><td>{p['proj']['REB']:.1f}</td>"
                      f"<td>{p['proj']['AST']:.1f}</td></tr>" for p in g["players"])
            + "</tbody></table></div>"
            for g in props_data["projections"]
        )
        proj_html = (f'<details class="proj"><summary>Projections for games without DraftKings lines yet '
                     f'({len(props_data["projections"])})</summary>{blocks}</details>')

    return (f'<h2>NBA player props <span class="badge badge-final">experimental</span></h2>'
            f'<p class="props-note">Overs only. My projection from ESPN season averages, blended 50/50 '
            f'with DraftKings\' no-vig price ("Chance"). Lines are pulled within ~6 hours of tip, '
            f'as odds-API credits allow. See MODEL.md section 8.</p>{rec_html}{picks_html}{proj_html}')


def render_page(nba_games, nfl_games, updated_et_str, warnings=None, record=None, props_data=None):
    warnings = warnings or []
    warnings_html = ""
    if warnings:
        items = "".join(f"<li>{esc(w)}</li>" for w in warnings)
        warnings_html = f'<div class="warnings"><strong>Data notes:</strong><ul>{items}</ul></div>'
    record_html = render_record(record)

    def section(title, games):
        if not games:
            return ""
        cards = "".join(render_game_card(g) for g in games)
        return f'<h2>{esc(title)}</h2><div class="cards">{cards}</div>'

    body_sections = section("NFL", nfl_games) + section("NBA", nba_games) + render_props(props_data)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NBA &amp; NFL Predictions</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f7f7f8;
    --card-bg: #ffffff;
    --text: #1a1a1a;
    --muted: #6b6b6b;
    --border: #e2e2e2;
    --accent: #1a56db;
    --pos: #0a7d34;
    --neg: #b91c1c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #121212;
      --card-bg: #1e1e1e;
      --text: #f0f0f0;
      --muted: #a0a0a0;
      --border: #333333;
      --accent: #6ea8fe;
      --pos: #4ade80;
      --neg: #f87171;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 16px;
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.2em; }}
  .updated {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1em; }}
  .disclaimer {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 0.85rem;
    color: var(--muted);
    margin-bottom: 1.5em;
  }}
  .warnings {{
    background: var(--card-bg);
    border: 1px solid #f0ad4e;
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 0.85rem;
    margin-bottom: 1.5em;
  }}
  .record {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 0.95rem;
    margin-bottom: 1.5em;
  }}
  .record .section-title {{ display: block; margin-bottom: 3px; }}
  h2 {{ font-size: 1.2rem; margin-top: 1.5em; border-bottom: 2px solid var(--border); padding-bottom: 4px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; margin-top: 12px; }}
  .card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
  }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
  .matchup {{ font-weight: 600; }}
  .badge {{ font-size: 0.7rem; padding: 2px 6px; border-radius: 4px; white-space: nowrap; }}
  .badge-final {{ background: var(--border); color: var(--muted); }}
  .badge-live {{ background: #c0392b; color: #fff; }}
  .tipoff {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 10px; }}
  .section {{ margin-top: 10px; }}
  .section-title {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 3px; }}
  .pick {{ font-size: 1.05rem; }}
  .pick-team {{ font-weight: 700; }}
  .edge {{ font-size: 0.85rem; color: var(--accent); margin-top: 4px; }}
  .odds-line {{ font-size: 0.88rem; }}
  .why {{ font-size: 0.88rem; color: var(--text); line-height: 1.4; margin: 0; padding-left: 18px; }}
  .why li {{ margin-bottom: 3px; }}
  .injury-group {{ font-size: 0.85rem; margin-top: 4px; }}
  .injury-team {{ font-weight: 600; margin-right: 4px; }}
  .injury-group ul {{ margin: 2px 0 6px 0; padding-left: 18px; }}
  .injury-group li {{ line-height: 1.35; }}
  .props-note {{ font-size: 0.85rem; color: var(--muted); margin: 8px 0 12px; }}
  .table-wrap {{ overflow-x: auto; }}
  table.props {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; background: var(--card-bg); }}
  table.props th, table.props td {{ padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }}
  table.props th {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); font-weight: 600; }}
  .muted {{ color: var(--muted); font-size: 0.8rem; }}
  .res-win {{ color: var(--pos); font-weight: 700; }}
  .res-loss {{ color: var(--neg); font-weight: 700; }}
  .res-void {{ color: var(--muted); }}
  details.proj {{ margin-top: 14px; }}
  details.proj summary {{ cursor: pointer; color: var(--accent); font-size: 0.9rem; }}
  .proj-game {{ margin-top: 12px; overflow-x: auto; }}
  footer {{ margin-top: 2em; color: var(--muted); font-size: 0.8rem; text-align: center; }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>NBA &amp; NFL Predictions</h1>
    <div class="updated">Predictions last updated {esc(updated_et_str)} ET</div>
    <div class="disclaimer">{esc(DISCLAIMER)}</div>
    {record_html}
    {warnings_html}
    {body_sections}
    <footer>Model methodology: see MODEL.md in the source repo. Data: ESPN, the-odds-api.com (DraftKings), Kalshi, Polymarket.</footer>
  </div>
</body>
</html>"""
