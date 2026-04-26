from brokers.zebuclient import ZebuClient
import requests
from datetime import datetime
import uuid
import asyncio

API_URL = "https://algoapi.dreamintraders.in/api/realtradegroups"

async def zebu_order(user, signal):
    try:
        creds = user["credentials"]

        client = ZebuClient(
            uid=creds["uid"],
            password=creds["password"],
            api_key=creds["apiKey"],
            vendor_code=creds["uid"],
            factor2=creds["factor2"]
        )

        # 🔐 LOGIN (important — ensures fresh jKey)
        client.login()

        qty = signal["quantity"] * user["multiplier"]

        # 🔁 SIDE mapping
        side = "B" if signal["side"] == "BUY" else "S"

        # 🔁 EXCHANGE mapping
        exch = signal["exchange"]  # "NFO", "NSE", "MCX"

        # 🎯 SYMBOL (VERY IMPORTANT for Zebu)
        # You MUST send tsym like: NIFTY13APR26C22900
        if signal.get("is_fno"):
            # build tsym
            expiry = signal["expiry"]  # "2026-04-13"
            strike = str(signal["strike"])
            symbol = signal["zebusymbol"]
            option_type = "C" if signal["is_ce"] else "P"

            # convert expiry → 13APR26
            from datetime import datetime
            dt = datetime.strptime(expiry, "%Y-%m-%d")
            formatted_expiry = dt.strftime("%d%b%y").upper()

            tsym = f"{symbol}{formatted_expiry}{option_type}{strike}"
        else:
            tsym = signal["symbol"]

        # 🛒 PLACE ORDER
        response = client.place_order(
            exch=exch,
            tsym=tsym,
            qty=qty,
            trantype=side
        )

        print(response)
        if response:

            print(f"✅ ZEBU order success {user['user_id']}")

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
        print(f"❌ ZEBU failed {user['user_id']}: {e}")