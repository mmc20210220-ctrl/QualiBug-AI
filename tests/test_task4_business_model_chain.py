# -*- coding: utf-8 -*-
"""Task-4 regression tests: business-model build chain fixes.

Guards three structural breaks that kept 7 hidden defects unreachable:

1. ``state_audit_planner`` picked operational probe endpoints (``/api/orders/
   health``) as entity audit reads — the probe body never carries the entity
   collection, so the audit was INDETERMINATE by construction (DB-001/DB-002/
   DB-004 audit arm).
2. The flow freezer demanded an IR operation index entry for browser
   page-observation steps (``protocol_step=ui_open``), killing every UI rule
   at freeze time with ``operation_unresolved:missing`` (UI-001/UI-003/UI-004).
3. A GET/HEAD validation rule with a source-declared numeric boundary had no
   decidable assertion projection and died as
   ``read_side_rule_lacks_decidable_assertion`` even though the boundary is
   decidable on the read's own response (DB-003 read-side arm).
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.assertion_dsl_base import (
    evaluate_assertion,
    registered_assertion_kinds,
)
from ai_test_asset_center.experiment_protocol_registry import (
    resolve_family_protocol,
)
from ai_test_asset_center.experiment_protocols_base import (
    _read_side_numeric_boundary_projection,
)
from ai_test_asset_center.readonly_audit_protocol import (
    ASSERTION_KIND_NUMERIC,
    install_readonly_audit_protocol,
)
from ai_test_asset_center.state_audit_planner import (
    _find_get_endpoint_for_entity,
    _is_operational_endpoint,
)


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install_readonly_audit_protocol()


# ────────────────────────────────────────────────────────────────────────────
# 1. Operational endpoint exclusion in the audit planner
# ────────────────────────────────────────────────────────────────────────────

def _op(op_id: str, method: str = "GET", path: str = "") -> dict:
    return {"id": op_id, "method": method, "path": path or f"/api/{op_id}"}


def test_operational_endpoint_detection() -> None:
    assert _is_operational_endpoint(_op("order_service_get__health", path="/api/orders/health"))
    assert _is_operational_endpoint(_op("svc_healthz", path="/healthz"))
    assert _is_operational_endpoint(_op("metrics_prom", path="/api/metrics"))
    assert not _is_operational_endpoint(_op("order_service_get", path="/api/orders"))
    assert not _is_operational_endpoint(_op("order_service_get__orderid", path="/api/orders/{id}"))


def test_audit_endpoint_excludes_health_probe() -> None:
    ops = [
        _op("order_service_get__health", path="/api/orders/health"),
        _op("order_service_get", path="/api/orders"),
        _op("order_service_get__orderid", path="/api/orders/{id}"),
    ]
    # prefer_list (uniqueness / numeric boundary) must land on the real
    # collection read, never the probe.
    chosen = _find_get_endpoint_for_entity("orders", ops, prefer_list=True)
    assert chosen is not None
    assert chosen["path"] == "/api/orders"


def test_audit_endpoint_detail_fallback_for_listless_entity() -> None:
    ops = [
        _op("payment_service_get__health", path="/api/payments/health"),
        _op("payment_service_get__order_orderid", path="/api/payments/order/{orderId}"),
    ]
    chosen = _find_get_endpoint_for_entity("payment", ops, prefer_list=True)
    assert chosen is not None
    assert chosen["path"] == "/api/payments/order/{orderId}"


def test_audit_endpoint_keeps_plain_list() -> None:
    ops = [
        _op("cart_service_get__items", path="/api/cart/items"),
        _op("cart_service_get__health", path="/api/cart/health"),
    ]
    chosen = _find_get_endpoint_for_entity("cart", ops, prefer_list=True)
    assert chosen is not None
    assert chosen["path"] == "/api/cart/items"


# ────────────────────────────────────────────────────────────────────────────
# 2. Flow freezer: UI surface steps are exempt from operation resolution
# ────────────────────────────────────────────────────────────────────────────

def _ui_experiment() -> dict:
    return {
        "experiment_id": "exp_ui_1",
        "obligation_id": "obl_ui_1",
        "compile_receipt": {"status": "COMPILED"},
        "observers": [{"observer_id": "ui_browser"}],
        "assertions": [{
            "kind": "ui_state_consistency",
            "ui_url": "http://localhost:3001",
            "states": ["ON_SALE", "OFF_SALE", "DRAFT"],
            "allowed_states": ["ON_SALE"],
            "forbidden_states": ["OFF_SALE", "DRAFT"],
        }],
        "control_plan": [{
            "step_id": "control_1",
            "operation_ref": "",
            "intent": "ui_page_observation",
            "protocol_step": "ui_open",
            "ui_url": "http://localhost:3001",
            "surface": "ui_browser",
            "observer_requirements": [{"observer_id": "ui_browser"}],
        }],
        "treatment_plan": [],
        "cleanup_plan": [],
    }


def test_freeze_ui_surface_step_compiles() -> None:
    from ai_test_asset_center.experiment_compile_freezer import (
        freeze_compiled_experiment,
    )

    exp = _ui_experiment()
    frozen = freeze_compiled_experiment(exp, behavior_ir={"operations": []})
    assert frozen["compile_receipt"]["status"] == "COMPILED"
    receipt = frozen["compile_freeze_receipt"]
    assert receipt["status"] == "FROZEN"
    req = frozen["flow_requirements"]
    # The UI step carries a stable virtual operation identity, is a required
    # step, and is never classified as a write.
    assert req["required_step_ids"] == ["control_1"]
    assert req["write_step_ids"] == []
    assert len(req["operation_refs"]) == 1
    assert req["operation_refs"][0].startswith("ui_page:")
    # The ui_browser observer binds to the ui_open step.
    bindings = [b for b in req["observer_bindings"] if b.get("step_id") == "control_1"]
    assert bindings and bindings[0]["observer_ids"] == ["ui_browser"]


def test_freeze_non_ui_step_with_missing_operation_still_blocks() -> None:
    from ai_test_asset_center.experiment_compile_freezer import (
        freeze_compiled_experiment,
    )

    exp = _ui_experiment()
    exp["control_plan"][0] = {
        "step_id": "control_1",
        "operation_ref": "op_missing",
        "intent": "authorized_control",
        "protocol_step": "positive_control",
    }
    frozen = freeze_compiled_experiment(exp, behavior_ir={"operations": []})
    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == "BLOCKED_FLOW_REQUIREMENTS_INVALID"
    assert "operation_unresolved" in frozen["compile_receipt"]["detail"]


# ────────────────────────────────────────────────────────────────────────────
# 3. Read-side numeric boundary projection (validation family)
# ────────────────────────────────────────────────────────────────────────────

def test_read_side_numeric_boundary_projection_compiles() -> None:
    result = _read_side_numeric_boundary_projection(
        property_spec={
            "template": "invariant_validation",
            "invariant_ref": "inv_qty_1",
            "expression": {
                "kind": "numeric_boundary",
                "operator": "must_hold",
                "operands": [{
                    "entity_ref": "inventory",
                    "field": "available_qty",
                    "field_id": "schema:inventory:available_qty",
                }],
                "raw": "`inventory`.`available_qty` 必须为正数，数量不允许为负",
                "equation": {"operator": "positive", "terms": ["available_qty"]},
            },
        },
        operation_ref="op_get_inventory",
        actor_ref="actor_admin",
    )
    assert result is not None
    assert result["status"] == "COMPILED"
    assertion = result["assertion"]
    assert assertion["kind"] == ASSERTION_KIND_NUMERIC
    assert assertion["field"] == "available_qty"
    assert assertion["operator"] == "positive"
    assert result["observers"] == [{"observer_id": "http_response"}]


def test_read_side_numeric_projection_missing_field_returns_none() -> None:
    result = _read_side_numeric_boundary_projection(
        property_spec={
            "template": "invariant_validation",
            "invariant_ref": "inv_x",
            "expression": {"kind": "prose", "raw": "something about state"},
        },
        operation_ref="op_get",
        actor_ref="actor_admin",
    )
    assert result is None


def test_read_side_numeric_projection_missing_actor_returns_none() -> None:
    result = _read_side_numeric_boundary_projection(
        property_spec={
            "template": "invariant_validation",
            "invariant_ref": "inv_qty",
            "expression": {
                "kind": "numeric_boundary",
                "operands": [{"entity_ref": "cart", "field": "qty"}],
                "equation": {"operator": "non_negative", "terms": ["qty"]},
            },
        },
        operation_ref="op_get",
        actor_ref="",
    )
    assert result is None


# ────────────────────────────────────────────────────────────────────────────
# 4. Read-only numeric audit: single-row detail response is decidable
# ────────────────────────────────────────────────────────────────────────────

def _numeric_envelope(rows_body, *, column="available_qty", operator="non_negative"):
    return {
        "assertion": {
            "kind": ASSERTION_KIND_NUMERIC,
            "field": column,
            "field_qualifier": "inventory",
            "operator": operator,
            "min_observed_rows": 2,
        },
        "observations": {"status_code": 200, "body": rows_body},
    }


def _evaluate_numeric(rows_body, *, column="available_qty", operator="non_negative"):
    env = _numeric_envelope(rows_body, column=column, operator=operator)
    return evaluate_assertion(env["assertion"], observations=env["observations"])


def test_numeric_audit_detects_single_row_violation() -> None:
    result = _evaluate_numeric({"available_qty": -5, "sku": "P1"})
    assert result.get("passed") is False
    assert result.get("reason_code") == "NUMERIC_BOUNDARY_VIOLATION_OBSERVED"


def test_numeric_audit_single_row_pass() -> None:
    result = _evaluate_numeric({"available_qty": 12, "sku": "P1"})
    assert result.get("passed") is True


def test_numeric_audit_single_row_missing_field_indeterminate() -> None:
    # A detail body without the declared field yields no locatable row: the
    # audit stays INDETERMINATE (never PASS from an unobservable field).
    result = _evaluate_numeric({"sku": "P1"})
    assert result.get("passed") is None
    assert result.get("reason_code") == "AUDIT_COLLECTION_NOT_OBSERVED"


def test_numeric_audit_empty_body_is_evidence_missing() -> None:
    # An empty body fails the kind-to-evidence contract before the evaluator:
    # no body, no rows, no verdict.
    result = _evaluate_numeric([])
    assert result.get("passed") is None
    assert result.get("reason_code") == "ASSERTION_EVIDENCE_MISSING"


def test_assertion_kinds_registered() -> None:
    assert ASSERTION_KIND_NUMERIC in set(registered_assertion_kinds())
    # The (validation, readonly_audit_validation) protocol must resolve.
    assert resolve_family_protocol("validation", "readonly_audit_validation") is not None
