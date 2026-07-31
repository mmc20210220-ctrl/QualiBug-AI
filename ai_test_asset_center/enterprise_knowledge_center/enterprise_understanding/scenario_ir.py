"""Compile governed Business Behavior IR bindings into non-executable Scenario IR v1.

This layer produces source-backed test-design units only. It never selects credentials, creates
request payloads, compiles concrete assertions, resolves UI locators, opens database connections,
or executes against an enterprise system.
"""
from __future__ import annotations

from typing import Any, Iterable

from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

SCENARIO_IR_SCHEMA = "qualibug.enterprise-test-scenario-ir.v1"
SCENARIO_IR_GATE_SCHEMA = "qualibug.enterprise-test-scenario-ir-gate.v1"

_NUMERIC_OPERATORS = {
    "EQUALS",
    "GREATER_THAN",
    "GREATER_THAN_OR_EQUAL",
    "LESS_THAN",
    "LESS_THAN_OR_EQUAL",
}
_INCLUSIVE_BOUNDARY_OPERATORS = {
    "EQUALS",
    "GREATER_THAN_OR_EQUAL",
    "LESS_THAN_OR_EQUAL",
}
_BUSINESS_REJECTION_MODALITIES = {
    "MUST_NOT",
    "FORBIDDEN",
    "PROHIBITED",
    "DENY",
}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _authoritative_api(binding: dict[str, Any]) -> dict[str, Any]:
    primary = text(binding.get("primary_api_interface_ref"))
    rows = [
        row
        for row in _dicts(binding.get("api_operation_bindings"))
        if bool(row.get("authoritative")) and text(row.get("status")) == "BOUND"
    ]
    if primary:
        selected = [row for row in rows if text(row.get("interface_id")) == primary]
        if len(selected) == 1:
            return selected[0]
    return rows[0] if len(rows) == 1 else {}


def _scenario_type(behavior: dict[str, Any]) -> tuple[str, list[str]]:
    decision = text(behavior.get("permission_decision"))
    modality = text(behavior.get("business_modality")).upper()
    authorization_explicit = bool(behavior.get("authorization_semantics_explicit"))
    actors = unique_text(as_list(behavior.get("actor_refs")))
    dimensions: list[str] = []
    if _dicts(behavior.get("state_effects")):
        dimensions.append("STATE_TRANSITION")

    # A denied authorization coordinate is different from a prohibited business
    # outcome. Only the former may become an unauthorized / actor-comparison design.
    if decision == "DENY" and authorization_explicit:
        if actors:
            dimensions.extend(["REJECTION", "AUTHORIZATION"])
            return "UNAUTHORIZED", unique_text(dimensions)
        dimensions.append("REJECTION")
        return "REJECTION", unique_text(dimensions)
    if modality in _BUSINESS_REJECTION_MODALITIES or decision == "DENY":
        dimensions.append("REJECTION")
        return "REJECTION", unique_text(dimensions)
    if decision == "REQUIRE_APPROVAL":
        dimensions.append("APPROVAL_REQUIRED")
    elif decision == "REQUIRE_CONFIRMATION":
        dimensions.append("CONFIRMATION_REQUIRED")
    else:
        dimensions.append("POSITIVE")
    return "POSITIVE", unique_text(dimensions)


def _expected_outcome(behavior: dict[str, Any]) -> dict[str, Any]:
    return {
        "permission_decision": text(behavior.get("permission_decision")) or "UNSPECIFIED",
        "business_modality": text(behavior.get("business_modality")) or "ASSERTS",
        "authorization_semantics_explicit": bool(
            behavior.get("authorization_semantics_explicit")
        ),
        "expected_effects": unique_text(as_list(behavior.get("expected_effects"))),
        "state_effects": _dicts(behavior.get("state_effects")),
        "data_effects": _dicts(behavior.get("data_effects")),
        "oracle_level": "SEMANTIC_EXPECTATION_ONLY",
        "concrete_assertion_compiled": False,
    }


def _observer_plan(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "condition_observers": _dicts(binding.get("condition_observer_bindings")),
        "effect_observers": _dicts(binding.get("effect_observer_bindings")),
        "response_observers": _dicts(binding.get("response_observer_bindings")),
        "runtime_query_compiled": False,
        "before_snapshot_compiled": False,
        "after_snapshot_compiled": False,
    }


