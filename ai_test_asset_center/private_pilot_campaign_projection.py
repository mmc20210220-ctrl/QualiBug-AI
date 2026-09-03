"""Campaign-scope projection helpers for command-center / continuous discovery."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .private_pilot_project_assets import _first_text
from .real_project_onboarding import _safe_project_id


def _report_finding_dedupe_key(item: dict[str, Any]) -> str:
    canonical_defect_id = str(item.get("canonical_defect_id") or "").strip()
    if canonical_defect_id:
        return f"canonical:{canonical_defect_id}"
    title = str(item.get("title") or item.get("description") or "")[:200].strip().lower()
    title = re.sub(r"^(\[[^\]]*\]\s*)+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
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
    summary["family_historical_carryover_defect_count"] = max(
        0, family_customer_ready_defect_count - max(0, int(current_campaign_customer_ready_defect_count or 0))
    )
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
            key = _report_finding_dedupe_key(finding)
            if key:
                deduped_keys.add(key)
    return {"raw": raw_count, "deduped": len(deduped_keys)}
