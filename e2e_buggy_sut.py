#!/usr/bin/env python
# ruff: noqa
"""
E2E 一次性靶场 (buggy SUT) — 纯标准库 http.server，无第三方依赖。

实现 e2e OpenAPI 的 4 个端点，并注入真实业务逻辑缺陷，供 QualiBug V12 管道
执行探测并复现:
  - POST /api/orders           : 不校验 quantity<=0 (可下负数量订单)
  - POST /api/orders/{id}/pay  : 非幂等 (重复支付累加金额)，且不校验金额
  - POST /api/orders/{id}/refund: 可退款超过已付金额 (违反资金守恒)
  - POST /api/register         : 接受 role=admin (公开注册即可提权)
  - GET  /api/orders/{id}      : 读取订单状态 (供 oracle 核对)
  - GET  /openapi.json         : 暴露契约
用法: python e2e_buggy_sut.py <port>
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_ORDERS: dict[str, dict] = {}
_USERS: dict[str, dict] = {}
_LOCK = threading.Lock()
_SEQ = {"n": 0}

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "E2E Buggy Shop", "version": "1.0.0"},
    "paths": {
        "/api/orders": {"post": {"operationId": "createOrder"}},
        "/api/orders/{id}/pay": {"post": {"operationId": "payOrder"}},
        "/api/orders/{id}/refund": {"post": {"operationId": "refundOrder"}},
        "/api/register": {"post": {"operationId": "registerUser"}},
    },
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        return

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/openapi.json":
            return self._send(200, OPENAPI)
        if path in ("/health", "/"):
            return self._send(200, {"ok": True})
        if path.startswith("/api/orders/"):
            oid = path.split("/")[3] if len(path.split("/")) > 3 else ""
            with _LOCK:
                o = _ORDERS.get(oid)
            if o:
                return self._send(200, o)
            return self._send(404, {"error": "not_found"})
        return self._send(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()
        parts = path.split("/")

        # POST /api/orders
        if path == "/api/orders":
            with _LOCK:
                _SEQ["n"] += 1
                oid = str(_SEQ["n"])
                qty = body.get("quantity", 1)
                # BUG 1: no validation that quantity > 0 (accepts negative/zero)
                order = {
                    "id": oid,
                    "product_id": body.get("product_id"),
                    "quantity": qty,
                    "status": "created",
                    "amount_paid": 0,
                    "amount_refunded": 0,
                }
                _ORDERS[oid] = order
            return self._send(201, order)

        # POST /api/orders/{id}/pay
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "orders" and parts[4] == "pay":
            oid = parts[3]
            with _LOCK:
                o = _ORDERS.get(oid)
                if not o:
                    return self._send(404, {"error": "not_found"})
                amount = body.get("amount", 100)
                # BUG 2: non-idempotent — paying twice accumulates, no state guard
                o["amount_paid"] += amount
                o["status"] = "paid"
            return self._send(200, o)

        # POST /api/orders/{id}/refund
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "orders" and parts[4] == "refund":
            oid = parts[3]
            with _LOCK:
                o = _ORDERS.get(oid)
                if not o:
                    return self._send(404, {"error": "not_found"})
                amount = body.get("amount", o.get("amount_paid", 0))
                # BUG 3: refund can exceed amount_paid (money conservation broken);
                # also allows refund with no prior payment.
                o["amount_refunded"] += amount
                o["status"] = "refunded"
            return self._send(200, o)

        # POST /api/register
        if path == "/api/register":
            uname = body.get("username", "")
            role = body.get("role", "user")
            with _LOCK:
                # BUG 4: accepts arbitrary role from public request (privilege escalation)
                _USERS[uname] = {"username": uname, "role": role}
            return self._send(201, {"username": uname, "role": role})

        return self._send(404, {"error": "not_found"})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"buggy SUT listening on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
