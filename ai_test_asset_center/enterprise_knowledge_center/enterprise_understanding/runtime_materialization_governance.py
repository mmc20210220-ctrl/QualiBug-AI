"""Govern Runtime Materialization drafts without mutating source runtime metadata."""
from __future__ import annotations

from typing import Any

from .runtime_materialization import project_runtime_materializations_to_asset
from .schema import as_dict, as_list, stable_id, text, unique_text

_ENVIRONMENT_UNKNOWN_KINDS = {
    "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_UNRESOLVED",
    "RUNTIME_MATERIALIZATION_BASE_URL_UNRESOLVED",
    "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_AMBIGUOUS",
}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _root_environment_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    runtime = as_dict(asset.get("runtime_environment"))
    contract = as_dict(asset.get("environment_contract"))
    project = as_dict(asset.get("project_configuration"))
    return {
        "environment_ref": text(
            asset.get("environment_ref")
            or runtime.get("environment_ref")
            or contract.get("environment_ref")
            or project.get("environment_ref")
        ),
        "environment_kind": text(
            runtime.get("environment_kind")
            or runtime.get("environment_type")
            or contract.get("environment_kind")
            or contract.get("environment_type")
            or project.get("environment_kind")
        ).upper(),
        "is_production": (
            runtime.get("is_production")
            if "is_production" in runtime
            else contract.get("is_production")
            if "is_production" in contract
            else project.get("is_production")
        ),
        "capabilities": unique_text(
            [
                *as_list(runtime.get("capabilities")),
                *as_list(contract.get("capabilities")),
                *as_list(project.get("environment_capabilities")),
            ]
        ),
        "reset_ref": text(
            runtime.get("reset_ref")
            or contract.get("reset_ref")
            or project.get("reset_ref")
        ),
        "base_url": text(
            runtime.get("base_url")
            or runtime.get("endpoint_ref")
            or contract.get("base_url")
            or contract.get("endpoint_ref")
            or project.get("base_url")
            or project.get("endpoint_ref")
        ),
    }


def _enabled_connectors(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _dicts(asset.get("connectors"))
        if row.get("enabled") is not False
        and text(row.get("endpoint_ref") or row.get("base_url") or row.get("url"))
    ]
    return list(
        {
            stable_id(
                "runtime_connector_candidate",
                row.get("connector_id"),
                row.get("endpoint_ref") or row.get("base_url") or row.get("url"),
            ): row
            for row in rows
        }.values()
    )


def _plan_index(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }


def _connector_candidates(
    asset: dict[str, Any], plan: dict[str, Any], expected_ref: str
) -> list[dict[str, Any]]:
    connectors = _enabled_connectors(asset)
    interface_id = text(as_dict(plan.get("action_entry")).get("interface_id"))
    exact = [
        row
        for row in connectors
        if interface_id
        and interface_id
        in {
            text(row.get("interface_id")),
            text(row.get("external_ref")),
            text(row.get("connector_id")),
        }
    ]
    if exact:
        return exact
    ref_exact = [
        row
        for row in connectors
        if expected_ref and text(row.get("environment_ref")) == expected_ref
    ]
    if ref_exact:
        return ref_exact
    return connectors if len(connectors) == 1 else []


