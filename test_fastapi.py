from fastapi import FastAPI, APIRouter, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re
from datetime import datetime
from find_instrument import FindInstrument
from find_security import FnoMaster, find_option_security
import os
import asyncio
from typing import Optional
from exit_engine.exit_models import StartMonitorRequest
from exit_engine.exit_manager import ExitManager
from exit_engine.history import load_history
from exit_engine.market_feed import MarketFeedService
from exit_engine.daily_reset import DailyReset
from dhan_token import token_status
from dispatcher import clear_last_prices


router = APIRouter()

STRTEGY_OPEN_TRADE_USER_URL = "https://dreaminalgo-backend-production.up.railway.app/api/realtradegroups/opentrades"
STRATEGY_OPEN_TRADES_URL = "https://algoapi.dreamintraders.in/api/realtradegroups/strategy-opentrades"

# Generates the first Dhan token (in memory) while downloading the master
fno_master = FnoMaster()
finder = FindInstrument()
exit_manager = ExitManager(fno_master, history_loader=load_history)
market_feed = MarketFeedService(fno_master)

# 08:30 every day: new Dhan token + fresh FNO master (no daily redeploy)
def clear_price_caches():
    exit_manager.last_ltp.clear()
    clear_last_prices()


daily_reset = DailyReset(fno_master, on_reset=clear_price_caches)

EXIT_ENGINE_SECRET = os.getenv("EXIT_ENGINE_SECRET", "")



class ExitUserOpenTradesRequest(BaseModel):
    user_id: str
    strategy_id: str
    broker_id: str
    date: str


@router.post("/exit-user-open-trades")
async def exit_user_open_trades(req: ExitUserOpenTradesRequest):

    try:

        ########################################################
        # Fetch user's open trades
        ########################################################


        open_res = requests.get(
            STRTEGY_OPEN_TRADE_USER_URL,
            json={
                "user_id": req.user_id,
                "strategy_id": req.strategy_id,
                "broker_id": req.broker_id,
                "date": req.date
            }
        )

        print("===================================")
        print("Fetching open trades for user")
        print("User ID      :", req.user_id)
        print("Strategy ID  :", req.strategy_id)
        print("Broker ID    :", req.broker_id)
        print("Date         :", req.date)
        print("Response     :", open_res.text)

        if open_res.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch open trades: {open_res.text}"
            )

        open_positions = open_res.json()

        ########################################################
        # No open trades
        ########################################################

        if not open_positions:

            return {
                "success": True,
                "message": "No open trades found.",
                "results": []
            }

        results = []

        ########################################################
        # Exit every open trade
        ########################################################

        for trade in open_positions:

            try:

                ################################################
                # Parse Symbol
                ################################################

                parsed = parse_symbol(trade["symbol"])

                print("===================================")
                print("Original Symbol :", trade["symbol"])
                print("Parsed          :", parsed)
                print("Strike          :", parsed["strike"])
                print("Option          :", parsed["option_type"])
                print("Expiry          :", parsed["expiry"])

                ################################################
                # Find Security ID
                ################################################

                option_row = find_option_security(
                    fno_master.df,
                    parsed["strike"],
                    parsed["option_type"],
                    parsed["expiry"],
                    parsed["underlying"]
                )

                security_id = option_row["SECURITY_ID"]

                ################################################
                # Find Token
                ################################################

                option = finder.get_option(
                    parsed["underlying"],
                    parsed["strike"],
                    parsed["option_type"]
                )

                token = option["token"]

                if not token:
                    raise Exception("Token not found")

                ################################################
                # Opposite Side
                ################################################

                exit_side = (
                    "SELL"
                    if trade["side"] == "BUY"
                    else "BUY"
                )

                ################################################
                # User Object
                ################################################

                user = {
                    "user_id": trade["user_id"],

                    "broker_name": trade["broker_name"],

                    "broker_account_id": trade["broker_id"],

                    "multiplier": (
                        trade["multiplier"]
                        if trade["multiplier"] is not None
                        else 1
                    ),

                    "credentials": trade["credentials"]
                }

                ################################################
                # Exit Signal
                ################################################

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

                    "reason": "USER OPEN TRADES EXIT",

                    "leg_name": trade["leg_name"],

                    "event_type": "EXIT",

                    "trade_id": trade["trade_id"],

                    "price": float(trade["price"]),

                    "pnl": float(trade.get("pnl", 0)),

                    "cum_pnl": float(trade.get("cum_pnl", 0))
                }

                ################################################
                # Execute Exit In Broker
                ################################################

                print("Executing exit")
                print("User      :", trade["user_id"])
                print("Broker    :", trade["broker_name"])
                print("Symbol    :", trade["symbol"])
                print("Side      :", exit_side)
                print("Quantity  :", trade["quantity"])

                await execute_exit(user, signal)

                ################################################
                # Success
                ################################################

                results.append({
                    "trade_id": trade["trade_id"],
                    "symbol": trade["symbol"],
                    "status": "EXITED"
                })

            except Exception as e:

                ################################################
                # Individual trade failure
                ################################################

                print(
                    f"Failed to exit trade "
                    f"{trade.get('trade_id')}: {e}"
                )

                results.append({
                    "trade_id": trade.get("trade_id"),
                    "symbol": trade.get("symbol"),
                    "status": "FAILED",
                    "error": str(e)
                })

        ########################################################
        # Final Response
        ########################################################

        return {
            "success": True,
            "message": "User open trades exit completed.",
            "results": results
        }

    except HTTPException:
        raise

    except Exception as e:

        print("User open trades exit error:", e)

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )




