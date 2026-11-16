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

def canonical_pair_key(token_a: str, token_b: str) -> str:
    a, b = sorted([token_a, token_b])
    return f"{a}::{b}"

# --- CONFIGURATION ---
TARGET_SWAP_SIZE_CC = Decimal("100.5")
MIN_SWAP_SIZE_CC = Decimal("100.0")
MAX_SWAP_SIZE_CC = Decimal("102.0")
POOL_MIN = Decimal("0.10")
POOL_MAX = Decimal("0.17")
DAILY_LIMIT = 25
POST_SWAP_COOLDOWN = 15  
RED_LIGHT_WAIT = 10      
MIN_CC_BALANCE = Decimal("5.0") 

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

PRICES_FILE = Path(__file__).parent / "prices.json"
STATUS_FILE = Path(__file__).parent / "hunter_status.json"

swaps_done = 0
execution_lock = asyncio.Lock() 
_last_swap_direction = {} 
_disabled_pairs = {} 
_net_cc_delta = {} 

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

async def check_and_swap(client, instruments, sell_token, buy_token, net_min, net_max, sell_amount_tokens):
    global swaps_done
    
    if swaps_done >= DAILY_LIMIT:
        return False
        
    account = await client.get_account_info()
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

    network_fee = Decimal("0")
    pool_fee_percent = Decimal("0")
    sdk_est_time = 0.0
    
    fees_obj = getattr(quote, "fees", None)
    if fees_obj:
        net_fee_obj = getattr(fees_obj, "network_fee", None)
        if net_fee_obj:
            network_fee = Decimal(str(getattr(net_fee_obj, "amount", 0)))
        pool_fee_percent = Decimal(str(getattr(fees_obj, "fee_percentage", 0))) * 100
        
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
                    return False

                end_time = time.perf_counter()
                exec_time = end_time - start_time
                tx_id = result.get('id')
                print(f" SWAP SUCCESS in {exec_time:.2f}s! TX ID: {tx_id}")
                
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
                
                swaps_done += 1
                print(f" Logged swap to database. Total today: {swaps_done}/{DAILY_LIMIT}")
                
                pair_key = canonical_pair_key(sell_token, buy_token)
                _last_swap_direction[pair_key] = "A_TO_B" if sell_token < buy_token else "B_TO_A"
                
                _net_cc_delta[sell_token] = _net_cc_delta.get(sell_token, Decimal("0")) - (sell_amount_tokens * cc_price)
                _net_cc_delta[buy_token] = _net_cc_delta.get(buy_token, Decimal("0")) + (sell_amount_tokens * cc_price)
                
                await asyncio.sleep(POST_SWAP_COOLDOWN) 
                return True
            except Exception as e:
                print(f" Swap Failed: {e}")
                return False
    else:
        return False

