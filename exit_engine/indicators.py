"""
Indicators for the exit engine.

Every indicator has the same interface so ExitMonitor doesn't care which one it uses:

    seed(candles)  -> warm up from historical candles, returns current value
    update(candle) -> feed one COMPLETED candle, returns the new value
    value          -> latest value (None until enough candles)

candle = {"open", "high", "low", "close", ...}
"""

from collections import deque


class EMAIndicator:
    """
    EMA seeded with the SMA of the first `period` closes,
    then  ema = (close - ema) * k + ema   with k = 2 / (period + 1)
    (same math as calculate_ema / update_ema in the strategies)
    """

    def __init__(self, period=9):
        if period <= 0:
            raise ValueError("EMA period must be > 0")

        self.period = period
        self.multiplier = 2 / (period + 1)
        self.value = None
        self._warmup = []

    @property
    def ready(self):
        return self.value is not None

    def seed(self, candles):
        for candle in candles:
            self.update(candle)
        return self.value

    def update(self, candle):
        close = float(candle["close"])

        # Not enough candles yet -> collect closes for the SMA seed
        if self.value is None:
            self._warmup.append(close)

            if len(self._warmup) == self.period:
                self.value = sum(self._warmup) / self.period
                self._warmup = []

            return self.value

        self.value = ((close - self.value) * self.multiplier) + self.value
        return self.value


class ParabolicSARIndicator:
    """
    Wilder's Parabolic SAR, ported from TradingView's ta.sar()
    so values match the charts users look at.

        SAR = prev_SAR + AF * (EP - prev_SAR)

    AF starts at `start`, grows by `increment` on every new extreme
    point, capped at `maximum`. When price penetrates SAR the trend
    reverses: SAR jumps to the old EP and AF resets.
    SAR is never allowed inside the previous two candles' range.

    The value returned for a candle is the SAR plotted on that candle
    (after any reversal), so:
        uptrend   -> SAR below the candle
        reversal  -> SAR above the candle  (close < SAR)
    """

    def __init__(self, start=0.02, increment=0.02, maximum=0.2):
        self.start = start
        self.increment = increment
        self.maximum = maximum

        self.value = None
        self.is_below = None        # True = uptrend (SAR below price)
        self.extreme = None         # EP
        self.acceleration = None    # AF

        self._bars = 0
        self._prev_close = None
        self._high1 = self._high2 = None
        self._low1 = self._low2 = None

    @property
    def ready(self):
        return self.value is not None

    def seed(self, candles):
        for candle in candles:
            self.update(candle)
        return self.value

    def update(self, candle):
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])

        bar_index = self._bars
        self._bars += 1

        # First candle: nothing to compare with yet
        if bar_index == 0:
            self._shift(high, low, close)
            return None

        first_trend_bar = False

        # Second candle: pick initial trend direction
        if bar_index == 1:
            if close > self._prev_close:
                self.is_below = True
                self.extreme = high
                self.value = self._low1
            else:
                self.is_below = False
                self.extreme = low
                self.value = self._high1

            first_trend_bar = True
            self.acceleration = self.start

        sar = self.value + self.acceleration * (self.extreme - self.value)

        # Reversal check
        if self.is_below:
            if sar > low:
                first_trend_bar = True
                self.is_below = False
                sar = max(high, self.extreme)
                self.extreme = low
                self.acceleration = self.start
        else:
            if sar < high:
                first_trend_bar = True
                self.is_below = True
                sar = min(low, self.extreme)
                self.extreme = high
                self.acceleration = self.start

        # New extreme point -> accelerate
        if not first_trend_bar:
            if self.is_below:
                if high > self.extreme:
                    self.extreme = high
                    self.acceleration = min(self.acceleration + self.increment, self.maximum)
            else:
                if low < self.extreme:
                    self.extreme = low
                    self.acceleration = min(self.acceleration + self.increment, self.maximum)

        # SAR can't sit inside the previous two candles
        if self.is_below:
            sar = min(sar, self._low1)
            if bar_index > 1:
                sar = min(sar, self._low2)
        else:
            sar = max(sar, self._high1)
            if bar_index > 1:
                sar = max(sar, self._high2)

        self.value = sar
        self._shift(high, low, close)
        return self.value

    def _shift(self, high, low, close):
        self._high2, self._high1 = self._high1, high
        self._low2, self._low1 = self._low1, low
        self._prev_close = close


