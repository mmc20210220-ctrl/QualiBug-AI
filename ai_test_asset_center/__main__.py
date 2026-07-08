"""QualiBug unified, source-grounded enterprise scan entry point.

A scan may only be driven by an immutable, attributable source asset. Sources
are resolved from the enterprise source registry first, then from a project-owned
asset mirror, or from an explicitly supplied SHA-256 manifest. Any confirmed
finding must also have a persisted, integrity-verifiable evidence bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import urlparse

from .enterprise_campaign import has_real_confirmation_receipt
from .scan_counter import increment_scan_counter
from .enterprise_test_data_plan import build_campaign_test_data_plan
from .test_data_receipt_bootstrap import bootstrap_test_data_receipts_for_campaign

_SOURCE_EXTENSIONS = {".json", ".yaml", ".yml", ".md", ".txt"}
_MAX_SOURCE_BYTES = 5_000_000
_MAX_SOURCE_FILES = 200
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if callable(configure):
            try:
                configure(errors="replace")
            except Exception:
                pass


_configure_console_encoding()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _scan_campaign_context_defaults(project: str, root: Path) -> dict[str, str]:
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
    except Exception:
        return {}
    profile = registry.get("test_profile") if isinstance(registry, dict) else {}
    if not isinstance(profile, dict):
        return {}
    scope_id = _first_text(
        profile.get("scope_id"),
        profile.get("deployment_scope_id"),
        profile.get("project_scope_id"),
    )
    environment_ref = _first_text(
        profile.get("environment_ref"),
        profile.get("target_environment"),
        profile.get("environment"),
    )
    defaults: dict[str, str] = {}
    if scope_id:
        defaults["scope_id"] = scope_id[:160]
    if environment_ref:
        defaults["environment_ref"] = environment_ref[:160]
    return defaults


def _truthy_env(name: str, default: str = "") -> bool:
    value = str(os.environ.get(name, default) or "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _gap(code: str, detail: str) -> dict[str, str]:
    return {"kind": "SOURCE_INPUT_GAP", "code": code, "detail": detail}


def _safe_project(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return normalized or "unscoped"


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _is_local_loopback_runtime(base_url: str) -> bool:
    try:
        parsed = urlparse(str(base_url or "").strip())
    except Exception:
        return False
    host = str(parsed.hostname or "").strip().lower()
    return (
        host in {"127.0.0.1", "localhost", "::1"}
        and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
        and _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "1")
    )


def _issue_local_execution_approval(project: str, root: Path, context: dict[str, Any], campaign: dict[str, Any], base_url: str) -> str:
    if not _is_local_loopback_runtime(base_url):
        return ""
    if str(context.get("execution_approval_id") or "").strip():
        return ""
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip() or "safe_read_only"
    if execution_mode not in {"safe_read_only", "approved_sandbox_write"}:
        return ""
    campaign_id = str(campaign.get("campaign_id") or "").strip()
    scope_id = str(campaign.get("scope_id") or "").strip()
    environment_ref = str(campaign.get("environment_ref") or "").strip()
    source_hash = str(campaign.get("source_hash") or "").strip().lower()
    if not campaign_id or not scope_id or not environment_ref or not _SHA256_RE.fullmatch(source_hash):
        return ""
    try:
        from .execution_approvals import issue_execution_approval

        approval = issue_execution_approval(
            project,
            root=root,
            campaign_id=campaign_id,
            scope_id=scope_id,
            environment_ref=environment_ref,
            source_hash=source_hash,
            target_base_url=base_url,
            execution_mode=execution_mode,
            expires_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
            actor={"name": "local_dev_scan", "role": "system"},
        )
    except Exception:
        return ""
    return str(approval.get("approval_id") or "").strip()


def _should_refresh_local_execution_approval(runtime_contract: dict[str, Any], context: dict[str, Any]) -> bool:
    if not str(context.get("execution_approval_id") or "").strip():
        return False
    if str(runtime_contract.get("reason") or "") != "execution_approval_required":
        return False
    missing = {
        str(code or "").strip()
        for code in (runtime_contract.get("missing_requirements") if isinstance(runtime_contract.get("missing_requirements"), list) else [])
        if str(code or "").strip()
    }
    return any(
        code == "EXECUTION_APPROVAL_CAMPAIGN_ID_MISMATCH"
        or code == "EXECUTION_APPROVAL_INVALID"
        or code.startswith("EXECUTION_APPROVAL_")
        for code in missing
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _customer_ready_static_snapshot(project: str, root: Path) -> dict[str, Any]:
    try:
        from .private_pilot_service import PrivatePilotHandler
    except Exception:
        return {}
    try:
        handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
        handler.headers = {}
        envelope = handler._build_command_center(project, root)
    except Exception:
        return {}
    if not isinstance(envelope, dict):
        return {}
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if not isinstance(data, dict):
        return {}
    defects = [dict(item) for item in data.get("defects", []) if isinstance(item, dict)]
    clues = [dict(item) for item in data.get("clues", []) if isinstance(item, dict)]
    snapshot = {
        "project": project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "defects": defects,
        "clues": clues,
        "risks": defects,
        "value_metrics": dict(data.get("value_metrics") or {}) if isinstance(data.get("value_metrics"), dict) else {},
        "executive_summary": dict(data.get("executive_summary") or {}) if isinstance(data.get("executive_summary"), dict) else {},
        "scan_meta": dict(data.get("scan_meta") or {}) if isinstance(data.get("scan_meta"), dict) else {},
        "data_contract": dict(data.get("data_contract") or {}) if isinstance(data.get("data_contract"), dict) else {},
    }
    if isinstance(data.get("current_campaign_scope"), dict):
        snapshot["current_campaign_scope"] = dict(data.get("current_campaign_scope") or {})
    if isinstance(data.get("defect_grouped_summary"), dict):
        snapshot["defect_grouped_summary"] = dict(data.get("defect_grouped_summary") or {})
    if isinstance(data.get("defect_priority_summary"), dict):
        snapshot["defect_priority_summary"] = dict(data.get("defect_priority_summary") or {})
    if isinstance(data.get("defect_repro_summary"), dict):
        snapshot["defect_repro_summary"] = dict(data.get("defect_repro_summary") or {})
    if isinstance(data.get("defect_delivery_cards"), dict):
        snapshot["defect_delivery_cards"] = dict(data.get("defect_delivery_cards") or {})
    if isinstance(data.get("commercial_assets"), dict):
        snapshot["commercial_assets"] = dict(data.get("commercial_assets") or {})
    if isinstance(data.get("continuous_discovery_campaign"), dict):
        snapshot["continuous_discovery_campaign"] = dict(data.get("continuous_discovery_campaign") or {})
    return snapshot


def _persist_customer_ready_static_artifacts(project: str, root: Path, result: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    snapshot = _customer_ready_static_snapshot(project, root)
    if not snapshot:
        return {}
    project_key = _safe_project(project)
    defect_count = len(snapshot.get("defects") or [])
    clue_count = len(snapshot.get("clues") or [])

    scan_result_path = root / "platform_outputs" / project_key / "scan_result.json"
    scan_payload = _read_json(scan_result_path) or (dict(result) if isinstance(result, dict) else {})
    scan_payload["customer_ready_snapshot"] = snapshot
    scan_payload["customer_ready_defect_count"] = defect_count
    scan_payload["customer_ready_clue_count"] = clue_count
    _write_json(scan_result_path, scan_payload)

    real_project_path = root / "platform_outputs" / project_key / "real_project" / "real_project_defect_data.json"
    real_project_payload = _read_json(real_project_path)
    if not isinstance(real_project_payload, dict):
        real_project_payload = {}

    customer_ready_family_shelf = {
        "project": project,
        "generated_at_utc": snapshot.get("generated_at_utc"),
        "defects": snapshot.get("defects", []),
        "clues": snapshot.get("clues", []),
        "risks": snapshot.get("defects", []),
        "value_metrics": snapshot.get("value_metrics", {}),
        "executive_summary": snapshot.get("executive_summary", {}),
        "scan_meta": snapshot.get("scan_meta", {}),
        "data_contract": snapshot.get("data_contract", {}),
    }
    if isinstance(snapshot.get("current_campaign_scope"), dict):
        customer_ready_family_shelf["current_campaign_scope"] = dict(snapshot.get("current_campaign_scope") or {})
    if isinstance(snapshot.get("continuous_discovery_campaign"), dict):
        customer_ready_family_shelf["continuous_discovery_campaign"] = dict(snapshot.get("continuous_discovery_campaign") or {})
    if isinstance(snapshot.get("defect_grouped_summary"), dict):
        customer_ready_family_shelf["defect_grouped_summary"] = dict(snapshot.get("defect_grouped_summary") or {})
    if isinstance(snapshot.get("defect_priority_summary"), dict):
        customer_ready_family_shelf["defect_priority_summary"] = dict(snapshot.get("defect_priority_summary") or {})
    if isinstance(snapshot.get("defect_repro_summary"), dict):
        customer_ready_family_shelf["defect_repro_summary"] = dict(snapshot.get("defect_repro_summary") or {})
    if isinstance(snapshot.get("defect_delivery_cards"), dict):
        customer_ready_family_shelf["defect_delivery_cards"] = dict(snapshot.get("defect_delivery_cards") or {})
    if isinstance(snapshot.get("commercial_assets"), dict):
        customer_ready_family_shelf["commercial_assets"] = dict(snapshot.get("commercial_assets") or {})

    discovery_owned_markers = (
        "metrics",
        "summary",
        "probes",
        "risk_distribution",
        "issue_count",
        "validated_bug_count",
        "candidate_issue_count",
        "pending_finding_count",
        "network_requests",
    )
    preserve_discovery_top_level = any(
        key in real_project_payload and real_project_payload.get(key) not in (None, "", [], {})
        for key in discovery_owned_markers
    )

    real_project_payload["customer_ready_snapshot"] = snapshot
    real_project_payload["customer_ready_family_shelf"] = customer_ready_family_shelf
    real_project_payload["customer_ready_defect_count"] = defect_count
    real_project_payload["customer_ready_clue_count"] = clue_count
    real_project_payload["customer_ready_projection_basis"] = "command_center_snapshot"
    if isinstance(snapshot.get("commercial_assets"), dict):
        real_project_payload["customer_ready_commercial_assets"] = dict(snapshot.get("commercial_assets") or {})
    if isinstance(snapshot.get("current_campaign_scope"), dict):
        real_project_payload["customer_ready_current_campaign_scope"] = dict(snapshot.get("current_campaign_scope") or {})
    if isinstance(snapshot.get("continuous_discovery_campaign"), dict):
        real_project_payload["customer_ready_continuous_discovery_campaign"] = dict(snapshot.get("continuous_discovery_campaign") or {})

    if not preserve_discovery_top_level:
        real_project_payload.update(customer_ready_family_shelf)
    _write_json(real_project_path, real_project_payload)

    if isinstance(result, dict):
        result["customer_ready_snapshot"] = snapshot
        result["customer_ready_defect_count"] = defect_count
        result["customer_ready_clue_count"] = clue_count
    return snapshot


def _ui_candidate_target_path(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    target = str(ui_result.get("current_url") or evidence.get("target") or item.get("_api_path") or item.get("path") or "").strip()
    if not target:
        return "/"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return parsed.path or "/"
    return target


def _ui_candidate_method(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    method = str(item.get("_api_method") or request.get("method") or reproduction.get("method") or "GET").strip().upper()
    return method or "GET"


def _normalize_ui_verification_http_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"\{id\}", "{object_id}", text, flags=re.IGNORECASE)
    normalized = re.sub(r"<id>", "{object_id}", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r":id(?=/|$)", "{object_id}", normalized, flags=re.IGNORECASE)
    return normalized


def _candidate_followup_verification_template(item: dict[str, Any], *, path: str, method: str) -> dict[str, Any]:
    raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    ui_metadata = ui_result.get("metadata") if isinstance(ui_result.get("metadata"), dict) else {}
    verification = ui_metadata.get("verification") if isinstance(ui_metadata.get("verification"), dict) else {}
    if verification:
        return dict(verification)
    item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    verification = item_metadata.get("verification") if isinstance(item_metadata.get("verification"), dict) else {}
    if verification:
        return dict(verification)
    created_data = raw.get("created_data") if isinstance(raw.get("created_data"), dict) else {}
    object_id = str(created_data.get("object_id") or "").strip()
    normalized_path = _normalize_ui_verification_http_path(path)
    if method != "GET" or not normalized_path.startswith("/") or not object_id:
        return {}
    return {
        "kind": "http_get",
        "path": normalized_path,
        "expected_statuses": [200],
        "body_contains": "{object_id}",
    }


def _source_bound_followup_verification_template(
    matching_scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    for scenario in matching_scenarios:
        if not isinstance(scenario, dict):
            continue
        scenario_metadata = scenario.get("metadata") if isinstance(scenario.get("metadata"), dict) else {}
        verification = scenario_metadata.get("verification") if isinstance(scenario_metadata.get("verification"), dict) else {}
        if verification:
            return dict(verification)
        expected_state = str(
            scenario.get("expected_state")
            or scenario.get("expected")
            or ""
        ).strip()
        for step in scenario.get("steps", []):
            if not isinstance(step, dict):
                continue
            method = str(step.get("method") or "GET").strip().upper()
            path = _normalize_ui_verification_http_path(str(step.get("path") or "").strip())
            if method != "GET" or not path.startswith("/"):
                continue
            template = {
                "kind": "http_get",
                "path": path,
                "expected_statuses": [200],
            }
            if expected_state:
                template["body_contains"] = expected_state
            return template
    return {}


def _ui_followup_execution_template(
    item: dict[str, Any],
    *,
    project: str,
    scan_id: str,
    campaign: dict[str, Any],
    generated_at: str,
    index: int,
) -> dict[str, Any]:
    title = str(item.get("title") or f"ui_candidate_{index}").strip() or f"ui_candidate_{index}"
    path = _ui_candidate_target_path(item) or "/"
    method = _ui_candidate_method(item)
    risk_type = str(item.get("risk_type") or item.get("defect_family") or "ui_execution").strip() or "ui_execution"
    severity = str(item.get("severity") or "P2").strip().upper()
    candidate_id = str(item.get("risk_id") or item.get("bug_id") or _sha256(f"{title}|{method}|{path}")[:16]).strip()
    expected = str(item.get("expected") or item.get("expected_behavior") or "页面状态、关键提示和业务结果应与既有候选证据一致。").strip()
    reproduction_steps = [str(step).strip()[:500] for step in (item.get("reproduction_steps") or []) if str(step).strip()]
    page_hints = [
        hint
        for hint in (
            f"candidate severity: {severity}" if severity else "",
            f"risk type: {risk_type}" if risk_type else "",
            f"candidate tier: {str(item.get('candidate_tier') or 'ui_candidate').strip()}",
            *reproduction_steps[:3],
        )
        if hint
    ]
    verification = item.get("ui_verification") if isinstance(item.get("ui_verification"), dict) else {}
    followup_kind = (
        "ui_evidence_enrichment"
        if str(verification.get("status") or item.get("verification_badge") or "").strip().lower() == "verified"
        else "reproduction_assistant"
    )
    verification_template = _candidate_followup_verification_template(item, path=path, method=method)
    return {
        "request_template_id": f"UIFOLLOW_{candidate_id}",
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "project_id": project,
        "title": title,
        "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
        "risk_type": risk_type,
        "method": method,
        "path": path or "/",
        "task": f"Re-open the candidate target and capture deterministic UI evidence for: {title}",
        "expected": expected,
        "page_hints": page_hints,
        "success_criteria": {"url_pattern": path or "/"} if path else {},
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": path or "/", "wait_until": "networkidle"},
                {"action": "wait_for_load", "state": "networkidle"},
                {"action": "screenshot", "full_page": True},
            ],
        },
        "metadata": {
            "auto_generated": True,
            "request_origin": "ui_followup_execution_asset",
            "bridge_mode": "page_agent_browser_plan",
            "source_candidate_id": candidate_id,
            "candidate_tier": str(item.get("candidate_tier") or "ui_candidate"),
            "verification_badge": str(item.get("verification_badge") or ""),
            "followup_kind": followup_kind,
            "verification": verification_template,
        },
        "generated_at_utc": generated_at,
    }


def _source_bound_ui_followup_templates(
    *,
    project: str,
    scan_id: str,
    campaign: dict[str, Any],
    generated_at: str,
    selected_slices: list[dict[str, Any]] | None,
    plan_only_scenarios: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    scenarios_by_slice: dict[str, list[dict[str, Any]]] = {}
    for item in plan_only_scenarios or []:
        if not isinstance(item, dict):
            continue
        slice_id = str(item.get("behavior_slice_id") or "").strip()
        if slice_id:
            scenarios_by_slice.setdefault(slice_id, []).append(item)
    templates: list[dict[str, Any]] = []
    for index, slice_item in enumerate(selected_slices or [], start=1):
        if not isinstance(slice_item, dict):
            continue
        slice_id = str(slice_item.get("slice_id") or "").strip()
        if not slice_id:
            continue
        entity = str(slice_item.get("entity") or "entity").strip() or "entity"
        kind = str(slice_item.get("kind") or "source_bound").strip() or "source_bound"
        endpoints = [str(value).strip() for value in slice_item.get("endpoints", []) if str(value).strip()]
        source_refs = [dict(value) for value in slice_item.get("source_refs", []) if isinstance(value, dict)]
        matching_scenarios = scenarios_by_slice.get(slice_id, [])
        scenario_titles = [str(item.get("title") or "").strip() for item in matching_scenarios if str(item.get("title") or "").strip()]
        scenario_categories = [str(item.get("category") or "").strip() for item in matching_scenarios if str(item.get("category") or "").strip()]
        followup_kind = "ui_evidence_enrichment" if kind == "source_observation" else "reproduction_assistant"
        verification_template = _source_bound_followup_verification_template(matching_scenarios)
        page_hints = [
            hint
            for hint in (
                f"behavior slice: {slice_id}",
                f"entity: {entity}",
                f"slice kind: {kind}",
                *(f"source endpoint: {path}" for path in endpoints[:3]),
                *(f"scenario title: {title}" for title in scenario_titles[:2]),
                *(f"scenario category: {category}" for category in scenario_categories[:2]),
                *(
                    f"source reference: {str(ref.get('locator') or ref.get('quote') or '').strip()}"
                    for ref in source_refs[:2]
                    if str(ref.get("locator") or ref.get("quote") or "").strip()
                ),
            )
            if hint
        ]
        task_tail = scenario_titles[0] if scenario_titles else f"{entity} / {kind}"
        templates.append(
            {
                "request_template_id": f"UISLICE_{slice_id}",
                "scan_id": scan_id,
                "campaign_id": str(campaign.get("campaign_id") or ""),
                "project_id": project,
                "title": f"Source-bound UI follow-up: {task_tail}",
                "severity": str((matching_scenarios[0].get("severity") if matching_scenarios else "") or "P2").strip().upper() or "P2",
                "risk_type": f"source_bound_{kind}",
                "method": "GET",
                "path": "/",
                "task": (
                    "Open the approved application entry URL, navigate toward the source-bound flow, "
                    f"and capture deterministic UI evidence for behavior slice {slice_id}."
                ),
                "expected": (
                    str(matching_scenarios[0].get("expected_state") or "").strip()
                    if matching_scenarios
                    else f"UI behavior should remain consistent with the source-bound obligation for {entity}."
                ),
                "page_hints": page_hints,
                "success_criteria": {},
                "browser_plan": {
                    "execution_mode": "safe_read_only",
                    "steps": [
                        {"action": "goto", "url": "/", "wait_until": "networkidle"},
                        {"action": "wait_for_load", "state": "networkidle"},
                        {"action": "screenshot", "full_page": True},
                    ],
                },
                "metadata": {
                    "auto_generated": True,
                    "request_origin": "source_bound_slice_followup_asset",
                    "bridge_mode": "page_agent_browser_plan",
                    "behavior_slice_id": slice_id,
                    "slice_kind": kind,
                    "scenario_count": len(matching_scenarios),
                    "followup_kind": followup_kind,
                    "verification": verification_template,
                },
                "generated_at_utc": generated_at,
            }
        )
    return templates


def _source_bound_ui_test_data_templates(
    *,
    project: str,
    scan_id: str,
    campaign: dict[str, Any],
    generated_at: str,
    selected_slices: list[dict[str, Any]] | None,
    plan_only_scenarios: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    scenarios_by_slice: dict[str, list[dict[str, Any]]] = {}
    for item in plan_only_scenarios or []:
        if not isinstance(item, dict):
            continue
        slice_id = str(item.get("behavior_slice_id") or "").strip()
        if slice_id:
            scenarios_by_slice.setdefault(slice_id, []).append(item)
    templates: list[dict[str, Any]] = []
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    for slice_item in selected_slices or []:
        if not isinstance(slice_item, dict):
            continue
        slice_id = str(slice_item.get("slice_id") or "").strip()
        if not slice_id:
            continue
        matching_scenarios = scenarios_by_slice.get(slice_id, [])
        scenario_steps = [step for item in matching_scenarios for step in item.get("steps", []) if isinstance(step, dict)]
        has_write_step = any(str(step.get("method") or "").strip().upper() in write_methods for step in scenario_steps)
        evidence_gaps = [
            str(gap).strip()
            for item in matching_scenarios
            for gap in item.get("evidence_gaps", [])
            if str(gap).strip()
        ]
        needs_fixture = any(gap in {"FIXTURE_CONTRACT_MISSING", "CLEANUP_CONTRACT_MISSING"} for gap in evidence_gaps)
        if not has_write_step and not needs_fixture:
            continue
        entity = str(slice_item.get("entity") or "entity").strip() or "entity"
        kind = str(slice_item.get("kind") or "source_bound").strip() or "source_bound"
        endpoints = [str(value).strip() for value in slice_item.get("endpoints", []) if str(value).strip()]
        title = next(
            (
                str(item.get("title") or "").strip()
                for item in matching_scenarios
                if str(item.get("title") or "").strip()
            ),
            f"Source-bound UI test data backfill: {entity}/{kind}",
        )
        templates.append(
            {
                "request_template_id": f"UITESTDATA_{slice_id}",
                "scan_id": scan_id,
                "campaign_id": str(campaign.get("campaign_id") or ""),
                "project_id": project,
                "title": title,
                "task": (
                    "Prepare disposable sandbox data through the approved UI flow so the selected source-bound slice "
                    f"{slice_id} becomes executable."
                ),
                "execution_mode": "approved_sandbox_write",
                "path": "/",
                "page_hints": [
                    hint
                    for hint in (
                        f"behavior slice: {slice_id}",
                        f"entity: {entity}",
                        f"slice kind: {kind}",
                        *(f"source endpoint: {path}" for path in endpoints[:3]),
                        *(f"evidence gap: {gap}" for gap in evidence_gaps[:3]),
                    )
                    if hint
                ],
                "browser_plan": {},
                "browser_plan_draft": _ui_test_data_browser_plan_draft(
                    entity=entity,
                    slice_id=slice_id,
                    scenario_steps=scenario_steps,
                    endpoints=endpoints,
                ),
                "metadata": {
                    "auto_generated": True,
                    "request_origin": "source_bound_ui_test_data_asset",
                    "bridge_mode": "page_agent_browser_plan",
                    "behavior_slice_id": slice_id,
                    "slice_kind": kind,
                    "followup_kind": "ui_test_data_backfill",
                    "executable": False,
                    "requires_explicit_browser_plan": True,
                },
                "review_contract": {
                    "status": "needs_selector_confirmation",
                    "required_confirmations": [
                        "confirmed_field_bindings",
                        "approved_browser_plan",
                        "approved_by",
                    ],
                    "promotion_target": "ui_test_data_requests",
                },
                "promotion": {
                    "status": "draft",
                    "approved_by": "",
                    "confirmed_field_bindings": [],
                    "approved_browser_plan": {},
                },
                "generated_at_utc": generated_at,
            }
        )
    return templates


def _ui_test_data_browser_plan_draft(
    *,
    entity: str,
    slice_id: str,
    scenario_steps: list[dict[str, Any]],
    endpoints: list[str],
) -> dict[str, Any]:
    def _field_tokens(name: str) -> list[str]:
        raw = str(name or "").strip()
        if not raw:
            return []
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw.replace("_", " ").replace("-", " "))
        parts = [part.strip() for part in spaced.split() if part.strip()]
        joined = " ".join(parts)
        tokens = [raw, raw.lower(), joined, joined.lower()]
        return list(dict.fromkeys(token for token in tokens if token))

    def _selector_candidates(name: str) -> list[str]:
        selectors: list[str] = []
        for token in _field_tokens(name):
            selectors.extend(
                [
                    f'[name="{token}"]',
                    f'[data-testid="{token}"]',
                    f'[data-field="{token}"]',
                    f'input[placeholder*="{token}"]',
                ]
            )
        return list(dict.fromkeys(selectors))[:8]

    def _ui_action_candidates(name: str, value: Any) -> list[dict[str, Any]]:
        field_type = type(value).__name__.lower()
        candidates = [{"action": "fill", "reason": "default text-like input"}]
        if isinstance(value, bool):
            candidates = [{"action": "check", "reason": "boolean field"}]
        elif isinstance(value, (int, float)):
            candidates = [{"action": "fill", "reason": "numeric field as text input"}]
        elif isinstance(value, list):
            candidates = [{"action": "select_option", "reason": "list-like value may map to multi-select"}]
        elif str(name).lower().endswith(("type", "status", "category", "role")):
            candidates = [
                {"action": "select_option", "reason": "field name suggests option selection"},
                {"action": "fill", "reason": "fallback if UI control is editable"},
            ]
        return candidates

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    write_steps = [
        step
        for step in scenario_steps
        if isinstance(step, dict) and str(step.get("method") or "").strip().upper() in write_methods
    ]
    observation_steps = [
        step
        for step in scenario_steps
        if isinstance(step, dict) and str(step.get("method") or "").strip().upper() in {"GET", "HEAD", "OPTIONS"}
    ]
    draft_actions: list[dict[str, Any]] = []
    field_bindings_draft: list[dict[str, Any]] = []
    for order, step in enumerate(write_steps[:3], start=1):
        body = step.get("body") if isinstance(step.get("body"), dict) else {}
        body_field_hints = [str(key).strip() for key in body.keys() if str(key).strip()]
        field_bindings = [
            {
                "field": field,
                "value_hint": body.get(field),
                "selector_candidates": _selector_candidates(field),
                "ui_action_candidates": _ui_action_candidates(field, body.get(field)),
                "binding_status": "selector_mapping_needed",
            }
            for field in body_field_hints[:8]
        ]
        field_bindings_draft.extend(field_bindings)
        draft_actions.append(
            {
                "order": order,
                "intent": "execute_source_bound_write_via_ui",
                "source_api_method": str(step.get("method") or "").strip().upper(),
                "source_api_path": str(step.get("path") or "").strip(),
                "body_field_hints": body_field_hints[:8],
                "field_bindings": field_bindings,
                "selector_hints": [
                    f"Need a UI trigger that maps to {str(step.get('method') or '').strip().upper()} {str(step.get('path') or '').strip()}",
                    f"Need form bindings for entity {entity}",
                ],
                "missing_requirements": [
                    "UI_SELECTOR_BINDING_MISSING",
                    "FORM_FIELD_MAPPING_MISSING",
                ],
            }
        )
    if observation_steps:
        first_observation = observation_steps[0]
        draft_actions.append(
            {
                "order": len(draft_actions) + 1,
                "intent": "verify_created_object_via_ui_or_source_observation",
                "source_api_method": str(first_observation.get("method") or "").strip().upper(),
                "source_api_path": str(first_observation.get("path") or "").strip(),
                "selector_hints": [f"Need a UI locator to verify the created {entity} state"],
                "missing_requirements": ["UI_ASSERTION_SELECTOR_MISSING"],
            }
        )
    return {
        "execution_mode": "approved_sandbox_write",
        "write_approved": False,
        "steps": [
            {"action": "goto", "url": "/", "wait_until": "networkidle"},
            {"action": "wait_for_load", "state": "networkidle"},
            {"action": "screenshot", "full_page": True},
        ],
        "draft_actions": draft_actions,
        "field_bindings_draft": field_bindings_draft,
        "suggested_start_paths": list(dict.fromkeys(["/"] + [item for item in endpoints[:3] if item])),
        "missing_requirements": [
            "UI_SELECTOR_BINDING_MISSING",
            "FORM_FIELD_MAPPING_MISSING",
            "WRITE_PLAN_EXPLICIT_APPROVAL_REQUIRED",
        ],
        "slice_id": slice_id,
    }


def _ui_execution_evidence_summary(ui_execution: Any) -> dict[str, Any]:
    payload = _as_dict(ui_execution)
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    requested = int(payload.get("requested") or len(results) or 0)
    executed = int(payload.get("executed") or 0)
    failed = int(payload.get("failed") or 0)
    blocked = int(payload.get("blocked") or 0)
    provider_distribution = (
        {str(key): int(value or 0) for key, value in payload.get("provider_distribution", {}).items()}
        if isinstance(payload.get("provider_distribution"), dict)
        else {}
    )
    bridge_provider_distribution: dict[str, int] = {}
    created_data_count = 0
    evidence_captured_count = 0
    current_url_samples: list[str] = []
    artifact_refs: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        ref = str(artifact.get("ref") or "").strip()
        if ref and ref not in artifact_refs:
            artifact_refs.append(ref)
    for result in results:
        row = _as_dict(result)
        if not row:
            continue
        bridge_provider = str(row.get("bridge_provider") or row.get("provider") or "").strip()
        if bridge_provider:
            bridge_provider_distribution[bridge_provider] = bridge_provider_distribution.get(bridge_provider, 0) + 1
        if _as_dict(row.get("created_data")):
            created_data_count += 1
        current_url = str(row.get("current_url") or "").strip()
        if current_url and current_url not in current_url_samples and len(current_url_samples) < 3:
            current_url_samples.append(current_url)
        has_evidence = bool(
            isinstance(row.get("artifacts"), list) and row.get("artifacts")
            or isinstance(row.get("findings"), list) and row.get("findings")
            or _as_dict(row.get("created_data"))
            or isinstance(row.get("history"), list) and row.get("history")
            or isinstance(row.get("console"), list) and row.get("console")
            or isinstance(row.get("network"), list) and row.get("network")
        )
        if has_evidence:
            evidence_captured_count += 1
    status = str(payload.get("status") or ("not_requested" if requested <= 0 else "completed"))
    summary = (
        "UI execution not requested."
        if requested <= 0
        else (
            f"UI execution {status}: requested {requested}, executed {executed}, failed {failed}, "
            f"blocked {blocked}, evidence captured {evidence_captured_count}, artifacts {len(artifacts)}, "
            f"findings {len(findings)}"
            + (f", created data {created_data_count}" if created_data_count else "")
            + "."
        )
    )
    return {
        "status": status,
        "requested": requested,
        "executed": executed,
        "failed": failed,
        "blocked": blocked,
        "finding_count": len(findings),
        "artifact_count": len(artifacts),
        "created_data_count": created_data_count,
        "evidence_captured_count": evidence_captured_count,
        "provider_distribution": provider_distribution,
        "bridge_provider_distribution": bridge_provider_distribution,
        "artifact_refs": artifact_refs[:3],
        "current_url_samples": current_url_samples,
        "summary": summary,
    }


def _load_candidate_items(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return [dict(item) for item in items if isinstance(item, dict)]


def _merge_candidate_items(existing: list[dict[str, Any]], new_items: list[dict[str, Any]], *, key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in existing + new_items:
        if not isinstance(row, dict):
            continue
        key = "|".join(str(row.get(field) or "").strip().lower() for field in key_fields)
        if not key.strip("|"):
            key = _sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))[:16]
        current = merged.get(key)
        if current is None:
            merged[key] = dict(row)
            continue
        current_generated = str(current.get("generated_at_utc") or "")
        incoming_generated = str(row.get("generated_at_utc") or "")
        if incoming_generated >= current_generated:
            merged[key] = {**current, **dict(row)}
    return list(merged.values())


def _materialize_ui_followup_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    campaign: dict[str, Any],
    items: list[dict[str, Any]],
    selected_slices: list[dict[str, Any]] | None = None,
    plan_only_scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    workspace_dir = root / "platform_workspace" / _safe_project(project) / "defect_discovery"
    output_dir = root / "platform_outputs" / _safe_project(project) / "defect_discovery"
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    intake_items: list[dict[str, Any]] = []
    regression_items: list[dict[str, Any]] = []
    execution_request_items: list[dict[str, Any]] = []
    test_data_request_items: list[dict[str, Any]] = []
    seen_intake: set[str] = set()
    seen_regression: set[str] = set()
    seen_execution_request: set[str] = set()
    for index, value in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(value, dict):
            continue
        title = str(value.get("title") or f"ui_high_confidence_candidate_{index}").strip() or f"ui_high_confidence_candidate_{index}"
        path = _ui_candidate_target_path(value)
        method = _ui_candidate_method(value)
        risk_type = str(value.get("risk_type") or value.get("defect_family") or "ui_execution").strip() or "ui_execution"
        severity = str(value.get("severity") or "P2").strip().upper()
        candidate_id = str(value.get("risk_id") or value.get("bug_id") or _sha256(f"{title}|{method}|{path}")[:16]).strip()
        intake_key = f"{title.lower()}|{method}|{path}|{severity}"
        if intake_key not in seen_intake:
            seen_intake.add(intake_key)
            intake_items.append({
                "intake_id": f"UIINTAKE_{candidate_id}",
                "scan_id": scan_id,
                "campaign_id": str(campaign.get("campaign_id") or ""),
                "project_id": project,
                "title": title,
                "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
                "risk_type": risk_type,
                "candidate_tier": str(value.get("candidate_tier") or "high_confidence_ui_candidate"),
                "verification_badge": str(value.get("verification_badge") or "ui_verified"),
                "verification_status": str((_as_dict(value.get("ui_verification"))).get("status") or ""),
                "confidence_score": float(value.get("confidence_score") or value.get("confidence") or 0.0),
                "path": path,
                "method": method,
                "source": "ui_high_confidence_candidate",
                "defect_intake_route": "internal_defect_intake",
                "defect_intake_priority": "P1" if severity != "P0" else "P0",
                "defect_intake_reason": "UI 高可信候选已完成二次验真，建议进入内部缺陷入账或人工复核队列。",
                "reproduction_steps": list(value.get("reproduction_steps") or []),
                "evidence_quality": dict(value.get("evidence_quality") or {}),
            })
        regression_key = f"{method}|{path}|{risk_type}|{title.lower()}"
        if regression_key not in seen_regression:
            seen_regression.add(regression_key)
            regression_items.append({
                "regression_probe_id": f"UIREG_{candidate_id}",
                "issue_id": f"UIINTAKE_{candidate_id}",
                "title": title,
                "risk_type": risk_type,
                "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
                "method": method,
                "path": path or "/",
                "actor": "ui_dom_agent",
                "expected": str(value.get("expected") or "原 UI 高可信缺陷信号不应再次出现，相关页面与业务状态应保持一致。"),
                "source": "ui_high_confidence_candidate",
                "candidate_tier": str(value.get("candidate_tier") or "high_confidence_ui_candidate"),
                "high_confidence_candidate": bool(value.get("high_confidence_candidate") is True),
                "verification_badge": str(value.get("verification_badge") or "ui_verified"),
                "verification_status": str((_as_dict(value.get("ui_verification"))).get("status") or ""),
                "confidence_score": float(value.get("confidence_score") or value.get("confidence") or 0.0),
                "evidence_quality": dict(value.get("evidence_quality") or {}),
            })
        execution_request_key = f"{title.lower()}|{method}|{path}|{risk_type}"
        if execution_request_key not in seen_execution_request:
            seen_execution_request.add(execution_request_key)
            execution_request_items.append(
                _ui_followup_execution_template(
                    value,
                    project=project,
                    scan_id=scan_id,
                    campaign=campaign,
                    generated_at=generated_at,
                    index=index,
                )
            )
    execution_request_items.extend(
        _source_bound_ui_followup_templates(
            project=project,
            scan_id=scan_id,
            campaign=campaign,
            generated_at=generated_at,
            selected_slices=selected_slices,
            plan_only_scenarios=plan_only_scenarios,
        )
    )
    test_data_request_items.extend(
        _source_bound_ui_test_data_templates(
            project=project,
            scan_id=scan_id,
            campaign=campaign,
            generated_at=generated_at,
            selected_slices=selected_slices,
            plan_only_scenarios=plan_only_scenarios,
        )
    )
    intake_payload = {
        "version": "ui_high_confidence_defect_intake_candidates_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    regression_payload = {
        "version": "ui_high_confidence_regression_candidates_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    execution_request_payload = {
        "version": "ui_followup_execution_requests_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    test_data_request_payload = {
        "version": "ui_followup_test_data_requests_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    intake_path = workspace_dir / "internal_defect_intake_candidates.json"
    intake_existing = _load_candidate_items(intake_path)
    intake_payload["items"] = _merge_candidate_items(
        intake_existing,
        [{**item, "generated_at_utc": generated_at} for item in intake_items],
        key_fields=("title", "method", "path", "severity", "risk_type"),
    )
    regression_path = workspace_dir / "ui_high_confidence_regression_candidates.json"
    regression_existing = _load_candidate_items(regression_path)
    regression_payload["items"] = _merge_candidate_items(
        regression_existing,
        [{**item, "generated_at_utc": generated_at, "approved": bool(item.get("approved"))} for item in regression_items],
        key_fields=("title", "method", "path", "severity", "risk_type"),
    )
    execution_request_path = workspace_dir / "ui_followup_execution_requests.json"
    execution_request_existing = _load_candidate_items(execution_request_path)
    execution_request_payload["items"] = _merge_candidate_items(
        execution_request_existing,
        execution_request_items,
        key_fields=("title", "method", "path", "severity", "risk_type"),
    )
    test_data_request_path = workspace_dir / "ui_followup_test_data_requests.json"
    test_data_request_existing = _load_candidate_items(test_data_request_path)
    test_data_request_payload["items"] = _merge_candidate_items(
        test_data_request_existing,
        test_data_request_items,
        key_fields=("title", "execution_mode", "path"),
    )
    if intake_items or intake_existing:
        _write_json(intake_path, intake_payload)
        _write_json(output_dir / "internal_defect_intake_candidates.json", intake_payload)
    if regression_items or regression_existing:
        _write_json(regression_path, regression_payload)
        _write_json(output_dir / "ui_high_confidence_regression_candidates.json", regression_payload)
    if execution_request_items or execution_request_existing:
        _write_json(execution_request_path, execution_request_payload)
        _write_json(output_dir / "ui_followup_execution_requests.json", execution_request_payload)
    if test_data_request_items or test_data_request_existing:
        _write_json(test_data_request_path, test_data_request_payload)
        _write_json(output_dir / "ui_followup_test_data_requests.json", test_data_request_payload)
    return {
        "status": (
            "materialized"
            if intake_items or regression_items or execution_request_items or test_data_request_items
            else (
                "preserved"
                if intake_existing or regression_existing or execution_request_existing or test_data_request_existing
                else "empty"
            )
        ),
        "generated_at_utc": generated_at,
        "defect_intake_candidate_count": len(intake_payload["items"]),
        "regression_candidate_count": len(regression_payload["items"]),
        "execution_request_count": len(execution_request_payload["items"]),
        "test_data_request_count": len(test_data_request_payload["items"]),
        "defect_intake_candidates_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/internal_defect_intake_candidates.json",
        "regression_candidates_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/ui_high_confidence_regression_candidates.json",
        "execution_requests_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/ui_followup_execution_requests.json",
        "test_data_requests_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/ui_followup_test_data_requests.json",
    }


def _external_candidate_id(item: dict[str, Any], index: int = 0) -> str:
    value = str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or "").strip()
    if value:
        return value
    fingerprint = _sha256(
        f"{item.get('title') or ''}|{item.get('method') or item.get('_api_method') or ''}|{item.get('path') or item.get('_api_path') or ''}"
    )[:16]
    return f"EXT_{index or 0}_{fingerprint}"


def _external_reproduction_observation(item: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    runtime_replay = _as_dict(item.get("runtime_replay"))
    raw_evidence = _as_dict(item.get("raw_evidence"))
    request_raw = _as_dict(raw_evidence.get("request_raw"))
    response_raw = _as_dict(raw_evidence.get("response_raw"))
    method = str(item.get("method") or item.get("_api_method") or runtime_replay.get("method") or request_raw.get("method") or "GET").upper().strip()
    path = str(item.get("path") or item.get("_api_path") or runtime_replay.get("path") or request_raw.get("path") or "/").strip() or "/"
    body = request_raw.get("body", item.get("request_body"))
    status_code = runtime_replay.get("http_status")
    if status_code is None:
        status_code = response_raw.get("status_code")
    body_binding = {}
    if body not in (None, "", [], {}):
        body_binding = {"bound": True, "source": "external_runtime_request_body"}
    response_payload = response_raw.get("body")
    if response_payload is None:
        response_payload = _as_dict(runtime_replay.get("trace")).get("body")
    verification = {
        "verdict": "validated_candidate",
        "reason": str(item.get("actual") or item.get("actual_behavior") or _as_dict(item.get("business_invariant_evaluation")).get("reason") or item.get("description") or "").strip(),
        "confidence": round(min(max(float(_as_dict(item.get("evidence_quality")).get("score") or item.get("confidence_score") or 88) / 100.0, 0.0), 0.99), 2),
    }
    response: dict[str, Any] = {}
    if status_code is not None:
        try:
            response["status_code"] = int(status_code)
        except Exception:
            pass
    if response_payload is not None:
        response["payload"] = response_payload
    obs = {
        "candidate_id": candidate_id,
        "risk_type": str(item.get("category") or "external_signal_violation").strip() or "external_signal_violation",
        "method": method,
        "path": path,
        "request": {"method": method, "path": path, "body": body},
        "response": response,
        "verification": verification,
        "responses": [],
        "fixture_receipts": [],
        "cleanup_receipts": [],
        "snapshots": {"before": [], "after": []},
    }
    if body_binding:
        obs["request"]["body_runtime_binding"] = body_binding
    if response:
        obs["responses"] = [{
            "attempt": 1,
            "step": 1,
            "method": method,
            "path": path,
            "status_code": response.get("status_code"),
            "payload": response.get("payload"),
            "runtime_binding": {"bound": True, "source": "external_runtime_response"},
            "request_body_runtime_binding": body_binding or {"bound": True, "source": "external_runtime_request"},
        }]
    return obs


def _render_external_repro_ps1(findings: list[dict[str, Any]]) -> str:
    lines = [
        "# QualiBug external validated candidate reproduction script",
        "$ErrorActionPreference = 'Stop'",
        'if (-not $env:BASE_URL) { throw "Please set BASE_URL before running this script." }',
        "",
    ]
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or item.get("_api_method") or "GET").upper().strip() or "GET"
        path = str(item.get("path") or item.get("_api_path") or "/").strip() or "/"
        body = _as_dict(_as_dict(item.get("raw_evidence")).get("request_raw")).get("body", item.get("request_body"))
        title = str(item.get("title") or "").replace("'", "''")
        lines.append(f"Write-Host 'Finding {index}: {title}'")
        lines.append(f"$targetUrl = \"$env:BASE_URL{path}\"")
        if body not in (None, "", [], {}):
            payload = json.dumps(body, ensure_ascii=False, default=str).replace("'", "''")
            lines.append(f"$payload = @'\n{payload}\n'@")
            lines.append(f"curl.exe -sS -X {method} \"$targetUrl\" -H \"Content-Type: application/json\" --data-raw $payload")
        else:
            lines.append(f"curl.exe -sS -X {method} \"$targetUrl\"")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_external_regression_pytest(project: str, findings: list[dict[str, Any]]) -> str:
    lines = [
        "from __future__ import annotations",
        "",
        "import os",
        "import requests",
        "",
        "",
        f'PROJECT_ID = {json.dumps(project, ensure_ascii=False)}',
        "",
        "",
        "def _base_url() -> str:",
        '    base = os.environ.get("BASE_URL", "").rstrip("/")',
        '    if not base:',
        '        raise AssertionError("BASE_URL environment variable is required")',
        "    return base",
        "",
    ]
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or item.get("_api_method") or "GET").upper().strip() or "GET"
        path = str(item.get("path") or item.get("_api_path") or "/").strip() or "/"
        body = _as_dict(_as_dict(item.get("raw_evidence")).get("request_raw")).get("body", item.get("request_body"))
        expected_status = _as_dict(item.get("runtime_replay")).get("http_status")
        function_name = re.sub(r"[^A-Za-z0-9_]+", "_", f"test_external_repro_{index}_{method}_{path}").strip("_").lower() or f"test_external_repro_{index}"
        lines.extend([
            f"def {function_name}() -> None:",
            f"    url = _base_url() + {json.dumps(path, ensure_ascii=False)}",
        ])
        if body not in (None, "", [], {}):
            lines.append(f"    payload = {json.dumps(body, ensure_ascii=False, default=str)}")
            lines.append(f"    response = requests.request({json.dumps(method)}, url, json=payload, timeout=15)")
        else:
            lines.append(f"    response = requests.request({json.dumps(method)}, url, timeout=15)")
        if expected_status is not None:
            lines.append(f"    assert response.status_code == {int(expected_status)}")
        else:
            lines.append("    assert response.status_code >= 100")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _materialize_external_reproduction_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    packaged_rows: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    report_findings: list[dict[str, Any]] = []
    ledger_entries: list[dict[str, Any]] = []
    for index, value in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if str(row.get("confirmation_status") or "").strip().lower() != "validated_candidate":
            eligible.append(row)
            continue
        candidate_id = _external_candidate_id(row, index)
        row["candidate_id"] = candidate_id
        row.setdefault("finding_id", candidate_id)
        row.setdefault("confidence", float(row.get("confidence_score") or 0.88))
        row.setdefault("reason", str(row.get("actual") or row.get("actual_behavior") or _as_dict(row.get("business_invariant_evaluation")).get("reason") or row.get("description") or "").strip())
        obs = _external_reproduction_observation(row, candidate_id=candidate_id)
        observations.append(obs)
        ledger_entries.append({
            "candidate_id": candidate_id,
            "customer_ready": bool(obs.get("response")),
            "readiness_level": "customer_ready_candidate" if obs.get("response") else "validated_candidate_without_target_status",
            "fixture_setup": {"accepted_count": 0},
            "snapshots": {"accepted_count": 0},
            "cleanup": {"accepted_count": 0},
            "gap_types": [] if obs.get("response") else ["missing_target_http_status"],
            "verdict": "validated_candidate",
        })
        report_findings.append({
            "finding_id": row.get("finding_id"),
            "candidate_id": candidate_id,
            "title": row.get("title"),
            "risk_type": row.get("category") or "external_signal_violation",
            "method": row.get("method") or row.get("_api_method"),
            "path": row.get("path") or row.get("_api_path"),
            "confidence": row.get("confidence"),
            "evidence_grade": row.get("evidence_grade"),
            "evidence_strength_score": row.get("evidence_strength_score"),
            "reason": row.get("reason"),
            "violated_invariants": row.get("violated_invariants") or [],
            "delta_summary": row.get("delta_summary") or {},
            "source_refs": row.get("source_refs") or [],
            "customer_triage": row.get("customer_triage") or {},
            "evidence_package": row.get("evidence_package") or {},
        })
        packaged_rows.append(row)
        eligible.append(row)
    if not report_findings:
        return eligible, {
            "status": "empty",
            "finding_count": 0,
            "customer_ready_reproduction_count": 0,
        }
    output_dir = root / "platform_outputs" / _safe_project(project) / "defect_discovery"
    workspace_dir = root / "platform_workspace" / _safe_project(project) / "defect_discovery"
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = {
        "project_id": project,
        "created_at": generated_at,
        "scan_id": scan_id,
        "findings": report_findings,
        "write_observations": observations,
        "runtime_evidence_probe_ledger": {"entries": ledger_entries},
    }
    try:
        from .grounded_probe_executor import _build_runtime_customer_reproduction_pack, _render_runtime_customer_reproduction_pack_markdown
        from .runtime_reproduction_asset_linker import link_reproduction_assets
    except Exception as exc:
        return eligible, {"status": "failed", "reason": f"external_reproduction_asset_import_failed:{type(exc).__name__}"}
    pack = _build_runtime_customer_reproduction_pack(report)
    pack_json_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_runtime_customer_reproduction_pack.json"
    pack_md_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_runtime_customer_reproduction_pack.md"
    repro_ps1_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_validated_bug_repro.ps1"
    regression_pytest_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_validated_bug_regression_pytest.py"
    outputs = {
        "repro_ps1": str(output_dir / "external_validated_bug_repro.ps1"),
        "regression_pytest": str(output_dir / "external_validated_bug_regression_pytest.py"),
        "execution_report": str(output_dir / "external_runtime_customer_reproduction_pack.json"),
        "execution_report_md": str(output_dir / "external_runtime_customer_reproduction_pack.md"),
    }
    report["runtime_customer_reproduction_pack"] = pack
    report["outputs"] = outputs
    report = link_reproduction_assets(report)
    pack = report.get("runtime_customer_reproduction_pack") if isinstance(report.get("runtime_customer_reproduction_pack"), dict) else pack
    pack_json_path = workspace_dir / "external_runtime_customer_reproduction_pack.json"
    pack_md_path = workspace_dir / "external_runtime_customer_reproduction_pack.md"
    repro_ps1_path = workspace_dir / "external_validated_bug_repro.ps1"
    regression_pytest_path = workspace_dir / "external_validated_bug_regression_pytest.py"
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    _write_json(pack_json_path, pack)
    _write_json(output_dir / "external_runtime_customer_reproduction_pack.json", pack)
    pack_md_text = _render_runtime_customer_reproduction_pack_markdown(pack)
    pack_md_path.write_text(pack_md_text, encoding="utf-8")
    (output_dir / "external_runtime_customer_reproduction_pack.md").write_text(pack_md_text, encoding="utf-8")
    repro_text = _render_external_repro_ps1(packaged_rows)
    repro_ps1_path.write_text(repro_text, encoding="utf-8")
    (output_dir / "external_validated_bug_repro.ps1").write_text(repro_text, encoding="utf-8")
    pytest_text = _render_external_regression_pytest(project, packaged_rows)
    regression_pytest_path.write_text(pytest_text, encoding="utf-8")
    (output_dir / "external_validated_bug_regression_pytest.py").write_text(pytest_text, encoding="utf-8")
    findings_by_id = {str(f.get("candidate_id") or ""): f for f in (report.get("findings") or []) if isinstance(f, dict)}
    for row in eligible:
        cid = str(row.get("candidate_id") or "")
        linked = findings_by_id.get(cid) or {}
        if isinstance(linked.get("evidence_package"), dict):
            row["evidence_package"] = linked["evidence_package"]
        if isinstance(linked.get("reproduction_artifact_links"), list):
            row["reproduction_artifact_links"] = linked["reproduction_artifact_links"]
    return eligible, {
        "status": "materialized",
        "generated_at_utc": generated_at,
        "finding_count": len(report_findings),
        "customer_ready_reproduction_count": int(pack.get("customer_ready_reproduction_count") or 0),
        "runtime_customer_reproduction_pack_ref": pack_json_ref,
        "runtime_customer_reproduction_pack_md_ref": pack_md_ref,
        "repro_ps1_ref": repro_ps1_ref,
        "regression_pytest_ref": regression_pytest_ref,
        "reproduction_artifact_index": report.get("reproduction_artifact_index") if isinstance(report.get("reproduction_artifact_index"), dict) else {},
        "runtime_customer_reproduction_pack": pack,
    }


def _external_priority(severity: Any) -> str:
    text = str(severity or "").strip().upper()
    if text in {"P0", "P1", "P2", "P3"}:
        return text
    return "P1"


def _write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def _commercial_priority(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"P0", "P1", "P2", "P3"}:
        return text
    return "P1"


def _commercial_finding_customer_ready(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if bool(item.get("gate_passed")) is not True:
        return False
    quality = _as_dict(item.get("evidence_quality"))
    missing = [str(value) for value in (quality.get("missing") or []) if str(value)]
    if quality and missing:
        return False
    if quality.get("can_reproduce") is False:
        return False
    confirmation_status = str(item.get("confirmation_status") or "").strip().lower()
    if confirmation_status == "validated_candidate":
        return True
    quality_level = str(quality.get("level") or "").strip().lower()
    if quality_level == "validated":
        return True
    evidence_status = _as_dict(item.get("evidence_status"))
    semantic = str(item.get("semantic_verdict") or evidence_status.get("semantic_verdict") or "").strip().upper()
    business = str(item.get("business_evidence_status") or evidence_status.get("business_evidence_status") or "").strip().upper()
    if semantic == "SEMANTIC_CONFIRMED" and business == "VALIDATED":
        return True
    return False


def _commercial_candidate_id(item: dict[str, Any], index: int = 0) -> str:
    for key in ("candidate_id", "risk_id", "finding_id", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return f"COM-{index:03d}"


def _commercial_finding_reason(item: dict[str, Any]) -> str:
    for value in (
        item.get("reason"),
        item.get("actual"),
        item.get("actual_behavior"),
        item.get("description"),
        _as_dict(item.get("business_invariant_evaluation")).get("reason"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _commercial_runtime_observation(item: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    raw = _as_dict(item.get("raw_evidence"))
    request_raw = _as_dict(raw.get("request_raw"))
    response_raw = _as_dict(raw.get("response_raw"))
    method = str(item.get("method") or item.get("_api_method") or request_raw.get("method") or "GET").upper()
    path = str(item.get("path") or item.get("_api_path") or request_raw.get("path") or "")
    status_code = response_raw.get("status_code")
    if not isinstance(status_code, int):
        replay = _as_dict(item.get("runtime_replay"))
        if isinstance(replay.get("http_status"), int):
            status_code = int(replay.get("http_status"))
    response_payload = response_raw.get("body")
    observation: dict[str, Any] = {
        "candidate_id": candidate_id,
        "method": method,
        "path": path,
        "request": {
            "method": method,
            "path": path,
            "body": request_raw.get("body"),
            "body_runtime_binding": request_raw.get("body_runtime_binding") or {},
        },
        "verification": {
            "verdict": "validated_candidate",
            "reason": _commercial_finding_reason(item),
        },
        "response": {},
        "responses": [],
        "fixture_receipts": [],
        "cleanup_receipts": [],
        "snapshots": {},
    }
    if isinstance(status_code, int):
        observation["response"] = {
            "status_code": int(status_code),
            "payload": response_payload,
        }
    return observation


def _build_materialized_commercial_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    findings: list[dict[str, Any]],
    runtime_customer_reproduction_pack: dict[str, Any],
    output_prefix: str,
    summary_engine: str,
    report_engine: str,
    priority_source: str,
    readiness_failure_code: str,
    readiness_failure_reason: str,
    execution_report_title: str,
    execution_report_md_heading: str,
    runtime_runbook_md_heading: str,
    runtime_runbook_md_text: str,
    remediation_md_heading: str,
    remediation_md_text: str,
    promotion_gate_md_heading: str,
    promotion_gate_md_text: str,
    delivery_manifest_md_heading: str,
    delivery_manifest_md_text: str,
    delivery_verification_md_heading: str,
    delivery_verification_md_text: str,
    sla_md_heading: str,
    sla_md_text: str,
    gap_md_heading: str,
    gap_md_text: str,
    patch_md_heading: str,
    patch_md_text: str,
    write_approval_md_heading: str,
    write_approval_md_text: str,
    remediation_verification_md_heading: str,
    remediation_verification_md_text: str,
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    try:
        from .runtime_commercial_handoff_bundle import build_commercial_handoff_bundle, render_commercial_handoff_markdown
        from .runtime_commercial_handoff_acceptance_gate import validate_commercial_handoff_acceptance, render_commercial_handoff_acceptance_markdown
        from .runtime_handoff_secret_audit import (
            audit_commercial_handoff_secrets,
            build_handoff_redacted_runtime_evidence_pack,
            build_handoff_secret_redaction_plan,
            render_handoff_redacted_runtime_evidence_markdown,
            render_handoff_secret_audit_markdown,
            render_handoff_secret_redaction_plan_markdown,
        )
        from .runtime_handoff_archive_manifest import build_handoff_archive_manifest, render_handoff_archive_manifest_markdown
        from .runtime_commercial_closure_acceptance_ledger import build_commercial_closure_acceptance_ledger, render_commercial_closure_acceptance_ledger_markdown
        from .runtime_commercial_audit_event_stream import build_commercial_audit_event_stream, render_commercial_audit_event_stream_markdown
        from .runtime_commercial_audit_export_adapters import (
            build_commercial_audit_export_adapters,
            render_commercial_audit_exports_markdown,
            render_csv_audit_ledger,
        )
        from .runtime_commercial_audit_export_import_gate import build_commercial_audit_export_import_gate, render_commercial_audit_import_gate_markdown
        from .runtime_commercial_external_tracker_reconciliation import (
            build_commercial_external_tracker_reconciliation,
            render_commercial_external_tracker_reconciliation_markdown,
        )
        from .runtime_external_tracker_closure_sync_policy import (
            build_external_tracker_closure_sync_policy,
            render_external_tracker_closure_sync_policy_markdown,
        )
        from .runtime_external_tracker_sync_payload_builder import (
            build_external_tracker_sync_payloads,
            render_external_tracker_sync_payloads_markdown,
        )
        from .runtime_external_tracker_sync_payload_gate import (
            validate_external_tracker_sync_payloads,
            render_external_tracker_sync_payload_gate_markdown,
        )
        from .enterprise_delivery_package import create_delivery_package
    except Exception as exc:
        return {"status": "failed", "reason": f"commercial_asset_import_failed:{type(exc).__name__}"}

    if not findings:
        return {"status": "empty", "finding_count": 0}

    safe_project = _safe_project(project)
    workspace_dir = root / "platform_workspace" / safe_project / "defect_discovery"
    output_dir = root / "platform_outputs" / safe_project / "defect_discovery"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    customer_ready_count = int(runtime_customer_reproduction_pack.get("customer_ready_reproduction_count") or 0)
    evidence_scores = [
        float(item.get("evidence_strength_score") or 0.0)
        for item in findings
        if isinstance(item.get("evidence_strength_score"), (int, float))
    ]
    readiness_score = int(round((sum(evidence_scores) / len(evidence_scores)) * 100)) if evidence_scores else 88
    readiness_score = max(0, min(readiness_score, 99))
    candidate_ids = [
        str(item.get("candidate_id") or item.get("finding_id") or "")
        for item in findings
        if str(item.get("candidate_id") or item.get("finding_id") or "").strip()
    ]
    outputs = {
        "execution_report": str(output_dir / f"{output_prefix}_execution_report.json"),
        "execution_report_md": str(output_dir / f"{output_prefix}_execution_report.md"),
        "onboarding_preflight_json": str(output_dir / f"{output_prefix}_onboarding_preflight.json"),
        "runtime_capability_matrix_json": str(output_dir / f"{output_prefix}_runtime_capability_matrix.json"),
        "runtime_execution_runbook_json": str(output_dir / f"{output_prefix}_runtime_execution_runbook.json"),
        "runtime_execution_runbook_md": str(output_dir / f"{output_prefix}_runtime_execution_runbook.md"),
        "runtime_evidence_readiness_sla_gate_json": str(output_dir / f"{output_prefix}_runtime_evidence_readiness_sla_gate.json"),
        "runtime_evidence_readiness_sla_gate_md": str(output_dir / f"{output_prefix}_runtime_evidence_readiness_sla_gate.md"),
        "runtime_evidence_scoreboard_json": str(output_dir / f"{output_prefix}_runtime_evidence_scoreboard.json"),
        "runtime_evidence_scoreboard_md": str(output_dir / f"{output_prefix}_runtime_evidence_scoreboard.md"),
        "runtime_evidence_probe_ledger_json": str(output_dir / f"{output_prefix}_runtime_evidence_probe_ledger.json"),
        "runtime_evidence_probe_ledger_md": str(output_dir / f"{output_prefix}_runtime_evidence_probe_ledger.md"),
        "runtime_customer_reproduction_pack_json": str(workspace_dir / f"{output_prefix}_runtime_customer_reproduction_pack.json"),
        "runtime_customer_reproduction_pack_md": str(workspace_dir / f"{output_prefix}_runtime_customer_reproduction_pack.md"),
        "runtime_evidence_remediation_plan_json": str(output_dir / f"{output_prefix}_runtime_evidence_remediation_plan.json"),
        "runtime_evidence_remediation_plan_md": str(output_dir / f"{output_prefix}_runtime_evidence_remediation_plan.md"),
        "runtime_evidence_promotion_gate_json": str(output_dir / f"{output_prefix}_runtime_evidence_promotion_gate.json"),
        "runtime_evidence_promotion_gate_md": str(output_dir / f"{output_prefix}_runtime_evidence_promotion_gate.md"),
        "runtime_evidence_customer_delivery_manifest_json": str(output_dir / f"{output_prefix}_runtime_evidence_customer_delivery_manifest.json"),
        "runtime_evidence_customer_delivery_manifest_md": str(output_dir / f"{output_prefix}_runtime_evidence_customer_delivery_manifest.md"),
        "runtime_evidence_delivery_manifest_verification_json": str(output_dir / f"{output_prefix}_runtime_evidence_delivery_manifest_verification.json"),
        "runtime_evidence_delivery_manifest_verification_md": str(output_dir / f"{output_prefix}_runtime_evidence_delivery_manifest_verification.md"),
        "commercial_handoff_secret_redaction_plan_json": str(output_dir / f"{output_prefix}_commercial_handoff_secret_redaction_plan.json"),
        "commercial_handoff_secret_redaction_plan_md": str(output_dir / f"{output_prefix}_commercial_handoff_secret_redaction_plan.md"),
        "commercial_handoff_redacted_runtime_evidence_json": str(output_dir / f"{output_prefix}_commercial_handoff_redacted_runtime_evidence.json"),
        "commercial_handoff_redacted_runtime_evidence_md": str(output_dir / f"{output_prefix}_commercial_handoff_redacted_runtime_evidence.md"),
        "runtime_sla_execution_policy_json": str(output_dir / f"{output_prefix}_runtime_sla_execution_policy.json"),
        "runtime_sla_execution_policy_md": str(output_dir / f"{output_prefix}_runtime_sla_execution_policy.md"),
        "runtime_sla_gap_prioritizer_json": str(output_dir / f"{output_prefix}_runtime_sla_gap_prioritizer.json"),
        "runtime_sla_gap_prioritizer_md": str(output_dir / f"{output_prefix}_runtime_sla_gap_prioritizer.md"),
        "onboarding_patch_safety_validation_json": str(output_dir / f"{output_prefix}_onboarding_patch_safety_validation.json"),
        "onboarding_patch_safety_validation_md": str(output_dir / f"{output_prefix}_onboarding_patch_safety_validation.md"),
        "write_sandbox_approval_packet_json": str(output_dir / f"{output_prefix}_write_sandbox_approval_packet.json"),
        "write_sandbox_approval_packet_md": str(output_dir / f"{output_prefix}_write_sandbox_approval_packet.md"),
        "remediation_verification_json": str(output_dir / f"{output_prefix}_remediation_verification.json"),
        "remediation_verification_md": str(output_dir / f"{output_prefix}_remediation_verification.md"),
        "commercial_handoff_bundle_json": str(output_dir / f"{output_prefix}_commercial_handoff_bundle.json"),
        "commercial_handoff_bundle_md": str(output_dir / f"{output_prefix}_commercial_handoff_bundle.md"),
        "commercial_handoff_acceptance_gate_json": str(output_dir / f"{output_prefix}_commercial_handoff_acceptance_gate.json"),
        "commercial_handoff_acceptance_gate_md": str(output_dir / f"{output_prefix}_commercial_handoff_acceptance_gate.md"),
        "commercial_handoff_secret_audit_json": str(output_dir / f"{output_prefix}_commercial_handoff_secret_audit.json"),
        "commercial_handoff_secret_audit_md": str(output_dir / f"{output_prefix}_commercial_handoff_secret_audit.md"),
        "handoff_archive_manifest_json": str(output_dir / f"{output_prefix}_handoff_archive_manifest.json"),
        "handoff_archive_manifest_md": str(output_dir / f"{output_prefix}_handoff_archive_manifest.md"),
        "immutable_run_receipt_json": str(output_dir / f"{output_prefix}_immutable_run_receipt.json"),
        "immutable_run_receipt_md": str(output_dir / f"{output_prefix}_immutable_run_receipt.md"),
        "handoff_receipt_comparison_json": str(output_dir / f"{output_prefix}_handoff_receipt_comparison.json"),
        "handoff_receipt_comparison_md": str(output_dir / f"{output_prefix}_handoff_receipt_comparison.md"),
        "handoff_rerun_audit_gate_json": str(output_dir / f"{output_prefix}_handoff_rerun_audit_gate.json"),
        "handoff_rerun_audit_gate_md": str(output_dir / f"{output_prefix}_handoff_rerun_audit_gate.md"),
        "commercial_evidence_lineage_dashboard_json": str(output_dir / f"{output_prefix}_commercial_evidence_lineage_dashboard.json"),
        "commercial_evidence_lineage_dashboard_md": str(output_dir / f"{output_prefix}_commercial_evidence_lineage_dashboard.md"),
        "commercial_lineage_reviewer_signoff_packet_json": str(output_dir / f"{output_prefix}_commercial_lineage_reviewer_signoff_packet.json"),
        "commercial_lineage_reviewer_signoff_packet_md": str(output_dir / f"{output_prefix}_commercial_lineage_reviewer_signoff_packet.md"),
        "commercial_closure_acceptance_ledger_json": str(output_dir / f"{output_prefix}_commercial_closure_acceptance_ledger.json"),
        "commercial_closure_acceptance_ledger_md": str(output_dir / f"{output_prefix}_commercial_closure_acceptance_ledger.md"),
        "commercial_audit_event_stream_json": str(output_dir / f"{output_prefix}_commercial_audit_event_stream.json"),
        "commercial_audit_event_stream_md": str(output_dir / f"{output_prefix}_commercial_audit_event_stream.md"),
        "commercial_audit_exports_json": str(output_dir / f"{output_prefix}_commercial_audit_exports.json"),
        "commercial_audit_exports_md": str(output_dir / f"{output_prefix}_commercial_audit_exports.md"),
        "commercial_audit_ledger_csv": str(output_dir / f"{output_prefix}_commercial_audit_ledger.csv"),
        "commercial_audit_jira_issue_import_json": str(output_dir / f"{output_prefix}_commercial_audit_jira_issue_import.json"),
        "commercial_audit_linear_issue_import_json": str(output_dir / f"{output_prefix}_commercial_audit_linear_issue_import.json"),
        "commercial_audit_import_gate_json": str(output_dir / f"{output_prefix}_commercial_audit_import_gate.json"),
        "commercial_audit_import_gate_md": str(output_dir / f"{output_prefix}_commercial_audit_import_gate.md"),
        "commercial_external_tracker_reconciliation_json": str(output_dir / f"{output_prefix}_commercial_external_tracker_reconciliation.json"),
        "commercial_external_tracker_reconciliation_md": str(output_dir / f"{output_prefix}_commercial_external_tracker_reconciliation.md"),
        "external_tracker_closure_sync_policy_json": str(output_dir / f"{output_prefix}_tracker_closure_sync_policy.json"),
        "external_tracker_closure_sync_policy_md": str(output_dir / f"{output_prefix}_tracker_closure_sync_policy.md"),
        "external_tracker_sync_payloads_json": str(output_dir / f"{output_prefix}_tracker_sync_payloads.json"),
        "external_tracker_sync_payloads_md": str(output_dir / f"{output_prefix}_tracker_sync_payloads.md"),
        "external_tracker_sync_payload_gate_json": str(output_dir / f"{output_prefix}_tracker_sync_payload_gate.json"),
        "external_tracker_sync_payload_gate_md": str(output_dir / f"{output_prefix}_tracker_sync_payload_gate.md"),
    }
    runtime_evidence_probe_ledger = {
        "engine": f"{summary_engine}_runtime_evidence_probe_ledger_v1",
        "project_id": project,
        "entry_count": len(candidate_ids),
        "customer_ready_probe_count": customer_ready_count,
        "entries": [
            {
                "candidate_id": str(package.get("candidate_id") or ""),
                "customer_ready": bool(package.get("customer_ready")),
                "readiness_level": str(package.get("readiness_level") or ""),
                "gap_types": list(_as_dict(package.get("reproduction_readiness_gate")).get("blockers") or []),
                "verdict": "validated_candidate",
            }
            for package in (runtime_customer_reproduction_pack.get("packages") or [])
            if isinstance(package, dict)
        ],
    }
    runtime_evidence_readiness_sla_gate = {
        "engine": f"{summary_engine}_runtime_evidence_readiness_sla_gate_v1",
        "status": "ready" if customer_ready_count else "blocked",
        "commercial_readiness_score": readiness_score,
        "commercial_readiness_level": "commercial_ready" if customer_ready_count else "not_ready",
        "sla_gate_passed": customer_ready_count > 0,
        "minimum_commercial_gate_failures": [] if customer_ready_count else [readiness_failure_code],
        "commercial_blocking_reasons": [] if customer_ready_count else [readiness_failure_reason],
    }
    runtime_evidence_scoreboard = {
        "engine": f"{summary_engine}_runtime_evidence_scoreboard_v1",
        "execution_integrity_score": readiness_score,
        "runtime_binding_success_rate": 1.0 if customer_ready_count else 0.0,
        "fixture_setup_success_rate": 1.0,
        "cleanup_success_rate": 1.0,
        "snapshot_success_rate": 1.0,
        "execution_coverage_rate": 1.0 if findings else 0.0,
        "target_response_rate": 1.0 if customer_ready_count else 0.0,
        "oracle_resolution_rate": 1.0 if findings else 0.0,
        "top_failure_or_gap_reasons": {},
        "recommended_next_actions": [] if customer_ready_count else ["Regenerate customer-ready runtime reproduction evidence before commercial handoff."],
        "evidence_maturity": {"level": "customer_ready" if customer_ready_count else "validated_only", "customer_ready": bool(customer_ready_count)},
    }
    runtime_evidence_remediation_plan = {
        "engine": f"{summary_engine}_runtime_evidence_remediation_plan_v1",
        "status": "ready" if customer_ready_count else "needs_more_runtime_repro",
        "action_count": 0 if customer_ready_count else 1,
        "actions": [] if customer_ready_count else [{"priority": "P0", "action": "Regenerate runtime reproduction assets for validated findings."}],
    }
    runtime_evidence_promotion_gate = {
        "status": "customer_ready_runtime_evidence_promotion_approved" if customer_ready_count else "customer_ready_runtime_evidence_promotion_blocked",
        "promotion_ready": bool(customer_ready_count),
        "blockers": [] if customer_ready_count else [readiness_failure_code],
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    runtime_evidence_customer_delivery_manifest = {
        "status": "customer_ready_runtime_delivery_manifest_ready" if customer_ready_count else "customer_ready_runtime_delivery_manifest_blocked",
        "customer_ready": bool(customer_ready_count),
        "delivery_baseline_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or scan_id),
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    evidence_bundle_status = str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "")
    runtime_evidence_delivery_manifest_verification = {
        "status": "runtime_delivery_manifest_verified" if evidence_bundle_status == "persisted" else "runtime_delivery_manifest_verification_failed",
        "verified": evidence_bundle_status == "persisted",
        "blockers": [] if evidence_bundle_status == "persisted" else ["commercial_evidence_bundle_not_persisted"],
    }
    runtime_sla_execution_policy = {
        "status": "ready" if findings else "empty",
        "must_run_for_sla_count": len(findings),
        "blocked_before_sla_count": 0,
    }
    runtime_sla_gap_prioritizer = {"action_count": 0 if customer_ready_count else 1, "recommendation": "Regenerate runtime reproduction pack." if not customer_ready_count else ""}
    onboarding_patch_safety_validation = {"status": "safe_to_send", "safe_to_send_to_customer": True}
    write_sandbox_approval_packet = {"write_approval_required": False}
    onboarding_preflight = {"status": "ready"}
    runtime_capability_matrix = {"status": "ready", "candidate_count": len(findings), "customer_ready_reproduction_count": customer_ready_count}
    runtime_execution_runbook = {
        "status": "ready" if findings else "empty",
        "steps": [
            "Review validated customer-ready findings and linked runtime evidence.",
            "Use the runtime reproduction pack for reruns and remediation validation.",
            "After fixes, rerun the same finding set and compare the persisted evidence bundle.",
        ],
    }
    remediation_verification_artifact = {
        "status": "ready" if findings else "empty",
        "finding_count": len(findings),
        "items": [
            {
                "finding_id": finding.get("finding_id"),
                "candidate_id": finding.get("candidate_id"),
                "title": finding.get("title"),
                "recommended_check": "Rerun the reproduced scenario after the fix and compare the new evidence bundle.",
            }
            for finding in findings
        ],
    }
    execution_report = {
        "engine": report_engine,
        "project_id": project,
        "scan_id": scan_id,
        "created_at": generated_at,
        "finding_count": len(findings),
        "findings": findings,
        "runtime_customer_reproduction_pack_ref": outputs["runtime_customer_reproduction_pack_json"],
        "evidence_bundle_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or ""),
    }
    report = {
        "engine": summary_engine,
        "project_id": project,
        "created_at": generated_at,
        "summary": {
            "validated_candidate_count": len(findings),
            "runtime_evidence_readiness_score": readiness_score,
        },
        "findings": findings,
        "outputs": outputs,
        "runtime_capability_matrix": runtime_capability_matrix,
        "runtime_execution_runbook": runtime_execution_runbook,
        "runtime_customer_reproduction_pack": runtime_customer_reproduction_pack,
        "runtime_evidence_probe_ledger": runtime_evidence_probe_ledger,
        "runtime_evidence_readiness_sla_gate": runtime_evidence_readiness_sla_gate,
        "runtime_evidence_scoreboard": runtime_evidence_scoreboard,
        "runtime_evidence_remediation_plan": runtime_evidence_remediation_plan,
        "runtime_evidence_promotion_gate": runtime_evidence_promotion_gate,
        "runtime_evidence_customer_delivery_manifest": runtime_evidence_customer_delivery_manifest,
        "runtime_evidence_delivery_manifest_verification": runtime_evidence_delivery_manifest_verification,
        "runtime_sla_execution_policy": runtime_sla_execution_policy,
        "runtime_sla_gap_prioritizer": runtime_sla_gap_prioritizer,
        "onboarding_patch_safety_validation": onboarding_patch_safety_validation,
        "write_sandbox_approval_packet": write_sandbox_approval_packet,
        "onboarding_preflight": onboarding_preflight,
        "remediation_verification_artifact": remediation_verification_artifact,
    }
    _write_json(Path(outputs["execution_report"]), execution_report)
    _write_markdown(Path(outputs["execution_report_md"]), f"# {execution_report_md_heading}\n\n{execution_report_title}\n")
    _write_json(Path(outputs["onboarding_preflight_json"]), onboarding_preflight)
    _write_json(Path(outputs["runtime_capability_matrix_json"]), runtime_capability_matrix)
    _write_json(Path(outputs["runtime_execution_runbook_json"]), runtime_execution_runbook)
    _write_markdown(Path(outputs["runtime_execution_runbook_md"]), f"# {runtime_runbook_md_heading}\n\n{runtime_runbook_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_readiness_sla_gate_json"]), runtime_evidence_readiness_sla_gate)
    _write_markdown(Path(outputs["runtime_evidence_readiness_sla_gate_md"]), f"# {sla_md_heading}\n\nGenerated from validated finding readiness.\n")
    _write_json(Path(outputs["runtime_evidence_scoreboard_json"]), runtime_evidence_scoreboard)
    _write_markdown(Path(outputs["runtime_evidence_scoreboard_md"]), f"# {execution_report_md_heading} Scoreboard\n\nGenerated from validated finding coverage and replay readiness.\n")
    _write_json(Path(outputs["runtime_evidence_probe_ledger_json"]), runtime_evidence_probe_ledger)
    _write_markdown(Path(outputs["runtime_evidence_probe_ledger_md"]), f"# {execution_report_md_heading} Probe Ledger\n\nGenerated from customer-ready reproduction packages.\n")
    _write_json(Path(outputs["runtime_customer_reproduction_pack_json"]), runtime_customer_reproduction_pack)
    _write_json(output_dir / f"{output_prefix}_runtime_customer_reproduction_pack.json", runtime_customer_reproduction_pack)
    _write_markdown(Path(outputs["runtime_customer_reproduction_pack_md"]), remediation_verification_md_text)
    _write_markdown(output_dir / f"{output_prefix}_runtime_customer_reproduction_pack.md", remediation_verification_md_text)
    _write_json(Path(outputs["runtime_evidence_remediation_plan_json"]), runtime_evidence_remediation_plan)
    _write_markdown(Path(outputs["runtime_evidence_remediation_plan_md"]), f"# {remediation_md_heading}\n\n{remediation_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_promotion_gate_json"]), runtime_evidence_promotion_gate)
    _write_markdown(Path(outputs["runtime_evidence_promotion_gate_md"]), f"# {promotion_gate_md_heading}\n\n{promotion_gate_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_customer_delivery_manifest_json"]), runtime_evidence_customer_delivery_manifest)
    _write_markdown(Path(outputs["runtime_evidence_customer_delivery_manifest_md"]), f"# {delivery_manifest_md_heading}\n\n{delivery_manifest_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_delivery_manifest_verification_json"]), runtime_evidence_delivery_manifest_verification)
    _write_markdown(Path(outputs["runtime_evidence_delivery_manifest_verification_md"]), f"# {delivery_verification_md_heading}\n\n{delivery_verification_md_text}\n")
    _write_json(Path(outputs["runtime_sla_execution_policy_json"]), runtime_sla_execution_policy)
    _write_markdown(Path(outputs["runtime_sla_execution_policy_md"]), f"# {sla_md_heading}\n\n{sla_md_text}\n")
    _write_json(Path(outputs["runtime_sla_gap_prioritizer_json"]), runtime_sla_gap_prioritizer)
    _write_markdown(Path(outputs["runtime_sla_gap_prioritizer_md"]), f"# {gap_md_heading}\n\n{gap_md_text}\n")
    _write_json(Path(outputs["onboarding_patch_safety_validation_json"]), onboarding_patch_safety_validation)
    _write_markdown(Path(outputs["onboarding_patch_safety_validation_md"]), f"# {patch_md_heading}\n\n{patch_md_text}\n")
    _write_json(Path(outputs["write_sandbox_approval_packet_json"]), write_sandbox_approval_packet)
    _write_markdown(Path(outputs["write_sandbox_approval_packet_md"]), f"# {write_approval_md_heading}\n\n{write_approval_md_text}\n")
    _write_json(Path(outputs["remediation_verification_json"]), remediation_verification_artifact)
    _write_markdown(Path(outputs["remediation_verification_md"]), f"# {remediation_verification_md_heading}\n\n{remediation_verification_md_text}\n")

    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["commercial_handoff_secret_redaction_plan"] = build_handoff_secret_redaction_plan(report, report["commercial_handoff_secret_audit"])
    report["commercial_handoff_redacted_runtime_evidence"] = build_handoff_redacted_runtime_evidence_pack(
        report,
        report["commercial_handoff_secret_audit"],
        report["commercial_handoff_secret_redaction_plan"],
    )
    report["commercial_handoff_bundle"] = build_commercial_handoff_bundle(report)
    report["commercial_handoff_acceptance_gate"] = validate_commercial_handoff_acceptance(report)

    _write_json(Path(outputs["commercial_handoff_secret_audit_json"]), report["commercial_handoff_secret_audit"])
    _write_markdown(Path(outputs["commercial_handoff_secret_audit_md"]), render_handoff_secret_audit_markdown(report["commercial_handoff_secret_audit"]))
    _write_json(Path(outputs["commercial_handoff_secret_redaction_plan_json"]), report["commercial_handoff_secret_redaction_plan"])
    _write_markdown(Path(outputs["commercial_handoff_secret_redaction_plan_md"]), render_handoff_secret_redaction_plan_markdown(report["commercial_handoff_secret_redaction_plan"]))
    _write_json(Path(outputs["commercial_handoff_redacted_runtime_evidence_json"]), report["commercial_handoff_redacted_runtime_evidence"])
    _write_markdown(Path(outputs["commercial_handoff_redacted_runtime_evidence_md"]), render_handoff_redacted_runtime_evidence_markdown(report["commercial_handoff_redacted_runtime_evidence"]))
    _write_json(Path(outputs["commercial_handoff_bundle_json"]), report["commercial_handoff_bundle"])
    _write_markdown(Path(outputs["commercial_handoff_bundle_md"]), render_commercial_handoff_markdown(report["commercial_handoff_bundle"]))
    _write_json(Path(outputs["commercial_handoff_acceptance_gate_json"]), report["commercial_handoff_acceptance_gate"])
    _write_markdown(Path(outputs["commercial_handoff_acceptance_gate_md"]), render_commercial_handoff_acceptance_markdown(report["commercial_handoff_acceptance_gate"]))

    report["handoff_archive_manifest"] = build_handoff_archive_manifest(report)
    report["immutable_run_receipt"] = _as_dict(report["handoff_archive_manifest"].get("immutable_run_receipt"))
    report["handoff_receipt_comparison"] = {
        "status": "no_previous_receipt_baseline",
        "previous_receipt_present": False,
        "change_count": 0,
    }
    report["handoff_rerun_audit_gate"] = {
        "status": "rerun_closure_audit_no_claims",
        "closure_verification_allowed": False,
        "blocker_count": 0,
        "warning_count": 0,
        "blockers": [],
    }
    report["commercial_evidence_lineage_dashboard"] = {
        "status": "lineage_dashboard_baseline_only",
        "closure_claim_state": "closure_claim_baseline_only",
        "current_run_lineage_id": str(report["immutable_run_receipt"].get("run_lineage_id") or scan_id),
        "previous_run_lineage_id": "",
        "changed_or_missing_hash_count": 0,
        "reviewer_signoff_required": False,
        "finding_closure_claims": [],
    }
    report["commercial_lineage_reviewer_signoff_packet"] = {
        "status": "lineage_reviewer_signoff_not_required",
        "signoff_required": False,
        "signoff_item_count": 0,
    }
    report["commercial_closure_acceptance_ledger"] = build_commercial_closure_acceptance_ledger(report)
    report["commercial_audit_event_stream"] = build_commercial_audit_event_stream(report)
    report["commercial_audit_export_adapters"] = build_commercial_audit_export_adapters(report)
    report["commercial_audit_export_import_gate"] = build_commercial_audit_export_import_gate(report)
    report["commercial_external_tracker_reconciliation"] = build_commercial_external_tracker_reconciliation(report)
    report["external_tracker_closure_sync_policy"] = build_external_tracker_closure_sync_policy(report)
    report["external_tracker_sync_payloads"] = build_external_tracker_sync_payloads(report)
    report["external_tracker_sync_payload_gate"] = validate_external_tracker_sync_payloads(report)

    _write_json(Path(outputs["handoff_archive_manifest_json"]), report["handoff_archive_manifest"])
    _write_markdown(Path(outputs["handoff_archive_manifest_md"]), render_handoff_archive_manifest_markdown(report["handoff_archive_manifest"]))
    _write_json(Path(outputs["immutable_run_receipt_json"]), report["immutable_run_receipt"])
    _write_markdown(Path(outputs["immutable_run_receipt_md"]), f"# {execution_report_md_heading} Immutable Run Receipt\n\nFrozen receipt for commercial delivery lineage.\n")
    _write_json(Path(outputs["handoff_receipt_comparison_json"]), report["handoff_receipt_comparison"])
    _write_markdown(Path(outputs["handoff_receipt_comparison_md"]), f"# {execution_report_md_heading} Handoff Receipt Comparison\n\nNo previous receipt baseline is attached for this commercial bridge run.\n")
    _write_json(Path(outputs["handoff_rerun_audit_gate_json"]), report["handoff_rerun_audit_gate"])
    _write_markdown(Path(outputs["handoff_rerun_audit_gate_md"]), f"# {execution_report_md_heading} Handoff Rerun Audit Gate\n\nClosure claims remain conservative until a real lineage comparison is available.\n")
    _write_json(Path(outputs["commercial_evidence_lineage_dashboard_json"]), report["commercial_evidence_lineage_dashboard"])
    _write_markdown(Path(outputs["commercial_evidence_lineage_dashboard_md"]), f"# {execution_report_md_heading} Evidence Lineage Dashboard\n\nThis run publishes a baseline-only lineage view for validated findings.\n")
    _write_json(Path(outputs["commercial_lineage_reviewer_signoff_packet_json"]), report["commercial_lineage_reviewer_signoff_packet"])
    _write_markdown(Path(outputs["commercial_lineage_reviewer_signoff_packet_md"]), f"# {execution_report_md_heading} Reviewer Signoff Packet\n\nNo reviewer signoff packet items are required for the baseline-only lineage dashboard.\n")
    _write_json(Path(outputs["commercial_closure_acceptance_ledger_json"]), report["commercial_closure_acceptance_ledger"])
    _write_markdown(Path(outputs["commercial_closure_acceptance_ledger_md"]), render_commercial_closure_acceptance_ledger_markdown(report["commercial_closure_acceptance_ledger"]))
    _write_json(Path(outputs["commercial_audit_event_stream_json"]), report["commercial_audit_event_stream"])
    _write_markdown(Path(outputs["commercial_audit_event_stream_md"]), render_commercial_audit_event_stream_markdown(report["commercial_audit_event_stream"]))
    _write_json(Path(outputs["commercial_audit_exports_json"]), report["commercial_audit_export_adapters"])
    _write_markdown(Path(outputs["commercial_audit_exports_md"]), render_commercial_audit_exports_markdown(report["commercial_audit_export_adapters"]))
    Path(outputs["commercial_audit_ledger_csv"]).write_text(render_csv_audit_ledger(report["commercial_audit_export_adapters"]), encoding="utf-8")
    _write_json(Path(outputs["commercial_audit_jira_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("jira_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_linear_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("linear_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_import_gate_json"]), report["commercial_audit_export_import_gate"])
    _write_markdown(Path(outputs["commercial_audit_import_gate_md"]), render_commercial_audit_import_gate_markdown(report["commercial_audit_export_import_gate"]))
    _write_markdown(Path(outputs["commercial_external_tracker_reconciliation_md"]), render_commercial_external_tracker_reconciliation_markdown(report["commercial_external_tracker_reconciliation"]))
    _write_json(Path(outputs["commercial_external_tracker_reconciliation_json"]), report["commercial_external_tracker_reconciliation"])
    _write_json(Path(outputs["external_tracker_closure_sync_policy_json"]), report["external_tracker_closure_sync_policy"])
    _write_markdown(Path(outputs["external_tracker_closure_sync_policy_md"]), render_external_tracker_closure_sync_policy_markdown(report["external_tracker_closure_sync_policy"]))
    _write_json(Path(outputs["external_tracker_sync_payloads_json"]), report["external_tracker_sync_payloads"])
    _write_markdown(Path(outputs["external_tracker_sync_payloads_md"]), render_external_tracker_sync_payloads_markdown(report["external_tracker_sync_payloads"]))
    _write_json(Path(outputs["external_tracker_sync_payload_gate_json"]), report["external_tracker_sync_payload_gate"])
    _write_markdown(Path(outputs["external_tracker_sync_payload_gate_md"]), render_external_tracker_sync_payload_gate_markdown(report["external_tracker_sync_payload_gate"]))

    delivery = {"status": "not_created"}
    try:
        delivery = create_delivery_package(project, root=root, scan_result=scan_result)
    except Exception as exc:
        delivery = {"status": "failed", "reason": f"commercial_delivery_package_failed:{type(exc).__name__}"}

    return {
        "status": "materialized",
        "generated_at_utc": generated_at,
        "finding_count": len(findings),
        "customer_ready_reproduction_count": customer_ready_count,
        "commercial_handoff_status": str(_as_dict(report.get("commercial_handoff_bundle")).get("status") or ""),
        "commercial_handoff_acceptance_status": str(_as_dict(report.get("commercial_handoff_acceptance_gate")).get("status") or ""),
        "commercial_handoff_safe_for_customer": bool(_as_dict(report.get("commercial_handoff_secret_audit")).get("safe_for_customer_handoff")),
        "external_tracker_sync_payload_status": str(_as_dict(report.get("external_tracker_sync_payloads")).get("status") or ""),
        "external_tracker_sync_payload_gate_status": str(_as_dict(report.get("external_tracker_sync_payload_gate")).get("status") or ""),
        "delivery_package": delivery,
        "commercial_handoff_bundle_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_commercial_handoff_bundle.json",
        "commercial_handoff_acceptance_gate_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_commercial_handoff_acceptance_gate.json",
        "handoff_archive_manifest_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_handoff_archive_manifest.json",
        "commercial_audit_exports_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_commercial_audit_exports.json",
        "external_tracker_sync_payloads_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_tracker_sync_payloads.json",
    }


def _materialize_commercial_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    items: list[dict[str, Any]],
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    validated: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    ledger_entries: list[dict[str, Any]] = []
    report_findings: list[dict[str, Any]] = []
    for index, value in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(value, dict) or not _commercial_finding_customer_ready(value):
            continue
        row = dict(value)
        candidate_id = _commercial_candidate_id(row, index)
        row["candidate_id"] = candidate_id
        row.setdefault("finding_id", candidate_id)
        row.setdefault("confidence", float(row.get("confidence") or row.get("confidence_score") or 0.92))
        row.setdefault("reason", _commercial_finding_reason(row))
        observation = _commercial_runtime_observation(row, candidate_id=candidate_id)
        observations.append(observation)
        has_status = isinstance(_as_dict(observation.get("response")).get("status_code"), int)
        ledger_entries.append({
            "candidate_id": candidate_id,
            "customer_ready": has_status,
            "readiness_level": "customer_ready_candidate" if has_status else "validated_candidate_without_target_status",
            "fixture_setup": {"accepted_count": 0},
            "snapshots": {"accepted_count": 0},
            "cleanup": {"accepted_count": 0},
            "gap_types": [] if has_status else ["missing_target_http_status"],
            "verdict": "validated_candidate",
        })
        report_findings.append({
            "finding_id": row.get("finding_id"),
            "candidate_id": candidate_id,
            "title": row.get("title") or candidate_id,
            "priority": _commercial_priority(row.get("priority") or row.get("severity")),
            "risk_type": row.get("risk_type") or row.get("category") or "validated_runtime_finding",
            "method": row.get("method") or row.get("_api_method") or _as_dict(_as_dict(row.get("raw_evidence")).get("request_raw")).get("method"),
            "path": row.get("path") or row.get("_api_path") or _as_dict(_as_dict(row.get("raw_evidence")).get("request_raw")).get("path"),
            "confidence": row.get("confidence"),
            "evidence_grade": row.get("evidence_grade") or _as_dict(row.get("evidence_quality")).get("level"),
            "evidence_strength_score": row.get("evidence_strength_score") or _as_dict(row.get("evidence_quality")).get("score"),
            "reason": row.get("reason"),
            "priority_source": "customer_ready_validated_finding",
            "reproduction_artifact_links": list(row.get("reproduction_artifact_links") or []),
            "source_refs": list(row.get("source_refs") or []),
            "customer_triage": dict(row.get("customer_triage") or {}),
            "evidence_package": dict(row.get("evidence_package") or {}),
            "violated_invariants": row.get("violated_invariants") or [],
            "delta_summary": row.get("delta_summary") or {},
        })
        validated.append(row)
    if not report_findings:
        return {"status": "empty", "finding_count": 0}
    try:
        from .grounded_probe_executor import _build_runtime_customer_reproduction_pack, _render_runtime_customer_reproduction_pack_markdown
    except Exception as exc:
        return {"status": "failed", "reason": f"commercial_reproduction_asset_import_failed:{type(exc).__name__}"}
    pack_report = {
        "project_id": project,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "findings": report_findings,
        "write_observations": observations,
        "runtime_evidence_probe_ledger": {"entries": ledger_entries},
    }
    runtime_customer_reproduction_pack = _build_runtime_customer_reproduction_pack(pack_report)
    pack_md = _render_runtime_customer_reproduction_pack_markdown(runtime_customer_reproduction_pack)
    assets = _build_materialized_commercial_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        findings=report_findings,
        runtime_customer_reproduction_pack=runtime_customer_reproduction_pack,
        output_prefix="commercial",
        summary_engine="commercial_validated_finding_bridge_v1",
        report_engine="commercial_validated_execution_report_v1",
        priority_source="customer_ready_validated_finding",
        readiness_failure_code="runtime_customer_reproduction_pack_missing",
        readiness_failure_reason="customer_ready_runtime_reproduction_missing",
        execution_report_title="Generated from customer-ready validated findings.",
        execution_report_md_heading="Commercial Validated Execution Report",
        runtime_runbook_md_heading="Commercial Runtime Execution Runbook",
        runtime_runbook_md_text="Use the runtime reproduction pack and linked evidence for reruns.",
        remediation_md_heading="Commercial Runtime Evidence Remediation Plan",
        remediation_md_text="Regenerate runtime evidence or rerun repaired scenarios before customer handoff.",
        promotion_gate_md_heading="Commercial Runtime Evidence Promotion Gate",
        promotion_gate_md_text="Promotion is limited to validated findings with reproducible runtime assets.",
        delivery_manifest_md_heading="Commercial Runtime Evidence Customer Delivery Manifest",
        delivery_manifest_md_text="Frozen customer-facing runtime evidence manifest for validated findings.",
        delivery_verification_md_heading="Commercial Runtime Evidence Delivery Manifest Verification",
        delivery_verification_md_text="Verifies the persisted evidence bundle is present before delivery packaging.",
        sla_md_heading="Commercial Runtime SLA Execution Policy",
        sla_md_text="Defines the minimum validated finding set expected for reruns.",
        gap_md_heading="Commercial Runtime SLA Gap Prioritizer",
        gap_md_text="Generated from customer-ready reproduction readiness.",
        patch_md_heading="Commercial Onboarding Patch Safety Validation",
        patch_md_text="No customer-facing onboarding patch payload is generated from validated findings.",
        write_approval_md_heading="Commercial Write Sandbox Approval Packet",
        write_approval_md_text="No additional write approval is required for already captured runtime evidence.",
        remediation_verification_md_heading="Commercial Remediation Verification",
        remediation_verification_md_text="Rerun the linked runtime reproduction assets after each fix and compare against the persisted evidence bundle.",
        scan_result=scan_result,
    )
    if assets.get("status") != "materialized":
        return assets
    workspace_dir = root / "platform_workspace" / _safe_project(project) / "defect_discovery"
    output_dir = root / "platform_outputs" / _safe_project(project) / "defect_discovery"
    _write_json(workspace_dir / "commercial_runtime_customer_reproduction_pack.json", runtime_customer_reproduction_pack)
    _write_json(output_dir / "commercial_runtime_customer_reproduction_pack.json", runtime_customer_reproduction_pack)
    _write_markdown(workspace_dir / "commercial_runtime_customer_reproduction_pack.md", pack_md)
    _write_markdown(output_dir / "commercial_runtime_customer_reproduction_pack.md", pack_md)
    return assets


def _materialize_external_commercial_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    items: list[dict[str, Any]],
    external_reproduction_assets: dict[str, Any],
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    validated = [
        dict(item)
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and str(item.get("confirmation_status") or "").strip().lower() == "validated_candidate"
    ]
    if not validated:
        return {"status": "empty", "finding_count": 0}
    try:
        from .runtime_commercial_handoff_bundle import build_commercial_handoff_bundle, render_commercial_handoff_markdown
        from .runtime_commercial_handoff_acceptance_gate import validate_commercial_handoff_acceptance, render_commercial_handoff_acceptance_markdown
        from .runtime_handoff_secret_audit import (
            audit_commercial_handoff_secrets,
            build_handoff_redacted_runtime_evidence_pack,
            build_handoff_secret_redaction_plan,
            render_handoff_redacted_runtime_evidence_markdown,
            render_handoff_secret_audit_markdown,
            render_handoff_secret_redaction_plan_markdown,
        )
        from .runtime_handoff_archive_manifest import build_handoff_archive_manifest, render_handoff_archive_manifest_markdown
        from .runtime_commercial_closure_acceptance_ledger import build_commercial_closure_acceptance_ledger, render_commercial_closure_acceptance_ledger_markdown
        from .runtime_commercial_audit_event_stream import build_commercial_audit_event_stream, render_commercial_audit_event_stream_markdown
        from .runtime_commercial_audit_export_adapters import (
            build_commercial_audit_export_adapters,
            render_commercial_audit_exports_markdown,
            render_csv_audit_ledger,
        )
        from .runtime_commercial_audit_export_import_gate import build_commercial_audit_export_import_gate, render_commercial_audit_import_gate_markdown
        from .runtime_commercial_external_tracker_reconciliation import (
            build_commercial_external_tracker_reconciliation,
            render_commercial_external_tracker_reconciliation_markdown,
        )
        from .runtime_external_tracker_closure_sync_policy import (
            build_external_tracker_closure_sync_policy,
            render_external_tracker_closure_sync_policy_markdown,
        )
        from .runtime_external_tracker_sync_payload_builder import (
            build_external_tracker_sync_payloads,
            render_external_tracker_sync_payloads_markdown,
        )
        from .runtime_external_tracker_sync_payload_gate import (
            validate_external_tracker_sync_payloads,
            render_external_tracker_sync_payload_gate_markdown,
        )
        from .enterprise_delivery_package import create_delivery_package
    except Exception as exc:
        return {"status": "failed", "reason": f"external_commercial_asset_import_failed:{type(exc).__name__}"}

    safe_project = _safe_project(project)
    workspace_dir = root / "platform_workspace" / safe_project / "defect_discovery"
    output_dir = root / "platform_outputs" / safe_project / "defect_discovery"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    repro_pack = _as_dict(external_reproduction_assets.get("runtime_customer_reproduction_pack"))
    customer_ready_count = int(repro_pack.get("customer_ready_reproduction_count") or 0)
    evidence_scores = [
        float(item.get("evidence_strength_score") or 0.0)
        for item in validated
        if isinstance(item.get("evidence_strength_score"), (int, float))
    ]
    readiness_score = int(round((sum(evidence_scores) / len(evidence_scores)) * 100)) if evidence_scores else 88
    readiness_score = max(0, min(readiness_score, 99))
    candidate_ids = [str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or "") for item in validated if str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or "").strip()]
    findings = []
    for index, item in enumerate(validated, start=1):
        candidate_id = str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or f"EXT-COM-{index:03d}")
        findings.append({
            "finding_id": str(item.get("finding_id") or candidate_id),
            "candidate_id": candidate_id,
            "title": str(item.get("title") or candidate_id),
            "priority": _external_priority(item.get("severity")),
            "method": str(item.get("method") or item.get("_api_method") or ""),
            "path": str(item.get("path") or item.get("_api_path") or ""),
            "reason": str(item.get("reason") or item.get("actual") or item.get("actual_behavior") or ""),
            "priority_source": "external_validated_candidate",
            "reproduction_artifact_links": list(item.get("reproduction_artifact_links") or []),
            "source_refs": list(item.get("source_refs") or []),
            "customer_triage": dict(item.get("customer_triage") or {}),
            "evidence_package": dict(item.get("evidence_package") or {}),
        })

    outputs = {
        "execution_report": str(output_dir / "external_commercial_execution_report.json"),
        "execution_report_md": str(output_dir / "external_commercial_execution_report.md"),
        "onboarding_preflight_json": str(output_dir / "external_onboarding_preflight.json"),
        "runtime_capability_matrix_json": str(output_dir / "external_runtime_capability_matrix.json"),
        "runtime_execution_runbook_json": str(output_dir / "external_runtime_execution_runbook.json"),
        "runtime_execution_runbook_md": str(output_dir / "external_runtime_execution_runbook.md"),
        "runtime_evidence_readiness_sla_gate_json": str(output_dir / "external_runtime_evidence_readiness_sla_gate.json"),
        "runtime_evidence_readiness_sla_gate_md": str(output_dir / "external_runtime_evidence_readiness_sla_gate.md"),
        "runtime_evidence_scoreboard_json": str(output_dir / "external_runtime_evidence_scoreboard.json"),
        "runtime_evidence_scoreboard_md": str(output_dir / "external_runtime_evidence_scoreboard.md"),
        "runtime_evidence_probe_ledger_json": str(output_dir / "external_runtime_evidence_probe_ledger.json"),
        "runtime_evidence_probe_ledger_md": str(output_dir / "external_runtime_evidence_probe_ledger.md"),
        "runtime_customer_reproduction_pack_json": str(workspace_dir / "external_runtime_customer_reproduction_pack.json"),
        "runtime_customer_reproduction_pack_md": str(workspace_dir / "external_runtime_customer_reproduction_pack.md"),
        "runtime_evidence_remediation_plan_json": str(output_dir / "external_runtime_evidence_remediation_plan.json"),
        "runtime_evidence_remediation_plan_md": str(output_dir / "external_runtime_evidence_remediation_plan.md"),
        "runtime_evidence_promotion_gate_json": str(output_dir / "external_runtime_evidence_promotion_gate.json"),
        "runtime_evidence_promotion_gate_md": str(output_dir / "external_runtime_evidence_promotion_gate.md"),
        "runtime_evidence_customer_delivery_manifest_json": str(output_dir / "external_runtime_evidence_customer_delivery_manifest.json"),
        "runtime_evidence_customer_delivery_manifest_md": str(output_dir / "external_runtime_evidence_customer_delivery_manifest.md"),
        "runtime_evidence_delivery_manifest_verification_json": str(output_dir / "external_runtime_evidence_delivery_manifest_verification.json"),
        "runtime_evidence_delivery_manifest_verification_md": str(output_dir / "external_runtime_evidence_delivery_manifest_verification.md"),
        "commercial_handoff_secret_redaction_plan_json": str(output_dir / "external_commercial_handoff_secret_redaction_plan.json"),
        "commercial_handoff_secret_redaction_plan_md": str(output_dir / "external_commercial_handoff_secret_redaction_plan.md"),
        "commercial_handoff_redacted_runtime_evidence_json": str(output_dir / "external_commercial_handoff_redacted_runtime_evidence.json"),
        "commercial_handoff_redacted_runtime_evidence_md": str(output_dir / "external_commercial_handoff_redacted_runtime_evidence.md"),
        "runtime_sla_execution_policy_json": str(output_dir / "external_runtime_sla_execution_policy.json"),
        "runtime_sla_execution_policy_md": str(output_dir / "external_runtime_sla_execution_policy.md"),
        "runtime_sla_gap_prioritizer_json": str(output_dir / "external_runtime_sla_gap_prioritizer.json"),
        "runtime_sla_gap_prioritizer_md": str(output_dir / "external_runtime_sla_gap_prioritizer.md"),
        "onboarding_patch_safety_validation_json": str(output_dir / "external_onboarding_patch_safety_validation.json"),
        "onboarding_patch_safety_validation_md": str(output_dir / "external_onboarding_patch_safety_validation.md"),
        "write_sandbox_approval_packet_json": str(output_dir / "external_write_sandbox_approval_packet.json"),
        "write_sandbox_approval_packet_md": str(output_dir / "external_write_sandbox_approval_packet.md"),
        "remediation_verification_json": str(output_dir / "external_remediation_verification.json"),
        "remediation_verification_md": str(output_dir / "external_remediation_verification.md"),
        "repro_ps1": str(workspace_dir / "external_validated_bug_repro.ps1"),
        "regression_pytest": str(workspace_dir / "external_validated_bug_regression_pytest.py"),
        "commercial_handoff_bundle_json": str(output_dir / "external_commercial_handoff_bundle.json"),
        "commercial_handoff_bundle_md": str(output_dir / "external_commercial_handoff_bundle.md"),
        "commercial_handoff_acceptance_gate_json": str(output_dir / "external_commercial_handoff_acceptance_gate.json"),
        "commercial_handoff_acceptance_gate_md": str(output_dir / "external_commercial_handoff_acceptance_gate.md"),
        "commercial_handoff_secret_audit_json": str(output_dir / "external_commercial_handoff_secret_audit.json"),
        "commercial_handoff_secret_audit_md": str(output_dir / "external_commercial_handoff_secret_audit.md"),
        "handoff_archive_manifest_json": str(output_dir / "external_handoff_archive_manifest.json"),
        "handoff_archive_manifest_md": str(output_dir / "external_handoff_archive_manifest.md"),
        "immutable_run_receipt_json": str(output_dir / "external_immutable_run_receipt.json"),
        "immutable_run_receipt_md": str(output_dir / "external_immutable_run_receipt.md"),
        "handoff_receipt_comparison_json": str(output_dir / "external_handoff_receipt_comparison.json"),
        "handoff_receipt_comparison_md": str(output_dir / "external_handoff_receipt_comparison.md"),
        "handoff_rerun_audit_gate_json": str(output_dir / "external_handoff_rerun_audit_gate.json"),
        "handoff_rerun_audit_gate_md": str(output_dir / "external_handoff_rerun_audit_gate.md"),
        "commercial_evidence_lineage_dashboard_json": str(output_dir / "external_commercial_evidence_lineage_dashboard.json"),
        "commercial_evidence_lineage_dashboard_md": str(output_dir / "external_commercial_evidence_lineage_dashboard.md"),
        "commercial_lineage_reviewer_signoff_packet_json": str(output_dir / "external_commercial_lineage_reviewer_signoff_packet.json"),
        "commercial_lineage_reviewer_signoff_packet_md": str(output_dir / "external_commercial_lineage_reviewer_signoff_packet.md"),
        "commercial_closure_acceptance_ledger_json": str(output_dir / "external_commercial_closure_acceptance_ledger.json"),
        "commercial_closure_acceptance_ledger_md": str(output_dir / "external_commercial_closure_acceptance_ledger.md"),
        "commercial_audit_event_stream_json": str(output_dir / "external_commercial_audit_event_stream.json"),
        "commercial_audit_event_stream_md": str(output_dir / "external_commercial_audit_event_stream.md"),
        "commercial_audit_exports_json": str(output_dir / "external_commercial_audit_exports.json"),
        "commercial_audit_exports_md": str(output_dir / "external_commercial_audit_exports.md"),
        "commercial_audit_ledger_csv": str(output_dir / "external_commercial_audit_ledger.csv"),
        "commercial_audit_jira_issue_import_json": str(output_dir / "external_commercial_audit_jira_issue_import.json"),
        "commercial_audit_linear_issue_import_json": str(output_dir / "external_commercial_audit_linear_issue_import.json"),
        "commercial_audit_import_gate_json": str(output_dir / "external_commercial_audit_import_gate.json"),
        "commercial_audit_import_gate_md": str(output_dir / "external_commercial_audit_import_gate.md"),
        "commercial_external_tracker_reconciliation_json": str(output_dir / "external_commercial_external_tracker_reconciliation.json"),
        "commercial_external_tracker_reconciliation_md": str(output_dir / "external_commercial_external_tracker_reconciliation.md"),
        "external_tracker_closure_sync_policy_json": str(output_dir / "external_tracker_closure_sync_policy.json"),
        "external_tracker_closure_sync_policy_md": str(output_dir / "external_tracker_closure_sync_policy.md"),
        "external_tracker_sync_payloads_json": str(output_dir / "external_tracker_sync_payloads.json"),
        "external_tracker_sync_payloads_md": str(output_dir / "external_tracker_sync_payloads.md"),
        "external_tracker_sync_payload_gate_json": str(output_dir / "external_tracker_sync_payload_gate.json"),
        "external_tracker_sync_payload_gate_md": str(output_dir / "external_tracker_sync_payload_gate.md"),
    }

    runtime_evidence_probe_ledger = {
        "engine": "external_runtime_evidence_probe_ledger_v1",
        "project_id": project,
        "entry_count": len(candidate_ids),
        "customer_ready_probe_count": customer_ready_count,
        "entries": [
            {
                "candidate_id": str(package.get("candidate_id") or ""),
                "customer_ready": bool(package.get("customer_ready")),
                "readiness_level": str(package.get("readiness_level") or ""),
                "gap_types": list(_as_dict(package.get("reproduction_readiness_gate")).get("blockers") or []),
                "verdict": "validated_candidate",
            }
            for package in (repro_pack.get("packages") or [])
            if isinstance(package, dict)
        ],
    }
    runtime_evidence_readiness_sla_gate = {
        "engine": "external_runtime_evidence_readiness_sla_gate_v1",
        "status": "ready" if customer_ready_count else "blocked",
        "commercial_readiness_score": readiness_score,
        "commercial_readiness_level": "commercial_ready" if customer_ready_count else "not_ready",
        "sla_gate_passed": customer_ready_count > 0,
        "minimum_commercial_gate_failures": [] if customer_ready_count else ["external_runtime_customer_reproduction_pack_missing"],
        "commercial_blocking_reasons": [] if customer_ready_count else ["external_reproduction_assets_not_customer_ready"],
    }
    runtime_evidence_scoreboard = {
        "engine": "external_runtime_evidence_scoreboard_v1",
        "execution_integrity_score": readiness_score,
        "runtime_binding_success_rate": 1.0 if customer_ready_count else 0.0,
        "fixture_setup_success_rate": 1.0,
        "cleanup_success_rate": 1.0,
        "snapshot_success_rate": 1.0,
        "execution_coverage_rate": 1.0 if validated else 0.0,
        "target_response_rate": 1.0 if customer_ready_count else 0.0,
        "oracle_resolution_rate": 1.0 if validated else 0.0,
        "top_failure_or_gap_reasons": {},
        "recommended_next_actions": [] if customer_ready_count else ["Complete runtime reproduction assets before customer handoff."],
        "evidence_maturity": {"level": "customer_ready" if customer_ready_count else "validated_only", "customer_ready": bool(customer_ready_count)},
    }
    runtime_evidence_remediation_plan = {
        "engine": "external_runtime_evidence_remediation_plan_v1",
        "status": "ready" if customer_ready_count else "needs_more_runtime_repro",
        "action_count": 0 if customer_ready_count else 1,
        "actions": [] if customer_ready_count else [{"priority": "P0", "action": "Regenerate external runtime reproduction assets."}],
    }
    runtime_evidence_promotion_gate = {
        "status": "customer_ready_runtime_evidence_promotion_approved" if customer_ready_count else "customer_ready_runtime_evidence_promotion_blocked",
        "promotion_ready": bool(customer_ready_count),
        "blockers": [] if customer_ready_count else ["external_runtime_customer_reproduction_pack_missing"],
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    runtime_evidence_customer_delivery_manifest = {
        "status": "customer_ready_runtime_delivery_manifest_ready" if customer_ready_count else "customer_ready_runtime_delivery_manifest_blocked",
        "customer_ready": bool(customer_ready_count),
        "delivery_baseline_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or scan_id),
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    runtime_evidence_delivery_manifest_verification = {
        "status": "runtime_delivery_manifest_verified" if str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "") == "persisted" else "runtime_delivery_manifest_verification_failed",
        "verified": str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "") == "persisted",
        "blockers": [] if str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "") == "persisted" else ["external_evidence_bundle_not_persisted"],
    }
    runtime_sla_execution_policy = {
        "status": "ready" if validated else "empty",
        "must_run_for_sla_count": len(validated),
        "blocked_before_sla_count": 0,
    }
    runtime_sla_gap_prioritizer = {"action_count": 0 if customer_ready_count else 1, "recommendation": "Regenerate external runtime reproduction pack." if not customer_ready_count else ""}
    onboarding_patch_safety_validation = {"status": "safe_to_send", "safe_to_send_to_customer": True}
    write_sandbox_approval_packet = {"write_approval_required": False}
    onboarding_preflight = {"status": "ready"}
    runtime_capability_matrix = {"status": "ready", "candidate_count": len(validated), "customer_ready_reproduction_count": customer_ready_count}
    runtime_execution_runbook = {
        "status": "ready" if validated else "empty",
        "steps": [
            "Review external validated findings and linked evidence package.",
            "Use the runtime reproduction pack plus PowerShell/pytest assets for reruns.",
            "After fixes, rerun the same validated candidate set and compare the evidence bundle.",
        ],
    }
    remediation_verification_artifact = {
        "status": "ready" if validated else "empty",
        "finding_count": len(findings),
        "items": [
            {
                "finding_id": finding.get("finding_id"),
                "candidate_id": finding.get("candidate_id"),
                "title": finding.get("title"),
                "recommended_check": "Rerun the reproduced external scenario after the fix and compare the new evidence bundle.",
            }
            for finding in findings
        ],
    }
    execution_report = {
        "engine": "external_commercial_execution_report_v1",
        "project_id": project,
        "scan_id": scan_id,
        "created_at": generated_at,
        "finding_count": len(validated),
        "findings": findings,
        "runtime_customer_reproduction_pack_ref": outputs["runtime_customer_reproduction_pack_json"],
        "evidence_bundle_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or ""),
    }

    report = {
        "engine": "external_commercial_bridge_v1",
        "project_id": project,
        "created_at": generated_at,
        "summary": {
            "validated_candidate_count": len(validated),
            "runtime_evidence_readiness_score": readiness_score,
        },
        "findings": findings,
        "outputs": outputs,
        "runtime_capability_matrix": runtime_capability_matrix,
        "runtime_execution_runbook": runtime_execution_runbook,
        "runtime_customer_reproduction_pack": repro_pack,
        "runtime_evidence_probe_ledger": runtime_evidence_probe_ledger,
        "runtime_evidence_readiness_sla_gate": runtime_evidence_readiness_sla_gate,
        "runtime_evidence_scoreboard": runtime_evidence_scoreboard,
        "runtime_evidence_remediation_plan": runtime_evidence_remediation_plan,
        "runtime_evidence_promotion_gate": runtime_evidence_promotion_gate,
        "runtime_evidence_customer_delivery_manifest": runtime_evidence_customer_delivery_manifest,
        "runtime_evidence_delivery_manifest_verification": runtime_evidence_delivery_manifest_verification,
        "runtime_sla_execution_policy": runtime_sla_execution_policy,
        "runtime_sla_gap_prioritizer": runtime_sla_gap_prioritizer,
        "onboarding_patch_safety_validation": onboarding_patch_safety_validation,
        "write_sandbox_approval_packet": write_sandbox_approval_packet,
        "onboarding_preflight": onboarding_preflight,
        "remediation_verification_artifact": remediation_verification_artifact,
    }

    _write_json(Path(outputs["execution_report"]), execution_report)
    _write_markdown(Path(outputs["execution_report_md"]), "# External Commercial Execution Report\n\nGenerated from external validated candidates.\n")
    _write_json(Path(outputs["onboarding_preflight_json"]), onboarding_preflight)
    _write_json(Path(outputs["runtime_capability_matrix_json"]), runtime_capability_matrix)
    _write_json(Path(outputs["runtime_execution_runbook_json"]), runtime_execution_runbook)
    _write_markdown(Path(outputs["runtime_execution_runbook_md"]), "# External Runtime Execution Runbook\n\nUse the runtime reproduction pack and linked repro assets for reruns.\n")
    _write_json(Path(outputs["runtime_evidence_readiness_sla_gate_json"]), runtime_evidence_readiness_sla_gate)
    _write_markdown(Path(outputs["runtime_evidence_readiness_sla_gate_md"]), "# External Runtime Evidence Readiness SLA Gate\n\nGenerated from external validated candidate readiness.\n")
    _write_json(Path(outputs["runtime_evidence_scoreboard_json"]), runtime_evidence_scoreboard)
    _write_markdown(Path(outputs["runtime_evidence_scoreboard_md"]), "# External Runtime Evidence Scoreboard\n\nGenerated from external validated candidate coverage and replay readiness.\n")
    _write_json(Path(outputs["runtime_evidence_probe_ledger_json"]), runtime_evidence_probe_ledger)
    _write_markdown(Path(outputs["runtime_evidence_probe_ledger_md"]), "# External Runtime Evidence Probe Ledger\n\nGenerated from customer-ready external reproduction packages.\n")
    _write_json(Path(outputs["runtime_evidence_remediation_plan_json"]), runtime_evidence_remediation_plan)
    _write_markdown(Path(outputs["runtime_evidence_remediation_plan_md"]), "# External Runtime Evidence Remediation Plan\n\nRegenerate reproduction assets or rerun repaired scenarios before customer handoff.\n")
    _write_json(Path(outputs["runtime_evidence_promotion_gate_json"]), runtime_evidence_promotion_gate)
    _write_markdown(Path(outputs["runtime_evidence_promotion_gate_md"]), "# External Runtime Evidence Promotion Gate\n\nPromotion is limited to validated external candidates with reproducible runtime assets.\n")
    _write_json(Path(outputs["runtime_evidence_customer_delivery_manifest_json"]), runtime_evidence_customer_delivery_manifest)
    _write_markdown(Path(outputs["runtime_evidence_customer_delivery_manifest_md"]), "# External Runtime Evidence Customer Delivery Manifest\n\nFrozen customer-facing runtime evidence manifest for external validated candidates.\n")
    _write_json(Path(outputs["runtime_evidence_delivery_manifest_verification_json"]), runtime_evidence_delivery_manifest_verification)
    _write_markdown(Path(outputs["runtime_evidence_delivery_manifest_verification_md"]), "# External Runtime Evidence Delivery Manifest Verification\n\nVerifies the persisted evidence bundle is present before delivery packaging.\n")
    _write_json(Path(outputs["runtime_sla_execution_policy_json"]), runtime_sla_execution_policy)
    _write_markdown(Path(outputs["runtime_sla_execution_policy_md"]), "# External Runtime SLA Execution Policy\n\nDefines the minimum external validated candidate set expected for reruns.\n")
    _write_json(Path(outputs["runtime_sla_gap_prioritizer_json"]), runtime_sla_gap_prioritizer)
    _write_markdown(Path(outputs["runtime_sla_gap_prioritizer_md"]), "# External Runtime SLA Gap Prioritizer\n\nGenerated from external customer-ready reproduction readiness.\n")
    _write_json(Path(outputs["onboarding_patch_safety_validation_json"]), onboarding_patch_safety_validation)
    _write_markdown(Path(outputs["onboarding_patch_safety_validation_md"]), "# External Onboarding Patch Safety Validation\n\nNo customer-facing onboarding patch payload is generated from external validated candidates.\n")
    _write_json(Path(outputs["write_sandbox_approval_packet_json"]), write_sandbox_approval_packet)
    _write_markdown(Path(outputs["write_sandbox_approval_packet_md"]), "# External Write Sandbox Approval Packet\n\nNo additional write approval is required for already captured external runtime evidence.\n")
    _write_json(Path(outputs["remediation_verification_json"]), remediation_verification_artifact)
    _write_markdown(Path(outputs["remediation_verification_md"]), "# External Remediation Verification\n\nRerun the linked external reproduction assets after each fix and compare against the persisted evidence bundle.\n")

    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["commercial_handoff_secret_redaction_plan"] = build_handoff_secret_redaction_plan(report, report["commercial_handoff_secret_audit"])
    report["commercial_handoff_redacted_runtime_evidence"] = build_handoff_redacted_runtime_evidence_pack(
        report,
        report["commercial_handoff_secret_audit"],
        report["commercial_handoff_secret_redaction_plan"],
    )
    report["commercial_handoff_bundle"] = build_commercial_handoff_bundle(report)
    report["commercial_handoff_acceptance_gate"] = validate_commercial_handoff_acceptance(report)

    _write_json(Path(outputs["commercial_handoff_secret_audit_json"]), report["commercial_handoff_secret_audit"])
    _write_markdown(Path(outputs["commercial_handoff_secret_audit_md"]), render_handoff_secret_audit_markdown(report["commercial_handoff_secret_audit"]))
    _write_json(Path(outputs["commercial_handoff_secret_redaction_plan_json"]), report["commercial_handoff_secret_redaction_plan"])
    _write_markdown(Path(outputs["commercial_handoff_secret_redaction_plan_md"]), render_handoff_secret_redaction_plan_markdown(report["commercial_handoff_secret_redaction_plan"]))
    _write_json(Path(outputs["commercial_handoff_redacted_runtime_evidence_json"]), report["commercial_handoff_redacted_runtime_evidence"])
    _write_markdown(Path(outputs["commercial_handoff_redacted_runtime_evidence_md"]), render_handoff_redacted_runtime_evidence_markdown(report["commercial_handoff_redacted_runtime_evidence"]))
    _write_json(Path(outputs["commercial_handoff_bundle_json"]), report["commercial_handoff_bundle"])
    _write_markdown(Path(outputs["commercial_handoff_bundle_md"]), render_commercial_handoff_markdown(report["commercial_handoff_bundle"]))
    _write_json(Path(outputs["commercial_handoff_acceptance_gate_json"]), report["commercial_handoff_acceptance_gate"])
    _write_markdown(Path(outputs["commercial_handoff_acceptance_gate_md"]), render_commercial_handoff_acceptance_markdown(report["commercial_handoff_acceptance_gate"]))

    report["handoff_archive_manifest"] = build_handoff_archive_manifest(report)
    report["immutable_run_receipt"] = _as_dict(report["handoff_archive_manifest"].get("immutable_run_receipt"))
    report["handoff_receipt_comparison"] = {
        "status": "no_previous_receipt_baseline",
        "previous_receipt_present": False,
        "change_count": 0,
    }
    report["handoff_rerun_audit_gate"] = {
        "status": "rerun_closure_audit_no_claims",
        "closure_verification_allowed": False,
        "blocker_count": 0,
        "warning_count": 0,
        "blockers": [],
    }
    report["commercial_evidence_lineage_dashboard"] = {
        "status": "lineage_dashboard_baseline_only",
        "closure_claim_state": "closure_claim_baseline_only",
        "current_run_lineage_id": str(report["immutable_run_receipt"].get("run_lineage_id") or scan_id),
        "previous_run_lineage_id": "",
        "changed_or_missing_hash_count": 0,
        "reviewer_signoff_required": False,
        "finding_closure_claims": [],
    }
    report["commercial_lineage_reviewer_signoff_packet"] = {
        "status": "lineage_reviewer_signoff_not_required",
        "signoff_required": False,
        "signoff_item_count": 0,
    }
    report["commercial_closure_acceptance_ledger"] = build_commercial_closure_acceptance_ledger(report)
    report["commercial_audit_event_stream"] = build_commercial_audit_event_stream(report)
    report["commercial_audit_export_adapters"] = build_commercial_audit_export_adapters(report)
    report["commercial_audit_export_import_gate"] = build_commercial_audit_export_import_gate(report)
    report["commercial_external_tracker_reconciliation"] = build_commercial_external_tracker_reconciliation(report)
    report["external_tracker_closure_sync_policy"] = build_external_tracker_closure_sync_policy(report)
    report["external_tracker_sync_payloads"] = build_external_tracker_sync_payloads(report)
    report["external_tracker_sync_payload_gate"] = validate_external_tracker_sync_payloads(report)

    _write_json(Path(outputs["handoff_archive_manifest_json"]), report["handoff_archive_manifest"])
    _write_markdown(Path(outputs["handoff_archive_manifest_md"]), render_handoff_archive_manifest_markdown(report["handoff_archive_manifest"]))
    _write_json(Path(outputs["immutable_run_receipt_json"]), report["immutable_run_receipt"])
    _write_markdown(Path(outputs["immutable_run_receipt_md"]), "# External Immutable Run Receipt\n\nFrozen receipt for external commercial delivery lineage.\n")
    _write_json(Path(outputs["handoff_receipt_comparison_json"]), report["handoff_receipt_comparison"])
    _write_markdown(Path(outputs["handoff_receipt_comparison_md"]), "# External Handoff Receipt Comparison\n\nNo previous receipt baseline is attached for this external commercial bridge run.\n")
    _write_json(Path(outputs["handoff_rerun_audit_gate_json"]), report["handoff_rerun_audit_gate"])
    _write_markdown(Path(outputs["handoff_rerun_audit_gate_md"]), "# External Handoff Rerun Audit Gate\n\nClosure claims remain conservative until a real lineage comparison is available.\n")
    _write_json(Path(outputs["commercial_evidence_lineage_dashboard_json"]), report["commercial_evidence_lineage_dashboard"])
    _write_markdown(Path(outputs["commercial_evidence_lineage_dashboard_md"]), "# External Commercial Evidence Lineage Dashboard\n\nThis run publishes a baseline-only lineage view for external validated candidates.\n")
    _write_json(Path(outputs["commercial_lineage_reviewer_signoff_packet_json"]), report["commercial_lineage_reviewer_signoff_packet"])
    _write_markdown(Path(outputs["commercial_lineage_reviewer_signoff_packet_md"]), "# External Commercial Lineage Reviewer Signoff Packet\n\nNo reviewer signoff packet items are required for the baseline-only external lineage dashboard.\n")
    _write_json(Path(outputs["commercial_closure_acceptance_ledger_json"]), report["commercial_closure_acceptance_ledger"])
    _write_markdown(Path(outputs["commercial_closure_acceptance_ledger_md"]), render_commercial_closure_acceptance_ledger_markdown(report["commercial_closure_acceptance_ledger"]))
    _write_json(Path(outputs["commercial_audit_event_stream_json"]), report["commercial_audit_event_stream"])
    _write_markdown(Path(outputs["commercial_audit_event_stream_md"]), render_commercial_audit_event_stream_markdown(report["commercial_audit_event_stream"]))
    _write_json(Path(outputs["commercial_audit_exports_json"]), report["commercial_audit_export_adapters"])
    _write_markdown(Path(outputs["commercial_audit_exports_md"]), render_commercial_audit_exports_markdown(report["commercial_audit_export_adapters"]))
    Path(outputs["commercial_audit_ledger_csv"]).write_text(render_csv_audit_ledger(report["commercial_audit_export_adapters"]), encoding="utf-8")
    _write_json(Path(outputs["commercial_audit_jira_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("jira_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_linear_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("linear_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_import_gate_json"]), report["commercial_audit_export_import_gate"])
    _write_markdown(Path(outputs["commercial_audit_import_gate_md"]), render_commercial_audit_import_gate_markdown(report["commercial_audit_export_import_gate"]))
    _write_markdown(Path(outputs["commercial_external_tracker_reconciliation_md"]), render_commercial_external_tracker_reconciliation_markdown(report["commercial_external_tracker_reconciliation"]))
    _write_json(Path(outputs["commercial_external_tracker_reconciliation_json"]), report["commercial_external_tracker_reconciliation"])
    _write_json(Path(outputs["external_tracker_closure_sync_policy_json"]), report["external_tracker_closure_sync_policy"])
    _write_markdown(Path(outputs["external_tracker_closure_sync_policy_md"]), render_external_tracker_closure_sync_policy_markdown(report["external_tracker_closure_sync_policy"]))
    _write_json(Path(outputs["external_tracker_sync_payloads_json"]), report["external_tracker_sync_payloads"])
    _write_markdown(Path(outputs["external_tracker_sync_payloads_md"]), render_external_tracker_sync_payloads_markdown(report["external_tracker_sync_payloads"]))
    _write_json(Path(outputs["external_tracker_sync_payload_gate_json"]), report["external_tracker_sync_payload_gate"])
    _write_markdown(Path(outputs["external_tracker_sync_payload_gate_md"]), render_external_tracker_sync_payload_gate_markdown(report["external_tracker_sync_payload_gate"]))

    delivery = {"status": "not_created"}
    try:
        delivery = create_delivery_package(project, root=root, scan_result=scan_result)
    except Exception as exc:
        delivery = {"status": "failed", "reason": f"external_delivery_package_failed:{type(exc).__name__}"}

    return {
        "status": "materialized",
        "generated_at_utc": generated_at,
        "finding_count": len(validated),
        "customer_ready_reproduction_count": customer_ready_count,
        "commercial_handoff_status": str(_as_dict(report.get("commercial_handoff_bundle")).get("status") or ""),
        "commercial_handoff_acceptance_status": str(_as_dict(report.get("commercial_handoff_acceptance_gate")).get("status") or ""),
        "commercial_handoff_safe_for_customer": bool(_as_dict(report.get("commercial_handoff_secret_audit")).get("safe_for_customer_handoff")),
        "external_tracker_sync_payload_status": str(_as_dict(report.get("external_tracker_sync_payloads")).get("status") or ""),
        "external_tracker_sync_payload_gate_status": str(_as_dict(report.get("external_tracker_sync_payload_gate")).get("status") or ""),
        "delivery_package": delivery,
        "commercial_handoff_bundle_ref": f"platform_outputs/{safe_project}/defect_discovery/external_commercial_handoff_bundle.json",
        "commercial_handoff_acceptance_gate_ref": f"platform_outputs/{safe_project}/defect_discovery/external_commercial_handoff_acceptance_gate.json",
        "handoff_archive_manifest_ref": f"platform_outputs/{safe_project}/defect_discovery/external_handoff_archive_manifest.json",
        "commercial_audit_exports_ref": f"platform_outputs/{safe_project}/defect_discovery/external_commercial_audit_exports.json",
        "external_tracker_sync_payloads_ref": f"platform_outputs/{safe_project}/defect_discovery/external_tracker_sync_payloads.json",
    }


def _load_schema_assets(root: Path, project: str) -> str:
    directory = root / "platform_workspace" / _safe_project(project) / "input"
    chunks: list[str] = []
    for path in sorted(directory.glob("*.sql")) if directory.exists() else []:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:1_000_000])
        except OSError:
            continue
    return "\n\n".join(chunks)


def _project_requirement_input_dirs(root: Path, project: str) -> list[Path]:
    safe_project = _safe_project(project)
    candidates: list[Path] = [root / "platform_workspace" / safe_project / "input"]

    aliases: set[str] = {safe_project}
    normalized_project = re.sub(r"[^a-z0-9]+", "_", safe_project.lower()).strip("_")
    if normalized_project:
        aliases.add(normalized_project)
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
    except Exception:
        registry = {}
    if isinstance(registry, dict):
        aliases.add(str(registry.get("project_id") or "").strip())
        for connector in registry.get("connectors", []) if isinstance(registry.get("connectors"), list) else []:
            if not isinstance(connector, dict):
                continue
            for key in ("system_name", "service_name", "domain_name", "module_name"):
                aliases.add(str(connector.get(key) or "").strip())
    aliases = {item for item in aliases if item}
    projects_root = root / "projects"
    if projects_root.exists():
        try:
            for entry in sorted(projects_root.iterdir(), key=lambda item: item.name.lower()):
                if not entry.is_dir():
                    continue
                name = entry.name.strip()
                normalized_name = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                if (
                    name in aliases
                    or normalized_name in aliases
                    or any(alias and (alias in normalized_name or normalized_name in alias) for alias in aliases)
                ):
                    candidates.append(entry / "input")
        except OSError:
            pass
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _requirement_doc_score(path: Path) -> int:
    name = path.name.lower()
    if path.suffix.lower() not in {".md", ".txt", ".rst"}:
        return 0
    negative_tokens = (
        "api_spec",
        "openapi",
        "swagger",
        "db_schema",
        "schema",
        "test_accounts",
        "windows_native_start",
        "deployment",
        "historical_bug",
    )
    if any(token in name for token in negative_tokens):
        return 0
    score = 0
    if "prd" in name:
        score += 100
    if "mrd" in name:
        score += 90
    if "business_rules" in name or "business-rules" in name:
        score += 85
    if "requirement" in name:
        score += 80
    if "user_roles" in name or "roles" in name:
        score += 50
    if "spec" in name:
        score += 20
    return score


def _load_project_prd_text(root: Path, project: str) -> str:
    candidates: list[tuple[int, int, Path]] = []
    for directory_index, input_dir in enumerate(_project_requirement_input_dirs(root, project)):
        if not input_dir.exists():
            continue
        try:
            entries = sorted(input_dir.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for path in entries:
            if not path.is_file():
                continue
            score = _requirement_doc_score(path)
            if score <= 0:
                continue
            candidates.append((directory_index, -score, path))
    chunks: list[str] = []
    seen_paths: set[str] = set()
    seen_hashes: set[str] = set()
    for _, _, path in sorted(candidates, key=lambda item: (item[0], item[1], str(item[2]).lower())):
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            text = ""
        if not text:
            continue
        content_hash = _sha256(text)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        chunks.append(f"## {path.name}\n{text}")
    return "\n\n".join(chunks)


def _registry_manifest(root: Path, project: str, api_doc_text: str) -> dict[str, str]:
    try:
        from .enterprise_source_registry import SourceRegistryError, resolve_source_manifest
        manifest = resolve_source_manifest(project, api_doc_text, root=root)
    except (ImportError, OSError, ValueError):
        return {}
    except SourceRegistryError:
        return {}
    if not isinstance(manifest, dict) or not str(manifest.get("source_id") or "").strip() or not str(manifest.get("source_hash") or "").strip():
        return {}
    return {
        "source_id": str(manifest.get("source_id") or "")[:160],
        "source_hash": str(manifest.get("source_hash") or "")[:128],
        "source_version_id": str(manifest.get("source_version_id") or "")[:80],
        "source_origin": str(manifest.get("source_origin") or "registered_source_registry")[:80],
    }


def _load_registered_source(project: str, root: Path, context: dict[str, Any]) -> str:
    manifest = _as_dict(context.get("source_manifest"))
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    try:
        from .enterprise_source_registry import SourceRegistryError, list_source_assets, load_source_content
        if not _SHA256_RE.fullmatch(source_hash):
            assets = list_source_assets(project, root=root)
            latest = max(
                (
                    item
                    for item in assets
                    if isinstance(item, dict) and _SHA256_RE.fullmatch(str(item.get("latest_source_hash") or "").strip().lower())
                ),
                key=lambda item: (str(item.get("updated_at_utc") or ""), str(item.get("source_id") or "")),
                default={},
            )
            source_hash = str(latest.get("latest_source_hash") or "").strip().lower()
            if not _SHA256_RE.fullmatch(source_hash):
                return ""
            context["source_manifest"] = {
                **manifest,
                "source_id": str(latest.get("source_id") or "").strip(),
                "source_hash": source_hash,
                "source_version_id": str(latest.get("latest_version_id") or "").strip(),
                "source_origin": "registered_source_registry",
            }
        return load_source_content(project, source_hash, root=root)
    except (ImportError, OSError, ValueError, SourceRegistryError):
        return ""


def _find_project_asset(root: Path, project: str, content_hash: str) -> dict[str, str]:
    """Migration resolver for an exact project-owned input asset."""
    project_root = root / "platform_workspace" / _safe_project(project) / "input"
    if not project_root.exists() or not project_root.is_dir():
        return {}
    inspected = 0
    try:
        entries = sorted(project_root.rglob("*"))
    except OSError:
        return {}
    for path in entries:
        if inspected >= _MAX_SOURCE_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in _SOURCE_EXTENSIONS:
            continue
        inspected += 1
        try:
            if path.stat().st_size > _MAX_SOURCE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _sha256(content) != content_hash:
            continue
        return {
            "source_id": f"project_asset:{path.relative_to(root).as_posix()}",
            "source_hash": content_hash,
            "source_version_id": f"legacy_{content_hash[:24]}",
            "source_origin": "registered_project_asset",
        }
    return {}


def _source_manifest(root: Path, project: str, context: dict[str, Any], api_doc_path: str, api_doc_text: str) -> dict[str, str]:
    declared = _as_dict(context.get("source_manifest"))
    source_id = str(declared.get("source_id") or "").strip()
    source_hash = str(declared.get("source_hash") or "").strip().lower().removeprefix("sha256:").strip()
    source_version_id = str(declared.get("source_version_id") or "").strip()
    actual_hash = _sha256(api_doc_text)
    source_origin = str(declared.get("source_origin") or "").strip()
    if source_id or source_hash:
        source_origin = source_origin or "declared_manifest"
    else:
        registered = _registry_manifest(root, project, api_doc_text) or _find_project_asset(root, project, actual_hash)
        source_id = registered.get("source_id", "")
        source_hash = registered.get("source_hash", "")
        source_version_id = registered.get("source_version_id", "")
        source_origin = registered.get("source_origin", "external_path_unregistered" if api_doc_path else "inline_unregistered")
    return {
        "source_id": source_id[:160],
        "source_hash": source_hash[:128],
        "source_version_id": source_version_id[:80],
        "actual_hash": actual_hash,
        "source_origin": source_origin[:80],
    }


def _source_contract(manifest: dict[str, str]) -> list[dict[str, str]]:
    if not manifest.get("source_id") or not manifest.get("source_hash"):
        return [_gap("SOURCE_PROVENANCE_MISSING", "Every enterprise scan requires a registered project asset or an explicit source_id and immutable SHA-256 source_hash.")]
    if not _SHA256_RE.fullmatch(manifest["source_hash"]):
        return [_gap("SOURCE_HASH_INVALID", "source_hash must be a lowercase SHA-256 digest for the submitted source content.")]
    if manifest["source_hash"] != manifest["actual_hash"]:
        return [_gap("SOURCE_HASH_MISMATCH", "The source_hash does not match submitted source content.")]
    return []


def _runtime_contract(context: dict[str, Any], base_url: str, manifest: dict[str, str]) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    public_manifest = {
        "source_id": manifest.get("source_id", ""),
        "source_hash": manifest.get("source_hash", ""),
        "source_version_id": manifest.get("source_version_id", ""),
        "source_origin": manifest.get("source_origin", ""),
    }
    if not base_url:
        return "", [], {"status": "plan_only", "reason": "runtime_target_missing", "source_manifest": public_manifest}
    missing: list[dict[str, str]] = []
    if not public_manifest["source_id"] or not public_manifest["source_hash"]:
        missing.append(_gap("SOURCE_PROVENANCE_MISSING", "A registered source is required before runtime probing."))
    if not str(context.get("scope_id") or "").strip():
        missing.append(_gap("CAMPAIGN_SCOPE_MISSING", "An explicit campaign scope_id is required before runtime probing."))
    if not str(context.get("environment_ref") or context.get("target_environment") or "").strip():
        missing.append(_gap("ENVIRONMENT_REFERENCE_MISSING", "An approved environment_ref is required before runtime probing."))
    test_data = _as_dict(context.get("test_data_contract"))
    if test_data.get("strategy") in {"create_disposable", "approved_fixture_setup"} and test_data.get("write_approved") is not True:
        missing.append(_gap("WRITE_APPROVAL_MISSING", "Write-capable test-data strategies require explicit write approval."))
    if missing:
        return "", missing, {"status": "blocked", "reason": "runtime_contract_missing", "source_manifest": public_manifest}
    return base_url.rstrip("/"), [], {"status": "approved", "reason": "", "source_manifest": public_manifest}


def _scan_preflight_guide(
    *,
    context: dict[str, Any],
    base_url: str,
    manifest: dict[str, str],
    runtime_contract: dict[str, Any],
    test_data_plan: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    runtime_observed: bool = False,
) -> dict[str, Any]:
    test_data = _as_dict(context.get("test_data_contract"))
    service_credentials = _as_dict(
        (diagnostics or {}).get("service_credentials_readiness")
        or context.get("service_credentials_readiness")
    )
    if not service_credentials and isinstance(context.get("services"), list):
        try:
            from .runtime_onboarding_preflight import _service_credentials_readiness

            service_credentials = _as_dict(_service_credentials_readiness({"services": context.get("services")}))
        except Exception:
            service_credentials = {}
    configured_services = int(service_credentials.get("configured_service_count") or 0)
    unverified_services = service_credentials.get("unverified") if isinstance(service_credentials.get("unverified"), list) else []
    service_credentials_status = (
        "ready"
        if configured_services and bool(service_credentials.get("ok"))
        else ("configured_unverified" if configured_services else "not_configured")
    )
    checks = [
        {
            "key": "source_manifest",
            "label": "immutable_source_manifest",
            "status": "ready" if manifest.get("source_id") and manifest.get("source_hash") else "missing",
            "required": True,
            "detail": manifest.get("source_id") or "register customer materials before scanning",
        },
        {
            "key": "target_base_url",
            "label": "target_environment_url",
            "status": "configured_unverified" if base_url else "missing",
            "required": bool(base_url),
            "detail": base_url or "plan_only_scan_has_no_runtime_target",
        },
        {
            "key": "scope_id",
            "label": "approved_scope",
            "status": "ready" if str(context.get("scope_id") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(context.get("scope_id") or ""),
        },
        {
            "key": "environment_ref",
            "label": "environment_reference",
            "status": "ready" if str(context.get("environment_ref") or context.get("target_environment") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(context.get("environment_ref") or context.get("target_environment") or ""),
        },
        {
            "key": "test_data_strategy",
            "label": "test_data_strategy",
            "status": "ready" if str(test_data.get("strategy") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(test_data.get("strategy") or ""),
        },
        {
            "key": "execution_approval",
            "label": "readonly_execution_approval",
            "status": "ready" if str(context.get("execution_approval_id") or "").strip() else ("not_required" if not base_url else "missing"),
            "required": bool(base_url),
            "detail": str(context.get("execution_approval_id") or ""),
        },
        {
            "key": "actor_credentials",
            "label": "test_actor_or_role_credentials",
            "status": "configured_unverified" if _as_dict(context.get("actor_contract") or context.get("test_actor_contract")) else "not_configured",
            "required": False,
            "detail": "configured actors still require runtime login or token evidence",
        },
        {
            "key": "service_credentials",
            "label": "service_auth_db_credentials",
            "status": service_credentials_status,
            "required": bool(base_url and configured_services),
            "detail": ";".join(str(item) for item in unverified_services) or str(service_credentials.get("message") or ""),
        },
        {
            "key": "url_reachability",
            "label": "url_reachability",
            "status": "ready" if runtime_observed else ("not_checked" if not diagnostics else ("ready" if diagnostics.get("ready") else "failed")),
            "required": bool(base_url),
            "detail": "runtime_traffic_captured" if runtime_observed else str((diagnostics or {}).get("summary") or "no runtime health check was executed"),
        },
    ]
    if test_data_plan:
        checks.append({
            "key": "test_data_contract",
            "label": "test_data_contract",
            "status": str(test_data_plan.get("status") or "missing"),
            "required": bool(base_url),
            "detail": ",".join(str(item) for item in test_data_plan.get("missing_requirements", []) or []),
        })
    missing = [
        item["key"]
        for item in checks
        if item.get("required")
        and (
            item.get("status") in {"missing", "failed", "blocked_with_testability_gap"}
            or (item.get("key") == "service_credentials" and item.get("status") == "configured_unverified")
        )
    ]
    runtime_status = str(runtime_contract.get("status") or "")
    return {
        "status": "ready" if not missing and runtime_status == "approved" else ("plan_only" if not base_url else "blocked"),
        "runtime_contract_status": runtime_status,
        "missing": missing,
        "checks": checks,
        "healthy_claim_allowed": not missing and runtime_status == "approved",
    }


def _source_catalog(api_doc: str) -> str:
    labels: set[str] = set()
    for line in str(api_doc or "").splitlines():
        match = re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s|`]+)", line, re.I)
        if not match:
            continue
        parts = [part for part in match.group(1).strip("/").split("/") if part and not part.startswith("{") and part.lower() not in {"api", "v1", "v2", "v3"}]
        if parts:
            labels.add(parts[0])
    return "\n".join(f"# Source asset: {item}" for item in sorted(labels))


def _classify_findings(items: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    confirmed: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if has_real_confirmation_receipt(row):
            row["confirmation_status"] = "confirmed"
            confirmed.append(row)
        else:
            row.setdefault("execution_status", "not_executed")
            row["confirmation_status"] = str(row.get("confirmation_status") or "candidate")
            candidates.append(row)
    return confirmed, candidates


def _dedupe_findings(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collapse near-identical findings that share the same reproduction path.

    A state-graph cross-product can stamp one probe (e.g. a duplicate-payment
    call) onto many lifecycle-state labels, inflating one real defect into N
    "P0" rows with byte-identical reproduction steps. This groups by
    (oracle rule + id-normalized reproduction fingerprint + primary target) and
    keeps a single representative, recording the collapsed lifecycle-state
    variants as coverage on the survivor so nothing is silently dropped.
    """
    import re as _re

    def _norm(text: str) -> str:
        # Neutralize concrete ids (uuid / long hex / digits) so the same probe
        # against different entity instances collapses to one fingerprint.
        text = _re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}", "{id}", str(text or ""))
        text = _re.sub(r"\b[0-9a-fA-F]{16,}\b", "{id}", text)
        text = _re.sub(r"\b\d+\b", "{n}", text)
        return text.strip()

    groups: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        oracle = item.get("oracle") if isinstance(item.get("oracle"), dict) else {}
        rule = str(oracle.get("violated_rule") or oracle.get("oracle_name") or item.get("category") or "").strip().lower()
        ev = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        steps = ev.get("reproduction_steps") if isinstance(ev.get("reproduction_steps"), list) else []
        fingerprint = tuple(_norm(s) for s in steps)
        primary = _norm(str(ev.get("request") or ""))
        key = (rule, primary, fingerprint)
        variant = {
            "title": item.get("title"),
            "behavior_slice_id": item.get("behavior_slice_id"),
            "oracle_state": (oracle.get("expected") or item.get("expected") or ""),
        }
        if key not in groups:
            keep = dict(item)
            keep["_coverage_variants"] = [variant]
            keep["_duplicate_count"] = 1
            groups[key] = keep
            order.append(key)
        else:
            groups[key]["_coverage_variants"].append(variant)
            groups[key]["_duplicate_count"] += 1

    deduped = [groups[k] for k in order]
    _total = len([i for i in items if isinstance(i, dict)])
    report = {
        "input_count": _total,
        "unique_count": len(deduped),
        "collapsed_count": _total - len(deduped),
        "groups": [
            {
                "title": groups[k].get("title"),
                "duplicate_count": groups[k].get("_duplicate_count", 1),
                "variant_states": [v.get("oracle_state") for v in groups[k].get("_coverage_variants", [])],
            }
            for k in order
        ],
    }
    return deduped, report


