"""
Daily reset at 08:30 IST - replaces the daily redeploy.

    08:30  new Dhan token (TOTP) + fresh FNO master (new weekly contracts)
    09:15  MarketFeedService picks the strikes and connects (its own loop)
"""

import threading
import time
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from dhan_token import refresh_access_token


IST = ZoneInfo("Asia/Kolkata")

RESET_TIME = dtime(8, 30)
RETRY_SECONDS = 120
MAX_ATTEMPTS = 5


class DailyReset:

    def __init__(self, fno_master, on_reset=None):
        """
        fno_master : FnoMaster to reload
        on_reset   : optional fn() for extra cleanup (e.g. clear caches)
        """

        self.fno_master = fno_master
        self.on_reset = on_reset

        self.status = {
            "last_reset_at": None,
            "next_reset_at": None,
            "error": None,
        }

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="exit-daily-reset").start()

    def _next_reset_time(self):
        now = datetime.now(IST)
        target = now.replace(
            hour=RESET_TIME.hour, minute=RESET_TIME.minute, second=0, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)
        return target

    def _run(self):
        while True:
            target = self._next_reset_time()
            self.status["next_reset_at"] = target.isoformat()
            print(f"[DailyReset] next reset at {target:%Y-%m-%d %H:%M} IST")

            while datetime.now(IST) < target:
                time.sleep(min((target - datetime.now(IST)).total_seconds(), 60))

            self.run_once()

    def run_once(self):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                refresh_access_token("daily reset")
                self.fno_master.reload()

                if self.on_reset:
                    self.on_reset()

                self.status.update(last_reset_at=datetime.now(IST).isoformat(), error=None)
                print("[DailyReset] done")
                return

            except Exception as e:
                self.status["error"] = str(e)
                print(f"[DailyReset] failed (attempt {attempt}):", e)
                time.sleep(RETRY_SECONDS)

        print("[DailyReset] giving up for today - still using the previous token / FNO master")
