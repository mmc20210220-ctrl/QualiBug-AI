from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple


RequestFunc = Callable[[Dict[str, Any]], Dict[str, Any]]


def _contains(value: Any, needle: str) -> bool:
    return needle in json.dumps(value, ensure_ascii=False).lower()


def _set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    cursor: Any = data
    parts = path.split(".")
    for part in parts[:-1]:
        if isinstance(cursor, dict):
            cursor = cursor.setdefault(part, {})
        else:
            return
    if isinstance(cursor, dict):
        cursor[parts[-1]] = value


@dataclass
class TestDataOrchestrator:
    """Infer and prepare API test data without manual fixtures.

    The orchestrator is intentionally deterministic: it derives a synthetic data catalog
    from the API DSL and captures runtime IDs from previous responses. Enterprise users
    can later plug the same contract into database seeders or masked production snapshots.
    """

    __test__ = False

    catalog: Dict[str, Any] = field(default_factory=dict)
    runtime_values: Dict[str, Any] = field(default_factory=dict)
    records: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.catalog:
            self.catalog = {
                "users": {
                    "normal": {"username": "alice", "password": "Alice123!", "role": "user", "token": "demo-user-token"},
                    "admin": {"username": "admin", "password": "Admin123!", "role": "admin", "token": "demo-admin-token"},
                },
                "products": {
                    "active_in_stock": {"id": "p-1001", "name": "Headphones", "stock": 10},
                    "missing": {"id": "missing-product"},
                    "out_of_stock": {"id": "p-1002", "name": "Keyboard", "stock": 0},
                },
                "coupons": {
                    "valid": {"code": "WELCOME10"},
                    "invalid": {"code": "INVALID_COUPON"},
                },
                "orders": {
                    "existing": {"id": "o-1001"},
                    "missing": {"id": "missing-order"},
                },
            }

    def prepare_case(self, case: Dict[str, Any], request_func: RequestFunc) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        materialized = self.materialize_case(case)
        dependencies = self.infer_dependencies(materialized)
        setup_steps: List[Dict[str, Any]] = []
        blocked_reasons: List[str] = []

        for dep in dependencies:
            step = self.prepare_dependency(dep, request_func)
            setup_steps.append(step)
            if step["status"] == "blocked":
                blocked_reasons.append(step["reason"])

        record = {
            "case_id": case.get("case_id"),
            "title": case.get("title"),
            "dependencies": dependencies,
            "setup_steps": setup_steps,
            "blocked": bool(blocked_reasons),
            "blocked_reasons": blocked_reasons,
            "materialized_path": materialized.get("path"),
            "materialized_body": materialized.get("body"),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.records.append(record)
        return materialized, record

    def infer_dependencies(self, case: Dict[str, Any]) -> List[Dict[str, Any]]:
        path = str(case.get("path") or "").lower()
        body = case.get("body")
        method = str(case.get("method") or "GET").upper()
        expected = int(case.get("expected_status") or 200)
        deps: List[Dict[str, Any]] = []

        if "admin" in path:
            deps.append({"type": "auth", "name": "admin_user", "required": expected < 400})
        elif any(k in path for k in ["cart", "checkout", "order", "me"]):
            deps.append({"type": "auth", "name": "normal_user", "required": expected < 400})

        if "missing-product" in path or _contains(body, "missing-product"):
            deps.append({"type": "product", "name": "missing_product", "required": True, "negative": True})
        elif "product" in path or "cart" in path or _contains(body, "product_id"):
            deps.append({"type": "product", "name": "active_in_stock_product", "required": expected < 400})

        if "missing-order" in path:
            deps.append({"type": "order", "name": "missing_order", "required": True, "negative": True})
        elif "order" in path and method == "GET":
            deps.append({"type": "order", "name": "existing_order", "required": expected < 400})

        if "checkout" in path or (method == "POST" and "orders" in path):
            deps.append({"type": "cart", "name": "cart_with_item", "required": expected < 400})

        if _contains(body, "coupon_code"):
            deps.append({"type": "coupon", "name": "valid_coupon" if expected < 400 else "invalid_coupon", "required": True})

        return self._unique_dependencies(deps)

    def prepare_dependency(self, dep: Dict[str, Any], request_func: RequestFunc) -> Dict[str, Any]:
        kind = dep["type"]
        name = dep["name"]
        if kind == "auth":
            user = self.catalog["users"]["admin" if "admin" in name else "normal"]
            self.runtime_values["auth_token"] = user["token"]
            self.runtime_values["username"] = user["username"]
            return {"dependency": dep, "status": "ready", "strategy": "synthetic_auth_profile", "value": user["username"], "reason": ""}
        if kind == "product":
            product = self.catalog["products"]["missing" if dep.get("negative") else "active_in_stock"]
            if not dep.get("negative"):
                self.runtime_values["product_id"] = product["id"]
            return {"dependency": dep, "status": "ready", "strategy": "synthetic_product_catalog", "value": product["id"], "reason": ""}
        if kind == "coupon":
            coupon = self.catalog["coupons"]["invalid" if "invalid" in name else "valid"]
            if "invalid" not in name:
                self.runtime_values["coupon_code"] = coupon["code"]
            return {"dependency": dep, "status": "ready", "strategy": "synthetic_coupon_catalog", "value": coupon["code"], "reason": ""}
        if kind == "order" and dep.get("negative"):
            return {"dependency": dep, "status": "ready", "strategy": "synthetic_missing_order", "value": self.catalog["orders"]["missing"]["id"], "reason": ""}
        if kind == "order":
            if self.runtime_values.get("order_id"):
                return {"dependency": dep, "status": "ready", "strategy": "runtime_created_order", "value": self.runtime_values["order_id"], "reason": ""}
            self.runtime_values["order_id"] = self.catalog["orders"]["existing"]["id"]
            return {"dependency": dep, "status": "ready", "strategy": "synthetic_existing_order", "value": self.runtime_values["order_id"], "reason": ""}
        if kind == "cart":
            product_id = self.runtime_values.get("product_id") or self.catalog["products"]["active_in_stock"]["id"]
            setup_case = {
                "method": "POST",
                "path": "/api/cart/items",
                "headers": {},
                "body": {"product_id": product_id, "quantity": 1},
                "expected_status": 200,
                "assertions": [],
            }
            response = request_func(setup_case)
            if response.get("status") == "passed":
                body = response.get("response_body") or {}
                if isinstance(body, dict) and body.get("cart_id"):
                    self.runtime_values["cart_id"] = body["cart_id"]
                return {"dependency": dep, "status": "ready", "strategy": "api_setup_post_cart_items", "value": self.runtime_values.get("cart_id", "cart"), "reason": ""}
            return {
                "dependency": dep,
                "status": "blocked",
                "strategy": "api_setup_post_cart_items",
                "value": "",
                "reason": "; ".join(response.get("failures") or ["cart setup failed"]),
            }
        return {"dependency": dep, "status": "ready", "strategy": "no_setup_required", "value": "", "reason": ""}

    def materialize_case(self, case: Dict[str, Any]) -> Dict[str, Any]:
        item = copy.deepcopy(case)
        path = str(item.get("path") or "")
        product_id = self.runtime_values.get("product_id") or self.catalog["products"]["active_in_stock"]["id"]
        order_id = self.runtime_values.get("order_id") or self.catalog["orders"]["existing"]["id"]
        path = path.replace("p-1001", product_id).replace("o-1001", order_id)
        item["path"] = path

        body = item.get("body")
        if isinstance(body, dict):
            if "product_id" in body and body.get("product_id") not in {"", "missing-product"}:
                body["product_id"] = product_id
            if "coupon_code" in body and not isinstance(body.get("coupon_code"), int):
                body["coupon_code"] = self.runtime_values.get("coupon_code") or self.catalog["coupons"]["valid"]["code"]
            item["body"] = body

        headers = dict(item.get("headers") or {})
        if headers.get("X-Role") == "admin":
            headers.setdefault("Authorization", f"Bearer {self.catalog['users']['admin']['token']}")
        elif headers.get("X-Role") == "user" or any(k in path.lower() for k in ["cart", "checkout", "order", "me"]):
            headers.setdefault("Authorization", f"Bearer {self.catalog['users']['normal']['token']}")
        item["headers"] = headers
        return item

    def capture_response(self, case: Dict[str, Any], response: Dict[str, Any]) -> None:
        body = response.get("response_body")
        if response.get("status") != "passed" or not isinstance(body, dict):
            return
        for key in ["order_id", "cart_id", "product_id", "token"]:
            if body.get(key):
                _set_path(self.runtime_values, key, body[key])

    @staticmethod
    def _unique_dependencies(deps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        unique = []
        for dep in deps:
            key = (dep.get("type"), dep.get("name"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(dep)
        return unique
