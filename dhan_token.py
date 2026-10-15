"""
Dhan access token, kept in memory only (no DB).

    get_access_token()        -> current token, generated on first use
    refresh_access_token()    -> generate a new one (TOTP), single-flight
    dhan_request()            -> requests wrapper: adds the token and, if Dhan
                                 rejects it, regenerates once and retries

A token is generated at startup (first call) and every morning by the
daily reset, so it never expires during market hours. The reactive
refresh covers tokens Dhan invalidates mid-day.
"""

import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pyotp
import requests
from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")

CLIENT_ID = os.getenv("CLIENT_ID")
PIN = os.getenv("PIN")
TOTP_SECRET = os.getenv("TOTP_SECRET")

GENERATE_URL = "https://auth.dhan.co/app/generateAccessToken"

# Dhan tokens live 24h; treat them as stale a bit earlier to be safe
TOKEN_LIFETIME = timedelta(hours=23)
MIN_REFRESH_GAP_SECONDS = 60

_lock = threading.Lock()
_token = None
_expires_at = None
_last_refresh = 0.0


def _generate_token():
    """TOTP login -> new access token. Retries on the next TOTP window."""

    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.post(
                GENERATE_URL,
                params={
                    "dhanClientId": CLIENT_ID,
                    "pin": PIN,
                    "totp": pyotp.TOTP(TOTP_SECRET).now(),
                },
                timeout=20,
            )
            response.raise_for_status()

            token = response.json().get("accessToken")
            if not token:
                raise Exception(f"No accessToken in response: {response.text}")

            return token

        except Exception as e:
            last_error = e
            print(f"[DhanToken] generate failed (attempt {attempt}):", e)

            # A TOTP code can't be reused - wait for the next 30s window
            time.sleep(30 - (time.time() % 30) + 1)

    raise Exception(f"Could not generate Dhan token: {last_error}")


def refresh_access_token(reason="manual", failed_token=None):
    """
    Generate a new token. Safe to call from many threads at once:
    only one generates, the rest get the new token.

    failed_token: the token a caller saw rejected. If another thread has
    already replaced it, that newer token is returned without regenerating.
    """

    global _token, _expires_at, _last_refresh

    with _lock:
        if failed_token and _token and _token != failed_token:
            return _token

        # Cooldown so repeated failures can't spam Dhan's login API
        if _token and time.monotonic() - _last_refresh < MIN_REFRESH_GAP_SECONDS:
            return _token

        print(f"[DhanToken] generating new token ({reason})")

        token = _generate_token()

        _token = token
        _expires_at = datetime.now(IST) + TOKEN_LIFETIME
        _last_refresh = time.monotonic()

        print(f"[DhanToken] new token ready, valid until ~{_expires_at:%Y-%m-%d %H:%M} IST")
        return _token


def get_access_token():
    with _lock:
        if _token and datetime.now(IST) < _expires_at:
            return _token

    return refresh_access_token("missing or expired")


def token_status():
    return {
        "has_token": _token is not None,
        "expires_at": _expires_at.isoformat() if _expires_at else None,
    }


def _is_auth_error(response):
    if response.status_code == 401:
        return True

    # Dhan returns DH-901 for an invalid / expired token
    return "DH-901" in (response.text or "")


def dhan_request(method, url, headers=None, **kwargs):
    """
    requests.request() with the Dhan token added. If Dhan rejects the
    token, regenerate it once and retry the call.
    """

    for attempt in range(2):
        token = get_access_token()

        response = requests.request(
            method,
            url,
            headers={**(headers or {}), "access-token": token, "client-id": CLIENT_ID or ""},
            **kwargs,
        )

        if attempt == 0 and _is_auth_error(response):
            print(f"[DhanToken] token rejected by {url}, regenerating")
            refresh_access_token("rejected by Dhan", failed_token=token)
            continue

        return response

    return response
