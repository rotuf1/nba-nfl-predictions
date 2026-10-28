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

actual_H   = 1 if H won, 0 if H lost
actual_A   = 1 - actual_H

point_diff       = abs(score_H - score_A)
winner_elo_diff  = pre-game rating gap (incl. HOME_ADV) in the winning team's favor

mov_mult (NFL) = ln(point_diff + 1) * (2.2 / (winner_elo_diff * 0.001 + 2.2))
mov_mult (NBA) = (point_diff + 3) ^ 0.8 / (7.5 + 0.006 * winner_elo_diff)

new_rating_H = rating_H + K * mov_mult * (actual_H - expected_H)
new_rating_A = rating_A + K * mov_mult * (actual_A - expected_A)
```

Margin of victory scales the rating change via `mov_mult`, using FiveThirtyEight's published
NFL/NBA Elo formulas (not derived from a grid search on this data — same "public, documented
formula reused as-is" approach as the Hollinger Game Score / fantasy-scoring injury weights in
section 3). A 1-point win and a 30-point win no longer move ratings the same amount. The
`winner_elo_diff` term in the denominator damps the effect for a big favorite winning big — an
already-expected blowout gets less credit than a similar-sized upset, so a heavy favorite
crushing a bad team doesn't inflate its rating just for doing what was expected.

### Constants (current)

| League | HOME_ADV | K-factor | MOV constants | Season regression |
|---|---|---|---|---|
| NBA | 40 Elo pts | 20 | offset=3.0, b=7.5, c=0.006 | new = 0.75 × old + 0.25 × 1500, applied once at the first game of each new season, then blended with the preseason win-total prior (below) |
| NFL | 24 Elo pts | 20 | a=2.2, b=0.001 | new = 0.75 × old + 0.25 × 1500, applied once at the first game of each new season |

These are standard starting points used by public Elo sports models (not derived from a formal
grid search on this data), chosen so ratings are transparent and stable rather than
over-fit. Season regression exists because rosters change materially between seasons — it pulls
every team 25% of the way back toward the 1500 mean before the new season's games are replayed,
so an early-season game a team wins isn't over-explained by two-year-old form.

**NBA preseason prior** (added 2026-10-23). Regression alone doesn't know about offseason
trades or players returning from injury, so Elo took ~20 games to catch up to a reshaped roster.
At the first game of each NBA season the regressed rating is now blended with a rating implied
by the team's sportsbook preseason win-total line (`data/nba_win_totals.json`):

```
p        = win_total_line / season_games            # 72 for 2020-21, else 82
raw      = 400 * log10(p / (1 - p))                  # Elo gap vs a .500 team
prior    = 1500 + raw - mean(raw over all 30 teams)
start    = 0.25 * regressed_elo + 0.75 * prior       # PRIOR_WEIGHT_ELO
```

Backtest (replay of stored 2019-20 to 2025-26 games, log loss of the pre-game probability):
each team's first 20 games 0.6309 -> 0.6212 (accuracy 63.4% -> 65.0%), full season 0.6257 ->
0.6222. Improved the first-20 window in all 7 seasons; leave-one-season-out (weight chosen on
the other 6) still improved pooled 0.6309 -> 0.6228. Any weight from 0 to 0.5 on Elo scored
about the same, so 0.25 isn't a knife-edge choice. Seasons without a full set of 30 lines fall
back to plain regression (logged as a warning). **Upkeep:** add each new season's lines to
`data/nba_win_totals.json` before opening night; the data is part of `constants_signature()`,
so editing it triggers the automatic rebuild. NFL has no prior yet.

**Home advantage** was retuned 2026-10-15 (from NBA 100 / NFL 48). It's the one constant
that *was* checked against this data: replaying every stored game under a grid of values
and scoring each game's pre-game probability by log loss, the best fit was ~40 for NBA
(2021-2026 seasons, 7,776 games) and ~24 for NFL (2020-2026, 1,766 games). Both match the
raw base rates: NBA home teams have won ~55% of games every season since 2020, NFL ~54%,
well below the 64% / 57% the old values implied for evenly matched teams. Home advantage
league-wide shrank noticeably after 2020, which is why the older published values ran high.

Changing any of these constants means the stored ratings were built under different rules.
`daily_run.py` detects this (`elo.constants_signature()` vs the `elo_constants` meta key) and
replays every stored game from scratch under the new constants (`elo.rebuild_ratings()`)
before predicting -- no refetching needed, since the `games` table holds every final score.

Constants live in `src/elo.py` as `HOME_ADV`, `K_FACTOR`, and `MOV_CONSTANTS` per league — change
them there if you retune, and update this table to match.

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

Each **Out** or **Doubtful** player (from each league's ESPN injury report) is weighted by that
specific player's own recent production, not counted the same as every other injury. This is
computed in `src/player_value.py` / `player_out_penalty()` in `daily_run.py`.

**Injured Reserve** is a special case. A player already on IR for a while has had their absence
baked into the team's recent results already (Elo naturally reflects a team's actual performance
without them), so counting it again here would double-count it — that's why it's excluded by
default. But a *newly*-placed, "designated to return" IR player is a genuinely new absence, not
yet reflected anywhere, so it counts the same as Out (full weight). The distinction is made from
ESPN's per-entry expected-return date: a real near-term date (empirically, always within ~10
weeks as of 2026-09) means short-term/designated-to-return; their generic season-ending
placeholder date (empirically, always ~5+ months out) means long-term — see
`SHORT_TERM_IR_MAX_DAYS` / `_is_short_term_ir()` in `daily_run.py`.

One exception to excluding season-ending IR (added 2026-10-26, after Jalen Hurts went on
season-ending IR having missed only one game and the model ignored him): if the player played a
regular-season game *this season*, his absence is phased in at weight
`1 - (team games since his last game) / N`, with N = **4** (NFL) / **10** (NBA). Once his team has
played N games without him, Elo has absorbed it and he drops out as before. The player's last game
date comes from his ESPN gamelog -- see `LONG_TERM_IR_PHASE_IN_GAMES` / `_long_term_ir_weight()`.

**Manual overrides** (`data/injury_overrides.json`, added 2026-10-26): ESPN's feed can drop a player
outright -- Hurts vanished from it hours before DAL@PHI kicked off, so the last pre-kickoff run
recorded PHI without him. Entries in this file (league -> team abbr -> list of {name, athlete_id,
position, status, return_date, optional until}) are merged into that team's ESPN injuries unless ESPN
already lists the same athlete id, then handled exactly like feed entries. Season-ending IR entries
fade out on their own; use `until` to expire an Out entry.

**NBA** — [Hollinger Game Score](https://en.wikipedia.org/wiki/Game_score), a well-known public
box-score formula (not invented for this project), from the player's per-game averages this
season (or last season, until the new one has games in the books):

```
game_score = PTS + 0.4*FGM - 0.7*FGA - 0.4*(FTA-FTM) + 0.7*ORB + 0.3*DRB
             + STL + 0.7*AST + 0.7*BLK - 0.4*PF - TOV
