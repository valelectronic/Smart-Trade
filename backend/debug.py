import asyncio
import json
from decimal import Decimal
from pathlib import Path
from cantex_api import connect_sdk

async def test():
    client = await connect_sdk()
    try:
        # 1. Load instruments
        admin = await client.get_account_admin()
        instruments = {}
        for item in getattr(admin, "instruments", []) or []:
            s = getattr(item, "instrument_symbol", None)
            i = getattr(item, "instrument", None)
            if s and i is not None:
                instruments[str(s).upper()] = i

        # 2. Get current prices to calculate the 100 CC size
        prices_file = Path(__file__).parent / "prices.json"
        with prices_file.open("r") as f:
            prices = json.load(f)
        
        mod_cc = Decimal(str(prices.get("MOD-CC", 12.30)))
        trkx_cc = Decimal(str(prices.get("TRKXDEFI-CC", 1049.00)))

        # Calculate exactly what the bot is trying to swap (~100 CC)
        # Selling TRKXDEFI -> Buying MOD
        sell_amount_trkx = (Decimal("100.0") / trkx_cc).quantize(Decimal("0.0001"))
        # Selling MOD -> Buying TRKXDEFI
        sell_amount_mod = (Decimal("100.0") / mod_cc).quantize(Decimal("0.0001"))

        print(f"--- CURRENT PRICES ---")
        print(f"TRKXDEFI: {trkx_cc} CC | MOD: {mod_cc} CC")
        print(f"Bot will try to sell: {sell_amount_trkx} TRKXDEFI OR {sell_amount_mod} MOD\n")

        # 3. Test TRKXDEFI -> MOD
        try:
            quote_a = await client.get_swap_quote(
                sell_amount=sell_amount_trkx,
                sell_instrument=instruments["TRKXDEFI"],
                buy_instrument=instruments["MOD"],
            )
            fee_a = quote_a.fees.network_fee.amount
            fee_a_token = getattr(quote_a.fees.network_fee.instrument, "id", "UNKNOWN")
            pool_a = quote_a.fees.fee_percentage * 100
            print(f"QUOTE TRKXDEFI -> MOD (Selling {sell_amount_trkx}):")
            print(f"  Network Fee: {fee_a} {fee_a_token}")
            print(f"  Pool Fee: {pool_a}%")
        except Exception as e:
            print(f"QUOTE TRKXDEFI -> MOD FAILED: {e}")

        # 4. Test MOD -> TRKXDEFI
        try:
            quote_b = await client.get_swap_quote(
                sell_amount=sell_amount_mod,
                sell_instrument=instruments["MOD"],
                buy_instrument=instruments["TRKXDEFI"],
            )
            fee_b = quote_b.fees.network_fee.amount
            fee_b_token = getattr(quote_b.fees.network_fee.instrument, "id", "UNKNOWN")
            pool_b = quote_b.fees.fee_percentage * 100
            print(f"\nQUOTE MOD -> TRKXDEFI (Selling {sell_amount_mod}):")
            print(f"  Network Fee: {fee_b} {fee_b_token}")
            print(f"  Pool Fee: {pool_b}%")
        except Exception as e:
            print(f"QUOTE MOD -> TRKXDEFI FAILED: {e}")

        print(f"\n--- YOUR RULE ---")
        print(f"Target Network Fee: 0.45 - 0.52")
        print(f"Target Pool Fee: 0.10% - 0.17%")

    finally:
        await client.close()

asyncio.run(test())