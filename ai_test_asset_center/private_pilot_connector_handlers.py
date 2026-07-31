"""Private-pilot HTTP surface for enterprise knowledge connectors.

This mixin is composed before the canonical HTTP router. It intercepts only the
project-scoped ``knowledge-connectors`` resource and delegates every other route
unchanged to ``HttpRoutingMixin``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .connector_connection_profiles import (
    ConnectorProfileError,
    commit_connector_sync_checkpoint,
    configure_feishu_connector,
    list_connector_connection_profiles,
    load_connector_sync_checkpoint,
    resolve_connector_connection_profile,
)
from .connector_sync_authority import (
    ConnectorSyncError,
    abort_connector_sync_run,
    list_connector_instances,
    load_connector_sync_run,
)
from .feishu_connector_adapter import (
    FeishuConnectorError,
    sync_feishu_connector,
    test_feishu_connector_connection,
)
from .real_project_onboarding import _safe_project_id

_ROUTE_MARKER = "knowledge-connectors"


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


def _profile_index(project: str, root: Path) -> dict[str, dict[str, Any]]:
    payload = list_connector_connection_profiles(project, root=root)
    return {
        _text(row.get("connector_instance_id"), 160): dict(row)
        for row in payload.get("profiles") or []
        if isinstance(row, dict)
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
        row = dict(raw)
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
        rows.append(row)
    return {
        "schema": "qualibug.knowledge-connector-inventory.v1",
        "project_id": project,
        "connectors": rows,
        "summary": {
            **dict(instances.get("summary") or {}),
            "profile_count": len(profiles),
            "credentials_configured_count": sum(
                bool(row.get("connection_profile", {}).get("credentials_configured"))
                for row in rows
            ),
        },
        "governance": {
            **dict(instances.get("governance") or {}),
            "credentials_returned_to_frontend": False,
            "connection_profiles_masked": True,
        },
    }


def _sanitize_sync_response(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("next_cursor", None)
    result["next_cursor_returned_to_client"] = False
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
            "cursor_mismatch",
            "previous_cursor_required",
            "checkpoint_integrity",
            "checkpoint_decryption",
            "status_change_blocked",
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
        return self._json(
            {
                "ok": False,
                "error": (
                    "KNOWLEDGE_CONNECTOR_PROFILE_ERROR"
                    if isinstance(exc, ConnectorProfileError)
                    else "KNOWLEDGE_CONNECTOR_SYNC_ERROR"
                    if isinstance(exc, ConnectorSyncError)
                    else "FEISHU_CONNECTOR_ERROR"
                ),
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
                _service().CONFIG_MANAGER_ROLES,
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
                        "data": run,
                        "source_content_returned": False,
                        "raw_cursor_returned": False,
                    }
                )
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        except (ConnectorProfileError, ConnectorSyncError, KeyError) as exc:
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
        result = configure_feishu_connector(
            project,
            connector_instance_id=connector,
            resource_scope=_text(body.get("resource_scope"), 1000),
            profile=profile,
            root=root,
            actor=actor,
            display_name=_text(body.get("display_name"), 240),
            status=_text(body.get("status"), 32) or "ACTIVE",
        )
        return self._json(
            {"ok": True, "data": result},
            201 if result["created"] else 200,
        )

    def _profile_resolver(self, project: str, root: Path):
        def resolve(profile_ref: str) -> dict[str, str]:
            return resolve_connector_connection_profile(
                project,
                profile_ref,
                root=root,
            )

        return resolve

    def _handle_knowledge_connector_action(
        self,
        project: str,
        connector: str,
        action: str,
        body: dict[str, Any],
        root: Path,
        actor: dict[str, Any],
    ) -> Any:
        resolver = self._profile_resolver(project, root)
        if action == "test":
            result = test_feishu_connector_connection(
                project,
                connector_instance_id=connector,
                resolve_connection_profile=resolver,
                root=root,
                timeout=float(body.get("timeout") or 15.0),
            )
            return self._json({"ok": True, "data": result})
        if action == "sync":
            previous_cursor = load_connector_sync_checkpoint(
                project,
                connector,
                root=root,
            )
            run = sync_feishu_connector(
                project,
                connector_instance_id=connector,
                resolve_connection_profile=resolver,
                root=root,
                actor=actor,
                previous_cursor=previous_cursor,
                deletion_policy=_text(body.get("deletion_policy"), 32)
                or "RETAIN",
                max_retire_count=int(body.get("max_retire_count") or 100),
                max_retire_ratio=float(body.get("max_retire_ratio") or 0.25),
                max_nodes=int(body.get("max_nodes") or 5000),
                max_export_polls=int(body.get("max_export_polls") or 20),
                export_poll_interval=float(
                    body.get("export_poll_interval") or 0.5
                ),
                allow_raw_text_fallback=bool(
                    body.get("allow_raw_text_fallback") is True
                ),
                timeout=float(body.get("timeout") or 15.0),
            )
            if run.get("status") == "COMPLETE":
                checkpoint = _text(run.get("next_cursor"), 500)
                if not checkpoint:
                    raise ConnectorProfileError(
                        "connector_sync_checkpoint_missing_after_complete_run"
                    )
                commit_connector_sync_checkpoint(
                    project,
                    connector,
                    checkpoint,
                    sync_epoch_id=_text(run.get("sync_epoch_id"), 160),
                    root=root,
                    actor=actor,
                )
            return self._json(
                {
                    "ok": run.get("status") == "COMPLETE",
                    "data": _sanitize_sync_response(run),
                },
                200 if run.get("status") == "COMPLETE" else 409,
            )
        if action == "abort":
            result = abort_connector_sync_run(
                project,
                connector_instance_id=connector,
                reason=_text(body.get("reason"), 1000)
                or "operator requested connector sync abort",
                root=root,
                actor=actor,
            )
            return self._json({"ok": True, "data": result})
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
            return self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
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
            return self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
        if not self._require_project_scope(project):
            return None
        if not self._require_connector_manager(actor, "knowledge connector operation"):
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
            if len(tail) == 2 and tail[1] in {"test", "sync", "abort"}:
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
            ValueError,
            TypeError,
        ) as exc:
            return self._knowledge_connector_error(exc)


__all__ = ["KnowledgeConnectorHandlersMixin"]
