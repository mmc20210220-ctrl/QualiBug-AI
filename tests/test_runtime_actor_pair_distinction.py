from __future__ import annotations

from ai_test_asset_center.experiment_compiler import (
    compile_experiment_for_obligation,
    compile_experiments,
)


def _actor(
    actor_id: str,
    *,
    account_ref: str,
    secret_ref: str,
    role: str = "user",
) -> dict:
    return {
        "id": actor_id,
        "role": role,
        "account_ref": account_ref,
        "credential_secret_ref": secret_ref,
        "account_status": "active",
        "runtime_bound": bool(secret_ref),
    }


def _behavior_ir(
    control: dict,
    treatment: dict,
) -> dict:
    return {
        "operations": [{
            "id": "op-private",
            "method": "GET",
            "path": "/private-summary",
            "read_write": "read",
        }],
        "actors": [control, treatment],
        "relations": [],
        "conflicts": [],
    }


def _obligation() -> dict:
    return {
        "obligation_id": "obl-authorization-pair",
        "risk_family": "authorization",
        "property": {
            "template": "authorization_control_treatment",
            "operation_ref": "op-private",
            "control_actor_ref": "actor-control",
            "treatment_actor_ref": "actor-treatment",
            "require_same_resource": True,
        },
        "required_operations": ["op-private"],
        "required_actors": ["actor-control", "actor-treatment"],
        "required_observers": ["http_response", "actor_identity"],
        "cleanup_requirement": {"required": False},
        "source_refs": [{"source_id": "permission-matrix"}],
    }


def test_distinct_actor_nodes_sharing_one_account_are_blocked() -> None:
    ir = _behavior_ir(
        _actor(
            "actor-control",
            account_ref="shared@example.test",
            secret_ref="secret_ref:test_accounts:control",
        ),
        _actor(
            "actor-treatment",
            account_ref="shared@example.test",
            secret_ref="secret_ref:test_accounts:treatment",
        ),
    )

    experiment = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == (
        "BLOCKED_RUNTIME_ACTOR_PAIR_NOT_DISTINCT"
    )
    assert experiment["compile_receipt"]["detail"] == (
        "runtime_actor_pair_not_distinct:shared_account_ref"
    )


def test_distinct_accounts_sharing_one_secret_are_blocked() -> None:
    ir = _behavior_ir(
        _actor(
            "actor-control",
            account_ref="owner@example.test",
            secret_ref="secret_ref:test_accounts:shared",
        ),
        _actor(
            "actor-treatment",
            account_ref="outsider@example.test",
            secret_ref="secret_ref:test_accounts:shared",
        ),
    )

    experiment = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["detail"] == (
        "runtime_actor_pair_not_distinct:shared_credential_secret_ref"
    )


def test_distinct_runtime_principals_compile_normally() -> None:
    ir = _behavior_ir(
        _actor(
            "actor-control",
            account_ref="owner@example.test",
            secret_ref="secret_ref:test_accounts:owner",
        ),
        _actor(
            "actor-treatment",
            account_ref="outsider@example.test",
            secret_ref="secret_ref:test_accounts:outsider",
        ),
    )

    experiment = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
    assert experiment["control_plan"][0]["actor_ref"] == "actor-control"
    assert experiment["treatment_plan"][0]["actor_ref"] == "actor-treatment"


def test_two_anonymous_actor_nodes_do_not_form_a_real_contrast() -> None:
    ir = _behavior_ir(
        _actor(
            "actor-control",
            account_ref="",
            secret_ref="",
            role="public",
        ),
        _actor(
            "actor-treatment",
            account_ref="",
            secret_ref="",
            role="anonymous",
        ),
    )

    experiment = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["detail"] == (
        "runtime_actor_pair_not_distinct:shared_anonymous_runtime_context"
    )


