"""Operational API handlers: campaign, replay, evidence, settings, metadata."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist
from .enterprise_pilot_runtime import build_enterprise_pilot_overview
from .private_pilot_json_io import _write_json_object_atomic
from .private_pilot_project_assets import _write_env_local
from .real_project_onboarding import _safe_project_id


class OpsHandlersMixin:
    def _handle_campaign_get(
        self,
        project: str,
        route: list[str],
        query: dict[str, list[str]],
        root: Path,
    ) -> None:
        """Serve the versioned campaign/read-model resources from one SSOT."""
        from .campaign_api_contract import (
            CampaignContractError,
            build_campaign_view,
            campaign_slices,
            finding_resource,
            finding_rows,
            structured_error,
        )

        try:
            campaign_id = route[0] if route else ""
            view = build_campaign_view(root, project, campaign_id)
            if not route:
                summary = {
                    key: view.get(key)
                    for key in (
                        "schema_version",
                        "campaign_id",
                        "project_id",
                        "status",
                        "pipeline_health",
                        "execution_status",
                        "selected_experiment_count",
                        "every_selected_experiment_has_receipt",
                        "formal_count_projection",
                        "external_evaluation",
                        "fingerprint",
                    )
                }
                return self._json({"ok": True, "data": [summary]})
            if len(route) == 1:
                return self._json({"ok": True, "data": view})
            resource = route[1]
            if resource == "slices" and len(route) == 2:
                return self._json(
                    {
                        "ok": True,
                        "data": campaign_slices(view),
                        "campaign_id": campaign_id,
                    }
                )
            if resource in {"identity-traces", "identity_traces"} and len(route) == 2:
                return self._json(
                    {
                        "ok": True,
                        "data": view.get("identity_traces") or [],
                        "campaign_id": campaign_id,
                    }
                )
            if resource == "findings" and len(route) == 2:
                classification = str(
                    (query.get("classification") or ["deliverable"])[0]
                )
                return self._json(
                    {
                        "ok": True,
                        "classification": classification,
                        "data": finding_rows(view, classification),
                        "campaign_id": campaign_id,
                    }
                )
            if (
                resource == "findings"
                and len(route) == 4
                and route[3] in {"evidence", "replay"}
            ):
                classification, finding = finding_resource(view, route[2])
                if route[3] == "evidence":
                    evidence = {
                        "finding_id": route[2],
                        "classification": classification,
                        "evidence": finding.get("evidence")
                        or finding.get("raw_evidence")
                        or {},
                        "evidence_chain": finding.get("evidence_chain") or [],
                        "source_refs": finding.get("source_refs")
                        or finding.get("doc_refs")
                        or [],
                    }
                    return self._json({"ok": True, "data": evidence})
                replay = {
                    "finding_id": route[2],
                    "classification": classification,
                    "reproduction": finding.get("reproduction") or {},
                    "replay_allowed": classification == "deliverable",
                }
                return self._json({"ok": True, "data": replay})
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        except CampaignContractError as exc:
            error = structured_error(
                stage="campaign_api",
                code="CAMPAIGN_RESOURCE_UNAVAILABLE",
                identity={
                    "project_id": project,
                    "campaign_id": route[0] if route else "",
                },
                retryability="after_new_scan_or_operator_action",
                operator_action=str(exc),
            )
            return self._json({"ok": False, "error": error}, 404)

    def _handle_db_test(self, body: dict[str, Any]) -> None:
        """Validate that a database DSN was provided without echoing secrets."""
        dsn = str(body.get("dsn") or "").strip()
        if not dsn:
            return self._json(
                {"ok": False, "error": "MISSING_DSN", "message": "Missing DSN."},
                400,
            )
        scheme = dsn.split(":", 1)[0].lower() if ":" in dsn else "unknown"
        return self._json(
            {
                "ok": True,
                "message": "DSN accepted for validation.",
                "db_type": scheme,
            }
        )

    def _handle_replay(
        self,
        project: str,
        root: Path,
        body: dict[str, Any],
    ) -> None:
        """Re-verify one defect and update state only from a conclusive oracle."""

        finding_id = str(body.get("finding_id") or "").strip()
        base_url_override = str(body.get("base_url") or "").strip()
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
                raise ValueError(
                    "command-center replay risks must be a list of objects"
                )

            phase = "replay_execution"
            from .replay_engine import ReplayEngine

            result = ReplayEngine(root, project).replay(
                finding_id,
                risks,
                base_url_override,
            )
            if not isinstance(result, dict):
                raise TypeError("replay result must be an object")

            verdict = str(result.get("verdict") or "").strip().lower()
            if result.get("ok") is True and verdict == "not_reproduced":
                phase = "status_persistence"
                target_status = "resolved"
                status_updated = db_persist.update_finding_status(
                    root,
                    finding_id,
                    target_status,
                    tenant_id=tenant_id,
                    project_id=project,
                )
                if status_updated is not True:
                    raise RuntimeError(
                        f"finding status persistence did not update finding: {finding_id}"
                    )
                result["finding_status"] = target_status
                result["message"] = "明确复现 Oracle 已不满足，Bug 标记为已修复。"
            elif result.get("ok") is True and verdict == "reproduced":
                phase = "status_persistence"
                target_status = "open"
                status_updated = db_persist.update_finding_status(
                    root,
                    finding_id,
                    target_status,
                    tenant_id=tenant_id,
                    project_id=project,
                )
                if status_updated is not True:
                    raise RuntimeError(
                        f"finding status persistence did not update finding: {finding_id}"
                    )
                result["finding_status"] = target_status
                result["message"] = "复现 Oracle 仍满足，Bug 保持打开。"
            elif result.get("ok") is True:
                result["finding_status"] = "unchanged"
                result["message"] = "证据不足，未改变 Bug 状态。"
            return self._json(result)
        except Exception as exc:
            _write_json_object_atomic(
                root / "platform_outputs" / project / "replay_last_error.json",
                {
                    "schema": "qualibug.replay-failure.v1",
                    "project": project,
                    "finding_id": finding_id,
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
                "error": error_code,
                "message": str(exc),
            }
            if result:
                response["replay_result"] = result
            return self._json(response, 500)

    def _handle_get_project_metadata(self, project: str, root: Path) -> None:
        from .enterprise_project_config import MultiServiceProject

        try:
            project_config = MultiServiceProject(project, root)
            return self._json(
                {
                    "ok": True,
                    "project": project,
                    **project_config.project_metadata(),
                }
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "METADATA_READ_FAILED",
                    "message": str(exc)[:300],
                },
                500,
            )

    def _handle_save_project_metadata(
        self,
        project: str,
        root: Path,
        body: dict[str, Any],
    ) -> None:
        from .enterprise_project_config import MultiServiceProject

        try:
            project_config = MultiServiceProject(project, root)
            updated = project_config.set_project_metadata(
                industry=body.get("industry") if "industry" in body else None,
                module_scope=body.get("module_scope")
                if "module_scope" in body
                else None,
                production_data_exclusion=body.get("production_data_exclusion")
                if "production_data_exclusion" in body
                else None,
            )
            return self._json(
                {
                    "ok": True,
                    "project": project,
                    "saved": project_config.project_metadata(),
                    "services_count": len(updated.get("services", [])),
                }
            )
        except (ValueError, TypeError) as exc:
            return self._json(
                {"ok": False, "error": "BAD_REQUEST", "message": str(exc)},
                400,
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "METADATA_SAVE_FAILED",
                    "message": str(exc)[:300],
                },
                500,
            )

    def _get_spectrum_status(self, project: str, root: Path) -> None:
        result_file = (
            root / "platform_outputs" / project / "spectrum" / "spectrum_result.json"
        )
        timestamp_file = (
            root / "platform_outputs" / project / "spectrum" / "spectrum_timestamp.txt"
        )
        if not result_file.exists():
            return self._json(
                {"ok": True, "status": "not_run", "message": "尚未运行全频谱检测"}
            )
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            last_run = (
                timestamp_file.read_text(encoding="utf-8").strip()
                if timestamp_file.exists()
                else ""
            )
            return self._json(
                {"ok": True, "status": "completed", "last_run": last_run, **result}
            )
        except Exception:
            return self._json(
                {"ok": True, "status": "error", "message": "无法读取检测结果"}
            )

    def _handle_reanalyze(
        self,
        project: str,
        root: Path,
        actor: dict[str, str],
    ) -> None:
        del actor
        try:
            from .enterprise_knowledge_center import (
                build_enterprise_business_knowledge_asset,
            )

            build_enterprise_business_knowledge_asset(project, root)
            build_enterprise_pilot_overview(project, root)
            dashboard = (
                root
                / "platform_outputs"
                / project
                / "enterprise_pilot_runtime"
                / "enterprise_pilot_center.html"
            )
            if dashboard.exists():
                dashboard.unlink()
            return self._json(
                {"ok": True, "message": "Knowledge base reanalysis completed."}
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "REANALYZE_FAILED",
                    "message": str(exc)[:300],
                },
                500,
            )

    def _handle_preview(
        self,
        project: str,
        body_or_source: dict[str, Any] | str,
        root: Path,
        actor: dict[str, Any] | None = None,
    ) -> None:
        source_id = (
            str(body_or_source.get("source_id") or "").strip()
            if isinstance(body_or_source, dict)
            else str(body_or_source).strip()
        )
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID"}, 400)
        try:
            from .connector_acl_authority import connector_source_visibility_decision
            from .enterprise_knowledge_center import list_enterprise_knowledge_sources

            inventory = list_enterprise_knowledge_sources(
                project,
                root=root,
                include_deleted=True,
            )
            connector_source = next(
                (
                    row
                    for row in inventory.get("sources") or []
                    if isinstance(row, dict)
                    and source_id
                    in {
                        str(row.get("source_id") or ""),
                        str(row.get("source_occurrence_id") or ""),
                        str(row.get("source_ref") or ""),
                    }
                    and str(row.get("source_ref") or "").startswith("connector://")
                ),
                None,
            )
            if connector_source is not None:
                decision = connector_source_visibility_decision(
                    project,
                    source_ref=str(connector_source.get("source_ref") or ""),
                    actor={**actor, "project_id": project} if actor else actor,
                    root=root,
                )
                if decision.get("allowed") is not True:
                    return self._json(
                        {
                            "ok": False,
                            "error": "SOURCE_NOT_VISIBLE",
                            "reason_code": decision.get("reason_code"),
                            "source_content_returned": False,
                        },
                        404,
                    )
            from .enterprise_knowledge_center import _load_registry

            registry = _load_registry(project, root)
            for source in registry.get("sources", []):
                if not isinstance(source, dict) or source.get("source_id") != source_id:
                    continue
                stored_path = str(source.get("stored_path") or "").strip()
                if not stored_path:
                    break
                source_path = (root / stored_path).resolve()
                root_resolved = root.resolve()
                if root_resolved != source_path and root_resolved not in source_path.parents:
                    return self._json(
                        {"ok": False, "error": "INVALID_STORED_PATH"},
                        400,
                    )
                if source_path.exists() and source_path.is_file():
                    text = source_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )[:50000]
                    return self._json(
                        {
                            "ok": True,
                            "source_id": source_id,
                            "filename": source.get("original_name", ""),
                            "content": text,
                        }
                    )
            return self._json(
                {"ok": False, "error": "NOT_FOUND", "message": "File not found."},
                404,
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "PREVIEW_FAILED",
                    "message": str(exc)[:300],
                },
                500,
            )

    def _handle_evidence_artifact(
        self,
        project: str,
        ref: str,
        root: Path,
    ) -> None:
        """Serve non-executable evidence only from the project browser-run tree."""

        relative_ref = str(ref or "").strip().lstrip("/\\")
        if not relative_ref:
            return self._json({"ok": False, "error": "MISSING_ARTIFACT_REF"}, 400)
        allowed_root = (
            root
            / "platform_workspace"
            / _safe_project_id(project)
            / "browser_runs"
        ).resolve()
        resolved = (root / relative_ref).resolve()
        try:
            resolved.relative_to(allowed_root)
        except ValueError:
            return self._json(
                {"ok": False, "error": "ARTIFACT_OUTSIDE_ALLOWED_SUBTREE"},
                403,
            )
        if not resolved.exists() or not resolved.is_file():
            return self._json({"ok": False, "error": "ARTIFACT_NOT_FOUND"}, 404)

        mime_by_suffix = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".har": "application/json",
            ".json": "application/json",
            ".zip": "application/zip",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".txt": "text/plain; charset=utf-8",
        }
        suffix = resolved.suffix.lower()
        mime = mime_by_suffix.get(suffix)
        if mime is None:
            return self._json({"ok": False, "error": "ARTIFACT_TYPE_BLOCKED"}, 415)
        try:
            file_size = resolved.stat().st_size
        except OSError:
            return self._json({"ok": False, "error": "ARTIFACT_READ_FAILED"}, 500)
        max_bytes = 50_000_000
        if file_size > max_bytes:
            return self._json({"ok": False, "error": "ARTIFACT_TOO_LARGE"}, 413)
        try:
            data = resolved.read_bytes()
        except OSError:
            return self._json({"ok": False, "error": "ARTIFACT_READ_FAILED"}, 500)
        try:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            if suffix in {".har", ".json", ".zip", ".txt"}:
                safe_name = resolved.name.replace('"', "")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{safe_name}"',
                )
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    def _handle_settings_save(self, body: dict[str, Any]) -> None:
        """Apply deployment-global LLM settings only for a signed platform admin."""

        actor = self._require_actor()
        if actor is None:
            return None
        if not self._require_role(
            actor,
            {"platform_admin"},
            "deployment-wide LLM settings update",
        ):
            return None
        updates: dict[str, str] = {}
        for key in ("llm_base_url", "llm_model", "llm_temperature", "llm_api_key"):
            if key in body and body[key] not in (None, ""):
                updates[key.upper()] = str(body[key])
        if updates:
            _write_env_local(updates)
            for key, value in updates.items():
                os.environ[key] = value
            try:
                from .llm_reasoning import reset_client

                reset_client()
            except Exception:
                pass
        for key in (
            "QUALIBUG_LLM_HEALTH_STATUS",
            "QUALIBUG_LLM_LAST_HEALTH_STATUS",
            "QUALIBUG_LLM_LAST_HEALTH_LABEL",
            "QUALIBUG_LLM_LAST_HEALTH_ERROR",
        ):
            os.environ.pop(key, None)
        llm_health = self._verify_llm_connectivity() if updates else self._llm_health()
        return self._json(
            {
                "ok": True,
                "llm_available": llm_health["available"],
                "llm_status": llm_health["status"],
                "llm_status_label": llm_health["label"],
                "llm_error": llm_health.get("error", ""),
                "message": "Deployment-wide LLM settings were saved.",
            }
        )
