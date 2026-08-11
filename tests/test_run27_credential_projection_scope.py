from __future__ import annotations


def test_projected_credential_is_removed_from_global_binding_and_fixture_dags() -> None:
    from ai_test_asset_center.credential_request_projection import (
        project_declared_credential_refs,
    )

    experiment = {
        "binding_plan": [
            {
                "target": "password",
                "status": "runtime_resolvable",
                "source_priority": "actor_credential_secret",
                "actor_ref": "actor-user",
                "credential_secret_ref": "secret_ref:test_accounts:user@example.test",
                "credential_actor_authority": "unique_required_actor",
                "body_template_paths": ["password"],
            }
        ],
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "login",
                "actor_ref": "actor-user",
                "body": {"email": "user@example.test", "password": "<PASSWORD>"},
            }
        ],
        "treatment_plan": [],
        "fixture_dag": {
            "nodes": [
                {"node_id": "bind-password", "kind": "runtime_read_binding", "target": "password"},
                {"node_id": "actor-user", "kind": "actor_context"},
            ],
            "setup_order": ["actor-user", "bind-password"],
            "edges": [{"from": "actor-user", "to": "bind-password"}],
        },
        "fixture_dependency_dag": {
            "nodes": [
                {"node_id": "bind-password", "kind": "runtime_read_binding", "target": "password"}
            ],
            "execution_order": ["bind-password"],
        },
    }
    behavior_ir = {
        "operations": [
            {
                "id": "login",
                "method": "POST",
                "path": "/api/login",
                "request_example": {"email": "user@example.test", "password": "<PASSWORD>"},
            }
        ]
    }

    projected, receipt = project_declared_credential_refs(
        experiment,
        behavior_ir=behavior_ir,
    )

    assert projected["control_plan"][0]["body"]["password"] == (
        "secret_ref:test_accounts:user@example.test"
    )
    assert projected["binding_plan"] == []
    assert receipt["removed_global_binding_targets"] == ["password"]
    assert projected["fixture_dag"]["setup_order"] == ["actor-user"]
    assert projected["fixture_dag"]["edges"] == []
    assert projected["fixture_dependency_dag"]["execution_order"] == []


def test_projection_blocks_if_same_credential_placeholder_survives_elsewhere() -> None:
    from ai_test_asset_center.credential_request_projection import (
        project_declared_credential_refs,
    )

    experiment = {
        "binding_plan": [
            {
                "target": "password",
                "status": "runtime_resolvable",
                "source_priority": "actor_credential_secret",
                "actor_ref": "actor-user",
                "credential_secret_ref": "secret_ref:test_accounts:user@example.test",
                "credential_actor_authority": "unique_required_actor",
                "body_template_paths": ["password"],
            }
        ],
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "login",
                "actor_ref": "actor-user",
                "body": {"password": "<PASSWORD>"},
            }
        ],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "operation_ref": "other",
                "actor_ref": "actor-user",
                "query": {"secret": "{password}"},
            }
        ],
    }
    behavior_ir = {
        "operations": [
            {"id": "login", "method": "POST", "path": "/api/login"},
            {"id": "other", "method": "GET", "path": "/api/other"},
        ]
    }

    projected, receipt = project_declared_credential_refs(
        experiment,
        behavior_ir=behavior_ir,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["issues"][0]["reason_code"] == "CREDENTIAL_BINDING_PROJECTION_INCOMPLETE"
    assert projected["binding_plan"]
