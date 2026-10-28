"""Cantex Version 1.5 monitoring engine."""

import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import websockets
from dotenv import load_dotenv

from cantex_api import connect_sdk, discover_market
from database import get_all_orders, init_db
from helpers import (
    dec,
    get_cc_balance,
    get_token_balance,
    log_csv,
    now_iso,
)
from preflight import quote_and_preflight
from rate_manager import LiveCCUsdRate
from telegram_alerts import TelegramAlerts
from api import broadcast_price, subscription_queue

import config

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

WS_URL = os.getenv("CANTEX_WS_URL", "wss://api.cantex.io/v1/ws/public")
RATE_MARKET_SYMBOL = getattr(config, "RATE_MARKET_SYMBOL", "CC-USDC")
BTC_MARKET_SYMBOL = "BTC-USDC"
ETH_MARKET_SYMBOL = "ETH-USDC"
COOLDOWN_SECONDS = float(getattr(config, "COOLDOWN_SECONDS", 60))

ORDER_REFRESH_SECONDS = 5
INITIAL_RECONNECT_SECONDS = 2
MAX_RECONNECT_SECONDS = 30

PRICES_FILE = ROOT / "prices.json"


def save_live_price(market, usd_price):
    """Save the latest USD price to a file and broadcast to WebSocket clients."""
    try:
        prices = {}
        if PRICES_FILE.exists():
            with PRICES_FILE.open("r") as f:
                prices = json.load(f)
        
        prices[market] = str(usd_price)
        
        with PRICES_FILE.open("w") as f:
            json.dump(prices, f)
            
        # Broadcast to connected WebSocket clients
        asyncio.create_task(broadcast_price(market, float(usd_price)))
            
    except Exception as e:
        print(f"[PRICE SAVE ERROR] {e}")


def log_event(event, order=None, **values):
    payload = {}
    if order is not None:
        payload.update({
            "order_id": order.get("id", ""),
            "market": order.get("market", ""),
            "trigger_price": order.get("trigger_price_usd", ""),
            "trigger_direction": order.get("direction", ""),
            "sell_token": order.get("sell_token", ""),
            "buy_token": order.get("buy_token", ""),
            "sell_amount": order.get("amount", ""),
        })
    payload.update(values)
    log_csv(event, **payload)


def order_signature(orders):
    return tuple(sorted((
        str(order["id"]), str(order["market"]), str(order["action"]),
        str(order["sell_token"]), str(order["buy_token"]),
        str(order["trigger_price_usd"]), str(order["direction"]), str(order["amount"])
    ) for order in orders))


async def load_all_instruments(client):
    admin = await client.get_account_admin()
    instruments = {}
    for item in getattr(admin, "instruments", []) or []:
        symbol = getattr(item, "instrument_symbol", None)
        instrument = getattr(item, "instrument", None)
        if symbol and instrument is not None:
            instruments[str(symbol).upper()] = instrument
    return instruments


async def discover_order_channels(orders):
    market_channels = {}
    for order in orders:
        symbol = str(order["market"])
        if symbol in market_channels:
            continue
        try:
            metadata, channel = await asyncio.to_thread(discover_market, symbol)
            market_channels[symbol] = channel
            print(f"[MARKET] Verified: {metadata.get('symbol')}; ticker={channel}")
        except Exception as exc:
            print(f"[MARKET ERROR] Order {order['id']}: could not discover {symbol}: {exc}")
            log_event("MARKET_DISCOVERY_FAILED", order, details=f"{type(exc).__name__}: {exc}")
    return market_channels


def usd_price_from_ticker(market_symbol, ticker_price, cc_usd_rate):
    symbol = str(market_symbol).upper()
    price = dec(ticker_price)
    if price is None or price <= 0:
        return None
    if symbol.endswith("-USDCX") or symbol.endswith("-USDC"):
        return price
    if symbol.endswith("-CC"):
        if cc_usd_rate is None or cc_usd_rate <= 0:
            return None
        return price * cc_usd_rate
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


