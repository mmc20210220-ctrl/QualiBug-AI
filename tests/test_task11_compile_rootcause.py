# -*- coding: utf-8 -*-
"""Task 11 — compile-phase root-cause blueprint tests (SPEC-11 evaluated version).

Stage 1: baseline semantic locks + the 4.4 correctness lock.

Baseline semantic locks pin the compile chain's current behavior byte-for-byte
(sha256 of the canonical JSON of the whole compile pack), so the 4.1/4.2/4.3
refactors cannot drift any semantics silently. The 4.4 lock is RED on the
current code: ``prioritize_experiments`` returns its ordering under
``"prioritized"`` but the batch executor reads ``"ordered_experiment_ids"``,
so the ordering has never reached the execution budget.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

from ai_test_asset_center import (
    _experiment_batch_executor_single_finding_mechanics as core_m,
)
from ai_test_asset_center import experiment_executor as executor_m
from ai_test_asset_center import safe_experiment_prioritizer as prio_m
from ai_test_asset_center.experiment_compiler import compile_experiments
from ai_test_asset_center.experiment_compiler_base import (
    _finalize_compiled_experiment,
    compile_experiments as serial_compile,
)

# ── synthetic fixture (real-shaped, deterministic, self-contained) ──────────


def _synthetic_behavior_ir() -> dict[str, Any]:
    """Compact Behavior IR in the shape the compile chain consumes."""
    return {
        "schema_version": "qualibug.behavior-ir.v1",
        "project_id": "synthetic_orders",
        "model_id": "model_synthetic_orders_v1",
        "operations": [
            {
                "id": "op_list_orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "entity_ref": "ent_orders",
                "source_refs": [{
                    "kind": "api_operation", "locator": "GET /api/orders",
                    "source_id": "api_spec",
                }],
            },
            {
                "id": "op_read_order",
                "method": "GET",
                "path": "/api/orders/{order_id}",
                "read_write": "read",
                "entity_ref": "ent_orders",
                "identity_fields": ["order_id"],
                "path_params": ["order_id"],
                "source_refs": [{
                    "kind": "api_operation", "locator": "GET /api/orders/{order_id}",
                    "source_id": "api_spec",
                }],
            },
            {
                "id": "op_create_order",
                "method": "POST",
                "path": "/api/orders",
                "read_write": "write",
                "entity_ref": "ent_orders",
                "request_example": {
                    "orderId": "<order_id>",
                    "amount": 100,
                    "status": "PENDING",
                },
                "source_refs": [{
                    "kind": "api_operation", "locator": "POST /api/orders",
                    "source_id": "api_spec",
                }],
            },
            {
                "id": "op_update_order",
                "method": "PATCH",
                "path": "/api/orders/{order_id}",
                "read_write": "write",
                "entity_ref": "ent_orders",
                "identity_fields": ["order_id"],
                "path_params": ["order_id"],
                "request_example": {
                    "orderId": "<order_id>",
                    "amount": 90,
                },
                "source_refs": [{
                    "kind": "api_operation", "locator": "PATCH /api/orders/{order_id}",
                    "source_id": "api_spec",
                }],
            },
            {
                "id": "op_create_payment",
                "method": "POST",
                "path": "/api/payments",
                "read_write": "write",
                "entity_ref": "ent_payments",
                "request_example": {
                    "orderId": "<order_id>",
                    "idempotencyKey": "<idempotency_key>",
                    "amount": 100,
                },
                "source_refs": [{
                    "kind": "api_operation", "locator": "POST /api/payments",
                    "source_id": "api_spec",
                }],
            },
            {
                "id": "op_read_payment",
                "method": "GET",
                "path": "/api/payments/{payment_id}",
                "read_write": "read",
                "entity_ref": "ent_payments",
                "identity_fields": ["payment_id"],
                "path_params": ["payment_id"],
                "source_refs": [{
                    "kind": "api_operation", "locator": "GET /api/payments/{payment_id}",
                    "source_id": "api_spec",
                }],
            },
            {
                "id": "op_list_inventory",
                "method": "GET",
                "path": "/api/inventory",
                "read_write": "read",
                "entity_ref": "ent_inventory",
                "source_refs": [{
                    "kind": "api_operation", "locator": "GET /api/inventory",
                    "source_id": "api_spec",
                }],
            },
            {
                "id": "op_adjust_inventory",
                "method": "PATCH",
                "path": "/api/inventory/{sku}",
                "read_write": "write",
                "entity_ref": "ent_inventory",
                "identity_fields": ["sku"],
                "path_params": ["sku"],
                "request_example": {
                    "sku": "<sku>",
                    "availableQty": 5,
                },
                "source_refs": [{
                    "kind": "api_operation", "locator": "PATCH /api/inventory/{sku}",
                    "source_id": "api_spec",
                }],
            },
        ],
        "actors": [
            {
                "id": "actor_admin",
                "role": "admin",
                "account_ref": "account:admin",
                "credential_secret_ref": "secret_ref:test_accounts:admin",
                "source_refs": [{
                    "kind": "runtime_actor", "locator": "admin",
                    "source_id": "runtime_actors",
                }],
            },
            {
                "id": "actor_customer",
                "role": "customer",
                "account_ref": "account:customer",
                "credential_secret_ref": "secret_ref:test_accounts:customer",
                "source_refs": [{
                    "kind": "runtime_actor", "locator": "customer",
                    "source_id": "runtime_actors",
                }],
            },
            {
                "id": "actor_manager",
                "role": "manager",
                "account_ref": "account:manager",
                "credential_secret_ref": "secret_ref:test_accounts:manager",
                "source_refs": [{
                    "kind": "runtime_actor", "locator": "manager",
                    "source_id": "runtime_actors",
                }],
            },
        ],
        "entities": [
            {"id": "ent_orders", "name": "orders"},
            {"id": "ent_payments", "name": "payments"},
            {"id": "ent_inventory", "name": "inventory"},
        ],
        "invariants": [
            {
                "id": "inv_payable_amount",
                "kind": "validation",
                "description": "orders.payable_amount must equal the sum of its payments",
                "entity_ref": "ent_orders",
                "operation_refs": ["op_update_order", "op_create_payment"],
                "expression": {
                    "kind": "validation",
                    "operator": "must_hold",
                    "operands": [
                        {"entity_ref": "ent_orders", "field": "payable_amount"},
                        {"entity_ref": "ent_payments", "field": "amount"},
                    ],
                },
                "source_refs": [{
                    "kind": "source_rule", "locator": "rule:payable_amount",
                    "source_id": "src_rule",
                }],
            },
            {
                "id": "inv_inventory_conservation",
                "kind": "conservation",
                "description": "inventory.available_qty is conserved across adjustment",
                "entity_ref": "ent_inventory",
                "operation_refs": ["op_adjust_inventory"],
                "expression": {
                    "kind": "conservation",
                    "equation": {
                        "operator": "unchanged_sum",
                        "terms": ["available_qty", "reserved_qty"],
                    },
                    "operands": [
                        {"entity_ref": "ent_inventory", "field": "available_qty"},
                        {"entity_ref": "ent_inventory", "field": "reserved_qty"},
                    ],
                },
                "source_refs": [{
                    "kind": "source_rule", "locator": "rule:inventory_conservation",
                    "source_id": "src_rule",
                }],
            },
            {
                "id": "inv_payment_idempotency",
                "kind": "idempotency",
                "description": "payments.idempotency_key effects are cardinality-one",
                "entity_ref": "ent_payments",
                "operation_refs": ["op_create_payment"],
                "expression": {
                    "kind": "idempotency",
                    "operator": "unique_effect_cardinality",
                    "operands": [
                        {"entity_ref": "ent_payments", "field": "idempotency_key"},
                    ],
                },
                "source_refs": [{
                    "kind": "source_rule", "locator": "rule:payment_idempotency",
                    "source_id": "src_rule",
                }],
            },
        ],
        "relations": [
            {
                "id": "rel_order_payment",
                "relation_type": "produces",
                "source": "op_create_payment",
                "target": "ent_payments",
                "source_ref": "op_create_payment",
                "from_ref": "op_create_payment",
                "to_ref": "ent_payments",
                "entity_ref": "ent_payments",
            },
            {
                "id": "rel_payment_order",
                "relation_type": "belongs_to",
                "source": "op_create_payment",
                "target": "ent_orders",
                "source_ref": "op_create_payment",
                "from_ref": "op_create_payment",
                "to_ref": "ent_orders",
            },
            {
                "id": "rel_order_read",
                "relation_type": "observes",
                "source": "op_read_order",
                "target": "ent_orders",
                "source_ref": "op_read_order",
                "from_ref": "op_read_order",
                "to_ref": "ent_orders",
            },
            {
                "id": "rel_inventory_observes",
                "relation_type": "observes",
                "source": "op_list_inventory",
                "target": "ent_inventory",
                "source_ref": "op_list_inventory",
                "from_ref": "op_list_inventory",
                "to_ref": "ent_inventory",
            },
            {
                "id": "rel_inventory_adjust",
                "relation_type": "produces",
                "source": "op_adjust_inventory",
                "target": "ent_inventory",
                "source_ref": "op_adjust_inventory",
                "from_ref": "op_adjust_inventory",
                "to_ref": "ent_inventory",
            },
        ],
        "conflicts": [
            {
                "id": "conf_order_perm",
                "status": "conflicting",
                "conflict_type": "permission_decision_conflict",
                "actor_ref": "actor_customer",
                "operation_ref": "op_read_order",
                "permission_decision": "deny",
                "source_ref": "src_rule",
            },
        ],
    }


def _synthetic_obligations() -> list[dict[str, Any]]:
    """Obligations in the real compiler-consumed shape (4 families)."""
    return [
        {
            "schema_version": "qualibug.test-obligation.v1",
            "obligation_id": "obl_synth_authz_read_order",
            "risk_family": "authorization",
            "declared_risk_family": "authorization",
            "confidence": 0.8,
            "compile_status": "PENDING",
            "cleanup_requirement": {"mode": "reverse_order", "required": False},
            "required_operations": ["op_read_order"],
            "required_actors": ["actor_admin", "actor_customer"],
            "required_observers": ["http_response", "actor_identity"],
            "subject_refs": ["op_read_order", "actor_admin", "actor_customer"],
            "relation_refs": ["rel_order_read"],
            "property": {
                "template": "permitted_operation_invocation",
                "operation_ref": "op_read_order",
                "operation_path_prefix": "/api/orders",
                "actor_ref": "actor_admin",
                "control_actor_ref": "actor_admin",
                "treatment_actor_ref": "actor_customer",
            },
            "source_refs": [
                {"kind": "api_operation", "locator": "GET /api/orders/{order_id}", "source_id": "api_spec"},
                {"kind": "permission_matrix", "locator": "admin", "source_id": "src_fbeeb0ac6435e03b"},
            ],
        },
        {
            "schema_version": "qualibug.test-obligation.v1",
            "obligation_id": "obl_synth_validation_amount",
            "risk_family": "validation",
            "declared_risk_family": "validation",
            "confidence": 0.7,
            "compile_status": "PENDING",
            "cleanup_requirement": {"mode": "reverse_order", "required": True},
            "required_operations": ["op_create_order"],
            "required_actors": ["actor_customer"],
            "required_observers": ["http_response"],
            "subject_refs": ["op_create_order", "inv_payable_amount"],
            "relation_refs": ["rel_order_payment"],
            "property": {
                "template": "invariant_validation",
                "operation_ref": "op_create_order",
                "operation_path_prefix": "/api/orders",
                "actor_ref": "actor_customer",
                "control_actor_ref": "actor_customer",
                "treatment_actor_ref": "actor_customer",
                "invariant_ref": "inv_payable_amount",
                "field_rule_binding": {
                    "operation_id": "op_create_order",
                    "rule_id": "rule:payable_amount",
                    "rule_type": "validation",
                    "required_field_ids": ["amount", "payable_amount"],
                    "statement": "orders.payable_amount must equal the sum of its payments",
                    "source_rule_refs": ["rule:payable_amount"],
                    "typed_expression": {
                        "kind": "validation",
                        "operator": "must_hold",
                        "operands": [
                            {"entity_ref": "ent_orders", "field": "payable_amount"},
                            {"entity_ref": "ent_payments", "field": "amount"},
                        ],
                    },
                },
                "expression": {
                    "kind": "validation",
                    "operator": "must_hold",
                    "operands": [
                        {"entity_ref": "ent_orders", "field": "payable_amount"},
                        {"entity_ref": "ent_payments", "field": "amount"},
                    ],
                    "raw": "orders.payable_amount must equal the sum of its payments",
                },
            },
            "source_refs": [
                {"kind": "api_operation", "locator": "POST /api/orders", "source_id": "api_spec"},
                {"kind": "source_rule", "locator": "rule:payable_amount", "source_id": "src_rule"},
            ],
        },
        {
            "schema_version": "qualibug.test-obligation.v1",
            "obligation_id": "obl_synth_conservation_inventory",
            "risk_family": "conservation",
            "declared_risk_family": "conservation",
            "confidence": 0.75,
            "compile_status": "PENDING",
            "cleanup_requirement": {"mode": "reverse_order", "required": True},
            "required_operations": ["op_adjust_inventory"],
            "required_actors": ["actor_manager"],
            "required_observers": ["http_response"],
            "subject_refs": ["op_adjust_inventory", "inv_inventory_conservation"],
            "relation_refs": ["rel_inventory_adjust"],
            "property": {
                "template": "invariant_conservation",
                "operation_ref": "op_adjust_inventory",
                "operation_path_prefix": "/api/inventory",
                "actor_ref": "actor_manager",
                "control_actor_ref": "actor_manager",
                "treatment_actor_ref": "actor_manager",
                "invariant_ref": "inv_inventory_conservation",
                "expression": {
                    "kind": "conservation",
                    "equation": {
                        "operator": "unchanged_sum",
                        "terms": ["available_qty", "reserved_qty"],
                    },
                    "operands": [
                        {"entity_ref": "ent_inventory", "field": "available_qty"},
                        {"entity_ref": "ent_inventory", "field": "reserved_qty"},
                    ],
                    "raw": "inventory.available_qty is conserved across adjustment",
                },
            },
            "source_refs": [
                {"kind": "api_operation", "locator": "PATCH /api/inventory/{sku}", "source_id": "api_spec"},
                {"kind": "source_rule", "locator": "rule:inventory_conservation", "source_id": "src_rule"},
            ],
        },
        {
            "schema_version": "qualibug.test-obligation.v1",
            "obligation_id": "obl_synth_idempotency_payment",
            "risk_family": "idempotency",
            "declared_risk_family": "idempotency",
            "confidence": 0.72,
            "compile_status": "PENDING",
            "cleanup_requirement": {"mode": "reverse_order", "required": True},
            "required_operations": ["op_create_payment"],
            "required_actors": ["actor_customer"],
            "required_observers": ["http_response"],
            "subject_refs": ["op_create_payment", "inv_payment_idempotency"],
            "relation_refs": ["rel_order_payment"],
            "property": {
                "template": "idempotent_effect_cardinality",
                "operation_ref": "op_create_payment",
                "operation_path_prefix": "/api/payments",
                "actor_ref": "actor_customer",
                "control_actor_ref": "actor_customer",
                "treatment_actor_ref": "actor_customer",
                "invariant_ref": "inv_payment_idempotency",
                "expression": {
                    "kind": "idempotency",
                    "operator": "unique_effect_cardinality",
                    "operands": [
                        {"entity_ref": "ent_payments", "field": "idempotency_key"},
                    ],
                    "raw": "payments.idempotency_key effects are cardinality-one",
                },
            },
            "source_refs": [
                {"kind": "api_operation", "locator": "POST /api/payments", "source_id": "api_spec"},
                {"kind": "source_rule", "locator": "rule:payment_idempotency", "source_id": "src_rule"},
            ],
        },
        {
            "schema_version": "qualibug.test-obligation.v1",
            "obligation_id": "obl_synth_authz_admin_orders",
            "risk_family": "authorization",
            "declared_risk_family": "authorization",
            "confidence": 0.8,
            "compile_status": "PENDING",
            "cleanup_requirement": {"mode": "reverse_order", "required": False},
            "required_operations": ["op_update_order"],
            "required_actors": ["actor_admin", "actor_customer"],
            "required_observers": ["http_response", "actor_identity"],
            "subject_refs": ["op_update_order", "actor_admin", "actor_customer"],
            "relation_refs": ["rel_order_payment"],
            "property": {
                "template": "permitted_operation_invocation",
                "operation_ref": "op_update_order",
                "operation_path_prefix": "/api/orders",
                "actor_ref": "actor_admin",
                "control_actor_ref": "actor_admin",
                "treatment_actor_ref": "actor_customer",
            },
            "source_refs": [
                {"kind": "api_operation", "locator": "PATCH /api/orders/{order_id}", "source_id": "api_spec"},
                {"kind": "permission_matrix", "locator": "admin", "source_id": "src_fbeeb0ac6435e03b"},
            ],
        },
    ]


def _strip_volatile(value: Any) -> Any:
    """Remove wall-clock fields (materialization receipt ``created_at``) so the
    semantic lock is byte-stable; the compile chain's only nondeterminism is a
    real timestamp, never logic."""
    if isinstance(value, dict):
        return {
            key: _strip_volatile(child)
            for key, child in value.items()
            if key != "created_at"
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _strip_volatile(value), sort_keys=True, ensure_ascii=False, default=str
        ).encode("utf-8")
    ).hexdigest()


# ── baseline semantic locks ──────────────────────────────────────────────────

def _compile_pack_fingerprint(obligations: list[dict], behavior_ir: dict) -> tuple[dict, str]:
    pack = compile_experiments(
        copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        environment_type="non-production",
        policy_version="v-test",
        available_adapters=frozenset({"http_api"}),
    )
    return pack, _canonical_hash(pack)


def test_compile_baseline_semantics_locked() -> None:
    """The compile chain output is pinned byte-for-byte.

    Any refactor of the compile phase (finalization dedup, batch index reuse,
    token-catalog reuse) must reproduce this exact pack: same experiments,
    same ids, same statuses, same reason codes, same plans.
    """
    behavior_ir = _synthetic_behavior_ir()
    obligations = _synthetic_obligations()
    pack, fingerprint = _compile_pack_fingerprint(obligations, behavior_ir)

    assert pack["schema_version"] == "qualibug.experiment-compile.v1"
    assert pack["compiled_count"] + pack["blocked_count"] + pack["abstract_count"] > 0
    # Every experiment carries a deterministic receipt.
    for exp in (
        pack["experiments"] + pack["blocked_experiments"] + pack["abstract_experiments"]
    ):
        receipt = exp.get("compile_receipt")
        assert isinstance(receipt, dict)
        assert exp.get("experiment_id")
        assert exp.get("obligation_id")
    # Baseline pin (compute and freeze once; any semantic drift breaks this).
    # Volatile wall-clock receipt fields (created_at) are normalized; verified
    # PYTHONHASHSEED-stable after the observer_plan_refs ordering fix.
    # Computed 2026-08-09 from the serial chain on the synthetic fixture:
    # 1 COMPILED / 2 BLOCKED (BLOCKED_CONFLICTING_SOURCE,
    # BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING) / 2 ABSTRACT
    # (BLOCKED_NON_REVERSIBLE_WRITE, BLOCKED_MISSING_BINDING).
    assert fingerprint == "18af9dd71435e13b9e3ac4d96887eeecc781f8f1fc6e2f37e447f7a1414d9628"
    # Explicit per-obligation outcome surface (status / reason / plans).
    by_oid: dict[str, dict] = {}
    for exp in (
        pack["experiments"] + pack["blocked_experiments"] + pack["abstract_experiments"]
    ):
        oid = exp["obligation_id"]
        receipt = exp["compile_receipt"]
        by_oid.setdefault(oid, []).append({
            "experiment_id": exp["experiment_id"],
            "status": receipt.get("status"),
            "reason_code": receipt.get("reason_code"),
            "control_steps": len(exp.get("control_plan") or []),
            "treatment_steps": len(exp.get("treatment_plan") or []),
            "cleanup_steps": len(exp.get("cleanup_plan") or []),
            "precondition_steps": len(exp.get("precondition_plan") or []),
            "observer_count": len(exp.get("observers") or []),
            "assertion_count": len(exp.get("assertions") or []),
            "binding_steps": len(exp.get("binding_plan") or []),
        })
    assert len(by_oid) == len(obligations)
    # The pack groups experiments by status, so ordering must hold within each
    # list (input order preserved), and the three lists together must cover
    # every input obligation.
    order = [o["obligation_id"] for o in obligations]
    seen_oids: set[str] = set()
    for key in ("experiments", "blocked_experiments", "abstract_experiments"):
        list_oids = [exp["obligation_id"] for exp in pack[key]]
        for oid in list_oids:
            assert oid in order, f"{oid} not an input obligation"
        assert list_oids == [o for o in order if o in set(list_oids)], (
            f"{key} not in input obligation order"
        )
        seen_oids.update(list_oids)
    assert seen_oids == set(order)


def test_finalize_compiled_experiment_is_content_idempotent() -> None:
    """Property 4.1 relies on: re-freezing a finalized experiment is a no-op."""
    behavior_ir = _synthetic_behavior_ir()
    pack = serial_compile(
        copy.deepcopy(_synthetic_obligations()),
        behavior_ir=behavior_ir,
        environment_type="non-production",
        policy_version="v-test",
        available_adapters=frozenset({"http_api"}),
    )
    exps = pack["experiments"] + pack["blocked_experiments"] + pack["abstract_experiments"]
    assert exps, "fixture must produce experiments"
    for exp in exps:
        once = _finalize_compiled_experiment(exp, behavior_ir=behavior_ir)
        twice = _finalize_compiled_experiment(once, behavior_ir=behavior_ir)
        assert _canonical_hash(once) == _canonical_hash(twice)


# ── 4.4 correctness lock (RED before the reader-key fix) ─────────────────────

def _prioritized_rows(
    oids: list[str], *, budget: int, quota: int = 1
) -> dict[str, Any]:
    """Deterministic fake scores with a clear high→low order (o0 > o1 > ...)."""
    order = sorted(oids, key=lambda oid: int(oid.split("_")[1]))
    return {
        "schema_version": "qualibug.experiment-priority.v1",
        "total_scored": len(oids),
        "budget": budget,
        "family_quota": quota,
        "prioritized": [
            {
                "obligation_id": oid,
                "experiment_id": f"exp:{oid}",
                "score": float(1000 - index),
                "execution_rank": index + 1,
                "within_budget": index < budget,
                "operation_key": "op:shared",
                "path_prefix": "/api/shared",
                "risk_family": "authorization",
            }
            for index, oid in enumerate(order)
        ],
    }


def _selected_rows(oids: list[str]) -> list[dict]:
    return [
        {
            "obligation_id": oid,
            "experiment_id": f"exp:{oid}",
            "risk_family": "authorization",
            "operation_key": "op:shared",
            "path_prefix": "/api/shared",
        }
        for oid in oids
    ]


def _run_serial_budget(selected: list[dict], *, budget: int, monkeypatch: pytest.MonkeyPatch):
    """Run the real serial batch executor's prioritize→reorder→budget logic with
    a fake per-experiment execution (no transport)."""
    monkeypatch.setattr(
        core_m, "load_actor_tokens", lambda root, project, base_url="": {}
    )

    def fake_execute_one(exp, **kwargs):
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": exp.get("experiment_id"),
            "status": "EXECUTED",
            "reason_code": "",
            "finding": None,
            "execution_receipt": {"status": "EXECUTED"},
        }

    monkeypatch.setattr(executor_m, "execute_one_experiment", fake_execute_one)
    return core_m.execute_selected_experiments(
        [dict(row) for row in selected],
        experiments_by_obligation={
            row["obligation_id"]: {"obligation_id": row["obligation_id"],
                                   "experiment_id": row["experiment_id"]}
            for row in selected
        },
        behavior_ir={"operations": []},
        root=".",
        project="synthetic",
        base_url="",
        runtime_contract={"validation_phase": "small_scale"},
        mainline_run={"campaign_id": "camp-synth"},
        campaign_id="camp-synth",
        experiment_budget=budget,
        validation_phase="small_scale",
    )


def test_prioritizer_ordering_applies_to_serial_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4.4 lock: the prioritizer's ordering must reach the batch budget.

    With budget 20 of 30 rows, the deferred set must be the prioritizer's
    bottom-ranked rows (not the tail of the planner's input order), and the
    executed rows must run in prioritized order.
    """
    oids = [f"obl_{index}" for index in range(30)]
    prioritized = _prioritized_rows(oids, budget=20)
    monkeypatch.setattr(
        prio_m, "prioritize_experiments", lambda **kwargs: copy.deepcopy(prioritized)
    )
    # Planner input order deliberately differs from priority order (priority is
    # o0-highest, input is reversed), so a reader that ignores the prioritizer
    # output fails the assertion.
    result = _run_serial_budget(
        _selected_rows(list(reversed(oids))), budget=20, monkeypatch=monkeypatch
    )

    executed_oids = [
        row.get("selected_obligation_id") or row.get("obligation_id")
        for row in result["results"]
    ]
    prioritized_ids = [row["obligation_id"] for row in prioritized["prioritized"]]
    # Execution order follows prioritized order (o0 .. o19), not the input tail.
    assert executed_oids == prioritized_ids[:20]
    # Deferral follows priority: bottom-ranked rows are deferred, not the input tail.
    deferred_ids = [
        row.get("obligation_id") for row in result.get("budget_deferred", [])
    ]
    assert deferred_ids == prioritized_ids[20:]
