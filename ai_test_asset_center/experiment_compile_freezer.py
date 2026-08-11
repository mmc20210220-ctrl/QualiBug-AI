"""Public compile-freeze facade with flow-data and request-build authority.

The core module freezes protocol steps and readback contracts. This facade first
projects source-declared credential coordinates into request bodies as opaque
secret refs, then freezes exact data dependencies and finally proves that those
dependencies can form the source-declared HTTP requests. Secret values never
enter the compiled artifact and a known-unbuildable request never becomes a
FROZEN execution plan.
"""
from copy import deepcopy

from . import experiment_compile_freezer_core as _core
from .credential_request_projection import project_declared_credential_refs
from .flow_data_execution_contract import (
    STATUS_FROZEN as FLOW_DATA_EXECUTION_FROZEN,
    freeze_flow_data_execution_contract,
)
from .flow_data_requirement import (
    STATUS_FROZEN as FLOW_DATA_FROZEN,
    build_flow_data_requirement,
)
from .real_id_resolver import (
    infer_path_params,
    normalize_path_placeholders,
)
from .request_build_contract import (
    STATUS_BLOCKED as REQUEST_BUILD_BLOCKED,
    build_request_build_contract,
)

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
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


def _binding_ref_target(raw: object) -> str:
    if isinstance(raw, dict):
        return _text(
            raw.get("target")
            or raw.get("binding_target")
            or raw.get("name")
            or raw.get("consumer_target")
        )
    return _text(raw)


def _requirement_projection_input(frozen: dict) -> dict:
    """Align compatibility aliases without mutating the compiled experiment."""
    projection_source = _legacy_projection_input(frozen)
    for phase in ("precondition", "control", "treatment"):
        normalized_steps: list[dict] = []
        for raw_step in _list(projection_source.get(f"{phase}_plan")):
            if not isinstance(raw_step, dict):
                continue
            step = deepcopy(raw_step)
            output_specs: list[dict] = []
            for raw_spec in _list(step.get("output_binding_specs")):
                if not isinstance(raw_spec, dict):
                    continue
                spec = deepcopy(raw_spec)
                canonical = _text(
                    spec.get("target")
                    or spec.get("canonical_field_id")
                    or spec.get("output_field")
                    or spec.get("field")
                )
                if canonical:
                    spec.setdefault("target", canonical)
                output_specs.append(spec)
            if output_specs:
                step["output_binding_specs"] = output_specs

            # A wait observer path is transport input just like the business
            # request path. Feed its placeholders into the read-only requirement
            # projection; the stored treatment step and graph remain unchanged.
            wait_contract = _dict(step.get("wait_contract"))
            wait_path = normalize_path_placeholders(
                _text(
                    wait_contract.get("path_template")
                    or wait_contract.get("path")
                )
            )
            wait_targets = [
                _text(value)
                for value in infer_path_params(wait_path)
                if _text(value)
            ]
            if wait_targets:
                input_refs = deepcopy(_list(step.get("input_binding_refs")))
                existing_targets = {
                    _binding_ref_target(raw) for raw in input_refs
                    if _binding_ref_target(raw)
                }
                for target in wait_targets:
                    if target not in existing_targets:
                        input_refs.append(target)
                        existing_targets.add(target)
                step["input_binding_refs"] = input_refs
                step["wait_binding_targets"] = wait_targets
            normalized_steps.append(step)
        projection_source[f"{phase}_plan"] = normalized_steps
    return projection_source


def _request_build_block_detail(contract: dict) -> str:
    details: list[str] = []
    for row in _list(contract.get("steps")):
        if not isinstance(row, dict) or _text(row.get("status")) != REQUEST_BUILD_BLOCKED:
            continue
        components = ",".join(
            _text(value)
            for value in _list(row.get("blocked_components"))
            if _text(value)
        )
        details.append(
            f"{_text(row.get('phase'))}:{_text(row.get('step_id'))}:"
            f"{_text(row.get('operation_ref'))}:{components or 'request'}"
        )
    return ("request_build_contract:" + ";".join(details[:12]))[:1000]


