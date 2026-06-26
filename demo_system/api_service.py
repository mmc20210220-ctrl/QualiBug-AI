from __future__ import annotations

from typing import Any, Dict, Optional


class ApiService:
    """Small in-memory API service for generated API tests.

    It simulates an enterprise backend so the OpenAPI generator can be demoed
    without starting Flask/FastAPI or requiring network ports.
    """

    def __init__(self):
        self.products = {
            1: {"id": 1, "name": "AI Test Book", "stock": 10, "price": 99},
            2: {"id": 2, "name": "Enterprise QA Course", "stock": 3, "price": 199},
        }
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.next_order_id = 1001
        self.users = [
            {"id": 1, "username": "admin", "role": "admin"},
            {"id": 2, "username": "demo_user", "role": "user"},
        ]

    def request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        method = method.upper()
        headers = headers or {}
        json = json or {}

        if method == "GET" and path == "/health":
            return self._response(200, {"status": "ok"})

        if method == "GET" and path == "/products":
            return self._response(200, {"items": list(self.products.values())})

        if method == "GET" and path.startswith("/products/"):
            product_id = self._safe_int(path.split("/")[-1])
            product = self.products.get(product_id)
            if not product:
                return self._response(404, {"error_code": "PRODUCT_NOT_FOUND"})
            return self._response(200, product)

        if method == "POST" and path == "/orders":
            product_id = json.get("product_id")
            quantity = json.get("quantity")
            if not isinstance(product_id, int) or not isinstance(quantity, int) or quantity < 1:
                return self._response(400, {"error_code": "INVALID_ORDER_REQUEST"})
            product = self.products.get(product_id)
            if not product:
                return self._response(404, {"error_code": "PRODUCT_NOT_FOUND"})
            if product["stock"] < quantity:
                return self._response(409, {"error_code": "OUT_OF_STOCK"})
            product["stock"] -= quantity
            order_id = self.next_order_id
            self.next_order_id += 1
            order = {"order_id": order_id, "product_id": product_id, "quantity": quantity, "status": "created"}
            self.orders[order_id] = order
            return self._response(201, order)

        if method == "GET" and path == "/admin/users":
            if headers.get("X-Role") != "admin":
                return self._response(403, {"error_code": "FORBIDDEN"})
            return self._response(200, {"items": self.users})

        return self._response(404, {"error_code": "ROUTE_NOT_FOUND"})

    @staticmethod
    def _safe_int(value: str) -> int:
        try:
            return int(value)
        except Exception:
            return -1

    @staticmethod
    def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"status_code": status_code, "body": body}
