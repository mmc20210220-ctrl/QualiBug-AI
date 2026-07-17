"""Command-center risk annotation and asset normalization helpers.

Extracted from ``private_pilot_service`` so the HTTP handler module stays thinner.
Symbols remain importable from ``private_pilot_service`` for compatibility.
The envelope normalizer stays in ``private_pilot_service`` because runtime patches
still wrap that module-level symbol.
"""
from __future__ import annotations

from typing import Any


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _collect_track_counts(items: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    status_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("bug_status") or "risk_clue")
        severity = str(item.get("severity") or "P2")
        status_counts[status] = status_counts.get(status, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
    return status_counts, severity_counts


def _rebuild_customer_display_contract(
    display_contract: dict[str, Any],
    display_risks: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts, severity_counts = _collect_track_counts(display_risks)

    rebuilt = dict(display_contract)
    rebuilt["schema_version"] = str(display_contract.get("schema_version") or "display-ready-v2026-07-05")
    rebuilt["materialized_risk_count"] = len(display_risks)
    rebuilt["ready_bug_count"] = len(display_risks)
    rebuilt["needs_validation_count"] = 0
    rebuilt["not_reproduced_count"] = 0
    rebuilt["status_counts"] = status_counts
    rebuilt["severity_counts"] = severity_counts
    rebuilt["customer_delivery_mode"] = (
        "严格客户口径：仅返回 reproduced + gate_passed 且具备真实原始证据的缺陷；"
        "疑似、线索、未复现与自动推断账本均不进入客户缺陷交付。"
    )
    return rebuilt


def _build_internal_clue_contract(
    display_contract: dict[str, Any],
    display_risks: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts, severity_counts = _collect_track_counts(display_risks)
    return {
        "schema_version": str(display_contract.get("schema_version") or "display-ready-v2026-07-05"),
        "source_of_truth": "backend.private_pilot_service._build_command_center",
        "display_key": "clues",
        "materialized_risk_count": len(display_risks),
        "raw_candidate_risk_count": len(display_risks),
        "ready_bug_count": 0,
        "needs_validation_count": status_counts.get("suspected", 0) + status_counts.get("risk_clue", 0),
        "not_reproduced_count": status_counts.get("not_reproduced", 0),
        "status_counts": status_counts,
        "severity_counts": severity_counts,
        "customer_delivery_mode": "内部线索池：仅供采证、复验和回归规划，不进入客户缺陷交付。",
        "internal_only": True,
    }


def _ui_verification_stats(items: Any) -> dict[str, int]:
    stats = {
        "ui_total": 0,
        "ui_candidate_total": 0,
        "ui_verified_candidate_total": 0,
        "ui_unverified_candidate_total": 0,
        "ui_high_confidence_candidate_total": 0,
    }
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if not _is_ui_risk_item(item):
            continue
        stats["ui_total"] += 1
        gate = item.get("ui_candidate_gate") if isinstance(item.get("ui_candidate_gate"), dict) else {}
        verification = item.get("ui_verification") if isinstance(item.get("ui_verification"), dict) else {}
        if gate.get("passed") is True:
            stats["ui_candidate_total"] += 1
            if verification.get("status") == "verified":
                stats["ui_verified_candidate_total"] += 1
            else:
                stats["ui_unverified_candidate_total"] += 1
        if item.get("high_confidence_candidate") is True:
            stats["ui_high_confidence_candidate_total"] += 1
    return stats


def _is_ui_risk_item(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if isinstance(item.get("ui_verification"), dict) or isinstance(item.get("ui_candidate_gate"), dict):
        return True
    if item.get("high_confidence_candidate") is True:
        return True
    badge = str(item.get("verification_badge") or "").strip().lower()
    if badge in {"ui_verified", "ui_candidate", "ui_signal"}:
        return True
    candidate_tier = str(item.get("candidate_tier") or "").strip().lower()
    if candidate_tier in {"ui_candidate", "high_confidence_ui_candidate"}:
        return True
    defect_family = str(item.get("defect_family") or "").strip().lower()
    risk_type = str(item.get("risk_type") or "").strip().lower()
    return defect_family == "ui" or risk_type in {"frontend_ui", "ui_execution", "frontend_execution_runtime"}


def _defect_intake_stats(items: Any) -> dict[str, int]:
    stats = {
        "defect_intake_recommended_total": 0,
        "customer_defect_intake_total": 0,
        "internal_defect_intake_total": 0,
    }
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("defect_intake_recommended") is True:
            stats["defect_intake_recommended_total"] += 1
            route = str(item.get("defect_intake_route") or "")
            if route == "customer_defect":
                stats["customer_defect_intake_total"] += 1
            elif route == "internal_defect_intake":
                stats["internal_defect_intake_total"] += 1
    return stats


def _command_center_priority_score(item: dict[str, Any]) -> float:
    severity = str(item.get("severity") or "P2").upper()
    severity_score = {"P0": 100.0, "P1": 80.0, "P2": 50.0, "P3": 25.0}.get(severity, 40.0)
    try:
        confidence = max(0.0, min(1.0, float(item.get("confidence_score") or item.get("confidence") or 0.0)))
    except Exception:
        confidence = 0.0
    verification = item.get("ui_verification") if isinstance(item.get("ui_verification"), dict) else {}
    evidence_quality = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
    verification_status = str(verification.get("status") or "").strip().lower()
    verification_bonus = 18.0 if verification_status == "verified" else 10.0 if item.get("verification_badge") == "ui_candidate" else 0.0
    high_conf_bonus = 25.0 if item.get("high_confidence_candidate") is True else 0.0
    cross_verified_bonus = 12.0 if str(evidence_quality.get("level") or "").strip().lower() in {"cross_verified", "validated"} else 0.0
    reproduced_bonus = 10.0 if str(item.get("bug_status") or "").strip().lower() == "reproduced" else 0.0
    gate_bonus = 8.0 if bool(item.get("gate_passed")) else 0.0
    return round(severity_score + confidence * 20.0 + verification_bonus + high_conf_bonus + cross_verified_bonus + reproduced_bonus + gate_bonus, 3)


def _command_center_priority_label(item: dict[str, Any], score: float) -> str:
    if bool(item.get("launch_blocking")) or str(item.get("severity") or "").upper() == "P0":
        return "P0"
    if item.get("high_confidence_candidate") is True or score >= 105:
        return "P1"
    if score >= 75:
        return "P2"
    return "P3"


def _defect_intake_fields(item: dict[str, Any], score: float) -> dict[str, Any]:
    severity = str(item.get("severity") or "").upper()
    reproduced = str(item.get("bug_status") or "").strip().lower() == "reproduced"
    high_conf = item.get("high_confidence_candidate") is True
    gate_passed = bool(item.get("gate_passed"))
    recommended = False
    route = "collect_more_evidence"
    reason = "证据尚未达到正式入账门槛，先留在线索池继续采证。"
    if reproduced and gate_passed:
        recommended = True
        route = "customer_defect"
        reason = "已复现且通过客户交付门禁，建议直接进入正式缺陷入账。"
    elif high_conf:
        recommended = True
        route = "internal_defect_intake"
        reason = "UI 候选已完成二次验真且证据质量较高，建议优先进入内部缺陷入账或复核队列。"
    return {
        "defect_intake_recommended": recommended,
        "defect_intake_route": route,
        "defect_intake_priority": "P0" if severity == "P0" else "P1" if recommended else "P2" if score >= 75 else "P3",
        "defect_intake_reason": reason,
    }


def _annotate_ui_risk_item(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item or {})
    is_ui = _is_ui_risk_item(row)
    verification = row.get("ui_verification") if isinstance(row.get("ui_verification"), dict) else {}
    gate = row.get("ui_candidate_gate") if isinstance(row.get("ui_candidate_gate"), dict) else {}
    status = str(verification.get("status") or "not_requested")
    if is_ui and status == "verified":
        row.setdefault("customer_evidence_label", "UI 二次验真通过")
        row.setdefault("verification_label", "已二次验真")
        row.setdefault("verification_badge", "ui_verified")
    elif is_ui and gate.get("passed") is True:
        row.setdefault("customer_evidence_label", "UI 候选待二次验真")
        row.setdefault("verification_label", "待二次验真")
        row.setdefault("verification_badge", "ui_candidate")
    elif is_ui:
        row.setdefault("customer_evidence_label", "UI 观测信号")
        row.setdefault("verification_label", "仅 UI 信号")
        row.setdefault("verification_badge", "ui_signal")
    priority_score = _command_center_priority_score(row)
    row["priority_score"] = priority_score
    row["priority_label"] = _command_center_priority_label(row, priority_score)
    row.update(_defect_intake_fields(row, priority_score))
    return row


def _normalize_commercial_assets(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    if not row:
        return {}
    commercial_handoff = row.get("commercial_handoff") if isinstance(row.get("commercial_handoff"), dict) else {}
    tracker_sync = row.get("tracker_sync") if isinstance(row.get("tracker_sync"), dict) else {}
    delivery = row.get("delivery_package") if isinstance(row.get("delivery_package"), dict) else {}
    artifact_refs = {
        "commercial_handoff_bundle_ref": _first_text(row.get("commercial_handoff_bundle_ref")),
        "commercial_handoff_acceptance_gate_ref": _first_text(row.get("commercial_handoff_acceptance_gate_ref")),
        "handoff_archive_manifest_ref": _first_text(row.get("handoff_archive_manifest_ref")),
        "commercial_audit_exports_ref": _first_text(row.get("commercial_audit_exports_ref")),
        "external_tracker_sync_payloads_ref": _first_text(row.get("external_tracker_sync_payloads_ref")),
        "delivery_package_ref": _first_text(delivery.get("package_ref")),
    }
    return {
        "status": _first_text(row.get("status"), "empty"),
        "finding_count": int(row.get("finding_count") or 0),
        "customer_ready_reproduction_count": int(row.get("customer_ready_reproduction_count") or 0),
        "commercial_handoff": {
            "status": _first_text(row.get("commercial_handoff_status"), commercial_handoff.get("status")),
            "acceptance_status": _first_text(row.get("commercial_handoff_acceptance_status"), commercial_handoff.get("acceptance_status")),
            "safe_for_customer": bool(
                row.get("commercial_handoff_safe_for_customer")
                if row.get("commercial_handoff_safe_for_customer") is not None
                else commercial_handoff.get("safe_for_customer")
            ),
        },
        "tracker_sync": {
            "payload_status": _first_text(row.get("external_tracker_sync_payload_status"), tracker_sync.get("payload_status")),
            "payload_gate_status": _first_text(row.get("external_tracker_sync_payload_gate_status"), tracker_sync.get("payload_gate_status")),
        },
        "delivery_package": {
            "status": _first_text(delivery.get("status"), "not_created"),
            "package_id": _first_text(delivery.get("package_id")),
            "package_ref": _first_text(delivery.get("package_ref")),
            "release_verdict": _first_text(delivery.get("release_verdict")),
            "evidence_bundle_id": _first_text(delivery.get("evidence_bundle_id")),
        },
        "artifact_refs": {key: value for key, value in artifact_refs.items() if value},
    }


def _commercial_assets_signal(value: Any) -> int:
    row = value if isinstance(value, dict) else {}
    if not row:
        return 0
    normalized = _normalize_commercial_assets(row)
    if not normalized:
        return 0
    score = 1
    if _first_text(normalized.get("status")) == "materialized":
        score += 100
    score += min(int(normalized.get("finding_count") or 0), 50)
    score += min(int(normalized.get("customer_ready_reproduction_count") or 0), 20)
    if _first_text((normalized.get("commercial_handoff") or {}).get("status")):
        score += 10
    if _first_text((normalized.get("tracker_sync") or {}).get("payload_status")):
        score += 10
    if _first_text((normalized.get("delivery_package") or {}).get("status")) == "created":
        score += 20
    score += len((normalized.get("artifact_refs") or {}))
    return score


def _select_commercial_assets(*values: Any) -> dict[str, Any]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for value in values:
        normalized = _normalize_commercial_assets(value)
        if normalized:
            candidates.append((_commercial_assets_signal(value), normalized))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _normalize_execution_evidence_summary(*values: Any) -> dict[str, Any]:
    candidate = next((value for value in values if isinstance(value, dict) and value), {})
    row = dict(candidate) if isinstance(candidate, dict) else {}
    if not row:
        return {}
    results = row.get("results") if isinstance(row.get("results"), list) else []
    artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), list) else []
    findings = row.get("findings") if isinstance(row.get("findings"), list) else []
    requested = int(row.get("requested") or row.get("total_items") or len(results) or 0)
    executed = int(row.get("executed") or 0)
    failed = int(row.get("failed") or 0)
    blocked = int(row.get("blocked") or 0)
    artifact_count = int(row.get("artifact_count") or len(artifacts))
    finding_count = int(row.get("finding_count") or len(findings))
    created_data_count = int(row.get("created_data_count") or 0)
    evidence_captured_count = int(row.get("evidence_captured_count") or 0)
    provider_distribution = (
        {str(key): int(value or 0) for key, value in row.get("provider_distribution", {}).items()}
        if isinstance(row.get("provider_distribution"), dict)
        else {}
    )
    bridge_provider_distribution = (
        {str(key): int(value or 0) for key, value in row.get("bridge_provider_distribution", {}).items()}
        if isinstance(row.get("bridge_provider_distribution"), dict)
        else {}
    )
    current_url_samples = [str(item).strip() for item in row.get("current_url_samples", []) if str(item).strip()][:3]
    artifact_refs = [str(item).strip() for item in row.get("artifact_refs", []) if str(item).strip()][:3]
    if not artifact_refs:
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("ref") or "").strip()
            if ref and ref not in artifact_refs:
                artifact_refs.append(ref)
            if len(artifact_refs) >= 3:
                break
    if not created_data_count or not evidence_captured_count or not bridge_provider_distribution or not current_url_samples:
        rebuilt_created_data_count = 0
        rebuilt_evidence_captured_count = 0
        rebuilt_bridge_provider_distribution = dict(bridge_provider_distribution)
        rebuilt_current_url_samples = list(current_url_samples)
        for result in results:
            if not isinstance(result, dict):
                continue
            if isinstance(result.get("created_data"), dict) and result.get("created_data"):
                rebuilt_created_data_count += 1
            provider = str(result.get("bridge_provider") or result.get("provider") or "").strip()
            if provider:
                rebuilt_bridge_provider_distribution[provider] = rebuilt_bridge_provider_distribution.get(provider, 0) + 1
            current_url = str(result.get("current_url") or "").strip()
            if current_url and current_url not in rebuilt_current_url_samples and len(rebuilt_current_url_samples) < 3:
                rebuilt_current_url_samples.append(current_url)
            has_evidence = bool(
                isinstance(result.get("artifacts"), list) and result.get("artifacts")
                or isinstance(result.get("findings"), list) and result.get("findings")
                or isinstance(result.get("created_data"), dict) and result.get("created_data")
                or isinstance(result.get("history"), list) and result.get("history")
                or isinstance(result.get("console"), list) and result.get("console")
                or isinstance(result.get("network"), list) and result.get("network")
            )
            if has_evidence:
                rebuilt_evidence_captured_count += 1
        if not created_data_count:
            created_data_count = rebuilt_created_data_count
        if not evidence_captured_count:
            evidence_captured_count = rebuilt_evidence_captured_count
        bridge_provider_distribution = rebuilt_bridge_provider_distribution
        current_url_samples = rebuilt_current_url_samples
    status = str(row.get("status") or ("not_requested" if requested <= 0 else "completed")).strip() or "not_requested"
    summary = str(row.get("summary") or "").strip()
    if not summary:
        summary = (
            "UI execution not requested."
            if requested <= 0
            else (
                f"UI execution {status}: requested {requested}, executed {executed}, failed {failed}, "
                f"blocked {blocked}, evidence captured {evidence_captured_count}, artifacts {artifact_count}, "
                f"findings {finding_count}"
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
        "finding_count": finding_count,
        "artifact_count": artifact_count,
        "created_data_count": created_data_count,
        "evidence_captured_count": evidence_captured_count,
        "provider_distribution": provider_distribution,
        "bridge_provider_distribution": bridge_provider_distribution,
        "artifact_refs": artifact_refs,
        "current_url_samples": current_url_samples,
        "summary": summary,
    }
