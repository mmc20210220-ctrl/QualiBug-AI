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
import tempfile
import time
import traceback
import uuid
import urllib.parse
import urllib.request
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


CONFIG_MANAGER_ROLES = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}
KNOWLEDGE_MANAGER_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
SETTINGS_MANAGER_ROLES = {"project_owner", "security_owner", "testops_admin", "admin"}
PROJECT_SCOPE_HEADER = "X-QualiBug-Project-Scopes"
MASKED_CREDENTIAL_VALUE = "********"


def _is_masked_credential_value(value: Any) -> bool:
    return str(value or "").strip() == MASKED_CREDENTIAL_VALUE


def _credential_update_value(incoming: Any, previous: Any = "") -> str:
    text = str(incoming or "").strip()
    if not text or _is_masked_credential_value(text):
        return str(previous or "")
    return text

# #region debug-point Z:debug-client
_DBG_ENV_CACHE: tuple[str, str] | None = None


def _dbg_env() -> tuple[str, str]:
    global _DBG_ENV_CACHE
    if _DBG_ENV_CACHE is not None:
        return _DBG_ENV_CACHE
    url = str(os.environ.get("QUALIBUG_DEBUG_SERVER_URL") or "").strip()
    session_id = str(os.environ.get("QUALIBUG_DEBUG_SESSION_ID") or "command-center-502").strip() or "command-center-502"
    env_path = Path(__file__).resolve().parents[1] / ".dbg" / f"{session_id}.env"
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            url = next((line.split("=", 1)[1].strip() for line in content.splitlines() if line.startswith("DEBUG_SERVER_URL=")), url)
            session_id = next((line.split("=", 1)[1].strip() for line in content.splitlines() if line.startswith("DEBUG_SESSION_ID=")), session_id)
    except Exception:
        pass
    _DBG_ENV_CACHE = (url, session_id)
    return _DBG_ENV_CACHE


def _dbg_report(*, hypothesis_id: str, msg: str, data: dict[str, Any] | None = None, run_id: str = "pre-fix", trace_id: str = "") -> None:
    # Debug reporting is disabled by default — must be explicitly enabled via
    # QUALIBUG_DEBUG_REPORT=1 to prevent unintended internal-state exfiltration.
    if not _truthy_env("QUALIBUG_DEBUG_REPORT", "0"):
        return
    try:
        url, session_id = _dbg_env()
        if not url:
            return
        payload = {
            "sessionId": session_id,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": "ai_test_asset_center/private_pilot_service.py",
            "msg": msg,
            "data": data or {},
            "traceId": trace_id,
            "ts": int(time.time() * 1000),
        }
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=0.2).read()
    except Exception:
        pass


def _dbg_fingerprint_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    import hashlib

    row = payload if isinstance(payload, dict) else {}
    source_manifest = row.get("source_manifest") if isinstance(row.get("source_manifest"), dict) else {}
    ui_target_resolution = row.get("ui_target_resolution") if isinstance(row.get("ui_target_resolution"), dict) else {}
    prd_text = str(row.get("prd") or "")
    api_doc = str(row.get("api_doc") or row.get("api_doc_text") or "")
    return {
        "project_id": str(row.get("project_id") or row.get("project") or ""),
        "scope_id": str(row.get("scope_id") or ""),
        "environment_ref": str(row.get("environment_ref") or row.get("target_environment") or ""),
        "execution_approval_id": str(row.get("execution_approval_id") or ""),
        "execution_mode": str(row.get("execution_mode") or ""),
        "base_url": str(row.get("base_url") or ""),
        "ui_base_url": str(row.get("ui_base_url") or ""),
        "ui_base_url_source": str(row.get("ui_base_url_source") or ""),
        "ui_target_resolution_status": str(ui_target_resolution.get("status") or ""),
        "ui_target_resolution_reason": str(ui_target_resolution.get("reason") or ""),
        "prd_len": len(prd_text),
        "prd_sha": hashlib.sha256(prd_text.encode("utf-8")).hexdigest() if prd_text else "",
        "api_len": len(api_doc),
        "api_sha": hashlib.sha256(api_doc.encode("utf-8")).hexdigest() if api_doc else "",
        "source_id": str(source_manifest.get("source_id") or ""),
        "source_hash": str(source_manifest.get("source_hash") or ""),
        "source_version_id": str(source_manifest.get("source_version_id") or ""),
    }

# #endregion
def _current_tenant() -> str:
    return os.environ.get("QUALIBUG_TENANT", "default")


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""








