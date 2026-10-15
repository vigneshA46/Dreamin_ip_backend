"""
One shared Dhan WebSocket for the exit engine.

Every trading day:
    wait for 09:15 -> NIFTY spot -> ATM -> nearest expiry
    -> subscribe NIFTY (13) + ATM +-50 strikes (CE + PE)
    -> every tick -> dispatcher.publish(security_id, tick)
    -> disconnect after market close

Dhan can't add instruments to a live connection, so the list is fixed
for the day (entries only happen on these strikes).
"""

import os
import struct
import threading
import time
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
from dhanhq import MarketFeed, DhanContext

from dhan_token import get_access_token, refresh_access_token, dhan_request
from dispatcher import publish


IST = ZoneInfo("Asia/Kolkata")

LTP_URL = "https://api.dhan.co/v2/marketfeed/ltp"

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
FEED_STOP = dtime(15, 31)

# Dhan WebSocket disconnect codes meaning the token is bad
# 807 = access token expired, 809 = authentication failed
AUTH_DISCONNECT_CODES = {807, 809}


class _AuthAwareFeed(MarketFeed):
    """MarketFeed that remembers why Dhan disconnected us."""

    disconnect_code = None

    def server_disconnection(self, data):
        try:
            self.disconnect_code = struct.unpack("<BHBIH", data[0:10])[4]
        except Exception:
            pass
        return super().server_disconnection(data)


