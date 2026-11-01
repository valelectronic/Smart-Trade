import asyncio
import json
import os
from pathlib import Path
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from helpers import dec
from rate_manager import LiveCCUsdRate

PRICES_FILE = Path(__file__).parent / "prices.json"

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
            
        try:
            if PRICES_FILE.exists():
                with PRICES_FILE.open("r") as f:
                    prices = json.load(f)
                rate_from_file = prices.get("CC-USDC")
                if rate_from_file:
                    return price * dec(rate_from_file)
        except Exception as e:
            print(f"[PRICE FALLBACK ERROR] {e}")
            
        return None
    return None

async def test_real_logic():
    ws_url = "wss://api.cantex.io/v1/ws/public"
    rate_source = LiveCCUsdRate()
    rate_channel = "market.CC-USDC.ticker"
    neoprime_channel = "market.NEOPRIME-CC.ticker"

    print("Connecting to Cantex WebSocket...")
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"op": "subscribe", "channels": [neoprime_channel, rate_channel]}))
        print(f"Subscribed to {neoprime_channel} and {rate_channel}")
        
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
                message = json.loads(raw)
                
                if message.get("op") in ["ping", "subscribed", "unsubscribed", "pong"]:
                    continue
                    
                data = message.get("data") or {}
                channel = message.get("channel")
                
                if channel == rate_channel:
                    val = dec(data.get("price"))
                    if val:
                        rate_source.update(val)
                        print(f"[RATE] CC-USDC: {val}")
                        
                        # --- NEW: SAVE THE RATE TO prices.json ---
                        try:
                            prices = {}
                            if PRICES_FILE.exists():
                                with PRICES_FILE.open("r") as f:
                                    prices = json.load(f)
                            prices["CC-USDC"] = str(val)
                            with PRICES_FILE.open("w") as f:
                                json.dump(prices, f)
                        except Exception as e:
                            print(f"[SAVE RATE ERROR] {e}")
                        # ------------------------------------------
                
                elif channel == neoprime_channel:
                    val = dec(data.get("price") or data.get("last_price") or data.get("last"))
                    if val:
                        current_rate = rate_source.get_rate()
                        usd_val = usd_price_from_ticker("NEOPRIME-CC", val, current_rate)
                        print(f"[NEOPRIME] tick: {val} | Rate: {current_rate} | USD: {usd_val}")
                        
        except asyncio.TimeoutError:
            print("TIMEOUT")

if __name__ == "__main__":
    asyncio.run(test_real_logic())