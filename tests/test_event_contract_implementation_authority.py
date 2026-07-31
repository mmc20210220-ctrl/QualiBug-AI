from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_contract_implementation_authority import (
    apply_event_contract_validation_failures,
    prepare_formal_event_contract_authority,
)


INTERFACE_ID = "api:POST:/orders"
ADMIN_BEHAVIOR = "behavior:create-order:admin"
AUDITOR_BEHAVIOR = "behavior:create-order:auditor"
ADMIN_BINDING = "binding:create-order:admin"
AUDITOR_BINDING = "binding:create-order:auditor"


def _contract(actor_ref: str = "actor:admin", *, valid: bool = True) -> dict:
    row = {
        "contract_id": f"event-contract:{actor_ref}",
        "source_refs": [
            {
                "source_id": "event-doc",
                "locator": f"events.{actor_ref}",
                "quote_hash": f"sha256:{actor_ref}",
            }
        ],
        "operation_ref": INTERFACE_ID,
        "actor_ref": actor_ref,
        "observer_path": "/test/events",
        "events_path": "$.items",
        "event_id_field": "id",
        "event_type_field": "type",
        "correlation_field": "orderId",
        "correlation_query_parameter": "orderId",
        "correlation_source": {
            "location": "treatment_response",
            "path": "$.id",
        },
        "expected_event_type": "OrderCreated",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 3000,
        "status": "accepted",
    }
    if not valid:
        row.pop("expected_event_type")
    return row


def _behavior(behavior_id: str, actor_ref: str) -> dict:
    return {
        "behavior_id": behavior_id,
        "status": "CONFIRMED",
        "actor_refs": [actor_ref],
        "preconditions": [],
    }


def _binding(binding_id: str, behavior_id: str, *, ready: bool = True) -> dict:
    return {
        "binding_id": binding_id,
        "behavior_ref": behavior_id,
        "primary_api_interface_ref": INTERFACE_ID,
        "status": "BOUND" if ready else "PARTIAL",
        "scenario_planning_ready": ready,
    }


def _asset_and_model(contracts: list[dict]) -> tuple[dict, dict]:
    asset = {
        "interfaces": [
            {
                "interface_id": INTERFACE_ID,
                "method": "POST",
                "path": "/orders",
                "operation_id": "createOrder",
            }
        ],
        "event_formal_contracts": contracts,
        "coverage_gaps": [],
    }
    model = {
        "business_behaviors": [
            _behavior(ADMIN_BEHAVIOR, "actor:admin"),
            _behavior(AUDITOR_BEHAVIOR, "actor:auditor"),
        ],
        "behavior_implementation_bindings": [
            _binding(ADMIN_BINDING, ADMIN_BEHAVIOR),
            _binding(AUDITOR_BINDING, AUDITOR_BEHAVIOR),
        ],
    }
    return asset, model


def test_valid_event_contract_enters_formal_chain() -> None:
    asset, model = _asset_and_model([_contract()])

    unknowns = prepare_formal_event_contract_authority(asset, model)

    assert unknowns == []
    assert len(asset["event_formal_contracts"]) == 1
    formal = asset["event_formal_contracts"][0]
    assert formal["schema_version"] == "qualibug.formal-event-contract.v1"
    assert formal["status"] == "accepted"
    assert formal["expected_event_type"] == "OrderCreated"
    assert asset["event_formal_contract_validation_failures"] == []
    assert asset["coverage_gaps"] == []


def test_invalid_exact_event_contract_is_retained_but_not_admitted() -> None:
    invalid = _contract(valid=False)
    asset, model = _asset_and_model([invalid])

    unknowns = prepare_formal_event_contract_authority(asset, model)

    assert asset["event_formal_contracts"] == []
    assert asset["event_formal_contract_candidates"] == [invalid]
    assert len(asset["event_formal_contract_validation_failures"]) == 1
    failure = asset["event_formal_contract_validation_failures"][0]
    assert failure["reason_codes"] == [
        "FORMAL_EVENT_FIELDS_MISSING:expected_event_type"
    ]
    assert len(unknowns) == 1
    unknown = unknowns[0]
    assert unknown["reason_code"] == "IMPLEMENTATION_EVENT_CONTRACT_INVALID"
    assert unknown["behavior_ref"] == ADMIN_BEHAVIOR
    assert unknown["implementation_binding_ref"] == ADMIN_BINDING
    assert unknown["event_contract_validation_reasons"] == [
        "FORMAL_EVENT_FIELDS_MISSING:expected_event_type"
    ]
    assert asset["coverage_gaps"][0]["gap_type"] == (
        "formal_event_contract_invalid_for_implementation_binding"
    )


def test_invalid_admin_contract_blocks_only_admin_binding() -> None:
    asset, model = _asset_and_model([_contract(valid=False)])
    validation_unknowns = prepare_formal_event_contract_authority(asset, model)
    bindings = model["behavior_implementation_bindings"]
    gate = {
        "status": "PASS",
        "entry_allowed": True,
        "scenario_planning_allowed": True,
        "metrics": {},
    }

    projected, unknowns, _conflicts, projected_gate = (
        apply_event_contract_validation_failures(
            bindings,
            [],
            [],
            gate,
            validation_unknowns,
        )
    )

    by_id = {row["binding_id"]: row for row in projected}
    assert by_id[ADMIN_BINDING]["scenario_planning_ready"] is False
    assert by_id[ADMIN_BINDING]["status"] == "PARTIAL"
    assert by_id[ADMIN_BINDING]["formal_event_contract_validation_blocked"] is True
    assert by_id[AUDITOR_BINDING]["scenario_planning_ready"] is True
    assert by_id[AUDITOR_BINDING]["status"] == "BOUND"
    assert projected_gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"
    assert projected_gate["entry_allowed"] is False
    assert projected_gate["metrics"][
        "formal_event_contract_validation_blocked_binding_count"
    ] == 1
    assert [row["behavior_ref"] for row in unknowns] == [ADMIN_BEHAVIOR]


def test_invalid_contract_for_unrelated_actor_does_not_block_other_behavior() -> None:
    asset, model = _asset_and_model(
        [_contract(actor_ref="actor:other", valid=False)]
    )

    unknowns = prepare_formal_event_contract_authority(asset, model)

    assert unknowns == []
    assert asset["event_formal_contracts"] == []
    assert len(asset["coverage_gaps"]) == 1
