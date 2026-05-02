from fastapi import APIRouter, HTTPException , Request
from pydantic import BaseModel
import requests
import re
from datetime import datetime
from find_instrument import FindInstrument
from find_security import load_fno_master, find_option_security
import os
from fastapi.responses import RedirectResponse
import httpx
import hashlib
import asyncpg
import json
from urllib.parse import parse_qs
from fastapi import FastAPI, WebSocket, APIRouter
import asyncio
from dhanhq import MarketFeed, DhanContext
from dispatcher import publish
from dhan_token import get_access_token
import os

app = FastAPI()
router = APIRouter()
finder = FindInstrument()
fno_df=load_fno_master()

DEPLOYMENT_STATUS_URL = "https://algoapi.dreamintraders.in/api/deployments/user/status"
OPEN_TRADES_URL = "https://algoapi.dreamintraders.in/api/realtradegroups/opentrades"

DATABASE_URL = os.getenv("DATABASE_URL")

async def get_db():
    return await asyncpg.connect(DATABASE_URL)




class ExitRequest(BaseModel):
    user_id: str
    strategy_id: str
    broker_account_id: str
    date: str


def parse_symbol(symbol: str):
    match = re.match(r"([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)", symbol)

    if not match:
        raise ValueError(f"Invalid symbol format: {symbol}")

    underlying, day, mon, year, strike, opt_type = match.groups()

    expiry = datetime.strptime(f"{day}{mon}{year}", "%d%b%y").strftime("%Y-%m-%d")

    return {
        "underlying": underlying,
        "strike": int(strike),
        "option_type": opt_type,
        "expiry": expiry
    }

async def execute_exit(user, signal):
    broker = user["broker_name"]

    if broker == "angelone":
        print("user",user,"signal",signal)
        from executors.angel_executor import angel_order
        return await angel_order(user, signal)

    elif broker == "dhan":
        from executors.dhan_executor import dhan_order
        return await dhan_order(user, signal)

    elif broker == "aliceblue":

        from executors.ant_executer import ant_order
        return await ant_order(user, signal)

    elif broker == "upstox":
        from executors.upstox_executor import upstox_order
        return await upstox_order(user, signal)

    elif broker == "zebumynt":

        from executors.zebu_executer import zebu_order
        return await zebu_order(user, signal)

    else:
        raise Exception("Unsupported broker")


@router.post("/exit-strategy")
async def exit_strategy(req: ExitRequest):
    try:

        payload = {
            "user_id": req.user_id,
            "strategy_id": req.strategy_id,
            "broker_account_id": req.broker_account_id,
            "date": req.date,
            "status": "EXITING"
        }

        requests.patch(DEPLOYMENT_STATUS_URL, json=payload)

        print("exiting done")

        open_res = requests.get(OPEN_TRADES_URL, json={
            "user_id": req.user_id,
            "strategy_id": req.strategy_id,
            "broker_id": req.broker_account_id,
            "date": req.date
        })

        if open_res.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Failed to fetch positions: {open_res.text}")

        open_positions = open_res.json()

        print("open_positions")
        print(open_positions)

        if not open_positions:
            payload["status"] = "CLOSED"
            requests.patch(DEPLOYMENT_STATUS_URL, json=payload)
            return {"message": "No open positions"}

        results = []

        for trade in open_positions:
            try:
        
                if trade.get("event_type") != "ENTRY":
                    continue

                parsed = parse_symbol(trade["symbol"])

                ce_row = find_option_security(
                    fno_df,
                    parsed["strike"],
                    parsed["option_type"],
                    parsed["expiry"],
                    parsed["underlying"]
                )

                security_id = ce_row["SECURITY_ID"]

                cerow = finder.get_option(
                    parsed["underlying"],
                    parsed["strike"],
                    parsed["option_type"]
                )

                token = cerow["token"]

                exit_side = "SELL" if trade["side"] == "BUY" else "BUY"



        
                user = {
                    "user_id": req.user_id,
                    "broker_name": trade.get("broker_name"),
                    "broker_account_id": req.broker_account_id,
                    "multiplier": 1,
                    "credentials": trade.get("credentials")   
                }

                signal = {
                    "strategy_id": req.strategy_id,

                    "option": parsed["option_type"],
                    "side": exit_side,
                    "quantity": trade["quantity"],

                    "security_id": security_id,
                    "token": token,

                    "symbol": trade["symbol"],
                    "exchange": "NFO",

                    "expiry": parsed["expiry"],
                    "strike": parsed["strike"],

                    "zebusymbol": parsed["underlying"],
                    "antsymbol": parsed["underlying"],

                    "is_ce": parsed["option_type"] == "CE",
                    "is_fno": True,

                    "pnl": float(trade.get("pnl", 0)),
                    "cum_pnl": float(trade.get("cum_pnl", 0)),

                    "reason": "AUTO EXIT",
                    "leg_name": trade.get("leg_name"),
                    "event_type": "EXIT",

                    "price": float(trade.get("price", 0))
                    }


                if not signal["token"]:
                    print(f"Skipping {signal['symbol']} - token missing")
                    results.append({
                        "trade_id": trade.get("id"),
                        "status": "FAILED",
                        "error": "Token missing"
                    })
                    continue

                await execute_exit(user, signal)

                results.append({
                    "trade_id": trade.get("id"),
                    "status": "EXITED"
                })

            except Exception as e:
                results.append({
                    "trade_id": trade.get("id"),
                    "status": "FAILED",
                    "error": str(e)
                })

        payload["status"] = "CLOSED"
        requests.patch(DEPLOYMENT_STATUS_URL, json=payload)

        return {
            "message": "Exit done",
            "results": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/flattrade/callback")
async def flattrade_callback(request: Request):
    conn = None
    try:
        conn = await get_db()
        request_code = request.query_params.get("request_code")
        broker_id = request.query_params.get("state")
        api_key = request.query_params.get("apiKey")
        api_secret = request.query_params.get("apiSecret")

        print("QUERY PARAMERERS")

        print(dict(request.query_params))

        if not request_code or not broker_id:
            raise HTTPException(status_code=400, detail="Missing request_code or brokerId")


        if not api_key or not api_secret:
            raise HTTPException(status_code=400, detail="Invalid broker credentials")

        # 🔐 SHA256(apiKey + request_code + apiSecret)
        raw = f"{api_key}{request_code}{api_secret}"
        hash_value = hashlib.sha256(raw.encode()).hexdigest()

        print("RAW STRING:", raw)
        print("HASH:", hash_value)

        # 🔁 Exchange token
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://authapi.flattrade.in/trade/apitoken",
                data={
                    "api_key": api_key,
                    "request_code": request_code,
                    "api_secret": hash_value
                    }
                )

            print(response)

        raw_text = response.text.strip()

        print(raw_text)
        if not raw_text:
            raise HTTPException(status_code=400, detail="Empty response from FlatTrade")

        parsed = parse_qs(raw_text)
        data = {k: v[0] for k, v in parsed.items()}

        print("PARSED:", data)

        print("STATUS:", response.status)
        print("RAW RESPONSE:", repr(response.text))


        if data.get("status") != "Ok":
            raise HTTPException(
                status_code=400,
                detail=data.get("emsg", "FlatTrade auth failed")
            )

        # ✅ FIX: Convert dict → JSON string
        update_data = json.dumps({
            "accessToken": data.get("token"),
            "clientId": data.get("client")
        })

        await conn.execute(
            """
            UPDATE broker_accounts
            SET credentials = credentials || $1::jsonb,
                status = 'connected'
            WHERE id = $2
            """,
            update_data,
            broker_id
        )

        # 🔁 Redirect
        return RedirectResponse(url="http://localhost:5173/brokers")

    except Exception as err:
        print("FlatTrade Callback Error:", str(err))
        raise HTTPException(status_code=500, detail="FlatTrade connection failed")

    finally:
        if conn:
            await conn.close()

