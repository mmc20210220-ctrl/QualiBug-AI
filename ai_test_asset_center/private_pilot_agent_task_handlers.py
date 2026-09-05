"""HTTP contract for project-scoped Agent Tasks and their event ledger."""
from __future__ import annotations

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
        if route is None or route[0] not in {"collection", "ground", "cancel"}:
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
