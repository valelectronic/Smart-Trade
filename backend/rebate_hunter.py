import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path
from cantex_api import connect_private_ws
from portfolio_cache import invalidate as invalidate_portfolio_cache
from helpers import get_cc_balance
from postgres_database import (
    get_user_hunter_control_state,
    activate_user_manual_intervention_lock,
    log_user_successful_swap,
    create_user_pending_swap,
    update_user_swap_transaction_status,
    get_user_unresolved_swaps,
    log_user_execution_event,
    get_user_active_hunter_rules,
    get_connection,
)
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Any
from telegram_alerts import send_daily_allowance_exhausted, send_transaction_needs_review

@dataclass
class UserRuntime:
    """Isolated in-memory Hunter state for one registered user.

    The scheduler supplies a client created from this user's securely loaded
    credentials. This module never falls back to shared environment credentials.
    """
    user_id: str
    # Must be created by the caller using this user's decrypted credentials.
    # run_user() does not create a client from shared environment variables.
    client: Any
    instruments: dict = field(default_factory=dict)
    swaps_done: int = 0
    swap_halted: bool = False
    last_swap_direction: dict = field(default_factory=dict)
    disabled_pairs: dict = field(default_factory=dict)
    pending_pairs: set = field(default_factory=set)
    net_cc_delta: dict = field(default_factory=dict)
    private_ws_task: Any = None
    daily_allowance_alerted: bool = False
    review_alerted_tx_ids: set = field(default_factory=set)
    review_alerted_without_tx: bool = False


# One global lock coordinates actual submissions across all user runtimes.
execution_lock = asyncio.Lock()

# Serialise quote requests across user runtimes to avoid simultaneous bursts.
# This limits concurrency; it does not add an artificial delay between requests.
quote_semaphore = asyncio.Semaphore(1)


async def _get_swap_quote(client, *, sell_amount, sell_instrument, buy_instrument):
    async with quote_semaphore:
        return await client.get_swap_quote(
            sell_amount=sell_amount,
            sell_instrument=sell_instrument,
            buy_instrument=buy_instrument,
        )


def _user_tag(runtime: UserRuntime) -> str:
    return f"[user={str(runtime.user_id)[:8]}]"


def canonical_pair_key(token_a: str, token_b: str) -> str:
    a, b = sorted([token_a.upper(), token_b.upper()])
    return f"{a}::{b}"


def restore_ping_pong_memory(user_id, token_pairs, runtime):
    """Restore the latest swap direction for each active pair using Hunter Rule order."""
    rule_orders = {}
    for pair in token_pairs:
        token_a = str(pair["a"]).upper()
        token_b = str(pair["b"]).upper()
        rule_orders[canonical_pair_key(token_a, token_b)] = (token_a, token_b)

    if not rule_orders:
        return

    restored = {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sell_token, buy_token "
                "FROM rebate_swaps WHERE user_id = %s ORDER BY created_at DESC",
                (str(user_id),)
            )
            for row in cur:
                sell_token = str(row["sell_token"]).upper()
                buy_token = str(row["buy_token"]).upper()
                pair_key = canonical_pair_key(sell_token, buy_token)

                if pair_key not in rule_orders or pair_key in restored:
                    continue

                token_a, token_b = rule_orders[pair_key]
                if sell_token == token_a and buy_token == token_b:
                    restored[pair_key] = "A_TO_B"
                elif sell_token == token_b and buy_token == token_a:
                    restored[pair_key] = "B_TO_A"

                if len(restored) == len(rule_orders):
                    break
    finally:
        conn.close()

    for pair_key in rule_orders:
        runtime.last_swap_direction.pop(pair_key, None)
    runtime.last_swap_direction.update(restored)
    for pair_key, direction in restored.items():
        print(f" Restored ping-pong memory for {user_id}: {pair_key} = {direction}")

    missing = set(rule_orders) - set(restored)
    if missing:
        print(f" No previous swap history found for {len(missing)} active pair(s).")

# --- CONFIGURATION ---
TARGET_SWAP_SIZE_CC = Decimal("101.5")
MIN_SWAP_SIZE_CC = Decimal("100.0")
MAX_SWAP_SIZE_CC = Decimal("102.0")
POOL_MIN = Decimal("0.10")
POOL_MAX = Decimal("0.17")
DEFAULT_DAILY_LIMIT = 25
POST_SWAP_COOLDOWN = 20
RED_LIGHT_WAIT = 10      
MIN_CC_BALANCE = Decimal("5.0") 

PRICES_FILE = Path(__file__).parent / "prices.json"
STATUS_FILE = Path(__file__).parent / "hunter_status.json"

DISABLED_PAIR_COOLDOWN = 360

def _allowance_remaining(user_id):
    state = get_user_hunter_control_state(user_id)
    if not state:
        raise RuntimeError("Hunter control state is unavailable")
    daily_limit = int(
        state.get("daily_swap_limit", state.get("daily_limit", DEFAULT_DAILY_LIMIT))
    )
    remaining = int(state.get("swaps_remaining", daily_limit))
    return max(0, min(daily_limit, remaining)), state


