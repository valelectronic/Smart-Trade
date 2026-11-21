"""PostgreSQL storage for the Cantex Rebate Hunter (Neon).

Tables used by the current Rebate Hunter:
- rebate_swaps: confirmed executions, uniquely identified by tx_id.
- hunter_rules: configured token-pair rules.
- hunter_control: persistent manual-intervention lock and daily allowance.

Legacy orders table/functions are intentionally not created or exposed here.
This code does not DROP an existing orders table or delete any existing data.
"""

import os
import json

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")
DAILY_SWAP_LIMIT = 25

if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL environment variable is missing. Check your .env file."
    )


def get_connection():
    """Create a short-lived PostgreSQL connection."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _ensure_control_row(cursor):
    """Ensure the singleton control row exists and reset allowance on UTC date change.

    The manual-intervention lock is deliberately preserved across daily resets.
    The initial allowance accounts for swaps already recorded today.
    """
    cursor.execute(
        """
        INSERT INTO hunter_control (
            id, manual_lock, lock_reason, lock_tx_id, locked_at,
            resolution_note, resolved_at, daily_date, swaps_remaining, updated_at
        )
        SELECT
            1, FALSE, NULL, NULL, NULL, NULL, NULL,
            (NOW() AT TIME ZONE 'UTC')::date,
            GREATEST(
                0,
                %s - (
                    SELECT COUNT(*)::integer
                    FROM rebate_swaps
                    WHERE created_at >=
                        (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                      AND created_at <
                        ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                         AT TIME ZONE 'UTC')
                )
            ),
            NOW()
        ON CONFLICT (id) DO NOTHING
        """,
        (DAILY_SWAP_LIMIT,),
    )

    # A new UTC day restores the allowance. Do not clear a manual lock.
    cursor.execute(
        """
        UPDATE hunter_control
        SET daily_date = (NOW() AT TIME ZONE 'UTC')::date,
            swaps_remaining = GREATEST(
                0,
                %s - (
                    SELECT COUNT(*)::integer
                    FROM rebate_swaps
                    WHERE created_at >=
                        (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                      AND created_at <
                        ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                         AT TIME ZONE 'UTC')
                )
            ),
            updated_at = NOW()
        WHERE id = 1
          AND daily_date <> (NOW() AT TIME ZONE 'UTC')::date
        """,
        (DAILY_SWAP_LIMIT,),
    )

    cursor.execute("SELECT * FROM hunter_control WHERE id = 1 FOR UPDATE")
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Could not initialise Hunter control state")
    return row


def init_db():
    """Create required tables and initialise the persistent control row."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rebate_swaps (
                    id SERIAL PRIMARY KEY,
                    tx_id TEXT UNIQUE NOT NULL,
                    pair TEXT NOT NULL,
                    sell_token TEXT NOT NULL,
                    buy_token TEXT NOT NULL,
                    sell_amount TEXT NOT NULL,
                    network_fee TEXT NOT NULL,
                    pool_fee TEXT NOT NULL,
                    usd_value TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hunter_rules (
                    id SERIAL PRIMARY KEY,
                    token_a TEXT NOT NULL,
                    token_b TEXT NOT NULL,
                    net_min TEXT NOT NULL,
                    net_max TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hunter_control (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    manual_lock BOOLEAN NOT NULL DEFAULT FALSE,
                    lock_reason TEXT,
                    lock_tx_id TEXT,
                    locked_at TIMESTAMPTZ,
                    resolution_note TEXT,
                    resolved_at TIMESTAMPTZ,
                    daily_date DATE NOT NULL,
                    swaps_remaining INTEGER NOT NULL
                        CHECK (swaps_remaining BETWEEN 0 AND 25),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS swap_transactions (
                    tx_id TEXT PRIMARY KEY,
                    sell_token TEXT NOT NULL,
                    buy_token TEXT NOT NULL,
                    sell_amount TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('PENDING', 'SUCCESSFUL', 'FAILED', 'UNKNOWN')
                    ),
                    submission_response JSONB,
                    pending_event JSONB,
                    execution_events JSONB NOT NULL DEFAULT '[]'::jsonb,
                    failure_event JSONB,
                    error TEXT,
                    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    confirmed_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_swap_transactions_status
                ON swap_transactions(status)
                """
            )
            _ensure_control_row(cursor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_hunter_control_state():
    """Return manual-lock state and today's remaining allowance."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            row = _ensure_control_row(cursor)
        connection.commit()
        return {
            "manual_intervention_required": bool(row["manual_lock"]),
            "lock_reason": row["lock_reason"],
            "lock_tx_id": row["lock_tx_id"],
            "locked_at": row["locked_at"].isoformat() if row["locked_at"] else None,
            "daily_date": row["daily_date"].isoformat(),
            "swaps_remaining": int(row["swaps_remaining"]),
            "daily_limit": DAILY_SWAP_LIMIT,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_daily_swaps_remaining(swaps_remaining: int):
    """Set the remaining allowance for today (0 to 25)."""
    if isinstance(swaps_remaining, bool) or not isinstance(swaps_remaining, int):
        raise ValueError("swaps_remaining must be an integer")
    if not 0 <= swaps_remaining <= DAILY_SWAP_LIMIT:
        raise ValueError(f"swaps_remaining must be between 0 and {DAILY_SWAP_LIMIT}")

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            _ensure_control_row(cursor)
            cursor.execute(
                """
                UPDATE hunter_control
                SET swaps_remaining = %s, updated_at = NOW()
                WHERE id = 1
                RETURNING daily_date, swaps_remaining, manual_lock
                """,
                (swaps_remaining,),
            )
            row = cursor.fetchone()
        connection.commit()
        return {
            "daily_date": row["daily_date"].isoformat(),
            "swaps_remaining": int(row["swaps_remaining"]),
            "daily_limit": DAILY_SWAP_LIMIT,
            "manual_intervention_required": bool(row["manual_lock"]),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def activate_manual_intervention_lock(reason: str, tx_id: str | None = None):
    """Persist a critical lock. Ordinary market/quote rejections do not belong here."""
    reason = str(reason or "").strip()
    if len(reason) < 5:
        raise ValueError("A meaningful manual-intervention reason is required")

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            _ensure_control_row(cursor)
            cursor.execute(
                """
                UPDATE hunter_control
                SET manual_lock = TRUE,
                    lock_reason = COALESCE(lock_reason, %s),
                    lock_tx_id = COALESCE(lock_tx_id, %s),
                    locked_at = COALESCE(locked_at, NOW()),
                    updated_at = NOW()
                WHERE id = 1
                RETURNING manual_lock, lock_reason, lock_tx_id, locked_at
                """,
                (reason[:2000], str(tx_id)[:300] if tx_id else None),
            )
            row = cursor.fetchone()
        connection.commit()
        return {
            "manual_intervention_required": bool(row["manual_lock"]),
            "lock_reason": row["lock_reason"],
            "lock_tx_id": row["lock_tx_id"],
            "locked_at": row["locked_at"].isoformat() if row["locked_at"] else None,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def clear_manual_intervention_lock(resolution_note: str):
    """Clear a lock only after an explicit owner resolution note."""
    note = str(resolution_note or "").strip()
    if len(note) < 5:
        raise ValueError("Resolution note must contain at least 5 characters")

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            _ensure_control_row(cursor)
            cursor.execute(
                """
                UPDATE hunter_control
                SET manual_lock = FALSE,
                    lock_reason = NULL,
                    lock_tx_id = NULL,
                    locked_at = NULL,
                    resolution_note = %s,
                    resolved_at = NOW(),
                    updated_at = NOW()
                WHERE id = 1 AND manual_lock = TRUE
                RETURNING id
                """,
                (note[:1000],),
            )
            cleared = cursor.fetchone() is not None
        connection.commit()
        return {"cleared": cleared}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_today_swap_count():
    """Count recorded swaps between today's UTC midnight and tomorrow's."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM rebate_swaps
                WHERE created_at >=
                    (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                  AND created_at <
                    ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                     AT TIME ZONE 'UTC')
                """
            )
            row = cursor.fetchone()
            return int(row["count"]) if row else 0
    finally:
        connection.close()


def log_successful_swap(
    tx_id, pair, sell_token, buy_token,
    sell_amount, net_fee, pool_fee, usd_value
):
    """Record a Cantex-confirmed execution exactly once.

    Returns True if inserted, False if tx_id was already present.
    A newly inserted execution decrements today's remaining allowance in the
    SAME database transaction. A duplicate never decrements it again.
    """
    tx_id = str(tx_id or "").strip()
    if not tx_id:
        raise ValueError("tx_id cannot be empty")

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO rebate_swaps (
                    tx_id, pair, sell_token, buy_token,
                    sell_amount, network_fee, pool_fee, usd_value
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tx_id) DO NOTHING
                RETURNING id
                """,
                (
                    tx_id, str(pair), str(sell_token), str(buy_token),
                    str(sell_amount), str(net_fee), str(pool_fee), str(usd_value),
                ),
            )
            inserted = cursor.fetchone() is not None

            if inserted:
                # Initialise/reset and lock the control row before decrementing.
                _ensure_control_row(cursor)
                cursor.execute(
                    """
                    UPDATE hunter_control
                    SET swaps_remaining = GREATEST(0, swaps_remaining - 1),
                        updated_at = NOW()
                    WHERE id = 1
                    """
                )

        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_today_fees_spent():
    """Return network fees spent today in CC, using UTC day boundaries."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(CAST(network_fee AS DECIMAL)), 0) AS total_fees
                FROM rebate_swaps
                WHERE created_at >=
                    (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                  AND created_at <
                    ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                     AT TIME ZONE 'UTC')
                """
            )
            row = cursor.fetchone()
            return float(row["total_fees"]) if row else 0.0
    finally:
        connection.close()


def delete_hunter_rule(rule_id: int):
    """Delete a Hunter rule by ID."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM hunter_rules WHERE id = %s", (rule_id,))
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    finally:
        connection.close()


def get_active_hunter_rules():
    """Return active Hunter rules (token pairs only)."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT token_a, token_b
                FROM hunter_rules
                WHERE is_active = TRUE
                ORDER BY id
                """
            )
            return cursor.fetchall()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Transaction lifecycle tracking. These records are separate from
# rebate_swaps: only a verified SUCCESSFUL transaction enters rebate_swaps.
# ---------------------------------------------------------------------------

def create_pending_swap(tx_id, sell_token, buy_token, sell_amount, submission_response=None):
    tx_id = str(tx_id or "").strip()
    if not tx_id:
        raise ValueError("tx_id cannot be empty")
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO swap_transactions
                    (tx_id, sell_token, buy_token, sell_amount, status, submission_response)
                VALUES (%s, %s, %s, %s, 'PENDING', %s::jsonb)
                ON CONFLICT (tx_id) DO NOTHING
                RETURNING tx_id
                """,
                (tx_id, str(sell_token), str(buy_token), str(sell_amount),
                 json.dumps(submission_response, default=str) if submission_response is not None else None),
            )
            inserted = cursor.fetchone() is not None
        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_swap_pending_event(tx_id, pending_event):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE swap_transactions
                SET pending_event = %s::jsonb, updated_at = NOW()
                WHERE tx_id = %s AND status IN ('PENDING', 'UNKNOWN')
                RETURNING tx_id
                """,
                (json.dumps(pending_event, default=str), str(tx_id)),
            )
            found = cursor.fetchone() is not None
        connection.commit()
        return found
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def add_swap_execution_event(tx_id, execution_event):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE swap_transactions
                SET execution_events = execution_events || %s::jsonb,
                    updated_at = NOW()
                WHERE tx_id = %s AND status IN ('PENDING', 'UNKNOWN')
                RETURNING tx_id
                """,
                (json.dumps([execution_event], default=str), str(tx_id)),
            )
            found = cursor.fetchone() is not None
        connection.commit()
        return found
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_swap_transaction_status(tx_id, status, error=None):
    status = str(status).upper()
    if status not in {"PENDING", "SUCCESSFUL", "FAILED", "UNKNOWN"}:
        raise ValueError("Invalid swap transaction status")
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE swap_transactions
                SET status = %s,
                    error = %s,
                    confirmed_at = CASE
                        WHEN %s IN ('SUCCESSFUL', 'FAILED') THEN NOW()
                        ELSE confirmed_at
                    END,
                    updated_at = NOW()
                WHERE tx_id = %s
                RETURNING tx_id
                """,
                (status, error, status, str(tx_id)),
            )
            found = cursor.fetchone() is not None
        connection.commit()
        return found
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_swap_failure_event(tx_id, failure_event):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE swap_transactions
                SET failure_event = %s::jsonb, updated_at = NOW()
                WHERE tx_id = %s AND status IN ('PENDING', 'UNKNOWN')
                RETURNING tx_id
                """,
                (json.dumps(failure_event, default=str), str(tx_id)),
            )
            found = cursor.fetchone() is not None
        connection.commit()
        return found
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_unresolved_swaps():
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM swap_transactions
                WHERE status IN ('PENDING', 'UNKNOWN')
                ORDER BY submitted_at ASC
                """
            )
            return cursor.fetchall()
    finally:
        connection.close()


def get_swap_transaction(tx_id):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM swap_transactions WHERE tx_id = %s",
                (str(tx_id),),
            )
            return cursor.fetchone()
    finally:
        connection.close()
