"""Security hardening for governed Runtime Materialization drafts.

Security is applied as an explicit projection over caller-owned data. No resolver is
replaced globally, so importing or calling this module cannot change later builds.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote

from . import runtime_materialization as _base
from . import runtime_materialization_governance as _governance
from .credential_identity import govern_runtime_materialization_credentials
from .runtime_materialization_messages import materialization_reason_message
from .schema import as_dict, as_list, stable_id, text


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _scrubbed_binding(slot: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "slot_id": text(slot.get("slot_id")),
        "field": text(slot.get("field")),
        "location": text(slot.get("location")).upper(),
        "resolution_status": reason,
        "draft_value_present": False,
        "secret_value_retained": False,
        "value_executed": False,
    }


def _materialization_unknown(
    materialization_id: str, reason: str, **details: Any
) -> dict[str, Any]:
    return {
        "unknown_id": stable_id(
            "runtime_materialization_unknown",
            materialization_id,
            reason,
            details.get("slot_id"),
            details.get("field"),
            details.get("binding_ref"),
        ),
        "kind": reason,
        "reason_code": reason,
        "message": materialization_reason_message(reason),
        "runtime_materialization_ref": materialization_id,
        "blocks_runtime_materialization": True,
        "execution_allowed": False,
        **details,
    }


def _binding_has_value_source(binding: dict[str, Any]) -> bool:
    if text(
        binding.get("fixture_ref")
        or binding.get("entity_ref")
        or binding.get("value_ref")
    ):
        return True
    if "value" in binding and binding.get("value") is not None:
        return _base._safe_literal(binding.get("value"))
    generator = as_dict(binding.get("generator"))
    return bool(text(generator.get("kind") or binding.get("generator_kind")))


def _attach_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        reason = text(row.get("reason_code") or row.get("kind"))
        if reason and not text(row.get("message")):
            row["message"] = materialization_reason_message(reason)
        result.append(row)
    return result


def _plan_index(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }


def _slot_index(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    request = as_dict(plan.get("request_template"))
    result: dict[str, dict[str, Any]] = {}
    for collection in _base._REQUEST_COLLECTIONS:
        for slot in _dicts(request.get(collection)):
            slot_id = text(slot.get("slot_id"))
            if slot_id:
                result[slot_id] = slot
    return result


def _secure_binding(
    *,
    plan: dict[str, Any],
    slot: dict[str, Any],
    binding: dict[str, Any],
    asset: dict[str, Any],
    materialization_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_kind = text(as_dict(slot.get("value_source")).get("source_kind"))
    if source_kind != "SOURCE_BACKED_SEMANTIC_VALUE":
        matches = [
            row
            for row in _base._input_catalog(asset)
            if _base._binding_matches(row, plan, slot)
        ]
        if len(matches) == 1 and not _base._approved(matches[0]):
            reason = "RUNTIME_MATERIALIZATION_VALUE_BINDING_NOT_APPROVED"
            return (
                _scrubbed_binding(slot, "BLOCKED_VALUE_BINDING_NOT_APPROVED"),
                [
                    _materialization_unknown(
                        materialization_id,
                        reason,
                        slot_id=slot.get("slot_id"),
                        field=slot.get("field"),
                        location=slot.get("location"),
                    )
                ],
            )
    if (
        bool(slot.get("required"))
        and binding.get("draft_value_present") is True
        and binding.get("draft_value") is None
    ):
        reason = "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_IS_NULL"
        return (
            _scrubbed_binding(slot, "BLOCKED_REQUIRED_VALUE_IS_NULL"),
            [
                _materialization_unknown(
                    materialization_id,
                    reason,
                    slot_id=slot.get("slot_id"),
                    field=slot.get("field"),
                    location=slot.get("location"),
                )
            ],
        )
    return dict(binding), []


def _rebuild_request_draft(
    materialization: dict[str, Any],
    plan: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    existing = dict(as_dict(materialization.get("request_draft")))
    request = as_dict(plan.get("request_template"))
    grouped: dict[str, list[dict[str, Any]]] = {
        collection: [] for collection in _base._REQUEST_COLLECTIONS
    }
    collection_by_location = {
        "PATH": "path_parameters",
        "QUERY": "query_parameters",
        "HEADER": "header_parameters",
        "COOKIE": "cookie_parameters",
        "BODY": "body_fields",
        "FORM": "form_fields",
    }
    for binding in bindings:
        collection = collection_by_location.get(text(binding.get("location")).upper())
        if collection:
            grouped[collection].append(binding)

    path = text(request.get("path_template"))
    for binding in grouped["path_parameters"]:
        field = text(binding.get("field"))
        token = _base._draft_token(binding)
        path = path.replace(f"{{{field}}}", quote(str(token), safe="{}:_-"))
    body: dict[str, Any] = {}
    flat_body: list[dict[str, Any]] = []
    for binding in grouped["body_fields"]:
        value = _base._draft_token(binding)
        field = text(binding.get("field"))
        nested = _base._set_nested(body, field, value)
        flat_body.append({"field": field, "value": value, "nested": nested})
    base_url = text(existing.get("base_url"))
    blocked = any(
        text(row.get("resolution_status")).startswith(
            ("BLOCKED", "UNRESOLVED", "AMBIGUOUS")
        )
        for row in bindings
    )
    existing.update(
        {
            "path_draft": path,
            "url_draft": (
                f"{base_url.rstrip('/')}/{path.lstrip('/')}"
                if base_url and path and not blocked
                else ""
            ),
            "query_draft": [
                {"field": row.get("field"), "value": _base._draft_token(row)}
                for row in grouped["query_parameters"]
            ],
            "header_draft": [
                {
                    "field": row.get("field"),
                    "value": _base._draft_token(row),
                    "sensitive": False,
                }
                for row in grouped["header_parameters"]
            ],
            "cookie_draft": [
                {"field": row.get("field"), "value": _base._draft_token(row)}
                for row in grouped["cookie_parameters"]
            ],
            "body_draft": body,
            "body_field_drafts": flat_body,
            "form_field_drafts": [
                {"field": row.get("field"), "value": _base._draft_token(row)}
                for row in grouped["form_fields"]
            ],
            "resolved_binding_count": sum(
                1
                for row in bindings
                if text(row.get("resolution_status")).startswith(
                    ("RESOLVED", "DEFERRED")
                )
            ),
            "draft_compiled": not blocked,
            "request_serialized": False,
            "request_sendable": False,
            "network_call_allowed": False,
        }
    )
    return existing


def _secure_projected_bindings(asset: dict[str, Any], model: dict[str, Any]) -> None:
    plans = _plan_index(asset)
    unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    secured_materializations: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_materializations")):
        materialization = dict(raw)
        materialization_id = text(materialization.get("materialization_id"))
        plan = plans.get(text(materialization.get("runtime_plan_ref"))) or {}
        slots = _slot_index(plan)
        secured_bindings: list[dict[str, Any]] = []
        for binding in _dicts(materialization.get("request_value_bindings")):
            slot = slots.get(text(binding.get("slot_id"))) or {
                "slot_id": binding.get("slot_id"),
                "field": binding.get("field"),
                "location": binding.get("location"),
                "required": False,
            }
            secured, rows = _secure_binding(
                plan=plan,
                slot=slot,
                binding=binding,
                asset=asset,
                materialization_id=materialization_id,
            )
            secured_bindings.append(secured)
            unknowns.extend(rows)
        materialization["request_value_bindings"] = secured_bindings
        materialization["request_draft"] = _rebuild_request_draft(
            materialization, plan, secured_bindings
        )
        secured_materializations.append(materialization)
    asset["runtime_materializations"] = secured_materializations
    model["runtime_materializations"] = [dict(row) for row in secured_materializations]
    asset["runtime_materialization_unknowns"] = list(
        {
            text(row.get("unknown_id")): row
            for row in _attach_messages(unknowns)
            if text(row.get("unknown_id"))
        }.values()
    )
    model["runtime_materialization_unknowns"] = [
        dict(row) for row in asset["runtime_materialization_unknowns"]
    ]


def _post_audit(asset: dict[str, Any], model: dict[str, Any]) -> None:
    unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    catalog = {
        text(row.get("binding_id")): row
        for row in _base._input_catalog(asset)
        if text(row.get("binding_id"))
    }
    for materialization in _dicts(asset.get("runtime_materializations")):
        materialization_id = text(materialization.get("materialization_id"))
        environment = as_dict(materialization.get("environment_binding"))
        if not text(environment.get("environment_ref")):
            unknowns.append(
                _materialization_unknown(
                    materialization_id,
                    "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_UNRESOLVED",
                )
            )
        for draft in _dicts(materialization.get("test_data_setup_drafts")):
            if text(draft.get("resolution_status")) != "RESOLVED_TEST_DATA_REFERENCE":
                continue
            binding_ref = text(draft.get("binding_ref"))
            binding = catalog.get(binding_ref) or {}
            if binding and _binding_has_value_source(binding):
                continue
            unknowns.append(
                _materialization_unknown(
                    materialization_id,
                    "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_HAS_NO_VALUE_SOURCE",
                    slot_id=draft.get("slot_ref"),
                    field=draft.get("field"),
                    binding_ref=binding_ref,
                )
            )
    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in _attach_messages(unknowns)
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_materialization_unknowns"] = all_unknowns
    model["runtime_materialization_unknowns"] = [dict(row) for row in all_unknowns]
    _governance._rebuild_gate(asset, model)


def install_secure_runtime_value_resolver() -> None:
    """Deprecated compatibility no-op; security no longer patches ``_resolve_slot``."""
    return None


def project_secure_runtime_materializations_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Project governed drafts, preserve credential identity, then rebuild the gate."""
    _governance.project_governed_runtime_materializations_to_asset(asset, model)
    govern_runtime_materialization_credentials(asset, model)
    _secure_projected_bindings(asset, model)
    _post_audit(asset, model)
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_materialization_unapproved_values_are_scrubbed": True,
            "runtime_materialization_required_null_values_block": True,
            "runtime_materialization_test_data_requires_actual_value_source": True,
            "runtime_materialization_environment_identity_required": True,
            "runtime_materialization_unknowns_include_readable_messages": True,
            "runtime_materialization_secure_projection_is_public_authority": True,
            "runtime_materialization_resolver_global_patch_enabled": False,
            "runtime_materialization_credential_identity_coordinates_preserved": True,
            "runtime_materialization_credential_substitution_allowed": False,
            "runtime_materialization_role_order_selection_allowed": False,
        }
    )
    asset["governance"] = governance
    return asset


def build_secure_runtime_materializations_v1(
    asset: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return secure detached drafts without modifying caller-owned objects."""
    working_asset = deepcopy(asset)
    working_model = deepcopy(model)
    project_secure_runtime_materializations_to_asset(working_asset, working_model)
    return (
        deepcopy(_dicts(working_asset.get("runtime_materializations"))),
        deepcopy(_dicts(working_asset.get("runtime_materialization_unknowns"))),
        deepcopy(as_dict(working_asset.get("runtime_materialization_gate"))),
    )


__all__ = [
    "install_secure_runtime_value_resolver",
    "build_secure_runtime_materializations_v1",
    "project_secure_runtime_materializations_to_asset",
]
