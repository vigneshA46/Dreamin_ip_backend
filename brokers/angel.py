import pyotp
from SmartApi import SmartConnect


class AngelAdapter:
    def __init__(self, api_key, client_id, password, totp_secret):
        self.api_key = api_key
        self.client_id = client_id
        self.password = password
        self.totp_secret = totp_secret

        self.client = None
        self.feed_token = None
        self.jwt_token = None

    # =========================
    # LOGIN
    # =========================
    def login(self):
        try:
            self.client = SmartConnect(api_key=self.api_key)

            totp = pyotp.TOTP(self.totp_secret).now()

            data = self.client.generateSession(
                self.client_id,
                self.password,
                totp
            )

            if not data.get("status"):
                raise Exception(data.get("message"))

            self.jwt_token = data["data"]["jwtToken"]
            self.feed_token = self.client.getfeedToken()

            return True

        except Exception as e:
            raise Exception(f"Login Failed: {str(e)}")

    # =========================
    # PLACE ORDER
    # =========================
    def place_order(
        self,
        symbol,
        token,
        side,          # BUY / SELL
        quantity,
        order_type="MARKET",
        product_type="INTRADAY",
        price=0,
        exchange="NFO"
     ):
        try:
            params = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": side,
                "exchange": exchange,
                "ordertype": order_type,
                "producttype": product_type,
                "duration": "DAY",
                "price": price,
                "quantity": quantity
            }

            response = self.client.placeOrder(params)

            # CASE 1: response is string (order_id)
            if isinstance(response, str):
                return response

            # CASE 2: response is dict
            if isinstance(response, dict):
                if not response.get("status"):
                    raise Exception(response.get("message"))

                return response.get("data", {}).get("orderid")

            # fallback
            raise Exception(f"Unexpected response: {response}")

        except Exception as e:
            raise Exception(f"Order Failed: {str(e)}")

    # =========================
    # MODIFY ORDER
    # =========================
    def modify_order(
        self,
        order_id,
        quantity,
        price=0,
        order_type="LIMIT",
        product_type="INTRADAY"
     ):
        try:
            params = {
                "variety": "NORMAL",
                "orderid": order_id,
                "ordertype": order_type,
                "producttype": product_type,
                "duration": "DAY",
                "price": price,
                "quantity": quantity
            }

            response = self.client.modifyOrder(params)
            return response.get("status", False)

        except Exception as e:
            raise Exception(f"Modify Failed: {str(e)}")

    # =========================
    # CANCEL ORDER
    # =========================
    def cancel_order(self, order_id):
        try:
            response = self.client.cancelOrder(order_id, "NORMAL")
            return response.get("status", False)

        except Exception as e:
            raise Exception(f"Cancel Failed: {str(e)}")

    # =========================
    # ORDER BOOK
    # =========================
    def get_order_book(self):
        try:
            return self.client.orderBook().get("data", [])
        except Exception as e:
            raise Exception(f"Order Book Failed: {str(e)}")

    # =========================
    # TRADE BOOK
    # =========================
    def get_trade_book(self):
        try:
            return self.client.tradeBook().get("data", [])
        except Exception as e:
            raise Exception(f"Trade Book Failed: {str(e)}")

    # =========================
    # SINGLE ORDER DETAILS
    # =========================
    def get_order_details(self, order_id):
        try:
            orders = self.client.orderBook().get("data", [])

            for o in orders:
                if o["orderid"] == order_id:
                    return o

            return None

        except Exception as e:
            raise Exception(f"Fetch Order Failed: {str(e)}")

    # =========================
    # LTP
    # =========================
    def get_ltp(self, symbol, token, exchange="NFO"):
        try:
            data = self.client.ltpData(exchange, symbol, token)
            return float(data["data"]["ltp"])

        except Exception as e:
            raise Exception(f"LTP Failed: {str(e)}")

    # =========================
    # MARGIN
    # =========================
    def get_margin(self):
        try:
            data = self.client.rmsLimit()
            return float(data["data"]["availablecash"])

        except Exception as e:
            raise Exception(f"Margin Failed: {str(e)}")

    # =========================
    # LOGOUT
    # =========================
    def logout(self):
        try:
            if self.client:
                self.client.terminateSession(self.client_id)
        except:
            pass