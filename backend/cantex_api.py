"""Cantex API helpers with strict per-user SDK credential isolation.

Multi-user execution must use create_user_client(user_id). It loads only the
specified user's encrypted credential record and never reads shared signing
keys from the environment.

connect_legacy_sdk() is retained solely for local testing or migration. It
must never be called by the multi-user Hunter runtime.
"""

from __future__ import annotations

import asyncio
import os
from typing import Iterable

import requests
from cantex_sdk import CantexSDK, IntentTradingKeySigner, OperatorKeySigner

from config import PUBLIC_BASE
from crypto import decrypt_credentials
from postgres_database import get_user_credentials_ciphertext


# config.PUBLIC_BASE is already the canonical configured public API base.
# Derive the SDK base URL from it so this module does not duplicate config.
if not isinstance(PUBLIC_BASE, str) or not PUBLIC_BASE.rstrip("/").endswith("/v1/public"):
    raise RuntimeError("config.PUBLIC_BASE must end with /v1/public")
BASE_URL = PUBLIC_BASE.rstrip("/")[: -len("/v1/public")]


def discover_market(symbol: str):
    """Return public metadata and ticker channel for a native Cantex market."""
    symbol = str(symbol or "").strip()
    if not symbol:
        raise ValueError("A market symbol is required.")

    response = requests.get(f"{PUBLIC_BASE}/markets/info", timeout=15)
    response.raise_for_status()
    payload = response.json()
    markets = payload.get("data", {}).get("markets", [])

    for market in markets:
        if market.get("symbol") == symbol and market.get("source") == "cantex":
            channel = market.get("channels", {}).get("ticker")
            if not channel:
                raise RuntimeError(f"{symbol} has no ticker channel in market metadata")
            return market, channel

    raise RuntimeError(f"Native market {symbol!r} was not found in market metadata")


async def connect_private_ws(client):
    """Open this authenticated user's private account event stream.

    CantexSDK 0.4.2 exposes the authenticated connection through its tracked
    _ws_connect method. The SDK tracks the returned socket and closes it when
    client.close() is called.
    """
    if client is None:
        raise ValueError("A user-specific Cantex client is required.")
    connector = getattr(client, "_ws_connect", None)
    if connector is None:
        raise RuntimeError("Installed Cantex SDK does not expose _ws_connect")
    return await connector("/v1/ws/private", authenticated=True)


async def load_instruments(
    client,
    required_symbols: Iterable[str] | None = None,
) -> dict:
    """Load this account's instruments, optionally validating required symbols."""
    if client is None:
        raise ValueError("A user-specific Cantex client is required.")

    account_admin = await client.get_account_admin()
    instruments = {}
    for item in getattr(account_admin, "instruments", []) or []:
        symbol = getattr(item, "instrument_symbol", None)
        instrument = getattr(item, "instrument", None)
        if symbol and instrument is not None:
            instruments[str(symbol).upper()] = instrument

    if required_symbols is not None:
        required = [str(symbol).strip().upper() for symbol in required_symbols]
        missing = [symbol for symbol in required if symbol not in instruments]
        if missing:
            raise RuntimeError(f"Could not resolve SDK instruments: {missing}")

    return instruments


async def create_user_client(user_id: str) -> CantexSDK:
    """Create and authenticate a fresh SDK client for exactly one user.

    The encrypted database row is user-scoped. The SDK's default API-key file
    persistence is disabled because that file would be shared across users.
    No plaintext signing key or decrypted credential mapping is logged.
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")

    record = await asyncio.to_thread(get_user_credentials_ciphertext, user_id)
    if not record:
        raise RuntimeError("No credentials configured for user")

    try:
        credentials = decrypt_credentials(record, user_id)
        operator_key = credentials["operator_key"]
        trading_key = credentials["trading_key"]

        operator_signer = OperatorKeySigner.from_hex(operator_key)
        trading_signer = IntentTradingKeySigner.from_hex(trading_key)

        # Critical isolation: never use the SDK's shared default api_key_path.
        client = CantexSDK(
            operator_signer,
            trading_signer,
            base_url=BASE_URL,
            api_key_path=None,
        )

        try:
            await client.authenticate()
        except Exception:
            await client.close()
            raise

        return client
    except Exception:
        # Do not propagate SDK/decryption exceptions that might contain
        # request details or credential-related internals into user-facing logs.
        raise RuntimeError(
            "Could not create an authenticated Cantex client for this user."
        ) from None
    finally:
        # Best-effort reference release; Python strings cannot be zeroised.
        if "credentials" in locals():
            del credentials
        if "operator_key" in locals():
            del operator_key
        if "trading_key" in locals():
            del trading_key


async def create_user_runtime(user_id: str):
    """Build a fresh UserRuntime backed by this user's authenticated SDK client.

    UserRuntime is imported lazily to avoid the import cycle created because
    rebate_hunter itself imports connect_private_ws from this module.
    """
    client = await create_user_client(user_id)
    try:
        from rebate_hunter import UserRuntime

        return UserRuntime(user_id=str(user_id), client=client)
    except Exception:
        await client.close()
        raise


async def connect_legacy_sdk() -> CantexSDK:
    """Legacy single-user helper. Must not be called from the multi-user runtime.

    Reads shared OPERATOR_KEY / INTENT_TRADING_KEY environment variables.
    Kept only for local testing or migration; production Hunter execution must
    use create_user_client(user_id).
    """
    operator_key = os.getenv("OPERATOR_KEY")
    trading_key = os.getenv("INTENT_TRADING_KEY")
    if not operator_key:
        raise RuntimeError("OPERATOR_KEY is missing from the environment")
    if not trading_key:
        raise RuntimeError("INTENT_TRADING_KEY is missing from the environment")

    client = CantexSDK(
        OperatorKeySigner.from_hex(operator_key),
        IntentTradingKeySigner.from_hex(trading_key),
        base_url=BASE_URL,
    )
    try:
        await client.authenticate()
        return client
    except Exception:
        await client.close()
        raise
    finally:
        del operator_key, trading_key


__all__ = [
    "BASE_URL",
    "connect_private_ws",
    "connect_legacy_sdk",
    "create_user_client",
    "create_user_runtime",
    "discover_market",
    "load_instruments",
]
