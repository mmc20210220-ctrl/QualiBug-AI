"""Runtime credentials must match the full Actor Authorization Contract coordinate."""
from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.credential_identity import (
    govern_runtime_materialization_credentials,
    govern_runtime_plan_credentials,
)


def _contract() -> dict:
    return {
        "contract_id": "contract:register",
        "operation_clause": {
            "operation_ref": "登记",
            "object_refs": ["入库单"],
        },
        "credential_requirements": [
            {
                "requirement_kind": "ACTOR_IDENTITY",
                "actor_ref": "仓库员",
                "credential_selection_required": True,
                "credential_selected": False,
                "automatic_role_substitution_allowed": False,
            }
        ],
        "oracle_plan": {"permission_decision_requirement": "ALLOW"},
    }


def _model() -> dict:
    return {
        "actors": [
            {
                "actor_id": "actor:warehouse-clerk",
                "name": "仓库员",
                "authorization_contracts": [
                    {
                        "authorization_contract_id": "auth:register-own-tenant",
                        "decision": "ALLOW",
                        "declared_decision": "ALLOW",
                        "resource_refs": ["入库单"],
                        "actions": ["登记"],
                        "scope": {
                            "tenant": "tenant-a",
                            "data_scope": "SELF",
                        },
                        "coordinate_complete": True,
                    }
                ],
            }
        ],
        "scenario_execution_contracts": [_contract()],
    }


def _plan(core_selected: str = "credential:wrong-core-choice") -> dict:
    return {
        "plan_id": "plan:register",
        "execution_contract_ref": "contract:register",
        "status": "TEMPLATE_READY",
        "formal_runtime_plan": True,
        "credential_template": {
            "credential_slots": [
                {
                    "slot_id": "slot:warehouse-clerk",
                    "actor_ref": "仓库员",
                    "credential_ref": core_selected,
                    "resolution_status": "CREDENTIAL_REF_RESOLVED",
                    "credential_value_loaded": False,
                    "automatic_role_substitution_allowed": False,
                }
            ],
            "credentials_selected": True,
        },
        "unresolved_runtime_plan_semantics": [],
    }


def _account(
    credential_ref: str,
    account_ref: str,
    tenant: str,
    *,
    status: str = "ACTIVE",
) -> dict:
    return {
        "credential_ref": credential_ref,
        "account_ref": account_ref,
        "role": "仓库员",
        "tenant_id": tenant,
        "data_scope": "SELF",
        "status": status,
    }


def _asset(accounts: list[dict]) -> dict:
    return {
        "scenario_execution_contracts": [_contract()],
        "runtime_plans": [_plan()],
        "runtime_plan_unknowns": [
            {
                "unknown_id": "old-role-only-ambiguity",
                "reason_code": "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS",
                "runtime_plan_ref": "plan:register",
                "blocks_runtime_plan": True,
            }
        ],
        "test_account_refs": accounts,
    }


def _slot(asset: dict) -> dict:
    return asset["runtime_plans"][0]["credential_template"]["credential_slots"][0]


def test_unique_full_coordinate_match_replaces_role_order_choice() -> None:
    asset = _asset(
        [
            _account("credential:tenant-b", "warehouse-b", "tenant-b"),
            _account("credential:tenant-a", "warehouse-a", "tenant-a"),
        ]
    )
    model = _model()

    govern_runtime_plan_credentials(asset, model)

    slot = _slot(asset)
    assert slot["credential_ref"] == "credential:tenant-a"
    assert slot["account_ref"] == "warehouse-a"
    assert slot["identity_match_status"] == "EXACT"
    assert slot["required_identity_coordinates"] == {
        "tenant_ref": ["tenant-a"],
        "ownership_scope": ["SELF"],
    }
    assert slot["credential_identity_coordinates"]["tenant_ref"] == ["tenant-a"]
    assert asset["runtime_plans"][0]["status"] == "TEMPLATE_READY"
    assert asset["runtime_plans"][0]["credential_template"][
        "credential_selection_by_role_order_allowed"
    ] is False
    assert asset["runtime_plan_unknowns"] == []


def test_role_match_without_required_tenant_coordinate_blocks() -> None:
    asset = _asset(
        [_account("credential:tenant-b", "warehouse-b", "tenant-b")]
    )
    model = _model()

    govern_runtime_plan_credentials(asset, model)

    slot = _slot(asset)
    assert slot["credential_ref"] is None
    assert slot["identity_match_status"] == "UNRESOLVED"
    assert asset["runtime_plans"][0]["status"] == "INCOMPLETE"
    assert asset["runtime_plans"][0]["formal_runtime_plan"] is False
    assert {
        row["reason_code"] for row in asset["runtime_plan_unknowns"]
    } == {"RUNTIME_PLAN_CREDENTIAL_IDENTITY_COORDINATE_MISMATCH"}


