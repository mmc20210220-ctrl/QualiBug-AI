"""Command-center envelope normalization with first-class post-hooks.

Runtime patches register post-hooks instead of replacing the service symbol.
``private_pilot_service._normalize_command_center_envelope`` remains a thin
dispatcher for call sites and compatibility tests.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .command_center_delivery_contract import normalize_command_center_delivery
from .customer_delivery_gate import split_customer_delivery_tracks as _partition_delivery_tracks
from .private_pilot_command_center_helpers import (
    _annotate_ui_risk_item,
    _build_internal_clue_contract,
    _defect_intake_stats,
    _first_text,
    _normalize_execution_evidence_summary,
    _rebuild_customer_display_contract,
    _select_commercial_assets,
    _ui_verification_stats,
)


def _current_campaign_scope_summary(payload: dict[str, Any]) -> dict[str, str]:
    from . import private_pilot_service as _svc

    return _svc._current_campaign_scope_summary(payload)


EnvelopePostHook = Callable[[dict[str, Any]], dict[str, Any]]
_ENVELOPE_POST_HOOKS: list[tuple[str, EnvelopePostHook]] = []


def register_envelope_post_hook(name: str, hook: EnvelopePostHook | None) -> None:
    """Register or replace a named post-normalize hook. Pass None to clear one name."""
    global _ENVELOPE_POST_HOOKS
    remaining = [(n, h) for n, h in _ENVELOPE_POST_HOOKS if n != name]
    if hook is not None:
        remaining.append((name, hook))
    _ENVELOPE_POST_HOOKS = remaining


def clear_envelope_post_hooks() -> None:
    global _ENVELOPE_POST_HOOKS
    _ENVELOPE_POST_HOOKS = []


def list_envelope_post_hooks() -> list[str]:
    return [name for name, _hook in _ENVELOPE_POST_HOOKS]


def _normalize_command_center_envelope_base(payload: dict[str, Any]) -> dict[str, Any]:
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


def normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_command_center_envelope_base(payload)
    for _name, hook in list(_ENVELOPE_POST_HOOKS):
        normalized = hook(normalized)
    return normalized