def _is_explicit_disabled_swap_rejection(error: Exception) -> bool:
    """Return True only for a deterministic Cantex rejection, not an uncertain submission."""
    message = str(error).lower()
    return (
        "token swapping is currently disabled" in message
        or "pool is disabled" in message
        or "market is not active" in message
    )


async def _activate_manual_lock(runtime: UserRuntime, reason: str, tx_id=None):
    runtime.swap_halted = True
    try:
        activate_user_manual_intervention_lock(runtime.user_id, reason=reason, tx_id=tx_id)
        print(f"[MANUAL INTERVENTION LOCK] {reason}")
    except Exception as db_err:
        print(f"[CRITICAL] Could not persist manual lock: {db_err}")

    # Notify once for this activation. Existing review-alert deduplication
    # prevents duplicate messages when the same transaction also needs review.
    await _notify_transaction_review(runtime, tx_id=tx_id, reason=reason)


async def _notify_transaction_review(runtime: UserRuntime, tx_id=None, reason=None):
    key = str(tx_id) if tx_id else None
    if key:
        if key in runtime.review_alerted_tx_ids:
            return
        runtime.review_alerted_tx_ids.add(key)
    elif runtime.review_alerted_without_tx:
        return
    else:
        runtime.review_alerted_without_tx = True
    await send_transaction_needs_review(runtime.user_id, tx_id=key, reason=reason)


async def _notify_daily_allowance_exhausted(runtime: UserRuntime):
    if runtime.daily_allowance_alerted:
        return
    runtime.daily_allowance_alerted = True
    try:
        _, state = _allowance_remaining(runtime.user_id)
        daily_limit = int(
            state.get("daily_swap_limit", state.get("daily_limit", DEFAULT_DAILY_LIMIT))
        )
    except Exception as exc:
        print(f"[ALLOWANCE NOTIFICATION WARNING] Could not read current daily target: {exc}")
        daily_limit = DEFAULT_DAILY_LIMIT
    await send_daily_allowance_exhausted(runtime.user_id, daily_limit)


def extract_quote_fees(quote):
    """Return validated (network_fee, pool_fee_percent), or None for unusable fee data."""
    fees_obj = getattr(quote, "fees", None)
    if fees_obj is None:
        return None

    network_fee_obj = getattr(fees_obj, "network_fee", None)
    network_fee_raw = getattr(network_fee_obj, "amount", None) if network_fee_obj is not None else None
    pool_fee_raw = getattr(fees_obj, "fee_percentage", None)
    if network_fee_raw is None or pool_fee_raw is None:
        return None

    try:
        network_fee = Decimal(str(network_fee_raw))
        pool_fee_percent = Decimal(str(pool_fee_raw)) * 100
    except Exception:
        return None

    if (not network_fee.is_finite() or not pool_fee_percent.is_finite()
            or network_fee < 0 or pool_fee_percent < 0):
        return None

    return network_fee, pool_fee_percent


def save_status(status_dict, runtime: UserRuntime | None = None):
    try:
        status_path = STATUS_FILE
        if runtime is not None:
            safe_user_id = str(runtime.user_id).replace("-", "")
            status_path = STATUS_FILE.with_name(f"hunter_status_{safe_user_id}.json")
        with status_path.open("w") as f:
            json.dump(status_dict, f, indent=2)
    except Exception as e:
        print(f"[STATUS SAVE ERROR] {e}")

def get_prices_in_cc_and_usd(token_symbol):
    if not PRICES_FILE.exists():
        return None, None
    try:
        with PRICES_FILE.open("r") as f:
            prices = json.load(f)
        token_cc_raw = prices.get(f"{token_symbol}-CC")
        cc_usd_raw = prices.get("CC-USDC")

        if token_cc_raw is None or cc_usd_raw is None:
            return None, None
        
        token_cc_price = Decimal(str(token_cc_raw))
        cc_usd_price = Decimal(str(cc_usd_raw))
        if token_cc_price <= 0 or cc_usd_price <= 0:
            return None, None
        token_usd_price = token_cc_price * cc_usd_price
        return token_cc_price, token_usd_price
    except Exception:
        return None, None


def _event_raw(event):
    raw = getattr(event, "raw", None)
    if isinstance(raw, dict):
        return raw
    return {}


async def _private_event_reader(ws, runtime: UserRuntime):
    """Log private Cantex events under this user's identity."""
    try:
        async for event in ws:
            raw = _event_raw(event)
            event_type = raw.get("type") or getattr(event, "event_type", None)
            if event_type in ("Pool.SwapExecuted", "Pool.SwapFailed"):
                try:
                    log_user_execution_event(runtime.user_id, raw)
                except Exception as exc:
                    print(f"[EVENT LOG WARNING] Could not store {event_type}: {exc}")
                if event_type == "Pool.SwapFailed":
                    print(f"[CANTEX SWAP FAILED EVENT] {raw}")
    except Exception as e:
        print(f"[WS READER DISCONNECTED] {e}")

