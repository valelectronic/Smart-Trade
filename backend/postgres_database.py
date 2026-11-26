"""Multi-user PostgreSQL persistence layer for Cantex Rebate Hunter.

This module contains only user-scoped account, Hunter, credential, and transaction
operations. Public market data is intentionally kept outside this database layer.

Schema changes are managed by the separately reviewed cutover SQL. init_db()
validates the expected schema; it does not create or mutate production tables.
"""
from __future__ import annotations

import json
import os
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

MAX_DAILY_SWAP_LIMIT = 100


def get_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is missing.")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def init_db():
    """Validate the completed multi-user schema without performing DDL."""
    required_columns = {
        "users": {"id", "username", "password_hash", "is_active", "created_at"},
        "user_credentials": {
            "user_id", "credentials_ciphertext", "credentials_nonce",
            "wrapped_dek", "wrapped_dek_nonce", "key_version",
        },
        "hunter_control": {
            "user_id", "manual_lock", "lock_reason", "lock_tx_id", "locked_at",
            "resolution_note", "resolved_at", "daily_date", "daily_swap_limit",
            "swaps_remaining", "updated_at",
        },
        "hunter_rules": {
            "id", "user_id", "token_a", "token_b", "net_min", "net_max",
            "is_active", "created_at",
        },
        "rebate_swaps": {
            "id", "user_id", "tx_id", "pair", "sell_token", "buy_token",
            "sell_amount", "network_fee", "pool_fee", "usd_value", "created_at",
        },
        "swap_transactions": {
            "tx_id", "user_id", "pair_key", "sell_token", "buy_token",
            "sell_amount", "status", "submission_response", "pending_event",
            "execution_events", "failure_event", "error", "submitted_at",
            "confirmed_at", "updated_at", "wallet_address", "last_balance_snapshot",
        },
        "swap_execution_events": {
            "id", "user_id", "event_id", "event_type", "payload", "created_at",
        },
        "hunter_resume_intents": {
            "user_id", "auto_resume", "exhausted_date", "updated_at",
        },
    }
    user_owned_tables = (
        "user_credentials", "hunter_control", "hunter_rules",
        "rebate_swaps", "swap_transactions", "swap_execution_events",
        "hunter_resume_intents",
    )

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (list(required_columns),),
            )
            actual_columns = {}
            nullability = {}
            for row in cursor.fetchall():
                actual_columns.setdefault(row["table_name"], set()).add(row["column_name"])
                nullability[(row["table_name"], row["column_name"])] = row["is_nullable"]

            problems = []
            for table, expected in required_columns.items():
                missing = expected - actual_columns.get(table, set())
                if missing:
                    problems.append(f"{table}: missing columns {', '.join(sorted(missing))}")

            if problems:
                raise RuntimeError(
                    "Multi-user database schema is not cutover-ready: "
                    + "; ".join(problems)
                )

            # Every user-owned row must have an owner before the application starts.
            nullable_owner_columns = [
                table for table in user_owned_tables
                if nullability.get((table, "user_id")) != "NO"
            ]
            if nullable_owner_columns:
                problems.append(
                    "user_id must be NOT NULL on: " + ", ".join(nullable_owner_columns)
                )

            # hunter_control is keyed only by user_id; the legacy singleton id must be gone.
            if "id" in actual_columns.get("hunter_control", set()):
                problems.append("hunter_control: obsolete id column must be dropped")

            cursor.execute(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.hunter_control'::regclass
                  AND contype = 'c'
                  AND conname = 'hunter_control_daily_swap_limit_check'
                LIMIT 1
                """
            )
            if cursor.fetchone() is None:
                problems.append(
                    "hunter_control must have CHECK constraint "
                    "hunter_control_daily_swap_limit_check (0 <= daily_swap_limit <= 100)"
                )

            expected_constraints = (
                ("hunter_control", "p", "PRIMARY KEY (user_id)",
                 "hunter_control must have PRIMARY KEY (user_id)"),
                ("swap_transactions", "p", "PRIMARY KEY (user_id, tx_id)",
                 "swap_transactions must have PRIMARY KEY (user_id, tx_id)"),
                ("rebate_swaps", "u", "UNIQUE (user_id, tx_id)",
                 "rebate_swaps must have UNIQUE (user_id, tx_id)"),
                ("hunter_rules", "u", "UNIQUE (user_id, token_a, token_b)",
                 "hunter_rules must have UNIQUE (user_id, token_a, token_b)"),
                ("hunter_resume_intents", "p", "PRIMARY KEY (user_id)",
                 "hunter_resume_intents must have PRIMARY KEY (user_id)"),
            )
            for table, constraint_type, definition, message in expected_constraints:
                cursor.execute(
                    """
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = %s::regclass
                      AND contype = %s
                      AND pg_get_constraintdef(oid) = %s
                    LIMIT 1
                    """,
                    (f"public.{table}", constraint_type, definition),
                )
                if cursor.fetchone() is None:
                    problems.append(message)

            cursor.execute(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.hunter_resume_intents'::regclass
                  AND contype = 'f'
                  AND pg_get_constraintdef(oid) ILIKE
                      'FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE'
                LIMIT 1
                """
            )
            if cursor.fetchone() is None:
                problems.append(
                    "hunter_resume_intents must reference users(id) with ON DELETE CASCADE"
                )

            # Verify ownership foreign keys for every user-owned table.
            for table in user_owned_tables:
                cursor.execute(
                    """
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = %s::regclass
                      AND contype = 'f'
                      AND pg_get_constraintdef(oid) LIKE
                          'FOREIGN KEY (user_id) REFERENCES users(id)%%'
                    LIMIT 1
                    """,
                    (f"public.{table}",),
                )
                if cursor.fetchone() is None:
                    problems.append(f"{table}: missing foreign key user_id -> users(id)")

            if problems:
                raise RuntimeError(
                    "Multi-user database schema is not cutover-ready: "
                    + "; ".join(problems)
                )
    finally:
        connection.close()



