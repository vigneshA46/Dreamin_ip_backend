import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from candle_builder import CandleBuilder
from exit_engine.indicators import create_indicator
from exit_engine.price_rules import PriceRules


IST = ZoneInfo("Asia/Kolkata")


def _tick_ltp(tick):
    try:
        ltp = float(tick["LTP"])
    except (KeyError, TypeError, ValueError):
        return None

    return ltp if ltp > 0 else None


class ExitMonitor:
    """
    One active exit monitor. Up to two exits run side by side and the
    first one to fire wins (once):

    1. Indicator exit, checked on every COMPLETED candle:
           tick -> CandleBuilder(timeframe) -> indicator.update() -> rule

           OPTION tracking      : close < indicator
           INDEX tracking + CE  : close < indicator
           INDEX tracking + PE  : close > indicator

       HalfTrend goes by its trend instead of the line:
           OPTION tracking      : trend is DOWN
           INDEX tracking + CE  : trend is DOWN
           INDEX tracking + PE  : trend is UP

    2. Price exit, checked on every TICK (see price_rules.py):
           fixed target / stop loss, or a trailing stop loss

    The monitor only DETECTS the exit. Placing the order is the
    manager's job (on_exit callback).
    """

    def __init__(
        self,
        config,
        watch_token,
        on_exit,
        on_sl_moved=None,
        on_candle=None,
        price_token=None,
    ):
        """
        config      : StartMonitorRequest
        watch_token : token whose candles feed the indicator (None if the indicator exit is off)
        price_token : token whose ticks feed the price exit (None if there is none)
        on_exit     : fn(monitor, trigger)
                      trigger = {"reason": INDICATOR | TARGET | STOP_LOSS | TRAILING_SL,
                                 "detail": str | None, "price": float | None,
                                 "candle": dict | None, "indicator_value": float | None}
        on_sl_moved : optional fn(monitor, new_sl, ltp) when a trailing SL moves
        on_candle   : optional fn(monitor, candle, indicator_value) for logging
        """

        self.config = config
        self.monitor_id = config.monitor_id

        self.watch_token = str(watch_token) if watch_token is not None else None
        self.price_token = str(price_token) if price_token is not None else None

        # ---- indicator exit
        self.builder = None
        self.indicator = None

        if config.indicator_enabled:
            self.builder = CandleBuilder(config.timeframe)
            self.indicator = create_indicator(config.indicator, config.indicator_period)

        # ---- price exit
        self.price_rules = None

        if config.price_exit_type != "NONE":
            self.price_rules = PriceRules(
                price_exit_type=config.price_exit_type,
                falls=config.price_falls_for_profit(),
                target_price=config.target_price,
                sl_price=config.sl_price,
                trailing_start=config.trailing_start,
                trailing_points=config.trailing_points,
                current_sl=config.current_sl,
                where="NIFTY index" if config.price_source == "INDEX" else "the option",
            )

        self.on_exit = on_exit
        self.on_sl_moved = on_sl_moved
        self.on_candle = on_candle

        self.active = True
        self.exited = False
        self._lock = threading.Lock()

        self.started_at = datetime.now(IST)
        self.last_candle = None
        self.candles_processed = 0
        self.last_price = None

        # Historical OHLC of the candle that was in progress at start,
        # merged into the first live candle (live builder missed its early ticks)
        self._partial_patch = None

    def tokens(self):
        """Every token this monitor needs ticks from."""
        return {t for t in (self.watch_token, self.price_token) if t is not None}

    # ---------------------------------------------------------
    # WARM-UP
    # ---------------------------------------------------------

    def seed(self, history_candles, in_progress_candle=None):
        """
        history_candles    : completed candles (oldest -> newest) at this timeframe
        in_progress_candle : candle of the current, unfinished bucket (optional)
        """

        if self.indicator is None:
            return None

        self.indicator.seed(history_candles)

        if history_candles:
            self.last_candle = history_candles[-1]

        self._partial_patch = in_progress_candle

        return self.indicator.value

    def condition_met_now(self):
        """
        True if the last historical candle already satisfies the indicator rule.
        (Option A: the monitor will exit on the first completed live candle.)
        """

        if self.indicator is None or not self.indicator.ready or not self.last_candle:
            return False

        return self.should_exit(float(self.last_candle["close"]), self.indicator.value)

    # ---------------------------------------------------------
    # INDICATOR RULE
    # ---------------------------------------------------------

    def should_exit(self, close, indicator_value):
        if indicator_value is None:
            return False

        exit_when_up = (
            self.config.tracking_source == "INDEX" and self.config.option_type == "PE"
        )

        trend = getattr(self.indicator, "trend", None)
        if trend is not None:
            return trend == ("UP" if exit_when_up else "DOWN")

        if exit_when_up:
            return close > indicator_value

        return close < indicator_value

    # ---------------------------------------------------------
    # LIVE
    # ---------------------------------------------------------

    def on_tick(self, token, tick):
        """Dispatcher handler, subscribed to every token in tokens()."""

        if not self.active:
            return

        token = str(token)

        if self.price_rules is not None and token == self.price_token:
            ltp = _tick_ltp(tick)

            if ltp is not None:
                self.check_price(ltp)

        if self.builder is not None and token == self.watch_token and self.active:
            candle = self.builder.process_tick(tick)

            if candle:
                self._on_candle(candle)

    # ---------- price exit

    def check_price(self, ltp):
        """Check one price against the target / SL / trailing rules. True if it exited."""

        with self._lock:
            if not self.active or self.exited or self.price_rules is None:
                return False

            self.last_price = ltp
            result = self.price_rules.evaluate(ltp)

            fired = result.reason is not None

            if fired:
                self.exited = True
                self.active = False

        if result.new_sl is not None and self.on_sl_moved:
            try:
                self.on_sl_moved(self, result.new_sl, ltp)
            except Exception as e:
                print(f"[ExitMonitor {self.monitor_id}] on_sl_moved error:", e)

        if not fired:
            return False

        print(f"[ExitMonitor {self.monitor_id}] EXIT SIGNAL {result.reason}: {result.detail}")

        self.on_exit(
            self,
            {
                "reason": result.reason,
                "detail": result.detail,
                "price": ltp,
                "candle": None,
                "indicator_value": None,
            },
        )

        return True

    # ---------- indicator exit

    def _on_candle(self, candle):
        if not self.active:
            return

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

        # Fire exactly once, even if stop() or a price exit races with us
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

        self.on_exit(
            self,
            {
                "reason": "INDICATOR",
                "detail": None,
                "price": None,
                "candle": candle,
                "indicator_value": value,
            },
        )

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
        rules = self.price_rules

        return {
            "monitor_id": self.monitor_id,
            "user_id": self.config.user_id,
            "option_type": self.config.option_type,
            "strike": self.config.strike,
            "watch_token": self.watch_token,
            "price_token": self.price_token,
            # indicator exit
            "indicator_enabled": self.config.indicator_enabled,
            "tracking_source": self.config.tracking_source,
            "timeframe": self.config.timeframe,
            "indicator": self.config.indicator,
            "indicator_value": self.indicator.value if self.indicator else None,
            "indicator_trend": getattr(self.indicator, "trend", None),
            "last_candle": self.last_candle,
            "candles_processed": self.candles_processed,
            # price exit
            "price_exit_type": self.config.price_exit_type,
            "price_source": self.config.price_source,
            "target_price": rules.target if rules else None,
            "current_sl": rules.sl if rules else None,
            "trailing_start": rules.start if rules else None,
            "trailing_points": rules.points if rules else None,
            "last_price": self.last_price,
            # state
            "active": self.active,
            "exited": self.exited,
            "started_at": self.started_at.isoformat(),
        }
