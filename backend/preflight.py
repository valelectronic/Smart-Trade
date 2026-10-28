"""Cantex quote and pre-flight checks.

This module NEVER submits or executes a swap.
"""

import time
import uuid
from decimal import Decimal

from config import MAX_SLIPPAGE_PERCENT
from helpers import (
    attr,
    dec,
    get_cc_balance,
    get_token_balance,
    log_csv,
    now_iso,
    symbol_for,
)


MAX_TRADE_POOL_PERCENT = Decimal("1.0")
MAX_EXECUTION_SECONDS = Decimal("30")


def _read(obj, path, default=None):
    current = obj
    try:
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part)
            if current is None:
                return default
        return current
    except (AttributeError, TypeError):
        return default


def _first_decimal(obj, paths):
    for path in paths:
        value = dec(_read(obj, path))
        if value is not None:
            return value
    return None


def _first_value(obj, paths):
    for path in paths:
        value = _read(obj, path)
        if value is not None:
            return value
    return None


def _log(event, order, **values):
    payload = {
        "market": order.get("market", ""),
        "trigger_price": order.get("trigger_price_usd", ""),
        "trigger_direction": order.get("direction", ""),
        "sell_token": order.get("sell_token", ""),
        "buy_token": order.get("buy_token", ""),
        "sell_amount": order.get("amount", ""),
        "order_id": order.get("id", ""),
    }
    payload.update(values)
    log_csv(event, **payload)


def _execution_seconds(quote):
    milliseconds = _first_decimal(quote, ["estimated_execution_time_ms", "execution_time_ms"])
    if milliseconds is not None:
        return milliseconds / Decimal("1000")

    return _first_decimal(quote, [
        "estimated_time_seconds",
        "estimated_execution_time_seconds",
        "execution_time_seconds",
        "estimated_execution_seconds",
    ])


def _pool_token_symbol(value, instruments):
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        upper = cleaned.upper()
        if upper in {"CC", "DSO::AMULET"}:
            return "CC"
        try:
            mapped = symbol_for(value, instruments)
            if mapped:
                mapped_upper = str(mapped).upper()
                if mapped_upper in {"CC", "DSO::AMULET"}:
                    return "CC"
                return mapped_upper
        except Exception:
            pass
        return cleaned.upper()

    try:
        mapped = symbol_for(value, instruments)
        if mapped:
            mapped_upper = str(mapped).upper()
            if mapped_upper in {"CC", "DSO::AMULET"}:
                return "CC"
            return mapped_upper
    except Exception:
        pass

    for path in ("symbol", "ticker", "code", "name", "identifier", "id"):
        item = _read(value, path)
        if item is not None:
            return _pool_token_symbol(item, instruments)
    return None


def _pool_size(quote, instruments):
    pool_size_usd = _first_decimal(quote, ["pool_size_usd", "liquidity_usd"])
    if pool_size_usd is not None:
        return pool_size_usd, "USD"

    pool_size = _first_decimal(quote, [
        "pool_size.amount", "pool_size", "pool.size.amount", "pool.size",
    ])

    pool_instrument = _first_value(quote, [
        "pool_size.instrument", "pool_size.instrument_symbol", "pool.token_symbol", "pool.instrument",
    ])
    pool_token = _pool_token_symbol(pool_instrument, instruments)

    if pool_size is None:
        return None, pool_token
    return pool_size, pool_token


def _trade_value_usd(order, usd_price, cc_usd_rate):
    amount = dec(order["amount"])
    market_parts = str(order["market"]).upper().split("-")

    if amount is None or amount <= 0:
        return None

    sell_token = str(order["sell_token"]).upper()

    if market_parts and sell_token == market_parts[0]:
        return amount * usd_price
    if sell_token == "CC" and cc_usd_rate is not None:
        return amount * cc_usd_rate
    if sell_token in {"USDCX", "USDC"}:
        return amount
    return None


def _pool_share(order, quote, usd_price, cc_usd_rate, instruments):
    pool_size, pool_token = _pool_size(quote, instruments)
    if pool_size is None or pool_size <= 0:
        return None, pool_size, pool_token

    if pool_token == "USD":
        trade_usd = _trade_value_usd(order, usd_price, cc_usd_rate)
        if trade_usd is None:
            return None, pool_size, pool_token
        return trade_usd / pool_size * Decimal("100"), pool_size, pool_token

    sell_token = str(order["sell_token"]).upper()
    if pool_token == sell_token:
        amount = dec(order["amount"])
        if amount is None:
            return None, pool_size, pool_token
        return amount / pool_size * Decimal("100"), pool_size, pool_token

    if pool_token == "CC":
        if sell_token == "CC":
            trade_cc = dec(order["amount"])
        else:
            trade_usd = _trade_value_usd(order, usd_price, cc_usd_rate)
            trade_cc = (trade_usd / cc_usd_rate) if trade_usd and cc_usd_rate and cc_usd_rate > 0 else None
        if trade_cc is None:
            return None, pool_size, pool_token
        return trade_cc / pool_size * Decimal("100"), pool_size, pool_token

    return None, pool_size, pool_token


