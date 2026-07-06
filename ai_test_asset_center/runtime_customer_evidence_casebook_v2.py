from __future__ import annotations

"""A domain-neutral proof-card builder for customer delivery."""

from hashlib import sha256
import json
from typing import Any

from .phase107_contract_notes import missing_receipt_checks, required_receipt_checks


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value in (None, "", {}) else [value])


def _text(value: Any, limit: int = 240) -> str:
    value = str(value if value is not None else "")
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + sha256(raw.encode("utf-8")).hexdigest()


def build_evidence_case(finding: dict[str, Any]) -> dict[str, Any]:
    """Build proof from supplied data only; never infer business semantics."""
    finding = finding if isinstance(finding, dict) else {}
    request = finding.get("request") if isinstance(finding.get("request"), dict) else {}
    method = _text(finding.get("method") or request.get("method")).upper()
    path = _text(finding.get("path") or request.get("path"))
    verify = finding.get("verification") if isinstance(finding.get("verification"), dict) else {}
    verdict = _text(verify.get("verdict") or finding.get("verdict"))
    claim = _text(verify.get("reason") or finding.get("customer_impact_summary") or finding.get("title"))
    snapshots = finding.get("snapshots") if isinstance(finding.get("snapshots"), dict) else {}
    before = [row for row in _items(snapshots.get("before")) if isinstance(row, dict)]
    after = [row for row in _items(snapshots.get("after")) if isinstance(row, dict)]
    responses = _items(finding.get("responses")) or _items(finding.get("response"))
    source_refs = _items(finding.get("source_refs"))
    receipt_flags = {
        "document_grounding": bool(source_refs),
        "executed_request": bool(method and path),
        "runtime_receipt": bool(responses or before or after),
        "assertion": bool(claim),
        "before_after_observation": bool(before and after),
    }
    missing = missing_receipt_checks(receipt_flags, method)
    validated = verdict in {"validated_candidate", "confirmed"}
    status = "customer_ready" if validated and not missing else "needs_more_evidence" if validated else "not_customer_ready"
    receipts = {
        "source_reference_count": len(source_refs),
        "target_response_count": len(responses),
        "before_observation_count": len(before),
        "after_observation_count": len(after),
    }
    lineage = {"finding_id": finding.get("finding_id") or finding.get("candidate_id"), "method": method, "path": path, "receipts": receipts}
    checks = required_receipt_checks(method)
    return {
        "finding_id": lineage["finding_id"],
        "proof_status": status,
        "coverage_score": round(sum(bool(receipt_flags.get(item)) for item in checks) / len(checks), 2),
        "evidence_gaps": missing,
        "receipt_summary": receipts,
        "lineage_digest": _digest(lineage),
        "customer_proof": {"claim": claim, "method": method, "path": path, "runtime_verdict": verdict, "next_actions": missing},
    }


def build_evidence_casebook(findings: list[dict[str, Any]]) -> dict[str, Any]:
    cases = [build_evidence_case(item) for item in findings if isinstance(item, dict)]
    by_status: dict[str, int] = {}
    for case in cases:
        key = str(case["proof_status"])
        by_status[key] = by_status.get(key, 0) + 1
    return {"engine": "runtime_customer_evidence_casebook_v2_phase107", "case_count": len(cases), "by_proof_status": dict(sorted(by_status.items())), "cases": cases}