async def run_monitor():
    init_db()

    telegram = None
    telegram_task = None
    client = None

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if telegram_token and telegram_chat_id:
        telegram = TelegramAlerts(telegram_token, telegram_chat_id)
        telegram_task = asyncio.create_task(telegram.poll(), name="cantex-telegram-poller")
        print("[TELEGRAM] Alerts and button acknowledgements enabled.")
    else:
        print("[TELEGRAM WARNING] Telegram credentials missing; alerts are disabled.")

    try:
        client = await connect_sdk()
        instruments = await load_all_instruments(client)
        account = await client.get_account_info()
        cc_balance = get_cc_balance(account)
        print(f"[ACCOUNT] Authenticated. CC balance: {cc_balance if cc_balance is not None else 'unavailable'}")

        rate_source = LiveCCUsdRate()
        rate_channel = f"market.{RATE_MARKET_SYMBOL}.ticker"
        btc_channel = f"market.{BTC_MARKET_SYMBOL}.ticker"
        eth_channel = f"market.{ETH_MARKET_SYMBOL}.ticker"

        last_trigger = {}
        armed = {}
        active_orders = []
        active_signature = None
        market_channels = {}
        reconnect_delay = INITIAL_RECONNECT_SECONDS

        print("[SAFETY] Quote-only mode. No swap-submission method is called.")

        while True:
            orders = await asyncio.to_thread(get_all_orders)
            signature = order_signature(orders)

            if signature != active_signature:
                print(f"[ORDERS] Database contains {len(orders)} order(s).")
                market_channels = await discover_order_channels(orders)
                active_orders = [order for order in orders if order["market"] in market_channels]
                for order in active_orders:
                    armed.setdefault(order["id"], True)
                    last_trigger.setdefault(order["id"], 0.0)
                active_ids = {order["id"] for order in active_orders}
                armed = {order_id: value for order_id, value in armed.items() if order_id in active_ids}
                last_trigger = {order_id: value for order_id, value in last_trigger.items() if order_id in active_ids}
                active_signature = signature

            subscribed_channels = sorted(set(market_channels.values()) | {rate_channel, btc_channel, eth_channel})
            print(f"[WS] Connecting to {WS_URL}")

            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=5_000_000) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "channels": subscribed_channels, "limit": 500}))
                    print(f"[WS] Connected and subscribed: {', '.join(subscribed_channels)}")
                    log_event("WS_CONNECTED", details=f"channels={','.join(subscribed_channels)}")
                    reconnect_delay = INITIAL_RECONNECT_SECONDS
                    next_order_refresh = time.monotonic() + ORDER_REFRESH_SECONDS

                    while True:
                        try:
                            new_market = subscription_queue.get_nowait()
                            if new_market not in market_channels:
                                try:
                                    _, channel = await asyncio.to_thread(discover_market, new_market)
                                    market_channels[new_market] = channel
                                    subscribed_channels = sorted(set(market_channels.values()) | {rate_channel, btc_channel, eth_channel})
                                    await ws.send(json.dumps({
                                        "op": "subscribe",
                                        "channels": [channel],
                                    }))
                                    print(f"[DYNAMIC] Subscribed to {new_market}")
                                except Exception as e:
                                    print(f"[DYNAMIC ERROR] Failed to subscribe to {new_market}: {e}")
                        except asyncio.QueueEmpty:
                            pass

                        if time.monotonic() >= next_order_refresh:
                            latest_orders = await asyncio.to_thread(get_all_orders)
                            latest_signature = order_signature(latest_orders)
                            if latest_signature != active_signature:
                                active_signature = latest_signature
                                market_channels = await discover_order_channels(latest_orders)
                                active_orders = [order for order in latest_orders if order["market"] in market_channels]
                                for order in active_orders:
                                    armed.setdefault(order["id"], True)
                                    last_trigger.setdefault(order["id"], 0.0)
                                active_ids = {order["id"] for order in active_orders}
                                armed = {key: value for key, value in armed.items() if key in active_ids}
                                last_trigger = {key: value for key, value in last_trigger.items() if key in active_ids}
                                subscribed_channels = sorted(set(market_channels.values()) | {rate_channel, btc_channel, eth_channel})
                                await ws.send(json.dumps({"op": "subscribe", "channels": subscribed_channels, "limit": 500}))
                                print(f"[ORDERS] Updated subscriptions: {', '.join(subscribed_channels)}")
                            next_order_refresh = time.monotonic() + ORDER_REFRESH_SECONDS

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=2)
                        except asyncio.TimeoutError:
                            rate_source.get_rate(warn_if_stale=True)
                            continue

                        try:
                            message = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            log_event("WS_BAD_JSON", details=str(raw)[:500])
                            continue

                        operation = message.get("op")
                        if operation == "ping":
                            await ws.send(json.dumps({"op": "pong"}))
                            continue
                        if operation in {"subscribed", "unsubscribed", "pong"}:
                            continue
                        if operation == "error":
                            log_event("WS_SERVER_ERROR", details=str(message))
                            print(f"[WS SERVER ERROR] {message}")
                            continue

                        channel = message.get("channel") or message.get("topic")
                        data = message.get("data") or {}

                        if channel == rate_channel:
                            rate_value = ticker_price_from_message(message)
                            if rate_value is None:
                                log_event("WS_BAD_RATE", details=f"data={data}")
                                continue
                            rate_source.update(rate_value)
                            # Broadcast the rate to the UI (it's already USD)
                            asyncio.create_task(broadcast_price(RATE_MARKET_SYMBOL, float(rate_value)))
                            continue

                        if channel == btc_channel:
                            btc_value = ticker_price_from_message(message)
                            if btc_value is not None:
                                # External stream is already USD
                                asyncio.create_task(broadcast_price(BTC_MARKET_SYMBOL, float(btc_value)))
                            continue

                        if channel == eth_channel:
                            eth_value = ticker_price_from_message(message)
                            if eth_value is not None:
                                # External stream is already USD
                                asyncio.create_task(broadcast_price(ETH_MARKET_SYMBOL, float(eth_value)))
                            continue

                        market_symbol = next((symbol for symbol, market_channel in market_channels.items() if market_channel == channel), None)
                        if market_symbol is None:
                            continue
                        if (isinstance(data, dict) and data.get("market") and str(data["market"]).upper() != market_symbol.upper()):
                            continue

                        price = ticker_price_from_message(message)
                        if price is None:
                            log_event("WS_BAD_PRICE", details=f"channel={channel}; data={data}")
                            continue

                        cc_usd_rate = rate_source.get_rate(warn_if_stale=True)
                        usd_price = usd_price_from_ticker(market_symbol, price, cc_usd_rate)
                        stamp = now_iso()

                        if usd_price is None:
                            log_event("USD_PRICE_UNAVAILABLE", market=market_symbol, price=price, cc_usd_rate=cc_usd_rate)
                            continue

                        save_live_price(market_symbol, usd_price)
                        print(f"[{stamp}] {market_symbol}: ticker={price}; USD-equivalent={usd_price}")
                        log_event("PRICE", market=market_symbol, price=price, usd_price=usd_price, cc_usd_rate=cc_usd_rate)

                        for order in active_orders:
                            if order["market"] != market_symbol:
                                continue
                            order_id = order["id"]
                            target = dec(order["trigger_price_usd"])
                            direction = str(order["direction"]).upper()

                            if target is None or target <= 0:
                                log_event("INVALID_ORDER_TRIGGER", order, details="Trigger price is invalid")
                                continue
                            if direction not in {"ABOVE", "BELOW"}:
                                log_event("INVALID_ORDER_DIRECTION", order, details=f"direction={direction}")
                                continue

                            condition_met = (usd_price >= target if direction == "ABOVE" else usd_price <= target)
                            condition_cleared = (usd_price < target if direction == "ABOVE" else usd_price > target)
                            cooldown_finished = (time.monotonic() - last_trigger.get(order_id, 0.0) >= COOLDOWN_SECONDS)

                            if condition_cleared and cooldown_finished:
                                armed[order_id] = True

                            if condition_met and armed.get(order_id, True) and cooldown_finished:
                                armed[order_id] = False
                                last_trigger[order_id] = time.monotonic()
                                print(f"[TRIGGER] {order_id}: {direction} ${target}; observed=${usd_price}")
                                log_event("TRIGGER", order, price=price, usd_price=usd_price, cc_usd_rate=cc_usd_rate, details=f"trigger_timestamp={stamp}")
                                await quote_and_preflight(client=client, instruments=instruments, order=order, price=price, usd_price=usd_price, cc_usd_rate=cc_usd_rate, trigger_time=stamp, telegram=telegram)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"[WS ERROR] {message}; reconnecting in {reconnect_delay}s")
                log_event("WS_DISCONNECTED", details=message)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_SECONDS)

    finally:
        if telegram_task is not None:
            telegram_task.cancel()
            try:
                await telegram_task
            except asyncio.CancelledError:
                pass
        if client is not None:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result

if __name__ == "__main__":
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\n[STOP] Monitoring stopped by user.")