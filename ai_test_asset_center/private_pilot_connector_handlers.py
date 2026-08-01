"""Private-pilot HTTP surface for enterprise knowledge connectors.

The HTTP layer owns authentication, public projection, and request shaping only. Trusted sync,
acceptance jobs, fenced configuration, checkpoint validation, automatic refresh, and retry policy
live in connector application services. Raw credentials, source content, cursors, report paths, and
remote-resource identities are never returned through lifecycle projections.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .connector_auto_sync import (
    connector_auto_sync_status,
    run_managed_connector_sync,
    run_managed_feishu_sync,
    test_managed_connector_connection,
    test_managed_feishu_connection,
)
from .connector_registry import (
    ConnectorRegistryError,
    build_default_connector_registry,
)
from .connector_configuration_service import (
    configure_managed_connector,
    configure_managed_feishu_connector,
    set_managed_connector_status,
)
from .connector_connection_profiles import (
    ConnectorProfileError,
    MASKED_SECRET,
    list_connector_connection_profiles,
    mark_connector_reauthorization_required,
)
from .connector_sync_authority import (
    ConnectorSyncError,
    list_connector_instances,
    list_connector_sync_runs,
    load_connector_sync_run,
)
from .feishu_connector_adapter import FeishuConnectorError
from .feishu_tenant_acceptance_jobs import (
    FeishuTenantAcceptanceJobError,
    get_current_feishu_tenant_acceptance_job,
    get_feishu_tenant_acceptance_job,
    start_feishu_tenant_acceptance_job,
)
from .feishu_tenant_acceptance_reports import (
    FeishuTenantAcceptanceReportError,
    latest_feishu_tenant_acceptance_summary,
    list_feishu_tenant_acceptance_reports,
    load_feishu_tenant_acceptance_report,
)
from .enterprise_knowledge_center import list_enterprise_knowledge_sources
from .real_project_onboarding import _safe_project_id

_ROUTE_MARKER = "knowledge-connectors"
_DEFAULT_MANAGED_FEISHU_SYNC = run_managed_feishu_sync
_DEFAULT_TEST_MANAGED_FEISHU_CONNECTION = test_managed_feishu_connection
_PRIVATE_CONNECTOR_FIELDS = {
    "fencing_generation",
    "last_fencing_token_issued_at_utc",
    "last_fencing_token_issued_by",
    "fencing_takeover_pending",
    "last_committed_cursor_fingerprint",
}
_PRIVATE_SYNC_RESPONSE_FIELDS = {
    "fencing_token",
    "previous_fencing_token",
    "takeover_attempt_id",
    "next_cursor",
}


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _service():
    from . import private_pilot_service as service

    return service


def _managed_sync_runner():
    """Use the generic dispatcher; retain the Feishu alias only for embedders/tests."""
    if run_managed_feishu_sync is not _DEFAULT_MANAGED_FEISHU_SYNC:
        return run_managed_feishu_sync
    return run_managed_connector_sync


def _managed_connection_tester():
    if (
        test_managed_feishu_connection
        is not _DEFAULT_TEST_MANAGED_FEISHU_CONNECTION
    ):
        return test_managed_feishu_connection
    return test_managed_connector_connection


def _connector_route(path: str) -> tuple[str, list[str]] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) < 5
        or parts[:3] != ["api", "v1", "projects"]
        or parts[4] != _ROUTE_MARKER
    ):
        return None
    return parts[3], parts[5:]


def _connector_type_route(path: str) -> list[str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if parts[:3] != ["api", "v1", "connector-types"] or len(parts) > 4:
        return None
    return parts[3:]


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    parsed = int(value if value not in (None, "") else default)
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"connector integer option must be between {minimum} and {maximum}"
        )
    return parsed


def _bounded_float(
    value: Any,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    parsed = float(value if value not in (None, "") else default)
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"connector numeric option must be between {minimum} and {maximum}"
        )
    return parsed


def _optional_bounded_int(
    value: Any,
    minimum: int,
    maximum: int,
) -> int | None:
    if value in (None, ""):
        return None
    return _bounded_int(value, minimum, minimum, maximum)


def _optional_bounded_float(
    value: Any,
    minimum: float,
    maximum: float,
) -> float | None:
    if value in (None, ""):
        return None
    return _bounded_float(value, minimum, minimum, maximum)


def _profile_index(project: str, root: Path) -> dict[str, dict[str, Any]]:
    payload = list_connector_connection_profiles(project, root=root)
    return {
        _text(row.get("connector_instance_id"), 160): dict(row)
        for row in payload.get("profiles") or []
        if isinstance(row, dict)
    }


def _public_connector_instance(value: dict[str, Any]) -> dict[str, Any]:
    row = dict(value or {})
    for field in _PRIVATE_CONNECTOR_FIELDS:
        row.pop(field, None)
    row["fencing_token_returned_to_client"] = False
    return row


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _remote_lifecycle_projection(run: dict[str, Any]) -> dict[str, Any]:
    raw = run.get("remote_lifecycle")
    lifecycle = dict(raw) if isinstance(raw, dict) else {}
    status = _text(
        lifecycle.get("status") or run.get("remote_lifecycle_status"), 80
    ) or "NOT_AVAILABLE"
    receipt_value = lifecycle.get("sync_receipt_persisted")
    receipt_persisted = (
        receipt_value if isinstance(receipt_value, bool) else None
    )
    return {
        "status": status,
        "authoritative_snapshot_complete": (
            lifecycle.get("authoritative_snapshot_complete") is True
        ),
        "present_count": _safe_int(lifecycle.get("present_count")),
        "absent_count": _safe_int(
            lifecycle.get("absent_count")
            if "absent_count" in lifecycle
            else run.get("remote_absent_count")
        ),
        "unconfirmed_missing_count": _safe_int(
            lifecycle.get("unconfirmed_missing_count")
            if "unconfirmed_missing_count" in lifecycle
            else run.get("remote_unconfirmed_missing_count")
        ),
        "retirement_eligible_count": _safe_int(
            lifecycle.get("retirement_eligible_count")
            if "retirement_eligible_count" in lifecycle
            else run.get("remote_retirement_eligible_count")
        ),
        "retired_count": _safe_int(
            lifecycle.get("retired_count")
            if "retired_count" in lifecycle
            else run.get("retired_count")
        ),
        "renamed_resource_count": _safe_int(
            lifecycle.get("renamed_resource_count")
            if "renamed_resource_count" in lifecycle
            else run.get("renamed_resource_count")
        ),
        "moved_resource_count": _safe_int(
            lifecycle.get("moved_resource_count")
            if "moved_resource_count" in lifecycle
            else run.get("moved_resource_count")
        ),
        "reappeared_resource_count": _safe_int(
            lifecycle.get("reappeared_resource_count")
            if "reappeared_resource_count" in lifecycle
            else run.get("reappeared_resource_count")
        ),
        "retire_after_complete_snapshots": _safe_int(
            lifecycle.get("retire_after_complete_snapshots")
        ),
        "requested_deletion_policy": _text(
            lifecycle.get("requested_deletion_policy")
            or run.get("requested_deletion_policy"),
            40,
        ),
        "effective_deletion_policy": _text(
            lifecycle.get("effective_deletion_policy")
            or run.get("effective_deletion_policy"),
            80,
        ),
        "absence_interpretation": _text(
            lifecycle.get("absence_interpretation"), 120
        ),
        "sync_receipt_persisted": receipt_persisted,
        "evidence_persistence_status": _text(
            lifecycle.get("evidence_persistence_status"), 40
        ),
        "remote_deletion_inferred": False,
        "permission_loss_inferred": False,
        "historical_source_bytes_retained": True,
        "customer_material_mutation_executed": False,
        "remote_resource_identities_returned": False,
        "source_refs_returned": False,
    }


def _empty_lifecycle(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "authoritative_snapshot_complete": False,
        "present_count": 0,
        "absent_count": 0,
        "unconfirmed_missing_count": 0,
        "retirement_eligible_count": 0,
        "retired_count": 0,
        "renamed_resource_count": 0,
        "moved_resource_count": 0,
        "reappeared_resource_count": 0,
        "retire_after_complete_snapshots": 0,
        "requested_deletion_policy": "",
        "effective_deletion_policy": "",
        "absence_interpretation": "",
        "sync_receipt_persisted": None,
        "evidence_persistence_status": "",
        "remote_deletion_inferred": False,
        "permission_loss_inferred": False,
        "historical_source_bytes_retained": True,
        "customer_material_mutation_executed": False,
        "remote_resource_identities_returned": False,
        "source_refs_returned": False,
    }


def _coverage_projection(
    project: str,
    connector: str,
    instance: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    epoch = _text(instance.get("last_successful_sync_epoch_id"), 160)
    if not epoch:
        return {
            "status": "NOT_AVAILABLE",
            "complete": False,
            "discovered_count": 0,
            "covered_count": 0,
            "unsupported_count": 0,
            "coverage_ratio": 0.0,
            "unsupported_resources": [],
            "remote_lifecycle": _empty_lifecycle("NOT_AVAILABLE"),
            "source_content_returned": False,
            "customer_material_mutation_executed": False,
        }
    try:
        run = load_connector_sync_run(
            project,
            connector_instance_id=connector,
            sync_epoch_id=epoch,
            root=root,
        )
    except (KeyError, ConnectorSyncError):
        return {
            "status": "UNKNOWN",
            "complete": False,
            "discovered_count": 0,
            "covered_count": 0,
            "unsupported_count": 0,
            "coverage_ratio": 0.0,
            "unsupported_resources": [],
            "remote_lifecycle": _empty_lifecycle("UNKNOWN"),
            "source_content_returned": False,
            "customer_material_mutation_executed": False,
        }

    materialized_count = _safe_int(run.get("materialized_item_count"))
    unchanged_count = _safe_int(run.get("unchanged_item_count"))
    unsupported_count = _safe_int(run.get("coverage_observation_count"))
    covered_count = materialized_count + unchanged_count
    discovered_count = covered_count + unsupported_count
    ratio = covered_count / discovered_count if discovered_count else 1.0
    unsupported_resources = [
        {
            "resource_index": index,
            "resource_kind": _text(row.get("resource_kind"), 160),
            "remote_object_type": _text(row.get("remote_object_type"), 80),
            "display_title": _text(row.get("display_title"), 300),
            "reason_code": _text(row.get("reason_code"), 160),
            "retry_trigger": _text(row.get("retry_trigger"), 160),
            "content_materialized": False,
            "source_occurrence_created": False,
            "customer_source_modified": False,
        }
        for index, row in enumerate((run.get("coverage_observations") or [])[:100])
        if isinstance(row, dict)
    ]
    status = _text(run.get("knowledge_coverage_status"), 80) or (
        "PARTIAL_UNSUPPORTED" if unsupported_count else "COMPLETE"
    )
    return {
        "status": status,
        "complete": status == "COMPLETE",
        "discovered_count": discovered_count,
        "covered_count": covered_count,
        "unsupported_count": unsupported_count,
        "coverage_ratio": ratio,
        "unsupported_resources": unsupported_resources,
        "unsupported_resources_truncated": unsupported_count
        > len(unsupported_resources),
        "last_sync_epoch_id": epoch,
        "last_completed_at_utc": _text(run.get("completed_at_utc"), 80),
        "remote_lifecycle": _remote_lifecycle_projection(run),
        "source_content_returned": False,
        "customer_material_mutation_executed": False,
    }


def _connector_inventory(project: str, root: Path) -> dict[str, Any]:
    instances = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    )
    profiles = _profile_index(project, root)
    rows: list[dict[str, Any]] = []
    for raw in instances.get("connector_instances") or []:
        if not isinstance(raw, dict):
            continue
        row = _public_connector_instance(raw)
        connector = _text(row.get("connector_instance_id"), 160)
        row["connection_profile"] = profiles.get(
            connector,
            {
                "connector_instance_id": connector,
                "credentials_configured": False,
                "checkpoint_configured": False,
                "plaintext_returned": False,
            },
        )
        row["auto_sync"] = connector_auto_sync_status(
            root,
            project,
            connector,
        )
        row["coverage"] = _coverage_projection(
            project,
            connector,
            raw,
            root,
        )
        row["acceptance"] = _acceptance_projection(
            latest_feishu_tenant_acceptance_summary(
                project,
                connector,
                root=root,
            ),
            connector,
        )
        rows.append(row)
    return {
        "schema": "qualibug.knowledge-connector-inventory.v1",
        "project_id": project,
        "connectors": rows,
        "summary": {
            **dict(instances.get("summary") or {}),
            "profile_count": len(profiles),
            "credentials_configured_count": sum(
                bool(
                    row.get("connection_profile", {}).get(
                        "credentials_configured"
                    )
                )
                for row in rows
            ),
            "automatic_refresh_enabled": any(
                bool(row.get("auto_sync", {}).get("enabled")) for row in rows
            ),
            "partial_coverage_connector_count": sum(
                row.get("coverage", {}).get("status")
                == "PARTIAL_UNSUPPORTED"
                for row in rows
            ),
            "unsupported_resource_count": sum(
                _safe_int(row.get("coverage", {}).get("unsupported_count"))
                for row in rows
            ),
            "remote_absent_resource_count": sum(
                _safe_int(
                    row.get("coverage", {})
                    .get("remote_lifecycle", {})
                    .get("absent_count")
                )
                for row in rows
            ),
            "remote_unconfirmed_missing_resource_count": sum(
                _safe_int(
                    row.get("coverage", {})
                    .get("remote_lifecycle", {})
                    .get("unconfirmed_missing_count")
                )
                for row in rows
            ),
            "remote_retired_resource_count": sum(
                _safe_int(
                    row.get("coverage", {})
                    .get("remote_lifecycle", {})
                    .get("retired_count")
                )
                for row in rows
            ),
            "acceptance_ready_connector_count": sum(
                int(row.get("acceptance", {}).get("acceptance_ready") is True)
                for row in rows
            ),
            "acceptance_not_run_connector_count": sum(
                int(row.get("acceptance", {}).get("status") == "NOT_RUN")
                for row in rows
            ),
        },
        "governance": {
            **dict(instances.get("governance") or {}),
            "credentials_returned_to_frontend": False,
            "connection_profiles_masked": True,
            "fencing_tokens_returned_to_frontend": False,
            "checkpoint_fingerprints_returned_to_frontend": False,
            "automatic_refresh_uses_existing_sync_authority": True,
            "coverage_projection_uses_persisted_sync_receipt": True,
            "coverage_projection_returns_source_content": False,
            "remote_lifecycle_projection_uses_persisted_sync_receipt": True,
            "remote_lifecycle_projection_returns_remote_identities": False,
            "remote_lifecycle_projection_returns_source_refs": False,
            "remote_absence_is_not_remote_deletion_proof": True,
            "permission_loss_is_not_inferred_from_absence": True,
            "acceptance_projection_uses_allowlisted_report_fields": True,
            "acceptance_projection_returns_source_content": False,
            "acceptance_projection_returns_raw_cursor": False,
            "acceptance_projection_returns_credentials": False,
            "acceptance_always_uses_retain_policy": True,
            "acceptance_runs_as_persistent_background_job": True,
            "customer_material_mutation_executed": False,
            "second_connector_registry_created": False,
            "second_fencing_registry_created": False,
        },
    }


def _connector_inventory_row(
    project: str,
    connector: str,
    root: Path,
) -> dict[str, Any]:
    inventory = _connector_inventory(project, root)
    row = next(
        (
            dict(item)
            for item in inventory.get("connectors") or []
            if isinstance(item, dict)
            and _text(item.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if row is None:
        raise KeyError("knowledge_connector_not_found")
    return row


def _acceptance_projection(
    value: Any,
    connector: str = "",
) -> dict[str, Any]:
    acceptance = dict(value) if isinstance(value, dict) else {}
    latest = acceptance.get("latest_report")
    latest_report = dict(latest) if isinstance(latest, dict) else None
    if latest_report is not None:
        latest_report = {
            key: latest_report.get(key)
            for key in (
                "report_id",
                "acceptance_id",
                "profile",
                "verdict",
                "acceptance_ready",
                "started_at_utc",
                "completed_at_utc",
                "summary",
            )
            if key in latest_report
        }
    return {
        "schema": "qualibug.knowledge-connector-acceptance.v1",
        "connector_instance_id": connector,
        "status": _text(acceptance.get("status"), 40) or "NOT_RUN",
        "acceptance_ready": acceptance.get("acceptance_ready") is True,
        "latest_report": latest_report,
        "source_content_returned": False,
        "raw_cursor_returned": False,
        "credential_values_returned": False,
        "filesystem_path_returned": False,
    }


def _connector_resources_projection(
    project: str,
    connector: str,
    root: Path,
) -> dict[str, Any]:
    row = _connector_inventory_row(project, connector, root)
    coverage = dict(row.get("coverage") or {})
    prefix = f"connector://{quote(connector, safe='._-')}/"
    source_inventory = list_enterprise_knowledge_sources(
        project,
        root=root,
        include_deleted=False,
    )
    resources: list[dict[str, Any]] = []
    for source in source_inventory.get("sources") or []:
        if not isinstance(source, dict):
            continue
        if not _text(source.get("source_ref"), 2000).startswith(prefix):
            continue
        resources.append(
            {
                "resource_index": len(resources),
                "display_title": _text(
                    source.get("original_name"),
                    300,
                ) or "UNNAMED_RESOURCE",
                "resource_kind": _text(source.get("source_type"), 120),
                "state": "MATERIALIZED",
                "updated_at_utc": _text(source.get("updated_at_utc"), 80),
            }
        )
        if len(resources) >= 100:
            break
    materialized_count = len(resources)
    for unsupported in coverage.get("unsupported_resources") or []:
        if not isinstance(unsupported, dict) or len(resources) >= 100:
            break
        resources.append(
            {
                "resource_index": len(resources),
                "display_title": _text(unsupported.get("display_title"), 300),
                "resource_kind": _text(
                    unsupported.get("resource_kind"),
                    120,
                ),
                "remote_object_type": _text(
                    unsupported.get("remote_object_type"),
                    80,
                ),
                "state": "UNSUPPORTED",
                "reason_code": _text(unsupported.get("reason_code"), 160),
            }
        )
    return {
        "schema": "qualibug.knowledge-connector-resources.v1",
        "project_id": project,
        "connector_instance_id": connector,
        "status": _text(coverage.get("status"), 80) or "NOT_AVAILABLE",
        "discovered_count": _safe_int(coverage.get("discovered_count")),
        "covered_count": _safe_int(coverage.get("covered_count")),
        "unsupported_count": _safe_int(coverage.get("unsupported_count")),
        "materialized_preview_count": materialized_count,
        "resources": resources,
        "preview_truncated": len(resources) >= 100,
        "source_content_returned": False,
        "raw_cursor_returned": False,
        "credential_values_returned": False,
        "remote_resource_identities_returned": False,
        "source_refs_returned": False,
    }


def _sanitize_sync_response(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for field in _PRIVATE_SYNC_RESPONSE_FIELDS:
        result.pop(field, None)
    if isinstance(result.get("remote_lifecycle"), dict):
        result["remote_lifecycle"] = _remote_lifecycle_projection(result)
    result["next_cursor_returned_to_client"] = False
    result["fencing_token_returned_to_client"] = False
    result["checkpoint_storage"] = "encrypted_connection_profile"
    result["source_content_returned"] = False
    result["remote_lifecycle_remote_resource_identities_returned"] = False
    result["remote_lifecycle_source_refs_returned"] = False
    return result


def _error_status(exc: Exception) -> int:
    message = str(exc or "")
    if any(
        token in message
        for token in (
            "not_found",
            "not_registered",
            "sync_run_not_active",
        )
    ):
        return 404
    if any(
        token in message
        for token in (
            "already_running",
            "lock_held",
            "owner_active",
            "owner_unverified",
            "fence_revoked",
            "fence_transaction_busy",
            "cursor_mismatch",
            "previous_cursor_required",
            "checkpoint_integrity",
            "checkpoint_decryption",
            "checkpoint_registry_mismatch",
            "checkpoint_missing_for_registry_commit",
            "checkpoint_exists_without_registry_commit",
            "checkpoint_commit_mismatch",
            "status_change_blocked",
            "transaction_busy",
        )
    ):
        return 409
    if any(
        token in message
        for token in (
            "transport_failed",
            "api_failed",
            "download_failed",
            "export_poll_exhausted",
            "connection_profile_resolution_failed",
        )
    ):
        return 502
    return 400


class KnowledgeConnectorHandlersMixin:
    """Authenticated project-scoped online knowledge connector HTTP routes."""

    def _knowledge_connector_error(self, exc: Exception) -> Any:
        error = (
            "KNOWLEDGE_CONNECTOR_PROFILE_ERROR"
            if isinstance(exc, ConnectorProfileError)
            else "KNOWLEDGE_CONNECTOR_SYNC_ERROR"
            if isinstance(exc, ConnectorSyncError)
            else "FEISHU_ACCEPTANCE_JOB_ERROR"
            if isinstance(exc, FeishuTenantAcceptanceJobError)
            else "FEISHU_ACCEPTANCE_REPORT_ERROR"
            if isinstance(exc, FeishuTenantAcceptanceReportError)
            else "KNOWLEDGE_CONNECTOR_ERROR"
        )
        return self._json(
            {
                "ok": False,
                "error": error,
                "message": _text(exc, 600),
            },
            _error_status(exc),
        )

    def _require_connector_manager(
        self,
        actor: dict[str, Any],
        action: str,
    ) -> bool:
        return bool(
            self._require_role(
                actor,
                _service().KNOWLEDGE_MANAGER_ROLES,
                action,
            )
        )

    def _handle_connector_type_get(self, tail: list[str]) -> Any:
        try:
            registry = build_default_connector_registry()
            if not tail:
                return self._json({"ok": True, "data": registry.catalog()})
            manifest = registry.manifest(_text(tail[0], 160))
            catalog = registry.catalog()
            return self._json(
                {
                    "ok": True,
                    "data": {
                        "schema": catalog["schema"],
                        "connector_type": manifest.as_dict(),
                        "governance": dict(catalog["governance"]),
                    },
                }
            )
        except ConnectorRegistryError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "CONNECTOR_MANIFEST_ERROR",
                    "message": _text(exc, 600),
                },
                404,
            )

    def _handle_knowledge_connector_get(
        self,
        project: str,
        tail: list[str],
        root: Path,
    ) -> Any:
        try:
            inventory = _connector_inventory(project, root)
            if not tail:
                return self._json({"ok": True, "data": inventory})
            connector = _text(tail[0], 160)
            if len(tail) == 1:
                row = next(
                    (
                        item
                        for item in inventory["connectors"]
                        if item.get("connector_instance_id") == connector
                    ),
                    None,
                )
                if row is None:
                    raise KeyError("knowledge_connector_not_found")
                return self._json({"ok": True, "data": row})
            if len(tail) == 2 and tail[1] == "resources":
                return self._json(
                    {
                        "ok": True,
                        "data": _connector_resources_projection(
                            project,
                            connector,
                            root,
                        ),
                    }
                )
            if len(tail) == 2 and tail[1] == "coverage":
                row = _connector_inventory_row(project, connector, root)
                coverage = dict(row.get("coverage") or {})
                coverage.update(
                    {
                        "schema": "qualibug.knowledge-connector-coverage.v1",
                        "connector_instance_id": connector,
                        "source_content_returned": False,
                        "raw_cursor_returned": False,
                        "credential_values_returned": False,
                    }
                )
                return self._json({"ok": True, "data": coverage})
            if len(tail) == 2 and tail[1] == "runs":
                return self._json(
                    {
                        "ok": True,
                        "data": list_connector_sync_runs(
                            project,
                            connector_instance_id=connector,
                            root=root,
                            limit=20,
                        ),
                    }
                )
            if len(tail) == 2 and tail[1] == "acceptance":
                row = _connector_inventory_row(project, connector, root)
                return self._json(
                    {
                        "ok": True,
                        "data": _acceptance_projection(
                            row.get("acceptance"),
                            connector,
                        ),
                    }
                )
            if len(tail) == 2 and tail[1] == "acceptance-reports":
                reports = list_feishu_tenant_acceptance_reports(
                    project,
                    connector,
                    root=root,
                    limit=20,
                )
                return self._json({"ok": True, "data": reports})
            if len(tail) == 3 and tail[1] == "acceptance-reports":
                report = load_feishu_tenant_acceptance_report(
                    project,
                    connector,
                    _text(tail[2], 80),
                    root=root,
                )
                return self._json({"ok": True, "data": report})
            if len(tail) == 3 and tail[1] == "acceptance-jobs":
                job = (
                    get_current_feishu_tenant_acceptance_job(
                        project,
                        connector,
                        root=root,
                    )
                    if tail[2] == "current"
                    else get_feishu_tenant_acceptance_job(
                        project,
                        connector,
                        _text(tail[2], 80),
                        root=root,
                    )
                )
                return self._json({"ok": True, "data": job})
            if len(tail) == 3 and tail[1] == "runs":
                run = load_connector_sync_run(
                    project,
                    connector_instance_id=connector,
                    sync_epoch_id=_text(tail[2], 160),
                    root=root,
                )
                return self._json(
                    {
                        "ok": True,
                        "data": _sanitize_sync_response(run),
                        "source_content_returned": False,
                        "raw_cursor_returned": False,
                        "fencing_token_returned": False,
                    }
                )
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        except (
            ConnectorProfileError,
            ConnectorSyncError,
            FeishuTenantAcceptanceJobError,
            FeishuTenantAcceptanceReportError,
            KeyError,
        ) as exc:
            return self._knowledge_connector_error(exc)

    def _handle_knowledge_connector_configure(
        self,
        project: str,
        body: dict[str, Any],
        root: Path,
        actor: dict[str, Any],
    ) -> Any:
        connector = _text(body.get("connector_instance_id"), 160)
        profile = body.get("connection_profile")
        if not isinstance(profile, dict):
            profile = {
                key: body.get(key)
                for key in (
                    "auth_mode",
                    "app_id",
                    "app_secret",
                    "tenant_access_token",
                    "user_access_token",
                )
                if key in body
            }
        connector_type = _text(body.get("connector_type"), 160)
        configuration_kwargs = {
            "connector_instance_id": connector,
            "resource_scope": _text(body.get("resource_scope"), 20000),
            "profile": profile,
            "root": root,
            "actor": actor,
            "display_name": _text(body.get("display_name"), 240),
            "status": _text(body.get("status"), 32) or "ACTIVE",
            "credential_expires_at_utc": body.get(
                "credential_expires_at_utc"
            ),
            "sync_policy": body.get("sync_policy"),
        }
        if connector_type:
            result = configure_managed_connector(
                project,
                connector_type=connector_type,
                **configuration_kwargs,
            )
        else:
            result = configure_managed_feishu_connector(
                project,
                **configuration_kwargs,
            )
        public_result = {
            "ok": bool(result.get("ok")),
            "created": bool(result.get("created")),
            "connector_instance": _public_connector_instance(
                dict(result.get("connector_instance") or {})
            ),
            "connection_profile": dict(result.get("connection_profile") or {}),
            "credential_storage": dict(result.get("credential_storage") or {}),
        }
        return self._json(
            {"ok": True, "data": public_result},
            201 if result["created"] else 200,
        )

    def _connector_update_profile(
        self,
        project: str,
        connector: str,
        body: dict[str, Any],
        root: Path,
    ) -> dict[str, Any]:
        current = _connector_inventory_row(project, connector, root)
        current_type = _text(current.get("connector_type"), 160)
        requested_type = _text(body.get("connector_type"), 160)
        if requested_type and requested_type != current_type:
            raise ValueError("connector_instance_type_is_immutable")
        current_profile = dict(current.get("connection_profile") or {})
        incoming = body.get("connection_profile")
        if incoming is None:
            incoming = {}
        if not isinstance(incoming, dict):
            raise ConnectorProfileError("connector_profile_must_be_object")
        profile = dict(incoming)
        profile.setdefault(
            "auth_mode",
            _text(current_profile.get("auth_mode"), 64),
        )
        if not incoming:
            for field, configured in dict(
                current_profile.get("configured_fields") or {}
            ).items():
                if configured is True:
                    profile[field] = MASKED_SECRET
        elif "auth_mode" not in incoming:
            for field, configured in dict(
                current_profile.get("configured_fields") or {}
            ).items():
                if configured is True and field not in profile:
                    profile[field] = MASKED_SECRET
        return {
            "connector_type": current_type,
            "connector_instance_id": connector,
            "resource_scope": (
                _text(body.get("resource_scope"), 20000)
                if "resource_scope" in body
                else _text(current.get("resource_scope"), 20000)
            ),
            "profile": profile,
            "root": root,
            "actor": body.get("_actor"),
            "display_name": (
                _text(body.get("display_name"), 240)
                if "display_name" in body
                else _text(current.get("display_name"), 240)
            ),
            "status": (
                _text(body.get("status"), 32).upper()
                if "status" in body
                else _text(current.get("status"), 32).upper()
            ),
            "credential_expires_at_utc": body.get(
                "credential_expires_at_utc",
                current_profile.get("credential_expires_at_utc", ""),
            ),
            "sync_policy": body.get("sync_policy"),
        }

    def _handle_knowledge_connector_patch(
        self,
        project: str,
        connector: str,
        body: dict[str, Any],
        root: Path,
        actor: dict[str, Any],
    ) -> Any:
        allowed = {
            "connector_type",
            "display_name",
            "resource_scope",
            "status",
            "connection_profile",
            "credential_expires_at_utc",
            "sync_policy",
        }
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ValueError(f"connector_patch_field_not_supported:{unknown[0]}")
        update_body = dict(body)
        update_body["_actor"] = actor
        kwargs = self._connector_update_profile(
            project,
            connector,
            update_body,
            root,
        )
        result = configure_managed_connector(project, **kwargs)
        public_result = {
            "ok": bool(result.get("ok")),
            "created": False,
            "connector_instance": _public_connector_instance(
                dict(result.get("connector_instance") or {})
            ),
            "connection_profile": dict(result.get("connection_profile") or {}),
            "credential_storage": dict(result.get("credential_storage") or {}),
        }
        return self._json({"ok": True, "data": public_result})

    def _handle_knowledge_connector_action(
        self,
        project: str,
        connector: str,
        action: str,
        body: dict[str, Any],
        root: Path,
        actor: dict[str, Any],
    ) -> Any:
        if action == "test":
            result = _managed_connection_tester()(
                project,
                connector,
                root=root,
                timeout=_bounded_float(
                    body.get("timeout"), 15.0, 1.0, 60.0
                ),
            )
            return self._json({"ok": True, "data": result})
        if action in {"pause", "resume"}:
            result = set_managed_connector_status(
                project,
                connector_instance_id=connector,
                status="PAUSED" if action == "pause" else "ACTIVE",
                root=root,
                actor=actor,
            )
            row = _connector_inventory_row(project, connector, root)
            return self._json(
                {
                    "ok": True,
                    "data": {
                        "action": action,
                        "connector_instance": _public_connector_instance(
                            dict(result.get("connector_instance") or row)
                        ),
                        "connection_profile": dict(
                            row.get("connection_profile") or {}
                        ),
                        "credential_values_returned": False,
                    },
                }
            )
        if action == "reauthorize":
            incoming_profile = body.get("connection_profile")
            if incoming_profile is not None:
                update_body = dict(body)
                update_body.setdefault("status", "ACTIVE")
                update_body["_actor"] = actor
                kwargs = self._connector_update_profile(
                    project,
                    connector,
                    update_body,
                    root,
                )
                result = configure_managed_connector(project, **kwargs)
                return self._json(
                    {
                        "ok": True,
                        "data": {
                            "action": action,
                            "connector_instance": _public_connector_instance(
                                dict(result.get("connector_instance") or {})
                            ),
                            "connection_profile": dict(
                                result.get("connection_profile") or {}
                            ),
                            "credential_storage": dict(
                                result.get("credential_storage") or {}
                            ),
                        },
                    }
                )
            result = mark_connector_reauthorization_required(
                project,
                connector,
                required=True,
                reason=_text(body.get("reason"), 300),
                root=root,
                actor=actor,
            )
            row = _connector_inventory_row(project, connector, root)
            return self._json(
                {
                    "ok": True,
                    "data": {
                        "action": action,
                        "connector_instance": _public_connector_instance(row),
                        "connection_profile": dict(
                            result.get("connection_profile") or {}
                        ),
                        "credential_values_returned": False,
                    },
                }
            )
        if action == "sync":
            run = _managed_sync_runner()(
                project,
                connector,
                root=root,
                actor=actor,
                deletion_policy=_text(body.get("deletion_policy"), 32)
                or "RETAIN",
                max_retire_count=_bounded_int(
                    body.get("max_retire_count"), 100, 0, 10_000
                ),
                max_retire_ratio=_bounded_float(
                    body.get("max_retire_ratio"), 0.25, 0.0, 1.0
                ),
                max_nodes=_bounded_int(
                    body.get("max_nodes"), 5000, 1, 100_000
                ),
                max_export_polls=_bounded_int(
                    body.get("max_export_polls"), 20, 1, 120
                ),
                export_poll_interval=_bounded_float(
                    body.get("export_poll_interval"), 0.5, 0.0, 5.0
                ),
                allow_raw_text_fallback=bool(
                    body.get("allow_raw_text_fallback") is True
                ),
                timeout=_bounded_float(
                    body.get("timeout"), 15.0, 1.0, 60.0
                ),
            )
            return self._json(
                {
                    "ok": run.get("status") == "COMPLETE",
                    "data": _sanitize_sync_response(run),
                },
                200 if run.get("status") == "COMPLETE" else 409,
            )
        if action == "acceptance":
            options = {
                "runs": _optional_bounded_int(body.get("runs"), 2, 10),
                "min_discovered_resources": _optional_bounded_int(
                    body.get("min_discovered_resources"), 0, 1_000_000
                ),
                "min_coverage_ratio": _optional_bounded_float(
                    body.get("min_coverage_ratio"), 0.0, 1.0
                ),
                "max_unsupported_ratio": _optional_bounded_float(
                    body.get("max_unsupported_ratio"), 0.0, 1.0
                ),
                "max_run_duration_seconds": _optional_bounded_float(
                    body.get("max_run_duration_seconds"), 1.0, 3600.0
                ),
                "max_nodes": _bounded_int(
                    body.get("max_nodes"), 100_000, 1, 100_000
                ),
                "max_export_polls": _bounded_int(
                    body.get("max_export_polls"), 40, 1, 120
                ),
                "export_poll_interval": _bounded_float(
                    body.get("export_poll_interval"), 0.5, 0.0, 5.0
                ),
                "allow_raw_text_fallback": bool(
                    body.get("allow_raw_text_fallback") is True
                ),
                "timeout": _bounded_float(
                    body.get("timeout"), 30.0, 1.0, 60.0
                ),
            }
            job = start_feishu_tenant_acceptance_job(
                project,
                connector,
                root=root,
                profile=_text(body.get("profile"), 40) or "pilot",
                actor=actor,
                options={key: value for key, value in options.items() if value is not None},
            )
            return self._json(
                {
                    "ok": True,
                    "data": job,
                    "source_content_returned": False,
                    "raw_cursor_returned": False,
                    "credential_values_returned": False,
                    "filesystem_path_returned": False,
                },
                202,
            )
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        connector_type_route = _connector_type_route(parsed.path)
        if connector_type_route is not None:
            self._init_request_context()
            root = self._root()
            actor = self._require_actor()
            if actor is None or self._require_tenant(root) is None:
                return None
            return self._handle_connector_type_get(connector_type_route)
        route = _connector_route(parsed.path)
        if route is None:
            return super().do_GET()
        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        try:
            project = _safe_project_id(route[0])
        except ValueError:
            return self._json(
                {"ok": False, "error": "PROJECT_NOT_FOUND"}, 404
            )
        if not self._require_project_scope(project):
            return None
        return self._handle_knowledge_connector_get(project, route[1], root)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = _connector_route(parsed.path)
        if route is None or len(route[1]) != 1:
            fallback = getattr(super(), "do_PATCH", None)
            if callable(fallback):
                return fallback()
            return self.send_error(501, "PATCH is not supported for this route")
        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        try:
            project = _safe_project_id(route[0])
        except ValueError:
            return self._json(
                {"ok": False, "error": "PROJECT_NOT_FOUND"}, 404
            )
        if not self._require_project_scope(project):
            return None
        if not self._require_connector_manager(
            actor, "knowledge connector configuration"
        ):
            return None
        try:
            return self._handle_knowledge_connector_patch(
                project,
                _text(route[1][0], 160),
                self._body(),
                root,
                actor,
            )
        except (
            ConnectorProfileError,
            ConnectorSyncError,
            ValueError,
            TypeError,
        ) as exc:
            return self._knowledge_connector_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = _connector_route(parsed.path)
        if route is None:
            return super().do_POST()
        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        try:
            project = _safe_project_id(route[0])
        except ValueError:
            return self._json(
                {"ok": False, "error": "PROJECT_NOT_FOUND"}, 404
            )
        if not self._require_project_scope(project):
            return None
        if not self._require_connector_manager(
            actor, "knowledge connector operation"
        ):
            return None
        try:
            body = self._body()
            tail = route[1]
            if not tail:
                return self._handle_knowledge_connector_configure(
                    project,
                    body,
                    root,
                    actor,
                )
            if len(tail) == 2 and tail[1] in {
                "test",
                "sync",
                "acceptance",
                "pause",
                "resume",
                "reauthorize",
            }:
                return self._handle_knowledge_connector_action(
                    project,
                    _text(tail[0], 160),
                    tail[1],
                    body,
                    root,
                    actor,
                )
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        except (
            ConnectorProfileError,
            ConnectorSyncError,
            FeishuConnectorError,
            FeishuTenantAcceptanceJobError,
            FeishuTenantAcceptanceReportError,
            ValueError,
            TypeError,
        ) as exc:
            return self._knowledge_connector_error(exc)


__all__ = [
    "KnowledgeConnectorHandlersMixin",
    "_connector_inventory",
    "_connector_route",
    "_connector_type_route",
    "_coverage_projection",
    "_public_connector_instance",
    "_remote_lifecycle_projection",
    "_sanitize_sync_response",
]