elo_penalty = -max(game_score - 5.0, 0) * 8.0   # NBA_REPLACEMENT_GAME_SCORE, NBA_VALUE_SCALE
```

The player is measured against a replacement-level backup (Game Score 5), since someone else
plays those minutes. At roughly 28 Elo per point of NBA spread, a star (Game Score ~22-25) is
worth about 5-6 points, a solid starter (~12) about 2, and a deep-bench player ~0. These are
round-number heuristics sized to how much NBA lines typically move on a single absence -- there's
no stored history of past injury reports to fit them against. (Before 2026-10-15 this was
`game_score * 1.5` with a -70 cap, which priced even an MVP-level absence at only ~1 point.)

**NFL** — there's no free, live, per-game, cross-position equivalent to Game Score. Real
Pro-Football-Reference Approximate Value is genuinely complex (separate formulas per position
group, uses team context, computed from full-season data) and can't be faithfully replicated
here. Instead, **skill positions only** (QB, RB, FB, WR, TE) get a production score built from
standard half-PPR fantasy-football scoring conventions — an established, publicly documented
scoring convention, just repurposed here as a value proxy, not invented for this project:

```
score = pass_yds*0.04 + pass_td*4 - pass_int*2
      + rush_yds*0.1  + rush_td*6
      + rec_yds*0.1   + rec_td*6  + rec*0.5      (each line only if that stat category exists)
