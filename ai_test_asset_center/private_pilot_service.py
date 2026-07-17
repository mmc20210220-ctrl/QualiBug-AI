from __future__ import annotations

"""Private-network HTTP entrypoint for the QualiBug pilot runtime.

The service binds to localhost by default. In private-cloud deployments, a
trusted reverse proxy or enterprise SSO gateway should authenticate users and
inject the actor/role headers documented below. The service never accepts raw
credential values; connectors only receive secret references.
"""

import json
import os
import re
import time
import traceback
import uuid
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from .enterprise_pilot_runtime import (
    build_enterprise_pilot_overview,
    list_pilot_tasks,
    operate_enterprise_pilot_runtime,
)
from . import db_persistence as db_persist
from . import jwt_auth
from .real_id_resolver import normalize_path_placeholders
from .real_project_onboarding import ROOT, _safe_project_id
from .scan_counter import increment_scan_counter
from .campaign_api_contract import CampaignContractError, structured_error
from .command_center_delivery_contract import normalize_command_center_delivery
from .customer_delivery_gate import split_customer_delivery_tracks as _partition_delivery_tracks

from .private_pilot_json_io import (  # noqa: F401
    _read_json_artifact,
    _read_json_object,
    _read_json_safe,
    _write_json_object_atomic,
)
from .private_pilot_debug_client import (  # noqa: F401
    _dbg_env,
    _dbg_fingerprint_payload,
    _dbg_report,
)
from .private_pilot_project_assets import (  # noqa: F401
    KNOWLEDGE_INGEST_BINARY_EXTENSIONS,
    KNOWLEDGE_INGEST_EXTENSIONS,
    KNOWLEDGE_INGEST_SOURCE_TYPES,
    KNOWLEDGE_INGEST_TEXT_EXTENSIONS,
    MASKED_CREDENTIAL_VALUE,
    ONBOARD_DOCUMENT_EXTENSIONS,
    ONBOARD_OPENAPI_EXTENSIONS,
    _credential_update_value,
    _extensions_accept,
    _extensions_label,
    _first_text,
    _is_masked_credential_value,
    _knowledge_asset_sources,
    _known_project_exists,
    _load_real_project_discovery_payload,
    _normalize_frontend_page_path,
    _project_output_dir_for_import,
    _root,
    _truthy_env,
    _write_env_local,
)
from .private_pilot_tenant_auth import (  # noqa: F401
    TenantAuthenticationError,
    _actor,
    _current_tenant,
    _parse_project_scopes,
    _tenant_from_headers,
)
from .private_pilot_campaign_projection import (  # noqa: F401
    _augment_continuous_discovery_campaign,
    _current_campaign_bundle_finding_stats,
    _current_campaign_scope_summary,
    _report_finding_dedupe_key as _finding_dedupe_key,
)
from .private_pilot_scan_aggregates import (  # noqa: F401
    _extend_stage3_impact_analysis,
    _synchronize_scan_aggregates,
)
from .private_pilot_regression_projection import (  # noqa: F401
    _build_regression_release_guidance,
    _build_regression_validation_summary,
    _load_regression_history,
    _load_regression_projection,
    _regression_lifecycle,
    _regression_lookup_keys,
    _regression_status_label,
    _regression_summary_title,
    _regression_trend_direction,
)
from .private_pilot_defect_summaries import (  # noqa: F401
    _build_defect_delivery_cards,
    _build_defect_grouped_summary,
    _build_defect_priority_summary,
    _build_defect_repro_summary,
    _claim_request_identity,
    _extract_step_calls,
    _finding_request_identity,
    _materialized_path_matches,
    _normalize_summary_path,
    _validate_api_path,
)
from .private_pilot_command_center_helpers import (  # noqa: F401
    _annotate_ui_risk_item,
    _build_internal_clue_contract,
    _collect_track_counts,
    _command_center_priority_label,
    _command_center_priority_score,
    _commercial_assets_signal,
    _defect_intake_fields,
    _defect_intake_stats,
    _is_ui_risk_item,
    _normalize_commercial_assets,
    _normalize_execution_evidence_summary,
    _rebuild_customer_display_contract,
    _select_commercial_assets,
    _ui_verification_stats,
)
from .private_pilot_command_center_builder import CommandCenterBuilderMixin
from .private_pilot_credentials_handlers import CredentialsHandlerMixin
from .private_pilot_http_routing import HttpRoutingMixin
from .private_pilot_ingest_handlers import IngestHandlersMixin
from .private_pilot_scan_handlers import ScanHandlersMixin
from .private_pilot_report_loading import ReportLoadingMixin
from .private_pilot_continuous import (  # noqa: F401
    _CONTINUOUS_STATE_FILE,
    _continuous_scan_loop,
    _continuous_state_path,
    _continuous_threads,
    _get_continuous_state,
    _mark_continuous_converged,
    _mark_continuous_max_rounds,
    _record_continuous_failure,
    _update_continuous_state,
)
from .private_pilot_command_center_envelope import (  # noqa: F401
    clear_envelope_post_hooks,
    list_envelope_post_hooks,
    normalize_command_center_envelope,
    register_envelope_post_hook,
)
from .private_pilot_scan_prep import (  # noqa: F401
    _frontend_entry_url_candidates,
    _has_campaign_id_mismatch,
    _http_url_text,
    _is_local_private_service,
    _issue_runtime_approval_for_result,
    _load_followup_ui_execution_requests,
    _load_followup_ui_test_data_requests,
    _maybe_issue_local_runtime_approval,
    _predicted_campaign_binding,
    _prepare_v12_scan_body,
    _read_project_prd_text,
    _resolve_followup_ui_test_data_browser_plan,
    _resolve_scan_runtime_defaults,
    _resolve_ui_base_url_from_profile,
    _run_ingest_auto_scan,
    _validate_scan_base_url,
)


