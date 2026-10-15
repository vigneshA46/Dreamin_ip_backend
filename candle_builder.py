from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


class CandleBuilder:
    """
    N-minute candle builder.

    Buckets are aligned to market open (09:15), so a 5-min builder
    produces 09:15, 09:20, 09:25 ... exactly like the broker charts.

    process_tick() returns the FINISHED candle when the first tick of
    the next bucket arrives, otherwise None.
    """

    def __init__(self, timeframe_minutes=1):
        if timeframe_minutes <= 0:
            raise ValueError("timeframe_minutes must be > 0")

        self.timeframe = timeframe_minutes
        self.current_candle = None
        self.current_bucket = None
        self.bucket_start_volume = None
        self.last_cum_volume = None

    def process_tick(self, tick):
        """
        tick (Dhan MarketFeed) must contain:
        type  -> "Quote Data" or "Ticker Data" (index sends ticker)
        LTP
        LTT   -> HH:MM:SS
        volume (optional, cumulative for the day)
        """

        if tick.get("type") not in ("Quote Data", "Ticker Data"):
            return None

        ltp = float(tick["LTP"])
        cum_volume = int(tick.get("volume") or 0)

        today = datetime.now(IST).date()
        tick_time = datetime.strptime(f"{today} {tick['LTT']}", "%Y-%m-%d %H:%M:%S")
        tick_time = tick_time.replace(tzinfo=IST)

        # Ignore pre-open / post-close ticks
        if not (MARKET_OPEN <= tick_time.time() < MARKET_CLOSE):
            return None

        bucket = self._bucket_start(tick_time)

        # First tick
        if self.current_bucket is None:
            self._start_new_candle(bucket, ltp, cum_volume)
            return None

        # Late / out-of-order tick from an older bucket
        if bucket < self.current_bucket:
            return None

        # New bucket started
        if bucket != self.current_bucket:
            finished_candle = self.current_candle
            self._start_new_candle(bucket, ltp, cum_volume)
            return finished_candle

        # Update existing candle
        self.current_candle["high"] = max(self.current_candle["high"], ltp)
        self.current_candle["low"] = min(self.current_candle["low"], ltp)
        self.current_candle["close"] = ltp

        # Dhan gives cumulative volume -> candle volume is the difference
        self.last_cum_volume = cum_volume
        self.current_candle["volume"] = max(cum_volume - self.bucket_start_volume, 0)

        return None

    def _bucket_start(self, tick_time):
        market_open = tick_time.replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
            second=0, microsecond=0
        )
        minutes_since_open = int((tick_time - market_open).total_seconds() // 60)
        bucket_index = minutes_since_open // self.timeframe
        return market_open + timedelta(minutes=bucket_index * self.timeframe)

    def _start_new_candle(self, bucket, ltp, cum_volume):
        # Volume traded before this candle's first tick belongs to the previous candle
        self.bucket_start_volume = (
            self.last_cum_volume if self.last_cum_volume is not None else cum_volume
        )
        self.last_cum_volume = cum_volume

        self.current_bucket = bucket
        self.current_candle = {
            "timestamp": bucket.isoformat(),
            "open": ltp,
            "high": ltp,
            "low": ltp,
            "close": ltp,
            "volume": max(cum_volume - self.bucket_start_volume, 0),
        }


class OneMinuteCandleBuilder:
    def __init__(self):
        self.current_candle = None
        self.current_minute = None

    def process_tick(self, tick):
        """
        tick must contain:
        LTP (price)
        volume
        LTT (HH:MM:SS)
        """

        if tick.get("type") != "Quote Data":
            return None

        ltp = float(tick["LTP"])
        volume = int(tick["volume"])
        ltt = tick["LTT"]

        today = datetime.now(IST).date()
        tick_time = datetime.strptime(f"{today} {ltt}", "%Y-%m-%d %H:%M:%S")
        tick_time = tick_time.replace(tzinfo=IST)

        minute_key = tick_time.replace(second=0, microsecond=0)

        # First tick
        if self.current_minute is None:
            self._start_new_candle(minute_key, ltp, volume)
            return None

        # New minute started
        if minute_key != self.current_minute:
            finished_candle = self.current_candle
            self._start_new_candle(minute_key, ltp, volume)
            return finished_candle

        # Update existing candle
        self.current_candle["high"] = max(self.current_candle["high"], ltp)
        self.current_candle["low"] = min(self.current_candle["low"], ltp)
        self.current_candle["close"] = ltp
        self.current_candle["volume"] = volume  # Dhan gives cumulative volume

        return None

    def _start_new_candle(self, minute_key, ltp, volume):
        self.current_minute = minute_key
        self.current_candle = {
            "timestamp": minute_key.isoformat(),
            "open": ltp,
            "high": ltp,
            "low": ltp,
            "close": ltp,
            "volume": volume
        }
 