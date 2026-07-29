"""Govern Runtime Plan request-contract variants without selecting a runtime representation."""
from __future__ import annotations

import json
import re
from typing import Any

from .runtime_plan import project_runtime_plans_to_asset
from .schema import as_dict, as_list, stable_id, text, unique_text

_REQUEST_LOCATIONS = (
    "path_parameters",
    "query_parameters",
    "header_parameters",
    "cookie_parameters",
    "body_fields",
    "form_fields",
)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _names(row: dict[str, Any]) -> set[str]:
    return {
        value
        for raw in (
            row.get("field"),
            row.get("name"),
            text(row.get("field")).split(".")[-1],
            text(row.get("name")).split(".")[-1],
        )
        if (value := _norm(raw))
    }


def _descriptors(interface: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in [
            *_dicts(interface.get("parameter_contracts")),
            *_dicts(interface.get("request_body_fields")),
        ]
        if text(row.get("location"))
    ]


def _signature(row: dict[str, Any]) -> str:
    return json.dumps(
        {
            "schema_type": text(row.get("schema_type")) or "UNSPECIFIED",
            "format": text(row.get("format")),
            "required": bool(row.get("required")),
            "enum": sorted(text(value) for value in as_list(row.get("enum"))),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _audit_plan(
    plan: dict[str, Any], interface: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated = dict(plan)
    request = dict(as_dict(updated.get("request_template")))
    descriptors = _descriptors(interface)
    unknowns: list[dict[str, Any]] = []
    for collection in _REQUEST_LOCATIONS:
        slots: list[dict[str, Any]] = []
        for raw in _dicts(request.get(collection)):
            slot = dict(raw)
            field = text(slot.get("field"))
            location = text(slot.get("location"))
            target_full = _norm(field)
            target_leaf = _norm(field.split(".")[-1])
            matches = [
                row
                for row in descriptors
                if text(row.get("location")) == location
                and (
                    target_full in _names(row)
                    or target_leaf in _names(row)
                )
            ]
            signatures = {_signature(row) for row in matches}
            media_types = unique_text(row.get("media_type") for row in matches)
            if len(signatures) > 1:
                reason = "RUNTIME_PLAN_REQUEST_FIELD_CONTRACT_CONFLICT"
                unknowns.append(
                    {
                        "unknown_id": stable_id(
                            "runtime_plan_unknown",
                            updated.get("plan_id"),
                            reason,
                            location,
                            field,
                        ),
                        "kind": reason,
                        "reason_code": reason,
                        "runtime_plan_ref": updated.get("plan_id"),
                        "execution_contract_ref": updated.get(
                            "execution_contract_ref"
                        ),
                        "field": field,
                        "location": location,
                        "contract_signatures": sorted(signatures),
                        "blocks_runtime_plan": True,
                        "execution_allowed": False,
                    }
                )
            if location in {"BODY", "FORM"} and len(media_types) > 1:
                reason = "RUNTIME_PLAN_REQUEST_MEDIA_TYPE_SELECTION_REQUIRED"
                slot.pop("media_type", None)
                slot["media_type_candidates"] = media_types
                slot["media_type_resolution_status"] = (
                    "RUNTIME_MEDIA_TYPE_SELECTION_REQUIRED"
                )
                unknowns.append(
                    {
                        "unknown_id": stable_id(
                            "runtime_plan_unknown",
                            updated.get("plan_id"),
                            reason,
                            location,
                            field,
                        ),
                        "kind": reason,
                        "reason_code": reason,
                        "runtime_plan_ref": updated.get("plan_id"),
                        "execution_contract_ref": updated.get(
                            "execution_contract_ref"
                        ),
                        "field": field,
                        "location": location,
                        "media_type_candidates": media_types,
                        "blocks_runtime_plan": False,
                        "execution_allowed": False,
                    }
                )
            slots.append(slot)
        request[collection] = slots
    critical = [row for row in unknowns if bool(row.get("blocks_runtime_plan"))]
    if critical:
        updated["status"] = "INCOMPLETE"
        updated["formal_runtime_plan"] = False
    updated["request_template"] = request
    updated["unresolved_runtime_plan_semantics"] = unique_text(
        [
            *as_list(updated.get("unresolved_runtime_plan_semantics")),
            *(row.get("reason_code") for row in unknowns),
        ]
    )
    updated["execution_allowed"] = False
    updated["network_calls_allowed"] = False
    updated["cleanup_actions_executable"] = False
    return updated, unknowns


def _refresh_projection(asset: dict[str, Any], model: dict[str, Any]) -> None:
    plans = _dicts(asset.get("runtime_plans"))
    unknowns = _dicts(asset.get("runtime_plan_unknowns"))
    gate = dict(as_dict(asset.get("runtime_plan_gate")))
    ready = sum(1 for row in plans if text(row.get("status")) == "TEMPLATE_READY")
    incomplete = sum(1 for row in plans if text(row.get("status")) == "INCOMPLETE")
    if incomplete:
        status = "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    elif plans:
        status = "PASS"
    else:
        status = text(gate.get("status")) or "NO_RUNTIME_PLAN_COMPILED"
    gate.update(
        {
            "status": status,
            "entry_allowed": status == "PASS",
            "runtime_plan_ready": status == "PASS",
            "execution_allowed": False,
        }
    )
    media_selection_count = sum(
        1
        for row in unknowns
        if text(row.get("reason_code"))
        == "RUNTIME_PLAN_REQUEST_MEDIA_TYPE_SELECTION_REQUIRED"
    )
    contract_conflict_count = sum(
        1
        for row in unknowns
        if text(row.get("reason_code"))
        == "RUNTIME_PLAN_REQUEST_FIELD_CONTRACT_CONFLICT"
    )
    metrics = dict(as_dict(gate.get("metrics")))
    metrics.update(
        {
            "runtime_plan_count": len(plans),
            "ready_runtime_plan_count": ready,
            "incomplete_runtime_plan_count": incomplete,
            "runtime_plan_unknown_count": len(unknowns),
            "request_media_type_selection_requirement_count": media_selection_count,
            "request_field_contract_conflict_count": contract_conflict_count,
        }
    )
    gate["metrics"] = metrics
    asset["runtime_plan_gate"] = gate
    model["runtime_plan_gate"] = dict(gate)

    by_plan = {text(row.get("plan_id")): row for row in plans}
    relationships: list[dict[str, Any]] = []
    for raw in as_list(asset.get("relationships")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        plan_id = ""
        if text(row.get("relation")) == "execution_contract_to_runtime_plan":
            plan_id = text(row.get("to"))
        elif text(row.get("relation")) == "runtime_plan_to_interface":
            plan_id = text(row.get("from"))
        plan = by_plan.get(plan_id)
        if plan is not None:
            accepted = text(plan.get("status")) == "TEMPLATE_READY"
            row["status"] = "accepted" if accepted else "candidate"
            row["confidence"] = 1.0 if accepted else 0.0
        relationships.append(row)
    asset["relationships"] = relationships
    asset["runtime_plan_relationships"] = [
        row
        for row in relationships
        if text(row.get("relation"))
        in {"execution_contract_to_runtime_plan", "runtime_plan_to_interface"}
    ]
    model["runtime_plan_relationships"] = [
        dict(row) for row in asset["runtime_plan_relationships"]
    ]

    projected = {
        "runtime_plan_status": status,
        "runtime_plan_ready": status == "PASS",
        "runtime_plan_count": len(plans),
        "runtime_plan_incomplete_count": incomplete,
        "runtime_plan_unknown_count": len(unknowns),
        "runtime_plan_relationship_count": len(
            asset["runtime_plan_relationships"]
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
    ]
    # Preserve the compiler's original gap and operator action for ordinary causes such as
    # unresolved locations or missing Oracle templates. Replace it only when this governance
    # layer discovered a source-contract conflict requiring a more specific resolution.
    if status == "BLOCKED_RUNTIME_PLAN_INCOMPLETE" and contract_conflict_count:
        gaps = [
            row
            for row in gaps
            if text(row.get("kind")) != "RUNTIME_PLAN_INCOMPLETE"
        ]
        gaps.append(
            {
                "kind": "RUNTIME_PLAN_INCOMPLETE",
                "gap_type": "runtime_plan_request_contract_conflict",
                "source_id": "*",
                "runtime_plan_status": status,
                "runtime_plan_metrics": metrics,
                "execution_allowed": False,
                "operator_action": (
                    "resolve conflicting source-declared request schemas; do not select a "
                    "media type or contract variant by order"
                ),
            }
        )
    asset["coverage_gaps"] = gaps


def project_governed_runtime_plans_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Compile Runtime Plans and apply source-contract conflict governance."""
    project_runtime_plans_to_asset(asset, model)
    interfaces = {
        text(row.get("interface_id")): row
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    }
    governed: list[dict[str, Any]] = []
    extra_unknowns: list[dict[str, Any]] = []
    for plan in _dicts(asset.get("runtime_plans")):
        interface_id = text(as_dict(plan.get("action_entry")).get("interface_id"))
        updated, unknowns = _audit_plan(plan, interfaces.get(interface_id) or {})
        governed.append(updated)
        extra_unknowns.extend(unknowns)
    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in [
                *_dicts(asset.get("runtime_plan_unknowns")),
                *extra_unknowns,
            ]
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_plans"] = governed
    asset["runtime_plan_unknowns"] = all_unknowns
    model["runtime_plans"] = [dict(row) for row in governed]
    model["runtime_plan_unknowns"] = [dict(row) for row in all_unknowns]
    _refresh_projection(asset, model)
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_plan_request_contract_variants_governed": True,
            "runtime_plan_does_not_select_media_type_by_source_order": True,
            "runtime_plan_conflicting_request_schemas_block": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["project_governed_runtime_plans_to_asset"]
