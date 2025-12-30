# Handoff: Elon Musk Weekly Tweet Forecaster (Polymarket XTracker)

Purpose: Forecast weekly tweet counts late in the market week, aligned to Polymarket’s XTracker markets. Method is empirical and minimal: completion factors (day-in-week) plus an intraday weight adjustment for the remaining hours.

## Data and Source
- Live API (no auth observed):
  - `GET https://xtracker.polymarket.com/api/users/elonmusk` → all markets (trackings) with IDs, start/end, isActive.
  - `GET https://xtracker.polymarket.com/api/trackings/{id}?includeStats=true` → totals, pace, percentComplete, daysRemaining, hourly `daily` array.
  - `GET https://xtracker.polymarket.com/api/users/elonmusk/posts?startDate=...&endDate=...` → per-tweet rows (includes retweets), `createdAt`, `importedAt`, `platformId`.
- Local snapshot (copied here): `data/xtracker_dump/` with `trackings_list.json`, `elon_user.json`, `stats_{id}.json`, `posts_{id}.json`. `stats_total == posts_count` in all cases.

## Definitions
- Counts include retweets (matches XTracker/market definition).
- day_idx = floor((now − market_start)/24h), anchored to the market start time (17:00 UTC for these windows). Do NOT use calendar weekday.

## Completion Factors (from 8 completed weeklies; refresh weekly)
- Median pct_done by day_idx: day0 0.13, day1 0.30, day2 0.41, day3 0.61, day4 0.71 (IQR ~0.67–0.73), day5 0.90 (IQR ~0.85–0.92), day6 1.00.
- Forecast accuracy (leave-one-out, completion median): median APE day2 ~33%, day3 ~22%, day4 ~8%, day5 ~3%.

## Intraday Weights (final 24h profile; normalized)
- Hour UTC → weight (approx.): 00:0.013, 01:0.021, 02:0.030, 03:0.025, 04:0.038, 05:0.114, 06:0.139, 07:0.013, 08:0.089, 09:0.038, 10:0.004, 11:0.038, 12:0.008, 13:0.101, 14:0.025, 15:0.021, 16:0.017, 17:0.034, 18:0.051, 19:0.072, 20:0.046, 21:0.021, 22:0.008, 23:0.034. Sum=1. Peaks: 05,06,08,13,18,19,20 UTC.
- These come from the last 24h of completed weeks; refresh as more weeks complete.

## Forecast Procedure (late-week, with intraday adjustment)
Inputs: `obs` (tweets so far), `start`, `end`, `now`, completion medians/IQRs, intraday weights.

1) Compute day_idx = floor((now − start)/24h).
2) Base finish: `finish_base = obs / median_pct_done[day_idx]`.
3) Base remaining: `remain_base = finish_base − obs`.
4) Compute remaining weight:
   - Let H = set of remaining UTC hours until expiry.
   - W_rem = sum(weights[h] for h in H).
   - Uniform baseline = (hours_left / 24).
   - Scaler = W_rem / (hours_left/24). (If <1, remaining window is light; if >1, it’s heavy.)
5) Adjust remaining: `remain_adj = remain_base * Scaler`.
6) Adjusted finish: `finish_adj = obs + remain_adj`.
7) Bands: split the base band into `obs` + `remain_portion`, scale the remain_portion by Scaler, add back `obs`. Keep a small burst buffer (+5–10) only if a peak hour remains; otherwise trim.

## Current Live Example (Dec 12–19, 2025 snapshot)
- obs=254, percentComplete=88%, ~7 hours left (10–16 UTC). W_rem≈0.214 vs uniform 0.292 → Scaler≈0.734 (light window).
- Base (day_idx≈5, median pct_done≈0.90): finish_base≈282; remain_base≈28.
- Adjusted: remain_adj≈28*0.734≈21; finish_adj≈275.
- Band (after scaling remaining): roughly 270–288; small tail to low 300s only if expecting a burst during the one strong hour (13 UTC).

## Trading Heuristic for Binned Markets
- If a bin lies fully outside the adjusted band → fade.
- If a bin contains the adjusted point and is within the adjusted band → overweight.
- If near a bin edge → size down and keep a small tail in the adjacent bin.
- Reduce/omit burst buffer if remaining hours exclude peak slots; include +10–20 only if high-weight hours remain.

## Maintenance
- Weekly: ingest new completed week, recompute completion medians/IQRs and intraday weights; update this doc.
- Keep the definition consistent (includes RTs). If the market definition changes, re-ingest and recompute factors.
- Monitor APE per week; if drift increases, tighten the window (use last 8–12 weeks only).

## Bot Implementation Notes (suggestion)
- A simple Python poller (cron or long-running) hitting `/api/users/elonmusk` → active ID, then `/api/trackings/{id}?includeStats=true`.
- Compute day_idx, finish_adj, band, and emit to Telegram with a terse message: obs, hours left, point, band, tail note.
- Config: bot token, chat ID, poll interval (e.g., hourly, faster near expiry).
