"""Utility functions, account balances and CSV logging."""

import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from config import CSV_FIELDS, CSV_PATH


# ============================================================
# GENERAL UTILITIES
# ============================================================

def now_iso():
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    )


def dec(value, default=None):
    """Safely convert a value to Decimal."""
    try:
        if value is None:
            return default

        return Decimal(str(value))

    except (InvalidOperation, ValueError, TypeError):
        return default


def attr(obj, path, default=None):
    """Safely access a dotted attribute path."""
    try:
        for part in path.split("."):
            obj = getattr(obj, part)

        return obj

    except (AttributeError, TypeError):
        return default


# ============================================================
# ACCOUNT BALANCES
# ============================================================

def get_token_balance(account_info, symbol):
    """Return a token's unlocked balance from account_info.tokens."""
    tokens = getattr(account_info, "tokens", None) or []

    for item in tokens:
        if getattr(item, "instrument_symbol", None) == symbol:
            balance = getattr(item, "unlocked_amount", None)

            if balance is not None:
                return dec(balance)

    return None


def get_cc_balance(account_info):
    """Return the unlocked CC balance used for network fees."""
    return get_token_balance(account_info, "CC")


# ============================================================
# SDK INSTRUMENT IDENTIFICATION
# ============================================================

def symbol_for(instrument, instruments):
    """Resolve an SDK instrument object to its token symbol."""
    if instrument is None:
        return "UNKNOWN"

    for symbol, candidate in instruments.items():
        if candidate == instrument:
            return symbol

        candidate_admin = getattr(candidate, "admin", None)
        instrument_admin = getattr(instrument, "admin", None)

        candidate_id = getattr(candidate, "id", None)
        instrument_id = getattr(instrument, "id", None)

        if (
            candidate_admin is not None
            and candidate_id is not None
            and candidate_admin == instrument_admin
            and candidate_id == instrument_id
        ):
            return symbol

    return "UNKNOWN"


# ============================================================
# CSV SCHEMA MANAGEMENT
# ============================================================

_csv_schema_checked = False


def ensure_csv_schema():
    """Create or migrate the CSV header without discarding old rows."""
    global _csv_schema_checked

    if _csv_schema_checked:
        return

    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        _csv_schema_checked = True
        return

    try:
        with CSV_PATH.open(
            "r",
            newline="",
            encoding="utf-8",
        ) as handle:
            reader = csv.DictReader(handle)
            old_fields = reader.fieldnames or []

            if old_fields == CSV_FIELDS:
                _csv_schema_checked = True
                return

            old_rows = list(reader)

        temp_path = CSV_PATH.with_suffix(".migrating.csv")

        with temp_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=CSV_FIELDS,
                extrasaction="ignore",
            )

            writer.writeheader()

            for old_row in old_rows:
                writer.writerow({
                    field: old_row.get(field, "")
                    for field in CSV_FIELDS
                })

        temp_path.replace(CSV_PATH)

        _csv_schema_checked = True

        print(
            "[CSV] Schema updated. Previous rows preserved."
        )

    except Exception as exc:
        raise RuntimeError(
            f"Could not update CSV schema: {exc}"
        ) from exc


# ============================================================
# CSV EVENT LOGGING
# ============================================================

def log_csv(event, order=None, **values):
    """Log an event with the corresponding watchlist order details."""
    ensure_csv_schema()

    order = order or {}

    row = {
        field: ""
        for field in CSV_FIELDS
    }

    row.update({
        "timestamp_utc": now_iso(),
        "event": event,
        "order_id": order.get("id", ""),
        "market": order.get("market", ""),
        "action": order.get("action", ""),
        "trigger_price_usd": order.get(
            "trigger_price_usd",
            "",
        ),
        "direction": order.get("direction", ""),
        "sell_token": order.get("sell_token", ""),
        "buy_token": order.get("buy_token", ""),
        "sell_amount": order.get("amount", ""),
    })

    for key, value in values.items():
        if key in row:
            row[key] = (
                str(value)
                if value is not None
                else ""
            )

    new_file = (
        not CSV_PATH.exists()
        or CSV_PATH.stat().st_size == 0
    )

    with CSV_PATH.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_FIELDS,
        )

        if new_file:
            writer.writeheader()

        writer.writerow(row)
        handle.flush()