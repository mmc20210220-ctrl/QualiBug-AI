# -*- coding: utf-8 -*-
"""Semantic contract binding adapter — unit tests.

The adapter repairs the CJK rule → interface binding gap for interface-
documented business contracts (per-endpoint 关键契约 lines) and the
conservation-equation modeling defect. Tests assert the generic behavior on a
small synthetic API document + PRD + schema so no benchmark-specific data is
needed: an industry-neutral 订单/支付-like micro document exercises the same
channels (section line range, verbatim containment, CJK action terms) and
expression structuring (field equality, upper bound, privacy rekind, state
transition binding).
"""
from __future__ import annotations

import re
from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._api import (
    build_runtime_source_knowledge_overlay,
)
from ai_test_asset_center.enterprise_knowledge_center.semantic_contract_binding import (
    apply_semantic_contract_binding,
)

# ── Synthetic industry-neutral micro document (generic 订单/支付 style) ──
API_DOC = """# 示例 API 文档

## 支付服务

### POST /api/payments/pay

支付订单

请求：
```json
{"orderId":"<order_id>","amount":6899,"idempotencyKey":"abc-001"}
```

- **关键契约**：支付金额必须等于应付金额；同一幂等键不得重复扣款。
- **关键契约**：支付成功后必须将订单状态置为 PAID。
- **状态码**：200/201 成功；409 幂等冲突。

### POST /api/payments/refund

退款

请求：
```json
{"orderId":"<order_id>","amount":100}
```

- **关键契约**：退款金额不得超过实际支付金额；重复退款必须幂等。

### POST /api/payments/channel-test

支付通道连通性测试

请求：
```json
{"channel":"MOCK"}
```

- **关键契约**：响应不得返回支付密钥或签名密钥。

### GET /api/orders

查询订单列表。
"""

PRD = """# 产品需求

## 3.2 支付

1. 订单必须处于 `PENDING_PAYMENT` 状态才能支付；
2. 支付金额必须等于订单应付金额；
3. 同一订单只能成功支付一次；
4. 支付成功后订单状态变为 `PAID`。

## 订单状态机

```txt
CREATED -> PENDING_PAYMENT -> PAID
PENDING_PAYMENT -> CANCELLED
```

禁止状态流转：

- CANCELLED -> PAID
- REFUNDED -> PAID
"""

SCHEMA = """CREATE TABLE orders (
  id UUID PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('CREATED','PENDING_PAYMENT','PAID','CANCELLED')),
  payable_amount NUMERIC(12,2) NOT NULL
);
CREATE TABLE payments (
  id UUID PRIMARY KEY,
  order_id UUID NOT NULL REFERENCES orders(id),
  amount NUMERIC(12,2) NOT NULL,
  idempotency_key TEXT
);
"""


def _overlay() -> dict[str, Any]:
    return build_runtime_source_knowledge_overlay(
        prd_text=PRD,
        api_spec_text=API_DOC,
        db_schema_text=SCHEMA,
    )


def _synthetic_interfaces(api_text: str, source_id: str) -> list[dict[str, Any]]:
    """Build interface rows the way the markdown parser would (### form).

    The parser truncates each section excerpt (``section[:900]``); contract
    lines documented at the END of a section routinely fall outside the
    excerpt — exactly the gap the adapter repairs. The synthetic excerpt is
    therefore cut well before the 关键契约 lines.
    """
    header_re = re.compile(r"^###\s+`?([A-Z]+)\s+(/api/[^\s`]+)`?", re.M)
    matches = list(header_re.finditer(api_text))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(api_text)
        section = api_text[start:end]
        method = match.group(1).upper()
        path = match.group(2)
        summary = next(
            (
                line.strip(" #-*")
                for line in section.splitlines()
                if line.strip() and not line.strip().startswith("|")
            ),
            f"{method} {path}",
        )
        rows.append({
            "interface_id": f"markdown_api:{method}:{path}",
            "source_id": source_id,
            "method": method,
            "path": path,
            "summary": summary,
            # OpenAPI-style description carrying the endpoint's own contract
            # declaration (业务约束/关键契约 lines), like the runtime corpus.
            # The final line exists only in the description — the markdown
            # extractor never sees it, so the materializer must create it.
            "description": section[:600] + "\n\n业务约束：支付失败时必须返回明确错误码，不得静默成功。",
            # Truncated before the contract lines, like the runtime parser.
            "source_excerpt": (match.group(0) + "\n" + section[:120]).strip()[:900],
        })
    return rows


