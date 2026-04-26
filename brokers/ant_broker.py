from TradeMaster.TradeSync import *
import hashlib


class AntBroker:
    def __init__(self, user_id, auth_code, secret_key):
        self.user_id = user_id
        self.auth_code = auth_code
        self.secret_key = secret_key

        self.trade = TradeHub(
            user_id=user_id,
            auth_code=auth_code,
            secret_key=secret_key
        )
        self.session = None
    def login(self):
        checksum = hashlib.sha256(
            (self.user_id + self.auth_code + self.secret_key).encode()
        ).hexdigest()

        self.session = self.trade.get_session_id(check_sum=checksum)

        if not self.session or not self.session.get("userSession"):
            raise Exception(f"Login failed: {self.session}")

        print("Login successful")


    def get_instrument(self, exchange, symbol):
        return self.trade.get_instrument(exchange=exchange, symbol=symbol)

    def get_fno_instrument(self, exchange, symbol, expiry, strike, is_ce):
        return self.trade.get_instrument_for_fno(
            exchange=exchange,
            symbol=symbol,
            expiry_date=expiry,
            strike=strike,
            is_fut=False,
            is_CE=is_ce
        )

    def place_market_order(self, instrument, qty, side, tag=""):
        return self.trade.placeOrder(
            instrument=instrument,
            transactionType=side,
            quantity=str(qty),
            orderComplexity=OrderComplexity.Regular,
            product=ProductType.MTF,
            orderType=OrderType.Market,
            price='',
            slTriggerPrice="",      # ✅ REQUIRED
            slLegPrice="",          # ✅ REQUIRED
            targetLegPrice="",
            marketProtectionPercent="",  
            validity=PositionType.posDAY,
            orderTag=tag
        )

    def exit_position(self, instrument, qty, side):
        return self.trade.positionSqrOff(
            instrument=instrument,
            transactionType=side,
            quantity=str(qty),
            orderComplexity=OrderComplexity.Regular,
            product=ProductType.Intraday,
            orderType=OrderType.Market,
            price="",
            validity=PositionType.posDAY
        )