def freeze_compiled_experiment(
    experiment: dict,
    *,
    behavior_ir: dict,
) -> dict:
    # Credential values are never compile-time data. Only the exact declared
    # secret coordinate is projected into the request body; the transport step
    # resolves that coordinate at runtime through the existing credential
    # authority. Project before the core freeze so all content hashes cover it.
    credential_projected, credential_receipt = project_declared_credential_refs(
        experiment,
        behavior_ir=behavior_ir,
    )
    if _text(credential_receipt.get("status")) == "BLOCKED":
        return _block(
            experiment,
            "BLOCKED_MISSING_BINDING",
            "credential_request_projection:"
            + ";".join(
                _text(row.get("reason_code"))
                + ":"
                + _text(row.get("target"))
                for row in _list(credential_receipt.get("issues"))[:12]
            ),
        )

    frozen = _core.freeze_compiled_experiment(
        credential_projected,
        behavior_ir=behavior_ir,
    )
    if _text(_dict(frozen.get("compile_receipt")).get("status")) != "COMPILED":
        return frozen
    if _text(_dict(frozen.get("compile_freeze_receipt")).get("status")) != "FROZEN":
        return frozen

    requirement = build_flow_data_requirement(
        _requirement_projection_input(frozen),
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
    execution_contract = freeze_flow_data_execution_contract(
        frozen,
        requirement,
    )
    if _text(execution_contract.get("status")) != FLOW_DATA_EXECUTION_FROZEN:
        return _block(
            frozen,
            _text(execution_contract.get("reason_code"))
            or "BLOCKED_FLOW_DATA_EXECUTION_CONTRACT_INCOMPLETE",
            _text(execution_contract.get("detail"))
            or "flow_data_execution_contract_not_frozen",
        )

    # FlowData answers "where can each dynamic value come from?". The request
    # contract answers the next question: "does that make every declared HTTP
    # request constructible?". Runtime channels stay DEFERRED, not fabricated.
    request_contract_input = deepcopy(frozen)
    request_contract_input["flow_data_requirement"] = requirement
    request_contract_input["flow_data_execution_contract"] = execution_contract
    request_build_contract = build_request_build_contract(
        request_contract_input,
        behavior_ir=behavior_ir,
        flow_execution_contract=execution_contract,
    )
    if _text(request_build_contract.get("status")) == REQUEST_BUILD_BLOCKED:
        blocked = _block(
            frozen,
            "BLOCKED_MISSING_BINDING",
            _request_build_block_detail(request_build_contract),
        )
        blocked["request_build_contract"] = request_build_contract
        return blocked

    result = deepcopy(frozen)
    requirement_id = _text(requirement.get("requirement_id"))
    requirement_fingerprint = _text(
        requirement.get("requirement_fingerprint")
    )
    execution_fingerprint = _text(
        execution_contract.get("contract_fingerprint")
    )
    request_build_fingerprint = _text(
        request_build_contract.get("contract_fingerprint")
    )
    result["flow_data_requirement"] = requirement
    result["flow_data_execution_contract"] = execution_contract
    result["request_build_contract"] = request_build_contract
    flow_requirements = deepcopy(_dict(result.get("flow_requirements")))
    flow_requirements.update(
        {
            "data_requirement_id": requirement_id,
            "data_requirement_fingerprint": requirement_fingerprint,
            "data_execution_contract_fingerprint": execution_fingerprint,
            "request_build_contract_fingerprint": request_build_fingerprint,
            "materialization_authority": _dict(
                requirement.get("materialization_authority")
            ),
            "cross_step_binding_authority": "process_graph_binding_ledger",
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
        "flow_data_execution_contract_fingerprint": execution_fingerprint,
        "request_build_contract_fingerprint": request_build_fingerprint,
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
        "flow_data_execution_contract_fingerprint": execution_fingerprint,
        "request_build_contract_fingerprint": request_build_fingerprint,
        "fixture_contract_authority": "flow_data_requirement",
        "request_build_authority": "request_build_contract",
    }
    return result


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