def _apply() -> tuple[dict[str, Any], dict[str, Any]]:
    overlay = _overlay()
    overlay["interfaces"] = _synthetic_interfaces(API_DOC, "runtime:markdown_api:test")
    for rule in overlay.get("rule_library") or []:
        if "markdown_api" in str(rule.get("source_id") or ""):
            rule["source_id"] = "runtime:markdown_api:test"
    # Edges computed by the overlay against its original (untruncated)
    # interfaces are stale after the replacement — start from the overlay's
    # own rule/interface inventory only, as the runtime planning path does.
    overlay["relationships"] = []
    return apply_semantic_contract_binding(overlay, api_spec_text=API_DOC), overlay


def test_interface_contract_rules_bind_to_their_endpoints() -> None:
    asset, _ = _apply()
    receipt = asset.get("semantic_contract_binding_receipt") or {}
    assert receipt.get("edge_count", 0) >= 3

    by_rule: dict[str, set[str]] = {}
    for edge in asset.get("relationships") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("relation") or "") != "rule_to_interface":
            continue
        if str(edge.get("derivation")) != "interface_contract_attachment":
            continue
        by_rule.setdefault(str(edge.get("from")), set()).add(str(edge.get("to")))

    statements = {
        str(rule.get("rule_id")): str(rule.get("statement"))
        for rule in asset.get("rule_library") or []
    }
    pay_edges = set()
    for rule_id, targets in by_rule.items():
        statement = statements.get(rule_id, "")
        if "同一幂等键不得重复扣款" in statement or "支付金额必须等于应付金额" in statement:
            pay_edges |= targets
    assert "markdown_api:POST:/api/payments/pay" in pay_edges

    refund_targets = set()
    for rule_id, targets in by_rule.items():
        statement = statements.get(rule_id, "")
        if "重复退款必须幂等" in statement or "退款金额不得超过" in statement:
            refund_targets |= targets
    assert "markdown_api:POST:/api/payments/refund" in refund_targets

    secret_targets = set()
    for rule_id, targets in by_rule.items():
        statement = statements.get(rule_id, "")
        if "响应不得返回支付密钥" in statement:
            secret_targets |= targets
    assert "markdown_api:POST:/api/payments/channel-test" in secret_targets


def test_edges_carry_evidence_and_are_accepted() -> None:
    asset, _ = _apply()
    for edge in asset.get("relationships") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("derivation")) != "interface_contract_attachment":
            continue
        assert str(edge.get("status")) == "accepted"
        assert str(edge.get("evidence_gate")) == "interface_contract_attachment"
        assert edge.get("evidence")
        assert edge.get("operation_locator") or (
            edge.get("evidence", {}).get("operation_locator")
        ) or (edge.get("evidence", {}).get("source_line"))
        assert str(edge.get("relation")) == "rule_to_interface"


def test_conservation_equation_structured_to_two_terms() -> None:
    asset, _ = _apply()
    structured = [
        rule
        for rule in asset.get("rule_library") or []
        if isinstance(rule, dict) and rule.get("equation")
    ]
    assert structured, "expected at least one structured conservation equation"
    saw_equality = False
    for rule in structured:
        equation = rule.get("equation") or {}
        terms = equation.get("terms") or []
        operator = equation.get("operator")
        assert operator in {
            "field_equality",
            "upper_bound",
            "non_negative",
        }
        if operator == "non_negative":
            assert len(terms) == 1
        else:
            # The 支付金额必须等于… contract resolves to a two-sided equality.
            assert len(terms) == 2
            if operator == "field_equality":
                saw_equality = True
    assert saw_equality


def test_sensitive_response_contract_routed_to_privacy() -> None:
    asset, _ = _apply()
    privacy_rules = [
        rule
        for rule in asset.get("rule_library") or []
        if isinstance(rule, dict)
        and str(rule.get("kind") or rule.get("risk_type")) == "privacy"
    ]
    assert any(
        "密钥" in str(rule.get("statement") or "")
        for rule in privacy_rules
    )


