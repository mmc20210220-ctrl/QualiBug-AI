"""Private-pilot HTTP surface for enterprise knowledge connectors.

The HTTP layer owns authentication, public projection, and request shaping only. Trusted sync,
acceptance, fenced configuration, checkpoint validation, automatic refresh, and retry policy live
in connector application services. Raw credentials, source content, cursors, and report paths are
never returned through this surface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .connector_auto_sync import (
    connector_auto_sync_status,
    run_managed_feishu_sync,
    test_managed_feishu_connection,
)
from .connector_configuration_service import configure_managed_feishu_connector
from .connector_connection_profiles import (
    ConnectorProfileError,
    list_connector_connection_profiles,
)
from .connector_sync_authority import (
    ConnectorSyncError,
    list_connector_instances,
    load_connector_sync_run,
)
from .feishu_connector_adapter import FeishuConnectorError
from .feishu_tenant_acceptance import (
    FeishuTenantAcceptanceError,
    run_feishu_tenant_acceptance,
)
from .feishu_tenant_acceptance_reports import (
    FeishuTenantAcceptanceReportError,
    latest_feishu_tenant_acceptance_summary,
    list_feishu_tenant_acceptance_reports,
    load_feishu_tenant_acceptance_report,
)
from .real_project_onboarding import _safe_project_id

_ROUTE_MARKER = "knowledge-connectors"
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


def _connector_route(path: str) -> tuple[str, list[str]] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) < 5
        or parts[:3] != ["api", "v1", "projects"]
        or parts[4] != _ROUTE_MARKER
    ):
        return None
    return parts[3], parts[5:]


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
            "source_content_returned": False,
            "customer_material_mutation_executed": False,
        }

    materialized_count = int(run.get("materialized_item_count") or 0)
    unchanged_count = int(run.get("unchanged_item_count") or 0)
    unsupported_count = int(run.get("coverage_observation_count") or 0)
    covered_count = materialized_count + unchanged_count
    discovered_count = covered_count + unsupported_count
    ratio = covered_count / discovered_count if discovered_count else 1.0
    unsupported_resources = [
        {
            "remote_resource_id": _text(row.get("remote_resource_id"), 1000),
            "resource_kind": _text(row.get("resource_kind"), 160),
            "remote_object_type": _text(row.get("remote_object_type"), 80),
            "display_title": _text(row.get("display_title"), 300),
            "reason_code": _text(row.get("reason_code"), 160),
            "retry_trigger": _text(row.get("retry_trigger"), 160),
            "content_materialized": False,
            "source_occurrence_created": False,
            "customer_source_modified": False,
        }
        for row in (run.get("coverage_observations") or [])[:100]
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
        row["acceptance"] = latest_feishu_tenant_acceptance_summary(
            project,
            connector,
            root=root,
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
                int(row.get("coverage", {}).get("unsupported_count") or 0)
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
            "acceptance_projection_uses_allowlisted_report_fields": True,
            "acceptance_projection_returns_source_content": False,
            "acceptance_projection_returns_raw_cursor": False,
            "acceptance_projection_returns_credentials": False,
            "acceptance_always_uses_retain_policy": True,
            "customer_material_mutation_executed": False,
            "second_connector_registry_created": False,
            "second_fencing_registry_created": False,
        },
    }


def _sanitize_sync_response(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for field in _PRIVATE_SYNC_RESPONSE_FIELDS:
        result.pop(field, None)
    result["next_cursor_returned_to_client"] = False
    result["fencing_token_returned_to_client"] = False
    result["checkpoint_storage"] = "encrypted_connection_profile"
    result["source_content_returned"] = False
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
            else "FEISHU_ACCEPTANCE_REPORT_ERROR"
            if isinstance(exc, FeishuTenantAcceptanceReportError)
            else "FEISHU_ACCEPTANCE_ERROR"
            if isinstance(exc, FeishuTenantAcceptanceError)
            else "FEISHU_CONNECTOR_ERROR"
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
        result = configure_managed_feishu_connector(
            project,
            connector_instance_id=connector,
            resource_scope=_text(body.get("resource_scope"), 1000),
            profile=profile,
            root=root,
            actor=actor,
            display_name=_text(body.get("display_name"), 240),
            status=_text(body.get("status"), 32) or "ACTIVE",
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
            result = test_managed_feishu_connection(
                project,
                connector,
                root=root,
                timeout=_bounded_float(
                    body.get("timeout"), 15.0, 1.0, 60.0
                ),
            )
            return self._json({"ok": True, "data": result})
        if action == "sync":
            run = run_managed_feishu_sync(
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
            report = run_feishu_tenant_acceptance(
                project,
                connector,
                root=root,
                profile=_text(body.get("profile"), 40) or "pilot",
                runs=_optional_bounded_int(body.get("runs"), 2, 10),
                min_discovered_resources=_optional_bounded_int(
                    body.get("min_discovered_resources"), 0, 1_000_000
                ),
                min_coverage_ratio=_optional_bounded_float(
                    body.get("min_coverage_ratio"), 0.0, 1.0
                ),
                max_unsupported_ratio=_optional_bounded_float(
                    body.get("max_unsupported_ratio"), 0.0, 1.0
                ),
                max_run_duration_seconds=_optional_bounded_float(
                    body.get("max_run_duration_seconds"), 1.0, 3600.0
                ),
                max_nodes=_bounded_int(
                    body.get("max_nodes"), 100_000, 1, 100_000
                ),
                max_export_polls=_bounded_int(
                    body.get("max_export_polls"), 40, 1, 120
                ),
                export_poll_interval=_bounded_float(
                    body.get("export_poll_interval"), 0.5, 0.0, 5.0
                ),
                allow_raw_text_fallback=bool(
                    body.get("allow_raw_text_fallback") is True
                ),
                timeout=_bounded_float(
                    body.get("timeout"), 30.0, 1.0, 60.0
                ),
                actor=actor,
            )
            report_id = Path(_text(report.get("report_path"), 1000)).stem
            public_report = load_feishu_tenant_acceptance_report(
                project,
                connector,
                report_id,
                root=root,
            )
            return self._json(
                {
                    "ok": True,
                    "accepted": public_report.get("acceptance_ready") is True,
                    "data": public_report,
                    "source_content_returned": False,
                    "raw_cursor_returned": False,
                    "credential_values_returned": False,
                    "filesystem_path_returned": False,
                }
            )
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
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
            FeishuTenantAcceptanceError,
            FeishuTenantAcceptanceReportError,
            ValueError,
            TypeError,
        ) as exc:
            return self._knowledge_connector_error(exc)


__all__ = [
    "KnowledgeConnectorHandlersMixin",
    "_connector_inventory",
    "_connector_route",
    "_coverage_projection",
    "_public_connector_instance",
    "_sanitize_sync_response",
]
