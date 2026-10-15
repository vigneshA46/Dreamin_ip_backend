"""
Resolve the option a monitor is protecting and place its exit order
through the existing Zebu executor (NRML, same as the entries).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from find_security import find_option_security
from executors.zebu_executer import zebu_order


IST = ZoneInfo("Asia/Kolkata")

DEFAULT_LOT_SIZE = {
    "NIFTY": 65,
}


# =========================================================
# INSTRUMENT RESOLUTION
# =========================================================

def resolve_option(fno_df, index_name, strike, option_type):
    """
    index + strike + CE/PE -> current (nearest, not expired) expiry contract.

    fno_df : Dhan NSE_FNO master (load_fno_master())
    """

    strike = int(float(strike))
    today = datetime.now(IST).strftime("%Y-%m-%d")

    row = find_option_security(fno_df, strike, option_type, today, index_name)

    expiry_date = pd.to_datetime(row["SM_EXPIRY_DATE"]).date()

    lot_size = row.get("LOT_SIZE")
    lot_size = int(lot_size) if pd.notna(lot_size) else DEFAULT_LOT_SIZE[index_name]

    symbol = (
        f"{index_name}"
        f"{expiry_date.strftime('%d')}"
        f"{expiry_date.strftime('%b').upper()}"
        f"{expiry_date.strftime('%y')}"
        f"{strike}{option_type}"
    )

    return {
        "security_id": str(int(row["SECURITY_ID"])),
        "symbol": symbol,
        "expiry": expiry_date.strftime("%Y-%m-%d"),
        "strike": strike,
        "option_type": option_type,
        "lot_size": lot_size,
    }


def validate_quantity(quantity, lot_size):
    if quantity % lot_size != 0:
        raise ValueError(
            f"Quantity {quantity} is not a multiple of lot size {lot_size}"
        )


# =========================================================
# SIGNAL / USER (same shape as the strategies' build_payload)
# =========================================================

def build_exit_signal(config, option, price, reason):
    pnl = 0
    if config.entry_price is not None:
        pnl = round((float(price) - config.entry_price) * config.quantity, 2)

    return {
        # real_trade_groups.strategy_id is TEXT and some queries cast it to
        # uuid, so use the monitor id (a uuid) to keep those queries safe
        "strategy_id": config.monitor_id,
        "option": option["option_type"],
        "side": "SELL",
        "quantity": config.quantity,
        "security_id": option["security_id"],
        "event_type": "EXIT",
        "leg_name": option["option_type"],
        "symbol": option["symbol"],
        "exchange": "NFO",
        "expiry": option["expiry"],
        "strike": option["strike"],
        "price": float(price),
        "pnl": pnl,
        "cum_pnl": pnl,
        "zebusymbol": config.index_name,
        "is_ce": option["option_type"] == "CE",
        "is_fno": True,
        "reason": reason,
    }


def build_exit_user(config):
    return {
        "user_id": config.user_id,
        "broker_name": config.broker_name,
        "broker_account_id": config.broker_account_id,
        "multiplier": 1,     # quantity is already the exact qty to sell
        "credentials": config.credentials,
    }


# =========================================================
# ORDER
# =========================================================

async def place_exit_order(config, option, price, reason="EXIT MONITOR"):
    user = build_exit_user(config)
    signal = build_exit_signal(config, option, price, reason)

    print(
        f"[ExitMonitor {config.monitor_id}] placing exit "
        f"ZEBU {signal['symbol']} SELL {signal['quantity']}"
    )

    return await zebu_order(user, signal)