CONFIG_MANAGER_ROLES = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}
KNOWLEDGE_MANAGER_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
SETTINGS_MANAGER_ROLES = {"project_owner", "security_owner", "testops_admin", "admin"}
PROJECT_SCOPE_HEADER = "X-QualiBug-Project-Scopes"

_TENANT = _current_tenant()


def _normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Thin dispatcher — patches register post-hooks instead of replacing this."""
    return normalize_command_center_envelope(payload)


class PrivatePilotHandler(
    HttpRoutingMixin,
    CredentialsHandlerMixin,
    IngestHandlersMixin,
    ScanHandlersMixin,
    ReportLoadingMixin,
    CommandCenterBuilderMixin,
    BaseHTTPRequestHandler,
):
    server_version = "QualiBugPrivatePilot/1.0"

    def _root(self) -> Path:
        configured = getattr(self.server, "qualibug_private_root", None)
        return Path(configured).resolve() if configured else _root()

    def _json(self, body: Any, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        try:
            raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if extra_headers:
                for _hk, _hv in extra_headers.items():
                    self.send_header(_hk, _hv)
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass  # client disconnected
        except Exception as exc:
            _dbg_report(
                hypothesis_id="A",
                msg=f"[DEBUG] json-response-failed status={status}",
                data={"exc_type": type(exc).__name__, "exc": str(exc)},
            )
            raise

    def _html(self, body: str, status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _project(self) -> str:
        query = parse_qs(urlparse(self.path).query)
        return _safe_project_id((query.get("project") or [""])[0])

    def _request_tenant(self) -> str:
        tenant_id = str(getattr(self, "_validated_tenant_id", "") or "").strip()
        if tenant_id:
            return tenant_id
        tenant_id = _tenant_from_headers(dict(self.headers), root=self._root())
        self._validated_tenant_id = tenant_id
        return tenant_id

    def _require_tenant(self, root: Path) -> str | None:
        try:
            tenant_id = _tenant_from_headers(dict(self.headers), root=root)
        except TenantAuthenticationError as exc:
            self._json(
                {
                    "ok": False,
                    "error": "INVALID_TENANT_CREDENTIAL",
                    "message": str(exc),
                },
                401,
            )
            return None
        self._validated_tenant_id = tenant_id
        return tenant_id

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if not size:
            return {}
        if size > 2_000_000:
            raise ValueError("Request body exceeds the private service limit.")
        raw = self.rfile.read(size)
        if not raw:
            return {}
        # Try UTF-8 first, then latin-1, then raw bytes
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                parsed = json.loads(raw.decode(encoding))
                return parsed if isinstance(parsed, dict) else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return {}

    def _require_actor(self) -> dict[str, str] | None:
        actor = _actor(self.headers)
        if actor is None:
            server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
            local_dev_actor_allowed = (
                _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "1")
                and server_host in {"127.0.0.1", "localhost", "::1"}
                and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
                and str(self.headers.get("X-QualiBug-No-Local-Dev") or "").strip() != "1"
            )
            if local_dev_actor_allowed:
                return {
                    "name": os.environ.get("QUALIBUG_LOCAL_ACTOR", "local_dev")[:120],
                    "role": os.environ.get("QUALIBUG_LOCAL_ROLE", "project_owner")[:64],
                }
        if actor is None:
            self._json(
                {
                    "ok": False,
                    "error": "MISSING_TRUSTED_ACTOR",
                    "message": "The private service requires trusted X-QualiBug-Actor and X-QualiBug-Role headers, unless localhost-only local development actor mode is enabled.",
                },
                401,
            )
        return actor

    def _require_role(self, actor: dict[str, str], allowed: set[str], action: str) -> bool:
        if actor.get("role") in allowed:
            return True
        self._json(
            {
                "ok": False,
                "error": "FORBIDDEN",
                "message": f"{action} requires one of: {', '.join(sorted(allowed))}.",
            },
            403,
        )
        return False

    def _require_project_scope(self, project: str) -> bool:
        """Require an explicit trusted project scope outside localhost dev mode.

        Actor and role headers establish *who* is calling, but they do not by
        themselves establish which customer/project data the caller may read or
        change.  In a public/private-cloud binding, the trusted reverse proxy
        must inject a comma-separated allow-list (or ``*`` for an explicitly
        authorized platform operator) through ``X-QualiBug-Project-Scopes``.

        The localhost-only development fallback remains intentionally narrow:
        it is available only while public binding is disabled, matching the
        existing local actor fallback used by the self-contained pilot demo.
        """
        raw = str(self.headers.get(PROJECT_SCOPE_HEADER) or self.headers.get(PROJECT_SCOPE_HEADER.lower()) or "")
        scopes, wildcard = _parse_project_scopes(raw)
        if wildcard or _safe_project_id(project) in scopes:
            return True

        server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
        local_development = (
            server_host in {"127.0.0.1", "localhost", "::1"}
            and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
            and _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "1")
        )
        if local_development:
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_SCOPE_FORBIDDEN",
                "message": f"Requested project is outside the trusted {PROJECT_SCOPE_HEADER} allow-list.",
            },
            403,
        )
        return False

    def _project_list_scope_filter(self) -> tuple[set[str], bool]:
        server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
        local_development = (
            server_host in {"127.0.0.1", "localhost", "::1"}
            and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
        )
        if local_development:
            return set(), True
        raw = str(self.headers.get(PROJECT_SCOPE_HEADER) or self.headers.get(PROJECT_SCOPE_HEADER.lower()) or "")
        return _parse_project_scopes(raw)

    def _require_known_project(self, project: str, root: Path) -> bool:
        project = _safe_project_id(project)
        if _known_project_exists(root, project):
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_NOT_FOUND",
                "message": f"项目 '{project}' 不存在，请先选择有效项目。",
            },
            404,
        )
        return False

    def _load_scan_history(self, project: str, root: Path) -> dict[str, Any]:
        """Load scan history from disk."""
        import json as _json
        history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
        if not history_path.exists():
            latest_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
            if latest_path.exists():
                try:
                    latest = _json.loads(latest_path.read_text(encoding="utf-8"))
                except Exception:
                    latest = {}
                return {
                    "ok": True,
                    "history": [latest],
                    "compatibility_mode": "legacy_findings_report_v1",
                    "canonical_api_family": "/api/v1/projects/{projectId}/*",
                }
            return {"ok": True, "history": []}
        try:
            return {"ok": True, "history": _json.loads(history_path.read_text(encoding="utf-8"))}
        except Exception:
            return {"ok": True, "history": []}

    def _list_project_inputs(self, project: str, root: Path) -> dict[str, Any]:
        """List project input files as knowledge sources from disk."""
        import json as _json, os as _os, time as _time
        input_dir = root / "platform_inputs" / project
        sources = []
        if input_dir.exists():
            config_path = input_dir / "real_project_config.json"
            source_dir = input_dir
            if config_path.exists():
                try:
                    config = _json.loads(config_path.read_text(encoding="utf-8"))
                    src = config.get("source_dataset", "")
                    if src and _os.path.isdir(src):
                        source_dir = Path(src)
                except Exception:
                    pass
            for f in sorted(source_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    ext = f.suffix.lower()
                    if ext in (".md",):
                        stype = "PRD" if "prd" in f.name.lower() else "\u4e1a\u52a1\u6587\u6863"
                    elif ext in (".yaml", ".yml", ".json"):
                        stype = "OpenAPI" if "openapi" in f.name.lower() else "\u89c4\u8303\u6587\u4ef6"
                    elif ext == ".sql":
                        stype = "\u6570\u636e\u5e93 Schema"
                    else:
                        stype = "\u4e1a\u52a1\u6587\u6863"
                    sources.append({
                        "source_id": f"input-{f.name}",
                        "filename": f.name,
                        "source_type": stype,
                        "status": "active",
                        "size_bytes": f.stat().st_size,
                        "created_at_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(f.stat().st_mtime)),
                        "uploaded_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(f.stat().st_mtime)),
                    })
        return {"ok": True, "sources": sources}

    def _render_onboard(self, project: str, root: Path) -> None:
        """Render a minimal project onboarding page."""
        from .product_ui import product_shell, section

        known = _known_project_exists(root, project)
        status = "Known project" if known else "Project has not been imported yet"
        body = section(
            "Project onboarding",
            "Import PRD, OpenAPI and business documents, then configure the target environment before running discovery.",
            f"<p class='text-muted'>{status}</p><p><a class='btn btn-primary' href='/materials?project={project}'>Open materials</a> <a class='btn btn-secondary' href='/settings?project={project}'>Open settings</a></p>",
            section_id="onboarding",
        )
        page = product_shell(
            title="Project onboarding",
            project_id=project,
            active="",
            eyebrow="Onboarding",
            headline="Start project onboarding",
            description="Complete the minimum inputs required for grounded discovery.",
            body=body,
        )
        self._html(page)

    def _render_findings(self, project: str, root: Path) -> None:
        """Render a minimal findings page for the private pilot server."""
        from .product_ui import product_shell, section

        body = section(
            "Findings",
            "Open the product frontend for the full evidence chain and remediation workflow.",
            f"<p><a class='btn btn-primary' href='/findings?project={project}'>Open findings</a> <a class='btn btn-secondary' href='/evidence?project={project}'>Open evidence</a></p>",
            section_id="findings",
        )
        page = product_shell(
            title="Findings",
            project_id=project,
            active="findings",
            eyebrow="Evidence",
            headline="Validated findings",
            description="Customer-safe summary of validated product risks.",
            body=body,
        )
        self._html(page)

    def _llm_available(self) -> bool:
        return self._llm_health()["available"]

    def _llm_health(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningConfig
            cfg = ReasoningConfig.from_env()
            configured = cfg.enabled
        except Exception as exc:
            return {"configured": False, "available": False, "status": "failed", "label": "Failed", "error": str(exc)[:160]}
        if not configured:
            return {"configured": False, "available": False, "status": "offline", "label": "Not configured"}
        forced_status = os.environ.get("QUALIBUG_LLM_HEALTH_STATUS", "").strip().lower()
        if forced_status in {"online", "failed"}:
            return {"configured": True, "available": forced_status == "online", "status": forced_status, "label": "Verified online" if forced_status == "online" else "Verification failed"}
        last_status = os.environ.get("QUALIBUG_LLM_LAST_HEALTH_STATUS", "").strip().lower()
        if last_status in {"online", "failed"}:
            return {
                "configured": True,
                "available": last_status == "online",
                "status": last_status,
                "label": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_LABEL", "Verified online" if last_status == "online" else "Verification failed"),
                "error": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_ERROR", ""),
            }
        return self._verify_llm_connectivity()

    def _verify_llm_connectivity(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningClient, ReasoningConfig

            cfg = ReasoningConfig.from_env()
            if not cfg.enabled:
                result = {"configured": False, "available": False, "status": "offline", "label": "Not configured", "error": "Missing LLM_BASE_URL, LLM_API_KEY or LLM_MODEL."}
            else:
                cfg.timeout_seconds = int(os.environ.get("LLM_HEALTH_TIMEOUT_SECONDS", "15"))
                client = ReasoningClient(cfg)
                client.health_check()
                result = {"configured": True, "available": True, "status": "online", "label": "Verified online"}
        except Exception as exc:
            result = {"configured": True, "available": False, "status": "failed", "label": "Verification failed", "error": str(exc)[:300]}
        os.environ["QUALIBUG_LLM_LAST_HEALTH_STATUS"] = str(result["status"])
        os.environ["QUALIBUG_LLM_LAST_HEALTH_LABEL"] = str(result["label"])
        if result.get("error"):
            os.environ["QUALIBUG_LLM_LAST_HEALTH_ERROR"] = str(result["error"])
        else:
            os.environ.pop("QUALIBUG_LLM_LAST_HEALTH_ERROR", None)
        return result


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


    def _render_report_html(self, project: str, root: Path) -> None:
        """Generate a standalone HTML report."""
        import json as _json, time as _time
        report_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
        history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
        report = {}
        if report_path.exists():
            try: report = _json.loads(report_path.read_text(encoding="utf-8"))
            except: pass
        history = []
        if history_path.exists():
            try: history = _json.loads(history_path.read_text(encoding="utf-8"))
            except: pass

        s2 = report.get("stage2_discovery", {})
        s1 = report.get("stage1_industry", {})
        s3 = report.get("stage3_impact_analysis", {})
        findings = s2.get("findings", [])
        analyses = s3.get("analyses", [])

        # Build HTML
        f_rows = ""
        for f in findings:
            sev = f.get("severity", "?")
            sev_color = "#dc2626" if sev in ("P0","P1") else "#d97706" if sev == "P2" else "#2563eb"
            f_rows += f"""<tr><td style="color:{sev_color};font-weight:700">{sev}</td><td>{f.get("title","-")}</td><td>{f.get("category","-")}</td><td>{f.get("confidence_score","-")}</td><td>{f.get("evidence","-")[:200]}</td></tr>"""

        html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<title>QualiBug Bug 鎵弿鎶ュ憡 - {project}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#1e293b;background:#f8fafc}}
h1{{font-size:24px;border-bottom:2px solid #3b82f6;padding-bottom:12px}}
h2{{font-size:18px;margin-top:32px;color:#334155}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}}
th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid #e2e8f0}}
th{{background:#f1f5f9;font-weight:700;color:#475569}}
.metric{{display:inline-block;text-align:center;padding:16px 24px;border-radius:8px;margin:8px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.metric strong{{display:block;font-size:28px;color:#3b82f6}}
.metric span{{font-size:11px;color:#94a3b8}}
.footer{{margin-top:32px;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:12px}}</style></head><body>
<h1>QualiBug AI 路 Bug 鎵弿鎶ュ憡</h1>
<p>椤圭洰: <strong>{project}</strong> 路 鐢熸垚鏃堕棿: <strong>{_time.strftime("%Y-%m-%d %H:%M:%S")}</strong></p>
<p>瀵硅薄: {s1.get("object_count",0)} 路 瀵硅薄鏁? {s1.get("object_count",0)} 路 椋庨櫓鍩? {s1.get("risk_count",0)}</p>
<div style="margin:20px 0">
<div class="metric"><span>鎬诲彂鐜?/span><strong>{len(findings)}</strong></div>
<div class="metric"><span>P0/P1</span><strong>{sum(1 for f in findings if str(f.get("severity","")) in ("P0","P1"))}</strong></div>
<div class="metric"><span>LLM 鍒嗘瀽</span><strong>{s3.get("llm_powered",0)}</strong></div>
<div class="metric"><span>瑕嗙洊</span><strong>{s3.get("total_analyses",0)}/{max(1,len(findings))}</strong></div>
</div>
<h2>Bug 鍙戠幇鍒楄〃</h2>
<table><tr><th>涓ラ噸搴?/th><th>鏍囬</th><th>绫诲埆</th><th>缃俊搴?/th><th>璇佹嵁</th></tr>{f_rows}</table>
<h2>鎵弿鍘嗗彶 (鏈€杩?10 娆?</h2>
<table><tr><th>鏃堕棿</th><th>鐘舵€?/th><th>鍙戠幇</th><th>P0/P1</th><th>瀵硅薄</th></tr>"""

        for h in history[-10:]:
            html += f"<tr><td>{h.get('timestamp_utc','-')}</td><td>{h.get('status','-')}</td><td>{h.get('total_findings',0)}</td><td>{h.get('p0p1_count',0)}</td><td>{h.get('industry','-')[:30]}</td></tr>"

        html += f"""</table>
<div class="footer">QualiBug AI Enterprise Edition 路 绉佹湁鍖栭儴缃?路 娴嬭瘯鐜鎵弿 路 缁濅笉瑙︾鐢熶骇鏁版嵁</div>
</body></html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", "inline")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_settings(self, project: str, root: Path) -> None:
        """Render a minimal settings page for the private pilot server."""
        from .product_ui import product_shell, section, h

        llm_health = self._llm_health()
        llm_status = str(llm_health.get("status") or "offline")
        llm_label = str(llm_health.get("label") or "Not configured")
        body = section(
            "System settings",
            "Use the product frontend for full customer, topology, connector and LLM configuration.",
            f"<p>LLM status: <strong>{h(llm_label)}</strong> ({h(llm_status)})</p><p><a class='btn btn-primary' href='/settings?project={project}'>Open settings</a></p>",
            section_id="settings",
        )
        page = product_shell(
            title="System settings",
            project_id=project,
            active="settings",
            eyebrow="Settings",
            headline="System configuration",
            description="Secrets are never rendered back to the browser.",
            body=body,
            llm_status=llm_status,
        )
        self._html(page)

    def _handle_continuous_start(self, project: str, root: Path, actor: dict[str, str], body: dict[str, Any]) -> None:
        """Start a continuous auto-scan loop for a project.

        The loop runs scans at intervals until convergence (no new findings
        for N consecutive rounds + coverage threshold) or explicit stop.
        Manual scans remain available in parallel — they do not conflict.
        """
        import threading as _threading
        key = (str(root), project)

        # Already running?
        if key in _continuous_threads and not _continuous_threads[key].get("stop"):
            return self._json({
                "ok": True,
                "message": "持续检测已在运行中。",
                "round": _continuous_threads[key].get("round", 0),
            })

        interval_s = int(body.get("interval_s", 60))  # default 60s between rounds
        interval_s = max(10, min(interval_s, 600))  # clamp 10s–10min

        # Reset converged flag
        state_file = _continuous_state_path(root, project)
        if state_file.exists():
            state = _read_json_object(state_file)
            state["status"] = "scanning"
            state["converged"] = False
            state.pop("converge_reason", None)
            state.pop("last_failure", None)
            state.pop("termination", None)
            _write_json_object_atomic(state_file, state)

        tenant_id = self._request_tenant()
        thread_entry = {"stop": False, "round": 0, "converged": False, "started_at": time.time()}
        _continuous_threads[key] = thread_entry

        t = _threading.Thread(
            target=_continuous_scan_loop,
            args=(root, project, tenant_id, interval_s),
            daemon=True,
        )
        t.start()
        _continuous_threads[key]["thread"] = t

        return self._json({
            "ok": True,
            "message": f"持续检测已启动，每 {interval_s} 秒一轮，直到覆盖收敛。",
            "interval_s": interval_s,
        })

    def _handle_continuous_stop(self, project: str, root: Path) -> None:
        """Stop the continuous auto-scan loop for a project."""
        key = (str(root), project)
        entry = _continuous_threads.get(key)
        if entry:
            entry["stop"] = True
            # Mark state
            state_file = _continuous_state_path(root, project)
            if state_file.exists():
                state = _read_json_object(state_file)
                state["status"] = "stopped"
                state["converged"] = False
                _write_json_object_atomic(state_file, state)
            return self._json({"ok": True, "message": "持续检测已手动停止。"})
        return self._json({"ok": True, "message": "持续检测未在运行。"})

    def _serve_frontend(self, parsed: "urllib.parse.ParseResult", root: Path) -> None:
        """Serve the prebuilt customer pilot SPA (frontend/dist). Public — called before
        the auth gate so the login page itself is reachable. Path-traversal hardened."""
        import mimetypes
        _dist_env = os.environ.get("QUALIBUG_FRONTEND_DIST")
        _dist = Path(_dist_env) if _dist_env else (Path(__file__).resolve().parent.parent / "frontend" / "dist")
        _dist_resolved = _dist.resolve()
        _rel = parsed.path.lstrip("/")
        if _rel in ("", "index.html"):
            _target = _dist_resolved / "index.html"
        elif _rel.startswith("assets/"):
            _target = (_dist_resolved / _rel).resolve()
        else:
            # SPA client-side route (e.g. /login, /settings, /scan) -> shell
            _target = _dist_resolved / "index.html"
        if _target != _dist_resolved and _dist_resolved not in _target.parents:
            return self._json({"ok": False, "error": "FORBIDDEN"}, 403)
        if not _target.exists() or not _target.is_file():
            return self._json({"ok": False, "error": "UI_NOT_BUILT",
                               "message": "frontend/dist 未构建，请先构建前端或设置 QUALIBUG_FRONTEND_DIST。"}, 404)
        try:
            _data = _target.read_bytes()
        except Exception:
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        _ctype = mimetypes.guess_type(str(_target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", _ctype)
        self.send_header("Content-Length", str(len(_data)))
        self.end_headers()
        self.wfile.write(_data)

    @staticmethod
    def _dedupe_risks(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in risks:
            key = "|".join([
                str(item.get("risk_id") or ""),
                str(item.get("title") or "")[:160],
                str(item.get("_api_method") or (item.get("evidence") or {}).get("method") or ""),
                str(item.get("_api_path") or (item.get("evidence") or {}).get("path") or ""),
            ]).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _evidence_trust_score(self, risks: list[dict[str, Any]]) -> float:
        if not risks:
            return 0.0
        total = 0
        for item in risks:
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            score = 0
            if self._first_text(evidence.get("path"), item.get("_api_path")):
                score += 18
            if self._first_text(item.get("expected"), item.get("suggested_action"), evidence.get("expected")):
                score += 18
            if self._first_text(item.get("actual"), item.get("summary"), evidence.get("actual")):
                score += 18
            if self._first_text(evidence.get("status_code"), evidence.get("response_status"), evidence.get("error")):
                score += 16
            if self._first_text(evidence.get("source_file"), item.get("evidence_hint")):
                score += 12
            if str(item.get("status") or "").lower() in {"confirmed", "validated", "reproduced"}:
                score += 18
            total += min(100, score)
        return round(total / max(1, len(risks)) / 100, 2)

    @staticmethod
    def _dedupe_docs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in rows:
            key = str(row.get("source_id") or row.get("display_name") or "").lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _extract_module(self, title: str, description: str) -> str:
        """Extract a meaningful module name from title/description content."""
        import re
        text = (title + " " + description).lower()
        # Known business modules
        for mod, keywords in [
            ("orders", ["order", "订单"]),
            ("payments", ["pay", "支付"]),
            ("users", ["user", "用户", "role", "admin", "register", "注册"]),
            ("products", ["product", "产品"]),
            ("inventory", ["inventory", "库存"]),
            ("permissions", ["permission", "权限", "auth", "认证"]),
            ("refunds", ["refund", "退款"]),
            ("notifications", ["notif", "通知"]),
        ]:
            if any(kw in text for kw in keywords):
                return mod
        return "system"

    def _match_docs_for_finding(self, title: str, docs: list[dict]) -> list[dict]:
        """Match enterprise documents to a finding by keyword overlap.

        通用方案：从 finding 标题动态提取关键词（2字以上的中文词、英文单词），
        与文档名/摘要做交集匹配。不硬编码任何业务关键词。
        """
        if not docs: return []
        import re as _re
        title_lower = title.lower()
        # 从标题动态提取关键词（通用：2字以上中文、3字母以上英文）
        cn_words = set(_re.findall(r'[\u4e00-\u9fff]{2,}', title_lower))
        en_words = set(w for w in _re.findall(r'[a-z]{3,}', title_lower) if w not in ('the', 'and', 'for', 'with', 'from'))
        keywords = cn_words | en_words
        if not keywords:
            return []
        matched = []
        for doc in docs:
            doc_text = f"{doc.get('display_name','')} {doc.get('excerpt','')} {doc.get('type','')}".lower()
            score = sum(1 for kw in keywords if kw in title_lower and kw in doc_text)
            if score > 0:
                matched.append({**doc, "relevance": score})
        return sorted(matched, key=lambda m: -m.get("relevance", 0))[:3]

    @staticmethod
    def _build_test_task_board(report: Any) -> dict | None:
        """主链 8: 测试任务看板 — 从 v12 报告原样透传任务生命周期看板。

        数据全部来自 v12 report，前端零变换渲染：
        - ledger: 行为切片账本（含主链 4 的 slice_status 任务状态）
        - slices: 行为切片列表（含每个任务的 status）
        - execution.production_data_blocked: 主链 5/6 生产数据安全边界拦截计数
        - evidence_chains_saved: 主链 7 已落地证据链计数
        无任务数据（既无 ledger 也无 slices）时返回 None，前端显示空态。
        """
        if not isinstance(report, dict):
            return None
        ledger = report.get("behavior_slice_ledger")
        ledger = ledger if isinstance(ledger, dict) else {}
        slices = report.get("behavior_slices")
        slices = slices if isinstance(slices, list) else []
        if not ledger and not slices:
            return None
        phases = report.get("phases") if isinstance(report.get("phases"), dict) else {}
        execution = phases.get("execution") if isinstance(phases.get("execution"), dict) else {}
        oracle = phases.get("oracle") if isinstance(phases.get("oracle"), dict) else {}

        def _int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        return {
            "ledger": dict(ledger),
            "slices": [dict(item) for item in slices if isinstance(item, dict)],
            "execution": {"production_data_blocked": _int(execution.get("production_data_blocked"))},
            "evidence_chains_saved": _int(oracle.get("evidence_chains_saved")),
        }

    def _scan_counter(self, project_id: str, root: Path) -> dict:
        """Track how many times V12 scan has run for this project."""
        import time
        counter_path = root / "platform_outputs" / project_id / "scan_counter.json"
        if counter_path.exists():
            return _read_json_object(counter_path)
        return {"count": 1, "first_scan_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}

    def _previous_finding_titles(self, project_id: str, root: Path) -> set:
        """Read previous scan findings from DB for convergence tracking."""
        try:
            db_persist.init_db(root)
            import sqlite3
            db_path = root / db_persist.DB_FILENAME
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            # Try both the project_id and a locale-agnostic normalized form
            # (strip common company-suffix tokens in any language, not only 科技).
            _project_aliases = {
                str(project_id),
                re.sub(r"(科技|技术|软件|信息|集团|有限公司|股份|inc|ltd|llc|corp|co)$", "", str(project_id), flags=re.I).strip("-_ "),
            }
            _project_aliases = {item for item in _project_aliases if item}
            _placeholders = ",".join("?" for _ in range(len(_project_aliases) or 1))
            rows = conn.execute(
                f"SELECT title FROM findings WHERE tenant_id IN (?, ?) AND project_id IN ({_placeholders}) ORDER BY created_at",
                (self._request_tenant(), "default", *sorted(_project_aliases)),
            ).fetchall()
            conn.close()
            return {r["title"][:120].lower() for r in rows}
        except Exception:
            return set()

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
    # ── Project-level customer metadata (主链 1) ──

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

    def log_message(self, fmt: str, *args: object) -> None:
        return


def run_private_pilot_service(root: Path | None = None, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    root = root or _root()
    host = host or os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"} and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1":
        raise ValueError("Public binding is disabled by default. Set QUALIBUG_ALLOW_PUBLIC_BIND=1 only behind a trusted reverse proxy.")
    selected_port = int(os.environ.get("QUALIBUG_PORT", "8088")) if port is None else int(port)
    server = ThreadingHTTPServer((host, selected_port), PrivatePilotHandler)
    server.qualibug_private_root = root
    # #region debug-point A:run-private-pilot-service
    _dbg_report(
        hypothesis_id="A",
        msg="[DEBUG] private-pilot service bound",
        data={
            "pid": os.getpid(),
            "root": str(root),
            "host": host,
            "port": selected_port,
        },
    )
    # #endregion
    return server


# ── Continuous discovery state management ─────────────────────────────



if __name__ == "__main__":
    raise SystemExit(
        "Unsupported launch path. Use qualibug-server "
        "(ai_test_asset_center.private_pilot_entrypoint:run_server)."
    )
