import requests
import uuid
from datetime import datetime
from dhanhq import dhanhq


class DhanAdapter:
    BASE_URL = "https://api.dhan.co/v2"


    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        

    # -------------------------------
    # 🔹 INTERNAL HELPER
    # -------------------------------
    def _headers(self):
        return {
            "Content-Type": "application/json",
            "access-token": self.access_token
        }

    def _handle_response(self, response):
        try:
            data = response.json()
        except Exception:
            raise Exception(f"Invalid response: {response.text}")

        if response.status_code not in [200, 202]:
            raise Exception(f"Dhan API Error: {data}")

        return data


    # -------------------------------
    # 🔹 2. PLACE ORDER
    # -------------------------------
    def place_order(
        self,
        security_id: str,
        side,  # BUY / SELL
        quantity: str,
        exchange_segment: str = "NSE_FNO",
        product_type: str = "INTRADAY",
        order_type: str = "MARKET",
    ):
        dhan = dhanhq(self.client_id,self.access_token)

        data = dhan.place_order(
        security_id = security_id,   # Nifty PE (example)
        exchange_segment=dhan.NSE_FNO,
        transaction_type=getattr(dhan, side),
        quantity=str(quantity),
        order_type=dhan.MARKET,
        product_type=dhan.INTRA,
        price=0
    )


        return data

    # -------------------------------
    # 🔹 1. GENERATE CORRELATION ID
    # -------------------------------
    def generate_correlation_id(self, strategy_id: str, run_id: str, action: str):
        unique = str(uuid.uuid4())[:8]
        return f"{strategy_id}_{run_id}_{action}_{unique}"
    # -------------------------------
    # 🔹 3. GET ORDER STATUS
    # -------------------------------
    def get_order_status(self, order_id: str = None, correlation_id: str = None):
        if order_id:
            url = f"{self.BASE_URL}/orders/{order_id}"
        elif correlation_id:
            url = f"{self.BASE_URL}/orders/external/{correlation_id}"
        else:
            raise ValueError("Either order_id or correlation_id required")

        response = requests.get(url, headers=self._headers())
        data = self._handle_response(response)

        return {
            "order_id": data.get("orderId"),
            "status": data.get("orderStatus"),
            "filled_qty": data.get("filledQty"),
            "remaining_qty": data.get("remainingQuantity"),
            "avg_price": data.get("averageTradedPrice"),
            "error": data.get("omsErrorDescription")
        }

    # -------------------------------
    # 🔹 4. GET ORDER BOOK
    # -------------------------------
    def get_order_book(self):
        url = f"{self.BASE_URL}/orders"

        response = requests.get(url, headers=self._headers())
        data = self._handle_response(response)

        return data  # return raw list for flexibility

    # -------------------------------
    # 🔹 5. GET TRADE BOOK
    # -------------------------------
    def get_trade_book(self):
        url = f"{self.BASE_URL}/trades"

        response = requests.get(url, headers=self._headers())
        data = self._handle_response(response)

        return data

    # -------------------------------
    # 🔹 6. CANCEL ORDER
    # -------------------------------
    def cancel_order(self, order_id: str):
        url = f"{self.BASE_URL}/orders/{order_id}"

        response = requests.delete(url, headers=self._headers())
        data = self._handle_response(response)

        return {
            "order_id": data.get("orderId"),
            "status": data.get("orderStatus")
        }