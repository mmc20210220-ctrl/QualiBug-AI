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
    AgentIntentError,
    build_agent_intent_plan,
    plan_coverage_unit_round,
)
from ai_test_asset_center.coverage_unit_registry import (
    MAX_ARMS_PER_UNIT,
    attach_canonical_obligation_keys,
    build_coverage_units,
    derive_arm_experiment,
    derive_canonical_obligation_key,
)
from ai_test_asset_center.discovery_runtime_planning import (
    derive_unit_execution_arms,
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


# ── 6. P0-1fix: multi-arm identity contract (compiled_experiment_mismatch) ────
# run22 end-to-end failure: coverage-unit representative rows did not carry
# ``experiment_id``, so ``build_agent_intent_plan`` raised
# ``compiled_experiment_mismatch:obl_09706b33eb4cd2762a10`` on the first
# selected row. These tests pin the identity contract end to end:
# every selected row (representative / derived arm / own-compiled variant /
# fallback-compiled variant) must reference exactly the COMPILED experiment
# registered under its obligation_id in the identity index.

IR_WITH_ACTORS = {
    "model_id": "bir-arms",
    "operations": [
        _operation("op-orders", "POST", "/api/orders"),
        _operation("op-orders-id", "GET", "/api/orders/{id}"),
    ],
    "actors": [
        {"id": "admin"},
        {"id": "buyer"},
        {"id": "seller"},
        {"id": "auditor"},
        {"id": "finance"},
        {"id": "owner-x"},
    ],
    "relations": [],
}


def _intent_compiled_experiment(
    obligation_id: str, experiment_id: str, control: str, treatment: str
) -> dict:
    """Compiled experiment that satisfies the intent gate (observers carry
    adapters, source_refs present, property carries operation_path_prefix)."""
    return {
        "schema_version": "qualibug.experiment-contract.v1",
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "risk_family": "authorization",
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        "actor_selection_contract": {
            "control_actor_ref": control,
            "treatment_actor_ref": treatment,
        },
        "property": {"operation_path_prefix": "/api/orders"},
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
        "observers": [
            {"observer_id": "http_response", "adapter": "http_api"},
            {"observer_id": "actor_identity", "adapter": "actor_identity"},
        ],
        "source_refs": [{
            "source_id": "api_spec",
            "kind": "api_operation",
            "locator": "POST /api/orders",
        }],
        "canonical_obligation_key": (
            "op:POST api/orders|kind:authorization|shape:control_treatment_access|rel:denies"
        ),
        "coverage_unit_id": "cunit_repro",
    }


def _intent_chain_obligations() -> list[dict]:
    return [
        _auth_obligation(
            "obl-a1", operation_ref="op-orders",
            control_actor="admin", treatment_actor="buyer",
        ),
        _auth_obligation(
            "obl-a2", operation_ref="op-orders",
            control_actor="admin", treatment_actor="seller",
        ),
        _auth_obligation(
            "obl-a3", operation_ref="op-orders",
            control_actor="admin", treatment_actor="auditor",
        ),
    ]


def _assert_identity_contract(plan, by_obligation) -> None:
    """Every selected row references the COMPILED experiment registered under
    its obligation_id — the contract build_agent_intent_plan enforces."""
    for row in plan["selected"]:
        exp = by_obligation.get(row["obligation_id"])
        assert exp is not None, f"no compiled experiment for {row['obligation_id']}"
        assert exp["experiment_id"] == row["experiment_id"], (
            f"experiment_id mismatch for {row['obligation_id']}: "
            f"index={exp['experiment_id']} row={row['experiment_id']}"
        )
        assert (
            exp.get("compile_receipt", {}).get("status") == "COMPILED"
        ), f"not compiled: {row['obligation_id']}"


def _build_unit_plan(obligations, experiments) -> tuple[dict, list[dict], dict]:
    annotated = attach_canonical_obligation_keys(obligations, behavior_ir=IR_WITH_ACTORS)
    units = build_coverage_units(annotated, behavior_ir=IR_WITH_ACTORS)["coverage_units"]
    by_id = {o["obligation_id"]: o for o in annotated}
    plan = plan_coverage_unit_round(
        units,
        obligations_by_id=by_id,
        experiments_by_obligation=experiments,
        behavior_ir=IR_WITH_ACTORS,
        budget=10,
    )
    return plan, units, by_id


def test_unit_plan_selected_rows_carry_experiment_id() -> None:
    """Regression for run22: representative rows must carry the compiled
    experiment_id, exactly like obligation planning (plan_obligation_round)."""
    obligations = _intent_chain_obligations()
    annotated = attach_canonical_obligation_keys(obligations, behavior_ir=IR_WITH_ACTORS)
    units = build_coverage_units(annotated, behavior_ir=IR_WITH_ACTORS)["coverage_units"]
    by_id = {o["obligation_id"]: o for o in annotated}
    compiled = {
        "obl-a1": _intent_compiled_experiment("obl-a1", "exp-obl-a1", "admin", "buyer"),
    }
    plan = plan_coverage_unit_round(
        units,
        obligations_by_id=by_id,
        experiments_by_obligation=compiled,
        behavior_ir=IR_WITH_ACTORS,
        budget=10,
    )
    assert len(plan["selected"]) == 1
    row = plan["selected"][0]
    assert row["experiment_id"] == compiled[row["obligation_id"]]["experiment_id"]
    # pending rows carry the same identity source (follow-on rounds re-plan)
    for pending in plan.get("pending_next_round") or []:
        assert "experiment_id" in pending


def test_run22_chain_intent_plan_passes_with_derived_arms() -> None:
    """Offline replay of the run22 planning chain: unit plan -> multi-arm
    derivation -> intent plan. Previously raised
    ``compiled_experiment_mismatch`` on the first representative row."""
    obligations = _intent_chain_obligations()
    plan, units, by_id = _build_unit_plan(obligations, {
        "obl-a1": _intent_compiled_experiment("obl-a1", "exp-obl-a1", "admin", "buyer"),
    })
    pack = {
        "experiments": [_intent_compiled_experiment("obl-a1", "exp-obl-a1", "admin", "buyer")],
        "blocked_experiments": [],
        "abstract_experiments": [],
        "compiled_count": 1,
        "blocked_count": 0,
        "abstract_count": 0,
    }
    all_experiments = [dict(r) for r in pack["experiments"]]
    by_obligation = {r["obligation_id"]: r for r in all_experiments}
    receipt = derive_unit_execution_arms(
        obligation_plan=plan,
        units=units,
        obligations_by_id=by_id,
        experiment_pack=pack,
        all_experiments=all_experiments,
        by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
        environment_type="test",
        policy_version="",
        available_adapters=frozenset({"http_api", "actor_identity"}),
        planning_context={},
    )
    assert receipt["arms_derived"] == 2
    assert receipt["arm_experiment_count"] == 2
    assert receipt["arm_own_compile_count"] == 0
    _assert_identity_contract(plan, by_obligation)
    intent = build_agent_intent_plan(
        plan,
        obligations=obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
    )
    assert intent["status"] == "VERIFIED"
    assert intent["intent_count"] == 3
    assert {i["obligation_id"] for i in intent["intents"]} == {
        "obl-a1", "obl-a2", "obl-a3"
    }


def test_derived_arm_identity_index_never_diverges_from_selected_row() -> None:
    """A derived arm's experiment_id must be force-indexed under its
    obligation_id — a stale/BLOCKED prior entry must not shadow the arm."""
    obligations = _intent_chain_obligations()
    plan, units, by_id = _build_unit_plan(obligations, {
        "obl-a1": _intent_compiled_experiment("obl-a1", "exp-obl-a1", "admin", "buyer"),
    })
    pack = {
        "experiments": [_intent_compiled_experiment("obl-a1", "exp-obl-a1", "admin", "buyer")],
        "blocked_experiments": [],
        "abstract_experiments": [],
        "compiled_count": 1,
        "blocked_count": 0,
        "abstract_count": 0,
    }
    all_experiments = [dict(r) for r in pack["experiments"]]
    by_obligation = {r["obligation_id"]: r for r in all_experiments}
    # obl-a2 previously registered with a BLOCKED (non-compiled) experiment:
    # the derived arm must supersede it in the identity index.
    by_obligation["obl-a2"] = {
        "obligation_id": "obl-a2",
        "experiment_id": "exp-obl-a2-blocked",
        "compile_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING"},
    }
    derive_unit_execution_arms(
        obligation_plan=plan,
        units=units,
        obligations_by_id=by_id,
        experiment_pack=pack,
        all_experiments=all_experiments,
        by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
        environment_type="test",
        policy_version="",
        available_adapters=frozenset({"http_api", "actor_identity"}),
        planning_context={},
    )
    assert by_obligation["obl-a2"]["experiment_id"] == "exp-obl-a1__arm_0"
    _assert_identity_contract(plan, by_obligation)


