from __future__ import annotations

"""Phase92V: customer delivery index for runtime-validated findings."""

from typing import Any


PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _sort_key(finding: dict[str, Any]) -> tuple[int, int, float, str]:
    priority = PRIORITY_ORDER.get(str(finding.get("priority") or "P3"), 9)
    severity = SEVERITY_ORDER.get(str(finding.get("severity") or "low"), 9)
    score = finding.get("evidence_strength_score")
    numeric_score = float(score) if isinstance(score, (int, float)) else 0.0
    return priority, severity, -numeric_score, str(finding.get("finding_id") or "")


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def _risk_family(finding: dict[str, Any]) -> str:
    triage = finding.get("customer_triage") if isinstance(finding.get("customer_triage"), dict) else {}
    return str(triage.get("risk_family") or "unknown")


def _top_invariant_kinds(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        for invariant in finding.get("violated_invariants") or []:
            if isinstance(invariant, dict) and invariant.get("kind"):
                key = str(invariant.get("kind"))
                counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20])


def _evidence_coverage(findings: list[dict[str, Any]]) -> dict[str, Any]:
    with_before_after = 0
    with_semantic_graph = 0
    with_cross_observer_failure = 0
    observer_kinds: set[str] = set()
    for finding in findings:
        package = finding.get("evidence_package") if isinstance(finding.get("evidence_package"), dict) else {}
        chain = package.get("evidence_chain") if isinstance(package.get("evidence_chain"), dict) else {}
        before = chain.get("before_snapshot_count") or 0
        after = chain.get("after_snapshot_count") or 0
        if isinstance(before, int) and isinstance(after, int) and before > 0 and after > 0:
            with_before_after += 1
        delta = package.get("delta_summary") if isinstance(package.get("delta_summary"), dict) else {}
        semantic = delta.get("semantic_graph") if isinstance(delta.get("semantic_graph"), dict) else {}
        if semantic.get("present"):
            with_semantic_graph += 1
        if delta.get("cross_observer_failures"):
            with_cross_observer_failure += 1
        for kind in chain.get("observer_kinds") or []:
            observer_kinds.add(str(kind))
    return {
        "before_after_snapshot_finding_count": with_before_after,
        "semantic_graph_finding_count": with_semantic_graph,
        "cross_observer_failure_finding_count": with_cross_observer_failure,
        "observer_kinds": sorted(observer_kinds),
    }


def build_customer_delivery_index(findings: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted([f for f in findings if isinstance(f, dict)], key=_sort_key)
    risk_families: dict[str, int] = {}
    for finding in ordered:
        family = _risk_family(finding)
        risk_families[family] = risk_families.get(family, 0) + 1
    top_items = []
    for finding in ordered[:25]:
        top_items.append({
            "finding_id": finding.get("finding_id"),
            "priority": finding.get("priority"),
            "severity": finding.get("severity"),
            "risk_type": finding.get("risk_type"),
            "endpoint": f"{finding.get('method')} {finding.get('path')}",
            "evidence_grade": finding.get("evidence_grade"),
            "evidence_strength_score": finding.get("evidence_strength_score"),
            "customer_impact_summary": finding.get("customer_impact_summary"),
            "violated_invariant_kinds": [str(x.get("kind")) for x in (finding.get("violated_invariants") or []) if isinstance(x, dict) and x.get("kind")][:8],
        })
    return {
        "engine": "runtime_customer_report_builder_v1_phase92v",
        "customer_delivery_ready": bool(ordered),
        "validated_finding_count": len(ordered),
        "by_priority": _count_by(ordered, "priority"),
        "by_severity": _count_by(ordered, "severity"),
        "by_risk_family": dict(sorted(risk_families.items())),
        "top_violated_invariant_kinds": _top_invariant_kinds(ordered),
        "evidence_coverage": _evidence_coverage(ordered),
        "top_customer_actions": top_items,
        "recommended_report_usage": "Start with P0/P1 findings that have strong evidence and before/after snapshots; use generated repro assets to create regression tests before fixing.",
    }
