"""Deterministic fix recommendations for confirmed bug findings."""

from __future__ import annotations

from typing import Any

from ai_test_asset_center.bug_risk_scoring import enrich_bug_with_risk


ROOT_CAUSE_RULES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "access_control",
        ("permission", "auth", "unauthorized", "privilege", "role", "tenant", "leak", "privacy"),
        "Access control or data isolation is likely incomplete.",
        "Add server-side authorization checks for the affected route, validate tenant/user ownership before data access, and add regression tests for low-privilege roles.",
    ),
    (
        "payment_or_financial_integrity",
        ("payment", "financial", "refund", "balance", "price", "amount", "duplicate", "order"),
        "Financial or order-state integrity is likely missing a business invariant.",
        "Move amount/order validation to the backend transaction boundary, add idempotency keys for write APIs, and verify state transitions with database-level consistency tests.",
    ),
    (
        "runtime_exception",
        ("500", "exception", "traceback", "crash", "null", "none", "undefined"),
        "The API appears to expose an unhandled runtime exception.",
        "Guard nullable inputs, normalize error handling, return a stable business error code, and add a regression test that replays the captured request payload.",
    ),
    (
        "performance_or_stability",
        ("timeout", "deadlock", "slow", "latency", "concurrency", "race"),
        "The defect is likely caused by unstable execution under load or concurrency.",
        "Add timeout budgets, lock ordering/idempotency controls, and a concurrency regression test that reproduces the observed timing condition.",
    ),
    (
        "input_validation",
        ("validation", "missing", "incorrect", "wrong", "invalid", "format", "required"),
        "Input or business-rule validation is likely incomplete.",
        "Centralize validation in the service layer, reject invalid states before persistence, and add boundary-value API tests for the affected fields.",
    ),
)


def _text_blob(finding: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("title", "summary", "description", "impact", "risk", "error", "category", "type", "evidence"):
        value = finding.get(key)
        if value is not None:
            values.append(str(value))
    return " ".join(values).lower()


def classify_root_cause(finding: dict[str, Any]) -> dict[str, str]:
    text = _text_blob(finding)
    for category, keywords, explanation, recommendation in ROOT_CAUSE_RULES:
        if any(keyword in text for keyword in keywords):
            return {
                "root_cause_category": category,
                "root_cause_summary": explanation,
                "fix_recommendation": recommendation,
            }

    return {
        "root_cause_category": "needs_triage",
        "root_cause_summary": "The available finding data is not specific enough to assign a precise root cause.",
        "fix_recommendation": "Replay the captured request and response, identify the failing service boundary, then add a minimal regression test before applying code changes.",
    }


def build_fix_recommendation(finding: dict[str, Any]) -> dict[str, Any]:
    enriched = enrich_bug_with_risk(finding)
    enriched.update(classify_root_cause(finding))
    return enriched


def build_fix_recommendation_report(findings: list[dict[str, Any]]) -> dict[str, Any]:
    recommendations = [build_fix_recommendation(finding) for finding in findings]
    by_root_cause: dict[str, int] = {}
    for item in recommendations:
        key = item["root_cause_category"]
        by_root_cause[key] = by_root_cause.get(key, 0) + 1

    return {
        "total_findings": len(recommendations),
        "root_cause_counts": by_root_cause,
        "recommendations": recommendations,
    }
