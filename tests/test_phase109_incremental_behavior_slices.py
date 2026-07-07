from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from ai_test_asset_center.business_state_graph import BusinessStateGraphBuilder
from ai_test_asset_center.policy_wiring import _behavior_slice_execution_value
from ai_test_asset_center.route_catalog_builder import RouteCatalogBuilder
from ai_test_asset_center.v12_pipeline import (
    _confirmed_oracle_finding,
    _login_parameter_fuzzer,
    _behavior_slice_settings,
    _rank_behavior_slices_for_selection,
    _runtime_contract,
    _schedule_behavior_slices,
    run_v12_pipeline,
)


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {
        "/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}},
        "/api/cases/{case_id}/reopen": {"patch": {"operationId": "reopenCase"}},
    },
    "components": {
        "schemas": {
            "Case": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "enum": ["DRAFT", "APPROVED", "CLOSED"]},
                },
            },
        },
    },
}, ensure_ascii=False)

DEPENDENCY_WRITE_API = """
### GET /api/orders
### GET /api/orders/:id

### POST /api/refunds

请求：

```json
{"orderId":"<order_id>","amount":100,"reason":"不想要了"}
```
"""

DEPENDENCY_SCHEMA = """
CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);

CREATE TABLE refunds (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES orders(id),
  status TEXT NOT NULL
);
"""

DB_SCHEMA = """
CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  state TEXT CHECK (state IN ('DRAFT', 'APPROVED', 'CLOSED'))
);
"""

PRD = """
# Case lifecycle
DRAFT -> APPROVED by approve

禁止状态流转：
CLOSED -> DRAFT by reopen

# Value constraint
aggregate_value must equal reconciled_value
"""

SOURCE_MANIFEST = {
    "source_id": "uploaded:case-api-v1",
    "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest(),
    "source_origin": "declared_manifest",
}


def test_builder_outputs_only_source_bound_slices_and_explicit_unbound_gap():
    builder = BusinessStateGraphBuilder()
    graphs = builder.build(PRD, API_SPEC, DB_SCHEMA)
    contract = builder.behavior_contract()
    assert set(graphs) == {"case"}
    assert contract["summary"]["total_slices"] >= 2
    transition_slices = [item for item in contract["slices"] if item["kind"] == "transition"]
    assert {item["slice_id"] for item in transition_slices}
    assert all(item["source_refs"] for item in transition_slices)
    assert any(item["endpoints"] for item in transition_slices)
    assert any(gap["kind"] == "UNBOUND_REQUIREMENT" for gap in contract["coverage_gaps"])
    assert all("case" not in gap["title"].lower() for gap in contract["coverage_gaps"])


def test_unique_schema_field_overlap_binds_invariant_without_inventing_state():
    db_schema = """
    CREATE TABLE reconciliations (
      id TEXT PRIMARY KEY,
      aggregate_value NUMERIC,
      reconciled_value NUMERIC
    );
    """
    prd = """
    # Reconciliation constraint
    aggregate_value must equal reconciled_value
    """
    builder = BusinessStateGraphBuilder()
    graphs = builder.build(prd, "", db_schema)
    contract = builder.behavior_contract()
    assert set(graphs) == {"reconciliation"}
    assert contract["summary"]["source_field_bound_invariant_count"] == 1
    invariant_slices = [item for item in contract["slices"] if item["kind"] == "invariant"]
    assert len(invariant_slices) == 1
    assert invariant_slices[0]["entity"] == "reconciliation"
    assert invariant_slices[0]["states"] == []
    assert "STATE_ANCHOR_NOT_SOURCE_BOUND" in invariant_slices[0]["evidence_gaps"]
    assert not contract["coverage_gaps"]


def test_chinese_state_requirement_binds_entity_and_get_observation_route():
    api_doc = """### GET /api/orders/:id
### GET /api/orders
### POST /api/payments/pay
"""
    db_schema = """CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT CHECK (status IN ('CREATED', 'PENDING_PAYMENT', 'PAID', 'CANCELLED'))
);
"""
    prd = """### 3.2 支付
1. 订单必须处于 `PENDING_PAYMENT` 状态；
2. 支付成功后订单状态变为 `PAID`；
"""
    builder = BusinessStateGraphBuilder()
    builder.build(prd, api_doc, db_schema)
    contract = builder.behavior_contract()

    invariant_slices = [item for item in contract["slices"] if item["kind"] == "invariant" and item["entity"] == "order"]
    assert invariant_slices
    assert any("/api/orders/:id" in item["endpoints"] or "/api/orders" in item["endpoints"] for item in invariant_slices)
    assert any("支付成功后订单状态变为 `PAID`" in ref["quote"] for item in invariant_slices for ref in item["source_refs"])


def test_chinese_section_title_binds_cancel_order_requirement_via_markdown_summary():
    api_doc = """### GET /api/orders/:id
查询订单。

### POST /api/orders/:id/cancel
取消订单。
"""
    db_schema = """CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT CHECK (status IN ('PENDING_PAYMENT', 'PAID', 'CANCELLED'))
);
"""
    prd = """### 3.3 取消订单
1. 已支付订单不能直接取消，只能发起退款；
2. 取消订单后必须释放库存；
"""
    builder = BusinessStateGraphBuilder()
    builder.build(prd, api_doc, db_schema)
    contract = builder.behavior_contract()

    assert all(item["title"] != "3.3 取消订单" for item in contract["coverage_gaps"])
    invariant_slices = [item for item in contract["slices"] if item["kind"] == "invariant" and item["entity"] == "order"]
    assert invariant_slices
    assert any("/api/orders/:id" in item["endpoints"] for item in invariant_slices)
    assert any("已支付订单不能直接取消" in ref["quote"] for item in invariant_slices for ref in item["source_refs"])


