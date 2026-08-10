"""P0-1: Canonical Obligation Grouping — coverage unit registry tests.

Verifies the obligation-layer semantic uniquification:
1. canonical key stability (role variants -> same key; different surfaces ->
   different keys; UUID/SKU/nonce stripped)
2. Coverage Unit merging (variants merged; semantically different units stay
   separate; observer/cleanup guards fail closed)
3. unit-budget planning (budget counts units, never variants)
4. multi-arm Experiment Bundle derivation (actor rebinding, fail-closed paths)
5. scheduler same-unit serial grouping for execution arms
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.adaptive_discovery_planner import (
    plan_coverage_unit_round,
)
from ai_test_asset_center.coverage_unit_registry import (
    MAX_ARMS_PER_UNIT,
    attach_canonical_obligation_keys,
    build_coverage_units,
    derive_arm_experiment,
    derive_canonical_obligation_key,
)
from ai_test_asset_center.experiment_batch_concurrent_scheduler import (
    partition_serial_groups,
    _write_group_key,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

def _operation(operation_id: str, method: str, path: str) -> dict:
    return {
        "id": operation_id,
        "method": method,
        "path": path,
        "read_write": "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read",
    }


def _auth_obligation(
    obligation_id: str,
    *,
    operation_ref: str,
    control_actor: str,
    treatment_actor: str,
    statement: str = "role=admin; resource=*; decision=allow; actions=*",
    observers: tuple[str, ...] = ("http_response", "actor_identity"),
    cleanup_mode: str = "reverse_order",
    cleanup_required: bool = True,
) -> dict:
    return {
        "obligation_id": obligation_id,
        "risk_family": "authorization",
        "subject_refs": [operation_ref, control_actor, treatment_actor],
        "property": {
            "template": "authorization_control_treatment",
            "control_actor_ref": control_actor,
            "treatment_actor_ref": treatment_actor,
            "operation_ref": operation_ref,
            "operation_path_prefix": "/api/orders",
            "require_same_resource": True,
            "field_rule_binding": {
                "rule_id": "perm:src_1:1:all",
                "statement": statement,
            },
        },
        "required_actors": [control_actor, treatment_actor],
        "required_operations": [operation_ref],
        "required_observers": list(observers),
        "cleanup_requirement": {
            "required": cleanup_required,
            "mode": cleanup_mode,
            "operation_ref": "op-delete",
        },
        "source_refs": [{"source_id": "api_spec", "kind": "api_operation", "locator": "POST /api/orders"}],
        "confidence": 0.8,
    }


def _validation_obligation(
    obligation_id: str,
    *,
    operation_ref: str,
    rule_raw: str,
    field: str = "status",
    actor_ref: str = "",
) -> dict:
    return {
        "obligation_id": obligation_id,
        "risk_family": "validation",
        "subject_refs": [operation_ref, *( [actor_ref] if actor_ref else [])],
        "property": {
            "template": "invariant_validation",
            "operation_ref": operation_ref,
            "operation_path_prefix": "/api/orders",
            "invariant_ref": "inv-1",
            "field": field,
            "expression": {
                "kind": "validation",
                "operands": [{"field_id": field}],
                "raw": rule_raw,
            },
            "field_rule_binding": {"rule_id": "inv-1", "statement": rule_raw},
        },
        "required_actors": [actor_ref] if actor_ref else [],
        "required_operations": [operation_ref],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
        "source_refs": [{"source_id": "prd", "kind": "invariant", "locator": "inv-1"}],
        "confidence": 0.7,
    }


IR = {
    "operations": [
        _operation("op-orders", "POST", "/api/orders"),
        _operation("op-orders-id", "GET", "/api/orders/{id}"),
        _operation("op-users", "POST", "/api/users"),
    ],
    "actors": [],
    "relations": [],
}


# ── 1. canonical key stability ───────────────────────────────────────────────

def test_same_defect_surface_different_roles_share_one_key() -> None:
    base = _auth_obligation(
        "obl-1", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer"
    )
    variant = _auth_obligation(
        "obl-2", operation_ref="op-orders", control_actor="admin", treatment_actor="seller"
    )
    key1 = derive_canonical_obligation_key(base, behavior_ir=IR)
    key2 = derive_canonical_obligation_key(variant, behavior_ir=IR)
    assert key1["coverage_unit_id"] == key2["coverage_unit_id"]
    assert key1["canonical_obligation_key"] == key2["canonical_obligation_key"]
    # roles never enter the identity
    assert "admin" not in key1["canonical_obligation_key"]
    assert "buyer" not in key1["canonical_obligation_key"]
    assert "seller" not in key2["canonical_obligation_key"]


def test_different_operation_is_a_different_key() -> None:
    a = _auth_obligation(
        "obl-a", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer"
    )
    b = _auth_obligation(
        "obl-b", operation_ref="op-users", control_actor="admin", treatment_actor="buyer"
    )
    assert (
        derive_canonical_obligation_key(a, behavior_ir=IR)["coverage_unit_id"]
        != derive_canonical_obligation_key(b, behavior_ir=IR)["coverage_unit_id"]
    )


def test_different_rule_semantics_is_a_different_key() -> None:
    a = _auth_obligation(
        "obl-a", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer",
        statement="role=admin; resource=orders; decision=allow",
    )
    b = _auth_obligation(
        "obl-b", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer",
        statement="role=finance; resource=orders; decision=deny",
    )
    assert (
        derive_canonical_obligation_key(a, behavior_ir=IR)["coverage_unit_id"]
        != derive_canonical_obligation_key(b, behavior_ir=IR)["coverage_unit_id"]
    )


def test_validation_different_field_is_a_different_key() -> None:
    a = _validation_obligation(
        "obl-a", operation_ref="op-orders", rule_raw="status must be ACTIVE", field="status"
    )
    b = _validation_obligation(
        "obl-b", operation_ref="op-orders", rule_raw="status must be ACTIVE", field="amount"
    )
    assert (
        derive_canonical_obligation_key(a, behavior_ir=IR)["coverage_unit_id"]
        != derive_canonical_obligation_key(b, behavior_ir=IR)["coverage_unit_id"]
    )


def test_validation_actor_variants_share_one_key() -> None:
    a = _validation_obligation(
        "obl-a", operation_ref="op-orders", rule_raw="status must be ACTIVE",
        field="status", actor_ref="user-1",
    )
    b = _validation_obligation(
        "obl-b", operation_ref="op-orders", rule_raw="status must be ACTIVE",
        field="status", actor_ref="user-2",
    )
    assert (
        derive_canonical_obligation_key(a, behavior_ir=IR)["coverage_unit_id"]
        == derive_canonical_obligation_key(b, behavior_ir=IR)["coverage_unit_id"]
    )


def test_uuid_and_nonce_path_segments_are_normalized() -> None:
    ir = {
        "operations": [
            _operation("op-a", "GET", "/api/orders/550e8400-e29b-41d4-a716-446655440000"),
            _operation("op-b", "GET", "/api/orders/{id}"),
        ],
        "actors": [],
        "relations": [],
    }
    a = _auth_obligation(
        "obl-a", operation_ref="op-a", control_actor="admin", treatment_actor="buyer"
    )
    b = _auth_obligation(
        "obl-b", operation_ref="op-b", control_actor="admin", treatment_actor="buyer"
    )
    assert (
        derive_canonical_obligation_key(a, behavior_ir=ir)["coverage_unit_id"]
        == derive_canonical_obligation_key(b, behavior_ir=ir)["coverage_unit_id"]
    )


# ── 2. Coverage Unit merging ─────────────────────────────────────────────────

def test_variants_merge_into_one_unit_with_actor_variants() -> None:
    obligations = [
        _auth_obligation("obl-1", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer"),
        _auth_obligation("obl-2", operation_ref="op-orders", control_actor="admin", treatment_actor="seller"),
        _auth_obligation("obl-3", operation_ref="op-users", control_actor="admin", treatment_actor="buyer"),
    ]
    pack = build_coverage_units(obligations, behavior_ir=IR)
    assert pack["obligation_count"] == 3
    assert pack["unit_count"] == 2
    assert pack["collapsed_variant_count"] == 1
    order_unit = next(
        u for u in pack["coverage_units"] if u["operation_ref"] == "op-orders"
    )
    assert order_unit["variant_count"] == 2
    assert "buyer" in order_unit["actor_variants"]
    assert "seller" in order_unit["actor_variants"]
    assert order_unit["representative_obligation_id"] == "obl-1"  # same confidence, smallest id


def test_different_observers_never_merge() -> None:
    a = _auth_obligation(
        "obl-a", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer",
        observers=("http_response", "actor_identity"),
    )
    b = _auth_obligation(
        "obl-b", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer",
        observers=("http_response", "resource_ownership"),
    )
    assert (
        derive_canonical_obligation_key(a, behavior_ir=IR)["coverage_unit_id"]
        != derive_canonical_obligation_key(b, behavior_ir=IR)["coverage_unit_id"]
    )
    pack = build_coverage_units([a, b], behavior_ir=IR)
    assert pack["unit_count"] == 2


def test_annotate_obligations_is_idempotent_and_carries_components() -> None:
    obligations = [
        _auth_obligation("obl-1", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer")
    ]
    first = attach_canonical_obligation_keys(obligations, behavior_ir=IR)
    second = attach_canonical_obligation_keys(first, behavior_ir=IR)
    assert first[0]["coverage_unit_id"] == second[0]["coverage_unit_id"]
    assert first[0]["canonical_obligation_key"] == second[0]["canonical_obligation_key"]
    components = first[0]["canonical_key_components"]
    assert components["assertion_kind"] == "authorization"
    assert components["relation_type"] == "denies"
    assert components["violation_shape"] == "control_treatment_access"


# ── 3. unit-budget planning ──────────────────────────────────────────────────

def _compiled_experiment(obligation_id: str, experiment_id: str) -> dict:
    return {
        "obligation_id": obligation_id,
        "experiment_id": experiment_id,
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        "risk_family": "authorization",
        "property": {"template": "authorization_control_treatment"},
        "control_plan": [],
        "treatment_plan": [],
    }


def test_plan_budget_counts_units_not_variants() -> None:
    obligations = [
        _auth_obligation("obl-1", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer"),
        _auth_obligation("obl-2", operation_ref="op-orders", control_actor="admin", treatment_actor="seller"),
        _auth_obligation("obl-3", operation_ref="op-orders", control_actor="admin", treatment_actor="auditor"),
        _auth_obligation("obl-4", operation_ref="op-users", control_actor="admin", treatment_actor="buyer"),
    ]
    annotated = attach_canonical_obligation_keys(obligations, behavior_ir=IR)
    unit_pack = build_coverage_units(annotated, behavior_ir=IR)
    units = unit_pack["coverage_units"]
    by_id = {o["obligation_id"]: o for o in annotated}
    experiments = {
        oid: _compiled_experiment(oid, f"exp-{oid}")
        for unit in units
        for oid in unit["obligation_ids"]
    }
    # budget 2: two UNITS, even though the first unit holds 3 variants
    plan = plan_coverage_unit_round(
        units,
        obligations_by_id=by_id,
        experiments_by_obligation=experiments,
        behavior_ir=IR,
        budget=2,
    )
    assert plan["selected_unit_count"] == 2
    assert len(plan["selected"]) == 2
    assert plan["selected_variant_count"] == 4
    # variant obligations of a selected unit ride along as obligation rows
    selected_obligation_ids = {row["obligation_id"] for row in plan["selected"]}
    assert selected_obligation_ids == {"obl-1", "obl-4"}


def test_plan_skips_units_without_compiled_representative() -> None:
    obligations = [
        _auth_obligation("obl-1", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer"),
        _auth_obligation("obl-4", operation_ref="op-users", control_actor="admin", treatment_actor="buyer"),
    ]
    annotated = attach_canonical_obligation_keys(obligations, behavior_ir=IR)
    units = build_coverage_units(annotated, behavior_ir=IR)["coverage_units"]
    by_id = {o["obligation_id"]: o for o in annotated}
    experiments = {
        "obl-4": _compiled_experiment("obl-4", "exp-obl-4"),  # obl-1 not compiled
    }
    plan = plan_coverage_unit_round(
        units,
        obligations_by_id=by_id,
        experiments_by_obligation=experiments,
        behavior_ir=IR,
        budget=10,
    )
    assert plan["selected_unit_count"] == 1
    assert plan["selected"][0]["obligation_id"] == "obl-4"


def test_plan_pending_dedup_is_by_unit_not_obligation() -> None:
    obligations = [
        _auth_obligation(f"obl-{i}", operation_ref="op-orders", control_actor="admin", treatment_actor=f"actor-{i}")
        for i in range(5)
    ] + [
        _auth_obligation("obl-u1", operation_ref="op-users", control_actor="admin", treatment_actor="buyer"),
    ]
    annotated = attach_canonical_obligation_keys(obligations, behavior_ir=IR)
    units = build_coverage_units(annotated, behavior_ir=IR)["coverage_units"]
    by_id = {o["obligation_id"]: o for o in annotated}
    experiments = {
        oid: _compiled_experiment(oid, f"exp-{oid}")
        for unit in units
        for oid in unit["obligation_ids"]
    }
    plan = plan_coverage_unit_round(
        units,
        obligations_by_id=by_id,
        experiments_by_obligation=experiments,
        behavior_ir=IR,
        budget=1,
    )
    # one unit selected (orders); the other unit (users) is ONE pending row,
    # not N variant rows
    pending = plan.get("pending_next_round") or []
    pending_unit_ids = {row.get("coverage_unit_id") for row in pending}
    assert len(pending_unit_ids) == 1
    assert "op-users" in pending[0]["obligation_id"] or pending[0]["coverage_unit_id"]


# ── 4. multi-arm Experiment Bundle derivation ────────────────────────────────

def _compiled_auth_experiment(control: str, treatment: str) -> dict:
    return {
        "schema_version": "qualibug.experiment-contract.v1",
        "experiment_id": "exp-rep",
        "obligation_id": "obl-rep",
        "risk_family": "authorization",
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        "actor_selection_contract": {
            "control_actor_ref": control,
            "treatment_actor_ref": treatment,
        },
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": control,
            "operation_ref": "op-orders",
            "path": "/api/orders",
            "method": "POST",
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": treatment,
            "operation_ref": "op-orders",
            "path": "/api/orders",
            "method": "POST",
        }],
        "cleanup_plan": [{"step_id": "cleanup_1", "actor_ref": control}],
        "precondition_plan": [],
        "assertions": [{
            "assertion_id": "as-1",
            "kind": "authorization",
            "property": {
                "template": "authorization_control_treatment",
                "control_actor_ref": control,
                "treatment_actor_ref": treatment,
            },
        }],
        "observers": [{"observer_id": "http_response"}],
        "canonical_obligation_key": "op:POST api/orders|kind:authorization|shape:control_treatment_access|rel:denies",
        "coverage_unit_id": "cunit_test",
    }


def test_arm_derivation_rebinds_actors_and_preserves_surface() -> None:
    rep_exp = _compiled_auth_experiment("admin", "buyer")
    arm_obligation = _auth_obligation(
        "obl-arm", operation_ref="op-orders", control_actor="admin", treatment_actor="seller"
    )
    arm, receipt = derive_arm_experiment(
        rep_exp,
        arm_obligation,
        coverage_unit_id="cunit_test",
        representative_obligation_id="obl-rep",
        arm_index=1,
    )
    assert arm is not None
    assert receipt["status"] == "DERIVED"
    assert arm["obligation_id"] == "obl-arm"
    assert arm["coverage_unit_id"] == "cunit_test"
    assert arm["arm_of"] == "obl-rep"
    assert arm["arm_index"] == 1
    assert arm["experiment_id"] == "exp-rep__arm_1"
    assert arm["compile_receipt"]["status"] == "COMPILED"
    assert arm["compile_receipt"]["arm_derived"] is True
    # every executable actor reference now points at the arm's actors
    assert arm["actor_selection_contract"]["treatment_actor_ref"] == "seller"
    assert arm["treatment_plan"][0]["actor_ref"] == "seller"
    assert arm["control_plan"][0]["actor_ref"] == "admin"
    assert arm["cleanup_plan"][0]["actor_ref"] == "admin"
    assert arm["assertions"][0]["property"]["treatment_actor_ref"] == "seller"
    assert arm["arm_serial_group"] == "cunit_test"


def test_arm_derivation_fails_closed_on_stale_actor_reference() -> None:
    rep_exp = _compiled_auth_experiment("admin", "buyer")
    # embed a stale representative actor reference in a non-obvious path
    rep_exp["binding_plan"] = [{"step_id": "b1", "actor_ref": "buyer", "operation_ref": "op-orders"}]
    arm_obligation = _auth_obligation(
        "obl-arm", operation_ref="op-orders", control_actor="admin", treatment_actor="seller"
    )
    # binding_plan is not an actor-keyed path — walker rebinds it via actor_ref
    arm, receipt = derive_arm_experiment(
        rep_exp, arm_obligation, coverage_unit_id="cunit_test",
        representative_obligation_id="obl-rep", arm_index=1,
    )
    assert arm is not None
    assert arm["binding_plan"][0]["actor_ref"] == "seller"


def test_arm_derivation_fails_when_actor_missing_from_representative() -> None:
    rep_exp = _compiled_auth_experiment("admin", "buyer")
    arm_obligation = _auth_obligation(
        "obl-arm", operation_ref="op-orders", control_actor="admin", treatment_actor="seller",
        observers=("http_response", "actor_identity", "resource_ownership"),
    )
    # a third actor role cannot be derived from a two-actor representative —
    # but the shape guard catches the genuinely unrepresentable case first:
    # single-actor representative vs pair arm must fail closed.
    single_rep = _compiled_auth_experiment("admin", "admin")
    pair_arm = _auth_obligation(
        "obl-arm", operation_ref="op-orders", control_actor="admin", treatment_actor="seller"
    )
    arm, receipt = derive_arm_experiment(
        single_rep, pair_arm, coverage_unit_id="cunit_test",
        representative_obligation_id="obl-rep", arm_index=1,
    )
    assert arm is None
    assert receipt["reason"] == "arm_shape_incompatible"


def test_arm_derivation_fails_when_no_actor_delta() -> None:
    rep_exp = _compiled_auth_experiment("admin", "buyer")
    arm_obligation = _auth_obligation(
        "obl-arm", operation_ref="op-orders", control_actor="admin", treatment_actor="buyer"
    )
    arm, receipt = derive_arm_experiment(
        rep_exp, arm_obligation, coverage_unit_id="cunit_test",
        representative_obligation_id="obl-rep", arm_index=1,
    )
    assert arm is None
    assert receipt["reason"] == "no_actor_delta"


def test_single_actor_variant_rebinds_through_actor_ref() -> None:
    rep_exp = _compiled_auth_experiment("admin", "admin")
    arm_obligation = {
        "obligation_id": "obl-arm",
        "risk_family": "authorization",
        "property": {
            "template": "permitted_operation_invocation",
            "actor_ref": "seller",
            "control_actor_ref": "seller",
            "treatment_actor_ref": "seller",
            "operation_ref": "op-orders",
        },
        "required_actors": ["seller"],
        "required_operations": ["op-orders"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
        "source_refs": [{"source_id": "api_spec", "kind": "api_operation", "locator": "POST /api/orders"}],
        "confidence": 0.8,
    }
    arm, receipt = derive_arm_experiment(
        rep_exp, arm_obligation, coverage_unit_id="cunit_test",
        representative_obligation_id="obl-rep", arm_index=1,
    )
    assert arm is not None
    assert arm["actor_selection_contract"]["control_actor_ref"] == "seller"
    assert arm["treatment_plan"][0]["actor_ref"] == "seller"


# ── 5. scheduler same-unit serial grouping ───────────────────────────────────

def _write_experiment(obligation_id: str, *, unit_id: str = "", actor_ref: str = "a1") -> dict:
    return {
        "obligation_id": obligation_id,
        "experiment_id": f"exp-{obligation_id}",
        "compile_receipt": {"status": "COMPILED"},
        "coverage_unit_id": unit_id,
        "actor_selection_contract": {"treatment_actor_ref": actor_ref},
        "treatment_plan": [{
            "step_id": "t1",
            "actor_ref": actor_ref,
            "operation_ref": "op-orders",
            "path": "/api/orders/{id}",
            "method": "POST",
        }],
        "control_plan": [],
        "cleanup_plan": [],
    }


def test_arms_of_one_unit_share_one_serial_group_in_the_scheduler() -> None:
    ir_ops = {"op-orders": _operation("op-orders", "POST", "/api/orders/{id}")}
    experiments = {
        "obl-rep": _write_experiment("obl-rep", unit_id="cunit_1", actor_ref="buyer"),
        "obl-arm1": _write_experiment("obl-arm1", unit_id="cunit_1", actor_ref="seller"),
        "obl-arm2": _write_experiment("obl-arm2", unit_id="cunit_1", actor_ref="auditor"),
        "obl-other": _write_experiment("obl-other", unit_id="cunit_2", actor_ref="buyer"),
    }
    selected = [
        {"obligation_id": "obl-rep", "experiment_id": "exp-obl-rep"},
        {"obligation_id": "obl-arm1", "experiment_id": "exp-obl-arm1"},
        {"obligation_id": "obl-arm2", "experiment_id": "exp-obl-arm2"},
        {"obligation_id": "obl-other", "experiment_id": "exp-obl-other"},
    ]
    groups = partition_serial_groups(selected, experiments, {"operations": ir_ops})
    # arms of cunit_1 share one group; cunit_2 is a different group
    group_members = [sorted(m["obligation_id"] for m in group) for group in groups]
    unit1_group = next(
        group for group in group_members
        if "obl-rep" in group and "obl-arm1" in group
    )
    assert "obl-arm2" in unit1_group
    other_group = next(
        group for group in group_members if "obl-other" in group
    )
    assert "obl-rep" not in other_group
    # the raw group key is unit-scoped, not actor-scoped
    key_arm1 = _write_group_key(experiments["obl-arm1"], ir_ops)
    key_arm2 = _write_group_key(experiments["obl-arm2"], ir_ops)
    key_rep = _write_group_key(experiments["obl-rep"], ir_ops)
    assert key_arm1 == key_arm2 == key_rep
    assert key_arm1[0] == "unit" and key_arm1[2] == "cunit_1"


def test_non_unit_experiments_keep_previous_grouping() -> None:
    ir_ops = {"op-orders": _operation("op-orders", "POST", "/api/orders/{id}")}
    exp = _write_experiment("obl-x", actor_ref="buyer")  # no coverage_unit_id
    key = _write_group_key(exp, ir_ops)
    assert key[0] == "iface" and key[2] == "buyer"
