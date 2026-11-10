"""
Keeps every active ExitMonitor and connects them to:
    dispatcher (live ticks)  ->  monitor  ->  Zebu exit  ->  Express callback

Also:
    recover()        on startup, resume ACTIVE monitors from Express
    run_auto_close() close monitors at their close time (15:14 for now)
"""

import asyncio
import os
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import httpx

from dispatcher import subscribe, unsubscribe, get_last_price
from exit_engine.exit_monitor import ExitMonitor
from exit_engine.broker_exit import resolve_option, validate_quantity, place_exit_order
from exit_engine.exit_models import StartMonitorRequest
from exit_engine.price_rules import fmt


EXPRESS_API_URL = os.getenv("EXPRESS_API_URL", "https://algoapi.dreamintraders.in")
EXIT_ENGINE_SECRET = os.getenv("EXIT_ENGINE_SECRET", "")

IST = ZoneInfo("Asia/Kolkata")

# Every monitor is closed at this time (later: per-user preference)
DEFAULT_AUTO_CLOSE = dtime(15, 14)
AUTO_CLOSE_CHECK_SECONDS = 15


def auto_close_time_today(close_time=DEFAULT_AUTO_CLOSE):
    return datetime.now(IST).replace(
        hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0
    )


class ExitManager:

    def __init__(self, fno_master, history_loader=None):
        """
        fno_master     : FnoMaster (reloaded every morning, read .df at use time)
        history_loader : fn(security_id, tracking_source, timeframe)
                         -> (completed_candles, in_progress_candle)
                         None = no warm-up (indicator warms on live candles)
        """

        self.fno_master = fno_master
        self.history_loader = history_loader

        self.monitors = {}       # monitor_id -> {"monitor": ExitMonitor, "option": dict}
        self.last_ltp = {}       # option security_id -> last traded price
        self.loop = None

    # ---------------------------------------------------------
    # START
    # ---------------------------------------------------------

    async def start(self, config):
        self.loop = asyncio.get_running_loop()

        if config.monitor_id in self.monitors:
            raise ValueError(f"Monitor {config.monitor_id} is already running")

        close_at = auto_close_time_today()

        if datetime.now(IST) >= close_at:
            raise ValueError(
                f"Exit monitors can't be started after {close_at:%H:%M}"
            )

        option = resolve_option(
            self.fno_master.df,
            config.index_name,
            config.strike,
            config.option_type,
        )

        validate_quantity(config.quantity, option["lot_size"])

        def token_for(source):
            return option["security_id"] if source == "OPTION" else config.index_security_id

        # chart the indicator reads candles from / chart the price levels are checked on
        watch_token = token_for(config.tracking_source) if config.indicator_enabled else None
        price_token = token_for(config.price_source) if config.price_exit_type != "NONE" else None

        monitor = ExitMonitor(
            config,
            watch_token,
            on_exit=self._on_exit_signal,
            on_sl_moved=self._on_sl_moved,
            price_token=price_token,
        )

        # Indicator warm-up (network call -> keep it off the event loop)
        if config.indicator_enabled and self.history_loader:
            history, in_progress = await asyncio.to_thread(
                self.history_loader,
                watch_token,
                config.tracking_source,
                config.timeframe,
            )
            monitor.seed(history, in_progress)

        condition_met = monitor.condition_met_now()

        self.monitors[config.monitor_id] = {
            "monitor": monitor,
            "option": option,
            "close_at": close_at,
        }

        # Option LTP is needed for the exit price even when tracking the index
        subscribe(option["security_id"], self._track_ltp)

        for token in monitor.tokens():
            subscribe(token, monitor.on_tick)

        # Target / SL already crossed -> exit right away on the latest price
        crossed_now = False

        if price_token is not None:
            last_price = get_last_price(price_token)

            if last_price is not None:
                crossed_now = monitor.check_price(last_price)

        print(
            f"[ExitManager] STARTED {config.monitor_id} {option['symbol']} "
            f"indicator={config.indicator if config.indicator_enabled else 'off'} "
            f"price_exit={config.price_exit_type}"
        )

        warnings = []

        if condition_met:
            warnings.append("Exit condition is already met - will exit on the next completed candle")

        if crossed_now:
            warnings.append("Target / stop loss level is already crossed - exiting now")

        return {
            "monitor_id": config.monitor_id,
            "symbol": option["symbol"],
            "security_id": option["security_id"],
            "watch_token": watch_token,
            "price_token": price_token,
            "indicator_value": monitor.indicator.value if monitor.indicator else None,
            "indicator_ready": monitor.indicator.ready if monitor.indicator else None,
            "current_sl": monitor.price_rules.sl if monitor.price_rules else None,
            "warning": " | ".join(warnings) if warnings else None,
        }

    # ---------------------------------------------------------
    # STOP
    # ---------------------------------------------------------

    def stop(self, monitor_id):
        entry = self.monitors.get(monitor_id)

        if not entry:
            return False

        monitor = entry["monitor"]

        # Exit signal already fired -> let the exit order finish
        if monitor.exited:
            return False

        self.monitors.pop(monitor_id, None)
        monitor.stop()

        for token in monitor.tokens():
            unsubscribe(token, monitor.on_tick)

        print(f"[ExitManager] STOPPED {monitor_id}")
        return True

    # ---------------------------------------------------------
    # EXIT
    # ---------------------------------------------------------

    def _track_ltp(self, token, tick):
        ltp = tick.get("LTP")
        if ltp:
            self.last_ltp[token] = float(ltp)

    def _on_sl_moved(self, monitor, new_sl, ltp):
        """Trailing SL moved (WebSocket thread) -> save it so a restart resumes from it."""

        print(f"[ExitManager] {monitor.monitor_id} trailing SL -> {fmt(new_sl)} (LTP {ltp:.2f})")

        asyncio.run_coroutine_threadsafe(
            self._save_trailing_sl(monitor.monitor_id, new_sl, ltp),
            self.loop,
        )

    async def _save_trailing_sl(self, monitor_id, new_sl, ltp):
        await self._call_express(
            "POST",
            f"/api/exitmoniter/{monitor_id}/engine-state",
            {
                "current_sl": new_sl,
                "message": f"Trailing SL moved to {fmt(new_sl)} (LTP {ltp:.2f})",
            },
        )

    def _on_exit_signal(self, monitor, trigger):
        """Runs on the WebSocket thread -> hand the order to the event loop."""

        for token in monitor.tokens():
            unsubscribe(token, monitor.on_tick)

        asyncio.run_coroutine_threadsafe(
            self._execute_exit(monitor, trigger),
            self.loop,
        )

    async def _execute_exit(self, monitor, trigger):
        monitor_id = monitor.monitor_id
        entry = self.monitors.pop(monitor_id, None)

        if not entry:
            return

        config = monitor.config
        option = entry["option"]

        reason = trigger["reason"]
        candle = trigger.get("candle")

        # Exit price = option LTP (an index candle / index LTP is not the option's price)
        price = self.last_ltp.get(option["security_id"])

        if price is None and trigger.get("price") is not None and config.price_source == "OPTION":
            price = trigger["price"]

        if price is None and candle is not None and config.tracking_source == "OPTION":
            price = float(candle["close"])

        if reason == "INDICATOR":
            value = trigger["indicator_value"]
            relation = "above" if (config.tracking_source == "INDEX" and config.option_type == "PE") else "below"
            source = "NIFTY" if config.tracking_source == "INDEX" else option["symbol"]

            if config.indicator == "HALFTREND":
                direction = "up" if relation == "above" else "down"
                condition = f"HalfTrend trend is {direction}"
            else:
                condition = f"closed {relation} {config.indicator}"

            what = (
                f"{source} {config.timeframe}m candle {candle['timestamp']} "
                f"{condition} "
                f"(close {float(candle['close']):.2f}, {config.indicator} {value:.2f})"
            )
        else:
            what = trigger["detail"]

        message = (
            f"{what}. "
            f"Exit SELL {config.quantity} {option['symbol']} @ {price if price is not None else 'MKT'}"
        )

        try:
            await place_exit_order(config, option, price or 0, reason="EXIT MONITOR")
        except Exception as e:
            print(f"[ExitManager] EXIT ORDER ERROR {monitor_id}: {e}")
            return

        await self._notify_express(monitor_id, price, message, reason)

    async def _notify_express(self, monitor_id, exit_price, message, reason):
        await self._call_express(
            "POST",
            f"/api/exitmoniter/{monitor_id}/exit",
            {"exit_price": exit_price, "message": message, "exit_reason": reason},
        )

    async def _close_in_express(self, monitor_id, status, message):
        await self._call_express(
            "POST",
            f"/api/exitmoniter/{monitor_id}/engine-close",
            {"status": status, "message": message},
        )

    async def _call_express(self, method, path, payload=None, attempts=3):
        """Express call with the engine secret; returns parsed JSON or None."""

        url = f"{EXPRESS_API_URL}{path}"
        headers = {"x-exit-engine-secret": EXIT_ENGINE_SECRET}

        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    res = await client.request(method, url, json=payload, headers=headers)

                if res.status_code < 300:
                    return res.json()

                print(f"[ExitManager] Express {method} {path} -> {res.status_code}: {res.text}")

                # Auth / not-found won't fix themselves by retrying
                if res.status_code in (401, 403, 404):
                    return None

            except Exception as e:
                print(f"[ExitManager] Express {method} {path} error (attempt {attempt}): {e}")

            await asyncio.sleep(2 * attempt)

        return None

    # ---------------------------------------------------------
    # AUTO CLOSE
    # ---------------------------------------------------------

    async def run_auto_close(self):
        """Background task: close every monitor that reached its close time."""

        print(f"[ExitManager] auto-close at {DEFAULT_AUTO_CLOSE:%H:%M} IST")

        while True:
            await asyncio.sleep(AUTO_CLOSE_CHECK_SECONDS)

            now = datetime.now(IST)

            for monitor_id, entry in list(self.monitors.items()):
                if now < entry["close_at"]:
                    continue

                if self.stop(monitor_id):
                    print(f"[ExitManager] AUTO-CLOSED {monitor_id}")
                    await self._close_in_express(
                        monitor_id,
                        "STOPPED",
                        f"Monitor auto-closed at {entry['close_at']:%H:%M}. "
                        f"Position was not exited by the monitor.",
                    )

    # ---------------------------------------------------------
    # RESTART RECOVERY
    # ---------------------------------------------------------

    async def recover(self):
        """
        On startup: resume every ACTIVE monitor from Express.
        Monitors from an earlier day, or past today's close time, are closed instead.
        """

        self.loop = asyncio.get_running_loop()

        data = None
        for attempt in range(1, 6):
            data = await self._call_express("GET", "/api/exitmoniter/engine/active")
            if data is not None:
                break
            await asyncio.sleep(10 * attempt)

        if data is None:
            print("[ExitManager] RECOVERY FAILED - could not load active monitors from Express")
            return

        rows = data.get("monitors", [])
        print(f"[ExitManager] recovery: {len(rows)} active monitor(s) in DB")

        now = datetime.now(IST)
        close_at = auto_close_time_today()

        for row in rows:
            monitor_id = row["id"]

            if monitor_id in self.monitors:
                continue

            created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).astimezone(IST)

            if created_at.date() != now.date() or now >= close_at:
                await self._close_in_express(
                    monitor_id,
                    "STOPPED",
                    f"Monitor auto-closed: the engine restarted after its "
                    f"{close_at:%H:%M} close time. Position was not exited by the monitor.",
                )
                continue

            try:
                config = StartMonitorRequest(
                    monitor_id=monitor_id,
                    **{k: v for k, v in row.items() if v is not None and k != "id"},
                )
                await self.start(config)
                print(f"[ExitManager] RESUMED {monitor_id}")

            except Exception as e:
                print(f"[ExitManager] could not resume {monitor_id}: {e}")
                await self._close_in_express(
                    monitor_id,
                    "FAILED",
                    f"Could not resume after engine restart: {e}",
                )

    # ---------------------------------------------------------
    # INFO
    # ---------------------------------------------------------

    def active(self):
        return [
            {**entry["monitor"].to_dict(), "close_at": entry["close_at"].isoformat()}
            for entry in self.monitors.values()
        ]
 