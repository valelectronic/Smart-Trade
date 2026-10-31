"""PostgreSQL storage for Cantex monitoring orders (Neon)."""

import os
import psycopg2
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
    """Create the orders table if it does not already exist."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
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
        connection.commit()

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

    with get_connection() as connection:
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

def get_all_orders():
    """Return every order, ordered by id."""
    with get_connection() as connection:
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

def delete_order(order_id):
    """Delete an order by id. Return True if an order was deleted."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM orders WHERE id = %s",
                (str(order_id),),
            )
            rowcount = cursor.rowcount
        connection.commit()

    return rowcount > 0