def test_batch_compiler_enforces_the_same_runtime_principal_rule() -> None:
    obligation = _obligation()
    ir = _behavior_ir(
        _actor(
            "actor-control",
            account_ref="shared@example.test",
            secret_ref="secret_ref:test_accounts:shared",
        ),
        _actor(
            "actor-treatment",
            account_ref="shared@example.test",
            secret_ref="secret_ref:test_accounts:shared",
        ),
    )

    result = compile_experiments(
        [obligation],
        behavior_ir=ir,
        environment_type="test",
    )

    assert result["compiled_count"] == 0
    assert result["blocked_count"] == 1
    assert result["block_reason_counts"] == {"BLOCKED_RUNTIME_ACTOR_PAIR_NOT_DISTINCT": 1}
    assert obligation["compile_status"] == "BLOCKED"


def test_compiler_never_substitutes_unrelated_actors_for_the_declared_pair() -> None:
    obligation = _obligation()
    ir = _behavior_ir(
        _actor(
            "actor-control",
            account_ref="shared@example.test",
            secret_ref="secret_ref:test_accounts:control",
        ),
        _actor(
            "actor-treatment",
            account_ref="shared@example.test",
            secret_ref="secret_ref:test_accounts:treatment",
        ),
    )
    ir["actors"].append(
        _actor(
            "actor-unrelated",
            account_ref="other@example.test",
            secret_ref="secret_ref:test_accounts:other",
        )
    )

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["detail"] == (
        "runtime_actor_pair_not_distinct:shared_account_ref"
    )


def test_empty_patch_without_source_declared_body_is_blocked() -> None:
    obligation = {
        **_obligation(),
        "obligation_id": "obl-empty-patch",
        "property": {
            **_obligation()["property"],
            "operation_ref": "op-empty-patch",
        },
        "required_operations": ["op-empty-patch"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
        },
    }
    ir = {
        "operations": [
            {
                "id": "op-empty-patch",
                "method": "PATCH",
                "path": "/resources/current",
                "read_write": "write",
                "request_schema": {},
                "request_example": {},
            },
            {
                "id": "op-read",
                "method": "GET",
                "path": "/resources/current",
                "read_write": "read",
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/current",
                "read_write": "write",
            },
        ],
        "actors": [
            _actor(
                "actor-control",
                account_ref="owner@example.test",
                secret_ref="secret_ref:test_accounts:owner",
            ),
            _actor(
                "actor-treatment",
                account_ref="outsider@example.test",
                secret_ref="secret_ref:test_accounts:outsider",
            ),
        ],
        "entities": [
            {
                "id": "entity-resource",
                "fields": ["id", "status"],
            }
        ],
        "relations": [
            {
                "id": "rel-mutation",
                "relation_type": "transitions",
                "operation_ref": "op-empty-patch",
                "to_ref": "entity-resource",
            },
            {
                "id": "rel-read",
                "relation_type": "observes",
                "operation_ref": "op-read",
                "to_ref": "entity-resource",
            },
        ],
        "conflicts": [],
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == (
        "BLOCKED_MISSING_BINDING"
    )
    assert experiment["compile_receipt"]["detail"] == (
        "source_declared_request_body_missing:op-empty-patch"
    )


def test_non_sensitive_single_actor_validation_is_unchanged() -> None:
    obligation = {
        "obligation_id": "obl-validation",
        "risk_family": "validation",
        "property": {
            "operation_ref": "op-create",
            "actor_ref": "actor-control",
            "field": "name",
            "validation_constraint": "required",
            "validation_constraint_value": True,
            "field_tokens": ["name"],
            "json_path": "$.name",
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-control"],
        "required_observers": ["http_response", "business_effect"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
        },
        "source_refs": [{"source_id": "api"}],
    }
    ir = {
        "operations": [
            {
                "id": "op-create",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
                "request_schema": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
                "request_example": {"name": "valid"},
            },
            {
                "id": "op-list",
                "method": "GET",
                "path": "/resources",
                "read_write": "read",
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
                "read_write": "write",
            },
        ],
        "actors": [
            _actor(
                "actor-control",
                account_ref="owner@example.test",
                secret_ref="secret_ref:test_accounts:owner",
            )
        ],
        "relations": [],
        "conflicts": [],
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
