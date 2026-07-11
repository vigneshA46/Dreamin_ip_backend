from test_api import router   
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
from fastapi import FastAPI, WebSocket, APIRouter , HTTPException
import asyncio
from dhanhq import MarketFeed, DhanContext
from dispatcher import publish
from dhan_token import get_access_token
import os
from queue import Queue


STRATEGY_OPEN_TRADES_URL = "https://algoapi.dreamintraders.in/api/realtradegroups/strategy-opentrades"
fno_df=load_fno_master()
finder = FindInstrument()
trade_log_queue = Queue()


class ExitRequest(BaseModel):
    user_id: str
    strategy_id: str
    broker_account_id: str
    date: str

class RecoverStrategyRequest(BaseModel):
    strategy_id: str




def log_trade_event(
    event_type,
    leg_name,
    token,
    symbol,
    side,
    lot,
    price,
    reason,
    pnl,
    cum_pnl,
    strategy_id,

        ):
    payload = {
        "run_id": strategy_id,
        "strategy_id": strategy_id,
        "trade_id": strategy_id,

        "event_type": event_type,
        "leg_name": leg_name,
        "token": int(token),
        "symbol": symbol,

        "side": side,
        "lots": lot,
        "quantity": lot * 65,

        "price": float(price),  # 🔥 safety

        "reason": reason,
        "deployed_by": strategy_id,
        "pnl": str(pnl),
        "cum_pnl": str(cum_pnl),
    }

    # 🔥 NON-BLOCKING
    trade_log_queue.put(payload)






def parse_symbol(symbol: str):
    match = re.match(r"([A-Z]+)(\d{2})([A-Z]{3})(\d+)(CE|PE)", symbol)

    if not match:
        raise ValueError(f"Invalid symbol format: {symbol}")

    underlying, day, mon, strike, opt_type = match.groups()

    current_year = datetime.now().year

    expiry = datetime.strptime(
        f"{day}{mon}{current_year}",
        "%d%b%Y"
    ).strftime("%Y-%m-%d")

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
        print("user",user,"signal",signal)
        return await zebu_order(user, signal)

    else:
        raise Exception("Unsupported broker")



@router.post("/recover-strategy")
async def recover_strategy(req: RecoverStrategyRequest):
    try:

        # Fetch today's open trades for the strategy
        open_res = requests.get(
            STRATEGY_OPEN_TRADES_URL,
            params={
                "strategy_id": req.strategy_id
            }
        )

        print("===================================")
        print("fetched open trades for strategy_id:", req.strategy_id)
        print("response ")
        print(open_res.text)

        if open_res.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch open trades: {open_res.text}"
            )

        open_positions = open_res.json()

        if not open_positions:
            return {
                "success": True,
                "message": "No open trades found.",
                "results": []
            }

        results = []

        for trade in open_positions:

            try:

                ########################################################
                # Parse Symbol
                ########################################################

                parsed = parse_symbol(trade["symbol"])

                print("===================================")
                print("Original Symbol :", trade["symbol"])
                print("Parsed :", parsed)
                print("Strike :", parsed["strike"])
                print("Option :", parsed["option_type"])
                print("Expiry :", parsed["expiry"])

                ########################################################
                # Security ID
                ########################################################

                option_row = find_option_security(
                    fno_df,
                    parsed["strike"],
                    parsed["option_type"],
                    parsed["expiry"],
                    parsed["underlying"]
                )

                security_id = option_row["SECURITY_ID"]

                ########################################################
                # Token
                ########################################################

                option = finder.get_option(
                    parsed["underlying"],
                    parsed["strike"],
                    parsed["option_type"]
                )

                token = option["token"]

                if not token:
                    raise Exception("Token not found")

                ########################################################
                # Opposite Side
                ########################################################

                exit_side = "SELL" if trade["side"] == "BUY" else "BUY"

                ########################################################
                # User Object
                ########################################################

                user = {
                    "user_id": trade["user_id"],
                    "broker_name": trade["broker_name"],
                    "broker_account_id": trade["broker_id"],
                    "multiplier": 1,
                    "credentials": trade["credentials"]
                }

                ########################################################
                # Signal
                ########################################################

                signal = {

                    "strategy_id": trade["strategy_id"],

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

                    "reason": "SERVER RECOVERY EXIT",

                    "leg_name": trade["leg_name"],

                    "event_type": "EXIT",

                    "trade_id": trade["trade_id"],

                    "price": float(trade["price"]),

                    "pnl": float(trade.get("pnl", 0)),

                    "cum_pnl": float(trade.get("cum_pnl", 0))

                }

                ########################################################
                # Execute Exit
                ########################################################

                await execute_exit(user, signal)

                results.append({
                    "trade_id": trade["trade_id"],
                    "symbol": trade["symbol"],
                    "status": "EXITED"
                })

            except Exception as e:

                results.append({
                    "trade_id": trade["trade_id"],
                    "symbol": trade["symbol"],
                    "status": "FAILED",
                    "error": str(e)
                })

        return {
            "success": True,
            "message": "Recovery completed.",
            "results": results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




app = FastAPI()
app.include_router(router)