def _base_scenario(
    behavior: dict[str, Any], binding: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    unknowns: list[dict[str, Any]] = []
    behavior_id = text(behavior.get("behavior_id"))
    binding_id = text(binding.get("binding_id"))
    api = _authoritative_api(binding)
    if not behavior_id or not binding_id or not api:
        unknowns.append(
            {
                "kind": "SCENARIO_ACTION_ENTRY_UNRESOLVED",
                "reason_code": "SCENARIO_ACTION_ENTRY_UNRESOLVED",
                "behavior_ref": behavior_id,
                "binding_ref": binding_id,
                "blocks_scenario_ir": True,
            }
        )
        return None, unknowns
    if text(behavior.get("status")) != "CONFIRMED" or not bool(
        binding.get("scenario_planning_ready")
    ):
        unknowns.append(
            {
                "kind": "SCENARIO_SOURCE_BEHAVIOR_NOT_READY",
                "reason_code": "SCENARIO_SOURCE_BEHAVIOR_NOT_READY",
                "behavior_ref": behavior_id,
                "binding_ref": binding_id,
                "behavior_status": behavior.get("status"),
                "binding_status": binding.get("status"),
                "blocks_scenario_ir": True,
            }
        )
        return None, unknowns

    scenario_type, dimensions = _scenario_type(behavior)
    decision = text(behavior.get("permission_decision")) or "UNSPECIFIED"
    unresolved: list[str] = []
    if decision in {"", "UNSPECIFIED", "CONFLICTED"} and not (
        as_list(behavior.get("expected_effects"))
        or as_list(behavior.get("state_effects"))
        or as_list(behavior.get("data_effects"))
        or text(behavior.get("business_modality")).upper()
        in _BUSINESS_REJECTION_MODALITIES
    ):
        unresolved.append("SCENARIO_EXPECTED_OUTCOME_UNRESOLVED")

    scenario_id = stable_id(
        "scenario_ir", behavior_id, binding_id, scenario_type, "base"
    )
    scenario = {
        "schema": SCENARIO_IR_SCHEMA,
        "scenario_id": scenario_id,
        "scenario_family_id": stable_id(
            "scenario_family", behavior.get("behavior_family_id")
        ),
        "scenario_type": scenario_type,
        "coverage_dimensions": dimensions,
        "title": f"{text(behavior.get('operation_ref')) or '业务动作'}—{scenario_type}场景",
        "behavior_ref": behavior_id,
        "implementation_binding_ref": binding_id,
        "actor_refs": unique_text(as_list(behavior.get("actor_refs"))),
        "object_refs": unique_text(as_list(behavior.get("object_refs"))),
        "operation_ref": behavior.get("operation_ref"),
        "trigger": dict(as_dict(behavior.get("trigger"))),
        "preconditions": _dicts(behavior.get("preconditions")),
        "condition_combinator": behavior.get("condition_combinator"),
        "action_entry": {
            "interface_id": api.get("interface_id"),
            "method": api.get("method"),
            "path": api.get("path"),
            "operation_id": api.get("operation_id"),
            "derivation": api.get("derivation"),
            "authoritative": True,
        },
        "ui_design_candidates": _dicts(binding.get("ui_action_bindings")),
        "observer_plan": _observer_plan(binding),
        "expected_outcome": _expected_outcome(behavior),
        "exceptions": unique_text(as_list(behavior.get("exceptions"))),
        "compensations": unique_text(as_list(behavior.get("compensations"))),
        "evidence": dedupe_evidence(
            [*as_list(behavior.get("evidence")), *as_list(binding.get("evidence"))]
        ),
        "unresolved_semantics": unresolved,
        "status": "INCOMPLETE" if unresolved else "PLANNABLE",
        "formal_scenario_ir": not bool(unresolved),
        "candidate_only": bool(unresolved),
        "execution_ready": False,
        "request_payload_compiled": False,
        "credentials_selected": False,
        "test_data_compiled": False,
        "ui_locator_compiled": False,
        "database_query_compiled": False,
        "expected_assertion_compiled": False,
        "cleanup_plan_compiled": False,
        "runtime_environment_validated": False,
    }
    for reason in unresolved:
        unknowns.append(
            {
                "kind": reason,
                "reason_code": reason,
                "scenario_ref": scenario_id,
                "behavior_ref": behavior_id,
                "blocks_scenario_ir": True,
            }
        )
    return scenario, unknowns


def _boundary_scenarios(
    base: dict[str, Any], behavior: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scenarios: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for slot in _dicts(behavior.get("preconditions")):
        operator = text(slot.get("operator_candidate"))
        value = as_dict(slot.get("value_candidate"))
        if operator not in _NUMERIC_OPERATORS or text(value.get("value_type")) != "NUMBER":
            continue
        threshold = value.get("normalized_value")
        scenario_id = stable_id(
            "scenario_ir",
            base.get("behavior_ref"),
            base.get("implementation_binding_ref"),
            "BOUNDARY",
            slot.get("slot_id"),
            threshold,
        )
        expected = dict(as_dict(base.get("expected_outcome")))
        unresolved: list[str] = []
        if operator not in _INCLUSIVE_BOUNDARY_OPERATORS:
            expected["permission_decision"] = "UNRESOLVED"
            expected["concrete_assertion_compiled"] = False
            unresolved.append("BOUNDARY_COMPLEMENT_OUTCOME_UNRESOLVED")
        boundary = {
            **base,
            "scenario_id": scenario_id,
            "scenario_type": "BOUNDARY",
            "coverage_dimensions": unique_text(
                [*as_list(base.get("coverage_dimensions")), "BOUNDARY"]
            ),
            "title": f"{text(base.get('operation_ref')) or '业务动作'}—边界值场景",
            "boundary": {
                "slot_ref": slot.get("slot_id"),
                "field_candidate": slot.get("field_candidate"),
                "operator": operator,
                "relation": "AT_THRESHOLD",
                "threshold": threshold,
                "unit": value.get("unit"),
                "source_value": value.get("raw"),
                "adjacent_value_generation_allowed": False,
            },
            "expected_outcome": expected,
            "unresolved_semantics": unresolved,
            "status": "INCOMPLETE" if unresolved else "PLANNABLE",
            "formal_scenario_ir": not bool(unresolved),
            "candidate_only": bool(unresolved),
        }
        scenarios.append(boundary)
        for reason in unresolved:
            unknowns.append(
                {
                    "kind": reason,
                    "reason_code": reason,
                    "scenario_ref": scenario_id,
                    "behavior_ref": behavior.get("behavior_id"),
                    "slot_ref": slot.get("slot_id"),
                    "blocks_scenario_ir": False,
                }
            )
    return scenarios, unknowns


def _state_transition_scenario(
    base: dict[str, Any], behavior: dict[str, Any]
) -> dict[str, Any] | None:
    state_effects = _dicts(behavior.get("state_effects"))
    if not state_effects:
        return None
    return {
        **base,
        "scenario_id": stable_id(
            "scenario_ir",
            base.get("behavior_ref"),
            base.get("implementation_binding_ref"),
            "STATE_TRANSITION",
            state_effects,
        ),
        "scenario_type": "STATE_TRANSITION",
        "coverage_dimensions": unique_text(
            [*as_list(base.get("coverage_dimensions")), "STATE_TRANSITION"]
        ),
        "title": f"{text(base.get('operation_ref')) or '业务动作'}—状态转换场景",
        "state_transition_expectations": state_effects,
    }


def _dedupe_unknowns(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        unknown_id = stable_id(
            "scenario_ir_unknown",
            row.get("kind"),
            row.get("scenario_ref"),
            row.get("behavior_ref"),
            row.get("slot_ref"),
        )
        row["unknown_id"] = unknown_id
        result[unknown_id] = row
    return sorted(result.values(), key=lambda row: text(row.get("unknown_id")))


def build_scenario_ir_v1(
    asset: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Compile Scenario IR only after the final semantic + implementation gate passes."""
    upstream = as_dict(asset.get("scenario_planning_gate"))
    behaviors = {
        text(row.get("behavior_id")): row
        for row in _dicts(model.get("business_behaviors"))
        if text(row.get("behavior_id"))
    }
    bindings = {
        text(row.get("behavior_ref")): row
        for row in _dicts(model.get("behavior_implementation_bindings"))
        if text(row.get("behavior_ref"))
    }
    scenarios: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []

    if not bool(upstream.get("scenario_planning_allowed")):
        return [], [], {
            "schema": SCENARIO_IR_GATE_SCHEMA,
            "status": "BLOCKED_SCENARIO_IR_UPSTREAM_GATE",
            "entry_allowed": False,
            "scenario_ir_ready": False,
            "execution_allowed": False,
            "upstream_scenario_planning_status": text(upstream.get("status")) or "NOT_BUILT",
            "metrics": {
                "scenario_count": 0,
                "plannable_scenario_count": 0,
                "incomplete_scenario_count": 0,
            },
            "quality_claim": "SCENARIO_IR_NOT_BUILT_WHEN_UPSTREAM_GATE_CLOSED",
        }

    for behavior_id, behavior in behaviors.items():
        binding = bindings.get(behavior_id) or {}
        if not bool(binding.get("scenario_planning_ready")):
            continue
        base, base_unknowns = _base_scenario(behavior, binding)
        unknowns.extend(base_unknowns)
        if base is None:
            continue
        scenarios.append(base)
        boundary_rows, boundary_unknowns = _boundary_scenarios(base, behavior)
        scenarios.extend(boundary_rows)
        unknowns.extend(boundary_unknowns)
        transition = _state_transition_scenario(base, behavior)
        if transition is not None:
            scenarios.append(transition)

    scenarios = list(
        {
            text(row.get("scenario_id")): row
            for row in scenarios
            if text(row.get("scenario_id"))
        }.values()
    )
    unknown_rows = _dedupe_unknowns(unknowns)
    plannable = sum(1 for row in scenarios if text(row.get("status")) == "PLANNABLE")
    incomplete = sum(1 for row in scenarios if text(row.get("status")) == "INCOMPLETE")
    ready_bindings = sum(
        1 for row in bindings.values() if bool(row.get("scenario_planning_ready"))
    )
    covered_behaviors = {
        text(row.get("behavior_ref"))
        for row in scenarios
        if text(row.get("status")) == "PLANNABLE"
    }
    critical_unknowns = [row for row in unknown_rows if bool(row.get("blocks_scenario_ir"))]
    if critical_unknowns or len(covered_behaviors) < ready_bindings:
        status = "BLOCKED_SCENARIO_IR_INCOMPLETE"
    elif scenarios:
        status = "PASS"
    else:
        status = "NO_SCENARIO_IR_COMPILED"
    gate = {
        "schema": SCENARIO_IR_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "scenario_ir_ready": status == "PASS",
        "execution_allowed": False,
        "upstream_scenario_planning_status": upstream.get("status"),
        "metrics": {
            "scenario_count": len(scenarios),
            "plannable_scenario_count": plannable,
            "incomplete_scenario_count": incomplete,
            "positive_scenario_count": sum(1 for row in scenarios if text(row.get("scenario_type")) == "POSITIVE"),
            "rejection_scenario_count": sum(1 for row in scenarios if text(row.get("scenario_type")) == "REJECTION"),
            "unauthorized_scenario_count": sum(1 for row in scenarios if text(row.get("scenario_type")) == "UNAUTHORIZED"),
            "boundary_scenario_count": sum(1 for row in scenarios if text(row.get("scenario_type")) == "BOUNDARY"),
            "state_transition_scenario_count": sum(1 for row in scenarios if text(row.get("scenario_type")) == "STATE_TRANSITION"),
            "ready_behavior_binding_count": ready_bindings,
            "covered_behavior_count": len(covered_behaviors),
            "scenario_ir_unknown_count": len(unknown_rows),
            "critical_scenario_ir_unknown_count": len(critical_unknowns),
        },
        "request_payload_compiled": False,
        "expected_assertion_compiled": False,
        "credentials_selected": False,
        "test_data_compiled": False,
        "runtime_environment_validated": False,
        "quality_claim": "SCENARIO_IR_DESIGN_CLOSURE_NOT_EXECUTABILITY_OR_BUG_FINDING",
    }
    return scenarios, unknown_rows, gate


def project_scenario_ir_to_asset(asset: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    scenarios, unknowns, gate = build_scenario_ir_v1(asset, model)
    evidence = dedupe_evidence(
        [row for scenario in scenarios for row in as_list(scenario.get("evidence")) if isinstance(row, dict)]
    )
    asset["scenario_ir"] = scenarios
    asset["scenario_ir_unknowns"] = unknowns
    asset["scenario_ir_evidence_index"] = evidence
    asset["scenario_ir_gate"] = gate
    model["scenario_ir"] = scenarios
    model["scenario_ir_unknowns"] = unknowns
    model["scenario_ir_evidence_index"] = evidence
    model["scenario_ir_gate"] = gate

    metrics = as_dict(gate.get("metrics"))
    summary = as_dict(asset.get("summary"))
    summary.update(
        {
            "scenario_ir_status": gate.get("status"),
            "scenario_ir_ready": bool(gate.get("entry_allowed")),
            "scenario_ir_count": int(metrics.get("scenario_count") or 0),
            "scenario_ir_plannable_count": int(metrics.get("plannable_scenario_count") or 0),
            "scenario_ir_incomplete_count": int(metrics.get("incomplete_scenario_count") or 0),
            "scenario_ir_boundary_count": int(metrics.get("boundary_scenario_count") or 0),
            "scenario_ir_state_transition_count": int(metrics.get("state_transition_scenario_count") or 0),
            "scenario_execution_allowed": False,
        }
    )
    asset["summary"] = summary

    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "scenario_ir_v1_enabled": True,
            "scenario_ir_requires_final_scenario_planning_gate": True,
            "scenario_ir_is_non_executable": True,
            "scenario_ir_does_not_select_credentials": True,
            "scenario_ir_does_not_compile_request_payloads": True,
            "scenario_ir_does_not_compile_concrete_assertions": True,
            "strict_boundary_complement_requires_source_evidence": True,
            "unauthorized_scenario_requires_explicit_denied_actor": True,
            "business_rejection_is_not_authorization_denial": True,
            "business_rejection_preserved_after_authorization_separation": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "SCENARIO_IR_SCHEMA",
    "SCENARIO_IR_GATE_SCHEMA",
    "build_scenario_ir_v1",
    "project_scenario_ir_to_asset",
]
