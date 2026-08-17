"""Govern canonical Behavior IR implementation bindings before scenario planning.

The base binder preserves source-backed and diagnostic matches. This closure is the sole
readiness authority: it requires one formal operation surface, every predicate leaf of the
canonical condition expression to be observable, and every mandatory outcome contract to
have its own observation channel. One observable effect can never stand in for a multi-
outcome business contract.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from ..database_observer_implementation_binding import (
    apply_approved_database_observers_to_slot,
)
from .behavior_ir_logic_gate import (
    condition_expression_complete,
    ensure_canonical_behavior_semantics,
    iter_condition_predicates,
    mandatory_outcomes,
    outcome_contracts_complete,
)
from .implementation_binding import (
    IMPLEMENTATION_BINDING_GATE_SCHEMA,
    _authoritative_interface_ids,
    build_behavior_implementation_bindings,
)
from .schema import as_dict, as_list, stable_id, text, unique_text

_ACTION_UNKNOWN_KINDS = {
    "BEHAVIOR_API_BINDING_UNRESOLVED",
    "BEHAVIOR_API_BINDING_AMBIGUOUS",
    "BEHAVIOR_API_BINDING_MULTIPLE_AUTHORITATIVE",
    "BEHAVIOR_AUTHORITATIVE_INTERFACE_MISSING",
}
_FIELD_UNKNOWN_KINDS = {
    "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED",
    "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
    "IMPLEMENTATION_OUTCOME_OBSERVER_UNRESOLVED",
}
_RECOMPUTED_UNKNOWN_KINDS = (
    _ACTION_UNKNOWN_KINDS
    | _FIELD_UNKNOWN_KINDS
    | {"IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED"}
)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _unknown_id(row: dict[str, Any]) -> str:
    return stable_id(
        "implementation_binding_unknown",
        row.get("kind"),
        row.get("behavior_ref"),
        row.get("slot_ref"),
        row.get("outcome_ref"),
        row.get("field_candidate"),
        row.get("interface_refs"),
        row.get("candidate_interface_refs"),
    )


def _dedupe_unknowns(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["unknown_id"] = _unknown_id(row)
        result[row["unknown_id"]] = row
    return sorted(result.values(), key=lambda row: text(row.get("unknown_id")))


def _authoritative_api_rows(binding: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _dicts(binding.get("api_operation_bindings"))
        if bool(row.get("authoritative")) and text(row.get("status")) == "BOUND"
    ]


def _table_identity_names(
    binding: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> set[str]:
    table = tables.get(text(binding.get("table_id"))) or {}
    return {
        _norm(value)
        for value in [
            binding.get("table"),
            table.get("name"),
            *as_list(table.get("aliases")),
        ]
        if _norm(value)
    }


def _database_binding_matches_behavior(
    binding: dict[str, Any],
    behavior: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> bool:
    if (
        text(binding.get("derivation"))
        == "operator_approved_database_observer_contract"
        and bool(binding.get("authoritative"))
        and text(binding.get("observer_id"))
    ):
        return True
    object_names = {
        _norm(value)
        for value in as_list(behavior.get("object_refs"))
        if _norm(value)
    }
    if not object_names:
        return False
    return bool(object_names & _table_identity_names(binding, tables))


def _govern_observer_slot(
    slot: dict[str, Any],
    *,
    behavior: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result = dict(slot)
    candidates = _dicts(result.get("bindings"))
    db_candidates = [
        row
        for row in candidates
        if text(row.get("binding_kind")) == "DATABASE_FIELD"
        and bool(row.get("authoritative"))
    ]
    matching_db_candidates = [
        row
        for row in db_candidates
        if _database_binding_matches_behavior(row, behavior, tables)
    ]
    non_db_observers = [
        row
        for row in candidates
        if text(row.get("binding_kind"))
        in {
            "API_RESPONSE_FIELD",
            "RUNTIME_STATE_OBSERVER",
            "UI_STATE_OBSERVER",
        }
        and bool(row.get("authoritative"))
    ]
    observable_candidates = [*matching_db_candidates, *non_db_observers]
    db_tables = {
        text(row.get("table_id"))
        for row in matching_db_candidates
        if text(row.get("table_id"))
    }
    api_contract_candidates = [
        row
        for row in candidates
        if text(row.get("binding_kind")) == "API_CONTRACT_FIELD"
    ]
    object_refs = [
        value for value in as_list(behavior.get("object_refs")) if text(value)
    ]

    if len(db_tables) > 1:
        status = "AMBIGUOUS"
        reason = "IMPLEMENTATION_FIELD_MULTIPLE_DATABASE_TABLES"
    elif (
        db_candidates
        and object_refs
        and not matching_db_candidates
        and not non_db_observers
    ):
        status = "OBJECT_TABLE_UNRESOLVED"
        reason = "IMPLEMENTATION_FIELD_OBJECT_TABLE_UNRESOLVED"
    elif observable_candidates:
        status = "BOUND"
        reason = ""
    elif api_contract_candidates:
        status = "CONTRACT_FIELD_ONLY"
        reason = "API_CONTRACT_FIELD_IS_NOT_RUNTIME_OBSERVER"
    else:
        status = "UNBOUND"
        reason = (
            text(result.get("reason_code"))
            or "IMPLEMENTATION_OBSERVER_UNRESOLVED"
        )

    result["status"] = status
    result["runtime_observer_available"] = status == "BOUND"
    result["request_contract_field_available"] = bool(api_contract_candidates)
    result["object_table_identity_confirmed"] = bool(matching_db_candidates)
    result["approved_database_observer_used"] = any(
        text(row.get("derivation"))
        == "operator_approved_database_observer_contract"
        for row in matching_db_candidates
    )
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
    ensure_canonical_behavior_semantics(behavior)
    return bool(
        text(behavior.get("status")) == "CONFIRMED"
        and condition_expression_complete(
            as_dict(behavior.get("condition_expression"))
        )
        and text(as_dict(behavior.get("operation_clause")).get("status"))
        == "CONFIRMED"
        and outcome_contracts_complete(behavior)
    )


def _matching_effect_slots(
    outcome: dict[str, Any],
    effect_slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    declared_slot_ref = text(
        outcome.get("observer_slot_ref")
        or outcome.get("effect_observer_slot_ref")
    )
    if declared_slot_ref:
        return [
            slot
            for slot in effect_slots
            if text(slot.get("slot_ref")) == declared_slot_ref
        ]
    field = _norm(outcome.get("field_ref"))
    if not field:
        return []
    return [
        slot
        for slot in effect_slots
        if field
        in {
            _norm(slot.get("source_field_candidate")),
            _norm(text(slot.get("source_field_candidate")).split(".")[-1]),
        }
    ]


def _outcome_observer_bindings(
    behavior: dict[str, Any],
    *,
    binding: dict[str, Any],
    effect_slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind every mandatory outcome independently; no any(effect) shortcut."""
    rows: list[dict[str, Any]] = []
    for outcome in mandatory_outcomes(behavior):
        outcome_ref = text(outcome.get("outcome_id"))
        outcome_type = text(outcome.get("outcome_type"))
        if outcome_type == "PERMISSION_DECISION":
            ready = _response_channel_ready(binding)
            rows.append(
                {
                    "outcome_ref": outcome_ref,
                    "outcome_type": outcome_type,
                    "status": "BOUND" if ready else "UNBOUND",
                    "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                    "reason_code": (
                        ""
                        if ready
                        else "IMPLEMENTATION_PERMISSION_CHANNEL_UNRESOLVED"
                    ),
                }
            )
            continue

        candidates = _matching_effect_slots(outcome, effect_slots)
        bound = [
            slot for slot in candidates if text(slot.get("status")) == "BOUND"
        ]
        ambiguous = [
            slot
            for slot in candidates
            if text(slot.get("status")) == "AMBIGUOUS"
        ]
        if len(bound) == 1:
            status = "BOUND"
            reason = ""
        elif len(bound) > 1 or ambiguous:
            status = "AMBIGUOUS"
            reason = "IMPLEMENTATION_OUTCOME_OBSERVER_AMBIGUOUS"
        else:
            status = "UNBOUND"
            reason = (
                "IMPLEMENTATION_OUTCOME_FIELD_UNRESOLVED"
                if text(outcome.get("field_ref"))
                else "IMPLEMENTATION_OUTCOME_OBSERVER_UNRESOLVED"
            )
        rows.append(
            {
                "outcome_ref": outcome_ref,
                "outcome_type": outcome_type,
                "source_field_candidate": outcome.get("field_ref"),
                "statement": outcome.get("statement"),
                "status": status,
                "observer_slot_refs": unique_text(
                    slot.get("slot_ref") for slot in candidates
                ),
                "reason_code": reason,
                "mandatory": True,
                "automatic_outcome_substitution_allowed": False,
            }
        )
    return rows