elo_penalty = -max(score, 0) * 1.5              # NFL_VALUE_SCALE (QBs: 5.0, NFL_QB_VALUE_SCALE)
```

QBs use a separate, larger scale (5.0, since 2026-10-26): a starting-QB swing moves NFL lines
several points, which the 1.5 skill-position scale priced at ~1 point. At ~25 Elo per NFL point,
a mid-season Hurts (score ~16) is ~3 points and an elite QB (~22) ~4.5. Heuristic, not fit.

**Fallback**: if a player's stats can't be fetched (missing athlete id, ESPN request failure),
or — NFL only — the player's position isn't one of the skill positions above (line, defense,
specialists have no comparably clean free per-game production stat), a flat penalty is used
instead: **-15** (NBA) / **-6** (NFL). A **Doubtful** player's penalty (computed the same way) is
multiplied by **0.5** before being added in. The total per team is capped at **-250** (NBA) /
**-150** (NFL; -50 before 2026-10-26) so a cluster of injuries can't blow past a sane bound.

**Questionable** is shown but doesn't move the number at all.

Read the injury report itself (shown in each card's own "Injury report" section, separate from
"Why") as the actual information — that section lists every player ESPN has flagged for either
team under a status relevant to *today's game* (Out, Doubtful, Questionable, short-term Injured
Reserve), not just the statuses that move the number above. Long-term/season-ending Injured
Reserve is left out of that list entirely, not just the numeric adjustment — it isn't news about
today's game, and a full IR list clutters the card. If the injury endpoint is unavailable for a
team, that team's section says "unavailable" (never a guessed or blank list), and the adjustment
for that team is skipped (treated as 0) and logged.

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
model_pick_prob          = model_prob_H if the model picked the home team, else (1 - model_prob_H)
market_prob_for_that_pick = true_market_prob_H if the model picked the home team, else (1 - true_market_prob_H)
edge = model_pick_prob - market_prob_for_that_pick
```

A positive edge means the model is *more* confident in its own pick than the (de-vigged) market
is; negative means the model is *less* confident in its own pick than the market is. The card
shows the model's pick and the market's own favorite (whichever side of `true_market_prob_H` is
>= 50%) as two separate lines, so if they ever name different teams that's visible directly —
edge itself always stays anchored to the model's pick specifically, not the market's. This is an
observation about a gap between two estimates, not a betting signal — see the disclaimer on the
site.

## 7. Prediction tracking (win/loss record)

Every pick made for a not-yet-final game is stored (`predictions` table in `team_ratings.db`,
keyed by the same `game_id` used to replay that game into Elo). Once the game's final score is
replayed in (see section 1), the stored pick is graded: **correct** if the picked team was the
actual winner, **incorrect** otherwise. A tie is graded (so it's never re-graded) but counts
toward neither column. A prediction is only recorded or updated while the game hasn't started yet (ESPN state `pre`),
so the graded pick is always the last one made **before kickoff** -- never one revised mid-game
on a live score or in-game injury, and never one made after the result was known. Once a game
has started, its card also shows that locked pick rather than re-deriving one. (Before
2026-10-15 picks kept updating until the game was final; that was changed after finding a pick
that had flipped mid-game.) Since 2026-10-19 the whole pre-kickoff card (DK/Kalshi/Polymarket
odds, market pick, edge, why, injuries) is stored with the pick (`predictions.card_json`), and
in-progress (LIVE badge) and final cards render that snapshot with only the status and score
updated -- before that, runs during a game showed live in-game odds and injury churn, which
looked like the picks were changing. The site runs at 11:50am, 3:40pm and 6:35pm ET, plus 8:45am ET on Sundays
during the NFL season, so the locked pick is normally from the 6:35pm run for evening games and
from the 8:45am run for 9:30am ET NFL international games. The running
correct/incorrect count (overall and per league) is shown on the site above the game cards. See
`record_prediction()` / `grade_prediction()` / `get_prediction_record()` in `src/elo.py`.

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
- Injury adjustment is a blunt per-player-status count, not player-value-weighted.
- Playoff games are replayed into Elo the same as regular-season games (no separate weighting).
- New teams/relocations/expansion teams start at 1500 with no history.