def test_variant_already_compiled_binds_own_experiment_not_an_arm() -> None:
    """Promotion path: when a variant already has its own COMPILED experiment
    (representative-compile fallback), deriving an arm would create a second
    compiled experiment claiming the same obligation id. The plan row must
    reference the variant's own experiment instead."""
    obligations = _intent_chain_obligations()
    annotated = attach_canonical_obligation_keys(obligations, behavior_ir=IR_WITH_ACTORS)
    units = build_coverage_units(annotated, behavior_ir=IR_WITH_ACTORS)["coverage_units"]
    by_id = {o["obligation_id"]: o for o in annotated}
    # Promotion: obl-a2 becomes the unit's representative (compiled), and the
    # fallback compile also produced obl-a3's own experiment.
    unit = units[0]
    unit["representative_obligation_id"] = "obl-a2"
    compiled = {
        "obl-a2": _intent_compiled_experiment("obl-a2", "exp-obl-a2", "admin", "seller"),
        "obl-a3": _intent_compiled_experiment("obl-a3", "exp-obl-a3", "admin", "auditor"),
    }
    plan = plan_coverage_unit_round(
        units,
        obligations_by_id=by_id,
        experiments_by_obligation=compiled,
        behavior_ir=IR_WITH_ACTORS,
        budget=10,
    )
    pack = {
        "experiments": list(compiled.values()),
        "blocked_experiments": [],
        "abstract_experiments": [],
        "compiled_count": len(compiled),
        "blocked_count": 0,
        "abstract_count": 0,
    }
    all_experiments = [dict(r) for r in pack["experiments"]]
    by_obligation = {r["obligation_id"]: r for r in all_experiments}
    receipt = derive_unit_execution_arms(
        obligation_plan=plan,
        units=units,
        obligations_by_id=by_id,
        experiment_pack=pack,
        all_experiments=all_experiments,
        by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
        environment_type="test",
        policy_version="",
        available_adapters=frozenset({"http_api", "actor_identity"}),
        planning_context={},
    )
    # obl-a1 (not compiled) becomes a derived arm; obl-a3 stays on its own
    # compiled experiment — no second experiment claiming obl-a3.
    assert receipt["arms_derived"] == 1
    assert receipt["arm_own_compile_count"] == 1
    assert receipt["arm_experiment_count"] == 1
    row_a3 = next(
        row for row in plan["selected"] if row["obligation_id"] == "obl-a3"
    )
    assert row_a3["experiment_id"] == "exp-obl-a3"
    assert row_a3["arm_origin"] == "own_compile"
    assert by_obligation["obl-a3"]["experiment_id"] == "exp-obl-a3"
    _assert_identity_contract(plan, by_obligation)
    intent = build_agent_intent_plan(
        plan,
        obligations=obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
    )
    assert intent["status"] == "VERIFIED"


