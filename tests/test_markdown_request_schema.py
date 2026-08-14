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


# Self-contained Markdown contract fixture. The parser only surfaces a field as
# ``required`` when the source declares it (a 必填/required column in a field
# table). The real benchmark_mall API_SPEC documents request bodies as JSON
# examples without a field table, so it cannot declare required-ness; this
# fixture supplies the explicit field tables the parser is expected to honor,
# keeping the test independent of any git-ignored runtime source file.
_BENCHMARK_MALL_SPEC = """\
# API 接口文档

### POST /api/auth/login

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 邮箱 |
| password | string | 是 | 密码 |

### POST /api/auth/register

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 邮箱 |
| password | string | 是 | 密码 |
| name | string | 是 | 姓名 |
| phone | string | 是 | 电话 |

### GET /api/products

查询商品列表。

### POST /api/products/admin

后台创建商品。seller/admin 可用。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| sku | string | 是 | SKU |
| name | string | 否 | 名称 |
| price | number | 否 | 价格 |
| stock | number | 否 | 库存 |

### POST /api/cart/items

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| sku | string | 是 | SKU |
| qty | number | 是 | 数量 |
| userId | string | 是 | 用户 |

### POST /api/coupons/validate

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| code | string | 是 | 券码 |
| items | array | 是 | 商品 |
| totalAmount | number | 是 | 总额 |

### POST /api/orders

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| items | array | 是 | 商品 |
| addressId | string | 是 | 地址 |
| couponCode | string | 否 | 优惠券 |

### POST /api/payments/pay

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| orderId | string | 是 | 订单 |
| amount | number | 是 | 金额 |
| channel | string | 是 | 渠道 |
| idempotencyKey | string | 是 | 幂等键 |

### POST /api/refunds

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| orderId | string | 是 | 订单 |
| amount | number | 是 | 金额 |
| reason | string | 是 | 原因 |
"""


def _parse_benchmark_mall() -> list[dict[str, object]]:
    return _markdown_api_operations(_BENCHMARK_MALL_SPEC, source_id="benchmark_mall")


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