def _read_json_safe(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _read_json_artifact(path: Path) -> Any:
    """Read a present JSON artifact or fail with its identity in the error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc


def _read_json_object(path: Path, *, missing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return dict(missing or {})
    payload = _read_json_artifact(path)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _write_json_object_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Persist a JSON object without exposing a partially written artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


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
    _validate_scan_base_url,
)


def _run_ingest_auto_scan(
    *,
    root: Path,
    project: str,
    body: dict[str, Any],
    raw: bytes,
    doc_type: str,
    source_manifest: dict[str, Any],
) -> None:
    """Run an ingest-triggered scan and persist a failure receipt before raising."""
    phase = "runtime_defaults"
    try:
        scan_context: dict[str, Any] = {}
        if source_manifest.get("source_id") and source_manifest.get("source_hash"):
            scan_context["source_manifest"] = dict(source_manifest)
        defaults = _resolve_scan_runtime_defaults(project, root, body)
        if defaults.get("scope_id"):
            scan_context["scope_id"] = defaults["scope_id"]
        if defaults.get("environment_ref"):
            scan_context["environment_ref"] = defaults["environment_ref"]
        api_text = raw.decode("utf-8", errors="replace") if doc_type in {"openapi", "markdown_api"} else ""

        phase = "scan"
        from .__main__ import scan as scan_project

        scan_result = scan_project(
            project,
            root,
            api_doc_text=api_text,
            campaign_context=scan_context,
            save_report=True,
        )
        if not isinstance(scan_result, dict):
            raise TypeError("ingest auto-scan result must be an object")

        phase = "state_update"
        _update_continuous_state(root, project, scan_result)
    except Exception as exc:
        _write_json_object_atomic(
            root / "platform_outputs" / project / "auto_scan_last_error.json",
            {
                "schema": "qualibug.auto-scan-failure.v1",
                "project": project,
                "doc_type": doc_type,
                "phase": phase,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        raise











class TenantAuthenticationError(Exception):
    """An explicitly supplied tenant credential could not be authenticated."""


def _tenant_from_headers(headers: dict, *, root: Path | None = None) -> str:
    """Resolve tenant identity, rejecting invalid explicit credentials."""
    # Credential precedence is fail-closed: once a caller supplies a higher-
    # priority credential, it must authenticate and cannot fall through to a
    # lower-priority credential or the local development tenant.
    auth_present = "Authorization" in headers or "authorization" in headers
    auth = str(headers.get("Authorization") or headers.get("authorization") or "").strip()
    if auth_present:
        if not auth.startswith("Bearer ") or not auth[7:].strip():
            raise TenantAuthenticationError("invalid bearer token")
        try:
            payload = jwt_auth.verify_token(auth[7:].strip())
        except Exception as exc:
            raise TenantAuthenticationError(f"bearer token verification failed: {exc}") from exc
        tenant_id = str(payload.get("sub") or "").strip() if isinstance(payload, dict) else ""
        if not tenant_id:
            raise TenantAuthenticationError("invalid bearer token")
        return tenant_id
    # 2. HttpOnly Cookie (set by /api/auth/login) — preferred over localStorage
    #    because it is not readable by JavaScript, mitigating XSS token theft.
    cookie = headers.get("Cookie") or headers.get("cookie") or ""
    if cookie:
        from http.cookies import SimpleCookie
        try:
            ck = SimpleCookie()
            ck.load(cookie)
            morsel = ck.get("qualibug_token")
            if morsel:
                payload = jwt_auth.verify_token(morsel.value)
                tenant_id = str(payload.get("sub") or "").strip() if isinstance(payload, dict) else ""
                if not tenant_id:
                    raise TenantAuthenticationError("invalid cookie token")
                return tenant_id
        except TenantAuthenticationError:
            raise
        except Exception as exc:
            raise TenantAuthenticationError(f"cookie token verification failed: {exc}") from exc
    # 3. API Key
    api_key_present = "X-API-Key" in headers or "x-api-key" in headers
    api_key = str(headers.get("X-API-Key") or headers.get("x-api-key") or "").strip()
    if api_key_present:
        if not api_key:
            raise TenantAuthenticationError("invalid API key")
        try:
            tenant_id = str(db_persist.verify_api_key(root or _root(), api_key) or "").strip()
        except Exception as exc:
            raise TenantAuthenticationError(f"API key verification failed: {exc}") from exc
        if not tenant_id:
            raise TenantAuthenticationError("invalid API key")
        return tenant_id
    return _current_tenant()

_TENANT = _current_tenant()


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




def _augment_continuous_discovery_campaign(
    payload: dict[str, Any],
    *,
    current_report_breakdown: dict[str, Any],
    delivery_defects: list[dict[str, Any]],
    current_campaign_customer_ready_defect_count: int = 0,
    current_campaign_bundle_finding_count_raw: int = 0,
) -> dict[str, Any]:
    campaign_payload = dict(payload or {})
    if not campaign_payload:
        return {}
    campaign = dict(campaign_payload.get("campaign") or {}) if isinstance(campaign_payload.get("campaign"), dict) else {}
    summary = dict(campaign_payload.get("summary") or {}) if isinstance(campaign_payload.get("summary"), dict) else {}
    current_run = dict(campaign_payload.get("current_run") or {}) if isinstance(campaign_payload.get("current_run"), dict) else {}
    current_confirmed = summary.get("confirmed_slice_count")
    if current_confirmed in (None, ""):
        current_confirmed = current_run.get("confirmed_slice_count")
    if current_confirmed in (None, ""):
        current_confirmed = campaign.get("confirmed_slice_count")
    try:
        current_confirmed_count = max(0, int(current_confirmed or 0))
    except (TypeError, ValueError):
        current_confirmed_count = 0
    family_customer_ready_defect_count = len([item for item in delivery_defects if isinstance(item, dict)])
    try:
        family_report_real_finding_count = max(0, int((current_report_breakdown or {}).get("total_findings") or 0))
    except (TypeError, ValueError):
        family_report_real_finding_count = 0
    alignment_status = (
        "aligned"
        if family_customer_ready_defect_count == current_confirmed_count
        else "family_expands_beyond_current_campaign"
        if family_customer_ready_defect_count > current_confirmed_count
        else "current_campaign_exceeds_family_shelf"
    )
    summary["current_campaign_confirmed_slice_count"] = current_confirmed_count
    summary["current_campaign_customer_ready_defect_count"] = max(0, int(current_campaign_customer_ready_defect_count or 0))
    summary["current_campaign_bundle_finding_count_raw"] = max(0, int(current_campaign_bundle_finding_count_raw or 0))
    summary["family_customer_ready_defect_count"] = family_customer_ready_defect_count
    summary["family_report_real_finding_count"] = family_report_real_finding_count
    summary["family_historical_carryover_defect_count"] = max(0, family_customer_ready_defect_count - max(0, int(current_campaign_customer_ready_defect_count or 0)))
    summary["confirmed_shelf_alignment_status"] = alignment_status
    summary["confirmed_shelf_reporting_scope"] = "campaign_confirmed=current_campaign; defect_shelf=family_aggregated"
    current_run["current_campaign_confirmed_slice_count"] = current_confirmed_count
    current_run["current_campaign_customer_ready_defect_count"] = max(0, int(current_campaign_customer_ready_defect_count or 0))
    current_run["current_campaign_bundle_finding_count_raw"] = max(0, int(current_campaign_bundle_finding_count_raw or 0))
    current_run["family_customer_ready_defect_count"] = family_customer_ready_defect_count
    current_run["family_report_real_finding_count"] = family_report_real_finding_count
    campaign_payload["summary"] = summary
    campaign_payload["current_run"] = current_run
    return campaign_payload


def _current_campaign_scope_summary(payload: dict[str, Any]) -> dict[str, str]:
    campaign_payload = payload if isinstance(payload, dict) else {}
    campaign = campaign_payload.get("campaign") if isinstance(campaign_payload.get("campaign"), dict) else {}
    summary = campaign_payload.get("summary") if isinstance(campaign_payload.get("summary"), dict) else {}
    current_run = campaign_payload.get("current_run") if isinstance(campaign_payload.get("current_run"), dict) else {}
    campaign_id = _first_text(campaign.get("campaign_id"), summary.get("campaign_id"), current_run.get("campaign_id"))
    scope_id = _first_text(campaign.get("scope_id"), summary.get("scope_id"), current_run.get("scope_id"))
    environment_ref = _first_text(
        campaign.get("environment_ref"),
        campaign.get("target_environment"),
        summary.get("environment_ref"),
        summary.get("target_environment"),
        current_run.get("environment_ref"),
        current_run.get("target_environment"),
    )
    lineage_campaign_id = _first_text(campaign.get("lineage_campaign_id"), summary.get("lineage_campaign_id"))
    source_hash = _first_text(campaign.get("source_hash"), summary.get("source_hash"))
    source_snapshot_hash = _first_text(campaign.get("source_snapshot_hash"), summary.get("source_snapshot_hash"))
    if not any((campaign_id, scope_id, environment_ref, lineage_campaign_id, source_hash, source_snapshot_hash)):
        return {}
    return {
        "campaign_id": campaign_id,
        "lineage_campaign_id": lineage_campaign_id,
        "scope_id": scope_id,
        "environment_ref": environment_ref,
        "source_hash": source_hash,
        "source_snapshot_hash": source_snapshot_hash,
    }


def _current_campaign_bundle_finding_stats(
    project_id: str,
    root: Path,
    campaign_payload: dict[str, Any],
) -> dict[str, int]:
    campaign = campaign_payload.get("campaign") if isinstance(campaign_payload.get("campaign"), dict) else {}
    campaign_id = str(campaign.get("campaign_id") or "").strip()
    if not campaign_id:
        return {"raw": 0, "deduped": 0}
    bundle_root = root / "platform_workspace" / _safe_project_id(project_id) / "evidence_bundles"
    if not bundle_root.exists():
        return {"raw": 0, "deduped": 0}
    deduped_keys: set[str] = set()
    raw_count = 0
    for bundle_dir in bundle_root.iterdir():
        if not bundle_dir.is_dir():
            continue
        campaign_path = bundle_dir / "campaign.json"
        findings_path = bundle_dir / "findings.json"
        if not campaign_path.exists() or not findings_path.exists():
            continue
        try:
            bundle_campaign = json.loads(campaign_path.read_text(encoding="utf-8") or "{}")
            bundle_findings = json.loads(findings_path.read_text(encoding="utf-8") or "[]")
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(bundle_campaign, dict) or str(bundle_campaign.get("campaign_id") or "").strip() != campaign_id:
            continue
        if not isinstance(bundle_findings, list):
            continue
        raw_count += len([item for item in bundle_findings if isinstance(item, dict)])
        for finding in bundle_findings:
            if not isinstance(finding, dict):
                continue
            key = PrivatePilotHandler._report_finding_dedupe_key(finding)
            if key:
                deduped_keys.add(key)
    return {"raw": raw_count, "deduped": len(deduped_keys)}
KNOWLEDGE_INGEST_SOURCE_TYPES = (
    "prd",
    "mrd",
    "openapi",
    "postman",
    "database_schema",
    "permission_matrix",
    "historical_bug",
    "ticket",
    "feishu_document",
    "confluence_document",
    "collaboration_document",
    "other_document",
)
KNOWLEDGE_INGEST_TEXT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".json",
    ".csv",
    ".sql",
    ".xml",
)
KNOWLEDGE_INGEST_BINARY_EXTENSIONS = (".pdf", ".docx")
KNOWLEDGE_INGEST_EXTENSIONS = KNOWLEDGE_INGEST_TEXT_EXTENSIONS + KNOWLEDGE_INGEST_BINARY_EXTENSIONS
ONBOARD_DOCUMENT_EXTENSIONS = (".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ".htm")
ONBOARD_OPENAPI_EXTENSIONS = (".yaml", ".yml", ".json")


def _extensions_label(items: tuple[str, ...]) -> str:
    return " ".join(items)


def _extensions_accept(items: tuple[str, ...]) -> str:
    return ",".join(items)


def _root() -> Path:
    configured = os.environ.get("QUALIBUG_PRIVATE_ROOT", "").strip()
    return Path(configured).resolve() if configured else ROOT


def _actor(headers: Any) -> dict[str, str] | None:
    name = str(headers.get("X-QualiBug-Actor") or headers.get("x-qualibug-actor") or "").strip()
    role = str(headers.get("X-QualiBug-Role") or headers.get("x-qualibug-role") or "").strip()
    if not name or not role:
        return None
    return {"name": name[:120], "role": role[:64]}


def _truthy_env(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _parse_project_scopes(raw: str) -> tuple[set[str], bool]:
    items = [item.strip() for item in str(raw or "").replace(";", ",").split(",") if item.strip()]
    wildcard = any(item == "*" for item in items)
    return {_safe_project_id(item) for item in items if item != "*"}, wildcard


def _load_real_project_discovery_payload(root: Path, project_id: str) -> dict[str, Any] | None:
    project = _safe_project_id(project_id)
    candidates = (
        root / "platform_outputs" / project / "real_project" / "real_project_defect_data.json",
        root / "platform_workspace" / project / "real_project" / "real_project_defect_data.json",
        root / "platform_workspace" / project / "defect_discovery" / "continuous_discovery_state.json",
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = _read_json_object(candidate)
        payload.setdefault("report_source_path", candidate.relative_to(root).as_posix() if candidate.is_relative_to(root) else str(candidate))
        return payload
    return None


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




def _normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    payload = normalize_command_center_delivery(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload

    def _keep_customer_risk(item: Any) -> bool:
        return isinstance(item, dict) and not bool(item.get("_summary_only"))

    raw_defects = data.get("defects")
    raw_clues = data.get("clues")
    legacy_risks = data.get("risks") if isinstance(data.get("risks"), list) else []
    commercial_assets = _select_commercial_assets(data.get("commercial_assets"), data.get("external_commercial_assets"))

    if isinstance(raw_defects, list) or isinstance(raw_clues, list):
        defects = [item for item in (raw_defects if isinstance(raw_defects, list) else []) if _keep_customer_risk(item)]
        clues = [item for item in (raw_clues if isinstance(raw_clues, list) else []) if _keep_customer_risk(item)]
    else:
        legacy_items = [item for item in legacy_risks if _keep_customer_risk(item)]
        defects, clues = _partition_delivery_tracks(legacy_items)
    defects = [_annotate_ui_risk_item(dict(item)) for item in defects if isinstance(item, dict)]
    clues = [_annotate_ui_risk_item(dict(item)) for item in clues if isinstance(item, dict)]
    ui_stats = _ui_verification_stats(defects + clues)
    intake_stats = _defect_intake_stats(defects + clues)
    execution_evidence_summary = _normalize_execution_evidence_summary(
        data.get("execution_evidence_summary"),
        data.get("ui_execution_summary"),
        data.get("ui_execution"),
    )

    contract_base = data.get("data_contract") if isinstance(data.get("data_contract"), dict) else {}
    customer_contract = _rebuild_customer_display_contract(contract_base, defects)
    clue_contract = _build_internal_clue_contract(contract_base, clues)

    severity_counts = customer_contract.get("severity_counts") if isinstance(customer_contract.get("severity_counts"), dict) else {}
    existing_scan_meta = dict(data.get("scan_meta") or {}) if isinstance(data.get("scan_meta"), dict) else {}
    existing_value_metrics = dict(data.get("value_metrics") or {}) if isinstance(data.get("value_metrics"), dict) else {}
    existing_executive_summary = dict(data.get("executive_summary") or {}) if isinstance(data.get("executive_summary"), dict) else {}
    current_report_breakdown = (
        existing_scan_meta.get("current_report_breakdown")
        if isinstance(existing_scan_meta.get("current_report_breakdown"), dict)
        else existing_value_metrics.get("current_report_breakdown")
        if isinstance(existing_value_metrics.get("current_report_breakdown"), dict)
        else existing_executive_summary.get("current_report_breakdown")
        if isinstance(existing_executive_summary.get("current_report_breakdown"), dict)
        else contract_base.get("current_report_breakdown")
        if isinstance(contract_base.get("current_report_breakdown"), dict)
        else {}
    )

    def _scope_count(*values: Any, fallback: int = 0) -> int:
        for value in values:
            try:
                if value not in (None, ""):
                    return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return max(0, int(fallback or 0))

    current_scope_total_findings = _scope_count(
        existing_scan_meta.get("current_report_total_findings"),
        current_report_breakdown.get("total_findings") if isinstance(current_report_breakdown, dict) else None,
        existing_scan_meta.get("total_findings"),
        existing_executive_summary.get("total_findings"),
        fallback=len(defects),
    )
    current_scope_customer_ready_defects = _scope_count(
        existing_scan_meta.get("current_report_customer_ready_defect_count"),
        existing_scan_meta.get("current_campaign_customer_ready_defect_count"),
        existing_scan_meta.get("customer_ready_defects"),
        existing_executive_summary.get("customer_ready_defects"),
        fallback=len(defects),
    )
    current_scope_materialized_findings = _scope_count(
        existing_scan_meta.get("current_report_materialized_findings"),
        existing_scan_meta.get("materialized_findings"),
        existing_executive_summary.get("materialized_findings"),
        fallback=current_scope_total_findings,
    )
    current_scope_ready_bug_count = _scope_count(
        existing_scan_meta.get("ready_bug_count"),
        existing_value_metrics.get("ready_bug_count"),
        existing_executive_summary.get("ready_bugs"),
        existing_executive_summary.get("total_bugs_found"),
        fallback=current_scope_customer_ready_defects,
    )
    campaign_scope = (
        existing_scan_meta.get("current_campaign_scope")
        if isinstance(existing_scan_meta.get("current_campaign_scope"), dict)
        else data.get("current_campaign_scope")
        if isinstance(data.get("current_campaign_scope"), dict)
        else _current_campaign_scope_summary(
            data.get("continuous_discovery_campaign") if isinstance(data.get("continuous_discovery_campaign"), dict) else {}
        )
    )
    family_customer_ready_defect_count = len(defects)
    p0_count = int(severity_counts.get("P0") or sum(1 for item in defects if item.get("severity") == "P0"))
    p1_count = int(severity_counts.get("P1") or sum(1 for item in defects if item.get("severity") == "P1"))
    p2_count = max(0, len(defects) - p0_count - p1_count)
    ready_bug_count = current_scope_ready_bug_count
    needs_validation_count = int(clue_contract.get("needs_validation_count") or 0)
    not_reproduced_count = int(clue_contract.get("not_reproduced_count") or 0)

    value_metrics = dict(data.get("value_metrics") or {})
    value_metrics["canonical_risk_count"] = current_scope_total_findings
    value_metrics["materialized_risk_count"] = current_scope_materialized_findings
    value_metrics["raw_candidate_risk_count"] = int(customer_contract.get("raw_candidate_risk_count") or current_scope_total_findings)
    value_metrics["ready_bug_count"] = ready_bug_count
    value_metrics["needs_validation_count"] = needs_validation_count
    value_metrics["not_reproduced_count"] = not_reproduced_count
    value_metrics["defect_count"] = family_customer_ready_defect_count
    value_metrics["clue_count"] = len(clues)
    value_metrics["p0_count"] = p0_count
    value_metrics["p1_count"] = p1_count
    value_metrics["p2_count"] = p2_count
    value_metrics["ready_p0_count"] = p0_count
    value_metrics["ready_p1_count"] = p1_count
    value_metrics["current_report_total_findings"] = current_scope_total_findings
    value_metrics["current_report_customer_ready_defect_count"] = current_scope_customer_ready_defects
    value_metrics["family_customer_ready_defect_count"] = family_customer_ready_defect_count
    if campaign_scope:
        value_metrics["current_campaign_scope"] = campaign_scope
    value_metrics["status_counts"] = customer_contract.get("status_counts") if isinstance(customer_contract.get("status_counts"), dict) else {}
    value_metrics["commercial_asset_materialized"] = 1 if commercial_assets.get("status") == "materialized" else 0
    value_metrics["commercial_delivery_package_created"] = 1 if _first_text((commercial_assets.get("delivery_package") or {}).get("status")) == "created" else 0
    if execution_evidence_summary:
        value_metrics["ui_execution_requested"] = int(execution_evidence_summary.get("requested") or 0)
        value_metrics["ui_execution_executed"] = int(execution_evidence_summary.get("executed") or 0)
        value_metrics["ui_execution_evidence_captured"] = int(execution_evidence_summary.get("evidence_captured_count") or 0)
        value_metrics["ui_execution_artifact_count"] = int(execution_evidence_summary.get("artifact_count") or 0)
    value_metrics.update(ui_stats)
    value_metrics.update(intake_stats)

    executive_summary = dict(data.get("executive_summary") or {})
    executive_summary["total_findings"] = current_scope_total_findings
    executive_summary["total_bugs_found"] = ready_bug_count
    executive_summary["ready_bugs"] = ready_bug_count
    executive_summary["customer_ready_defects"] = current_scope_customer_ready_defects
    executive_summary["family_customer_ready_defects"] = family_customer_ready_defect_count
    executive_summary["internal_clues"] = len(clues)
    executive_summary["needs_validation_findings"] = needs_validation_count
    executive_summary["not_reproduced_findings"] = not_reproduced_count
    executive_summary["raw_candidate_findings"] = int(customer_contract.get("raw_candidate_risk_count") or current_scope_total_findings)
    executive_summary["critical_bugs"] = p0_count
    executive_summary["high_priority_bugs"] = p1_count
    executive_summary["materialized_findings"] = current_scope_materialized_findings
    executive_summary["ui_candidate_findings"] = ui_stats["ui_candidate_total"]
    executive_summary["ui_verified_candidates"] = ui_stats["ui_verified_candidate_total"]
    executive_summary["ui_high_confidence_candidates"] = ui_stats["ui_high_confidence_candidate_total"]
    executive_summary["defect_intake_recommended"] = intake_stats["defect_intake_recommended_total"]
    executive_summary["internal_defect_intake_candidates"] = intake_stats["internal_defect_intake_total"]
    if execution_evidence_summary:
        executive_summary["ui_execution_requested"] = int(execution_evidence_summary.get("requested") or 0)
        executive_summary["ui_execution_executed"] = int(execution_evidence_summary.get("executed") or 0)
        executive_summary["ui_execution_evidence_captured"] = int(execution_evidence_summary.get("evidence_captured_count") or 0)
        executive_summary["ui_execution_summary"] = str(execution_evidence_summary.get("summary") or "")
    executive_summary["commercial_handoff_status"] = _first_text((commercial_assets.get("commercial_handoff") or {}).get("status"))
    executive_summary["delivery_package_status"] = _first_text((commercial_assets.get("delivery_package") or {}).get("status"))
    if campaign_scope:
        executive_summary["current_campaign_scope"] = campaign_scope

    scan_meta = dict(data.get("scan_meta") or {})
    scan_meta["total_findings"] = current_scope_total_findings
    scan_meta["materialized_findings"] = current_scope_materialized_findings
    scan_meta["raw_candidate_findings"] = int(customer_contract.get("raw_candidate_risk_count") or current_scope_total_findings)
    scan_meta["ready_bug_count"] = ready_bug_count
    scan_meta["needs_validation_findings"] = needs_validation_count
    scan_meta["not_reproduced_findings"] = not_reproduced_count
    scan_meta["customer_ready_defects"] = current_scope_customer_ready_defects
    scan_meta["current_report_total_findings"] = current_scope_total_findings
    scan_meta["current_report_materialized_findings"] = current_scope_materialized_findings
    scan_meta["current_report_customer_ready_defect_count"] = current_scope_customer_ready_defects
    scan_meta["family_customer_ready_defect_count"] = family_customer_ready_defect_count
    scan_meta["family_materialized_findings"] = family_customer_ready_defect_count
    scan_meta["internal_clue_count"] = len(clues)
    scan_meta["ui_candidate_findings"] = ui_stats["ui_candidate_total"]
    scan_meta["ui_verified_candidates"] = ui_stats["ui_verified_candidate_total"]
    scan_meta["ui_high_confidence_candidates"] = ui_stats["ui_high_confidence_candidate_total"]
    scan_meta["defect_intake_recommended"] = intake_stats["defect_intake_recommended_total"]
    if execution_evidence_summary:
        scan_meta["ui_execution_requested"] = int(execution_evidence_summary.get("requested") or 0)
        scan_meta["ui_execution_executed"] = int(execution_evidence_summary.get("executed") or 0)
        scan_meta["ui_execution_evidence_captured"] = int(execution_evidence_summary.get("evidence_captured_count") or 0)
        scan_meta["ui_execution_artifact_count"] = int(execution_evidence_summary.get("artifact_count") or 0)
    scan_meta["commercial_handoff_status"] = _first_text((commercial_assets.get("commercial_handoff") or {}).get("status"))
    scan_meta["external_tracker_sync_payload_status"] = _first_text((commercial_assets.get("tracker_sync") or {}).get("payload_status"))
    scan_meta["delivery_package_status"] = _first_text((commercial_assets.get("delivery_package") or {}).get("status"))
    if campaign_scope:
        scan_meta["current_campaign_scope"] = campaign_scope

    data["defects"] = defects
    data["clues"] = clues
    data["risks"] = defects
    if commercial_assets:
        data["commercial_assets"] = commercial_assets
    if execution_evidence_summary:
        data["execution_evidence_summary"] = execution_evidence_summary
    if campaign_scope:
        data["current_campaign_scope"] = campaign_scope
    data["scan_meta"] = scan_meta
    data["value_metrics"] = value_metrics
    data["data_contract"] = {
        **customer_contract,
        "endpoint": data.get("data_contract", {}).get("endpoint") if isinstance(data.get("data_contract"), dict) else "",
        "frontend_entry": "frontend/src/api/client.ts:getFindings",
        "backend_builder": "ai_test_asset_center/private_pilot_service.py:_build_command_center",
        "formatter": "ai_test_asset_center/display_ready_formatter.py:format_findings_display_ready",
        "display_key": "defects",
        "compatibility_alias": "risks",
        "contract_rule": "客户页优先渲染 data.defects；data.risks 仅为兼容别名。内部待验证线索统一放在 data.clues。",
    }
    data["delivery_tracks"] = {
        "defects": {
            **customer_contract,
            "display_key": "defects",
            "compatibility_alias": "risks",
        },
        "clues": clue_contract,
    }
    data["executive_summary"] = executive_summary
    payload["data"] = data
    return payload


def _write_env_local(updates: dict[str, str]) -> Path:
    configured = os.environ.get("QUALIBUG_ENV_LOCAL_PATH", "").strip()
    env_path = Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1] / ".env.local"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else [
        "# Local-only QualiBug LLM credentials.",
        "# This file is ignored by git. Do not share it.",
        "",
    ]
    keys = set(updates)
    written: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        raw = line.strip()
        key = raw.split("=", 1)[0].strip().upper() if "=" in raw and not raw.startswith("#") else ""
        if key in keys:
            new_lines.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            new_lines.append(line)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    for key in sorted(keys - written):
        new_lines.append(f"{key}={updates[key]}")
    env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return env_path


def _known_project_exists(root: Path, project: str) -> bool:
    project = _safe_project_id(project)
    candidates = (
        root / "platform_inputs" / project / "real_project_config.json",
        root / "platform_outputs" / project,
        root / "platform_workspace" / project,
    )
    return any(path.exists() for path in candidates)


def _project_output_dir_for_import(root: Path, project_id: str) -> tuple[str, Path]:
    safe_project_id = _safe_project_id(project_id)
    output_dir = (root / "platform_outputs" / safe_project_id).resolve()
    platform_outputs = (root / "platform_outputs").resolve()
    if platform_outputs not in output_dir.parents and output_dir != platform_outputs:
        raise ValueError("project output path escaped platform_outputs")
    return safe_project_id, output_dir


def _knowledge_asset_sources(asset: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    inventory = asset.get("source_inventory") or asset.get("sources") or []
    if not isinstance(inventory, list):
        return rows
    for item in inventory:
        if not isinstance(item, dict):
            continue
        stored_path = str(item.get("stored_path") or item.get("path") or "")
        path = root / stored_path if stored_path and not Path(stored_path).is_absolute() else Path(stored_path) if stored_path else None
        size = path.stat().st_size if path and path.exists() and path.is_file() else int(item.get("size_bytes") or 0)
        rows.append({
            "source_id": str(item.get("source_id") or item.get("id") or ""),
            "filename": str(item.get("original_name") or item.get("filename") or item.get("name") or ""),
            "source_type": str(item.get("source_type") or item.get("type") or ""),
            "status": str(item.get("status") or "active"),
            "size_bytes": size,
            "uploaded_at": str(item.get("created_at_utc") or item.get("uploaded_at") or item.get("created_at") or ""),
            "version": item.get("version", 1),
            "parse_status": str((item.get("parse") or {}).get("parse_status") or item.get("parse_status") or ""),
        })
    return rows


def _normalize_frontend_page_path(path: str) -> str:
    clean = "/" + str(path or "/").strip().strip("/")
    return {
        "/materials": "/knowledge",
        "/evidence": "/findings",
    }.get(clean, clean)


def _synchronize_scan_aggregates(report: dict[str, Any]) -> dict[str, Any]:
    """Make every scan view derive its counts from the final calibrated list.

    Health, semantic and validation stages may replace the discovery list after
    the autonomous pipeline has built its executive summary. Keeping the
    aggregation here prevents release and management views from under-reporting
    the final risk population.
    """
    stage2 = dict(report.get("stage2_discovery") or {})
    findings = [item for item in (stage2.get("findings") or []) if isinstance(item, dict)]
    stage2["findings"] = findings
    stage2["total_findings"] = len(findings)
    severities = sorted({str(item.get("severity") or "unknown") for item in findings})
    stage2["by_severity"] = {
        severity: sum(1 for item in findings if str(item.get("severity") or "unknown") == severity)
        for severity in severities
    }
    report["stage2_discovery"] = stage2

    executive = dict(report.get("executive_summary") or {})
    executive["total_bugs_found"] = len(findings)
    executive["critical_bugs"] = sum(1 for item in findings if str(item.get("severity") or "") == "P0")
    executive["high_priority_bugs"] = sum(1 for item in findings if str(item.get("severity") or "") == "P1")

    stage3 = dict(report.get("stage3_impact_analysis") or {})
    analyses = [item for item in (stage3.get("analyses") or []) if isinstance(item, dict)]
    stage3["analyses"] = analyses
    stage3["total_analyses"] = len(analyses)
    stage3["llm_powered"] = sum(1 for item in analyses if item.get("source") == "llm_evidence_impact")
    stage3["heuristic"] = len(analyses) - int(stage3["llm_powered"])
    report["stage3_impact_analysis"] = stage3
    executive["impact_analyses"] = len(analyses)
    executive["llm_powered_analyses"] = int(stage3["llm_powered"])
    report["executive_summary"] = executive
    return report


def _extend_stage3_impact_analysis(report: dict[str, Any], analyses: list[dict[str, Any]]) -> None:
    """Append post-pipeline impact notes without discarding LLM assessments."""
    if not analyses:
        return
    stage3 = dict(report.get("stage3_impact_analysis") or {})
    existing = [item for item in (stage3.get("analyses") or []) if isinstance(item, dict)]
    existing.extend(item for item in analyses if isinstance(item, dict))
    stage3["analyses"] = existing
    stage3["total_analyses"] = len(existing)
    stage3["llm_powered"] = sum(1 for item in existing if item.get("source") == "llm_evidence_impact")
    stage3["heuristic"] = sum(1 for item in existing if item.get("source") != "llm_evidence_impact")
    report["stage3_impact_analysis"] = stage3


class PrivatePilotHandler(BaseHTTPRequestHandler):
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        project = self._project()
        root = self._root()
        # Serve the prebuilt customer pilot SPA for any non-API path (public, before the
        # auth gate so the login page itself is reachable). Known legacy server-rendered
        # pages are excluded so they keep working behind auth.
        if not parsed.path.startswith("/api") and parsed.path != "/health":
            _legacy_served = {"/onboard", "/dashboard", "/knowledge", "/benchmark", "/release"}
            if parsed.path not in _legacy_served:
                return self._serve_frontend(parsed, root)
        if parsed.path in {"/health", "/api/health"}:
            import platform, sys
            llm_health = self._llm_health()
            try:
                from .bug_knowledge_graph import EnterprisePatternLibrary
                lib = EnterprisePatternLibrary()
                pattern_count = lib.stats().get("total_patterns", 0)
            except Exception:
                pattern_count = 0
            return self._json(
                {
                    "ok": True,
                    "service": "qualibug_private_pilot",
                    "version": "phase61",
                    "private_root": str(root),
                    "private_root_exists": root.exists(),
                    "public_bind_allowed": os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1",
                    "python_version": sys.version.split()[0],
                    "platform": platform.system(),
                    "llm_available": llm_health["available"],
                    "llm_status": llm_health,
                    "pattern_library_patterns": pattern_count,
                }
            )
        # Every non-health route requires a trusted actor. `_require_actor()`
        # keeps the narrowly-scoped localhost development fallback, but only
        # when public binding is disabled and the caller has not explicitly
        # requested a negative-auth probe. This prevents public/private-cloud
        # GET endpoints from silently serving project data to anonymous users.
        actor = self._require_actor()
        if actor is None:
            return
        if self._require_tenant(root) is None:
            return
        _route_parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        _route_project = (
            _route_parts[3]
            if len(_route_parts) >= 4 and _route_parts[:3] == ["api", "v1", "projects"]
            else ""
        )
        if _route_project:
            project = _safe_project_id(_route_project)
        if not self._require_project_scope(project):
            return
        if parsed.path == "/onboard":
            return self._render_onboard(project, root)
        if parsed.path in {"/", "/dashboard"}:
            build_enterprise_pilot_overview(project, root)
            path = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            fallback = "<h1>Enterprise pilot dashboard is not generated yet.</h1>"
            return self._html(path.read_text(encoding="utf-8") if path.exists() else fallback)
        if parsed.path == "/knowledge":
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset, render_enterprise_business_knowledge_center

            asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
            return self._html(render_enterprise_business_knowledge_center(project, root, asset))
        if parsed.path == "/benchmark":
            from .enterprise_testops_control_plane import render_multi_industry_benchmark_report, run_multi_industry_benchmark

            report = run_multi_industry_benchmark(project, root)
            return self._html(render_multi_industry_benchmark_report(report))
        if parsed.path == "/release":
            from .release_risk_dashboard import build_release_risk_dashboard, render_release_risk_dashboard_html

            dashboard = build_release_risk_dashboard(project, root)
            return self._html(render_release_risk_dashboard_html(dashboard))
        if parsed.path == "/settings":
            return self._render_settings(project, root)
        if parsed.path == "/findings":
            return self._render_findings(project, root)
        if parsed.path == "/api/v1/projects":
            # Merge DB projects + filesystem-discovered projects (dedup by project_id)
            tenant_id = self._request_tenant()
            try:
                db_persist.init_db(root)
                items = db_persist.list_projects(root, tenant_id)
            except Exception:
                items = []
            # Always scan filesystem to discover projects not yet in DB
            scopes, wildcard = self._project_list_scope_filter()
            seen: set[str] = {str(it.get("project_id") or "") for it in items}
            for base_name in ("platform_outputs", "platform_workspace", "platform_inputs"):
                for d in sorted((root / base_name).glob("*")):
                    if not d.is_dir():
                        continue
                    if not wildcard and d.name not in scopes:
                        continue
                    if d.name in seen:
                        continue
                    seen.add(d.name)
                    items.append({
                        "project_id": d.name,
                        "customer_name": d.name,
                        "project_name": d.name,
                        "source": base_name,
                    })
            return self._json({"ok": True, "data": items})
        if len(_route_parts) >= 5 and _route_parts[:3] == ["api", "v1", "projects"] and _route_parts[4] == "campaigns":
            return self._handle_campaign_get(project, _route_parts[5:], parse_qs(parsed.query), root)
        # Bridge: serve V12 results in legacy format for Dashboard/Findings
        if parsed.path.startswith("/api/v1/projects/") and parsed.path.endswith("/command-center"):
            pid = parsed.path.split("/")[4] if len(parsed.path.split("/")) >= 5 else ""
            pid = urllib.parse.unquote(pid)
            trace_id = uuid.uuid4().hex
            started = time.perf_counter()
            _dbg_report(
                hypothesis_id="C",
                msg="[DEBUG] command-center enter",
                data={"project_id": pid, "path": parsed.path},
                trace_id=trace_id,
            )
            try:
                payload = self._build_command_center(pid, root)
                try:
                    from .display_ready_formatter import sanitize_customer_evidence_payload
                    payload = sanitize_customer_evidence_payload(payload)
                except Exception as sanitize_exc:
                    _dbg_report(
                        hypothesis_id="F",
                        msg="[WARN] command-center evidence response sanitize skipped",
                        data={"project_id": pid, "error": str(sanitize_exc)},
                        trace_id=trace_id,
                    )
                payload = _normalize_command_center_envelope(payload)
                _dbg_report(
                    hypothesis_id="C",
                    msg="[DEBUG] command-center built",
                    data={
                        "project_id": pid,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "keys": list(payload.keys())[:16],
                        "defect_count": len(((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("defects") or [])),
                        "clue_count": len(((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("clues") or [])),
                        "scan_meta": (((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("scan_meta") if isinstance((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("scan_meta"), dict) else {})),
                    },
                    trace_id=trace_id,
                )
                return self._json(payload)
            except BaseException as exc:
                _dbg_report(
                    hypothesis_id="B",
                    msg="[DEBUG] command-center exception",
                    data={"project_id": pid, "exc_type": type(exc).__name__, "exc": str(exc)},
                    trace_id=trace_id,
                )
                return self._json(
                    {"ok": False, "error": "COMMAND_CENTER_FAILED", "message": str(exc)},
                    500,
                )
        if parsed.path == "/api/tenants/create":
            return self._json({"ok": False, "error": "METHOD_NOT_ALLOWED", "message": "Use POST /api/tenants/create."}, 405)
        if parsed.path == "/api/auth/password/reset":
            return self._json({"ok": False, "error": "METHOD_NOT_ALLOWED", "message": "Use POST /api/auth/password/reset."}, 405)
        if parsed.path == "/api/connectors/list":
            from .enterprise_pilot_runtime import load_connector_registry
            registry = load_connector_registry(project, root)
            connectors = registry.get("connectors", [])
            return self._json({"ok": True, "connectors": connectors})
        if parsed.path == "/api/control-plane/overview":
            from .enterprise_testops_control_plane import build_enterprise_testops_control_plane, load_enterprise_testops_control_plane

            return self._json({"ok": True, "control_plane": load_enterprise_testops_control_plane(project, root) or build_enterprise_testops_control_plane(project, root)})
        if parsed.path == "/api/knowledge/asset":
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset

            asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
            # Also include project input files as knowledge sources
            input_files = self._list_project_inputs(project, root)
            existing_sources = _knowledge_asset_sources(asset, root)
            if not isinstance(existing_sources, list):
                existing_sources = []
            input_sources = input_files.get("sources", [])
            if not isinstance(input_sources, list):
                input_sources = []
            merged_by_key: dict[str, dict[str, Any]] = {}
            order: list[str] = []
            for item in list(existing_sources) + list(input_sources):
                if not isinstance(item, dict):
                    continue
                key = str(item.get("source_id") or item.get("id") or item.get("filename") or "")
                if not key:
                    continue
                current = merged_by_key.get(key)
                if current is None:
                    merged_by_key[key] = dict(item)
                    order.append(key)
                    continue
                incoming_uploaded_at = str(item.get("uploaded_at") or item.get("created_at_utc") or item.get("created_at") or "").strip()
                if not str(current.get("uploaded_at") or current.get("created_at_utc") or current.get("created_at") or "").strip() and incoming_uploaded_at:
                    current["uploaded_at"] = incoming_uploaded_at
                if int(current.get("size_bytes") or 0) <= 0 and int(item.get("size_bytes") or 0) > 0:
                    current["size_bytes"] = int(item.get("size_bytes") or 0)
            merged = [merged_by_key[k] for k in order]
            asset["sources"] = merged
            if not isinstance(asset.get("summary"), dict):
                asset["summary"] = {}
            asset["summary"]["active_source_count"] = len(asset["sources"])
            return self._json({"ok": True, "knowledge_asset": asset})
        if parsed.path == "/api/knowledge/preview":
            return self._handle_preview(project, {"source_id": parse_qs(parsed.query).get("source_id", [""])[0]}, root)
        if parsed.path == "/api/evidence/artifact":
            return self._handle_evidence_artifact(project, parse_qs(parsed.query).get("ref", [""])[0], root)
        # Settings-page read-back routes (P0-1 fix): these were previously only
        # declared inside do_POST behind a `if self.command == "GET"` guard that can
        # never be true there, so real GET requests fell through to 404. Wire them
        # into do_GET so the customer settings page can load saved config.
        if parsed.path == "/api/v1/services/credentials":
            return self._handle_get_service_credentials(project, root)
        if parsed.path == "/api/v1/project/metadata":
            return self._handle_get_project_metadata(project, root)
        if parsed.path == "/api/v1/scan/preflight":
            return self._handle_scan_preflight(project, root)
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)


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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        root = self._root()
        # Auth & tenant routes — no actor required
        if parsed.path in ("/api/auth/login", "/api/tenants/create", "/api/auth/password/reset"):
            try:
                body = self._body()
            except Exception:
                return self._json({"ok": False, "error": "BAD_REQUEST"}, 400)
            db_persist.init_db(root)
            if parsed.path == "/api/auth/login":
                username = str(body.get("username") or body.get("api_key") or "").strip()
                password = str(body.get("password") or "").strip()
                auth_result = db_persist.authenticate_tenant(root, username, password)
                if not auth_result:
                    return self._json({"ok": False, "error": "INVALID_CREDENTIALS"}, 401)
                token = jwt_auth.create_token(
                    str(auth_result["tenant_id"]),
                    str(auth_result.get("role") or "admin"),
                )
                _cookie_flags = "HttpOnly; SameSite=Lax; Path=/"
                if os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1":
                    _cookie_flags += "; Secure"
                return self._json({
                    "ok": True,
                    "token": token,
                    "tenant_id": auth_result["tenant_id"],
                    "role": auth_result.get("role") or "admin",
                }, extra_headers={"Set-Cookie": f"qualibug_token={token}; {_cookie_flags}"})
            if parsed.path == "/api/auth/password/reset":
                tid = str(body.get("tenant_id") or body.get("workspace_id") or "").strip()
                username = str(body.get("username") or "").strip()
                new_password = str(body.get("new_password") or body.get("password") or "")
                reset_result = db_persist.reset_tenant_password(
                    root,
                    tenant_id=tid,
                    username=username,
                    new_password=new_password,
                )
                if not reset_result.get("ok"):
                    error = str(reset_result.get("error") or "RESET_DENIED")
                    if error == "PASSWORD_TOO_SHORT":
                        return self._json({"ok": False, "error": error, "message": "密码长度至少 8 位"}, 400)
                    if error == "MISSING_FIELDS":
                        return self._json({"ok": False, "error": error, "message": "请填写完整重置信息"}, 400)
                    # Generic denial — do not reveal which field mismatched.
                    return self._json(
                        {"ok": False, "error": "RESET_DENIED", "message": "工作区或账号不匹配，无法重置密码"},
                        403,
                    )
                return self._json({
                    "ok": True,
                    "tenant_id": reset_result["tenant_id"],
                    "username": reset_result["username"],
                })
            if parsed.path == "/api/tenants/create":
                tid = str(body.get("tenant_id") or "").strip()
                name = str(body.get("name") or "").strip()
                username = str(body.get("username") or "").strip()
                password = str(body.get("password") or "").strip()
                role = str(body.get("role") or "admin").strip() or "admin"
                if not tid or not name or not username or not password:
                    return self._json({"ok": False, "error": "MISSING_FIELDS"}, 400)
                tenant_result = db_persist.create_tenant(
                    root,
                    tid,
                    name,
                    username=username,
                    password=password,
                    role=role,
                )
                if not tenant_result.get("ok"):
                    return self._json(tenant_result)
                db_persist.create_project(root, tid, tid, name)
                return self._json({"ok": True, "tenant_id": tid, "username": username, "role": role})
        actor = self._require_actor()
        if actor is None:
            return
        if self._require_tenant(root) is None:
            return
        try:
            body = self._body()
            route_project = ""
            route_parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
            if len(route_parts) >= 4 and route_parts[:3] == ["api", "v1", "projects"]:
                route_project = route_parts[3]
            project = _safe_project_id(str(body.get("project_id") or route_project or self._project()))
            if not self._require_project_scope(project):
                return
            if parsed.path == "/api/v1/evaluations/submissions":
                from .campaign_api_contract import build_evaluation_submission

                return self._json({"ok": True, "data": build_evaluation_submission(root, project, body)}, 201)
            if len(route_parts) == 5 and route_parts[:3] == ["api", "v1", "projects"] and route_parts[4] == "campaigns":
                from .campaign_api_contract import create_campaign

                return self._json({"ok": True, "data": create_campaign(root, project, body)}, 201)
            if (
                len(route_parts) == 7
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4] == "campaigns"
                and route_parts[6] in {"run", "resume"}
            ):
                from .campaign_api_contract import CampaignContractError, load_created_campaign

                campaign_contract = load_created_campaign(root, project, route_parts[5])
                if campaign_contract.get("status") != "ready":
                    raise CampaignContractError(
                        "campaign is not ready; resolve target_policy_decision.blocking_codes before execution"
                    )
                runtime_input = campaign_contract.get("runtime_input") if isinstance(campaign_contract.get("runtime_input"), dict) else {}
                scan_body = {
                    **body,
                    **runtime_input,
                    "project_id": project,
                    "campaign_id": route_parts[5],
                    "target_policy_decision": campaign_contract.get("target_policy_decision"),
                }
                return self._handle_v12_scan(project, root, actor, scan_body)
            if (
                len(route_parts) == 6
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4:] == ["environment", "preflight"]
            ):
                return self._handle_scan_preflight(project, root, body)
            if parsed.path == "/api/knowledge/ingest":
                if not self._require_role(actor, KNOWLEDGE_MANAGER_ROLES, "knowledge source ingestion"):
                    return
                return self._handle_ingest(project, body, root, actor)
            elif parsed.path.startswith("/api/knowledge/delete"):
                if not self._require_role(actor, KNOWLEDGE_MANAGER_ROLES, "knowledge source deletion"):
                    return
                return self._handle_delete(project, body, root, actor)
            elif parsed.path == "/api/environment/config":
                from .enterprise_testops_control_plane import save_environment_config
                result = save_environment_config(project, body.get("payload") or body, root, actor)
                # Clear dashboard cache so it picks up new env config
                dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
                if dash_html.exists(): dash_html.unlink()
                return self._json(result)
            elif parsed.path == "/api/v1/scan":
                # #region debug-point A0:scan-route-enter
                _dbg_report(
                    hypothesis_id="A0",
                    msg="[DEBUG] do_POST matched /api/v1/scan",
                    data={"project": project, "path": parsed.path, "body_keys": sorted(body.keys()) if isinstance(body, dict) else []},
                    trace_id=str(uuid.uuid4()),
                )
                # #endregion
                return self._handle_v12_scan(project, root, actor, body)
            elif parsed.path == "/api/v1/scan/preflight":
                return self._handle_scan_preflight(project, root)
            elif parsed.path == "/api/v1/continuous/status":
                return self._json(_get_continuous_state(root, project))
            elif parsed.path == "/api/v1/continuous/start":
                return self._handle_continuous_start(project, root, actor, body)
            elif parsed.path == "/api/v1/continuous/stop":
                return self._handle_continuous_stop(project, root)
            elif parsed.path == "/api/v1/spectrum/status":
                return self._get_spectrum_status(project, root)
            elif parsed.path == "/api/v1/db-test":
                return self._handle_db_test(body)
            elif parsed.path == "/api/v1/replay":
                return self._handle_replay(project, root, body)
            elif parsed.path.startswith("/api/v1/projects/") and parsed.path.endswith("/regression/run"):
                return self._handle_regression_run(project, root, body)
            elif parsed.path == "/api/v1/services/credentials":
                if self.command == "GET":
                    return self._handle_get_service_credentials(project, root)
                return self._handle_save_service_credentials(project, root, body)
            elif parsed.path == "/api/v1/project/metadata":
                if self.command == "GET":
                    return self._handle_get_project_metadata(project, root)
                return self._handle_save_project_metadata(project, root, body)
            elif parsed.path == "/api/settings/save":
                if not self._require_role(actor, SETTINGS_MANAGER_ROLES, "system settings update"):
                    return
                return self._handle_settings_save(body)
            elif parsed.path == "/api/connectors/register":
                result = operate_enterprise_pilot_runtime(project, "register_connector", body, root, actor)
                return self._json({"ok": True, "message": "Connector registered."})
            else:
                return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
            return self._json(result)
        except CampaignContractError as exc:
            error = structured_error(
                stage="campaign_api",
                code="CAMPAIGN_CONTRACT_BLOCKED",
                identity={"project_id": locals().get("project", "")},
                retryability="after_operator_action",
                operator_action=str(exc),
            )
            return self._json({"ok": False, "error": error}, 409)
        except PermissionError as exc:
            return self._json({"ok": False, "error": "FORBIDDEN", "message": str(exc)}, 403)
        except (ValueError, KeyError) as exc:
            return self._json({"ok": False, "error": "BAD_REQUEST", "message": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive private-service boundary
            return self._json({"ok": False, "error": "INTERNAL_ERROR", "message": str(exc)[:300]}, 500)

    def _handle_ingest(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Handle document ingestion via API with verbatim byte storage."""
        import base64
        from .document_change_watcher import ingest_document
        from .enterprise_knowledge_center import ingest_enterprise_knowledge_documents

        if not self._require_known_project(project, root):
            return
        doc_type = str(body.get("type") or body.get("doc_type") or "prd").strip().lower() or "prd"
        filename = Path(str(body.get("filename") or body.get("name") or f"{doc_type}.md")).name or f"{doc_type}.md"
        content_b64 = str(body.get("content") or body.get("data") or "")
        if not content_b64:
            return self._json({"ok": False, "error": "MISSING_CONTENT", "message": "Missing base64 encoded file content."}, 400)

        # Decode once and persist the original bytes so PDF/DOCX uploads are not
        # corrupted by an unnecessary UTF-8 text round-trip.
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception:
            return self._json({"ok": False, "error": "DECODE_FAILED", "message": "Base64 解码失败，请检查文件内容。"}, 400)

        # Save to project input dir
        input_dir = root / "platform_workspace" / project / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        out_path = input_dir / filename
        out_path.write_bytes(raw)

        # Run document intelligence pipeline
        from .document_change_watcher import ingest_document as _ingest
        doc_info = _ingest(str(out_path))
        if not isinstance(doc_info, dict) or doc_info.get("ok") is not True:
            message = str(doc_info.get("error") or "document intelligence returned an invalid result") if isinstance(doc_info, dict) else "document intelligence result must be an object"
            out_path.unlink(missing_ok=True)
            return self._json(
                {"ok": False, "error": "DOCUMENT_INGEST_FAILED", "message": message},
                500,
            )

        # Ingest into knowledge center 鈥?must pass document envelope dicts
        source_manifest: dict[str, Any] = {}
        knowledge_updated = False
        source_id = ""
        ingest_status = "pending"
        auto_scan_reason = ""
        ingest_phase = "knowledge_center"
        try:
            ingest_result = ingest_enterprise_knowledge_documents(project, [{"file_path": str(out_path), "filename": filename, "source_type": doc_type}], root=root, actor=actor)
            if not isinstance(ingest_result, dict):
                raise TypeError("knowledge ingest result must be an object")
            if "ok" not in ingest_result:
                raise ValueError("knowledge ingest result missing ok=true")
            if ingest_result.get("ok") is not True and ingest_result.get("ok") is not False:
                raise ValueError("knowledge ingest result ok must be a boolean")
            if ingest_result.get("ok") is False:
                out_path.unlink(missing_ok=True)
                errors = ingest_result.get("errors") if isinstance(ingest_result.get("errors"), list) else []
                first_error = errors[0].get("error") if errors and isinstance(errors[0], dict) else "unknown"
                message = "资料导入失败：" + str(first_error)
                return self._json({"ok": False, "error": "INGEST_FAILED", "message": message}, 500)
            knowledge_updated = True
            created = ingest_result.get("created", [])
            duplicates = ingest_result.get("duplicates", [])
            if not isinstance(created, list):
                raise ValueError("knowledge ingest result created must be a list")
            if not isinstance(duplicates, list):
                raise ValueError("knowledge ingest result duplicates must be a list")
            source_id = ""
            ingest_status = "created"
            if created and isinstance(created[0], dict):
                source_id = str(created[0].get("source_id") or "")
            elif duplicates and isinstance(duplicates[0], dict):
                source_id = str(duplicates[0].get("source_id") or "")
                ingest_status = "duplicate"
            ingest_phase = "source_registry"
            from .enterprise_source_registry import register_source_asset, resolve_source_manifest

            source_asset_id = source_id or f"{doc_type}:{filename}"
            text_content = raw.decode("utf-8", errors="replace")
            source_manifest = resolve_source_manifest(project, text_content, root=root)
            if not source_manifest:
                source_manifest = register_source_asset(
                    project,
                    source_asset_id,
                    text_content,
                    source_type=doc_type,
                    root=root,
                    actor=actor,
                    origin="knowledge_ingest",
                    filename=filename,
                    metadata={
                        "knowledge_source_id": source_id,
                        "storage_mode": "verbatim_bytes",
                        "input_path": str(out_path.relative_to(root)) if str(out_path).startswith(str(root)) else str(out_path),
                    },
                )
            if not isinstance(source_manifest, dict):
                raise TypeError("source manifest must be an object")
            manifest_source_id = str(source_manifest.get("source_id") or "").strip()
            manifest_source_hash = str(source_manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
            if not manifest_source_id or re.fullmatch(r"[0-9a-f]{64}", manifest_source_hash) is None:
                raise ValueError("source manifest missing valid source_id/source_hash")
            # Clear caches so dashboard picks up new data
            ingest_phase = "cache_invalidation"
            knowledge_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
            if knowledge_cache.exists():
                knowledge_cache.unlink()
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists():
                dash_html.unlink()

            # ── Validate before auto-scan ──
            # Trigger auto-scan for ALL meaningful project documents
            # (PRD, DB schema, business rules, configs etc. all contain bug-relevant info)
            ingest_phase = "auto_scan_validation"
            auto_scan_types = {"openapi", "prd", "markdown_api", "db_design", "business_rules",
                              "test_data", "config", "deploy", "ui_design", "collaboration_document",
                              "mobile_android", "mobile_ios"}
            auto_scan_reason = ""
            should_auto_scan = doc_type in auto_scan_types
            if should_auto_scan and doc_type in ("openapi", "markdown_api"):
                # Parse the uploaded file to verify it has real API endpoints
                try:
                    from .universal_api_parser import parse_to_openapi
                    parsed = parse_to_openapi(str(out_path))
                    paths = parsed.get("paths", {})
                    if not paths:
                        should_auto_scan = False
                        auto_scan_reason = "文件未检测到有效的API端点定义，跳过自动检测。请确认文件格式正确。"
                    # Even a single endpoint is valid — always trigger if parseable
                except Exception as exc:
                    should_auto_scan = False
                    auto_scan_reason = f"文件解析失败，跳过自动检测：{exc}"

            if should_auto_scan and doc_type == "prd":
                # PRD changes of any size can introduce bugs (e.g., a single
                # logic rule change can break payment calculation). Always trigger.
                auto_scan_reason = "PRD 已更新，自动触发检测。"

            # ── Auto-trigger scan (validated) ──
            if should_auto_scan:
                ingest_phase = "auto_scan_schedule"
                import threading as _threading
                _threading.Thread(
                    target=_run_ingest_auto_scan,
                    kwargs={
                        "root": root,
                        "project": project,
                        "body": dict(body),
                        "raw": raw,
                        "doc_type": doc_type,
                        "source_manifest": dict(source_manifest),
                    },
                    daemon=True,
                ).start()
                ingest_status = f"{ingest_status}_auto_scanning"
            elif auto_scan_reason:
                ingest_status = f"{ingest_status}_scan_skipped"
        except Exception as exc:
            _write_json_object_atomic(
                root / "platform_outputs" / project / "knowledge_ingest_last_error.json",
                {
                    "schema": "qualibug.knowledge-ingest-failure.v1",
                    "project": project,
                    "filename": filename,
                    "doc_type": doc_type,
                    "phase": ingest_phase,
                    "knowledge_updated": knowledge_updated,
                    "source_id": source_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            raise

        return self._json({
            "ok": True,
            "source_id": source_id,
            "ingest_status": ingest_status,
            "auto_scan": "triggered" if "auto_scanning" in ingest_status else ("skipped" if "scan_skipped" in ingest_status else "not_applicable"),
            "auto_scan_reason": auto_scan_reason or "",
            "filename": filename,
            "doc_type": doc_type,
            "size_bytes": len(raw),
            "path": str(out_path),
            "storage_mode": "verbatim_bytes",
            "source_manifest": source_manifest,
            "supported_source_types": list(KNOWLEDGE_INGEST_SOURCE_TYPES),
            "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
            "doc_info": doc_info,
            "knowledge_updated": knowledge_updated,
            "message": f"'{filename}' imported." if knowledge_updated else "File saved but knowledge index was not updated.",
        })

    def _handle_delete(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Delete a knowledge source by source_id."""
        from .enterprise_knowledge_center import delete_enterprise_knowledge_source
        source_id = str(body.get("source_id") or "").strip()
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID", "message": "Missing source_id."}, 400)
        try:
            result = delete_enterprise_knowledge_source(project, source_id, root, actor)
        except KeyError:
            return self._json({"ok": False, "error": "NOT_FOUND", "message": f"Source {source_id} was not found or already deleted."}, 404)
        try:
            asset_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
            if asset_cache.exists(): asset_cache.unlink()
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists(): dash_html.unlink()
        except Exception: pass
        filename = str(result.get("original_name") or source_id)
        return self._json({
            "ok": True,
            "source_id": source_id,
            "filename": filename,
            "removed_paths": result.get("removed_paths") or [],
            "message": f"'{filename}' permanently deleted.",
        })

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

    def _handle_scan_preflight(self, project: str, root: Path, body: dict[str, Any] | None = None) -> None:
        """Customer-facing readiness check: surface actionable blockers BEFORE a scan
        is launched, instead of failing late with a 400/500 the UI can't explain."""
        body = dict(body or {})
        reasons: list[dict[str, str]] = []
        # 1) service credentials configured?
        _cfg = root / "platform_workspace" / project / "multi_service_config.json"
        _services: list = []
        if _cfg.exists():
            try:
                _services = json.loads(_cfg.read_text(encoding="utf-8")).get("services", [])
            except Exception:
                pass
        if not _services:
            reasons.append({"code": "NO_CREDENTIALS", "message": "尚未配置任何服务凭证，请先在「设置」页保存。"})
        # 2) source ingested — with type-awareness so the UI can tell the
        #    customer WHY the scan might still fail even with sources present.
        _assets: list[dict[str, Any]] = []
        try:
            from .enterprise_source_registry import list_source_assets
            _assets = list_source_assets(project, root=root)
        except Exception:
            _assets = []
        if not _assets:
            reasons.append({"code": "NO_SOURCE", "message": "尚未入库任何资料（PRD / OpenAPI 等），请先上传。"})
        else:
            # Classify source types so the Run Center can surface actionable hints:
            #   - has_api_spec: the scan CAN produce executable probes
            #   - has_prd_only: the scan will run but may produce zero API probes
            _source_types = {str(a.get("source_type") or "").strip().lower() for a in _assets}
            _has_openapi = bool(_source_types & {"openapi", "openapi3", "swagger", "postman", "api_spec"})
            _has_db = bool(_source_types & {"db_design", "database_schema", "sql", "db_schema"})
            _has_prd = bool(_source_types & {"prd", "requirement", "business_rules", "collaboration_document", "other_document"})
            if not _has_openapi:
                reasons.append({
                    "code": "NO_API_SPEC",
                    "message": (
                        "已入库 {} 份资料，但缺少 API 接口规范（OpenAPI / Swagger / Postman）。"
                        "扫描将无法生成可执行的 API 探针，只能产出基于 PRD 的候选线索。"
                        "请上传被测系统的接口文档后再运行。"
                    ).format(len(_assets)),
                })
            # 3) target base_url / connector endpoint?
        _approved_url = str(body.get("approved_base_url") or "").strip()
        _base_url = str(body.get("target_url") or body.get("base_url") or _approved_url or "").strip()
        _environment_type = str(body.get("environment_type") or body.get("environment_kind") or "").strip()
        _environment_ref = str(body.get("environment_ref") or body.get("target_id") or "").strip()
        _project_config = root / "platform_inputs" / project / "real_project_config.json"
        if _project_config.is_file():
            try:
                _project_values = json.loads(_project_config.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                reasons.append({"code": "PROJECT_CONFIG_INVALID", "message": f"项目运行配置无法解析: {exc}"})
                _project_values = {}
            if isinstance(_project_values, dict):
                _base_url = _base_url or str(_project_values.get("base_url") or "").strip()
                _approved_url = _approved_url or str(_project_values.get("approved_base_url") or "").strip()
                if not _approved_url:
                    _approved_urls = _project_values.get("approved_base_urls")
                    if isinstance(_approved_urls, list) and len(_approved_urls) == 1:
                        _approved_url = str(_approved_urls[0] or "").strip()
                _environment_type = _environment_type or str(_project_values.get("environment_type") or _project_values.get("environment_kind") or "").strip()
                _environment_ref = _environment_ref or str(_project_values.get("environment_ref") or _project_values.get("target_id") or "").strip()
        try:
            from .enterprise_pilot_runtime import load_connector_registry
            for _c in load_connector_registry(project, root).get("connectors", []):
                if _c.get("enabled"):
                    _ep = str(_c.get("endpoint_ref") or "")
                    if not _base_url and _ep.startswith(("http://", "https://")):
                        _base_url = _ep
                        break
        except Exception:
            pass
        if not _base_url:
            reasons.append({"code": "NO_TARGET", "message": "未配置被测目标 base_url 或启用连接器端点。"})

        from .target_policy import build_target_policy_decision

        _read_only = bool(body.get("read_only"))
        _target_policy = build_target_policy_decision(
            requested_base_url=_base_url,
            approved_base_url=_approved_url,
            environment_type=_environment_type,
            environment_ref=_environment_ref,
            execution_mode="safe_read_only" if _read_only else "approved_sandbox_write",
            runtime_status="approved",
        )
        _policy_blocking_codes = list(_target_policy.get("blocking_codes") or [])
        if _read_only:
            _policy_blocking_codes = [
                code for code in _policy_blocking_codes
                if code not in {"READ_ONLY_MODE", "UNKNOWN_ENVIRONMENT", "PRODUCTION_WRITE_BLOCKED"}
            ]
        for _code in _policy_blocking_codes:
            reasons.append({
                "code": str(_code),
                "message": "补全明确环境类型、环境标识和精确批准 URL 后重试；生产或未知环境写入不会放行。",
            })

        # Integrate with CapabilityGapResolver for gap detection and resolution tasks
        gap_summary = None
        if reasons:
            try:
                from .capability_gap_resolver import CapabilityGapResolver
                resolver = CapabilityGapResolver(project_id=project)
                detected_gaps = resolver.detect_from_scan_preflight(reasons)
                gap_summary = resolver.build_gap_report(detected_gaps)
            except Exception:
                pass

        _blocking_codes = list(dict.fromkeys(str(item.get("code") or "") for item in reasons if item.get("code")))
        response = {
            "ok": True,
            "schema_version": "qualibug.environment-preflight.v1",
            "project_id": project,
            "ready": len(_blocking_codes) == 0,
            "blocking_codes": _blocking_codes,
            "reasons": reasons,
            "target_policy_decision": _target_policy,
            "input_checks": {
                "credentials": {"status": "passed" if _services else "blocked", "service_count": len(_services)},
                "sources": {"status": "passed" if _assets else "blocked", "source_count": len(_assets)},
                "target": {"status": "passed" if _base_url else "blocked", "target_url": _base_url, "approved_base_url": _approved_url},
                "environment": {"status": "passed" if _environment_type and _environment_ref else "blocked", "environment_type": _environment_type, "environment_ref": _environment_ref},
                "target_policy": {"status": "passed" if (_target_policy.get("read_allowed") if _read_only else _target_policy.get("write_allowed")) else "blocked"},
            },
        }
        if gap_summary:
            response["gap_summary"] = gap_summary
        return self._json(response)

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

    def _handle_v12_scan(self, project: str, root: Path, actor: dict[str, str], body: dict[str, Any]) -> None:
        try:
            from .__main__ import scan
            from .private_pilot_scan_context_contract import build_campaign_context_from_scan_body
            trace_id = str(uuid.uuid4())

            # B3 (customer one-click run): when the scan body does not already carry a
            # usable source_manifest, auto-bind the project's most recently ingested
            # source so the scan has real scope instead of running source-less. Never
            # fabricate a manifest — only bind an existing registered source.
            _manifest = body.get("source_manifest") if isinstance(body.get("source_manifest"), dict) else {}
            _manifest_ok = bool(str(_manifest.get("source_id") or "").strip()) and len(str(_manifest.get("source_hash") or "").strip()) == 64
            if not _manifest_ok:
                try:
                    from .enterprise_source_registry import list_source_assets
                    for _asset in list_source_assets(project, root=root):
                        _sid = str(_asset.get("source_id") or "").strip()
                        _sh = str(_asset.get("latest_source_hash") or "").strip().lower()
                        if _sid and len(_sh) == 64:
                            body = dict(body)
                            body["source_manifest"] = {"source_id": _sid, "source_hash": _sh}
                            break
                except Exception as _bind_exc:
                    _dbg_report(hypothesis_id="B3", msg="[WARN] auto source bind skipped",
                                data={"error": str(_bind_exc)}, trace_id=trace_id)

            prepared_body = _prepare_v12_scan_body(
                project,
                root,
                actor,
                body,
                local_dev_mode=_is_local_private_service(self.server),
            )
            api_doc = str(prepared_body.get("api_doc") or prepared_body.get("api_doc_text") or "")
            base_url = str(prepared_body.get("base_url") or "").strip()
            # SSRF guard: validate user-supplied base_url before it reaches scan().
            if base_url:
                from .ssrf_guard import SsrfBlockedError
                try:
                    _validate_scan_base_url(base_url, local_dev_mode=_is_local_private_service(self.server))
                except SsrfBlockedError as exc:
                    return self._json({"ok": False, "error": "SSRF_BLOCKED", "message": str(exc)}, 400)
            if not api_doc:
                p = root / "platform_outputs" / project / "api_spec.md"
                if p.exists(): api_doc = p.read_text(encoding="utf-8")
            # Auto-build API doc + base_url from connectors
            if not api_doc or not base_url:
                from .enterprise_pilot_runtime import load_connector_registry
                reg = load_connector_registry(project, root)
                connectors = reg.get("connectors", [])
                enabled = [c for c in connectors if c.get("enabled")]
                if enabled:
                    if not base_url:
                        for c in enabled:
                            ep = c.get("endpoint_ref", "")
                            if ep and (ep.startswith("http://") or ep.startswith("https://")):
                                base_url = ep; break
                # A connector URL is target identity only. Endpoint contracts must
                # come from registered project sources; never synthesize routes.
            if api_doc and not prepared_body.get("api_doc"):
                prepared_body["api_doc"] = api_doc
            if base_url and not prepared_body.get("base_url"):
                prepared_body["base_url"] = base_url
            if not str(body.get("execution_mode") or "").strip():
                prepared_body.pop("execution_mode", None)
            prepared_body = _prepare_v12_scan_body(
                project,
                root,
                actor,
                prepared_body,
                local_dev_mode=_is_local_private_service(self.server),
            )
            campaign_context = build_campaign_context_from_scan_body(prepared_body)
            # #region debug-point A:prepared-http-scan-body
            _dbg_report(
                hypothesis_id="A",
                msg="[DEBUG] prepared http scan payload",
                data={
                    "actor": {"name": str(actor.get("name") or ""), "role": str(actor.get("role") or "")},
                    "local_dev_mode": _is_local_private_service(self.server),
                    "prepared_body": _dbg_fingerprint_payload(prepared_body),
                    "campaign_context": _dbg_fingerprint_payload(campaign_context),
                },
                trace_id=trace_id,
            )
            # #endregion

            result = scan(
                project=project,
                root=root,
                prd_text=str(prepared_body.get("prd", "")),
                api_doc_text=str(prepared_body.get("api_doc") or prepared_body.get("api_doc_text") or api_doc),
                base_url=str(prepared_body.get("base_url") or base_url).strip(),
                multi_layer=bool(str(prepared_body.get("base_url") or base_url).strip()),
                campaign_context=campaign_context,
            )
            # #region debug-point C:http-scan-result
            _dbg_report(
                hypothesis_id="C",
                msg="[DEBUG] http scan result campaign",
                data={
                    "campaign": result.get("campaign") if isinstance(result.get("campaign"), dict) else {},
                    "runtime_contract": result.get("runtime_contract") if isinstance(result.get("runtime_contract"), dict) else {},
                    "incremental_discovery": result.get("phases", {}).get("incremental_discovery") if isinstance(result.get("phases"), dict) else {},
                },
                trace_id=trace_id,
            )
            # #endregion
            retry_approval_id = _issue_runtime_approval_for_result(
                project,
                root,
                actor,
                prepared_body,
                result,
                local_dev_mode=_is_local_private_service(self.server),
            )
            if retry_approval_id and _has_campaign_id_mismatch(result):
                prepared_body["execution_approval_id"] = retry_approval_id
                campaign_context = build_campaign_context_from_scan_body(prepared_body)
                # #region debug-point D:retry-http-scan-body
                _dbg_report(
                    hypothesis_id="D",
                    msg="[DEBUG] retry http scan payload",
                    data={
                        "prepared_body": _dbg_fingerprint_payload(prepared_body),
                        "campaign_context": _dbg_fingerprint_payload(campaign_context),
                        "retry_approval_id": retry_approval_id,
                    },
                    trace_id=trace_id,
                )
                # #endregion
                result = scan(
                    project=project,
                    root=root,
                    prd_text=str(prepared_body.get("prd", "")),
                    api_doc_text=str(prepared_body.get("api_doc") or prepared_body.get("api_doc_text") or api_doc),
                    base_url=str(prepared_body.get("base_url") or base_url).strip(),
                    multi_layer=bool(str(prepared_body.get("base_url") or base_url).strip()),
                    campaign_context=campaign_context,
                )
                # #region debug-point E:retry-http-scan-result
                _dbg_report(
                    hypothesis_id="E",
                    msg="[DEBUG] retry http scan result campaign",
                    data={
                        "campaign": result.get("campaign") if isinstance(result.get("campaign"), dict) else {},
                        "runtime_contract": result.get("runtime_contract") if isinstance(result.get("runtime_contract"), dict) else {},
                    },
                    trace_id=trace_id,
                )
                # #endregion
            # Persist to DB — use cumulative merge so bugs accumulate across scans
            try:
                db_persist.init_db(root)
                # Extract findings from report
                report_path = root / "platform_outputs" / project / "intelligence_report.json"
                report_data = {}
                if report_path.exists():
                    import json as _jr
                    report_data = _jr.loads(report_path.read_text(encoding="utf-8"))
                findings_list = report_data.get("real_findings") or report_data.get("bug_scores") or []
                # Also include multi-source findings from scan result
                if isinstance(result.get("db_findings"), list):
                    findings_list = list(findings_list) + result["db_findings"]
                if isinstance(result.get("e2e_findings"), list):
                    findings_list = list(findings_list) + result["e2e_findings"]
                if isinstance(result.get("deep_findings"), list):
                    findings_list = list(findings_list) + result["deep_findings"]
                if isinstance(result.get("ui_findings"), list):
                    findings_list = list(findings_list) + result["ui_findings"]
                # Dedupe input list before merging
                seen_titles: set[str] = set()
                deduped_findings: list[dict] = []
                for f in (findings_list if isinstance(findings_list, list) else []):
                    if not isinstance(f, dict):
                        continue
                    t = str(f.get("title") or f.get("description", ""))[:160].lower()
                    if t in seen_titles:
                        continue
                    seen_titles.add(t)
                    deduped_findings.append(f)
                # Save scan record
                enriched = dict(result)
                enriched["findings"] = [
                    {"title": str(f.get("title") or f.get("description", ""))[:120],
                     "severity": str(f.get("severity", "P1")),
                     "category": str(f.get("category", "")),
                     "description": str(f.get("description", ""))[:500],
                     "confidence_score": float(f.get("confidence_score") or f.get("score") or 0),
                     "_api_path": str(f.get("_api_path") or f.get("path") or ""),
                     "_api_method": str(f.get("_api_method") or f.get("method") or ""),
                     "evidence": f.get("evidence") if isinstance(f.get("evidence"), dict) else {}}
                    for f in deduped_findings
                ]
                scan_id = db_persist.save_scan(root, self._request_tenant(), project, enriched)
                # Cumulative merge — bugs accumulate, never silently dropped
                merge_result = db_persist.merge_findings_cumulative(
                    root, self._request_tenant(), project, scan_id, enriched["findings"]
                )
            except Exception:
                merge_result = {}
            # Increment scan counter through the shared helper so CLI and HTTP
            # entrypoints project the same run metadata into command center.
            try:
                increment_scan_counter(root / "platform_outputs" / project / "scan_counter.json")
            except Exception:
                pass
            # Also write raw findings for frontend Dashboard/Findings compatibility
            try:
                # Re-read from evaluation result
                out_dir = root / "platform_outputs" / project
                eval_json = out_dir / "intelligence_report.json"
                if eval_json.exists():
                    import json as _j2
                    existing = _j2.loads(eval_json.read_text(encoding="utf-8"))
                    # Merge raw findings — only set raw_total once (first scan), never decrement
                    old_raw = existing.get("raw_total", 0)
                    new_raw = result.get("total_findings", 0)
                    existing["raw_total"] = max(old_raw, new_raw) if old_raw else new_raw
                    # Preserve real_findings across scans
                    if not existing.get("real_findings"):
                        existing["real_findings"] = existing.get("bug_scores", [])
                    existing["layers"] = result.get("layers", {})
                    # Merge DB verification findings
                    db_finds = result.get("db_findings")
                    if isinstance(db_finds, list) and db_finds:
                        existing["db_verification"] = {"findings": db_finds, "total": len(db_finds)}
                    e2e_finds = result.get("e2e_findings")
                    if isinstance(e2e_finds, list) and e2e_finds:
                        existing.setdefault("e2e_findings", []).extend(e2e_finds)
                    deep_finds = result.get("deep_findings")
                    if isinstance(deep_finds, list) and deep_finds:
                        existing.setdefault("deep_findings", []).extend(deep_finds)
                    ui_finds = result.get("ui_findings")
                    if isinstance(ui_finds, list) and ui_finds:
                        existing.setdefault("ui_findings", []).extend(ui_finds)
                    eval_json.write_text(_j2.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            # Save spectrum result to disk for Dashboard polling
            if result.get("spectrum"):
                spectrum_dir = root / "platform_outputs" / project / "spectrum"
                spectrum_dir.mkdir(parents=True, exist_ok=True)
                import json as _json_save
                _json_save.dump(result["spectrum"], (spectrum_dir / "spectrum_result.json").open("w", encoding="utf-8"),
                               ensure_ascii=False, default=str)

            # Update continuous state for manual scans too
            _update_continuous_state(root, project, result)

            return self._json({"ok": True, "scan_id": result.get("scan_id",""), "grade": result.get("grade",""),
                "score": result.get("score",0), "coverage": result.get("coverage",0),
                "total_findings": result.get("total_findings",0), "total_ms": result.get("total_ms",0),
                "layers": result.get("layers",{}),
                "spectrum": result.get("spectrum", {}),
                "auto_har": result.get("auto_har", {}),
                # Honest run-status fields the Run Center / Dashboard need to
                # distinguish executed / blocked / plan_only / partial_coverage
                # instead of misleading the customer. scan() already computes these;
                # they were previously dropped from the HTTP envelope so the frontend
                # always saw execution_status == undefined.
                "execution_status": result.get("execution_status", ""),
                "campaign": result.get("campaign", {}),
                "coverage_gaps": result.get("coverage_gaps", []),
                "runtime_contract": result.get("runtime_contract", {}),
                "test_data_plan": result.get("test_data_plan", {}),
                "release_gate": result.get("release_gate", {}),
                "execution_evidence_summary": result.get("execution_evidence_summary", {}),
                "report_path": result.get("report_path", ""),
                "benchmark_metrics": result.get("benchmark_metrics", {}),
                "cumulative": merge_result,})
        except Exception as e:
            return self._json({"ok": False, "error": "V12_SCAN_FAILED", "message": str(e)[:500]}, 500)

    def _handle_regression_run(self, project: str, root: Path, body: dict[str, Any]) -> None:
        if not self._require_known_project(project, root):
            return
        mode = str(body.get("mode") or "release").strip().lower() or "release"
        if mode not in {"smoke", "release", "full"}:
            return self._json(
                {
                    "ok": False,
                    "error": "BAD_REGRESSION_MODE",
                    "message": "回归模式仅支持 smoke、release 或 full。",
                },
                400,
            )
        allow_destructive = bool(body.get("allow_destructive_execution") is True)
        dry_run = bool(body.get("dry_run") is True)
        try:
            from .regression_runner import run_regression_suite

            result = run_regression_suite(
                project_id=project,
                root=root,
                options={
                    "mode": mode,
                    "allow_destructive_execution": allow_destructive,
                    "dry_run": dry_run,
                },
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "REGRESSION_RUN_FAILED",
                    "message": str(exc)[:400],
                },
                500,
            )

        regression_summary: dict[str, Any] = {}
        try:
            command_center = self._build_command_center(project, root)
            if isinstance(command_center, dict):
                regression_summary = command_center.get("data", {}).get("regression_summary", {})
        except Exception:
            regression_summary = {}

        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        ci_feedback = result.get("ci_feedback") if isinstance(result.get("ci_feedback"), dict) else {}
        failures = result.get("failures") if isinstance(result.get("failures"), list) else []
        return self._json(
            {
                "ok": True,
                "project_id": project,
                "mode": mode,
                "summary": summary,
                "ci_feedback": ci_feedback,
                "regression_summary": regression_summary,
                "failures": failures[:20],
                "artifacts": {
                    "regression_suite_ref": str(result.get("regression_suite_ref") or ""),
                    "run_result_ref": f"platform_outputs/{project}/regression_run/regression_run_result.json",
                    "run_report_ref": f"platform_outputs/{project}/regression_run/regression_failure_report.html",
                },
                "governance": {
                    "dry_run": dry_run,
                    "allow_destructive_execution": allow_destructive,
                    "safe_by_default": not allow_destructive,
                },
            }
        )

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any]:
        return _read_json_object(path)

    @staticmethod
    def _mtime_utc(path: Path) -> str:
        try:
            return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(path.stat().st_mtime))
        except Exception:
            return ""

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _normalize_severity(value: Any) -> str:
        text = str(value or "").strip().upper()
        return {"CRITICAL": "P0", "HIGH": "P1", "MEDIUM": "P2", "LOW": "P2"}.get(text, text if text in {"P0", "P1", "P2", "P3"} else "P1")

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _report_signal_count(payload: dict[str, Any]) -> int:
        def _count_list(value: Any) -> int:
            return len(value) if isinstance(value, list) else 0

        direct_counts = [
            payload.get("risk_total"),
            payload.get("risk_count"),
            payload.get("total_findings"),
            payload.get("total_bugs_found"),
            payload.get("total_found"),
            payload.get("raw_total"),
            (payload.get("executive_summary") or {}).get("total_findings") if isinstance(payload.get("executive_summary"), dict) else None,
            (payload.get("summary") or {}).get("total_findings") if isinstance(payload.get("summary"), dict) else None,
        ]
        materialized = max(
            _count_list(payload.get("real_findings")),
            _count_list(payload.get("findings")),
            _count_list(payload.get("bug_scores")),
            _count_list(payload.get("e2e_findings")),
            _count_list(payload.get("deep_findings")),
            _count_list(payload.get("ui_findings")),
            _count_list((payload.get("db_verification") or {}).get("findings") if isinstance(payload.get("db_verification"), dict) else None),
        )
        parsed_counts: list[int] = [materialized]
        for value in direct_counts:
            try:
                parsed_counts.append(int(float(value)))
            except Exception:
                continue
        return max(parsed_counts or [0])

    @staticmethod
    def _report_summary_number(report: dict[str, Any], *keys: str, fallback: int = 0) -> int:
        scopes: list[Any] = [report]
        for nested in ("executive_summary", "summary", "value_metrics", "scan_meta"):
            value = report.get(nested)
            if isinstance(value, dict):
                scopes.append(value)
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            for key in keys:
                if key not in scope:
                    continue
                try:
                    return int(float(scope.get(key)))
                except Exception:
                    continue
        return fallback

    @staticmethod
    def _discovery_current_scope_summary(payload: dict[str, Any]) -> dict[str, Any]:
        campaign_projection = payload.get("continuous_discovery_campaign") if isinstance(payload.get("continuous_discovery_campaign"), dict) else {}
        summary = campaign_projection.get("summary") if isinstance(campaign_projection.get("summary"), dict) else {}
        current_run = campaign_projection.get("current_run") if isinstance(campaign_projection.get("current_run"), dict) else {}
        campaign = campaign_projection.get("campaign") if isinstance(campaign_projection.get("campaign"), dict) else {}
        scopes = [current_run, summary, campaign_projection, campaign]

        def _pick_int(*keys: str) -> int | None:
            for scope in scopes:
                if not isinstance(scope, dict):
                    continue
                for key in keys:
                    if key not in scope:
                        continue
                    try:
                        return int(float(scope.get(key)))
                    except Exception:
                        continue
            return None

        total_findings = _pick_int(
            "current_campaign_bundle_finding_count_raw",
            "current_report_total_findings",
            "total_findings",
        )
        customer_ready_defects = _pick_int(
            "current_campaign_customer_ready_defect_count",
            "current_report_customer_ready_defect_count",
            "customer_ready_defects",
            "ready_bug_count",
        )
        confirmed_slice_count = _pick_int(
            "current_campaign_confirmed_slice_count",
            "confirmed_slice_count",
        )
        if total_findings is None and customer_ready_defects is None and confirmed_slice_count is None:
            return {}
        return {
            "total_findings": max(0, int(total_findings or 0)),
            "customer_ready_defects": max(0, int(customer_ready_defects or 0)),
            "confirmed_slice_count": max(0, int(confirmed_slice_count or 0)),
            "report_source_path": str(payload.get("report_source_path") or ""),
        }

    def _load_v12_report(self, project_id: str, root: Path) -> dict[str, Any]:
        project = _safe_project_id(project_id)
        explicit_candidates = [
            root / "platform_outputs" / project / "intelligence_report.json",
            root / "platform_outputs" / project / "v12_report.json",
            root / "platform_outputs" / project / "scan_result.json",
            root / "platform_workspace" / project / "intelligence_report.json",
            root / "platform_workspace" / project / "v12_report.json",
            root / "platform_workspace" / project / "scan_result.json",
            root / "benchmark_outputs" / project / "intelligence_report.json",
        ]

        # Do not return the first/newest JSON blindly. Real backend runs can write
        # summary numbers to one report while evidence files are written under
        # platform_workspace. Pick the strongest source-of-truth by materialized
        # finding signal, then by mtime. This prevents the React page from reading
        # an older/empty scan_result while the backend report shows newer totals.
        candidate_payloads: list[tuple[int, float, dict[str, Any]]] = []
        for path in explicit_candidates:
            if not path.exists():
                continue
            payload = self._read_json_dict(path)
            if not payload:
                continue
            payload.setdefault("report_source_path", path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path))
            candidate_payloads.append((self._report_signal_count(payload), path.stat().st_mtime, payload))

        workspace_report = self._load_workspace_report(project, root)
        if workspace_report:
            workspace_path = root / "platform_workspace" / project
            candidate_payloads.append((
                self._report_signal_count(workspace_report),
                workspace_path.stat().st_mtime if workspace_path.exists() else time.time(),
                workspace_report,
            ))

        # When the latest scan_result only contains the current scan delta, a
        # completed campaign can legitimately produce 0 new findings while
        # historical confirmed defects still exist in a related evidence bundle.
        # Recover the strongest same-snapshot/lineage evidence bundle so the
        # command center reflects real customer-visible defects instead of a
        # misleading empty list.
        anchor_candidate = max(candidate_payloads, key=lambda item: (item[1], item[0])) if candidate_payloads else None
        anchor_report = anchor_candidate[2] if anchor_candidate else {}
        related_bundle_candidates = self._load_evidence_bundle_report_candidates(project, root, anchor_report)
        candidate_payloads.extend(related_bundle_candidates)
        aggregate_candidate = self._aggregate_related_report_candidate(anchor_candidate, related_bundle_candidates)
        if aggregate_candidate is not None:
            candidate_payloads.append(aggregate_candidate)

        if candidate_payloads:
            candidate_payloads.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidate_payloads[0][2]

        # Last-resort benchmark aggregate: useful when a benchmark run was written
        # outside platform_outputs but the frontend still asks for that project.
        batch_path = root / "benchmark_outputs" / "batch_report.json"
        batch = self._read_json_dict(batch_path)
        results = batch.get("results")
        if isinstance(results, dict):
            matched = results.get(project) or results.get(project_id)
            if isinstance(matched, dict):
                return {
                    "project_id": project,
                    "project_name": project_id,
                    "generated_at_utc": self._mtime_utc(batch_path),
                    "system_grade": str(matched.get("grade") or matched.get("system_grade") or ""),
                    "overall_score": self._coerce_float(matched.get("score") or matched.get("overall_score"), 0),
                    "total_findings": int(matched.get("total_found") or matched.get("total_findings") or 0),
                    "real_findings": [],
                    "summary": "benchmark aggregate only",
                    "report_source_path": "benchmark_outputs/batch_report.json",
                }
        return {}

    def _load_current_scan_report(self, project_id: str, root: Path) -> dict[str, Any]:
        project = _safe_project_id(project_id)
        candidates = [
            root / "platform_outputs" / project / "intelligence_report.json",
            root / "platform_outputs" / project / "v12_report.json",
            root / "platform_outputs" / project / "scan_result.json",
            root / "platform_workspace" / project / "intelligence_report.json",
            root / "platform_workspace" / project / "v12_report.json",
            root / "platform_workspace" / project / "scan_result.json",
        ]
        chosen: tuple[float, int, dict[str, Any]] | None = None
        for path in candidates:
            if not path.exists():
                continue
            payload = self._read_json_dict(path)
            if not payload:
                continue
            payload.setdefault("report_source_path", path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path))
            score = path.stat().st_mtime
            signal_count = self._report_signal_count(payload)
            if chosen is None or (score, signal_count) > (chosen[0], chosen[1]):
                chosen = (score, signal_count, payload)
        return chosen[2] if chosen else {}

    def _load_evidence_bundle_report_candidates(
        self,
        project_id: str,
        root: Path,
        anchor_report: dict[str, Any] | None = None,
    ) -> list[tuple[int, float, dict[str, Any]]]:
        project = _safe_project_id(project_id)
        bundle_root = root / "platform_workspace" / project / "evidence_bundles"
        if not bundle_root.exists():
            return []
        anchor = anchor_report if isinstance(anchor_report, dict) else {}
        anchor_campaign = anchor.get("campaign") if isinstance(anchor.get("campaign"), dict) else {}
        anchor_campaign_id = self._first_text(anchor_campaign.get("campaign_id"), anchor.get("campaign_id"))
        anchor_lineage_id = self._first_text(anchor_campaign.get("lineage_campaign_id"))
        anchor_snapshot = self._first_text(anchor_campaign.get("source_snapshot_hash"), (anchor.get("behavior_slice_ledger") or {}).get("source_snapshot_hash") if isinstance(anchor.get("behavior_slice_ledger"), dict) else "")
        anchor_source_hash = self._first_text(anchor_campaign.get("source_hash"), (anchor.get("runtime_contract") or {}).get("source_manifest", {}).get("source_hash") if isinstance((anchor.get("runtime_contract") or {}).get("source_manifest"), dict) else "")
        anchor_scope_id = self._first_text(anchor_campaign.get("scope_id"), anchor.get("scope_id"))
        anchor_environment_ref = self._first_text(anchor_campaign.get("environment_ref"), anchor.get("environment_ref"))
        anchored = bool(anchor_campaign_id or anchor_snapshot or anchor_source_hash)
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        for manifest_path in sorted(bundle_root.glob("evb_*/manifest.json")):
            manifest = self._read_json_dict(manifest_path)
            if not manifest or self._first_text(manifest.get("project_id")) != project:
                continue
            campaign = self._read_json_dict(manifest_path.with_name("campaign.json"))
            findings_path = manifest_path.with_name("findings.json")
            if not findings_path.exists():
                continue
            try:
                raw_findings = json.loads(findings_path.read_text(encoding="utf-8") or "[]")
            except Exception:
                continue
            if not isinstance(raw_findings, list):
                continue
            findings = [item for item in raw_findings if isinstance(item, dict)]
            if not findings:
                continue
            bundle_campaign_id = self._first_text(campaign.get("campaign_id"), manifest.get("campaign_id"))
            bundle_lineage_id = self._first_text(campaign.get("lineage_campaign_id"))
            bundle_snapshot = self._first_text(campaign.get("source_snapshot_hash"))
            bundle_source_hash = self._first_text(campaign.get("source_hash"), (manifest.get("source_manifest") or {}).get("source_hash") if isinstance(manifest.get("source_manifest"), dict) else "")
            bundle_scope_id = self._first_text(campaign.get("scope_id"), manifest.get("scope_id"))
            bundle_environment_ref = self._first_text(campaign.get("environment_ref"), manifest.get("environment_ref"))
            family_ids = {value for value in (anchor_campaign_id, anchor_lineage_id) if value}
            bundle_family_ids = {value for value in (bundle_campaign_id, bundle_lineage_id) if value}
            family_match = bool(family_ids and bundle_family_ids.intersection(family_ids))
            scope_match = not (anchor_scope_id and bundle_scope_id) or anchor_scope_id == bundle_scope_id
            environment_match = not (anchor_environment_ref and bundle_environment_ref) or anchor_environment_ref == bundle_environment_ref
            source_match = bool(
                (anchor_snapshot and bundle_snapshot == anchor_snapshot)
                or (anchor_source_hash and bundle_source_hash == anchor_source_hash)
            )
            if anchored:
                if family_ids:
                    if not (family_match and scope_match and environment_match):
                        continue
                elif not (source_match and scope_match and environment_match):
                    continue
            created_at = self._first_text(manifest.get("created_at_utc"), self._mtime_utc(findings_path))
            payload = {
                "project_id": project,
                "project_name": project_id,
                "generated_at_utc": created_at,
                "system_grade": self._first_text(anchor.get("system_grade"), anchor.get("grade")),
                "overall_score": self._coerce_float(anchor.get("overall_score"), self._coerce_float(anchor.get("score"), 0.0)),
                "total_findings": len(findings),
                "raw_total": len(findings),
                "real_findings": findings,
                "bug_scores": findings,
                "summary": f"从 evidence bundle 回填 {len(findings)} 条历史确认结果。",
                "report_source_path": findings_path.relative_to(root).as_posix() if findings_path.is_relative_to(root) else str(findings_path),
                "campaign": campaign,
                "evidence_bundle": {
                    "bundle_id": self._first_text(manifest.get("bundle_id"), manifest_path.parent.name),
                    "manifest_ref": str(manifest_path.relative_to(root)) if manifest_path.is_relative_to(root) else str(manifest_path),
                    "evidence_level": self._first_text(manifest.get("evidence_level")),
                },
            }
            candidates.append((self._report_signal_count(payload), findings_path.stat().st_mtime, payload))
        return candidates

    @staticmethod
    def _report_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("real_findings", "findings", "bug_scores"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _report_finding_dedupe_key(item: dict[str, Any]) -> str:
        import re as _re

        title = str(item.get("title") or item.get("description") or "")[:200].strip().lower()
        title = _re.sub(r"^(\[[^\]]*\]\s*)+", "", title)
        title = _re.sub(r"\s+", " ", title).strip()
        method = str(
            item.get("_api_method")
            or item.get("method")
            or (item.get("evidence") or {}).get("method")
            or ""
        ).strip().upper()
        path = str(
            item.get("_api_path")
            or item.get("path")
            or (item.get("evidence") or {}).get("path")
            or ""
        ).strip()
        risk_id = str(item.get("risk_id") or item.get("id") or "").strip().lower()
        if title or method or path:
            return f"{title}|{method}|{path}"
        return risk_id

    def _aggregate_related_report_candidate(
        self,
        anchor_candidate: tuple[int, float, dict[str, Any]] | None,
        related_candidates: list[tuple[int, float, dict[str, Any]]],
    ) -> tuple[int, float, dict[str, Any]] | None:
        if anchor_candidate is None:
            return None
        source_candidates = [item for item in [*related_candidates, anchor_candidate] if isinstance(item[2], dict)]
        if len(source_candidates) < 2:
            return None

        strongest_signal = max(item[0] for item in source_candidates)
        merged_findings: dict[str, dict[str, Any]] = {}
        for _signal, _mtime, payload in sorted(source_candidates, key=lambda item: item[1]):
            for finding in self._report_findings(payload):
                key = self._report_finding_dedupe_key(finding)
                if not key:
                    continue
                merged_findings[key] = dict(finding)

        merged_total = len(merged_findings)
        if merged_total <= strongest_signal:
            return None

        latest_signal, latest_mtime, latest_payload = max(source_candidates, key=lambda item: (item[1], item[0]))
        anchor_payload = anchor_candidate[2]
        merged_payload = dict(anchor_payload)
        merged_list = list(merged_findings.values())
        source_refs: list[str] = []
        for _signal, _mtime, payload in sorted(source_candidates, key=lambda item: item[1]):
            ref = str(payload.get("report_source_path") or "").strip()
            if ref and ref not in source_refs:
                source_refs.append(ref)
        merged_payload.update({
            "project_id": self._first_text(anchor_payload.get("project_id"), latest_payload.get("project_id")),
            "project_name": self._first_text(anchor_payload.get("project_name"), latest_payload.get("project_name")),
            "generated_at_utc": self._first_text(latest_payload.get("generated_at_utc"), anchor_payload.get("generated_at_utc")),
            "total_findings": merged_total,
            "raw_total": merged_total,
            "real_findings": merged_list,
            "bug_scores": merged_list,
            "summary": f"聚合当前报告与 {max(0, len(source_refs) - 1)} 个关联 evidence bundle，回填 {merged_total} 条历史确认结果。",
            "report_source_path": f"aggregated:{source_refs[0]}" if source_refs else "aggregated:evidence_bundle_union",
            "report_source_paths": source_refs,
        })
        if not isinstance(merged_payload.get("campaign"), dict) and isinstance(latest_payload.get("campaign"), dict):
            merged_payload["campaign"] = latest_payload.get("campaign")
        if isinstance(latest_payload.get("evidence_bundle"), dict) or source_refs:
            merged_payload["evidence_bundle"] = {
                **(latest_payload.get("evidence_bundle") if isinstance(latest_payload.get("evidence_bundle"), dict) else {}),
                "aggregate": True,
                "source_count": len(source_refs),
            }
        return (merged_total, max(latest_mtime, anchor_candidate[1]), merged_payload)

    def _load_workspace_report(self, project_id: str, root: Path) -> dict[str, Any]:
        workspace = root / "platform_workspace" / project_id
        if not workspace.exists():
            return {}
        findings: list[dict[str, Any]] = []
        sources: list[str] = []
        defect_dir = workspace / "defect_discovery"
        if defect_dir.exists():
            for path in sorted(defect_dir.glob("*_run.json")):
                payload = self._read_json_dict(path)
                if not payload:
                    continue
                for key in ("findings", "counterexample_findings", "readiness_findings", "structure_findings"):
                    raw_items = payload.get(key)
                    if not isinstance(raw_items, list):
                        continue
                    for index, item in enumerate(raw_items):
                        if isinstance(item, dict):
                            normalized = self._normalize_workspace_finding(item, payload, path, index)
                            if normalized:
                                findings.append(normalized)
                                sources.append(path.name)

        # Real HTTP probe execution can produce direct runtime evidence. Keep only
        # suspicious or failed probes so the frontend does not label normal probes as bugs.
        probe_result = workspace / "real_project" / "probe_execution_result.json"
        payload = self._read_json_dict(probe_result)
        items = payload.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                if not item.get("suspicious") and not item.get("error"):
                    continue
                normalized = self._normalize_probe_execution_finding(item, probe_result, index)
                if normalized:
                    findings.append(normalized)
                    sources.append(probe_result.name)

        findings = self._dedupe_risks(findings)
        if not findings:
            return {}
        p0 = sum(1 for item in findings if self._normalize_severity(item.get("severity")) == "P0")
        p1 = sum(1 for item in findings if self._normalize_severity(item.get("severity")) == "P1")
        score = 97.0 if p0 + p1 else 80.0
        grade = "A+" if score >= 95 else "A" if score >= 85 else "B" if score >= 70 else "C"
        latest = max((path.stat().st_mtime for path in defect_dir.glob("*.json") if path.exists()), default=workspace.stat().st_mtime if workspace.exists() else time.time())
        generated = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(latest))
        return {
            "project_id": project_id,
            "project_name": project_id,
            "generated_at_utc": generated,
            "system_grade": grade,
            "overall_score": score,
            "total_findings": len(findings),
            "raw_total": len(findings),
            "real_findings": findings,
            "bug_scores": findings,
            "summary": f"从 platform_workspace 聚合 {len(findings)} 条真实检测结果 / 覆盖缺口。",
            "report_source_path": f"platform_workspace/{project_id}",
            "workspace_sources": sorted(set(sources)),
        }

    def _normalize_workspace_finding(self, item: dict[str, Any], payload: dict[str, Any], source_path: Path, index: int) -> dict[str, Any]:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        method = self._first_text(item.get("method"), evidence.get("method"), (item.get("probe") or {}).get("method") if isinstance(item.get("probe"), dict) else "").upper()
        path = self._first_text(item.get("path"), item.get("path_template"), evidence.get("path"), evidence.get("path_template"), (item.get("probe") or {}).get("path") if isinstance(item.get("probe"), dict) else "")
        title = self._first_text(item.get("title"), item.get("technical_title"), item.get("detail"), item.get("description"), f"{source_path.stem} finding {index + 1}")
        risk_type = self._first_text(item.get("risk_type"), item.get("category"), item.get("business_assurance_type"), source_path.stem)
        status = self._first_text(item.get("status"), item.get("verdict"), "needs_human_review")
        confidence = self._coerce_float(item.get("confidence_score"), self._coerce_float(item.get("confidence"), self._coerce_float(item.get("score"), 0.75)))
        quality_gap = (
            "coverage_gap" in risk_type
            or "assurance" in risk_type
            or status in {"needs_human_review", "candidate", "pending"}
            or bool(item.get("claim_guard"))
        )
        expected = self._first_text(item.get("expected"), item.get("expected_behavior"), evidence.get("expected"), item.get("test_oracle"))
        actual = self._first_text(item.get("actual"), item.get("actual_behavior"), item.get("bug_signal"), item.get("summary"), item.get("detail"), item.get("description"))
        steps = item.get("reproduction_steps") if isinstance(item.get("reproduction_steps"), list) else []
        if not steps:
            steps = [
                f"定位检测来源：{source_path.name}",
                f"回放业务动作：{method or '业务操作'} {path or title}",
                "对比预期规则、真实返回、日志与 DB 快照，确认是否可复现。",
            ]
        return {
            "risk_id": self._first_text(item.get("risk_id"), item.get("finding_id"), item.get("issue_id"), item.get("bug_id"), f"{source_path.stem}_{index}"),
            "title": title,
            "technical_title": f"{method} {path} · {title}" if method or path else title,
            "severity": self._normalize_severity(item.get("severity")),
            "status": "pending" if quality_gap else ("confirmed" if status in {"confirmed", "validated", "reproduced"} else status),
            "risk_type": risk_type,
            "defect_family": self._first_text(item.get("defect_family"), "scenario_flow" if quality_gap else risk_type),
            "summary": actual or title,
            "business_impact": actual or title,
            "suggested_action": expected or "补齐真实请求、响应、日志与 DB 快照后再进入缺陷交付。",
            "expected": expected,
            "actual": actual,
            "confidence_score": confidence,
            "reproducibility_score": confidence if not quality_gap else min(confidence, 0.45),
            "affected_business_flow": {"name": self._first_text(item.get("flow"), item.get("contract_id"), risk_type)},
            "affected_modules": [self._extract_module(title, actual)],
            "affected_roles": [],
            "first_seen_at": self._first_text(item.get("first_seen_at"), payload.get("generated_at_utc"), self._mtime_utc(source_path)),
            "last_verified_at": self._first_text(item.get("last_verified_at"), payload.get("generated_at_utc"), self._mtime_utc(source_path)),
            "reproduction_steps": steps,
            "quality_assurance_gap": quality_gap,
            "evidence_hint": f"来源文件：{source_path.name}；执行策略：{self._first_text(item.get('execution_policy'), evidence.get('execution_policy'), 'unknown')}",
            "evidence": {**evidence, "method": method, "path": path, "source_file": source_path.name, "expected": expected, "actual": actual},
            "_api_path": path,
            "_api_method": method,
        }

    def _normalize_probe_execution_finding(self, item: dict[str, Any], source_path: Path, index: int) -> dict[str, Any]:
        probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
        method = self._first_text(probe.get("method"), item.get("method")).upper()
        path = self._first_text(probe.get("path"), item.get("path"))
        status_code = item.get("response_status")
        error = self._first_text(item.get("error"), item.get("reason"))
        title = self._first_text(probe.get("title"), f"运行时探针异常：{method} {path}")
        return {
            "risk_id": self._first_text(item.get("probe_id"), probe.get("probe_id"), f"probe_exec_{index}"),
            "title": title,
            "technical_title": f"{method} {path} · {title}",
            "severity": self._normalize_severity(probe.get("severity") or "P1"),
            "status": "confirmed" if item.get("suspicious") else "pending",
            "risk_type": self._first_text(probe.get("risk_type"), "runtime_probe"),
            "defect_family": self._first_text(probe.get("defect_family"), "runtime_probe"),
            "summary": error or title,
            "business_impact": error or title,
            "suggested_action": self._first_text(probe.get("expected"), "对照响应码、日志与 DB 结果确认是否为可复现缺陷。"),
            "expected": self._first_text(probe.get("expected")),
            "actual": error or f"response_status={status_code}",
            "confidence_score": self._coerce_float(item.get("confidence"), 0.70),
            "reproducibility_score": self._coerce_float(item.get("confidence"), 0.70),
            "affected_business_flow": {"name": self._first_text(probe.get("risk_type"), "runtime_probe")},
            "affected_modules": [self._extract_module(title, error)],
            "affected_roles": [],
            "first_seen_at": self._mtime_utc(source_path),
            "last_verified_at": self._mtime_utc(source_path),
            "reproduction_steps": [f"执行 {method} {path}", "记录响应状态码、响应体、请求时间与 traceId", "核对业务数据是否出现不符合预期的副作用"],
            "quality_assurance_gap": not bool(item.get("suspicious")),
            "evidence_hint": f"来源文件：{source_path.name}；response_status={status_code}",
            "evidence": {"method": method, "path": path, "status_code": status_code, "error": error, "source_file": source_path.name},
            "_api_path": path,
            "_api_method": method,
        }

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

    def _v12_findings(self, report: dict[str, Any], enterprise_docs: list[dict] | None = None) -> list[dict[str, Any]]:
        raw_items = report.get("real_findings") or report.get("findings") or report.get("bug_scores") or []
        if not isinstance(raw_items, list):
            return []
        docs = enterprise_docs or []
        findings: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            title = self._first_text(item.get("title"), item.get("bug_title"), item.get("technical_title"), item.get("description"), f"V12 finding {index + 1}")
            description = self._first_text(item.get("description"), item.get("summary"), item.get("actual"), item.get("actual_behavior"), title)
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
            raw_evidence = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
            request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
            reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
            har_evidence = reproduction.get("har_evidence") if isinstance(reproduction.get("har_evidence"), dict) else {}
            expected = self._first_text(item.get("expected_behavior"), item.get("expected"), evidence.get("expected"), evidence.get("assertion"), item.get("suggested_action"))
            actual = self._first_text(item.get("actual_behavior"), item.get("actual"), evidence.get("actual"), item.get("business_impact"), description)
            steps = item.get("reproduction_steps") if isinstance(item.get("reproduction_steps"), list) else []
            if not steps:
                steps = evidence.get("reproduction_steps") if isinstance(evidence.get("reproduction_steps"), list) else []
            if not steps:
                steps = reproduction.get("steps") if isinstance(reproduction.get("steps"), list) else []
            claim_method, claim_path = _claim_request_identity(
                title,
                description,
                expected,
                actual,
                self._first_text(evidence.get("assertion")),
                self._first_text(item.get("suggested_action")),
                [str(step) for step in steps if str(step or "").strip()],
            )

            import re
            text_for_route = " ".join([title, description, str(evidence), str(probe)])
            # 提取 API 路径——用 ASCII-only 正则避免中文/日文等被误匹配为路径
            # \w 在 Python re 默认包含 Unicode，会把路径后的中文叙述一并匹配
            path_match = re.search(r'(/api/[a-zA-Z0-9_/{}.-]+|/[a-zA-Z0-9{}.-]+/[a-zA-Z0-9_/{}.-]+)', text_for_route)
            api_path = self._first_text(
                item.get("_api_path"),
                item.get("path"),
                item.get("path_template"),
                claim_path,
                evidence.get("path"),
                evidence.get("path_template"),
                probe.get("path"),
                request_raw.get("path"),
                har_evidence.get("path"),
                reproduction.get("path"),
                path_match.group(1) if path_match else "",
            )
            # 验证提取的路径是合法 API 端点（防止描述文本被误判为路径）
            if api_path:
                api_path = _validate_api_path(api_path)
            if api_path and "{" in api_path:
                for candidate_path in (request_raw.get("path"), har_evidence.get("path"), reproduction.get("path")):
                    concrete_path = _validate_api_path(str(candidate_path or "").strip())
                    if concrete_path and _materialized_path_matches(api_path, concrete_path):
                        api_path = concrete_path
                        break
            method_match = re.search(r'\b(POST|GET|PUT|DELETE|PATCH)\b', text_for_route, re.IGNORECASE)
            api_method = self._first_text(
                item.get("_api_method"),
                item.get("method"),
                claim_method,
                evidence.get("method"),
                probe.get("method"),
                request_raw.get("method"),
                har_evidence.get("method"),
                reproduction.get("method"),
                method_match.group(1) if method_match else "",
            ).upper()

            matched = item.get("_doc_refs") if isinstance(item.get("_doc_refs"), list) else (self._match_docs_for_finding(title, docs) if docs else [])
            severity = self._normalize_severity(item.get("severity"))
            risk_type = self._first_text(item.get("category"), item.get("risk_type"), item.get("business_assurance_type"), "业务规则验证")
            confirmation_status = self._first_text(item.get("confirmation_status"), item.get("status"), item.get("verdict"), item.get("bug_confirmation"), "candidate").lower()
            gate_passed = bool(item.get("gate_passed"))
            bug_status = self._first_text(item.get("bug_status"), "reproduced" if gate_passed else "risk_clue")
            evidence_status = item.get("evidence_status") if isinstance(item.get("evidence_status"), dict) else {}
            evidence_quality = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
            semantic_verdict = self._first_text(item.get("semantic_verdict"), evidence_status.get("semantic_verdict"))
            business_evidence_status = self._first_text(item.get("business_evidence_status"), evidence_status.get("business_evidence_status")).upper()
            final_review_status = self._first_text(item.get("final_review_status"), evidence_status.get("final_review_status"))
            missing_requirements = item.get("missing_requirements") if isinstance(item.get("missing_requirements"), list) else (
                evidence_status.get("missing_requirements") if isinstance(evidence_status.get("missing_requirements"), list) else []
            )
            status = "confirmed" if confirmation_status in {"confirmed", "validated", "validated_candidate"} else ("pending" if confirmation_status in {"candidate", "pending"} else confirmation_status)
            quality_gap = (
                bool(item.get("quality_assurance_gap"))
                or "coverage_gap" in risk_type
                or confirmation_status in {"candidate", "pending", "needs_human_review"}
                or not gate_passed
                or bug_status != "reproduced"
                or business_evidence_status not in {"VALIDATED", "READY"}
            )
            timestamp = self._first_text(
                item.get("timestamp"),
                evidence.get("timestamp"),
                raw_evidence.get("timestamp"),
                item.get("last_verified_at"),
                report.get("generated_at_utc"),
            )
            reproducibility = item.get("reproducibility") if isinstance(item.get("reproducibility"), dict) else {}
            if not reproducibility:
                har_evidence = reproduction.get("har_evidence") if isinstance(reproduction.get("har_evidence"), dict) else {}
                reproducibility = {
                    "reproducible": bool(gate_passed and (steps or raw_evidence.get("has_real_evidence") or har_evidence)),
                    "reproduction_confidence": self._coerce_float(
                        item.get("reproducibility_score"),
                        0.95 if gate_passed else (0.7 if confirmation_status in {"confirmed", "validated", "validated_candidate"} else 0.45),
                    ),
                }
            failed_assertions = item.get("failed_assertions") if isinstance(item.get("failed_assertions"), list) else []
            if not failed_assertions and expected and actual and expected != actual:
                failed_assertions = [{
                    "type": "semantic_violation",
                    "rule": self._first_text(evidence.get("assertion"), item.get("description"), title),
                    "expected": expected,
                    "actual": actual,
                }]
            if har_evidence:
                har_evidence = {
                    **har_evidence,
                    "method": self._first_text(har_evidence.get("method"), api_method, raw_evidence.get("request_raw", {}).get("method")),
                    "path": self._first_text(har_evidence.get("path"), api_path, raw_evidence.get("request_raw", {}).get("path")),
                    "actor": self._first_text(har_evidence.get("actor"), raw_evidence.get("request_raw", {}).get("actor"), evidence.get("actor")),
                    "duration_ms": har_evidence.get("duration_ms") if har_evidence.get("duration_ms") is not None else (
                        raw_evidence.get("response_raw", {}).get("duration_ms") if isinstance(raw_evidence.get("response_raw"), dict) else 0
                    ),
                }
            request_path = _validate_api_path(self._first_text(request_raw.get("path")))
            request_method = self._first_text(request_raw.get("method")).upper()
            if (
                request_path
                and api_path
                and isinstance(raw_evidence.get("response_raw"), dict)
                and raw_evidence.get("response_raw")
                and (
                    normalize_path_placeholders(request_path) != normalize_path_placeholders(api_path)
                    or (request_method and api_method and request_method != api_method)
                )
            ):
                raw_evidence = {
                    **raw_evidence,
                    "response_raw": {},
                }
            # 不伪造复现步骤——如果没有真实步骤，留空列表，由 formatter 生成标记为 [指引] 的建议

            finding_evidence = dict(evidence)
            finding_evidence.update({
                "path": api_path,
                "method": api_method,
                "summary": self._first_text(evidence.get("summary"), item.get("summary"), description),
                "expected": expected,
                "actual": actual,
            })
            if item.get("evidence_hint") and not finding_evidence.get("source_file"):
                finding_evidence["source_file"] = str(item.get("evidence_hint"))

            findings.append({
                "candidate_id": self._first_text(item.get("candidate_id")),
                "slice_id": self._first_text(item.get("slice_id")),
                "obligation_id": self._first_text(item.get("obligation_id")),
                "experiment_id": self._first_text(item.get("experiment_id")),
                "execution_id": self._first_text(item.get("execution_id")),
                "evidence_id": self._first_text(item.get("evidence_id")),
                "finding_id": self._first_text(item.get("finding_id")),
                "risk_id": self._first_text(item.get("risk_id"), item.get("bug_id"), item.get("evidence_id"), item.get("finding_id"), f"v12_{index}"),
                "id": self._first_text(item.get("risk_id"), item.get("bug_id"), item.get("evidence_id"), item.get("finding_id"), f"v12_{index}"),
                "title": title,
                "technical_title": f"{api_method} {api_path} · {title}" if api_method or api_path else title,
                "severity": severity,
                "status": "pending" if quality_gap and status != "confirmed" else status,
                "risk_type": risk_type,
                "defect_family": self._first_text(item.get("defect_family"), "scenario_flow" if quality_gap else risk_type),
                "summary": actual or title,
                "business_impact": self._first_text(item.get("business_impact"), actual, title),
                "suggested_action": expected or "补齐真实复现证据后进入缺陷闭环。",
                "expected": expected,
                "actual": actual,
                "confidence_score": self._coerce_float(item.get("confidence_score"), self._coerce_float(item.get("score"), self._coerce_float(item.get("confidence"), 0.75))),
                "reproducibility_score": self._coerce_float(item.get("reproducibility_score"), 0.85 if status in {"confirmed", "validated", "reproduced"} else 0.45 if quality_gap else 0.70),
                "affected_business_flow": {"name": self._first_text(item.get("flow"), item.get("category"), risk_type, "system")},
                "affected_modules": [self._first_text(item.get("module"), item.get("category"), (api_path.split("/")[1] if api_path.startswith("/") and len(api_path.split("/")) > 1 else ""), self._extract_module(title, description))],
                "affected_roles": item.get("affected_roles") if isinstance(item.get("affected_roles"), list) else [],
                "first_seen_at": self._first_text(item.get("first_seen_at"), report.get("generated_at_utc")),
                "last_verified_at": self._first_text(item.get("last_verified_at"), report.get("generated_at_utc")),
                "reproduction_steps": steps,
                "quality_assurance_gap": quality_gap,
                "evidence_hint": self._first_text(item.get("evidence_hint"), finding_evidence.get("source_file")),
                "timestamp": timestamp,
                "bug_status": bug_status,
                "gate_passed": gate_passed,
                "execution_status": self._first_text(item.get("execution_status"), "executed" if gate_passed else "planned"),
                "confirmation_status": confirmation_status,
                "semantic_verdict": semantic_verdict,
                "business_evidence_status": business_evidence_status,
                "final_review_status": final_review_status,
                "missing_requirements": missing_requirements,
                "evidence_quality": evidence_quality,
                "evidence_status": evidence_status,
                "raw_evidence": raw_evidence,
                "reproduction": reproduction,
                "reproducibility": reproducibility,
                "failed_assertions": failed_assertions,
                "har_evidence": har_evidence,
                "customer_delivery_status": self._first_text(item.get("customer_delivery_status"), "defect" if gate_passed and bug_status == "reproduced" else "clue"),
                "_doc_refs": matched,
                "doc_refs": matched,
                "evidence": finding_evidence,
                "_api_path": api_path,
                "_api_method": api_method,
            })
        return findings

    def _load_enterprise_docs(self, project_id: str, root: Path) -> list[dict]:
        """Load enterprise knowledge documents for evidence association.

        来源优先级（文件系统优先）：
        1. JSON 文件 — 上传走 ingest_enterprise_knowledge_documents，写入
           source_registry.json + enterprise_knowledge_center/sources/，
           不写 knowledge_docs 表，因此文件系统是文档的实际存储位置。
        2. SQLite 数据库 knowledge_docs 表 — 补充源，用请求上下文中的真实
           tenant_id 查询（不再硬编码 "default"，避免租户隔离失效）。

        所有路径均按 project_id 严格隔离，绝不跨项目/跨客户读取文档。
        """
        rows: list[dict[str, Any]] = []

        # ── 1. 从 JSON 文件加载（上传文档的实际存储位置，优先）──
        candidates = [
            root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "source_registry.json",
            root / "platform_workspace" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
            root / "platform_outputs" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
        ]
        for doc_path in candidates:
            if not doc_path.exists():
                continue
            try:
                asset = json.loads(doc_path.read_text(encoding="utf-8") or "{}")
            except Exception:
                continue
            sources = asset.get("source_inventory") or asset.get("sources") or asset.get("items") or []
            if isinstance(sources, dict):
                sources = list(sources.values())
            for s in sources if isinstance(sources, list) else []:
                if not isinstance(s, dict):
                    continue
                source_id = self._first_text(s.get("source_id"), s.get("id"), s.get("stored_path"), s.get("filename"))
                label = self._first_text(s.get("display_name"), s.get("filename"), s.get("original_name"), s.get("name"), source_id)
                if source_id or label:
                    rows.append({
                        "source_id": source_id or label,
                        "display_name": label,
                        "type": self._first_text(s.get("type"), s.get("source_type"), "文档"),
                        "excerpt": self._first_text(s.get("summary"), s.get("excerpt"), s.get("content"))[:260],
                    })

        # ── 2. 从数据库加载（补充源，用真实 tenant_id 保证租户隔离）──
        try:
            from . import db_persistence as dbp
            tenant_id = self._request_tenant()
            db_docs = dbp.get_knowledge_docs(root, tenant_id, project_id)
            for d in db_docs:
                content = ""
                try:
                    content = dbp.get_knowledge_doc_content(root, d.get("source_id", ""))
                except Exception:
                    pass
                rows.append({
                    "source_id": d.get("source_id", ""),
                    "display_name": d.get("display_name", ""),
                    "type": d.get("type", "文档"),
                    "excerpt": content[:260] if content else "",
                })
        except Exception:
            pass

        return self._dedupe_docs(rows)

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

    def _load_knowledge_summary(self, project_id: str, root: Path) -> dict[str, Any]:
        """Load a compact business-facing summary for the dashboard.

        Morning backend runs often write into platform_workspace before a
        formal report is materialized under platform_outputs.  The UI must
        read both locations, otherwise dashboard numbers look unrelated to the
        backend result.
        """
        candidates = [
            root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
            root / "platform_outputs" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
            root / "platform_workspace" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "source_registry.json",
        ]
        for doc_path in candidates:
            if not doc_path.exists():
                continue
            try:
                asset = json.loads(doc_path.read_text(encoding="utf-8") or "{}")
            except Exception:
                continue
            summary = asset.get("summary") if isinstance(asset, dict) else None
            if isinstance(summary, dict):
                return {
                    "active_source_count": int(summary.get("active_source_count") or summary.get("source_count") or 0),
                    "rule_count": int(summary.get("rule_count") or 0),
                    "risk_domain_count": int(summary.get("risk_domain_count") or 0),
                    "oracle_count": int(summary.get("oracle_count") or 0),
                    "business_object_count": int(summary.get("business_object_count") or 0),
                    "state_machine_count": int(summary.get("state_machine_count") or 0),
                    "knowledge_ready": bool(summary.get("knowledge_ready") or summary.get("ready")),
                }
            # Fallback for registry-style files: surface source count instead of zero.
            sources = []
            if isinstance(asset, dict):
                raw_sources = asset.get("source_inventory") or asset.get("sources") or asset.get("items") or []
                if isinstance(raw_sources, dict):
                    sources = list(raw_sources.values())
                elif isinstance(raw_sources, list):
                    sources = raw_sources
            if sources:
                return {
                    "active_source_count": len(sources),
                    "rule_count": 0,
                    "risk_domain_count": 0,
                    "oracle_count": 0,
                    "business_object_count": 0,
                    "state_machine_count": 0,
                    "knowledge_ready": True,
                }
        return {}

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
    def _build_test_task_board(report: dict) -> dict | None:
        """主链 8: 从 v12 报告抽取测试任务看板数据，供前端零变换渲染。

        返回 {ledger, slices, execution, evidence_chains_saved}；当报告缺少任务
        数据（尚未生成行为路径计划）时返回 None，前端据此显示空态。纯函数、单
        一真相源，便于单测。
        """
        if not isinstance(report, dict):
            return None
        ledger = report.get("behavior_slice_ledger") if isinstance(report.get("behavior_slice_ledger"), dict) else {}
        slices = report.get("behavior_slices") if isinstance(report.get("behavior_slices"), list) else []
        if not ledger and not slices:
            return None
        phases = report.get("phases") if isinstance(report.get("phases"), dict) else {}
        exec_phase = phases.get("execution") if isinstance(phases.get("execution"), dict) else {}
        oracle_phase = phases.get("oracle") if isinstance(phases.get("oracle"), dict) else {}
        return {
            "ledger": ledger,
            "slices": slices,
            "execution": {"production_data_blocked": int(exec_phase.get("production_data_blocked") or 0)},
            "evidence_chains_saved": int(oracle_phase.get("evidence_chains_saved") or 0),
        }

    def _build_command_center(self, project_id: str, root: Path) -> dict:
        report = self._load_v12_report(project_id, root)
        current_scan_report = self._load_current_scan_report(project_id, root)
        real_discovery_payload = _load_real_project_discovery_payload(root, project_id)
        current_report_source = current_scan_report if current_scan_report else report
        discovery_current_scope = self._discovery_current_scope_summary(real_discovery_payload or {})
        enterprise_docs = self._load_enterprise_docs(project_id, root)
        knowledge_summary = self._load_knowledge_summary(project_id, root)
        discovery_payload = real_discovery_payload or self._auto_discovery_payload(project_id, root, report)
        risks = self._v12_findings(report, enterprise_docs)
        risks.extend(self._load_db_findings(root, project_id))
        risks.extend(self._load_perf_regressions(root, project_id))
        risks.extend(self._load_spectrum_findings(root, project_id))
        risks.extend(self._load_multi_layer_findings(root, project_id))
        # Load DB verification findings
        db_verify = report.get("db_verification", {})
        if isinstance(db_verify.get("findings"), list):
            for f in db_verify["findings"]:
                f.setdefault("risk_type", "db_verification")
                f.setdefault("defect_family", "data_integrity")
            risks.extend(db_verify["findings"])
        # E2E flow findings
        e2e = report.get("e2e_findings", [])
        if isinstance(e2e, list):
            for f in e2e:
                f.setdefault("risk_type", "e2e_flow")
                f.setdefault("defect_family", "business_flow")
            risks.extend(e2e)
        # Deep verifier findings
        deep = report.get("deep_findings", [])
        if isinstance(deep, list):
            for f in deep:
                f.setdefault("risk_type", "深度验证")
                f.setdefault("defect_family", "deep_test")
            risks.extend(deep)
        # Frontend UI findings
        ui = report.get("ui_findings", [])
        if isinstance(ui, list):
            for f in ui:
                f.setdefault("risk_type", "frontend_ui")
                f.setdefault("defect_family", "ui")
                risks.append(_annotate_ui_risk_item(dict(f)))
        # ── 累积 findings：从 DB 加载跨扫描累积的未修复 bug ──
        # 这是"bug 货架"模型的核心：只要 bug 没修复（status='open'），
        # 就一直保留在列表里，即使本次扫描没触发也要展示。
        # 注意：只加载不在当前 report findings 中的（避免双重计算）
        tenant_id = self._request_tenant()
        cumulative = db_persist.get_cumulative_findings(root, tenant_id, project_id)
        if cumulative:
            import re as _re2
            current_keys: set[str] = set()
            for r in risks:
                t = str(r.get("title") or "")[:200].strip().lower()
                t = _re2.sub(r'^(\[[^\]]*\]\s*)+', '', t)
                t = _re2.sub(r'\s+', ' ', t).strip()
                m = str(r.get("_api_method") or (r.get("evidence") or {}).get("method") or "").upper()
                p = str(r.get("_api_path") or (r.get("evidence") or {}).get("path") or "").strip()
                current_keys.add(f"{t}|{m}|{p}")
            for f in cumulative:
                t = str(f.get("title") or "")[:200].strip().lower()
                t = _re2.sub(r'^(\[[^\]]*\]\s*)+', '', t)
                t = _re2.sub(r'\s+', ' ', t).strip()
                m = str(f.get("_api_method") or (f.get("evidence") or {}).get("method") or "").upper()
                p = str(f.get("_api_path") or (f.get("evidence") or {}).get("path") or "").strip()
                key = f"{t}|{m}|{p}"
                if key not in current_keys:
                    f.setdefault("risk_type", f.get("category") or "累积发现")
                    f.setdefault("defect_family", "cumulative")
                    f.setdefault("_cumulative", True)
                    risks.append(f)
                    current_keys.add(key)
        risks = self._dedupe_risks([item for item in risks if isinstance(item, dict)])

        # ── 为所有未关联文档的 finding 补充文档匹配（通用，非 v12 finding 也需要）──
        if enterprise_docs:
            for item in risks:
                if not item.get("_doc_refs"):
                    title = str(item.get("title") or "")
                    matched = self._match_docs_for_finding(title, enterprise_docs)
                    if matched:
                        item["_doc_refs"] = matched

        # ── Generic: convert code identifiers to customer-facing labels ──
        import re
        # Rule: any snake_case or lowercase_underscore identifier is internal;
        # infer a readable label from the finding's actual category/title instead.
        _INTERNAL_PATTERNS = [
            (re.compile(r".*_verifier$"), "验证引擎"),
            (re.compile(r".*_discovery$|.*_scanner$"), "检测引擎"),
            (re.compile(r".*_engine$|.*_oracle$"), "规则引擎"),
            (re.compile(r".*_pipeline$|.*_pilot$"), "分析引擎"),
            (re.compile(r".*_command_center$|.*_center$"), "分析引擎"),
            (re.compile(r".*_bridge$|.*_enricher$"), "证据引擎"),
        ]

        def _is_internal_name(s: str) -> bool:
            """A string looks internal if it's snake_case with no Chinese chars."""
            if not s or not isinstance(s, str):
                return False
            # Has underscores and no Chinese characters
            return "_" in s and not any("\u4e00" <= c <= "\u9fff" for c in s)

        def _to_customer_label(s: str, title: str = "") -> str:
            """Convert internal identifier to customer label using title hints."""
            if not _is_internal_name(s):
                return s
            # Try pattern match first
            for pat, label in _INTERNAL_PATTERNS:
                if pat.match(s):
                    return label
            # Infer from title keywords
            t = title.lower()
            if "权限" in t or "auth" in t:
                return "权限检测"
            if "状态" in t or "state" in t or "禁止路径" in t:
                return "状态机分析"
            if "库存" in t or "inventory" in t:
                return "数据完整性检测"
            if "并发" in t or "concurrent" in t:
                return "并发检测"
            if "幂等" in t or "idempotent" in t:
                return "幂等检测"
            if "数据" in t or "泄露" in t or "data" in t:
                return "数据安全检测"
            # Generic fallback: just say "业务规则验证"
            return "业务规则验证"

        for r in risks:
            for field in ("source", "risk_type", "defect_family"):
                val = r.get(field, "")
                if val and _is_internal_name(str(val)):
                    new_val = _to_customer_label(str(val), str(r.get("title", "")))
                    r[field] = new_val
        # Debug: verify cleanup worked
        _remaining = sum(1 for r in risks for f in ("source","risk_type","defect_family") if r.get(f) and _is_internal_name(str(r.get(f,""))))
        if _remaining:
            print(f"[CLEANUP] WARNING: {_remaining} internal name fields remain after cleanup", flush=True)

        # ── HAR Bridge: enrich findings with real HTTP call evidence ──
        from .har_bridge import enrich_findings_batch_with_har, load_har_entries
        scan_result_path = root / "platform_outputs" / project_id / "scan_result.json"
        if scan_result_path.exists():
            scan_result = self._read_json_dict(scan_result_path)
            har_entries = load_har_entries(scan_result) if scan_result else []
            if har_entries:
                risks = enrich_findings_batch_with_har(risks, har_entries)
        if isinstance(report, dict):
            har_entries_rpt = load_har_entries(report)
            if har_entries_rpt:
                risks = enrich_findings_batch_with_har(risks, har_entries_rpt)

        # ── V3 Evidence Enrichment: three-perspective evidence chain ──
        from .evidence_enricher_v3 import enrich_findings_batch, load_enterprise_context
        enterprise_ctx = load_enterprise_context(project_id, root)
        risks = enrich_findings_batch(risks, enterprise_ctx)

        # ── Display-Ready Formatting: unify all findings into display-ready JSON ──
        from .display_ready_formatter import (
            _build_display_contract,
            _compute_commercial_value,
            _compute_scores,
            format_findings_display_ready,
            sanitize_customer_evidence_payload,
        )
        raw_display_risks, _display_metrics = format_findings_display_ready(risks, enterprise_ctx, report)
        sanitized_display_risks = sanitize_customer_evidence_payload(raw_display_risks)
        display_risks = sanitized_display_risks if isinstance(sanitized_display_risks, list) else []
        display_metrics = {
            "scores": _compute_scores(display_risks, report),
            "commercial_value": _compute_commercial_value(display_risks, report),
            "display_contract": _build_display_contract(display_risks, report),
        }

        all_display_risks = [
            item for item in display_risks
            if isinstance(item, dict) and not bool(item.get("_summary_only"))
        ]
        delivery_defects, internal_clues = _partition_delivery_tracks(all_display_risks)
        display_risks = delivery_defects
        overall_display_contract = display_metrics.get("display_contract") if isinstance(display_metrics.get("display_contract"), dict) else {}
        if isinstance(display_metrics.get("display_contract"), dict):
            display_metrics["display_contract"] = _rebuild_customer_display_contract(
                overall_display_contract,
                display_risks,
            )
        clue_contract = _build_internal_clue_contract(
            overall_display_contract,
            internal_clues,
        )

        materialized_total = len(display_risks)
        ui_stats = _ui_verification_stats(
            report.get("ui_candidate_findings")
            if isinstance(report.get("ui_candidate_findings"), list)
            else report.get("ui_findings")
        )
        intake_stats = _defect_intake_stats(all_display_risks)
        scores = display_metrics.get("scores") or {}
        commercial_value = display_metrics.get("commercial_value") or {}
        display_contract = display_metrics.get("display_contract") if isinstance(display_metrics.get("display_contract"), dict) else {}
        if not display_contract:
            status_counts = {}
            severity_counts = {}
            for item in display_risks:
                status = str(item.get("bug_status") or "risk_clue")
                severity = str(item.get("severity") or "P2")
                status_counts[status] = status_counts.get(status, 0) + 1
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
            display_contract = {
                "schema_version": "display-ready-fallback",
                "source_of_truth": "backend.private_pilot_service._build_command_center",
                "display_key": "risks",
                "materialized_risk_count": materialized_total,
                "raw_candidate_risk_count": max(materialized_total, self._report_summary_number(report, "risk_total", "risk_count", "total_findings", "total_bugs_found", "total_found", "raw_total", fallback=0)),
                "ready_bug_count": sum(1 for item in display_risks if item.get("bug_status") == "reproduced" and bool(item.get("gate_passed"))),
                "needs_validation_count": status_counts.get("suspected", 0) + status_counts.get("risk_clue", 0),
                "not_reproduced_count": status_counts.get("not_reproduced", 0),
                "status_counts": status_counts,
                "severity_counts": severity_counts,
            }

        regression_summary = _load_regression_projection(root, project_id, delivery_defects)

        defect_grouped_summary = _build_defect_grouped_summary(delivery_defects)
        defect_priority_summary = _build_defect_priority_summary(delivery_defects)
        defect_repro_summary = _build_defect_repro_summary(delivery_defects)
        defect_delivery_cards = _build_defect_delivery_cards(delivery_defects)
        current_report_findings = self._report_findings(current_report_source)
        current_report_category_counts: dict[str, int] = {}
        for item in current_report_findings:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or item.get("risk_type") or "uncategorized").strip() or "uncategorized"
            current_report_category_counts[category] = current_report_category_counts.get(category, 0) + 1
        current_report_path = str(current_report_source.get("report_source_path") or current_report_source.get("report_path") or "")
        if discovery_current_scope.get("report_source_path"):
            current_report_path = str(discovery_current_scope.get("report_source_path") or current_report_path)
        current_report_breakdown = {
            "total_findings": len(current_report_findings),
            "category_counts": current_report_category_counts,
            "report_source_path": current_report_path,
        }
        current_campaign_bundle_stats = {"raw": 0, "deduped": 0}
        if real_discovery_payload and isinstance(discovery_payload.get("continuous_discovery_campaign"), dict):
            current_campaign_bundle_stats = _current_campaign_bundle_finding_stats(
                project_id,
                root,
                discovery_payload["continuous_discovery_campaign"],
            )
        discovery_total_findings = int(discovery_current_scope.get("total_findings") or 0)
        current_scope_total_findings = max(
            0,
            int(current_report_breakdown.get("total_findings") or 0),
            discovery_total_findings,
            int(current_campaign_bundle_stats.get("raw") or 0),
        )
        current_scope_raw_candidate_findings = max(
            current_scope_total_findings,
            int(current_report_source.get("raw_total") or current_report_source.get("total_findings") or current_scope_total_findings or 0),
        )
        has_campaign_scope = bool(discovery_current_scope) or bool(
            int(current_campaign_bundle_stats.get("deduped") or 0)
            or int(current_campaign_bundle_stats.get("raw") or 0)
        )
        if has_campaign_scope:
            # Real campaign scope present: customer-ready defects is the deduped /
            # validated campaign count, NEVER the raw bundle total. Using the raw
            # total (current_scope_total_findings) here conflated "本轮候选总数"
            # with "本轮 customer-ready 缺陷数".
            current_scope_customer_ready_defect_count = max(
                0,
                int(discovery_current_scope.get("customer_ready_defects") or 0),
                int(current_campaign_bundle_stats.get("deduped") or 0),
            )
            if current_scope_total_findings:
                current_scope_customer_ready_defect_count = min(
                    current_scope_customer_ready_defect_count,
                    current_scope_total_findings,
                )
        else:
            current_scope_customer_ready_defect_count = max(
                0,
                int(self._report_summary_number(
                    current_report_source,
                    "customer_ready_defects",
                    "ready_bug_count",
                    "total_bugs_found",
                    fallback=current_scope_total_findings,
                ) or current_scope_total_findings),
            )
            current_scope_customer_ready_defect_count = min(
                current_scope_customer_ready_defect_count or current_scope_total_findings,
                current_scope_total_findings or len(delivery_defects),
            )
        current_scope_materialized_findings = max(
            current_scope_customer_ready_defect_count,
            current_scope_total_findings,
        )
        family_customer_ready_defect_count = len(delivery_defects)
        campaign_scope = _current_campaign_scope_summary(
            discovery_payload.get("continuous_discovery_campaign")
            if isinstance(discovery_payload.get("continuous_discovery_campaign"), dict)
            else {}
        )

        canonical_total = materialized_total
        raw_candidate_total = int(display_contract.get("raw_candidate_risk_count") or canonical_total)
        ready_bug_count = current_scope_customer_ready_defect_count or int(display_contract.get("ready_bug_count") or 0)
        needs_validation_count = int(display_contract.get("needs_validation_count") or 0)
        not_reproduced_count = int(display_contract.get("not_reproduced_count") or 0)
        status_counts = display_contract.get("status_counts") if isinstance(display_contract.get("status_counts"), dict) else {}
        severity_counts = display_contract.get("severity_counts") if isinstance(display_contract.get("severity_counts"), dict) else {}
        canonical_p0 = int(severity_counts.get("P0") or sum(1 for item in display_risks if item.get("severity") == "P0"))
        canonical_p1 = int(severity_counts.get("P1") or sum(1 for item in display_risks if item.get("severity") == "P1"))
        canonical_p2 = max(0, canonical_total - canonical_p0 - canonical_p1)
        ready_p0 = sum(1 for item in display_risks if item.get("bug_status") == "reproduced" and bool(item.get("gate_passed")) and item.get("severity") == "P0")
        ready_p1 = sum(1 for item in display_risks if item.get("bug_status") == "reproduced" and bool(item.get("gate_passed")) and item.get("severity") == "P1")
        evidence_trust = self._evidence_trust_score(risks)
        if scores:
            evidence_trust = scores.get("evidence_trust_score", evidence_trust)
        try:
            evidence_trust = max(0.0, min(100.0, float(evidence_trust or 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid evidence_trust_score in command-center projection") from exc
        try:
            ai_points = max(int(report.get("raw_total") or 0), int(report.get("total_findings") or 0), raw_candidate_total, canonical_total)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid finding count in command-center report") from exc
        if canonical_total <= 0:
            evidence_grade = "C"
        elif evidence_trust >= 85:
            evidence_grade = "A"
        elif evidence_trust >= 70:
            evidence_grade = "B"
        elif evidence_trust >= 50:
            evidence_grade = "C"
        else:
            evidence_grade = "D"
        scan_counter = self._scan_counter(project_id, root)
        execution_evidence_summary = _normalize_execution_evidence_summary(
            report.get("execution_evidence_summary"),
            report.get("ui_execution_summary"),
            report.get("ui_execution"),
        )
        commercial_assets = _select_commercial_assets(report.get("external_commercial_assets"), report.get("commercial_assets"))
        if regression_summary:
            regression_summary.update(_build_regression_release_guidance(regression_summary, commercial_assets))
        validation_summary = regression_summary.get("validation_summary") if isinstance(regression_summary.get("validation_summary"), dict) else {}

        scan_meta = {
            "scan_id": str(current_report_source.get("scan_id") or report.get("scan_id") or ""),
            "run_count": int(scan_counter.get("count") or 0),
            "first_scan_at": str(scan_counter.get("first_scan_at") or ""),
            "last_scan_at": str(scan_counter.get("last_scan_at") or report.get("generated_at_utc") or ""),
            "total_ms": int(current_report_source.get("total_ms") or report.get("total_ms") or 0),
            "total_findings": current_scope_total_findings,
            "materialized_findings": current_scope_materialized_findings,
            "raw_candidate_findings": current_scope_raw_candidate_findings,
            "ready_bug_count": ready_bug_count,
            "needs_validation_findings": needs_validation_count,
            "not_reproduced_findings": not_reproduced_count,
            "customer_ready_defects": current_scope_customer_ready_defect_count,
            "current_report_total_findings": current_scope_total_findings,
            "current_report_materialized_findings": current_scope_materialized_findings,
            "current_report_customer_ready_defect_count": current_scope_customer_ready_defect_count,
            "current_campaign_bundle_finding_count_raw": int(current_campaign_bundle_stats.get("raw") or 0),
            "family_customer_ready_defect_count": family_customer_ready_defect_count,
            "family_materialized_findings": materialized_total,
            "internal_clue_count": len(internal_clues),
            "ui_candidate_findings": ui_stats["ui_candidate_total"],
            "ui_verified_candidates": ui_stats["ui_verified_candidate_total"],
            "ui_high_confidence_candidates": ui_stats["ui_high_confidence_candidate_total"],
            "defect_intake_recommended": intake_stats["defect_intake_recommended_total"],
            "grade": evidence_grade,
            "score": evidence_trust,
            "regression_last_run_at": _first_text((regression_summary.get("latest_run") or {}).get("generated_at")),
            "regression_last_run_mode": _first_text((regression_summary.get("latest_run") or {}).get("suite_mode")),
            "regression_gate_status": _first_text((regression_summary.get("latest_run") or {}).get("gate_status")),
            "regression_failed_defect_count": int(regression_summary.get("failed_defect_count") or 0),
            "regression_pending_defect_count": int(regression_summary.get("pending_defect_count") or 0),
            "regression_covered_defect_count": int(regression_summary.get("covered_defect_count") or 0),
            "regression_history_run_count": int(regression_summary.get("history_run_count") or 0),
            "regression_trend_direction": _first_text(regression_summary.get("trend_direction")),
            "release_recommendation": _first_text(regression_summary.get("release_recommendation")),
            "customer_delivery_readiness": _first_text(regression_summary.get("customer_delivery_readiness")),
            "regression_double_run_verified": 1 if validation_summary.get("double_run_verified") else 0,
            "regression_repeated_failure_defect_count": int(validation_summary.get("repeated_failure_defect_count") or 0),
            "report_path": current_report_path or str(current_report_source.get("report_source_path") or current_report_source.get("report_path") or report.get("report_source_path") or report.get("report_path") or ""),
            "current_report_breakdown": current_report_breakdown,
        }
        # ── P6: Benchmark metrics (only when ground truth exists) ──
        benchmark_data: dict[str, Any] = {}
        _bm_path = root / "platform_outputs" / project_id / "benchmark" / "benchmark_metrics.json"
        if _bm_path.exists():
            benchmark_data = _read_json_object(_bm_path)
        if benchmark_data:
            scan_meta["benchmark_metrics"] = benchmark_data
        if campaign_scope:
            scan_meta["current_campaign_scope"] = campaign_scope
        data = {
            "project_id": project_id,
            "project_name": str(report.get("project_name") or project_id),
            "industry": str(report.get("industry") or "multi_layer"),
            "updated_at": scan_meta["last_scan_at"] or str(report.get("generated_at_utc") or ""),
            "live_map": {"status": "completed" if report else "idle"},
            "scan_meta": scan_meta,
            "execution_evidence_summary": execution_evidence_summary,
            "regression_summary": regression_summary,
            "defects": delivery_defects,
            "clues": internal_clues,
            "risks": display_risks,
            "defect_grouped_summary": defect_grouped_summary,
            "defect_priority_summary": defect_priority_summary,
            "defect_repro_summary": defect_repro_summary,
            "defect_delivery_cards": defect_delivery_cards,
            "value_metrics": {
                "evidence_trust_score": evidence_trust,
                "ai_equivalent_test_points": ai_points,
                "canonical_risk_count": current_scope_total_findings,
                "materialized_risk_count": current_scope_materialized_findings,
                "raw_candidate_risk_count": current_scope_raw_candidate_findings,
                "ready_bug_count": ready_bug_count,
                "needs_validation_count": needs_validation_count,
                "not_reproduced_count": not_reproduced_count,
                "defect_count": family_customer_ready_defect_count,
                "clue_count": len(internal_clues),
                "current_report_total_findings": current_scope_total_findings,
                "current_report_customer_ready_defect_count": current_scope_customer_ready_defect_count,
                "family_customer_ready_defect_count": family_customer_ready_defect_count,
                "p0_count": canonical_p0,
                "p1_count": canonical_p1,
                "p2_count": canonical_p2,
                "ready_p0_count": ready_p0,
                "ready_p1_count": ready_p1,
                "status_counts": status_counts,
                "ui_total": ui_stats["ui_total"],
                "ui_candidate_total": ui_stats["ui_candidate_total"],
                "ui_verified_candidate_total": ui_stats["ui_verified_candidate_total"],
                "ui_unverified_candidate_total": ui_stats["ui_unverified_candidate_total"],
                "ui_high_confidence_candidate_total": ui_stats["ui_high_confidence_candidate_total"],
                "defect_intake_recommended_total": intake_stats["defect_intake_recommended_total"],
                "customer_defect_intake_total": intake_stats["customer_defect_intake_total"],
                "internal_defect_intake_total": intake_stats["internal_defect_intake_total"],
                "regression_covered_defect_count": int(regression_summary.get("covered_defect_count") or 0),
                "regression_passed_defect_count": int(regression_summary.get("passed_defect_count") or 0),
                "regression_failed_defect_count": int(regression_summary.get("failed_defect_count") or 0),
                "regression_needs_review_defect_count": int(regression_summary.get("needs_review_defect_count") or 0),
                "regression_pending_defect_count": int(regression_summary.get("pending_defect_count") or 0),
                "regression_not_covered_defect_count": int(regression_summary.get("not_covered_defect_count") or 0),
                "regression_gate_status": _first_text((regression_summary.get("latest_run") or {}).get("gate_status")),
                "regression_last_run_mode": _first_text((regression_summary.get("latest_run") or {}).get("suite_mode")),
                "regression_history_run_count": int(regression_summary.get("history_run_count") or 0),
                "regression_trend_direction": _first_text(regression_summary.get("trend_direction")),
                "release_recommendation": _first_text(regression_summary.get("release_recommendation")),
                "customer_delivery_readiness": _first_text(regression_summary.get("customer_delivery_readiness")),
                "regression_double_run_verified": 1 if validation_summary.get("double_run_verified") else 0,
                "regression_repeated_failure_defect_count": int(validation_summary.get("repeated_failure_defect_count") or 0),
                "scores": scores,
                "commercial_value": commercial_value,
                "current_report_breakdown": current_report_breakdown,
                "defect_grouped_summary": defect_grouped_summary,
                "defect_priority_summary": defect_priority_summary,
                "defect_repro_summary": defect_repro_summary,
                "defect_delivery_cards": defect_delivery_cards,
                "regression_summary": regression_summary,
            },
            "data_contract": {
                **display_contract,
                "endpoint": f"/api/v1/projects/{project_id}/command-center",
                "frontend_entry": "frontend/src/api/client.ts:getFindings",
                "backend_builder": "ai_test_asset_center/private_pilot_service.py:_build_command_center",
                "formatter": "ai_test_asset_center/display_ready_formatter.py:format_findings_display_ready",
                "display_key": "defects",
                "compatibility_alias": "risks",
                "contract_rule": "客户页优先渲染 data.defects；data.risks 仅为兼容别名。内部待验证线索统一放在 data.clues。",
                "current_report_breakdown": current_report_breakdown,
                "defect_grouped_summary": defect_grouped_summary,
                "defect_priority_summary": defect_priority_summary,
                "defect_repro_summary": defect_repro_summary,
                "defect_delivery_cards": defect_delivery_cards,
                "regression_summary": regression_summary,
            },
            "delivery_tracks": {
                "defects": {
                    **display_contract,
                    "display_key": "defects",
                    "compatibility_alias": "risks",
                },
                "clues": clue_contract,
            },
            "business_flow_summary": {"total": ai_points},
            "executive_summary": {
                "total_findings": current_scope_total_findings,
                "total_bugs_found": ready_bug_count,
                "ready_bugs": ready_bug_count,
                "customer_ready_defects": current_scope_customer_ready_defect_count,
                "family_customer_ready_defects": family_customer_ready_defect_count,
                "internal_clues": len(internal_clues),
                "needs_validation_findings": needs_validation_count,
                "not_reproduced_findings": not_reproduced_count,
                "raw_candidate_findings": current_scope_raw_candidate_findings,
                "critical_bugs": ready_p0,
                "high_priority_bugs": ready_p1,
                "materialized_findings": current_scope_materialized_findings,
                "ui_candidate_findings": ui_stats["ui_candidate_total"],
                "ui_verified_candidates": ui_stats["ui_verified_candidate_total"],
                "ui_high_confidence_candidates": ui_stats["ui_high_confidence_candidate_total"],
                "defect_intake_recommended": intake_stats["defect_intake_recommended_total"],
                "regression_gate_status": _first_text((regression_summary.get("latest_run") or {}).get("gate_status")),
                "regression_failed_defects": int(regression_summary.get("failed_defect_count") or 0),
                "regression_pending_defects": int(regression_summary.get("pending_defect_count") or 0),
                "regression_covered_defects": int(regression_summary.get("covered_defect_count") or 0),
                "regression_history_run_count": int(regression_summary.get("history_run_count") or 0),
                "regression_trend_direction": _first_text(regression_summary.get("trend_direction")),
                "release_recommendation": _first_text(regression_summary.get("release_recommendation")),
                "release_recommendation_label": _first_text(regression_summary.get("release_recommendation_label")),
                "customer_delivery_readiness": _first_text(regression_summary.get("customer_delivery_readiness")),
                "customer_delivery_readiness_label": _first_text(regression_summary.get("customer_delivery_readiness_label")),
                "regression_double_run_verified": 1 if validation_summary.get("double_run_verified") else 0,
                "regression_repeated_failure_defects": int(validation_summary.get("repeated_failure_defect_count") or 0),
                "llm_powered_analyses": ai_points,
                "system_grade": scan_meta["grade"],
                "overall_score": scan_meta["score"],
                "current_report_breakdown": current_report_breakdown,
                "defect_grouped_summary": defect_grouped_summary,
                "defect_priority_summary": defect_priority_summary,
                "defect_repro_summary": defect_repro_summary,
                "defect_delivery_cards": defect_delivery_cards,
                "regression_summary": regression_summary,
            },
        }
        if campaign_scope:
            data["current_campaign_scope"] = campaign_scope
            data["value_metrics"]["current_campaign_scope"] = campaign_scope
            data["executive_summary"]["current_campaign_scope"] = campaign_scope
        # Discovery funnel observability (five-stage + blockers). Prefer the
        # freshest scan report; never invent numbers when absent.
        discovery_funnel = None
        for source in (current_scan_report, report, real_discovery_payload):
            if isinstance(source, dict) and isinstance(source.get("discovery_funnel"), dict):
                discovery_funnel = source.get("discovery_funnel")
                break
            if isinstance(source, dict) and isinstance(source.get("v12"), dict):
                nested = source["v12"].get("discovery_funnel")
                if isinstance(nested, dict):
                    discovery_funnel = nested
                    break
        if isinstance(discovery_funnel, dict):
            data["discovery_funnel"] = discovery_funnel
            data["scan_meta"]["discovery_funnel"] = discovery_funnel
            pipeline_health = discovery_funnel.get("pipeline_health") if isinstance(discovery_funnel.get("pipeline_health"), dict) else None
            if not pipeline_health:
                for source in (current_scan_report, report, real_discovery_payload):
                    if isinstance(source, dict) and isinstance(source.get("pipeline_health"), dict):
                        pipeline_health = source.get("pipeline_health")
                        break
                    if isinstance(source, dict) and isinstance(source.get("v12"), dict):
                        nested_funnel = source["v12"].get("discovery_funnel")
                        if isinstance(nested_funnel, dict) and isinstance(nested_funnel.get("pipeline_health"), dict):
                            pipeline_health = nested_funnel.get("pipeline_health")
                            break
            if isinstance(pipeline_health, dict):
                data["pipeline_health"] = pipeline_health
                data["scan_meta"]["pipeline_health"] = pipeline_health
                data["scan_meta"]["pipeline_health_status"] = str(pipeline_health.get("status") or "")
                data["scan_meta"]["empty_findings_means_no_bugs"] = bool(pipeline_health.get("empty_findings_means_no_bugs"))
        if knowledge_summary:
            data["knowledge_summary"] = knowledge_summary
        if real_discovery_payload and isinstance(discovery_payload.get("continuous_discovery_campaign"), dict):
            data["continuous_discovery_campaign"] = _augment_continuous_discovery_campaign(
                discovery_payload["continuous_discovery_campaign"],
                current_report_breakdown=current_report_breakdown,
                delivery_defects=delivery_defects,
                current_campaign_customer_ready_defect_count=int(current_campaign_bundle_stats.get("deduped") or 0),
                current_campaign_bundle_finding_count_raw=int(current_campaign_bundle_stats.get("raw") or 0),
            )
        if real_discovery_payload and isinstance(discovery_payload.get("metrics"), dict):
            data["continuous_discovery_metrics"] = {
                key: value
                for key, value in discovery_payload["metrics"].items()
                if str(key).startswith("continuous_discovery_") or str(key) == "doc_completeness"
            }
        spectrum = self._load_spectrum_status_payload(root, project_id)
        if spectrum:
            data["spectrum"] = spectrum
        # ── 累积 findings 统计 + continuous 状态 ──
        tenant_id = self._request_tenant()
        data["cumulative_stats"] = db_persist.get_finding_stats(root, tenant_id, project_id)
        if commercial_assets:
            data["commercial_assets"] = commercial_assets
            data["scan_meta"]["commercial_handoff_status"] = _first_text((commercial_assets.get("commercial_handoff") or {}).get("status"))
            data["scan_meta"]["external_tracker_sync_payload_status"] = _first_text((commercial_assets.get("tracker_sync") or {}).get("payload_status"))
            data["scan_meta"]["delivery_package_status"] = _first_text((commercial_assets.get("delivery_package") or {}).get("status"))
            data["value_metrics"]["commercial_asset_materialized"] = 1 if commercial_assets.get("status") == "materialized" else 0
            data["value_metrics"]["commercial_delivery_package_created"] = 1 if _first_text((commercial_assets.get("delivery_package") or {}).get("status")) == "created" else 0
            data["executive_summary"]["commercial_handoff_status"] = _first_text((commercial_assets.get("commercial_handoff") or {}).get("status"))
            data["executive_summary"]["delivery_package_status"] = _first_text((commercial_assets.get("delivery_package") or {}).get("status"))
        # ── Rounds Summary (Round 1-4 data for dashboard) ──
        from .rounds_summary import build_rounds_summary
        data["rounds_summary"] = build_rounds_summary(project_id, root)

        # ── 主链 8: 测试任务看板 ──
        # Surface the per-task lifecycle status board (主链 4), the production-data
        # safety-boundary block count (主链 5/6), and the persisted evidence-chain
        # count (主链 7) so the frontend can render the full main-chain closure.
        # All fields come straight from the v12 report; no transformation.
        _test_task_board = self._build_test_task_board(report)
        if _test_task_board:
            data["test_task_board"] = _test_task_board
        # ── 主链 4/5 覆盖诚实性: surface unexecuted high-value slices + any grade
        # downgrade so the frontend never shows a clean completion while
        # authorization/isolation/money/concurrency checks were silently skipped. ──
        _coverage_honesty = current_scan_report.get("coverage_honesty") if isinstance(current_scan_report, dict) else None
        if isinstance(_coverage_honesty, dict):
            data["coverage_honesty"] = _coverage_honesty
            if _test_task_board:
                data["test_task_board"]["coverage_honesty"] = _coverage_honesty
        data["continuous_state"] = _get_continuous_state(root, project_id)
        # External evaluation / commercial quality projection (Phase 0 SSOT).
        # NOT_MEASURED must never surface as a quality score of 100 or 0.
        try:
            from .discovery_quality_projection import (
                attach_quality_projection_to_scan_result,
                suppress_benchmark_quality_when_not_measured,
            )
            from .discovery_mainline_contract import MainlineContractError

            _scan_for_quality: dict[str, Any] = {}
            for source in (current_scan_report, report, real_discovery_payload):
                if isinstance(source, dict) and source:
                    _scan_for_quality = dict(source)
                    break
            _current_findings_declared = False
            if isinstance(current_scan_report, dict):
                for _finding_key in ("findings", "real_findings"):
                    if isinstance(current_scan_report.get(_finding_key), list):
                        _scan_for_quality["findings"] = list(current_scan_report.get(_finding_key) or [])
                        _current_findings_declared = True
                        break
                if isinstance(current_scan_report.get("candidate_findings"), list):
                    _scan_for_quality["candidate_findings"] = list(current_scan_report.get("candidate_findings") or [])
            if not _current_findings_declared and not _scan_for_quality.get("findings"):
                _scan_for_quality["findings"] = list(delivery_defects or [])
            if not _scan_for_quality.get("candidate_findings"):
                _scan_for_quality["candidate_findings"] = list(internal_clues or [])
            if isinstance(discovery_funnel, dict):
                _scan_for_quality["discovery_funnel"] = discovery_funnel
            # Prefer v12 nested obligation/experiment fields when present on report.
            if isinstance(current_scan_report, dict):
                for _key in (
                    "test_obligations",
                    "experiment_compile",
                    "obligation_plan",
                    "execution_adapters",
                    "behavior_ir",
                    "phases",
                    "pipeline_health",
                    "campaign",
                    "behavior_slice_ledger",
                    "v12",
                ):
                    if _key not in _scan_for_quality and current_scan_report.get(_key) is not None:
                        _scan_for_quality[_key] = current_scan_report.get(_key)
            if isinstance(report, dict):
                for _key in (
                    "test_obligations",
                    "experiment_compile",
                    "obligation_plan",
                    "execution_adapters",
                    "behavior_ir",
                    "phases",
                    "v12",
                ):
                    if _key not in _scan_for_quality and report.get(_key) is not None:
                        _scan_for_quality[_key] = report.get(_key)
            _projected = attach_quality_projection_to_scan_result(_scan_for_quality)
            _external = _projected.get("external_evaluation") if isinstance(_projected.get("external_evaluation"), dict) else {}
            _counts = _projected.get("formal_count_projection") if isinstance(_projected.get("formal_count_projection"), dict) else {}
            _classification = (
                _projected.get("finding_classification")
                if isinstance(_projected.get("finding_classification"), dict)
                else {}
            )
            _scope_counts = (
                _projected.get("scope_counts")
                if isinstance(_projected.get("scope_counts"), dict)
                else {}
            )
            _obl = (
                _projected.get("obligation_execution_projection")
                if isinstance(_projected.get("obligation_execution_projection"), dict)
                else {}
            )
            _run_delivery = (
                _projected.get("run_delivery_readiness")
                if isinstance(_projected.get("run_delivery_readiness"), dict)
                else {}
            )
            _commercial_readiness = (
                _projected.get("commercial_readiness")
                if isinstance(_projected.get("commercial_readiness"), dict)
                else {}
            )
            data["external_evaluation"] = _external
            data["commercial_readiness"] = _commercial_readiness
            data["run_delivery_readiness"] = _run_delivery
            data["release_gate"] = _projected.get("release_gate") or {}
            data["formal_count_projection"] = _counts
            data["finding_classification"] = _classification
            data["scope_counts"] = _scope_counts
            data["obligation_execution_projection"] = _obl
            data["quality_claim_status"] = _projected.get("quality_claim_status") or "NOT_MEASURED"
            data["commercial_quality_score"] = _projected.get("commercial_quality_score")
            data["score_semantics"] = _projected.get("score_semantics") or {}
            data["scan_meta"]["external_evaluation"] = _external
            data["scan_meta"]["quality_claim_status"] = data["quality_claim_status"]
            data["scan_meta"]["commercial_quality_score"] = data["commercial_quality_score"]
            data["scan_meta"]["formal_customer_deliverable_count"] = _counts.get("formal_customer_deliverable_count")
            data["scan_meta"]["published_formal_deliverable_count"] = _run_delivery.get(
                "published_formal_deliverable_count"
            )
            data["scan_meta"]["run_delivery_readiness"] = _run_delivery
            data["scan_meta"]["commercial_readiness"] = _commercial_readiness
            data["scan_meta"]["obligation_execution_projection"] = _obl
            # The formal delivery gate is the only source for customer-facing
            # defect lists and counts.  Legacy readiness/campaign counters are
            # retained only as diagnostics below, never as commercial defects.
            _eligible_formal_deliverables = list(_classification.get("deliverable") or [])
            _formal_deliverables = (
                _eligible_formal_deliverables
                if _run_delivery.get("release_ready") is True
                else []
            )
            _candidate_findings = list(_classification.get("candidate") or [])
            _rejected_findings = list(_classification.get("rejected") or [])
            _eligible_formal_count = int(
                _counts.get("formal_customer_deliverable_count") or 0
            )
            _formal_count = int(
                _run_delivery.get("published_formal_deliverable_count") or 0
            )
            _legacy_count_diagnostics = {
                "current_report_readiness_count": data["scan_meta"].get("current_report_customer_ready_defect_count"),
                "current_campaign_readiness_count": data["scan_meta"].get("current_campaign_customer_ready_defect_count"),
                "project_family_readiness_count": data["scan_meta"].get("family_customer_ready_defect_count"),
                "semantics": "diagnostic_only_not_formal_customer_deliverables",
            }
            data["deliverable_findings"] = _formal_deliverables
            data["eligible_formal_deliverable_findings"] = _eligible_formal_deliverables
            data["candidate_findings"] = _candidate_findings
            data["rejected_findings"] = _rejected_findings
            data["defects"] = _formal_deliverables
            data["risks"] = _formal_deliverables
            data["clues"] = _candidate_findings
            data["legacy_count_diagnostics"] = _legacy_count_diagnostics
            data["project_history"] = {
                "measurement_status": "NOT_MEASURED",
                "formal_customer_deliverable_count": len(delivery_defects or []),
                "deliverable_findings": list(delivery_defects or []),
                "note": "Historical shelf is a separate scope and is never merged into current-run formal counts.",
            }
            data["scope_counts"] = {
                **_scope_counts,
                "current_run_formal_deliverable": _eligible_formal_count,
                "current_campaign_formal_deliverable": _eligible_formal_count,
                "current_run_published_formal_deliverable": _formal_count,
                "project_open_formal_deliverable": None,
                "project_open_measurement_status": "NOT_MEASURED",
            }
            for _surface in (data["scan_meta"], data["executive_summary"], data["value_metrics"]):
                _surface["formal_customer_deliverable_count"] = _formal_count
                _surface["current_report_customer_ready_defect_count"] = _formal_count
                _surface["customer_ready_defects"] = _formal_count
                _surface["current_campaign_customer_ready_defect_count"] = _formal_count
                _surface["legacy_count_diagnostics"] = _legacy_count_diagnostics
            data["executive_summary"]["total_bugs_found"] = _formal_count
            data["executive_summary"]["ready_bugs"] = _formal_count
            data["value_metrics"]["defect_count"] = _formal_count
            data["value_metrics"]["ready_bug_count"] = _formal_count
            data["scan_meta"]["ready_bug_count"] = _formal_count
            if _test_task_board and _obl:
                data["test_task_board"]["obligation_execution_projection"] = _obl
            if str(_external.get("measurement_status") or "").upper() != "MEASURED":
                data["executive_summary"]["overall_score"] = None
                data["executive_summary"]["commercial_quality_suppressed"] = True
                data["executive_summary"]["quality_label"] = "尚未完成外部质量评测"
                data["value_metrics"]["commercial_quality_score"] = None
                data["value_metrics"]["quality_claim_status"] = "NOT_MEASURED"
            if isinstance(data.get("scan_meta"), dict) and isinstance(data["scan_meta"].get("benchmark_metrics"), dict):
                data["scan_meta"]["benchmark_metrics"] = suppress_benchmark_quality_when_not_measured(
                    data["scan_meta"]["benchmark_metrics"],
                    _external,
                )
        except MainlineContractError:
            raise
        except Exception as _quality_exc:
            data["external_evaluation"] = {
                "measurement_status": "NOT_MEASURED",
                "reason": f"quality_projection_failed:{type(_quality_exc).__name__}",
                "display": {
                    "quality_label": "尚未完成外部质量评测",
                    "suppress_quality_score": True,
                    "suppress_recall_precision": True,
                },
            }
            data["quality_claim_status"] = "NOT_MEASURED"
            data["commercial_quality_score"] = None
            data["executive_summary"]["overall_score"] = None
            data["executive_summary"]["quality_label"] = "尚未完成外部质量评测"
        return {
            "ok": True,
            "data": data,
        }

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

    @staticmethod
    def _load_db_findings(root: Path, project_id: str) -> list[dict]:
        report = root / "platform_outputs" / project_id / "scan_result.json"
        if not report.exists():
            return []
        data = _read_json_object(report)
        db_verification = data.get("db_verification")
        if db_verification is None:
            return []
        if not isinstance(db_verification, dict):
            raise ValueError(f"db_verification must be an object: {report}")
        db_findings = db_verification.get("findings", [])
        if not isinstance(db_findings, list):
            raise ValueError(f"db_verification.findings must be a list: {report}")
        for index, finding in enumerate(db_findings):
            if not isinstance(finding, dict):
                raise ValueError(f"db_verification.findings[{index}] must be an object: {report}")
            finding.setdefault("risk_type", "db_snapshot")
            finding.setdefault("defect_family", "data_integrity")
            ev_row = finding.get("evidence", {}).get("db_row") if isinstance(finding.get("evidence"), dict) else None
            if isinstance(ev_row, dict) and ev_row:
                for value in ev_row.values():
                    if value is not None and str(value).strip():
                        finding.setdefault("source_value", str(value))
                        break
            description = str(finding.get("description") or "").strip()
            if description:
                finding.setdefault("actual_behavior", description)
        return db_findings

    @staticmethod
    def _load_perf_regressions(root: Path, project_id: str) -> list[dict]:
        perf_file = root / "platform_outputs" / project_id / "performance" / "baseline.json"
        if not perf_file.exists():
            return []
        history = _read_json_artifact(perf_file)
        if not isinstance(history, list):
            raise ValueError(f"performance baseline must be a list: {perf_file}")
        if len(history) < 2:
            return []
        latest = history[-1]
        if not isinstance(latest, dict):
            raise ValueError(f"latest performance baseline must be an object: {perf_file}")
        regressions = latest.get("regressions", [])
        if not isinstance(regressions, list):
            raise ValueError(f"performance regressions must be a list: {perf_file}")
        findings: list[dict[str, Any]] = []
        for index, regression in enumerate(regressions):
            if not isinstance(regression, dict):
                raise ValueError(f"performance regressions[{index}] must be an object: {perf_file}")
            finding = {
                "risk_id": "perf_reg_" + str(regression.get("metric") or "unknown"),
                "title": regression.get("detail", ""),
                "severity": regression.get("severity", "P2"),
                "risk_type": "performance_regression",
                "defect_family": "performance",
                "source": "performance_baseline",
            }
            if regression.get("confidence") is not None:
                finding["confidence_score"] = float(regression["confidence"])
            findings.append(finding)
        return findings

    @staticmethod
    def _load_spectrum_findings(root: Path, project_id: str) -> list[dict]:
        spectrum = root / "platform_outputs" / project_id / "spectrum" / "spectrum_result.json"
        if not spectrum.exists():
            return []
        data = _read_json_object(spectrum)
        capabilities = data.get("capabilities", [])
        if not isinstance(capabilities, list):
            raise ValueError(f"spectrum capabilities must be a list: {spectrum}")
        findings: list[dict[str, Any]] = []
        for cap_index, capability in enumerate(capabilities):
            if not isinstance(capability, dict):
                raise ValueError(f"spectrum capabilities[{cap_index}] must be an object: {spectrum}")
            if capability.get("id") == "test_gen":
                continue
            capability_findings = capability.get("findings", [])
            if not isinstance(capability_findings, list):
                raise ValueError(f"spectrum capability findings must be a list: {spectrum}")
            for finding_index, source_finding in enumerate(capability_findings):
                if not isinstance(source_finding, dict):
                    raise ValueError(f"spectrum finding {cap_index}:{finding_index} must be an object: {spectrum}")
                if not source_finding.get("bug_id"):
                    continue
                finding = {
                    "risk_id": source_finding.get("bug_id", ""),
                    "title": f"[全频谱] {source_finding.get('title', '')}",
                    "severity": source_finding.get("severity", "P2"),
                    "risk_type": f"spectrum_{capability.get('id', 'unknown')}",
                    "defect_family": "spectrum",
                    "summary": str(source_finding.get("description", source_finding.get("title", ""))),
                    "source": "full_spectrum",
                }
                if source_finding.get("confidence") is not None:
                    finding["confidence_score"] = float(source_finding["confidence"])
                findings.append(finding)
        return findings

    @staticmethod
    def _load_spectrum_status_payload(root: Path, project_id: str) -> dict:
        result_file = root / "platform_outputs" / project_id / "spectrum" / "spectrum_result.json"
        ts_file = root / "platform_outputs" / project_id / "spectrum" / "spectrum_timestamp.txt"
        if not result_file.exists():
            return {"status": "not_run", "message": "尚未运行全频谱检测", "summary": {"total_findings": 0}, "capabilities": []}
        result = _read_json_object(result_file)
        capabilities = result.get("capabilities")
        summary = result.get("summary")
        if capabilities is not None and not isinstance(capabilities, list):
            raise ValueError(f"spectrum capabilities must be a list: {result_file}")
        if summary is not None and not isinstance(summary, dict):
            raise ValueError(f"spectrum summary must be an object: {result_file}")
        return {
            "status": "completed",
            "last_run": ts_file.read_text(encoding="utf-8").strip() if ts_file.exists() else "",
            "summary": summary or {"total_findings": 0},
            "capabilities": capabilities or [],
        }

    @staticmethod
    def _load_multi_layer_findings(root: Path, project_id: str) -> list[dict]:
        scan_file = root / "platform_outputs" / project_id / "scan_result.json"
        if not scan_file.exists():
            return []
        data = _read_json_object(scan_file)
        layers = data.get("layers", {})
        if not isinstance(layers, dict):
            raise ValueError(f"scan layers must be an object: {scan_file}")
        findings: list[dict[str, Any]] = []
        for layer_name, layer_data in layers.items():
            if not isinstance(layer_data, dict):
                raise ValueError(f"scan layer {layer_name} must be an object: {scan_file}")
            details = layer_data.get("findings_details", [])
            if not isinstance(details, list):
                raise ValueError(f"scan layer {layer_name}.findings_details must be a list: {scan_file}")
            for index, source_finding in enumerate(details):
                if not isinstance(source_finding, dict):
                    raise ValueError(f"scan layer {layer_name} finding {index} must be an object: {scan_file}")
                finding = {
                    "risk_id": f"layer_{layer_name}_{index}",
                    "title": f"[{str(layer_name).upper()}] {source_finding.get('title', '')}",
                    "severity": source_finding.get("severity", "P2"),
                    "risk_type": f"multi_layer_{layer_name}",
                    "defect_family": "multi_layer",
                    "summary": source_finding.get("description", ""),
                    "source": f"multi_layer_{layer_name}",
                }
                if source_finding.get("confidence") is not None:
                    finding["confidence_score"] = float(source_finding["confidence"])
                findings.append(finding)
        return findings

    def _scan_counter(self, project_id: str, root: Path) -> dict:
        """Track how many times V12 scan has run for this project."""
        import time
        counter_path = root / "platform_outputs" / project_id / "scan_counter.json"
        if counter_path.exists():
            return _read_json_object(counter_path)
        return {"count": 1, "first_scan_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}

    def _auto_discovery_payload(self, project_id: str, root: Path, report: dict[str, Any]) -> dict:
        """Auto-generate continuous discovery payload — tracks convergence across rounds."""
        import time
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        real_findings = report.get("real_findings") or report.get("bug_scores") or []
        if isinstance(real_findings, list):
            real_findings = [item for item in real_findings if isinstance(item, dict)]
        else:
            real_findings = []
        total_findings = len(real_findings)
        raw_total = int(report.get("raw_total") or report.get("total_findings") or total_findings)

        # Track convergence: compare with previous scan findings
        prev_titles = self._previous_finding_titles(project_id, root)
        # Build current titles from report — match the DB storage format
        current_titles = set()
        for f in real_findings:
            t = str(f.get("title") or f.get("description", ""))[:120]
            if t: current_titles.add(t.lower())  # Normalize for matching
        new_count = len(current_titles - prev_titles)
        confirmed_count = len(current_titles & prev_titles)
        resolved_count = max(0, raw_total - total_findings)

        verified = sum(1 for f in real_findings if max(float(f.get("confidence_score", 0)), float(f.get("score", 0)), float(f.get("confidence", 0))) > 0.5)
        blocked = sum(1 for f in real_findings if str(f.get("severity", "")).upper() in ("P0", "CRITICAL"))

        scan_counter = self._scan_counter(project_id, root)
        current_run = scan_counter.get("count", total_findings // 3 or 1)
        total_discovered = len(prev_titles | current_titles)  # All unique findings ever
        pending = max(0, raw_total - total_discovered)

        return {
            "project_id": project_id,
            "generated_at_utc": now,
            "continuous_discovery_campaign": {
                "summary": {
                    "campaign_state": "in_progress",
                    "run_count": current_run,
                    "coverage_ledger_entry_count": raw_total,
                    "validated_frontier_count": confirmed_count + new_count,
                    "remaining_actionable_frontier_count": pending,
                    "blocked_frontier_count": blocked,
                    "revalidation_queue_size": max(0, total_findings - verified),
                    "can_stop_now": pending == 0 and new_count == 0,
                    "frontier_burn_down_count": confirmed_count,
                    "frontier_burn_down_rate": round(confirmed_count / max(1, total_discovered), 2),
                    "current_run_validated_yield": new_count,
                    "marginal_validated_yield_threshold": max(1, raw_total // 5),
                    "new_this_round": new_count,
                    "confirmed": confirmed_count,
                    "total_discovered": total_discovered,
                },
                "coverage_ledger": {
                    "entries": [
                        {"last_status": "validated" if str(f.get("title",""))[:80] in prev_titles else "new",
                         "frontier": {"title": str(f.get("title", f.get("description", "")))[:60]},
                         "last_blocker_reason": ""}
                        for f in real_findings[:10]
                    ],
                    "status_counts": {"validated": confirmed_count, "new": new_count, "blocked": blocked, "pending": pending},
                },
                "recommended_frontier": [
                    {"title": str(f.get("title", f.get("description", "")))[:60],
                     "value_score": int(float(f.get("confidence_score", f.get("score", 1))) * 10)}
                    for f in real_findings[:5]
                ],
            },
            "metrics": {
                "continuous_discovery_coverage": round(total_discovered / max(1, raw_total) * 100, 1),
                "continuous_discovery_total": raw_total,
                "continuous_discovery_verified": total_discovered,
                "continuous_discovery_new": new_count,
                "continuous_discovery_confirmed": confirmed_count,
                "continuous_discovery_blocked": blocked,
                "doc_completeness": self._doc_completeness_score(project_id, root),
            },
        }

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

    def _doc_completeness_score(self, project_id: str, root: Path) -> int:
        """Score 0-100 based on uploaded enterprise documents knowledge richness."""
        try:
            candidates = [
                root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
                root / "platform_outputs" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
                root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
                root / "platform_workspace" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            ]
            for kc_path in candidates:
                if not kc_path.exists():
                    continue
                import json as _jk
                kc = _jk.loads(kc_path.read_text(encoding="utf-8") or "{}")
                raw_sources = kc.get("source_inventory") or kc.get("sources") or kc.get("items") or []
                sources = len(raw_sources) if isinstance(raw_sources, list) else len(raw_sources.keys()) if isinstance(raw_sources, dict) else 0
                rules = len(kc.get("rule_library") or [])
                states = len(kc.get("state_machines") or [])
                roles = len(kc.get("roles") or [])
                interfaces = len(kc.get("interfaces") or [])
                score = min(100, sources * 15 + rules * 8 + states * 5 + roles * 3 + interfaces * 3)
                return max(0, score)
            return 0
        except Exception as e:
            print(f"ERROR in _doc_completeness_score: {e}")
            return 0

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

    # ── Multi-Service Credential Management ──

    def _handle_get_service_credentials(self, project: str, root: Path) -> None:
        """Return current multi-service credential configuration."""
        config_path = root / "platform_workspace" / project / "multi_service_config.json"
        try:
            data = _read_json_object(config_path)
            services = data.get("services", [])
            if not isinstance(services, list) or any(not isinstance(item, dict) for item in services):
                raise ValueError(f"credential config services must be a list of objects: {config_path}")
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "CREDENTIAL_CONFIG_INVALID",
                    "message": str(exc),
                    "project": project,
                },
                500,
            )
        return self._json({"project": project, "services": services})

    def _handle_save_service_credentials(self, project: str, root: Path, body: dict) -> None:
        """Save credentials for a single service, merging into multi_service_config.json."""
        service_data = body.get("service", {})
        if not isinstance(service_data, dict) or not str(service_data.get("name") or "").strip():
            return self._json({"ok": False, "error": "MISSING_NAME",
                              "message": "service.name is required"}, 400)
        previous_name = str(body.get("previous_name") or "").strip()
        config_path = root / "platform_workspace" / project / "multi_service_config.json"
        try:
            config = _read_json_object(config_path)
            services = config.get("services", [])
            if not isinstance(services, list) or any(not isinstance(item, dict) for item in services):
                raise ValueError(f"credential config services must be a list of objects: {config_path}")
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "CREDENTIAL_CONFIG_INVALID",
                    "message": str(exc),
                    "project": project,
                },
                500,
            )
        config.setdefault("services", [])
        config.setdefault("project_name", project)
        config.setdefault("cross_service_contracts", [])
        config.setdefault("external_integrations", [])

        # Upsert: update existing or append new
        name = str(service_data["name"]).strip()
        role_accounts = service_data.get("role_accounts") or []
        if not isinstance(role_accounts, list) or any(not isinstance(account, dict) for account in role_accounts):
            return self._json(
                {"ok": False, "error": "INVALID_ROLE_ACCOUNTS", "message": "service.role_accounts must be a list of objects"},
                400,
            )
        updated = False
        for i, svc in enumerate(config["services"]):
            if svc.get("name") in {name, previous_name}:
                existing = dict(svc)
                previous_auth = svc.get("auth") if isinstance(svc.get("auth"), dict) else {}
                existing["name"] = name
                existing["base_url"] = service_data.get("base_url", "")
                existing["enabled"] = bool(service_data.get("enabled", True))
                # Build auth section — include all role accounts
                auth = {
                    "type": service_data.get("auth_type", "password_login"),
                    "login_api": service_data.get("login_api", "/auth/login"),
                }
                # Multi-role accounts (new)
                for ra in role_accounts:
                    if isinstance(ra, dict) and ra.get("role") and ra.get("username"):
                        role = str(ra["role"])
                        previous_role = previous_auth.get(role) if isinstance(previous_auth.get(role), dict) else {}
                        password = _credential_update_value(ra.get("password"), previous_role.get("password"))
                        auth[role] = {"username": ra["username"]}
                        if password:
                            auth[role]["password"] = password
                # Legacy single admin (backward compat)
                if not role_accounts:
                    if service_data.get("admin_user"):
                        auth.setdefault("admin", {})
                        auth["admin"]["username"] = service_data["admin_user"]
                    previous_admin = previous_auth.get("admin") if isinstance(previous_auth.get("admin"), dict) else {}
                    admin_password = _credential_update_value(service_data.get("admin_pass"), previous_admin.get("password"))
                    if admin_password:
                        auth.setdefault("admin", {})
                        auth["admin"]["password"] = admin_password
                bearer_token = _credential_update_value(service_data.get("bearer_token"), previous_auth.get("bearer_token"))
                if bearer_token:
                    auth["bearer_token"] = bearer_token
                api_key = _credential_update_value(service_data.get("api_key"), previous_auth.get("api_key"))
                if api_key:
                    auth["api_key"] = api_key
                existing["auth"] = auth
                for legacy_key in ("login_api", "auth_type", "admin_user", "admin_pass", "bearer_token", "api_key"):
                    existing.pop(legacy_key, None)

                # Build db section
                if any(service_data.get(k) for k in ("db_host", "db_name")):
                    previous_db = svc.get("db") if isinstance(svc.get("db"), dict) else {}
                    existing["db"] = {
                        "host": service_data.get("db_host", ""),
                        "port": int(service_data.get("db_port", 3306)),
                        "name": service_data.get("db_name", ""),
                        "user": service_data.get("db_user", ""),
                        "password": _credential_update_value(service_data.get("db_pass"), previous_db.get("password")),
                    }
                else:
                    existing.pop("db", None)
                config["services"][i] = existing
                updated = True
                break

        if not updated:
            auth = {
                "type": service_data.get("auth_type", "password_login"),
                "login_api": service_data.get("login_api", "/auth/login"),
            }
            # Multi-role accounts
            for ra in role_accounts:
                if isinstance(ra, dict) and ra.get("role") and ra.get("username"):
                    role = str(ra["role"])
                    auth[role] = {"username": ra["username"]}
                    password = _credential_update_value(ra.get("password"))
                    if password:
                        auth[role]["password"] = password
            # Legacy single admin fallback
            if not role_accounts and service_data.get("admin_user"):
                auth["admin"] = {
                    "username": service_data["admin_user"],
                    "password": service_data.get("admin_pass", ""),
                }
            bearer_token = _credential_update_value(service_data.get("bearer_token"))
            if bearer_token:
                auth["bearer_token"] = bearer_token
            api_key = _credential_update_value(service_data.get("api_key"))
            if api_key:
                auth["api_key"] = api_key
            svc = {
                "name": name, "base_url": service_data.get("base_url", ""),
                "enabled": service_data.get("enabled", True),
                "description": "", "depends_on": [], "exposes_to": [],
                "auth": auth,
            }
            if any(service_data.get(k) for k in ("db_host", "db_name")):
                svc["db"] = {
                    "host": service_data.get("db_host", ""),
                    "port": int(service_data.get("db_port", 3306)),
                    "name": service_data.get("db_name", ""),
                    "user": service_data.get("db_user", ""),
                    "password": _credential_update_value(service_data.get("db_pass")),
                }
            config["services"].append(svc)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        # Encrypt sensitive credential fields before writing to disk so that
        # secrets are not stored in plaintext in multi_service_config.json.
        from .credential_crypto import encrypt as _enc_secret, is_encrypted as _is_enc
        for _svc in config.get("services", []):
            if not isinstance(_svc, dict):
                continue
            _auth = _svc.get("auth")
            if isinstance(_auth, dict):
                for _role_cfg in _auth.values():
                    if isinstance(_role_cfg, dict):
                        _pw = _role_cfg.get("password")
                        if _pw and not _is_enc(_pw):
                            _role_cfg["password"] = _enc_secret(_pw)
                for _field in ("bearer_token", "api_key"):
                    _val = _auth.get(_field)
                    if _val and not _is_enc(_val):
                        _auth[_field] = _enc_secret(_val)
            _db_cfg = _svc.get("db")
            if isinstance(_db_cfg, dict):
                _db_pw = _db_cfg.get("password")
                if _db_pw and not _is_enc(_db_pw):
                    _db_cfg["password"] = _enc_secret(_db_pw)
        _write_json_object_atomic(config_path, config)

        # Reload credentials and perform a real password-login health check.
        # Static bearer/API-key configuration remains unverified because the
        # credential manager cannot prove it against a protected endpoint.
        try:
            from .enterprise_credential_manager import EnterpriseCredentialManager
            mgr = EnterpriseCredentialManager(project, root)
            mgr.load_from_file(config_path)
            mgr.load_from_env()
            login_results = mgr.login_all_services()
            if not isinstance(login_results, dict):
                raise TypeError("credential login results must be an object")
            auth_roles = login_results.get(name) or {}
            if not isinstance(auth_roles, dict) or any(type(ok) is not bool for ok in auth_roles.values()):
                raise ValueError("credential role health results must be boolean values")
            target_service = next(
                (item for item in config["services"] if isinstance(item, dict) and item.get("name") == name),
                None,
            )
            if target_service is None:
                raise ValueError(f"saved service missing from credential config: {name}")
            target_auth = target_service.get("auth") if isinstance(target_service.get("auth"), dict) else {}
            role_credentials = [
                value
                for value in target_auth.values()
                if isinstance(value, dict) and (value.get("username") or value.get("password"))
            ]
            static_token_only = bool(target_auth.get("bearer_token") or target_auth.get("api_key")) and not role_credentials
            verified = bool(auth_roles) and all(ok is True for ok in auth_roles.values()) and not static_token_only
            auth_check = {
                "service": name,
                "roles": auth_roles,
                "all_ok": verified,
                "status": "verified" if verified else "configured_unverified" if static_token_only else "failed",
            }
            if static_token_only:
                auth_check["reason"] = "static credential configured without a live protected-endpoint health check"
        except Exception as exc:
            auth_check = {
                "service": name,
                "roles": {},
                "all_ok": False,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

        target_service = next(
            (item for item in config["services"] if isinstance(item, dict) and item.get("name") == name),
            None,
        )
        if target_service is None:
            raise ValueError(f"saved service missing from credential config: {name}")
        target_service["auth_check"] = auth_check
        _write_json_object_atomic(config_path, config)

        if not auth_check["all_ok"]:
            _write_json_object_atomic(
                root / "platform_outputs" / project / "credential_verification_last_error.json",
                {
                    "schema": "qualibug.credential-verification-failure.v1",
                    "project": project,
                    "service": name,
                    "status": auth_check["status"],
                    "error_type": auth_check.get("error_type", ""),
                    "error": auth_check.get("error") or auth_check.get("reason") or "credential health check failed",
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            return self._json({
                "ok": False,
                "saved": True,
                "error": "CREDENTIAL_VERIFICATION_FAILED",
                "service": name,
                "services_count": len(config["services"]),
                "auth_check": auth_check,
            }, 207)

        return self._json({
            "ok": True,
            "saved": True,
            "service": name,
            "services_count": len(config["services"]),
            "auth_check": auth_check,
        })

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

_CONTINUOUS_STATE_FILE = "continuous_discovery_state.json"

# In-memory tracking of active continuous-scan threads per project.
# Key: (root, project_id), Value: dict with thread + stop flag.
_continuous_threads: dict[tuple[str, str], dict[str, Any]] = {}


def _continuous_scan_loop(root: Path, project: str, tenant_id: str, interval_s: int) -> None:
    """Background loop: run scans at intervals until convergence or stop.

    Convergence = consecutive N rounds with zero new findings AND coverage
    above threshold. Once converged, the loop auto-stops and records the
    reason so the UI can show "覆盖收敛，自动暂停".
    """
    import time as _time
    from .__main__ import scan as _scan_fn

    key = (str(root), project)
    no_new_rounds = 0
    CONVERGE_ROUNDS = 3  # 连续3轮无新发现视为收敛
    CONVERGE_COVERAGE = 0.7
    MAX_ROUNDS = 20  # 安全上限，防止无限循环

    for round_num in range(1, MAX_ROUNDS + 1):
        # Check stop flag
        entry = _continuous_threads.get(key)
        if not entry or entry.get("stop"):
            break

        phase = "scan"
        try:
            # Run scan
            result = _scan_fn(project, root, save_report=True)
            if not isinstance(result, dict):
                raise TypeError("continuous scan result must be an object")

            # Cumulative merge
            phase = "cumulative_merge"
            db_persist.init_db(root)
            report_path = root / "platform_outputs" / project / "intelligence_report.json"
            report_data = _read_json_object(report_path)
            findings_value = report_data.get("real_findings") or report_data.get("bug_scores") or []
            if not isinstance(findings_value, list):
                raise ValueError(f"continuous report findings must be a list: {report_path}")
            if any(not isinstance(finding, dict) for finding in findings_value):
                raise ValueError(f"continuous report findings must contain objects: {report_path}")
            findings_list = list(findings_value)
            enriched = dict(result)
            enriched["findings"] = findings_list
            scan_id = db_persist.save_scan(root, tenant_id, project, enriched)
            merge_result = db_persist.merge_findings_cumulative(root, tenant_id, project, scan_id, findings_list)
            if not isinstance(merge_result, dict):
                raise TypeError("cumulative merge result must be an object")
            new_count = int(merge_result.get("new") or 0)

            # Update continuous state
            phase = "state_update"
            _update_continuous_state(root, project, result)

            # Convergence check
            if new_count == 0:
                no_new_rounds += 1
            else:
                no_new_rounds = 0

            phase = "convergence"
            coverage = float(result.get("coverage", 0) or 0)
            converged = no_new_rounds >= CONVERGE_ROUNDS and coverage >= CONVERGE_COVERAGE

            # Update thread entry with progress
            if key in _continuous_threads:
                _continuous_threads[key]["round"] = round_num
                _continuous_threads[key]["last_new"] = new_count
                _continuous_threads[key]["no_new_rounds"] = no_new_rounds
                if converged:
                    _continuous_threads[key]["converged"] = True
                    _continuous_threads[key]["stop"] = True
                    # Mark state as converged
                    _mark_continuous_converged(root, project, reason="连续{}轮无新发现且覆盖率≥{:.0%}".format(CONVERGE_ROUNDS, CONVERGE_COVERAGE))
                    break
        except Exception as exc:
            if key in _continuous_threads:
                _continuous_threads[key].update({
                    "status": "failed",
                    "failed_phase": phase,
                    "failed_round": round_num,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "stop": True,
                })
            _continuous_threads.pop(key, None)
            _record_continuous_failure(root, project, round_num=round_num, phase=phase, error=exc)
            raise

        # Wait for next interval (check stop flag every second)
        for _ in range(interval_s):
            entry = _continuous_threads.get(key)
            if not entry or entry.get("stop"):
                break
            _time.sleep(1)
    else:
        try:
            _mark_continuous_max_rounds(root, project, max_rounds=MAX_ROUNDS)
        finally:
            _continuous_threads.pop(key, None)
        return

    # Clean up thread entry
    _continuous_threads.pop(key, None)


def _continuous_state_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / project / "defect_discovery" / _CONTINUOUS_STATE_FILE


def _record_continuous_failure(
    root: Path,
    project: str,
    *,
    round_num: int,
    phase: str,
    error: Exception,
) -> None:
    state_file = _continuous_state_path(root, project)
    failure = {
        "project": project,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "round": round_num,
        "phase": phase,
        "error_type": type(error).__name__,
        "error": str(error),
    }
    _write_json_object_atomic(
        state_file.with_name("continuous_discovery_last_error.json"),
        failure,
    )
    state = _read_json_object(state_file)
    state["status"] = "failed"
    state["converged"] = False
    state.pop("converge_reason", None)
    state.pop("termination", None)
    state["last_failure"] = failure
    _write_json_object_atomic(state_file, state)


def _mark_continuous_converged(root: Path, project: str, reason: str) -> None:
    """Mark the continuous discovery state as converged with a reason."""
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)
    state["status"] = "converged"
    state["converged"] = True
    state["converge_reason"] = reason
    state.pop("last_failure", None)
    state.pop("termination", None)
    _write_json_object_atomic(state_file, state)


def _mark_continuous_max_rounds(root: Path, project: str, *, max_rounds: int) -> None:
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)
    state["status"] = "max_rounds_reached"
    state["converged"] = False
    state.pop("converge_reason", None)
    state["termination"] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason_code": "MAX_ROUNDS_REACHED",
        "round": max_rounds,
    }
    _write_json_object_atomic(state_file, state)


def _update_continuous_state(root: Path, project: str, scan_result: dict) -> None:
    """Track continuous discovery coverage state after each auto-scan."""
    state_dir = root / "platform_workspace" / project / "defect_discovery"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)

    total_findings = scan_result.get("total_findings", 0)
    coverage = scan_result.get("coverage", 0)
    grade = scan_result.get("grade", "C")
    total_ms = scan_result.get("total_ms", 0)

    # Track scan runs
    runs = state.get("runs", [])
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError(f"continuous discovery runs must be a list of objects: {state_file}")
    runs.append({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "findings": total_findings,
        "coverage": coverage,
        "grade": grade,
        "duration_ms": total_ms,
    })
    # Keep last 50 runs
    runs = runs[-50:]

    state["runs"] = runs
    state["status"] = "scanning"
    state["converged"] = False
    state["last_scan"] = runs[-1]["timestamp"] if runs else ""
    state["total_runs"] = len(runs)
    state.pop("converge_reason", None)
    state.pop("termination", None)
    state.pop("last_failure", None)

    _write_json_object_atomic(state_file, state)


def _get_continuous_state(root: Path, project: str) -> dict[str, Any]:
    """Get the current continuous discovery state."""
    state_file = root / "platform_workspace" / project / "defect_discovery" / _CONTINUOUS_STATE_FILE
    if not state_file.exists():
        return {
            "status": "idle",
            "converged": False,
            "runs": [],
            "total_runs": 0,
            "message": "尚未运行过持续检测。上传文档后将自动开始。"
        }
    state = _read_json_object(state_file)
    runs = state.get("runs", [])
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError(f"continuous discovery runs must be a list of objects: {state_file}")
    last_failure = state.get("last_failure")
    if last_failure is not None and not isinstance(last_failure, dict):
        raise ValueError(f"continuous discovery last_failure must be an object: {state_file}")
    termination = state.get("termination")
    if termination is not None and not isinstance(termination, dict):
        raise ValueError(f"continuous discovery termination must be an object: {state_file}")
    last_run = runs[-1] if runs else {}
    status = str(state.get("status") or "idle")
    if status == "failed" and last_failure:
        message = (
            f"持续检测失败（阶段 {last_failure.get('phase') or 'unknown'}，"
            f"第 {last_failure.get('round') or 0} 轮）：{last_failure.get('error') or 'unknown error'}"
        )
    elif status == "max_rounds_reached" and termination:
        message = f"持续检测已达到 {termination.get('round') or 0} 轮安全上限，未判定为收敛。"
    elif state.get("converged"):
        message = "持续检测覆盖已收敛，系统自动暂停。上传新文档后将自动恢复。"
    elif runs:
        message = "持续检测进行中，系统检测到新的覆盖空间。"
    else:
        message = "等待首次扫描..."
    return {
        "status": status,
        "converged": state.get("converged", False),
        "runs": runs[-10:],
        "total_runs": state.get("total_runs", len(runs)),
        "last_scan": state.get("last_scan", ""),
        "last_findings": last_run.get("findings", 0),
        "last_coverage": last_run.get("coverage", 0),
        "last_failure": last_failure or {},
        "termination": termination or {},
        "message": message,
    }


if __name__ == "__main__":
    raise SystemExit(
        "Unsupported launch path. Use qualibug-server "
        "(ai_test_asset_center.private_pilot_entrypoint:run_server)."
    )
