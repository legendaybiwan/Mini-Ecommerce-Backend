"""
Database access layer for the e-commerce backend.

The most important function here is `place_order`, which solves a classic
concurrency bug: two customers trying to buy the last unit of a product at
the same time.

The race condition, explained:
    Naive approach:
        1. SELECT stock_quantity FROM products WHERE id = ?   -- read: 1 left
        2. if stock_quantity >= requested_qty: allow purchase
        3. UPDATE products SET stock_quantity = stock_quantity - qty

    If two requests run step 1 at the same moment, BOTH see "1 left", BOTH
    pass the check in step 2, and BOTH proceed to step 3 - selling the same
    unit twice. This is the same fundamental bug as the unsafe rate limiter
    from the previous project (read-check-write without atomicity), just
    showing up in a different domain.

The fix used here: a single atomic UPDATE with the check embedded in the
WHERE clause:

    UPDATE products
    SET stock_quantity = stock_quantity - ?
    WHERE id = ? AND stock_quantity >= ?

This single SQL statement performs the check-and-decrement as one atomic
operation from the database's point of view - no other transaction can
interleave between "check" and "decrement" because there is no separate
"check" step. We then look at how many rows the UPDATE affected
(`cursor.rowcount`): if 0 rows were updated, the WHERE clause's stock check
failed, meaning insufficient stock - so we know to reject the order, with no
window for a second writer to sneak in.

This is combined with SQLite's transaction (BEGIN/COMMIT) so that, for
multi-item orders, either every line item's stock is decremented or none of
them are (atomicity across the whole order, not just one row).
"""

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).parent / "store.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables if they don't exist yet."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    """
    Yields a SQLite connection with foreign keys enabled and a busy timeout
    set, so concurrent writers wait briefly for locks instead of immediately
    failing with 'database is locked'.
    """
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class InsufficientStockError(Exception):
    def __init__(self, product_id: int, requested: int, available: int):
        self.product_id = product_id
        self.requested = requested
        self.available = available
        super().__init__(
            f"Product {product_id}: requested {requested}, only {available} available"
        )


class ProductNotFoundError(Exception):
    pass


@dataclass
class OrderItemRequest:
    product_id: int
    quantity: int


def create_product(name: str, price_cents: int, stock_quantity: int, db_path: Path = DB_PATH) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO products (name, price_cents, stock_quantity) VALUES (?, ?, ?)",
            (name, price_cents, stock_quantity),
        )
        conn.commit()
        return cur.lastrowid


def get_product(product_id: int, db_path: Path = DB_PATH) -> sqlite3.Row | None:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()


def list_products(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT * FROM products ORDER BY id").fetchall()


def place_order(items: list[OrderItemRequest], db_path: Path = DB_PATH) -> int:
    """
    Places an order for one or more products, atomically decrementing stock.

    Raises:
        ProductNotFoundError   if any product_id doesn't exist.
        InsufficientStockError if any item's requested quantity exceeds
                                available stock at the moment of purchase.

    On success, returns the new order's id. On any failure, NO stock is
    decremented for ANY item in the order (the whole order either fully
    succeeds or fully fails - this is the "atomicity across the whole
    order" guarantee mentioned in the module docstring).
    """
    with get_connection(db_path) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")  # acquire a write lock up front

            order_total_cents = 0
            line_items = []  # (product_id, quantity, price_at_purchase_cents)

            for item in items:
                product = conn.execute(
                    "SELECT id, price_cents, stock_quantity FROM products WHERE id = ?",
                    (item.product_id,),
                ).fetchone()

                if product is None:
                    raise ProductNotFoundError(f"Product {item.product_id} does not exist")

                # The atomic check-and-decrement: this is the line that
                # actually prevents the race condition. The WHERE clause's
                # stock_quantity >= ? check and the decrement happen as one
                # indivisible operation as far as other transactions are
                # concerned, because SQLite holds the write lock (acquired
                # by BEGIN IMMEDIATE above) for the duration of this
                # transaction - no other writer can run its own UPDATE on
                # this table until we COMMIT or ROLLBACK.
                cur = conn.execute(
                    """
                    UPDATE products
                    SET stock_quantity = stock_quantity - ?
                    WHERE id = ? AND stock_quantity >= ?
                    """,
                    (item.quantity, item.product_id, item.quantity),
                )

                if cur.rowcount == 0:
                    # The UPDATE matched 0 rows, meaning the WHERE clause's
                    # stock check failed (not enough stock right now).
                    raise InsufficientStockError(
                        product_id=item.product_id,
                        requested=item.quantity,
                        available=product["stock_quantity"],
                    )

                line_total = product["price_cents"] * item.quantity
                order_total_cents += line_total
                line_items.append((item.product_id, item.quantity, product["price_cents"]))

            order_cur = conn.execute(
                "INSERT INTO orders (status, total_cents) VALUES ('confirmed', ?)",
                (order_total_cents,),
            )
            order_id = order_cur.lastrowid

            for product_id, quantity, price_cents in line_items:
                conn.execute(
                    """
                    INSERT INTO order_items (order_id, product_id, quantity, price_at_purchase_cents)
                    VALUES (?, ?, ?, ?)
                    """,
                    (order_id, product_id, quantity, price_cents),
                )

            conn.commit()
            return order_id

        except (InsufficientStockError, ProductNotFoundError):
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise


def get_order(order_id: int, db_path: Path = DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            return None

        items = conn.execute(
            """
            SELECT oi.product_id, p.name, oi.quantity, oi.price_at_purchase_cents
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = ?
            """,
            (order_id,),
        ).fetchall()

        return {
            "id": order["id"],
            "status": order["status"],
            "total_cents": order["total_cents"],
            "created_at": order["created_at"],
            "items": [dict(item) for item in items],
        }
