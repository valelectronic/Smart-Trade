"""SQLite storage for Cantex monitoring orders."""

import os
import sqlite3
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = Path(
    os.getenv("CANTEX_DB_PATH", str(BACKEND_DIR / "cantex.db"))
).resolve()

ORDER_COLUMNS = (
    "id",
    "market",
    "action",
    "sell_token",
    "buy_token",
    "route_token",  # <--- ADDED THIS
    "trigger_price_usd",
    "direction",
    "amount",
)


def get_connection():
    """Create a short-lived SQLite connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(DB_PATH),
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def init_db():
    """Create the orders table if it does not already exist."""
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                market TEXT NOT NULL,
                action TEXT NOT NULL,
                sell_token TEXT NOT NULL,
                buy_token TEXT NOT NULL,
                route_token TEXT,  -- <--- ADDED THIS
                trigger_price_usd TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount TEXT NOT NULL
            )
            """
        )


def add_order(order):
    """
    Insert an order.

    `order` may be a dictionary or another mapping containing all
    ORDER_COLUMNS fields. Returns the stored order as a dictionary.
    """
    # Allow route_token to be None/empty for direct swaps
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
        connection.execute(
            """
            INSERT INTO orders (
                id, market, action, sell_token, buy_token, 
                route_token, trigger_price_usd, direction, amount
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(values[column] for column in ORDER_COLUMNS),
        )

    return values


def get_all_orders():
    """Return every order, ordered by id."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id, market, action, sell_token, buy_token, 
                route_token, trigger_price_usd, direction, amount
            FROM orders
            ORDER BY id
            """
        ).fetchall()

    return [dict(row) for row in rows]


def delete_order(order_id):
    """Delete an order by id. Return True if an order was deleted."""
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM orders WHERE id = ?",
            (str(order_id),),
        )

    return cursor.rowcount > 0


# Create the database and table when this module is imported.
init_db()