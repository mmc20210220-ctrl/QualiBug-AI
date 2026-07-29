"""Govern Business Behavior IR implementation bindings before scenario planning.

The base binder preserves every source-backed and diagnostic match.  This closure recomputes
whether those observations are sufficient for scenario planning.  It never changes Business
Behavior IR semantics and never compiles executable requests or assertions.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .implementation_binding import (
    IMPLEMENTATION_BINDING_GATE_SCHEMA,
    _authoritative_interface_ids,
    build_behavior_implementation_bindings,
)
from .schema import as_dict, as_list, stable_id, text

_ACTION_UNKNOWN_KINDS = {
    "BEHAVIOR_API_BINDING_UNRESOLVED",
    "BEHAVIOR_API_BINDING_AMBIGUOUS",
    "BEHAVIOR_API_BINDING_MULTIPLE_AUTHORITATIVE",
    "BEHAVIOR_AUTHORITATIVE_INTERFACE_MISSING",
}
_FIELD_UNKNOWN_KINDS = {
    "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED",
    "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [row for row in as_list(value) if isinstance(row, dict)]


def _unknown_id(row: dict[str, Any]) -> str:
    return stable_id(
        "implementation_binding_unknown",
        row.get("kind"),
        row.get("behavior_ref"),
        row.get("slot_ref"),
        row.get("field_candidate"),
        row.get("interface_refs"),
    )


def _dedupe_unknowns(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.setdefault("unknown_id", _unknown_id(row))
        result[text(row.get("unknown_id")) or _unknown_id(row)] = row
    return sorted(result.values(), key=lambda row: text(row.get("unknown_id")))


def _authoritative_api_rows(binding: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _dicts(binding.get("api_operation_bindings"))
        if bool(row.get("authoritative")) and text(row.get("status")) == "BOUND"
    ]


def _govern_observer_slot(slot: dict[str, Any]) -> dict[str, Any]:
    result = dict(slot)
    candidates = _dicts(result.get("bindings"))
    db_candidates = [
        row
        for row in candidates
        if text(row.get("binding_kind")) == "DATABASE_FIELD"
        and bool(row.get("authoritative"))
    ]
    observable_candidates = [
        row
        for row in candidates
        if text(row.get("binding_kind"))
        in {
            "DATABASE_FIELD",
            "API_RESPONSE_FIELD",
            "RUNTIME_STATE_OBSERVER",
            "UI_STATE_OBSERVER",
        }
        and bool(row.get("authoritative"))
    ]
    db_tables = {
        text(row.get("table_id")) for row in db_candidates if text(row.get("table_id"))
    }
    api_contract_candidates = [
        row
        for row in candidates
        if text(row.get("binding_kind")) == "API_CONTRACT_FIELD"
    ]

    if len(db_tables) > 1:
        status = "AMBIGUOUS"
        reason = "IMPLEMENTATION_FIELD_MULTIPLE_DATABASE_TABLES"
    elif observable_candidates:
        status = "BOUND"
        reason = ""
    elif api_contract_candidates:
        status = "CONTRACT_FIELD_ONLY"
        reason = "API_CONTRACT_FIELD_IS_NOT_RUNTIME_OBSERVER"
    else:
        status = "UNBOUND"
        reason = text(result.get("reason_code")) or "IMPLEMENTATION_OBSERVER_UNRESOLVED"

    result["status"] = status
    result["runtime_observer_available"] = status == "BOUND"
    result["request_contract_field_available"] = bool(api_contract_candidates)
    result["api_request_field_is_runtime_observer"] = False
    if reason:
        result["reason_code"] = reason
    else:
        result.pop("reason_code", None)
    return result


def _response_channel_ready(binding: dict[str, Any]) -> bool:
    return any(
        bool(row.get("authoritative"))
        and text(row.get("status")) in {"BOUND_CHANNEL_ONLY", "BOUND"}
        for row in _dicts(binding.get("response_observer_bindings"))
    )


def _behavior_semantic_ready(behavior: dict[str, Any]) -> bool:
    if text(behavior.get("status")) != "CONFIRMED":
        return False
    conditions = _dicts(behavior.get("preconditions"))
    if len(conditions) > 1 and text(behavior.get("condition_combinator")) in {
        "",
        "UNRESOLVED",
    }:
        return False
    return True


def build_governed_behavior_implementation_bindings(
    asset: dict[str, Any], behaviors: Iterable[dict[str, Any]]
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Return fail-closed implementation bindings suitable for scenario planning."""
    behavior_rows = [dict(row) for row in behaviors if isinstance(row, dict)]
    behavior_by_id = {
        text(row.get("behavior_id")): row
        for row in behavior_rows
        if text(row.get("behavior_id"))
    }
    bindings, base_unknowns, conflicts, _base_gate = build_behavior_implementation_bindings(
        asset, behavior_rows
    )
    interface_ids = {
        text(row.get("interface_id"))
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    }
    unknowns = [
        dict(row)
        for row in base_unknowns
        if isinstance(row, dict)
        and text(row.get("kind")) not in _ACTION_UNKNOWN_KINDS | _FIELD_UNKNOWN_KINDS
    ]

    governed: list[dict[str, Any]] = []
    for raw_binding in bindings:
        binding = dict(raw_binding)
        behavior_id = text(binding.get("behavior_ref"))
        behavior = behavior_by_id.get(behavior_id) or {}
        expected_authoritative = _authoritative_interface_ids(asset, behavior)
        missing_authoritative = sorted(expected_authoritative - interface_ids)
        api_rows = _authoritative_api_rows(binding)
        api_ids = sorted(
            {
                text(row.get("interface_id"))
                for row in api_rows
                if text(row.get("interface_id"))
            }
        )

        if missing_authoritative:
            unknowns.append(
                {
                    "kind": "BEHAVIOR_AUTHORITATIVE_INTERFACE_MISSING",
                    "reason_code": "BEHAVIOR_AUTHORITATIVE_INTERFACE_MISSING",
                    "behavior_ref": behavior_id,
                    "interface_refs": missing_authoritative,
                    "blocks_scenario_planning": True,
                    "automatic_resolution_allowed": False,
                }
            )
        if len(api_ids) > 1:
            unknowns.append(
                {
                    "kind": "BEHAVIOR_API_BINDING_MULTIPLE_AUTHORITATIVE",
                    "reason_code": "BEHAVIOR_API_BINDING_MULTIPLE_AUTHORITATIVE",
                    "behavior_ref": behavior_id,
                    "interface_refs": api_ids,
                    "blocks_scenario_planning": True,
                    "automatic_primary_endpoint_selection_allowed": False,
                }
            )
        elif not api_ids:
            candidates = [
                text(row.get("interface_id"))
                for row in _dicts(binding.get("api_operation_bindings"))
                if text(row.get("interface_id"))
            ]
            unknowns.append(
                {
                    "kind": (
                        "BEHAVIOR_API_BINDING_AMBIGUOUS"
                        if len(candidates) > 1
                        else "BEHAVIOR_API_BINDING_UNRESOLVED"
                    ),
                    "reason_code": (
                        "BEHAVIOR_API_BINDING_AMBIGUOUS"
                        if len(candidates) > 1
                        else "BEHAVIOR_API_BINDING_UNRESOLVED"
                    ),
                    "behavior_ref": behavior_id,
                    "candidate_interface_refs": candidates,
                    "operation_ref": behavior.get("operation_ref"),
                    "blocks_scenario_planning": True,
                }
            )

        condition_slots = [
            _govern_observer_slot(row)
            for row in _dicts(binding.get("condition_observer_bindings"))
        ]
        effect_slots = [
            _govern_observer_slot(row)
            for row in _dicts(binding.get("effect_observer_bindings"))
        ]
        binding["condition_observer_bindings"] = condition_slots
        binding["effect_observer_bindings"] = effect_slots

        for slot in condition_slots:
            if text(slot.get("status")) != "BOUND":
                unknowns.append(
                    {
                        "kind": "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED",
                        "reason_code": text(slot.get("reason_code"))
                        or "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED",
                        "behavior_ref": behavior_id,
                        "slot_ref": slot.get("slot_ref"),
                        "field_candidate": slot.get("source_field_candidate"),
                        "status": slot.get("status"),
                        "blocks_scenario_planning": True,
                    }
                )

        condition_ready = (
            all(text(row.get("status")) == "BOUND" for row in condition_slots)
            if _dicts(behavior.get("preconditions"))
            else True
        )
        effect_ready = any(
            text(row.get("status")) == "BOUND" for row in effect_slots
        ) or _response_channel_ready(binding)
        if not effect_ready:
            unknowns.append(
                {
                    "kind": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
                    "reason_code": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
                    "behavior_ref": behavior_id,
                    "blocks_scenario_planning": True,
                }
            )

        semantic_ready = _behavior_semantic_ready(behavior)
        if not semantic_ready:
            unknowns.append(
                {
                    "kind": "IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED",
                    "reason_code": "IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED",
                    "behavior_ref": behavior_id,
                    "behavior_status": behavior.get("status"),
                    "blocks_scenario_planning": True,
                }
            )

        action_ready = len(api_ids) == 1 and not missing_authoritative
        has_conflict = any(
            text(row.get("behavior_ref")) == behavior_id
            and text(row.get("status")) == "UNRESOLVED"
            for row in conflicts
            if isinstance(row, dict)
        )
        scenario_ready = bool(
            action_ready
            and condition_ready
            and effect_ready
            and semantic_ready
            and not has_conflict
        )
        ambiguous = len(api_ids) > 1 or any(
            text(row.get("status")) == "AMBIGUOUS"
            for row in [*condition_slots, *effect_slots]
        )
        binding["status"] = (
            "CONFLICTED"
            if has_conflict
            else "AMBIGUOUS"
            if ambiguous
            else "BOUND"
            if scenario_ready
            else "PARTIAL"
            if api_ids or condition_slots or effect_slots or as_list(
                binding.get("ui_action_bindings")
            )
            else "UNBOUND"
        )
        binding["primary_api_interface_ref"] = api_ids[0] if len(api_ids) == 1 else ""
        binding["authoritative_api_interface_count"] = len(api_ids)
        binding["scenario_planning_ready"] = scenario_ready
        binding["execution_ready"] = False
        binding["request_payload_compiled"] = False
        binding["expected_assertion_compiled"] = False
        binding["runtime_observer_gate_enforced"] = True
        binding["api_request_field_is_runtime_observer"] = False
        governed.append(binding)

    deduped_unknowns = _dedupe_unknowns(unknowns)
    counts: dict[str, int] = defaultdict(int)
    for binding in governed:
        counts[text(binding.get("status")) or "UNKNOWN"] += 1
    ready = sum(1 for row in governed if bool(row.get("scenario_planning_ready")))
    if conflicts or counts["CONFLICTED"]:
        status = "BLOCKED_IMPLEMENTATION_BINDING_CONFLICT"
    elif governed and ready == len(governed):
        status = "PASS"
    elif governed:
        status = "PARTIAL_IMPLEMENTATION_BINDING"
    else:
        status = "NO_BEHAVIOR_IMPLEMENTATION_BINDING"

    gate = {
        "schema": IMPLEMENTATION_BINDING_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "scenario_planning_allowed": status == "PASS",
        "execution_allowed": False,
        "metrics": {
            "behavior_binding_count": len(governed),
            "scenario_ready_binding_count": ready,
            "bound_binding_count": counts["BOUND"],
            "partial_binding_count": counts["PARTIAL"],
            "unbound_binding_count": counts["UNBOUND"],
            "ambiguous_binding_count": counts["AMBIGUOUS"],
            "conflicted_binding_count": counts["CONFLICTED"],
            "implementation_binding_conflict_count": len(conflicts),
            "implementation_binding_unknown_count": len(deduped_unknowns),
            "scenario_ready_rate": round(ready / len(governed), 4)
            if governed
            else 0.0,
        },
        "required_operator_action": (
            "resolve implementation binding conflicts before scenario planning"
            if status.startswith("BLOCKED")
            else "provide one source-backed action endpoint and observable condition/effect surfaces for every confirmed behavior"
            if status != "PASS"
            else ""
        ),
        "quality_claim": "IMPLEMENTATION_BINDING_CLOSURE_NOT_RUNTIME_VERIFICATION",
        "semantic_understanding_gate_is_separate": True,
        "single_primary_action_interface_required": True,
        "api_request_field_is_runtime_observer": False,
        "arbitrary_endpoint_fallback_allowed": False,
        "token_overlap_is_authoritative": False,
        "request_payload_compiled": False,
        "expected_assertion_compiled": False,
    }
    return governed, deduped_unknowns, conflicts, gate


__all__ = ["build_governed_behavior_implementation_bindings"]
