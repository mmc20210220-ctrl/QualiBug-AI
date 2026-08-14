"""Regression tests: markdown parser must surface request-example template
tokens (e.g. ``<order_id>`` / ``<address_id>``) as ``request_schema.content`` so
the binding graph can detect body placeholders and either resolve them or
fail-fast with BLOCKED_MISSING_BINDING.

Before the fix (§8.5 series) write methods without a field-dictionary table
never emitted ``request_schema`` at all, so ``build_binding_plan`` saw zero
placeholders and silently let literal ``<order_id>`` tokens reach the target
as transport failures.  See FUNNEL_ROOTCAUSE_20260802 (120 BLOCKED_MISSING_BINDING).
"""
from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._parsing import (
    _markdown_api_operations,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.runtime_binding_graph import build_binding_plan


_SPEC = """
### POST /api/orders

请求：

```json
{
  "items": [{"sku": "SKU-PHONE-001", "qty": 1}],
  "couponCode": "NEW100",
  "addressId": "<address_id>"
}
```

### GET /api/orders

查询订单列表。

### GET /api/orders/:id

查询订单。

### GET /api/users/addresses

查询地址列表。
"""


def _ops(text: str) -> dict[str, dict]:
    return {
        f"{o['method']} {o['path']}": o
        for o in _markdown_api_operations(text, source_id="unit")
    }


def test_write_op_emits_request_schema_with_example_tokens() -> None:
    ops = _ops(_SPEC)
    op = ops["POST /api/orders"]
    schema = op.get("request_schema")
    assert isinstance(schema, dict)
    content = schema.get("content")
    assert isinstance(content, dict)
    example = content.get("application/json", {}).get("example")
    assert isinstance(example, dict)
    assert example.get("addressId") == "<address_id>"


def test_body_placeholder_detected_and_fail_closed() -> None:
    ops = _ops(_SPEC)
    ir = build_behavior_ir_from_knowledge_asset(None, api_operations=list(ops.values()))
    plan = build_binding_plan(
        operation=ops["POST /api/orders"],
        obligation={},
        behavior_ir=ir,
        available_values={},
    )
    entries = [p for p in plan if p.get("target") == "address_id"]
    # The §8.5 regression guarantee: the placeholder is surfaced in the binding
    # plan and never silently passed through as a literal token.
    assert entries, "address_id placeholder must be detected from request example"
    entry = entries[0]
    # A request-body identity field is only resolvable from a source-declared
    # FK/reference relation. The markdown example alone declares no such relation,
    # so the body-identity authority must fail closed instead of inferring one
    # from the field name.
    assert entry.get("status") == "blocked"
    assert entry.get("blocked_reason") == "BODY_IDENTITY_RELATION_NOT_SOURCE_DECLARED"
    assert (entry.get("resolver_operations") or []) == []


def test_path_placeholder_detected_for_collection_read() -> None:
    ops = _ops(_SPEC)
    ir = build_behavior_ir_from_knowledge_asset(None, api_operations=list(ops.values()))
    plan = build_binding_plan(
        operation=ops["GET /api/orders/:id"],
        obligation={},
        behavior_ir=ir,
        available_values={},
    )
    entries = [p for p in plan if p.get("target") == "id"]
    assert entries, "id path placeholder must be detected"
    assert entries[0].get("status") == "runtime_resolvable"
