"""Scan-report aggregate synchronization helpers for private-pilot views."""
from __future__ import annotations

from typing import Any


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