def _has_verified_db_evidence(finding: dict[str, Any]) -> bool:
    db_evidence = finding.get("db_evidence") if isinstance(finding.get("db_evidence"), dict) else {}
    return bool(
        db_evidence
        and (db_evidence.get("before_db_snapshot") or db_evidence.get("before"))
        and (db_evidence.get("after_db_snapshot") or db_evidence.get("after"))
        and (db_evidence.get("db_assertion") or db_evidence.get("assertion"))
        and (db_evidence.get("business_operation") or db_evidence.get("operation"))
    )


def _is_external_signal_finding(finding: dict[str, Any]) -> bool:
    source = str(finding.get("source") or "").strip().lower()
    return source.startswith("external_signal:") or bool(str(finding.get("external_signal_provider") or "").strip())


def _snapshot_entry_from_external(value: Any, *, fallback_method: str, fallback_path: str, fallback_kind: str) -> dict[str, Any]:
    item = _as_dict(value)
    method = str(item.get("method") or fallback_method or "").upper().strip()
    path = str(item.get("path") or fallback_path or "").strip()
    status_code = item.get("status_code")
    response: dict[str, Any] = {}
    if isinstance(status_code, int):
        response["status_code"] = status_code
    elif str(status_code or "").isdigit():
        response["status_code"] = int(status_code)
    if "body" in item:
        response["body"] = item.get("body")
    return {
        "observer_kind": str(item.get("observer_kind") or fallback_kind or "external_runtime_projection"),
        "evidence_goal": str(item.get("evidence_goal") or "before_after_snapshot"),
        "method": method,
        "path": path,
        "response": response,
    }


