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

DB_REFRESH_SECONDS = 7200  # 2 hours (saves DB compute)
INITIAL_RECONNECT_SECONDS = 2
MAX_RECONNECT_SECONDS = 30
PRINT_COOLDOWN_SECONDS = 5

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
    except Exception as e:
        print(f"[PRICE SAVE ERROR] {e}")

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
    market_channels = {}
    try:
        rules = await asyncio.to_thread(get_active_hunter_rules)
        if not rules:
            return market_channels
        for rule in rules:
            try:
                token_a = rule["token_a"]
                token_b = rule["token_b"]
            except (KeyError, TypeError):
                continue
            for token in [token_a, token_b]:
                symbol = f"{token}-CC"
                if symbol not in market_channels:
                    try:
                        _, channel = await asyncio.to_thread(discover_market, symbol)
                        market_channels[symbol] = channel
                        print(f"[HUNTER] Verified: {symbol}; ticker={channel}")
                    except Exception as e:
                        print(f"[HUNTER ERROR] Could not discover {symbol}: {e}")
    except Exception as e:
        print(f"[HUNTER FETCH ERROR] {e}")
    return market_channels

async def run_monitor():
    client = await connect_sdk()
    
    rate_channel = f"market.{RATE_MARKET_SYMBOL}.ticker"
    btc_channel = f"market.{BTC_MARKET_SYMBOL}.ticker"

    last_print_time = {}
    last_db_fetch_time = 0
    market_channels = {}
    reconnect_delay = INITIAL_RECONNECT_SECONDS
    
    # Initialize rate from memory, then try to load from prices.json
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

    while True:
        if last_db_fetch_time == 0:
            hunter_channels = await discover_hunter_channels()
            market_channels.update(hunter_channels)
            last_db_fetch_time = time.monotonic()

        subscribed_channels = sorted(set(market_channels.values()) | {rate_channel, btc_channel})
        print(f"[WS] Connecting to {WS_URL}")

        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=5_000_000) as ws:
                await ws.send(json.dumps({"op": "subscribe", "channels": subscribed_channels, "limit": 500}))
                print(f"[WS] Connected and subscribed: {', '.join(subscribed_channels)}")
                reconnect_delay = INITIAL_RECONNECT_SECONDS

                while True:
                    if time.monotonic() - last_db_fetch_time >= DB_REFRESH_SECONDS:
                        hunter_channels = await discover_hunter_channels()
                        market_channels.update(hunter_channels)
                        subscribed_channels = sorted(set(market_channels.values()) | {rate_channel, btc_channel})
                        await ws.send(json.dumps({"op": "subscribe", "channels": subscribed_channels, "limit": 500}))
                        print(f"[LEAN] Updated subscriptions: {', '.join(subscribed_channels)}")
                        last_db_fetch_time = time.monotonic()

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
                    data = message.get("data") or {}

                    if channel == rate_channel:
                        rate_value = ticker_price_from_message(message)
                        if rate_value is None:
                            continue
                        latest_cc_usd_rate = rate_value
                        save_live_price(RATE_MARKET_SYMBOL, rate_value)
                        continue

                    if channel == btc_channel:
                        btc_value = ticker_price_from_message(message)
                        if btc_value is not None:
                            save_live_price(BTC_MARKET_SYMBOL, btc_value)
                        continue

                    market_symbol = next((symbol for symbol, market_channel in market_channels.items() if market_channel == channel), None)
                    if market_symbol is None:
                        continue

                    price = ticker_price_from_message(message)
                    if price is None:
                        continue

                    save_live_price(market_symbol, price)
                    
                    usd_price = usd_price_from_ticker(market_symbol, price, latest_cc_usd_rate)
                    if usd_price is not None:
                        usd_market_symbol = market_symbol.replace("-CC", "-USD")
                        save_live_price(usd_market_symbol, usd_price)

                    now = time.monotonic()
                    if now - last_print_time.get(market_symbol, 0) >= PRINT_COOLDOWN_SECONDS:
                        print(f"[{now_iso()}] {market_symbol}: ticker={price}; USD={usd_price}")
                        last_print_time[market_symbol] = now

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[WS ERROR] {type(exc).__name__}: {exc}; reconnecting in {reconnect_delay}s")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\n[STOP] Monitoring stopped by user.")