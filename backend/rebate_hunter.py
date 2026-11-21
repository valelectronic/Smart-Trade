import asyncio
import json
import time
import httpx
from decimal import Decimal
import os
from pathlib import Path
from cantex_api import connect_sdk, connect_private_ws
from helpers import get_cc_balance
from postgres_database import (
    get_today_swap_count, log_successful_swap, get_connection,
    get_hunter_control_state, activate_manual_intervention_lock,
    create_pending_swap, update_swap_pending_event,
    add_swap_execution_event, update_swap_transaction_status,
    update_swap_failure_event, get_unresolved_swaps,
)
from datetime import datetime, timezone, timedelta

def canonical_pair_key(token_a: str, token_b: str) -> str:
    a, b = sorted([token_a, token_b])
    return f"{a}::{b}"


def restore_ping_pong_memory(token_pairs):
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
                "FROM rebate_swaps ORDER BY created_at DESC"
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

    # Clear only currently active pairs first. This also removes stale memory
    # if a rule was removed and later re-added with no recorded swap history.
    for pair_key in rule_orders:
        _last_swap_direction.pop(pair_key, None)
    _last_swap_direction.update(restored)
    for pair_key, direction in restored.items():
        print(f" Restored ping-pong memory: {pair_key} = {direction}")

    missing = set(rule_orders) - set(restored)
    if missing:
        print(f" No previous swap history found for {len(missing)} active pair(s).")

# --- CONFIGURATION ---
TARGET_SWAP_SIZE_CC = Decimal("101.5")
MIN_SWAP_SIZE_CC = Decimal("100.0")
MAX_SWAP_SIZE_CC = Decimal("102.0")
POOL_MIN = Decimal("0.10")
POOL_MAX = Decimal("0.17")
DAILY_LIMIT = 25
POST_SWAP_COOLDOWN = 20
RED_LIGHT_WAIT = 10      
MIN_CC_BALANCE = Decimal("5.0") 

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

PRICES_FILE = Path(__file__).parent / "prices.json"
STATUS_FILE = Path(__file__).parent / "hunter_status.json"

swaps_done = 0
swap_halted = False
execution_lock = asyncio.Lock() 
_last_swap_direction = {} 
_disabled_pairs = {} 
_net_cc_delta = {}
_event_queue = asyncio.Queue()
_private_ws_task = None
PRIVATE_EVENT_TIMEOUT = 45
EXECUTION_SETTLE_SECONDS = 3

def _allowance_remaining():
    state = get_hunter_control_state()
    if not state:
        raise RuntimeError("Hunter control state is unavailable")
    remaining = int(state.get("swaps_remaining", DAILY_LIMIT))
    return max(0, min(DAILY_LIMIT, remaining)), state


def _activate_manual_lock(reason: str, tx_id=None):
    """Persistent lock is reserved for low CC reserve or no funded swap direction."""
    global swap_halted
    swap_halted = True
    try:
        activate_manual_intervention_lock(reason=reason, tx_id=tx_id)
        print(f"[MANUAL INTERVENTION LOCK] {reason}")
    except Exception as db_err:
        print(f"[CRITICAL] Could not persist manual lock: {db_err}")


def _mark_swap_unknown(tx_id, reason: str):
    """Persist and log the precise reason a submitted swap remains unresolved."""
    message = f"[SWAP UNKNOWN] tx_id={tx_id} reason={reason}"
    print(message)
    try:
        update_swap_transaction_status(tx_id, "UNKNOWN", reason)
    except Exception as db_err:
        print(f"[CRITICAL] Could not persist UNKNOWN status for {tx_id}: {db_err}")


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


def save_status(status_dict):
    try:
        with STATUS_FILE.open("w") as f:
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


def _instrument_symbol(value):
    if isinstance(value, dict):
        return str(value.get("id") or "").upper()
    return str(value or "").upper()