class HalfTrendIndicator:
    """
    HalfTrend [everget], ported from the TradingView script.

    .value = the HalfTrend line, .trend = "UP" / "DOWN" (None until ready)

    highPrice / lowPrice = highest high / lowest low of the last `amplitude` candles
    highma / lowma       = SMA of highs / lows over `amplitude`

    Waiting to flip down : maxLow = max(lowPrice, maxLow)
                           flip when highma < maxLow and close < previous low
    Waiting to flip up   : minHigh = min(highPrice, minHigh)
                           flip when lowma > minHigh and close > previous high

    Line: up only rises, down only falls, and a flip starts from the
    other side's last value. The script's ATR only draws the channels
    and arrows, it doesn't affect the trend or the line, so it's left out.
    """

    def __init__(self, amplitude=2):
        if amplitude <= 0:
            raise ValueError("HalfTrend amplitude must be > 0")

        self.amplitude = amplitude
        self.value = None

        self._is_down = False       # script's trend (False = 0 = up)
        self._next_down = False     # script's nextTrend
        self._max_low = None
        self._min_high = None
        self._up = None
        self._down = None

        self._highs = deque(maxlen=amplitude)
        self._lows = deque(maxlen=amplitude)
        self._prev_high = None
        self._prev_low = None

    @property
    def ready(self):
        return self.value is not None

    @property
    def trend(self):
        if self.value is None:
            return None
        return "DOWN" if self._is_down else "UP"

    def seed(self, candles):
        for candle in candles:
            self.update(candle)
        return self.value

    def update(self, candle):
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])

        prev_high = self._prev_high if self._prev_high is not None else high
        prev_low = self._prev_low if self._prev_low is not None else low

        if self._max_low is None:
            self._max_low = prev_low
            self._min_high = prev_high

        self._highs.append(high)
        self._lows.append(low)
        self._prev_high = high
        self._prev_low = low

        # Not enough candles for the SMAs yet
        if len(self._highs) < self.amplitude:
            return None

        high_price = max(self._highs)
        low_price = min(self._lows)
        high_ma = sum(self._highs) / self.amplitude
        low_ma = sum(self._lows) / self.amplitude

        was_down = self._is_down

        if self._next_down:
            self._max_low = max(low_price, self._max_low)

            if high_ma < self._max_low and close < prev_low:
                self._is_down = True
                self._next_down = False
                self._min_high = high_price
        else:
            self._min_high = min(high_price, self._min_high)

            if low_ma > self._min_high and close > prev_high:
                self._is_down = False
                self._next_down = True
                self._max_low = low_price

        if not self._is_down:
            if was_down:
                self._up = self._down
            else:
                self._up = (
                    self._max_low if self._up is None else max(self._max_low, self._up)
                )
            self.value = self._up
        else:
            if not was_down:
                self._down = self._up
            else:
                self._down = (
                    self._min_high if self._down is None else min(self._min_high, self._down)
                )
            self.value = self._down

        return self.value


EMA_PERIODS = {
    "EMA9": 9,
    "EMA20": 20,
}


def create_indicator(indicator, indicator_period=None):
    """
    Build an indicator from the exit_monitors row values.
    indicator_period overrides the EMA period / HalfTrend amplitude if given.
    """

    if indicator in EMA_PERIODS:
        return EMAIndicator(indicator_period or EMA_PERIODS[indicator])

    if indicator == "PARABOLIC_SAR":
        return ParabolicSARIndicator()

    if indicator == "HALFTREND":
        return HalfTrendIndicator(indicator_period or 2)

    raise ValueError(f"Unsupported indicator: {indicator}")
 