def test_inventory_report_route_is_reused_as_inventory_observation_endpoint():
    api_doc = """### GET /api/reports/inventory-risk
库存风险报表。
"""
    db_schema = """CREATE TABLE inventory (
  sku TEXT PRIMARY KEY,
  available_qty INT NOT NULL,
  status TEXT CHECK (status IN ('HEALTHY', 'LOW'))
);
"""
    prd = """### 3.5 库存
1. 不允许库存为负；
"""
    builder = BusinessStateGraphBuilder()
    builder.build(prd, api_doc, db_schema)
    contract = builder.behavior_contract()

    assert all(item["title"] != "3.5 库存" for item in contract["coverage_gaps"])
    invariant_slices = [item for item in contract["slices"] if item["kind"] == "invariant" and item["entity"] == "inventory"]
    assert invariant_slices
    assert any("/api/reports/inventory-risk" in item["endpoints"] for item in invariant_slices)


def test_cart_items_schema_entity_reuses_cart_collection_observation_route():
    api_doc = """### GET /api/cart/items
查询当前用户购物车。
"""
    db_schema = """CREATE TABLE users (
  id TEXT PRIMARY KEY
);
CREATE TABLE cart_items (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  sku TEXT NOT NULL
);
"""
    builder = BusinessStateGraphBuilder()
    builder.build("", api_doc, db_schema)
    contract = builder.behavior_contract()

    dependency_slices = [item for item in contract["slices"] if item["kind"] == "dependency" and item["entity"] == "cart_item"]
    assert dependency_slices
    assert any("/api/cart/items" in item["endpoints"] for item in dependency_slices)


def test_payment_invariant_reuses_related_order_observation_route_when_payment_has_no_get():
    api_doc = """### GET /api/orders
查询订单列表。

### GET /api/orders/:id
查询订单。

### POST /api/payments/pay
请求：

```json
{"orderId":"<order_id>","amount":6899}
```
"""
    db_schema = """CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);
CREATE TABLE payments (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES orders(id),
  status TEXT NOT NULL CHECK (status IN ('INIT', 'SUCCESS', 'FAILED', 'REFUNDED'))
);
"""
    prd = """### 3.2 支付
1. 支付必须成功，状态变为 `SUCCESS`；
"""
    builder = BusinessStateGraphBuilder()
    builder.build(prd, api_doc, db_schema)
    contract = builder.behavior_contract()

    invariant_slices = [item for item in contract["slices"] if item["kind"] == "invariant" and item["entity"] == "payment"]
    assert invariant_slices
    assert any("/api/orders" in item["endpoints"] for item in invariant_slices)
    assert all("OBSERVATION_ROUTE_NOT_SOURCE_BOUND" not in item["evidence_gaps"] for item in invariant_slices)


def test_behavior_slice_policy_guardrails_cap_budget_and_round_bounds(monkeypatch):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "999")
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "0")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "999")
    assert _behavior_slice_execution_value("max_behavior_slices_per_round", 999, 15) == 15
    assert _behavior_slice_execution_value("incremental_discovery_round", 0, 1) == 1
    assert _behavior_slice_execution_value("incremental_discovery_round_limit", 999, 3) == 12


def test_parameter_fuzzer_executes_baseline_get_without_query_params(monkeypatch):
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    fuzzer = ParameterFuzzer("http://example.test")
    calls: list[tuple[str, str, str]] = []

    def fake_call(method: str, path: str, body=None, token: str = ""):
        calls.append((method, path, token))
        return 500, {"error": "boom"}, 1.5

    monkeypatch.setattr(fuzzer, "_call", fake_call)

    findings = fuzzer.fuzz_all([{"method": "GET", "path": "/api/orders"}], max_variants=1)

    assert calls == [("GET", "/api/orders", "")]
    assert len(findings) == 1
    assert findings[0]["method"] == "GET"
    assert findings[0]["path"] == "/api/orders"


def test_markdown_route_catalog_extracts_colon_style_path_param():
    routes = RouteCatalogBuilder().build("### GET /api/orders/:id")
    assert len(routes) == 1
    assert routes[0].path == "/api/orders/:id"
    assert routes[0].path_params == ["id"]


def test_parameter_fuzzer_skips_unresolved_colon_path_param_route(monkeypatch):
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    fuzzer = ParameterFuzzer("http://example.test")
    calls: list[tuple[str, str, str]] = []

    def fake_call(method: str, path: str, body=None, token: str = ""):
        calls.append((method, path, token))
        if path.startswith("/api/orders") or path == "/api":
            return 200, [], 1.0
        raise AssertionError(f"unexpected call to unresolved route: {path}")

    monkeypatch.setattr(fuzzer, "_call", fake_call)

    findings = fuzzer.fuzz_all([{"method": "GET", "path": "/api/orders/:id", "path_params": ["id"]}], max_variants=1)

    assert calls
    assert calls[0] == ("GET", "/api/orders", "")
    assert all(path != "/api/orders/:id" for _, path, _ in calls)
    assert all(not path.startswith("/api/orders/") or path == "/api/orders" for _, path, _ in calls)
    assert findings == []


def test_parameter_fuzzer_resolves_real_id_from_paginated_collection(monkeypatch):
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    fuzzer = ParameterFuzzer("http://example.test")
    calls: list[tuple[str, str, str]] = []

    def fake_call(method: str, path: str, body=None, token: str = ""):
        calls.append((method, path, token))
        if path == "/api/orders":
            return 404, {"error": "not_found"}, 1.0
        if path == "/api/orders?page=1&size=1":
            return 200, {"items": [{"id": "ord_123"}]}, 1.0
        if path == "/api/orders/ord_123":
            return 200, {"id": "ord_123", "status": "PAID"}, 1.0
        raise AssertionError(f"unexpected call: {path}")

    monkeypatch.setattr(fuzzer, "_call", fake_call)

    findings = fuzzer.fuzz_all([{"method": "GET", "path": "/api/orders/:id", "path_params": ["id"]}], max_variants=1)

    assert calls == [
        ("GET", "/api/orders", ""),
        ("GET", "/api/orders?page=1&size=1", ""),
        ("GET", "/api/orders/ord_123", ""),
    ]
    assert findings == []


