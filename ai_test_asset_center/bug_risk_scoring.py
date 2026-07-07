"""Risk scoring and severity classification for confirmed bug findings."""

from __future__ import annotations

import re
from typing import Any


SEVERITY_ORDER = ("P0", "P1", "P2", "P3")


def _text_blob(finding: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("title", "summary", "description", "impact", "risk", "error", "category", "type"):
        value = finding.get(key)
        if value is not None:
            values.append(str(value))
    return " ".join(values).lower()


def _status_code(finding: dict[str, Any]) -> int | None:
    candidates = [finding.get("status_code")]
    response = finding.get("response")
    if isinstance(response, dict):
        candidates.extend([response.get("status"), response.get("status_code")])
    runtime_evidence = finding.get("runtime_evidence")
    if isinstance(runtime_evidence, dict):
        candidates.append(runtime_evidence.get("status_code"))
        nested_response = runtime_evidence.get("response")
        if isinstance(nested_response, dict):
            candidates.extend([nested_response.get("status"), nested_response.get("status_code")])

    for value in candidates:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    text_match = re.search(r"\b([45]\d{2})\b", _text_blob(finding))
    if text_match:
        return int(text_match.group(1))
    return None


def score_bug_risk(finding: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic risk score and severity for one bug finding.

    The score is intentionally conservative and explainable. It does not require
    model output, so enterprise reports can be reproduced in CI and audits.
    """

    text = _text_blob(finding)
    status_code = _status_code(finding)
    score = 0
    reasons: list[str] = []

    critical_terms = ("data loss", "financial", "payment", "security", "permission", "auth", "privacy", "leak")
    major_terms = ("crash", "500", "exception", "timeout", "deadlock", "concurrency", "duplicate", "corruption")
    medium_terms = ("validation", "incorrect", "wrong", "missing", "failed", "error", "inconsistent")

    if any(term in text for term in critical_terms):
        score += 50
        reasons.append("critical business/security impact")

    if any(term in text for term in major_terms):
        score += 30
        reasons.append("major runtime or data-integrity symptom")

    if any(term in text for term in medium_terms):
        score += 30
        reasons.append("functional correctness symptom")

    if status_code is not None:
        if status_code >= 500:
            score += 25
            reasons.append(f"server-side failure status {status_code}")
        elif status_code >= 400:
            score += 10
            reasons.append(f"client-visible failure status {status_code}")

    if finding.get("confirmed_bug") or finding.get("confirmed") or finding.get("is_confirmed"):
        score += 10
        reasons.append("confirmed bug candidate")

    if finding.get("runtime_evidence") or finding.get("request") or finding.get("response"):
        score += 5
        reasons.append("runtime evidence available")

    score = min(score, 100)
    if score >= 80:
        severity = "P0"
    elif score >= 55:
        severity = "P1"
    elif score >= 30:
        severity = "P2"
    else:
        severity = "P3"

    return {
        "risk_score": score,
        "severity": severity,
        "risk_reasons": reasons or ["low explicit impact in available finding data"],
    }


def enrich_bug_with_risk(finding: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(finding)
    enriched.update(score_bug_risk(finding))
    return enriched


def build_bug_risk_report(findings: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = [enrich_bug_with_risk(finding) for finding in findings]
    severity_counts = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in enriched:
        severity_counts[finding["severity"]] += 1

    highest = None
    for severity in SEVERITY_ORDER:
        candidates = [item for item in enriched if item["severity"] == severity]
        if candidates:
            highest = max(candidates, key=lambda item: item["risk_score"])
            break

    return {
        "total_findings": len(enriched),
        "severity_counts": severity_counts,
        "highest_risk_finding": highest,
        "findings": enriched,
    }