def _stub_compile_factory(status: str = "COMPILED"):
    def _stub_compile(obligations, **kwargs):
        experiments = []
        for obligation in obligations:
            prop = obligation.get("property", {})
            control = prop.get("control_actor_ref") or prop.get("actor_ref") or "admin"
            treatment = prop.get("treatment_actor_ref") or control
            exp = _intent_compiled_experiment(
                obligation["obligation_id"],
                f"exp-fb-{obligation['obligation_id']}",
                control,
                treatment,
            )
            exp["compile_receipt"] = {"status": status, "reason_code": ""}
            experiments.append(exp)
        return {
            "experiments": experiments if status == "COMPILED" else [],
            "blocked_experiments": (
                [] if status == "COMPILED" else experiments
            ),
            "abstract_experiments": [],
            "compiled_count": len(experiments) if status == "COMPILED" else 0,
            "blocked_count": 0 if status == "COMPILED" else len(experiments),
            "abstract_count": 0,
        }
    return _stub_compile


def _undeliverable_arm_variant() -> dict:
    """Same surface as the representative (same template/rule -> same coverage
    unit) but with a third actor role: 3 distinct actors vs the pair
    representative -> ``arm_shape_incompatible`` -> undeliverable as an arm."""
    variant = _auth_obligation(
        "obl-a2", operation_ref="op-orders",
        control_actor="admin", treatment_actor="seller",
    )
    variant["property"]["actor_ref"] = "owner-x"
    variant["required_actors"] = ["admin", "seller", "owner-x"]
    return variant


