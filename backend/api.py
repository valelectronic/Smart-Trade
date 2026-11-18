"""FastAPI endpoints for the Cantex Rebate Hunter (Lean Mode)."""

import asyncio
import json
import time
import os
import requests  # <-- ADDED
from pathlib import Path
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cantex_api import connect_sdk
from config import PUBLIC_BASE  # <-- ADDED
from postgres_database import (
    init_db,
    get_connection,
    get_today_fees_spent,
    delete_hunter_rule,
)

# --- All Tokens Cache ---
_all_tokens_cache = {"tokens": [], "timestamp": 0}
# --- Fees cache (protects Neon from 10s frontend polling) ---
_fees_cache = {"data": {}, "timestamp": 0}
# --- Kill switch file ---
PAUSE_FILE = Path(__file__).parent / "bot_control.json"



@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="Cantex Rebate Hunter API",
    version="2.0.0",
    description="Lean API for the Rebate Hunter. No Buy/Sell orders.",
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

# --- Data Models ---
class HunterRuleCreate(BaseModel):
    token_a: str
    token_b: str
    net_min: str
    net_max: str

class PauseRequest(BaseModel):
        paused: bool


@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/portfolio-value")
async def get_portfolio_value():
    try:
        client = await connect_sdk()
        try:
            account = await client.get_account_info()
            balances = {}
            for item in getattr(account, "tokens", []) or []:
                symbol = getattr(item, "instrument_symbol", None)
                unlocked = getattr(item, "unlocked_amount", None)
                if symbol and unlocked is not None:
                    balances[symbol.upper()] = Decimal(str(unlocked))
        finally:
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
            elif f"{token_upper}-CC" in prices:
                token_cc_price = Decimal(prices[f"{token_upper}-CC"])
                usd_price = token_cc_price * cc_rate
            elif token_upper == "CC":
                usd_price = cc_rate

            if usd_price > 0:
                value = amount * usd_price
                total_usd += value
                breakdown[token] = str(value)

        return {"total_usd": str(total_usd), "breakdown": breakdown, "status": "success"}
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.post("/api/hunter-pause")
def set_pause_state(req: PauseRequest):
    try:
        tmp = PAUSE_FILE.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump({"paused": req.paused}, f)
        os.replace(tmp, PAUSE_FILE)  # atomic — bot never reads a half-written file
        return {"status": "success", "paused": req.paused}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/hunter-pause")
def get_pause_state():
    if PAUSE_FILE.exists():
        try:
            with PAUSE_FILE.open("r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"paused": False}

@app.get("/api/balances")
async def get_balances():
    try:
        client = await connect_sdk()
        try:
            account = await client.get_account_info()
            balances = {}
            for item in getattr(account, "tokens", []) or []:
                symbol = getattr(item, "instrument_symbol", None)
                unlocked = getattr(item, "unlocked_amount", None)
                if symbol and unlocked is not None:
                    balances[symbol.upper()] = str(Decimal(str(unlocked)))
            return balances
        finally:
            close = getattr(client, "close", None)
            if close:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/hunter-status")
def get_hunter_status():
    status_file = Path(__file__).parent / "hunter_status.json"
    if not status_file.exists():
        return {"status": "Not running", "net_fee": 0, "pool_fee": 0}
    try:
        with status_file.open("r") as f:
            return json.load(f)
    except Exception as e:
        return {"status": "Error reading status", "error": str(e)}

@app.get("/api/hunter-rules")
def get_hunter_rules():
    connection = get_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT id, token_a, token_b, net_min, net_max FROM hunter_rules WHERE is_active = TRUE")
            rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()

@app.post("/api/hunter-rules")
def save_hunter_rule(rule: HunterRuleCreate):
    connection = get_connection()
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hunter_rules (token_a, token_b, net_min, net_max, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (token_a, token_b)
                DO UPDATE SET net_min = EXCLUDED.net_min,
                              net_max = EXCLUDED.net_max,
                              is_active = TRUE
                """,
                (rule.token_a.upper(), rule.token_b.upper(), rule.net_min, rule.net_max)
            )
        connection.commit()
        return {"status": "success", "message": "Rule saved"}
    finally:
        connection.close()

@app.delete("/api/hunter-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_hunter_rule(rule_id: int):
    try:
        deleted = delete_hunter_rule(rule_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Rule not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.get("/api/hunter-fees")
def get_fees_spent():
    global _fees_cache
    now = time.time()

    # Serve from cache for 30s — the frontend polls every 10s,
    # so without this we'd hit Neon 6x more than necessary.
    if now - _fees_cache["timestamp"] < 30 and _fees_cache["data"]:
        return _fees_cache["data"]

    try:
        total = get_today_fees_spent()
        data = {"total_fees_cc": total, "status": "success"}
        _fees_cache = {"data": data, "timestamp": now}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/hunter-history")
def get_hunter_history():
    connection = get_connection()
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT tx_id, pair, sell_token, buy_token, sell_amount, network_fee, pool_fee, usd_value, created_at 
                FROM rebate_swaps 
                ORDER BY created_at DESC 
                LIMIT 20
                """
            )
            rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()

@app.get("/api/market-prices")
def get_market_prices():
    """Lightweight endpoint that reads prices.json. No DB, no SDK."""
    prices_file = Path(__file__).parent / "prices.json"
    prices = {}
    if prices_file.exists():
        try:
            with prices_file.open("r") as f:
                prices = json.load(f)
        except Exception:
            pass
    
    return {
        "cc_usd": float(prices.get("CC-USDC", 0)),
        "btc_usd": float(prices.get("BTC-USDC", 0)),
        "status": "success"
    }

@app.get("/api/all-tokens")
def get_all_tokens():
    global _all_tokens_cache
    now = time.time()

    if now - _all_tokens_cache["timestamp"] < 600:
        return _all_tokens_cache["tokens"]

    try:
        response = requests.get(f"{PUBLIC_BASE}/markets/info", timeout=10)
        response.raise_for_status()
        markets = response.json().get("data", {}).get("markets", [])

        tokens = set()
        for m in markets:
            symbol = m.get("symbol", "")
            if "-" not in symbol:
                continue
            # Split from the RIGHT: last part is quote currency, rest is base token.
            # "TF-USDT-CC" -> base="TF-USDT", quote="CC"
            # "MOD-CC"     -> base="MOD",     quote="CC"
            # "BTC-CC"     -> base="BTC",     quote="CC"
            base, _, quote = symbol.rpartition("-")
            if base:
                tokens.add(base.upper())
            if quote:
                tokens.add(quote.upper())

        tokens.discard("CC")
        tokens.discard("USDC")
        tokens.discard("USDCX")

        result = sorted(list(tokens))
        _all_tokens_cache = {"tokens": result, "timestamp": now}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)