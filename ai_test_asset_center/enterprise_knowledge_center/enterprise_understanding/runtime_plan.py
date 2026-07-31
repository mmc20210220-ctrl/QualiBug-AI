"""Compile canonical outcome-aware Runtime Plan v1 templates.

Unchanged request, credential, cleanup and environment mechanics live in the private
``_runtime_plan_mechanics`` module. This public module is the only orchestration authority:
conditions remain predicate guards, while every mandatory outcome receives one independently
traceable oracle template identified by ``outcome_ref``.
"""
from __future__ import annotations

from typing import Any

from . import _runtime_plan_mechanics as _core
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

RUNTIME_PLAN_SCHEMA = _core.RUNTIME_PLAN_SCHEMA
RUNTIME_PLAN_GATE_SCHEMA = _core.RUNTIME_PLAN_GATE_SCHEMA

_SUPPORTED_OUTCOME_BINDINGS = {
    "DATABASE_FIELD",
    "API_RESPONSE_FIELD",
    "API_RESPONSE_OUTCOME_CHANNEL",
    "RUNTIME_STATE_OBSERVER",
    "UI_STATE_OBSERVER",
}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _observer_candidates(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slot in _dicts(value):
        if text(slot.get("binding_kind")):
            rows.append(dict(slot))
        for binding in _dicts(slot.get("bindings")):
            row = dict(binding)
            row.setdefault("slot_ref", slot.get("slot_ref"))
            row.setdefault("predicate_ref", slot.get("predicate_ref") or slot.get("slot_ref"))
            row.setdefault("outcome_ref", slot.get("outcome_ref"))
            row.setdefault("purpose", slot.get("purpose"))
            rows.append(row)
    return rows


def _condition_oracle_templates(
    contract: dict[str, Any], oracle: dict[str, Any]
) -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for observer in _observer_candidates(oracle.get("condition_observers")):
        if text(observer.get("binding_kind")) != "DATABASE_FIELD":
            continue
        table = text(observer.get("table") or observer.get("table_id"))
        field = text(observer.get("field"))
        predicate_ref = text(observer.get("predicate_ref") or observer.get("slot_ref"))
        if not table or not field:
            continue
        templates.append(
            {
                "template_id": stable_id(
                    "runtime_oracle_template",
                    contract.get("contract_id"),
                    "CONDITION_GUARD",
                    predicate_ref,
                    table,
                    field,
                ),
                "template_kind": "DATABASE_FIELD_SNAPSHOT",
                "semantic_role": "CONDITION_GUARD",
                "predicate_ref": predicate_ref,
                "outcome_ref": None,
                "phase": "BEFORE",
                "table_ref": observer.get("table_id") or table,
                "table": table,
                "field": field,
                "entity_identity_source": "SAME_SCENARIO_ENTITY_IDENTITY",
                "query_template_compiled": True,
                "sql_compiled": False,
                "database_connection_opened": False,
            }
        )
    return templates


def _outcome_template(
    contract: dict[str, Any],
    interface: dict[str, Any],
    requirement: dict[str, Any],
    observer: dict[str, Any],
) -> dict[str, Any] | None:
    outcome_ref = text(requirement.get("outcome_ref"))
    outcome_type = text(requirement.get("outcome_type"))
    binding_kind = text(observer.get("binding_kind"))
    common = {
        "semantic_role": "MANDATORY_OUTCOME",
        "outcome_ref": outcome_ref,
        "outcome_type": outcome_type,
        "assertion_requirement_ref": stable_id(
            "outcome_assertion_requirement",
            contract.get("contract_id"),
            outcome_ref,
        ),
        "concrete_assertion_compiled": False,
    }
    if binding_kind == "DATABASE_FIELD":
        table = text(observer.get("table") or observer.get("table_id"))
        field = text(observer.get("field"))
        if not table or not field:
            return None
        return {
            **common,
            "template_id": stable_id(
                "runtime_oracle_template",
                contract.get("contract_id"),
                outcome_ref,
                binding_kind,
                table,
                field,
            ),
            "template_kind": "DATABASE_FIELD_SNAPSHOT",
            "phase": "BEFORE_AND_AFTER",
            "table_ref": observer.get("table_id") or table,
            "table": table,
            "field": field,
            "entity_identity_source": "SAME_SCENARIO_ENTITY_IDENTITY",
            "query_template_compiled": True,
            "sql_compiled": False,
            "database_connection_opened": False,
        }
    interface_id = (
        observer.get("interface_id")
        or as_dict(contract.get("action_contract")).get("interface_id")
    )
    if binding_kind == "API_RESPONSE_FIELD":
        field = text(observer.get("field") or requirement.get("field_ref"))
        if not interface_id or not field:
            return None
        return {
            **common,
            "template_id": stable_id(
                "runtime_oracle_template",
                contract.get("contract_id"),
                outcome_ref,
                binding_kind,
                interface_id,
                field,
            ),
            "template_kind": "HTTP_RESPONSE_FIELD_CAPTURE",
            "phase": "AFTER",
            "interface_id": interface_id,
            "field": field,
            "declared_response_contracts": _dicts(interface.get("response_contracts")),
            "capture_status": True,
            "capture_body": True,
            "query_template_compiled": True,
            "network_call_compiled": False,
        }
    if binding_kind == "API_RESPONSE_OUTCOME_CHANNEL":
        if not interface_id:
            return None
        return {
            **common,
            "template_id": stable_id(
                "runtime_oracle_template",
                contract.get("contract_id"),
                outcome_ref,
                binding_kind,
                interface_id,
            ),
            "template_kind": "HTTP_RESPONSE_CAPTURE",
            "phase": "AFTER",
            "interface_id": interface_id,
            "declared_response_contracts": _dicts(interface.get("response_contracts")),
            "permission_decision_requirement": requirement.get("expected_decision"),
            "capture_status": True,
            "capture_headers": True,
            "capture_body": True,
            "query_template_compiled": True,
            "network_call_compiled": False,
        }
    if binding_kind == "RUNTIME_STATE_OBSERVER":
        return {
            **common,
            "template_id": stable_id(
                "runtime_oracle_template",
                contract.get("contract_id"),
                outcome_ref,
                binding_kind,
            ),
            "template_kind": "RUNTIME_STATE_CAPTURE",
            "phase": "BEFORE_AND_AFTER",
            "observer_contract": dict(observer),
            "query_template_compiled": True,
            "runtime_probe_executed": False,
        }
    if binding_kind == "UI_STATE_OBSERVER":
        return {
            **common,
            "template_id": stable_id(
                "runtime_oracle_template",
                contract.get("contract_id"),
                outcome_ref,
                binding_kind,
            ),
            "template_kind": "UI_STATE_CAPTURE",
            "phase": "AFTER",
            "observer_contract": dict(observer),
            "query_template_compiled": True,
            "ui_probe_executed": False,
        }
    return None


def _canonical_oracle_templates(
    contract: dict[str, Any], interface: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    oracle = as_dict(contract.get("oracle_plan"))
    requirements = _dicts(oracle.get("outcome_assertion_requirements"))
    if not requirements:
        legacy, unknowns = _core._oracle_templates(contract, interface)
        legacy = dict(legacy)
        legacy.update(
            {
                "canonical_outcome_identity_required": False,
                "mandatory_outcome_refs": [],
                "covered_mandatory_outcome_refs": [],
                "missing_mandatory_outcome_refs": [],
                "mandatory_outcome_coverage_complete": True,
                "legacy_observer_aggregation_authoritative": True,
            }
        )
        return legacy, unknowns

    templates = _condition_oracle_templates(contract, oracle)
    unknowns: list[dict[str, Any]] = []
    required_refs: list[str] = []
    covered_refs: list[str] = []
    for requirement in requirements:
        outcome_ref = text(requirement.get("outcome_ref"))
        if not outcome_ref:
            unknowns.append(
                {
                    "kind": "RUNTIME_PLAN_CANONICAL_OUTCOME_REF_MISSING",
                    "reason_code": "RUNTIME_PLAN_CANONICAL_OUTCOME_REF_MISSING",
                    "contract_ref": contract.get("contract_id"),
                    "blocks_runtime_plan": True,
                }
            )
            continue
        required_refs.append(outcome_ref)
        observer_slots = _dicts(requirement.get("observer_requirements"))
        candidates = [
            row
            for row in _observer_candidates(observer_slots)
            if text(row.get("binding_kind")) in _SUPPORTED_OUTCOME_BINDINGS
        ]
        if len(observer_slots) != 1 or len(candidates) != 1:
            unknowns.append(
                {
                    "kind": "RUNTIME_PLAN_OUTCOME_OBSERVER_UNRESOLVED",
                    "reason_code": "RUNTIME_PLAN_OUTCOME_OBSERVER_UNRESOLVED",
                    "contract_ref": contract.get("contract_id"),
                    "outcome_ref": outcome_ref,
                    "candidate_observer_count": len(candidates),
                    "blocks_runtime_plan": True,
                }
            )
            continue
        template = _outcome_template(contract, interface, requirement, candidates[0])
        if template is None:
            unknowns.append(
                {
                    "kind": "RUNTIME_PLAN_OUTCOME_ORACLE_TEMPLATE_UNRESOLVED",
                    "reason_code": "RUNTIME_PLAN_OUTCOME_ORACLE_TEMPLATE_UNRESOLVED",
                    "contract_ref": contract.get("contract_id"),
                    "outcome_ref": outcome_ref,
                    "binding_kind": candidates[0].get("binding_kind"),
                    "blocks_runtime_plan": True,
                }
            )
            continue
        templates.append(template)
        covered_refs.append(outcome_ref)

    required_refs = unique_text(required_refs)
    covered_refs = unique_text(covered_refs)
    missing_refs = sorted(set(required_refs) - set(covered_refs))
    for outcome_ref in missing_refs:
        if any(text(row.get("outcome_ref")) == outcome_ref for row in unknowns):
            continue
        unknowns.append(
            {
                "kind": "RUNTIME_PLAN_OUTCOME_ORACLE_TEMPLATE_UNRESOLVED",
                "reason_code": "RUNTIME_PLAN_OUTCOME_ORACLE_TEMPLATE_UNRESOLVED",
                "contract_ref": contract.get("contract_id"),
                "outcome_ref": outcome_ref,
                "blocks_runtime_plan": True,
            }
        )
    templates = list(
        {
            text(row.get("template_id")): row
            for row in templates
            if text(row.get("template_id"))
        }.values()
    )
    return (
        {
            "templates": templates,
            "oracle_templates_compiled": bool(templates),
            "canonical_outcome_identity_required": True,
            "mandatory_outcome_refs": required_refs,
            "covered_mandatory_outcome_refs": covered_refs,
            "missing_mandatory_outcome_refs": missing_refs,
            "mandatory_outcome_coverage_complete": bool(required_refs)
            and not missing_refs,
            "condition_guard_template_count": sum(
                1
                for row in templates
                if text(row.get("semantic_role")) == "CONDITION_GUARD"
            ),
            "mandatory_outcome_template_count": sum(
                1
                for row in templates
                if text(row.get("semantic_role")) == "MANDATORY_OUTCOME"
            ),
            "legacy_observer_aggregation_authoritative": False,
            "concrete_assertions_compiled": False,
            "http_status_assertion_compiled": False,
            "response_body_assertion_compiled": False,
            "database_assertion_compiled": False,
        },
        unknowns,
    )


def _snapshot_template(
    contract: dict[str, Any], oracle_template: dict[str, Any]
) -> dict[str, Any]:
    snapshot = dict(_core._snapshot_template(contract, oracle_template))
    outcome_requirements = [
        {
            "outcome_ref": row.get("outcome_ref"),
            "oracle_template_ref": row.get("template_id"),
            "phase": row.get("phase"),
        }
        for row in _dicts(oracle_template.get("templates"))
        if text(row.get("semantic_role")) == "MANDATORY_OUTCOME"
        and text(row.get("outcome_ref"))
    ]
    snapshot.update(
        {
            "outcome_snapshot_requirements": outcome_requirements,
            "mandatory_outcome_refs": unique_text(
                row.get("outcome_ref") for row in outcome_requirements
            ),
            "canonical_outcome_identity_preserved": bool(
                oracle_template.get("canonical_outcome_identity_required")
            ),
        }
    )
    return snapshot


def _compile_runtime_plan(
    contract: dict[str, Any], interface: dict[str, Any], asset: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if text(contract.get("status")) != "REQUIREMENTS_READY":
        return None, []
    action = as_dict(contract.get("action_contract"))
    request, request_unknowns = _core._request_template(contract, interface)
    credentials, credential_unknowns = _core._credential_template(
        contract, interface, asset
    )
    oracle, oracle_unknowns = _canonical_oracle_templates(contract, interface)
    cleanup, cleanup_unknowns = _core._cleanup_template(contract)
    unknowns = [
        *request_unknowns,
        *credential_unknowns,
        *oracle_unknowns,
        *cleanup_unknowns,
    ]
    plan_id = stable_id(
        "runtime_plan", contract.get("contract_id"), action.get("interface_id")
    )
    evidence = dedupe_evidence(as_list(contract.get("evidence")))
    if not evidence:
        unknowns.append(
            {
                "kind": "RUNTIME_PLAN_SOURCE_EVIDENCE_MISSING",
                "reason_code": "RUNTIME_PLAN_SOURCE_EVIDENCE_MISSING",
                "contract_ref": contract.get("contract_id"),
                "blocks_runtime_plan": True,
            }
        )
    critical = [row for row in unknowns if bool(row.get("blocks_runtime_plan"))]
    plan = {
        "schema": RUNTIME_PLAN_SCHEMA,
        "plan_id": plan_id,
        "execution_contract_ref": contract.get("contract_id"),
        "scenario_ref": contract.get("scenario_ref"),
        "behavior_ref": contract.get("behavior_ref"),
        "implementation_binding_ref": contract.get("implementation_binding_ref"),
        "scenario_type": contract.get("scenario_type"),
        "action_entry": {
            "interface_id": action.get("interface_id"),
            "method": action.get("method"),
            "path": action.get("path"),
            "operation_id": action.get("operation_id"),
            "authoritative": bool(action.get("authoritative")),
        },
        "request_template": request,
        "credential_template": credentials,
        "test_data_setup_templates": _dicts(contract.get("test_data_requirements")),
        "oracle_query_templates": oracle,
        "snapshot_template": _snapshot_template(contract, oracle),
        "cleanup_step_templates": cleanup,
        "environment_template": _core._environment_template(asset, contract),
        "evidence": evidence,
        "canonical_outcome_identity_required": bool(
            oracle.get("canonical_outcome_identity_required")
        ),
        "mandatory_outcome_refs": as_list(oracle.get("mandatory_outcome_refs")),
        "unresolved_runtime_plan_semantics": unique_text(
            row.get("reason_code") for row in unknowns
        ),
        "status": "INCOMPLETE" if critical else "TEMPLATE_READY",
        "formal_runtime_plan": not bool(critical),
        "execution_allowed": False,
        "network_calls_allowed": False,
        "request_values_materialized": False,
        "http_request_compiled": False,
        "credentials_loaded": False,
        "test_data_materialized": False,
        "database_queries_executable": False,
        "oracle_assertions_compiled": False,
        "snapshots_materialized": False,
        "cleanup_actions_executable": False,
        "runtime_environment_validated": False,
    }
    normalized: list[dict[str, Any]] = []
    for row in unknowns:
        item = dict(row)
        item["unknown_id"] = stable_id(
            "runtime_plan_unknown",
            plan_id,
            item.get("reason_code"),
            item.get("field"),
            item.get("actor_ref"),
            item.get("outcome_ref"),
        )
        item["runtime_plan_ref"] = plan_id
        item["execution_allowed"] = False
        normalized.append(item)
    return plan, normalized


def build_runtime_plans_v1(
    asset: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    upstream = as_dict(asset.get("scenario_execution_contract_gate"))
    contracts = _dicts(
        asset.get("scenario_execution_contracts")
        or model.get("scenario_execution_contracts")
    )
    if not bool(upstream.get("entry_allowed")):
        return [], [], {
            "schema": RUNTIME_PLAN_GATE_SCHEMA,
            "status": "BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE",
            "entry_allowed": False,
            "runtime_plan_ready": False,
            "execution_allowed": False,
            "upstream_execution_contract_status": text(upstream.get("status"))
            or "NOT_BUILT",
            "metrics": {
                "runtime_plan_count": 0,
                "ready_runtime_plan_count": 0,
                "incomplete_runtime_plan_count": 0,
            },
            "quality_claim": "RUNTIME_PLAN_NOT_BUILT_WHEN_EXECUTION_CONTRACT_GATE_CLOSED",
        }
    interfaces = _core._interface_index(asset)
    plans: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    ready_contracts = [
        row for row in contracts if text(row.get("status")) == "REQUIREMENTS_READY"
    ]
    for contract in ready_contracts:
        interface_id = text(as_dict(contract.get("action_contract")).get("interface_id"))
        interface = interfaces.get(interface_id) or {
            **as_dict(contract.get("action_contract")),
            "interface_id": interface_id,
        }
        plan, rows = _compile_runtime_plan(contract, interface, asset)
        unknowns.extend(rows)
        if plan is not None:
            plans.append(plan)
    plans = list(
        {
            text(row.get("plan_id")): row
            for row in plans
            if text(row.get("plan_id"))
        }.values()
    )
    unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if text(row.get("unknown_id"))
        }.values()
    )
    ready = sum(1 for row in plans if text(row.get("status")) == "TEMPLATE_READY")
    incomplete = sum(1 for row in plans if text(row.get("status")) == "INCOMPLETE")
    covered = {text(row.get("execution_contract_ref")) for row in plans}
    status = (
        "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
        if incomplete or len(covered) < len(ready_contracts)
        else "PASS"
        if plans
        else "NO_RUNTIME_PLAN_COMPILED"
    )
    required_outcomes = sum(
        len(as_list(as_dict(row.get("oracle_query_templates")).get("mandatory_outcome_refs")))
        for row in plans
    )
    covered_outcomes = sum(
        len(
            as_list(
                as_dict(row.get("oracle_query_templates")).get(
                    "covered_mandatory_outcome_refs"
                )
            )
        )
        for row in plans
    )
    gate = {
        "schema": RUNTIME_PLAN_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "runtime_plan_ready": status == "PASS",
        "execution_allowed": False,
        "upstream_execution_contract_status": upstream.get("status"),
        "canonical_outcome_identity_required": any(
            bool(row.get("canonical_outcome_identity_required")) for row in plans
        ),
        "metrics": {
            "runtime_plan_count": len(plans),
            "ready_runtime_plan_count": ready,
            "incomplete_runtime_plan_count": incomplete,
            "covered_execution_contract_count": len(covered),
            "ready_execution_contract_count": len(ready_contracts),
            "mandatory_outcome_requirement_count": required_outcomes,
            "covered_mandatory_outcome_count": covered_outcomes,
            "runtime_plan_unknown_count": len(unknowns),
        },
        "network_calls_allowed": False,
        "credentials_loaded": False,
        "request_values_materialized": False,
        "http_requests_compiled": False,
        "database_queries_executable": False,
        "oracle_assertions_compiled": False,
        "cleanup_actions_executable": False,
        "runtime_environment_validated": False,
        "quality_claim": "RUNTIME_TEMPLATE_CLOSURE_NOT_EXECUTION_READINESS_OR_BUG_FINDING",
    }
    return plans, unknowns, gate


def _relationships(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plans:
        plan_id = text(plan.get("plan_id"))
        contract_id = text(plan.get("execution_contract_ref"))
        interface_id = text(as_dict(plan.get("action_entry")).get("interface_id"))
        accepted = text(plan.get("status")) == "TEMPLATE_READY"
        for relation, source, target, derivation in (
            (
                "execution_contract_to_runtime_plan",
                contract_id,
                plan_id,
                "runtime_plan_compiler",
            ),
            (
                "runtime_plan_to_interface",
                plan_id,
                interface_id,
                "authoritative_execution_contract_action",
            ),
        ):
            if source and target:
                rows.append(
                    {
                        "edge_id": stable_id("edge", relation, source, target),
                        "from": source,
                        "to": target,
                        "relation": relation,
                        "status": "accepted" if accepted else "candidate",
                        "confidence": 1.0 if accepted else 0.0,
                        "derivation": derivation,
                        "evidence": {"execution_allowed": False},
                    }
                )
        for outcome_ref in as_list(
            as_dict(plan.get("oracle_query_templates")).get(
                "covered_mandatory_outcome_refs"
            )
        ):
            if plan_id and text(outcome_ref):
                rows.append(
                    {
                        "edge_id": stable_id(
                            "edge",
                            "runtime_plan_to_mandatory_outcome",
                            plan_id,
                            outcome_ref,
                        ),
                        "from": plan_id,
                        "to": outcome_ref,
                        "relation": "runtime_plan_to_mandatory_outcome",
                        "status": "accepted" if accepted else "candidate",
                        "confidence": 1.0 if accepted else 0.0,
                        "derivation": "canonical_outcome_oracle_compiler",
                        "evidence": {"execution_allowed": False},
                    }
                )
    return list(
        {
            text(row.get("edge_id")): row
            for row in rows
            if text(row.get("edge_id"))
        }.values()
    )


def project_runtime_plans_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    plans, unknowns, gate = build_runtime_plans_v1(asset, model)
    relationships = _relationships(plans)
    evidence = dedupe_evidence(
        [
            row
            for plan in plans
            for row in as_list(plan.get("evidence"))
            if isinstance(row, dict)
        ]
    )
    for container in (asset, model):
        container["runtime_plans"] = plans
        container["runtime_plan_unknowns"] = unknowns
        container["runtime_plan_evidence_index"] = evidence
        container["runtime_plan_relationships"] = relationships
        container["runtime_plan_gate"] = gate
    asset["relationships"] = list(
        {
            text(row.get("edge_id")): dict(row)
            for row in [*as_list(asset.get("relationships")), *relationships]
            if isinstance(row, dict) and text(row.get("edge_id"))
        }.values()
    )
    metrics = as_dict(gate.get("metrics"))
    projected = {
        "runtime_plan_status": gate.get("status"),
        "runtime_plan_ready": bool(gate.get("entry_allowed")),
        "runtime_plan_count": int(metrics.get("runtime_plan_count") or 0),
        "runtime_plan_incomplete_count": int(
            metrics.get("incomplete_runtime_plan_count") or 0
        ),
        "runtime_plan_unknown_count": len(unknowns),
        "runtime_plan_relationship_count": len(relationships),
        "runtime_plan_mandatory_outcome_requirement_count": int(
            metrics.get("mandatory_outcome_requirement_count") or 0
        ),
        "runtime_plan_covered_mandatory_outcome_count": int(
            metrics.get("covered_mandatory_outcome_count") or 0
        ),
        "runtime_execution_allowed": False,
    }
    summary = dict(as_dict(asset.get("summary")))
    summary.update(projected)
    asset["summary"] = summary
    source_summary = dict(as_dict(model.get("source_summary")))
    source_summary.update(projected)
    model["source_summary"] = source_summary
    model_metrics = dict(as_dict(model.get("metrics")))
    model_metrics.update(projected)
    model["metrics"] = model_metrics

    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and text(row.get("kind"))
        not in {
            "RUNTIME_PLAN_UPSTREAM_BLOCKED",
            "RUNTIME_PLAN_INCOMPLETE",
            "RUNTIME_PLAN_NOT_COMPILED",
        }
    ]
    if text(gate.get("status")) != "PASS":
        status = text(gate.get("status"))
        kind = (
            "RUNTIME_PLAN_UPSTREAM_BLOCKED"
            if status == "BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE"
            else "RUNTIME_PLAN_NOT_COMPILED"
            if status == "NO_RUNTIME_PLAN_COMPILED"
            else "RUNTIME_PLAN_INCOMPLETE"
        )
        gaps.append(
            {
                "kind": kind,
                "gap_type": "runtime_plan_template_not_closed",
                "source_id": "*",
                "runtime_plan_status": status,
                "runtime_plan_metrics": dict(metrics),
                "execution_allowed": False,
                "operator_action": (
                    "resolve one source-backed oracle observer and template for every mandatory "
                    "outcome; do not let condition or legacy response observers cover it implicitly"
                ),
            }
        )
    asset["coverage_gaps"] = gaps
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_plan_v1_enabled": True,
            "runtime_plan_requires_execution_contract_gate": True,
            "runtime_plan_request_locations_require_source_contract": True,
            "runtime_plan_uses_credential_refs_only": True,
            "runtime_plan_plaintext_credentials_allowed": False,
            "runtime_plan_values_are_not_materialized": True,
            "runtime_plan_oracles_are_templates_not_assertions": True,
            "runtime_plan_cleanup_steps_are_templates_not_actions": True,
            "runtime_plan_does_not_enable_execution": True,
            "runtime_plan_requires_canonical_outcome_assertion_requirements": True,
            "runtime_plan_each_mandatory_outcome_has_explicit_outcome_ref": True,
            "runtime_plan_condition_and_outcome_observers_are_separate": True,
            "runtime_plan_legacy_observer_aggregation_authoritative": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "RUNTIME_PLAN_SCHEMA",
    "RUNTIME_PLAN_GATE_SCHEMA",
    "build_runtime_plans_v1",
    "project_runtime_plans_to_asset",
]