def _undeliverable_plan(obligations):
    """Unit plan whose obl-a2 cannot be derived as an arm (third actor role
    vs pair representative -> arm_shape_incompatible -> fallback)."""
    annotated = attach_canonical_obligation_keys(obligations, behavior_ir=IR_WITH_ACTORS)
    units = build_coverage_units(annotated, behavior_ir=IR_WITH_ACTORS)["coverage_units"]
    by_id = {o["obligation_id"]: o for o in annotated}
    rep_exp = _intent_compiled_experiment("obl-a1", "exp-obl-a1", "admin", "buyer")
    plan = plan_coverage_unit_round(
        units,
        obligations_by_id=by_id,
        experiments_by_obligation={"obl-a1": rep_exp},
        behavior_ir=IR_WITH_ACTORS,
        budget=10,
    )
    pack = {
        "experiments": [rep_exp],
        "blocked_experiments": [],
        "abstract_experiments": [],
        "compiled_count": 1,
        "blocked_count": 0,
        "abstract_count": 0,
    }
    return plan, units, by_id, pack


def test_undeliverable_arm_falls_back_to_compile_and_stays_consistent() -> None:
    """Fail-closed fallback: an arm that cannot be derived (shape
    incompatible) falls back to an independent compile; the compiled fallback
    experiment enters the identity index and the selected set, and the intent
    plan still verifies (回退路径完整)."""
    obligations = _intent_chain_obligations()
    # obl-a2 carries a third actor role: pair rep cannot rebind it.
    obligations[1] = _undeliverable_arm_variant()
    plan, units, by_id, pack = _undeliverable_plan(obligations)
    all_experiments = [dict(r) for r in pack["experiments"]]
    by_obligation = {r["obligation_id"]: r for r in all_experiments}
    receipt = derive_unit_execution_arms(
        obligation_plan=plan,
        units=units,
        obligations_by_id=by_id,
        experiment_pack=pack,
        all_experiments=all_experiments,
        by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
        environment_type="test",
        policy_version="",
        available_adapters=frozenset({"http_api", "actor_identity"}),
        planning_context={},
        compile_callback=_stub_compile_factory("COMPILED"),
    )
    assert receipt["arm_failed_count"] == 1
    assert receipt["arm_fallback_compile_count"] == 1
    assert receipt["arm_fallback_compiled_selected_count"] == 1
    assert by_obligation["obl-a2"]["experiment_id"] == "exp-fb-obl-a2"
    assert by_obligation["obl-a2"] in all_experiments
    row_a2 = next(
        row for row in plan["selected"] if row["obligation_id"] == "obl-a2"
    )
    assert row_a2["experiment_id"] == "exp-fb-obl-a2"
    assert row_a2["arm_origin"] == "fallback_compile"
    _assert_identity_contract(plan, by_obligation)
    intent = build_agent_intent_plan(
        plan,
        obligations=obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
    )
    assert intent["status"] == "VERIFIED"


def test_fallback_variant_still_blocked_stays_honestly_unselected() -> None:
    """Fail-closed honesty: a fallback compile that still returns non-COMPILED
    is never added to selected — it cannot trip the intent gate."""
    obligations = _intent_chain_obligations()
    obligations[1] = _undeliverable_arm_variant()
    plan, units, by_id, pack = _undeliverable_plan(obligations)
    all_experiments = [dict(r) for r in pack["experiments"]]
    by_obligation = {r["obligation_id"]: r for r in all_experiments}
    receipt = derive_unit_execution_arms(
        obligation_plan=plan,
        units=units,
        obligations_by_id=by_id,
        experiment_pack=pack,
        all_experiments=all_experiments,
        by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
        environment_type="test",
        policy_version="",
        available_adapters=frozenset({"http_api", "actor_identity"}),
        planning_context={},
        compile_callback=_stub_compile_factory("BLOCKED"),
    )
    assert receipt["arm_fallback_compile_count"] == 1
    assert receipt["arm_fallback_compiled_selected_count"] == 0
    assert not any(
        row["obligation_id"] == "obl-a2" for row in plan["selected"]
    )
    _assert_identity_contract(plan, by_obligation)
    intent = build_agent_intent_plan(
        plan,
        obligations=obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=IR_WITH_ACTORS,
    )
    assert intent["status"] == "VERIFIED"
