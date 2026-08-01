"""Authorization experiments must vary one identity dimension and nothing else."""
from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import experiment_compiler, experiment_executor
from ai_test_asset_center.authorization_comparison_contract import (
    attach_authorization_comparison_contract,
    bind_runtime_actor_identity_context,
    validate_authorization_comparison_contract,
)


def _actor(
    actor_id: str,
    role: str,
    *,
    tenant: str = "tenant-a",
    organization: str = "org-a",
    ownership: str = "",
    account_ref: str = "",
) -> dict:
    coordinates: dict[str, list[str]] = {
        "tenant_ref": [tenant],
        "organization_ref": [organization],
    }
    if ownership:
        coordinates["ownership_scope"] = [ownership]
    return {
        "id": actor_id,
        "actor_id": actor_id,
        "role": role,
        "account_ref": account_ref,
        "credential_secret_ref": (
            f"secret_ref:test_accounts:{account_ref}" if account_ref else f"secret_ref:actor:{role}"
        ),
        "identity_coordinates": coordinates,
    }


def _behavior_ir(control: dict, treatment: dict) -> dict:
    return {
        "actors": [control, treatment],
        "operations": [{"id": "op:read-order", "method": "GET", "path": "/orders/{order_id}"}],
    }


def _experiment(
    family: str,
    *,
    control_body: dict | None = None,
    treatment_body: dict | None = None,
) -> dict:
    control_step = {
        "step_id": "control_1",
        "actor_ref": "actor:control",
        "operation_ref": "op:read-order",
        "path": "/orders/{order_id}",
    }
    treatment_step = {
        "step_id": "treatment_1",
        "actor_ref": "actor:treatment",
        "operation_ref": "op:read-order",
        "path": "/orders/{order_id}",
    }
    if control_body is not None:
        control_step["body"] = deepcopy(control_body)
    if treatment_body is not None:
        treatment_step["body"] = deepcopy(treatment_body)
    return {
        "experiment_id": "exp:authorization-pair",
        "obligation_id": "obl:authorization-pair",
        "risk_family": family,
        "compile_receipt": {"status": "COMPILED"},
        "actor_selection_contract": {
            "control_actor_ref": "actor:control",
            "treatment_actor_ref": "actor:treatment",
        },
        "control_plan": [control_step],
        "treatment_plan": [treatment_step],
        "binding_plan": [
            {
                "target": "order_id",
                "status": "runtime_resolvable",
                "resolver_operations": ["op:list-orders"],
            }
        ],
        "source_identity_fields": ["order_id"],
    }


def _obligation(family: str, **property_values: object) -> dict:
    return {
        "obligation_id": "obl:authorization-pair",
        "risk_family": family,
        "property": {"template": "authorization_boundary", **property_values},
        "source_refs": [{"kind": "requirement", "locator": "REQ-AUTH-1"}],
    }


def test_role_authorization_keeps_tenant_org_and_resource_symmetric() -> None:
    ir = _behavior_ir(
        _actor("actor:control", "管理员"),
        _actor("actor:treatment", "访客"),
    )

    governed, reason, detail = attach_authorization_comparison_contract(
        _experiment("authorization"),
        _obligation("authorization"),
        ir,
    )

    assert (reason, detail) == ("", "")
    contract = governed["authorization_comparison_contract"]
    assert contract["comparison_dimension"] == "ROLE_PERMISSION"
    assert contract["same_operation_required"] is True
    assert contract["same_request_baseline_required"] is True
    assert contract["resource_identity_binding_targets"] == ["order_id"]
    assert contract["invariant_identity_dimensions"] == [
        "tenant_ref",
        "organization_ref",
        "department_ref",
        "warehouse_ref",
        "project_ref",
        "region_ref",
        "ownership_scope",
    ]


def test_role_authorization_blocks_cross_tenant_pair() -> None:
    ir = _behavior_ir(
        _actor("actor:control", "管理员", tenant="tenant-a"),
        _actor("actor:treatment", "访客", tenant="tenant-b"),
    )

    _, reason, detail = attach_authorization_comparison_contract(
        _experiment("authorization"),
        _obligation("authorization"),
        ir,
    )

    assert reason == "BLOCKED_MISSING_ACTOR"
    assert detail == (
        "authorization_comparison_identity_invalid:"
        "comparison_invariant_coordinate_mismatch:tenant_ref"
    )


