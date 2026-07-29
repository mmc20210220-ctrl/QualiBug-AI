"""Resolve Runtime Plan v1 into auditable, non-sendable materialization drafts.

This layer may bind explicit non-secret literals, fixture/value references, environment metadata
and credential references. It never reads a secret value, executes a generator, opens a network or
database connection, serializes a sendable request, performs cleanup, or reports a Bug.
"""
from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import quote

from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

RUNTIME_MATERIALIZATION_SCHEMA = "qualibug.runtime-materialization-contract.v1"
RUNTIME_MATERIALIZATION_GATE_SCHEMA = "qualibug.runtime-materialization-gate.v1"

_REQUEST_COLLECTIONS = (
    "path_parameters",
    "query_parameters",
    "header_parameters",
    "cookie_parameters",
    "body_fields",
    "form_fields",
)
_NON_PRODUCTION_KINDS = {
    "DEV",
    "DEVELOPMENT",
    "TEST",
    "TESTING",
    "SIT",
    "UAT",
    "STAGING",
    "SANDBOX",
    "QA",
}
_APPROVED_STATUSES = {"APPROVED", "READY", "ACTIVE", "VALIDATED"}
_ALLOWED_GENERATORS = {"UUID", "TIMESTAMP", "SEQUENCE", "RANDOM_SUFFIX"}
_SENSITIVE_FIELD_RE = re.compile(
    r"(?:authorization|proxy-authorization|cookie|set-cookie|password|passwd|pwd|secret|"
    r"token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret)",
    re.I,
)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _approved(row: dict[str, Any]) -> bool:
    if row.get("approved_for_materialization") is True or row.get("approved") is True:
        return True
    return text(row.get("status")).upper() in _APPROVED_STATUSES


def _containers(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        asset,
        as_dict(asset.get("runtime_environment")),
        as_dict(asset.get("environment_contract")),
        as_dict(asset.get("project_configuration")),
        as_dict(asset.get("runtime_configuration")),
    ]


def _collect_rows(asset: dict[str, Any], keys: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for container in _containers(asset):
        for key in keys:
            value = container.get(key)
            if isinstance(value, dict):
                rows.append(dict(value))
            rows.extend(_dicts(value))
    return rows


def _unknown(
    contract_id: Any,
    reason: str,
    *,
    blocks: bool = True,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(details or {})
    return {
        "unknown_id": stable_id(
            "runtime_materialization_unknown",
            contract_id,
            reason,
            payload.get("slot_id"),
            payload.get("field"),
            payload.get("actor_ref"),
        ),
        "kind": reason,
        "reason_code": reason,
        "runtime_materialization_ref": contract_id,
        "blocks_runtime_materialization": blocks,
        "execution_allowed": False,
        **payload,
    }


def _environment_catalog(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _collect_rows(
        asset,
        (
            "runtime_environments",
            "environments",
            "environment_refs",
            "runtime_environment",
            "environment_contract",
        ),
    )
    rows.extend(_dicts(asset.get("connectors")))
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        environment_ref = text(
            raw.get("environment_ref")
            or raw.get("environment_id")
            or raw.get("env_ref")
            or raw.get("id")
        )
        base_url = text(
            raw.get("base_url")
            or raw.get("endpoint_ref")
            or raw.get("target_url")
            or raw.get("url")
        )
        kind = text(
            raw.get("environment_kind")
            or raw.get("environment_type")
            or raw.get("kind")
            or raw.get("type")
        ).upper()
        capabilities = unique_text(
            [
                *as_list(raw.get("capabilities")),
                *as_list(raw.get("environment_capabilities")),
            ]
        )
        if raw.get("disposable") is True:
            capabilities.append("DISPOSABLE")
        if raw.get("resettable") is True or text(raw.get("reset_ref")):
            capabilities.append("RESETTABLE")
        normalized.append(
            {
                "environment_ref": environment_ref,
                "base_url": base_url,
                "environment_kind": kind,
                "is_production": raw.get("is_production"),
                "capabilities": unique_text(capabilities),
                "reset_ref": text(raw.get("reset_ref")),
                "source_kind": text(raw.get("source_kind")) or "PROJECT_RUNTIME_METADATA",
            }
        )
    return list(
        {
            stable_id(
                "environment_candidate",
                row.get("environment_ref"),
                row.get("base_url"),
                row.get("environment_kind"),
            ): row
            for row in normalized
            if row.get("environment_ref") or row.get("base_url")
        }.values()
    )


def _environment_binding(
    plan: dict[str, Any], asset: dict[str, Any], contract_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    template = as_dict(plan.get("environment_template"))
    expected_ref = text(template.get("environment_ref"))
    catalog = _environment_catalog(asset)
    matches = [
        row
        for row in catalog
        if expected_ref and text(row.get("environment_ref")) == expected_ref
    ]
    if not expected_ref and len(catalog) == 1:
        matches = catalog
    unknowns: list[dict[str, Any]] = []
    if not matches:
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_UNRESOLVED",
                details={"environment_ref": expected_ref},
            )
        )
        selected: dict[str, Any] = {}
    elif len(matches) > 1:
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_AMBIGUOUS",
                details={
                    "environment_ref": expected_ref,
                    "candidate_count": len(matches),
                },
            )
        )
        selected = {}
    else:
        selected = matches[0]
    if selected and not text(selected.get("base_url")):
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_BASE_URL_UNRESOLVED",
                details={"environment_ref": selected.get("environment_ref")},
            )
        )
    write = bool(as_dict(plan.get("cleanup_step_templates")).get("write_action"))
    kind = text(selected.get("environment_kind")).upper()
    explicitly_non_production = selected.get("is_production") is False or kind in _NON_PRODUCTION_KINDS
    explicitly_production = selected.get("is_production") is True or kind in {"PROD", "PRODUCTION"}
    if write and explicitly_production:
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_PRODUCTION_WRITE_FORBIDDEN",
                details={"environment_ref": selected.get("environment_ref")},
            )
        )
    elif write and not explicitly_non_production:
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_NON_PRODUCTION_ENVIRONMENT_UNPROVEN",
                details={"environment_ref": selected.get("environment_ref")},
            )
        )
    return (
        {
            "environment_ref": selected.get("environment_ref") or expected_ref,
            "base_url": selected.get("base_url"),
            "environment_kind": selected.get("environment_kind"),
            "capabilities": as_list(selected.get("capabilities")),
            "reset_ref": selected.get("reset_ref"),
            "environment_metadata_resolved": bool(selected),
            "base_url_resolved": bool(text(selected.get("base_url"))),
            "non_production_proven": explicitly_non_production,
            "production_write_forbidden": True,
            "network_access_allowed": False,
            "environment_probe_executed": False,
        },
        unknowns,
    )


