"""FastAPI endpoints for the Cantex Rebate Hunter (Lean Mode)."""

import asyncio
import json
import time
from pathlib import Path
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cantex_api import connect_sdk
from postgres_database import (
    init_db,
    get_connection,
    get_today_fees_spent,
    delete_hunter_rule,
)

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
                "UPDATE hunter_rules SET is_active = FALSE WHERE token_a = %s AND token_b = %s",
                (rule.token_a.upper(), rule.token_b.upper())
            )
            cur.execute(
                "INSERT INTO hunter_rules (token_a, token_b, net_min, net_max) VALUES (%s, %s, %s, %s)",
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
    try:
        total = get_today_fees_spent()
        return {"total_fees_cc": total, "status": "success"}
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)