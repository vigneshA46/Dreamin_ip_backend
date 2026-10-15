import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from candle_builder import CandleBuilder
from exit_engine.indicators import create_indicator


IST = ZoneInfo("Asia/Kolkata")


class ExitMonitor:
    """
    One active exit monitor.

        tick -> CandleBuilder(timeframe) -> completed candle
             -> indicator.update() -> exit rule -> on_exit()  (once)

    Exit rule (checked on every COMPLETED candle):
        OPTION tracking      : close < indicator
        INDEX tracking + CE  : close < indicator
        INDEX tracking + PE  : close > indicator

    The monitor only DETECTS the exit. Placing the order is the
    manager's job (on_exit callback).
    """

    def __init__(self, config, watch_token, on_exit, on_candle=None):
        """
        config      : StartMonitorRequest
        watch_token : security id whose candles are tracked
                      (option security id, or "13" for NIFTY index)
        on_exit     : fn(monitor, candle, indicator_value)
        on_candle   : optional fn(monitor, candle, indicator_value) for logging
        """

        self.config = config
        self.monitor_id = config.monitor_id
        self.watch_token = str(watch_token)

        self.builder = CandleBuilder(config.timeframe)
        self.indicator = create_indicator(config.indicator, config.indicator_period)

        self.on_exit = on_exit
        self.on_candle = on_candle

        self.active = True
        self.exited = False
        self._lock = threading.Lock()

        self.started_at = datetime.now(IST)
        self.last_candle = None
        self.candles_processed = 0

        # Historical OHLC of the candle that was in progress at start,
        # merged into the first live candle (live builder missed its early ticks)
        self._partial_patch = None

    # ---------------------------------------------------------
    # WARM-UP
    # ---------------------------------------------------------

    def seed(self, history_candles, in_progress_candle=None):
        """
        history_candles    : completed candles (oldest -> newest) at this timeframe
        in_progress_candle : candle of the current, unfinished bucket (optional)
        """

        self.indicator.seed(history_candles)

        if history_candles:
            self.last_candle = history_candles[-1]

        self._partial_patch = in_progress_candle

        return self.indicator.value

    def condition_met_now(self):
        """
        True if the last historical candle already satisfies the exit rule.
        (Option A: the monitor will exit on the first completed live candle.)
        """

        if not self.indicator.ready or not self.last_candle:
            return False

        return self.should_exit(float(self.last_candle["close"]), self.indicator.value)

    # ---------------------------------------------------------
    # RULE
    # ---------------------------------------------------------

    def should_exit(self, close, indicator_value):
        if indicator_value is None:
            return False

        if self.config.tracking_source == "INDEX" and self.config.option_type == "PE":
            return close > indicator_value

        return close < indicator_value

    # ---------------------------------------------------------
    # LIVE
    # ---------------------------------------------------------

    def on_tick(self, token, tick):
        """Dispatcher handler: subscribe(watch_token, monitor.on_tick)"""

        if not self.active:
            return

        candle = self.builder.process_tick(tick)

        if candle:
            self._on_candle(candle)

    def _on_candle(self, candle):
        candle = self._apply_partial_patch(candle)

        value = self.indicator.update(candle)

        self.last_candle = candle
        self.candles_processed += 1

        if self.on_candle:
            try:
                self.on_candle(self, candle, value)
            except Exception as e:
                print(f"[ExitMonitor {self.monitor_id}] on_candle error:", e)

        if not self.indicator.ready:
            return

        if not self.should_exit(float(candle["close"]), value):
            return

        # Fire exactly once, even if stop() races with us
        with self._lock:
            if not self.active or self.exited:
                return
            self.exited = True
            self.active = False

        print(
            f"[ExitMonitor {self.monitor_id}] EXIT SIGNAL "
            f"{candle['timestamp']} close={candle['close']} "
            f"{self.config.indicator}={value:.2f}"
        )

        self.on_exit(self, candle, value)

    def _apply_partial_patch(self, candle):
        patch = self._partial_patch

        if not patch:
            return candle

        self._partial_patch = None

        if patch.get("timestamp") != candle["timestamp"]:
            return candle

        return {
            **candle,
            "open": float(patch["open"]),
            "high": max(candle["high"], float(patch["high"])),
            "low": min(candle["low"], float(patch["low"])),
        }

    # ---------------------------------------------------------
    # CONTROL
    # ---------------------------------------------------------

    def stop(self):
        with self._lock:
            self.active = False

    def to_dict(self):
        return {
            "monitor_id": self.monitor_id,
            "user_id": self.config.user_id,
            "watch_token": self.watch_token,
            "tracking_source": self.config.tracking_source,
            "option_type": self.config.option_type,
            "strike": self.config.strike,
            "timeframe": self.config.timeframe,
            "indicator": self.config.indicator,
            "indicator_value": self.indicator.value,
            "last_candle": self.last_candle,
            "candles_processed": self.candles_processed,
            "active": self.active,
            "exited": self.exited,
            "started_at": self.started_at.isoformat(),
        }
