from brokers.ant_broker import AntBroker
from TradeMaster.TradeSync import TransactionType, Exchange
import requests
from datetime import datetime
import uuid
import asyncio

API_URL = "https://algoapi.dreamintraders.in/api/realtradegroups"

async def ant_order(user, signal):
    try:
        creds = user["credentials"]

        adapter = AntBroker(
            user_id=creds["userId"],
            auth_code=creds["authCode"],
            secret_key=creds["apiKey"]
        )

        adapter.login()

        qty = signal["quantity"] * user["multiplier"]

        # 🔁 map side
        side = TransactionType.Buy if signal["side"] == "BUY" else TransactionType.Sell

        # 🔁 map exchange (IMPORTANT)
        exchange_map = {
            "NSE": Exchange.NSE,
            "NFO": Exchange.NFO,
            "MCX": Exchange.MCX
        }

        exchange = exchange_map.get(signal["exchange"])

        # 🎯 instrument
        if signal.get("is_fno"):
            instrument = adapter.get_fno_instrument(
                exchange=exchange,
                symbol=signal["antsymbol"],
                expiry=signal["expiry"],
                strike=str(signal["strike"]),
                is_ce=signal["is_ce"]
            )
        else:
            instrument = adapter.get_instrument(
                exchange=exchange,
                symbol=signal["antsymbol"]
            )

        # 🛒 order
        response = adapter.place_market_order(
            instrument=instrument,
            qty=qty,
            side=side,
            tag=f"algo_123"
        )

        print(response)
        if response:

            print(f"✅ ANT order success {user['user_id']}")

            payload = {
                "user_id": user["user_id"],
                "strategy_id": signal["strategy_id"],
                "broker_id": user["broker_account_id"], 
                "trade_id": str(uuid.uuid4()),
                "trade_date": datetime.now().strftime("%Y-%m-%d"),
                "event_type": signal["event_type"],
                "leg_name": signal["leg_name"],
                "symbol": signal["symbol"],
                "side": signal["side"],
                "quantity": qty,
                "price": signal["price"],
                "pnl": signal["pnl"],
                "cum_pnl": signal["cum_pnl"],
                "reason": signal["reason"]
            }

            #print("Payload:",payload)

            response = await asyncio.to_thread(requests.post, API_URL, json=payload)

            #print("📥 STATUS:", response.status_code)
            #print("📥 RESPONSE:", response.text)

            if response.status_code == 201:
                print("Trade logged successfully")
            else:
                print(f"API failed: {response.text}")

    except Exception as e:
        print(f"❌ ANT failed {user['user_id']}: {e}")