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
        prob = g["model_pick_prob"]
        pick_html = (f'<div class="pick-row"><span class="pick-big">{esc(pick_team)}</span>'
                     f'<span class="pick-prob">{fmt_pct(prob)}</span></div>'
                     f'<div class="conf-bar"><span style="width:{max(0, min(prob, 1)) * 100:.0f}%"></span></div>')
    else:
        pick_html = '<div class="pick-row"><span class="pick-big">unavailable</span></div>'

    market_pick_team = g.get("market_pick_team_name")
    if market_pick_team is not None:
        market_pick_html = (f'<strong>{esc(market_pick_team)}</strong> '
                             f'{fmt_pct(g["market_pick_prob"])}')
    else:
        market_pick_html = "unavailable"

    edge_html = ""
    if g.get("edge") is not None:
        edge_pts = g["edge"] * 100
        if abs(edge_pts) < 0.5:
            edge_html = '<span class="chip">model ≈ market</span>'
        elif g["edge"] > 0:
            edge_html = f'<span class="chip chip-pos">+{edge_pts:.1f} pts vs market</span>'
        else:
            edge_html = f'<span class="chip chip-neg">{edge_pts:.1f} pts vs market</span>'

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

      <div class="pick-box">
        <div class="pick-label">Model pick {edge_html}</div>
        {pick_html}
      </div>

      <div class="section market">
        <div>Market: {market_pick_html}</div>
        <div class="odds-line">{dk_line} · Kalshi: {kalshi_html} · Polymarket: {poly_html}</div>
      </div>

      <div class="section">
        <div class="section-title">Why</div>
        <ul class="why">{"".join(f"<li>{esc(b)}</li>" for b in g.get('why') or [])}</ul>
      </div>

      <details class="section injuries">
        <summary class="section-title">Injury report ({len(g.get('away_injuries') or []) + len(g.get('home_injuries') or [])})</summary>
        {render_injury_group(g['away_name'], g.get('away_injuries') or [], g.get('away_injuries_ok', False))}
        {render_injury_group(g['home_name'], g.get('home_injuries') or [], g.get('home_injuries_ok', False))}
      </details>
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


def fmt_price(book, american):
    """'DK -127', or 'Kalshi 72¢' (the cost of a $1 contract, fee included)."""
    if book != "Kalshi":
        return f"DK {fmt_odds(american)}"
    cost = -american / (-american + 100) if american < 0 else 100 / (american + 100)
    return f"Kalshi {cost * 100:.0f}¢"


