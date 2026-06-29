"""
Flask REST API for the mini e-commerce backend.

Run with:
    python3 app/server.py

Endpoints:
    GET  /products            list all products
    POST /products             create a product
    GET  /products/<id>        get one product
    POST /orders                place an order (one or more items)
    GET  /orders/<id>           get order details

See README.md for example curl commands.
"""

from flask import Flask, request, jsonify

from db import (
    init_db,
    create_product,
    get_product,
    list_products,
    place_order,
    get_order,
    OrderItemRequest,
    InsufficientStockError,
    ProductNotFoundError,
)

app = Flask(__name__)


@app.route("/products", methods=["GET"])
def api_list_products():
    products = list_products()
    return jsonify([dict(p) for p in products])


@app.route("/products", methods=["POST"])
def api_create_product():
    data = request.get_json(silent=True) or {}

    name = data.get("name")
    price_cents = data.get("price_cents")
    stock_quantity = data.get("stock_quantity", 0)

    if not name or price_cents is None:
        return jsonify({"error": "name and price_cents are required"}), 400

    if not isinstance(price_cents, int) or price_cents < 0:
        return jsonify({"error": "price_cents must be a non-negative integer"}), 400

    if not isinstance(stock_quantity, int) or stock_quantity < 0:
        return jsonify({"error": "stock_quantity must be a non-negative integer"}), 400

    product_id = create_product(name, price_cents, stock_quantity)
    return jsonify(dict(get_product(product_id))), 201


@app.route("/products/<int:product_id>", methods=["GET"])
def api_get_product(product_id):
    product = get_product(product_id)
    if product is None:
        return jsonify({"error": "product not found"}), 404
    return jsonify(dict(product))


@app.route("/orders", methods=["POST"])
def api_place_order():
    data = request.get_json(silent=True) or {}
    raw_items = data.get("items")

    if not raw_items or not isinstance(raw_items, list):
        return jsonify({"error": "items must be a non-empty list"}), 400

    try:
        items = []
        for raw in raw_items:
            product_id = raw.get("product_id")
            quantity = raw.get("quantity")
            if not isinstance(product_id, int) or not isinstance(quantity, int) or quantity <= 0:
                return jsonify({"error": "each item needs integer product_id and positive integer quantity"}), 400
            items.append(OrderItemRequest(product_id=product_id, quantity=quantity))
    except AttributeError:
        return jsonify({"error": "each item must be an object with product_id and quantity"}), 400

    try:
        order_id = place_order(items)
    except ProductNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except InsufficientStockError as e:
        return jsonify({
            "error": "insufficient_stock",
            "product_id": e.product_id,
            "requested": e.requested,
            "available": e.available,
        }), 409  # 409 Conflict: the classic status code for "state changed under you"

    return jsonify(get_order(order_id)), 201


@app.route("/orders/<int:order_id>", methods=["GET"])
def api_get_order(order_id):
    order = get_order(order_id)
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(order)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001, use_reloader=False)
