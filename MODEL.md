# Model methodology

This document describes exactly how a prediction on the site is produced. Nothing here is
tuned to hindsight results after the fact without this file being updated to match.

## 1. Elo rating system

Every team in a league starts a fresh rating history at **1500**. Games are replayed in
chronological order (oldest first) to bring ratings up to date. Only **final score, home/away,
and date** are used — no play-by-play or advanced stats feed the Elo update itself.

For a game between a home team `H` and away team `A`:

```
expected_H = 1 / (1 + 10 ^ (-(rating_H + HOME_ADV - rating_A) / 400))
expected_A = 1 - expected_H

actual_H   = 1 if H won, 0 if H lost   (no soft credit for margin of victory)
actual_A   = 1 - actual_H

new_rating_H = rating_H + K * (actual_H - expected_H)
new_rating_A = rating_A + K * (actual_A - expected_A)
```

This is plain win/loss Elo — it deliberately does **not** weight by margin of victory. That's a
simplification: a 1-point win and a 30-point win move ratings by the same amount. It's easier to
audit and explain than a margin-of-victory-adjusted system, at some cost to accuracy.

### Constants (current)

| League | HOME_ADV | K-factor | Season regression |
|---|---|---|---|
| NBA | 100 Elo pts | 20 | new = 0.75 × old + 0.25 × 1500, applied once at the first game of each new season |
| NFL | 48 Elo pts | 20 | new = 0.75 × old + 0.25 × 1500, applied once at the first game of each new season |

These are standard starting points used by public Elo sports models (not derived from a formal
grid search on this data), chosen so ratings are transparent and stable rather than
over-fit. Season regression exists because rosters change materially between seasons — it pulls
every team 25% of the way back toward the 1500 mean before the new season's games are replayed,
so an early-season game a team wins isn't over-explained by two-year-old form.

Constants live in `src/elo.py` as `HOME_ADV` and `K_FACTOR` per league — change them there if you
retune, and update this table to match.

## 2. Rest adjustment (applied only to today's prediction, not stored back into Elo)

Rest is not part of the permanent Elo rating — it's a same-day nudge applied only when computing
today's win probability, since it reflects a transient state, not team strength.

- **NBA back-to-back**: a team playing on zero days of rest (played yesterday) gets a
  **-25 Elo point** adjustment to its effective rating for today's game only.
- **NFL short week**: a team playing on 5 or fewer days of rest since its last game (e.g. a
  Thursday game following a Sunday game) gets a **-25 Elo point** adjustment.
- **NFL extra rest**: a team coming off a bye week (10-21 days rest) gets a **+15 Elo point**
  adjustment. Above 21 days is treated as an offseason gap (e.g. a season opener), not a bye
  week, so no adjustment is applied.

These values are round-number heuristics, documented here so they're auditable, not fit to
outcome data. If a rest computation can't be made (missing prior-game date), no adjustment is
applied and this is logged.

## 3. Injury adjustment (applied only to today's prediction, same as rest)

Using each league's ESPN injury report for the two teams in a game:

- **-6 Elo points** for each player listed **Out**, capped at **-18** per team.
- **-3 Elo points** for each player listed **Doubtful**, capped at **-9** per team.

This is a deliberately coarse, count-based heuristic — it does **not** attempt to weight players
by importance (e.g. a bench player "Out" counts the same as a star), because doing that
correctly requires a paid or proprietary player-value dataset. Treat the injury adjustment as a
rough signal, and read the injury report itself (shown in each card's own "Injury report"
section, separate from "Why") as the actual information — that section lists every player ESPN
has flagged for either team under any status (Out, Doubtful, Questionable, etc.), not just the
two statuses that move the number above. If the injury endpoint is unavailable for a team, that
team's section says "unavailable" (never a guessed or blank list), and the adjustment for that
team is skipped (treated as 0) and logged.

## 4. Final model probability for today's game

```
effective_rating_H = rating_H + rest_adjustment_H + injury_adjustment_H
effective_rating_A = rating_A + rest_adjustment_A + injury_adjustment_A

model_prob_H = 1 / (1 + 10 ^ (-(effective_rating_H + HOME_ADV - effective_rating_A) / 400))
```

`HOME_ADV` is applied once, here, on top of the day's adjusted ratings — it is not baked into the
stored Elo numbers themselves.

## 5. Market "true" probability (de-vigged)

The sportsbook (DraftKings, via the-odds-api.com) moneyline is American odds. Convert to raw
implied probability:

```
implied = 100 / (odds + 100)          if odds > 0
implied = -odds / (-odds + 100)       if odds < 0
```

Raw implied probabilities for the two sides of a moneyline sum to **more** than 100% — the
excess is the sportsbook's built-in margin ("vig"). The de-vigged "true" market probability
normalizes the two sides so they sum to exactly 100%:

```
true_prob_H = implied_H / (implied_H + implied_A)
```

This de-vigged number is what the site calls the "true market probability," and it's what
**edge** is measured against — not the raw DraftKings number, which is shown separately for
reference.

Kalshi and Polymarket prices are already probabilities for a single "Yes" contract (no two-sided
vig to remove the same way), so their implied probability is shown as-is from the market's last
price, when a matching market exists for the game. When no matching market exists, the site shows
"no market" rather than a fabricated number.

## 6. Edge

Edge is framed around whichever team the model actually picked (`pick`), not always the home
team -- that reads much more directly as "does the market agree with the model's own pick":

```
model_pick_prob   = model_prob_H if the model picked the home team, else (1 - model_prob_H)
market_pick_prob  = true_market_prob_H if the model picked the home team, else (1 - true_market_prob_H)
edge = model_pick_prob - market_pick_prob
```

A positive edge means the model is *more* bullish on its own pick than the (de-vigged) market
is; negative means the model is *less* bullish on its own pick than the market is. If the
market's own favorite (whichever side of `true_market_prob_H` is >= 50%) differs from the
model's pick, the site calls that out explicitly as a disagreement, since edge alone doesn't
surface that case. This is an observation about a gap between two estimates, not a betting
signal — see the disclaimer on the site.

## Data sources

- Historical results: [nflverse](https://github.com/nflverse/nfldata) `games.csv` for NFL;
  basketball-reference.com season schedule/results pages for NBA (scraped with delays between
  requests, once, to seed the database).
- Daily schedule/scores/injuries: ESPN's public (undocumented) scoreboard and injury endpoints.
- Odds: the-odds-api.com (DraftKings moneyline), Kalshi public markets, Polymarket Gamma API.

## Known limitations

- Preseason games (ESPN `season.type == 1`) are excluded entirely -- not replayed into Elo,
  not shown on the site -- since rosters and results aren't representative of the real season.
  A day with only preseason games is treated as an off-day for that league.
- No margin-of-victory weighting in Elo.
- Injury adjustment is a blunt per-player-status count, not player-value-weighted.
- Playoff games are replayed into Elo the same as regular-season games (no separate weighting).
- New teams/relocations/expansion teams start at 1500 with no history.
