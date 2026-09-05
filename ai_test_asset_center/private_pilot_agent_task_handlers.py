"""HTTP contract for project-scoped Agent Tasks and their event ledger."""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import unquote, urlparse

from .agent_task_grounding import build_agent_task_grounding
from .agent_task_grounding_store import apply_agent_task_grounding
from .agent_task_store import (
    AgentTaskConflict,
    AgentTaskError,
    AgentTaskNotFound,
    AgentTaskValidationError,
    cancel_agent_task,
    create_agent_task,
    get_agent_task,
    list_agent_task_events,
    list_agent_tasks,
    claim_agent_task_execution,
)
from .product_logging import get_logger
from .real_project_onboarding import _safe_project_id

_AGENT_TASK_ROLES = {"project_owner", "qa_lead", "testops_admin", "admin"}
_agent_logger = get_logger("qualibug.agent_task")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _parse_agent_task_route(path: str) -> tuple[str, str, str] | None:
    parts = [unquote(part) for part in str(path or "").split("/") if part]
    if len(parts) < 5 or parts[:3] != ["api", "v1", "projects"]:
        return None
    if parts[4] != "agent-tasks":
        return None
    project = parts[3]
    if len(parts) == 5:
        return "collection", project, ""
    task_id = parts[5] if len(parts) >= 6 else ""
    if len(parts) == 6:
        return "task", project, task_id
    if len(parts) == 7 and parts[6] == "events":
        return "events", project, task_id
    if len(parts) == 7 and parts[6] == "ground":
        return "ground", project, task_id
    if len(parts) == 7 and parts[6] == "execute":
        return "execute", project, task_id
    if len(parts) == 7 and parts[6] == "cancel":
        return "cancel", project, task_id
    return None


