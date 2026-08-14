"""Tests for hypothesis → behavior-slice bridge (mainline unification)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.hypothesis_slice_bridge import (
    hypotheses_to_obligations,
    hypotheses_to_slices,
    hypotheses_to_source_candidates,
)
from ai_test_asset_center.obligation_source_adapter import adapt_source_candidates_to_obligations


def _adapter_ir() -> dict:
    ir = empty_behavior_ir(project_id="adapter-test")
    ir.update({
        "operations": [{
            "id": "op-create-resource",
            "operation_id": "createResource",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
            "source_refs": [{"source_id": "SRC-API"}],
        }],
        "relations": [{
            "id": "relation-produces-resource",
            "relation_type": "produces",
            "from_ref": "op-create-resource",
            "to_ref": "entity-resource",
            "operation_ref": "op-create-resource",
            "actor_ref": "",
            "preconditions": [],
            "effects": [],
            "source_refs": [{"source_id": "SRC-API"}],
        }],
    })
    return ir


def test_adapter_preserves_intent_without_execution_authority() -> None:
    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-1",
            "risk_family": "idempotency",
            "method": "POST",
            "path": "/resources",
            "source_refs": [{"source_id": "SRC-1"}],
            "property": {"template": "idempotent_effect_cardinality"},
        }],
        _adapter_ir(),
    )

    obligation = result["obligations"][0]
    assert obligation["source_refs"]
    assert obligation["required_operations"] == ["op-create-resource"]
    assert obligation["relation_refs"] == ["relation-produces-resource"]
    serialized = json.dumps(result)
    assert "send_request" not in serialized
    assert "gate_passed" not in serialized
    assert "oracle" not in serialized.lower()


def test_adapter_blocks_candidate_without_exact_ir_join() -> None:
    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-unbound",
            "risk_family": "state",
            "method": "POST",
            "path": "/unknown",
            "source_refs": [{"source_id": "SRC-1"}],
        }],
        _adapter_ir(),
    )

    assert result["obligations"] == []
    assert result["coverage_gaps"][0]["code"] == "BLOCKED_MISSING_IR_RELATION"


def test_adapter_preserves_explicit_state_transition_refs() -> None:
    ir = empty_behavior_ir(project_id="adapter-state-test")
    ir.update({
        "operations": [{
            "id": "op-activate",
            "method": "PATCH",
            "path": "/resources/{id}",
            "read_write": "write",
        }],
        "states": [
            {"id": "state-draft", "entity_ref": "entity-resource"},
            {"id": "state-active", "entity_ref": "entity-resource"},
        ],
        "relations": [{
            "id": "relation-activate",
            "relation_type": "transitions",
            "from_ref": "state-draft",
            "to_ref": "state-active",
            "operation_ref": "op-activate",
            "actor_ref": "",
            "preconditions": [],
            "effects": [],
            "source_refs": [{"source_id": "SRC-STATE"}],
        }],
    })

    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-state",
            "risk_family": "state",
            "method": "PATCH",
            "path": "/resources/:resourceId",
            "source_refs": [{"source_id": "SRC-STATE"}],
        }],
        ir,
    )

    obligation = result["obligations"][0]
    assert obligation["property"]["from_state_ref"] == "state-draft"
    assert obligation["property"]["to_state_ref"] == "state-active"
    assert obligation["property"]["entity_ref"] == "entity-resource"


def test_adapter_module_has_no_execution_or_delivery_dependencies(monkeypatch) -> None:
    source = (
        _REPO_ROOT / "ai_test_asset_center" / "obligation_source_adapter.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "experiment_executor",
        "oracle_engine",
        "customer_delivery_gate",
        "requests",
        "urllib",
    ):
        assert forbidden not in source

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("pure adapter attempted HTTP"),
    )
    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-pure",
            "risk_family": "idempotency",
            "method": "POST",
            "path": "/resources",
            "source_refs": [{"source_id": "SRC-1"}],
        }],
        _adapter_ir(),
    )
    assert result["obligations"]


def test_bridge_exposes_source_candidates_and_routes_them_to_adapter() -> None:
    hypotheses = [{
        "hypothesis_id": "hypothesis-1",
        "title": "Repeated create may duplicate the business effect",
        "category": "idempotency",
        "method": "POST",
        "related_endpoints": ["/resources"],
        "source_refs": [{"source_id": "SRC-1", "quote": "create once"}],
    }]
    endpoints = [{
        "operation_id": "createResource",
        "entity": "resource",
        "action": "create",
        "path": "/resources",
        "method": "POST",
    }]

    candidates, funnel = hypotheses_to_source_candidates(
        hypotheses,
        api_endpoints=endpoints,
        origin="llm_reasoner",
    )
    adapted, adapted_funnel = hypotheses_to_obligations(
        hypotheses,
        api_endpoints=endpoints,
        behavior_ir=_adapter_ir(),
        origin="llm_reasoner",
    )

    assert funnel["bound"] == 1
    assert candidates[0]["candidate_id"] == "hypothesis-1"
    assert candidates[0]["path"] == "/resources"
    assert not any(key.endswith("_oracle") for key in candidates[0])
    assert "gate_passed" not in candidates[0]
    assert adapted["obligations"][0]["required_operations"] == ["op-create-resource"]
    assert adapted_funnel["adapted_obligation_count"] == 1


def test_hypotheses_to_slices_binds_real_endpoint_and_drops_unbound():
    api_endpoints = [
        {"entity": "order", "action": "create", "path": "/api/v1/resources", "method": "POST"},
        {"entity": "order", "action": "list", "path": "/api/v1/resources", "method": "GET"},
        {"entity": "payment", "action": "pay", "path": "/api/v1/payments", "method": "POST"},
    ]
    hypotheses = [
        {
            "hypothesis_id": "ana_1",
            "title": "资源创建缺少幂等保护",
            "category": "idempotency",
            "severity": "P1",
            "entity": "order",
            "related_endpoints": ["/api/v1/resources"],
            "expected_behavior": "重复提交不得产生多条资源",
            "_reasoner_engine": "business_rules",
        },
        {
            "hypothesis_id": "ana_unbound",
            "title": "无法绑定的假设",
            "category": "invariant",
            "severity": "P2",
            "entity": "nonexistent_entity_xyz",
            "description": "没有任何可匹配的路径或实体",
        },
    ]

    slices, funnel = hypotheses_to_slices(hypotheses, api_endpoints=api_endpoints, origin="analyzer")

    assert funnel["input"] == 2
    assert funnel["bound"] == 1
    assert funnel["dropped_no_endpoint"] == 1
    assert funnel["dropped_reason_counts"] == {"entity_not_in_catalog": 1}
    assert funnel["dropped_samples"][0]["hypothesis_id"] == "ana_unbound"
    assert funnel["dropped_samples"][0]["reason"] == "entity_not_in_catalog"
    assert "request" not in funnel["dropped_samples"][0]
    assert funnel["by_origin"]["analyzer"]["bound"] == 1
    assert len(slices) == 1

    slice_row = slices[0]
    assert slice_row["endpoints"] == ["/api/v1/resources"]
    assert slice_row["endpoints"][0].startswith("/")
    assert slice_row["source_refs"], "source_refs must be non-empty for source grounding"
    assert slice_row["slice_id"].startswith("BHV_")
    assert slice_row["kind"] in {"invariant", "permission", "isolation", "concurrency", "money"}
    # Oracle field must map to a real oracle class name
    oracle_fields = [k for k in slice_row if k.endswith("_oracle")]
    assert oracle_fields, "slice must carry an oracle binding"
    assert any(
        slice_row[k] in {
            "IdempotencyOracle", "ConsistencyOracle", "PermissionOracle",
            "TenantIsolationOracle", "ConcurrencyOracle", "MoneyOracle",
            "InventoryOracle", "StateOracle", "WorkflowOracle",
            "CacheConsistencyOracle", "TransactionOracle", "AuditOracle",
            "PrivacyOracle",
        }
        for k in oracle_fields
    )


def test_hypotheses_to_slices_binds_via_trigger_and_entity_plural():
    api_endpoints = [
        {"entity": "refund", "action": "approve", "path": "/api/refunds/:id/approve", "method": "POST"},
        {"entity": "order", "action": "list", "path": "/api/orders", "method": "GET"},
    ]
    hypotheses = [
        {
            "hypothesis_id": "hist_1",
            "title": "退款重复审批",
            "category": "concurrency",
            "severity": "P1",
            "entity": "refunds",
            "trigger": "POST /api/refunds/{id}/approve",
        },
        {
            "hypothesis_id": "hist_2",
            "title": "普通用户越权查看订单",
            "category": "permission",
            "severity": "P0",
            "description": "orders endpoint missing ownership check",
            "method": "GET",
        },
    ]
    slices, funnel = hypotheses_to_slices(hypotheses, api_endpoints=api_endpoints, origin="analyzer")
    assert funnel["bound"] == 2
    assert funnel["dropped_no_endpoint"] == 0
    paths = {s["endpoints"][0] for s in slices}
    assert "/api/refunds/:id/approve" in paths
    assert "/api/orders" in paths


def test_hypotheses_to_slices_binds_entity_action_hint_to_endpoint():
    api_endpoints = [
        {"entity": "claim", "action": "approve", "path": "/api/claims/:id/approve", "method": "POST"},
        {"entity": "claim", "action": "list", "path": "/api/claims", "method": "GET"},
        {"entity": "payment", "action": "pay", "path": "/api/payments/pay", "method": "POST"},
    ]
    hypotheses = [
        {
            "hypothesis_id": "entity_action_1",
            "title": "Approval can bypass required checks",
            "category": "invariant",
            "severity": "P1",
            "entity": "Claim.approve",
        },
        {
            "hypothesis_id": "entity_action_2",
            "title": "Payment amount is not conserved",
            "category": "money",
            "severity": "P1",
            "entity": "Payment.pay",
        },
    ]

    slices, funnel = hypotheses_to_slices(hypotheses, api_endpoints=api_endpoints, origin="llm_reasoner")

    assert funnel["bound"] == 2
    paths = {row["_bound_path"] for row in slices}
    assert "/api/claims/:id/approve" in paths
    assert "/api/payments/pay" in paths


def test_hypotheses_to_slices_binds_compound_entity_names_to_path_tokens():
    api_endpoints = [
        {
            "entity": "user",
            "action": "addresses",
            "path": "/api/users/:userId/addresses",
            "method": "GET",
        },
        {
            "entity": "order",
            "action": "list",
            "path": "/api/orders",
            "method": "GET",
        },
        {
            "entity": "product",
            "action": "detail",
            "path": "/api/products/:productId",
            "method": "GET",
        },
    ]
    hypotheses = [
        {
            "hypothesis_id": "compound_snake",
            "title": "Ownership checks can be bypassed",
            "category": "permission",
            "severity": "P1",
            "entity": "user_address",
            "method": "GET",
        },
        {
            "hypothesis_id": "compound_camel",
            "title": "Returned records are inconsistent",
            "category": "invariant",
            "severity": "P2",
            "entity": "OrderList",
            "method": "GET",
        },
    ]

    slices, funnel = hypotheses_to_slices(
        hypotheses, api_endpoints=api_endpoints, origin="llm_reasoner"
    )

    assert funnel["bound"] == 2
    assert funnel["dropped_no_endpoint"] == 0
    assert {row["_bound_path"] for row in slices} == {
        "/api/users/:userId/addresses",
        "/api/orders",
    }


def test_hypotheses_to_slices_empty_catalog_drops_all():
    hypotheses = [
        {
            "hypothesis_id": "x1",
            "title": "有路径但目录为空",
            "related_endpoints": ["/api/v1/resources"],
            "category": "permission",
        }
    ]
    slices, funnel = hypotheses_to_slices(hypotheses, api_endpoints=[], origin="llm_reasoner")
    assert slices == []
    assert funnel["bound"] == 0
    assert funnel["dropped_no_endpoint"] == 1
    assert funnel["dropped_reason_counts"] == {"api_catalog_empty": 1}
    assert funnel["dropped_samples"][0]["path_hints"] == ["/api/v1/resources"]
    assert funnel["origin"] == "llm_reasoner"


def test_hypothesis_drop_reason_distinguishes_missing_operation_and_path():
    api_endpoints = [
        {
            "entity": "order",
            "action": "list",
            "path": "/api/orders",
            "method": "GET",
            "operation_id": "listOrders",
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "missing_op",
            "operation_id": "approveRefund",
            "title": "operation based probe",
        },
        {
            "hypothesis_id": "missing_path",
            "related_endpoints": ["/api/refunds/{id}/approve"],
            "title": "path based probe",
        },
    ]

    slices, funnel = hypotheses_to_slices(
        hypotheses, api_endpoints=api_endpoints, origin="llm_reasoner"
    )

    assert slices == []
    assert funnel["dropped_reason_counts"] == {
        "operation_id_not_in_catalog": 1,
        "path_hint_not_in_catalog": 1,
    }
    assert {row["hypothesis_id"] for row in funnel["dropped_samples"]} == {
        "missing_op",
        "missing_path",
    }


def test_adapter_drops_unregistered_family_as_gap_not_crash() -> None:
    """Regression: an unregistered family used to KeyError the whole adapter.

    Baseline 2026-08-04 crashed at obligation_source_adapter._RELATION_TYPES_BY_FAMILY
    with KeyError 'audit', which aborted the mainline reasoner augmentation and
    discarded every engine's hypotheses.  An unknown family must fail closed per
    candidate with a named coverage-gap code, never abort the loop.
    """
    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-unknown-family",
            "risk_family": "quantum_entanglement",
            "method": "POST",
            "path": "/resources",
            "source_refs": [{"source_id": "SRC-1"}],
        }],
        _adapter_ir(),
    )

    assert result["obligations"] == []
    assert result["coverage_gaps"][0]["code"] == "BLOCKED_RISK_FAMILY_UNSUPPORTED"
    assert "quantum_entanglement" in result["coverage_gaps"][0]["detail"]


def test_bridge_audit_vocabulary_resolves_through_registry() -> None:
    """The Reasoner emits 'audit'; the registry owns its resolution.

    'audit' is the hypothesis-bridge spelling of the audit_trail capability gap
    (no assertion kind or observer exists), so it must resolve to canonical
    'validation' with the capability-gap reason code recorded -- the adapter
    then compiles it as a validation obligation instead of crashing.
    """
    from ai_test_asset_center.test_obligation import resolve_risk_family

    resolved = resolve_risk_family("audit")
    assert resolved["canonical"] == "validation"
    assert resolved["registered"] is True

    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-audit",
            "risk_family": "audit",
            "method": "POST",
            "path": "/resources",
            "source_refs": [{"source_id": "SRC-1"}],
        }],
        _adapter_ir(),
    )
    assert result["obligations"]
    assert result["obligations"][0]["risk_family"] == "validation"


def test_adapter_resolves_role_actor_to_executable_account() -> None:
    """Regression: Reasoner obligations referenced non-executable role actors.

    Permission-matrix role actors carry synthetic ``secret_ref:actor:*``
    credentials, so the binding gate blocked all 603 of them at compile time.
    The adapter must substitute the executable same-role account actor so the
    obligation runs as a real declared identity.
    """
    from ai_test_asset_center.obligation_source_adapter import (
        _executable_actor_for_role,
    )

    actors_by_id = {
        "bir_admin_role": {
            "id": "bir_admin_role",
            "role": "admin",
            "role_key": "admin",
            "credential_secret_ref": "secret_ref:actor:admin",
        },
        "bir_admin_account": {
            "id": "bir_admin_account",
            "role": "admin",
            "role_key": "admin",
            "credential_secret_ref": "secret_ref:test_accounts:admin@example.com",
            "account_ref": "admin@example.com",
            "runtime_bound": True,
        },
        "bir_finance_account": {
            "id": "bir_finance_account",
            "role": "finance",
            "role_key": "finance",
            "credential_secret_ref": "secret_ref:test_accounts:finance01@example.com",
            "account_ref": "finance01@example.com",
            "runtime_bound": True,
        },
    }

    assert _executable_actor_for_role("bir_admin_role", actors_by_id) == "bir_admin_account"
    # Already executable actors pass through unchanged.
    assert _executable_actor_for_role("bir_admin_account", actors_by_id) == "bir_admin_account"
    # No executable same-role actor: stays unchanged (blocks visibly later).
    assert _executable_actor_for_role("bir_finance_account", actors_by_id) == "bir_finance_account"


def test_adapter_obligation_uses_executable_actors() -> None:
    """A reasoner obligation pair resolves both refs before make_obligation."""
    ir = empty_behavior_ir(project_id="adapter-actor-test")
    ir.update({
        "operations": [{
            "id": "op-admin-action",
            "method": "POST",
            "path": "/admin/actions",
            "read_write": "write",
            "source_refs": [{"source_id": "SRC-API"}],
        }],
        "actors": [
            {
                "id": "bir_admin_role",
                "role": "admin",
                "role_key": "admin",
                "credential_secret_ref": "secret_ref:actor:admin",
            },
            {
                "id": "bir_admin_account",
                "role": "admin",
                "role_key": "admin",
                "credential_secret_ref": "secret_ref:test_accounts:admin@example.com",
                "account_ref": "admin@example.com",
                "runtime_bound": True,
            },
            {
                "id": "bir_finance_role",
                "role": "finance",
                "role_key": "finance",
                "credential_secret_ref": "secret_ref:actor:finance",
            },
            {
                "id": "bir_finance_account",
                "role": "finance",
                "role_key": "finance",
                "credential_secret_ref": "secret_ref:test_accounts:finance01@example.com",
                "account_ref": "finance01@example.com",
                "runtime_bound": True,
            },
        ],
        "relations": [
            {
                "id": "rel-permits-admin",
                "relation_type": "permits",
                "from_ref": "bir_admin_role",
                "actor_ref": "bir_admin_role",
                "operation_ref": "op-admin-action",
                "preconditions": [],
                "effects": [],
                "source_refs": [{"source_id": "SRC-PERM"}],
            },
            {
                "id": "rel-denies-finance",
                "relation_type": "denies",
                "from_ref": "bir_finance_role",
                "actor_ref": "bir_finance_role",
                "operation_ref": "op-admin-action",
                "preconditions": [],
                "effects": [],
                "source_refs": [{"source_id": "SRC-PERM"}],
            },
        ],
    })

    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-actor",
            "risk_family": "authorization",
            "method": "POST",
            "path": "/admin/actions",
            "source_refs": [{"source_id": "SRC-1"}],
        }],
        ir,
    )

    assert result["obligations"]
    obl = result["obligations"][0]
    assert set(obl["required_actors"]) == {"bir_admin_account", "bir_finance_account"}
    assert obl["property"]["control_actor_ref"] == "bir_admin_account"
    assert obl["property"]["treatment_actor_ref"] == "bir_finance_account"


def test_adapter_carries_depth_and_flags_uncompiled_cascade() -> None:
    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-deep",
            "risk_family": "idempotency",
            "method": "POST",
            "path": "/resources",
            "entity": "resource",
            "source_refs": [{"source_id": "SRC-1"}],
            "depth": {
                "cascade_chain": [{"from": "order", "to": "settlement"}],
                "source_state": "OVERDUE",
            },
        }],
        _adapter_ir(),
    )

    # Depth is carried into the obligation identity (observable end-to-end).
    assert result["depth_carried_count"] == 1
    obligation = result["obligations"][0]
    assert obligation["property"]["depth"]["cascade_chain"]
    assert obligation["property"]["depth"]["source_state"] == "OVERDUE"
    # The cross-entity cascade cannot compile to one operation → explicit gap.
    assert result["depth_uncompiled_count"] == 1
    assert any(
        gap["code"] == "BLOCKED_DEEP_COMPREHENSION_UNCOMPILED"
        and gap["detail"] == "cascade_chain_uncompiled"
        for gap in result["coverage_gaps"]
    )


def test_adapter_flat_candidate_has_no_depth_counters() -> None:
    result = adapt_source_candidates_to_obligations(
        [{
            "candidate_id": "candidate-flat",
            "risk_family": "idempotency",
            "method": "POST",
            "path": "/resources",
            "source_refs": [{"source_id": "SRC-1"}],
        }],
        _adapter_ir(),
    )

    assert result["depth_carried_count"] == 0
    assert result["depth_uncompiled_count"] == 0
    assert not any(
        gap["code"] == "BLOCKED_DEEP_COMPREHENSION_UNCOMPILED"
        for gap in result["coverage_gaps"]
    )
