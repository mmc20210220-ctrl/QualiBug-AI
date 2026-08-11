from __future__ import annotations


def _step(*, body: dict, contract: dict | None = None, readback: dict | None = None) -> dict:
    row = {
        "step_id": "create-1",
        "phase": "treatment",
        "method": "POST",
        "governance_receipt": {
            "write": {"status_code": 201, "body": body},
            "before": {"status_code": 200, "body": {"items": []}},
            "materialized_request_body": {"name": "fixture"},
        },
    }
    if contract is not None:
        row["identity_output_binding"] = contract
    if readback is not None:
        row["governance_receipt"]["response_bound_after"] = {
            "status_code": 200,
            "body": readback,
        }
    return row


def _contract(path: str = "id") -> dict:
    return {
        "schema_version": "qualibug.identity-output-binding.v1",
        "status": "FROZEN",
        "entity_ref": "entity-item",
        "source_identity_field": "id",
        "source_path": path,
        "consumer_targets": ["itemId"],
        "alias_targets": ["itemId", "id"],
        "source_authority": "behavior_ir.entities.identity_fields",
    }


def test_generic_success_code_cannot_prove_new_entity_effect() -> None:
    from ai_test_asset_center.observer_contracts import (
        _response_only_effect_authority,
    )

    allowed, reason, authority = _response_only_effect_authority(
        [_step(body={"code": "SUCCESS"})],
        {
            "effect_basis": "write_response_new_identity",
            "treatment_effect_count": 1,
        },
    )

    assert allowed is False
    assert reason == "BUSINESS_EFFECT_IDENTITY_OUTPUT_AUTHORITY_MISSING"
    assert authority == {}


def test_frozen_declared_identity_output_can_authorize_write_response_effect() -> None:
    from ai_test_asset_center.observer_contracts import (
        _response_only_effect_authority,
    )

    allowed, reason, authority = _response_only_effect_authority(
        [_step(body={"id": "I-1", "code": "SUCCESS"}, contract=_contract())],
        {
            "effect_basis": "write_response_new_identity",
            "treatment_effect_count": 1,
        },
    )

    assert allowed is True
    assert reason == "frozen_identity_output"
    assert authority["source_path"] == "id"


def test_declared_identity_value_must_exist_at_exact_source_path() -> None:
    from ai_test_asset_center.observer_contracts import (
        _response_only_effect_authority,
    )

    allowed, reason, _ = _response_only_effect_authority(
        [_step(body={"code": "I-1"}, contract=_contract("id"))],
        {
            "effect_basis": "write_response_new_identity",
            "treatment_effect_count": 1,
        },
    )

    assert allowed is False
    assert reason == "BUSINESS_EFFECT_IDENTITY_OUTPUT_VALUE_MISSING"


def test_response_bound_effect_requires_same_declared_identity_in_readback() -> None:
    from ai_test_asset_center.observer_contracts import (
        _response_only_effect_authority,
    )

    allowed, reason, _ = _response_only_effect_authority(
        [
            _step(
                body={"id": "I-1"},
                contract=_contract(),
                readback={"id": "OTHER"},
            )
        ],
        {
            "effect_basis": "response_bound_create_observer",
            "treatment_effect_count": 1,
        },
    )

    assert allowed is False
    assert reason == "BUSINESS_EFFECT_IDENTITY_READBACK_MISMATCH"


def test_compiled_identity_contract_is_injected_only_for_exact_runtime_step() -> None:
    from ai_test_asset_center.observer_contracts import (
        _inject_identity_output_contracts,
        _restore_injected_contracts,
    )

    runtime_step = _step(body={"id": "I-1"})
    observations = {"execution_steps": [runtime_step]}
    changes = _inject_identity_output_contracts(
        {
            "treatment_plan": [
                {
                    "step_id": "create-1",
                    "identity_output_binding": _contract(),
                }
            ]
        },
        observations,
    )

    assert runtime_step["identity_output_binding"] == _contract()
    _restore_injected_contracts(changes)
    assert "identity_output_binding" not in runtime_step