def _credential_binding(
    plan: dict[str, Any], contract_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    template = as_dict(plan.get("credential_template"))
    slots: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for raw in _dicts(template.get("credential_slots")):
        credential_ref = text(raw.get("credential_ref"))
        actor = text(raw.get("actor_ref"))
        slot_id = text(raw.get("slot_id"))
        if not credential_ref:
            unknowns.append(
                _unknown(
                    contract_id,
                    "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED",
                    details={"slot_id": slot_id, "actor_ref": actor},
                )
            )
        slots.append(
            {
                "slot_id": slot_id,
                "actor_ref": actor,
                "credential_ref": credential_ref,
                "environment_ref": raw.get("environment_ref"),
                "binding_kind": "CREDENTIAL_REFERENCE",
                "secret_loader_required": bool(credential_ref),
                "secret_value_loaded": False,
                "secret_value_retained": False,
                "automatic_role_substitution_allowed": False,
            }
        )
    security_placeholders: list[dict[str, Any]] = []
    primary_ref = next((text(row.get("credential_ref")) for row in slots if text(row.get("credential_ref"))), "")
    for requirement in _dicts(template.get("security_requirements")):
        scheme_type = text(requirement.get("type")).upper()
        scheme_name = text(requirement.get("scheme_name")).lower()
        location = text(requirement.get("in")).upper()
        name = text(requirement.get("name"))
        if scheme_type == "HTTP" and scheme_name == "bearer":
            location, name = "HEADER", "Authorization"
        security_placeholders.append(
            {
                "scheme": requirement.get("scheme"),
                "scheme_type": scheme_type,
                "location": location or "AUTH_FLOW",
                "name": name,
                "credential_ref": primary_ref,
                "placeholder": f"{{{{secret_ref:{primary_ref}}}}}" if primary_ref else "",
                "secret_value_loaded": False,
                "request_injection_executed": False,
            }
        )
    return (
        {
            "credential_slots": slots,
            "security_placeholders": security_placeholders,
            "credential_refs_resolved": bool(slots) and all(text(row.get("credential_ref")) for row in slots),
            "secret_values_loaded": False,
            "plaintext_credentials_allowed": False,
            "authentication_executed": False,
        },
        unknowns,
    )


def _input_catalog(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return _collect_rows(
        asset,
        (
            "runtime_input_bindings",
            "materialization_bindings",
            "request_value_bindings",
            "entity_bindings",
            "test_data_bindings",
        ),
    )


def _fixture_catalog(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = _collect_rows(
        asset,
        (
            "test_data_fixtures",
            "approved_fixtures",
            "entity_fixtures",
            "fixtures",
        ),
    )
    return {
        text(row.get("fixture_ref") or row.get("fixture_id") or row.get("id")): row
        for row in rows
        if text(row.get("fixture_ref") or row.get("fixture_id") or row.get("id"))
    }


def _binding_matches(binding: dict[str, Any], plan: dict[str, Any], slot: dict[str, Any]) -> bool:
    plan_ref = text(binding.get("runtime_plan_ref") or binding.get("plan_ref"))
    if plan_ref and plan_ref != text(plan.get("plan_id")):
        return False
    slot_candidates = {
        text(slot.get("slot_id")),
        text(as_dict(slot.get("value_source")).get("source_slot_ref")),
    }
    binding_slot = text(binding.get("slot_id") or binding.get("slot_ref") or binding.get("source_slot_ref"))
    if binding_slot and binding_slot in slot_candidates:
        return True
    field = _norm(slot.get("field"))
    binding_field = _norm(binding.get("field") or binding.get("field_path"))
    location = text(slot.get("location")).upper()
    binding_location = text(binding.get("location")).upper()
    return bool(field and field == binding_field and (not binding_location or binding_location == location))


def _safe_literal(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 4096
    if isinstance(value, list):
        return len(value) <= 100 and all(_safe_literal(item) for item in value)
    if isinstance(value, dict):
        return len(value) <= 100 and all(
            isinstance(key, str) and len(key) <= 256 and _safe_literal(item)
            for key, item in value.items()
        )
    return False


def _coerce_source_literal(source: dict[str, Any], slot: dict[str, Any]) -> tuple[Any, str]:
    value = source.get("normalized_value")
    if value in (None, ""):
        value = source.get("raw")
    schema_type = text(slot.get("schema_type") or source.get("value_type")).upper()
    if isinstance(value, str):
        stripped = value.strip()
        try:
            if schema_type in {"INTEGER", "INT", "LONG"}:
                return int(stripped), "INTEGER"
            if schema_type in {"NUMBER", "FLOAT", "DOUBLE", "DECIMAL"}:
                return float(stripped), "NUMBER"
            if schema_type in {"BOOLEAN", "BOOL"}:
                if stripped.lower() in {"true", "1", "yes", "是"}:
                    return True, "BOOLEAN"
                if stripped.lower() in {"false", "0", "no", "否"}:
                    return False, "BOOLEAN"
        except (TypeError, ValueError):
            return value, "TYPE_MISMATCH"
    return value, schema_type or type(value).__name__.upper()


def _fixture_value(
    fixture: dict[str, Any], binding: dict[str, Any], field: str
) -> tuple[Any, bool]:
    values = as_dict(fixture.get("values") or fixture.get("field_values") or fixture.get("data"))
    value_path = text(binding.get("value_path") or binding.get("field") or field)
    current: Any = values
    for token in [part for part in value_path.split(".") if part]:
        if not isinstance(current, dict) or token not in current:
            return None, False
        current = current[token]
    return current, True


def _resolve_slot(
    plan: dict[str, Any],
    slot: dict[str, Any],
    asset: dict[str, Any],
    contract_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    field = text(slot.get("field"))
    location = text(slot.get("location")).upper()
    slot_id = text(slot.get("slot_id"))
    source = as_dict(slot.get("value_source"))
    source_kind = text(source.get("source_kind"))
    unknowns: list[dict[str, Any]] = []
    if _SENSITIVE_FIELD_RE.search(field):
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_SENSITIVE_FIELD_REQUIRES_CREDENTIAL_REF",
                details={"slot_id": slot_id, "field": field, "location": location},
            )
        )
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "resolution_status": "BLOCKED_SENSITIVE_LITERAL",
            "draft_value_present": False,
            "secret_value_retained": False,
        }, unknowns
    if source_kind == "SOURCE_BACKED_SEMANTIC_VALUE":
        value, value_type = _coerce_source_literal(source, slot)
        if value_type == "TYPE_MISMATCH" or not _safe_literal(value):
            unknowns.append(
                _unknown(
                    contract_id,
                    "RUNTIME_MATERIALIZATION_SOURCE_LITERAL_INVALID",
                    details={"slot_id": slot_id, "field": field, "location": location},
                )
            )
            return {
                "slot_id": slot_id,
                "field": field,
                "location": location,
                "resolution_status": "BLOCKED_SOURCE_LITERAL_INVALID",
                "draft_value_present": False,
            }, unknowns
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "binding_kind": "SOURCE_BACKED_LITERAL",
            "draft_value": value,
            "draft_value_type": value_type,
            "source_slot_ref": source.get("source_slot_ref"),
            "resolution_status": "RESOLVED_LITERAL",
            "draft_value_present": True,
            "value_executed": False,
        }, []

    matches = [row for row in _input_catalog(asset) if _binding_matches(row, plan, slot)]
    if not matches:
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_MISSING",
                details={
                    "slot_id": slot_id,
                    "field": field,
                    "location": location,
                    "source_kind": source_kind,
                },
            )
        )
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "resolution_status": "UNRESOLVED_REQUIRED_BINDING",
            "draft_value_present": False,
        }, unknowns
    if len(matches) > 1:
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_AMBIGUOUS",
                details={
                    "slot_id": slot_id,
                    "field": field,
                    "location": location,
                    "candidate_count": len(matches),
                },
            )
        )
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "resolution_status": "AMBIGUOUS_REQUIRED_BINDING",
            "draft_value_present": False,
        }, unknowns
    binding = matches[0]
    if not _approved(binding):
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_VALUE_BINDING_NOT_APPROVED",
                details={"slot_id": slot_id, "field": field, "location": location},
            )
        )
    generator = as_dict(binding.get("generator"))
    generator_kind = text(generator.get("kind") or binding.get("generator_kind")).upper()
    if generator_kind:
        if generator_kind not in _ALLOWED_GENERATORS:
            unknowns.append(
                _unknown(
                    contract_id,
                    "RUNTIME_MATERIALIZATION_GENERATOR_UNSUPPORTED",
                    details={"slot_id": slot_id, "field": field, "generator_kind": generator_kind},
                )
            )
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "binding_kind": "RUNTIME_GENERATOR_DESCRIPTOR",
            "generator": {**generator, "kind": generator_kind},
            "resolution_status": "DEFERRED_GENERATOR",
            "draft_value_present": False,
            "generator_executed": False,
        }, unknowns
    fixture_ref = text(binding.get("fixture_ref"))
    if fixture_ref:
        fixture = _fixture_catalog(asset).get(fixture_ref)
        if not fixture or not _approved(fixture):
            unknowns.append(
                _unknown(
                    contract_id,
                    "RUNTIME_MATERIALIZATION_FIXTURE_REF_UNRESOLVED",
                    details={"slot_id": slot_id, "field": field, "fixture_ref": fixture_ref},
                )
            )
            return {
                "slot_id": slot_id,
                "field": field,
                "location": location,
                "fixture_ref": fixture_ref,
                "resolution_status": "UNRESOLVED_FIXTURE_REF",
                "draft_value_present": False,
            }, unknowns
        value, found = _fixture_value(fixture, binding, field)
        if found and _safe_literal(value):
            return {
                "slot_id": slot_id,
                "field": field,
                "location": location,
                "binding_kind": "APPROVED_FIXTURE_LITERAL",
                "fixture_ref": fixture_ref,
                "draft_value": value,
                "draft_value_present": True,
                "resolution_status": "RESOLVED_FIXTURE_VALUE",
                "fixture_materialized": False,
            }, unknowns
        value_ref = text(binding.get("value_ref") or binding.get("entity_ref"))
        if value_ref:
            return {
                "slot_id": slot_id,
                "field": field,
                "location": location,
                "binding_kind": "APPROVED_FIXTURE_VALUE_REFERENCE",
                "fixture_ref": fixture_ref,
                "value_ref": value_ref,
                "placeholder": f"{{{{value_ref:{value_ref}}}}}",
                "draft_value_present": False,
                "resolution_status": "RESOLVED_VALUE_REFERENCE",
                "reference_resolved_at_execution": False,
            }, unknowns
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_FIXTURE_VALUE_PATH_UNRESOLVED",
                details={"slot_id": slot_id, "field": field, "fixture_ref": fixture_ref},
            )
        )
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "fixture_ref": fixture_ref,
            "resolution_status": "UNRESOLVED_FIXTURE_VALUE",
            "draft_value_present": False,
        }, unknowns
    if "value" in binding:
        value = binding.get("value")
        if not _safe_literal(value):
            unknowns.append(
                _unknown(
                    contract_id,
                    "RUNTIME_MATERIALIZATION_BOUND_LITERAL_INVALID",
                    details={"slot_id": slot_id, "field": field},
                )
            )
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "binding_kind": "APPROVED_RUNTIME_LITERAL",
            "draft_value": value if _safe_literal(value) else None,
            "draft_value_present": _safe_literal(value),
            "resolution_status": "RESOLVED_RUNTIME_LITERAL" if _safe_literal(value) else "BLOCKED_LITERAL_INVALID",
            "binding_ref": binding.get("binding_id"),
            "value_executed": False,
        }, unknowns
    value_ref = text(binding.get("value_ref") or binding.get("entity_ref"))
    if value_ref:
        return {
            "slot_id": slot_id,
            "field": field,
            "location": location,
            "binding_kind": "EXPLICIT_VALUE_REFERENCE",
            "value_ref": value_ref,
            "placeholder": f"{{{{value_ref:{value_ref}}}}}",
            "draft_value_present": False,
            "resolution_status": "RESOLVED_VALUE_REFERENCE",
            "reference_resolved_at_execution": False,
        }, unknowns
    unknowns.append(
        _unknown(
            contract_id,
            "RUNTIME_MATERIALIZATION_BINDING_HAS_NO_VALUE_SOURCE",
            details={"slot_id": slot_id, "field": field, "location": location},
        )
    )
    return {
        "slot_id": slot_id,
        "field": field,
        "location": location,
        "resolution_status": "UNRESOLVED_BINDING_CONTENT",
        "draft_value_present": False,
    }, unknowns


