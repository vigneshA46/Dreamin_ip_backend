from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

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
 