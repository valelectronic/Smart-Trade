"""FastAPI endpoints for managing Cantex monitoring orders."""

import requests
import psycopg2
import psycopg2.errors
import uuid
import json
import asyncio
import time
from pathlib import Path
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from cantex_api import connect_sdk
from helpers import get_token_balance

from postgres_database import add_order, delete_order, get_all_orders, init_db
from config import PUBLIC_BASE

# --- Price Cache ---
_price_cache = {}
_last_price_fetch = 0
_price_lock = asyncio.Lock()

# --- Quote Rate Limiter ---
_last_quote_time = 0
_quote_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Cantex Monitor API",
    version="1.5.0",
    description="Manage monitoring orders. This API does not execute swaps.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://frontend-hazel-iota-70.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


subscription_queue = asyncio.Queue()


class OrderCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str | None = None
    action: Literal["BUY", "SELL"]
    sell_token: str = Field(min_length=1, max_length=50)
    buy_token: str = Field(min_length=1, max_length=50)
    trigger_price_usd: Decimal = Field(gt=0)
    direction: Literal["ABOVE", "BELOW"]
    amount: Decimal = Field(gt=0)

    def to_database_record(self):
        sell_token = self.sell_token.upper()
        buy_token = self.buy_token.upper()

        if sell_token == buy_token:
            raise ValueError("sell_token and buy_token must differ")

        safe_direction = "BELOW" if self.action == "BUY" else "ABOVE"

        target_token = buy_token
        target_market = f"{target_token}-CC"
        
        prices_file = Path(__file__).parent / "prices.json"
        
        if prices_file.exists():
            try:
                with prices_file.open("r") as f:
                    prices = json.load(f)
                    
                if target_market in prices:
                    live_price = Decimal(prices[target_market])
                    trigger_price = Decimal(self.trigger_price_usd)
                    
                    if self.action == "BUY" and trigger_price > live_price:
                        raise ValueError(f"BUY price (${trigger_price}) cannot be higher than current price of {target_token} (${live_price:.4f}).")
                        
                    if self.action == "SELL" and trigger_price < live_price:
                        raise ValueError(f"SELL price (${trigger_price}) cannot be lower than current price of {target_token} (${live_price:.4f}).")
            except json.JSONDecodeError:
                pass

        try:
            response = requests.get(f"{PUBLIC_BASE}/markets/info", timeout=10)
            response.raise_for_status()
            all_markets = response.json().get("data", {}).get("markets", [])
            valid_symbols = {m["symbol"] for m in all_markets if m.get("source") == "cantex"}
        except Exception as e:
            raise ValueError(f"Could not fetch markets from Cantex to validate route: {str(e)}")

        route_token = None
        market = ""

        if self.action == "BUY":
            monitor_token = buy_token
        else:
            monitor_token = sell_token

        if f"{sell_token}-{buy_token}" in valid_symbols:
            market = f"{sell_token}-{buy_token}"
        elif f"{buy_token}-{sell_token}" in valid_symbols:
            market = f"{buy_token}-{sell_token}"
        elif f"{buy_token}-CC" in valid_symbols:
            market = f"{buy_token}-CC"
            route_token = sell_token
        elif f"{sell_token}-CC" in valid_symbols:
            market = f"{sell_token}-CC"
            route_token = buy_token
        else:
            market = f"{buy_token}-CC"
            route_token = sell_token

        return {
            "id": self.id or uuid.uuid4().hex,
            "market": market,
            "action": self.action,
            "sell_token": sell_token,
            "buy_token": buy_token,
            "route_token": route_token,
            "trigger_price_usd": str(self.trigger_price_usd),
            "direction": safe_direction,
            "amount": str(self.amount),
        }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/markets")