def _draft_token(binding: dict[str, Any]) -> Any:
    if binding.get("draft_value_present") is True:
        return binding.get("draft_value")
    if text(binding.get("placeholder")):
        return binding.get("placeholder")
    generator = as_dict(binding.get("generator"))
    if generator:
        return f"{{{{generator:{text(generator.get('kind')).lower()}}}}}"
    return f"{{{{unresolved:{text(binding.get('slot_id'))}}}}}"


def _media_type_binding(
    plan: dict[str, Any], asset: dict[str, Any], contract_id: str
) -> tuple[str, list[dict[str, Any]]]:
    request = as_dict(plan.get("request_template"))
    candidates = unique_text(
        [
            *as_list(request.get("request_body_media_types")),
            *[
                media
                for slot in [
                    *_dicts(request.get("body_fields")),
                    *_dicts(request.get("form_fields")),
                ]
                for media in [
                    slot.get("media_type"),
                    *as_list(slot.get("media_type_candidates")),
                ]
                if text(media)
            ],
        ]
    )
    if not candidates:
        return "", []
    if len(candidates) == 1:
        return candidates[0], []
    bindings = _collect_rows(asset, ("request_media_type_bindings", "media_type_bindings"))
    matches = [
        row
        for row in bindings
        if (
            text(row.get("runtime_plan_ref") or row.get("plan_ref"))
            in {"", text(plan.get("plan_id"))}
        )
        and text(row.get("interface_id"))
        in {"", text(as_dict(plan.get("action_entry")).get("interface_id"))}
        and text(row.get("media_type")) in candidates
        and _approved(row)
    ]
    if len(matches) == 1:
        return text(matches[0].get("media_type")), []
    reason = (
        "RUNTIME_MATERIALIZATION_MEDIA_TYPE_SELECTION_MISSING"
        if not matches
        else "RUNTIME_MATERIALIZATION_MEDIA_TYPE_SELECTION_AMBIGUOUS"
    )
    return "", [
        _unknown(
            contract_id,
            reason,
            details={"media_type_candidates": candidates, "candidate_count": len(matches)},
        )
    ]


