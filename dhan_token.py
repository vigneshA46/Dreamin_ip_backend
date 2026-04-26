import os
import time
import requests
import pyotp
from datetime import datetime
from dotenv import load_dotenv
from postgres import get_db_connection

load_dotenv()

BROKER = "dhan"
CLIENT_ID = os.getenv("CLIENT_ID")
PIN = os.getenv("PIN")
TOTP_SECRET = os.getenv("TOTP_SECRET")


def get_token_from_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT access_token, expiry_time
        FROM broker_access_tokens
        WHERE broker_name = %s AND client_id = %s
    """, (BROKER, CLIENT_ID))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return None, None

    return row[0], row[1]


def save_token_to_db(token, expiry):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO broker_access_tokens
        (broker_name, client_id, access_token, expiry_time)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (broker_name, client_id)
        DO UPDATE SET
            access_token = EXCLUDED.access_token,
            expiry_time = EXCLUDED.expiry_time,
            updated_at = NOW()
    """, (BROKER, CLIENT_ID, token, expiry))

    conn.commit()
    cur.close()
    conn.close()


def get_access_token():
    # 1️⃣ Check DB
    token, expiry = get_token_from_db()

    if token and expiry > datetime.utcnow():
        print("✅ Using cached Dhan token from DB")
        return token

    # 2️⃣ Generate new token
    totp = pyotp.TOTP(TOTP_SECRET).now()

    response = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": CLIENT_ID,
            "pin": PIN,
            "totp": totp
        },
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    token = data["accessToken"]
    expiry = datetime.fromisoformat(data["expiryTime"])

    # 3️⃣ Save to DB
    save_token_to_db(token, expiry)

    print("🔐 New Dhan token generated & saved to DB")

    return token

 