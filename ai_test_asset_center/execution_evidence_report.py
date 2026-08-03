"""Execution-evidence quality metrics for behavior-modeling verification.

Executable hypotheses are necessary but not sufficient. A hypothesis can include
clear API steps while still producing no observed request/response evidence.
This module measures whether verification outputs are backed by concrete runtime
signals such as status codes, requests, responses, errors, or probe evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .scan_post_hooks import register_scan_post_hook

HOOK_NAME = "execution_evidence_report"

EVIDENCE_KEYS = {
    "request",
    "requests",
    "response",
    "responses",
    "status_code",
    "status",
    "http_status",
    "error",
    "errors",
    "exception",
    "traceback",
    "evidence",
    "probe",
    "probes",
    "runtime_evidence",
    "verification_result",
    "verification_results",
    "observed",
    "observations",
}

STATUS_EVIDENCE_KEYS = {"status", "status_code", "http_status"}


def _has_status_evidence(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        try:
            int(text)
            return True
        except ValueError:
            return text.upper().startswith("HTTP ")
    return False


def _has_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_has_non_empty_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_non_empty_value(item) for item in value.values())
    return True


def has_runtime_evidence(candidate: Any) -> bool:
    """Return True when a candidate contains concrete runtime verification evidence."""
    if isinstance(candidate, dict):
        for key, value in candidate.items():
            key_text = str(key).lower()
            if key_text in STATUS_EVIDENCE_KEYS:
                if _has_status_evidence(value):
                    return True
                continue
            if key_text in EVIDENCE_KEYS and _has_non_empty_value(value):
                return True
            if has_runtime_evidence(value):
                return True
        return False

    if isinstance(candidate, (list, tuple, set)):
        return any(has_runtime_evidence(item) for item in candidate)

    return False


def build_execution_evidence_report(
    verification_items: Any,
    *,
    engine_names: list[str] | None = None,
) -> dict[str, Any]:
    """Build stable evidence-backed metrics from verification outputs.

    Args:
        verification_items: Either a list of verification records or a dict mapping
            engine name to verification records.
        engine_names: Optional ordered list of expected engines; missing engines are
            reported with zero evidence-backed output.
    """
    if isinstance(verification_items, dict):
        by_engine = verification_items
    else:
        by_engine = {"default": verification_items if isinstance(verification_items, list) else []}

    expected_engines = list(engine_names or by_engine.keys())
    per_engine_total: dict[str, int] = {}
    per_engine_evidence: dict[str, int] = {}
    per_engine_ratio: dict[str, float] = {}

    total_items = 0
    evidence_items = 0

    for engine in expected_engines:
        raw_items = by_engine.get(engine, [])
        if isinstance(raw_items, dict):
            items = list(raw_items.values())
        elif isinstance(raw_items, list):
            items = raw_items
        else:
            items = []

        engine_total = len(items)
        engine_evidence = sum(1 for item in items if has_runtime_evidence(item))
        per_engine_total[str(engine)] = engine_total
        per_engine_evidence[str(engine)] = engine_evidence
        per_engine_ratio[str(engine)] = engine_evidence / engine_total if engine_total else 0.0
        total_items += engine_total
        evidence_items += engine_evidence

    no_evidence_engines = [engine for engine in expected_engines if per_engine_evidence.get(str(engine), 0) == 0]

    return {
        "evidence_backed_items": evidence_items,
        "non_evidence_backed_items": total_items - evidence_items,
        "evidence_backed_ratio": evidence_items / total_items if total_items else 0.0,
        "per_engine_verification_items": per_engine_total,
        "per_engine_evidence_backed_items": per_engine_evidence,
        "per_engine_evidence_backed_ratio": per_engine_ratio,
        "engines_with_no_evidence_backed_output": no_evidence_engines,
    }


def attach_execution_evidence_report(
    scan_result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Measure whether the scan's verification outputs carry runtime evidence.

    The finding carriers on the scan result are treated as verification items;
    evidence-backed ratios are projected without altering any finding.
    """
    if not isinstance(scan_result, dict):
        return scan_result
    carriers = [
        key
        for key in (
            "real_findings",
            "bug_scores",
            "db_findings",
            "e2e_findings",
            "deep_findings",
            "ui_findings",
        )
        if isinstance(scan_result.get(key), list)
    ]
    verification_items = {key: scan_result[key] for key in carriers}
    if not verification_items:
        verification_items = []
    scan_result["execution_evidence_report"] = build_execution_evidence_report(
        verification_items,
        engine_names=carriers or None,
    )
    return scan_result


def install_execution_evidence_report() -> None:
    register_scan_post_hook(HOOK_NAME, attach_execution_evidence_report)
