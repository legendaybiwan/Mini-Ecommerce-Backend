-- Schema for the mini e-commerce backend.
--
-- Design notes (the kind of thing you should be able to explain in an
-- interview):
--
-- 1. `stock_quantity` lives on `products`, not in a separate "inventory"
--    table. For a system this size, splitting it out adds a join with no
--    real benefit - you'd only split it if inventory needed its own
--    lifecycle (e.g. per-warehouse stock, reservations, etc).
--
-- 2. `orders` and `order_items` are normalized (1-to-many): an order can
--    contain multiple products, and each line item records the price AT
--    THE TIME OF ORDER (price_at_purchase), not a live foreign key lookup
--    into `products.price`. This matters because product prices change
--    over time - if you joined live, a past order's total would silently
--    change when you updated a product's price today. Recording price at
--    purchase time is what real e-commerce systems do (Amazon, Flipkart
--    included) for exactly this reason.
--
-- 3. CHECK (stock_quantity >= 0) is a DB-level guardrail. Even if a bug in
--    application code tried to push stock negative, the database itself
--    refuses the write. Defense in depth: don't rely on app logic alone
--    to enforce invariants that matter for correctness.
--
-- 4. Indexes: `idx_order_items_order_id` speeds up "get all items for this
--    order" (a very common query pattern: anytime you render an order's
--    detail page). Without it, SQLite has to scan the whole order_items
--    table for every single order lookup.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    price_cents   INTEGER NOT NULL CHECK (price_cents >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'confirmed', 'failed', 'cancelled')),
    total_cents INTEGER NOT NULL DEFAULT 0 CHECK (total_cents >= 0),
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id        INTEGER NOT NULL REFERENCES products(id),
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    price_at_purchase_cents INTEGER NOT NULL CHECK (price_at_purchase_cents >= 0)
);

CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product_id ON order_items(product_id);
