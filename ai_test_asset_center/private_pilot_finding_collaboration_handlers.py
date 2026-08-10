"""Finding collaboration HTTP/projection layer.

This mixin is intentionally orthogonal to finding evidence authority. It exposes
human workflow metadata, projects the stable SQLite finding id onto display-ready
rows, and makes replay status persistence use that stable id instead of a UI id.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import db_persistence as db_persist
from .finding_collaboration import (
    annotate_command_center_collaboration,
    list_finding_collaboration,
    update_finding_collaboration,
)
from .private_pilot_json_io import _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

_COLLABORATION_PATH = "/api/v1/findings/collaboration"


def _text(value: Any) -> str:
    return str(value or "").strip()


class FindingCollaborationHandlersMixin:
    def _build_command_center(self, project_id: str, root: Path) -> dict:
        payload = super()._build_command_center(project_id, root)
        if not isinstance(payload, dict):
            return payload
        try:
            tenant_id = self._request_tenant()
            payload = annotate_command_center_collaboration(
                payload,
                root,
                tenant_id,
                project_id,
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            defects = data.get("defects") if isinstance(data.get("defects"), list) else []
            data["finding_collaboration_projection"] = {
                "status": "available",
                "persistence_bound_count": sum(
                    1
                    for item in defects
                    if isinstance(item, dict) and _text(item.get("finding_persistence_id"))
                ),
                "display_count": len(defects),
                "authority": "sqlite.findings + finding_collaboration",
            }
            payload["data"] = data
        except Exception as exc:
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            data["finding_collaboration_projection"] = {
                "status": "unavailable",
                "message": f"{type(exc).__name__}: {str(exc)[:240]}",
                "finding_evidence_affected": False,
            }
            payload["data"] = data
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != _COLLABORATION_PATH:
            return super().do_GET()

        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        query = parse_qs(parsed.query)
        try:
            project = _safe_project_id(
                _text((query.get("project") or [""])[0] or self._project())
            )
        except ValueError:
            return self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
        if not self._require_project_scope(project):
            return None
        try:
            items = list_finding_collaboration(
                root,
                self._request_tenant(),
                project,
            )
            return self._json(
                {
                    "ok": True,
                    "schema_version": "qualibug.finding-collaboration.v1",
                    "project_id": project,
                    "items": items,
                }
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "FINDING_COLLABORATION_READ_FAILED",
                    "message": str(exc)[:300],
                },
                500,
            )

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != _COLLABORATION_PATH:
            return super().do_POST()

        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        from . import private_pilot_service as service

        if not self._require_role(
            actor,
            service.CONFIG_MANAGER_ROLES,
            "finding collaboration update",
        ):
            return None
        try:
            body = self._body()
            project = _safe_project_id(
                _text(body.get("project_id") or body.get("project") or self._project())
            )
            if not self._require_project_scope(project):
                return None
            finding_id = _text(
                body.get("finding_persistence_id") or body.get("finding_id")
            )
            patch = body.get("patch") if isinstance(body.get("patch"), dict) else {
                key: body[key]
                for key in (
                    "workflow_status",
                    "assignee",
                    "fix_version",
                    "developer_feedback",
                    "disposition",
                    "disposition_note",
                    "external_issue_url",
                )
                if key in body
            }
            updated = update_finding_collaboration(
                root,
                self._request_tenant(),
                project,
                finding_id,
                patch,
                actor_name=_text(actor.get("name") or actor.get("username")),
            )
            return self._json(
                {
                    "ok": True,
                    "schema_version": "qualibug.finding-collaboration.v1",
                    "project_id": project,
                    "item": updated,
                }
            )
        except KeyError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "FINDING_NOT_FOUND",
                    "message": str(exc),
                },
                404,
            )
        except (ValueError, TypeError) as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "BAD_REQUEST",
                    "message": str(exc),
                },
                400,
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "FINDING_COLLABORATION_UPDATE_FAILED",
                    "message": str(exc)[:300],
                },
                500,
            )

    def _handle_replay(
        self,
        project: str,
        root: Path,
        body: dict[str, Any],
    ) -> None:
        """Replay by display id, persist conclusive status by SQLite finding id."""

        finding_id = _text(body.get("finding_id"))
        base_url_override = _text(body.get("base_url"))
        if not finding_id:
            return self._json(
                {
                    "ok": False,
                    "error": "MISSING_FINDING_ID",
                    "message": "finding_id is required",
                },
                400,
            )
        phase = "command_center"
        target_status = ""
        persistence_id = ""
        result: dict[str, Any] = {}
        try:
            tenant_id = self._request_tenant()
            command_center = self._build_command_center(project, root)
            if not isinstance(command_center, dict):
                raise TypeError("command-center replay source must be an object")
            command_data = command_center.get("data")
            if not isinstance(command_data, dict):
                raise ValueError("command-center replay data must be an object")
            risks = command_data.get("risks") or []
            if not isinstance(risks, list) or any(
                not isinstance(risk, dict) for risk in risks
            ):
                raise ValueError("command-center replay risks must be a list of objects")

            selected = next(
                (
                    risk
                    for risk in risks
                    if _text(risk.get("id")) == finding_id
                ),
                None,
            )
            if isinstance(selected, dict):
                persistence_id = _text(selected.get("finding_persistence_id"))

            phase = "replay_execution"
            from .replay_engine import ReplayEngine

            result = ReplayEngine(root, project).replay(
                finding_id,
                risks,
                base_url_override,
            )
            if not isinstance(result, dict):
                raise TypeError("replay result must be an object")

            verdict = _text(result.get("verdict")).lower()
            if result.get("ok") is True and verdict in {"not_reproduced", "reproduced"}:
                phase = "status_persistence"
                if not persistence_id:
                    return self._json(
                        {
                            "ok": False,
                            "error": "FINDING_PERSISTENCE_ID_UNRESOLVED",
                            "message": (
                                "回放已得到明确结论，但该展示 Finding 无法唯一映射到 SQLite 持久化记录；"
                                "系统未写入任何状态，避免更新错误 Bug。"
                            ),
                            "finding_id": finding_id,
                            "replay_result": result,
                        },
                        409,
                    )
                target_status = "resolved" if verdict == "not_reproduced" else "open"
                status_updated = db_persist.update_finding_status(
                    root,
                    persistence_id,
                    target_status,
                    tenant_id=tenant_id,
                    project_id=project,
                )
                if status_updated is not True:
                    raise RuntimeError(
                        f"finding status persistence did not update finding: {persistence_id}"
                    )
                result["finding_status"] = target_status
                result["finding_persistence_id"] = persistence_id
                result["message"] = (
                    "明确复现 Oracle 已不满足，Bug 标记为已修复。"
                    if target_status == "resolved"
                    else "复现 Oracle 仍满足，Bug 保持打开。"
                )
            elif result.get("ok") is True:
                result["finding_status"] = "unchanged"
                result["finding_persistence_id"] = persistence_id
                result["message"] = "证据不足，未改变 Bug 状态。"
            return self._json(result)
        except Exception as exc:
            _write_json_object_atomic(
                root / "platform_outputs" / project / "replay_last_error.json",
                {
                    "schema": "qualibug.replay-failure.v1",
                    "project": project,
                    "finding_id": finding_id,
                    "finding_persistence_id": persistence_id,
                    "phase": phase,
                    "target_status": target_status,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            error_code = (
                "REPLAY_STATUS_PERSIST_FAILED"
                if phase == "status_persistence"
                else "REPLAY_FAILED"
            )
            response: dict[str, Any] = {
                "ok": False,
                "finding_id": finding_id,
                "finding_persistence_id": persistence_id,
                "error": error_code,
                "message": str(exc),
            }
            if result:
                response["replay_result"] = result
            return self._json(response, 500)


__all__ = ["FindingCollaborationHandlersMixin"]
