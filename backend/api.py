"""FastAPI endpoints for the Cantex Rebate Hunter.

Control-state database functions are implemented in the next database stage:
- get_hunter_control_state()
- set_daily_swaps_remaining(swaps_remaining)
- clear_manual_intervention_lock(resolution_note)
"""

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
from pydantic import BaseModel, Field

from cantex_api import connect_sdk
from config import PUBLIC_BASE
from postgres_database import (
    init_db,
    get_connection,
    delete_hunter_rule,
    get_hunter_control_state,
    set_daily_swaps_remaining,
    clear_manual_intervention_lock,
)

# --- All Tokens Cache ---
_all_tokens_cache = {"tokens": [], "timestamp": 0}
# Rule-change notification remains a lightweight local signal.
RULE_CHANGE_FILE = Path(__file__).resolve().parent / "hunter_rules_changed.flag"


def signal_hunter_rules_changed():
    """
    Notify the monitor that Hunter Rules have changed.

    Uses an atomic file replacement so the monitor never
    reads a partially written notification.
    """
    try:
        tmp = RULE_CHANGE_FILE.with_suffix(".tmp")

        with tmp.open("w") as f:
            f.write(str(time.time_ns()))

        os.replace(tmp, RULE_CHANGE_FILE)

        print("[API] Hunter Rules change signal written.")

    except Exception as e:
        # The database change has already been committed.
        # Do not report a failed rule save because the local
        # notification failed. The monitor's 15-minute
        # fallback refresh remains available.
        print(f"[API WARNING] Could not signal Hunter Rules change: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="Cantex Rebate Hunter API",
    version="2.1.0",
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

class DailyAllowanceRequest(BaseModel):
    swaps_remaining: int = Field(ge=0, le=25)

class ManualInterventionClearRequest(BaseModel):
    acknowledged: bool
    resolution_note: str = Field(min_length=5, max_length=1000)


@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/hunter-control")
def get_hunter_control():
    """Return persistent intervention-lock and daily-allowance state."""
    try:
        state = get_hunter_control_state()
        # Keep both names during the UI/backend transition.
        state["manual_lock"] = bool(state.get("manual_intervention_required", False))
        return state
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read Hunter control state: {e}")


@app.put("/api/hunter-allowance")
def update_daily_allowance(req: DailyAllowanceRequest):
    """Set how many additional swaps the Hunter may execute today (0–25)."""
    try:
        state = set_daily_swaps_remaining(req.swaps_remaining)
        return {"status": "success", "message": "Daily allowance updated", **state}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not update daily allowance: {e}")


@app.post("/api/manual-intervention/clear")
def clear_manual_intervention(req: ManualInterventionClearRequest):
    """Clear a persistent critical-execution lock only after owner acknowledgement."""
    if req.acknowledged is not True:
        raise HTTPException(status_code=400, detail="Explicit owner acknowledgement is required")
    try:
        result = clear_manual_intervention_lock(req.resolution_note.strip())
        if not result.get("cleared", False):
            raise HTTPException(status_code=409, detail="No active manual-intervention lock was cleared")
        return {"status": "success", **result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not clear manual-intervention lock: {e}")


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

        signal_hunter_rules_changed()

        return {"status": "success", "message": "Rule saved"}
    finally:
        connection.close()

@app.delete("/api/hunter-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_hunter_rule(rule_id: int):
    try:
        deleted = delete_hunter_rule(rule_id)

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="Rule not found"
            )

        signal_hunter_rules_changed()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(status_code=status.HTTP_204_NO_CONTENT)

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
    """Return the latest BTC and CC USD prices saved by the market monitor."""
    prices_file = Path(__file__).resolve().parent / "prices.json"
    prices = {}

    if prices_file.exists():
        try:
            with prices_file.open("r", encoding="utf-8") as f:
                prices = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[API WARNING] Could not read market prices: {exc}")

    def safe_price(key):
        try:
            value = float(prices.get(key, 0) or 0)
            return value if value > 0 else 0
        except (TypeError, ValueError):
            return 0

    return {
        "cc_usd": safe_price("CC-USDC"),
        "btc_usd": safe_price("BTC-USDC"),
        "status": "success",
    }


@app.get("/api/hunter-daily-stats")
def get_daily_stats():
    connection = get_connection()
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT sell_amount, network_fee, pool_fee, usd_value
                FROM rebate_swaps
                WHERE created_at >= (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                  AND created_at < ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC')
                """
            )
            swaps = cur.fetchall()

            total_net_fee = 0.0
            total_pool_fee_cc = 0.0
            swap_count = len(swaps)

            for s in swaps:
                total_net_fee += float(s["network_fee"])
                total_pool_fee_cc += float(s["sell_amount"]) * (float(s["pool_fee"]) / 100)

            estimated_rebate_cc = 23.4 * (swap_count / 25.0)
            net_profit_cc = estimated_rebate_cc - total_net_fee - total_pool_fee_cc

            return {
                "status": "success",
                "swap_count": swap_count,
                "total_net_fee": total_net_fee,
                "total_pool_fee_cc": total_pool_fee_cc,
                "estimated_rebate_cc": estimated_rebate_cc,
                "net_profit_cc": net_profit_cc,
            }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
    finally:
        connection.close()

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