def _ledger_amount_matches(expected: Decimal, observed: Decimal) -> bool:
    """Match SDK amounts despite Cantex ledger decimal precision truncation.

    The submitted quote amount can contain more decimal places than the amount
    emitted in Pool.SwapExecuted. Allow at most one unit of the least
    significant decimal place actually present in either value. Route
    uniqueness is still enforced separately, so this tolerance cannot silently
    choose between multiple candidate legs.
    """
    expected = Decimal(str(expected))
    observed = Decimal(str(observed))
    if expected == observed:
        return True

    expected_quantum = Decimal(1).scaleb(expected.as_tuple().exponent)
    observed_quantum = Decimal(1).scaleb(observed.as_tuple().exponent)
    tolerance = max(abs(expected_quantum), abs(observed_quantum))
    return abs(expected - observed) <= tolerance


async def _private_event_reader(ws):
    """Continuously buffer private Cantex events; reconnect is handled by SDK wrapper."""
    async for event in ws:
        raw = _event_raw(event)
        event_type = raw.get("type") or getattr(event, "event_type", None)
        if event_type in ("Pool.SwapPending", "Pool.SwapExecuted", "Pool.SwapFailed"):
            await _event_queue.put(raw)


async def _next_event_until(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        return await asyncio.wait_for(_event_queue.get(), timeout=remaining)
    except asyncio.TimeoutError:
        return None


async def _confirm_submitted_swap(tx_id, sell_token, buy_token, sell_amount):
    """Correlate pending by exact SDK ID; confirm only a unique, complete route.

    SwapExecuted has no swap ID. Therefore execution events are accepted only
    when wallet/user match the pending event and their ledger route uniquely
    reconstructs the requested input-to-output path within this isolated window.
    Any ambiguity remains UNKNOWN.
    """
    deadline = time.monotonic() + PRIVATE_EVENT_TIMEOUT
    pending = None
    executions = []

    while time.monotonic() < deadline:
        raw = await _next_event_until(deadline)
        if raw is None:
            break
        data = raw.get("data") or {}
        typ = raw.get("type")

        if typ == "Pool.SwapPending" and str(data.get("id")) == str(tx_id):
            pending = raw
            update_swap_pending_event(tx_id, raw)
            break

    if pending is None:
        _mark_swap_unknown(
            tx_id,
            "No SwapPending event with the exact SDK submission ID was observed."
        )
        return False

    pending_data = pending.get("data") or {}
    pending_user = str(pending.get("user_id") or "")
    pending_wallet = str(pending.get("wallet_address") or "")
    expected_sell = _instrument_symbol(pending_data.get("input_instrument_id"))
    expected_buy = _instrument_symbol(pending_data.get("output_instrument_id"))

    if expected_sell != str(sell_token).upper() or expected_buy != str(buy_token).upper():
        _mark_swap_unknown(
            tx_id, "Pending event instruments do not match submitted pair. "
            f"pending={expected_sell}->{expected_buy}, submitted={str(sell_token).upper()}->{str(buy_token).upper()}"
        )
        return False

    # Collect all execution/failure events in the confirmation window.
    while time.monotonic() < deadline:
        raw = await _next_event_until(deadline)
        if raw is None:
            break
        if raw.get("user_id") != pending_user or raw.get("wallet_address") != pending_wallet:
            continue
        typ = raw.get("type")
        data = raw.get("data") or {}
        if typ == "Pool.SwapFailed":
            if str(data.get("id")) == str(tx_id):
                update_swap_failure_event(tx_id, raw)
                update_swap_transaction_status(tx_id, "FAILED", "Matching SwapFailed event received.")
                print(f"[SWAP FAILED] tx_id={tx_id} matching Pool.SwapFailed event received.")
                return False
        elif typ == "Pool.SwapExecuted":
            details = data.get("swap_details") or {}
            executions.append(raw)
            add_swap_execution_event(tx_id, raw)

            # Wait briefly for other legs emitted with the same ledger timestamp.
            ledger_ts = data.get("ledger_created_at")
            if ledger_ts:
                settle_deadline = time.monotonic() + EXECUTION_SETTLE_SECONDS
                while time.monotonic() < settle_deadline:
                    extra = await _next_event_until(settle_deadline)
                    if extra is None:
                        break
                    if extra.get("user_id") == pending_user and extra.get("wallet_address") == pending_wallet:
                        if extra.get("type") == "Pool.SwapExecuted":
                            extra_data = extra.get("data") or {}
                            if extra_data.get("ledger_created_at") == ledger_ts:
                                executions.append(extra)
                                add_swap_execution_event(tx_id, extra)
                                # Start the quiet window again after each matching route leg.
                                settle_deadline = time.monotonic() + EXECUTION_SETTLE_SECONDS
                        elif extra.get("type") == "Pool.SwapFailed":
                            if str((extra.get("data") or {}).get("id")) == str(tx_id):
                                update_swap_failure_event(tx_id, extra)
                                update_swap_transaction_status(tx_id, "FAILED", "Matching SwapFailed event received.")
                                print(f"[SWAP FAILED] tx_id={tx_id} matching Pool.SwapFailed event received.")
                                return False
                break

    if not executions:
        _mark_swap_unknown(
            tx_id, "No matching SwapExecuted events observed before timeout."
        )
        return False

    # A Cantex routed swap emits sequential legs sharing ledger_created_at.
    # Reconstruct a single unambiguous chain from the pending input to output.
    groups = {}
    for event in executions:
        data = event.get("data") or {}
        details = data.get("swap_details") or {}
        ts = data.get("ledger_created_at")
        if not ts:
            continue
        groups.setdefault(ts, []).append(details)

    valid_groups = []
    for ts, legs in groups.items():
        # Route ordering is inferred by amount continuity, not event arrival order.
        remaining = list(legs)
        current_symbol = expected_sell
        current_amount = Decimal(str(sell_amount))
        chain = []
        while remaining:
            matches = []
            for leg in remaining:
                inp = leg.get("input_instrument_id") or {}
                out = leg.get("output_instrument_id") or {}
                in_symbol = _instrument_symbol(inp)
                out_symbol = _instrument_symbol(out)
                try:
                    in_amount = Decimal(str(leg.get("input_amount")))
                    out_amount = Decimal(str(leg.get("output_amount")))
                except Exception:
                    continue
                # Cantex may serialise the executed input amount at lower
                # decimal precision than the SDK submission amount. Compare
                # within one unit of the least significant reported decimal,
                # rather than requiring impossible string-level equality.
                if in_symbol == current_symbol and _ledger_amount_matches(current_amount, in_amount):
                    matches.append((leg, out_symbol, out_amount))
            if len(matches) != 1:
                break
            leg, current_symbol, current_amount = matches[0]
            chain.append(leg)
            remaining.remove(leg)
            if current_symbol == expected_buy:
                if not remaining:
                    valid_groups.append((ts, chain, current_amount))
                break

    if len(valid_groups) != 1:
        _mark_swap_unknown(
            tx_id,
            "Execution events could not be reconstructed into one unique complete route. "
            f"execution_event_count={len(executions)}, ledger_groups={len(groups)}, "
            f"valid_routes={len(valid_groups)}"
        )
        return False

    _, chain, final_amount = valid_groups[0]
    update_swap_transaction_status(tx_id, "SUCCESSFUL")
    return True


async def check_and_swap(client, instruments, sell_token, buy_token, net_min, net_max, sell_amount_tokens, pair_key=None, direction=None):
    global swaps_done, swap_halted
    
    
    if swap_halted:
        return "HALTED_MANUAL_INTERVENTION"
    try:
        remaining, control_state = _allowance_remaining()
        if control_state.get("manual_intervention_required"):
            swap_halted = True
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
        print("[CC BALANCE WAIT] Could not read CC balance. Retrying on the next scan.")
        return False
    if cc_balance < MIN_CC_BALANCE:
        _activate_manual_lock(
            f"CC balance is {cc_balance} CC, below the configured {MIN_CC_BALANCE} CC network-fee reserve. "
            "Add CC, then clear the lock in the dashboard."
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
        quote = await client.get_swap_quote(
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
        "swaps_done": swaps_done
    })

    if network_ok and pool_ok:
        async with execution_lock:
            print(f" CONDITIONS MET! Locking execution queue...")
            print(f" Details: {sell_amount_tokens} {sell_token} (~${total_usd_value:.2f} USD, {(sell_amount_tokens * cc_price):.2f} CC)")
            
            start_time = time.perf_counter()
            try:
                result = await client.swap(
                    sell_amount=sell_amount_tokens,
                    sell_instrument=instruments[sell_token],
                    buy_instrument=instruments[buy_token],
                )

                tx_id = result.get("id") if isinstance(result, dict) else None
                verify_flag = result.get("verify") if isinstance(result, dict) else None
                if not tx_id:
                    print(
                        "[SWAP RETRY] Submission response did not contain a transaction ID. "
                        "No manual lock applied; waiting before the next scan."
                    )
                    await asyncio.sleep(POST_SWAP_COOLDOWN)
                    return False

                if verify_flag is not True:
                    print(
                        f"[SUBMISSION WARNING] tx_id={tx_id} verify={verify_flag!r}. "
                        "The ID is available, so the Hunter will check matching ledger events "
                        "rather than treating this flag alone as proof of failure."
                    )

                create_pending_swap(
                    tx_id=tx_id,
                    sell_token=sell_token,
                    buy_token=buy_token,
                    sell_amount=str(sell_amount_tokens),
                    submission_response=result,
                )
                print(f" [SUBMITTED] TX ID: {tx_id}; awaiting ledger execution events.")

                try:
                    successful = await _confirm_submitted_swap(
                        tx_id, sell_token, buy_token, sell_amount_tokens
                    )
                except Exception as confirm_err:
                    _mark_swap_unknown(tx_id, f"Confirmation processing error: {confirm_err}")
                    successful = False

                if not successful:
                    print(
                        f"[SWAP RETRY] Transaction {tx_id} was not confirmed as a complete route. "
                        "It remains recorded for visibility, but it will not activate the manual lock. "
                        f"Retrying after {POST_SWAP_COOLDOWN}s."
                    )
                    await asyncio.sleep(POST_SWAP_COOLDOWN)
                    return False

                end_time = time.perf_counter()
                print(f" [LEDGER CONFIRMED] Swap completed in {end_time - start_time:.2f}s. TX ID: {tx_id}")

                logged = False
                db_error = None
                for attempt in range(3):
                    try:
                        logged = log_successful_swap(
                            tx_id=tx_id,
                            pair=f"{sell_token}->{buy_token}",
                            sell_token=sell_token,
                            buy_token=buy_token,
                            sell_amount=float(sell_amount_tokens),
                            net_fee=float(network_fee),
                            pool_fee=float(pool_fee_percent),
                            usd_value=float(total_usd_value)
                        )
                        db_error = None
                        break
                    except Exception as err:
                        db_error = err
                        print(f"[DB LOG RETRY {attempt + 1}/3] {err}")
                        if attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))

                if db_error is not None:
                    print(
                        f"[DB LOG DELAYED] Cantex confirmed transaction {tx_id}, "
                        f"but history logging failed after retries: {db_error}. "
                        "No manual lock applied; the Hunter will retry on a later scan."
                    )
                    await asyncio.sleep(POST_SWAP_COOLDOWN)
                    return False

                if logged:
                    swaps_done += 1
                    print(f" Confirmed swap recorded. Session count: {swaps_done}/{DAILY_LIMIT}")
                else:
                    print(f" Transaction {tx_id} already exists in DB; duplicate count prevented.")

                if pair_key and direction:
                    _last_swap_direction[pair_key] = direction
                else:
                    _last_swap_direction[canonical_pair_key(sell_token, buy_token)] = "A_TO_B"

                _net_cc_delta[sell_token] = _net_cc_delta.get(sell_token, Decimal("0")) - (sell_amount_tokens * cc_price)
                _net_cc_delta[buy_token] = _net_cc_delta.get(buy_token, Decimal("0")) + (sell_amount_tokens * cc_price)

                # Pool.SwapExecuted / SDK submission confirmation is the execution
                # signal. Wallet balances can lag behind the ledger, so refresh them
                # for the next decision but never reverse a confirmed swap or lock
                # the Hunter solely because this read is stale.
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
                error_text = str(e).lower()
                if (
                    pair_key
                    and "token swapping is currently disabled" in error_text
                ):
                    disabled_until = time.time() + 300
                    _disabled_pairs[pair_key] = disabled_until
                    print(
                        f"[PAIR TEMPORARILY SKIPPED] {pair_key}: Cantex reports "
                        f"that token swapping is disabled. Skipping this pair "
                        f"for 5 minutes; other eligible markets remain active."
                    )
                else:
                    print(
                        f"[SWAP RETRY] Swap submission/confirmation raised "
                        f"{type(e).__name__}: {e}. No manual lock applied; "
                        f"retrying after {POST_SWAP_COOLDOWN}s."
                    )
                await asyncio.sleep(POST_SWAP_COOLDOWN)
                return False
    else:
        return False


