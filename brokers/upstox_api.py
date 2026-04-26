import upstox_client
from upstox_client.rest import ApiException
import requests


class UpstoxBroker:
    def __init__(self, access_token):
        configuration = upstox_client.Configuration()
        configuration.access_token = access_token

        self.client = upstox_client.ApiClient(configuration)

        self.order_api = upstox_client.OrderApiV3(self.client)
        self.user_api = upstox_client.UserApi(self.client)

        self.api_version = "2.0"

    # 🔹 Check Access Token (User Profile)
    def get_profile(self):
        try:
            response = self.user_api.get_profile(self.api_version)

            return {
                "status": "success",
                "data": response
            }

        except ApiException as e:
            return {
                "status": "error",
                "message": str(e)
            }

    # 🔹 Place Order
    def place_order(
        self,
        instrument_token,
        qty,
        side,
        order_type="MARKET",
        product="I",
        price=0,
        trigger_price=0,
        algo_name="dreamin_algo"
     ):
        try:
            body = upstox_client.PlaceOrderV3Request(
                quantity=qty,
                product=product,
                validity="DAY",
                price=price,
                instrument_token=instrument_token,
                order_type=order_type,
                transaction_type=side,
                disclosed_quantity=0,
                trigger_price=trigger_price,
                is_amo=False,
                slice=True
            )

            response = self.order_api.place_order(body, algo_name=algo_name)

            return {"status": "success", "data": response}

        except ApiException as e:
            return {"status": "error", "message": str(e)}

    # 🔹 Cancel Order
    def cancel_order(self, order_id):
        try:
            response = self.order_api.cancel_order(order_id)
            return {"status": "success", "data": response}
        except ApiException as e:
            return {"status": "error", "message": str(e)}

    # 🔹 Modify Order
    def modify_order(self, order_id, qty=None, price=None):
        try:
            body = upstox_client.ModifyOrderV3Request(
                quantity=qty,
                price=price
            )

            response = self.order_api.modify_order(body, order_id)
            return {"status": "success", "data": response}

        except ApiException as e:
            return {"status": "error", "message": str(e)}


    def get_option(
        self,
        symbol="NIFTY",
        option_type="CE",      # CE / PE
        mode="ATM",            # ATM / OFFSET / STRIKE
        offset=0,              # used if OFFSET
        strike=None,           # used if STRIKE
        expiry="current_week"
     ):
        try:
            url = "https://api.upstox.com/v2/instruments/search"

            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.client.configuration.access_token}"
            }

            params = {
                "instrument_types": option_type,
                "expiry": expiry,
                "page_number": 1,
                "records": 1
            }

            # 🔥 Mode handling
            if mode == "ATM":
                params["query"] = symbol
                params["atm_offset"] = 0

            elif mode == "OFFSET":
                params["query"] = symbol
                params["atm_offset"] = offset

            elif mode == "STRIKE":
                if strike is None:
                    return {"status": "error", "message": "strike required"}
                params["query"] = f"{symbol} {strike}"

            else:
                return {"status": "error", "message": "invalid mode"}

            response = requests.get(url, headers=headers, params=params)
            data = response.json()

            if data["status"] != "success" or not data["data"]:
                return {"status": "error", "message": "No instrument found"}

            inst = data["data"][0]

            return {
                "status": "success",
                "instrument_key": inst["instrument_key"],
                "lot_size": inst["lot_size"],
                "trading_symbol": inst["trading_symbol"],
                "strike": inst["strike_price"],
                "expiry": inst["expiry"]
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

            