def get_markets():
    try:
        response = requests.get(f"{PUBLIC_BASE}/markets/info", timeout=15)
        response.raise_for_status()
        markets = response.json().get("data", {}).get("markets", [])
        
        valid_markets = []
        for m in markets:
            if m.get("source") == "cantex":
                symbol = m.get("symbol", "")
                parts = symbol.split("-")
                if len(parts) == 2:
                    valid_markets.append({
                        "symbol": symbol,
                        "base": parts[0],
                        "quote": parts[1]
                    })
        
        return sorted(valid_markets, key=lambda x: x["symbol"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch markets: {str(e)}")


@app.get("/api/price")
async def get_price(sell: str, buy: str):
    try:
        s = sell.upper()
        b = buy.upper()
        
        # Stablecoins are always $1
        if s in {"USDC", "USDCX"} and b in {"USD", "USDC"}:
            return {"market": f"{s}-{b}", "price": "1.0"}
        if b in {"USDC", "USDCX"} and s in {"USD", "USDC"}:
            return {"market": f"{s}-{b}", "price": "1.0"}
        
        prices_file = Path(__file__).parent / "prices.json"
        prices = {}
        if prices_file.exists():
            try:
                with prices_file.open("r") as f:
                    prices = json.load(f)
            except Exception:
                pass

        cc_rate = Decimal(prices.get("CC-USDC", "0"))
        if cc_rate <= 0:
            return {"error": "CC rate not available", "price": "0"}

        if b in {"USD", "USDC"}:
            if f"{s}-USDC" in prices:
                return {"market": f"{s}-USDC", "price": prices[f"{s}-USDC"]}
            if f"{s}-CC" in prices:
                usd_price = Decimal(prices[f"{s}-CC"]) * cc_rate
                return {"market": f"{s}-CC", "price": str(usd_price), "converted": True}
        elif b == "CC":
            if f"{s}-CC" in prices:
                return {"market": f"{s}-CC", "price": prices[f"{s}-CC"]}
        
        return {"error": f"No price for {s}/{b}", "price": "0"}
    except Exception as e:
        return {"error": str(e), "price": "0"}


@app.get("/api/pool-info")
def get_pool_info(sell: str, buy: str, action: str = "BUY"):
    try:
        s = sell.upper()
        b = buy.upper()
        monitor_token = b if action.upper() == "BUY" else s
        market = f"{monitor_token}-CC"

        response = requests.get(f"{PUBLIC_BASE}/pools/state", timeout=5)
        response.raise_for_status()
        pools = response.json().get("data", {}).get("pools", [])
        
        for pool in pools:
            if pool.get("symbol") == market:
                reserves = pool.get("reserves", {})
                pool_size = reserves.get("CC") or reserves.get("cc") or "0"
                return {
                    "market": market,
                    "pool_size_cc": str(pool_size),
                    "fee_percent": pool.get("fee_percent", "0.15")
                }
        
        return {"error": "Pool not found", "pool_size_cc": "0", "fee_percent": "0"}
    except Exception as e:
        return {"error": str(e), "pool_size_cc": "0", "fee_percent": "0"}


@app.get("/api/quote")
async def get_quote(sell: str, buy: str, amount: str):
    global _last_quote_time
    async with _quote_lock:
        now = time.monotonic()
        if now - _last_quote_time < 1.0:
            await asyncio.sleep(1.0 - (now - _last_quote_time))
        _last_quote_time = time.monotonic()

    try:
        s = sell.upper()
        b = buy.upper()
        sell_amount = Decimal(amount)
        
        client = await connect_sdk()
        account_admin = await client.get_account_admin()
        
        instruments = {}
        for item in getattr(account_admin, "instruments", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            instrument = getattr(item, "instrument", None)
            if symbol and instrument is not None:
                instruments[str(symbol).upper()] = instrument
                
        if s not in instruments or b not in instruments:
            return {"error": "Missing SDK instrument", "status": "failed"}
            
        quote = await client.get_swap_quote(
            sell_amount=sell_amount,
            sell_instrument=instruments[s],
            buy_instrument=instruments[b],
        )
        
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
                
        output = 0.0
        network_fee = 0.0
        pool_fee = 0.0
        exec_time = 0.0
        pool_size = "0"

        try:
            returned_obj = getattr(quote, "returned", None)
            if returned_obj is not None:
                output = float(getattr(returned_obj, "amount", 0))

            fees_obj = getattr(quote, "fees", None)
            if fees_obj is not None:
                net_fee_obj = getattr(fees_obj, "network_fee", None)
                if net_fee_obj is not None:
                    network_fee = float(getattr(net_fee_obj, "amount", 0))
                pool_fee = float(getattr(fees_obj, "fee_percentage", 0))

            exec_sec = getattr(quote, "estimated_time_seconds", None)
            if exec_sec is not None:
                exec_time = float(exec_sec)

            pools_list = getattr(quote, "pools", None)
            if pools_list:
                for pool in pools_list:
                    reserve_a = getattr(pool, "reserve_a", None)
                    reserve_b = getattr(pool, "reserve_b", None)
                    token_a = getattr(pool, "token_a_instrument", None)
                    token_b = getattr(pool, "token_b_instrument", None)
                    
                    if token_a and str(token_a).upper() == "CC":
                        pool_size = str(reserve_a)
                        break
                    elif token_b and str(token_b).upper() == "CC":
                        pool_size = str(reserve_b)
                        break

        except Exception as parse_err:
            print(f"[QUOTE PARSE ERROR] {parse_err}")

        return {
            "expected_output": output,
            "network_fee_cc": network_fee,
            "pool_fee_percent": pool_fee,
            "execution_time_seconds": exec_time,
            "pool_size_cc": pool_size,
            "status": "success"
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.get("/api/portfolio-value")
async def get_portfolio_value():
    try:
        client = await connect_sdk()
        account = await client.get_account_info()
        balances = {}
        for item in getattr(account, "tokens", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            unlocked = getattr(item, "unlocked_amount", None)
            if symbol and unlocked is not None:
                balances[symbol.upper()] = Decimal(str(unlocked))
        
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result

        prices_file = Path(__file__).parent / "prices.json"
        prices = {}
        if prices_file.exists():
            try:
                with prices_file.open("r") as f:
                    prices = json.load(f)
            except Exception:
                pass
        
        cc_rate = Decimal(prices.get("CC-USDC", "0"))
        if cc_rate <= 0:
            return {"error": "CC rate not available", "status": "failed"}

        total_usd = Decimal("0")
        breakdown = {}

        for token, amount in balances.items():
            if amount <= 0:
                continue

            token_upper = token.upper()
            usd_price = Decimal("0")

            if f"{token_upper}-USDC" in prices:
                usd_price = Decimal(prices[f"{token_upper}-USDC"])
            elif f"{token_upper}-USDCX" in prices:
                usd_price = Decimal(prices[f"{token_upper}-USDCX"])
            elif f"{token_upper}-CC" in prices:
                usd_price = Decimal(prices[f"{token_upper}-CC"]) * cc_rate
            elif token_upper == "CC":
                usd_price = cc_rate
            elif token_upper == "USDCX":
                usd_price = Decimal("1")

            if usd_price > 0:
                value = amount * usd_price
                total_usd += value
                breakdown[token] = str(value)

        return {
            "total_usd": str(total_usd),
            "breakdown": breakdown,
            "status": "success"
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.post("/api/subscribe")
async def subscribe_market(market: str):
    await subscription_queue.put(market)
    return {"status": "subscribed", "market": market}


@app.get("/api/balances")
async def get_balances():
    try:
        client = await connect_sdk()
        account = await client.get_account_info()
        
        balances = {}
        
        for item in getattr(account, "tokens", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            unlocked = getattr(item, "unlocked_amount", None)
            
            if symbol and unlocked is not None:
                bal = Decimal(str(unlocked))
                balances[symbol.upper()] = str(bal)
                
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
                
        return balances
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch balances: {str(e)}")


@app.get("/api/orders")
def list_orders():
    return get_all_orders()


@app.post("/api/orders", status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate):
    try:
        record = order.to_database_record()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        result = add_order(record)
        try:
            from monitor import subscription_queue
            subscription_queue.put_nowait("refresh")
        except Exception:
            pass
        return result
    except psycopg2.errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An order with id {record['id']!r} already exists.",
        ) from exc
    except (psycopg2.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


active_connections = []

@app.websocket("/ws/prices")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
    except Exception:
        if websocket in active_connections:
            active_connections.remove(websocket)


async def broadcast_price(market: str, usd_price: float):
    for connection in active_connections:
        try:
            await connection.send_json({"market": market, "price": usd_price})
        except Exception:
            pass


@app.get("/api/history")
def get_history(sell: str, buy: str):
    try:
        s = sell.upper()
        b = buy.upper()
        
        if s == "CC":
            market = f"{b}-CC"
        else:
            market = f"{s}-CC"
            
        import random
        prices_file = Path(__file__).parent / "prices.json"
        base_price = 1.00
        
        if prices_file.exists():
            with prices_file.open("r") as f:
                prices = json.load(f)
                if market in prices:
                    base_price = float(prices[market])

        history = []
        for i in range(24):
            variation = random.uniform(-0.02, 0.02) 
            history.append({"time": f"{i}:00", "price": base_price + variation})
            
        return history
    except Exception as e:
        return []


@app.delete("/api/orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_order(order_id: str):
    try:
        deleted = delete_order(order_id)
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete the order.",
        ) from exc

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id!r} was not found.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)