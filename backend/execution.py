import asyncio
from decimal import Decimal
from cantex_api import connect_sdk

# We are testing MOD exactly as you requested.
TOKEN_A = "MOD"
TOKEN_B = "TF-USDT" 
TEST_AMOUNT = Decimal("1")

async def main():
    print(f"Connecting to Cantex SDK...")
    client = await connect_sdk()
    
    try:
        account_admin = await client.get_account_admin()
        instruments = {}
        for item in getattr(account_admin, "instruments", []) or []:
            symbol = getattr(item, "instrument_symbol", None)
            instrument = getattr(item, "instrument", None)
            if symbol and instrument is not None:
                instruments[str(symbol).upper()] = instrument

        if TOKEN_A not in instruments or TOKEN_B not in instruments:
            print(f"❌ Error: Could not find {TOKEN_A} or {TOKEN_B}.")
            return

        print(f"Fetching quote for {TEST_AMOUNT} {TOKEN_A} -> {TOKEN_B}...")
        
        quote = await client.get_swap_quote(
            sell_amount=TEST_AMOUNT,
            sell_instrument=instruments[TOKEN_A],
            buy_instrument=instruments[TOKEN_B],
        )

        print("🟢 Quote received. Executing swap...")
        
        # We added sell_amount to fix the previous error
        result = await client.swap(
            sell_amount=TEST_AMOUNT,
            sell_instrument=instruments[TOKEN_A],
            buy_instrument=instruments[TOKEN_B],
        )
        
        print("✅ SWAP EXECUTED SUCCESSFULLY!")
        print(f"📄 Transaction Result: {result}")

    except Exception as e:
        print(f"❌ Swap Failed: {e}")
    
    finally:
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        print("Connection closed.")

if __name__ == "__main__":
    asyncio.run(main())