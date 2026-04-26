import pandas as pd
import requests
import os

URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
FILE = "angel_instruments.csv"

class FindInstrument:
    def __init__(self):
        if not os.path.exists(FILE):
            data = requests.get(URL).json()
            pd.DataFrame(data).to_csv(FILE, index=False)

        self.df = pd.read_csv(FILE)
        self.df["strike"] = self.df["strike"].astype(float) / 100
        self.df["expiry"] = pd.to_datetime(self.df["expiry"], format="%d%b%Y", errors="coerce")

    def get_index(self, name):
        row = self.df[(self.df["name"] == name) & (self.df["exch_seg"] == "NSE") & (self.df["instrumenttype"]=="AMXIDX")].iloc[0]
        return row

    def get_option(self, name, strike, opt_type):
        df = self.df[
            (self.df["name"] == name) &
            (self.df["instrumenttype"] == "OPTIDX")
        ]

        expiry = df.sort_values("expiry").iloc[0]["expiry"]
        df = df[df["expiry"] == expiry]

        row = df[
            (df["strike"] == strike) &
            (df["symbol"].str.endswith(opt_type))
        ].iloc[0]

        return row

    def get_mcx_option(self, name, strike, opt_type):
        df = self.df[
            (self.df["name"] == name) &
            (self.df["instrumenttype"] == "OPTFUT")
        ]

        expiry = df.sort_values("expiry").iloc[0]["expiry"]
        df = df[df["expiry"] == expiry]

        row = df[
            (df["strike"] == strike) &
            (df["symbol"].str.endswith(opt_type))
        ].iloc[0]

        return row


    def get_futures(self, name, seg):
        df = self.df[
            (self.df["name"] == name) &
            (self.df["instrumenttype"] == "FUTIDX")
        ]

        expiry = df.sort_values("expiry").iloc[0]["expiry"]
        df = df[df["expiry"] == expiry]

        return df


    def get_mcx_futures(self, name):
        df = self.df[
            (self.df["name"] == name) &
            (self.df["instrumenttype"] == "FUTCOM")&
            (self.df["exch_seg"] == "MCX")
        ]

        expiry = df.sort_values("expiry").iloc[0]["expiry"]
        df = df[df["expiry"] == expiry]

        return df
