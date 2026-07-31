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
_MATERIALIZATION_GAP_KINDS = {
    "RUNTIME_MATERIALIZATION_UPSTREAM_BLOCKED",
    "RUNTIME_MATERIALIZATION_INCOMPLETE",
    "RUNTIME_MATERIALIZATION_NOT_COMPILED",
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
) -> tuple[dict[str, Any], set[str]]:
    updated = dict(materialization)
    environment = dict(as_dict(updated.get("environment_binding")))
    plan = _plan_index(asset).get(text(updated.get("runtime_plan_ref"))) or {}
    root = _root_environment_metadata(asset)
    expected_ref = text(environment.get("environment_ref") or root.get("environment_ref"))
    current_url = text(environment.get("base_url"))
    if current_url and expected_ref:
        return updated, set()
    candidates = _connector_candidates(asset, plan, expected_ref)
    if len(candidates) != 1:
        return updated, set()
    connector = candidates[0]
    base_url = text(
        connector.get("endpoint_ref") or connector.get("base_url") or connector.get("url")
    )
    merged_ref = expected_ref or text(connector.get("environment_ref"))
    if not base_url or not merged_ref:
        return updated, set()
    merged = {
        **environment,
        "environment_ref": merged_ref,
        "base_url": base_url,
        "environment_kind": environment.get("environment_kind")
        or root.get("environment_kind"),
        "is_production": root.get("is_production"),
        "capabilities": unique_text(
            [
                *as_list(environment.get("capabilities")),
                *as_list(root.get("capabilities")),
                *as_list(connector.get("capabilities")),
            ]
        ),
        "reset_ref": environment.get("reset_ref")
        or root.get("reset_ref")
        or connector.get("reset_ref"),
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
    request.update(
        {
            "base_url": base_url,
            "url_draft": f"{base_url.rstrip('/')}/{path.lstrip('/')}" if path else "",
            "request_sendable": False,
            "network_call_allowed": False,
        }
    )
    updated["request_draft"] = request
    removed = set(_ENVIRONMENT_UNKNOWN_KINDS)
    if bool(merged.get("non_production_proven")):
        removed.add("RUNTIME_MATERIALIZATION_NON_PRODUCTION_ENVIRONMENT_UNPROVEN")
    return updated, removed


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
        and not text(row.get("resolution_status")).startswith(
            ("UNRESOLVED", "BLOCKED", "AMBIGUOUS")
        )
    ]
    return rows[0] if len(rows) == 1 else {}


