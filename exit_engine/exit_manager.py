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

from dispatcher import subscribe, unsubscribe
from exit_engine.exit_monitor import ExitMonitor
from exit_engine.broker_exit import resolve_option, validate_quantity, place_exit_order
from exit_engine.exit_models import StartMonitorRequest


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

        if config.tracking_source == "OPTION":
            watch_token = option["security_id"]
        else:
            watch_token = config.index_security_id

        monitor = ExitMonitor(config, watch_token, on_exit=self._on_exit_signal)

        # Warm-up (network call -> keep it off the event loop)
        if self.history_loader:
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
        subscribe(watch_token, monitor.on_tick)

        print(
            f"[ExitManager] STARTED {config.monitor_id} "
            f"{option['symbol']} watch={watch_token} "
            f"{config.indicator}={monitor.indicator.value}"
        )

        return {
            "monitor_id": config.monitor_id,
            "symbol": option["symbol"],
            "security_id": option["security_id"],
            "watch_token": watch_token,
            "indicator_value": monitor.indicator.value,
            "indicator_ready": monitor.indicator.ready,
            "warning": (
                "Exit condition is already met - will exit on the next completed candle"
                if condition_met else None
            ),
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
        unsubscribe(monitor.watch_token, monitor.on_tick)

        print(f"[ExitManager] STOPPED {monitor_id}")
        return True

    # ---------------------------------------------------------
    # EXIT
    # ---------------------------------------------------------

    def _track_ltp(self, token, tick):
        ltp = tick.get("LTP")
        if ltp:
            self.last_ltp[token] = float(ltp)

    def _on_exit_signal(self, monitor, candle, indicator_value):
        """Runs on the WebSocket thread -> hand the order to the event loop."""

        unsubscribe(monitor.watch_token, monitor.on_tick)

        asyncio.run_coroutine_threadsafe(
            self._execute_exit(monitor, candle, indicator_value),
            self.loop,
        )

    async def _execute_exit(self, monitor, candle, indicator_value):
        monitor_id = monitor.monitor_id
        entry = self.monitors.pop(monitor_id, None)

        if not entry:
            return

        config = monitor.config
        option = entry["option"]

        # Exit price = option LTP (candle close is the index value when tracking INDEX)
        price = self.last_ltp.get(option["security_id"])
        if price is None and config.tracking_source == "OPTION":
            price = float(candle["close"])

        relation = "above" if (config.tracking_source == "INDEX" and config.option_type == "PE") else "below"
        source = "NIFTY" if config.tracking_source == "INDEX" else option["symbol"]

        if config.indicator == "HALFTREND":
            direction = "up" if relation == "above" else "down"
            condition = f"HalfTrend trend is {direction}"
        else:
            condition = f"closed {relation} {config.indicator}"

        message = (
            f"{source} {config.timeframe}m candle {candle['timestamp']} "
            f"{condition} "
            f"(close {float(candle['close']):.2f}, {config.indicator} {indicator_value:.2f}). "
            f"Exit SELL {config.quantity} {option['symbol']} @ {price if price is not None else 'MKT'}"
        )

        try:
            await place_exit_order(config, option, price or 0, reason="EXIT MONITOR")
        except Exception as e:
            print(f"[ExitManager] EXIT ORDER ERROR {monitor_id}: {e}")
            return

        await self._notify_express(monitor_id, price, message)

    async def _notify_express(self, monitor_id, exit_price, message):
        await self._call_express(
            "POST",
            f"/api/exitmoniter/{monitor_id}/exit",
            {"exit_price": exit_price, "message": message},
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
 