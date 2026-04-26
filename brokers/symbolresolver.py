import zipfile
import io
import requests
from datetime import datetime


class SymbolResolver:
    def __init__(self):
        self.symbols = []
        self.index = {}

    # -------------------------
    # 📥 Load Master File
    # -------------------------
    def load_master(self):
        url = "https://go.mynt.in/NFO_symbols.txt.zip"

        res = requests.get(url)
        z = zipfile.ZipFile(io.BytesIO(res.content))
        data = z.read(z.namelist()[0]).decode("utf-8")

        self.symbols = data.split("\n")
        print(f"✅ Loaded {len(self.symbols)} symbols")

        self._build_index()

    # -------------------------
    # ⚡ Build Fast Index
    # -------------------------
    def _build_index(self):
        for line in self.symbols:
            if not line.strip():
                continue

            parts = line.split("|")

            tsym = parts[0]

            # Filter only NIFTY options
            if "NIFTY" not in tsym:
                continue

            if "C" not in tsym and "P" not in tsym:
                continue

            try:
                # Example: NIFTY16APR26C23400
                name = "NIFTY"
                strike = int(tsym[-5:])
                opt_type = "CE" if "C" in tsym else "PE"

                expiry_str = tsym.replace("NIFTY", "")[:7]  # 16APR26
                expiry_date = datetime.strptime(expiry_str, "%d%b%y")

                key = (name, strike, opt_type)

                if key not in self.index:
                    self.index[key] = []

                self.index[key].append({
                    "tsym": tsym,
                    "expiry": expiry_date
                })

            except:
                continue

        print("⚡ Index built")

    # -------------------------
    # 🎯 Get Option Symbol
    # -------------------------
    def get_option_symbol(self, name, strike, opt_type, target_date):
        target_date = datetime.strptime(target_date, "%Y-%m-%d")

        key = (name, strike, opt_type)

        if key not in self.index:
            raise Exception("❌ No such strike/type found")

        contracts = self.index[key]

        # Find nearest expiry >= target_date
        valid = [c for c in contracts if c["expiry"] >= target_date]

        if not valid:
            raise Exception("❌ No future expiry found")

        nearest = min(valid, key=lambda x: x["expiry"])

        return nearest["tsym"], nearest["expiry"]