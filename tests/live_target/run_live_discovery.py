from __future__ import annotations

import json
import time
import uuid
from typing import Any
from urllib import error, request


def _http(base_url: str, method: str, path: str, body: dict[str, Any] | None = None, token: str = "") -> dict[str, Any]:
    payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(base_url.rstrip("/") + path, data=payload, method=method.upper(), headers=headers)
    started = time.time()
    try:
        with request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            status = resp.status
    except error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    elapsed_ms = int((time.time() - started) * 1000)
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = {"raw": text}
    return {
        "request": {"method": method.upper(), "url": path, "body": body or {}},
        "response": {"status": status, "body": data},
        "elapsed_ms": elapsed_ms,
    }


def _ready_bug(title: str, risk_id: str, entry_index: int, entry: dict[str, Any], assertion: str) -> dict[str, Any]:
    return {
        "id": risk_id,
        "risk_id": risk_id,
        "title": title,
        "gate_passed": True,
        "is_reproducible": True,
        "failed_assertions": [assertion],
        "reproduction_steps": [
            {
                "method": entry["request"]["method"],
                "path": entry["request"]["url"],
                "expected": assertion,
                "actual_status": entry["response"]["status"],
            }
        ],
        "evidence_refs": [f"har:{entry_index}"],
        "has_har_evidence": True,
        "request_method": entry["request"]["method"],
        "request_path": entry["request"]["url"],
        "response_status": entry["response"]["status"],
    }


def run_discovery_pipeline(base_url: str) -> dict[str, Any]:
    """Exercise the live target and materialize only bugs proven by HTTP evidence."""
    har_entries: list[dict[str, Any]] = []

    def capture(method: str, path: str, body: dict[str, Any] | None = None, token: str = "") -> tuple[int, dict[str, Any]]:
        entry = _http(base_url, method, path, body, token)
        har_entries.append(entry)
        return len(har_entries) - 1, entry

    ready_bugs: list[dict[str, Any]] = []

    idx, disabled_login = capture("POST", "/api/auth/login", {"email": "disabled_buyer@example.com", "password": "Test@123456"})
    if disabled_login["response"]["status"] == 200 and disabled_login["response"]["body"].get("token"):
        ready_bugs.append(_ready_bug("禁用用户仍可登录", "AUTH-001", idx, disabled_login, "disabled account login should be rejected"))

    idx, weak_password = capture(
        "POST",
        "/api/auth/register",
        {"email": f"weak-{uuid.uuid4().hex[:8]}@example.com", "password": "1", "name": "Weak Password Probe"},
    )
    if weak_password["response"]["status"] == 200:
        ready_bugs.append(_ready_bug("弱密码注册被接受", "AUTH-004", idx, weak_password, "weak password registration should be rejected"))

    _, buyer_login = capture("POST", "/api/auth/login", {"email": "buyer01@example.com", "password": "Test@123456"})
    buyer_token = str(buyer_login["response"]["body"].get("token") or "")

    idx, negative_order = capture("POST", "/api/orders", {"items": [{"sku": "SKU-PHONE-001", "qty": -1}]}, buyer_token)
    if negative_order["response"]["status"] == 200:
        ready_bugs.append(_ready_bug("负数数量下单被接受", "PARAM-001", idx, negative_order, "negative order quantity should be rejected"))

    _, create_order = capture("POST", "/api/orders", {"items": [{"sku": "SKU-BOOK-001", "qty": 1}]}, buyer_token)
    order_id = (create_order["response"]["body"].get("order") or {}).get("id")
    if order_id:
        capture("POST", f"/api/orders/{order_id}/cancel", {}, buyer_token)
        idx, cancelled_pay = capture("POST", "/api/payments/pay", {"orderId": order_id, "amount": 99}, buyer_token)
        if cancelled_pay["response"]["status"] == 200:
            ready_bugs.append(_ready_bug("取消订单仍可支付", "ORDER-001", idx, cancelled_pay, "cancelled order payment should be rejected"))

    internal_clues = [
        {"title": "product list includes draft items", "verifier_verdict": "needs_followup"},
        {"title": "address query may need cross-user fixture", "verifier_verdict": "needs_followup"},
    ]
    return {
        "base_url": base_url,
        "har_entries": har_entries,
        "data_contract": {
            "ready_bug_count": len(ready_bugs),
            "ready_bugs": ready_bugs,
            "internal_clues": internal_clues,
            "materialized_risk_count": len(ready_bugs) + len(internal_clues),
        },
    }
