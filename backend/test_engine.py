import asyncio
import json
import os
from pathlib import Path
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from cantex_api import discover_market
from rate_manager import LiveCCUsdRate
from helpers import dec

PRICES_FILE = Path(__file__).parent / "prices.json"

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

async def diagnose():
    print("--- STARTING DIAGNOSTIC ---")
    
    # 1. Test Market Discovery
    test_market = "NEOPRIME-CC"
    print(f"1. Discovering market for {test_market}...")
    try:
        metadata, channel = await asyncio.to_thread(discover_market, test_market)
        print(f"   -> SUCCESS: Found channel {channel}")
    except Exception as e:
        print(f"   -> FAILED: {e}")
        return

    # 2. Check if prices.json has the price
    print("\n2. Checking prices.json file...")
    if PRICES_FILE.exists():
        with PRICES_FILE.open("r") as f:
            prices = json.load(f)
        if test_market in prices:
            print(f"   -> SUCCESS: prices.json has {test_market} at {prices[test_market]}")
        else:
            print(f"   -> FAILED: {test_market} is NOT in prices.json")
    else:
        print("   -> FAILED: prices.json does not exist")

    # 3. Test WebSocket stream and conversion
    print(f"\n3. Connecting to WebSocket to test stream...")
    ws_url = os.getenv("CANTEX_WS_URL", "wss://api.cantex.io/v1/ws/public")
    rate_source = LiveCCUsdRate()
    rate_channel = "market.CC-USDC.ticker"
    
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"op": "subscribe", "channels": [channel, rate_channel]}))
        print(f"   -> Subscribed to {channel} and {rate_channel}")
        
        ticks_received = 0
        try:
            while ticks_received < 5:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                message = json.loads(raw)
                
                if message.get("op") in ["ping", "subscribed", "unsubscribed", "pong"]:
                    continue
                    
                data = message.get("data") or {}
                msg_channel = message.get("channel")
                
                if msg_channel == rate_channel:
                    val = dec(data.get("price"))
                    if val:
                        rate_source.update(val)
                        print(f"   [WS] Received CC-USDC rate: {val}")
                elif msg_channel == channel:
                    val = dec(data.get("price") or data.get("last_price") or data.get("last"))
                    if val:
                        ticks_received += 1
                        current_rate = rate_source.get_rate()
                        usd_val = usd_price_from_ticker(test_market, val, current_rate)
                        print(f"   [WS] Received {test_market} tick: {val}. Converted to USD: {usd_val} (Rate used: {current_rate})")
                        
        except asyncio.TimeoutError:
            print("   -> TIMEOUT: Did not receive enough ticks from WebSocket.")

if __name__ == "__main__":
    asyncio.run(diagnose())