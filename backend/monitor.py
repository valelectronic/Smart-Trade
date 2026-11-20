"""Lean price monitor for the Rebate Hunter."""

import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import websockets
from dotenv import load_dotenv

from cantex_api import connect_sdk, discover_market
from postgres_database import get_active_hunter_rules
from helpers import dec, now_iso

import config


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

WS_URL = os.getenv("CANTEX_WS_URL", "wss://api.cantex.io/v1/ws/public")
RATE_MARKET_SYMBOL = getattr(config, "RATE_MARKET_SYMBOL", "CC-USDC")
BTC_MARKET_SYMBOL = "BTC-USDC"

# Hunter rule refresh
RULE_FALLBACK_REFRESH_SECONDS = 900  # 15 minutes

# Wallet market refresh
WALLET_REFRESH_SECONDS = 300  # 5 minutes

# Retry failed discovery without querying continuously
DISCOVERY_RETRY_SECONDS = 30

# WebSocket
INITIAL_RECONNECT_SECONDS = 2
MAX_RECONNECT_SECONDS = 30
PRINT_COOLDOWN_SECONDS = 5

# Local signal written by api.py after successful Hunter rule changes
RULE_CHANGE_FILE = ROOT / "hunter_rules_changed.flag"

PRICES_FILE = ROOT / "prices.json"


def save_live_price(market, price_value):
    try:
        prices = {}

        if PRICES_FILE.exists():
            with PRICES_FILE.open("r") as f:
                prices = json.load(f)

        prices[market] = str(price_value)

        tmp_file = PRICES_FILE.with_suffix(".tmp")

        with tmp_file.open("w") as f:
            json.dump(prices, f)

        tmp_file.replace(PRICES_FILE)

    except Exception as exc:
        print(f"[PRICE SAVE ERROR] {exc}")


def usd_price_from_ticker(market_symbol, ticker_price, cc_usd_rate):
    symbol = str(market_symbol).upper()
    price = dec(ticker_price)

    if price is None or price <= 0:
        return None

    if symbol.endswith("-USDCX") or symbol.endswith("-USDC"):
        return price

    if symbol.endswith("-CC"):
        if cc_usd_rate is not None and cc_usd_rate > 0:
            return price * cc_usd_rate
        return None

    return None


def ticker_price_from_message(message):
    data = message.get("data") or {}

    if not isinstance(data, dict):
        return None

    for key in ("price", "last_price", "last"):
        value = dec(data.get(key))

        if value is not None and value > 0:
            return value

    return None


async def discover_hunter_channels():
    """Return discovered Hunter markets, or None if discovery failed."""
    market_channels = {}

    try:
        rules = await asyncio.to_thread(get_active_hunter_rules)

        # A successful database query with no active rules is valid.
        if not rules:
            return {}

        for rule in rules:
            try:
                token_a = rule["token_a"]
                token_b = rule["token_b"]
            except (KeyError, TypeError) as exc:
                print(f"[HUNTER ERROR] Invalid rule data: {exc}")
                return None

            for token in (token_a, token_b):
                symbol = f"{token}-CC"

                if symbol in market_channels:
                    continue

                try:
                    _, channel = await asyncio.to_thread(
                        discover_market, symbol
                    )

                    market_channels[symbol] = channel

                    print(
                        f"[HUNTER] Verified: "
                        f"{symbol}; ticker={channel}"
                    )

                except Exception as exc:
                    print(
                        f"[HUNTER ERROR] Could not discover "
                        f"{symbol}: {exc}"
                    )
                    # Keep the previous complete subscription set.
                    return None

        return market_channels

    except Exception as exc:
        print(f"[HUNTER FETCH ERROR] {exc}")
        return None


