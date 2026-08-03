"""Executive validation summary aggregation.

This module aggregates existing behavior, evidence, traceability, regression,
and coverage reports into one assurance summary. It stays inside the product
boundary: discover, prove, report, and regression validate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .scan_post_hooks import register_scan_post_hook

HOOK_NAME = "validation_summary"

SUMMARY_SECTION_KEYS = (
    "behavior_registry",
    "evidence_packages",
    "regression_assets",
    "behavior_traceability",
    "behavior_coverage",
)


def attach_validation_summary(
    scan_result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Project an executive validation summary onto the scan result.

    Only report sections that actually exist in the result contribute; missing
    sections stay visibly absent instead of being invented.
    """
    if not isinstance(scan_result, dict):
        return scan_result
    reports = {
        key: scan_result[key]
        for key in SUMMARY_SECTION_KEYS
        if isinstance(scan_result.get(key), dict)
    }
    scan_result["validation_summary"] = build_validation_summary_report(reports)
    return scan_result


def install_validation_summary() -> None:
    register_scan_post_hook(HOOK_NAME, attach_validation_summary)


def _as_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    return int(_as_number(value))


def _counts(report: dict[str, Any], key: str) -> dict[str, int]:
    value = report.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(name): _as_int(count) for name, count in value.items()}


def _section(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _assurance_level(coverage_percent: float, traceability_percent: float, evidence_percent: float, failed_regressions: int) -> str:
    if failed_regressions:
        return "regression_attention"
    if coverage_percent >= 90 and traceability_percent >= 90 and evidence_percent >= 90:
        return "strong"
    if coverage_percent >= 70 and traceability_percent >= 70 and evidence_percent >= 70:
        return "developing"
    return "limited"


def _attention_items(
    coverage_report: dict[str, Any],
    traceability_report: dict[str, Any],
    evidence_report: dict[str, Any],
    regression_report: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    coverage_counts = _counts(coverage_report, "coverage_bucket_counts")
    if coverage_counts.get("uncovered", 0):
        items.append({"area": "coverage", "state": "uncovered_behaviors", "count": coverage_counts["uncovered"]})

    trace_counts = _counts(traceability_report, "status_counts")
    if trace_counts.get("partial", 0) or trace_counts.get("unlinked", 0):
        items.append(
            {
                "area": "traceability",
                "state": "incomplete_chains",
                "count": trace_counts.get("partial", 0) + trace_counts.get("unlinked", 0),
            }
        )

    incomplete_evidence = _as_int(evidence_report.get("total_packages")) - _as_int(
        evidence_report.get("evidence_complete_packages")
    )
    if incomplete_evidence > 0:
        items.append({"area": "evidence", "state": "incomplete_packages", "count": incomplete_evidence})

    regression_counts = _counts(regression_report, "comparison_counts")
    if regression_counts.get("failed", 0) or regression_counts.get("blocked", 0):
        items.append(
            {
                "area": "regression_validation",
                "state": "non_validated_results",
                "count": regression_counts.get("failed", 0) + regression_counts.get("blocked", 0),
            }
        )

    return items


def build_validation_summary(reports: dict[str, Any]) -> dict[str, Any]:
    """Aggregate report outputs into a single validation summary."""

    registry_report = _section(reports, "behavior_registry")
    evidence_report = _section(reports, "evidence_packages")
    regression_report = _section(reports, "regression_assets")
    traceability_report = _section(reports, "behavior_traceability")
    coverage_report = _section(reports, "behavior_coverage")

    coverage_percent = _as_number(coverage_report.get("covered_behavior_percent"))
    traceability_percent = _as_number(traceability_report.get("complete_traceability_percent"))
    evidence_percent = _as_number(evidence_report.get("evidence_completeness_percent"))
    regression_counts = _counts(regression_report, "comparison_counts")
    failed_regressions = regression_counts.get("failed", 0)

    return {
        "product_boundary": "discover-prove-report-regression-validate",
        "summary_type": "validation_assurance",
        "assurance_level": _assurance_level(
            coverage_percent,
            traceability_percent,
            evidence_percent,
            failed_regressions,
        ),
        "north_star": {
            "metric": "confirmed_violation_rate",
            "confirmed_violations": _as_int(evidence_report.get("confirmed_packages")),
            "detected_violations": _as_int(evidence_report.get("total_packages")),
        },
        "behavior_state": {
            "total_behaviors": _as_int(registry_report.get("total_behaviors") or coverage_report.get("total_behaviors")),
            "registry_status_counts": _counts(registry_report, "status_counts"),
            "covered_behavior_percent": coverage_percent,
            "observed_or_covered_behavior_percent": _as_number(
                coverage_report.get("observed_or_covered_behavior_percent")
            ),
        },
        "evidence_state": {
            "total_packages": _as_int(evidence_report.get("total_packages")),
            "confirmed_packages": _as_int(evidence_report.get("confirmed_packages")),
            "evidence_complete_packages": _as_int(evidence_report.get("evidence_complete_packages")),
            "evidence_completeness_percent": evidence_percent,
        },
        "traceability_state": {
            "total_traces": _as_int(traceability_report.get("total_traces")),
            "trace_status_counts": _counts(traceability_report, "status_counts"),
            "complete_traceability_percent": traceability_percent,
        },
        "regression_state": {
            "total_assets": _as_int(regression_report.get("total_assets")),
            "confirmed_violation_assets": _as_int(regression_report.get("confirmed_violation_assets")),
            "comparison_counts": regression_counts,
        },
        "attention_items": _attention_items(
            coverage_report,
            traceability_report,
            evidence_report,
            regression_report,
        ),
    }


def build_validation_summary_report(reports: dict[str, Any]) -> dict[str, Any]:
    """Return a customer-ready validation summary report."""

    summary = build_validation_summary(reports)
    detected = summary["north_star"]["detected_violations"]
    confirmed = summary["north_star"]["confirmed_violations"]
    summary["north_star"]["confirmed_violation_rate"] = round((confirmed / detected) * 100, 2) if detected else 0.0
    return summary
