"""Multi-user FastAPI endpoints for the Cantex Rebate Hunter.

This API scopes account data to the authenticated user. Public market data
remains shared. Trading execution and per-user SDK signing stay in the
Hunter runtime; this module does not submit swaps.

This API requires the multi-user schema cutover to be complete. Endpoints
that touch per-user state will fail if called before the cutover SQL has run.
The server entry point (run_server.py) owns init_db() schema validation.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, SecretStr

from config import PUBLIC_BASE
from postgres_database import (
    create_user,
    get_user_auth_record,
    get_user_by_id,
    get_user_hunter_control_state,
    apply_user_manual_swap_count,
    clear_user_manual_intervention_lock,
    get_user_hunter_rules,
    create_user_hunter_rule,
    delete_user_hunter_rule,
    get_user_rebate_history,
    get_user_today_swap_count,
    save_user_credentials,
    get_user_credentials_ciphertext,
    delete_user_credentials,
)
from crypto import encrypt_user_credentials
from portfolio_cache import get as get_cached_portfolio, set as set_cached_portfolio, invalidate as invalidate_portfolio_cache


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOKEN_TTL_SECONDS = 60 * 60 * 24
MAX_RULES_PER_USER = 2
DAILY_SWAP_LIMIT = 25
TOKEN_CACHE_SECONDS = 600
AUTH_SECRET = os.getenv("APP_AUTH_SECRET", "")
_password_hasher = PasswordHasher()
_all_tokens_cache = {"tokens": [], "timestamp": 0}
RULE_CHANGE_FILE = Path(__file__).resolve().parent / "hunter_rules_changed.flag"


def _auth_secret() -> bytes:
    if len(AUTH_SECRET) < 32:
        raise RuntimeError(
            "APP_AUTH_SECRET must be configured with at least 32 characters."
        )
    return AUTH_SECRET.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    """Decode URL-safe base64 strictly, including token signature fields."""
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid base64 value")
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)


def _issue_token(user_id: str) -> str:
    payload = {
        "sub": str(user_id),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_auth_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def _read_token(token: str) -> str:
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError("Invalid token length")
        body, supplied_signature = token.split(".", 1)
        expected = hmac.new(
            _auth_secret(), body.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(supplied_signature)):
            raise ValueError("Invalid token signature")
        payload = json.loads(_b64decode(body))
        if not isinstance(payload, dict) or not isinstance(payload.get("sub"), str):
            raise ValueError("Invalid token payload")
        if isinstance(payload.get("exp"), bool) or not isinstance(payload.get("exp"), int):
            raise ValueError("Invalid token expiry")
        if payload["exp"] <= int(time.time()):
            raise ValueError("Token expired")
        user_id = str(payload["sub"]).strip()
        if not user_id:
            raise ValueError("Missing user")
        return user_id
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired login token")


def current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Login required")
    user_id = _read_token(authorization[7:].strip())
    user = get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="Account is unavailable")
    return user


def signal_hunter_rules_changed():
    """Notify the shared monitor that its required market subscriptions changed."""
    try:
        temporary_file = RULE_CHANGE_FILE.with_suffix(".tmp")
        temporary_file.write_text(str(time.time_ns()), encoding="utf-8")
        os.replace(temporary_file, RULE_CHANGE_FILE)
    except Exception as exc:
        # The rule has already been saved. The monitor's fallback refresh remains.
        print(f"[API WARNING] Could not signal Hunter Rules change: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # run_server.py attaches the process-wide SlotManager before Uvicorn
    # starts. Fail closed if this API is launched without that manager.
    slot_manager = getattr(app.state, "slot_manager", None)
    if slot_manager is None:
        raise RuntimeError(
            "SlotManager is not attached. Start the multi-user service via run_server.py."
        )

    from cantex_api import create_user_runtime
    slot_manager.set_runtime_factory(create_user_runtime)
    yield


app = FastAPI(
    title="Cantex Rebate Hunter API",
    version="3.0.0",
    description="Multi-user API. Swap execution is handled by the Hunter runtime.",
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


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=256)


class CantexCredentialsRequest(BaseModel):
    """Private Cantex signing credentials supplied over HTTPS."""
    operator_key: SecretStr = Field(min_length=1)
    trading_key: SecretStr = Field(min_length=1)


class HunterRuleCreate(BaseModel):
    token_a: str = Field(min_length=1, max_length=64)
    token_b: str = Field(min_length=1, max_length=64)
    net_min: str
    net_max: str


class ManualSwapCountRequest(BaseModel):
    # Successful swaps completed manually in Cantex during the current UTC day.
    manual_swaps_today: int = Field(ge=0, le=DAILY_SWAP_LIMIT)


class ManualInterventionClearRequest(BaseModel):
    acknowledged: bool
    resolution_note: str = Field(min_length=5, max_length=1000)


# ---------------------------------------------------------------------------
# Public and authentication endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest):
    username = req.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must contain at least 3 characters")
    try:
        password_hash = _password_hasher.hash(req.password)
        user = create_user(username, password_hash)
        if not user:
            raise HTTPException(status_code=409, detail="Username is already in use")
        token = _issue_token(str(user["id"]))
        return {
            "status": "success",
            "token": token,
            "token_type": "bearer",
            "expires_in": TOKEN_TTL_SECONDS,
            "user": {"id": str(user["id"]), "username": user["username"]},
        }
    except HTTPException:
        raise
    except Exception as exc:
        print(
            f"[REGISTER ERROR] {type(exc).__name__}",
            flush=True
        )
        raise HTTPException(
            status_code=500,
            detail="Could not create account"
        ) from None


@app.post("/api/login")
def login(req: LoginRequest):
    record = get_user_auth_record(req.username.strip())
    if not record or not record.get("is_active"):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    try:
        _password_hasher.verify(record["password_hash"], req.password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = _issue_token(str(record["id"]))
    return {
        "status": "success",
        "token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "user": {"id": str(record["id"]), "username": record["username"]},
    }


@app.get("/api/me")
def get_me(user=Depends(current_user)):
    return {"id": str(user["id"]), "username": user["username"]}


# ---------------------------------------------------------------------------
# Per-user Hunter control
# ---------------------------------------------------------------------------

@app.get("/api/hunter-control")
def get_hunter_control(user=Depends(current_user)):
    try:
        state = get_user_hunter_control_state(user["id"])
        state["manual_lock"] = bool(state.get("manual_intervention_required", False))
        return state
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hunter control state: {exc}")


@app.put("/api/hunter-allowance")
def update_daily_allowance(req: ManualSwapCountRequest, user=Depends(current_user)):
    """Apply manually completed swaps to this user's remaining daily allowance."""
    try:
        state = apply_user_manual_swap_count(
            user["id"], req.manual_swaps_today
        )
        return {
            "status": "success",
            "message": "Manual swaps recorded; Hunter allowance recalculated.",
            **state,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not update the daily Hunter allowance.",
        )


