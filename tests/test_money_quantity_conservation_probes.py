"""Focused tests for money_quantity_conservation probe generation and oracles."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import urllib.parse

from ai_test_asset_center.business_state_graph import _api_facts
from ai_test_asset_center.oracle_engine import MoneyOracle
from ai_test_asset_center.private_pilot_system_behavior_space_patch import (
    _direct_system_promise_oracle_result,
)
from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator
from ai_test_asset_center.supplementary_behavior_slices import generate_money_slices
import re


def test_money_slices_prioritize_pay_and_refund() -> None:
    api_doc = Path("projects/benchmark_mall/input/API_SPEC.md").read_text(encoding="utf-8")
    _, _, endpoints = _api_facts(
        api_doc,
        re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I),
    )
    slices = generate_money_slices(
        endpoints,
        {"role": "buyer", "email": "a", "password": "b"},
        "/api/auth/login",
        max_slices=12,
    )
    paths = [s["_money_path"] for s in slices]
    assert any("/payments/pay" in p for p in paths), paths
    assert any("/refunds" in p for p in paths), paths
    # Financial leaves should outrank shallow generic creates in priority.
    pay = next(s for s in slices if "/payments/pay" in s["_money_path"])
    assert float(pay["priority"]) >= 0.86


def test_money_slice_pay_body_binds_order_id_placeholder() -> None:
    api_doc = Path("projects/benchmark_mall/input/API_SPEC.md").read_text(encoding="utf-8")
    slice_meta = {
        "entity": "payments",
        "slice_id": "m_pay",
        "priority": 0.9,
        "source_refs": [],
        "_money_method": "POST",
        "_money_path": "/api/payments/pay",
        "_login_path": "/api/auth/login",
        "_login_body": {"email": "a", "password": "b"},
        "_default_actor": "buyer",
        "_default_email": "buyer@test.com",
        "_default_password": "pass",
    }
    scenario = SemanticScenarioGenerator._money_slice(slice_meta, 1, api_doc)
    assert scenario is not None
    assert scenario.category == "money_quantity_conservation"
    write = next(s for s in scenario.steps if s.action.startswith("money_probe_"))
    assert write.body_template.get("orderId") == "{orderId}"
    assert isinstance(write.body_template.get("amount"), (int, float))
    assert any(s.action.startswith("bootstrap_create_") for s in scenario.steps)
    assert any(s.api_path == "/api/orders" and s.api_method == "POST" for s in scenario.steps)


def test_runtime_bootstrap_create_overrides_stale_money_bindings(monkeypatch) -> None:
    from ai_test_asset_center.v12_pipeline import _execute_scenario

    captured_payment_bodies: list[dict] = []

    class Response:
        def __init__(self, status: int, body):
            self.status = status
            self._body = body
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit: int = -1):
            return json.dumps(self._body).encode("utf-8")

    def fake_urlopen(request, timeout=10):
        method = request.get_method()
        path = urllib.parse.urlparse(request.full_url).path
        body = json.loads(request.data.decode("utf-8")) if getattr(request, "data", None) else {}
        if method == "GET" and path == "/api/orders":
            return Response(200, [{"id": "old-order", "payable_amount": "1.00"}])
        if method == "POST" and path == "/api/orders":
            return Response(201, {"id": "new-order", "payable_amount": "6999.00"})
        if method == "POST" and path == "/api/payments/pay":
            captured_payment_bodies.append(body)
            return Response(200, {"status": "PAID"})
        return Response(404, {"error": "unexpected"})

    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline._record_v12_har", lambda *args, **kwargs: None)

    scenario = SimpleNamespace(
        id="money-bootstrap-binding",
        category="money_quantity_conservation",
        actor_token="token",
        steps=[
            SimpleNamespace(
                action="resolve_body_orderId",
                api_method="GET",
                api_path="/api/orders",
                body_template={},
                extract_from_response=["orderId"],
                expected_status=200,
                actor="buyer",
            ),
            SimpleNamespace(
                action="bootstrap_create_orderId",
                api_method="POST",
                api_path="/api/orders",
                body_template={"items": [{"sku": "SKU-1", "qty": 1}]},
                extract_from_response=["id", "orderId"],
                expected_status=201,
                actor="buyer",
            ),
            SimpleNamespace(
                action="money_probe_POST",
                api_method="POST",
                api_path="/api/payments/pay",
                body_template={"orderId": "{orderId}", "amount": 6899, "channel": "BALANCE"},
                extract_from_response=[],
                expected_status=200,
                actor="buyer",
            ),
        ],
    )

    trace = _execute_scenario(scenario, "http://target.test")

    assert trace["errors"] == []
    assert captured_payment_bodies == [
        {"orderId": "new-order", "amount": 6999, "channel": "BALANCE"}
    ]


def test_runtime_amount_binding_covers_state_transition_pay_step(monkeypatch) -> None:
    from ai_test_asset_center.v12_pipeline import _execute_scenario

    captured_payment_bodies: list[dict] = []

    class Response:
        def __init__(self, status: int, body):
            self.status = status
            self._body = body
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit: int = -1):
            return json.dumps(self._body).encode("utf-8")

    def fake_urlopen(request, timeout=10):
        method = request.get_method()
        path = urllib.parse.urlparse(request.full_url).path
        body = json.loads(request.data.decode("utf-8")) if getattr(request, "data", None) else {}
        if method == "GET" and path == "/api/orders":
            return Response(200, [{"id": "stale-order", "payable_amount": "1.00"}])
        if method == "POST" and path == "/api/orders":
            return Response(201, {"id": "fresh-order", "payable_amount": "6999.00"})
        if method == "POST" and path == "/api/payments/pay":
            captured_payment_bodies.append(body)
            return Response(200, {"status": "PAID"})
        return Response(404, {"error": "unexpected"})

    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline._record_v12_har", lambda *args, **kwargs: None)

    scenario = SimpleNamespace(
        id="state-transition-pay-binding",
        category="state_machine",
        actor_token="token",
        steps=[
            SimpleNamespace(
                action="resolve_body_orderId",
                api_method="GET",
                api_path="/api/orders",
                body_template={},
                extract_from_response=["orderId"],
                expected_status=200,
                actor="buyer",
            ),
            SimpleNamespace(
                action="bootstrap_create_orderId",
                api_method="POST",
                api_path="/api/orders",
                body_template={"items": [{"sku": "SKU-1", "qty": 1}]},
                extract_from_response=["id", "orderId"],
                expected_status=201,
                actor="buyer",
            ),
            SimpleNamespace(
                action="transition_pay",
                api_method="POST",
                api_path="/api/payments/pay",
                body_template={"orderId": "{orderId}", "amount": 6899, "channel": "BALANCE"},
                extract_from_response=[],
                expected_status=200,
                actor="buyer",
            ),
        ],
    )

    trace = _execute_scenario(scenario, "http://target.test")

    assert trace["errors"] == []
    assert captured_payment_bodies == [
        {"orderId": "fresh-order", "amount": 6999, "channel": "BALANCE"}
    ]


def test_transition_observer_prefers_declared_collection_read() -> None:
    endpoints = [
        {"entity": "order", "method": "GET", "path": "/api/orders/{id}"},
        {"entity": "order", "method": "GET", "path": "/api/orders"},
    ]

    assert SemanticScenarioGenerator._entity_read_endpoint("order", endpoints) == "/api/orders"


def test_read_observer_projects_bound_entity_from_collection_fallback(monkeypatch) -> None:
    from ai_test_asset_center.v12_pipeline import _execute_scenario

    requested_paths: list[str] = []

    class Response:
        def __init__(self, status: int, body):
            self.status = status
            self._body = body
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit: int = -1):
            return json.dumps(self._body).encode("utf-8")

    def fake_urlopen(request, timeout=10):
        method = request.get_method()
        path = urllib.parse.urlparse(request.full_url).path
        requested_paths.append(path)
        body = json.loads(request.data.decode("utf-8")) if getattr(request, "data", None) else {}
        if method == "POST" and path == "/api/orders":
            return Response(201, {"id": "fresh-order", "payable_amount": "6999.00"})
        if method == "POST" and path == "/api/payments/pay":
            assert body == {"orderId": "fresh-order", "amount": 6999, "channel": "BALANCE"}
            return Response(200, {"status": "PAID"})
        if method == "GET" and path == "/api/orders/fresh-order":
            return Response(404, {"error": "not found"})
        if method == "GET" and path == "/api/orders":
            return Response(200, [
                {"id": "stale-order", "status": "PENDING_PAYMENT"},
                {"id": "fresh-order", "status": "PAID"},
            ])
        return Response(404, {"error": "unexpected"})

    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline._record_v12_har", lambda *args, **kwargs: None)

    scenario = SimpleNamespace(
        id="observer-fallback-binding",
        category="state_machine",
        actor_token="token",
        runtime_hints={"declared_get_paths": ["/api/orders/{id}", "/api/orders"]},
        steps=[
            SimpleNamespace(
                action="create_entity",
                api_method="POST",
                api_path="/api/orders",
                body_template={"items": [{"sku": "SKU-1", "qty": 1}]},
                extract_from_response=["id", "orderId"],
                expected_status=201,
                actor="buyer",
            ),
            SimpleNamespace(
                action="transition_pay",
                api_method="POST",
                api_path="/api/payments/pay",
                body_template={"orderId": "{orderId}", "amount": 6899, "channel": "BALANCE"},
                extract_from_response=[],
                expected_status=200,
                actor="buyer",
            ),
            SimpleNamespace(
                action="observe_transition_result",
                api_method="GET",
                api_path="/api/orders/{id}",
                body_template={},
                extract_from_response=["status"],
                expected_status=200,
                actor="buyer",
            ),
        ],
    )

    trace = _execute_scenario(scenario, "http://target.test")

    assert trace["errors"] == []
    assert requested_paths[-2:] == ["/api/orders/fresh-order", "/api/orders"]
    observed = trace["steps"][-1]
    assert observed["path"] == "/api/orders"
    assert observed["status"] == 200
    assert observed["response"]["body"] == {"id": "fresh-order", "status": "PAID"}
    assert observed["observer_projection"]["original_status"] == 404
    assert observed["observer_projection"]["matched_key"] == "id"


def test_money_oracle_flags_payment_amount_mismatch() -> None:
    oracle = MoneyOracle()
    trace = {
        "steps": [
            {
                "action": "money_probe_POST",
                "method": "POST",
                "path": "/api/payments/pay",
                "status": 200,
                "request": {"body": {"orderId": "o1", "amount": 1}},
                "response": {"status_code": 200, "body": {"payableAmount": 6899, "status": "PAID"}},
            }
        ]
    }
    result = oracle.evaluate({}, trace)
    assert result.passed is False
    assert result.violated_rule == "payment_amount_mismatch"


def test_system_promise_oracle_recognizes_money_quantity_conservation_dimension() -> None:
    scenario = {
        "runtime_hints": {
            "system_promise_invariant": "金额必须守恒",
            "system_promise_verification_intent": {
                "verification_direction": "正向验证",
                "conservation_constraints": ["金额/库存必须在操作前后保持守恒"],
            },
        }
    }
    trace = {
        "steps": [
            {
                "method": "GET",
                "path": "/api/orders/1",
                "response": {"status_code": 200, "body": {"amount": -5}},
            }
        ]
    }
    hints = {
        "promise_id": "p_money",
        "dimensions": ["money_quantity_conservation"],
        "surface_plan": ["api"],
    }
    result = _direct_system_promise_oracle_result(scenario, trace, hints)
    assert result is not None
    assert result.passed is False
    assert "negative_value" in str(result.violated_rule)