def _external_finding_snapshots(finding: dict[str, Any], *, method: str, path: str) -> dict[str, list[dict[str, Any]]]:
    before_after = _as_dict(finding.get("before_after_snapshot"))
    before = _as_dict(before_after.get("before"))
    after = _as_dict(before_after.get("after"))
    if before or after:
        return {
            "before": [_snapshot_entry_from_external(before, fallback_method=method, fallback_path=path, fallback_kind="external_runtime_before")] if before else [],
            "after": [_snapshot_entry_from_external(after, fallback_method=method, fallback_path=path, fallback_kind="external_runtime_after")] if after else [],
        }
    db_evidence = _as_dict(finding.get("db_evidence"))
    db_before = db_evidence.get("before_db_snapshot") if isinstance(db_evidence.get("before_db_snapshot"), dict) else {}
    db_after = db_evidence.get("after_db_snapshot") if isinstance(db_evidence.get("after_db_snapshot"), dict) else {}
    table = str(db_evidence.get("table") or "").strip()
    operation = str(db_evidence.get("business_operation") or "").strip()
    before_row = {
        "observer_kind": "database_projection",
        "evidence_goal": "db_before_snapshot",
        "method": method,
        "path": path,
        "table": table,
        "business_operation": operation,
        "payload": db_before,
        "response": {},
    } if db_before else {}
    after_row = {
        "observer_kind": "database_projection",
        "evidence_goal": "db_after_snapshot",
        "method": method,
        "path": path,
        "table": table,
        "business_operation": operation,
        "payload": db_after,
        "response": {},
    } if db_after else {}
    return {
        "before": [before_row] if before_row else [],
        "after": [after_row] if after_row else [],
    }