def create_user(username: str, password_hash: str):
    username = str(username or '').strip()
    password_hash = str(password_hash or '').strip()
    if not username:
        raise ValueError('username cannot be empty')
    if not password_hash:
        raise ValueError('password_hash cannot be empty')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                INSERT INTO public.users (username, password_hash)\n                VALUES (%s, %s)\n                ON CONFLICT ((LOWER(username))) DO NOTHING\n                RETURNING id, username, is_active, created_at\n                ', (username, password_hash))
            row = cursor.fetchone()
        connection.commit()
        return dict(row) if row else None
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_user_auth_record(username: str):
    username = str(username or '').strip()
    if not username:
        return None
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                SELECT id, username, password_hash, is_active, created_at\n                FROM public.users\n                WHERE LOWER(username) = LOWER(%s)\n                LIMIT 1\n                ', (username,))
            row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        connection.close()

def get_user_by_id(user_id):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                SELECT id, username, is_active, created_at\n                FROM public.users\n                WHERE id = %s\n                ', (str(user_id),))
            row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        connection.close()

def list_active_users():
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                SELECT id, username, created_at\n                FROM public.users\n                WHERE is_active = TRUE\n                ORDER BY created_at ASC, id ASC\n                ')
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()

def set_user_active(user_id, is_active: bool):
    if not isinstance(is_active, bool):
        raise ValueError('is_active must be a boolean')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                UPDATE public.users\n                SET is_active = %s\n                WHERE id = %s\n                RETURNING id, username, is_active, created_at\n                ', (is_active, str(user_id)))
            row = cursor.fetchone()
        connection.commit()
        return dict(row) if row else None
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def save_user_credentials(user_id, credentials_ciphertext: bytes, credentials_nonce: bytes, wrapped_dek: bytes, wrapped_dek_nonce: bytes, key_version: str):
    binary_fields = {'credentials_ciphertext': credentials_ciphertext, 'credentials_nonce': credentials_nonce, 'wrapped_dek': wrapped_dek, 'wrapped_dek_nonce': wrapped_dek_nonce}
    for field_name, value in binary_fields.items():
        if not isinstance(value, (bytes, bytearray, memoryview)) or not value:
            raise ValueError(f'{field_name} must be non-empty bytes')
    key_version = str(key_version or '').strip()
    if not key_version:
        raise ValueError('key_version cannot be empty')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                INSERT INTO public.user_credentials (\n                    user_id,\n                    credentials_ciphertext,\n                    credentials_nonce,\n                    wrapped_dek,\n                    wrapped_dek_nonce,\n                    key_version,\n                    created_at,\n                    updated_at\n                )\n                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())\n                ON CONFLICT (user_id) DO UPDATE SET\n                    credentials_ciphertext = EXCLUDED.credentials_ciphertext,\n                    credentials_nonce = EXCLUDED.credentials_nonce,\n                    wrapped_dek = EXCLUDED.wrapped_dek,\n                    wrapped_dek_nonce = EXCLUDED.wrapped_dek_nonce,\n                    key_version = EXCLUDED.key_version,\n                    updated_at = NOW()\n                RETURNING user_id, key_version, created_at, updated_at\n                ', (str(user_id), bytes(credentials_ciphertext), bytes(credentials_nonce), bytes(wrapped_dek), bytes(wrapped_dek_nonce), key_version))
            row = cursor.fetchone()
        connection.commit()
        return dict(row) if row else None
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_user_credentials_ciphertext(user_id):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                SELECT\n                    user_id,\n                    credentials_ciphertext,\n                    credentials_nonce,\n                    wrapped_dek,\n                    wrapped_dek_nonce,\n                    key_version,\n                    created_at,\n                    updated_at\n                FROM public.user_credentials\n                WHERE user_id = %s\n                ', (str(user_id),))
            row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        connection.close()

