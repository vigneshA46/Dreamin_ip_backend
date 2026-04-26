import requests
import json
import hashlib
import time


class ZebuClient:
    def __init__(self, uid, password, api_key, vendor_code, factor2):
        self.uid = uid
        self.password = password
        self.api_key = api_key
        self.vendor_code = vendor_code
        self.factor2 = factor2

        self.base_url = "https://go.mynt.in/NorenWClientTP"
        self.jKey = None
        self.actid = uid

    # -------------------------
    # 🔐 Utility: SHA256
    # -------------------------
    def _sha256(self, data):
        return hashlib.sha256(data.encode()).hexdigest()

    # -------------------------
    # 🔐 LOGIN
    # -------------------------
    def login(self):
        url = f"{self.base_url}/QuickAuth"

        pwd_hash = self._sha256(self.password)
        appkey_hash = self._sha256(f"{self.uid}|{self.api_key}")

        data = {
            "uid": self.uid,
            "pwd": pwd_hash,
            "factor2": self.factor2,
            "apkversion": "1.0.0",
            "imei": "12345678",
            "vc": self.vendor_code,
            "appkey": appkey_hash,
            "source": "API"
        }

        payload = "jData=" + json.dumps(data)
        headers = {"Content-Type": "application/json"}

        res = requests.post(url, headers=headers, data=payload)
        response = res.json()

        if response.get("stat") != "Ok":
            raise Exception(f"Login Failed: {response.get('emsg')}")

        self.jKey = response["susertoken"]
        self.actid = response["actid"]

        print("✅ Login Success")
        return response

    # -------------------------
    # 🔁 INTERNAL REQUEST HANDLER
    # -------------------------
    def _post(self, endpoint, data):
        if not self.jKey:
            self.login()

        url = f"{self.base_url}/{endpoint}"
        payload = "jData=" + json.dumps(data) + "&jKey=" + self.jKey
        headers = {"Content-Type": "application/json"}

        res = requests.post(url, headers=headers, data=payload)
        response = res.json()

        # 🔥 Auto re-login if session expired
        if response.get("stat") == "Not_Ok" and "Session Expired" in response.get("emsg", ""):
            print("🔄 Session expired. Re-logging...")
            self.login()
            payload = "jData=" + json.dumps(data) + "&jKey=" + self.jKey
            res = requests.post(url, headers=headers, data=payload)
            response = res.json()

        return response

    # -------------------------
    # 👤 CLIENT DETAILS
    # -------------------------
    def get_client_details(self):
        data = {
            "uid": self.uid,
            "actid": self.actid,
            "brkname": "ZEBU"
        }
        return self._post("ClientDetails", data)

    # -------------------------
    # 💥 PLACE ORDER
    # -------------------------
    def place_order(
        self,
        exch,
        tsym,
        qty,
        trantype,   # "B" or "S"
        prd="M",    # MIS/NRML
        prctyp="MKT",
        prc="0",
        ret="DAY"
    ):
        data = {
            "uid": self.uid,
            "actid": self.actid,
            "exch": exch,
            "tsym": tsym,
            "qty": str(qty),
            "prc": str(prc),
            "prd": prd,
            "trantype": trantype,
            "prctyp": prctyp,
            "ret": ret,
            "ordersource": "API"
        }

        response = self._post("PlaceOrder", data)

        if response.get("stat") != "Ok":
            print("❌ Order Failed:", response.get("emsg"))
        else:
            print("✅ Order Placed:", response.get("norenordno"))

        return response