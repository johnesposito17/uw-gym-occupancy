# Phase 2 plan — predicting occupancy 24h ahead

Phase 1 (this repo) just collects. Don't start modeling until there's enough
history — realistically **several weeks**, ideally spanning at least one full
academic rhythm (regular weeks + a football gameday + the run-up to an exam
period) so the model has seen the patterns it's meant to predict.

## Target

For each `zone`, predict occupancy (or `pct_full`) **24 hours ahead**, at the
same 10-minute grid we collect on. Decide up front whether the target is:
- raw `count`, or
- `pct_full` (normalizes across zones of very different capacity — probably
  the better modeling target, but keep raw count for interpretability).

Handle `is_closed` explicitly: a closed zone isn't "0 people," it's "no signal."
Either predict only open hours, or model open/closed as a separate step. Getting
this wrong is the single biggest risk flagged in Phase 1.

## Candidate features

- **hour of day** (cyclical: sin/cos, not a raw 0–23 int)
- **day of week** (cyclical)
- **week of semester** — needs the UW academic calendar (semester start dates,
  add/drop, study days). Occupancy behaves very differently week 1 vs week 10.
- **weather** — [Open-Meteo](https://open-meteo.com/) free API, Madison WI.
  Temperature, precipitation, maybe "feels like." Bad weather → more indoor gym.
  For a *24h-ahead* model, use the forecast available at prediction time, not
  the actual observed weather (avoid leakage).
- **home football gamedays** — UW home game schedule; gameday reshapes the whole
  day. Also basketball/hockey at the Kohl Center for evening effects.
- **exam periods** — finals/midterms; occupancy usually dips then rebounds.
- **breaks** — Thanksgiving, winter, spring break, summer: near-empty buildings.

## Baseline (build this FIRST)

Historical mean by **(zone × day-of-week × hour-of-day)** — i.e. "what's the
average for a Tuesday at 5pm in this zone." Simple, hard to beat, and it's the
honest bar any fancier model has to clear.

## Define "beating the baseline" BEFORE modeling

Pick the metric and the margin now, in writing, so it can't be rationalized later:

- **Metric:** MAE on held-out data, reported in people (and/or percentage
  points of `pct_full`), per zone and pooled.
- **Split:** time-based, not random — train on earlier weeks, test on later
  weeks. Random splits leak because adjacent 10-min rows are near-identical.
- **Bar:** the model must beat the (day-of-week × hour) baseline's MAE by a
  margin that matters in practice — e.g. **≥15% lower MAE**, and it must win on
  the hard cases (gamedays, exam weeks, weather swings), not just quiet Tuesdays
  where everything is easy.
- Also sanity-check against a naive "same time yesterday" and "same time last
  week" predictor — sometimes those are surprisingly strong and worth beating.

## Then the app (Phase 3)

Small web app: current occupancy per zone + a predicted weekly heatmap
(day-of-week × hour). Keep it static/serverless if possible; the CSV in this
repo can be the data source.
