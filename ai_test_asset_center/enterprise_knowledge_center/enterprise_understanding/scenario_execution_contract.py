"""Compile canonical Scenario IR into non-executable execution requirements.

This compiler never reconstructs business semantics from legacy scenario projections. It
accepts the canonical operation clause, condition expression and outcome contracts, then
turns each predicate and each mandatory outcome into an explicit runtime requirement.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from .behavior_ir_logic_gate import (
    condition_expression_complete,
    iter_condition_predicates,
    project_outcome_effects,
)
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

SCENARIO_EXECUTION_CONTRACT_SCHEMA = "qualibug.scenario-execution-contract.v1"
SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA = "qualibug.scenario-execution-contract-gate.v1"

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_PATH_PARAMETER_RE = re.compile(r"\{([^{}]+)\}")


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _condition_value(predicate: dict[str, Any]) -> dict[str, Any]:
    value = as_dict(predicate.get("value_candidate"))
    return {
        "raw": value.get("raw") or predicate.get("raw_value"),
        "value_type": value.get("value_type") or "TEXT",
        "normalized_value": value.get("normalized_value"),
        "unit": value.get("unit"),
        "source_backed_semantic_value": True,
        "runtime_value_materialized": False,
    }


def _field_index(fields: Iterable[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in fields:
        value = text(raw)
        if value and _norm(value):
            result[_norm(value.split(".")[-1])] = value
    return result


def _canonical_scenario_reasons(scenario: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    operation = as_dict(scenario.get("operation_clause"))
    expression = as_dict(scenario.get("condition_expression"))
    outcomes = _dicts(scenario.get("outcome_contracts"))
    if text(operation.get("status")) != "CONFIRMED":
        reasons.append("EXECUTION_CONTRACT_OPERATION_CLAUSE_MISSING")
    if not condition_expression_complete(expression):
        reasons.append("EXECUTION_CONTRACT_CONDITION_EXPRESSION_INCOMPLETE")
    mandatory = [row for row in outcomes if bool(row.get("mandatory"))]
    if not mandatory or any(
        text(row.get("status")) != "CONFIRMED" for row in mandatory
    ):
        reasons.append("EXECUTION_CONTRACT_OUTCOME_CONTRACTS_INCOMPLETE")
    if not text(scenario.get("canonical_semantics_version")):
        reasons.append("EXECUTION_CONTRACT_CANONICAL_SEMANTICS_MISSING")
    return unique_text(reasons)


def _path_parameter_requirements(
    scenario: dict[str, Any], contract_fields: list[str]
) -> list[dict[str, Any]]:
    action = as_dict(scenario.get("action_entry"))
    parameters = unique_text(_PATH_PARAMETER_RE.findall(text(action.get("path"))))
    predicates = iter_condition_predicates(
        as_dict(scenario.get("condition_expression"))
    )
    result: list[dict[str, Any]] = []
    for name in parameters:
        normalized = _norm(name)
        candidates = [
            row
            for row in predicates
            if normalized
            and normalized
            in {
                _norm(row.get("field_candidate")),
                _norm(text(row.get("field_candidate")).split(".")[-1]),
            }
        ]
        source = candidates[0] if len(candidates) == 1 else {}
        result.append(
            {
                "field": name,
                "location": "PATH",
                "required": True,
                "source_predicate_ref": source.get("predicate_id"),
                "source_slot_ref": source.get("slot_ref"),
                "semantic_value_requirement": (
                    _condition_value(source) if source else {}
                ),
                "runtime_value_source": (
                    "SOURCE_BACKED_CONDITION_PREDICATE"
                    if source
                    else "RUNTIME_ENTITY_IDENTIFIER"
                ),
                "runtime_value_materialized": False,
                "contract_declared": (
                    name in contract_fields
                    or _norm(name) in _field_index(contract_fields)
                ),
            }
        )
    return result


def _request_field_requirements(
    scenario: dict[str, Any], contract_fields: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    indexed = _field_index(contract_fields)
    request_fields: list[dict[str, Any]] = []
    setup_requirements: list[dict[str, Any]] = []
    for predicate in iter_condition_predicates(
        as_dict(scenario.get("condition_expression"))
    ):
        field = text(predicate.get("field_candidate"))
        canonical = indexed.get(_norm(field.split(".")[-1]))
        requirement = {
            "predicate_ref": predicate.get("predicate_id"),
            "slot_ref": predicate.get("slot_ref"),
            "field_candidate": field,
            "operator": predicate.get("operator_candidate"),
            "semantic_value_requirement": _condition_value(predicate),
            "runtime_value_materialized": False,
        }
        if canonical:
            request_fields.append(
                {
                    **requirement,
                    "field": canonical,
                    "location": "UNRESOLVED_CONTRACT_LOCATION",
                    "derivation": (
                        "EXACT_CONDITION_PREDICATE_TO_DECLARED_CONTRACT_FIELD"
                    ),
                    "required": True,
                }
            )
        else:
            setup_requirements.append(
                {
                    **requirement,
                    "requirement_kind": "EXISTING_ENTITY_OR_SYSTEM_STATE",
                    "derivation": (
                        "CONDITION_PREDICATE_NOT_DECLARED_AS_ACTION_REQUEST_FIELD"
                    ),
                }
            )
    return request_fields, setup_requirements


def _credential_requirements(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    actors = unique_text(
        as_list(as_dict(scenario.get("operation_clause")).get("actor_refs"))
    )
    if not actors:
        return [
            {
                "requirement_kind": "AUTHENTICATION_CONTEXT",
                "actor_ref": "UNSPECIFIED_ACTOR",
                "credential_selection_required": True,
                "credential_selected": False,
                "automatic_role_substitution_allowed": False,
            }
        ]
    return [
        {
            "requirement_kind": "ACTOR_IDENTITY",
            "actor_ref": actor,
            "credential_selection_required": True,
            "credential_selected": False,
            "automatic_role_substitution_allowed": False,
        }
        for actor in actors
    ]


def _outcome_observer_map(
    scenario: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    observers = as_dict(scenario.get("observer_plan"))
    for row in _dicts(observers.get("outcome_observers")):
        outcome_ref = text(row.get("outcome_ref"))
        if outcome_ref:
            result.setdefault(outcome_ref, []).append(row)
    return result


def _oracle_plan(
    scenario: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    outcomes = _dicts(scenario.get("outcome_contracts"))
    mandatory = [row for row in outcomes if bool(row.get("mandatory"))]
    observer_map = _outcome_observer_map(scenario)
    unresolved: list[str] = []
    assertions: list[dict[str, Any]] = []
    for outcome in mandatory:
        outcome_ref = text(outcome.get("outcome_id"))
        observers = [
            row
            for row in observer_map.get(outcome_ref, [])
            if text(row.get("status")) == "BOUND"
        ]
        if len(observers) != 1:
            unresolved.append("EXECUTION_CONTRACT_OUTCOME_OBSERVER_UNRESOLVED")
        assertions.append(
            {
                "outcome_ref": outcome_ref,
                "outcome_type": outcome.get("outcome_type"),
                "target_object_refs": unique_text(
                    as_list(outcome.get("target_object_refs"))
                ),
                "field_ref": outcome.get("field_ref"),
                "expected_decision": outcome.get("expected_decision"),
                "from_value": outcome.get("from_value"),
                "to_value": outcome.get("to_value"),
                "operator": outcome.get("operator"),
                "value_ref": outcome.get("value_ref"),
                "statement": outcome.get("statement"),
                "observation_phase": outcome.get("observation_phase"),
                "observer_requirements": observers,
                "observer_binding_complete": len(observers) == 1,
                "concrete_assertion_compiled": False,
            }
        )

    permission = next(
        (
            row
            for row in mandatory
            if text(row.get("outcome_type")) == "PERMISSION_DECISION"
        ),
        {},
    )
    state = [
        row
        for row in mandatory
        if text(row.get("outcome_type")) == "STATE_TRANSITION"
    ]
    data = [
        row
        for row in mandatory
        if text(row.get("outcome_type"))
        not in {"PERMISSION_DECISION", "STATE_TRANSITION"}
    ]
    observers = as_dict(scenario.get("observer_plan"))
    return (
        {
            "oracle_level": "CANONICAL_OUTCOME_CONTRACT_REQUIREMENTS",
            "outcome_contract_requirements": deepcopy(mandatory),
            "outcome_assertion_requirements": assertions,
            "permission_decision_requirement": text(
                permission.get("expected_decision")
            )
            or "UNSPECIFIED",
            "semantic_effect_requirements": project_outcome_effects(mandatory),
            "state_effect_requirements": deepcopy(state),
            "data_effect_requirements": deepcopy(data),
            "condition_observers": _dicts(observers.get("condition_observers")),
            "outcome_observers": _dicts(observers.get("outcome_observers")),
            "effect_observers": _dicts(observers.get("effect_observers")),
            "response_observers": _dicts(observers.get("response_observers")),
            "http_status_expectation": "UNRESOLVED_FROM_SOURCE_CONTRACT",
            "response_body_assertion_requirements": [],
            "database_assertion_requirements": [],
            "concrete_assertion_compiled": False,
            "legacy_oracle_fields_are_projections": True,
            "raw_text_oracle_reparse_allowed": False,
        },
        unique_text(unresolved),
    )


def _snapshot_plan(scenario: dict[str, Any]) -> dict[str, Any]:
    observers = as_dict(scenario.get("observer_plan"))
    condition_observers = _dicts(observers.get("condition_observers"))
    outcome_observers = _dicts(observers.get("outcome_observers"))
    after_observers = [
        row
        for row in outcome_observers
        if text(row.get("status")) == "BOUND"
    ]
    before_outcomes = [
        row
        for row in _dicts(scenario.get("outcome_contracts"))
        if bool(row.get("mandatory"))
        and text(row.get("observation_phase")) not in {"", "RESPONSE"}
    ]
    return {
        "before_snapshot_required": bool(condition_observers or before_outcomes),
        "after_snapshot_required": bool(after_observers),
        "before_observer_requirements": condition_observers,
        "after_observer_requirements": after_observers,
        "snapshot_consistency_scope": "SAME_SCENARIO_ENTITY_IDENTITY",
        "runtime_query_compiled": False,
        "before_snapshot_compiled": False,
        "after_snapshot_compiled": False,
    }


def _cleanup_requirements(scenario: dict[str, Any]) -> dict[str, Any]:
    method = text(as_dict(scenario.get("action_entry")).get("method")).upper()
    write = method in _WRITE_METHODS
    compensations = unique_text(
        row.get("statement")
        for row in _dicts(scenario.get("outcome_contracts"))
        if text(row.get("outcome_type")) == "COMPENSATION"
    )
    if not write:
        strategy = "NOT_REQUIRED_READ_ONLY_ACTION"
    elif compensations:
        strategy = "SOURCE_BACKED_COMPENSATION_REQUIRED"
    else:
        strategy = "REVERSIBLE_CLEANUP_OR_ISOLATED_SANDBOX_REQUIRED"
    return {
        "write_action": write,
        "cleanup_required": write,
        "strategy_requirement": strategy,
        "source_backed_compensation_candidates": compensations,
        "destructive_execution_allowed_without_cleanup": False,
        "cleanup_action_compiled": False,
        "cleanup_verification_compiled": False,
    }


def _compile_contract(
    scenario: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    unknowns: list[dict[str, Any]] = []
    scenario_id = text(scenario.get("scenario_id"))
    action = as_dict(scenario.get("action_entry"))
    if text(scenario.get("status")) != "PLANNABLE":
        return None, []
    unresolved = _canonical_scenario_reasons(scenario)
    if (
        not scenario_id
        or not bool(action.get("authoritative"))
        or not text(action.get("interface_id"))
    ):
        unresolved.append("EXECUTION_CONTRACT_AUTHORITATIVE_ACTION_ENTRY_MISSING")
    contract_fields = unique_text(as_list(action.get("contract_fields")))
    request_fields, setup_requirements = _request_field_requirements(
        scenario, contract_fields
    )
    path_parameters = _path_parameter_requirements(scenario, contract_fields)
    oracle, oracle_unknowns = _oracle_plan(scenario)
    unresolved.extend(oracle_unknowns)
    contract_id = stable_id(
        "scenario_execution_contract", scenario_id, action.get("interface_id")
    )
    evidence = dedupe_evidence(as_list(scenario.get("evidence")))
    if not evidence:
        unresolved.append("EXECUTION_CONTRACT_SOURCE_EVIDENCE_MISSING")
    unresolved = unique_text(unresolved)
    contract = {
        "schema": SCENARIO_EXECUTION_CONTRACT_SCHEMA,
        "contract_id": contract_id,
        "scenario_ref": scenario_id,
        "behavior_ref": scenario.get("behavior_ref"),
        "implementation_binding_ref": scenario.get("implementation_binding_ref"),
        "scenario_type": scenario.get("scenario_type"),
        "canonical_semantics_version": scenario.get("canonical_semantics_version"),
        "operation_clause": deepcopy(as_dict(scenario.get("operation_clause"))),
        "condition_expression": deepcopy(
            as_dict(scenario.get("condition_expression"))
        ),
        "outcome_contracts": deepcopy(_dicts(scenario.get("outcome_contracts"))),
        "action_contract": {
            "interface_id": action.get("interface_id"),
            "method": action.get("method"),
            "path": action.get("path"),
            "operation_id": action.get("operation_id"),
            "authoritative": bool(action.get("authoritative")),
            "derivation": action.get("derivation"),
            "declared_contract_fields": contract_fields,
        },
        "request_contract": {
            "path_parameter_requirements": path_parameters,
            "request_field_requirements": request_fields,
            "undeclared_request_fields_allowed": False,
            "request_field_locations_resolved": False,
            "request_payload_compiled": False,
        },
        "credential_requirements": _credential_requirements(scenario),
        "test_data_requirements": setup_requirements,
        "oracle_plan": oracle,
        "snapshot_plan": _snapshot_plan(scenario),
        "cleanup_requirements": _cleanup_requirements(scenario),
        "evidence": evidence,
        "unresolved_contract_semantics": unresolved,
        "status": "INCOMPLETE" if unresolved else "REQUIREMENTS_READY",
        "formal_execution_contract": not bool(unresolved),
        "execution_allowed": False,
        "request_payload_compiled": False,
        "credentials_selected": False,
        "test_data_materialized": False,
        "database_connections_opened": False,
        "ui_locators_compiled": False,
        "expected_assertions_compiled": False,
        "snapshots_compiled": False,
        "cleanup_plan_compiled": False,
        "runtime_environment_validated": False,
        "canonical_semantics_required": True,
        "legacy_semantic_fields_are_authoritative": False,
        "downstream_raw_text_reparse_allowed": False,
    }
    for reason in unresolved:
        unknowns.append(
            {
                "unknown_id": stable_id(
                    "scenario_execution_contract_unknown", contract_id, reason
                ),
                "kind": reason,
                "reason_code": reason,
                "contract_ref": contract_id,
                "scenario_ref": scenario_id,
                "blocks_execution_contract": True,
                "execution_allowed": False,
            }
        )
    return contract, unknowns


def build_scenario_execution_contracts(
    asset: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scenario_gate = as_dict(asset.get("scenario_ir_gate"))
    scenarios = _dicts(asset.get("scenario_ir") or model.get("scenario_ir"))
    if not bool(scenario_gate.get("entry_allowed")):
        return [], [], {
            "schema": SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA,
            "status": "BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE",
            "entry_allowed": False,
            "execution_contract_ready": False,
            "execution_allowed": False,
            "upstream_scenario_ir_status": text(scenario_gate.get("status"))
            or "NOT_BUILT",
            "metrics": {
                "execution_contract_count": 0,
                "ready_execution_contract_count": 0,
                "incomplete_execution_contract_count": 0,
                "canonical_execution_contract_count": 0,
            },
            "quality_claim": (
                "EXECUTION_CONTRACT_NOT_BUILT_WHEN_SCENARIO_IR_GATE_CLOSED"
            ),
        }
    contracts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    plannable_scenarios = [
        row for row in scenarios if text(row.get("status")) == "PLANNABLE"
    ]
    for scenario in plannable_scenarios:
        contract, rows = _compile_contract(scenario)
        unknowns.extend(rows)
        if contract is not None:
            contracts.append(contract)
    contracts = list(
        {
            text(row.get("contract_id")): row
            for row in contracts
            if text(row.get("contract_id"))
        }.values()
    )
    ready = sum(
        1
        for row in contracts
        if text(row.get("status")) == "REQUIREMENTS_READY"
    )
    incomplete = sum(
        1 for row in contracts if text(row.get("status")) == "INCOMPLETE"
    )
    covered = {text(row.get("scenario_ref")) for row in contracts}
    if incomplete or len(covered) < len(plannable_scenarios):
        status = "BLOCKED_EXECUTION_CONTRACT_INCOMPLETE"
    elif contracts:
        status = "PASS"
    else:
        status = "NO_EXECUTION_CONTRACT_COMPILED"
    gate = {
        "schema": SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "execution_contract_ready": status == "PASS",
        "execution_allowed": False,
        "upstream_scenario_ir_status": scenario_gate.get("status"),
        "metrics": {
            "execution_contract_count": len(contracts),
            "ready_execution_contract_count": ready,
            "incomplete_execution_contract_count": incomplete,
            "covered_scenario_count": len(covered),
            "plannable_scenario_count": len(plannable_scenarios),
            "canonical_execution_contract_count": sum(
                1
                for row in contracts
                if bool(row.get("operation_clause"))
                and bool(row.get("condition_expression"))
                and bool(as_list(row.get("outcome_contracts")))
            ),
            "mandatory_outcome_requirement_count": sum(
                len(
                    as_list(
                        as_dict(row.get("oracle_plan")).get(
                            "outcome_contract_requirements"
                        )
                    )
                )
                for row in contracts
            ),
            "path_parameter_requirement_count": sum(
                len(
                    as_list(
                        as_dict(row.get("request_contract")).get(
                            "path_parameter_requirements"
                        )
                    )
                )
                for row in contracts
            ),
            "request_field_requirement_count": sum(
                len(
                    as_list(
                        as_dict(row.get("request_contract")).get(
                            "request_field_requirements"
                        )
                    )
                )
                for row in contracts
            ),
            "credential_requirement_count": sum(
                len(as_list(row.get("credential_requirements")))
                for row in contracts
            ),
            "test_data_requirement_count": sum(
                len(as_list(row.get("test_data_requirements")))
                for row in contracts
            ),
            "write_cleanup_requirement_count": sum(
                1
                for row in contracts
                if bool(
                    as_dict(row.get("cleanup_requirements")).get(
                        "cleanup_required"
                    )
                )
            ),
            "execution_contract_unknown_count": len(unknowns),
        },
        "request_payload_compiled": False,
        "credentials_selected": False,
        "test_data_materialized": False,
        "expected_assertions_compiled": False,
        "snapshots_compiled": False,
        "cleanup_plan_compiled": False,
        "runtime_environment_validated": False,
        "canonical_semantics_required": True,
        "legacy_semantic_fields_are_authoritative": False,
        "downstream_raw_text_reparse_allowed": False,
        "quality_claim": (
            "EXECUTION_REQUIREMENTS_CLOSURE_NOT_RUNTIME_EXECUTABILITY_OR_BUG_FINDING"
        ),
    }
    return contracts, unknowns, gate


def _relationships(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for contract in contracts:
        contract_id = text(contract.get("contract_id"))
        scenario_id = text(contract.get("scenario_ref"))
        interface_id = text(
            as_dict(contract.get("action_contract")).get("interface_id")
        )
        accepted = text(contract.get("status")) == "REQUIREMENTS_READY"
        if contract_id and scenario_id:
            edges.append(
                {
                    "edge_id": stable_id(
                        "edge",
                        "scenario_ir_to_execution_contract",
                        scenario_id,
                        contract_id,
                    ),
                    "from": scenario_id,
                    "to": contract_id,
                    "relation": "scenario_ir_to_execution_contract",
                    "status": "accepted" if accepted else "candidate",
                    "confidence": 1.0 if accepted else 0.0,
                    "derivation": "scenario_execution_contract_compiler",
                    "evidence": {"execution_allowed": False},
                }
            )
        if contract_id and interface_id:
            edges.append(
                {
                    "edge_id": stable_id(
                        "edge",
                        "execution_contract_to_interface",
                        contract_id,
                        interface_id,
                    ),
                    "from": contract_id,
                    "to": interface_id,
                    "relation": "execution_contract_to_interface",
                    "status": "accepted" if accepted else "candidate",
                    "confidence": 1.0 if accepted else 0.0,
                    "derivation": "authoritative_scenario_action_entry",
                    "evidence": {"execution_allowed": False},
                }
            )
    return list(
        {
            text(row.get("edge_id")): row
            for row in edges
            if text(row.get("edge_id"))
        }.values()
    )


def project_scenario_execution_contracts(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    contracts, unknowns, gate = build_scenario_execution_contracts(asset, model)
    relationships = _relationships(contracts)
    evidence = dedupe_evidence(
        [
            row
            for contract in contracts
            for row in as_list(contract.get("evidence"))
            if isinstance(row, dict)
        ]
    )
    asset["scenario_execution_contracts"] = contracts
    asset["scenario_execution_contract_unknowns"] = unknowns
    asset["scenario_execution_contract_evidence_index"] = evidence
    asset["scenario_execution_contract_relationships"] = relationships
    asset["scenario_execution_contract_gate"] = gate
    asset["relationships"] = list(
        {
            text(row.get("edge_id")): dict(row)
            for row in [*as_list(asset.get("relationships")), *relationships]
            if isinstance(row, dict) and text(row.get("edge_id"))
        }.values()
    )
    model["scenario_execution_contracts"] = contracts
    model["scenario_execution_contract_unknowns"] = unknowns
    model["scenario_execution_contract_evidence_index"] = evidence
    model["scenario_execution_contract_relationships"] = relationships
    model["scenario_execution_contract_gate"] = gate

    metrics = as_dict(gate.get("metrics"))
    projected = {
        "scenario_execution_contract_status": gate.get("status"),
        "scenario_execution_contract_ready": bool(gate.get("entry_allowed")),
        "scenario_execution_contract_count": int(
            metrics.get("execution_contract_count") or 0
        ),
        "scenario_execution_contract_incomplete_count": int(
            metrics.get("incomplete_execution_contract_count") or 0
        ),
        "scenario_execution_contract_unknown_count": len(unknowns),
        "scenario_execution_contract_relationship_count": len(relationships),
        "scenario_execution_allowed": False,
    }
    summary = as_dict(asset.get("summary"))
    summary.update(projected)
    asset["summary"] = summary
    source_summary = as_dict(model.get("source_summary"))
    source_summary.update(projected)
    model["source_summary"] = source_summary
    model_metrics = as_dict(model.get("metrics"))
    model_metrics.update(projected)
    model["metrics"] = model_metrics

    gap_kinds = {
        "SCENARIO_EXECUTION_CONTRACT_UPSTREAM_BLOCKED",
        "SCENARIO_EXECUTION_CONTRACT_INCOMPLETE",
        "SCENARIO_EXECUTION_CONTRACT_NOT_COMPILED",
    }
    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) not in gap_kinds
    ]
    status = text(gate.get("status"))
    if status != "PASS":
        if status == "BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE":
            kind = "SCENARIO_EXECUTION_CONTRACT_UPSTREAM_BLOCKED"
        elif status == "NO_EXECUTION_CONTRACT_COMPILED":
            kind = "SCENARIO_EXECUTION_CONTRACT_NOT_COMPILED"
        else:
            kind = "SCENARIO_EXECUTION_CONTRACT_INCOMPLETE"
        gaps.append(
            {
                "kind": kind,
                "gap_type": "scenario_execution_contract_not_closed",
                "source_id": "*",
                "scenario_execution_contract_status": status,
                "scenario_execution_contract_metrics": dict(metrics),
                "execution_allowed": False,
                "operator_action": (
                    "resolve canonical action, predicate, outcome-observer or "
                    "source-evidence requirements; never reconstruct semantics "
                    "from legacy fields"
                ),
            }
        )
    asset["coverage_gaps"] = gaps
    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "scenario_execution_contract_v1_enabled": True,
            "scenario_execution_contract_requires_scenario_ir_gate": True,
            "runtime_values_are_requirements_not_invented_payloads": True,
            "request_field_location_inference_allowed": False,
            "credential_auto_selection_allowed": False,
            "concrete_assertion_generation_allowed": False,
            "write_execution_without_cleanup_allowed": False,
            "scenario_execution_contract_does_not_enable_execution": True,
            "execution_contract_consumes_canonical_condition_expression": True,
            "execution_contract_consumes_canonical_operation_clause": True,
            "execution_contract_consumes_canonical_outcome_contracts": True,
            "execution_contract_raw_text_reparse_allowed": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "SCENARIO_EXECUTION_CONTRACT_SCHEMA",
    "SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA",
    "build_scenario_execution_contracts",
    "project_scenario_execution_contracts",
]
