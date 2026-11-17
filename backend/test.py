import asyncio
from decimal import Decimal
from cantex_api import connect_sdk

async def test():
    client = await connect_sdk()
    try:
        admin = await client.get_account_admin()
        instruments = {}
        for item in getattr(admin, "instruments", []) or []:
            s = getattr(item, "instrument_symbol", None)
            i = getattr(item, "instrument", None)
            if s and i is not None:
                instruments[str(s).upper()] = i

        print("instruments keys:", list(instruments.keys()))
        print("has TRKXDEFI:", "TRKXDEFI" in instruments)
        print("has MOD:", "MOD" in instruments)

        if "TRKXDEFI" not in instruments or "MOD" not in instruments:
            print(">>> MISSING INSTRUMENT — this is the bug. Fix instruments map.")
            return

        q = await client.get_swap_quote(
            sell_amount=Decimal("0.1"),
            sell_instrument=instruments["TRKXDEFI"],
            buy_instrument=instruments["MOD"],
        )
        print("quote fees:", q.fees)
        net = getattr(getattr(q, "fees", None), "network_fee", None)
        print("network_fee:", getattr(net, "amount", None))
        print("fee_percentage:", getattr(getattr(q, "fees", None), "fee_percentage", None))
    finally:
        close = getattr(client, "close", None)
        if close:
            r = close()
            if asyncio.iscoroutine(r):
                await r

asyncio.run(test())