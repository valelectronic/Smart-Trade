"""Central configuration for the Cantex Trading Assistant."""

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv


# ============================================================
# PATHS AND ENVIRONMENT
# ============================================================

ROOT = Path(__file__).resolve().parent

load_dotenv(ROOT / ".env")

CSV_PATH = ROOT / "monitor_log.csv"

BASE_URL = os.getenv(
    "CANTEX_BASE_URL",
    "https://api.cantex.io",
).rstrip("/")

PUBLIC_BASE = f"{BASE_URL}/v1/public"

WS_URL = os.getenv(
    "CANTEX_WS_URL",
    "wss://api.cantex.io/v1/ws/public",
)


# ============================================================
# GLOBAL SETTINGS
# ============================================================

RATE_MARKET_SYMBOL = "CC-USDC"

MAX_SLIPPAGE_PERCENT = Decimal("1.0")

COOLDOWN_SECONDS = 120

RATE_STALE_SECONDS = 120

RECONNECT_INITIAL_SECONDS = 2

RECONNECT_MAX_SECONDS = 30


# ============================================================
# WATCHLIST
# ============================================================
# 
# Notice the market is now "USDXLR-CC" because that is the 
# real pool that exists on Cantex. The script will convert 
# the CC price to USD automatically using the CC-USDC rate.
# ============================================================

WATCHLIST = [
    {
        "id": "usdxlr_buy",
        "market": "USDXLR-CC",
        "action": "BUY",
        "sell_token": "CC",
        "buy_token": "USDXLR",
        "trigger_price_usd": Decimal("0.98"),
        "direction": "BELOW",
        "amount": Decimal("10"),
    },
    {
        "id": "usdxlr_sell",
        "market": "USDXLR-CC",
        "action": "SELL",
        "sell_token": "USDXLR",
        "buy_token": "CC",
        "trigger_price_usd": Decimal("1.0150"),
        "direction": "ABOVE",
        "amount": Decimal("10"),
    },
]


# ============================================================
# CSV SCHEMA
# ============================================================

CSV_FIELDS = [
    "timestamp_utc",
    "event",
    "order_id",
    "market",
    "action",
    "price",
    "usd_price",
    "cc_usd_rate",
    "trigger_price_usd",
    "direction",
    "sell_token",
    "buy_token",
    "sell_amount",
    "expected_output",
    "slippage_sdk_value",
    "slippage_percent",
    "max_slippage_percent",
    "network_fee_amount",
    "network_fee_token",
    "pool_fee_percentage",
    "admin_fee_amount",
    "liquidity_fee_amount",
    "cc_balance",
    "sell_token_balance",
    "trigger_to_quote_ms",
    "quote_status",
    "preflight_status",
    "telegram_status",
    "details",
     "pool_fee_amount",
    "pool_size",
    "pool_size_token",
    "pool_share_percent",
    "execution_time_seconds",
    "net_output",
    "total_fees_output_equivalent"
]