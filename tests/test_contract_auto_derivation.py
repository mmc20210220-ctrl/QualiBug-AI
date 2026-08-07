"""Source-contract auto-derivation (four-link breadth closure, 档位 C).

Covers: latency / stability / event contract detection from source text with
verbatim anchoring, operation + actor binding, declared-contract precedence,
conflicting-claim visibility, methodology-default separation, fail-closed
skips, and the end-to-end path (derive -> Behavior IR invariant -> obligation)
through the installed semantic binding chain.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center import discovery_runtime_planning  # noqa: E402
from ai_test_asset_center.contract_auto_derivation import (  # noqa: E402
    DERIVATION_SCHEMA,
    derive_source_contracts,
)

# Production-shaped runtime actor row (from test_accounts.json / configured
# accounts): no secret_ref on the row itself, so the IR role node falls back
# to "secret_ref:actor:<role>" and is filtered by the executable check,
# leaving exactly one executable account node per role.
_ACTOR = {
    "id": "actor-customer",
    "role": "customer",
    "account_ref": "cust-1",
    "runtime_bound": True,
}

_OPS = [
    {
        "method": "GET",
        "path": "/api/orders",
        "operation_id": "getOrders",
        "source_id": "api_spec",
        "summary": "查询订单列表",
        "description": "查询订单，响应时间不超过 200ms，错误率低于 1%",
    },
    {
        "method": "POST",
        "path": "/api/orders",
        "operation_id": "createOrder",
        "source_id": "api_spec",
        "summary": "创建订单",
        "description": "创建订单，响应时间不超过 150ms",
    },
    {
        "method": "GET",
        "path": "/api/messages",
        "operation_id": "pollMessages",
        "source_id": "api_spec",
        "summary": "轮询消息",
        "description": (
            "支付完成后发送 payment.completed 事件，消费者轮询 "
            "GET /api/messages?processed=false，消息字段 event_id、event_type、order_id"
        ),
    },
    {
        "method": "GET",
        "path": "/api/inventory",
        "operation_id": "getInventory",
        "source_id": "api_spec",
        "summary": "库存",
        "description": "可用性不低于 99.9%",
    },
]


def _derive(asset: dict | None = None, **kwargs) -> tuple[dict, dict]:
    defaults = {
        "api_spec_text": "",
        "prd_text": "",
        "operations": _OPS,
        "runtime_actors": [_ACTOR],
    }
    defaults.update(kwargs)
    return derive_source_contracts(
        asset if asset is not None else {"asset_id": "asset-1"},
        **defaults,
    )


# ---------------------------------------------------------------------------
# Derivation shapes
# ---------------------------------------------------------------------------


def test_latency_and_stability_derive_with_verbatim_anchoring() -> None:
    asset, receipt = _derive()
    assert receipt["schema_version"] == DERIVATION_SCHEMA
    assert receipt["derived"] == {"performance": 1, "stability": 2, "event": 1}

    perf = asset["performance_formal_contracts"]
    assert len(perf) == 1
    row = perf[0]
    assert row["method"] == "GET" and row["operation_path"] == "/api/orders"
    assert row["max_latency_ms"] == 200.0
    assert row["percentile"] == "p95"  # methodology default, not a source claim
    assert row["derivation"] == "auto_detected_from_source"
    assert row["actor_role"] == "customer"
    ref = row["source_refs"][0]
    assert ref["source_id"] == "api_spec"
    assert ref["quote_hash"]
    # verbatim anchor: the quote is a real substring of the source statement
    assert "响应时间不超过 200ms" in ref["quote"]

    stab = {r["operation_path"]: r for r in asset["stability_formal_contracts"]}
    assert set(stab) == {"/api/orders", "/api/inventory"}
    assert stab["/api/orders"]["sample_count"] == 10  # methodology default
    assert stab["/api/orders"]["max_failed_samples"] == 0  # 1% -> floor(10*0.01)
    assert stab["/api/inventory"]["max_failed_samples"] == 0  # 99.9% -> 0.1%


def test_event_contract_derives_only_from_complete_statement() -> None:
    asset, _ = _derive()
    rows = asset["event_formal_contracts"]
    assert len(rows) == 1
    row = rows[0]
    assert row["observer_path"] == "/api/messages"
    assert row["expected_event_type"] == "payment.completed"
    assert row["event_id_field"] == "event_id"
    assert row["event_type_field"] == "event_type"
    assert row["correlation_field"] == "order_id"
    assert row["correlation_query_parameter"] == "processed"
    assert row["correlation_source"] == {"location": "query", "path": "order_id"}
    # methodology defaults, receipted separately from source facts
    assert row["expected_min_count"] == 1
    assert row["expected_max_count"] == 1
    assert row["observation_window_ms"] == 10_000


def test_non_get_head_operation_latency_claim_is_skipped() -> None:
    asset, receipt = _derive()
    skips = [s for s in receipt["skipped"] if s["kind"] == "performance"]
    assert any(s["reason"] == "non_get_head_operation" for s in skips)


def test_methodology_defaults_are_explicit_in_receipt() -> None:
    _, receipt = _derive()
    defaults = receipt["methodology_defaults"]
    assert defaults["performance"]["sample_count"] == 5
    assert defaults["stability"]["sample_count"] == 10
    assert defaults["event"]["observation_window_ms"] == 10_000


# ---------------------------------------------------------------------------
# Precedence / dedup / conflict visibility
# ---------------------------------------------------------------------------


def test_declared_contract_wins_over_derived() -> None:
    asset, receipt = _derive({
        "asset_id": "asset-1",
        "performance_formal_contracts": [
            {"contract_id": "declared-1", "method": "GET", "operation_path": "/api/orders"}
        ],
    })
    assert len(asset["performance_formal_contracts"]) == 1
    assert asset["performance_formal_contracts"][0]["contract_id"] == "declared-1"
    assert any(s["reason"] == "already_declared_contract" for s in receipt["skipped"])


def test_conflicting_derived_claims_are_visible_not_silent() -> None:
    asset, receipt = _derive(prd_text="GET /api/orders 响应时间不超过 300ms")
    conflicts = [s for s in receipt["skipped"] if s["reason"] == "conflicting_derived_claims"]
    assert len(conflicts) == 1
    assert conflicts[0]["prior_value"] == 200.0  # operation-scoped (more precise)
    assert conflicts[0]["skipped_value"] == 300.0  # PRD-scoped
    assert len(asset["performance_formal_contracts"]) == 1


def test_text_scoped_statement_binds_to_exact_operation() -> None:
    clean_ops = [
        {"method": "GET", "path": "/api/orders", "operation_id": "getOrders",
         "source_id": "api_spec", "summary": "x", "description": "y"},
    ]
    asset, receipt = derive_source_contracts(
        {"asset_id": "asset-1"},
        api_spec_text="",
        prd_text="核心接口 GET /api/orders 响应时间不超过 300ms，其他接口无要求。",
        operations=clean_ops,
        runtime_actors=[_ACTOR],
    )
    rows = asset["performance_formal_contracts"]
    assert len(rows) == 1
    assert rows[0]["max_latency_ms"] == 300.0
    assert rows[0]["operation_path"] == "/api/orders"


def test_text_scoped_statement_without_matching_operation_is_skipped() -> None:
    clean_ops = [
        {"method": "GET", "path": "/api/orders", "operation_id": "getOrders",
         "source_id": "api_spec", "summary": "x", "description": "y"},
    ]
    _, receipt = derive_source_contracts(
        {"asset_id": "asset-1"},
        api_spec_text="",
        prd_text="GET /api/nonexistent 响应时间不超过 300ms",
        operations=clean_ops,
        runtime_actors=[_ACTOR],
    )
    assert any(s["reason"] == "operation_not_found_or_ambiguous" for s in receipt["skipped"])


# ---------------------------------------------------------------------------
# Fail-closed / toggles
# ---------------------------------------------------------------------------


def test_vague_statements_derive_nothing() -> None:
    clean_ops = [
        {"method": "GET", "path": "/api/orders", "operation_id": "getOrders",
         "source_id": "api_spec", "summary": "x", "description": "y"},
    ]
    asset, receipt = derive_source_contracts(
        {"asset_id": "asset-1"},
        api_spec_text="",
        prd_text="接口必须稳定、性能要好。",
        operations=clean_ops,
        runtime_actors=[_ACTOR],
    )
    assert receipt["status"] == "NO_CONTRACTS_DERIVED"
    assert "performance_formal_contracts" not in asset
    assert "stability_formal_contracts" not in asset


def test_disabled_and_empty_operation_cases() -> None:
    _, receipt = _derive(enabled=False)
    assert receipt["status"] == "DISABLED"

    asset, receipt = derive_source_contracts(
        {"asset_id": "asset-1"}, api_spec_text="", prd_text="",
        operations=[], runtime_actors=[_ACTOR],
    )
    assert receipt["status"] == "SKIPPED"
    assert asset == {"asset_id": "asset-1"}


# ---------------------------------------------------------------------------
# End-to-end: derive -> Behavior IR invariant -> obligation
# ---------------------------------------------------------------------------


def test_derived_contract_binds_into_ir_and_compiles_obligation() -> None:
    from ai_test_asset_center.discovery_runtime_semantic_binding import (
        build_behavior_ir_with_semantic_operation_bindings,
    )
    from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir

    asset, receipt = _derive()
    assert receipt["status"] == "CONSUMED"

    ir = build_behavior_ir_with_semantic_operation_bindings(
        asset,
        project_id="auto-derivation-e2e",
        api_operations=_OPS,
        runtime_actors=[_ACTOR],
        available_surfaces={"http_api": True},
    )
    invariants = [row for row in ir.get("invariants", []) if isinstance(row, dict)]
    kinds = {str(row.get("expression", {}).get("kind")) for row in invariants}
    assert "latency_budget_contract" in kinds
    assert "read_stability_contract" in kinds
    assert "event_delivery_contract" in kinds
    bound = [
        row for row in invariants
        if str(row.get("expression", {}).get("kind")) == "latency_budget_contract"
        and row.get("binding_status") == "source_identity_bound"
    ]
    assert bound, "derived performance contract must bind with source identity"

    obligations = compile_obligations_from_behavior_ir(
        ir,
        root=str(ROOT),
        project="auto-derivation-e2e",
    )
    families = {
        str(row.get("risk_family"))
        for row in obligations.get("obligations", [])
        if isinstance(row, dict)
    }
    assert "performance_latency" in families
