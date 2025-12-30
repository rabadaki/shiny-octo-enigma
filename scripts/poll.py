#!/usr/bin/env python3
"""
Poll XTracker for Elon Musk's active tracking and emit a forecast using completion factors
with an intraday weight adjustment. Outputs to stdout; wire up Telegram by posting
the message (see config).
"""

import os
import sys
import time
import argparse
import json
import ssl
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import certifi


# Completion medians (pct_done by day_idx); refresh as new weeks complete
COMPLETION_MEDIAN = {
    0: 0.130373,
    1: 0.302914,
    2: 0.407371,
    3: 0.605009,
    4: 0.713868,
    5: 0.903805,
    6: 1.0,
}

# Intraday weights (final 24h profile), normalized to sum=1; refresh weekly if you ingest new weeks
INTRADAY_WEIGHTS = {
    0: 0.013,
    1: 0.021,
    2: 0.030,
    3: 0.025,
    4: 0.038,
    5: 0.114,
    6: 0.139,
    7: 0.013,
    8: 0.089,
    9: 0.038,
    10: 0.004,
    11: 0.038,
    12: 0.008,
    13: 0.101,
    14: 0.025,
    15: 0.021,
    16: 0.017,
    17: 0.034,
    18: 0.051,
    19: 0.072,
    20: 0.046,
    21: 0.021,
    22: 0.008,
    23: 0.034,
}


def normalize_weights(weights):
    s = sum(weights.values())
    return {k: v / s for k, v in weights.items()} if s else weights


def _http_json(url, params=None, method="GET", data=None, timeout_s=20):
    """
    Minimal HTTP JSON client using stdlib + certifi.
    - **params**: dict -> querystring
    - **data**: dict/bytes/str -> request body (for POST)
    """
    if params:
        qs = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{qs}"

    body = None
    headers = {"User-Agent": "stalerbot-xtracker/1.0"}
    if data is not None:
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data

    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
        raw = resp.read()
        # some endpoints return application/json; others might be text/plain
        return json.loads(raw.decode("utf-8"))


def fetch_json(url, params=None):
    return _http_json(url, params=params, method="GET")


def _dump_root() -> str:
    # Railway volume should mount at /data; prefer env override
    return os.environ.get("XTRACKER_DUMP_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "xtracker_dump"
    )


