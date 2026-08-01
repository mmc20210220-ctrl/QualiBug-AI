"""Authenticated HTTP surface for enterprise identity measurement and review."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .enterprise_knowledge_center.transaction_lock import KnowledgeTransactionBusy
from .private_pilot_debug_client import _dbg_report
from .real_project_onboarding import _safe_project_id


def _text(value: Any) -> str:
    return str(value or "").strip()


def _route(path: str) -> tuple[str, str]:
    parts = [unquote(part) for part in urlparse(path).path.split("/") if part]
    if len(parts) < 5 or parts[:3] != ["api", "v1", "projects"]:
        return "", ""
    if parts[4] != "identity-benchmark":
        return "", ""
    if len(parts) == 5:
        return parts[3], "workspace"
    if len(parts) == 6 and parts[5] in {
        "manifest",
        "annotation-package",
        "annotation-compile",
        "ground-truth",
        "quality-policy",
        "run",
        "structural-review",
        "structural-review-decision",
    }:
        return parts[3], parts[5]
    return "", ""


class IdentityBenchmarkHttpMixin:
    """Route identity measurement and review before the canonical general router."""

    def _identity_benchmark_error(self, exc: Exception, *, project: str) -> Any:
        detail = _text(exc)
        if isinstance(exc, PermissionError):
            return self._json(
                {"ok": False, "error": "FORBIDDEN", "message": detail}, 403
            )
        if isinstance(exc, KnowledgeTransactionBusy):
            return self._json(
                {
                    "ok": False,
                    "error": "IDENTITY_BENCHMARK_TRANSACTION_BUSY",
                    "message": "该项目正在执行另一项知识变更，请稍后重试。",
                    "retryable": True,
                },
                409,
            )
        if isinstance(exc, KeyError):
            return self._json(
                {
                    "ok": False,
                    "error": "IDENTITY_BENCHMARK_NOT_FOUND",
                    "message": detail,
                },
                404,
            )
        if isinstance(exc, ValueError):
            return self._json(
                {
                    "ok": False,
                    "error": "IDENTITY_BENCHMARK_BAD_REQUEST",
                    "message": detail,
                },
                400,
            )
        _dbg_report(
            hypothesis_id="IDENTITY_BENCHMARK_API",
            msg="[ERROR] identity benchmark route failed",
            data={
                "project_id": project,
                "path": _text(getattr(self, "path", "")),
                "exc_type": type(exc).__name__,
            },
            trace_id=_text(getattr(self, "_qualibug_corr_id", "")),
        )
        return self._json(
            {
                "ok": False,
                "error": "IDENTITY_BENCHMARK_INTERNAL_ERROR",
                "message": "身份标注、评测与裁决资源暂时不可用。",
            },
            500,
        )

    def _identity_benchmark_context(
        self, raw_project: str
    ) -> tuple[str, Path, dict[str, Any]] | None:
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        try:
            project = _safe_project_id(raw_project)
        except ValueError:
            self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
            return None
        if not self._require_project_scope(project):
            return None
        if not self._require_known_project(project, root):
            return None
        from . import private_pilot_service as service

        if not self._require_role(
            actor,
            service.KNOWLEDGE_MANAGER_ROLES,
            "enterprise identity benchmark",
        ):
            return None
        return project, root, actor

    def do_GET(self) -> None:  # noqa: N802
        raw_project, action = _route(_text(getattr(self, "path", "")))
        if not raw_project or action not in {
            "workspace",
            "manifest",
            "annotation-package",
            "structural-review",
        }:
            return super().do_GET()
        self._init_request_context()
        context = self._identity_benchmark_context(raw_project)
        if context is None:
            return None
        project, root, _actor = context
        try:
            if action == "annotation-package":
                from .enterprise_knowledge_center.enterprise_understanding.identity_annotation_operator import (
                    get_identity_annotation_task_package,
                )

                return self._json(
                    {
                        "ok": True,
                        "project_id": project,
                        "data": get_identity_annotation_task_package(project, root),
                    }
                )
            if action == "structural-review":
                from .enterprise_knowledge_center.enterprise_understanding.identity_structural_review import (
                    get_identity_structural_review_queue,
                )

                return self._json(
                    {
                        "ok": True,
                        "project_id": project,
                        "data": get_identity_structural_review_queue(project, root),
                    }
                )

            from .enterprise_knowledge_center.enterprise_understanding.identity_benchmark_workflow import (
                get_identity_benchmark_workspace,
            )

            workspace = get_identity_benchmark_workspace(project, root)
        except Exception as exc:
            return self._identity_benchmark_error(exc, project=project)
        if action == "manifest":
            return self._json(
                {
                    "ok": True,
                    "project_id": project,
                    "data": workspace.get("manifest") or {},
                }
            )
        return self._json({"ok": True, "data": workspace})

    def do_POST(self) -> None:  # noqa: N802
        raw_project, action = _route(_text(getattr(self, "path", "")))
        if not raw_project or action not in {
            "annotation-compile",
            "ground-truth",
            "quality-policy",
            "run",
            "structural-review-decision",
        }:
            return super().do_POST()
        self._init_request_context()
        context = self._identity_benchmark_context(raw_project)
        if context is None:
            return None
        project, root, actor = context
        try:
            body = self._body()
            if not isinstance(body, dict):
                raise ValueError("identity_benchmark_request_body_must_be_object")
            if action == "annotation-compile":
                from .enterprise_knowledge_center.enterprise_understanding.identity_annotation_operator import (
                    compile_and_import_identity_annotations,
                )

                result = compile_and_import_identity_annotations(
                    project,
                    body,
                    actor=actor,
                    root=root,
                )
                status = 201 if result.get("ground_truth_imported") is True else 200
                return self._json({"ok": True, "data": result}, status)
            if action == "structural-review-decision":
                from .enterprise_knowledge_center.enterprise_understanding.identity_structural_review import (
                    record_identity_structural_review_decision,
                )

                result = record_identity_structural_review_decision(
                    project,
                    candidate_id=_text(body.get("candidate_id")),
                    action=_text(body.get("action")),
                    canonical_entity_id=_text(body.get("canonical_entity_id")),
                    rationale=_text(body.get("rationale")),
                    actor=actor,
                    root=root,
                    rebuild=True,
                )
                return self._json({"ok": True, "data": result}, 201)

            if action == "run":
                from .enterprise_knowledge_center.enterprise_understanding.identity_benchmark_workflow import (
                    run_identity_benchmark,
                )

                result = run_identity_benchmark(
                    project,
                    actor=actor,
                    root=root,
                )
                return self._json({"ok": True, "data": result}, 201)

            if action == "ground-truth":
                from .enterprise_knowledge_center.enterprise_understanding.identity_benchmark_workflow import (
                    import_identity_ground_truth,
                )

                payload = body.get("ground_truth") or body.get("payload") or body
                if not isinstance(payload, dict):
                    raise ValueError("identity_ground_truth_payload_required")
                result = import_identity_ground_truth(
                    project,
                    payload,
                    manifest_id=_text(
                        body.get("manifest_id")
                        or body.get("annotation_manifest_id")
                        or payload.get("manifest_id")
                        or payload.get("annotation_manifest_id")
                    ),
                    actor=actor,
                    root=root,
                    rebuild=True,
                )
                return self._json({"ok": True, "data": result}, 201)

            from .enterprise_knowledge_center.enterprise_understanding.identity_benchmark_workflow import (
                update_identity_quality_policy,
            )

            payload = body.get("quality_policy") or body.get("payload") or body
            if not isinstance(payload, dict):
                raise ValueError("identity_quality_policy_payload_required")
            result = update_identity_quality_policy(
                project,
                payload,
                actor=actor,
                root=root,
                rebuild=True,
            )
            return self._json({"ok": True, "data": result}, 201)
        except Exception as exc:
            return self._identity_benchmark_error(exc, project=project)


__all__ = ["IdentityBenchmarkHttpMixin"]
