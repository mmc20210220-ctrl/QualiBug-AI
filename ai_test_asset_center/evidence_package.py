"""Evidence package primitives for confirmed behavior violations.

Evidence packages are customer-grade proof artifacts. They summarize what was
observed, how it was reproduced, and why it matters. They intentionally do not
recommend fixes or repairs; QualiBug-AI discovers, proves, reports, and
validates, while customers decide how to change their systems.
"""

from __future__ import annotations

from typing import Any


REPAIR_LANGUAGE = ("fix", "repair", "recommendation", "remediation", "patch", "pull request")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    return [value]


def _first_text(*values: Any, default: str) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _violation_id(item: dict[str, Any], fallback_index: int) -> str:
    return _first_text(
        item.get("violation_id"),
        item.get("bug_id"),
        item.get("finding_id"),
        item.get("id"),
        default=f"VIO-{fallback_index:04d}",
    )


def _behavior_id(item: dict[str, Any], fallback_index: int) -> str:
    return _first_text(
        item.get("behavior_id"),
        item.get("behavior"),
        default=f"BEH-{fallback_index:04d}",
    )


def _runtime_evidence(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("runtime_evidence") or item.get("evidence") or item.get("execution_evidence") or {}
    if isinstance(evidence, dict):
        return evidence
    return {"observations": _as_list(evidence)}


def _request_response(item: dict[str, Any], runtime_evidence: dict[str, Any]) -> dict[str, Any]:
    request = item.get("request") or runtime_evidence.get("request") or runtime_evidence.get("http_request")
    response = item.get("response") or runtime_evidence.get("response") or runtime_evidence.get("http_response")
    return {
        "request": request or {},
        "response": response or {},
    }


def _reproduction_steps(item: dict[str, Any], runtime_evidence: dict[str, Any]) -> list[Any]:
    explicit_steps = _as_list(item.get("reproduction_steps") or item.get("steps_to_reproduce"))
    if explicit_steps:
        return explicit_steps

    request = item.get("request") or runtime_evidence.get("request") or runtime_evidence.get("http_request")
    if request:
        return [
            "Execute the captured request against the affected behavior endpoint.",
            "Compare the runtime response with the expected behavior contract.",
            "Confirm that the observed response reproduces the violation evidence.",
        ]

    return ["Replay the captured validation artifact and compare observed behavior with expected behavior."]


def _risk_context(item: dict[str, Any]) -> dict[str, Any]:
    risk = item.get("risk") or item.get("risk_context") or item.get("risk_assessment") or {}
    if not isinstance(risk, dict):
        risk = {"summary": risk}

    severity = _first_text(item.get("severity"), risk.get("severity"), default="unclassified")
    return {
        **risk,
        "severity": severity,
        "risk_score": item.get("risk_score", risk.get("risk_score")),
    }


def _traceability(item: dict[str, Any], violation_id: str, behavior_id: str) -> dict[str, Any]:
    return {
        "behavior_id": behavior_id,
        "violation_id": violation_id,
        "evidence_ids": _as_list(item.get("evidence_id") or item.get("evidence_ids")),
        "validation_run_ids": _as_list(item.get("validation_run_id") or item.get("validation_runs")),
        "regression_asset_ids": _as_list(item.get("regression_asset_id") or item.get("regression_assets")),
    }


def build_evidence_package(item: dict[str, Any], fallback_index: int = 1) -> dict[str, Any]:
    """Build a deterministic evidence package for a violation artifact."""

    violation_id = _violation_id(item, fallback_index)
    behavior_id = _behavior_id(item, fallback_index)
    runtime_evidence = _runtime_evidence(item)
    request_response = _request_response(item, runtime_evidence)

    package = {
        "package_id": f"EP-{violation_id}",
        "violation": {
            "violation_id": violation_id,
            "title": _first_text(item.get("title"), item.get("name"), default=violation_id),
            "behavior_id": behavior_id,
            "behavior_name": _first_text(item.get("behavior_name"), item.get("behavior"), default=behavior_id),
            "category": _first_text(item.get("category"), item.get("domain"), default="uncategorized"),
            "confirmed": bool(item.get("confirmed_bug") or item.get("confirmed") or item.get("is_confirmed")),
        },
        "runtime_evidence": runtime_evidence,
        "request_response_evidence": request_response,
        "reproduction_steps": _reproduction_steps(item, runtime_evidence),
        "risk_context": _risk_context(item),
        "traceability": _traceability(item, violation_id, behavior_id),
        "audit_package": {
            "source_artifact_type": _first_text(item.get("artifact_type"), default="violation"),
            "evidence_complete": bool(runtime_evidence),
            "customer_action_owner": "customer",
            "product_action_owner": "QualiBug-AI",
            "product_boundary": "discover-prove-report-regression-validate",
        },
        "customer_ready_export": True,
    }

    return package


def build_evidence_package_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a report containing evidence packages and completeness metrics."""

    packages = [build_evidence_package(item, index) for index, item in enumerate(items, start=1)]
    complete = [package for package in packages if package["audit_package"]["evidence_complete"]]
    confirmed = [package for package in packages if package["violation"]["confirmed"]]

    return {
        "total_packages": len(packages),
        "confirmed_packages": len(confirmed),
        "evidence_complete_packages": len(complete),
        "evidence_completeness_percent": round((len(complete) / len(packages)) * 100, 2) if packages else 0.0,
        "packages": packages,
    }