@app.post("/api/manual-intervention/clear")
def clear_manual_intervention(
    req: ManualInterventionClearRequest, user=Depends(current_user)
):
    if req.acknowledged is not True:
        raise HTTPException(status_code=400, detail="Explicit owner acknowledgement is required")
    try:
        result = clear_user_manual_intervention_lock(
            user["id"], req.resolution_note.strip()
        )
        if not result.get("cleared", False):
            raise HTTPException(
                status_code=409,
                detail="No active manual-intervention lock was cleared",
            )
        return {"status": "success", **result}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not clear manual-intervention lock: {exc}")


# ---------------------------------------------------------------------------
# Per-user rules and history
# ---------------------------------------------------------------------------

@app.get("/api/hunter-rules")
def get_hunter_rules(user=Depends(current_user)):
    try:
        return get_user_hunter_rules(user["id"], active_only=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hunter Rules: {exc}")


@app.post("/api/hunter-rules")
def save_hunter_rule(rule: HunterRuleCreate, user=Depends(current_user)):
    token_a = rule.token_a.strip().upper()
    token_b = rule.token_b.strip().upper()
    if token_a == token_b:
        raise HTTPException(status_code=400, detail="The two tokens must be different")

    try:
        existing = get_user_hunter_rules(user["id"], active_only=True)
        same_pair_exists = any(
            row["token_a"].upper() == token_a and row["token_b"].upper() == token_b
            for row in existing
        )
        if not same_pair_exists and len(existing) >= MAX_RULES_PER_USER:
            raise HTTPException(
                status_code=400,
                detail=f"You can have a maximum of {MAX_RULES_PER_USER} active Hunter Rules",
            )

        saved = create_user_hunter_rule(
            user["id"], token_a, token_b, rule.net_min, rule.net_max, True
        )
        signal_hunter_rules_changed()
        return {"status": "success", "message": "Rule saved", "rule": saved}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save Hunter Rule: {exc}")


@app.delete("/api/hunter-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_hunter_rule(rule_id: int, user=Depends(current_user)):
    try:
        deleted = delete_user_hunter_rule(user["id"], rule_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Rule not found")
        signal_hunter_rules_changed()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/hunter-history")
def get_hunter_history(
    limit: int = 20, offset: int = 0, user=Depends(current_user)
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    try:
        return get_user_rebate_history(user["id"], limit=limit, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hunter history: {exc}")


@app.get("/api/hunter-daily-stats")
def get_daily_stats(user=Depends(current_user)):
    try:
        # The history query is user-scoped. Only today's UTC records are included.
        rows = get_user_rebate_history(user["id"], limit=1000, offset=0)
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date()
        todays_rows = [
            row for row in rows
            if row.get("created_at") and row["created_at"].date() == today
        ]

        total_net_fee = sum(float(row.get("network_fee") or 0) for row in todays_rows)
        total_pool_fee_cc = sum(
            float(row.get("sell_amount") or 0) * (float(row.get("pool_fee") or 0) / 100)
            for row in todays_rows
        )
        swap_count = get_user_today_swap_count(user["id"])
        estimated_rebate_cc = 23.4 * (swap_count / 25.0)

        return {
            "status": "success",
            "swap_count": swap_count,
            "total_net_fee": total_net_fee,
            "total_pool_fee_cc": total_pool_fee_cc,
            "estimated_rebate_cc": estimated_rebate_cc,
            "net_profit_cc": estimated_rebate_cc - total_net_fee - total_pool_fee_cc,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate daily stats: {exc}")


# ---------------------------------------------------------------------------
# Shared public market data
# ---------------------------------------------------------------------------

@app.get("/api/market-prices")
def get_market_prices():
    """Return shared public BTC and CC USD prices written by the market monitor."""
    prices_file = Path(__file__).resolve().parent / "prices.json"
    prices = {}
    if prices_file.exists():
        try:
            prices = json.loads(prices_file.read_text(encoding="utf-8"))
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


@app.get("/api/all-tokens")
def get_all_tokens():
    now = time.time()
    if now - _all_tokens_cache["timestamp"] < TOKEN_CACHE_SECONDS:
        return _all_tokens_cache["tokens"]

    try:
        response = requests.get(f"{PUBLIC_BASE}/markets/info", timeout=10)
        response.raise_for_status()
        markets = response.json().get("data", {}).get("markets", [])
        tokens = set()

        for market in markets:
            symbol = market.get("symbol", "")
            if "-" not in symbol:
                continue
            base, _, quote = symbol.rpartition("-")
            if base:
                tokens.add(base.upper())
            if quote:
                tokens.add(quote.upper())

        tokens.difference_update({"CC", "USDC", "USDCX"})
        result = sorted(tokens)
        _all_tokens_cache.update(tokens=result, timestamp=now)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Per-user Cantex credential storage
# ---------------------------------------------------------------------------

@app.put("/api/credentials")
def put_cantex_credentials(
    req: CantexCredentialsRequest,
    user=Depends(current_user),
):
    """Encrypt and store only the authenticated user's Cantex credentials."""
    user_id = str(user["id"])
    operator_key = req.operator_key.get_secret_value().strip()
    trading_key = req.trading_key.get_secret_value().strip()

    if not operator_key or not trading_key:
        raise HTTPException(
            status_code=400,
            detail="Both Cantex credentials are required.",
        )

    try:
        encrypted_record = encrypt_user_credentials(
            user_id=user_id,
            operator_key=operator_key,
            trading_key=trading_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError:
        # Includes missing/invalid CANTEX_MASTER_KEY configuration.
        raise HTTPException(
            status_code=503,
            detail="Credential encryption is not configured correctly.",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not encrypt Cantex credentials.",
        ) from None

    try:
        saved = save_user_credentials(user_id=user_id, **encrypted_record)
        if not saved:
            raise RuntimeError("Credential record was not saved")
        invalidate_portfolio_cache(user_id)
        return {
            "status": "success",
            "configured": True,
            "message": "Cantex credentials securely stored.",
        }
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not securely store Cantex credentials.",
        ) from None
    finally:
        # Best-effort release of local references; Python strings cannot be
        # guaranteed to be zeroised in memory.
        del operator_key, trading_key


@app.get("/api/credentials")
def get_cantex_credential_status(user=Depends(current_user)):
    """Return configuration status only; never return ciphertext or keys."""
    try:
        record = get_user_credentials_ciphertext(str(user["id"]))
        return {"status": "success", "configured": record is not None}
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve Cantex credential status.",
        ) from None


@app.delete("/api/credentials")
def delete_cantex_credentials(user=Depends(current_user)):
    """Delete only the authenticated user's encrypted Cantex credentials."""
    user_id = str(user["id"])
    try:
        deleted = delete_user_credentials(user_id)
        invalidate_portfolio_cache(user_id)
        return {"status": "success", "deleted": bool(deleted)}
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not delete Cantex credentials.",
        ) from None


@app.get("/api/portfolio-value")
async def get_portfolio_value(user=Depends(current_user)):
    """Calculate the authenticated user's portfolio value from their own balances.

    Market prices are shared public data from prices.json. Account balances are
    always retrieved through the authenticated user's isolated Cantex client.
    Tokens without a usable USD conversion are reported as unpriced, not valued
    at zero.
    """
    account_data = await get_balances(user)
    balances = account_data.get("balances", [])

    prices_file = Path(__file__).resolve().parent / "prices.json"
    try:
        prices = json.loads(prices_file.read_text(encoding="utf-8")) if prices_file.exists() else {}
        if not isinstance(prices, dict):
            prices = {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[PORTFOLIO WARNING] Could not read market prices: {type(exc).__name__}")
        prices = {}

    def positive_decimal(value):
        try:
            parsed = Decimal(str(value))
            return parsed if parsed.is_finite() and parsed > 0 else None
        except (InvalidOperation, TypeError, ValueError):
            return None

    cc_usd = positive_decimal(prices.get("CC-USDC"))
    total_usd = Decimal("0")
    token_rows = []
    breakdown = {}
    unpriced_symbols = []

    for row in balances:
        symbol = str(row.get("symbol") or "").upper()
        amount = positive_decimal(row.get("available"))
        if not symbol or amount is None:
            continue

        usd_price = positive_decimal(prices.get(f"{symbol}-USDC"))
        if usd_price is None and symbol == "CC":
            usd_price = cc_usd
        elif usd_price is None:
            token_cc_price = positive_decimal(prices.get(f"{symbol}-CC"))
            if token_cc_price is not None and cc_usd is not None:
                usd_price = token_cc_price * cc_usd

        value_usd = amount * usd_price if usd_price is not None else None
        if value_usd is not None:
            total_usd += value_usd
            breakdown[symbol] = str(value_usd)
        else:
            unpriced_symbols.append(symbol)

        token_rows.append({
            "symbol": symbol,
            "available": str(row.get("available")),
            "locked": str(row.get("locked", "0")),
            "price_usd": str(usd_price) if usd_price is not None else None,
            "value_usd": str(value_usd) if value_usd is not None else None,
            "valuation_status": "priced" if value_usd is not None else "unpriced",
        })

    token_rows.sort(key=lambda item: (
        item["value_usd"] is None,
        -(Decimal(item["value_usd"]) if item["value_usd"] is not None else Decimal("0")),
        item["symbol"],
    ))

    return {
        "status": "success",
        "total_usd": str(total_usd),
        "valuation_complete": not unpriced_symbols,
        "unpriced_symbols": unpriced_symbols,
        "breakdown": breakdown,
        "tokens": token_rows,
        "updated_at": account_data.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Endpoints intentionally not exposed yet
# ---------------------------------------------------------------------------

@app.get("/api/balances")
async def get_balances(user=Depends(current_user)):
    """Read balances from the authenticated user's own Cantex account.

    A fresh, isolated SDK client is created for this request. No shared legacy
    credentials are used, and the client is always closed after the read.
    """
    user_id = str(user["id"])

    cached_result = get_cached_portfolio(user_id)
    if cached_result is not None:
        return cached_result

    # Fail clearly before attempting SDK authentication if this user has not
    # saved both Cantex signing keys.
    try:
        credential_record = await asyncio.to_thread(
            get_user_credentials_ciphertext, user_id
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not check this user's Cantex connection.",
        ) from None

    if not credential_record:
        raise HTTPException(
            status_code=409,
            detail="Cantex keys are not configured for this account.",
        )

    client = None
    try:
        from cantex_api import create_user_client

        client = await create_user_client(user_id)
        account_info = await client.get_account_info()

        # Cantex SDK/account response versions may expose the holdings under
        # tokens, instruments, or balances. Accept these known structures.
        holdings = getattr(account_info, "tokens", None)
        if holdings is None:
            holdings = getattr(account_info, "instruments", None)
        if holdings is None:
            holdings = getattr(account_info, "balances", None)

        if holdings is None and isinstance(account_info, dict):
            holdings = (
                account_info.get("tokens")
                or account_info.get("instruments")
                or account_info.get("balances")
            )

        if holdings is None:
            raise RuntimeError("Cantex account response did not include holdings")

        if isinstance(holdings, dict):
            items = []
            for key, value in holdings.items():
                if isinstance(value, (str, int, float)):
                    items.append({"symbol": key, "available": value})
                elif isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("symbol", key)
                    items.append(item)
                else:
                    items.append(value)
        else:
            items = holdings

        balances = []

        for item in items or []:
            if isinstance(item, dict):
                symbol = item.get("instrument_symbol") or item.get("symbol")
                available = item.get(
                    "unlocked_amount",
                    item.get("available", item.get("amount")),
                )
                locked = item.get("locked_amount")
            else:
                symbol = getattr(item, "instrument_symbol", None)
                available = getattr(
                    item,
                    "unlocked_amount",
                    getattr(item, "available", getattr(item, "amount", None)),
                )
                locked = getattr(item, "locked_amount", None)

            if not symbol:
                continue

            row = {
                "symbol": str(symbol).upper(),
                "available": str(available) if available is not None else None,
            }
            if locked is not None:
                row["locked"] = str(locked)
            balances.append(row)

        if not balances:
            raise RuntimeError("Cantex returned no recognised account holdings")

        balances.sort(key=lambda row: row["symbol"])
        cc_balance = next(
            (row["available"] for row in balances if row["symbol"] == "CC"),
            None,
        )

        result = {
            "status": "success",
            "connected": True,
            "balances": balances,
            "cc_balance": cc_balance,
            "updated_at": int(time.time()),
        }
        set_cached_portfolio(user_id, result)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        # Keep SDK internals, request details and credential-related data out
        # of the API response and logs.
        print(
            f"[CANTEX ACCOUNT ERROR] User {user_id[:8]}: {type(exc).__name__}",
            flush=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve account information from Cantex. Check the saved keys and try again.",
        ) from None
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception as exc:
                print(
                    f"[CANTEX CLIENT CLOSE WARNING] User {user_id[:8]}: "
                    f"{type(exc).__name__}",
                    flush=True,
                )


@app.get("/api/hunter-status")
def get_hunter_status(user=Depends(current_user)):
    """Return Hunter status for the authenticated user only."""
    slot_manager = getattr(app.state, "slot_manager", None)
    if slot_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Hunter scheduler is not available.",
        )

    user_id = str(user["id"])

    try:
        control_state = get_user_hunter_control_state(user_id)
        if not control_state:
            raise RuntimeError("Hunter control state is unavailable")

        remaining = int(control_state.get("swaps_remaining", DAILY_SWAP_LIMIT))
        remaining = max(0, min(DAILY_SWAP_LIMIT, remaining))
        manual_intervention_required = bool(
            control_state.get("manual_intervention_required", False)
        )

        active_slots = getattr(slot_manager, "active", {})
        waiting_users = getattr(slot_manager, "waiting", [])
        exhausted_users = getattr(slot_manager, "_daily_exhausted", set())

        slot = active_slots.get(user_id)
        is_waiting = user_id in waiting_users
        is_daily_exhausted = remaining <= 0 or user_id in exhausted_users

        runtime = slot.runtime if slot is not None else None
        if manual_intervention_required or (
            runtime is not None and bool(getattr(runtime, "swap_halted", False))
        ):
            hunter_state = "manual_intervention_required"
        elif is_daily_exhausted:
            hunter_state = "daily_limit_reached"
        elif slot is not None:
            hunter_state = "running"
        elif is_waiting:
            hunter_state = "waiting"
        else:
            hunter_state = "stopped"

        latest_scan = None
        safe_user_id = user_id.replace("-", "")
        status_path = (
            Path(__file__).resolve().parent
            / f"hunter_status_{safe_user_id}.json"
        )
        if status_path.exists():
            try:
                latest_scan = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(
                    f"[API WARNING] Could not read Hunter status for user "
                    f"{safe_user_id[:8]}: {exc}"
                )

        swaps_done = DAILY_SWAP_LIMIT - remaining
        if runtime is not None:
            swaps_done = max(
                swaps_done,
                min(DAILY_SWAP_LIMIT, int(getattr(runtime, "swaps_done", 0))),
            )

        return {
            "status": "success",
            "hunter_state": hunter_state,
            "is_running": hunter_state == "running",
            "is_waiting": hunter_state == "waiting",
            "swaps_done": swaps_done,
            "swaps_remaining": remaining,
            "daily_limit": DAILY_SWAP_LIMIT,
            "manual_intervention_required": manual_intervention_required,
            "pending_pairs": (
                len(getattr(runtime, "pending_pairs", set()))
                if runtime is not None else 0
            ),
            "latest_scan": latest_scan,
        }
    except HTTPException:
        raise
    except Exception as exc:
        print(
            f"[HUNTER STATUS ERROR] User {user_id[:8]}: "
            f"{type(exc).__name__}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve this user's Hunter status.",
        ) from None


# ---------------------------------------------------------------------------
# Hunter slot lifecycle
# ---------------------------------------------------------------------------

def _get_slot_manager():
    manager = getattr(app.state, "slot_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Hunter scheduler is not available.",
        )
    return manager


@app.post("/api/slot/request")
async def request_hunter_slot(user=Depends(current_user)):
    """Start this user's Hunter or place them in the FIFO waiting queue."""
    manager = _get_slot_manager()
    user_id = str(user["id"])

    try:
        # This read also applies the database's normal daily rollover.
        control_state = await asyncio.to_thread(
            get_user_hunter_control_state, user_id
        )
        remaining = int(control_state.get("swaps_remaining", DAILY_SWAP_LIMIT))

        if remaining <= 0:
            # Keep the in-memory marker aligned with the database allowance.
            exhausted = getattr(manager, "_daily_exhausted", None)
            if exhausted is not None:
                exhausted.add(user_id)
            raise HTTPException(
                status_code=409,
                detail="Today's successful-swap allowance has been used. Try again after the daily reset.",
            )

        # If the database has rolled into a new day, clear yesterday's
        # in-memory exhaustion marker before allowing a fresh request.
        if user_id in getattr(manager, "_daily_exhausted", set()):
            clear_exhaustion = getattr(manager, "clear_daily_exhaustion", None)
            if clear_exhaustion is not None:
                await clear_exhaustion(user_id)

        result = await manager.request_slot(user_id)
        if result in ("started", "active"):
            state = "active"
            message = "Hunter is active."
        elif result == "waiting":
            state = "waiting"
            message = "All Hunter slots are occupied. You have been queued."
        elif result == "daily_exhausted":
            state = "daily_limit_reached"
            message = "Today's successful-swap allowance has been used."
        else:
            raise RuntimeError(f"Unexpected slot manager result: {result!r}")

        return {
            "status": result,
            "slot_status": state,
            "message": message,
            "capacity": {
                "max_slots": int(manager.max_slots),
                "active_count": len(manager.active),
                "waiting_count": len(manager.waiting),
            },
        }
    except HTTPException:
        raise
    except RuntimeError as exc:
        # A missing runtime factory / shutting-down scheduler is a service issue.
        print(
            f"[SLOT REQUEST ERROR] User {user_id[:8]}: {type(exc).__name__}",
            flush=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Hunter could not be started. Check that the scheduler and account setup are ready.",
        ) from None
    except Exception as exc:
        print(
            f"[SLOT REQUEST ERROR] User {user_id[:8]}: {type(exc).__name__}",
            flush=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not initialise this user's Cantex Hunter runtime.",
        ) from None


@app.post("/api/slot/release")
async def release_hunter_slot(user=Depends(current_user)):
    """Stop or dequeue only the authenticated user's Hunter."""
    manager = _get_slot_manager()
    user_id = str(user["id"])

    try:
        released = await manager.release_slot(user_id)
        return {
            "status": "released" if released else "not_active",
            "released": bool(released),
            "slot_status": "stopped",
            "message": (
                "Hunter stopped and its runtime released."
                if released
                else "This user has no active or queued Hunter."
            ),
            "capacity": {
                "max_slots": int(manager.max_slots),
                "active_count": len(manager.active),
                "waiting_count": len(manager.waiting),
            },
        }
    except Exception as exc:
        print(
            f"[SLOT RELEASE ERROR] User {user_id[:8]}: {type(exc).__name__}",
            flush=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Could not release this user's Hunter slot.",
        ) from None


@app.get("/api/slot/status")
def get_hunter_slot_status(user=Depends(current_user)):
    """Return slot state for this user without exposing other users' IDs."""
    manager = _get_slot_manager()
    user_id = str(user["id"])

    active = user_id in manager.active
    waiting_users = manager.waiting
    waiting = user_id in waiting_users
    exhausted = user_id in getattr(manager, "_daily_exhausted", set())

    if active:
        slot_status = "active"
    elif waiting:
        slot_status = "waiting"
    elif exhausted:
        slot_status = "daily_limit_reached"
    else:
        slot_status = "stopped"

    queue_position = (
        waiting_users.index(user_id) + 1 if waiting else None
    )

    return {
        "status": "success",
        "slot_status": slot_status,
        "is_active": active,
        "is_waiting": waiting,
        "queue_position": queue_position,
        "daily_limit_reached": exhausted,
        "capacity": {
            "max_slots": int(manager.max_slots),
            "active_count": len(manager.active),
            "waiting_count": len(waiting_users),
        },
    }



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