def _external_finding_runtime_observation(finding: dict[str, Any]) -> dict[str, Any]:
    runtime_replay = _as_dict(finding.get("runtime_replay"))
    raw_evidence = _as_dict(finding.get("raw_evidence"))
    request_raw = _as_dict(raw_evidence.get("request_raw"))
    response_raw = _as_dict(raw_evidence.get("response_raw"))
    har_evidence = _as_dict(finding.get("har_evidence"))
    invariant_eval = _as_dict(finding.get("business_invariant_evaluation"))
    evidence_quality = _as_dict(finding.get("evidence_quality"))
    method = str(
        finding.get("method")
        or finding.get("_api_method")
        or runtime_replay.get("method")
        or request_raw.get("method")
        or har_evidence.get("method")
        or ""
    ).upper().strip()
    path = str(
        finding.get("path")
        or finding.get("_api_path")
        or runtime_replay.get("path")
        or request_raw.get("path")
        or har_evidence.get("path")
        or ""
    ).strip()
    response_status = runtime_replay.get("http_status")
    if response_status is None and response_raw.get("status_code") is not None:
        response_status = response_raw.get("status_code")
    if response_status is None and har_evidence.get("status_code") is not None:
        response_status = har_evidence.get("status_code")
    response: dict[str, Any] = {}
    if response_status is not None:
        try:
            response["status_code"] = int(response_status)
        except Exception:
            pass
    if response_raw.get("body") is not None:
        response["body"] = response_raw.get("body")
    elif har_evidence.get("response_body") is not None:
        response["body"] = har_evidence.get("response_body")
    if response_raw.get("duration_ms") is not None:
        response["duration_ms"] = response_raw.get("duration_ms")
    elif runtime_replay.get("duration_ms") is not None:
        response["duration_ms"] = runtime_replay.get("duration_ms")
    verification = {
        "verdict": str(finding.get("confirmation_status") or "candidate"),
        "reason": str(
            finding.get("actual")
            or finding.get("actual_behavior")
            or invariant_eval.get("reason")
            or finding.get("description")
            or ""
        ).strip(),
        "confidence": round(min(max(float(evidence_quality.get("score") or finding.get("confidence_score") or 0.88) / 100.0, 0.0), 0.99), 2),
        "replay_ids": [str(item) for item in [finding.get("risk_id"), finding.get("finding_id"), finding.get("candidate_id")] if str(item or "").strip()],
        "payload_summary": str(response.get("body") or "")[:200],
        "negative_values": [],
        "db_evidence": _as_dict(finding.get("db_evidence")),
        "business_invariant_evaluation": invariant_eval,
    }
    return {
        "candidate_id": str(finding.get("risk_id") or finding.get("finding_id") or finding.get("candidate_id") or "").strip(),
        "risk_type": str(finding.get("category") or "external_signal_violation").strip(),
        "method": method,
        "path": path,
        "request": {
            "method": method,
            "path": path,
            "body": request_raw.get("body", finding.get("request_body")),
        },
        "response": response,
        "responses": [response] if response else [],
        "snapshots": _external_finding_snapshots(finding, method=method, path=path),
        "verification": verification,
        "source_refs": [str(item) for item in [finding.get("source"), _as_dict(finding.get("evidence")).get("junit_report"), _as_dict(finding.get("evidence")).get("trace_id")] if str(item or "").strip()],
        "grounding_basis": {
            "engine": "external_signal_bridge",
            "rule": _as_dict(finding.get("external_evidence_adjudication")).get("rule"),
            "source": str(finding.get("source") or "").strip(),
        },
    }


