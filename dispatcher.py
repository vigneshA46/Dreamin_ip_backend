""" import time
import threading
from datetime import datetime, time as dtime
import pytz
import requests
from dhan_token import get_access_token


ACCESS_TOKEN = get_access_token()


IST = pytz.timezone("Asia/Kolkata")

subscriptions = {}
active_tokens = set()
token_market = {}
lock = threading.Lock()

NSE_HOLIDAYS = set()
MCX_HOLIDAYS = set()

def load_market_holidays():
    global NSE_HOLIDAYS, MCX_HOLIDAYS

    try:
        ACCESS_TOKEN = get_access_token()

        url = "https://api.dhan.co/v2/market/holidays"
        headers = {"access-token": ACCESS_TOKEN}

        res = requests.get(url, headers=headers, timeout=5)
        data = res.json()

        nse = set()
        mcx = set()

        for item in data.get("data", []):
            date = item.get("date")
            exchange = item.get("exchange", "")

            if "NSE" in exchange:
                nse.add(date)

            if "MCX" in exchange:
                mcx.add(date)

        NSE_HOLIDAYS = nse
        MCX_HOLIDAYS = mcx

        print("Holidays Loaded")

    except Exception as e:
        print("❌ Holiday API failed:", e)

def refresh_holidays():
    while True:
        load_market_holidays()
        time.sleep(86400)

def is_nse_open(now):
    return dtime(9, 5) <= now.time() <= dtime(15, 20)


def is_mcx_open(now):
    return dtime(9, 0) <= now.time() <= dtime(23, 20)

def is_nse_trading_day(now):
    date_str = now.strftime("%Y-%m-%d")
    return now.weekday() < 5 and date_str not in NSE_HOLIDAYS


def is_mcx_trading_day(now):
    date_str = now.strftime("%Y-%m-%d")
    return now.weekday() < 5 and date_str not in MCX_HOLIDAYS

def is_token_active(token):
    now = datetime.now(IST)

    market = token_market.get(str(token))

    if not market:
        return False

    if market == "MCX":
        return is_mcx_trading_day(now) and is_mcx_open(now)

    if market == "NSE":
        return is_nse_trading_day(now) and is_nse_open(now)

    return False

def _update_market_from_msg(msg):
    try:
        token = str(msg["security_id"])
        segment = msg.get("exchange_segment", "")
        if segment.startswith("MCX"):
            token_market[token] = "MCX"
        elif segment.startswith("NSE"):
            token_market[token] = "NSE"

    except:
        pass

def subscribe(token, handler):
    with lock:
        handlers = subscriptions.setdefault(token, [])
        if handler not in handlers:
            handlers.append(handler)


def unsubscribe(token, handler):
    with lock:
        if token in subscriptions and handler in subscriptions[token]:
            subscriptions[token].remove(handler)
            if not subscriptions[token]:
                del subscriptions[token]

def _auto_manager():
    print("Dispatcher Running")

    while True:
        with lock:
            tokens = list(subscriptions.keys())

        for token in tokens:
            active = is_token_active(token)

            if active and token not in active_tokens:
                print(f"ACTIVE [{token_market.get(token,'NSE')}] {token}")
                active_tokens.add(token)

            elif not active and token in active_tokens:
                print(f"INACTIVE [{token_market.get(token,'NSE')}] {token}")
                active_tokens.remove(token)

        time.sleep(2)


def publish(token, data):

    _update_market_from_msg(data)

    if token not in active_tokens:
        return

    with lock:
        handlers = subscriptions.get(token, [])

    for handler in handlers:
        handler(token, data)


load_market_holidays()

threading.Thread(target=refresh_holidays, daemon=True).start()
threading.Thread(target=_auto_manager, daemon=True).start() """


subscriptions = {}

def subscribe(token, handler):
    subscriptions.setdefault(token, []).append(handler)

def publish(token, data):
    for handler in subscriptions.get(token, []):
        handler(token, data)