class RecoverStrategyRequest(BaseModel):
    strategy_id: str

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
                    fno_master.df,
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
                    "multiplier": trade["multiplier"] if trade["multiplier"] is not None else 1,
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


########################################################
# EXIT MONITOR (called by Express only)
########################################################

def verify_exit_engine_secret(secret):
    if EXIT_ENGINE_SECRET and secret != EXIT_ENGINE_SECRET:
        raise HTTPException(status_code=401, detail="Invalid exit engine secret")


@router.post("/exit-monitor/start")
async def start_exit_monitor(
    req: StartMonitorRequest,
    x_exit_engine_secret: Optional[str] = Header(default=None)
):
    verify_exit_engine_secret(x_exit_engine_secret)

    try:
        result = await exit_manager.start(req)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        print("Exit monitor start error:", e)
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, **result}


@router.post("/exit-monitor/{monitor_id}/stop")
async def stop_exit_monitor(
    monitor_id: str,
    x_exit_engine_secret: Optional[str] = Header(default=None)
):
    verify_exit_engine_secret(x_exit_engine_secret)

    stopped = exit_manager.stop(monitor_id)

    return {
        "success": True,
        "stopped": stopped,
        "message": "Monitor stopped" if stopped else "Monitor was not running"
    }


@router.get("/exit-monitor/active")
async def active_exit_monitors(
    x_exit_engine_secret: Optional[str] = Header(default=None)
):
    verify_exit_engine_secret(x_exit_engine_secret)

    return {"success": True, "monitors": exit_manager.active()}


@router.get("/exit-monitor/feed")
async def exit_monitor_feed_status(
    x_exit_engine_secret: Optional[str] = Header(default=None)
):
    verify_exit_engine_secret(x_exit_engine_secret)

    return {
        "success": True,
        "feed": market_feed.status,
        "daily_reset": daily_reset.status,
        "dhan_token": token_status(),
    }



app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://hasan3324algoadmin.dreamintraders.in"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
 


@app.on_event("startup")
async def start_market_feed():
    market_feed.start()
    daily_reset.start()

    # keep references so the tasks aren't garbage collected
    app.state.background_tasks = [
        asyncio.create_task(exit_manager.recover()),
        asyncio.create_task(exit_manager.run_auto_close()),
    ]