async def discover_wallet_channels(client):
    """Return markets for the top five wallet tokens, or None on failure."""
    market_channels = {}

    try:
        account = await client.get_account_info()
        balances = []

        for item in getattr(account, "tokens", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            unlocked = getattr(item, "unlocked_amount", None)

            if symbol and unlocked is not None and float(unlocked) > 0:
                token = str(symbol).upper()

                if token == "CC":
                    continue

                balances.append((token, float(unlocked)))

        # Preserve the existing selection logic: top five by raw balance.
        balances.sort(key=lambda item: item[1], reverse=True)
        top_tokens = [item[0] for item in balances[:5]]

        for token in top_tokens:
            market_symbol = f"{token}-CC"

            if market_symbol in market_channels:
                continue

            try:
                _, channel = await asyncio.to_thread(
                    discover_market, market_symbol
                )

                market_channels[market_symbol] = channel

                print(
                    f"[WALLET] Verified: "
                    f"{market_symbol}; ticker={channel}"
                )

            except Exception as exc:
                print(
                    f"[WALLET ERROR] Could not discover "
                    f"{market_symbol}: {exc}"
                )
                # Do not replace the previous complete wallet set.
                return None

        return market_channels

    except Exception as exc:
        print(f"[WALLET FETCH ERROR] {exc}")
        return None


def get_rule_change_stamp():
    """Return the local Hunter rule-change file timestamp."""
    try:
        return RULE_CHANGE_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return 0
    except OSError as exc:
        print(f"[HUNTER SIGNAL ERROR] {exc}")
        return 0


async def run_monitor():
    client = await connect_sdk()

    rate_channel = f"market.{RATE_MARKET_SYMBOL}.ticker"
    btc_channel = f"market.{BTC_MARKET_SYMBOL}.ticker"

    last_print_time = {}

    # Last successful discovery times.
    last_rule_refresh = 0.0
    last_wallet_refresh = 0.0

    # Last attempt times throttle retries after failures.
    last_rule_attempt = 0.0
    last_wallet_attempt = 0.0

    rule_discovery_failed = False
    wallet_discovery_failed = False

    last_seen_rule_change = get_rule_change_stamp()

    rule_channels = {}
    wallet_channels = {}
    market_channels = {}

    reconnect_delay = INITIAL_RECONNECT_SECONDS

    # Load the last known CC/USD rate.
    latest_cc_usd_rate = Decimal("0.10")

    if PRICES_FILE.exists():
        try:
            with PRICES_FILE.open("r") as f:
                prices = json.load(f)

            saved_rate = prices.get("CC-USDC")

            if saved_rate:
                latest_cc_usd_rate = Decimal(str(saved_rate))

        except Exception:
            pass

    # Initial discovery. A failure is retried inside the WebSocket loop.
    # A successful empty result is valid and clears obsolete subscriptions.
    last_rule_attempt = time.monotonic()
    initial_rule_signal = get_rule_change_stamp()
    initial_rules = await discover_hunter_channels()

    if initial_rules is not None:
        rule_channels = initial_rules
        last_rule_refresh = time.monotonic()
        last_seen_rule_change = initial_rule_signal
    else:
        rule_discovery_failed = True
        print(
            "[HUNTER] Initial discovery failed. "
            "Existing subscriptions, if any, will be preserved and retried."
        )

    last_wallet_attempt = time.monotonic()
    initial_wallet = await discover_wallet_channels(client)

    if initial_wallet is not None:
        wallet_channels = initial_wallet
        last_wallet_refresh = time.monotonic()
    else:
        wallet_discovery_failed = True
        print(
            "[WALLET] Initial discovery failed. "
            "Existing subscriptions, if any, will be preserved and retried."
        )

    while True:
        market_channels = {
            **rule_channels,
            **wallet_channels,
        }

        subscribed_channels = sorted(
            set(market_channels.values()) | {rate_channel, btc_channel}
        )

        print(f"[WS] Connecting to {WS_URL}")

        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=5_000_000,
            ) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "op": "subscribe",
                            "channels": subscribed_channels,
                            "limit": 500,
                        }
                    )
                )

                print(
                    f"[WS] Connected and subscribed: "
                    f"{', '.join(subscribed_channels)}"
                )

                reconnect_delay = INITIAL_RECONNECT_SECONDS

                while True:
                    now = time.monotonic()
                    subscription_changed = False

                    # ---------------------------------
                    # HUNTER RULE REFRESH
                    # ---------------------------------

                    current_signal = get_rule_change_stamp()
                    signal_changed = current_signal != last_seen_rule_change

                    fallback_due = (
                        last_rule_refresh == 0
                        or now - last_rule_refresh
                        >= RULE_FALLBACK_REFRESH_SECONDS
                    )

                    retry_due = (
                        last_rule_attempt == 0
                        or now - last_rule_attempt
                        >= DISCOVERY_RETRY_SECONDS
                    )

                    # A new signal after a successful discovery is acted on
                    # immediately. Failed discovery attempts are throttled.
                    immediate_signal_refresh = (
                        signal_changed and not rule_discovery_failed
                    )

                    if immediate_signal_refresh or (
                        retry_due and (signal_changed or fallback_due)
                    ):
                        if signal_changed:
                            print(
                                "[HUNTER] Rule change detected. "
                                "Refreshing subscriptions..."
                            )
                        else:
                            print("[HUNTER] Scheduled fallback refresh.")

                        # Capture before discovery. A newer change during
                        # discovery will be detected on the next pass.
                        signal_at_attempt = current_signal
                        last_rule_attempt = time.monotonic()

                        new_rule_channels = await discover_hunter_channels()

                        if new_rule_channels is not None:
                            last_rule_refresh = time.monotonic()
                            last_seen_rule_change = signal_at_attempt
                            rule_discovery_failed = False

                            if new_rule_channels != rule_channels:
                                print(
                                    "[HUNTER] Rule market subscriptions changed."
                                )
                                print(
                                    f"[HUNTER] Previous: "
                                    f"{sorted(rule_channels.keys())}"
                                )
                                print(
                                    f"[HUNTER] Current: "
                                    f"{sorted(new_rule_channels.keys())}"
                                )

                                rule_channels = new_rule_channels
                                subscription_changed = True
                        else:
                            rule_discovery_failed = True
                            print(
                                "[HUNTER] Discovery failed. "
                                "Keeping existing subscriptions."
                            )

                    # ---------------------------------
                    # WALLET MARKET REFRESH
                    # ---------------------------------

                    wallet_refresh_due = (
                        last_wallet_refresh == 0
                        or now - last_wallet_refresh
                        >= WALLET_REFRESH_SECONDS
                    )

                    wallet_retry_due = (
                        last_wallet_attempt == 0
                        or now - last_wallet_attempt
                        >= DISCOVERY_RETRY_SECONDS
                    )

                    if wallet_refresh_due and (
                        not wallet_discovery_failed or wallet_retry_due
                    ):
                        last_wallet_attempt = time.monotonic()

                        new_wallet_channels = (
                            await discover_wallet_channels(client)
                        )

                        if new_wallet_channels is not None:
                            last_wallet_refresh = time.monotonic()
                            wallet_discovery_failed = False

                            if new_wallet_channels != wallet_channels:
                                print(
                                    "[WALLET] Wallet market subscriptions changed."
                                )

                                wallet_channels = new_wallet_channels
                                subscription_changed = True
                        else:
                            wallet_discovery_failed = True
                            print(
                                "[WALLET] Discovery failed. "
                                "Keeping existing subscriptions."
                            )

                    # ---------------------------------
                    # RECONNECT ONLY WHEN SUBSCRIPTIONS CHANGE
                    # ---------------------------------

                    if subscription_changed:
                        print(
                            "[WS] Reconnecting to apply updated subscriptions..."
                        )
                        await ws.close()
                        break

                    # ---------------------------------
                    # RECEIVE WEBSOCKET MESSAGES
                    # ---------------------------------

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        message = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    operation = message.get("op")

                    if operation == "ping":
                        await ws.send(json.dumps({"op": "pong"}))
                        continue

                    if operation in {"subscribed", "unsubscribed", "pong"}:
                        continue

                    if operation == "error":
                        print(f"[WS SERVER ERROR] {message}")
                        continue

                    channel = message.get("channel") or message.get("topic")

                    # ---------------------------------
                    # CC/USD REFERENCE PRICE
                    # ---------------------------------

                    if channel == rate_channel:
                        rate_value = ticker_price_from_message(message)

                        if rate_value is None:
                            continue

                        latest_cc_usd_rate = rate_value
                        save_live_price(RATE_MARKET_SYMBOL, rate_value)
                        continue

                    # ---------------------------------
                    # BTC REFERENCE PRICE
                    # ---------------------------------

                    if channel == btc_channel:
                        btc_value = ticker_price_from_message(message)

                        if btc_value is not None:
                            save_live_price(BTC_MARKET_SYMBOL, btc_value)

                        continue

                    # ---------------------------------
                    # HUNTER AND WALLET MARKET PRICES
                    # ---------------------------------

                    market_symbol = next(
                        (
                            symbol
                            for symbol, market_channel in market_channels.items()
                            if market_channel == channel
                        ),
                        None,
                    )

                    if market_symbol is None:
                        continue

                    price = ticker_price_from_message(message)

                    if price is None:
                        continue

                    save_live_price(market_symbol, price)

                    usd_price = usd_price_from_ticker(
                        market_symbol,
                        price,
                        latest_cc_usd_rate,
                    )

                    if usd_price is not None:
                        usd_market_symbol = market_symbol.replace(
                            "-CC", "-USD"
                        )
                        save_live_price(usd_market_symbol, usd_price)

                    now = time.monotonic()

                    if (
                        now - last_print_time.get(market_symbol, 0)
                        >= PRINT_COOLDOWN_SECONDS
                    ):
                        print(
                            f"[{now_iso()}] "
                            f"{market_symbol}: "
                            f"ticker={price}; USD={usd_price}"
                        )
                        last_print_time[market_symbol] = now

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                f"[WS ERROR] {type(exc).__name__}: "
                f"{exc}; reconnecting in {reconnect_delay}s"
            )

            await asyncio.sleep(reconnect_delay)

            reconnect_delay = min(
                reconnect_delay * 2,
                MAX_RECONNECT_SECONDS,
            )


if __name__ == "__main__":
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\n[STOP] Monitoring stopped by user.")