def test_state_transitions_bound_to_to_state_performer() -> None:
    asset, _ = _apply()
    bound = []
    for machine in asset.get("state_machines") or []:
        for key in ("transitions", "forbidden_transitions"):
            for transition in machine.get(key) or []:
                if isinstance(transition, dict) and transition.get("operation_ref"):
                    bound.append((
                        key,
                        transition.get("from"),
                        transition.get("to"),
                        transition.get("operation_ref"),
                    ))
    # The 置为 PAID effect contract must bind the PAID transitions (allowed
    # and forbidden) to the payment write operation.
    paid_bindings = [b for b in bound if str(b[2]).upper() == "PAID"]
    assert paid_bindings
    assert any(
        "/api/payments/" in str(b[3]) for b in paid_bindings
    )


def test_cross_cutting_annotation_does_not_flood_binding() -> None:
    """A statement repeated across many endpoints is not endpoint evidence."""
    api_text = API_DOC + """

### GET /api/orders/one

查询订单列表，普通用户只能使用自己的 ID。

### GET /api/orders/two

查询订单列表，普通用户只能使用自己的 ID。

### GET /api/orders/three

查询订单列表，普通用户只能使用自己的 ID。

### GET /api/orders/four

查询订单列表，普通用户只能使用自己的 ID。

### GET /api/orders/five

查询订单列表，普通用户只能使用自己的 ID。

### GET /api/orders/six

查询订单列表，普通用户只能使用自己的 ID。

### GET /api/orders/seven

查询订单列表，普通用户只能使用自己的 ID。
"""
    overlay = build_runtime_source_knowledge_overlay(
        prd_text=PRD,
        api_spec_text=api_text,
        db_schema_text=SCHEMA,
    )
    overlay["interfaces"] = _synthetic_interfaces(api_text, "runtime:markdown_api:test")
    for rule in overlay.get("rule_library") or []:
        if "markdown_api" in str(rule.get("source_id") or ""):
            rule["source_id"] = "runtime:markdown_api:test"
    asset = apply_semantic_contract_binding(overlay, api_spec_text=api_text)
    for edge in asset.get("relationships") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("derivation")) != "interface_contract_attachment":
            continue
        assert "只能使用自己的 ID" not in str(edge.get("evidence"))


def test_prd_rule_binds_via_cjk_action_term_channel() -> None:
    asset, _ = _apply()
    statements = {
        str(rule.get("rule_id")): str(rule.get("statement"))
        for rule in asset.get("rule_library") or []
    }
    pay_bound = False
    for edge in asset.get("relationships") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("to")) == "markdown_api:POST:/api/payments/pay":
            statement = statements.get(str(edge.get("from")), "")
            if "同一订单只能成功支付一次" in statement:
                pay_bound = True
    assert pay_bound


def test_openapi_description_contracts_materialized_as_rules() -> None:
    """Interface-declared contracts (OpenAPI description 业务约束 lines) that
    no extractor turns into rules must be materialized as operation-attached
    rules with accepted edges — the contract semantics then enter the same
    rule_to_interface channel as every other rule."""
    asset, _ = _apply()
    materialized = [
        rule
        for rule in asset.get("rule_library") or []
        if isinstance(rule, dict)
        and str(rule.get("rule_id", "")).startswith("rule:interface_contract")
    ]
    assert materialized, "expected materialized interface-contract rules"
    for rule in materialized:
        assert "#interface=" in str(rule.get("source_locator"))
        # Each materialized rule rides an accepted edge to its own interface.
        bound = [
            edge
            for edge in asset.get("relationships") or []
            if isinstance(edge, dict)
            and str(edge.get("from")) == str(rule.get("rule_id"))
            and str(edge.get("status")) == "accepted"
        ]
        assert bound
    # The description-only contract (never seen by the markdown extractor)
    # must be materialized and bound to a payment write interface.
    desc_only = [
        rule
        for rule in materialized
        if "不得静默成功" in str(rule.get("statement"))
    ]
    assert desc_only
    assert any(
        str(edge.get("to")) == "markdown_api:POST:/api/payments/pay"
        for edge in asset.get("relationships") or []
        if isinstance(edge, dict)
        and str(edge.get("from")) == str(desc_only[0].get("rule_id"))
    )