async def check_and_swap(runtime: UserRuntime, client, instruments, sell_token, buy_token, net_min, net_max, sell_amount_tokens, pair_key=None, direction=None):
    if runtime.swap_halted:
        return "HALTED_MANUAL_INTERVENTION"
    try:
        remaining, control_state = _allowance_remaining(runtime.user_id)
        if control_state.get("manual_intervention_required"):
            runtime.swap_halted = True
            print("[MANUAL INTERVENTION LOCK] Owner review is required.")
            return "HALTED_MANUAL_INTERVENTION"
        if remaining <= 0:
            return False
    except Exception as control_err:
        print(f"[CONTROL STATE ERROR] Failing closed for this attempt: {control_err}")
        return "HALTED_CONTROL_STATE"

    try:
        account = await client.get_account_info()
    except Exception as e:
        print(f" Could not fetch account info: {e}")
        return False
    cc_balance = get_cc_balance(account)
    if cc_balance is None:
        print("[BALANCE ERROR] Could not determine CC balance; skipping this attempt.")
        return False
    if cc_balance < MIN_CC_BALANCE:
        await _activate_manual_lock(
            runtime,
            f"CC reserve is below the required {MIN_CC_BALANCE} CC (current: {cc_balance} CC)."
        )
        return "HALTED_MANUAL_INTERVENTION"

    cc_price, usd_price = get_prices_in_cc_and_usd(sell_token)
    if not cc_price or cc_price <= 0:
        return False

    total_usd_value = sell_amount_tokens * usd_price

    current_balance = Decimal("0")
    for item in getattr(account, "tokens", []) or []:
        symbol = getattr(item, "instrument_symbol", None)
        unlocked = getattr(item, "unlocked_amount", None)
        if symbol and str(symbol).upper() == sell_token:
            current_balance = Decimal(str(unlocked))
            break

    if current_balance < sell_amount_tokens:
        print(f" Not enough {sell_token}. Have: {current_balance}, Need: {sell_amount_tokens}. Skipping.")
        return False

    try:
        quote = await _get_swap_quote(
            client,
            sell_amount=sell_amount_tokens,
            sell_instrument=instruments[sell_token],
            buy_instrument=instruments[buy_token],
        )
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "Too Many Requests" in error_str or "rate limit" in error_str.lower():
            print(f" RATE LIMIT HIT. Cooling down for 30 seconds...")
            await asyncio.sleep(30) 
            return False
        else:
            print(f" Quote failed for {sell_token}->{buy_token}: {e}")
            return False

    fee_values = extract_quote_fees(quote)
    if fee_values is None:
        print(f" Quote for {sell_token}->{buy_token} returned missing or invalid fee data. Skipping execution.")
        return False
    network_fee, pool_fee_percent = fee_values
    sdk_est_time = 0.0
        
    exec_sec = getattr(quote, "estimated_time_seconds", None)
    if exec_sec is not None:
        sdk_est_time = float(exec_sec)

    network_ok = net_min <= network_fee <= net_max
    pool_ok = POOL_MIN <= pool_fee_percent <= POOL_MAX

    save_status({
        "time": time.strftime("%H:%M:%S"),
        "pair": f"{sell_token}->{buy_token}",
        "net_fee": float(network_fee),
        "pool_fee": float(pool_fee_percent),
        "usd_value": float(total_usd_value),
        "cc_size": float(sell_amount_tokens * cc_price),
        "sdk_est_time": sdk_est_time,
        "status": " MET" if (network_ok and pool_ok) else " WAITING",
        "swaps_done": runtime.swaps_done
    }, runtime)

    if network_ok and pool_ok:
        async with execution_lock:
            print(f" CONDITIONS MET! Locking execution queue...")
            print(f" Details: {sell_amount_tokens} {sell_token} (~${total_usd_value:.2f} USD, {(sell_amount_tokens * cc_price):.2f} CC)")
            
            start_time = time.perf_counter()
            try:
                account_before = await client.get_account_info()
                balance_snapshot = {
                    str(getattr(item, "instrument_symbol", "")).upper():
                        str(getattr(item, "unlocked_amount", "0"))
                    for item in (getattr(account_before, "tokens", []) or [])
                    if getattr(item, "instrument_symbol", None)
                }
                wallet_address = getattr(account_before, "address", None)
                result = await client.swap(
                    sell_amount=sell_amount_tokens,
                    sell_instrument=instruments[sell_token],
                    buy_instrument=instruments[buy_token],
                )

                tx_id = result.get("id") if isinstance(result, dict) else None
                verify_flag = result.get("verify") if isinstance(result, dict) else None
                if not tx_id:
                    print("[SUBMISSION UNCERTAIN] Cantex returned no submission ID; this pair will be held for review.")
                    runtime.pending_pairs.add(pair_key or canonical_pair_key(sell_token, buy_token))
                    await _notify_transaction_review(
                        runtime, reason="Cantex returned no submission ID; outcome is uncertain."
                    )
                    return "PENDING_PAIR"

                pair_id = pair_key or canonical_pair_key(sell_token, buy_token)

                if verify_flag is not True:
                    print(f"[SUBMISSION PENDING] tx_id={tx_id} verify={verify_flag!r}; pair {pair_id} blocked, other pairs remain active.")
                    runtime.pending_pairs.add(pair_id)
                    try:
                        create_user_pending_swap(
                            runtime.user_id,
                            tx_id=tx_id,
                            sell_token=sell_token,
                            buy_token=buy_token,
                            sell_amount=str(sell_amount_tokens),
                            submission_response=result,
                            wallet_address=wallet_address,
                            balance_snapshot=balance_snapshot,
                        )
                    except Exception as db_err:
                        print(f"[DB PENDING LOG WARNING] Could not create pending record: {db_err}")
                    await _notify_transaction_review(
                        runtime, tx_id=tx_id,
                        reason=f"Submission verification was not True (verify={verify_flag!r})."
                    )
                    return "PENDING_PAIR"

                # verify=True is the approved operational signal.
                end_time = time.perf_counter()
                print(f"[SUBMISSION VERIFIED] tx_id={tx_id}; acknowledged in {end_time - start_time:.2f}s.")
                
                # Try to log the pending record, but don't crash if it fails.
                try:
                    create_user_pending_swap(
                        runtime.user_id,
                        tx_id=tx_id,
                        sell_token=sell_token,
                        buy_token=buy_token,
                        sell_amount=str(sell_amount_tokens),
                        submission_response=result,
                        wallet_address=wallet_address,
                        balance_snapshot=balance_snapshot,
                    )
                except Exception as db_err:
                    print(f"[DB PENDING LOG WARNING] Could not create pending record: {db_err} (Proceeding with success flow).")

                logged = False
                db_error = None
                for attempt in range(3):
                    try:
                        logged = log_user_successful_swap(
                            runtime.user_id,
                            tx_id=tx_id,
                            pair=f"{sell_token}->{buy_token}",
                            sell_token=sell_token,
                            buy_token=buy_token,
                            sell_amount=float(sell_amount_tokens),
                            net_fee=float(network_fee),
                            pool_fee=float(pool_fee_percent),
                            usd_value=float(total_usd_value),
                        )
                        db_error = None
                        break
                    except Exception as err:
                        db_error = err
                        print(f"[DB LOG RETRY {attempt + 1}/3] {err}")
                        if attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))

                if db_error is not None:
                    await _activate_manual_lock(
                        runtime,
                        f"Cantex confirmed transaction {tx_id}, but DB logging failed after retries: {db_error}",
                        tx_id=tx_id
                    )
                    await _notify_transaction_review(
                        runtime, tx_id=tx_id,
                        reason="Cantex verified the swap, but database logging failed after retries."
                    )
                    return "HALTED_MANUAL_INTERVENTION"

                try:
                    update_user_swap_transaction_status(runtime.user_id, tx_id, "SUCCESSFUL")
                except Exception as db_err:
                    print(f"[DB STATUS WARNING] Could not update transaction status to SUCCESSFUL: {db_err}")

                runtime.pending_pairs.discard(pair_id)

                if logged:
                    runtime.swaps_done += 1
                    invalidate_portfolio_cache(runtime.user_id)
                    print(f" Confirmed swap recorded. Session successful swaps: {runtime.swaps_done}")
                else:
                    print(f" Transaction {tx_id} already exists in DB; duplicate count prevented.")

                if pair_key and direction:
                    runtime.last_swap_direction[pair_key] = direction
                else:
                    runtime.last_swap_direction[canonical_pair_key(sell_token, buy_token)] = "A_TO_B"

                runtime.net_cc_delta[sell_token] = runtime.net_cc_delta.get(sell_token, Decimal("0")) - (sell_amount_tokens * cc_price)
                runtime.net_cc_delta[buy_token] = runtime.net_cc_delta.get(buy_token, Decimal("0")) + (sell_amount_tokens * cc_price)

                await asyncio.sleep(POST_SWAP_COOLDOWN)
                try:
                    account_after = await client.get_account_info()
                    print("[BALANCE REFRESH] Account snapshot refreshed after confirmed swap.")
                except Exception as refresh_err:
                    print(
                        f"[BALANCE REFRESH DELAYED] Swap {tx_id} is confirmed and logged; "
                        f"account snapshot will be retried on the next scan: {refresh_err}"
                    )

                return True
            except Exception as e:
                pair_id = pair_key or canonical_pair_key(sell_token, buy_token)

                # Cantex's explicit disabled-token/pool response is a deterministic
                # rejection. It is NOT an uncertain submission and must not create
                # a pending transaction or trigger manual review. Hold only this
                # pair for the agreed six-minute retry interval.
                if _is_explicit_disabled_swap_rejection(e):
                    runtime.disabled_pairs[pair_id] = time.time() + DISABLED_PAIR_COOLDOWN
                    print(
                        f"[PAIR DISABLED] {pair_id}: Cantex explicitly rejected the swap "
                        f"({e}); retrying this pair in {DISABLED_PAIR_COOLDOWN} seconds. "
                        "No manual lock; other pairs remain eligible."
                    )
                    return "PAIR_COOLDOWN"

                # All other submission/confirmation exceptions remain fail-safe:
                # the outcome may be uncertain, so block this pair and request review.
                runtime.pending_pairs.add(pair_id)
                print(f"[SWAP OUTCOME UNCERTAIN] {e}. Blocking only {pair_id}; other pairs continue.")
                await _notify_transaction_review(
                    runtime, reason=f"Swap submission/confirmation raised {type(e).__name__}; outcome is uncertain."
                )
                return "PENDING_PAIR"
    else:
        return False