def _set_nested(target: dict[str, Any], path: str, value: Any) -> bool:
    tokens = [part for part in path.split(".") if part and "[]" not in part]
    if not tokens or len(tokens) != len([part for part in path.split(".") if part]):
        return False
    current = target
    for token in tokens[:-1]:
        child = current.get(token)
        if child is None:
            child = {}
            current[token] = child
        if not isinstance(child, dict):
            return False
        current = child
    current[tokens[-1]] = value
    return True


def _request_draft(
    plan: dict[str, Any],
    asset: dict[str, Any],
    environment: dict[str, Any],
    credentials: dict[str, Any],
    contract_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    request = as_dict(plan.get("request_template"))
    bindings: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for collection in _REQUEST_COLLECTIONS:
        rows: list[dict[str, Any]] = []
        for slot in _dicts(request.get(collection)):
            binding, rows_unknown = _resolve_slot(plan, slot, asset, contract_id)
            rows.append(binding)
            bindings.append(binding)
            unknowns.extend(rows_unknown)
        grouped[collection] = rows
    media_type, media_unknowns = _media_type_binding(plan, asset, contract_id)
    unknowns.extend(media_unknowns)
    path = text(request.get("path_template"))
    for binding in grouped.get("path_parameters", []):
        field = text(binding.get("field"))
        token = _draft_token(binding)
        rendered = quote(str(token), safe="{}:_-")
        path = path.replace(f"{{{field}}}", rendered)
    query = [
        {"field": row.get("field"), "value": _draft_token(row)}
        for row in grouped.get("query_parameters", [])
    ]
    headers = [
        {"field": row.get("field"), "value": _draft_token(row), "sensitive": False}
        for row in grouped.get("header_parameters", [])
    ]
    body: dict[str, Any] = {}
    flat_body: list[dict[str, Any]] = []
    for row in grouped.get("body_fields", []):
        value = _draft_token(row)
        field = text(row.get("field"))
        nested = _set_nested(body, field, value)
        flat_body.append({"field": field, "value": value, "nested": nested})
    base_url = text(environment.get("base_url"))
    url_draft = f"{base_url.rstrip('/')}/{path.lstrip('/')}" if base_url and path else ""
    return (
        {
            "method": request.get("method"),
            "interface_id": request.get("interface_id"),
            "operation_id": request.get("operation_id"),
            "base_url": base_url,
            "path_draft": path,
            "url_draft": url_draft,
            "query_draft": query,
            "header_draft": headers,
            "cookie_draft": [
                {"field": row.get("field"), "value": _draft_token(row)}
                for row in grouped.get("cookie_parameters", [])
            ],
            "body_media_type": media_type,
            "body_draft": body,
            "body_field_drafts": flat_body,
            "form_field_drafts": [
                {"field": row.get("field"), "value": _draft_token(row)}
                for row in grouped.get("form_fields", [])
            ],
            "security_placeholders": _dicts(credentials.get("security_placeholders")),
            "required_slot_count": sum(
                1
                for collection in _REQUEST_COLLECTIONS
                for row in _dicts(request.get(collection))
                if bool(row.get("required"))
            ),
            "resolved_binding_count": sum(
                1
                for row in bindings
                if text(row.get("resolution_status")).startswith(("RESOLVED", "DEFERRED"))
            ),
            "draft_compiled": not any(bool(row.get("blocks_runtime_materialization")) for row in unknowns),
            "request_serialized": False,
            "request_sendable": False,
            "network_call_allowed": False,
        },
        bindings,
        unknowns,
    )


def _test_data_drafts(
    plan: dict[str, Any], asset: dict[str, Any], contract_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    drafts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    catalog = _input_catalog(asset)
    for requirement in _dicts(plan.get("test_data_setup_templates")):
        field = text(requirement.get("field") or requirement.get("field_candidate"))
        slot_ref = text(requirement.get("slot_ref"))
        matches = [
            row
            for row in catalog
            if (
                text(row.get("runtime_plan_ref") or row.get("plan_ref"))
                in {"", text(plan.get("plan_id"))}
            )
            and (
                (slot_ref and text(row.get("slot_ref") or row.get("source_slot_ref")) == slot_ref)
                or (field and _norm(row.get("field") or row.get("field_path")) == _norm(field))
            )
        ]
        if len(matches) != 1 or not _approved(matches[0]):
            reason = (
                "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_MISSING"
                if not matches
                else "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_AMBIGUOUS"
                if len(matches) > 1
                else "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_NOT_APPROVED"
            )
            unknowns.append(
                _unknown(
                    contract_id,
                    reason,
                    details={"slot_id": slot_ref, "field": field, "candidate_count": len(matches)},
                )
            )
            drafts.append(
                {
                    "slot_ref": slot_ref,
                    "field": field,
                    "requirement_kind": requirement.get("requirement_kind"),
                    "resolution_status": "UNRESOLVED",
                    "setup_executed": False,
                }
            )
            continue
        binding = matches[0]
        drafts.append(
            {
                "slot_ref": slot_ref,
                "field": field,
                "requirement_kind": requirement.get("requirement_kind"),
                "fixture_ref": binding.get("fixture_ref"),
                "entity_ref": binding.get("entity_ref"),
                "value_ref": binding.get("value_ref"),
                "binding_ref": binding.get("binding_id"),
                "resolution_status": "RESOLVED_TEST_DATA_REFERENCE",
                "setup_executed": False,
                "database_query_executed": False,
            }
        )
    return drafts, unknowns


def _identity_binding(bindings: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        row
        for row in bindings
        if (
            text(row.get("binding_kind"))
            in {"APPROVED_FIXTURE_VALUE_REFERENCE", "EXPLICIT_VALUE_REFERENCE", "APPROVED_FIXTURE_LITERAL"}
            and (
                text(row.get("location")) == "PATH"
                or re.search(r"(?:^id$|_id$|编号$|单号$|编码$)", text(row.get("field")), re.I)
            )
        )
    ]
    return candidates[0] if len(candidates) == 1 else {}


def _assertion_drafts(
    plan: dict[str, Any], bindings: list[dict[str, Any]], contract_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    drafts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    identity = _identity_binding(bindings)
    for template in _dicts(as_dict(plan.get("oracle_query_templates")).get("templates")):
        kind = text(template.get("template_kind"))
        if kind == "DATABASE_FIELD_SNAPSHOT":
            if not identity:
                unknowns.append(
                    _unknown(
                        contract_id,
                        "RUNTIME_MATERIALIZATION_ENTITY_IDENTITY_BINDING_UNRESOLVED",
                        details={"oracle_template_ref": template.get("template_id")},
                    )
                )
            drafts.append(
                {
                    "draft_id": stable_id("assertion_draft", contract_id, template.get("template_id")),
                    "draft_kind": "DATABASE_SNAPSHOT_QUERY_AST",
                    "phase": template.get("phase"),
                    "table_ref": template.get("table_ref"),
                    "table": template.get("table"),
                    "select_fields": [template.get("field")],
                    "entity_identity_binding_ref": identity.get("slot_id"),
                    "entity_identity_field": identity.get("field"),
                    "query_ast_compiled": bool(identity),
                    "sql_compiled": False,
                    "database_connection_opened": False,
                    "assertion_executable": False,
                }
            )
        elif kind == "HTTP_RESPONSE_CAPTURE":
            drafts.append(
                {
                    "draft_id": stable_id("assertion_draft", contract_id, template.get("template_id")),
                    "draft_kind": "HTTP_RESPONSE_SEMANTIC_ASSERTION_DRAFT",
                    "phase": template.get("phase"),
                    "permission_decision_requirement": template.get("permission_decision_requirement"),
                    "declared_response_contracts": _dicts(template.get("declared_response_contracts")),
                    "capture_status": bool(template.get("capture_status")),
                    "capture_headers": bool(template.get("capture_headers")),
                    "capture_body": bool(template.get("capture_body")),
                    "expected_http_status_resolved": False,
                    "jsonpath_assertion_compiled": False,
                    "assertion_executable": False,
                }
            )
    return drafts, unknowns


def _cleanup_draft(
    plan: dict[str, Any],
    asset: dict[str, Any],
    environment: dict[str, Any],
    contract_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    template = as_dict(plan.get("cleanup_step_templates"))
    if not bool(template.get("write_action")):
        return {
            "write_action": False,
            "strategy": "NO_CLEANUP_REQUIRED",
            "cleanup_binding_resolved": True,
            "cleanup_executable": False,
            "cleanup_executed": False,
        }, []
    steps = _dicts(template.get("steps"))
    operation_candidates = unique_text(
        candidate
        for step in steps
        for candidate in as_list(step.get("operation_candidates"))
    )
    unknowns: list[dict[str, Any]] = []
    if operation_candidates:
        bindings = _collect_rows(asset, ("cleanup_bindings", "compensation_bindings"))
        matches = [
            row
            for row in bindings
            if text(row.get("candidate") or row.get("compensation")) in operation_candidates
            and _approved(row)
        ]
        if len(matches) != 1:
            reason = (
                "RUNTIME_MATERIALIZATION_CLEANUP_BINDING_MISSING"
                if not matches
                else "RUNTIME_MATERIALIZATION_CLEANUP_BINDING_AMBIGUOUS"
            )
            unknowns.append(
                _unknown(
                    contract_id,
                    reason,
                    details={"operation_candidates": operation_candidates, "candidate_count": len(matches)},
                )
            )
            selected: dict[str, Any] = {}
        else:
            selected = matches[0]
        return {
            "write_action": True,
            "strategy": "SOURCE_BACKED_COMPENSATION_BINDING",
            "operation_candidates": operation_candidates,
            "selected_compensation": selected.get("candidate") or selected.get("compensation"),
            "cleanup_interface_ref": selected.get("interface_id"),
            "cleanup_binding_ref": selected.get("binding_id"),
            "same_entity_identity_required": True,
            "cleanup_binding_resolved": bool(selected),
            "cleanup_executable": False,
            "cleanup_executed": False,
            "cleanup_verification_executed": False,
        }, unknowns
    capabilities = {text(value).upper() for value in as_list(environment.get("capabilities"))}
    reset_ref = text(environment.get("reset_ref"))
    sandbox_ready = bool(capabilities & {"DISPOSABLE", "RESETTABLE"} or reset_ref)
    if not sandbox_ready:
        unknowns.append(
            _unknown(
                contract_id,
                "RUNTIME_MATERIALIZATION_SAFE_CLEANUP_CAPABILITY_UNRESOLVED",
                details={"environment_ref": environment.get("environment_ref")},
            )
        )
    return {
        "write_action": True,
        "strategy": "ISOLATED_SANDBOX_RESET",
        "environment_ref": environment.get("environment_ref"),
        "environment_capabilities": sorted(capabilities),
        "reset_ref": reset_ref,
        "cleanup_binding_resolved": sandbox_ready,
        "automatic_database_deletion_allowed": False,
        "cleanup_executable": False,
        "cleanup_executed": False,
        "cleanup_verification_executed": False,
    }, unknowns


def _compile_materialization(
    plan: dict[str, Any], asset: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contract_id = stable_id("runtime_materialization", plan.get("plan_id"), plan.get("scenario_ref"))
    environment, environment_unknowns = _environment_binding(plan, asset, contract_id)
    credentials, credential_unknowns = _credential_binding(plan, contract_id)
    request, bindings, request_unknowns = _request_draft(
        plan, asset, environment, credentials, contract_id
    )
    test_data, test_data_unknowns = _test_data_drafts(plan, asset, contract_id)
    assertions, assertion_unknowns = _assertion_drafts(plan, bindings, contract_id)
    cleanup, cleanup_unknowns = _cleanup_draft(plan, asset, environment, contract_id)
    unknowns = [
        *environment_unknowns,
        *credential_unknowns,
        *request_unknowns,
        *test_data_unknowns,
        *assertion_unknowns,
        *cleanup_unknowns,
    ]
    critical = [row for row in unknowns if bool(row.get("blocks_runtime_materialization"))]
    evidence = dedupe_evidence(as_list(plan.get("evidence")))
    if not evidence:
        row = _unknown(contract_id, "RUNTIME_MATERIALIZATION_SOURCE_EVIDENCE_MISSING")
        unknowns.append(row)
        critical.append(row)
    contract = {
        "schema": RUNTIME_MATERIALIZATION_SCHEMA,
        "materialization_id": contract_id,
        "runtime_plan_ref": plan.get("plan_id"),
        "execution_contract_ref": plan.get("execution_contract_ref"),
        "scenario_ref": plan.get("scenario_ref"),
        "behavior_ref": plan.get("behavior_ref"),
        "implementation_binding_ref": plan.get("implementation_binding_ref"),
        "environment_binding": environment,
        "credential_binding": credentials,
        "request_value_bindings": bindings,
        "test_data_setup_drafts": test_data,
        "request_draft": request,
        "assertion_drafts": assertions,
        "cleanup_draft": cleanup,
        "evidence": evidence,
        "unresolved_materialization_semantics": unique_text(
            row.get("reason_code") for row in unknowns
        ),
        "status": "INCOMPLETE" if critical else "DRAFT_READY",
        "formal_runtime_materialization": not bool(critical),
        "execution_allowed": False,
        "request_sendable": False,
        "request_serialized": False,
        "network_calls_allowed": False,
        "secret_values_loaded": False,
        "credential_injection_executed": False,
        "generators_executed": False,
        "test_data_setup_executed": False,
        "database_queries_executable": False,
        "assertions_executable": False,
        "snapshots_materialized": False,
        "cleanup_executable": False,
        "cleanup_executed": False,
        "bug_classification_allowed": False,
    }
    return contract, unknowns


def build_runtime_materializations_v1(
    asset: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    upstream = as_dict(asset.get("runtime_plan_gate"))
    plans = _dicts(asset.get("runtime_plans") or model.get("runtime_plans"))
    if not bool(upstream.get("entry_allowed")):
        return [], [], {
            "schema": RUNTIME_MATERIALIZATION_GATE_SCHEMA,
            "status": "BLOCKED_RUNTIME_MATERIALIZATION_UPSTREAM_PLAN_GATE",
            "entry_allowed": False,
            "runtime_materialization_ready": False,
            "execution_allowed": False,
            "upstream_runtime_plan_status": text(upstream.get("status")) or "NOT_BUILT",
            "metrics": {
                "runtime_materialization_count": 0,
                "ready_runtime_materialization_count": 0,
                "incomplete_runtime_materialization_count": 0,
            },
            "quality_claim": "MATERIALIZATION_NOT_BUILT_WHEN_RUNTIME_PLAN_GATE_CLOSED",
        }
    ready_plans = [row for row in plans if text(row.get("status")) == "TEMPLATE_READY"]
    contracts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for plan in ready_plans:
        contract, rows = _compile_materialization(plan, asset)
        contracts.append(contract)
        unknowns.extend(rows)
    contracts = list(
        {
            text(row.get("materialization_id")): row
            for row in contracts
            if text(row.get("materialization_id"))
        }.values()
    )
    unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if text(row.get("unknown_id"))
        }.values()
    )
    ready = sum(1 for row in contracts if text(row.get("status")) == "DRAFT_READY")
    incomplete = sum(1 for row in contracts if text(row.get("status")) == "INCOMPLETE")
    covered = {text(row.get("runtime_plan_ref")) for row in contracts}
    if incomplete or len(covered) < len(ready_plans):
        status = "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    elif contracts:
        status = "PASS"
    else:
        status = "NO_RUNTIME_MATERIALIZATION_COMPILED"
    gate = {
        "schema": RUNTIME_MATERIALIZATION_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "runtime_materialization_ready": status == "PASS",
        "execution_allowed": False,
        "upstream_runtime_plan_status": upstream.get("status"),
        "metrics": {
            "runtime_materialization_count": len(contracts),
            "ready_runtime_materialization_count": ready,
            "incomplete_runtime_materialization_count": incomplete,
            "covered_runtime_plan_count": len(covered),
            "ready_runtime_plan_count": len(ready_plans),
            "request_value_binding_count": sum(
                len(_dicts(row.get("request_value_bindings"))) for row in contracts
            ),
            "test_data_setup_draft_count": sum(
                len(_dicts(row.get("test_data_setup_drafts"))) for row in contracts
            ),
            "assertion_draft_count": sum(
                len(_dicts(row.get("assertion_drafts"))) for row in contracts
            ),
            "runtime_materialization_unknown_count": len(unknowns),
        },
        "request_sendable": False,
        "network_calls_allowed": False,
        "secret_values_loaded": False,
        "database_queries_executable": False,
        "assertions_executable": False,
        "cleanup_executable": False,
        "bug_classification_allowed": False,
        "quality_claim": "AUDITABLE_DRAFT_CLOSURE_NOT_EXECUTION_READINESS_OR_BUG_FINDING",
    }
    return contracts, unknowns, gate


def _relationships(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for contract in contracts:
        materialization_id = text(contract.get("materialization_id"))
        plan_id = text(contract.get("runtime_plan_ref"))
        accepted = text(contract.get("status")) == "DRAFT_READY"
        if materialization_id and plan_id:
            rows.append(
                {
                    "edge_id": stable_id(
                        "edge", "runtime_plan_to_materialization", plan_id, materialization_id
                    ),
                    "from": plan_id,
                    "to": materialization_id,
                    "relation": "runtime_plan_to_materialization",
                    "status": "accepted" if accepted else "candidate",
                    "confidence": 1.0 if accepted else 0.0,
                    "derivation": "runtime_materialization_compiler",
                    "evidence": {"execution_allowed": False},
                }
            )
    return rows


def project_runtime_materializations_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, model)
    relationships = _relationships(contracts)
    evidence = dedupe_evidence(
        [
            row
            for contract in contracts
            for row in as_list(contract.get("evidence"))
            if isinstance(row, dict)
        ]
    )
    asset["runtime_materializations"] = contracts
    asset["runtime_materialization_unknowns"] = unknowns
    asset["runtime_materialization_evidence_index"] = evidence
    asset["runtime_materialization_relationships"] = relationships
    asset["runtime_materialization_gate"] = gate
    asset["relationships"] = list(
        {
            text(row.get("edge_id")): dict(row)
            for row in [*as_list(asset.get("relationships")), *relationships]
            if isinstance(row, dict) and text(row.get("edge_id"))
        }.values()
    )
    model["runtime_materializations"] = contracts
    model["runtime_materialization_unknowns"] = unknowns
    model["runtime_materialization_evidence_index"] = evidence
    model["runtime_materialization_relationships"] = relationships
    model["runtime_materialization_gate"] = gate
    metrics = as_dict(gate.get("metrics"))
    projected = {
        "runtime_materialization_status": gate.get("status"),
        "runtime_materialization_ready": bool(gate.get("entry_allowed")),
        "runtime_materialization_count": int(
            metrics.get("runtime_materialization_count") or 0
        ),
        "runtime_materialization_incomplete_count": int(
            metrics.get("incomplete_runtime_materialization_count") or 0
        ),
        "runtime_materialization_unknown_count": len(unknowns),
        "runtime_materialization_relationship_count": len(relationships),
        "materialized_execution_allowed": False,
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
    gap_kinds = {
        "RUNTIME_MATERIALIZATION_UPSTREAM_BLOCKED",
        "RUNTIME_MATERIALIZATION_INCOMPLETE",
        "RUNTIME_MATERIALIZATION_NOT_COMPILED",
    }
    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) not in gap_kinds
    ]
    status = text(gate.get("status"))
    if status != "PASS":
        if status == "BLOCKED_RUNTIME_MATERIALIZATION_UPSTREAM_PLAN_GATE":
            kind = "RUNTIME_MATERIALIZATION_UPSTREAM_BLOCKED"
        elif status == "NO_RUNTIME_MATERIALIZATION_COMPILED":
            kind = "RUNTIME_MATERIALIZATION_NOT_COMPILED"
        else:
            kind = "RUNTIME_MATERIALIZATION_INCOMPLETE"
        gaps.append(
            {
                "kind": kind,
                "gap_type": "runtime_materialization_draft_not_closed",
                "source_id": "*",
                "runtime_materialization_status": status,
                "runtime_materialization_metrics": dict(metrics),
                "execution_allowed": False,
                "operator_action": (
                    "bind an explicit non-production environment, credential refs, required "
                    "runtime values, approved test data, entity identity and safe cleanup; "
                    "do not insert sample values or secret material"
                ),
            }
        )
    asset["coverage_gaps"] = gaps
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_materialization_v1_enabled": True,
            "runtime_materialization_requires_runtime_plan_gate": True,
            "runtime_materialization_source_literals_must_be_non_secret": True,
            "runtime_materialization_dynamic_values_require_explicit_binding": True,
            "runtime_materialization_uses_credential_refs_only": True,
            "runtime_materialization_plaintext_credentials_allowed": False,
            "runtime_materialization_write_requires_non_production_environment": True,
            "runtime_materialization_write_requires_safe_cleanup": True,
            "runtime_materialization_requests_are_not_sendable": True,
            "runtime_materialization_assertions_are_not_executable": True,
            "runtime_materialization_does_not_enable_bug_classification": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "RUNTIME_MATERIALIZATION_SCHEMA",
    "RUNTIME_MATERIALIZATION_GATE_SCHEMA",
    "build_runtime_materializations_v1",
    "project_runtime_materializations_to_asset",
]