def test_two_accounts_matching_same_coordinate_remain_ambiguous() -> None:
    asset = _asset(
        [
            _account("credential:tenant-a-1", "warehouse-a-1", "tenant-a"),
            _account("credential:tenant-a-2", "warehouse-a-2", "tenant-a"),
        ]
    )
    model = _model()

    govern_runtime_plan_credentials(asset, model)

    slot = _slot(asset)
    assert slot["credential_ref"] is None
    assert asset["runtime_plans"][0]["status"] == "INCOMPLETE"
    unknown = asset["runtime_plan_unknowns"][0]
    assert unknown["reason_code"] == "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS"
    assert unknown["candidate_credential_refs"] == [
        "credential:tenant-a-1",
        "credential:tenant-a-2",
    ]


def test_inactive_matching_account_is_not_selected() -> None:
    asset = _asset(
        [
            _account(
                "credential:tenant-a-disabled",
                "warehouse-a-disabled",
                "tenant-a",
                status="DISABLED",
            )
        ]
    )
    model = _model()

    govern_runtime_plan_credentials(asset, model)

    assert _slot(asset)["credential_ref"] is None
    assert asset["runtime_plan_unknowns"][0]["reason_code"] == (
        "RUNTIME_PLAN_CREDENTIAL_REF_UNRESOLVED"
    )


def test_materialization_preserves_planned_account_identity_coordinates() -> None:
    asset = _asset(
        [_account("credential:tenant-a", "warehouse-a", "tenant-a")]
    )
    model = _model()
    govern_runtime_plan_credentials(asset, model)
    asset["runtime_materializations"] = [
        {
            "materialization_id": "materialization:register",
            "runtime_plan_ref": "plan:register",
            "credential_binding": {
                "credential_slots": [
                    {
                        "slot_id": "slot:warehouse-clerk",
                        "actor_ref": "仓库员",
                        "credential_ref": "credential:tenant-a",
                    }
                ],
                "credential_refs_resolved": True,
            },
        }
    ]
    asset["runtime_materialization_unknowns"] = []

    govern_runtime_materialization_credentials(asset, model)

    binding = asset["runtime_materializations"][0]["credential_binding"]
    slot = binding["credential_slots"][0]
    assert slot["credential_ref"] == "credential:tenant-a"
    assert slot["account_ref"] == "warehouse-a"
    assert slot["identity_match_status"] == "EXACT"
    assert slot["required_identity_coordinates"]["tenant_ref"] == ["tenant-a"]
    assert binding["credential_identity_coordinates_resolved"] is True
    assert asset["runtime_materialization_unknowns"] == []


def test_materialization_cannot_substitute_another_same_role_account() -> None:
    asset = _asset(
        [_account("credential:tenant-a", "warehouse-a", "tenant-a")]
    )
    model = _model()
    govern_runtime_plan_credentials(asset, model)
    asset["runtime_materializations"] = [
        {
            "materialization_id": "materialization:register",
            "runtime_plan_ref": "plan:register",
            "credential_binding": {
                "credential_slots": [
                    {
                        "slot_id": "slot:warehouse-clerk",
                        "actor_ref": "仓库员",
                        "credential_ref": "credential:tenant-b",
                    }
                ],
                "credential_refs_resolved": True,
            },
        }
    ]
    asset["runtime_materialization_unknowns"] = []

    govern_runtime_materialization_credentials(asset, model)

    binding = asset["runtime_materializations"][0]["credential_binding"]
    slot = binding["credential_slots"][0]
    assert slot["credential_ref"] is None
    assert slot["identity_match_status"] == "UNRESOLVED"
    assert binding["credential_refs_resolved"] is False
    assert asset["runtime_materialization_unknowns"][0]["reason_code"] == (
        "RUNTIME_MATERIALIZATION_CREDENTIAL_IDENTITY_SUBSTITUTED"
    )


def test_governance_does_not_mutate_account_source_rows() -> None:
    accounts = [_account("credential:tenant-a", "warehouse-a", "tenant-a")]
    original = deepcopy(accounts)
    asset = _asset(accounts)

    govern_runtime_plan_credentials(asset, _model())

    assert accounts == original