def delete_user_credentials(user_id):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM public.user_credentials WHERE user_id = %s', (str(user_id),))
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def _require_user_id(user_id):
    value = str(user_id or '').strip()
    if not value:
        raise ValueError('user_id is required')
    return value

def _ensure_user_control_row(cursor, user_id):
    """Create/lock user state; requires the per-user daily_swap_limit column."""
    user_id = _require_user_id(user_id)
    cursor.execute(
        """
        INSERT INTO hunter_control (
            user_id, manual_lock, lock_reason, lock_tx_id, locked_at,
            resolution_note, resolved_at, daily_date, daily_swap_limit,
            swaps_remaining, updated_at
        )
        SELECT
            %s, FALSE, NULL, NULL, NULL, NULL, NULL,
            (NOW() AT TIME ZONE 'UTC')::date,
            25,
            GREATEST(
                0,
                25 - (
                    SELECT COUNT(*)::integer
                    FROM rebate_swaps
                    WHERE user_id = %s
                      AND created_at >=
                          (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                      AND created_at <
                          ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                           AT TIME ZONE 'UTC')
                )
            ),
            NOW()
        ON CONFLICT (user_id) DO NOTHING
        """,
        (user_id, user_id),
    )
    cursor.execute(
        """
        UPDATE hunter_control
        SET daily_date = (NOW() AT TIME ZONE 'UTC')::date,
            swaps_remaining = GREATEST(
                0,
                daily_swap_limit - (
                    SELECT COUNT(*)::integer
                    FROM rebate_swaps
                    WHERE user_id = %s
                      AND created_at >=
                          (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                      AND created_at <
                          ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                           AT TIME ZONE 'UTC')
                )
            ),
            updated_at = NOW()
        WHERE user_id = %s
          AND daily_date <> (NOW() AT TIME ZONE 'UTC')::date
        """,
        (user_id, user_id),
    )
    cursor.execute(
        'SELECT * FROM hunter_control WHERE user_id = %s FOR UPDATE',
        (user_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError('Could not initialise user Hunter control state')
    return row

def get_user_hunter_control_state(user_id):
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            row = _ensure_user_control_row(cursor, user_id)
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM rebate_swaps
                WHERE user_id = %s
                  AND created_at >=
                      (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                  AND created_at <
                      ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                       AT TIME ZONE 'UTC')
                """,
                (user_id,),
            )
            count_row = cursor.fetchone()
        connection.commit()
        return {
            'manual_intervention_required': bool(row['manual_lock']),
            'lock_reason': row['lock_reason'],
            'lock_tx_id': row['lock_tx_id'],
            'locked_at': row['locked_at'].isoformat() if row['locked_at'] else None,
            'daily_date': row['daily_date'].isoformat(),
            'daily_swap_limit': int(row['daily_swap_limit']),
            'daily_limit': int(row['daily_swap_limit']),
            'swaps_completed': int(count_row['count']) if count_row else 0,
            'swaps_remaining': int(row['swaps_remaining']),
            'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_user_hunter_resume_intent(user_id, exhausted_date=None):
    """Persist that a user wants the Hunter active across daily resets/restarts.

    exhausted_date is NULL while the Hunter is intended to be active (or queued).
    When the daily target is reached, it is set to that UTC date. The supervisor
    may resume only after the database's daily_date advances beyond that date.
    """
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO hunter_resume_intents (
                    user_id, auto_resume, exhausted_date, updated_at
                )
                VALUES (%s, TRUE, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE
                SET auto_resume = TRUE,
                    exhausted_date = EXCLUDED.exhausted_date,
                    updated_at = NOW()
                """,
                (user_id, exhausted_date),
            )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_user_hunter_resume_intents():
    """Return all persisted auto-resume intents for startup/recovery."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, exhausted_date, updated_at
                FROM hunter_resume_intents
                WHERE auto_resume = TRUE
                ORDER BY updated_at ASC, user_id ASC
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "user_id": str(row["user_id"]),
                "exhausted_date": (
                    row["exhausted_date"].isoformat()
                    if row["exhausted_date"] else None
                ),
                "updated_at": (
                    row["updated_at"].isoformat()
                    if row["updated_at"] else None
                ),
            }
            for row in rows
        ]
    finally:
        connection.close()


def clear_user_hunter_resume_intent(user_id):
    """Remove a user's automatic-resume intent after an explicit stop."""
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM hunter_resume_intents WHERE user_id = %s",
                (user_id,),
            )
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_user_daily_swap_limit(user_id, new_limit: int):
    """Set a user's daily Hunter target; manual external swaps are not counted."""
    user_id = _require_user_id(user_id)
    if isinstance(new_limit, bool) or not isinstance(new_limit, int):
        raise ValueError('daily_swap_limit must be an integer')
    if not 0 <= new_limit <= MAX_DAILY_SWAP_LIMIT:
        raise ValueError(
            f'daily_swap_limit must be between 0 and {MAX_DAILY_SWAP_LIMIT}'
        )

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            _ensure_user_control_row(cursor, user_id)
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM rebate_swaps
                WHERE user_id = %s
                  AND created_at >=
                      (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                  AND created_at <
                      ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')
                       AT TIME ZONE 'UTC')
                """,
                (user_id,),
            )
            count_row = cursor.fetchone()
            completed = int(count_row['count']) if count_row else 0
            cursor.execute(
                """
                UPDATE hunter_control
                SET daily_swap_limit = %s,
                    swaps_remaining = GREATEST(0, %s - %s),
                    updated_at = NOW()
                WHERE user_id = %s
                RETURNING daily_date, daily_swap_limit, swaps_remaining, manual_lock
                """,
                (new_limit, new_limit, completed, user_id),
            )
            row = cursor.fetchone()

        connection.commit()
        return {
            'daily_date': row['daily_date'].isoformat(),
            'daily_swap_limit': int(row['daily_swap_limit']),
            'daily_limit': int(row['daily_swap_limit']),
            'swaps_completed': completed,
            'swaps_remaining': int(row['swaps_remaining']),
            'manual_intervention_required': bool(row['manual_lock']),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def activate_user_manual_intervention_lock(user_id, reason: str, tx_id: str | None=None):
    user_id = _require_user_id(user_id)
    reason = str(reason or '').strip()
    if len(reason) < 5:
        raise ValueError('A meaningful manual-intervention reason is required')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            _ensure_user_control_row(cursor, user_id)
            cursor.execute('\n                UPDATE hunter_control\n                SET manual_lock = TRUE,\n                    lock_reason = COALESCE(lock_reason, %s),\n                    lock_tx_id = COALESCE(lock_tx_id, %s),\n                    locked_at = COALESCE(locked_at, NOW()),\n                    updated_at = NOW()\n                WHERE user_id = %s\n                RETURNING manual_lock, lock_reason, lock_tx_id, locked_at\n                ', (reason[:2000], str(tx_id)[:300] if tx_id else None, user_id))
            row = cursor.fetchone()
        connection.commit()
        return {'manual_intervention_required': bool(row['manual_lock']), 'lock_reason': row['lock_reason'], 'lock_tx_id': row['lock_tx_id'], 'locked_at': row['locked_at'].isoformat() if row['locked_at'] else None}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def clear_user_manual_intervention_lock(user_id, resolution_note: str):
    user_id = _require_user_id(user_id)
    note = str(resolution_note or '').strip()
    if len(note) < 5:
        raise ValueError('Resolution note must contain at least 5 characters')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            _ensure_user_control_row(cursor, user_id)
            cursor.execute('\n                UPDATE hunter_control\n                SET manual_lock = FALSE,\n                    lock_reason = NULL,\n                    lock_tx_id = NULL,\n                    locked_at = NULL,\n                    resolution_note = %s,\n                    resolved_at = NOW(),\n                    updated_at = NOW()\n                WHERE user_id = %s AND manual_lock = TRUE\n                RETURNING user_id\n                ', (note[:1000], user_id))
            cleared = cursor.fetchone() is not None
        connection.commit()
        return {'cleared': cleared}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_user_today_swap_count(user_id):
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("\n                SELECT COUNT(*) AS count\n                FROM rebate_swaps\n                WHERE user_id = %s\n                  AND created_at >=\n                      (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')\n                  AND created_at <\n                      ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')\n                       AT TIME ZONE 'UTC')\n                ", (user_id,))
            row = cursor.fetchone()
            return int(row['count']) if row else 0
    finally:
        connection.close()

def log_user_successful_swap(
    user_id, tx_id, pair, sell_token, buy_token,
    sell_amount, net_fee, pool_fee, usd_value,
):
    user_id = _require_user_id(user_id)
    tx_id = str(tx_id or "").strip()
    if not tx_id:
        raise ValueError("tx_id cannot be empty")

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            # Keep this ordering: the row lock must remain held through the swap
            # insert and allowance decrement until commit. Moving this call after
            # the insert, or committing between these operations, can corrupt the
            # per-user daily allowance under concurrent/retried logging.
            _ensure_user_control_row(cursor, user_id)
            cursor.execute(
                """
                INSERT INTO rebate_swaps (
                    user_id, tx_id, pair, sell_token, buy_token,
                    sell_amount, network_fee, pool_fee, usd_value
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, tx_id) DO NOTHING
                RETURNING id
                """,
                (
                    user_id, tx_id, str(pair), str(sell_token), str(buy_token),
                    str(sell_amount), str(net_fee), str(pool_fee), str(usd_value),
                ),
            )
            inserted = cursor.fetchone() is not None

            if inserted:
                cursor.execute(
                    """
                    UPDATE hunter_control
                    SET swaps_remaining = GREATEST(0, swaps_remaining - 1),
                        updated_at = NOW()
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_user_today_fees_spent(user_id):
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("\n                SELECT COALESCE(SUM(CAST(network_fee AS DECIMAL)), 0) AS total_fees\n                FROM rebate_swaps\n                WHERE user_id = %s\n                  AND created_at >=\n                      (date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')\n                  AND created_at <\n                      ((date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day')\n                       AT TIME ZONE 'UTC')\n                ", (user_id,))
            row = cursor.fetchone()
            return float(row['total_fees']) if row else 0.0
    finally:
        connection.close()

