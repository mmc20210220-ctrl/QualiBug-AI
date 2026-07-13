from __future__ import annotations

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.experiment_compiler import compile_experiments
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


def _privacy_ir(*, masked: bool = False) -> dict:
    rule = {
        "rule_id": "privacy-users-field",
        "statement": (
            "email must be masked in user reports"
            if masked
            else "passwordHash must not be returned in user reports"
        ),
        "kind": "privacy",
        "operator": "must_mask" if masked else "must_not_return",
        "operands": [
            {
                "field": "email" if masked else "passwordHash",
                "policy": "must_mask" if masked else "must_not_return",
                **(
                    {"mask_pattern": r"^[^@]{1,2}\*+@[^@]+$"}
                    if masked
                    else {}
                ),
            }
        ],
        "operation_ref": "listUsers",
        "source_id": "privacy-policy",
    }
    return build_behavior_ir_from_knowledge_asset(
        {
            "permission_matrix": [
                {
                    "role": "auditor",
                    "resource": "/reports/users",
                    "actions": ["read"],
                    "source_id": "roles",
                }
            ],
            "rule_library": [rule],
        },
        project_id="privacy-test",
        api_operations=[
            {
                "operation_id": "listUsers",
                "method": "GET",
                "path": "/reports/users",
                "summary": "List user report rows",
                "source_id": "api-spec",
            }
        ],
        runtime_actors=[
            {
                "role": "auditor",
                "account_ref": "auditor01",
                "secret_ref": "secret_ref:test_accounts:auditor01",
                "status": "active",
            }
        ],
    )


def _field_obligation(ir: dict) -> dict:
    result = compile_obligations_from_behavior_ir(ir)
    matches = [
        row
        for row in result["obligations"]
        if row.get("risk_family") == "privacy"
        and (row.get("property") or {}).get("privacy_test_mode")
        == "field_policy"
    ]
    assert len(matches) == 1, result
    return matches[0]


def test_explicit_forbidden_field_becomes_single_actor_privacy_obligation() -> None:
    ir = _privacy_ir()
    result = compile_obligations_from_behavior_ir(ir)
    obligation = _field_obligation(ir)

    assert obligation["property"]["privacy_policy"] == "absent"
    assert obligation["property"]["field_tokens"] == ["passwordHash"]
    assert obligation["property"]["json_path"] == "$.passwordHash"
    assert len(obligation["required_actors"]) == 1
    assert obligation["required_observers"] == [
        "http_response",
        "source_invariant",
    ]
    assert not [
        gap
        for gap in result["coverage_gaps"]
        if gap.get("code") == "BLOCKED_MISSING_ACTOR_PAIR"
        and gap.get("subject_ref")
        == obligation["property"]["invariant_ref"]
    ]


def test_privacy_field_obligation_compiles_to_one_real_get() -> None:
    ir = _privacy_ir()
    obligation = _field_obligation(ir)
    compiled = compile_experiments(
        [obligation],
        behavior_ir=ir,
        environment_type="test",
    )

    assert compiled["compiled_count"] == 1, compiled
    assert compiled["blocked_count"] == 0, compiled
    experiment = compiled["experiments"][0]
    assert experiment["control_plan"] == []
    assert len(experiment["treatment_plan"]) == 1
    assert (
        experiment["treatment_plan"][0]["protocol_step"]
        == "privacy_field_read"
    )
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == "privacy_field_policy"
    assert assertion["privacy_policy"] == "absent"


def test_forbidden_field_leak_is_violation_without_copying_secret_value() -> None:
    assertion = {
        "assertion_id": "assert-privacy",
        "kind": "privacy_field_policy",
        "privacy_policy": "absent",
        "field_tokens": ["passwordHash"],
        "json_path": "$.passwordHash",
    }
    verdict = evaluate_assertion(
        assertion,
        observations={
            "status_code": 200,
            "body": {
                "data": [
                    {
                        "id": "user-1",
                        "passwordHash": "secret-hash-value",
                    }
                ]
            },
        },
    )

    assert verdict["status"] == "VIOLATION"
    assert verdict["reason_code"] == "PRIVACY_FORBIDDEN_FIELD_EXPOSED"
    assert verdict["actual"]["occurrence_count"] == 1
    assert "secret-hash-value" not in str(verdict)


def test_absent_policy_passes_only_with_meaningful_resource_evidence() -> None:
    assertion = {
        "assertion_id": "assert-privacy",
        "kind": "privacy_field_policy",
        "privacy_policy": "absent",
        "field_tokens": ["passwordHash"],
        "json_path": "$.passwordHash",
    }
    passed = evaluate_assertion(
        assertion,
        observations={
            "status_code": 200,
            "body": {"data": [{"id": "user-1", "email": "a@example.com"}]},
        },
    )
    assert passed["status"] == "PASS"

    empty = evaluate_assertion(
        assertion,
        observations={"status_code": 200, "body": {"data": []}},
    )
    assert empty["status"] == "INDETERMINATE"
    assert empty["reason_code"] == "PRIVACY_RESOURCE_EVIDENCE_MISSING"


def test_mask_policy_uses_only_source_declared_regex() -> None:
    obligation = _field_obligation(_privacy_ir(masked=True))
    assertion = {
        "assertion_id": "assert-mask",
        "kind": "privacy_field_policy",
        **{
            key: obligation["property"][key]
            for key in (
                "privacy_policy",
                "field_tokens",
                "json_path",
                "mask_pattern",
                "allow_absent",
            )
        },
    }

    masked = evaluate_assertion(
        assertion,
        observations={
            "status_code": 200,
            "body": {"data": [{"id": "user-1", "email": "a***@example.com"}]},
        },
    )
    assert masked["status"] == "PASS"

    raw = evaluate_assertion(
        assertion,
        observations={
            "status_code": 200,
            "body": {"data": [{"id": "user-1", "email": "alice@example.com"}]},
        },
    )
    assert raw["status"] == "VIOLATION"
    assert raw["reason_code"] == "PRIVACY_MASK_POLICY_VIOLATED"
    assert "alice@example.com" not in str(raw)


def test_unstructured_privacy_rule_does_not_invent_a_field_test() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "permission_matrix": [
                {
                    "role": "auditor",
                    "resource": "/reports/users",
                    "actions": ["read"],
                }
            ],
            "rule_library": [
                {
                    "rule_id": "privacy-vague",
                    "statement": "User data must be protected",
                    "kind": "privacy",
                    "operator": "must_hold",
                    "operands": [],
                    "operation_ref": "listUsers",
                }
            ],
        },
        project_id="privacy-vague",
        api_operations=[
            {
                "operation_id": "listUsers",
                "method": "GET",
                "path": "/reports/users",
            }
        ],
        runtime_actors=[
            {
                "role": "auditor",
                "account_ref": "auditor01",
                "secret_ref": "secret_ref:test_accounts:auditor01",
            }
        ],
    )
    result = compile_obligations_from_behavior_ir(ir)

    assert not [
        row
        for row in result["obligations"]
        if row.get("risk_family") == "privacy"
        and (row.get("property") or {}).get("privacy_test_mode")
        == "field_policy"
    ]
    assert [
        gap
        for gap in result["coverage_gaps"]
        if gap.get("code") == "BLOCKED_MISSING_ACTOR_PAIR"
    ]