def _non_production(metadata: dict[str, Any]) -> bool:
    kind = text(metadata.get("environment_kind")).upper()
    return metadata.get("is_production") is False or kind in {
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


def _resolve_split_environment(
    asset: dict[str, Any], materialization: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    updated = dict(materialization)
    environment = dict(as_dict(updated.get("environment_binding")))
    plan = _plan_index(asset).get(text(updated.get("runtime_plan_ref"))) or {}
    root = _root_environment_metadata(asset)
    expected_ref = text(environment.get("environment_ref") or root.get("environment_ref"))
    if text(environment.get("base_url")):
        return updated, False
    candidates = _connector_candidates(asset, plan, expected_ref)
    if len(candidates) != 1:
        return updated, False
    connector = candidates[0]
    base_url = text(connector.get("endpoint_ref") or connector.get("base_url") or connector.get("url"))
    if not base_url:
        return updated, False
    merged = {
        **environment,
        "environment_ref": expected_ref or text(connector.get("environment_ref")),
        "base_url": base_url,
        "environment_kind": environment.get("environment_kind") or root.get("environment_kind"),
        "is_production": root.get("is_production"),
        "capabilities": unique_text(
            [
                *as_list(environment.get("capabilities")),
                *as_list(root.get("capabilities")),
                *as_list(connector.get("capabilities")),
            ]
        ),
        "reset_ref": environment.get("reset_ref") or root.get("reset_ref") or connector.get("reset_ref"),
        "environment_metadata_resolved": True,
        "base_url_resolved": True,
        "non_production_proven": _non_production({**root, **connector}),
        "derived_from_split_project_and_connector_metadata": True,
        "connector_ref": connector.get("connector_id"),
        "network_access_allowed": False,
        "environment_probe_executed": False,
    }
    updated["environment_binding"] = merged
    request = dict(as_dict(updated.get("request_draft")))
    path = text(request.get("path_draft"))
    request["base_url"] = base_url
    request["url_draft"] = f"{base_url.rstrip('/')}/{path.lstrip('/')}" if path else ""
    request["request_sendable"] = False
    request["network_call_allowed"] = False
    updated["request_draft"] = request
    return updated, True


def _identity_candidate(materialization: dict[str, Any]) -> dict[str, Any]:
    rows = [
        row
        for row in _dicts(materialization.get("request_value_bindings"))
        if text(row.get("location")) == "PATH"
        and (
            row.get("draft_value_present") is True
            or text(row.get("value_ref"))
            or text(row.get("placeholder"))
        )
        and not text(row.get("resolution_status")).startswith(("UNRESOLVED", "BLOCKED", "AMBIGUOUS"))
    ]
    return rows[0] if len(rows) == 1 else {}


def _repair_identity_assertions(materialization: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    updated = dict(materialization)
    identity = _identity_candidate(updated)
    if not identity:
        return updated, False
    changed = False
    assertions: list[dict[str, Any]] = []
    for raw in _dicts(updated.get("assertion_drafts")):
        row = dict(raw)
        if (
            text(row.get("draft_kind")) == "DATABASE_SNAPSHOT_QUERY_AST"
            and not text(row.get("entity_identity_binding_ref"))
        ):
            row["entity_identity_binding_ref"] = identity.get("slot_id")
            row["entity_identity_field"] = identity.get("field")
            row["query_ast_compiled"] = True
            row["sql_compiled"] = False
            row["assertion_executable"] = False
            changed = True
        assertions.append(row)
    updated["assertion_drafts"] = assertions
    return updated, changed


def _missing_security_credential(materialization: dict[str, Any]) -> bool:
    credentials = as_dict(materialization.get("credential_binding"))
    placeholders = _dicts(credentials.get("security_placeholders"))
    slots = _dicts(credentials.get("credential_slots"))
    return bool(placeholders) and not any(text(row.get("credential_ref")) for row in slots)


def _rebuild_gate(asset: dict[str, Any], model: dict[str, Any]) -> None:
    rows = _dicts(asset.get("runtime_materializations"))
    unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    blocking_by_contract = {
        text(row.get("runtime_materialization_ref"))
        for row in unknowns
        if bool(row.get("blocks_runtime_materialization"))
    }
    rebuilt: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        materialization_id = text(row.get("materialization_id"))
        blocked = materialization_id in blocking_by_contract
        row["status"] = "INCOMPLETE" if blocked else "DRAFT_READY"
        row["formal_runtime_materialization"] = not blocked
        row["execution_allowed"] = False
        row["request_sendable"] = False
        row["network_calls_allowed"] = False
        row["secret_values_loaded"] = False
        row["database_queries_executable"] = False
        row["assertions_executable"] = False
        row["cleanup_executable"] = False
        row["bug_classification_allowed"] = False
        rebuilt.append(row)
    asset["runtime_materializations"] = rebuilt
    model["runtime_materializations"] = [dict(row) for row in rebuilt]
    ready = sum(1 for row in rebuilt if text(row.get("status")) == "DRAFT_READY")
    incomplete = sum(1 for row in rebuilt if text(row.get("status")) == "INCOMPLETE")
    status = (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
        if incomplete
        else "PASS"
        if rebuilt
        else text(as_dict(asset.get("runtime_materialization_gate")).get("status"))
        or "NO_RUNTIME_MATERIALIZATION_COMPILED"
    )
    gate = dict(as_dict(asset.get("runtime_materialization_gate")))
    gate.update(
        {
            "status": status,
            "entry_allowed": status == "PASS",
            "runtime_materialization_ready": status == "PASS",
            "execution_allowed": False,
            "request_sendable": False,
            "network_calls_allowed": False,
            "secret_values_loaded": False,
            "database_queries_executable": False,
            "assertions_executable": False,
            "cleanup_executable": False,
            "bug_classification_allowed": False,
        }
    )
    metrics = dict(as_dict(gate.get("metrics")))
    metrics.update(
        {
            "runtime_materialization_count": len(rebuilt),
            "ready_runtime_materialization_count": ready,
            "incomplete_runtime_materialization_count": incomplete,
            "runtime_materialization_unknown_count": len(unknowns),
        }
    )
    gate["metrics"] = metrics
    asset["runtime_materialization_gate"] = gate
    model["runtime_materialization_gate"] = dict(gate)
    accepted = {text(row.get("materialization_id")) for row in rebuilt if text(row.get("status")) == "DRAFT_READY"}
    relationships: list[dict[str, Any]] = []
    for raw in as_list(asset.get("relationships")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if text(row.get("relation")) == "runtime_plan_to_materialization":
            ok = text(row.get("to")) in accepted
            row["status"] = "accepted" if ok else "candidate"
            row["confidence"] = 1.0 if ok else 0.0
        relationships.append(row)
    asset["relationships"] = relationships
    asset["runtime_materialization_relationships"] = [
        row for row in relationships if text(row.get("relation")) == "runtime_plan_to_materialization"
    ]
    model["runtime_materialization_relationships"] = [
        dict(row) for row in asset["runtime_materialization_relationships"]
    ]
    projected = {
        "runtime_materialization_status": status,
        "runtime_materialization_ready": status == "PASS",
        "runtime_materialization_count": len(rebuilt),
        "runtime_materialization_incomplete_count": incomplete,
        "runtime_materialization_unknown_count": len(unknowns),
        "runtime_materialization_relationship_count": len(
            asset["runtime_materialization_relationships"]
        ),
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


def project_governed_runtime_materializations_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Compile drafts, merge split metadata and preserve fail-close semantics."""
    project_runtime_materializations_to_asset(asset, model)
    unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    governed: list[dict[str, Any]] = []
    removed_by_contract: dict[str, set[str]] = {}
    added: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_materializations")):
        row, environment_repaired = _resolve_split_environment(asset, raw)
        row, identity_repaired = _repair_identity_assertions(row)
        materialization_id = text(row.get("materialization_id"))
        removed: set[str] = set()
        if environment_repaired:
            removed |= _ENVIRONMENT_UNKNOWN_KINDS
            plan = _plan_index(asset).get(text(row.get("runtime_plan_ref"))) or {}
            write = bool(as_dict(plan.get("cleanup_step_templates")).get("write_action"))
            if write and not bool(as_dict(row.get("environment_binding")).get("non_production_proven")):
                added.append(
                    {
                        "unknown_id": stable_id(
                            "runtime_materialization_unknown",
                            materialization_id,
                            "RUNTIME_MATERIALIZATION_NON_PRODUCTION_ENVIRONMENT_UNPROVEN",
                        ),
                        "kind": "RUNTIME_MATERIALIZATION_NON_PRODUCTION_ENVIRONMENT_UNPROVEN",
                        "reason_code": "RUNTIME_MATERIALIZATION_NON_PRODUCTION_ENVIRONMENT_UNPROVEN",
                        "runtime_materialization_ref": materialization_id,
                        "environment_ref": as_dict(row.get("environment_binding")).get("environment_ref"),
                        "blocks_runtime_materialization": True,
                        "execution_allowed": False,
                    }
                )
        if identity_repaired:
            removed.add("RUNTIME_MATERIALIZATION_ENTITY_IDENTITY_BINDING_UNRESOLVED")
        if _missing_security_credential(row):
            added.append(
                {
                    "unknown_id": stable_id(
                        "runtime_materialization_unknown",
                        materialization_id,
                        "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED",
                    ),
                    "kind": "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED",
                    "reason_code": "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED",
                    "runtime_materialization_ref": materialization_id,
                    "blocks_runtime_materialization": True,
                    "execution_allowed": False,
                }
            )
        removed_by_contract[materialization_id] = removed
        governed.append(row)
    filtered = [
        row
        for row in unknowns
        if text(row.get("reason_code"))
        not in removed_by_contract.get(text(row.get("runtime_materialization_ref")), set())
    ]
    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in [*filtered, *added]
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_materializations"] = governed
    asset["runtime_materialization_unknowns"] = all_unknowns
    model["runtime_materializations"] = [dict(row) for row in governed]
    model["runtime_materialization_unknowns"] = [dict(row) for row in all_unknowns]
    _rebuild_gate(asset, model)
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_materialization_split_environment_metadata_governed": True,
            "runtime_materialization_unique_connector_derivation_allowed": True,
            "runtime_materialization_multiple_connector_guessing_allowed": False,
            "runtime_materialization_path_identity_may_feed_snapshot_ast": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["project_governed_runtime_materializations_to_asset"]
