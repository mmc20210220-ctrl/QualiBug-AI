from __future__ import annotations

from typing import Any

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import build_chinese_first_comprehension
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.fact_permission_matrix import (
    materialize_fact_permission_matrix,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.segregation_of_duties_authority import (
    materialize_sod_contracts,
)
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def _fact(fid: str, role: str, action: str) -> dict[str, Any]:
    return {
        "fact_id": fid,
        "status": "ACCEPTED",
        "kind": "RULE",
        "raw_statement": f"{role}可以{action}订单",
        "modality": "MAY",
        "subject": {"actor_refs": [role]},
        "action": {"canonical": action},
        "object": {"entity_refs": ["订单"]},
        "source_spans": [{"source_id": "prd:sod", "locator": fid}],
    }


def _asset() -> dict[str, Any]:
    return {
        "roles": [{"role": "申请人"}, {"role": "审批人"}],
        "interfaces": [
            {
                "interface_id": "op-create",
                "operation_id": "createOrder",
                "method": "POST",
                "path": "/api/orders",
                "entity_refs": ["订单"],
                "summary": "创建订单",
                "request_example": {"name": "source-declared"},
            },
            {
                "interface_id": "op-approve",
                "operation_id": "approveOrder",
                "method": "POST",
                "path": "/api/orders/{order_id}/approve",
                "entity_refs": ["订单"],
                "summary": "审批订单",
            },
            {
                "interface_id": "op-get",
                "operation_id": "getOrder",
                "method": "GET",
                "path": "/api/orders/{order_id}",
                "entity_refs": ["订单"],
                "summary": "查看订单",
            },
            {
                "interface_id": "op-list",
                "operation_id": "listOrders",
                "method": "GET",
                "path": "/api/orders",
                "entity_refs": ["订单"],
                "summary": "订单列表",
            },
            {
                "interface_id": "op-delete",
                "operation_id": "deleteOrder",
                "method": "DELETE",
                "path": "/api/orders/{order_id}",
                "entity_refs": ["订单"],
                "summary": "删除订单",
            },
        ],
        "business_fact_ledger": {"items": [
            _fact("fact-apply", "申请人", "创建"),
            _fact("fact-approve", "审批人", "审批"),
        ]},
        "permission_matrix": [],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def _understood(statement: str = "申请与审批必须由不同人员完成。") -> dict[str, Any]:
    asset = _asset()
    materialize_sod_contracts(asset, [{
        "source_id": "prd:sod",
        "filename": "职责分离.md",
        "text": statement,
    }])
    return materialize_fact_permission_matrix(asset)




def _understood_from_chinese() -> dict[str, Any]:
    asset = _asset()
    asset.pop("business_fact_ledger")
    asset["business_objects"] = [{"object": "订单"}]
    return build_chinese_first_comprehension(
        asset,
        [{
            "source_id": "prd:sod",
            "filename": "职责分离.md",
            "text": "申请人可以创建订单。审批人可以审批订单。申请与审批必须由不同人员完成。",
        }],
    )

def _runtime_actors() -> list[dict[str, str]]:
    return [
        {"role": "申请人", "account_ref": "shared@example.test", "secret_ref": "secret_ref:shared"},
        {"role": "审批人", "account_ref": "shared@example.test", "secret_ref": "secret_ref:shared"},
        {"role": "审批人", "account_ref": "independent@example.test", "secret_ref": "secret_ref:independent"},
    ]


def test_post_actions_bind_to_exact_semantic_operation() -> None:
    asset = _understood()
    coordinates = {(row["role"], row["interface_id"]) for row in asset["permission_matrix"]}
    assert ("申请人", "op-create") in coordinates
    assert ("审批人", "op-approve") in coordinates
    assert ("申请人", "op-approve") not in coordinates
    assert ("审批人", "op-create") not in coordinates


def test_same_account_keeps_two_role_assignments_with_shared_credential_identity() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_understood(), runtime_actors=_runtime_actors())
    shared = [row for row in ir["actors"] if row.get("account_ref") == "shared@example.test"]
    assert {row["role"] for row in shared} == {"申请人", "审批人"}
    assert len({row["id"] for row in shared}) == 2
    assert len({row["credential_identity_ref"] for row in shared}) == 1
    assert len({row["role_assignment_ref"] for row in shared}) == 2


def test_explicit_sod_contract_binds_to_effective_permission_matrix() -> None:
    asset = _understood()
    assert len(asset["segregation_of_duties_policies"]) == 1
    policy = asset["segregation_of_duties_policies"][0]
    assert policy["setup_role"] == "申请人"
    assert policy["guarded_role"] == "审批人"
    assert policy["setup_operation_ref"] == "op-create"
    assert policy["guarded_operation_ref"] == "op-approve"
    assert policy["resource_ref"] == "订单"


def test_conditional_sod_is_fail_closed() -> None:
    asset = _understood("金额超过一万元时，申请与审批必须由不同人员完成。")
    assert asset.get("segregation_of_duties_policies") == []
    assert any(
        row.get("reason_code") == "SOD_CONDITION_OR_ROLE_COORDINATE_UNRESOLVED"
        for row in asset["segregation_of_duties_contracts"]
    )


def test_sod_obligation_reuses_authorization_control_treatment_compiler() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_understood(), runtime_actors=_runtime_actors())
    result = compile_obligations_from_behavior_ir(ir)
    obligation = next(row for row in result["obligations"] if row.get("property", {}).get("sod_policy_id"))
    prop = obligation["property"]
    actors = {row["id"]: row for row in ir["actors"]}
    assert prop["template"] == "authorization_control_treatment"
    assert actors[prop["control_actor_ref"]]["account_ref"] == "independent@example.test"
    assert actors[prop["treatment_actor_ref"]]["account_ref"] == "shared@example.test"
    assert actors[prop["fixture_owner_actor_ref"]]["account_ref"] == "shared@example.test"
    assert actors[prop["fixture_owner_actor_ref"]]["role"] == "申请人"

    experiment = compile_experiment_for_obligation(obligation, behavior_ir=ir, environment_type="test")
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
    assert any(row.get("fixture_owner_actor_ref") == prop["fixture_owner_actor_ref"] for row in experiment["binding_plan"])
    assert sum(row.get("target") == "fixture:owned_resource" for row in experiment["binding_plan"]) == 1
    assert experiment["control_plan"][0]["actor_ref"] == prop["control_actor_ref"]
    assert experiment["treatment_plan"][0]["actor_ref"] == prop["treatment_actor_ref"]