async def fetch_active_hunter_rules():
    try:
        async with httpx.AsyncClient(timeout=15.0) as client_http:
            res = await client_http.get(
                f"{API_URL}/api/hunter-rules"
            )

        if res.status_code != 200:
            print(f" API Error: {res.status_code}")
            return None

        rules_data = res.json()

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

async def main():
    global swaps_done, swap_halted
    swap_halted = False
    print(" Starting Rebate Hunter (Dynamic 100-102 CC + Bidirectional Opportunity Scan)...")

    try:
        remaining, control_state = _allowance_remaining()
    except Exception as control_err:
        print(f"[CRITICAL] Cannot read Hunter control state; fail-closed: {control_err}")
        return "HALTED_CONTROL_STATE"

    if control_state.get("manual_intervention_required"):
        swap_halted = True
        print("[MANUAL INTERVENTION LOCK] Persistent lock active. Owner review is required.")
        return "HALTED_MANUAL_INTERVENTION"

    swaps_done = DAILY_LIMIT - remaining
    print(f" Today's effective allowance: {remaining} remaining ({swaps_done}/{DAILY_LIMIT} used).")

    if remaining <= 0:
        now = datetime.now(timezone.utc)
        next_midnight_utc = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_next_run = int((next_midnight_utc - now).total_seconds())
        print(f" Daily allowance exhausted. Sleeping {seconds_until_next_run}s until UTC midnight.")
        return f"DAILY_LIMIT_SLEEP:{seconds_until_next_run}"

    restored_rule_signature = None
    remaining = DAILY_LIMIT

    client = await connect_sdk()
    
    try:
        global _private_ws_task
        private_ws = await connect_private_ws(client)
        _private_ws_task = asyncio.create_task(_private_event_reader(private_ws))

        unresolved = get_unresolved_swaps()
        if unresolved:
            print(
                f"[TRANSACTION TRACKING] {len(unresolved)} unresolved transaction(s) "
                "remain in the database. They are retained for visibility, "
                "but they will not block the Hunter from scanning."
            )
            for item in unresolved:
                print(
                    f"[TRANSACTION TRACKING] tx_id={item.get('tx_id')} "
                    f"status={item.get('status')} — monitoring continues."
                )

        account_admin = await client.get_account_admin()
        instruments = {}
        for item in getattr(account_admin, "instruments", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            instrument = getattr(item, "instrument", None)
            if symbol and instrument is not None:
                instruments[str(symbol).upper()] = instrument

        while remaining > 0 and not swap_halted:
            try:
                remaining, control_state = _allowance_remaining()
                if control_state.get("manual_intervention_required"):
                    swap_halted = True
                    print("[MANUAL INTERVENTION LOCK] Persistent lock activated. Stopping Hunter.")
                    break
                swaps_done = DAILY_LIMIT - remaining
                if remaining <= 0:
                    break
            except Exception as control_err:
                print(f"[CONTROL STATE ERROR] Cannot safely read allowance; pausing for 60 seconds: {control_err}")
                await asyncio.sleep(60)
                continue

            # Always refresh active rules from the database
            TOKEN_PAIRS = await fetch_active_hunter_rules()

            # API unavailable: pause and retry
            if TOKEN_PAIRS is None:
                print(" Could not refresh rules. Retrying in 15 seconds...")
                await asyncio.sleep(15)
                continue

            # Restore memory when the active rule set or its A/B order changes.
            rule_signature = tuple(sorted(
                (canonical_pair_key(pair["a"], pair["b"]),
                 str(pair["a"]).upper(), str(pair["b"]).upper())
                for pair in TOKEN_PAIRS
            ))
            if rule_signature != restored_rule_signature:
                try:
                    restore_ping_pong_memory(TOKEN_PAIRS)
                    restored_rule_signature = rule_signature
                except Exception as e:
                    print(f" Could not restore ping-pong memory: {e}")
                    await asyncio.sleep(15)
                    continue

            # No active rules: wait
            if not TOKEN_PAIRS:
                print(" NO ACTIVE RULES. Waiting 60 seconds...")
                save_status({
                    "time": time.strftime("%H:%M:%S"),
                    "pair": "NO_RULES",
                    "net_fee": 0,
                    "pool_fee": 0,
                    "usd_value": 0,
                    "cc_size": 0,
                    "sdk_est_time": 0,
                    "status": "NO_RULES",
                    "swaps_done": swaps_done
                })
                await asyncio.sleep(60)
                continue

            print(f" Active rules: {len(TOKEN_PAIRS)}")

            try:
                scan_account = await client.get_account_info()
                scan_cc_balance = get_cc_balance(scan_account)
                if scan_cc_balance is not None and scan_cc_balance < MIN_CC_BALANCE:
                    _activate_manual_lock(
                        f"CC balance is {scan_cc_balance} CC, below the configured "
                        f"{MIN_CC_BALANCE} CC network-fee reserve. Add CC, then clear the lock in the dashboard."
                    )
                    break
                if scan_cc_balance is None:
                    print("[CC BALANCE WAIT] CC balance unavailable; continuing scan and retrying.")
            except Exception as balance_err:
                print(f"[CC BALANCE CHECK RETRY] {balance_err}. Continuing scan.")

            best_opportunity = None
            lowest_fee = Decimal("999.0")
            scan_stats = {
                "rules": len(TOKEN_PAIRS), "price_missing": 0,
                "balance_rejected": 0, "instrument_missing": 0,
                "stack_guard": 0, "quote_errors": 0,
                "fee_data_invalid": 0, "network_fee_rejected": 0,
                "pool_fee_rejected": 0, "qualifying_quotes": 0,
                "balance_evaluable_pairs": 0, "balance_eligible_directions": 0,
            }
            rate_limit_hit = False

            for pair in TOKEN_PAIRS:
            
                token_a = pair["a"]
                token_b = pair["b"]
                net_min = pair["net_min"]
                net_max = pair["net_max"]
                
                pair_key = canonical_pair_key(token_a, token_b)

                if token_a not in instruments or token_b not in instruments:
                    scan_stats["instrument_missing"] += 1
                    print(f" [SCAN] {pair_key}: instrument unavailable; skipping this scan.")
                    continue
                
                if pair_key in _disabled_pairs:
                    if time.time() < _disabled_pairs[pair_key]:
                        continue
                    _disabled_pairs.pop(pair_key, None)
                    print(f"[PAIR RECHECK] Temporary skip expired for {pair_key}; checking it again.")
                
                try:
                    account = await client.get_account_info()
                except Exception as e:
                    print(f" Could not fetch account info: {e}")
                    break
                balance_a = Decimal("0")
                balance_b = Decimal("0")
                for item in getattr(account, "tokens", []) or []:
                    symbol = getattr(item, "instrument_symbol", None)
                    unlocked = getattr(item, "unlocked_amount", None)
                    # DIAGNOSTIC: show every token Cantex returns

                    if symbol and str(symbol).upper() == token_a:
                        balance_a = Decimal(str(unlocked))
                    if symbol and str(symbol).upper() == token_b:
                        balance_b = Decimal(str(unlocked))

                a_price_cc, _ = get_prices_in_cc_and_usd(token_a)
                b_price_cc, _ = get_prices_in_cc_and_usd(token_b)
                
                
                if not a_price_cc or not b_price_cc:
                    scan_stats["price_missing"] += 1
                    continue

                scan_stats["balance_evaluable_pairs"] += 1

                # Evaluate both directions, then enforce strict A/B alternation
                # after the first confirmed swap for this pair. A successful A->B
                # must be followed by B->A, and vice versa. If the required reverse
                # direction is not currently balance-eligible or does not qualify
                # on fees/stack guard, wait; never repeat the previous direction.
                possible_directions = []

                def try_direction(sell_tok, buy_tok, sell_bal, sell_price):
                    """Add a direction when its actual sell-token balance qualifies."""
                    if sell_price is None or sell_price <= 0:
                        return
                    if (sell_bal * sell_price) < MIN_SWAP_SIZE_CC:
                        return

                    target_amount = TARGET_SWAP_SIZE_CC / sell_price
                    amount_to_sell = min(sell_bal, target_amount)
                    if (amount_to_sell * sell_price) > MAX_SWAP_SIZE_CC:
                        amount_to_sell = (MAX_SWAP_SIZE_CC / sell_price).quantize(Decimal("0.0001"))
                    if (amount_to_sell * sell_price) < MIN_SWAP_SIZE_CC:
                        return

                    scan_stats["balance_eligible_directions"] += 1
                    dir_label = "A_TO_B" if sell_tok == token_a else "B_TO_A"
                    possible_directions.append({
                        "sell": sell_tok,
                        "buy": buy_tok,
                        "amount": amount_to_sell,
                        "dir": dir_label,
                    })

                # Re-read both balances on every scan. Once a swap has been
                # confirmed, however, only the opposite direction may execute.
                try_direction(token_a, token_b, balance_a, a_price_cc)
                try_direction(token_b, token_a, balance_b, b_price_cc)

                pair_key_for_direction = canonical_pair_key(token_a, token_b)
                last_direction = _last_swap_direction.get(pair_key_for_direction)
                if last_direction == "A_TO_B":
                    required_direction = "B_TO_A"
                elif last_direction == "B_TO_A":
                    required_direction = "A_TO_B"
                else:
                    required_direction = None

                if required_direction:
                    required_opportunities = [
                        item for item in possible_directions
                        if item["dir"] == required_direction
                    ]
                    if not required_opportunities:
                        available = ", ".join(
                            f"{item['sell']}->{item['buy']}" for item in possible_directions
                        ) or "neither direction"
                        print(
                            f"[ALTERNATION WAIT] {token_a}/{token_b}: last confirmed direction "
                            f"was {last_direction}; waiting for {required_direction}. "
                            f"Currently balance-eligible: {available}."
                        )
                        continue
                    possible_directions = required_opportunities

                if not possible_directions:
                    scan_stats["balance_rejected"] += 1
                    print(f"[BALANCE WAIT] {token_a}/{token_b} — neither direction currently has ≥{MIN_SWAP_SIZE_CC} CC available. Will recheck.")
                    continue

                pair_opportunities = []
                for direction in possible_directions:
                    sell = direction["sell"]
                    buy = direction["buy"]
                    req_sell = direction["amount"]
                    req_cc = req_sell * (a_price_cc if sell == token_a else b_price_cc)
                    
                     # Stack guard protects against over-accumulation over many swaps.
                    # For the last 3 swaps of the day, bypass it — the marginal
                    # accumulation is negligible and we don't want to miss a rebate.
                    if remaining > 3 and _net_cc_delta.get(buy, Decimal("0")) + req_cc > Decimal("200"):
                        scan_stats["stack_guard"] += 1
                        print(f" [STACK GUARD] Skipping {sell}->{buy}. Too much {buy} stacked.")
                        continue

                    try:
                        quote = await client.get_swap_quote(
                            sell_amount=req_sell,
                            sell_instrument=instruments[sell],
                            buy_instrument=instruments[buy],
                        )
                    except Exception as e:
                        error_str = str(e).lower()
                        if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
                            print(" RATE LIMIT in scan. Cooling down 30s before the next scan...")
                            rate_limit_hit = True
                            break

                        scan_stats["quote_errors"] += 1
                        print(f" [QUOTE ERROR] {sell}->{buy}: {e}. Keeping the pair active; checking other eligible directions.")
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
                        "sell": sell,
                        "buy": buy,
                        "amount": req_sell,
                        "dir": direction["dir"],
                        "net_min": net_min,
                        "net_max": net_max,
                        "network_fee": network_fee,
                    })

                if rate_limit_hit:
                    break

                if pair_opportunities:
                    # If this is the pair's first confirmed swap, either
                    # qualifying direction may be selected by lowest network fee.
                    # After that, the alternation filter above leaves only the
                    # required reverse direction eligible.
                    pair_choice = min(
                        pair_opportunities,
                        key=lambda item: item["network_fee"],
                    )
                    if pair_choice["network_fee"] < lowest_fee:
                        lowest_fee = pair_choice["network_fee"]
                        best_opportunity = {
                            key: value for key, value in pair_choice.items()
                            if key != "network_fee"
                        }

            if swap_halted:
                break

            print(
                " [SCAN SUMMARY] "
                + " | ".join(f"{key}={value}" for key, value in scan_stats.items())
            )

            if (
                scan_stats["balance_evaluable_pairs"] > 0
                and scan_stats["balance_eligible_directions"] == 0
            ):
                reason = (
                    f"No active market direction has enough unlocked token balance "
                    f"to meet the {MIN_SWAP_SIZE_CC} CC minimum across "
                    f"{scan_stats['balance_evaluable_pairs']} price-evaluable pair(s). "
                    "Add funds or adjust active pairs, then clear the lock in the dashboard."
                )
                _activate_manual_lock(reason)
                break

            if rate_limit_hit:
                await asyncio.sleep(30)
                continue

            if best_opportunity:
                print(f" Best fee found: {lowest_fee} CC for {best_opportunity['sell']} -> {best_opportunity['buy']}")
                executed = await check_and_swap(
                    client, instruments, 
                    best_opportunity["sell"], best_opportunity["buy"], 
                    best_opportunity["net_min"], best_opportunity["net_max"],
                    best_opportunity["amount"],
                    pair_key=canonical_pair_key(best_opportunity["sell"], best_opportunity["buy"]),
                    direction=best_opportunity["dir"]
                )
                if not executed:
                    await asyncio.sleep(2)
                    save_status({
                        "time": time.strftime("%H:%M:%S"),
                        "pair": "Scanning...",
                        "net_fee": 0,
                        "pool_fee": 0,
                        "usd_value": 0,
                        "cc_size": 0,
                        "sdk_est_time": 0,
                        "status": " WAITING",
                        "swaps_done": swaps_done
                    })
                    await asyncio.sleep(RED_LIGHT_WAIT)
            else:
                print(f" No green fees found. Waiting {RED_LIGHT_WAIT}s...")
                save_status({
                    "time": time.strftime("%H:%M:%S"),
                    "pair": "Scanning...",
                    "net_fee": 0,
                    "pool_fee": 0,
                    "usd_value": 0,
                    "cc_size": 0,
                    "sdk_est_time": 0,
                    "status": " WAITING",
                    "swaps_done": swaps_done
                })
                await asyncio.sleep(RED_LIGHT_WAIT)

    except KeyboardInterrupt:
        print("\n Stopped by user.")
    finally:
        if _private_ws_task and not _private_ws_task.done():
            _private_ws_task.cancel()
            try:
                await _private_ws_task
            except asyncio.CancelledError:
                pass
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        print("Connection closed.")
    if swap_halted:
        return "HALTED_MANUAL_INTERVENTION"
    if remaining <= 0:
        now = datetime.now(timezone.utc)
        next_midnight_utc = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_next_run = max(1, int((next_midnight_utc - now).total_seconds()))
        print(f" Daily allowance exhausted. Sleeping {seconds_until_next_run}s until UTC midnight.")
        return f"DAILY_LIMIT_SLEEP:{seconds_until_next_run}"
    return False

if __name__ == "__main__":
    asyncio.run(main())