"""
Indicator warm-up: past candles from Dhan's intraday chart API.

Dhan only serves 1/5/15/25/60-min candles, so we always fetch 1-min
candles and build any timeframe ourselves (buckets aligned to 09:15,
same as CandleBuilder) -> history and live candles line up exactly.
"""

import math
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from dhan_token import dhan_request


IST = ZoneInfo("Asia/Kolkata")

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
MARKET_MINUTES_PER_DAY = 375

SEGMENTS = {
    "INDEX": ("IDX_I", "INDEX"),
    "OPTION": ("NSE_FNO", "OPTIDX"),
}


def fetch_minute_candles(security_id, tracking_source, from_dt, to_dt):
    exchange_segment, instrument = SEGMENTS[tracking_source]

    res = dhan_request(
        "POST",
        INTRADAY_URL,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": "1",
            "oi": False,
            "fromDate": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": to_dt.strftime("%Y-%m-%d %H:%M:%S"),
        },
        timeout=15,
    )

    if res.status_code != 200:
        raise Exception(f"Dhan intraday API {res.status_code}: {res.text}")

    raw = res.json()
    timestamps = raw.get("timestamp", [])

    candles = []

    for i in range(len(timestamps)):
        ts = datetime.fromtimestamp(timestamps[i], IST)

        if not (MARKET_OPEN <= ts.time() < MARKET_CLOSE):
            continue

        candles.append({
            "time": ts,
            "open": float(raw["open"][i]),
            "high": float(raw["high"][i]),
            "low": float(raw["low"][i]),
            "close": float(raw["close"][i]),
            "volume": float(raw["volume"][i]) if raw.get("volume") else 0,
        })

    candles.sort(key=lambda c: c["time"])
    return candles


def bucket_start(ts, timeframe):
    market_open = ts.replace(hour=9, minute=15, second=0, microsecond=0)
    minutes_since_open = int((ts - market_open).total_seconds() // 60)
    return market_open + timedelta(minutes=(minutes_since_open // timeframe) * timeframe)


def resample(minute_candles, timeframe):
    """1-min candles -> N-min candles, buckets aligned to 09:15 every day."""

    candles = []
    current = None

    for m in minute_candles:
        bucket = bucket_start(m["time"], timeframe)

        if current is None or current["bucket"] != bucket:
            if current:
                candles.append(current)

            current = {
                "bucket": bucket,
                "timestamp": bucket.isoformat(),
                "open": m["open"],
                "high": m["high"],
                "low": m["low"],
                "close": m["close"],
                "volume": m["volume"],
            }
            continue

        current["high"] = max(current["high"], m["high"])
        current["low"] = min(current["low"], m["low"])
        current["close"] = m["close"]
        current["volume"] += m["volume"]

    if current:
        candles.append(current)

    return candles


def load_history(security_id, tracking_source, timeframe, candle_count=200, now=None):
    """
    Returns (completed_candles, in_progress_candle)

    completed_candles  : last `candle_count` finished candles, oldest -> newest
    in_progress_candle : candle of the bucket we're currently inside (or None);
                         ExitMonitor merges it into its first live candle
    """

    now = now or datetime.now(IST)

    # Enough trading days for candle_count candles + weekends/holidays
    trading_days = math.ceil(candle_count * timeframe / MARKET_MINUTES_PER_DAY)
    calendar_days = min(trading_days * 2 + 4, 85)

    from_dt = (now - timedelta(days=calendar_days)).replace(
        hour=9, minute=15, second=0, microsecond=0
    )

    minute_candles = fetch_minute_candles(security_id, tracking_source, from_dt, now)
    candles = resample(minute_candles, timeframe)

    in_progress = None

    if candles:
        last = candles[-1]
        last_end = last["bucket"] + timedelta(minutes=timeframe)

        if last["bucket"] <= now < last_end:
            in_progress = candles.pop()

    for c in candles:
        c.pop("bucket", None)
    if in_progress:
        in_progress.pop("bucket", None)

    completed = candles[-candle_count:]

    print(
        f"[History] {tracking_source} {security_id} {timeframe}m: "
        f"{len(completed)} candles"
        f"{' + in-progress ' + in_progress['timestamp'] if in_progress else ''}"
    )

    return completed, in_progress
