from __future__ import annotations

"""Coverage report for full-spectrum defect families."""

from typing import Any

from .defect_family_registry import iter_defect_families, resolve_defect_family


def build_bug_family_coverage_report(
    probes: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    capability_rows: list[dict[str, Any]] | None = None,
    health_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    capability_rows = capability_rows or []
    health_context = health_context or {}
    family_rows: list[dict[str, Any]] = []
    probe_items = [item for item in probes if isinstance(item, dict)]
    issue_items = [item for item in issues if isinstance(item, dict)]
    capability_map: dict[str, list[dict[str, Any]]] = {}
    for row in capability_rows:
        if not isinstance(row, dict):
            continue
        family_id = str(row.get("defect_family") or row.get("family_id") or "")
        if not family_id:
            continue
        capability_map.setdefault(family_id, []).append(row)
    browser_ui_health = health_context.get("browser_ui_health") if isinstance(health_context.get("browser_ui_health"), dict) else {}
    browser_ui_reason_code = str(browser_ui_health.get("reason_code") or "")
    browser_ui_blocked = browser_ui_reason_code not in {"", "OK"}
    browser_ui_affected_families = {"ui", "uiux", "compatibility"}
    missing_family_reasons: dict[str, dict[str, Any]] = {}
    for family in iter_defect_families():
        family_id = str(family.get("family_id") or "")
        family_probes = [item for item in probe_items if str(item.get("defect_family") or resolve_defect_family(item).get("family_id") or "") == family_id]
        family_issues = [item for item in issue_items if str(item.get("defect_family") or resolve_defect_family(item).get("family_id") or "") == family_id]
        family_cap_rows = capability_map.get(family_id, [])
        probe_level_cap_rows = [row for row in family_cap_rows if isinstance(row, dict) and not row.get("capability_id")]
        capability_level_rows = [row for row in family_cap_rows if isinstance(row, dict) and row.get("capability_id")]
        executable_probe_count = sum(1 for row in probe_level_cap_rows if str(row.get("preflight_lane") or "").endswith("ready"))
        ready_capability_count = sum(1 for row in capability_level_rows if str(row.get("preflight_lane") or "").endswith("ready"))
        blocked_capability_count = sum(1 for row in capability_level_rows if "blocked" in str(row.get("preflight_lane") or "") or str(row.get("preflight_lane") or "") == "plan_only")
        blocked_probe_count = sum(1 for item in family_probes if str(item.get("capability_gate") or "") == "browser_ui_unavailable")
        candidate_only_count = sum(1 for item in family_issues if float(item.get("confidence") or 0.0) < 0.75)
        validated_count = sum(1 for item in family_issues if float(item.get("confidence") or 0.0) >= 0.75)
        coverage_gap_reason_code = ""
        coverage_gap_reason = ""
        coverage_gap_action = ""
        if browser_ui_blocked and family_id in browser_ui_affected_families:
            coverage_gap_reason_code = browser_ui_reason_code
            coverage_gap_reason = str(browser_ui_health.get("reason") or "")
            coverage_gap_action = str(browser_ui_health.get("action") or "")
            if validated_count <= 0 and blocked_probe_count > 0:
                missing_family_reasons[family_id] = {
                    "reason_code": coverage_gap_reason_code,
                    "reason": coverage_gap_reason,
                    "action": coverage_gap_action,
                }
        family_rows.append(
            {
                "family_id": family_id,
                "display_name": family.get("display_name"),
                "reporting_bucket": family.get("reporting_bucket"),
                "probe_count": len(family_probes),
                "issue_count": len(family_issues),
                "executable_probe_count": executable_probe_count,
                "blocked_probe_count": blocked_probe_count,
                "ready_capability_count": ready_capability_count,
                "blocked_capability_count": blocked_capability_count,
                "candidate_only_count": candidate_only_count,
                "validated_count": validated_count,
                "coverage_status": (
                    "validated"
                    if validated_count
                    else "candidate_only"
                    if family_probes or family_issues
                    else "not_covered"
                ),
                "coverage_gap_reason_code": coverage_gap_reason_code,
                "coverage_gap_reason": coverage_gap_reason,
                "coverage_gap_action": coverage_gap_action,
                "required_evidence": list(family.get("required_evidence") or []),
                "probe_sources": list(family.get("probe_sources") or []),
            }
        )
    missing_probe_families = [row["family_id"] for row in family_rows if int(row.get("probe_count") or 0) <= 0]
    missing_validated_families = [row["family_id"] for row in family_rows if int(row.get("validated_count") or 0) <= 0]
    return {
        "family_count": len(family_rows),
        "covered_family_count": sum(1 for row in family_rows if row["coverage_status"] != "not_covered"),
        "validated_family_count": sum(1 for row in family_rows if row["coverage_status"] == "validated"),
        "candidate_only_family_count": sum(1 for row in family_rows if row["coverage_status"] == "candidate_only"),
        "missing_probe_families": missing_probe_families,
        "missing_validated_families": missing_validated_families,
        "missing_family_reasons": missing_family_reasons,
        "rows": family_rows,
    }