def test_login_parameter_fuzzer_uses_registry_credentials(tmp_path):
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "connectors": [],
                "test_profile": {
                    "test_credentials": {
                        "buyer": {
                            "email": "buyer01@example.com",
                            "password": "Test@123456",
                        }
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    class StubFuzzer:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def login(self, email: str = "", password: str = "", login_path: str = "", body_template=None) -> bool:
            self.calls.append({"email": email, "password": password, "login_path": login_path})
            return True

    stub = StubFuzzer()

    assert _login_parameter_fuzzer(
        stub,
        [{"method": "POST", "path": "/api/auth/login", "operation_id": "login"}],
        "demo",
        tmp_path,
    )
    assert stub.calls == [
        {
            "email": "buyer01@example.com",
            "password": "Test@123456",
            "login_path": "/api/auth/login",
        }
    ]


def test_slice_budget_is_hard_capped_at_fifteen(monkeypatch):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "999")
    assert _behavior_slice_settings()["slice_budget"] == 15


def test_plan_only_slice_is_not_misclassified_as_confirmed():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 1, "round_limit": 2},
        [{"behavior_slice_id": "BHV_example", "execution_status": "not_executed", "confirmation_status": "candidate", "gate_passed": False}],
    )
    assert selection["status"] == "planned"
    assert selection["confirmed_slice_ids"] == []
    assert selection["selected_slice_ids"] == ["BHV_example"]


