from __future__ import annotations

import ai_test_asset_center.obligation_compiler as obligation_compiler
from ai_test_asset_center.experiment_compiler import (
    compile_experiment_for_obligation,
)


def _actor(actor_id: str, role: str) -> dict:
    return {
        "id": actor_id,
        "role": role,
        "account_ref": f"{actor_id}@example.test",
        "credential_secret_ref": (
            f"secret_ref:test_accounts:{actor_id}@example.test"
        ),
        "account_status": "active",
        "runtime_bound": True,
        "confidence": 0.9,
        "source_refs": [{"source_id": f"account:{actor_id}"}],
    }


def _behavior_ir(*, include_deny: bool = True) -> dict:
    relations = [
        {
            "id": "permit-owner",
            "relation_type": "permits",
            "from_ref": "actor-owner",
            "actor_ref": "actor-owner",
            "to_ref": "op-secret",
            "operation_ref": "op-secret",
            "confidence": 0.9,
            "source_refs": [{"source_id": "roles"}],
        }
    ]
    if include_deny:
        relations.append({
            "id": "deny-outsider",
            "relation_type": "denies",
            "from_ref": "actor-outsider",
            "actor_ref": "actor-outsider",
            "to_ref": "op-secret",
            "operation_ref": "op-secret",
            "confidence": 0.9,
            "source_refs": [{"source_id": "roles"}],
        })
    return {
        "actors": [
            _actor("actor-owner", "owner"),
            _actor("actor-outsider", "outsider"),
        ],
        "operations": [
            {
                "id": "op-secret",
                "method": "GET",
                "path": "/secrets/{id}",
                "read_write": "read",
                "confidence": 0.9,
                "source_refs": [{"source_id": "api"}],
            },
            {
                "id": "op-secret-list",
                "method": "GET",
                "path": "/secrets",
                "read_write": "read",
                "confidence": 0.9,
                "source_refs": [{"source_id": "api"}],
            },
        ],
        "invariants": [{
            "id": "inv-private-secret",
            "expression": {"kind": "privacy_scope"},
            "confidence": 0.9,
            "source_refs": [{"source_id": "prd"}],
        }],
        "relations": relations,
        "conflicts": [],
    }


def _single_actor_result(family: str = "privacy") -> dict:
    obligation = {
        "schema_version": "qualibug.test-obligation.v1",
        "obligation_id": f"obl-single-{family}",
        "risk_family": family,
        "subject_refs": [
            "inv-private-secret",
            "op-secret",
            "actor-owner",
        ],
        "property": {
            "template": f"invariant_{family}",
            "invariant_ref": "inv-private-secret",
            "expression": {"kind": f"{family}_scope"},
            "operation_ref": "op-secret",
            "actor_ref": "actor-owner",
        },
        "required_actors": ["actor-owner"],
        "required_operations": ["op-secret"],
        "required_fixtures": [],
        "required_observers": ["typed_assertion", "source_invariant"],
        "cleanup_requirement": {"required": False},
        "source_refs": [{"source_id": "prd"}],
        "relation_refs": ["permit-owner"],
        "confidence": 0.8,
        "compile_status": "PENDING",
    }
    return {
        "schema_version": "qualibug.obligation-compile.v1",
        "behavior_ir_model_id": "model-test",
        "obligation_count": 1,
        "by_family": {family: 1},
        "obligations": [obligation],
        "coverage_gaps": [],
    }


def test_privacy_obligation_is_rebuilt_with_distinct_source_actors(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        obligation_compiler._base,
        "_original_compile",
        lambda behavior_ir, **kwargs: _single_actor_result("privacy"),
    )

    result = obligation_compiler.compile_obligations_from_behavior_ir(
        _behavior_ir()
    )

    assert result["obligation_count"] == 1
    obligation = result["obligations"][0]
    assert obligation["risk_family"] == "privacy"
    assert obligation["required_actors"] == [
        "actor-owner",
        "actor-outsider",
    ]
    assert obligation["property"]["control_actor_ref"] == "actor-owner"
    assert obligation["property"]["treatment_actor_ref"] == "actor-outsider"
    assert obligation["property"]["require_same_resource"] is True
    assert "actor_ref" not in obligation["property"]
    assert {
        "http_response",
        "actor_identity",
        "authorization_comparison",
        "typed_assertion",
        "source_invariant",
    } <= set(obligation["required_observers"])
    assert {"permit-owner", "deny-outsider"} <= set(
        obligation["relation_refs"]
    )


def test_paired_privacy_obligation_compiles_to_distinct_control_treatment(
    monkeypatch,
) -> None:
    behavior_ir = _behavior_ir()
    monkeypatch.setattr(
        obligation_compiler._base,
        "_original_compile",
        lambda behavior_ir, **kwargs: _single_actor_result("privacy"),
    )
    paired = obligation_compiler.compile_obligations_from_behavior_ir(
        behavior_ir
    )["obligations"][0]

    experiment = compile_experiment_for_obligation(
        paired,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
    assert experiment["control_plan"][0]["actor_ref"] == "actor-owner"
    assert experiment["treatment_plan"][0]["actor_ref"] == "actor-outsider"
    observer_ids = {
        observer["observer_id"]
        for observer in experiment["observers"]
    }
    assert "authorization_comparison" in observer_ids
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == "privacy"
    assert assertion["require_control"] is True


def test_missing_denied_actor_becomes_coverage_gap_instead_of_fake_experiment(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        obligation_compiler._base,
        "_original_compile",
        lambda behavior_ir, **kwargs: _single_actor_result("privacy"),
    )

    result = obligation_compiler.compile_obligations_from_behavior_ir(
        _behavior_ir(include_deny=False)
    )

    assert result["obligations"] == []
    assert result["obligation_count"] == 0
    gap = next(
        gap
        for gap in result["coverage_gaps"]
        if gap["code"] == "BLOCKED_MISSING_ACTOR_PAIR"
    )
    assert gap["risk_family"] == "privacy"
    assert gap["operation_ref"] == "op-secret"
    assert gap["required_relation_types"] == ["permits", "denies"]


def test_visibility_uses_the_same_source_grounded_pairing_rule(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        obligation_compiler._base,
        "_original_compile",
        lambda behavior_ir, **kwargs: _single_actor_result("visibility"),
    )

    result = obligation_compiler.compile_obligations_from_behavior_ir(
        _behavior_ir()
    )

    obligation = result["obligations"][0]
    assert obligation["risk_family"] == "visibility"
    assert obligation["required_actors"] == [
        "actor-owner",
        "actor-outsider",
    ]
    assert "authorization_comparison" in obligation["required_observers"]
