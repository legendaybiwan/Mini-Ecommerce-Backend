"""
Concurrency proof for the e-commerce backend's order placement.

Demonstrates the classic "last item in stock" overselling bug: many
customers buying the same product at the same instant. Compares the naive
unsafe read-check-write pattern against this project's atomic
UPDATE ... WHERE stock_quantity >= ? approach.

Run:
    python3 tests/concurrency_test.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from db import init_db, create_product, place_order, get_product, OrderItemRequest, InsufficientStockError, DB_PATH
import unsafe_db


def run_unsafe_scenario(num_buyers: int, initial_stock: int) -> tuple[int, int]:
    if unsafe_db.DB_PATH.exists():
        unsafe_db.DB_PATH.unlink()
    unsafe_db.init_unsafe_db()
    pid = unsafe_db.create_unsafe_product("Last PS5 in stock (unsafe)", 49999, initial_stock)

    def attempt(_):
        return unsafe_db.unsafe_buy(pid, 1, simulate_delay=True)

    with ThreadPoolExecutor(max_workers=num_buyers) as pool:
        results = list(pool.map(attempt, range(num_buyers)))

    successes = sum(1 for r in results if r)
    final_stock = unsafe_db.get_unsafe_stock(pid)
    return successes, final_stock


def run_safe_scenario(num_buyers: int, initial_stock: int) -> tuple[int, int]:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()
    pid = create_product("Last PS5 in stock (safe)", 49999, initial_stock)

    def attempt(_):
        try:
            place_order([OrderItemRequest(product_id=pid, quantity=1)])
            return True
        except InsufficientStockError:
            return False

    with ThreadPoolExecutor(max_workers=num_buyers) as pool:
        results = list(pool.map(attempt, range(num_buyers)))

    successes = sum(1 for r in results if r)
    final_stock = get_product(pid)["stock_quantity"]
    return successes, final_stock


if __name__ == "__main__":
    NUM_BUYERS = 20
    INITIAL_STOCK = 5

    print(f"Scenario: {INITIAL_STOCK} units in stock, {NUM_BUYERS} customers buy 1 unit each, concurrently\n")

    unsafe_successes, unsafe_final_stock = run_unsafe_scenario(NUM_BUYERS, INITIAL_STOCK)
    expected_correct_stock = max(0, INITIAL_STOCK - NUM_BUYERS)
    print(f"[UNSAFE read-check-write]  'sold' = {unsafe_successes} of {NUM_BUYERS} attempts  "
          f"final_stock = {unsafe_final_stock}  (should be {INITIAL_STOCK} if all rejected once empty)")
    if unsafe_successes > INITIAL_STOCK:
        print(f"           -> BUG: oversold by {unsafe_successes - INITIAL_STOCK} units. "
              f"Lost updates also mean the final_stock number itself can't be trusted -\n"
              f"              concurrent writers overwrote each other's decrements instead of stacking them.")

    safe_successes, safe_final_stock = run_safe_scenario(NUM_BUYERS, INITIAL_STOCK)
    status = "CORRECT" if safe_successes == INITIAL_STOCK and safe_final_stock == 0 else "BUG"
    print(f"\n[SAFE   atomic UPDATE...WHERE]  sold = {safe_successes} of {NUM_BUYERS} attempts  "
          f"final_stock = {safe_final_stock}  -> {status}")