def sleep_until(target):
    while True:
        remaining = (target - datetime.now(IST)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


class MarketFeedService:

    def __init__(
        self,
        fno_master,
        index_name="NIFTY",
        index_security_id="13",
        strike_step=50,
        strikes_each_side=50,
    ):
        self.fno_master = fno_master
        self.index_name = index_name
        self.index_security_id = str(index_security_id)
        self.strike_step = strike_step
        self.strikes_each_side = strikes_each_side

        self.client_id = os.getenv("CLIENT_ID")
        self.feed = None

        self.status = {
            "state": "NOT_STARTED",
            "trading_date": None,
            "spot": None,
            "atm": None,
            "expiry": None,
            "token_count": 0,
            "last_tick_at": None,
            "ticks": 0,
            "error": None,
        }

    # ---------------------------------------------------------
    # LIFECYCLE
    # ---------------------------------------------------------

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="exit-market-feed").start()

    def _run(self):
        while True:
            try:
                self._run_day()
            except Exception as e:
                self.status.update(state="ERROR", error=str(e))
                print("[MarketFeed] error:", e)
                time.sleep(10)

    def _run_day(self):
        now = datetime.now(IST)

        # Weekend or after close -> wait for the next day
        if now.weekday() >= 5 or now.time() >= MARKET_CLOSE:
            next_open = (now + timedelta(days=1)).replace(
                hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
            )
            self.status.update(state="WAITING_NEXT_DAY")
            print(f"[MarketFeed] market closed, waiting until {next_open}")
            sleep_until(next_open)
            return

        market_open = now.replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
        )

        if now < market_open:
            self.status.update(state="WAITING_0915")
            print(f"[MarketFeed] waiting for {market_open}")
            sleep_until(market_open)

        spot = self.get_spot()
        atm = round(spot / self.strike_step) * self.strike_step
        option_tokens, expiry = self.build_option_tokens(atm)

        self.status.update(
            trading_date=datetime.now(IST).strftime("%Y-%m-%d"),
            spot=spot,
            atm=atm,
            expiry=expiry,
            token_count=len(option_tokens) + 1,
            error=None,
        )

        print(
            f"[MarketFeed] {self.index_name} spot {spot} ATM {atm} "
            f"expiry {expiry} -> {len(option_tokens)} options + index"
        )

        self._stream_until_close(option_tokens)

    # ---------------------------------------------------------
    # INSTRUMENTS
    # ---------------------------------------------------------

    def get_spot(self):
        for attempt in range(1, 6):
            try:
                res = dhan_request(
                    "POST",
                    LTP_URL,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={"IDX_I": [int(self.index_security_id)]},
                    timeout=10,
                )
                data = res.json()
                return float(data["data"]["IDX_I"][self.index_security_id]["last_price"])

            except Exception as e:
                print(f"[MarketFeed] spot fetch failed (attempt {attempt}):", e)
                time.sleep(2)

        raise Exception(f"Could not fetch {self.index_name} spot price")

    def build_option_tokens(self, atm):
        today = pd.Timestamp(datetime.now(IST).date())
        df = self.fno_master.df

        options = df[
            (df["INSTRUMENT"] == "OPTIDX")
            & (df["UNDERLYING_SYMBOL"] == self.index_name)
            & (df["SM_EXPIRY_DATE"] >= today)
        ]

        expiry = options["SM_EXPIRY_DATE"].min()

        low = atm - self.strikes_each_side * self.strike_step
        high = atm + self.strikes_each_side * self.strike_step

        options = options[
            (options["SM_EXPIRY_DATE"] == expiry)
            & (options["STRIKE_PRICE"] >= low)
            & (options["STRIKE_PRICE"] <= high)
            & (options["STRIKE_PRICE"] % self.strike_step == 0)
            & (options["OPTION_TYPE"].isin(["CE", "PE"]))
        ]

        tokens = sorted({str(int(s)) for s in options["SECURITY_ID"]})

        return tokens, pd.Timestamp(expiry).strftime("%Y-%m-%d")

    # ---------------------------------------------------------
    # STREAM
    # ---------------------------------------------------------

    def _stream_until_close(self, option_tokens):
        instruments = [(MarketFeed.IDX, self.index_security_id, MarketFeed.Quote)]
        instruments += [(MarketFeed.NSE_FNO, t, MarketFeed.Quote) for t in option_tokens]

        stop_at = datetime.now(IST).replace(
            hour=FEED_STOP.hour, minute=FEED_STOP.minute, second=0, microsecond=0
        )

        connect_failures = 0

        while datetime.now(IST) < stop_at:
            # get_data() blocks, so a timer closes the socket at market close
            closer = threading.Timer(
                max((stop_at - datetime.now(IST)).total_seconds(), 0),
                self._close_feed,
            )
            closer.daemon = True
            closer.start()

            token = get_access_token()
            feed = None
            connected = False

            try:
                context = DhanContext(self.client_id, token)
                feed = self.feed = _AuthAwareFeed(context, instruments, "v2")
                feed.run_forever()

                connected = True
                connect_failures = 0
                self.status.update(state="CONNECTED", error=None)
                print(f"[MarketFeed] connected, {len(instruments)} instruments")

                while datetime.now(IST) < stop_at:
                    data = feed.get_data()

                    # Only price packets (skip prev-close / OI / status packets)
                    if not data or "security_id" not in data or "LTP" not in data:
                        continue

                    self.status["ticks"] += 1
                    self.status["last_tick_at"] = data.get("LTT")

                    publish(str(data["security_id"]), data)

            except Exception as e:
                if datetime.now(IST) >= stop_at:
                    break

                self.status.update(state="RECONNECTING", error=str(e))
                print("[MarketFeed] disconnected, reconnecting in 3s:", e)

                if not connected:
                    connect_failures += 1

                # Token rejected (or can't connect at all) -> new token before reconnecting
                code = getattr(feed, "disconnect_code", None)
                if code in AUTH_DISCONNECT_CODES or connect_failures >= 2:
                    try:
                        refresh_access_token(
                            f"websocket disconnect {code or 'connect failed'}",
                            failed_token=token,
                        )
                    except Exception as token_error:
                        print("[MarketFeed] token refresh failed:", token_error)

                time.sleep(3)

            finally:
                closer.cancel()
                self._close_feed()

        self.status.update(state="CLOSED_FOR_DAY")
        print("[MarketFeed] market closed, feed stopped")

    def _close_feed(self):
        feed, self.feed = self.feed, None
        if not feed:
            return
        try:
            feed.close_connection()
        except Exception:
            pass
