from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center._parsing import (
    _markdown_api_operations,
)
from ai_test_asset_center.experiment_plan_step_executor_core import (
    execute_non_barrier_plans,
)
from ai_test_asset_center.experiment_runtime_support import (
    _foreign_key_field_names,
    _foreign_key_violations,
)


# ---------------------------------------------------------------------------
# Unit tests for the shared helpers (the actual new logic)
# ---------------------------------------------------------------------------


def test_fk_field_names_reads_x_foreign_key() -> None:
    op = {
        "request_schema": {
            "properties": {
                "userId": {"x-foreign-key": True},
                "sku": {},
            }
        }
    }
    assert _foreign_key_field_names(op) == ["userId"]


def test_fk_field_names_safe_when_no_contract() -> None:
    assert _foreign_key_field_names({}) == []
    assert _foreign_key_field_names({"request_schema": {}}) == []


def test_fk_violations_blocks_fabricated_values() -> None:
    op = {
        "request_schema": {
            "properties": {
                "userId": {"x-foreign-key": True},
                "couponCode": {"x-foreign-key": True},
            }
        }
    }
    # integer default 1 -> fabricated
    assert _foreign_key_violations({"userId": 1}, op) == ["userId"]
    # bare string "1" -> fabricated default (FK ids are UUID/code-shaped)
    assert _foreign_key_violations({"userId": "1"}, op) == ["userId"]
    # embedded placeholder "<user_id>"
    assert _foreign_key_violations({"userId": "<user_id>"}, op) == ["userId"]
    # sentinel word
    assert _foreign_key_violations({"couponCode": "none"}, op) == ["couponCode"]
    assert _foreign_key_violations({"couponCode": "FAKE"}, op) == ["couponCode"]


def test_fk_violations_accepts_real_values() -> None:
    op = {
        "request_schema": {
            "properties": {
                "userId": {"x-foreign-key": True},
                "couponCode": {"x-foreign-key": True},
            }
        }
    }
    # real-looking values pass
    assert _foreign_key_violations({"userId": "usr-uuid-123", "couponCode": "NEW100"}, op) == []
    # absent optional FK is left to the target (not flagged)
    assert _foreign_key_violations({"userId": "usr-uuid-123"}, op) == []
    # non-FK field with sentinel value is not flagged
    assert _foreign_key_violations({"sku": "test"}, {"request_schema": {"properties": {"sku": {}}}}) == []


def test_fk_violations_safe_when_no_fk_declared() -> None:
    op = {"request_schema": {"properties": {"userId": {}}}}
    assert _foreign_key_violations({"userId": 1}, op) == []


# ---------------------------------------------------------------------------
# Markdown contract: benchmark_mall POST /api/cart/items declares userId FK
# ---------------------------------------------------------------------------


def _benchmark_mall_ops() -> list[dict]:
    spec = Path("platform_inputs/benchmark_mall/API_SPEC.md").read_text(encoding="utf-8")
    return _markdown_api_operations(spec, source_id="benchmark_mall")


def test_markdown_cart_items_declares_user_id_foreign_key() -> None:
    ops = _benchmark_mall_ops()
    cart = [
        o for o in ops
        if o.get("method") == "POST" and o.get("path") == "/api/cart/items"
    ]
    assert cart, "POST /api/cart/items not parsed"
    props = (cart[0].get("request_schema") or {}).get("properties") or {}
    assert "userId" in props, props
    assert props["userId"].get("x-foreign-key") is True


def test_markdown_products_admin_sku_is_not_foreign_key() -> None:
    ops = _benchmark_mall_ops()
    admin = [
        o for o in ops
        if o.get("method") == "POST" and o.get("path") == "/api/products/admin"
    ]
    assert admin, "POST /api/products/admin not parsed"
    props = (admin[0].get("request_schema") or {}).get("properties") or {}
    # sku is required (not-null constraint) but is a natural key, not a FK.
    assert "sku" in props
    assert props["sku"].get("x-foreign-key") is not True


def test_markdown_get_endpoint_has_no_request_schema() -> None:
    ops = _benchmark_mall_ops()
    get_cart = [
        o for o in ops
        if o.get("method") == "GET" and o.get("path") == "/api/cart/items"
    ]
    assert get_cart, "GET /api/cart/items not parsed"
    assert "request_schema" not in get_cart[0]


# ---------------------------------------------------------------------------
# End-to-end: the executor must block a write whose FK body value is fabricated
# ---------------------------------------------------------------------------


def _run_cart_post(body: dict) -> dict:
    op = {
        "method": "POST",
        "path": "/api/cart/items",
        "request_schema": {
            "type": "object",
            "required": ["sku", "userId"],
            "properties": {
                "sku": {"type": "string"},
                "userId": {"type": "string", "x-foreign-key": True},
            },
        },
    }
    return execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=[
            {
                "step_id": "treatment_1",
                "method": "POST",
                "operation_ref": "op-1",
                "actor_ref": "actor-1",
                "path": "/api/cart/items",
                "body": body,
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


def _fk_blocks(result: dict) -> list[dict]:
    return [
        s
        for s in result["steps"]
        if str(s.get("skipped_reason", "")).startswith("BLOCKED_FABRICATED_FOREIGN_KEY")
    ]


def test_executor_blocks_write_with_fabricated_fk() -> None:
    result = _run_cart_post({"sku": "SKU-1", "userId": "1"})
    blocked = _fk_blocks(result)
    assert blocked, result
    assert "userId" in blocked[0]["skipped_reason"]
    assert any(
        r.startswith("fabricated_foreign_key:")
        for r in result["pre_transport_block_reasons"]
    )


def test_executor_blocks_write_with_embedded_placeholder_fk() -> None:
    # Embedded placeholder (not the whole value) slips past the body-placeholder
    # gate but is still caught as a fabricated FK reference.
    result = _run_cart_post({"sku": "SKU-1", "userId": "prefix-<user_id>"})
    blocked = _fk_blocks(result)
    assert blocked, result
    assert "userId" in blocked[0]["skipped_reason"]


def test_executor_does_not_block_real_fk_value() -> None:
    result = _run_cart_post({"sku": "SKU-1", "userId": "usr-uuid-abc"})
    assert not _fk_blocks(result), result
    assert not any(
        r.startswith("fabricated_foreign_key:")
        for r in result["pre_transport_block_reasons"]
    )
