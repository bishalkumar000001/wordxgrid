"""
ludo_auth.py — Telegram WebApp initData validation.
Validates HMAC-SHA256 signature from Telegram Mini App initData.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import unquote

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


def validate_init_data(init_data: str, max_age_seconds: int = 86400):
    """
    Validate Telegram WebApp initData HMAC-SHA256 signature.
    Returns parsed user dict on success, None on failure.
    """
    if not init_data:
        return None
    if not BOT_TOKEN:
        # Dev mode: skip validation but still parse
        return _parse_user_from_init_data(init_data)

    try:
        params = _parse_query_string(init_data)
        received_hash = params.pop("hash", None)
        if not received_hash:
            return None

        auth_date = int(params.get("auth_date", 0))
        if time.time() - auth_date > max_age_seconds:
            return None

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        computed = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed, received_hash):
            return None

        return _parse_user_from_params(params)

    except Exception:
        return None


def _parse_query_string(init_data: str) -> dict:
    """Parse URL-encoded query string into dict."""
    params = {}
    for part in init_data.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = unquote(v)
    return params


def _parse_user_from_params(params: dict) -> dict:
    """Extract and parse the user object from decoded params."""
    try:
        user_str = params.get("user", "{}")
        return json.loads(user_str)
    except Exception:
        return {}


def _parse_user_from_init_data(init_data: str) -> dict:
    """Parse user from initData without validation (dev/test only)."""
    try:
        params = _parse_query_string(init_data)
        return _parse_user_from_params(params)
    except Exception:
        return {}


def get_user_display_name(user: dict) -> str:
    """Return a display name from a Telegram user dict."""
    first = user.get("first_name", "").strip()
    last = user.get("last_name", "").strip()
    if last:
        return f"{first} {last}".strip()
    return first or f"User{user.get('id', 0)}"