def create_user_hunter_rule(user_id, token_a, token_b, net_min, net_max, is_active=True):
    user_id = _require_user_id(user_id)
    token_a = str(token_a or '').strip()
    token_b = str(token_b or '').strip()
    if not token_a or not token_b or token_a.upper() == token_b.upper():
        raise ValueError('A valid, distinct token pair is required')
    if not isinstance(is_active, bool):
        raise ValueError('is_active must be a boolean')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                INSERT INTO hunter_rules (\n                    user_id, token_a, token_b, net_min, net_max, is_active\n                )\n                VALUES (%s, %s, %s, %s, %s, %s)\n                ON CONFLICT (user_id, token_a, token_b) DO UPDATE SET\n                    net_min = EXCLUDED.net_min,\n                    net_max = EXCLUDED.net_max,\n                    is_active = EXCLUDED.is_active\n                RETURNING id, user_id, token_a, token_b, net_min, net_max, is_active, created_at\n                ', (user_id, token_a, token_b, str(net_min), str(net_max), is_active))
            row = cursor.fetchone()
        connection.commit()
        return dict(row) if row else None
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_user_hunter_rules(user_id, active_only=False):
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                SELECT id, user_id, token_a, token_b, net_min, net_max,\n                       is_active, created_at\n                FROM hunter_rules\n                WHERE user_id = %s\n                  AND (%s = FALSE OR is_active = TRUE)\n                ORDER BY id\n                ', (user_id, bool(active_only)))
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()

