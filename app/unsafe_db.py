"""
Deliberately UNSAFE version of order placement, using the naive
"read stock, check, then write" pattern that most beginner tutorials show.

This file exists ONLY to prove the bug that `db.place_order` fixes. Run
`tests/concurrency_test.py` to see both versions compared side by side.

DO NOT use this pattern in real code - it WILL oversell your last unit of
stock under concurrent load, as the test demonstrates.
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "unsafe_store.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_unsafe_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA_PATH.read_text())


def create_unsafe_product(name: str, price_cents: int, stock_quantity: int) -> int:
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cur = conn.execute(
            "INSERT INTO products (name, price_cents, stock_quantity) VALUES (?, ?, ?)",
            (name, price_cents, stock_quantity),
        )
        conn.commit()
        return cur.lastrowid


def get_unsafe_stock(product_id: int) -> int:
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        row = conn.execute(
            "SELECT stock_quantity FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        return row[0]


def unsafe_buy(product_id: int, quantity: int, simulate_delay: bool = False) -> bool:
    """
    The naive, UNSAFE pattern: separate read, check, and write steps with
    no atomicity between them. Returns True if the "purchase" succeeded
    from this thread's point of view.

    `simulate_delay`: when True, sleeps briefly between the read and the
    write to simulate realistic request processing time (e.g. running
    fraud checks, calling a payment gateway, rendering a confirmation -
    anything that takes a few milliseconds in a real API). This widens the
    race window so the bug reproduces reliably for demonstration purposes.
    In production this window exists anyway, just narrower and less
    convenient to demonstrate in a quick test - which makes it MORE
    dangerous, not less, since it fails intermittently and is hard to catch
    in testing.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        # Step 1: READ current stock.
        row = conn.execute(
            "SELECT stock_quantity FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        current_stock = row[0]

        # Step 2: CHECK in application code (not in the database).
        if current_stock < quantity:
            return False

        # <-- THE RACE WINDOW: between the check above and the write below,
        #     another thread can run steps 1-2 and ALSO decide it's safe to
        #     buy, because it read the same stale "current_stock" value.
        if simulate_delay:
            time.sleep(0.05)

        # Step 3: WRITE the new stock value, computed from the stale read.
        new_stock = current_stock - quantity
        conn.execute(
            "UPDATE products SET stock_quantity = ? WHERE id = ?",
            (new_stock, product_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()