def build_governed_behavior_implementation_bindings(
    asset: dict[str, Any],
    behaviors: Iterable[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Return fail-closed implementation bindings suitable for scenario planning."""
    behavior_rows = [
        ensure_canonical_behavior_semantics(dict(row))
        for row in behaviors
        if isinstance(row, dict)
    ]
    behavior_by_id = {
        text(row.get("behavior_id")): row
        for row in behavior_rows
        if text(row.get("behavior_id"))
    }
    bindings, base_unknowns, conflicts, _base_gate = (
        build_behavior_implementation_bindings(asset, behavior_rows)
    )
    interface_ids = {
        text(row.get("interface_id"))
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    }
    tables = {
        text(row.get("table_id")): row
        for row in [
            *_dicts(asset.get("data_tables")),
            *_dicts(asset.get("tables")),
        ]
        if text(row.get("table_id"))
    }
    unknowns = [
        dict(row)
        for row in base_unknowns
        if isinstance(row, dict)
        and text(row.get("kind")) not in _RECOMPUTED_UNKNOWN_KINDS
    ]

    governed: list[dict[str, Any]] = []
    approved_observer_use_count = 0
    mandatory_outcome_count = 0
    bound_outcome_count = 0

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
            kind = (
                "BEHAVIOR_API_BINDING_AMBIGUOUS"
                if len(candidates) > 1
                else "BEHAVIOR_API_BINDING_UNRESOLVED"
            )
            unknowns.append(
                {
                    "kind": kind,
                    "reason_code": kind,
                    "behavior_ref": behavior_id,
                    "candidate_interface_refs": candidates,
                    "operation_ref": as_dict(
                        behavior.get("operation_clause")
                    ).get("operation_ref"),
                    "blocks_scenario_planning": True,
                }
            )

        condition_slots = [
            _govern_observer_slot(
                apply_approved_database_observers_to_slot(
                    row,
                    asset=asset,
                    api_rows=api_rows,
                ),
                behavior=behavior,
                tables=tables,
            )
            for row in _dicts(binding.get("condition_observer_bindings"))
        ]
        effect_slots = [
            _govern_observer_slot(
                apply_approved_database_observers_to_slot(
                    row,
                    asset=asset,
                    api_rows=api_rows,
                ),
                behavior=behavior,
                tables=tables,
            )
            for row in _dicts(binding.get("effect_observer_bindings"))
        ]
        approved_observer_use_count += sum(
            1
            for slot in [*condition_slots, *effect_slots]
            if bool(slot.get("approved_database_observer_used"))
        )
        binding["condition_observer_bindings"] = condition_slots
        binding["effect_observer_bindings"] = effect_slots

        for slot in condition_slots:
            if text(slot.get("status")) != "BOUND":
                unknowns.append(
                    {
                        "kind": "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED",
                        "reason_code": (
                            text(slot.get("reason_code"))
                            or "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED"
                        ),
                        "behavior_ref": behavior_id,
                        "slot_ref": slot.get("slot_ref"),
                        "field_candidate": slot.get(
                            "source_field_candidate"
                        ),
                        "status": slot.get("status"),
                        "blocks_scenario_planning": True,
                    }
                )

        predicates = iter_condition_predicates(
            as_dict(behavior.get("condition_expression"))
        )
        condition_ready = bool(
            condition_expression_complete(
                as_dict(behavior.get("condition_expression"))
            )
            and len(condition_slots) == len(predicates)
            and all(
                text(row.get("status")) == "BOUND"
                for row in condition_slots
            )
        ) if predicates else condition_expression_complete(
            as_dict(behavior.get("condition_expression"))
        )

        outcome_slots = _outcome_observer_bindings(
            behavior,
            binding=binding,
            effect_slots=effect_slots,
        )
        binding["outcome_observer_bindings"] = outcome_slots
        mandatory_outcome_count += len(outcome_slots)
        bound_outcome_count += sum(
            1 for row in outcome_slots if text(row.get("status")) == "BOUND"
        )
        for outcome_slot in outcome_slots:
            if text(outcome_slot.get("status")) == "BOUND":
                continue
            unknowns.append(
                {
                    "kind": "IMPLEMENTATION_OUTCOME_OBSERVER_UNRESOLVED",
                    "reason_code": (
                        text(outcome_slot.get("reason_code"))
                        or "IMPLEMENTATION_OUTCOME_OBSERVER_UNRESOLVED"
                    ),
                    "behavior_ref": behavior_id,
                    "outcome_ref": outcome_slot.get("outcome_ref"),
                    "outcome_type": outcome_slot.get("outcome_type"),
                    "field_candidate": outcome_slot.get(
                        "source_field_candidate"
                    ),
                    "status": outcome_slot.get("status"),
                    "blocks_scenario_planning": True,
                }
            )
        effect_ready = bool(outcome_slots) and all(
            text(row.get("status")) == "BOUND" for row in outcome_slots
        )

        semantic_ready = _behavior_semantic_ready(behavior)
        if not semantic_ready:
            unknowns.append(
                {
                    "kind": "IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED",
                    "reason_code": "IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED",
                    "behavior_ref": behavior_id,
                    "behavior_status": behavior.get("status"),
                    "unresolved_semantics": as_list(
                        behavior.get("unresolved_semantics")
                    ),
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
            for row in [
                *condition_slots,
                *effect_slots,
                *outcome_slots,
            ]
        )
        binding["status"] = (
            "CONFLICTED"
            if has_conflict
            else "AMBIGUOUS"
            if ambiguous
            else "BOUND"
            if scenario_ready
            else "PARTIAL"
            if (
                api_ids
                or condition_slots
                or effect_slots
                or outcome_slots
                or as_list(binding.get("ui_action_bindings"))
            )
            else "UNBOUND"
        )
        binding["primary_api_interface_ref"] = (
            api_ids[0] if len(api_ids) == 1 else ""
        )
        binding["authoritative_api_interface_count"] = len(api_ids)
        binding["scenario_planning_ready"] = scenario_ready
        binding["execution_ready"] = False
        binding["request_payload_compiled"] = False
        binding["expected_assertion_compiled"] = False
        binding["runtime_observer_gate_enforced"] = True
        binding["object_table_identity_gate_enforced"] = True
        binding["database_mapping_authority_gate_enforced"] = True
        binding["api_request_field_is_runtime_observer"] = False
        binding["canonical_condition_expression_enforced"] = True
        binding["all_mandatory_outcomes_must_be_observable"] = True
        binding["single_observed_effect_cannot_cover_multiple_outcomes"] = True
        governed.append(binding)

    deduped_unknowns = _dedupe_unknowns(unknowns)
    counts: dict[str, int] = defaultdict(int)
    for binding in governed:
        counts[text(binding.get("status")) or "UNKNOWN"] += 1
    ready = sum(
        1 for row in governed if bool(row.get("scenario_planning_ready"))
    )
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
            "approved_database_observer_slot_count": (
                approved_observer_use_count
            ),
            "mandatory_outcome_count": mandatory_outcome_count,
            "bound_mandatory_outcome_count": bound_outcome_count,
            "mandatory_outcome_binding_rate": (
                round(
                    bound_outcome_count / mandatory_outcome_count,
                    4,
                )
                if mandatory_outcome_count
                else 0.0
            ),
            "scenario_ready_rate": (
                round(ready / len(governed), 4) if governed else 0.0
            ),
        },
        "required_operator_action": (
            "resolve implementation binding conflicts before scenario planning"
            if status.startswith("BLOCKED")
            else (
                "provide one source-backed action endpoint and observable "
                "condition/outcome surfaces for every confirmed behavior"
            )
            if status != "PASS"
            else ""
        ),
        "quality_claim": (
            "IMPLEMENTATION_BINDING_CLOSURE_NOT_RUNTIME_VERIFICATION"
        ),
        "semantic_understanding_gate_is_separate": True,
        "single_primary_action_interface_required": True,
        "object_table_identity_required_for_database_observers": True,
        "operator_approved_mapping_required_for_scoped_database_observers": True,
        "raw_database_fields_cannot_bypass_mapping_authority": True,
        "api_request_field_is_runtime_observer": False,
        "arbitrary_endpoint_fallback_allowed": False,
        "token_overlap_is_authoritative": False,
        "canonical_condition_expression_required": True,
        "all_mandatory_outcomes_must_be_observable": True,
        "one_observer_cannot_implicitly_cover_multiple_outcomes": True,
        "request_payload_compiled": False,
        "expected_assertion_compiled": False,
    }
    return governed, deduped_unknowns, conflicts, gate


__all__ = ["build_governed_behavior_implementation_bindings"]
