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
                return self._json({"ok": True, "data": campaign_slices(view), "campaign_id": campaign_id})
            if resource in {"identity-traces", "identity_traces"} and len(route) == 2:
                return self._json({"ok": True, "data": view.get("identity_traces") or [], "campaign_id": campaign_id})
            if resource == "findings" and len(route) == 2:
                classification = str((query.get("classification") or ["deliverable"])[0])
                return self._json({
                    "ok": True,
                    "classification": classification,
                    "data": finding_rows(view, classification),
                    "campaign_id": campaign_id,
                })
            if resource == "findings" and len(route) == 4 and route[3] in {"evidence", "replay"}:
                classification, finding = finding_resource(view, route[2])
                if route[3] == "evidence":
                    evidence = {
                        "finding_id": route[2],
                        "classification": classification,
                        "evidence": finding.get("evidence") or finding.get("raw_evidence") or {},
                        "evidence_chain": finding.get("evidence_chain") or [],
                        "source_refs": finding.get("source_refs") or finding.get("doc_refs") or [],
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
                identity={"project_id": project, "campaign_id": route[0] if route else ""},
                retryability="after_new_scan_or_operator_action",
                operator_action=str(exc),
            )
            return self._json({"ok": False, "error": error}, 404)

    def _handle_db_test(self, body: dict[str, Any]) -> None:
        """Validate that a database DSN was provided without echoing secrets."""
        dsn = str(body.get("dsn") or "").strip()
        if not dsn:
            return self._json({"ok": False, "error": "MISSING_DSN", "message": "Missing DSN."}, 400)
        scheme = dsn.split(":", 1)[0].lower() if ":" in dsn else "unknown"
        return self._json({"ok": True, "message": "DSN accepted for validation.", "db_type": scheme})

    def _handle_replay(self, project: str, root: Path, body: dict[str, Any]) -> None:
        """Handle replay request: re-execute finding against live test environment.

        If replay shows the bug no longer reproduces (success=False), mark the
        finding as 'resolved' in the cumulative store so it drops off the open
        bug shelf.
        """
        finding_id = str(body.get("finding_id") or "").strip()
        base_url_override = str(body.get("base_url") or "").strip()
        if not finding_id:
            return self._json({"ok": False, "error": "MISSING_FINDING_ID", "message": "finding_id is required"}, 400)
        phase = "command_center"
        target_status = ""
        result: dict[str, Any] = {}
        try:
            command_center = self._build_command_center(project, root)
            if not isinstance(command_center, dict):
                raise TypeError("command-center replay source must be an object")
            command_data = command_center.get("data")
            if not isinstance(command_data, dict):
                raise ValueError("command-center replay data must be an object")
            risks = command_data.get("risks") or []
            if not isinstance(risks, list) or any(not isinstance(risk, dict) for risk in risks):
                raise ValueError("command-center replay risks must be a list of objects")

            phase = "replay_execution"
            from .replay_engine import ReplayEngine
            engine = ReplayEngine(root, project)
            result = engine.replay(finding_id, risks, base_url_override)
            if not isinstance(result, dict):
                raise TypeError("replay result must be an object")

            if result.get("ok") is True and result.get("success") is False:
                phase = "status_persistence"
                target_status = "resolved"
                status_updated = db_persist.update_finding_status(root, finding_id, target_status)
                if status_updated is not True:
                    raise RuntimeError(f"finding status persistence did not update finding: {finding_id}")
                result["finding_status"] = target_status
                result["message"] = "复现失败：Bug 已不再触发，标记为已修复。"
            elif result.get("ok") is True and result.get("success") is True:
                phase = "status_persistence"
                target_status = "open"
                status_updated = db_persist.update_finding_status(root, finding_id, target_status)
                if status_updated is not True:
                    raise RuntimeError(f"finding status persistence did not update finding: {finding_id}")
                result["finding_status"] = target_status
                result["message"] = "复现成功：Bug 仍然存在。"
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
            error_code = "REPLAY_STATUS_PERSIST_FAILED" if phase == "status_persistence" else "REPLAY_FAILED"
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
        """Return customer-maintained project metadata (industry / module_scope
        / production_data_exclusion safety boundary)."""
        from .enterprise_project_config import MultiServiceProject
        try:
            msp = MultiServiceProject(project, root)
            return self._json({"ok": True, "project": project, **msp.project_metadata()})
        except Exception as exc:
            return self._json({"ok": False, "error": "METADATA_READ_FAILED", "message": str(exc)[:300]}, 500)

    def _handle_save_project_metadata(self, project: str, root: Path, body: dict) -> None:
        """Persist customer-maintained project metadata.

        Accepted fields (all optional; only provided fields are updated):
          - industry: str
          - module_scope: list[str]
          - production_data_exclusion: list[str]  (hard safety boundary consumed
            by the probe executor; the system will never touch these paths/data)
        """
        from .enterprise_project_config import MultiServiceProject
        industry = body.get("industry")
        module_scope = body.get("module_scope")
        production_data_exclusion = body.get("production_data_exclusion")
        try:
            msp = MultiServiceProject(project, root)
            updated = msp.set_project_metadata(
                industry=industry if industry is not None else None,
                module_scope=module_scope if module_scope is not None else None,
                production_data_exclusion=production_data_exclusion if production_data_exclusion is not None else None,
            )
            return self._json({
                "ok": True,
                "project": project,
                "saved": msp.project_metadata(),
                "services_count": len(updated.get("services", [])),
            })
        except (ValueError, TypeError) as exc:
            return self._json({"ok": False, "error": "BAD_REQUEST", "message": str(exc)}, 400)
        except Exception as exc:
            return self._json({"ok": False, "error": "METADATA_SAVE_FAILED", "message": str(exc)[:300]}, 500)

    def _get_spectrum_status(self, project: str, root: Path) -> None:
        """Get the latest full-spectrum scan result."""
        result_file = root / "platform_outputs" / project / "spectrum" / "spectrum_result.json"
        ts_file = root / "platform_outputs" / project / "spectrum" / "spectrum_timestamp.txt"
        if not result_file.exists():
            return self._json({"ok": True, "status": "not_run", "message": "尚未运行全频谱检测"})
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            last_run = ts_file.read_text(encoding="utf-8").strip() if ts_file.exists() else ""
            return self._json({"ok": True, "status": "completed", "last_run": last_run, **result})
        except Exception:
            return self._json({"ok": True, "status": "error", "message": "无法读取检测结果"})

    def _handle_reanalyze(self, project: str, root: Path, actor: dict[str, str]) -> None:
        """Rebuild knowledge center with fresh data."""
        try:
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset
            build_enterprise_business_knowledge_asset(project, root)
            build_enterprise_pilot_overview(project, root)
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists(): dash_html.unlink()
            return self._json({"ok": True, "message": "Knowledge base reanalysis completed."})
        except Exception as e:
            return self._json({"ok": False, "error": "REANALYZE_FAILED", "message": str(e)[:300]}, 500)

    def _handle_preview(self, project: str, body_or_source: dict[str, Any] | str, root: Path) -> None:
        """Return file content for preview. Supports both POST body and GET query param."""
        source_id = ""
        if isinstance(body_or_source, dict):
            source_id = str(body_or_source.get("source_id") or "").strip()
        else:
            source_id = str(body_or_source).strip()
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID"}, 400)
        try:
            from .enterprise_knowledge_center import _load_registry
            registry = _load_registry(project, root)
            for s in registry.get("sources", []):
                if s.get("source_id") == source_id:
                    stored_path = str(s.get("stored_path") or "").strip()
                    if not stored_path:
                        break
                    src_path = (root / stored_path).resolve()
                    root_resolved = root.resolve()
                    if root_resolved != src_path and root_resolved not in src_path.parents:
                        return self._json({"ok": False, "error": "INVALID_STORED_PATH"}, 400)
                    if src_path.exists():
                        text = src_path.read_text(encoding="utf-8", errors="replace")[:50000]
                        return self._json({"ok": True, "source_id": source_id, "filename": s.get("original_name",""), "content": text})
            return self._json({"ok": False, "error": "NOT_FOUND", "message": "File not found."}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": "PREVIEW_FAILED", "message": str(e)[:300]}, 500)

    def _handle_evidence_artifact(self, project: str, ref: str, root: Path) -> None:
        """Serve a browser/UI evidence artifact (screenshot, HAR, trace zip, video).

        Security: path-traversal hardened — only files under
        ``platform_workspace/<project>/browser_runs/`` are served,
        extensions are whitelisted, and the resolved path must stay inside root.

        GET /api/evidence/artifact?project=<project>&ref=<relative-path>
        """
        ref = str(ref or "").strip().lstrip("/").lstrip("\\")
        if not ref:
            return self._json({"ok": False, "error": "MISSING_ARTIFACT_REF"}, 400)
        # Only serve from the browser_runs subtree for this project.
        allowed_prefix = Path("platform_workspace") / _safe_project_id(project) / "browser_runs"
        candidate = (root / ref)
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            return self._json({"ok": False, "error": "INVALID_ARTIFACT_PATH"}, 400)
        root_resolved = root.resolve()
        allowed_resolved = (root / allowed_prefix).resolve()
        if (
            root_resolved != allowed_resolved
            and root_resolved not in resolved.parents
            and root_resolved not in allowed_resolved.parents
        ):
            return self._json({"ok": False, "error": "INVALID_STORED_PATH"}, 400)
        # Resolved must start with the allowed prefix.
        try:
            resolved.relative_to(allowed_resolved)
        except ValueError:
            return self._json({"ok": False, "error": "ARTIFACT_OUTSIDE_ALLOWED_SUBTREE"}, 403)
        if not candidate.exists() or not resolved.is_file():
            return self._json({"ok": False, "error": "ARTIFACT_NOT_FOUND"}, 404)
        # Extension whitelist — serve only evidence assets, never scripts or binaries.
        suffix = resolved.suffix.lower()
        ALLOWED = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".har", ".json", ".zip", ".mp4", ".webm", ".txt", ".html", ".css"}
        if suffix not in ALLOWED:
            return self._json({"ok": False, "error": "ARTIFACT_TYPE_BLOCKED"}, 415)
        # MIME map (data-driven, not blind best-guess).
        MIME: dict[str, str] = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
            ".har": "application/json", ".json": "application/json",
            ".zip": "application/zip", ".mp4": "video/mp4", ".webm": "video/webm",
            ".txt": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }
        mime = MIME.get(suffix, "application/octet-stream")
        max_bytes = 50_000_000
        try:
            file_size = resolved.stat().st_size
        except OSError:
            return self._json({"ok": False, "error": "ARTIFACT_READ_FAILED"}, 500)
        if file_size > max_bytes:
            return self._json({"ok": False, "error": "ARTIFACT_TOO_LARGE"}, 413)
        try:
            data = resolved.read_bytes()[:max_bytes]
        except OSError:
            return self._json({"ok": False, "error": "ARTIFACT_READ_FAILED"}, 500)
        try:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    def _handle_settings_save(self, body: dict[str, Any]) -> None:
        """Apply LLM settings for a customer-local private service."""
        updates = {}
        for key in ["llm_base_url", "llm_model", "llm_temperature", "llm_api_key"]:
            if key in body and body[key]:
                updates[key.upper()] = str(body[key])
        if updates:
            _write_env_local(updates)
        for key, val in updates.items():
            os.environ[key] = val
        if updates:
            try:
                from .llm_reasoning import reset_client
                reset_client()
            except Exception:
                pass
        # Clear forced/cached health status before re-verification so a newly
        # verified key is reflected by /health and Settings immediately.
        for _key in ("QUALIBUG_LLM_HEALTH_STATUS", "QUALIBUG_LLM_LAST_HEALTH_STATUS", "QUALIBUG_LLM_LAST_HEALTH_LABEL", "QUALIBUG_LLM_LAST_HEALTH_ERROR"):
            os.environ.pop(_key, None)
        llm_health = self._verify_llm_connectivity() if updates else self._llm_health()
        return self._json({
            "ok": True,
            "llm_available": llm_health["available"],
            "llm_status": llm_health["status"],
            "llm_status_label": llm_health["label"],
            "llm_error": llm_health.get("error", ""),
            "message": "LLM settings were saved to .env.local for this private deployment.",
        })
