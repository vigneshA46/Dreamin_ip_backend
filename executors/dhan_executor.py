# executors/dhan_executor.py

from brokers.dhan import DhanAdapter
from dhan_token import get_access_token
import requests
from datetime import datetime
import uuid
import asyncio

API_URL = "https://algoapi.dreamintraders.in/api/realtradegroups"

async def dhan_order(user, signal):
    try:
        creds = user["credentials"]

        #print("CREDS:",creds)
        token= get_access_token()

        adapter = DhanAdapter(
            client_id=creds["clientId"],
            access_token=creds["accessToken"]
        )

        qty = signal["quantity"] * user["multiplier"]

        response = adapter.place_order(
            security_id=signal["security_id"],
            side=signal["side"],
            quantity=qty
        )
        print(response)
        if response:

            print(f"✅ DHAN order success {user['user_id']}")

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
        print(f"❌ DHAN failed {user['user_id']}: {e}")