"""
Indicators for the exit engine.

Every indicator has the same interface so ExitMonitor doesn't care which one it uses:

    seed(candles)  -> warm up from historical candles, returns current value
    update(candle) -> feed one COMPLETED candle, returns the new value
    value          -> latest value (None until enough candles)

candle = {"open", "high", "low", "close", ...}
"""


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


EMA_PERIODS = {
    "EMA9": 9,
    "EMA20": 20,
}


def create_indicator(indicator, indicator_period=None):
    """
    Build an indicator from the exit_monitors row values.
    indicator_period overrides the default EMA period if given.
    """

    if indicator in EMA_PERIODS:
        return EMAIndicator(indicator_period or EMA_PERIODS[indicator])

    if indicator == "PARABOLIC_SAR":
        return ParabolicSARIndicator()

    raise ValueError(f"Unsupported indicator: {indicator}")
 