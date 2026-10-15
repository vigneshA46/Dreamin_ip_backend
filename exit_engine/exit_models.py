from typing import Literal, Optional

from pydantic import BaseModel, Field


NIFTY_INDEX_SECURITY_ID = "13"


class StartMonitorRequest(BaseModel):
    """
    Payload Express sends to POST /exit-monitor/start
    (one row of exit_monitors + broker credentials)
    """

    monitor_id: str
    user_id: str

    broker_account_id: str
    broker_name: str
    credentials: dict

    symbol: str
    security_id: Optional[str] = None      # resolved by Python if not sent

    index_name: str = "NIFTY"
    index_security_id: str = NIFTY_INDEX_SECURITY_ID

    option_type: Literal["CE", "PE"]
    strike: str

    quantity: int = Field(gt=0)
    timeframe: int = Field(gt=0)           # minutes

    indicator: Literal["EMA9", "EMA20", "PARABOLIC_SAR"]
    indicator_period: Optional[int] = Field(default=None, gt=0)

    tracking_source: Literal["INDEX", "OPTION"]

    entry_price: Optional[float] = None
    entry_trade_id: Optional[str] = None