def get_user_active_hunter_rules(user_id):
    return get_user_hunter_rules(user_id, active_only=True)

def delete_user_hunter_rule(user_id, rule_id: int):
    user_id = _require_user_id(user_id)
    if isinstance(rule_id, bool) or not isinstance(rule_id, int):
        raise ValueError('rule_id must be an integer')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM hunter_rules WHERE user_id = %s AND id = %s', (user_id, rule_id))
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def create_user_pending_swap(user_id, tx_id, sell_token, buy_token, sell_amount, submission_response=None, wallet_address=None, balance_snapshot=None):
    user_id = _require_user_id(user_id)
    tx_id = str(tx_id or '').strip()
    if not tx_id:
        raise ValueError('tx_id cannot be empty')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("\n                INSERT INTO swap_transactions (\n                    user_id, tx_id, pair_key, sell_token, buy_token,\n                    sell_amount, status, submission_response,\n                    wallet_address, last_balance_snapshot\n                )\n                VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', %s::jsonb, %s, %s::jsonb)\n                ON CONFLICT (user_id, tx_id) DO NOTHING\n                RETURNING tx_id\n                ", (user_id, tx_id, f'{str(sell_token).upper()}::{str(buy_token).upper()}', str(sell_token), str(buy_token), str(sell_amount), json.dumps(submission_response, default=str) if submission_response is not None else None, str(wallet_address) if wallet_address else None, json.dumps(balance_snapshot, default=str) if balance_snapshot is not None else None))
            inserted = cursor.fetchone() is not None
        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def _update_user_swap_json_field(user_id, tx_id, column_name, payload, allowed_statuses):
    user_id = _require_user_id(user_id)
    allowed_columns = {'pending_event': 'pending_event', 'execution_events': 'execution_events', 'failure_event': 'failure_event'}
    if column_name not in allowed_columns:
        raise ValueError('Invalid transaction JSON field')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            if column_name == 'execution_events':
                sql = '\n                    UPDATE swap_transactions\n                    SET execution_events = execution_events || %s::jsonb,\n                        updated_at = NOW()\n                    WHERE user_id = %s AND tx_id = %s\n                      AND status = ANY(%s)\n                    RETURNING tx_id\n                '
                value = json.dumps([payload], default=str)
            else:
                sql = f'\n                    UPDATE swap_transactions\n                    SET {allowed_columns[column_name]} = %s::jsonb,\n                        updated_at = NOW()\n                    WHERE user_id = %s AND tx_id = %s\n                      AND status = ANY(%s)\n                    RETURNING tx_id\n                '
                value = json.dumps(payload, default=str)
            cursor.execute(sql, (value, user_id, str(tx_id), list(allowed_statuses)))
            found = cursor.fetchone() is not None
        connection.commit()
        return found
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def update_user_swap_pending_event(user_id, tx_id, pending_event):
    return _update_user_swap_json_field(user_id, tx_id, 'pending_event', pending_event, ('PENDING', 'UNKNOWN'))

