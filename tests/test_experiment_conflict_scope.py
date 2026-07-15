from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.experiment_compiler import (
    compile_experiment_for_obligation,
    compile_experiments,
)


def _behavior_ir(conflicts: list[dict] | None = None) -> dict:
    return {
        "operations": [
            {
                "id": "op-a",
                "method": "GET",
                "path": "/reports/a",
                "read_write": "read",
            },
            {
                "id": "op-b",
                "method": "GET",
                "path": "/reports/b",
                "read_write": "read",
            },
        ],
        "actors": [
            {
                "id": "actor-a",
                "role": "auditor",
                "account_ref": "auditor01",
                "credential_secret_ref": "secret_ref:test_accounts:auditor01",
                "runtime_bound": True,
            }
        ],
        "relations": [],
        "states": [],
        "invariants": [
            {
                "id": "inv-a",
                "expression": {
                    "kind": "privacy",
                    "operator": "must_not_return",
                    "operands": [{"field": "secret"}],
                },
            }
        ],
        "conflicts": list(conflicts or []),
    }


def _privacy_field_obligation() -> dict:
    return {
        "obligation_id": "obl-privacy-field-a",
        "risk_family": "privacy",
        "property": {
            "template": "privacy_field_policy",
            "privacy_test_mode": "field_policy",
            "privacy_policy": "absent",
            "field_tokens": ["secret"],
            "json_path": "$.secret",
            "actor_ref": "actor-a",
            "operation_ref": "op-a",
            "invariant_ref": "inv-a",
            "expression": {
                "kind": "privacy",
                "operator": "must_not_return",
                "operands": [{"field": "secret"}],
            },
        },
        "required_operations": ["op-a"],
        "required_actors": ["actor-a"],
        "required_fixtures": [],
        "required_observers": ["http_response", "source_invariant"],
        "cleanup_requirement": {"required": False},
        "source_refs": [{"source_id": "privacy-policy"}],
        "relation_refs": [],
    }


def test_single_actor_privacy_field_mode_bypasses_pair_only_gate() -> None:
    experiment = compile_experiment_for_obligation(
        _privacy_field_obligation(),
        behavior_ir=_behavior_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
    assert experiment["control_plan"] == []
    assert experiment["treatment_plan"][0]["actor_ref"] == "actor-a"


def test_unrelated_scoped_conflict_does_not_block_experiment() -> None:
    ir = _behavior_ir([
        {
            "id": "conflict-op-b",
            "status": "conflicting",
            "conflict_type": "operation_schema_conflict",
            "operation_ref": "op-b",
        }
    ])
    experiment = compile_experiment_for_obligation(
        _privacy_field_obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment


def test_relevant_scoped_conflict_still_blocks_fail_closed() -> None:
    ir = _behavior_ir([
        {
            "id": "conflict-op-a",
            "status": "conflicting",
            "conflict_type": "operation_schema_conflict",
            "operation_ref": "op-a",
        }
    ])
    experiment = compile_experiment_for_obligation(
        _privacy_field_obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert (
        experiment["compile_receipt"]["reason_code"]
        == "BLOCKED_CONFLICTING_SOURCE"
    )
    assert experiment["compile_receipt"]["detail"] == "conflict-op-a"


def test_permission_conflict_for_other_role_does_not_block_actor_experiment() -> None:
    ir = _behavior_ir([
        {
            "id": "conflict-seller-op-a",
            "status": "conflicting",
            "conflict_type": "permission_decision_conflict",
            "role_key": "seller",
            "operation_ref": "op-a",
            "decisions": ["DENY", "PERMIT"],
        }
    ])

    experiment = compile_experiment_for_obligation(
        _privacy_field_obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment


def test_permission_conflict_for_same_role_remains_fail_closed() -> None:
    ir = _behavior_ir([
        {
            "id": "conflict-auditor-op-a",
            "status": "conflicting",
            "conflict_type": "permission_decision_conflict",
            "role_key": "auditor",
            "operation_ref": "op-a",
            "decisions": ["DENY", "PERMIT"],
        }
    ])

    experiment = compile_experiment_for_obligation(
        _privacy_field_obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["detail"] == "conflict-auditor-op-a"


def test_unscoped_conflict_remains_global_fail_closed() -> None:
    ir = _behavior_ir([
        {
            "id": "conflict-global",
            "status": "conflicting",
            "conflict_type": "source_snapshot_conflict",
        }
    ])
    experiment = compile_experiment_for_obligation(
        _privacy_field_obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["detail"] == "conflict-global"


def test_batch_compiler_uses_same_conflict_scope_rule() -> None:
    obligation = _privacy_field_obligation()
    ir = _behavior_ir([
        {
            "id": "conflict-op-b",
            "status": "conflicting",
            "conflict_type": "operation_schema_conflict",
            "operation_ref": "op-b",
        }
    ])
    result = compile_experiments(
        [deepcopy(obligation)],
        behavior_ir=ir,
        environment_type="test",
    )

    assert result["compiled_count"] == 1, result
    assert result["blocked_count"] == 0, result