def test_tenant_isolation_requires_same_role_and_distinct_tenant() -> None:
    ir = _behavior_ir(
        _actor("actor:control", "仓库员", tenant="tenant-a"),
        _actor("actor:treatment", "仓库员", tenant="tenant-b"),
    )

    governed, reason, detail = attach_authorization_comparison_contract(
        _experiment("isolation"),
        _obligation("isolation"),
        ir,
    )

    assert (reason, detail) == ("", "")
    contract = governed["authorization_comparison_contract"]
    assert contract["comparison_dimension"] == "TENANT_SCOPE"
    assert "tenant_ref" not in contract["invariant_identity_dimensions"]
    assert contract["allowed_varying_identity_dimensions"] == [
        "account_ref",
        "tenant_ref",
    ]


def test_tenant_isolation_blocks_role_and_tenant_changing_together() -> None:
    ir = _behavior_ir(
        _actor("actor:control", "管理员", tenant="tenant-a"),
        _actor("actor:treatment", "访客", tenant="tenant-b"),
    )

    _, reason, detail = attach_authorization_comparison_contract(
        _experiment("isolation"),
        _obligation("isolation"),
        ir,
    )

    assert reason == "BLOCKED_MISSING_ACTOR"
    assert "comparison_role_changed_with_tenant_scope" in detail


def test_ownership_comparison_allows_only_declared_owner_binding_mutation() -> None:
    ir = _behavior_ir(
        _actor("actor:control", "客户", ownership="SELF"),
        _actor("actor:treatment", "客户", ownership="OTHER"),
    )
    experiment = _experiment(
        "isolation",
        control_body={"owner_id": "owner-a", "amount": 10},
        treatment_body={"owner_id": "{user_id}", "amount": 10},
    )

    governed, reason, detail = attach_authorization_comparison_contract(
        experiment,
        _obligation(
            "isolation",
            ownership_param="owner_id",
            ownership_param_location="body",
            identity_binding_target="user_id",
        ),
        ir,
    )

    assert (reason, detail) == ("", "")
    contract = governed["authorization_comparison_contract"]
    assert contract["comparison_dimension"] == "OWNERSHIP_RELATION"
    assert contract["allowed_request_mutation_paths"] == ["body.owner_id"]
    assert contract["observed_request_diff_paths"] == ["body.owner_id"]


def test_ownership_comparison_blocks_unrelated_request_change() -> None:
    ir = _behavior_ir(
        _actor("actor:control", "客户", ownership="SELF"),
        _actor("actor:treatment", "客户", ownership="OTHER"),
    )
    experiment = _experiment(
        "isolation",
        control_body={"owner_id": "owner-a", "amount": 10},
        treatment_body={"owner_id": "{user_id}", "amount": 99},
    )

    _, reason, detail = attach_authorization_comparison_contract(
        experiment,
        _obligation(
            "isolation",
            ownership_param="owner_id",
            ownership_param_location="body",
        ),
        ir,
    )

    assert reason == "BLOCKED_MISSING_BINDING"
    assert detail == "authorization_comparison_request_asymmetry:body.amount"


def test_ownership_query_binding_does_not_block_on_omitted_empty_control_query() -> None:
    ir = _behavior_ir(
        _actor("actor:control", "瀹㈡埛", ownership="SELF"),
        _actor("actor:treatment", "瀹㈡埛", ownership="OTHER"),
    )
    experiment = _experiment("isolation")
    experiment["treatment_plan"][0]["query"] = {"user.id": "{user_id}"}

    governed, reason, detail = attach_authorization_comparison_contract(
        experiment,
        _obligation(
            "isolation",
            ownership_param="user.id",
            ownership_param_location="query",
            identity_binding_target="user_id",
        ),
        ir,
    )

    assert (reason, detail) == ("", "")
    contract = governed["authorization_comparison_contract"]
    assert contract["allowed_request_mutation_paths"] == ["query.user.id"]
    assert contract["observed_request_diff_paths"] == ["query.user.id"]