def add_user_swap_execution_event(user_id, tx_id, execution_event):
    return _update_user_swap_json_field(user_id, tx_id, 'execution_events', execution_event, ('PENDING', 'UNKNOWN'))

def update_user_swap_failure_event(user_id, tx_id, failure_event):
    return _update_user_swap_json_field(user_id, tx_id, 'failure_event', failure_event, ('PENDING', 'UNKNOWN'))

def update_user_swap_transaction_status(user_id, tx_id, status, error=None):
    user_id = _require_user_id(user_id)
    status = str(status).upper()
    if status not in {'PENDING', 'SUCCESSFUL', 'FAILED', 'UNKNOWN'}:
        raise ValueError('Invalid swap transaction status')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("\n                UPDATE swap_transactions\n                SET status = %s,\n                    error = %s,\n                    confirmed_at = CASE\n                        WHEN %s IN ('SUCCESSFUL', 'FAILED') THEN NOW()\n                        ELSE confirmed_at\n                    END,\n                    updated_at = NOW()\n                WHERE user_id = %s AND tx_id = %s\n                RETURNING tx_id\n                ", (status, error, status, user_id, str(tx_id)))
            found = cursor.fetchone() is not None
        connection.commit()
        return found
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_user_unresolved_swaps(user_id):
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("\n                SELECT *\n                FROM swap_transactions\n                WHERE user_id = %s AND status IN ('PENDING', 'UNKNOWN')\n                ORDER BY submitted_at ASC\n                ", (user_id,))
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()