class AgentTaskHandlersMixin:
    def _agent_task_request_scope(self, project_raw: str) -> tuple[str, Any] | None:
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        try:
            project = _safe_project_id(project_raw)
        except ValueError:
            self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
            return None
        if not self._require_project_scope(project):
            return None
        if not self._require_known_project(project, root):
            return None
        return project, actor

    def _agent_task_error(self, exc: Exception) -> Any:
        if isinstance(exc, AgentTaskNotFound):
            return self._json(
                {"ok": False, "error": "AGENT_TASK_NOT_FOUND", "message": "Agent Task 不存在。"},
                404,
            )
        if isinstance(exc, AgentTaskValidationError):
            return self._json(
                {"ok": False, "error": "AGENT_TASK_BAD_REQUEST", "message": str(exc)},
                400,
            )
        if isinstance(exc, AgentTaskConflict):
            return self._json(
                {"ok": False, "error": "AGENT_TASK_CONFLICT", "message": str(exc)},
                409,
            )
        _agent_logger.error(
            "agent_task.request_failed",
            exc_info=True,
            extra={
                "context": {
                    "event": "agent_task.request_failed",
                    "path": _text(getattr(self, "path", "")),
                    "correlation_id": _text(getattr(self, "_qualibug_corr_id", "")),
                    "exc_type": type(exc).__name__,
                }
            },
        )
        return self._json(
            {
                "ok": False,
                "error": "AGENT_TASK_INTERNAL_ERROR",
                "message": "Agent Task 持久化或 Grounding 资源暂时不可用。",
            },
            500,
        )

    def _agent_task_body(self) -> dict[str, Any] | None:
        try:
            return self._body()
        except ValueError as exc:
            self._json(
                {
                    "ok": False,
                    "error": "AGENT_TASK_BAD_REQUEST",
                    "message": str(exc),
                },
                400,
            )
            return None

    def _ground_agent_task(
        self,
        *,
        project: str,
        tenant_id: str,
        task: dict[str, Any],
        correlation_id: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        request = dict(body or {})
        preflight_request = (
            dict(request.get("preflight"))
            if isinstance(request.get("preflight"), dict)
            else {}
        )
        grounding = build_agent_task_grounding(
            self._root(),
            tenant_id=tenant_id,
            project_id=project,
            task=task,
            requested_target_ids=_string_list(request.get("test_target_ids")),
            preflight_request=preflight_request,
        )
        grounded = apply_agent_task_grounding(
            self._root(),
            tenant_id=tenant_id,
            project_id=project,
            task_id=_text(task.get("task_id")),
            grounding=grounding,
            correlation_id=correlation_id,
        )
        return grounded, grounding

    def _prepare_agent_task_execution(self, project: str, task_id: str, request: dict[str, Any], owner: dict[str, Any]) -> dict[str, Any]:
        """Bind project-scope execution under the canonical lease; no new executor."""
        from .agent_task_grounding import _scan_preflight_payload, _snapshot_ref
        from .private_pilot_product_catalog import _test_intelligence_source_fingerprint
        from .enterprise_knowledge_center.composition import pin_enterprise_business_knowledge_asset
        from .enterprise_source_registry import compose_project_source_manifest

        if set(request) - {"execution_scope", "read_only"} or request.get("execution_scope") != "project":
            raise AgentTaskValidationError("agent_task_explicit_project_scope_required")
        if "read_only" in request and not isinstance(request["read_only"], bool):
            raise AgentTaskValidationError("agent_task_read_only_must_be_boolean")
        tenant_id = self._request_tenant()
        root = self._root()
        task = get_agent_task(root, tenant_id=tenant_id, project_id=project, task_id=task_id)
        if task.get("execution_claim_status") not in {None, "", "NOT_CLAIMED"}:
            return {"execute": False, "task": task}
        if task.get("intent") == "analyze_requirements":
            raise AgentTaskValidationError("analysis_task_does_not_execute_scans")
        if task.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise AgentTaskConflict("agent_task_terminal")
        expected = (task.get("source_snapshot") or {}).get("snapshot_ref")
        fingerprint = _test_intelligence_source_fingerprint(root, tenant_id, project)
        if not expected or expected != _snapshot_ref(fingerprint):
            raise AgentTaskConflict("agent_task_snapshot_changed_recheck_context")
        preflight = _scan_preflight_payload(project, root, request)
        if preflight.get("ready") is not True:
            raise AgentTaskConflict("agent_task_preflight_blocked:" + ",".join(preflight.get("blocking_codes") or ["NOT_READY"]))
        snapshot_ref = pin_enterprise_business_knowledge_asset(project, root)
        manifest = compose_project_source_manifest(project, root=root)
        if not manifest.get("source_id") or len(_text(manifest.get("source_hash"))) != 64:
            raise AgentTaskConflict("agent_task_source_manifest_unavailable")
        if _test_intelligence_source_fingerprint(root, tenant_id, project) != fingerprint:
            raise AgentTaskConflict("agent_task_sources_changed_during_pin")
        checks = preflight.get("input_checks") or {}
        target = checks.get("target") or {}
        environment = checks.get("environment") or {}
        scan_body = {
            "source_manifest": {key: manifest[key] for key in ("source_id", "source_hash")},
            "base_url": _text(target.get("target_url")),
            "approved_base_url": _text(target.get("approved_base_url")),
            "environment_type": _text(environment.get("environment_type")),
            "environment_ref": _text(environment.get("environment_ref")),
            "read_only": request.get("read_only") is True,
        }
        claim_id = uuid.uuid4().hex
        claimed = claim_agent_task_execution(
            root, tenant_id=tenant_id, project_id=project, task_id=task_id,
            claim_id=claim_id, lease_token=_text(owner.get("token")),
            execution_scope="project", execution_snapshot_ref=snapshot_ref,
            correlation_id=_text(getattr(self, "_qualibug_corr_id", "")),
        )
        return {
            "execute": claimed.get("execution_claim_id") == claim_id,
            "task": claimed, "scan_body": scan_body,
            "task_id": task_id, "tenant_id": tenant_id, "claim_id": claim_id,
            "snapshot_ref": snapshot_ref,
        }

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = _parse_agent_task_route(parsed.path)
        if route is None or route[0] not in {"collection", "task", "events"}:
            return super().do_GET()

        self._init_request_context()
        scope = self._agent_task_request_scope(route[1])
        if scope is None:
            return None
        project, _actor = scope
        tenant_id = self._request_tenant()
        try:
            if route[0] == "collection":
                items = list_agent_tasks(
                    self._root(), tenant_id=tenant_id, project_id=project,
                )
                return self._json({
                    "ok": True,
                    "schema_version": "qualibug.agent-task-list.v1",
                    "project_id": project,
                    "items": items,
                })
            if route[0] == "events":
                items = list_agent_task_events(
                    self._root(),
                    tenant_id=tenant_id,
                    project_id=project,
                    task_id=route[2],
                )
                return self._json(
                    {
                        "ok": True,
                        "schema_version": "qualibug.agent-task-event-list.v1",
                        "project_id": project,
                        "task_id": route[2],
                        "items": items,
                    }
                )
            task = get_agent_task(
                self._root(),
                tenant_id=tenant_id,
                project_id=project,
                task_id=route[2],
            )
            return self._json({"ok": True, "data": task})
        except Exception as exc:
            return self._agent_task_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = _parse_agent_task_route(parsed.path)
        if route is None or route[0] not in {"collection", "ground", "cancel", "execute"}:
            return super().do_POST()

        self._init_request_context()
        scope = self._agent_task_request_scope(route[1])
        if scope is None:
            return None
        project, actor = scope
        if not self._require_role(actor, _AGENT_TASK_ROLES, "agent task mutation"):
            return None

        tenant_id = self._request_tenant()
        correlation_id = _text(getattr(self, "_qualibug_corr_id", ""))
        try:
            if route[0] == "cancel":
                from .scan_cancellation import request_scan_cancel
                from .agent_task_store import record_agent_task_cancel_request
                existing = get_agent_task(self._root(), tenant_id=tenant_id, project_id=project, task_id=route[2])
                if existing.get("execution_claim_status") not in {None, "", "NOT_CLAIMED"}:
                    if existing.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
                        return self._json({"ok": True, "data": existing})
                    token = _text(existing.get("execution_lease_token"))
                    if not token:
                        raise AgentTaskConflict("agent_task_execution_owner_unknown")
                    cancellation = request_scan_cancel(self._root(), project, requester=actor, expected_token=token)
                    if cancellation.get("requested") is not True:
                        raise AgentTaskConflict(_text(cancellation.get("reason_code")))
                    task = record_agent_task_cancel_request(self._root(), tenant_id=tenant_id, project_id=project, task_id=route[2])
                    return self._json({"ok": True, "data": task})
                task = cancel_agent_task(
                    self._root(),
                    tenant_id=tenant_id,
                    project_id=project,
                    task_id=route[2],
                    correlation_id=correlation_id,
                )
                return self._json({"ok": True, "data": task})

            body = self._agent_task_body()
            if body is None:
                return None

            if route[0] == "execute":
                return self._handle_v12_scan(project, self._root(), actor, body, agent_task_id=route[2])

            if route[0] == "ground":
                existing = get_agent_task(
                    self._root(),
                    tenant_id=tenant_id,
                    project_id=project,
                    task_id=route[2],
                )
                task, grounding = self._ground_agent_task(
                    project=project,
                    tenant_id=tenant_id,
                    task=existing,
                    correlation_id=correlation_id,
                    body=body,
                )
                return self._json(
                    {
                        "ok": True,
                        "schema_version": "qualibug.agent-task-grounding.v1",
                        "data": task,
                        "grounding": grounding,
                    }
                )

            created = create_agent_task(
                self._root(),
                tenant_id=tenant_id,
                project_id=project,
                goal=_text(body.get("goal")),
                intent=_text(body.get("intent")),
                actor_role=_text(actor.get("role")) if isinstance(actor, dict) else "",
                correlation_id=correlation_id,
            )
            task, grounding = self._ground_agent_task(
                project=project,
                tenant_id=tenant_id,
                task=created,
                correlation_id=correlation_id,
            )
            return self._json(
                {
                    "ok": True,
                    "schema_version": "qualibug.agent-task-create.v2",
                    "data": task,
                    "grounding": grounding,
                },
                201,
            )
        except Exception as exc:
            return self._agent_task_error(exc)