def _repair_identity_assertions(
    materialization: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
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
            row.update(
                {
                    "entity_identity_binding_ref": identity.get("slot_id"),
                    "entity_identity_field": identity.get("field"),
                    "query_ast_compiled": True,
                    "sql_compiled": False,
                    "assertion_executable": False,
                }
            )
            changed = True
        assertions.append(row)
    updated["assertion_drafts"] = assertions
    return updated, changed


def _repair_cleanup(materialization: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    updated = dict(materialization)
    cleanup = dict(as_dict(updated.get("cleanup_draft")))
    environment = as_dict(updated.get("environment_binding"))
    if not bool(cleanup.get("write_action")) or bool(cleanup.get("cleanup_binding_resolved")):
        return updated, False
    if text(cleanup.get("strategy")) != "ISOLATED_SANDBOX_RESET":
        return updated, False
    capabilities = {
        text(value).upper() for value in as_list(environment.get("capabilities"))
    }
    reset_ref = text(environment.get("reset_ref"))
    ready = bool(capabilities & {"DISPOSABLE", "RESETTABLE"} or reset_ref)
    if not ready:
        return updated, False
    cleanup.update(
        {
            "environment_ref": environment.get("environment_ref"),
            "environment_capabilities": sorted(capabilities),
            "reset_ref": reset_ref,
            "cleanup_binding_resolved": True,
            "automatic_database_deletion_allowed": False,
            "cleanup_executable": False,
            "cleanup_executed": False,
            "cleanup_verification_executed": False,
        }
    )
    updated["cleanup_draft"] = cleanup
    return updated, True


def _missing_security_credential(materialization: dict[str, Any]) -> bool:
    credentials = as_dict(materialization.get("credential_binding"))
    placeholders = _dicts(credentials.get("security_placeholders"))
    slots = _dicts(credentials.get("credential_slots"))
    return bool(placeholders) and not any(
        text(row.get("credential_ref")) for row in slots
    )


def _blocking_unknowns_by_contract(
    unknowns: list[dict[str, Any]], contract_id: str
) -> list[dict[str, Any]]:
    return [
        row
        for row in unknowns
        if text(row.get("runtime_materialization_ref")) == contract_id
        and bool(row.get("blocks_runtime_materialization"))
    ]


def _rebuild_gate(asset: dict[str, Any], model: dict[str, Any]) -> None:
    rows = _dicts(asset.get("runtime_materializations"))
    unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    rebuilt: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        materialization_id = text(row.get("materialization_id"))
        contract_unknowns = [
            item
            for item in unknowns
            if text(item.get("runtime_materialization_ref")) == materialization_id
        ]
        blocked = bool(_blocking_unknowns_by_contract(unknowns, materialization_id))
        row.update(
            {
                "status": "INCOMPLETE" if blocked else "DRAFT_READY",
                "formal_runtime_materialization": not blocked,
                "unresolved_materialization_semantics": unique_text(
                    item.get("reason_code") for item in contract_unknowns
                ),
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
        )
        request = dict(as_dict(row.get("request_draft")))
        request["draft_compiled"] = not blocked
        request["request_serialized"] = False
        request["request_sendable"] = False
        request["network_call_allowed"] = False
        row["request_draft"] = request
        rebuilt.append(row)
    asset["runtime_materializations"] = rebuilt
    model["runtime_materializations"] = [dict(row) for row in rebuilt]
    ready = sum(1 for row in rebuilt if text(row.get("status")) == "DRAFT_READY")
    incomplete = sum(1 for row in rebuilt if text(row.get("status")) == "INCOMPLETE")
    previous_status = text(
        as_dict(asset.get("runtime_materialization_gate")).get("status")
    )
    status = (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
        if incomplete
        else "PASS"
        if rebuilt
        else previous_status or "NO_RUNTIME_MATERIALIZATION_COMPILED"
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

    accepted = {
        text(row.get("materialization_id"))
        for row in rebuilt
        if text(row.get("status")) == "DRAFT_READY"
    }
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
        row
        for row in relationships
        if text(row.get("relation")) == "runtime_plan_to_materialization"
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

    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) not in _MATERIALIZATION_GAP_KINDS
    ]
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


def project_governed_runtime_materializations_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Compile drafts, merge split metadata and preserve fail-close semantics."""
    project_runtime_materializations_to_asset(asset, model)
    original_unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    governed: list[dict[str, Any]] = []
    removed_by_contract: dict[str, set[str]] = {}
    added: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_materializations")):
        row, removed = _resolve_split_environment(asset, raw)
        row, identity_repaired = _repair_identity_assertions(row)
        row, cleanup_repaired = _repair_cleanup(row)
        materialization_id = text(row.get("materialization_id"))
        if identity_repaired:
            removed.add("RUNTIME_MATERIALIZATION_ENTITY_IDENTITY_BINDING_UNRESOLVED")
        if cleanup_repaired:
            removed.add("RUNTIME_MATERIALIZATION_SAFE_CLEANUP_CAPABILITY_UNRESOLVED")
        plan = _plan_index(asset).get(text(row.get("runtime_plan_ref"))) or {}
        write = bool(as_dict(plan.get("cleanup_step_templates")).get("write_action"))
        environment = as_dict(row.get("environment_binding"))
        if write and not bool(environment.get("non_production_proven")):
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
                    "environment_ref": environment.get("environment_ref"),
                    "blocks_runtime_materialization": True,
                    "execution_allowed": False,
                }
            )
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
        for row in original_unknowns
        if text(row.get("reason_code"))
        not in removed_by_contract.get(
            text(row.get("runtime_materialization_ref")), set()
        )
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
            "runtime_materialization_split_sandbox_capability_may_repair_cleanup_draft": True,
            "runtime_materialization_stale_gaps_are_rebuilt": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["project_governed_runtime_materializations_to_asset"]