async def main():
    global swaps_done
    print(" Starting Rebate Hunter (Dynamic 100-102 CC + Strict Ping-Pong)...")
    
    swaps_done = get_today_swap_count()
    print(f" Today's swaps already completed: {swaps_done}/{DAILY_LIMIT}")
    
    if swaps_done >= DAILY_LIMIT:
        print(" Daily limit already reached. Bot will sleep.")
        return True

    # --- RESTORE PING-PONG MEMORY FROM DB ---
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT sell_token, buy_token FROM rebate_swaps ORDER BY created_at DESC LIMIT 1")
                last_swap = cur.fetchone()
                if last_swap:
                    pair_key = canonical_pair_key(last_swap["sell_token"], last_swap["buy_token"])
                    _last_swap_direction[pair_key] = "A_TO_B" if last_swap["sell_token"] < last_swap["buy_token"] else "B_TO_A"
                    print(f" Restored memory: Last swap was {last_swap['sell_token']} -> {last_swap['buy_token']}")
        finally:
            conn.close()
    except Exception as e:
        print(f" Could not restore ping-pong memory: {e}")

    print(" Fetching active hunter rules from database...")
    TOKEN_PAIRS = []
    try:
        async with httpx.AsyncClient() as client_http:
            res = await client_http.get(f"{API_URL}/api/hunter-rules")
        if res.status_code == 200:
            rules_data = res.json()
            TOKEN_PAIRS = [{"a": r["token_a"], "b": r["token_b"], "net_min": Decimal(str(r.get("net_min", "0.45"))), "net_max": Decimal(str(r.get("net_max", "0.60")))} for r in rules_data]
            print(f" Loaded {len(TOKEN_PAIRS)} rules: {TOKEN_PAIRS}")
        else:
            print(f" API Error: {res.status_code}")
            return False
    except Exception as e:
        print(f" Failed to connect to API: {e}")
        return False
        
    if not TOKEN_PAIRS:
        print(" No active rules found. Exiting.")
        return False

    client = await connect_sdk()
    
    try:
        account_admin = await client.get_account_admin()
        instruments = {}
        for item in getattr(account_admin, "instruments", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            instrument = getattr(item, "instrument", None)
            if symbol and instrument is not None:
                instruments[str(symbol).upper()] = instrument

        while swaps_done < DAILY_LIMIT:
            best_opportunity = None
            lowest_fee = Decimal("999.0")

            for pair in TOKEN_PAIRS:
                token_a = pair["a"]
                token_b = pair["b"]
                net_min = pair["net_min"]
                net_max = pair["net_max"]
                
                pair_key = canonical_pair_key(token_a, token_b)
                
                if pair_key in _disabled_pairs and time.time() < _disabled_pairs[pair_key]:
                    continue
                
                account = await client.get_account_info()
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
                    continue

                # --- STRICT PING-PONG & 100-102 CC BOUNDS ---
                last_dir = _last_swap_direction.get(pair_key)
                
                forced_dir = None
                if last_dir == "A_TO_B":
                    forced_dir = "B_TO_A"
                elif last_dir == "B_TO_A":
                    forced_dir = "A_TO_B"

                possible_directions = []
                
                if forced_dir == "A_TO_B" or (forced_dir is None and (balance_a * a_price_cc) >= MIN_SWAP_SIZE_CC):
                    amount_to_sell = balance_a
                    if (amount_to_sell * a_price_cc) > MAX_SWAP_SIZE_CC:
                        amount_to_sell = (MAX_SWAP_SIZE_CC / a_price_cc).quantize(Decimal("0.0001"))
                    
                    if (amount_to_sell * a_price_cc) >= MIN_SWAP_SIZE_CC:
                        possible_directions.append({"sell": token_a, "buy": token_b, "amount": amount_to_sell, "dir": "A_TO_B"})
                        
                elif forced_dir == "B_TO_A" or (forced_dir is None and (balance_b * b_price_cc) >= MIN_SWAP_SIZE_CC):
                    amount_to_sell = balance_b
                    if (amount_to_sell * b_price_cc) > MAX_SWAP_SIZE_CC:
                        amount_to_sell = (MAX_SWAP_SIZE_CC / b_price_cc).quantize(Decimal("0.0001"))
                        
                    if (amount_to_sell * b_price_cc) >= MIN_SWAP_SIZE_CC:
                        possible_directions.append({"sell": token_b, "buy": token_a, "amount": amount_to_sell, "dir": "B_TO_A"})

                if not possible_directions:
                    continue

                for direction in possible_directions:
                    sell = direction["sell"]
                    buy = direction["buy"]
                    req_sell = direction["amount"]
                    req_cc = req_sell * (a_price_cc if sell == token_a else b_price_cc)
                    
                    if _net_cc_delta.get(buy, Decimal("0")) + req_cc > Decimal("200"):
                        print(f" [STACK GUARD] Skipping {sell}->{buy}. Too much {buy} stacked.")
                        continue

                    try:
                        quote = await client.get_swap_quote(
                            sell_amount=req_sell,
                            sell_instrument=instruments[sell],
                            buy_instrument=instruments[buy],
                        )
                        network_fee = Decimal(str(getattr(getattr(quote, "fees", None), "network_fee", None).amount))
                        
                        if net_min <= network_fee <= net_max and network_fee < lowest_fee:
                            lowest_fee = network_fee
                            best_opportunity = {
                                "sell": sell,
                                "buy": buy,
                                "amount": req_sell,
                                "dir": direction["dir"],
                                "net_min": net_min,
                                "net_max": net_max
                            }
                    except Exception as e:
                        error_str = str(e).lower()
                        if "429" in error_str or "rate limit" in error_str:
                            print(" RATE LIMIT in scan. Cooling down 30s...")
                            await asyncio.sleep(30)
                            break
                        
                        print(f" Error on {pair_key}: {e}. Disabling pair for 5 minutes.")
                        _disabled_pairs[pair_key] = time.time() + 300
                        break

            if best_opportunity:
                print(f" Best fee found: {lowest_fee} CC for {best_opportunity['sell']} -> {best_opportunity['buy']}")
                executed = await check_and_swap(
                    client, instruments, 
                    best_opportunity["sell"], best_opportunity["buy"], 
                    best_opportunity["net_min"], best_opportunity["net_max"],
                    best_opportunity["amount"]
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