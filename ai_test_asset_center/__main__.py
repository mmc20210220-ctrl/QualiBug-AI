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
from .scan_diagnostics import increment_scan_counter
from .enterprise_test_data_plan import build_campaign_test_data_plan
from .test_data_receipt_bootstrap import bootstrap_test_data_receipts_for_campaign
from .target_policy import build_target_policy_decision
from .customer_delivery_gate import (
    customer_delivery_rejection_reasons,
    is_customer_deliverable_defect,
)

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

from .product_scan_mainline import (  # noqa: F401
    CanonicalProductScopeError,
    _apply_scan_execution_defaults,
    _as_dict,
    _bind_discovery_mainline_identity,
    _bind_scan_rows_to_mainline,
    _canonical_product_scope,
    _first_text,
    _gap,
    _reject_evaluator_private_context,
    _safe_project,
    _scan_campaign_context_defaults,
    _sha256,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON only after unified recursive redaction + secret scan."""
    from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

    try:
        write_json_redacted(path, payload)
    except ArtifactSecretLeakError as exc:
        # Fail closed: do not leave a secret-bearing artifact on disk.
        import sys as _sys

        print(
            f"[scan] FAILED_SAFE artifact secret scan blocked write to {path}: {exc}",
            file=_sys.stderr,
        )
        raise


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
        "defects": snapshot.get("defects", []) or (
            [dict(f, is_reproducible=True) for f in (result.get("findings") or [])
             if isinstance(f, dict) and f.get("customer_delivery_status") == "defect"]
            if isinstance(result, dict) else []
        ),
        "clues": snapshot.get("clues", []),
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


from .scan_commercial_assets import (  # noqa: F401
    _external_priority,
    _write_markdown,
    _commercial_priority,
    _commercial_finding_customer_ready,
    _commercial_candidate_id,
    _commercial_finding_reason,
    _commercial_runtime_observation,
    _build_materialized_commercial_assets,
    _materialize_commercial_assets,
    _materialize_external_commercial_assets,
)


def _load_schema_assets(root: Path, project: str) -> str:
    """Load project-scoped database schema for data-layer observation planning.

    Checks, in order:
    1. ``platform_workspace/<project>/input/*.sql`` (canonical workspace)
    2. ``platform_inputs/<project>/schema.sql`` (ingested customer materials)
    3. ``platform_inputs/<project>/DB_SCHEMA.md`` (markdown schema doc)
    """
    safe = _safe_project(project)
    chunks: list[str] = []
    seen_hashes: set[str] = set()

    def _ingest(path: Path) -> None:
        if not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return
        if not text:
            return
        digest = _sha256(text)
        if digest in seen_hashes:
            return
        seen_hashes.add(digest)
        chunks.append(text[:1_000_000])

    workspace_sql = root / "platform_workspace" / safe / "input"
    if workspace_sql.exists():
        for path in sorted(workspace_sql.glob("*.sql")):
            _ingest(path)

    inputs_dir = root / "platform_inputs" / safe
    _ingest(inputs_dir / "schema.sql")
    db_schema_md = inputs_dir / "DB_SCHEMA.md"
    if db_schema_md.exists():
        _ingest(db_schema_md)

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
    """Load the best available registered source as API doc text.

    Prefers OpenAPI / Swagger / Postman type sources.  Falls back to any
    registered source when no API spec is available — the caller can still
    extract partial endpoint hints from PRDs and DB schemas.
    """
    manifest = _as_dict(context.get("source_manifest"))
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    try:
        from .enterprise_source_registry import SourceRegistryError, list_source_assets, load_source_content
        if not _SHA256_RE.fullmatch(source_hash):
            assets = list_source_assets(project, root=root)
            _api_spec_types = {"openapi", "openapi3", "swagger", "postman", "api_spec"}

            def _sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
                """Weight: API-spec sources first, then by recency."""
                _type = str(item.get("source_type") or "").strip().lower()
                _is_api = 0 if _type in _api_spec_types else 1
                return (_is_api, str(item.get("updated_at_utc") or ""), str(item.get("source_id") or ""))

            latest = min(
                (
                    item
                    for item in assets
                    if isinstance(item, dict) and _SHA256_RE.fullmatch(str(item.get("latest_source_hash") or "").strip().lower())
                ),
                key=_sort_key,
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
    environment_ref = str(context.get("environment_ref") or context.get("target_environment") or "").strip()
    if not environment_ref:
        missing.append(_gap("ENVIRONMENT_REFERENCE_MISSING", "An approved environment_ref is required before runtime probing."))
    environment_type = _first_text(
        context.get("environment_type"),
        context.get("environment_kind"),
        context.get("environment_class"),
    ).lower()
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip() or "safe_read_only"
    if execution_mode == "approved_sandbox_write" and not environment_type:
        missing.append(_gap("UNKNOWN_ENVIRONMENT", "Write execution requires an explicit non-production environment_type."))
    test_data = _as_dict(context.get("test_data_contract"))
    if test_data.get("strategy") in {"create_disposable", "approved_fixture_setup"} and test_data.get("write_approved") is not True:
        missing.append(_gap("WRITE_APPROVAL_MISSING", "Write-capable test-data strategies require explicit write approval."))
    approved_base_url = _first_text(
        context.get("approved_base_url"),
        _as_dict(context.get("target_policy")).get("approved_base_url"),
        base_url if not missing else "",
    )
    decision = build_target_policy_decision(
        requested_base_url=base_url,
        approved_base_url=approved_base_url,
        environment_type=environment_type,
        environment_ref=environment_ref,
        execution_mode=execution_mode,
        runtime_status="approved" if not missing else "blocked",
    )
    if execution_mode == "approved_sandbox_write" and not decision.get("write_allowed"):
        for code in decision.get("blocking_codes") or []:
            missing.append(_gap(str(code), "Target Policy blocked governed write execution."))
    elif not decision.get("read_allowed"):
        for code in decision.get("blocking_codes") or []:
            missing.append(_gap(str(code), "Target Policy blocked runtime access."))
    if missing:
        return "", missing, {
            "status": "blocked",
            "reason": "runtime_contract_missing",
            "source_manifest": public_manifest,
            "environment_ref": environment_ref,
            "environment_kind": environment_type,
            "execution_mode": execution_mode,
            "target_policy_decision": decision,
        }
    normalized_base = str(decision.get("approved_base_url") or "")
    return normalized_base, [], {
        "status": "approved",
        "reason": "",
        "requested_base_url": str(decision.get("requested_base_url") or ""),
        "approved_base_url": normalized_base,
        "environment_ref": environment_ref,
        "environment_kind": environment_type,
        "execution_mode": execution_mode,
        "source_manifest": public_manifest,
        "target_policy_decision": decision,
    }


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
    target_decision = _as_dict(runtime_contract.get("target_policy_decision"))
    if not target_decision and base_url:
        target_decision = build_target_policy_decision(
            requested_base_url=base_url,
            approved_base_url=runtime_contract.get("approved_base_url"),
            environment_type=_first_text(
                runtime_contract.get("environment_kind"),
                context.get("environment_type"),
                context.get("environment_kind"),
                context.get("environment_class"),
            ),
            environment_ref=_first_text(
                runtime_contract.get("environment_ref"),
                context.get("environment_ref"),
                context.get("target_environment"),
            ),
            execution_mode=_first_text(runtime_contract.get("execution_mode"), context.get("execution_mode"), "safe_read_only"),
            runtime_status=runtime_contract.get("status"),
        )
    source_bound_nonproduction = bool(
        target_decision.get("write_allowed")
        and manifest.get("source_id")
        and manifest.get("source_hash")
        and str(context.get("scope_id") or "").strip()
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
            "status": "ready" if target_decision.get("read_allowed") else ("configured_unverified" if base_url else "missing"),
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
            "key": "environment_type",
            "label": "explicit_environment_classification",
            "status": "ready" if str(target_decision.get("environment_type") or "").strip() else "missing",
            "required": bool(base_url and str(context.get("execution_mode") or "") == "approved_sandbox_write"),
            "detail": str(target_decision.get("environment_type") or ""),
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
            "label": "execution_authorization_basis",
            "status": "ready" if str(context.get("execution_approval_id") or "").strip() else ("not_required" if source_bound_nonproduction or not base_url or str(context.get("execution_mode") or "") == "safe_read_only" else "missing"),
            "required": bool(base_url and str(context.get("execution_mode") or "") == "approved_sandbox_write" and not source_bound_nonproduction),
            "detail": str(context.get("execution_approval_id") or target_decision.get("authorization_basis") or ""),
        },
        {
            "key": "target_policy",
            "label": "target_policy_decision",
            "status": "ready" if target_decision.get("status") == "approved" else "failed",
            "required": bool(base_url),
            "detail": ",".join(str(code) for code in target_decision.get("blocking_codes") or []),
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
        "blocking_codes": sorted(set(str(code) for code in target_decision.get("blocking_codes") or [] if str(code))),
        "target_policy_decision": target_decision,
        "checks": checks,
        "healthy_claim_allowed": not missing and runtime_status == "approved" and bool(target_decision.get("read_allowed") if base_url else True),
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
        if has_real_confirmation_receipt(row) and is_customer_deliverable_defect(row):
            row["confirmation_status"] = "confirmed"
            confirmed.append(row)
        else:
            reasons = customer_delivery_rejection_reasons(row)
            row["upstream_gate_passed"] = bool(row.get("gate_passed"))
            row["gate_passed"] = False
            row["customer_delivery_status"] = "candidate"
            row["customer_delivery_gate_reasons"] = reasons
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

    def _protocol_body(value: Any) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            text = str(value or "")
        # Normalize only volatile identities. Numeric boundary/amount values are
        # business semantics and must remain distinct.
        text = _re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}", "{id}", text)
        text = _re.sub(r"\b[0-9a-fA-F]{16,}\b", "{id}", text)
        return text

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
        protocol_rules = {"server_5xx", "expected_status_mismatch", "wrong_create_status", "200_with_error"}
        if rule in protocol_rules:
            raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
            request_raw = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
            response_raw = raw.get("response_raw") if isinstance(raw.get("response_raw"), dict) else {}
            key = (
                "protocol_runtime",
                rule,
                str(request_raw.get("method") or "").upper(),
                _norm(str(request_raw.get("path") or "")),
                int(response_raw.get("status_code") or 0),
                str(request_raw.get("actor") or ""),
                _norm(str(oracle.get("expected") or item.get("expected") or "")),
                _protocol_body(request_raw.get("body")) if "body" in request_raw else "",
            )
        else:
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
    formal_projection = _as_dict(v12.get("formal_count_projection"))
    canonical_representatives = (
        formal_projection.get("canonical_representative_findings")
        if isinstance(formal_projection.get("canonical_representative_findings"), list)
        else []
    )
    external_findings = v12.get("external_findings") if isinstance(v12.get("external_findings"), list) else []
    runtime_candidates = (
        v12.get("candidate_findings")
        if isinstance(v12.get("candidate_findings"), list)
        else []
    )
    registry = _as_dict(v12.get("canonical_defect_registry"))
    registry_ids = [
        str(value or "").strip()
        for value in registry.get("canonical_defect_ids", [])
    ] if isinstance(registry.get("canonical_defect_ids"), list) else []
    representative_rows = [
        dict(item) for item in canonical_representatives if isinstance(item, dict)
    ]
    representative_ids = [
        str(item.get("canonical_defect_id") or "").strip()
        for item in representative_rows
    ]
    declared_rows = [dict(item) for item in findings if isinstance(item, dict)]
    if registry:
        if representative_ids != registry_ids:
            raise ValueError("canonical_representative_scope_mismatch")
        persisted_findings = representative_rows
    else:
        persisted_findings = declared_rows
    persisted_candidates = [
        dict(item)
        for item in [*runtime_candidates, *external_findings]
        if isinstance(item, dict)
    ]
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
        candidate_findings=persisted_candidates,
        canonical_defect_registry=registry,
        delivery_occurrences=(
            v12.get("delivery_occurrences")
            if isinstance(v12.get("delivery_occurrences"), list)
            else []
        ),
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
    from .discovery_funnel import reconcile_product_pipeline_health

    discovery_funnel: dict[str, Any] = {}
    pipeline_health = reconcile_product_pipeline_health(
        {},
        execution_status="blocked",
        preflight_diagnostics={"ready": False, "all_checks_passed": False, "errors": 1},
    )
    discovery_funnel["pipeline_health"] = pipeline_health
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

    # ── Phase 108R: Auto-generate Issue Lifecycle Center after scan ──
    # Acceptance Criterion 12: lifecycle center aggregates discovery + regression
    # states and auto-migrates bug status based on evidence.
    try:
        from .issue_lifecycle_center import build_issue_lifecycle_center
        lifecycle = build_issue_lifecycle_center(project, root, options={"auto_generate_missing": False})
        result["lifecycle_center"] = {
            "ref": f"platform_outputs/{_safe_project(project)}/issue_lifecycle/issue_lifecycle.json",
            "summary": lifecycle.get("summary", {}),
            "active_issue_count": lifecycle.get("summary", {}).get("active_issue_count", 0),
        }
    except Exception:
        pass

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


# High-risk slice kinds a customer cares most about: authorization / tenant
# isolation / money conservation / concurrency. Silently skipping any of these
# while reporting a clean completion is a delivery-credibility hazard.
_HIGH_VALUE_SLICE_KINDS = ("permission", "isolation", "money", "concurrency")


def _apply_coverage_honesty_guard(
    v12: dict[str, Any], grade: str, execution_status: str
) -> tuple[dict[str, Any], str]:
    """主链 4/5 覆盖诚实性守卫 (coverage honesty guard).

    The behavior model routinely plans more slices than a single campaign can
    execute — permission / isolation / money / concurrency slices often need a
    multi-actor runtime contract (multiple logged-in principals + state
    preconditions). When those high-value slices are never executed but the
    campaign still reports a clean 'inconclusive'/'evidence_ready' completion, a
    customer could read "completed / 0 findings" while EVERY authorization check
    was silently skipped.

    This guard makes that non-silent: it lists the unexecuted high-value slices
    and downgrades a *completed* clean grade to 'partial_coverage'. It only ever
    ADDS signal and only downgrades — it never upgrades a grade nor fabricates
    coverage. Single source of truth: the v12 ``behavior_slices`` contract +
    ``behavior_slice_ledger`` execution status (same producers as 主链 4).
    """
    slices = v12.get("behavior_slices") if isinstance(v12.get("behavior_slices"), list) else []
    ledger = v12.get("behavior_slice_ledger") if isinstance(v12.get("behavior_slice_ledger"), dict) else {}
    slice_status = ledger.get("slice_status") if isinstance(ledger.get("slice_status"), dict) else {}
    attempted_ids = {str(x) for x in (ledger.get("attempted_slice_ids") or []) if x}
    _attempted_states = {"running", "passed", "failed", "confirmed", "blocked"}

    high_value_total = 0
    unexecuted: list[dict[str, Any]] = []
    for s in slices:
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind") or "").strip().lower()
        if kind not in _HIGH_VALUE_SLICE_KINDS:
            continue
        high_value_total += 1
        sid = str(s.get("slice_id") or s.get("id") or "")
        state = str(slice_status.get(sid) or "").strip().lower()
        executed = (state in _attempted_states) or (bool(sid) and sid in attempted_ids)
        if not executed:
            unexecuted.append({
                "slice_id": sid,
                "kind": kind,
                "entity": s.get("entity"),
                "endpoints": list(s.get("endpoints") or [])[:6],
                "status": state or "not_executed",
            })

    honesty: dict[str, Any] = {
        "high_value_kinds": list(_HIGH_VALUE_SLICE_KINDS),
        "total_slices": len(slices),
        "high_value_total": high_value_total,
        "high_value_unexecuted": len(unexecuted),
        "unexecuted_high_value_slices": unexecuted[:50],
        "honest": len(unexecuted) == 0,
        "downgraded": False,
    }
    # ── Distinguish: 'resumable partial' (campaign is still active with a
    # concrete next_round — the customer can re-run the scan to continue) vs
    # 'terminal partial' (campaign claims completed yet high-value slices were
    # silently skipped — genuine credibility risk). ──
    _campaign = v12.get("campaign") if isinstance(v12.get("campaign"), dict) else {}
    _campaign_status = str(_campaign.get("campaign_status") or ledger.get("campaign_status") or "").strip().lower()
    _next_round = ledger.get("next_round")
    _has_next = bool(
        (_next_round is not None and str(_next_round).strip().isdigit() and int(_next_round) > 0)
    )
    honesty["campaign_status"] = _campaign_status or "unknown"
    honesty["next_round"] = _next_round
    honesty["resumable"] = _campaign_status == "active" and _has_next
    honesty["terminal_skip"] = _campaign_status == "completed" and not honesty["honest"]
    if honesty["terminal_skip"]:
        honesty["actionable"] = (
            "Campaign marked completed but high-value slices (permission/isolation/"
            "money/concurrency) were never executed — this is a genuine coverage gap "
            "that may hide authorization or data-integrity defects."
        )
    elif honesty["resumable"]:
        honesty["actionable"] = (
            "Campaign is still active — re-run the scan to continue from the "
            "next round and cover the remaining high-value slices."
        )
    # Only act on a *completed* campaign: a blocked / not-executed scan already
    # signals incompleteness through its own grade, and the phase-110 contract
    # tests rely on those staying 'blocked'/'inconclusive'.
    if execution_status == "completed" and unexecuted and grade in {"inconclusive", "evidence_ready"}:
        honesty["grade_before_guard"] = grade
        honesty["downgraded"] = True
        grade = "partial_coverage"
    return honesty, grade


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


def _scan_impl(project: str, root: Optional[Path] = None, *, prd_text: str = "", api_doc_path: str = "", api_doc_text: str = "", base_url: str = "", ci_gate: bool = False, multi_layer: bool = True, output_dir: Optional[Path] = None, save_report: bool = True, campaign_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run the single enterprise-safe discovery and evidence pipeline."""
    context = dict(campaign_context or {})
    # First-class merge of private-pilot ContextVar campaign context. Nested or
    # continuous callers may bind pending metadata without monkey-patching scan().
    try:
        from .private_pilot_scan_context_contract import current_scan_campaign_context

        pending_context = current_scan_campaign_context()
    except Exception:
        pending_context = None
    if isinstance(pending_context, dict) and pending_context:
        for key, value in pending_context.items():
            if key == "base_url":
                continue
            if value and (key not in context or not context.get(key)):
                context[key] = value
        if pending_context.get("base_url") and not base_url:
            base_url = str(pending_context["base_url"]).rstrip("/")
    _reject_evaluator_private_context(context)
    root = Path(root or Path.cwd())
    project = str(project or "").strip()
    if not project:
        return {"success": False, "error": "project is required"}

    # ── Session health gate: auto-detect and recover from stale/corrupt sessions ──
    # Long-running loops can leave behind FAILED_TERMINAL or orphaned RUNNING
    # leases that block all subsequent scan() calls. This gate diagnoses and
    # auto-recovers without requiring manual git reset --hard.
    try:
        from .loop_runtime import LoopRuntimeSession
        session_health = LoopRuntimeSession.ensure_session_healthy(project, root / "platform_outputs" / _safe_project(project))
        if not session_health.get("can_proceed", True):
            return {
                "success": False,
                "error": "session_unhealthy",
                "session_health": session_health,
                "hint": "A stale or corrupt loop session is blocking scans. "
                        "Try restarting the backend service or manually running: "
                        "LoopRuntimeSession.force_reset_stale_session(project_id, output_dir)",
            }
        if session_health.get("action") == "auto_reset":
            # Log that we auto-recovered so operators can see it
            import sys as _sys
            print(
                f"[scan] auto-recovered from stale session: "
                f"{session_health.get('reset_summary', {}).get('cleaned', [])}",
                file=_sys.stderr, flush=True,
            )
    except Exception:
        # Never block a scan due to a session-health check failure itself;
        # the check is advisory.
        pass

    context_defaults = _scan_campaign_context_defaults(project, root)
    if context_defaults.get("scope_id") and not str(context.get("scope_id") or "").strip():
        context["scope_id"] = context_defaults["scope_id"]
    if context_defaults.get("environment_ref") and not str(context.get("environment_ref") or context.get("target_environment") or "").strip():
        context["environment_ref"] = context_defaults["environment_ref"]
    if context_defaults.get("environment_type") and not _first_text(
        context.get("environment_type"),
        context.get("environment_kind"),
        context.get("environment_class"),
    ):
        context["environment_type"] = context_defaults["environment_type"]
    # Keep every product entrypoint on the same execution contract. The
    # private-pilot HTTP path already derives these defaults; direct scan()
    # callers (automation, CI and benchmark harnesses) must behave identically.
    # Production/unknown targets remain fail-closed by the shared sandbox gate.
    context = _apply_scan_execution_defaults(context, base_url)
    if api_doc_path and not api_doc_text:
        try:
            api_doc_text = Path(api_doc_path).read_text(encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"api_doc_path is unreadable: {exc}"}
    if not str(api_doc_text or "").strip():
        api_doc_text = _load_registered_source(project, root, context)
    if not str(api_doc_text or "").strip():
        return {"success": False, "error": "api_doc_text, api_doc_path, or a registered source_manifest is required"}

    # Keep immutable source identity separate from the derived, merged API
    # catalog. Enrichment may add other registered documents for planning, but
    # it must never rewrite the primary source hash recorded by the customer.
    source_api_doc_text = api_doc_text
    try:
        from .api_doc_assets import enrich_api_spec_text

        api_doc_text = enrich_api_spec_text(root, project, api_doc_text)
    except Exception:
        pass
    context["_source_verification_text"] = source_api_doc_text

    started = time.time()
    manifest = _source_manifest(root, project, context, api_doc_path, source_api_doc_text)
    context["source_manifest"] = {"source_id": manifest["source_id"], "source_hash": manifest["source_hash"], "source_version_id": manifest["source_version_id"], "source_origin": manifest["source_origin"]}
    provenance_gaps = _source_contract(manifest)
    approved_base_url, runtime_gaps, initial_runtime_contract = _runtime_contract(context, base_url, manifest)
    if base_url and context.get("runtime_scenario_contract"):
        from .runtime_scenario_contract_gate import runtime_scenario_contract_gaps

        scenario_gaps = runtime_scenario_contract_gaps(context)
        if scenario_gaps:
            missing_requirements = sorted(
                {
                    str(item.get("code") or "")
                    for item in scenario_gaps
                    if str(item.get("code") or "")
                }
            )
            blocked_runtime_contract = {
                **initial_runtime_contract,
                "status": "blocked",
                "reason": "runtime_scenario_contract_blocked",
                "approved_base_url": "",
                "missing_requirements": missing_requirements,
            }
            return _blocked_result(
                project,
                root,
                started,
                provenance_gaps + runtime_gaps + scenario_gaps,
                blocked_runtime_contract,
                context,
                save_report,
                output_dir,
            )
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
        # Preflight must consume the same exact target grant as the runtime
        # executor. This permits an explicitly approved internal test target
        # without weakening the production/unknown SSRF boundary.
        if approved_base_url:
            diagnostics_config["approved_base_url"] = approved_base_url
        environment_kind = str(
            context.get("environment_kind")
            or context.get("environment_type")
            or context.get("target_environment_kind")
            or ""
        ).strip()
        if environment_kind:
            diagnostics_config["environment_kind"] = environment_kind
            diagnostics_config["environment_type"] = environment_kind
        diagnostics_config.setdefault("execution_mode", str(context.get("execution_mode") or ""))
        diagnostics = run_preflight(diagnostics_config, api_doc_text)
    except Exception as exc:
        diagnostics = {"ready": False, "checks": [], "summary": f"preflight_unavailable:{type(exc).__name__}"}

    context = _bind_discovery_mainline_identity(
        project=project,
        context=context,
        started=started,
    )
    try:
        from .v12_pipeline import run_v12_pipeline
        v12 = run_v12_pipeline(project=project, root=root, prd_text=prd_text, api_spec_text=api_doc_text, db_schema_text=schema_text, base_url=approved_base_url, campaign_context=context)
    except Exception as exc:
        return {"success": False, "error": f"v12_pipeline_failed:{type(exc).__name__}:{exc}"}

    runtime_contract = _as_dict(v12.get("runtime_contract")) or initial_runtime_contract
    phases = _as_dict(v12.get("phases"))
    execution = _as_dict(phases.get("execution"))
    campaign = _as_dict(v12.get("campaign"))

    # Resolve a pre-registered execution approval by the campaign's stable
    # bindings (scope / environment / source hash / target origin). Each scan
    # run receives a fresh campaign_id, so matching must ignore campaign_id and
    # rely on the immutable binding tuple. This backfills runtime_contract so a
    # scan that omitted an explicit execution_approval_id still surfaces the
    # governing approval for audit, WITHOUT silently auto-issuing a new one.
    if not isinstance(runtime_contract.get("execution_approval"), dict) or not runtime_contract.get("execution_approval"):
        _rc_scope = str(campaign.get("scope_id") or context.get("scope_id") or "").strip()
        _rc_env = str(
            runtime_contract.get("environment_ref")
            or context.get("environment_ref")
            or context.get("target_environment")
            or ""
        ).strip()
        _rc_source = str(
            _as_dict(runtime_contract.get("source_manifest")).get("source_hash")
            or manifest.get("source_hash")
            or ""
        ).strip().lower()
        _rc_target = str(runtime_contract.get("approved_base_url") or approved_base_url or "").strip()
        if _rc_scope and _rc_env and _rc_source and _rc_target:
            try:
                from .execution_approvals import resolve_execution_approval_for_campaign

                _resolved = resolve_execution_approval_for_campaign(
                    project,
                    root=root,
                    scope_id=_rc_scope,
                    environment_ref=_rc_env,
                    source_hash=_rc_source,
                    target_base_url=_rc_target,
                )
                if _resolved.get("found"):
                    runtime_contract = dict(runtime_contract)
                    runtime_contract["execution_approval"] = _resolved["approval"]
            except Exception:
                pass

    from .discovery_funnel import effective_execution_status

    execution_status = effective_execution_status(v12)
    canonical_scope = _canonical_product_scope(v12)
    if canonical_scope["status"] != "VERIFIED":
        v12["formal_count_projection"] = dict(
            canonical_scope["formal_count_projection"]
        )
    confirmed = list(canonical_scope["findings"])
    candidates = list(canonical_scope["candidates"])
    delivery_occurrences = list(canonical_scope["delivery_occurrences"])
    canonical_registry = dict(canonical_scope["canonical_defect_registry"])
    dedupe_input_count = int(
        canonical_registry.get("delivery_occurrence_count")
        if canonical_registry
        else len(delivery_occurrences)
    )
    dedupe_output_count = int(
        canonical_registry.get("canonical_defect_count")
        if canonical_registry
        else len(confirmed)
    )
    dedupe_report = {
        "schema_version": "qualibug.canonical-dedupe-report.v1",
        "authority": "canonical_defect_registry",
        "status": canonical_scope["status"],
        "input_count": dedupe_input_count,
        "output_count": dedupe_output_count,
        "unique_count": dedupe_output_count,
        "delivery_occurrence_count": dedupe_input_count,
        "collapsed_count": max(0, dedupe_input_count - dedupe_output_count),
        "title_or_path_dedupe_used": False,
    }
    external_findings = v12.get("external_findings") if isinstance(v12.get("external_findings"), list) else []
    external_findings = _bind_scan_rows_to_mainline(
        [dict(item) for item in external_findings if isinstance(item, dict)],
        v12,
    )
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
    ui_execution = _as_dict(v12.get("ui_execution"))
    p4_ui_evidence_bridge: dict[str, Any] = {"status": "not_requested"}
    try:
        evidence_bundle = _persist_execution_evidence(project, root, scan_id, campaign, runtime_contract, execution_status, v12)

        # ── Phase 108Q: Bridge browser execution HAR/screenshot to evidence bundle ──
        # Acceptance Criterion 10: end-to-end P4 UI evidence chain.
        if isinstance(ui_execution, dict) and ui_execution.get("har_ref"):
            try:
                from .har_bridge import bridge_browser_har_to_findings
                all_findings = confirmed + candidates
                har_enriched = bridge_browser_har_to_findings(
                    ui_execution, all_findings, root=root,
                )
                p4_ui_evidence_bridge = _as_dict(har_enriched.get("har_summary"))
            except Exception as exc:
                p4_ui_evidence_bridge = {
                    "status": "failed",
                    "reason": f"har_bridge_error:{type(exc).__name__}",
                    "error": str(exc)[:300],
                }

    except Exception as exc:
        print(f"[scan] Evidence persistence failed: {exc}", file=sys.stderr)
        failure = {
            "success": False,
            "scan_id": scan_id,
            "project": project,
            "execution_status": "FAILED_SAFE",
            "customer_output_status": "BLOCKED_EVIDENCE_PERSISTENCE",
            "failure_stage": "evidence_persistence",
            "error": "evidence_bundle_persistence_failed",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:500],
            "findings": [],
            "candidate_findings": [
                *candidates,
                *[
                    {
                        **dict(item),
                        "finding_class": "candidate",
                        "customer_delivery_status": "blocked",
                        "customer_delivery_gate_reasons": [
                            "EVIDENCE_BUNDLE_PERSISTENCE_FAILED"
                        ],
                    }
                    for item in confirmed
                ],
            ],
            "delivery_occurrence_count": len(delivery_occurrences),
            "canonical_defect_count_blocked": len(confirmed),
            "canonical_registry_fingerprint": str(
                canonical_registry.get("registry_fingerprint") or ""
            ),
            "pipeline_health": {
                "status": "FAILED_SAFE",
                "empty_findings_means_no_bugs": False,
                "stage_failures": ["EVIDENCE_BUNDLE_PERSISTENCE_FAILED"],
            },
        }
        failure_path = (
            root
            / "platform_outputs"
            / _safe_project(project)
            / "scan_result.json"
        )
        _write_json(failure_path, failure)
        return failure

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
        environment_kind=_first_text(
            runtime_contract.get("environment_kind"),
            runtime_contract.get("environment_type"),
            context.get("environment_kind"),
            context.get("target_environment"),
        ),
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
    # A scan whose execution status is "plan_only" (no runtime target supplied,
    # so nothing was attempted) cannot complete and grades as "blocked", not a
    # clean "inconclusive". Note we key on the derived *execution_status*, not
    # runtime_contract.status: a discovery that planned-but-skipped (e.g. a
    # stubbed pipeline returning phases.execution.status == "skipped") still
    # grades "inconclusive" unless execution was genuinely blocked/plan_only.
    _rc_status = str(runtime_contract.get("status") or "")
    grade = "blocked" if _rc_status == "blocked" or execution_status == "blocked" or execution_status == "plan_only" else ("inconclusive" if not confirmed else "evidence_ready")
    # ── 主链 4/5 覆盖诚实性守卫: never report a clean completion while high-value
    # (permission/isolation/money/concurrency) slices were silently unexecuted. ──
    coverage_honesty, grade = _apply_coverage_honesty_guard(v12, grade, execution_status)
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
    # Product runtime may expose only GT-free coverage. Hidden-ground-truth
    # scoring belongs to the evaluator process and must never run in scan().
    benchmark_metrics: dict[str, Any] = {}
    try:
        from .risk_coverage_projection import (
            compute_product_coverage_projection,
            persist_product_coverage_projection,
        )
        benchmark_metrics = compute_product_coverage_projection(
            confirmed,
            candidates=candidates,
        )
        if benchmark_metrics:
            persist_product_coverage_projection(
                project,
                benchmark_metrics,
                root=root,
            )
    except Exception as benchmark_error:
        # Coverage computation failures remain explicit, but never trigger a
        # fallback to evaluator-private scoring.
        benchmark_metrics = {
            "benchmark_active": False,
            "ground_truth_available": False,
            "status": "FAILED_SAFE",
            "reason": "benchmark_compute_failed",
            "error": str(benchmark_error)[:400],
        }
        print(f"  [WARN] Benchmark compute failed (non-fatal): {benchmark_error}", flush=True)
    ui_findings = v12.get("ui_findings") if isinstance(v12.get("ui_findings"), list) else []
    ui_candidate_findings = _ui_candidate_gate(ui_findings)
    ui_candidate_findings = _verify_ui_candidate_findings(ui_candidate_findings, root=root, runtime_contract=runtime_contract)
    ui_candidate_findings = _mark_high_confidence_ui_candidates(ui_candidate_findings)
    ui_candidate_findings = _bind_scan_rows_to_mainline(
        [dict(item) for item in ui_candidate_findings if isinstance(item, dict)],
        v12,
    )
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
    from .discovery_funnel import build_funnel, reconcile_product_pipeline_health

    discovery_funnel = build_funnel(v12)
    pipeline_health = reconcile_product_pipeline_health(
        _as_dict(discovery_funnel.get("pipeline_health")),
        execution_status=execution_status,
        preflight_diagnostics=diagnostics,
    )
    discovery_funnel["pipeline_health"] = pipeline_health
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
        "mainline_run": v12.get("mainline_run"),
        "obligation_attempt_ledger": v12.get("obligation_attempt_ledger"),
        "canonical_defect_registry": canonical_registry,
        "formal_delivery_authority": v12.get("formal_delivery_authority"),
        "formal_count_projection": canonical_scope["formal_count_projection"],
        "defect_identity_consistency": canonical_scope[
            "defect_identity_consistency"
        ],
        "delivery_occurrences": delivery_occurrences,
        "ui_followup_assets": ui_followup_assets,
        "p4_ui_evidence_bridge": p4_ui_evidence_bridge,
        "commercial_assets": commercial_assets,
        "external_reproduction_assets": external_reproduction_assets,
        "external_commercial_assets": external_commercial_assets,
        "preflight_diagnostics": diagnostics, "input_gaps": input_gaps, "coverage_gaps": coverage_gaps,
        "scan_preflight_guide": preflight_guide,
        "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "campaign": campaign, "test_data_bootstrap": test_data_bootstrap,
        "ui_test_data_bootstrap": ui_test_data_bootstrap,
        "behavior_slice_ledger": v12.get("behavior_slice_ledger", {}), "incremental_discovery": incremental,
        "execution_status": execution_status,
        "coverage_honesty": coverage_honesty,
        "db_verification": db_verification,
        "benchmark_metrics": benchmark_metrics,
        "dedupe_report": dedupe_report,
        "discovery_verdict": _discovery_verdict(confirmed, db_verification),
        "discovery_funnel": discovery_funnel,
        "pipeline_health": pipeline_health,
        "ci_gate": {"status": "not_evaluated" if ci_gate else "not_requested", "reason": "confirmed_receipts_and_approved_baseline_required" if ci_gate else ""},
        "auto_har": v12.get("auto_har", {}), "evidence_bundle": evidence_bundle, "release_gate": release_gate, "ui_execution": ui_execution, "ui_execution_summary": ui_execution_summary, "execution_evidence_summary": ui_execution_summary, "external_signal_execution": external_signal_execution, "v12": v12,
    }
    from .discovery_quality_projection import (
        attach_quality_projection_to_scan_result,
        suppress_benchmark_quality_when_not_measured,
    )

    result = attach_quality_projection_to_scan_result(result)
    result["benchmark_metrics"] = suppress_benchmark_quality_when_not_measured(
        _as_dict(result.get("benchmark_metrics")),
        _as_dict(result.get("external_evaluation")),
    )
    # Evidence-driven Harness evolution observability. This is derived only
    # from the completed real V12 run and persists redacted lineage/status
    # summaries; raw bodies, credentials, and benchmark ground truth are never
    # copied into the evolution artifacts.
    try:
        from .discovery_trace_ledger import build_discovery_trace_ledger, persist_trace_ledger
        from .discovery_weakness_miner import mine_discovery_weaknesses, persist_weakness_report
        from .discovery_harness_proposer import propose_harness_candidates, persist_harness_proposals
        from .enterprise_project_config import MultiServiceProject
        from .policy_registry import get_policy_registry

        _active_policy = get_policy_registry().get_active()
        _policy_id = str(getattr(_active_policy, "policy_id", "") or getattr(_active_policy, "policy_version", "") or "unversioned-policy")
        _industry = str(context.get("industry") or "").strip()
        if not _industry:
            _industry = str(MultiServiceProject(project, root).project_metadata().get("industry") or "").strip()
        if not _industry:
            _industry = "unclassified"
        _target_id = str(
            context.get("target_id")
            or context.get("scope_id")
            or context.get("environment_ref")
            or project
        ).strip()
        _evaluation_mode = str(context.get("evaluation_mode") or "operational").strip()
        _trace_ledger = build_discovery_trace_ledger(
            v12,
            run_id=str(context["run_id"]),
            policy_id=_policy_id,
            target_id=_target_id,
            project_id=project,
            industry=_industry,
            evaluation_mode=_evaluation_mode,
        )
        result["trace_ledger"] = _trace_ledger
        v12["trace_ledger"] = _trace_ledger
        _weakness_report = mine_discovery_weaknesses([_trace_ledger])
        if _active_policy is None:
            raise RuntimeError("active policy is required for bounded Harness proposal generation")
        _proposal_report = propose_harness_candidates(
            _weakness_report,
            _active_policy.strategy,
        )
        _evolution_root = root / "platform_outputs" / _safe_project(project) / "discovery_evolution"
        _trace_path = persist_trace_ledger(_trace_ledger, _evolution_root / "trace_ledgers")
        _weakness_path = persist_weakness_report(_weakness_report, _evolution_root / "weakness_reports")
        _proposal_path = persist_harness_proposals(_proposal_report, _evolution_root / "harness_proposals")
        result["discovery_evolution"] = {
            "status": "observed",
            "policy_id": _policy_id,
            "industry": _industry,
            "evaluation_mode": _evaluation_mode,
            "trace_count": int(_trace_ledger.get("trace_count") or 0),
            "outcome_counts": dict(_trace_ledger.get("outcome_counts") or {}),
            "failure_signature_counts": dict(_trace_ledger.get("failure_signature_counts") or {}),
            "weakness_pattern_count": int(_weakness_report.get("pattern_count") or 0),
            "proposal_eligible_pattern_count": int(_weakness_report.get("proposal_eligible_pattern_count") or 0),
            "selected_patterns_for_proposal": list(_weakness_report.get("selected_patterns_for_proposal") or []),
            "harness_proposal_count": int(_proposal_report.get("proposal_count") or 0),
            "blocked_proposal_pattern_count": int(_proposal_report.get("blocked_pattern_count") or 0),
            "trace_ledger_ref": str(_trace_path.relative_to(root)).replace("\\", "/"),
            "weakness_report_ref": str(_weakness_path.relative_to(root)).replace("\\", "/"),
            "harness_proposals_ref": str(_proposal_path.relative_to(root)).replace("\\", "/"),
        }
    except Exception as evolution_error:
        # This feature is not allowed to disappear silently. An empty plan is
        # explicitly BLOCKED; a trace/mining failure is FAILED_SAFE. Neither may
        # claim readiness or promote a policy from this run.
        _attempt_ledger = _as_dict(v12.get("obligation_attempt_ledger"))
        _no_selected_obligations = int(
            _attempt_ledger.get("selected_count") or 0
        ) == 0
        result["discovery_evolution"] = {
            "status": "BLOCKED" if _no_selected_obligations else "FAILED_SAFE",
            "error_type": type(evolution_error).__name__,
            "error": str(evolution_error)[:500],
            "reason": (
                "NO_OBLIGATIONS_SELECTED"
                if _no_selected_obligations
                else "DISCOVERY_EVOLUTION_OBSERVABILITY_FAILED"
            ),
            "promotion_allowed": False,
        }
        coverage_gaps.append(
            _gap(
                (
                    "NO_OBLIGATIONS_SELECTED"
                    if _no_selected_obligations
                    else "DISCOVERY_EVOLUTION_OBSERVABILITY_FAILED"
                ),
                (
                    "No source-grounded obligations were selected; planning and policy promotion are blocked."
                    if _no_selected_obligations
                    else f"Trace ledger or weakness mining failed ({type(evolution_error).__name__}); policy promotion is blocked."
                ),
            )
        )
        import sys as _evolution_sys
        print(
            (
                "[scan] Discovery evolution blocked: no obligations selected"
                if _no_selected_obligations
                else f"[scan] Discovery evolution observability failed: {evolution_error}"
            ),
            file=_evolution_sys.stderr,
        )
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {
            "project": project,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "real_findings": confirmed,
            "findings": confirmed,
            "candidate_findings": candidates,
            "risk_clues": candidates,
            "mainline_run": v12.get("mainline_run"),
            "obligation_attempt_ledger": v12.get("obligation_attempt_ledger"),
            "canonical_defect_registry": canonical_registry,
            "formal_delivery_authority": v12.get("formal_delivery_authority"),
            "formal_count_projection": result.get("formal_count_projection"),
            "run_delivery_readiness": result.get("run_delivery_readiness"),
            "commercial_readiness": result.get("commercial_readiness"),
            "external_evaluation": result.get("external_evaluation"),
            "defect_identity_consistency": result.get("defect_identity_consistency"),
            "delivery_occurrences": delivery_occurrences,
            "campaign": campaign,
            "coverage_gaps": coverage_gaps,
            "scan_preflight_guide": preflight_guide,
            "runtime_contract": runtime_contract,
            "test_data_plan": test_data_plan,
            "test_data_bootstrap": test_data_bootstrap,
            "behavior_slice_ledger": result["behavior_slice_ledger"],
            "execution_status": execution_status,
            "coverage_honesty": coverage_honesty,
            "evidence_bundle": evidence_bundle,
            "release_gate": result.get("release_gate"),
            "ui_execution_summary": ui_execution_summary,
            "execution_evidence_summary": ui_execution_summary,
            "ui_followup_assets": ui_followup_assets,
            "external_reproduction_assets": external_reproduction_assets,
            "external_commercial_assets": external_commercial_assets,
        })
        result["report_path"] = str(report_path)
    output_root = root / "platform_outputs" / _safe_project(project)
    _write_json(output_root / "scan_result.json", result)
    increment_scan_counter(output_root / "scan_counter.json")
    _persist_customer_ready_static_artifacts(project, root, result)

    # ── Phase 108R: Auto-generate Issue Lifecycle Center after scan ──
    # Acceptance Criterion 12: lifecycle center aggregates discovery + regression
    # states and auto-migrates bug status based on evidence.
    try:
        from .issue_lifecycle_center import build_issue_lifecycle_center
        lifecycle = build_issue_lifecycle_center(project, root, options={"auto_generate_missing": False})
        result["lifecycle_center"] = {
            "ref": f"platform_outputs/{_safe_project(project)}/issue_lifecycle/issue_lifecycle.json",
            "summary": lifecycle.get("summary", {}),
            "active_issue_count": lifecycle.get("summary", {}).get("active_issue_count", 0),
        }
    except Exception:
        pass

    # ── Closed-Loop Learning: extract patterns + generate probes for next scan ──
    try:
        from .closed_loop_feedback import build_closed_loop_context

        if confirmed:
            feedback = build_closed_loop_context(project, root, confirmed)
            result["closed_loop"] = {
                "patterns": feedback.get("total_patterns", 0),
                "new_patterns": feedback.get("new_this_scan", 0),
                "generated_probes": len(feedback.get("generated_probes", [])),
            }

            # Persist generated probes so the next scan can consume them
            probes = feedback.get("generated_probes", [])
            if probes:
                probe_pool_path = output_root / "learned_probes.json"
                _write_json(probe_pool_path, {
                    "schema": "learned_probes.v1",
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": "closed_loop_feedback",
                    "probe_count": len(probes),
                    "probes": probes,
                })
                result["closed_loop"]["probe_pool_path"] = str(probe_pool_path)
    except Exception as e:
        print(f"[scan] Closed-loop learning failed: {e}", file=sys.stderr)
        failure_code = f"CLOSED_LOOP_LEARNING_FAILED:{type(e).__name__}:{str(e)[:200]}"
        result.setdefault("stage_failures", []).append(failure_code)
        result["closed_loop"] = {
            "status": "failed_safe",
            "stage": "closed_loop_learning",
            "code": "CLOSED_LOOP_LEARNING_FAILED",
            "identity": {"project_id": project, "scan_id": result.get("scan_id")},
            "retryability": "after_operator_action",
            "operator_action": "Inspect closed-loop history and generated probe inputs, then rerun the campaign.",
            "error": f"{type(e).__name__}:{str(e)[:300]}",
        }
        pipeline_health = _as_dict(result.get("pipeline_health"))
        if str(pipeline_health.get("status") or "").upper() not in {"FAILED_SAFE"}:
            pipeline_health["status"] = "DEGRADED"
        pipeline_health["empty_findings_means_no_bugs"] = False
        pipeline_health.setdefault("stage_failures", []).append(failure_code)
        pipeline_health["operator_note"] = (
            "闭环学习阶段失败；已执行缺陷收据仍保留，但学习产物不可用，需修复后重跑。"
        )
        result["pipeline_health"] = pipeline_health
        funnel = _as_dict(result.get("discovery_funnel"))
        if funnel:
            funnel["pipeline_health"] = dict(pipeline_health)
            result["discovery_funnel"] = funnel

    return result



def scan(project: str, root: Optional[Path] = None, *, prd_text: str = "", api_doc_path: str = "", api_doc_text: str = "", base_url: str = "", ci_gate: bool = False, multi_layer: bool = True, output_dir: Optional[Path] = None, save_report: bool = True, campaign_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Public scan entry — runs core discovery then first-class post-hooks."""
    from .scan_post_hooks import apply_scan_post_hooks

    result = _scan_impl(
        project,
        root,
        prd_text=prd_text,
        api_doc_path=api_doc_path,
        api_doc_text=api_doc_text,
        base_url=base_url,
        ci_gate=ci_gate,
        multi_layer=multi_layer,
        output_dir=output_dir,
        save_report=save_report,
        campaign_context=campaign_context,
    )
    resolved_root = Path(root or Path.cwd())
    return apply_scan_post_hooks(result, project=str(project or "").strip(), root=resolved_root)


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
    parser.add_argument("--environment-type", default="")
    parser.add_argument("--source-id", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--source-version-id", default="")
    parser.add_argument("--execution-approval-id", default="")
    parser.add_argument("--execution-mode", default="")
    parser.add_argument("--test-data-strategy", default="")
    parser.add_argument("--ci-gate", action="store_true")
    parser.add_argument("--no-multi-layer", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    context = _build_cli_campaign_context(args)
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


def _build_cli_campaign_context(args: Any) -> dict[str, Any]:
    from .private_pilot_scan_context_contract import (
        default_scan_execution_mode,
        default_scan_test_data_contract,
    )

    body = {
        "base_url": getattr(args, "base_url", ""),
        "scope_id": getattr(args, "scope_id", ""),
        "environment_ref": getattr(args, "environment_ref", ""),
        "environment_type": getattr(args, "environment_type", ""),
        "execution_mode": getattr(args, "execution_mode", ""),
    }
    execution_mode = (
        str(getattr(args, "execution_mode", "") or "").strip()
        or default_scan_execution_mode(body)
    )
    body["execution_mode"] = execution_mode
    test_data_contract: dict[str, Any] = {}
    strategy = str(getattr(args, "test_data_strategy", "") or "").strip()
    if strategy:
        test_data_contract["strategy"] = strategy
        if strategy in {"create_disposable", "approved_fixture_setup"} and execution_mode == "approved_sandbox_write":
            test_data_contract["write_approved"] = True
            if strategy == "create_disposable":
                scope_ref = str(
                    getattr(args, "scope_id", "")
                    or getattr(args, "environment_ref", "")
                    or ""
                ).strip()
                if scope_ref:
                    test_data_contract["disposable_scope_ref"] = scope_ref
    else:
        test_data_contract = default_scan_test_data_contract(body)
    context = {
        "scope_id": getattr(args, "scope_id", ""),
        "environment_ref": getattr(args, "environment_ref", ""),
        "environment_type": getattr(args, "environment_type", ""),
        "source_manifest": {
            "source_id": getattr(args, "source_id", ""),
            "source_hash": getattr(args, "source_hash", ""),
            "source_version_id": getattr(args, "source_version_id", ""),
        },
        "execution_approval_id": getattr(args, "execution_approval_id", ""),
        "execution_mode": execution_mode,
    }
    if test_data_contract:
        context["test_data_contract"] = test_data_contract
    return context


if __name__ == "__main__":
    main()
