import asyncio
import json
import time
import httpx
from decimal import Decimal
import os
from pathlib import Path
from cantex_api import connect_sdk
from helpers import get_cc_balance
from postgres_database import get_today_swap_count, log_successful_swap, get_connection
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

async def check_and_swap(client, instruments, sell_token, buy_token, net_min, net_max, sell_amount_tokens, pair_key=None, direction=None):
    global swaps_done, swap_halted
    
    
    if swaps_done >= DAILY_LIMIT:
        return False
        
    try:
        account = await client.get_account_info()
    except Exception as e:
        print(f" Could not fetch account info: {e}")
        return False
    cc_balance = get_cc_balance(account)
    if cc_balance is not None and cc_balance < MIN_CC_BALANCE:
        print(f" Low CC balance ({cc_balance} CC). Pausing to protect gas.")
        return False

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
                
                if not result or not result.get("verify"):
                    print(f" PARTIAL SWAP DETECTED. Halting for manual review.")
                    swap_halted = True
                    return "HALTED_PARTIAL_SWAP"

                end_time = time.perf_counter()
                exec_time = end_time - start_time
                tx_id = result.get('id')
                print(f" SWAP SUCCESS in {exec_time:.2f}s! TX ID: {tx_id}")

                # --- VERIFY THE SWAP ACTUALLY EXECUTED ON CANTEX ---
                await asyncio.sleep(POST_SWAP_COOLDOWN)

                try:
                    account_after = await client.get_account_info()
                    balance_after = Decimal("0")
                    for item in getattr(account_after, "tokens", []) or []:
                        sym = getattr(item, "instrument_symbol", None)
                        if sym and str(sym).upper() == sell_token:
                            balance_after = Decimal(str(getattr(item, "unlocked_amount", 0)))
                            break

                    actual_decrease = current_balance - balance_after
                    if actual_decrease < sell_amount_tokens * Decimal("0.99"):
                        print(f" [SWAP FAILED ON CANTEX] Expected {sell_amount_tokens} {sell_token} decrease, got {actual_decrease}. Halting.")
                        swap_halted = True
                        return "HALTED_PARTIAL_SWAP"
                except Exception as verify_err:
                    print(f" [BALANCE CHECK ERROR] Could not verify swap: {verify_err}. Proceeding with count.")

                try:
                    log_successful_swap(
                        tx_id=tx_id,
                        pair=f"{sell_token}->{buy_token}",
                        sell_token=sell_token,
                        buy_token=buy_token,
                        sell_amount=float(sell_amount_tokens),
                        net_fee=float(network_fee),
                        pool_fee=float(pool_fee_percent),
                        usd_value=float(total_usd_value)
                    )
                except Exception as db_err:
                    print(f" [DB ERROR] Swap succeeded but log failed: {db_err}. Incrementing count anyway.")

                swaps_done += 1
                print(f" Logged swap to database. Total today: {swaps_done}/{DAILY_LIMIT}")

                if pair_key and direction:
                    _last_swap_direction[pair_key] = direction
                else:
                    _last_swap_direction[canonical_pair_key(sell_token, buy_token)] = "A_TO_B"

                _net_cc_delta[sell_token] = _net_cc_delta.get(sell_token, Decimal("0")) - (sell_amount_tokens * cc_price)
                _net_cc_delta[buy_token] = _net_cc_delta.get(buy_token, Decimal("0")) + (sell_amount_tokens * cc_price)

                return True
            except Exception as e:
                print(f" Swap Failed: {e}")
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
    print(" Starting Rebate Hunter (Dynamic 100-102 CC + Strict Ping-Pong)...")
    
    swaps_done = get_today_swap_count()
    print(f" Today's swaps already completed: {swaps_done}/{DAILY_LIMIT}")
    
    if swaps_done >= DAILY_LIMIT:
        now = datetime.now(timezone.utc)
        next_midnight_utc = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_next_run = int((next_midnight_utc - now).total_seconds())
        print(f" Daily limit reached ({swaps_done}/{DAILY_LIMIT}). Sleeping {seconds_until_next_run}s until UTC midnight.")
        return f"DAILY_LIMIT_SLEEP:{seconds_until_next_run}"

    restored_rule_signature = None

    client = await connect_sdk()
    
    try:
        account_admin = await client.get_account_admin()
        instruments = {}
        for item in getattr(account_admin, "instruments", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            instrument = getattr(item, "instrument", None)
            if symbol and instrument is not None:
                instruments[str(symbol).upper()] = instrument

        while swaps_done < DAILY_LIMIT and not swap_halted:

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

            best_opportunity = None
            lowest_fee = Decimal("999.0")
            scan_stats = {
                "rules": len(TOKEN_PAIRS), "price_missing": 0,
                "balance_rejected": 0, "instrument_missing": 0,
                "stack_guard": 0, "quote_errors": 0,
                "fee_data_invalid": 0, "network_fee_rejected": 0,
                "pool_fee_rejected": 0, "qualifying_quotes": 0,
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
                
                if pair_key in _disabled_pairs and time.time() < _disabled_pairs[pair_key]:
                    continue
                
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

                 # --- PING-PONG WITH FALLBACK (no hardcoding, honors DB order) ---
                last_dir = _last_swap_direction.get(pair_key)

                forced_dir = None
                if last_dir == "A_TO_B":
                    forced_dir = "B_TO_A"
                elif last_dir == "B_TO_A":
                    forced_dir = "A_TO_B"

                possible_directions = []

                def try_direction(sell_tok, buy_tok, sell_bal, sell_price):
                    """Helper: adds a direction to possible_directions if balance ≥ MIN_SWAP_SIZE_CC.
                    Targets TARGET_SWAP_SIZE_CC, capped at MAX, floored at MIN."""
                    if sell_price is None or sell_price <= 0:
                        return
                    if (sell_bal * sell_price) < MIN_SWAP_SIZE_CC:
                        return

                    # Target 101.5 CC worth of tokens, but never sell more than we have
                    target_amount = TARGET_SWAP_SIZE_CC / sell_price
                    amount_to_sell = min(sell_bal, target_amount)

                    # Hard cap at MAX (capital ceiling)
                    if (amount_to_sell * sell_price) > MAX_SWAP_SIZE_CC:
                        amount_to_sell = (MAX_SWAP_SIZE_CC / sell_price).quantize(Decimal("0.0001"))

                    # Hard floor at MIN (rebate eligibility)
                    if (amount_to_sell * sell_price) < MIN_SWAP_SIZE_CC:
                        return

                    dir_label = "A_TO_B" if sell_tok == token_a else "B_TO_A"
                    possible_directions.append({
                        "sell": sell_tok,
                        "buy": buy_tok,
                        "amount": amount_to_sell,
                        "dir": dir_label,
                    })

                # 1. Try the forced (reverse of last swap) direction first
                if forced_dir == "A_TO_B":
                    try_direction(token_a, token_b, balance_a, a_price_cc)
                elif forced_dir == "B_TO_A":
                    try_direction(token_b, token_a, balance_b, b_price_cc)
                else:
                    # No history — try A→B first, then B→A
                    try_direction(token_a, token_b, balance_a, a_price_cc)
                    if not possible_directions:
                        try_direction(token_b, token_a, balance_b, b_price_cc)

                # 2. NO FALLBACK. If the forced direction has no balance, skip the pair.
                # Doing the same direction as the last swap creates a double-forward,
                # which loses round-trip rebate eligibility. Wait for the balance instead.

                if not possible_directions:
                    scan_stats["balance_rejected"] += 1
                    print(f"[SKIP] {token_a}/{token_b} — neither direction has ≥{MIN_SWAP_SIZE_CC} CC balance")
                    continue

                for direction in possible_directions:
                    sell = direction["sell"]
                    buy = direction["buy"]
                    req_sell = direction["amount"]
                    req_cc = req_sell * (a_price_cc if sell == token_a else b_price_cc)
                    
                     # Stack guard protects against over-accumulation over many swaps.
                    # For the last 3 swaps of the day, bypass it — the marginal
                    # accumulation is negligible and we don't want to miss a rebate.
                    if swaps_done < 22 and _net_cc_delta.get(buy, Decimal("0")) + req_cc > Decimal("200"):
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
                    if network_fee < lowest_fee:
                        lowest_fee = network_fee
                        best_opportunity = {
                            "sell": sell,
                            "buy": buy,
                            "amount": req_sell,
                            "dir": direction["dir"],
                            "net_min": net_min,
                            "net_max": net_max
                        }

                if rate_limit_hit:
                    break

            print(
                " [SCAN SUMMARY] "
                + " | ".join(f"{key}={value}" for key, value in scan_stats.items())
            )

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
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        print("Connection closed.")
    return False

if __name__ == "__main__":
    asyncio.run(main())