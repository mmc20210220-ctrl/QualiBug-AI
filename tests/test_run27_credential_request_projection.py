from __future__ import annotations


def _binding() -> dict:
    return {
        "target": "password",
        "status": "runtime_resolvable",
        "source_priority": "actor_credential_secret",
        "actor_ref": "actor-user",
        "credential_secret_ref": "secret_ref:test_accounts:user@example.test",
        "credential_actor_authority": "unique_required_actor",
        "body_template_paths": ["credentials.password"],
    }


def _ir() -> dict:
    return {
        "operations": [
            {
                "id": "login",
                "method": "POST",
                "path": "/api/auth/login",
                "request_example": {
                    "credentials": {
                        "email": "user@example.test",
                        "password": "<PASSWORD>",
                    }
                },
            }
        ]
    }


def test_projection_uses_declared_body_path_and_persists_only_secret_ref() -> None:
    from ai_test_asset_center.credential_request_projection import (
        project_declared_credential_refs,
    )

    projected, receipt = project_declared_credential_refs(
        {
            "binding_plan": [_binding()],
            "control_plan": [
                {
                    "step_id": "control_1",
                    "operation_ref": "login",
                    "actor_ref": "actor-user",
                    "body": {
                        "credentials": {
                            "email": "user@example.test",
                            "password": "<PASSWORD>",
                        }
                    },
                }
            ],
            "treatment_plan": [],
        },
        behavior_ir=_ir(),
    )

    body = projected["control_plan"][0]["body"]
    assert body["credentials"]["password"] == (
        "secret_ref:test_accounts:user@example.test"
    )
    assert receipt["status"] == "PROJECTED"
    assert receipt["projected_binding_count"] == 1
    assert receipt["secret_value_persisted"] is False
    assert "real-password" not in repr(projected)


def test_projection_does_not_overwrite_concrete_body_value() -> None:
    from ai_test_asset_center.credential_request_projection import (
        project_declared_credential_refs,
    )

    projected, receipt = project_declared_credential_refs(
        {
            "binding_plan": [_binding()],
            "control_plan": [
                {
                    "step_id": "control_1",
                    "operation_ref": "login",
                    "actor_ref": "actor-user",
                    "body": {
                        "credentials": {
                            "email": "user@example.test",
                            "password": "already-source-concrete",
                        }
                    },
                }
            ],
            "treatment_plan": [],
        },
        behavior_ir=_ir(),
    )

    assert projected["control_plan"][0]["body"]["credentials"]["password"] == (
        "already-source-concrete"
    )
    assert receipt["rows"][0]["status"] == "NOT_USED"


def test_projection_fails_closed_when_consuming_step_actor_differs() -> None:
    from ai_test_asset_center.credential_request_projection import (
        project_declared_credential_refs,
    )

    projected, receipt = project_declared_credential_refs(
        {
            "binding_plan": [_binding()],
            "control_plan": [
                {
                    "step_id": "control_1",
                    "operation_ref": "login",
                    "actor_ref": "actor-other",
                    "body": {
                        "credentials": {
                            "email": "user@example.test",
                            "password": "<PASSWORD>",
                        }
                    },
                }
            ],
            "treatment_plan": [],
        },
        behavior_ir=_ir(),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["issues"][0]["reason_code"] == (
        "CREDENTIAL_BINDING_STEP_ACTOR_MISMATCH"
    )
    assert projected["control_plan"][0]["body"]["credentials"]["password"] == (
        "<PASSWORD>"
    )


def test_missing_credential_coordinate_is_blocked_without_secret_value() -> None:
    from ai_test_asset_center.credential_request_projection import (
        project_declared_credential_refs,
    )

    bad = _binding()
    bad["credential_secret_ref"] = ""
    projected, receipt = project_declared_credential_refs(
        {
            "binding_plan": [bad],
            "control_plan": [],
            "treatment_plan": [],
        },
        behavior_ir=_ir(),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["issues"][0]["reason_code"] == (
        "CREDENTIAL_BINDING_COORDINATE_INCOMPLETE"
    )
    assert receipt["secret_value_persisted"] is False
    assert "password" not in repr(projected.get("credential_request_projection_receipt", {})).lower() or (
        "secret_value_persisted" in projected["credential_request_projection_receipt"]
    )
