from __future__ import annotations

"""Compatibility bridge that enriches the current delivery index with proof cards."""

from typing import Any

from .runtime_customer_evidence_casebook_v2 import build_evidence_casebook
from .runtime_customer_report_builder import build_customer_delivery_index


def build_customer_delivery_index_with_proof(findings: list[dict[str, Any]]) -> dict[str, Any]:
    report = build_customer_delivery_index(findings)
    casebook = build_evidence_casebook(findings)
    proof_by_id = {str(case.get("finding_id") or ""): case for case in casebook.get("cases") or []}
    for row in report.get("top_customer_actions") or []:
        finding_id = str(row.get("finding_id") or "")
        proof = proof_by_id.get(finding_id, {})
        row["proof_status"] = proof.get("proof_status")
        row["proof_coverage_score"] = proof.get("coverage_score")
        row["evidence_gaps"] = proof.get("evidence_gaps") or []
        row["lineage_digest"] = proof.get("lineage_digest")
    report["customer_evidence_casebook"] = casebook
    report["engine"] = "runtime_customer_report_proof_bridge_v1_phase107"
    report["recommended_report_usage"] = "Only present customer_ready proof cards as verified value; keep evidence gaps visible until the requested receipts are collected."
    return report