def test_role_pair_sod_statement_uses_explicit_catalog_roles() -> None:
    asset = _understood("申请人和审批人不能由同一人担任。")
    policy = asset["segregation_of_duties_policies"][0]
    assert policy["setup_role"] == "申请人"
    assert policy["guarded_role"] == "审批人"


def test_non_explicit_workflow_sequence_does_not_create_sod_contract() -> None:
    asset = _understood("申请人提交订单后，审批人进行审批。")
    assert asset.get("segregation_of_duties_policies") == []
    assert asset.get("segregation_of_duties_contracts") == []


def test_sod_without_independent_guarded_account_remains_visible_gap() -> None:
    runtime = [
        {"role": "申请人", "account_ref": "shared@example.test", "secret_ref": "secret_ref:shared"},
        {"role": "审批人", "account_ref": "shared@example.test", "secret_ref": "secret_ref:shared"},
    ]
    ir = build_behavior_ir_from_knowledge_asset(_understood(), runtime_actors=runtime)
    result = compile_obligations_from_behavior_ir(ir)
    assert not any(row.get("property", {}).get("sod_policy_id") for row in result["obligations"])
    assert any(
        row.get("reason") == "independent_guarded_actor_unresolved"
        for row in result["coverage_gaps"]
    )


def test_materializing_sod_contracts_is_idempotent() -> None:
    asset = _asset()
    sources = [{"source_id": "prd:sod", "filename": "职责分离.md", "text": "申请与审批必须由不同人员完成。"}]
    materialize_sod_contracts(asset, sources)
    first_ids = [row["contract_id"] for row in asset["segregation_of_duties_contracts"]]
    materialize_sod_contracts(asset, sources)
    assert [row["contract_id"] for row in asset["segregation_of_duties_contracts"]] == first_ids


def test_full_chinese_material_enters_sod_mainline() -> None:
    understood = _understood_from_chinese()
    assert {(row["role"], row["interface_id"]) for row in understood["permission_matrix"]} == {
        ("申请人", "op-create"),
        ("审批人", "op-approve"),
    }
    assert len(understood["segregation_of_duties_policies"]) == 1
    ir = build_behavior_ir_from_knowledge_asset(understood, runtime_actors=_runtime_actors())
    result = compile_obligations_from_behavior_ir(ir)
    assert any(row.get("property", {}).get("sod_policy_id") for row in result["obligations"])
