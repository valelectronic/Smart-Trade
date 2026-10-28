"""Cantex SDK connection, instrument lookup and market discovery."""

import os

import requests

from cantex_sdk import (
    CantexSDK,
    OperatorKeySigner,
    IntentTradingKeySigner,
)

from config import BASE_URL, PUBLIC_BASE


# ============================================================
# SDK CONNECTION
# ============================================================

async def connect_sdk():
    """Authenticate a Cantex SDK client using environment credentials."""
    operator_key = os.getenv("OPERATOR_KEY")
    trading_key = os.getenv("INTENT_TRADING_KEY")

    if not operator_key:
        raise RuntimeError(
            "OPERATOR_KEY is missing from .env"
        )

    if not trading_key:
        raise RuntimeError(
            "INTENT_TRADING_KEY is missing from .env"
        )

    client = CantexSDK(
        OperatorKeySigner.from_hex(operator_key),
        IntentTradingKeySigner.from_hex(trading_key),
        base_url=BASE_URL,
    )

    await client.authenticate()

    return client


# ============================================================
# INSTRUMENT LOOKUP
# ============================================================

async def load_instruments(client, required_symbols):
    """Load SDK instruments for all required sell/buy tokens."""
    account_admin = await client.get_account_admin()

    instruments = {}

    for item in getattr(
        account_admin,
        "instruments",
        [],
    ) or []:
        symbol = getattr(
            item,
            "instrument_symbol",
            None,
        )

        instrument = getattr(
            item,
            "instrument",
            None,
        )

        if symbol and instrument is not None:
            instruments[symbol] = instrument

    missing = sorted(
        set(required_symbols) - set(instruments)
    )

    if missing:
        raise RuntimeError(
            "Could not resolve SDK instruments for: "
            + ", ".join(missing)
        )

    return instruments


# ============================================================
# PUBLIC MARKET DISCOVERY
# ============================================================

def discover_market(symbol):
    """Return Cantex market metadata and its ticker channel."""
    response = requests.get(
        f"{PUBLIC_BASE}/markets/info",
        timeout=15,
    )

    response.raise_for_status()

    payload = response.json()

    markets = (
        payload.get("data", {}).get("markets", [])
    )

    for market in markets:
        if (
            market.get("symbol") == symbol
            and market.get("source") == "cantex"
        ):
            ticker_channel = (
                market.get("channels", {}).get("ticker")
            )

            if not ticker_channel:
                raise RuntimeError(
                    f"No ticker channel found for {symbol}"
                )

            return market, ticker_channel

    raise RuntimeError(
        f"Native Cantex market {symbol!r} "
        "was not found in market metadata."
    )