tokens = set()
clients = set()
lock = asyncio.Lock()

feed = None
loop = None
restart_lock = asyncio.Lock()

async def connect(ws: WebSocket):
    await ws.accept()
    async with lock:
        clients.add(ws)

async def disconnect(ws: WebSocket):
    async with lock:
        clients.discard(ws)

async def broadcast(data):
    dead = []
    async with lock:
        for c in clients:
            try:
                await c.send_json(data)
            except:
                dead.append(c)

        for d in dead:
            clients.discard(d)

def is_valid_token(token: str):
    return token.isdigit() and len(token) > 3


def on_message(msg):
    token = str(msg.get("security_id"))

    if msg.get("ltp") is None:
        return

    publish(token, msg)

    try:
        asyncio.run_coroutine_threadsafe(broadcast(msg), loop)
    except Exception as e:
        print("Broadcast error:", e)

def on_connect(msg=None):
    print("WS CONNECTED")

def on_close(msg=None):
    print("WS CLOSED")

def start_dhan_ws():
    global feed

    try:
        access_token = get_access_token()
        client_id = os.getenv("CLIENT_ID")

        dhan_context = DhanContext(client_id, access_token)

        instruments = []
        instruments.append((MarketFeed.IDX, "13", MarketFeed.Quote))

        valid_tokens = []
        for t in list(tokens):
            if is_valid_token(t):
                valid_tokens.append(t)
            else:
                print("Skipping invalid token:", t)

        instruments.extend([
            (MarketFeed.NSE_FNO, t, MarketFeed.Quote)
            for t in valid_tokens
        ])

        print("Starting WS with:", instruments)

        feed = MarketFeed(dhan_context, instruments, "v2")

        feed.on_message = on_message
        feed.on_connect = on_connect
        feed.on_close = on_close

        feed.run_forever()

    except Exception as e:
        print("WS ERROR:", e)

async def restart_ws():
    global feed

    async with restart_lock:

        if feed:
            try:
                print("Closing WS...")
                await feed.disconnect()
            except:
                pass

            # wait for proper cleanup
            await asyncio.sleep(3)

        print("Starting new WS...")
        loop.run_in_executor(None, start_dhan_ws)

@router.post("/add-token")
async def add_token(exchange: str, token: str):

    if token in tokens:
        return {"status": "already exists"}

    if not is_valid_token(token):
        return {"error": "Invalid token"}

    async with lock:
        tokens.add(token)

    await restart_ws()

    return {"status": "added", "tokens": list(tokens)}


@router.post("/clear-tokens")
async def clear_tokens():
    async with lock:
        tokens.clear()

    await restart_ws()

    return {"status": "cleared"}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await connect(ws)

    try:
        while True:
            await ws.receive_text()
    except:
        await disconnect(ws)

@app.on_event("startup")
async def startup():
    global loop
    loop = asyncio.get_running_loop()

    loop.run_in_executor(None, start_dhan_ws)


app.include_router(router)
