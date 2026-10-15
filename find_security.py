import threading
import pandas as pd
import pytz
from io import StringIO
from dotenv import load_dotenv
from dhan_token import dhan_request


load_dotenv()

IDX_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
FNO_MASTER_URL   = "https://api.dhan.co/v2/instrument/NSE_FNO"

IST = pytz.timezone("Asia/Kolkata")




# =====================================================
# STEP 4: LOAD FNO MASTER
# =====================================================

def load_fno_master() -> pd.DataFrame:
    print("...downloading FNO master")

    r = dhan_request("GET", FNO_MASTER_URL, timeout=60)
    r.raise_for_status()

    # ✅ Use header from API (IMPORTANT)
    df = pd.read_csv(StringIO(r.text), low_memory=False)

    # ✅ Drop unwanted column
    if "Unnamed: 31" in df.columns:
        df = df.drop(columns=["Unnamed: 31"])

    # ✅ Type conversions
    df["STRIKE_PRICE"] = pd.to_numeric(df["STRIKE_PRICE"], errors="coerce")
    df["SM_EXPIRY_DATE"] = pd.to_datetime(df["SM_EXPIRY_DATE"], errors="coerce")

    return df



class FnoMaster:
    """
    Holds the FNO master and lets the daily reset swap in a fresh copy.
    Always read .df at use time - never keep the DataFrame around -
    so new weekly contracts show up without a redeploy.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._df = load_fno_master()

    @property
    def df(self):
        return self._df

    def reload(self):
        df = load_fno_master()
        with self._lock:
            self._df = df
        print(f"...FNO master reloaded ({len(df)} rows)")




def find_option_security(df, strike, option_type, trade_date, target_symbol):
    trade_date = pd.to_datetime(trade_date)

    opt = df[
        (df["INSTRUMENT"] == "OPTIDX") &
        (df["UNDERLYING_SYMBOL"] == target_symbol) &
        (df["STRIKE_PRICE"] == strike) &
        (df["OPTION_TYPE"] == option_type) &
        (df["SM_EXPIRY_DATE"] >= trade_date)
    ]

    if opt.empty:
        raise ValueError(f"❌ No {option_type} found for strike {strike}")

    return opt.sort_values("SM_EXPIRY_DATE").iloc[0]