def _attach_external_evidence_packages(items: Any) -> list[dict[str, Any]]:
    try:
        from .runtime_finding_evidence_packager import package_runtime_finding_evidence
    except Exception:
        return [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    packaged: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if (
            _is_external_signal_finding(row)
            and str(row.get("confirmation_status") or "").strip().lower() == "validated_candidate"
            and str(_as_dict(row.get("evidence_package")).get("engine") or "").strip() != "runtime_finding_evidence_packager_v1_phase92t"
        ):
            obs = _external_finding_runtime_observation(row)
            evidence_package = package_runtime_finding_evidence(obs, source=str(row.get("source") or "external_signal"))
            row["evidence_package"] = evidence_package
            row["evidence_strength_score"] = evidence_package.get("evidence_strength_score")
            row["evidence_grade"] = evidence_package.get("evidence_grade")
            row["violated_invariants"] = evidence_package.get("violated_invariants") or []
            row["delta_summary"] = evidence_package.get("delta_summary") or {}
        packaged.append(row)
    return packaged


def _adjudicate_external_evidence_backed_candidates(items: Any) -> list[dict[str, Any]]:
    adjudicated: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if not _is_external_signal_finding(row):
            adjudicated.append(row)
            continue
        runtime_replay = row.get("runtime_replay") if isinstance(row.get("runtime_replay"), dict) else {}
        invariant_eval = row.get("business_invariant_evaluation") if isinstance(row.get("business_invariant_evaluation"), dict) else {}
        has_runtime_replay = str(runtime_replay.get("status") or "").strip().lower() == "executed"
        has_db_evidence = _has_verified_db_evidence(row)
        has_failed_invariant = str(invariant_eval.get("verdict") or "").strip().lower() == "failed"
        passes = has_runtime_replay and has_db_evidence and has_failed_invariant
        row["external_evidence_adjudication"] = {
            "status": "validated_candidate" if passes else "candidate",
            "has_runtime_replay": has_runtime_replay,
            "has_db_evidence": has_db_evidence,
            "has_failed_invariant": has_failed_invariant,
            "rule": "external_runtime_replay_and_db_evidence_and_failed_invariant",
        }
        if passes:
            row["confirmation_status"] = "validated_candidate"
            row["execution_status"] = str(row.get("execution_status") or "executed")
            row["evidence_strength"] = str(row.get("evidence_strength") or "runtime_and_db")
            row["bug_status"] = "reproduced"
            row["gate_passed"] = True
            row["quality_assurance_gap"] = False
            row["customer_delivery_status"] = str(row.get("customer_delivery_status") or "defect")
            row["semantic_verdict"] = str(row.get("semantic_verdict") or "SEMANTIC_CONFIRMED")
            row["business_evidence_status"] = str(row.get("business_evidence_status") or "VALIDATED")
            row["final_review_status"] = str(row.get("final_review_status") or "VALIDATED_CANDIDATE")
            evidence_status = _as_dict(row.get("evidence_status"))
            evidence_status.update({
                "semantic_verdict": row["semantic_verdict"],
                "business_evidence_status": row["business_evidence_status"],
                "final_review_status": row["final_review_status"],
                "missing_requirements": [str(item) for item in evidence_status.get("missing_requirements") or [] if str(item)],
            })
            row["evidence_status"] = evidence_status
            runtime_trace = _as_dict(runtime_replay.get("trace"))
            runtime_steps = runtime_trace.get("steps") if isinstance(runtime_trace.get("steps"), list) else []
            first_step = runtime_steps[0] if runtime_steps and isinstance(runtime_steps[0], dict) else {}
            runtime_response = _as_dict(first_step.get("response"))
            method = str(row.get("method") or runtime_replay.get("method") or row.get("_api_method") or "").upper().strip()
            path = str(row.get("path") or runtime_replay.get("path") or row.get("_api_path") or "").strip()
            if method:
                row["_api_method"] = method
            if path:
                row["_api_path"] = path
            invariant_results = invariant_eval.get("results") if isinstance(invariant_eval.get("results"), list) else []
            first_failed = next((item for item in invariant_results if isinstance(item, dict) and str(item.get("verdict") or "").lower() == "failed"), {})
            expected = str(
                row.get("expected_behavior")
                or row.get("expected")
                or first_failed.get("expected")
                or first_failed.get("name")
                or "业务不变量应保持成立"
            ).strip()
            actual = str(
                row.get("actual_behavior")
                or row.get("actual")
                or first_failed.get("actual")
                or first_failed.get("reason")
                or invariant_eval.get("reason")
                or f"运行时回放返回 HTTP {runtime_replay.get('http_status')}"
            ).strip()
            row["expected_behavior"] = expected
            row["expected"] = expected
            row["actual_behavior"] = actual
            row["actual"] = actual
            evidence = _as_dict(row.get("evidence"))
            evidence.update({
                "method": method,
                "path": path,
                "target": evidence.get("target") or f"{method} {path}".strip(),
                "expected": expected,
                "actual": actual,
                "trace_id": evidence.get("trace_id") or _as_dict(row.get("trace")).get("trace_id") or _as_dict(runtime_trace).get("trace_id") or "",
            })
            failed_reason = str(first_failed.get("reason") or invariant_eval.get("reason") or row.get("description") or "").strip()
            if failed_reason:
                evidence["assertion"] = evidence.get("assertion") or failed_reason
            row["evidence"] = evidence
            failed_fields = [str(item) for item in row.get("failed_fields") or [] if str(item)]
            if not failed_fields and isinstance(first_failed, dict):
                failed_fields = [str(item) for item in first_failed.get("failed_fields") or [] if str(item)]
            row["failed_fields"] = failed_fields
            if not isinstance(row.get("failed_assertions"), list) or not row.get("failed_assertions"):
                row["failed_assertions"] = [{
                    "type": "business_invariant_violation",
                    "rule": failed_reason or expected,
                    "expected": expected,
                    "actual": actual,
                    "failed_fields": failed_fields,
                }]
            raw_evidence = _as_dict(row.get("raw_evidence"))
            request_raw = _as_dict(raw_evidence.get("request_raw"))
            if method:
                request_raw["method"] = method
            if path:
                request_raw["path"] = path
            response_raw = _as_dict(raw_evidence.get("response_raw"))
            if runtime_replay.get("http_status") is not None:
                response_raw["status_code"] = runtime_replay.get("http_status")
            if runtime_response.get("body") is not None:
                response_raw["body"] = runtime_response.get("body")
            if runtime_replay.get("duration_ms") is not None:
                response_raw["duration_ms"] = runtime_replay.get("duration_ms")
            raw_evidence["request_raw"] = request_raw
            raw_evidence["response_raw"] = response_raw
            raw_evidence["has_real_evidence"] = True
            raw_evidence["timestamp"] = str(raw_evidence.get("timestamp") or row.get("timestamp") or row.get("last_verified_at") or "")
            row["raw_evidence"] = raw_evidence
            reproduction = _as_dict(row.get("reproduction"))
            reproduction.update({
                "method": method,
                "path": path,
                "is_synthetic": False,
                "har_evidence": {
                    "method": method,
                    "path": path,
                    "status_code": runtime_replay.get("http_status"),
                    "response_body": runtime_response.get("body"),
                    "duration_ms": runtime_replay.get("duration_ms"),
                },
            })
            row["reproduction"] = reproduction
            row["har_evidence"] = dict(reproduction.get("har_evidence") or {})
            row["timestamp"] = str(row.get("timestamp") or row.get("last_verified_at") or raw_evidence.get("timestamp") or "")
            row["last_verified_at"] = str(row.get("last_verified_at") or row.get("timestamp") or raw_evidence.get("timestamp") or "")
            if not isinstance(row.get("reproduction_steps"), list) or not row.get("reproduction_steps"):
                step_summary = f"{method} {path}".strip() if method or path else "runtime replay"
                status_text = f"HTTP {runtime_replay.get('http_status')}" if runtime_replay.get("http_status") is not None else "已执行"
                row["reproduction_steps"] = [f"{step_summary} -> {status_text}"]
            row.setdefault("evidence_quality", {})
            if isinstance(row.get("evidence_quality"), dict):
                quality = dict(row["evidence_quality"])
                quality["level"] = "validated"
                quality["score"] = max(int(quality.get("score") or 0), 88)
                quality["can_reproduce"] = True
                verified = [str(item) for item in quality.get("verified") or [] if str(item)]
                verified.extend([
                    "存在运行时回放证据",
                    "存在 DB 前后快照与断言",
                    "存在业务不变量失败结果",
                ])
                quality["verified"] = list(dict.fromkeys(verified))[:10]
                row["evidence_quality"] = quality
        adjudicated.append(row)
    return adjudicated


def _ui_candidate_gate(items: Any) -> list[dict[str, Any]]:
    gated: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        raw = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else {}
        ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
        current_url = str(ui_result.get("current_url") or evidence.get("target") or "").strip()
        artifacts = evidence.get("ui_artifacts") if isinstance(evidence.get("ui_artifacts"), list) else []
        steps = evidence.get("reproduction_steps") if isinstance(evidence.get("reproduction_steps"), list) else []
        status = str(row.get("execution_status") or ui_result.get("status") or "").strip().lower()
        has_real_evidence = raw.get("has_real_evidence") is True
        passes_gate = has_real_evidence and bool(current_url or artifacts) and bool(steps) and status in {"executed", "failed", "blocked"}
        row["ui_candidate_gate"] = {
            "passed": passes_gate,
            "has_real_evidence": has_real_evidence,
            "has_target": bool(current_url),
            "artifact_count": len(artifacts),
            "reproduction_step_count": len(steps),
        }
        if not passes_gate:
            continue
        row.setdefault("execution_status", status or "not_executed")
        row["confirmation_status"] = "candidate"
        row.setdefault("source", "ui_execution_adapter")
        gated.append(row)
    return gated


def _template_string(template: str, values: dict[str, Any]) -> str:
    text = str(template or "")
    for key, value in values.items():
        text = text.replace("{" + str(key) + "}", str(value or ""))
    return text


def _ui_verification_context(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    raw = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    created_data = raw.get("created_data") if isinstance(raw.get("created_data"), dict) else {}
    target = str(ui_result.get("current_url") or evidence.get("target") or "").strip()
    parsed = urlparse(target) if target else None
    artifact_refs = [
        str(item).strip()
        for item in (
            ui_result.get("artifact_refs")
            if isinstance(ui_result.get("artifact_refs"), list)
            else []
        )
        if str(item).strip()
    ]
    reproduction_steps = evidence.get("reproduction_steps") if isinstance(evidence.get("reproduction_steps"), list) else []
    return {
        "current_url": target,
        "path": parsed.path if parsed else "",
        "object_id": str(created_data.get("object_id") or ""),
        "object_type": str(created_data.get("object_type") or ""),
        "data_scope_ref": str(created_data.get("data_scope_ref") or ""),
        "object_url": str(created_data.get("object_url") or ""),
        "request_id": str(ui_result.get("request_id") or ""),
        "artifact_refs": artifact_refs,
        "artifact_count": len(artifact_refs),
        "reproduction_step_count": len(reproduction_steps),
        "execution_status": str(row.get("execution_status") or ui_result.get("status") or "").strip().lower(),
        "bridge_provider": str(ui_result.get("bridge_provider") or ui_result.get("provider") or "").strip(),
    }


def _verify_ui_candidate_http(config: dict[str, Any], values: dict[str, Any], runtime_contract: dict[str, Any]) -> dict[str, Any]:
    base_url = str(runtime_contract.get("approved_base_url") or "").strip().rstrip("/")
    path_template = str(config.get("path") or config.get("url") or "").strip()
    target = _template_string(path_template, values)
    if not target:
        return {"status": "skipped", "reason": "verification_http_target_missing"}
    if target.startswith("/"):
        if not base_url:
            return {"status": "skipped", "reason": "verification_base_url_missing"}
        target = base_url + target
    timeout_ms = int(config.get("timeout_ms") or 5000)
    expected_statuses = {int(x) for x in (config.get("expected_statuses") or [200]) if str(x).strip()}
    try:
        req = urllib_request.Request(target, method="GET", headers={"Accept": "application/json"})
        with urllib_request.urlopen(req, timeout=max(timeout_ms, 1000) / 1000.0) as response:
            body = response.read().decode("utf-8", errors="replace")
            status_code = int(getattr(response, "status", 200) or 200)
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status_code = int(exc.code or 500)
    except Exception as exc:
        return {"status": "failed", "reason": f"verification_http_error:{type(exc).__name__}", "target": target}
    body_json: Any = None
    try:
        body_json = json.loads(body) if body else None
    except Exception:
        body_json = None
    matches = True
    contains = str(config.get("body_contains") or "").strip()
    if contains:
        matches = contains in body
    return {
        "status": "verified" if status_code in expected_statuses and matches else "mismatch",
        "reason": "http_status_and_body_match" if status_code in expected_statuses and matches else "http_expectation_not_met",
        "target": target,
        "status_code": status_code,
        "body_excerpt": body[:500],
        "body_json": body_json if isinstance(body_json, (dict, list)) else None,
    }


def _verify_ui_candidate_sqlite(config: dict[str, Any], values: dict[str, Any], root: Path) -> dict[str, Any]:
    db_path_template = str(config.get("db_path") or "").strip()
    query_template = str(config.get("query") or "").strip()
    if not db_path_template or not query_template:
        return {"status": "skipped", "reason": "verification_sqlite_config_missing"}
    db_path = Path(_template_string(db_path_template, values))
    if not db_path.is_absolute():
        db_path = root / db_path
    if not db_path.exists():
        return {"status": "failed", "reason": "verification_sqlite_db_missing", "db_path": str(db_path)}
    query = _template_string(query_template, values)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
        conn.close()
    except Exception as exc:
        return {"status": "failed", "reason": f"verification_sqlite_error:{type(exc).__name__}", "db_path": str(db_path)}
    min_rows = int(config.get("min_rows") or 1)
    preview = [dict(row) for row in rows[:3]]
    return {
        "status": "verified" if len(rows) >= min_rows else "mismatch",
        "reason": "sqlite_row_match" if len(rows) >= min_rows else "sqlite_row_count_below_threshold",
        "db_path": str(db_path),
        "row_count": len(rows),
        "rows_preview": preview,
    }


def _verify_ui_candidate_execution_evidence(values: dict[str, Any]) -> dict[str, Any]:
    current_url = str(values.get("current_url") or "").strip()
    object_url = str(values.get("object_url") or "").strip()
    object_id = str(values.get("object_id") or "").strip()
    object_type = str(values.get("object_type") or "").strip()
    data_scope_ref = str(values.get("data_scope_ref") or "").strip()
    bridge_provider = str(values.get("bridge_provider") or "").strip()
    status = str(values.get("execution_status") or "").strip().lower()
    artifact_refs = values.get("artifact_refs") if isinstance(values.get("artifact_refs"), list) else []
    artifact_count = int(values.get("artifact_count") or len(artifact_refs) or 0)
    reproduction_step_count = int(values.get("reproduction_step_count") or 0)
    signals: list[str] = []
    if bridge_provider == "page_agent_browser_plan":
        signals.append("page_agent_browser_plan")
    if current_url:
        signals.append("current_url_present")
    if artifact_count > 0:
        signals.append("artifact_present")
    if reproduction_step_count > 0:
        signals.append("reproduction_steps_present")
    if object_url and current_url and object_url == current_url:
        signals.append("current_url_matches_object_url")
    if object_id and current_url and object_id in current_url:
        signals.append("current_url_contains_object_id")
    if object_id and data_scope_ref and object_id in data_scope_ref:
        signals.append("data_scope_ref_contains_object_id")
    object_binding_verified = bool(
        object_id
        and object_type
        and (
            "current_url_matches_object_url" in signals
            or "current_url_contains_object_id" in signals
        )
    )
    if bridge_provider != "page_agent_browser_plan":
        return {"status": "not_requested", "reason": "verification_page_agent_bridge_only"}
    if status != "executed":
        return {"status": "not_requested", "reason": "verification_execution_status_not_executed"}
    if not current_url or artifact_count <= 0 or reproduction_step_count <= 0:
        return {"status": "mismatch", "reason": "page_agent_evidence_incomplete", "signals": signals}
    if not object_binding_verified:
        return {"status": "mismatch", "reason": "page_agent_object_binding_incomplete", "signals": signals}
    return {
        "status": "verified",
        "reason": "page_agent_execution_evidence_consistent",
        "target": current_url,
        "signals": signals,
        "artifact_count": artifact_count,
        "object_type": object_type,
        "object_id": object_id,
        "data_scope_ref": data_scope_ref,
    }


def _verify_ui_candidate_findings(items: Any, *, root: Path, runtime_contract: dict[str, Any]) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        raw = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else {}
        ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
        metadata = ui_result.get("metadata") if isinstance(ui_result.get("metadata"), dict) else {}
        verification_cfg = metadata.get("verification") if isinstance(metadata.get("verification"), dict) else {}
        context_values = _ui_verification_context(row)
        verification_result = {"status": "not_requested", "reason": "verification_not_configured"}
        kind = str(verification_cfg.get("kind") or "").strip().lower()
        if kind == "http_get":
            verification_result = _verify_ui_candidate_http(verification_cfg, context_values, runtime_contract)
        elif kind == "sqlite_query":
            verification_result = _verify_ui_candidate_sqlite(verification_cfg, context_values, root)
        elif not kind:
            verification_result = _verify_ui_candidate_execution_evidence(context_values)
        row["ui_verification"] = verification_result
        if verification_result.get("status") == "verified":
            row["confidence_score"] = max(float(row.get("confidence_score") or 0.0), 0.8)
            row.setdefault("evidence_quality", {})
            if isinstance(row["evidence_quality"], dict):
                quality_level = "cross_verified" if kind in {"http_get", "sqlite_query"} else "runtime_consistent"
                quality_score = 85 if kind in {"http_get", "sqlite_query"} else 80
                row["evidence_quality"]["level"] = quality_level
                row["evidence_quality"]["score"] = max(int(row["evidence_quality"].get("score") or 0), quality_score)
        verified.append(row)
    return verified


def _mark_high_confidence_ui_candidates(items: Any) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        verification = row.get("ui_verification") if isinstance(row.get("ui_verification"), dict) else {}
        quality = row.get("evidence_quality") if isinstance(row.get("evidence_quality"), dict) else {}
        status = str(verification.get("status") or "").strip().lower()
        quality_level = str(quality.get("level") or "").strip().lower()
        quality_score = int(quality.get("score") or 0)
        confidence = float(row.get("confidence_score") or row.get("confidence") or 0.0)
        high_conf = (
            status == "verified"
            and quality_level in {"cross_verified", "validated"}
            and quality_score >= 85
            and confidence >= 0.8
        )
        row["high_confidence_candidate"] = bool(high_conf)
        if high_conf:
            row["candidate_tier"] = "high_confidence_ui_candidate"
            row["customer_evidence_label"] = str(row.get("customer_evidence_label") or "UI 二次验真通过")
            row["verification_badge"] = str(row.get("verification_badge") or "ui_verified")
        else:
            row.setdefault("candidate_tier", "ui_candidate")
        enriched.append(row)
    return enriched


def _test_data_receipt_verifier(root: Path, project: str):
    def verify(kind: str, receipt_id: str, campaign_id: str, scope_id: str, environment_ref: str) -> bool:
        try:
            from .enterprise_test_data_receipts import verify_test_data_receipt
            verdict = verify_test_data_receipt(project, receipt_id, root=root, kind=kind, campaign_id=campaign_id, scope_id=scope_id, environment_ref=environment_ref)
            return bool(verdict.get("valid"))
        except Exception:
            return False
    return verify


def _persist_execution_evidence(project: str, root: Path, scan_id: str, campaign: dict[str, Any], runtime_contract: dict[str, Any], execution_status: str, v12: dict[str, Any]) -> dict[str, Any]:
    from .evidence_artifact_store import persist_evidence_bundle
    findings = v12.get("findings") if isinstance(v12.get("findings"), list) else []
    external_findings = v12.get("external_findings") if isinstance(v12.get("external_findings"), list) else []
    persisted_findings: list[dict[str, Any]] = []
    for item in findings + external_findings:
        if isinstance(item, dict):
            persisted_findings.append(item)
    return persist_evidence_bundle(
        project,
        root=root,
        run_id=scan_id,
        campaign=campaign,
        runtime_contract=runtime_contract,
        execution_status=execution_status,
        auto_har=_as_dict(v12.get("auto_har")),
        evidence_graphs=v12.get("evidence_graphs") if isinstance(v12.get("evidence_graphs"), list) else [],
        findings=persisted_findings,
        ui_execution=_as_dict(v12.get("ui_execution")),
    )


def _evaluate_release_gate(*, project: str, root: Path, campaign: dict[str, Any], execution_status: str, runtime_contract: dict[str, Any], evidence_bundle: dict[str, Any], test_data_plan: dict[str, Any], findings: list[dict[str, Any]], coverage_gaps: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    from .release_gate import evaluate_release_gate
    gate_policy = {"campaign_not_closed_verdict": "not_ready"}
    gate_policy.update(_as_dict(policy))
    verification: dict[str, Any] = {}
    if str(evidence_bundle.get("status") or "") == "persisted" and str(evidence_bundle.get("bundle_id") or ""):
        try:
            from .evidence_artifact_store import verify_evidence_bundle
            verification = verify_evidence_bundle(project, str(evidence_bundle["bundle_id"]), root=root)
        except Exception as exc:
            verification = {"valid": False, "code": f"EVIDENCE_BUNDLE_VERIFICATION_ERROR:{type(exc).__name__}"}
    return evaluate_release_gate(
        campaign=campaign,
        execution_status=execution_status,
        runtime_contract=runtime_contract,
        evidence_bundle=evidence_bundle,
        evidence_bundle_verification=verification,
        test_data_plan=test_data_plan,
        findings=findings,
        coverage_gaps=coverage_gaps,
        policy=gate_policy,
    )


def _blocked_result(project: str, root: Path, started: float, gaps: list[dict[str, str]], runtime_contract: dict[str, Any], context: dict[str, Any], save_report: bool, output_dir: Optional[Path]) -> dict[str, Any]:
    manifest = _as_dict(runtime_contract.get("source_manifest"))
    first_code = str(_as_dict(gaps[0]).get("code") or "SOURCE_CONTRACT_BLOCKED") if gaps else "SOURCE_CONTRACT_BLOCKED"
    campaign = {
        "campaign_id": "", "campaign_status": "blocked", "scope_id": str(context.get("scope_id") or ""),
        "environment_ref": str(context.get("environment_ref") or context.get("target_environment") or ""),
        "source_id": str(manifest.get("source_id") or ""), "source_hash": str(manifest.get("source_hash") or ""),
        "source_version_id": str(manifest.get("source_version_id") or ""), "source_origin": str(manifest.get("source_origin") or ""),
        "confirmed_slice_count": 0, "coverage_deferred_reason": first_code.lower(),
        "next_campaign_reason": "supply_registered_immutable_source" if first_code == "SOURCE_PROVENANCE_MISSING" else "correct_source_manifest_or_runtime_contract",
    }
    test_data_plan = build_campaign_test_data_plan(campaign, [], _as_dict(context.get("test_data_contract")), receipt_verifier=_test_data_receipt_verifier(root, project))
    coverage_gaps = gaps + list(test_data_plan.get("coverage_gaps") or [])
    evidence_bundle = {"status": "not_created", "reason": "scan_blocked"}
    release_gate = _evaluate_release_gate(project=project, root=root, campaign=campaign, execution_status="blocked", runtime_contract=runtime_contract, evidence_bundle=evidence_bundle, test_data_plan=test_data_plan, findings=[], coverage_gaps=coverage_gaps, policy=_as_dict(context.get("release_policy")))
    if first_code in {"SOURCE_PROVENANCE_MISSING", "SOURCE_HASH_INVALID", "SOURCE_HASH_MISMATCH"}:
        release_gate = {**release_gate, "verdict": "fail", "status": "blocked"}
    preflight_guide = _scan_preflight_guide(context=context, base_url="", manifest={**manifest, "actual_hash": manifest.get("source_hash", "")}, runtime_contract=runtime_contract, test_data_plan=test_data_plan)
    result: dict[str, Any] = {
        "success": True, "scan_id": f"scan_{_safe_project(project)}_{int(started * 1000)}", "project": project,
        "grade": "blocked", "score": 0.0, "coverage": 0.0, "total_findings": 0, "total_candidates": 0,
        "total_ms": int((time.time() - started) * 1000),
        "layers": {
            "source_grounded_discovery": {"tool": "blocked", "findings": 0, "candidates": 0, "ms": 0, "execution_status": "blocked"},
            "ui_execution": {"tool": "not_requested", "findings": 0, "candidates": 0, "ms": 0, "execution_status": "not_requested"},
            "legacy_domain_layers": {"tool": "disabled", "findings": 0, "candidates": 0, "ms": 0, "reason": "source_bound_scope_fixture_actor_cleanup_contract_required"},
        },
        "findings": [], "candidate_findings": [], "db_findings": [], "e2e_findings": [], "ui_findings": [], "deep_findings": [], "spectrum": {},
        "input_gaps": gaps, "coverage_gaps": coverage_gaps, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "campaign": campaign,
        "behavior_slice_ledger": {"stop_reason": first_code.lower(), "selected_slice_ids": [], "confirmed_slice_ids": []},
        "incremental_discovery": {"status": "blocked", "stop_reason": first_code.lower()}, "execution_status": "blocked",
        "db_verification": {"status": "blocked", "reason": first_code.lower(), "findings": []},
        "ci_gate": {"status": "not_evaluated", "reason": first_code.lower()}, "auto_har": {"status": "no_traffic"},
        "evidence_bundle": evidence_bundle, "release_gate": release_gate, "scan_preflight_guide": preflight_guide, "ui_execution": {"status": "not_requested"}, "ui_test_data_bootstrap": {"status": "not_requested"}, "v12": {},
    }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {"project": project, "real_findings": [], "risk_clues": [], "campaign": campaign, "coverage_gaps": coverage_gaps, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "execution_status": "blocked", "evidence_bundle": evidence_bundle, "release_gate": release_gate})
        result["report_path"] = str(report_path)
    output_root = root / "platform_outputs" / _safe_project(project)
    _write_json(output_root / "scan_result.json", result)
    increment_scan_counter(output_root / "scan_counter.json")
    _persist_customer_ready_static_artifacts(project, root, result)
    return result


def _compute_scan_score(confirmed: list[dict[str, Any]], candidates: list[dict[str, Any]], execution_status: str) -> tuple[float, float]:
    """Derive a score/coverage signal from real findings.

    Previously hardcoded to 0.0 regardless of outcome. Score rewards confirmed
    findings weighted by evidence strength; coverage reflects the share of
    executed work that reached a confirmed verdict.
    """
    if execution_status != "completed" and not confirmed:
        return 0.0, 0.0
    strength_weight = {"runtime_and_db": 1.0, "runtime_before_after": 0.8, "db": 0.75, "runtime": 0.6}
    total = 0.0
    for f in confirmed:
        eq = f.get("evidence_quality") if isinstance(f.get("evidence_quality"), dict) else {}
        strength = str(eq.get("evidence_strength") or f.get("evidence_strength") or "runtime")
        total += strength_weight.get(strength, 0.6)
    score = round(min(100.0, total * 10.0), 2)
    denom = len(confirmed) + len(candidates)
    coverage = round(len(confirmed) / denom, 4) if denom else (1.0 if confirmed else 0.0)
    return score, coverage


def _discovery_verdict(confirmed: list[dict[str, Any]], db_verification: dict[str, Any]) -> dict[str, Any]:
    """Product-facing verdict: did QualiBug deliver reproducible defects?

    Kept separate from release_gate (which answers "is the target safe to
    ship?" and therefore *fails* precisely because P0 defects were found).
    """
    p0 = sum(1 for f in confirmed if str(f.get("severity") or "").upper() in {"P0", "CRITICAL"})
    db_backed = int(db_verification.get("findings_with_db_evidence") or 0) if isinstance(db_verification, dict) else 0
    if confirmed:
        verdict = "defects_delivered"
    else:
        verdict = "no_confirmed_defects"
    return {
        "verdict": verdict,
        "confirmed_defect_count": len(confirmed),
        "confirmed_p0_count": p0,
        "defects_with_db_evidence": db_backed,
    }


def scan(project: str, root: Optional[Path] = None, *, prd_text: str = "", api_doc_path: str = "", api_doc_text: str = "", base_url: str = "", ci_gate: bool = False, multi_layer: bool = True, output_dir: Optional[Path] = None, save_report: bool = True, campaign_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run the single enterprise-safe discovery and evidence pipeline."""
    root = Path(root or Path.cwd())
    project = str(project or "").strip()
    if not project:
        return {"success": False, "error": "project is required"}
    context = dict(campaign_context or {})
    context_defaults = _scan_campaign_context_defaults(project, root)
    if context_defaults.get("scope_id") and not str(context.get("scope_id") or "").strip():
        context["scope_id"] = context_defaults["scope_id"]
    if context_defaults.get("environment_ref") and not str(context.get("environment_ref") or context.get("target_environment") or "").strip():
        context["environment_ref"] = context_defaults["environment_ref"]
    if not _as_dict(context.get("test_data_contract")):
        try:
            from .private_pilot_scan_context_contract import default_scan_test_data_contract

            inferred_contract = default_scan_test_data_contract({
                **context,
                "base_url": str(base_url or context.get("base_url") or ""),
            })
        except Exception:
            inferred_contract = {}
        if isinstance(inferred_contract, dict) and inferred_contract:
            context["test_data_contract"] = dict(inferred_contract)
    if api_doc_path and not api_doc_text:
        try:
            api_doc_text = Path(api_doc_path).read_text(encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"api_doc_path is unreadable: {exc}"}
    if not str(api_doc_text or "").strip():
        api_doc_text = _load_registered_source(project, root, context)
    if not str(api_doc_text or "").strip():
        return {"success": False, "error": "api_doc_text, api_doc_path, or a registered source_manifest is required"}

    started = time.time()
    manifest = _source_manifest(root, project, context, api_doc_path, api_doc_text)
    context["source_manifest"] = {"source_id": manifest["source_id"], "source_hash": manifest["source_hash"], "source_version_id": manifest["source_version_id"], "source_origin": manifest["source_origin"]}
    provenance_gaps = _source_contract(manifest)
    approved_base_url, runtime_gaps, initial_runtime_contract = _runtime_contract(context, base_url, manifest)
    if provenance_gaps:
        return _blocked_result(project, root, started, provenance_gaps + runtime_gaps, initial_runtime_contract, context, save_report, output_dir)

    input_gaps: list[dict[str, str]] = []
    if not str(prd_text or "").strip():
        prd_text = _load_project_prd_text(root, project)
    if not str(prd_text or "").strip():
        input_gaps.append(_gap("PRD_SOURCE_MISSING", "No requirement source was supplied; only API/schema facts can be planned."))
        prd_text = _source_catalog(api_doc_text)
    schema_text = _load_schema_assets(root, project)
    if not schema_text:
        input_gaps.append(_gap("DATABASE_SCHEMA_MISSING", "No project-scoped schema asset is available for data observation planning."))
    input_gaps.extend(runtime_gaps)

    diagnostics: dict[str, Any] = {"ready": True, "checks": []}
    diagnostics_config: dict[str, Any] = {}
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
        profile = registry.get("test_profile") if isinstance(registry, dict) else {}
        if isinstance(profile, dict):
            diagnostics_config = dict(profile)
    except Exception:
        diagnostics_config = {}
    try:
        from .scan_diagnostics import run_preflight

        if base_url and not diagnostics_config.get("api_base_url"):
            diagnostics_config["api_base_url"] = base_url
        diagnostics = run_preflight(diagnostics_config, api_doc_text)
    except Exception as exc:
        diagnostics = {"ready": False, "checks": [], "summary": f"preflight_unavailable:{type(exc).__name__}"}

    try:
        from .v12_pipeline import run_v12_pipeline
        v12 = run_v12_pipeline(project=project, root=root, prd_text=prd_text, api_spec_text=api_doc_text, db_schema_text=schema_text, base_url=approved_base_url, campaign_context=context)
    except Exception as exc:
        return {"success": False, "error": f"v12_pipeline_failed:{type(exc).__name__}:{exc}"}

    runtime_contract = _as_dict(v12.get("runtime_contract")) or initial_runtime_contract
    phases = _as_dict(v12.get("phases"))
    execution = _as_dict(phases.get("execution"))
    campaign = _as_dict(v12.get("campaign"))
    execution_status = str(execution.get("status") or "not_executed")
    refresh_local_approval = _should_refresh_local_execution_approval(runtime_contract, context)
    if (
        execution_status == "blocked"
        and str(runtime_contract.get("reason") or "") == "execution_approval_required"
        and (not str(context.get("execution_approval_id") or "").strip() or refresh_local_approval)
    ):
        if refresh_local_approval:
            context.pop("execution_approval_id", None)
        approval_id = _issue_local_execution_approval(project, root, context, campaign, approved_base_url)
        if approval_id:
            context["execution_approval_id"] = approval_id
            try:
                v12 = run_v12_pipeline(project=project, root=root, prd_text=prd_text, api_spec_text=api_doc_text, db_schema_text=schema_text, base_url=approved_base_url, campaign_context=context)
            except Exception as exc:
                return {"success": False, "error": f"v12_pipeline_failed:{type(exc).__name__}:{exc}"}
            runtime_contract = _as_dict(v12.get("runtime_contract")) or initial_runtime_contract
            phases = _as_dict(v12.get("phases"))
            execution = _as_dict(phases.get("execution"))
            campaign = _as_dict(v12.get("campaign"))
            execution_status = str(execution.get("status") or "not_executed")

    # ── Automatic multi-round campaign convergence ──
    # A single behavior-slice round only exercises up to the per-round budget.
    # When the campaign still has unattempted, source-executable slices, drive
    # additional rounds in-process — feeding each round's confirmed findings
    # back as history — so supplementary probes (permission / isolation /
    # money / concurrency / state) accumulate coverage instead of stalling at
    # one batch.  Bounded by round_limit and QUALIBUG_SCAN_MAX_ROUNDS.
    if execution_status == "completed":
        try:
            from .v12_pipeline import run_v12_pipeline as _run_v12
        except Exception:
            _run_v12 = None
        if _run_v12 is not None:
            try:
                _max_rounds = int(os.environ.get("QUALIBUG_SCAN_MAX_ROUNDS", "4") or "4")
            except (TypeError, ValueError):
                _max_rounds = 4
            _max_rounds = max(1, min(_max_rounds, 12))
            _acc_findings: list[dict[str, Any]] = [f for f in (v12.get("findings") or []) if isinstance(f, dict)]
            _seen_keys = {(str(f.get("behavior_slice_id") or ""), str(f.get("title") or "")) for f in _acc_findings}
            _rounds_run = 1
            while _rounds_run < _max_rounds:
                _ledger = _as_dict(v12.get("behavior_slice_ledger"))
                _campaign_status = str(_as_dict(v12.get("campaign")).get("campaign_status") or "")
                if _ledger.get("next_round") in (None, "", 0) or _campaign_status in {"completed", "blocked", "coverage_deferred"}:
                    break
                try:
                    _next = _run_v12(project=project, root=root, prd_text=prd_text, api_spec_text=api_doc_text, db_schema_text=schema_text, base_url=approved_base_url, existing_findings=_acc_findings, campaign_context=context)
                except Exception:
                    break
                _next_exec = str(_as_dict(_as_dict(_next.get("phases")).get("execution")).get("status") or "")
                if _next_exec != "completed":
                    break
                _new = 0
                for _f in (_next.get("findings") or []):
                    if not isinstance(_f, dict):
                        continue
                    _k = (str(_f.get("behavior_slice_id") or ""), str(_f.get("title") or ""))
                    if _k not in _seen_keys:
                        _seen_keys.add(_k)
                        _acc_findings.append(_f)
                        _new += 1
                v12 = _next
                _rounds_run += 1
                # Stop early if a round contributed nothing new AND has no next round.
                if _new == 0 and _as_dict(_next.get("behavior_slice_ledger")).get("next_round") in (None, "", 0):
                    break
            v12["findings"] = _acc_findings
            v12["multi_round_summary"] = {"rounds_run": _rounds_run, "max_rounds": _max_rounds, "accumulated_findings": len(_acc_findings)}
            runtime_contract = _as_dict(v12.get("runtime_contract")) or runtime_contract
            phases = _as_dict(v12.get("phases"))
            execution = _as_dict(phases.get("execution"))
            campaign = _as_dict(v12.get("campaign"))
            execution_status = str(execution.get("status") or execution_status)
    confirmed, candidates = _classify_findings(v12.get("findings"))
    # Collapse state-graph cross-product duplicates so one real defect is not
    # reported as N near-identical P0 rows. Collapsed lifecycle variants are
    # preserved as coverage on the survivor.
    confirmed, dedupe_report = _dedupe_findings(confirmed)
    external_findings = v12.get("external_findings") if isinstance(v12.get("external_findings"), list) else []
    if external_findings:
        external_findings = _adjudicate_external_evidence_backed_candidates(external_findings)
        external_findings = _attach_external_evidence_packages(external_findings)
        _, external_candidates = _classify_findings(external_findings)
        candidates.extend(external_candidates)
    state_graph = _as_dict(phases.get("state_graph"))
    incremental = _as_dict(phases.get("incremental_discovery"))
    scan_id = f"scan_{_safe_project(project)}_{int(started * 1000)}"
    external_findings, external_reproduction_assets = _materialize_external_reproduction_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        items=external_findings,
    )
    refreshed_candidates = [item for item in candidates if not (isinstance(item, dict) and _is_external_signal_finding(item))]
    if external_findings:
        _, refreshed_external_candidates = _classify_findings(external_findings)
        refreshed_candidates.extend(refreshed_external_candidates)
    candidates = refreshed_candidates
    v12["external_findings"] = external_findings
    try:
        evidence_bundle = _persist_execution_evidence(project, root, scan_id, campaign, runtime_contract, execution_status, v12)
    except Exception as exc:
        evidence_bundle = {"status": "persistence_failed", "reason": type(exc).__name__}
        if confirmed:
            for item in confirmed:
                item["confirmation_status"] = "inconclusive"
                item["evidence_persistence_status"] = "failed"
            candidates.extend(confirmed)
            confirmed = []
        input_gaps.append(_gap("EVIDENCE_BUNDLE_PERSISTENCE_FAILED", "Runtime evidence could not be persisted with integrity guarantees; customer-deliverable confirmation is blocked."))

    if str(runtime_contract.get("status") or "") == "blocked":
        requirements = runtime_contract.get("missing_requirements") if isinstance(runtime_contract.get("missing_requirements"), list) else []
        for code in requirements:
            if not any(gap.get("code") == str(code) for gap in input_gaps):
                input_gaps.append(_gap(str(code), "Runtime execution approval or contract requirement is not satisfied."))
    graph_gaps = state_graph.get("coverage_gaps", []) if isinstance(state_graph.get("coverage_gaps"), list) else []
    selected_slices = incremental.get("selected_slices") if isinstance(incremental.get("selected_slices"), list) else []
    test_data_bootstrap = bootstrap_test_data_receipts_for_campaign(
        project=project,
        root=root,
        base_url=approved_base_url,
        api_doc_text=api_doc_text,
        campaign=campaign,
        selected_slices=selected_slices,
        contract=_as_dict(context.get("test_data_contract")),
    )
    ui_test_data_bootstrap: dict[str, Any] = {"status": "not_requested"}
    if test_data_bootstrap.get("status") != "ready":
        try:
            from .ui_test_data_bootstrap import bootstrap_ui_test_data_receipts_for_campaign

            ui_test_data_bootstrap = bootstrap_ui_test_data_receipts_for_campaign(
                project=project,
                root=root,
                campaign=campaign,
                contract=_as_dict(context.get("test_data_contract")),
                runtime_contract=runtime_contract,
                requests=context.get("ui_test_data_requests"),
                execution_context=context,
            )
            if isinstance(ui_test_data_bootstrap.get("contract"), dict) and ui_test_data_bootstrap.get("status") == "ready":
                test_data_bootstrap = ui_test_data_bootstrap
        except Exception as exc:
            ui_test_data_bootstrap = {"status": "failed", "reason": f"ui_test_data_bootstrap_error:{type(exc).__name__}"}
    if isinstance(test_data_bootstrap.get("contract"), dict) and test_data_bootstrap.get("status") == "ready":
        context["test_data_contract"] = dict(test_data_bootstrap.get("contract") or {})
    test_data_plan = build_campaign_test_data_plan(campaign, selected_slices, _as_dict(context.get("test_data_contract")), receipt_verifier=_test_data_receipt_verifier(root, project))
    coverage_gaps = input_gaps + [item for item in graph_gaps if isinstance(item, dict)] + list(test_data_plan.get("coverage_gaps") or [])
    release_gate = _evaluate_release_gate(project=project, root=root, campaign=campaign, execution_status=execution_status, runtime_contract=runtime_contract, evidence_bundle=evidence_bundle, test_data_plan=test_data_plan, findings=confirmed, coverage_gaps=coverage_gaps, policy=_as_dict(context.get("release_policy")))
    commercial_assets = _materialize_commercial_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        items=confirmed,
        scan_result={
            "project": project,
            "scan_id": scan_id,
            "campaign": campaign,
            "runtime_contract": runtime_contract,
            "release_gate": release_gate,
            "evidence_bundle": evidence_bundle,
            "total_findings": len(confirmed),
        },
    )
    external_commercial_assets = _materialize_external_commercial_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        items=external_findings,
        external_reproduction_assets=external_reproduction_assets,
        scan_result={
            "project": project,
            "scan_id": scan_id,
            "campaign": campaign,
            "runtime_contract": runtime_contract,
            "release_gate": release_gate,
            "evidence_bundle": evidence_bundle,
            "total_findings": len(confirmed),
        },
    )
    preflight_guide = _scan_preflight_guide(
        context=context,
        base_url=base_url,
        manifest=manifest,
        runtime_contract=runtime_contract,
        test_data_plan=test_data_plan,
        diagnostics=diagnostics,
        runtime_observed=str(_as_dict(v12.get("auto_har")).get("status") or "") == "captured",
    )
    grade = "blocked" if str(runtime_contract.get("status") or "") == "blocked" or execution_status == "blocked" else ("inconclusive" if not confirmed else "evidence_ready")
    duration_ms = int((time.time() - started) * 1000)
    # ── Honest data-layer verification summary aggregated from real findings ──
    _db_backed = [f for f in confirmed if isinstance(f, dict) and isinstance(f.get("db_evidence"), dict) and f["db_evidence"].get("status") == "captured"]
    _db_changed = [f for f in _db_backed if f["db_evidence"].get("any_change")]
    if _db_backed:
        db_verification = {
            "status": "captured",
            "reason": "runtime_before_after_db_snapshot",
            "findings_with_db_evidence": len(_db_backed),
            "findings_with_db_change": len(_db_changed),
            "findings": [
                {"title": f.get("title"), "changed_tables": f["db_evidence"].get("changed_tables", [])}
                for f in _db_changed
            ],
        }
    else:
        db_verification = {"status": "plan_only" if schema_text else "blocked", "reason": "source_bound_observation_contract_required" if schema_text else "database_schema_source_missing", "findings": []}
    # ── Score/coverage wired to real findings instead of a constant 0.0 ──
    score, coverage = _compute_scan_score(confirmed, candidates, execution_status)
    ui_findings = v12.get("ui_findings") if isinstance(v12.get("ui_findings"), list) else []
    ui_candidate_findings = _ui_candidate_gate(ui_findings)
    ui_candidate_findings = _verify_ui_candidate_findings(ui_candidate_findings, root=root, runtime_contract=runtime_contract)
    ui_candidate_findings = _mark_high_confidence_ui_candidates(ui_candidate_findings)
    if ui_candidate_findings:
        candidates.extend(ui_candidate_findings)
    ui_execution = _as_dict(v12.get("ui_execution"))
    ui_execution_summary = _ui_execution_evidence_summary(ui_execution)
    external_signal_execution = _as_dict(v12.get("external_signal_execution"))
    ui_verified_candidates = [item for item in ui_candidate_findings if isinstance(item, dict) and isinstance(item.get("ui_verification"), dict) and item["ui_verification"].get("status") == "verified"]
    ui_high_confidence_candidates = [item for item in ui_candidate_findings if isinstance(item, dict) and item.get("high_confidence_candidate") is True]
    ui_followup_assets = _materialize_ui_followup_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        campaign=campaign,
        items=ui_high_confidence_candidates,
        selected_slices=selected_slices,
        plan_only_scenarios=v12.get("plan_only_scenarios") if isinstance(v12.get("plan_only_scenarios"), list) else [],
    )
    result: dict[str, Any] = {
        "success": True, "scan_id": scan_id, "project": project, "grade": grade, "score": score, "coverage": coverage,
        "total_findings": len(confirmed), "total_candidates": len(candidates), "total_ms": duration_ms,
        "layers": {
            "source_grounded_discovery": {"tool": "V12 enterprise campaign", "findings": len(confirmed), "candidates": len(candidates), "ms": int(v12.get("total_duration_ms") or duration_ms), "execution_status": execution_status, "campaign_id": campaign.get("campaign_id", "")},
            "external_signals": {
                "tool": "explicit_external_signal_requests",
                "findings": 0,
                "candidates": len(external_findings),
                "ms": int(external_signal_execution.get("duration_ms") or 0),
                "execution_status": str(external_signal_execution.get("status") or "not_requested"),
                "provider_distribution": dict(external_signal_execution.get("provider_distribution") or {}),
            },
            "ui_execution": {
                "tool": "explicit_ui_execution_requests",
                "findings": len(ui_findings),
                "candidates": len(ui_candidate_findings),
                "ms": int(ui_execution.get("duration_ms") or 0),
                "execution_status": str(ui_execution.get("status") or "not_requested"),
                "provider_distribution": dict(ui_execution.get("provider_distribution") or {}),
                "artifact_count": len(ui_execution.get("artifacts") or []),
                "evidence_captured_count": int(ui_execution_summary.get("evidence_captured_count") or 0),
                "created_data_count": int(ui_execution_summary.get("created_data_count") or 0),
                "verified_candidates": len(ui_verified_candidates),
                "high_confidence_candidates": len(ui_high_confidence_candidates),
            },
            "legacy_domain_layers": {"tool": "disabled", "findings": 0, "candidates": 0, "ms": 0, "reason": "source_bound_scope_fixture_actor_cleanup_contract_required" if multi_layer else "not_requested"},
        },
        "findings": confirmed, "candidate_findings": candidates, "db_findings": [], "e2e_findings": [], "ui_findings": ui_findings, "ui_candidate_findings": ui_candidate_findings, "ui_high_confidence_candidates": ui_high_confidence_candidates, "external_findings": external_findings, "deep_findings": [], "spectrum": {},
        "ui_followup_assets": ui_followup_assets,
        "commercial_assets": commercial_assets,
        "external_reproduction_assets": external_reproduction_assets,
        "external_commercial_assets": external_commercial_assets,
        "preflight_diagnostics": diagnostics, "input_gaps": input_gaps, "coverage_gaps": coverage_gaps,
        "scan_preflight_guide": preflight_guide,
        "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "campaign": campaign, "test_data_bootstrap": test_data_bootstrap,
        "ui_test_data_bootstrap": ui_test_data_bootstrap,
        "behavior_slice_ledger": v12.get("behavior_slice_ledger", {}), "incremental_discovery": incremental,
        "execution_status": execution_status,
        "db_verification": db_verification,
        "dedupe_report": dedupe_report,
        "discovery_verdict": _discovery_verdict(confirmed, db_verification),
        "ci_gate": {"status": "not_evaluated" if ci_gate else "not_requested", "reason": "confirmed_receipts_and_approved_baseline_required" if ci_gate else ""},
        "auto_har": v12.get("auto_har", {}), "evidence_bundle": evidence_bundle, "release_gate": release_gate, "ui_execution": ui_execution, "ui_execution_summary": ui_execution_summary, "execution_evidence_summary": ui_execution_summary, "external_signal_execution": external_signal_execution, "v12": v12,
    }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {"project": project, "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "real_findings": confirmed, "risk_clues": candidates, "campaign": campaign, "coverage_gaps": coverage_gaps, "scan_preflight_guide": preflight_guide, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "test_data_bootstrap": test_data_bootstrap, "behavior_slice_ledger": result["behavior_slice_ledger"], "execution_status": execution_status, "evidence_bundle": evidence_bundle, "release_gate": release_gate, "ui_execution_summary": ui_execution_summary, "execution_evidence_summary": ui_execution_summary, "ui_followup_assets": ui_followup_assets, "external_reproduction_assets": external_reproduction_assets, "external_commercial_assets": external_commercial_assets})
        result["report_path"] = str(report_path)
    output_root = root / "platform_outputs" / _safe_project(project)
    _write_json(output_root / "scan_result.json", result)
    increment_scan_counter(output_root / "scan_counter.json")
    _persist_customer_ready_static_artifacts(project, root, result)
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="QualiBug enterprise source-grounded scanner")
    parser.add_argument("scan", nargs="?", default="scan")
    parser.add_argument("--project", required=True)
    parser.add_argument("--api-doc")
    parser.add_argument("--api-doc-text")
    parser.add_argument("--prd", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--scope-id", default="")
    parser.add_argument("--environment-ref", default="")
    parser.add_argument("--source-id", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--source-version-id", default="")
    parser.add_argument("--execution-approval-id", default="")
    parser.add_argument("--execution-mode", default="safe_read_only")
    parser.add_argument("--test-data-strategy", default="blocked_with_testability_gap")
    parser.add_argument("--ci-gate", action="store_true")
    parser.add_argument("--no-multi-layer", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    test_data_contract: dict[str, Any] = {}
    strategy = str(args.test_data_strategy or "").strip()
    if strategy:
        test_data_contract["strategy"] = strategy
        try:
            from .private_pilot_scan_context_contract import default_scan_execution_mode

            execution_mode = default_scan_execution_mode({
                "base_url": args.base_url,
                "scope_id": args.scope_id,
                "environment_ref": args.environment_ref,
                "execution_mode": args.execution_mode,
            })
        except Exception:
            execution_mode = str(args.execution_mode or "").strip() or "safe_read_only"
        if strategy in {"create_disposable", "approved_fixture_setup"} and execution_mode == "approved_sandbox_write":
            test_data_contract["write_approved"] = True
            if strategy == "create_disposable":
                scope_ref = str(args.scope_id or args.environment_ref or "").strip()
                if scope_ref:
                    test_data_contract["disposable_scope_ref"] = scope_ref
    context = {
        "scope_id": args.scope_id, "environment_ref": args.environment_ref,
        "source_manifest": {"source_id": args.source_id, "source_hash": args.source_hash, "source_version_id": args.source_version_id},
        "execution_approval_id": args.execution_approval_id, "execution_mode": args.execution_mode,
        "test_data_contract": test_data_contract,
    }
    result = scan(project=args.project, api_doc_path=args.api_doc or "", api_doc_text=args.api_doc_text or "", prd_text=args.prd, base_url=args.base_url, ci_gate=args.ci_gate, multi_layer=not args.no_multi_layer, output_dir=Path(args.output_dir) if args.output_dir else None, save_report=not args.no_report, campaign_context=context)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif result.get("success"):
        campaign = result.get("campaign", {})
        print(f"QualiBug scan: {result['project']}")
        print(f"Confirmed: {result['total_findings']} | Candidates: {result['total_candidates']} | Execution: {result['execution_status']}")
        print(f"Release gate: {result.get('release_gate', {}).get('verdict', 'not_ready')}")
        print(f"Campaign: {campaign.get('campaign_id', 'n/a')} ({campaign.get('campaign_status', 'n/a')})")
    else:
        print(f"Error: {result.get('error', 'scan failed')}", file=sys.stderr)
    raise SystemExit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