def get_user_swap_transaction(user_id, tx_id):
    user_id = _require_user_id(user_id)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                SELECT *\n                FROM swap_transactions\n                WHERE user_id = %s AND tx_id = %s\n                ', (user_id, str(tx_id)))
            row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        connection.close()

def log_user_execution_event(user_id, event):
    user_id = _require_user_id(user_id)
    event = event if isinstance(event, dict) else {'raw': str(event)}
    data = event.get('data') or {}
    event_id = data.get('id') or event.get('id')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                INSERT INTO swap_execution_events (\n                    user_id, event_id, event_type, payload\n                )\n                VALUES (%s, %s, %s, %s::jsonb)\n                ', (user_id, str(event_id) if event_id is not None else None, str(event.get('type') or 'Pool.SwapExecuted'), json.dumps(event, default=str)))
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_user_rebate_history(user_id, limit=100, offset=0):
    user_id = _require_user_id(user_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or (not 1 <= limit <= 1000):
        raise ValueError('limit must be an integer between 1 and 1000')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('offset must be a non-negative integer')
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('\n                SELECT *\n                FROM rebate_swaps\n                WHERE user_id = %s\n                ORDER BY created_at DESC\n                LIMIT %s OFFSET %s\n                ', (user_id, limit, offset))
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()

def cleanup_old_execution_events(retention_hours: int = 24, batch_size: int = 500) -> int:
    """Delete old execution-event rows only when their exact transaction is terminal.

    Events without an explicit tx_id in payload (top-level or payload.data) are
    deliberately retained. Events for PENDING/UNKNOWN or missing transactions
    are also retained so cleanup cannot erase evidence needed for reconciliation.
    """
    if isinstance(retention_hours, bool) or not isinstance(retention_hours, int):
        raise ValueError("retention_hours must be an integer")
    if retention_hours < 1:
        raise ValueError("retention_hours must be at least 1")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("batch_size must be an integer")
    if not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be between 1 and 10000")

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH eligible AS (
                    SELECT event.id
                    FROM public.swap_execution_events AS event
                    JOIN public.swap_transactions AS tx
                      ON tx.user_id = event.user_id
                     AND tx.tx_id = COALESCE(
                         NULLIF(event.payload->>'tx_id', ''),
                         NULLIF(event.payload->'data'->>'tx_id', '')
                     )
                    WHERE event.created_at < NOW() - (%s * INTERVAL '1 hour')
                      AND tx.status IN ('SUCCESSFUL', 'FAILED')
                    ORDER BY event.created_at ASC, event.id ASC
                    LIMIT %s
                    FOR UPDATE OF event SKIP LOCKED
                )
                DELETE FROM public.swap_execution_events AS event
                USING eligible
                WHERE event.id = eligible.id
                """,
                (retention_hours, batch_size),
            )
            deleted = cursor.rowcount
        connection.commit()
        return int(deleted)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