def _ensure_dump_bootstrap(min_stats_for_active=True):
    """
    Best-effort bootstrap of dump files into XTRACKER_DUMP_ROOT.
    This makes the bot self-heal on a fresh Railway deploy (empty volume).
    """
    root = _dump_root()
    os.makedirs(root, exist_ok=True)

    trackings_path = os.path.join(root, "trackings_list.json")
    elon_path = os.path.join(root, "elon_user.json")

    try:
        user = fetch_json("https://xtracker.polymarket.com/api/users/elonmusk")
        # keep exact payload for debugging
        with open(elon_path, "w", encoding="utf-8") as f:
            json.dump(user, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[bootstrap] failed to fetch elonmusk user: {e}", file=sys.stderr)
        user = None

    if user is not None:
        try:
            # Normalize to a canonical {data:[...]} format (since APIs vary)
            trackings = user.get("data", {}).get("trackings", [])
            with open(trackings_path, "w", encoding="utf-8") as f:
                json.dump({"data": trackings}, f, indent=2, sort_keys=True)
        except Exception as e:
            print(f"[bootstrap] failed to write trackings_list.json: {e}", file=sys.stderr)

    if not min_stats_for_active:
        return

    # ensure at least stats_{active_id}.json exists (useful for later ML/bands)
    try:
        active = get_active_tracking()
        tid = active["id"]
        stats_path = os.path.join(root, f"stats_{tid}.json")
        if not os.path.exists(stats_path):
            stats_payload = fetch_json(
                f"https://xtracker.polymarket.com/api/trackings/{tid}?includeStats=true"
            )
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump(stats_payload, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[bootstrap] failed to write active stats: {e}", file=sys.stderr)


def get_active_tracking():
    data = fetch_json("https://xtracker.polymarket.com/api/users/elonmusk")
    trackings = data["data"]["trackings"]
    active = [t for t in trackings if t.get("isActive")]
    if not active:
        raise RuntimeError("No active tracking found")
    # Pick the nearest end date
    active.sort(key=lambda t: t["endDate"])
    return active[0]


def forecast(obs, start_iso, end_iso, now=None):
    now = now or datetime.now(timezone.utc)
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))

    # day_idx aligned to market clock
    day_idx = int((now - start).total_seconds() // 86400)
    # Clamp to last full day for forecasting (day6 median is 1.0, which kills remaining calc)
    day_idx = min(day_idx, max(COMPLETION_MEDIAN.keys()) - 1)
    median_pct = COMPLETION_MEDIAN.get(day_idx)
    if not median_pct or median_pct <= 0:
        return None, None, None, day_idx

    finish_base = obs / median_pct
    remain_base = finish_base - obs

    # remaining hours and intraday scaler
    weights = normalize_weights(INTRADAY_WEIGHTS.copy())
    hours_left = max(0, int((end - now).total_seconds() // 3600))
    remaining_hours = []
    t = now
    for _ in range(hours_left):
        remaining_hours.append(t.hour)
        t += timedelta(hours=1)
    W_rem = sum(weights.get(h, 0) for h in remaining_hours)
    uniform = hours_left / 24 if hours_left else 1
    scaler = W_rem / uniform if uniform > 0 else 1

    remain_adj = remain_base * scaler
    finish_adj = obs + remain_adj

    # Bands: use completion IQR only if available; here we just build a simple +/-10% on remaining
    band_low = obs + remain_adj * 0.9
    band_high = obs + remain_adj * 1.1

    return finish_adj, band_low, band_high, day_idx


def format_message(title, obs, finish, band_low, band_high, day_idx, hours_left):
    lines = []
    lines.append(f"{title}")
    lines.append(f"Now: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Tweets so far: {obs}")
    lines.append(f"day_idx: {day_idx}, hours left: {hours_left}")
    if finish is None:
        lines.append("Insufficient data for forecast.")
    else:
        lines.append(f"Adj. finish: {finish:.0f} (band {band_low:.0f}-{band_high:.0f})")
    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    return _http_json(url, method="POST", data=payload, timeout_s=20)


def get_chat_id_from_updates(token):
    """Grab the most recent chat_id from getUpdates (requires a prior message to the bot)."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        data = _http_json(url, method="GET", timeout_s=20)
        for update in reversed(data.get("result", [])):
            msg = update.get("message") or update.get("channel_post")
            if msg and "chat" in msg:
                return msg["chat"]["id"]
    except Exception:
        return None
    return None


def poll_commands(token, handler, offset=None, poll_delay=5):
    """
    Long-poll getUpdates and respond to /fetch or /start commands.
    `handler(chat_id)` should send the forecast.
    """
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    while True:
        params = {"timeout": 10}
        if offset is not None:
            params["offset"] = offset
        try:
            data = _http_json(url, params=params, method="GET", timeout_s=25)
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                text = (msg.get("text") or "").strip()
                if isinstance(text, str) and (
                    text.startswith("/fetch") or text.startswith("/start") or text.startswith("/refresh")
                ):
                    handler(chat_id)
        except Exception as e:
            print(f"Command poll error: {e}", file=sys.stderr)
        time.sleep(poll_delay)


def main():
    parser = argparse.ArgumentParser(description="XTracker tweet forecaster")
    parser.add_argument("--loop", action="store_true", help="run on a fixed interval (uses POLL_INTERVAL_SECONDS)")
    parser.add_argument("--listen", action="store_true", help="listen for /fetch commands via getUpdates")
    args = parser.parse_args()

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_var = [os.environ.get("TELEGRAM_CHAT_ID") or "122628236"]
    poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "0"))  # 0 = single run

    def run_once(chat_override=None):
        # best-effort: ensure dump exists so later upgrades (ML/bands) have data
        _ensure_dump_bootstrap(min_stats_for_active=True)

        tracking = get_active_tracking()
        tid = tracking["id"]
        stats = fetch_json(f"https://xtracker.polymarket.com/api/trackings/{tid}?includeStats=true")["data"]["stats"]
        obs = stats["total"]
        start = tracking["startDate"]
        end = tracking["endDate"]

        finish, band_low, band_high, day_idx = forecast(obs, start, end)
        hours_left = max(0, int((datetime.fromisoformat(end.replace('Z','+00:00')) - datetime.now(timezone.utc)).total_seconds() // 3600))
        msg = format_message(tracking["title"], obs, finish, band_low, band_high, day_idx, hours_left)
        print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")

        if tg_token:
            # If chat_id not set, try to discover from getUpdates (send /start to the bot first)
            tg_chat = tg_chat_var[0]
            if not tg_chat or (isinstance(tg_chat, str) and "<your_chat_id>" in tg_chat):
                discovered = get_chat_id_from_updates(tg_token)
                if discovered:
                    tg_chat_var[0] = discovered
                    tg_chat = discovered
                    print(f"Discovered chat_id from getUpdates: {tg_chat}")
            target_chat = chat_override or tg_chat
            if target_chat:
                try:
                    send_telegram(tg_token, target_chat, msg)
                    print("Sent to Telegram.")
                except Exception as e:
                    print(f"Telegram send failed: {e}", file=sys.stderr)
            else:
                print("Telegram chat_id not set and could not be discovered from getUpdates.")
        else:
            print("Telegram not configured (set TELEGRAM_BOT_TOKEN).")

    # If both enabled, run listener in a background thread and keep the loop in the main thread.
    if args.listen and tg_token:
        print("Listening for /fetch commands...")
        t = threading.Thread(
            target=lambda: poll_commands(tg_token, lambda chat_id: run_once(chat_override=chat_id)),
            daemon=True,
        )
        t.start()

    if args.loop and poll_interval > 0:
        print(f"Looping every {poll_interval} seconds...")
        while True:
            run_once()
            time.sleep(poll_interval)
    else:
        # single-run mode (or listen-only mode, but we still send one at boot)
        run_once()


if __name__ == "__main__":
    main()
