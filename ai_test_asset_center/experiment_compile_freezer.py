"""Public compile-freeze facade with one flow-data authority.

The core module freezes protocol steps and readback contracts. This facade then
freezes exact data dependencies across the complete flow, after every plan is
available. The legacy Disposable Fixture Contract remains a compatibility
projection and cannot declare whole-flow readiness.
"""
from copy import deepcopy

from . import experiment_compile_freezer_core as _core
from .flow_data_requirement import (
    STATUS_FROZEN as FLOW_DATA_FROZEN,
    build_flow_data_requirement,
)

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def _legacy_projection_input(frozen: dict) -> dict:
    """Normalize stored V1.5 contract fields for read-only coverage projection."""
    projection_source = deepcopy(frozen)
    contract = deepcopy(
        _dict(projection_source.get("disposable_fixture_contract"))
    )
    if not contract:
        return projection_source

    create_plan = [
        row
        for row in _list(contract.get("create_plan"))
        if isinstance(row, dict)
    ]
    create_operation_ref = _text(
        contract.get("create_operation_ref")
        or contract.get("create_operation_id")
        or (
            _dict(create_plan[0]).get("operation_ref")
            if create_plan
            else ""
        )
    )
    entity_ref = _text(
        contract.get("entity_ref")
        or contract.get("entity_id")
        or contract.get("primary_entity_id")
    )
    if create_operation_ref:
        contract["create_operation_ref"] = create_operation_ref
    if entity_ref:
        contract["entity_ref"] = entity_ref
    projection_source["disposable_fixture_contract"] = contract
    return projection_source


def freeze_compiled_experiment(
    experiment: dict,
    *,
    behavior_ir: dict,
) -> dict:
    frozen = _core.freeze_compiled_experiment(
        experiment,
        behavior_ir=behavior_ir,
    )
    if _text(_dict(frozen.get("compile_receipt")).get("status")) != "COMPILED":
        return frozen
    if _text(_dict(frozen.get("compile_freeze_receipt")).get("status")) != "FROZEN":
        return frozen

    requirement = build_flow_data_requirement(
        _legacy_projection_input(frozen),
        behavior_ir=behavior_ir,
    )
    if _text(requirement.get("status")) != FLOW_DATA_FROZEN:
        return _block(
            frozen,
            _text(requirement.get("reason_code"))
            or "BLOCKED_FLOW_DATA_BINDING_INCOMPLETE",
            _text(requirement.get("detail"))
            or "flow_data_requirement_not_frozen",
        )

    result = deepcopy(frozen)
    requirement_id = _text(requirement.get("requirement_id"))
    requirement_fingerprint = _text(
        requirement.get("requirement_fingerprint")
    )
    result["flow_data_requirement"] = requirement
    flow_requirements = deepcopy(_dict(result.get("flow_requirements")))
    flow_requirements.update(
        {
            "data_requirement_id": requirement_id,
            "data_requirement_fingerprint": requirement_fingerprint,
            "materialization_authority": _dict(
                requirement.get("materialization_authority")
            ),
        }
    )
    result["flow_requirements"] = flow_requirements

    legacy_contract = deepcopy(
        _dict(result.get("disposable_fixture_contract"))
    )
    if legacy_contract:
        projection = _dict(
            requirement.get("legacy_disposable_fixture_projection")
        )
        legacy_contract.update(
            {
                "projection_only": True,
                "is_flow_data_authority": False,
                "flow_data_requirement_id": requirement_id,
                "flow_coverage_status": _text(
                    projection.get("coverage_status")
                ),
                "covered_flow_operation_refs": list(
                    projection.get("covered_operation_refs") or []
                ),
                "covered_flow_entity_refs": list(
                    projection.get("covered_entity_refs") or []
                ),
            }
        )
        result["disposable_fixture_contract"] = legacy_contract

    existing_receipt = deepcopy(
        _dict(result.get("compile_freeze_receipt"))
    )
    reseal_payload = {
        "experiment_id": _text(result.get("experiment_id")),
        "obligation_id": _text(result.get("obligation_id")),
        "primary_operation_ref": _text(
            existing_receipt.get("primary_operation_ref")
        ),
        "flow_requirements": flow_requirements,
        "flow_data_requirement_id": requirement_id,
        "flow_data_requirement_fingerprint": requirement_fingerprint,
    }
    freeze_fingerprint = _fingerprint(reseal_payload)
    result["compile_freeze_receipt"] = {
        **existing_receipt,
        "status": "FROZEN",
        "freeze_fingerprint": freeze_fingerprint,
        **reseal_payload,
    }
    result["compile_receipt"] = {
        **_dict(result.get("compile_receipt")),
        "compile_freeze_status": "FROZEN",
        "compile_freeze_fingerprint": freeze_fingerprint,
        "flow_data_requirement_id": requirement_id,
        "flow_data_requirement_fingerprint": requirement_fingerprint,
        "fixture_contract_authority": "flow_data_requirement",
    }
    return result


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