def test_history_advances_to_next_unattempted_slice_after_real_attempt():
    selection = _schedule_behavior_slices(
        [
            {"slice_id": "BHV_first", "entity": "example", "kind": "transition", "endpoints": ["/api/examples/{id}"]},
            {"slice_id": "BHV_second", "entity": "example", "kind": "invariant", "endpoints": ["/api/examples"]},
        ],
        {"slice_budget": 1, "round_number": 1, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_first"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "planned"
    assert selection["selection_mode"] == "next_unattempted_executable_after_history"
    assert selection["selected_slice_ids"] == ["BHV_second"]
    assert selection["confirmed_slice_ids"] == []


def test_scheduler_stops_after_all_pending_slices_were_attempted_without_confirmation():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 1, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_example"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "stopped"
    assert selection["stop_reason"] == "all_pending_slices_attempted_needs_new_evidence_or_policy"


def test_scheduler_retries_executable_pending_slice_after_history_exhaustion():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition", "endpoints": ["/api/examples/{id}"]}],
        {"slice_budget": 1, "round_number": 2, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_example"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "planned"
    assert selection["selection_mode"] == "retry_executable_after_history"
    assert selection["selected_slice_ids"] == ["BHV_example"]


def test_scheduler_prefers_route_backed_slice_after_history():
    selection = _schedule_behavior_slices(
        [
            {"slice_id": "BHV_dependency", "entity": "refund", "kind": "dependency", "endpoints": []},
            {"slice_id": "BHV_observation", "entity": "order", "kind": "source_observation", "endpoints": ["/api/orders"]},
        ],
        {"slice_budget": 1, "round_number": 2, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_old"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "planned"
    assert selection["selection_mode"] == "next_unattempted_executable_after_history"
    assert selection["selected_slice_ids"] == ["BHV_observation"]


def test_scheduler_prefers_new_executable_slice_over_previously_attempted_higher_priority_slice():
    selection = _schedule_behavior_slices(
        [
            {"slice_id": "BHV_invariant", "entity": "order", "kind": "invariant", "priority": 0.55, "endpoints": ["/api/orders"]},
            {"slice_id": "BHV_dependency", "entity": "refund", "kind": "dependency", "priority": 0.4, "endpoints": ["/api/orders"]},
        ],
        {"slice_budget": 1, "round_number": 2, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_invariant"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "planned"
    assert selection["selection_mode"] == "next_unattempted_executable_after_history"
    assert selection["selected_slice_ids"] == ["BHV_dependency"]


def test_rank_behavior_slices_prefers_runtime_write_risk_over_observation_only():
    ranked = _rank_behavior_slices_for_selection(
        [
            {"slice_id": "BHV_observe", "entity": "catalog", "kind": "source_observation", "priority": 0.6, "source_refs": [{"source_type": "api_document"}]},
            {"slice_id": "BHV_write", "entity": "order", "kind": "invariant", "priority": 0.55, "source_refs": [{"source_type": "requirement"}]},
        ],
        [
            SimpleNamespace(
                behavior_slice_id="BHV_observe",
                execution_policy="safe_read_only",
                category="source_observation",
                severity="P2",
                evidence_gaps=[],
                steps=[1],
                confidence=0.5,
                is_forbidden_path=False,
                is_boundary_path=False,
                is_concurrent=False,
            ),
            SimpleNamespace(
                behavior_slice_id="BHV_write",
                execution_policy="approved_sandbox_write",
                category="state_machine",
                severity="P1",
                evidence_gaps=[],
                steps=[1, 2, 3],
                confidence=0.7,
                is_forbidden_path=False,
                is_boundary_path=False,
                is_concurrent=False,
            ),
        ],
    )
    assert [item["slice_id"] for item in ranked[:2]] == ["BHV_write", "BHV_observe"]


def test_scheduler_spreads_budget_across_distinct_selection_families():
    ranked = _rank_behavior_slices_for_selection(
        [
            {"slice_id": "BHV_pay_a", "entity": "order", "kind": "invariant", "priority": 0.9, "source_refs": [{"source_type": "requirement"}], "endpoints": ["/api/payments/pay"]},
            {"slice_id": "BHV_pay_b", "entity": "order", "kind": "invariant", "priority": 0.89, "source_refs": [{"source_type": "requirement"}], "endpoints": ["/api/payments/pay"]},
            {"slice_id": "BHV_pay_c", "entity": "order", "kind": "invariant", "priority": 0.88, "source_refs": [{"source_type": "requirement"}], "endpoints": ["/api/payments/pay"]},
            {"slice_id": "BHV_cancel", "entity": "order", "kind": "invariant", "priority": 0.6, "source_refs": [{"source_type": "requirement"}], "endpoints": ["/api/orders/{id}/cancel"]},
            {"slice_id": "BHV_refund", "entity": "order", "kind": "invariant", "priority": 0.59, "source_refs": [{"source_type": "requirement"}], "endpoints": ["/api/refunds"]},
        ],
        [
            SimpleNamespace(
                behavior_slice_id="BHV_pay_a",
                title="order: CREATED -> /api/payments/pay",
                execution_policy="approved_sandbox_write",
                category="concurrency",
                severity="P0",
                evidence_gaps=[],
                steps=[1, 2, 3, 4],
                confidence=0.9,
                is_forbidden_path=False,
                is_boundary_path=False,
                is_concurrent=True,
            ),
            SimpleNamespace(
                behavior_slice_id="BHV_pay_b",
                title="order: CREATED -> /api/payments/pay",
                execution_policy="approved_sandbox_write",
                category="concurrency",
                severity="P0",
                evidence_gaps=[],
                steps=[1, 2, 3, 4],
                confidence=0.9,
                is_forbidden_path=False,
                is_boundary_path=False,
                is_concurrent=True,
            ),
            SimpleNamespace(
                behavior_slice_id="BHV_pay_c",
                title="order: CREATED -> /api/payments/pay",
                execution_policy="approved_sandbox_write",
                category="concurrency",
                severity="P0",
                evidence_gaps=[],
                steps=[1, 2, 3, 4],
                confidence=0.9,
                is_forbidden_path=False,
                is_boundary_path=False,
                is_concurrent=True,
            ),
            SimpleNamespace(
                behavior_slice_id="BHV_cancel",
                title="order: CREATED -> /api/orders/{id}/cancel",
                execution_policy="approved_sandbox_write",
                category="state_machine",
                severity="P0",
                evidence_gaps=[],
                steps=[1, 2, 3],
                confidence=0.8,
                is_forbidden_path=True,
                is_boundary_path=False,
                is_concurrent=False,
            ),
            SimpleNamespace(
                behavior_slice_id="BHV_refund",
                title="order: CREATED -> /api/refunds",
                execution_policy="approved_sandbox_write",
                category="state_machine",
                severity="P0",
                evidence_gaps=[],
                steps=[1, 2, 3],
                confidence=0.8,
                is_forbidden_path=True,
                is_boundary_path=False,
                is_concurrent=False,
            ),
        ],
    )
    selection = _schedule_behavior_slices(
        ranked,
        {"slice_budget": 3, "round_number": 1, "round_limit": 3},
        [],
    )
    assert selection["status"] == "planned"
    assert set(selection["selected_slice_ids"]) == {"BHV_pay_a", "BHV_cancel", "BHV_refund"}


def test_rank_behavior_slices_prioritizes_newly_materialized_fallback_slice():
    ranked = _rank_behavior_slices_for_selection(
        [
            {"slice_id": "BHV_existing_pay", "entity": "order", "kind": "invariant", "priority": 0.9, "source_refs": [{"source_type": "requirement"}]},
            {"slice_id": "BHV_new_inventory", "entity": "inventory", "kind": "invariant", "priority": 0.45, "source_refs": [{"source_type": "requirement"}]},
        ],
        [
            SimpleNamespace(
                behavior_slice_id="BHV_existing_pay",
                title="order: CREATED -> /api/payments/pay",
                execution_policy="approved_sandbox_write",
                category="concurrency",
                severity="P0",
                evidence_gaps=[],
                steps=[1, 2, 3, 4],
                confidence=0.9,
                is_forbidden_path=False,
                is_boundary_path=False,
                is_concurrent=True,
                selection_origin="",
            ),
            SimpleNamespace(
                behavior_slice_id="BHV_new_inventory",
                title="[Source observation] inventory: /api/inventory",
                execution_policy="safe_read_only",
                category="invariant",
                severity="P2",
                evidence_gaps=[],
                steps=[1],
                confidence=0.45,
                is_forbidden_path=False,
                is_boundary_path=False,
                is_concurrent=False,
                selection_origin="active_slice_fallback_materialized",
            ),
        ],
    )
    assert [item["slice_id"] for item in ranked[:2]] == ["BHV_new_inventory", "BHV_existing_pay"]


def test_scheduler_stops_when_only_non_executable_slices_remain_after_history():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_dependency", "entity": "refund", "kind": "dependency", "endpoints": []}],
        {"slice_budget": 1, "round_number": 2, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_old"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "stopped"
    assert selection["stop_reason"] == "remaining_unattempted_slices_not_source_executable"


def test_scheduler_stops_instead_of_retrying_when_remaining_unattempted_are_not_executable():
    selection = _schedule_behavior_slices(
        [
            {"slice_id": "BHV_invariant", "entity": "order", "kind": "invariant", "priority": 0.55, "endpoints": ["/api/orders"]},
            {"slice_id": "BHV_dependency", "entity": "refund", "kind": "dependency", "priority": 0.4, "endpoints": []},
        ],
        {"slice_budget": 1, "round_number": 2, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_invariant"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "stopped"
    assert selection["selection_mode"] == "history_exhausted"
    assert selection["stop_reason"] == "remaining_unattempted_slices_not_source_executable"
    assert selection["selected_slice_ids"] == []


def test_scheduler_respects_explicit_round_limit():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 4, "round_limit": 3},
        [],
    )
    assert selection["status"] == "stopped"
    assert selection["stop_reason"] == "configured_round_limit_reached"


def test_pipeline_preserves_original_markdown_doc_for_dependency_write_scenarios(monkeypatch, tmp_path):
    import ai_test_asset_center.v12_pipeline as v12_pipeline_module

    manifest = {
        "source_id": "uploaded:dependency-write-api",
        "source_hash": hashlib.sha256(DEPENDENCY_WRITE_API.encode("utf-8")).hexdigest(),
        "source_origin": "declared_manifest",
    }
    executed: list[dict[str, object]] = []

    def fake_execute(scenario, base_url: str, max_retries: int = 2):
        executed.append(
            {
                "entity": getattr(scenario, "entity", ""),
                "execution_policy": getattr(scenario, "execution_policy", ""),
                "steps": [
                    {
                        "method": getattr(step, "api_method", ""),
                        "path": getattr(step, "api_path", ""),
                        "body": getattr(step, "body_template", {}),
                    }
                    for step in getattr(scenario, "steps", []) or []
                ],
            }
        )
        return {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": [], "duration_ms": 0}

    monkeypatch.setattr(
        v12_pipeline_module,
        "_execution_approval_contract",
        lambda context, campaign, approved_base_url, root: {"status": "approved", "code": ""},
    )
    monkeypatch.setattr(v12_pipeline_module, "_read_only_runtime_token", lambda *args, **kwargs: "tok_test")
    monkeypatch.setattr(v12_pipeline_module, "_execute_scenario", fake_execute)

    result = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text="",
        api_spec_text=DEPENDENCY_WRITE_API,
        db_schema_text=DEPENDENCY_SCHEMA,
        base_url="http://example.test",
        campaign_context={
            "scope_id": "dependency-runtime",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )

    assert result["phases"]["execution"]["status"] == "completed"
    refund = next(item for item in executed if item["entity"] == "refund")
    assert refund["execution_policy"] == "approved_sandbox_write"
    assert refund["steps"] == [
        {"method": "GET", "path": "/api/orders", "body": {}},
        {"method": "POST", "path": "/api/refunds", "body": {"orderId": "{id}", "amount": 100, "reason": "不想要了"}},
        {"method": "GET", "path": "/api/orders", "body": {}},
    ]


def test_pipeline_promotes_runtime_write_oracle_violation_to_confirmed_receipt(monkeypatch, tmp_path):
    import ai_test_asset_center.v12_pipeline as v12_pipeline_module
    from ai_test_asset_center.oracle_engine import OracleResult

    manifest = {
        "source_id": "uploaded:dependency-write-api",
        "source_hash": hashlib.sha256(DEPENDENCY_WRITE_API.encode("utf-8")).hexdigest(),
        "source_origin": "declared_manifest",
    }

    def fake_execute(scenario, base_url: str, max_retries: int = 2):
        return {
            "scenario_id": getattr(scenario, "id", "?"),
            "steps": [
                {
                    "action": "observe_dependency_entity",
                    "method": "GET",
                    "path": "/api/orders",
                    "status": 200,
                    "response": {"status_code": 200, "headers": {}, "body": {"id": "ord_123", "status": "PAID"}},
                    "expected_status": 200,
                },
                {
                    "action": "execute_dependency_write",
                    "method": "POST",
                    "path": "/api/refunds",
                    "status": 201,
                    "response": {"status_code": 201, "headers": {}, "body": {"refundId": "rf_123"}},
                    "expected_status": 409,
                },
            ],
            "errors": [],
            "duration_ms": 12,
        }

    class StubOracle:
        def evaluate(self, scenario, trace, snapshots):
            return [
                OracleResult(
                    passed=False,
                    oracle_name="StateOracle",
                    layer="L3",
                    violated_rule="forbidden_transition",
                    expected="禁止的状态转换应被阻止",
                    actual="HTTP 201",
                    severity="P0",
                    confidence=0.93,
                    explanation="禁止路径被接受",
                )
            ]

    monkeypatch.setattr(
        v12_pipeline_module,
        "_execution_approval_contract",
        lambda context, campaign, approved_base_url, root: {"status": "approved", "code": ""},
    )
    monkeypatch.setattr(v12_pipeline_module, "_read_only_runtime_token", lambda *args, **kwargs: "tok_test")
    monkeypatch.setattr(v12_pipeline_module, "_execute_scenario", fake_execute)
    monkeypatch.setattr("ai_test_asset_center.oracle_engine.OracleEngine", lambda: StubOracle())

    result = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text="",
        api_spec_text=DEPENDENCY_WRITE_API,
        db_schema_text=DEPENDENCY_SCHEMA,
        base_url="http://example.test",
        campaign_context={
            "scope_id": "dependency-runtime",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
            "execution_approval_id": "eap_test",
            "execution_mode": "approved_sandbox_write",
        },
    )

    finding = next(item for item in result["findings"] if item.get("source") == "v12_state_graph")
    assert finding["confirmation_status"] == "confirmed"
    assert finding["gate_passed"] is True
    assert finding["bug_status"] == "reproduced"
    assert finding["evidence"]["request"] == "POST /api/refunds"
    assert finding["evidence"]["response"] == "HTTP 201"
    assert finding["evidence"]["assertion"] == "禁止的状态转换应被阻止"
    assert finding["raw_evidence"]["request_raw"]["path"] == "/api/refunds"
    assert finding["raw_evidence"]["response_raw"]["status_code"] == 201
    assert finding["reproduction"]["method"] == "POST"
    assert finding["reproduction"]["path"] == "/api/refunds"
    assert finding["before_after_snapshot"]["before"]["path"] == "/api/orders"
    assert finding["before_after_snapshot"]["after"]["path"] == "/api/refunds"
    assert finding["evidence_status"]["business_evidence_status"] == "VALIDATED"


def test_pipeline_does_not_advance_round_without_runtime_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "1")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "3")
    monkeypatch.delenv("QUALIBUG_DISCOVERY_ROUND", raising=False)
    first = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    second = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    ledger_path = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "v12_behavior_slice_ledger.json"
    campaign_dir = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "campaigns"
    assert ledger_path.exists()
    assert campaign_dir.exists()
    assert first["campaign"]["campaign_mode"] == "created"
    assert second["campaign"]["campaign_mode"] == "resumed"
    assert first["campaign"]["campaign_id"] == second["campaign"]["campaign_id"]


def test_pipeline_can_start_behavior_contract_rerun_from_completed_campaign(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    builder = BusinessStateGraphBuilder()
    builder.build(PRD, API_SPEC, DB_SCHEMA)
    all_slice_ids = [str(item.get("slice_id") or "") for item in builder.behavior_contract()["slices"]]
    stored["campaign_status"] = "completed"
    stored["status"] = "completed"
    stored["attempted_slice_ids"] = all_slice_ids
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    rerun = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    resumed = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert rerun["campaign"]["campaign_id"] != initial["campaign"]["campaign_id"]
    assert rerun["campaign"]["lineage_campaign_id"] == initial["campaign"]["campaign_id"]
    assert rerun["campaign"]["rerun_key"].startswith("behavior_contract:")
    assert rerun["campaign"]["campaign_mode"] == "created"
    assert rerun["phases"]["incremental_discovery"]["status"] == "planned"
    assert resumed["campaign"]["campaign_id"] == rerun["campaign"]["campaign_id"]
    assert resumed["campaign"]["campaign_mode"] == "resumed"


def test_confirmed_oracle_finding_allows_safe_read_only_runtime_confirmation() -> None:
    scenario = SimpleNamespace(
        actor_token="tok-read",
        actors=["auditor"],
        execution_policy="safe_read_only",
        title="read-only auth boundary",
        category="auth_boundary",
        behavior_slice_id="BHV_READ_1",
        steps=[{"action": "request", "method": "GET", "path": "/api/orders/tenant-b"}],
    )
    oracle_result = SimpleNamespace(
        passed=False,
        severity="P1",
        oracle_name="ReadOnlyOracle",
        explanation="read-only endpoint leaked cross-tenant data",
        confidence=0.97,
        expected="只读接口应拒绝越权访问",
        actual="HTTP 200 returned foreign tenant record",
        violated_rule="readonly_scope_enforced",
        to_dict=lambda: {"oracle": "ReadOnlyOracle"},
    )
    evidence = SimpleNamespace(
        evidence_id="ev-read-1",
        reproduction_steps="GET /api/orders/tenant-b\nobserve foreign tenant order returned",
        vote_summary={"confirmation_threshold_met": True},
        layers_triggered=["runtime", "oracle"],
    )
    trace = {
        "steps": [
            {
                "method": "GET",
                "path": "/api/orders/tenant-b",
                "status": 200,
                "response": {"status_code": 200, "body": {"tenant": "tenant-b", "id": "ord-1"}},
            }
        ]
    }

    finding = _confirmed_oracle_finding(
        scenario,
        trace,
        oracle_result,
        evidence,
        campaign_id="CMP_READ_1",
        discovery_round=1,
        base_url="https://example.test",
    )

    assert finding["confirmation_status"] == "confirmed"
    assert finding["gate_passed"] is True
    assert finding["bug_status"] == "reproduced"
    assert finding["evidence"]["actor"] == "auditor"
    assert finding["business_invariant_evaluation"] == {}
    assert finding["db_evidence"] == {}
    assert finding["evidence_strength"] == "runtime_before_after"


def test_confirmed_oracle_finding_preserves_db_and_business_invariant_evidence_from_trace() -> None:
    scenario = SimpleNamespace(
        actor_token="tok-write",
        actors=["readonly"],
        execution_policy="approved_sandbox_write",
        title="refund flow",
        category="dependency",
        behavior_slice_id="BHV_DEP_1",
        steps=[{"action": "request", "method": "POST", "path": "/api/refunds"}],
    )
    oracle_result = SimpleNamespace(
        passed=False,
        severity="P0",
        oracle_name="ConsistencyOracle",
        explanation="refund write created unexpected side effect",
        confidence=0.93,
        expected="跨实体依赖应保持一致",
        actual="HTTP 201 with DB diff",
        violated_rule="cross_entity_dependency_broken",
        to_dict=lambda: {"oracle": "ConsistencyOracle"},
    )
    evidence = SimpleNamespace(
        evidence_id="ev-dep-1",
        reproduction_steps="GET /api/orders\nPOST /api/refunds\nGET /api/orders",
        vote_summary={"confirmation_threshold_met": True},
        layers_triggered=["L5"],
    )
    trace = {
        "steps": [
            {
                "action": "observe_dependency_entity",
                "method": "GET",
                "path": "/api/orders",
                "status": 200,
                "response": {"status_code": 200, "body": {"id": "ord-1", "status": "PAID"}},
            },
            {
                "action": "execute_dependency_write",
                "method": "POST",
                "path": "/api/refunds",
                "status": 201,
                "response": {"status_code": 201, "body": {"id": "rf-1", "orderId": "ord-1"}},
                "expected_status": 409,
            },
            {
                "action": "verify_dependency_effect_after_write",
                "method": "GET",
                "path": "/api/orders",
                "status": 200,
                "response": {"status_code": 200, "body": {"id": "ord-1", "status": "REFUND_REQUESTED"}},
            },
        ],
        "business_invariant_evaluation": {"verdict": "failed", "reason": "status changed unexpectedly"},
        "db_evidence": {
            "before_db_snapshot": {"row_count": 0},
            "after_db_snapshot": {"row_count": 1},
            "db_assertion": "refund rows changed 0->1",
            "business_operation": "POST /api/refunds",
            "table": "refunds",
        },
    }

    finding = _confirmed_oracle_finding(
        scenario,
        trace,
        oracle_result,
        evidence,
        campaign_id="CMP_DEP_1",
        discovery_round=1,
        base_url="https://example.test",
    )

    assert finding["confirmation_status"] == "confirmed"
    assert finding["db_evidence"]["table"] == "refunds"
    assert finding["business_invariant_evaluation"]["verdict"] == "failed"
    assert finding["raw_evidence"]["db_snapshot"]["table"] == "refunds"
    assert finding["evidence_strength"] == "runtime_and_db"


def test_pipeline_does_not_persist_history_when_execution_approval_is_missing(monkeypatch, tmp_path):
    manifest = {
        "source_id": "uploaded:approval-gated-api",
        "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest(),
        "source_origin": "declared_manifest",
    }
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "1")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "3")
    monkeypatch.delenv("QUALIBUG_DISCOVERY_ROUND", raising=False)

    blocked = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        base_url="http://example.test",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )

    ledger_path = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "v12_behavior_slice_ledger.json"
    campaign_dir = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "campaigns"
    assert blocked["phases"]["execution"]["status"] == "blocked"
    assert blocked["runtime_contract"]["reason"] == "execution_approval_required"
    assert not ledger_path.exists()
    assert not campaign_dir.exists()

    monkeypatch.setattr(
        "ai_test_asset_center.v12_pipeline._execution_approval_contract",
        lambda context, campaign, approved_base_url, root: {"status": "approved", "code": ""},
    )
    approved = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        base_url="http://example.test",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
            "execution_approval_id": "eap_test",
        },
    )

    assert approved["campaign"]["campaign_mode"] == "created"
    assert approved["behavior_slice_ledger"]["round"] == 1
    assert approved["behavior_slice_ledger"]["selection_mode"] == "round_paging"


def test_pipeline_ignores_persisted_slice_history_from_different_snapshot(tmp_path):
    builder = BusinessStateGraphBuilder()
    builder.build(PRD, API_SPEC, DB_SCHEMA)
    contract = builder.behavior_contract()
    slice_ids = [item["slice_id"] for item in contract["slices"] if item.get("slice_id")]
    assert slice_ids
    ledger_path = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "v12_behavior_slice_ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "campaign_id": "CMP_old_snapshot",
                "campaign_status": "coverage_deferred",
                "scope_id": "scope-a",
                "source_snapshot_hash": "different-snapshot",
                "project": "generic-project",
                "round": 1,
                "round_limit": 3,
                "slice_budget": 15,
                "selection_mode": "history_exhausted",
                "selected_slice_ids": [],
                "attempted_slice_ids": slice_ids,
                "confirmed_slice_ids": [],
                "next_round": None,
                "stop_reason": "all_pending_slices_attempted_needs_new_evidence_or_policy",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert result["phases"]["incremental_discovery"]["status"] == "planned"
    assert result["behavior_slice_ledger"]["selected_slice_ids"]
    assert result["behavior_slice_ledger"]["stop_reason"] != "all_pending_slices_attempted_needs_new_evidence_or_policy"


def test_pipeline_recovers_stale_deferred_campaign_without_attempt_history(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 1
    stored["attempted_slice_ids"] = []
    stored["confirmation_receipts"] = {}
    stored["coverage_deferred_reason"] = "all_pending_slices_attempted_needs_new_evidence_or_policy"
    stored["next_campaign_reason"] = "source_binding_or_runtime_evidence_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "active"
    assert recovered["behavior_slice_ledger"]["round"] == 1
    assert recovered["phases"]["incremental_discovery"]["status"] == "planned"
    assert recovered["behavior_slice_ledger"]["selected_slice_ids"]


def test_pipeline_recovers_stale_deferred_campaign_when_new_slices_exist_for_same_snapshot(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 1
    stored["attempted_slice_ids"] = ["BHV_legacy_only"]
    stored["coverage_deferred_reason"] = "all_pending_slices_attempted_needs_new_evidence_or_policy"
    stored["next_campaign_reason"] = "source_binding_or_runtime_evidence_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "active"
    assert recovered["phases"]["incremental_discovery"]["status"] == "planned"
    assert recovered["behavior_slice_ledger"]["selected_slice_ids"]
    assert recovered["behavior_slice_ledger"]["selection_mode"] == "next_unattempted_executable_after_history"


def test_pipeline_recovers_round_exhausted_campaign_when_unattempted_slices_now_exist(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 3
    stored["automatic_round_limit"] = 3
    stored["attempted_slice_ids"] = ["BHV_legacy_only"]
    stored["coverage_deferred_reason"] = "slice_budget_reached"
    stored["next_campaign_reason"] = "source_binding_or_runtime_evidence_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "active"
    assert recovered["campaign"]["round_count"] == 0
    assert recovered["phases"]["incremental_discovery"]["status"] == "planned"
    assert recovered["behavior_slice_ledger"]["selected_slice_ids"]


def test_pipeline_recovers_deferred_campaign_when_attempted_slice_becomes_executable(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    attempted = next(iter(initial["behavior_slice_ledger"]["selected_slice_ids"]), "")
    assert attempted
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 1
    stored["attempted_slice_ids"] = [attempted]
    stored["coverage_deferred_reason"] = "all_pending_slices_attempted_needs_new_evidence_or_policy"
    stored["next_campaign_reason"] = "new_runtime_evidence_fixture_actor_or_policy_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "active"
    assert recovered["phases"]["incremental_discovery"]["status"] == "planned"
    assert recovered["behavior_slice_ledger"]["selection_mode"] in {"retry_executable_after_history", "next_unattempted_executable_after_history", "best_executable_after_history"}
    assert recovered["behavior_slice_ledger"]["selected_slice_ids"]


def test_pipeline_does_not_auto_recover_deferred_campaign_when_only_already_attempted_slices_remain(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    builder = BusinessStateGraphBuilder()
    builder.build(PRD, API_SPEC, DB_SCHEMA)
    all_slice_ids = [str(item.get("slice_id") or "") for item in builder.behavior_contract()["slices"]]
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 3
    stored["automatic_round_limit"] = 3
    stored["attempted_slice_ids"] = all_slice_ids
    stored["coverage_deferred_reason"] = "all_pending_slices_attempted_needs_new_evidence_or_policy"
    stored["next_campaign_reason"] = "new_runtime_evidence_fixture_actor_or_policy_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "coverage_deferred"
    assert recovered["phases"]["incremental_discovery"]["status"] == "stopped"


def test_pipeline_uses_raw_markdown_api_doc_for_state_graph_binding(tmp_path):
    api_doc = """### GET /api/reports/inventory-risk
库存风险报表。
"""
    db_schema = """CREATE TABLE inventory (
  sku TEXT PRIMARY KEY,
  available_qty INT NOT NULL,
  status TEXT CHECK (status IN ('HEALTHY', 'LOW'))
);
"""
    prd = """### 3.5 库存
1. 不允许库存为负；
"""
    manifest = {
        "source_id": "uploaded:inventory-md",
        "source_hash": hashlib.sha256(api_doc.encode("utf-8")).hexdigest(),
        "source_origin": "declared_manifest",
    }

    result = run_v12_pipeline(
        project="markdown-binding-project",
        root=tmp_path,
        prd_text=prd,
        api_spec_text=api_doc,
        db_schema_text=db_schema,
        campaign_context={
            "scope_id": "inventory-risk",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )

    gaps = result["phases"]["state_graph"]["coverage_gaps"]
    assert all(item["title"] != "3.5 库存" for item in gaps)


def test_inventory_requirement_prefers_business_entity_over_report_carrier() -> None:
    api_doc = """# API 接口文档

## Report

### GET /api/reports/inventory-risk

库存风险报表。
"""
    db_schema = """CREATE TABLE inventory (
  sku TEXT PRIMARY KEY,
  available_qty INT NOT NULL,
  locked_qty INT NOT NULL DEFAULT 0,
  warehouse_code TEXT NOT NULL
);
"""
    prd = """### 3.5 库存
1. 下单时锁定库存；
2. 不允许库存为负；
3. 并发下单不得超卖。
"""
    builder = BusinessStateGraphBuilder()
    builder.build(prd, api_doc, db_schema)
    contract = builder.behavior_contract()

    assert all(item["title"] != "3.5 库存" for item in contract["coverage_gaps"])
    inventory_slices = [item for item in contract["slices"] if item["entity"] == "inventory"]
    assert inventory_slices
    assert any("/api/reports/inventory-risk" in item["endpoints"] for item in inventory_slices)


def test_direct_v12_target_execution_is_blocked_without_enterprise_contract(tmp_path):
    result = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        base_url="https://example.invalid",
    )
    assert result["runtime_contract"]["status"] == "blocked"
    assert result["phases"]["execution"]["status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"
    assert result["campaign"]["confirmed_slice_count"] == 0


def test_direct_runtime_contract_accepts_verified_manifest_without_network_access():
    contract = _runtime_contract(
        {"scope_id": "case-lifecycle", "environment_ref": "approved-test", "source_manifest": SOURCE_MANIFEST},
        "https://example.invalid",
        API_SPEC,
    )
    assert contract["status"] == "approved"
    assert contract["approved_base_url"] == "https://example.invalid"
    assert contract["source_manifest"]["source_id"] == "uploaded:case-api-v1"


def test_direct_v12_rejects_hash_mismatch_before_any_execution(tmp_path):
    result = run_v12_pipeline(
        project="enterprise-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        base_url="https://example.invalid",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": {"source_id": "uploaded:case-api-v1", "source_hash": "0" * 64},
        },
    )
    assert result["runtime_contract"]["status"] == "blocked"
    assert "SOURCE_HASH_MISMATCH" in result["runtime_contract"]["missing_requirements"]
    assert result["phases"]["execution"]["status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"


def test_campaign_persists_verified_source_identity_for_plan_only_runs(tmp_path):
    result = run_v12_pipeline(
        project="enterprise-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    assert "error" not in result
    assert result["runtime_contract"]["status"] == "plan_only"
    assert result["campaign"]["source_id"] == "uploaded:case-api-v1"
    assert result["campaign"]["source_hash"] == SOURCE_MANIFEST["source_hash"]
    assert result["campaign"]["source_snapshot_hash"] != SOURCE_MANIFEST["source_hash"]


def test_pipeline_selects_different_source_slices_across_explicit_rounds(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "1")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "2")
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "1")
    first = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    assert "error" not in first
    assert first["phases"]["incremental_discovery"]["status"] == "planned"
    assert len(first["behavior_slice_ledger"]["selected_slice_ids"]) == 1
    assert first["phases"]["execution"]["status"] == "skipped"
    assert all(item["behavior_slice_id"] for item in first["plan_only_scenarios"])
    assert all(item["discovery_round"] == 1 for item in first["plan_only_scenarios"])
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "2")
    second = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    assert "error" not in second
    assert second["phases"]["incremental_discovery"]["status"] == "planned"
    assert first["behavior_slice_ledger"]["selected_slice_ids"] != second["behavior_slice_ledger"]["selected_slice_ids"]
    assert all(item["discovery_round"] == 2 for item in second["plan_only_scenarios"])
