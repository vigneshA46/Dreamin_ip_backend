from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


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

    # ---- indicator exit (can be switched off) ----
    indicator_enabled: bool = True
    timeframe: Optional[int] = Field(default=None, gt=0)      # minutes
    indicator: Optional[Literal["EMA9", "EMA20", "PARABOLIC_SAR", "HALFTREND"]] = None
    indicator_period: Optional[int] = Field(default=None, gt=0)
    tracking_source: Optional[Literal["INDEX", "OPTION"]] = None

    # ---- price exit: fixed target / SL, or trailing SL (only one) ----
    price_exit_type: Literal["NONE", "FIXED", "TRAILING"] = "NONE"
    price_source: Optional[Literal["INDEX", "OPTION"]] = None
    target_price: Optional[float] = Field(default=None, gt=0)
    sl_price: Optional[float] = Field(default=None, gt=0)
    trailing_start: Optional[float] = Field(default=None, gt=0)
    trailing_points: Optional[float] = Field(default=None, gt=0)
    current_sl: Optional[float] = Field(default=None, gt=0)   # saved trailing SL (restart)

    entry_price: Optional[float] = None
    entry_trade_id: Optional[str] = None

    def price_falls_for_profit(self):
        """Index PE is the only case where the price has to FALL for profit."""
        return self.price_source == "INDEX" and self.option_type == "PE"

    @model_validator(mode="after")
    def check_exit_rules(self):
        if self.indicator_enabled and not (
            self.indicator and self.timeframe and self.tracking_source
        ):
            raise ValueError(
                "indicator, timeframe and tracking_source are required when the indicator exit is on"
            )

        if not self.indicator_enabled and self.price_exit_type == "NONE":
            raise ValueError("Turn on at least one exit: indicator, target or trailing")

        if self.price_exit_type == "NONE":
            return self

        if not self.price_source:
            raise ValueError("price_source is required for a target / trailing exit")

        falls = self.price_falls_for_profit()

        if self.price_exit_type == "FIXED":
            if self.target_price is None and self.sl_price is None:
                raise ValueError("Enter a target price, a stop loss, or both")

            if (
                self.target_price is not None
                and self.sl_price is not None
                and (self.target_price >= self.sl_price if falls else self.target_price <= self.sl_price)
            ):
                raise ValueError(
                    "For a PE on the index, the target must be below the stop loss"
                    if falls else "The target must be above the stop loss"
                )

        else:
            if self.trailing_start is None or self.trailing_points is None or self.sl_price is None:
                raise ValueError("Trailing needs trailing_start, trailing_points and sl_price")

            if self.trailing_start >= self.sl_price if falls else self.trailing_start <= self.sl_price:
                raise ValueError(
                    "For a PE on the index, the trailing start must be below the stop loss"
                    if falls else "The trailing start must be above the stop loss"
                )

        return self