async def fetch_active_hunter_rules(user_id):
    try:
        rules_data = get_user_active_hunter_rules(user_id)

        token_pairs = [
            {
                "a": r["token_a"],
                "b": r["token_b"],
                "net_min": Decimal(str(r.get("net_min", "0.45"))),
                "net_max": Decimal(str(r.get("net_max", "0.60")))
            }
            for r in rules_data
        ]

        return token_pairs

    except Exception as e:
        print(f" Failed to fetch hunter rules: {e}")
        return None

async def run_user(runtime: UserRuntime):
    """Run the preserved Hunter scan loop for one user runtime."""
    runtime.swap_halted = False
    client = runtime.client
    if client is None:
        raise RuntimeError("UserRuntime.client must be created from this user's credentials")
    instruments = runtime.instruments
    print(f" Starting Rebate Hunter for user {runtime.user_id} (Dynamic 100-102 CC + Bidirectional Opportunity Scan)...")

    try:
        remaining, control_state = _allowance_remaining(runtime.user_id)
    except Exception as control_err:
        print(f"[CRITICAL] Cannot read Hunter control state; fail-closed: {control_err}")
        return "HALTED_CONTROL_STATE"

    if control_state.get("manual_intervention_required"):
        runtime.swap_halted = True
        print("[MANUAL INTERVENTION LOCK] Persistent lock active. Owner review is required.")
        return "HALTED_MANUAL_INTERVENTION"

    daily_limit = int(
        control_state.get("daily_swap_limit", control_state.get("daily_limit", DEFAULT_DAILY_LIMIT))
    )
    runtime.swaps_done = int(
        control_state.get("swaps_completed", daily_limit - remaining)
    )
    print(
        f" Today's effective allowance: {remaining} remaining "
        f"({runtime.swaps_done}/{daily_limit} used)."
    )

    if remaining <= 0:
        now = datetime.now(timezone.utc)
        next_midnight_utc = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_next_run = int((next_midnight_utc - now).total_seconds())
        print(f" Daily allowance exhausted. Slot will be released; daily reset is at UTC midnight.")
        await _notify_daily_allowance_exhausted(runtime)
        return f"DAILY_LIMIT_SLEEP:{seconds_until_next_run}"

    restored_rule_signature = None

    try:
        private_ws = await connect_private_ws(client)
        runtime.private_ws_task = asyncio.create_task(_private_event_reader(private_ws, runtime))

        if not runtime.instruments:
            account_admin = await client.get_account_admin()
            for item in getattr(account_admin, "instruments", []) or []:
                symbol = getattr(item, "instrument_symbol", None)
                instrument = getattr(item, "instrument", None)
                if symbol and instrument is not None:
                    runtime.instruments[str(symbol).upper()] = instrument
        instruments = runtime.instruments
        if not instruments:
            raise RuntimeError(f"No Cantex instruments were loaded for user {runtime.user_id}")

        unresolved = get_user_unresolved_swaps(runtime.user_id)
        for pending in unresolved:
            runtime.pending_pairs.add(canonical_pair_key(pending["sell_token"], pending["buy_token"]))
        if runtime.pending_pairs:
            print(f"[PENDING PAIRS] Restored {len(runtime.pending_pairs)} pair-specific submission hold(s); other pairs remain scannable.")


        while remaining > 0 and not runtime.swap_halted:
            try:
                remaining, control_state = _allowance_remaining(runtime.user_id)
                if control_state.get("manual_intervention_required"):
                    runtime.swap_halted = True
                    print("[MANUAL INTERVENTION LOCK] Persistent lock activated. Stopping Hunter.")
                    break
                daily_limit = int(
                    control_state.get(
                        "daily_swap_limit",
                        control_state.get("daily_limit", DEFAULT_DAILY_LIMIT),
                    )
                )
                runtime.swaps_done = int(
                    control_state.get("swaps_completed", daily_limit - remaining)
                )
                if remaining <= 0:
                    break
            except Exception as control_err:
                print(f"[CONTROL STATE ERROR] Cannot safely read allowance; pausing for 60 seconds: {control_err}")
                await asyncio.sleep(60)
                continue

            TOKEN_PAIRS = await fetch_active_hunter_rules(runtime.user_id)
            if TOKEN_PAIRS is None:
                print(" Could not refresh rules. Retrying in 15 seconds...")
                await asyncio.sleep(15)
                continue

            rule_signature = tuple(sorted(
                (canonical_pair_key(pair["a"], pair["b"]),
                 str(pair["a"]).upper(), str(pair["b"]).upper())
                for pair in TOKEN_PAIRS
            ))
            if rule_signature != restored_rule_signature:
                try:
                    restore_ping_pong_memory(runtime.user_id, TOKEN_PAIRS, runtime)
                    restored_rule_signature = rule_signature
                except Exception as e:
                    print(f" Could not restore ping-pong memory: {e}")
                    await asyncio.sleep(15)
                    continue

            if not TOKEN_PAIRS:
                print(" NO ACTIVE RULES. Waiting 60 seconds...")
                save_status({
                    "time": time.strftime("%H:%M:%S"), "pair": "NO_RULES", "net_fee": 0, "pool_fee": 0,
                    "usd_value": 0, "cc_size": 0, "sdk_est_time": 0, "status": "NO_RULES", "swaps_done": runtime.swaps_done
                }, runtime)
                await asyncio.sleep(60)
                continue

            print(f"{_user_tag(runtime)} Active rules: {len(TOKEN_PAIRS)}")

            best_opportunity = None
            lowest_fee = Decimal("999.0")
            scan_stats = {
                "rules": len(TOKEN_PAIRS), "price_missing": 0, "balance_rejected": 0, "instrument_missing": 0,
                "stack_guard": 0, "quote_errors": 0, "fee_data_invalid": 0, "network_fee_rejected": 0,
                "pool_fee_rejected": 0, "qualifying_quotes": 0,
                "disabled_pairs_cooling_down": 0, "pending_pairs": 0,
                "disabled_rejections_this_scan": 0,
            }
            rate_limit_hit = False

            # One fresh account snapshot per user per scan, reused across pairs.
            try:
                account = await client.get_account_info()
            except Exception as e:
                print(f"{_user_tag(runtime)} Could not fetch account info for this scan: {e}")
                await asyncio.sleep(RED_LIGHT_WAIT)
                continue

            for pair in TOKEN_PAIRS:
                token_a = pair["a"]
                token_b = pair["b"]
                net_min = pair["net_min"]
                net_max = pair["net_max"]
                pair_key = canonical_pair_key(token_a, token_b)

                if token_a not in instruments or token_b not in instruments:
                    scan_stats["instrument_missing"] += 1
                    print(f"{_user_tag(runtime)} [SCAN] {pair_key}: instrument unavailable; skipping this scan.")
                    continue
                
                disabled_until = runtime.disabled_pairs.get(pair_key)
                if disabled_until and time.time() < disabled_until:
                    scan_stats["disabled_pairs_cooling_down"] += 1
                    retry_in = max(1, int(disabled_until - time.time()))
                    print(
                        f"{_user_tag(runtime)} [PAIR COOLDOWN] {pair_key}: Cantex previously "
                        f"reported swapping disabled; retry in {retry_in}s. Skipping this pair only."
                    )
                    continue
                if disabled_until:
                    runtime.disabled_pairs.pop(pair_key, None)
                    print(f"{_user_tag(runtime)} [PAIR RETRY] {pair_key}: 360-second disabled-pair cooldown expired.")
                if pair_key in runtime.pending_pairs:
                    scan_stats["pending_pairs"] += 1
                    print(f"{_user_tag(runtime)} [PAIR PENDING] {pair_key}: unresolved submission; skipping this pair only.")
                    continue
                
                balance_a = Decimal("0")
                balance_b = Decimal("0")
                for item in getattr(account, "tokens", []) or []:
                    symbol = getattr(item, "instrument_symbol", None)
                    unlocked = getattr(item, "unlocked_amount", None)

                    if symbol and str(symbol).upper() == token_a:
                        balance_a = Decimal(str(unlocked))
                    if symbol and str(symbol).upper() == token_b:
                        balance_b = Decimal(str(unlocked))

                a_price_cc, _ = get_prices_in_cc_and_usd(token_a)
                b_price_cc, _ = get_prices_in_cc_and_usd(token_b)
                
                if not a_price_cc or not b_price_cc:
                    scan_stats["price_missing"] += 1
                    continue

                # Check both balances independently before applying ping-pong order.
                # This prevents a shortfall in one side being misreported as a
                # shortfall on both sides, and makes the next required direction clear.
                balance_eligible_directions = []

                def try_direction(sell_tok, buy_tok, sell_bal, sell_price):
                    if sell_price is None or sell_price <= 0:
                        return
                    available_cc = sell_bal * sell_price
                    if available_cc < MIN_SWAP_SIZE_CC:
                        print(
                            f" [BALANCE CHECK] {sell_tok}->{buy_tok}: "
                            f"{available_cc:.4f} CC available; minimum is {MIN_SWAP_SIZE_CC} CC."
                        )
                        return
                    target_amount = TARGET_SWAP_SIZE_CC / sell_price
                    amount_to_sell = min(sell_bal, target_amount)
                    if (amount_to_sell * sell_price) > MAX_SWAP_SIZE_CC:
                        amount_to_sell = (MAX_SWAP_SIZE_CC / sell_price).quantize(Decimal("0.0001"))
                    if (amount_to_sell * sell_price) < MIN_SWAP_SIZE_CC:
                        return
                    dir_label = "A_TO_B" if sell_tok == token_a else "B_TO_A"
                    balance_eligible_directions.append({
                        "sell": sell_tok, "buy": buy_tok, "amount": amount_to_sell,
                        "dir": dir_label, "available_cc": available_cc,
                    })

                try_direction(token_a, token_b, balance_a, a_price_cc)
                try_direction(token_b, token_a, balance_b, b_price_cc)

                last_direction = runtime.last_swap_direction.get(pair_key)
                if last_direction:
                    required_direction = "B_TO_A" if last_direction == "A_TO_B" else "A_TO_B"
                    possible_directions = [
                        d for d in balance_eligible_directions if d["dir"] == required_direction
                    ]
                    if not possible_directions:
                        scan_stats["balance_rejected"] += 1
                        other_side_ready = any(
                            d["dir"] == last_direction for d in balance_eligible_directions
                        )
                        if other_side_ready:
                            print(
                                f"[PING-PONG WAIT] {token_a}/{token_b}: next required direction "
                                f"{required_direction} does not have enough balance. The previous "
                                f"direction {last_direction} has funds, but will NOT be repeated; "
                                f"waiting for the opposite side to become eligible."
                            )
                        else:
                            print(
                                f"[BALANCE WAIT] {token_a}/{token_b}: neither direction has the "
                                f"required {MIN_SWAP_SIZE_CC} CC available."
                            )
                        continue
                else:
                    # First-ever trade for this pair: use whichever side is funded.
                    possible_directions = balance_eligible_directions

                if not possible_directions:
                    scan_stats["balance_rejected"] += 1
                    print(
                        f"[BALANCE WAIT] {token_a}/{token_b}: neither direction has the "
                        f"required {MIN_SWAP_SIZE_CC} CC available."
                    )
                    continue

                pair_opportunities = []
                for direction in possible_directions:
                    sell = direction["sell"]
                    buy = direction["buy"]
                    req_sell = direction["amount"]
                    req_cc = req_sell * (a_price_cc if sell == token_a else b_price_cc)
                    
                    if remaining > 3 and runtime.net_cc_delta.get(buy, Decimal("0")) + req_cc > Decimal("200"):
                        scan_stats["stack_guard"] += 1
                        print(f" [STACK GUARD] Skipping {sell}->{buy}. Too much {buy} stacked.")
                        continue

                    try:
                        quote = await _get_swap_quote(
                            client,
                            sell_amount=req_sell,
                            sell_instrument=instruments[sell],
                            buy_instrument=instruments[buy],
                        )
                    except Exception as e:
                        error_str = str(e).lower()
                        if "token swapping is currently disabled" in error_str or "market is not active" in error_str or "pool is disabled" in error_str:
                            runtime.disabled_pairs[pair_key] = time.time() + DISABLED_PAIR_COOLDOWN
                            scan_stats["disabled_rejections_this_scan"] += 1
                            print(
                                f"{_user_tag(runtime)} [PAIR DISABLED] {pair_key}: Cantex rejected "
                                f"the quote because swapping is disabled; retrying in "
                                f"{DISABLED_PAIR_COOLDOWN}s. Other pairs remain eligible."
                            )
                            continue
                        if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
                            print(f"{_user_tag(runtime)} RATE LIMIT in scan. Cooling down 30s before the next scan...")
                            rate_limit_hit = True
                            break
                        if "system busy" in error_str or "temporarily busy" in error_str or "try again later" in error_str:
                            scan_stats["quote_errors"] += 1
                            print(
                                f"[TEMPORARY QUOTE ISSUE] {sell}->{buy}: Cantex is busy; "
                                "skipping this direction for this scan. Other pairs/users continue."
                            )
                            continue
                        scan_stats["quote_errors"] += 1
                        print(f" [QUOTE ERROR] {sell}->{buy}: {e}. Keeping the pair active.")
                        continue

                    fee_values = extract_quote_fees(quote)
                    if fee_values is None:
                        scan_stats["fee_data_invalid"] += 1
                        print(f" [QUOTE REJECTED] {sell}->{buy}: missing or invalid fee data.")
                        continue

                    network_fee, pool_fee_percent = fee_values
                    if not (net_min <= network_fee <= net_max):
                        scan_stats["network_fee_rejected"] += 1
                        continue
                    if not (POOL_MIN <= pool_fee_percent <= POOL_MAX):
                        scan_stats["pool_fee_rejected"] += 1
                        continue

                    scan_stats["qualifying_quotes"] += 1
                    pair_opportunities.append({
                        "sell": sell, "buy": buy, "amount": req_sell, "dir": direction["dir"],
                        "net_min": net_min, "net_max": net_max, "network_fee": network_fee,
                    })

                if rate_limit_hit: break

                if pair_opportunities:
                    pair_choice = min(pair_opportunities, key=lambda item: item["network_fee"])
                    if pair_choice["network_fee"] < lowest_fee:
                        lowest_fee = pair_choice["network_fee"]
                        best_opportunity = {k: v for k, v in pair_choice.items() if k != "network_fee"}

            print(f"{_user_tag(runtime)} [SCAN SUMMARY] " + " | ".join(f"{key}={value}" for key, value in scan_stats.items()))

            if rate_limit_hit:
                await asyncio.sleep(30)
                continue

            if best_opportunity:
                print(f" Best fee found: {lowest_fee} CC for {best_opportunity['sell']} -> {best_opportunity['buy']}")
                executed = await check_and_swap(
                    runtime, client, instruments, best_opportunity["sell"], best_opportunity["buy"], 
                    best_opportunity["net_min"], best_opportunity["net_max"], best_opportunity["amount"],
                    pair_key=canonical_pair_key(best_opportunity["sell"], best_opportunity["buy"]),
                    direction=best_opportunity["dir"]
                )
                if not executed:
                    await asyncio.sleep(2)
                    save_status({
                        "time": time.strftime("%H:%M:%S"), "pair": "Scanning...", "net_fee": 0, "pool_fee": 0,
                        "usd_value": 0, "cc_size": 0, "sdk_est_time": 0, "status": " WAITING", "swaps_done": runtime.swaps_done
                    }, runtime)
                    await asyncio.sleep(RED_LIGHT_WAIT)
            else:
                blocked_count = (
                    scan_stats["disabled_pairs_cooling_down"]
                    + scan_stats["pending_pairs"]
                    + scan_stats["disabled_rejections_this_scan"]
                )
                if scan_stats["disabled_rejections_this_scan"]:
                    print(
                        f"{_user_tag(runtime)} [SCAN RESULT] Cantex disabled swapping for "
                        f"{scan_stats['disabled_rejections_this_scan']} pair(s) during this scan. "
                        f"Retry cooldown: {DISABLED_PAIR_COOLDOWN}s. Waiting {RED_LIGHT_WAIT}s before rescanning."
                    )
                elif scan_stats["disabled_pairs_cooling_down"] or scan_stats["pending_pairs"]:
                    print(
                        f"{_user_tag(runtime)} [SCAN RESULT] No executable opportunity this scan: "
                        f"{scan_stats['disabled_pairs_cooling_down']} pair(s) in Cantex-disabled cooldown, "
                        f"{scan_stats['pending_pairs']} pair(s) awaiting transaction review. "
                        f"Waiting {RED_LIGHT_WAIT}s; other eligible pairs continue to be scanned."
                    )
                else:
                    print(f"{_user_tag(runtime)} No green fees found. Waiting {RED_LIGHT_WAIT}s...")
                save_status({
                    "time": time.strftime("%H:%M:%S"), "pair": "Scanning...", "net_fee": 0, "pool_fee": 0,
                    "usd_value": 0, "cc_size": 0, "sdk_est_time": 0, "status": " WAITING", "swaps_done": runtime.swaps_done
                }, runtime)
                await asyncio.sleep(RED_LIGHT_WAIT)

    except KeyboardInterrupt:
        print("\n Stopped by user.")
    finally:
        if runtime.private_ws_task and not runtime.private_ws_task.done():
            runtime.private_ws_task.cancel()
            try:
                await runtime.private_ws_task
            except asyncio.CancelledError:
                pass
        print(f"Hunter runtime stopped for user {runtime.user_id}.")
    if runtime.swap_halted:
        return "HALTED_MANUAL_INTERVENTION"
    if remaining <= 0:
        now = datetime.now(timezone.utc)
        next_midnight_utc = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_next_run = max(1, int((next_midnight_utc - now).total_seconds()))
        print(f" Daily allowance exhausted. Slot will be released; daily reset is at UTC midnight.")
        await _notify_daily_allowance_exhausted(runtime)
        return f"DAILY_LIMIT_SLEEP:{seconds_until_next_run}"
    return False

if __name__ == "__main__":
    raise SystemExit(
        "rebate_hunter.py now exposes run_user(UserRuntime); "
        "start it through the multi-user slot manager."
    )