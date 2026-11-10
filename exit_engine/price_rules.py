"""
Price-based exits, checked on every tick of the chosen chart (index or option).

    FIXED     target and/or stop loss
    TRAILING  stop loss that steps up once price reaches the start level

Direction: an option, or an index CE, profits when price RISES.
An index PE profits when the index FALLS, so every level is mirrored
(target below, SL above, trailing SL moves down).

Trailing steps (CE, start 25050, SL 25020, 30 points):
    LTP reaches 25050 -> SL stays 25020
    LTP reaches 25080 -> SL 25050
    LTP reaches 25110 -> SL 25080 ...
i.e. the SL moves `points` for every `points` price moves past the start.
It only ever moves in the profit direction.
"""

import math
from dataclasses import dataclass
from typing import Optional


def fmt(value):
    """25500.0 -> '25500', 25012.35 -> '25012.35'"""
    return f"{value:.2f}".rstrip("0").rstrip(".")


@dataclass
class PriceResult:
    reason: Optional[str] = None     # TARGET / STOP_LOSS / TRAILING_SL when an exit fires
    detail: Optional[str] = None     # human readable text for the log
    new_sl: Optional[float] = None   # set when the trailing SL moved


class PriceRules:

    def __init__(
        self,
        price_exit_type,
        falls,
        target_price=None,
        sl_price=None,
        trailing_start=None,
        trailing_points=None,
        current_sl=None,
        where="the option",
    ):
        """
        falls      : True when price must fall for profit (index PE)
        current_sl : saved trailing SL when resuming after a restart
        where      : "NIFTY index" / "the option", only used in messages
        """

        self.type = price_exit_type
        self.falls = falls
        self.target = target_price
        self.base_sl = sl_price
        self.start = trailing_start
        self.points = trailing_points
        self.where = where

        self.sl = current_sl if current_sl is not None else sl_price

    def evaluate(self, ltp):
        """Check one price. Exits take priority over moving the SL."""

        # ----- target
        if self.target is not None and (
            ltp <= self.target if self.falls else ltp >= self.target
        ):
            return PriceResult(
                reason="TARGET",
                detail=f"Target {fmt(self.target)} reached on {self.where} (LTP {ltp:.2f})",
            )

        # ----- stop loss (initial, or the trailed one)
        if self.sl is not None and (ltp >= self.sl if self.falls else ltp <= self.sl):
            trailed = self.type == "TRAILING" and self.sl != self.base_sl
            label = "Trailing SL" if trailed else "Stop loss"

            return PriceResult(
                reason="TRAILING_SL" if trailed else "STOP_LOSS",
                detail=f"{label} {fmt(self.sl)} hit on {self.where} (LTP {ltp:.2f})",
            )

        # ----- trail the SL
        if self.type == "TRAILING":
            progress = (self.start - ltp) if self.falls else (ltp - self.start)

            if progress >= 0:
                steps = math.floor(progress / self.points + 1e-9)
                moved = steps * self.points
                new_sl = round(self.base_sl - moved if self.falls else self.base_sl + moved, 2)

                if (new_sl < self.sl) if self.falls else (new_sl > self.sl):
                    self.sl = new_sl
                    return PriceResult(new_sl=new_sl)

        return PriceResult()