async def quote_and_preflight(
    client,
    instruments,
    order,
    price,
    usd_price,
    cc_usd_rate,
    trigger_time,
    telegram,
):
    started = time.perf_counter()
    quote_time = now_iso()

    sell_token = str(order["sell_token"]).upper()
    buy_token = str(order["buy_token"]).upper()
    sell_amount = dec(order["amount"])
    market = str(order["market"])

    try:
        if sell_amount is None or sell_amount <= 0:
            raise ValueError("Order amount must be greater than zero")

        if sell_token not in instruments or buy_token not in instruments:
            raise RuntimeError(f"Missing SDK instrument: {sell_token} or {buy_token}")

        # SDK handles the bundled routing automatically
        quote = await client.get_swap_quote(
            sell_amount=sell_amount,
            sell_instrument=instruments[sell_token],
            buy_instrument=instruments[buy_token],
        )

        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        output = _first_decimal(quote, ["returned.amount", "returned_amount", "expected_output"])
        slippage_raw = _first_decimal(quote, ["prices.slippage", "slippage"])
        slippage_percent = (slippage_raw * Decimal("100")) if slippage_raw is not None else None

        network_fee = _first_decimal(quote, ["fees.network_fee.amount", "network_fee.amount", "network_fee"])
        fee_instrument = _first_value(quote, ["fees.network_fee.instrument", "network_fee.instrument"])
        fee_token = symbol_for(fee_instrument, instruments) if fee_instrument is not None else None

        liquidity_fee = _first_decimal(quote, ["fees.amount_liquidity", "fees.liquidity_fee.amount", "fees.pool_fee.amount"])
        admin_fee = _first_decimal(quote, ["fees.amount_admin", "fees.admin_fee.amount"])
        
        pool_fee = Decimal("0")
        if liquidity_fee is not None or admin_fee is not None:
            pool_fee = (liquidity_fee or Decimal("0")) + (admin_fee or Decimal("0"))

        execution_seconds = _execution_seconds(quote)
        pool_share_percent, pool_size, pool_size_token = _pool_share(order, quote, usd_price, cc_usd_rate, instruments)

        account = await client.get_account_info()
        cc_balance = get_cc_balance(account)
        sell_balance = get_token_balance(account, sell_token)

        common = {
            "price": price,
            "usd_price": usd_price,
            "cc_usd_rate": cc_usd_rate,
            "expected_output": output,
            "slippage_sdk_value": slippage_raw,
            "slippage_percent": slippage_percent,
            "max_slippage_percent": MAX_SLIPPAGE_PERCENT,
            "network_fee_amount": network_fee,
            "network_fee_token": fee_token,
            "pool_fee_amount": pool_fee,
            "admin_fee_amount": admin_fee,
            "liquidity_fee_amount": liquidity_fee,
            "pool_size": pool_size,
            "pool_size_token": pool_size_token,
            "pool_share_percent": pool_share_percent,
            "execution_time_seconds": execution_seconds,
            "cc_balance": cc_balance,
            "sell_token_balance": sell_balance,
            "trigger_to_quote_ms": latency_ms,
            "quote_status": "QUOTE_RETURNED",
        }

        failure = None

        if slippage_percent is None:
            failure = ("SLIPPAGE_UNAVAILABLE", "Slippage was missing from the quote")
        elif slippage_percent >= MAX_SLIPPAGE_PERCENT:
            failure = ("SLIPPAGE_TOO_HIGH", f"Slippage {slippage_percent}% is not below {MAX_SLIPPAGE_PERCENT}%")
        elif pool_share_percent is None:
            failure = ("POOL_SIZE_UNAVAILABLE", "Pool size or its denomination could not be verified")
        elif pool_share_percent > MAX_TRADE_POOL_PERCENT:
            failure = ("TRADE_TOO_LARGE_FOR_POOL", f"Trade represents {pool_share_percent:.4f}% of the pool")
        elif execution_seconds is None:
            failure = ("EXECUTION_TIME_UNAVAILABLE", "Estimated execution time is missing from the quote")
        elif execution_seconds > MAX_EXECUTION_SECONDS:
            failure = ("EXECUTION_TIME_TOO_HIGH", f"Estimated execution time is {execution_seconds}s")
        elif output is None or output <= 0:
            failure = ("EXPECTED_OUTPUT_UNAVAILABLE", "Expected output is missing or invalid")
        elif network_fee is None or fee_token is None:
            failure = ("NETWORK_FEE_UNAVAILABLE", "Network fee amount or denomination is unavailable")
        elif fee_token.upper() != "CC":
            failure = ("NETWORK_FEE_TOKEN_UNEXPECTED", f"Expected CC network fee, received {fee_token}")
        elif cc_balance is None or cc_balance < network_fee:
            failure = ("INSUFFICIENT_CC_FOR_GAS", f"CC balance {cc_balance} is below network fee {network_fee} CC")
        elif sell_balance is None or sell_balance < sell_amount:
            failure = ("INSUFFICIENT_SELL_TOKEN_BALANCE", f"Available {sell_token}: {sell_balance}; required: {sell_amount}")

        if failure:
            event, reason = failure
            _log(event, order, **{**common, "preflight_status": "FAILED", "details": reason})
            print(f"[PREFLIGHT FAILED] {order['id']}: {reason}")
            return

        # ==========================================
        # THE ACCURATE MATH (REAL MONEY)
        # ==========================================
        
        # 1. Convert Network Fee (CC) to USD
        network_fee_usd = Decimal("0")
        if network_fee and cc_usd_rate and cc_usd_rate > 0:
            network_fee_usd = network_fee * cc_usd_rate

        # 2. Convert Pool Fee to USD (Pool fee is in buy_token)
        pool_fee_usd = Decimal("0")
        if pool_fee and pool_fee > 0:
            if buy_token in {"USDCX", "USDC"}:
                pool_fee_usd = pool_fee # 1 USDCX = 1 USD
            elif buy_token == "CC" and cc_usd_rate:
                pool_fee_usd = pool_fee * cc_usd_rate
            else:
                # For other tokens, we don't have the exact USD rate. 
                # But since pool_fee is already deducted from output in the SDK, 
                # we only need to subtract it from the final net output, not in USD.
                pass
        
        # 3. Total USD Fees
        total_fees_usd = network_fee_usd + pool_fee_usd

        # 4. Calculate Net Output
        net_output = output
        if pool_fee and pool_fee > 0:
            net_output -= pool_fee # Deduct pool fee directly from token output
        
        # Deduct network fee from net output
        if network_fee_usd > 0:
            if buy_token in {"USDCX", "USDC"}:
                net_output -= network_fee_usd # 1 USD = 1 USDCX
            elif buy_token == "CC":
                net_output -= network_fee
            else:
                # For any other token, we subtract the USD equivalent from the token value
                # This is an estimate, but 100% accurate if the token is pegged.
                net_output_usd = net_output * usd_price
                net_output_usd -= network_fee_usd
                net_output = net_output_usd / usd_price if usd_price > 0 else net_output

        if net_output <= 0:
            _log("NET_OUTPUT_NOT_POSITIVE", order, **{**common, "net_output": net_output, "preflight_status": "FAILED"})
            print(f"[PREFLIGHT FAILED] {order['id']}: calculated net output is not positive after fees")
            return

        callback_id = uuid.uuid4().hex[:12]
        pool_size_text = f"{pool_size} {pool_size_token}"

        message = (
            f"🚨 TRADE READY: {order['action']} {sell_amount} {sell_token}\n"
            f"Market: {market}\n"
            f"Target: ${dec(order['trigger_price_usd'])} ({order['direction']})\n"
            f"Price at trigger: ${usd_price:.6f}\n\n"
            "✅ All pre-flight safety checks passed\n"
            f"• Expected Output: {output:.6f} {buy_token}\n"
            f"• Pool size: {pool_size_text}\n"
            f"• Slippage: {slippage_percent:.4f}%\n"
            f"• Execution time: {execution_seconds}s\n"
            f"• Network Fee: {network_fee} CC\n"
            f"• CC Balance: {cc_balance} CC\n"
            f"• **Net Output: {net_output:.6f} {buy_token}**\n\n"
            "⚠️ QUOTE ONLY — NO SWAP EXECUTED."
        )

        print("\n" + "=" * 65)
        print(message)
        print(f"Quote latency: {latency_ms} ms")
        print("=" * 65)

        telegram_status = "SKIPPED_NOT_CONFIGURED"
        if telegram is not None:
            try:
                await telegram.send_trade_ready(message, callback_id)
                telegram_status = "SENT"
                print(f"[TELEGRAM] Alert sent for {order['id']}.")
            except Exception as exc:
                telegram_status = "SEND_FAILED"
                print(f"[TELEGRAM WARNING] {type(exc).__name__}: {exc}")

        _log(
            "TRADE_READY",
            order,
            **{
                **common,
                "net_output": net_output,
                "total_fees_usd": total_fees_usd,
                "quote_status": "QUOTE_RETURNED; NOT AN EXECUTION",
                "preflight_status": "PASSED",
                "telegram_status": telegram_status,
                "details": f"trigger_timestamp={trigger_time}; quote_timestamp={quote_time}; callback_id={callback_id}; execution=NOT_EXECUTED",
            },
        )

    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        print(f"[QUOTE ERROR] {order.get('id')}: {type(exc).__name__}: {exc}")
        _log("QUOTE_ERROR", order, trigger_to_quote_ms=latency_ms, quote_status="FAILED", preflight_status="NOT_RUN", details=str(exc))