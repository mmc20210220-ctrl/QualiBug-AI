"""Finding collaboration, evidence sharing and replay identity HTTP layer.

Human workflow metadata is orthogonal to automated finding evidence authority.
Public evidence shares are frozen redacted snapshots behind opaque expiring
capabilities; they never authorize access to the tenant/project runtime.
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
from .finding_evidence_shares import (
    build_external_finding_snapshot,
    create_finding_evidence_share,
    list_finding_evidence_shares,
    resolve_finding_evidence_share,
    revoke_finding_evidence_share,
)
from .private_pilot_json_io import _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

_COLLABORATION_PATH = "/api/v1/findings/collaboration"
_EVIDENCE_SHARES_PATH = "/api/v1/findings/evidence-shares"
_EVIDENCE_SHARE_REVOKE_PATH = "/api/v1/findings/evidence-shares/revoke"
_PUBLIC_EVIDENCE_SHARE_RESOLVE_PATH = "/api/public/v1/evidence-share/resolve"


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
        if parsed.path not in {_COLLABORATION_PATH, _EVIDENCE_SHARES_PATH}:
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

        if parsed.path == _EVIDENCE_SHARES_PATH:
            finding_id = _text((query.get("finding_persistence_id") or [""])[0])
            if not finding_id:
                return self._json(
                    {
                        "ok": False,
                        "error": "MISSING_FINDING_PERSISTENCE_ID",
                        "message": "finding_persistence_id is required",
                    },
                    400,
                )
            try:
                items = list_finding_evidence_shares(
                    root,
                    self._request_tenant(),
                    project,
                    finding_id,
                )
                return self._json(
                    {
                        "ok": True,
                        "schema_version": "qualibug.finding-evidence-share-list.v1",
                        "project_id": project,
                        "finding_persistence_id": finding_id,
                        "items": items,
                    }
                )
            except Exception as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "EVIDENCE_SHARE_LIST_FAILED",
                        "message": str(exc)[:300],
                    },
                    500,
                )

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

        if parsed.path == _PUBLIC_EVIDENCE_SHARE_RESOLVE_PATH:
            self._init_request_context()
            try:
                body = self._body()
                resolved = resolve_finding_evidence_share(
                    self._root(),
                    _text(body.get("token")),
                )
            except (ValueError, TypeError):
                resolved = None
            if not resolved:
                return self._json(
                    {
                        "ok": False,
                        "error": "SHARE_NOT_FOUND_OR_EXPIRED",
                        "message": "该分享链接不存在、已过期或已被撤销。",
                    },
                    404,
                )
            return self._json(
                {
                    "ok": True,
                    "schema_version": "qualibug.public-finding-evidence-share.v1",
                    **resolved,
                }
            )

        if parsed.path not in {
            _COLLABORATION_PATH,
            _EVIDENCE_SHARES_PATH,
            _EVIDENCE_SHARE_REVOKE_PATH,
        }:
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
            "finding collaboration or evidence sharing",
        ):
            return None
        try:
            body = self._body()
            project = _safe_project_id(
                _text(body.get("project_id") or body.get("project") or self._project())
            )
            if not self._require_project_scope(project):
                return None

            if parsed.path == _EVIDENCE_SHARE_REVOKE_PATH:
                revoked = revoke_finding_evidence_share(
                    root,
                    self._request_tenant(),
                    project,
                    _text(body.get("share_id")),
                )
                if not revoked:
                    return self._json(
                        {
                            "ok": False,
                            "error": "SHARE_NOT_FOUND",
                            "message": "分享记录不存在、已撤销或不属于当前项目。",
                        },
                        404,
                    )
                return self._json({"ok": True, "revoked": True})

            finding_id = _text(
                body.get("finding_persistence_id") or body.get("finding_id")
            )

            if parsed.path == _EVIDENCE_SHARES_PATH:
                if not finding_id:
                    raise ValueError("finding_persistence_id is required")
                command_center = self._build_command_center(project, root)
                data = command_center.get("data") if isinstance(command_center, dict) and isinstance(command_center.get("data"), dict) else {}
                defects = data.get("defects") if isinstance(data.get("defects"), list) else []
                selected = next(
                    (
                        item
                        for item in defects
                        if isinstance(item, dict)
                        and _text(item.get("finding_persistence_id")) == finding_id
                    ),
                    None,
                )
                if not isinstance(selected, dict):
                    return self._json(
                        {
                            "ok": False,
                            "error": "FINDING_SHARE_SOURCE_UNRESOLVED",
                            "message": (
                                "当前问题未能唯一绑定到可交付 Finding；"
                                "系统不会通过标题猜测生成外部分享。"
                            ),
                        },
                        409,
                    )
                snapshot = build_external_finding_snapshot(
                    selected,
                    project_name=_text(data.get("project_name") or project),
                )
                share = create_finding_evidence_share(
                    root,
                    self._request_tenant(),
                    project,
                    finding_id,
                    snapshot,
                    ttl_seconds=int(body.get("ttl_seconds") or 24 * 60 * 60),
                    actor_name=_text(actor.get("name") or actor.get("username")),
                )
                return self._json(
                    {
                        "ok": True,
                        "schema_version": "qualibug.finding-evidence-share.v1",
                        "share": {
                            **share,
                            "share_path": f"/shared-evidence#{share['token']}",
                        },
                    },
                    201,
                )

            patch = body.get("patch") if isinstance(body.get("patch"), dict) else {
                key: body[key]
                for key in (
                    "handling_status",
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
                    "error": "FINDING_COLLABORATION_OR_SHARE_UPDATE_FAILED",
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