def render_props(props_data):
    """NBA player-prop section (src/props.py's run() output), or "" if there's nothing."""
    if not props_data or not (props_data["picks"] or props_data["projections"]):
        return ""
    rec = props_data["record"]
    rec_html = ""
    if rec["wins"] + rec["losses"]:
        rec_html = (f'<div class="record"><span class="section-title">Prop record</span> '
                    f'{fmt_record(rec["wins"], rec["losses"])}</div>')

    picks_html = ""
    if props_data["picks"]:
        result_badge = {"win": '<span class="res-win">✓</span>', "loss": '<span class="res-loss">✗</span>',
                        "void": '<span class="res-void">void</span>'}
        rows = "".join(
            f"<tr><td><strong>{esc(p['player'])}</strong> <span class=\"muted\">{esc(p['team'])}</span></td>"
            f"<td><span class=\"prop-pick\">{(p.get('side') or 'over').title()} {p['line']:g} {esc(p['stat'])}</span></td>"
            f"<td>{fmt_price(p.get('book') or 'DK', p['price'])}</td>"
            f"<td>{p['proj']:.1f}</td><td><strong>{p['prob'] * 100:.0f}%</strong></td>"
            f"<td>{result_badge.get(p['result'], '')}"
            f"{'' if p['actual'] is None else ' ' + format(p['actual'], 'g')}</td>"
            f"<td class=\"muted\">{esc(p['matchup'])}</td></tr>"
            for p in props_data["picks"]
        )
        picks_html = (f'<div class="table-wrap"><table class="props"><thead><tr><th>Player</th><th>Pick</th>'
                      f'<th>Price</th><th>Proj</th><th>Chance</th><th>Result</th><th>Game</th></tr></thead>'
                      f'<tbody>{rows}</tbody></table></div>')

    proj_html = ""
    if props_data["projections"]:
        pick_tag = '<span class="prop-pick">PICK</span>'
        pick_dot = ' <span class="pick-dot">PICK</span>'

        def player_row(pl, has_lines):
            has_pick = any(e["pick"] for e in pl["props"])
            if pl["props"]:
                rows = "".join(
                    f'<tr class="{"is-pick" if e["pick"] else ""}"><td>{esc(e["stat"])}</td>'
                    f'<td>{e["side"].title()} {e["line"]:g}</td><td>{fmt_price(e["book"], e["price"])}</td>'
                    f'<td>{pl["proj"][e["stat"]]:.1f}</td><td>{e["chance"] * 100:.0f}%</td>'
                    f'<td>{pick_tag if e["pick"] else ""}</td></tr>'
                    for e in pl["props"])
                body = (f'<table class="props mini"><thead><tr><th>Stat</th><th>Line</th><th>Price</th>'
                        f'<th>Proj</th><th>Chance</th><th></th></tr></thead><tbody>{rows}</tbody></table>')
            else:
                body = (f'<div class="muted no-lines">'
                        f'{"No props at 60%+." if has_lines else "No prop lines yet."}'
                        f'</div>')
            return (f'<details class="player"><summary class="prow">'
                    f'<span class="pname">{esc(pl["name"])} <span class="muted">{esc(pl["team"])}</span>'
                    f'{pick_dot if has_pick else ""}</span>'
                    f'<span>{pl["min"]:.0f}</span><span>{pl["proj"]["PTS"]:.1f}</span>'
                    f'<span>{pl["proj"]["REB"]:.1f}</span><span>{pl["proj"]["AST"]:.1f}</span></summary>'
                    f'{body}</details>')

        blocks = ""
        for g in props_data["projections"]:
            status = (f'{g["n_picks"]} pick{"" if g["n_picks"] == 1 else "s"}' if g["has_lines"]
                      else "no lines yet")
            blocks += (f'<details class="proj-game"><summary><span class="g-title">{esc(g["matchup"])}</span> '
                       f'<span class="muted">{esc(g["tipoff"])} · {status}</span></summary>'
                       f'<div class="prow prow-head"><span>Player</span><span>Min</span><span>Pts</span>'
                       f'<span>Reb</span><span>Ast</span></div>'
                       + "".join(player_row(pl, g["has_lines"]) for pl in g["players"]) + '</details>')
        proj_html = (f'<h3 class="sub">Player projections <span class="muted">(tap a player for their props)</span>'
                     f'</h3>{blocks}')

    return (f'<h2>NBA player props <span class="badge badge-final">experimental</span></h2>'
            f'<p class="props-note">Overs and unders at 60%+ only, priced -150 or better (pays 1.67x or more). '
            f'My projection from ESPN season averages (starters\' minutes trimmed when a blowout is expected), '
            f'blended 50/50 with the market ("Chance"): the consensus of up to 9 sportsbooks where their lines are in, '
            f'otherwise Kalshi\'s own price. Kalshi prices (points, rebounds, assists, PRA) cover every game; '
            f'DraftKings lines are added within ~6 hours of tip as odds-API credits allow. Kalshi prices '
            f'include its trading fee. See MODEL.md section 8.</p>{rec_html}{picks_html}{proj_html}')


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
    --accent-soft: #e8efff;
    --track: #dfe5f2;
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
      --accent-soft: #1b2740;
      --track: #2c3a55;
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
  .pick-box {{ background: var(--accent-soft); border-left: 4px solid var(--accent); border-radius: 8px; padding: 10px 12px; margin-top: 4px; }}
  .pick-label {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent); font-weight: 700; display: flex; justify-content: space-between; align-items: center; gap: 6px; }}
  .pick-row {{ display: flex; justify-content: space-between; align-items: baseline; gap: 10px; margin-top: 4px; }}
  .pick-big {{ font-size: 1.35rem; font-weight: 800; line-height: 1.2; }}
  .pick-prob {{ font-size: 1.35rem; font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; }}
  .conf-bar {{ height: 6px; background: var(--track); border-radius: 3px; margin-top: 8px; overflow: hidden; }}
  .conf-bar span {{ display: block; height: 100%; background: var(--accent); }}
  .chip {{ font-size: 0.68rem; font-weight: 600; text-transform: none; letter-spacing: 0; padding: 2px 7px; border-radius: 10px; background: var(--card-bg); color: var(--muted); white-space: nowrap; }}
  .chip-pos {{ color: var(--pos); }}
  .chip-neg {{ color: var(--neg); }}
  .market {{ font-size: 0.85rem; color: var(--muted); }}
  .market strong {{ color: var(--text); }}
  details.injuries summary {{ cursor: pointer; }}
  .prop-pick {{ background: var(--accent-soft); color: var(--accent); font-weight: 700; padding: 2px 8px; border-radius: 6px; }}
  .odds-line {{ font-size: 0.8rem; color: var(--muted); margin-top: 2px; }}
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
  h3.sub {{ font-size: 1rem; margin: 1.4em 0 8px; }}
  details.proj-game {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin-top: 10px; }}
  details.proj-game > summary {{ cursor: pointer; font-size: 0.95rem; }}
  .g-title {{ font-weight: 700; }}
  .prow {{ display: grid; grid-template-columns: minmax(0, 1fr) 2.6rem 2.8rem 2.8rem 2.8rem; gap: 4px; align-items: center; padding: 7px 4px; font-size: 0.88rem; font-variant-numeric: tabular-nums; }}
  .prow > span:not(.pname) {{ text-align: right; }}
  .prow-head {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); border-bottom: 1px solid var(--border); margin-top: 8px; }}
  details.player {{ border-bottom: 1px solid var(--border); }}
  details.player > summary {{ cursor: pointer; list-style: none; }}
  details.player > summary::-webkit-details-marker {{ display: none; }}
  details.player > summary .pname::before {{ content: "▸ "; color: var(--muted); }}
  details.player[open] > summary .pname::before {{ content: "▾ "; }}
  details.player[open] > summary {{ background: var(--accent-soft); border-radius: 6px; }}
  .pname {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .pick-dot {{ font-size: 0.62rem; font-weight: 800; color: var(--accent); background: var(--accent-soft); padding: 1px 5px; border-radius: 4px; margin-left: 4px; }}
  table.props.mini {{ margin: 6px 0 10px; font-size: 0.82rem; }}
  table.props.mini tr.is-pick td {{ background: var(--accent-soft); font-weight: 700; }}
  .no-lines {{ padding: 4px 4px 10px 18px; }}
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
