from __future__ import annotations

"""P4 customer-facing value scorecard.

P4 turns technical benchmark evidence into a management-readable proof of value.
It is intentionally customer-safe: it summarizes seed defect IDs, titles,
severity and counts, without exposing raw request/response payloads.
"""

from collections import Counter
from typing import Any


_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _severity(value: Any) -> str:
    text = str(value or "P2").upper().strip()
    return text if text in _SEVERITY_ORDER else "P2"


def _safe_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "seed_id": str(item.get("seed_id") or item.get("id") or "")[:120],
        "title": str(item.get("title") or item.get("seed_id") or "")[:240],
        "severity": _severity(item.get("severity")),
        "kind": str(item.get("kind") or "")[:80],
        "status": str(item.get("status") or "")[:40],
    }


def _value_level(found: int, total: int, p0_found: int, detection_rate: float) -> str:
    if total <= 0:
        return "insufficient_data"
    if found <= 0:
        return "not_proven"
    if p0_found > 0 and detection_rate >= 0.8:
        return "critical_value_proven"
    if p0_found > 0 or detection_rate >= 0.5:
        return "value_proven"
    return "early_signal"


def _next_actions(level: str, missed_count: int) -> list[str]:
    if level == "critical_value_proven":
        actions = [
            "Use the scorecard in the customer executive readout.",
            "Convert found P0/P1 defects into customer-safe evidence stories.",
            "Run a second benchmark round on another business flow.",
        ]
    elif level == "value_proven":
        actions = [
            "Review found defects with the customer QA and product owners.",
            "Expand the seed benchmark to include more high-risk flows.",
        ]
    elif level == "early_signal":
        actions = [
            "Add more P0/P1 seed defects before executive presentation.",
            "Improve scenario coverage for the current business flow.",
        ]
    elif level == "not_proven":
        actions = [
            "Do not present this as a value proof yet.",
            "Inspect missed defects and add targeted runtime scenarios.",
        ]
    else:
        actions = [
            "Provide p3_seed_bug_benchmark before generating customer value proof.",
        ]
    if missed_count > 0 and level not in {"not_proven", "insufficient_data"}:
        actions.append("Triage missed defects and classify whether they are scenario, oracle, or evidence gaps.")
    return actions[:4]


def build_p4_customer_value_scorecard(scan_result: dict[str, Any]) -> dict[str, Any]:
    result = _as_dict(scan_result)
    benchmark = _as_dict(result.get("p3_seed_bug_benchmark"))
    findings = [_safe_item(item) for item in _as_list(benchmark.get("findings")) if isinstance(item, dict)]
    missed = [_safe_item(item) for item in _as_list(benchmark.get("missed")) if isinstance(item, dict)]
    total = int(benchmark.get("total_seed_defects") or len(findings) + len(missed) or 0)
    found = int(benchmark.get("found_count") or len(findings) or 0)
    missed_count = int(benchmark.get("missed_count") or len(missed) or 0)
    detection_rate = float(benchmark.get("detection_rate") or (found / total if total else 0.0))
    severity_counts = Counter(item["severity"] for item in findings)
    missed_severity_counts = Counter(item["severity"] for item in missed)
    p0_found = int(severity_counts.get("P0", 0))
    p1_found = int(severity_counts.get("P1", 0))
    level = _value_level(found, total, p0_found, detection_rate)
    release_gate = _as_dict(result.get("release_gate"))
    evidence_bundle = _as_dict(result.get("evidence_bundle"))
    runtime_contract = _as_dict(result.get("runtime_contract"))
    headline_zh = f"本轮种子缺陷发现率 {_pct(detection_rate)}，命中 {found}/{total} 个已知高风险缺陷。"
    if p0_found or p1_found:
        headline_zh += f" 其中 P0 {p0_found} 个，P1 {p1_found} 个。"
    headline_en = f"Seed-defect detection rate is {_pct(detection_rate)} with {found}/{total} known high-risk defects found."
    if p0_found or p1_found:
        headline_en += f" P0 found: {p0_found}; P1 found: {p1_found}."
    next_actions = _next_actions(level, missed_count)
    return {
        "schema_version": "p4-customer-value-scorecard-v1",
        "value_level": level,
        "customer_safe": True,
        "project": str(result.get("project") or ""),
        "benchmark_grade": str(benchmark.get("grade") or ""),
        "board_metrics": {
            "seed_defects_total": total,
            "seed_defects_found": found,
            "seed_defects_missed": missed_count,
            "detection_rate": detection_rate,
            "p0_found": p0_found,
            "p1_found": p1_found,
            "observed_http_calls": int(benchmark.get("observed_http_calls") or 0),
        },
        "severity_distribution": dict(sorted(severity_counts.items(), key=lambda item: _SEVERITY_ORDER.get(item[0], 99))),
        "missed_severity_distribution": dict(sorted(missed_severity_counts.items(), key=lambda item: _SEVERITY_ORDER.get(item[0], 99))),
        "customer_safe_findings": sorted(findings, key=lambda item: (_SEVERITY_ORDER.get(item["severity"], 99), item["seed_id"])),
        "customer_safe_missed": sorted(missed, key=lambda item: (_SEVERITY_ORDER.get(item["severity"], 99), item["seed_id"])),
        "execution_context": {
            "runtime_status": str(runtime_contract.get("status") or ""),
            "execution_status": str(result.get("execution_status") or ""),
            "evidence_bundle_status": str(evidence_bundle.get("status") or ""),
            "release_gate_verdict": str(release_gate.get("verdict") or release_gate.get("status") or ""),
        },
        "executive_summary_zh": headline_zh,
        "executive_summary_en": headline_en,
        "next_actions": next_actions,
    }
