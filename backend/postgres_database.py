"""PostgreSQL storage for Cantex monitoring orders and rebate swaps (Neon)."""

import os
import psycopg2
from datetime import datetime, timezone
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is missing. Check your .env file.")

ORDER_COLUMNS = (
    "id",
    "market",
    "action",
    "sell_token",
    "buy_token",
    "route_token",
    "trigger_price_usd",
    "direction",
    "amount",
)

def get_connection():
    """Create a short-lived PostgreSQL connection via PgBouncer."""
    connection = psycopg2.connect(
        DATABASE_URL, 
        cursor_factory=RealDictCursor, 
    )
    return connection

def init_db():
    """Create all tables if they do not already exist."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            # 1. Orders table (kept for backward compatibility, unused in Lean Mode)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    action TEXT NOT NULL,
                    sell_token TEXT NOT NULL,
                    buy_token TEXT NOT NULL,
                    route_token TEXT,
                    trigger_price_usd TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    amount TEXT NOT NULL
                )
                """
            )
            # 2. Rebate Swaps table
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
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # 3. Hunter Rules table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hunter_rules (
                    id SERIAL PRIMARY KEY,
                    token_a TEXT NOT NULL,
                    token_b TEXT NOT NULL,
                    net_min TEXT NOT NULL,
                    net_max TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        connection.commit()
    finally:
        connection.close()

def get_today_swap_count():
    """Returns the number of successful swaps executed today."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) as count 
                FROM rebate_swaps 
                WHERE created_at >= CURRENT_DATE
                """
            )
            row = cursor.fetchone()
            return row["count"] if row else 0
    finally:
        connection.close()

def log_successful_swap(tx_id, pair, sell_token, buy_token, sell_amount, net_fee, pool_fee, usd_value):
    """Logs a successful swap to the database."""
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
                """,
                (
                    str(tx_id), str(pair), str(sell_token), str(buy_token),
                    str(sell_amount), str(net_fee), str(pool_fee), str(usd_value)
                ),
            )
        connection.commit()
    finally:
        connection.close()

def get_today_fees_spent():
    """Returns the total network fees spent today in CC."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(CAST(network_fee AS DECIMAL)), 0) as total_fees
                FROM rebate_swaps 
                WHERE created_at >= CURRENT_DATE
                """
            )
            row = cursor.fetchone()
            return float(row["total_fees"]) if row else 0.0
    finally:
        connection.close()

def delete_hunter_rule(rule_id: int):
    """Delete a hunter rule by ID."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM hunter_rules WHERE id = %s", (rule_id,))
            rowcount = cursor.rowcount
        connection.commit()
        return rowcount > 0
    finally:
        connection.close()

def get_active_hunter_rules():
    """Returns a list of active hunter rules (tokens only)."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT token_a, token_b FROM hunter_rules WHERE is_active = TRUE"
            )
            return cursor.fetchall()
    finally:
        connection.close()

# Legacy order functions (kept for backward compatibility, unused in Lean Mode)
def add_order(order):
    """Insert an order into PostgreSQL."""
    missing = [
        column
        for column in ORDER_COLUMNS
        if column not in order or (order[column] is None and column != "route_token")
    ]
    if missing:
        raise ValueError(f"Missing required order fields: {missing}")

    values = {
        column: (str(order[column]).strip() if order[column] is not None else "")
        for column in ORDER_COLUMNS
    }

    if not values["id"]:
        raise ValueError("Order id cannot be empty")

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO orders (
                    id, market, action, sell_token, buy_token, 
                    route_token, trigger_price_usd, direction, amount
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                tuple(values[column] for column in ORDER_COLUMNS),
            )
        connection.commit()
        return values
    finally:
        connection.close()

def get_all_orders():
    """Return every order, ordered by id."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, market, action, sell_token, buy_token, 
                    route_token, trigger_price_usd, direction, amount
                FROM orders
                ORDER BY id
                """
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()

def delete_order(order_id):
    """Delete an order by id. Return True if an order was deleted."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM orders WHERE id = %s",
                (str(order_id),),
            )
            rowcount = cursor.rowcount
        connection.commit()
        return rowcount > 0
    finally:
        connection.close()