# Tweet Count Forecaster (Elon Musk, Polymarket XTracker)

This repo holds data and notes to forecast weekly tweet counts for Elon Musk, aligned to Polymarket’s XTracker markets. It includes a cleaned dump of past markets, a simple late‑week forecasting method, and guidance to build a small bot (e.g., Telegram) that polls the XTracker API and emits ranges.

## Layout
- `data/xtracker_dump/` – API snapshots: `trackings_list.json`, `elon_user.json`, `stats_{id}.json`, `posts_{id}.json` (hourly counts and per-tweet records).
- `docs/handbook.md` – strategy, API endpoints, forecasting method, and handoff notes.
- `scripts/` – place pollers/CLI tools here.
- `config/` – sample config (e.g., bot token, polling interval).

## Running (future)
- Add a Python script in `scripts/` to poll the active tracking ID (`/api/users/elonmusk` → `/api/trackings/{id}?includeStats=true`), compute the adjusted forecast (see `docs/handbook.md`), and post to your channel (e.g., Telegram).
- Recompute completion factors weekly as new markets finish; update `docs/handbook.md` with refreshed medians/IQRs and intraday weights.
# Updated Tue Dec 30 15:02:11 JST 2025
