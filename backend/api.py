"""FastAPI endpoints for managing Cantex monitoring orders."""

import requests
import sqlite3
import uuid
import json
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from cantex_api import connect_sdk
from helpers import get_token_balance

from database import add_order, delete_order, get_all_orders, init_db
from config import PUBLIC_BASE


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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global queue to communicate with the monitor
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

        target_token = buy_token if self.action == "BUY" else sell_token
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

        if f"{sell_token}-{buy_token}" in valid_symbols:
            market = f"{sell_token}-{buy_token}"
        elif f"{buy_token}-{sell_token}" in valid_symbols:
            market = f"{buy_token}-{sell_token}"
        elif sell_token == "CC" and f"{buy_token}-CC" in valid_symbols:
            market = f"{buy_token}-CC"
        elif buy_token == "CC" and f"{sell_token}-CC" in valid_symbols:
            market = f"{sell_token}-CC"
        else:
            if f"{sell_token}-CC" in valid_symbols:
                market = f"{sell_token}-CC"
                route_token = buy_token
            elif f"{buy_token}-CC" in valid_symbols:
                market = f"{buy_token}-CC"
                route_token = sell_token
            else:
                raise ValueError(f"Could not find a valid route for {sell_token} to {buy_token} on Cantex.")

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
def get_price(sell: str, buy: str):
    try:
        s = sell.upper()
        b = buy.upper()
        
        if b in {"USD", "USDC"}:
            market = f"{s}-{b}"
        elif s in {"USD", "USDC"}:
            market = f"{b}-{s}"
        else:
            if s == "CC":
                market = f"{b}-CC"
            else:
                market = f"{s}-CC"
            
        prices_file = Path(__file__).parent / "prices.json"
        
        if not prices_file.exists():
            return {"error": "Price data not ready yet", "price": "0"}
            
        with prices_file.open("r") as f:
            prices = json.load(f)
            
        if market not in prices:
            return {"error": f"No live price for {market} yet", "price": "0"}
            
        return {"market": market, "price": prices[market]}
    except Exception as e:
        return {"error": str(e), "price": "0"}


@app.post("/api/subscribe")
async def subscribe_market(market: str):
    """Tell the monitor to start tracking this market."""
    await subscription_queue.put(market)
    return {"status": "subscribed", "market": market}


@app.get("/api/balances")
async def get_balances():
    try:
        client = await connect_sdk()
        account = await client.get_account_info()
        
        tokens = ["CC", "NEOPRIME", "USDCX", "USDXLR", "USX", "USTY", "tf-usdt"]
        balances = {}
        
        for t in tokens:
            bal = get_token_balance(account, t)
            if bal is not None:
                balances[t] = str(bal)
                
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
        return add_order(record)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An order with id {record['id']!r} already exists.",
        ) from exc
    except (sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# WebSocket for live prices
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
    except sqlite3.Error as exc:
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