def test_runtime_account_binding_validates_tenant_isolation_pair() -> None:
    control = _actor("actor:control", "仓库员", tenant="", organization="", account_ref="control")
    treatment = _actor("actor:treatment", "仓库员", tenant="", organization="", account_ref="treatment")
    control["identity_coordinates"] = {}
    treatment["identity_coordinates"] = {}
    ir = _behavior_ir(control, treatment)
    governed, reason, _ = attach_authorization_comparison_contract(
        _experiment("isolation"),
        _obligation("isolation"),
        ir,
    )
    assert reason == ""

    runtime_ir = bind_runtime_actor_identity_context(
        ir,
        [
            {
                "account_ref": "control",
                "credential_ref": "secret_ref:test_accounts:control",
                "role": "仓库员",
                "tenant_id": "tenant-a",
                "organization_id": "org-a",
                "status": "ACTIVE",
            },
            {
                "account_ref": "treatment",
                "credential_ref": "secret_ref:test_accounts:treatment",
                "role": "仓库员",
                "tenant_id": "tenant-b",
                "organization_id": "org-a",
                "status": "ACTIVE",
            },
        ],
    )

    assert validate_authorization_comparison_contract(governed, runtime_ir) == (
        True,
        "",
        "",
    )


def test_runtime_tenant_isolation_blocks_missing_tenant_coordinate() -> None:
    control = _actor("actor:control", "仓库员", tenant="", organization="", account_ref="control")
    treatment = _actor("actor:treatment", "仓库员", tenant="", organization="", account_ref="treatment")
    control["identity_coordinates"] = {}
    treatment["identity_coordinates"] = {}
    ir = _behavior_ir(control, treatment)
    governed, reason, _ = attach_authorization_comparison_contract(
        _experiment("isolation"),
        _obligation("isolation"),
        ir,
    )
    assert reason == ""

    runtime_ir = bind_runtime_actor_identity_context(
        ir,
        [
            {
                "account_ref": "control",
                "credential_ref": "secret_ref:test_accounts:control",
                "role": "仓库员",
                "status": "ACTIVE",
            },
            {
                "account_ref": "treatment",
                "credential_ref": "secret_ref:test_accounts:treatment",
                "role": "仓库员",
                "status": "ACTIVE",
            },
        ],
    )

    assert validate_authorization_comparison_contract(governed, runtime_ir) == (
        False,
        "BLOCKED_MISSING_ACTOR",
        "authorization_comparison_tenant_coordinate_unresolved",
    )


def test_compiler_facade_attaches_contract_after_existing_core(monkeypatch) -> None:
    ir = _behavior_ir(
        _actor("actor:control", "管理员"),
        _actor("actor:treatment", "访客"),
    )
    base_experiment = _experiment("authorization")
    monkeypatch.setattr(experiment_compiler._base, "_runtime_pair_problem", lambda *_: "")
    monkeypatch.setattr(
        experiment_compiler,
        "_original_compile_experiment",
        lambda *args, **kwargs: deepcopy(base_experiment),
    )

    result = experiment_compiler.compile_experiment_for_obligation(
        _obligation("authorization"),
        behavior_ir=ir,
        environment_type="test",
    )

    assert result["compile_receipt"]["status"] == "COMPILED"
    assert result["authorization_comparison_contract"]["comparison_dimension"] == (
        "ROLE_PERMISSION"
    )


def test_executor_private_preflight_uses_comparison_authority(monkeypatch) -> None:
    ir = _behavior_ir(
        _actor("actor:control", "管理员", tenant="tenant-a"),
        _actor("actor:treatment", "访客", tenant="tenant-a"),
    )
    governed, reason, _ = attach_authorization_comparison_contract(
        _experiment("authorization"),
        _obligation("authorization"),
        ir,
    )
    assert reason == ""
    broken_ir = deepcopy(ir)
    broken_ir["actors"][1]["identity_coordinates"]["tenant_ref"] = ["tenant-b"]
    monkeypatch.setattr(
        experiment_executor,
        "_original_preflight",
        lambda *args, **kwargs: (True, "", ""),
    )

    ok, block_reason, detail = experiment_executor._graph_aware_preflight(
        governed,
        behavior_ir=broken_ir,
        actor_tokens={},
    )

    assert ok is False
    assert block_reason == "BLOCKED_MISSING_ACTOR"
    assert detail == "authorization_comparison_coordinate_mismatch:tenant_ref"
