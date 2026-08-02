from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center.behavior_ir_core import _request_schema_for_operation
from ai_test_asset_center.enterprise_knowledge_center._parsing import (
    _markdown_api_operations,
)
from ai_test_asset_center.experiment_plan_step_executor_core import (
    execute_non_barrier_plans,
)


SPEC_PATH = (
    Path(__file__).resolve().parent.parent
    / "platform_inputs"
    / "benchmark_mall"
    / "API_SPEC.md"
)


def _parse_benchmark_mall() -> list[dict[str, object]]:
    text = SPEC_PATH.read_text(encoding="utf-8")
    return _markdown_api_operations(text, source_id="benchmark_mall")


def _find(operations: list[dict[str, object]], method: str, path: str) -> dict[str, object]:
    for op in operations:
        if str(op.get("method")) == method and str(op.get("path")) == path:
            return op
    raise AssertionError(f"operation not found: {method} {path}")


# ---------------------------------------------------------------------------
# The Markdown parser must surface contract-declared required fields as
# request_schema so the required-body-field guard fires for Markdown specs.
# ---------------------------------------------------------------------------


def test_products_admin_declares_sku_required() -> None:
    op = _find(_parse_benchmark_mall(), "POST", "/api/products/admin")
    schema = op.get("request_schema")
    assert isinstance(schema, dict), op
    required = schema.get("required") or []
    assert "sku" in required, required
    # The field is documented as required, not merely present in the body.
    assert schema.get("properties", {}).get("sku") is not None


def test_non_required_fields_not_in_required_list() -> None:
    op = _find(_parse_benchmark_mall(), "POST", "/api/products/admin")
    required = (op.get("request_schema") or {}).get("required") or []
    # name/price/stock are documented as optional (必填=否) -> not required.
    assert "name" not in required
    assert "price" not in required


def test_orders_required_vs_optional_split() -> None:
    op = _find(_parse_benchmark_mall(), "POST", "/api/orders")
    required = (op.get("request_schema") or {}).get("required") or []
    assert "addressId" in required, required  # foreign key, required
    assert "items" in required, required
    assert "couponCode" not in required, required  # documented optional


def test_get_endpoints_do_not_get_request_schema() -> None:
    # GET has no body; required query params must NOT become body fields.
    op = _find(_parse_benchmark_mall(), "GET", "/api/products")
    assert "request_schema" not in op, op


def test_all_write_endpoints_emit_request_schema() -> None:
    expected = {
        ("POST", "/api/auth/login"): {"email", "password"},
        ("POST", "/api/auth/register"): {"email", "password", "name", "phone"},
        ("POST", "/api/cart/items"): {"sku", "qty", "userId"},
        ("POST", "/api/coupons/validate"): {"code", "items", "totalAmount"},
        ("POST", "/api/payments/pay"): {"orderId", "amount", "channel", "idempotencyKey"},
        ("POST", "/api/refunds"): {"orderId", "amount", "reason"},
    }
    operations = _parse_benchmark_mall()
    for (method, path), fields in expected.items():
        op = _find(operations, method, path)
        required = (op.get("request_schema") or {}).get("required") or []
        assert set(required) == fields, (method, path, required)


# ---------------------------------------------------------------------------
# The behavior_ir_core request_schema projection must preserve the Markdown
# declared `required` so the runtime guard reads it unchanged.
# ---------------------------------------------------------------------------


def test_behavior_ir_preserves_markdown_required() -> None:
    markdown_op = {
        "method": "POST",
        "path": "/api/products/admin",
        "request_schema": {
            "type": "object",
            "required": ["sku"],
            "properties": {"sku": {"type": "string"}},
        },
    }
    projected = _request_schema_for_operation(markdown_op)
    assert "sku" in (projected.get("required") or []), projected


# ---------------------------------------------------------------------------
# End-to-end: a parsed benchmark_mall Markdown op, fed to the real executor
# with a body missing the contract-declared `sku`, must be blocked pre-transport
# (reproducing and preventing the real 500 not-null failure).
# ---------------------------------------------------------------------------


def test_executor_blocks_parsed_benchmark_mall_op_missing_sku() -> None:
    op = _find(_parse_benchmark_mall(), "POST", "/api/products/admin")
    assert op.get("request_schema"), "precondition: parser emitted request_schema"

    result = execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=[
            {
                "step_id": "treatment_1",
                "method": "POST",
                "operation_ref": "op-1",
                "actor_ref": "actor-1",
                "path": "/api/products/admin",
                "body": {"name": "no-sku"},
            }
        ],
        consumed_barrier_steps=set(),
        actors={"actor-1": {"actor_id": "actor-1"}},
        ops={"op-1": op},
        tokens={},
        runtime_bindings={},
        activation_requirements={"control": [], "treatment": ["treatment_1"]},
        observations={},
        eid="exp-1",
        oid="obl-1",
        resolved_campaign_id="CMP-1",
        resolved_execution_id="exec-1",
        campaign_id="CMP-1",
        root=Path("/tmp"),
        project="proj-1",
        base_url="http://target.invalid",
        runtime_contract={},
    )
    blocked = [
        s
        for s in result["steps"]
        if str(s.get("skipped_reason", "")).startswith(
            "BLOCKED_MISSING_REQUIRED_BODY_FIELDS"
        )
    ]
    assert blocked, result
    assert "sku" in blocked[0]["skipped_reason"]
