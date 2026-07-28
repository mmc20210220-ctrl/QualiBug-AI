"""Receipt-backed, redacted evidence projection for discovery results.

The execution authority already returns steps, typed observer receipts, contract
oracle verdicts and optional findings. This module projects only their identities
and statuses into evidence graphs and trace summaries. It never copies request or
response bodies, credentials, headers, database rows or other raw payloads, and
it never creates a finding that the delivery authority did not already create.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .discovery_runtime_execution import (
    run_experiment_candidate as _run_experiment_candidate,
)

SCHEMA_VERSION = "qualibug.formal-evidence-projection.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _execution_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _dict(_dict(result).get("experiment_execution")).get("results")
    if isinstance(raw, dict):
        return [
            dict(row)
            for _, row in sorted(raw.items(), key=lambda item: str(item[0]))
            if isinstance(row, dict)
        ]
    return [dict(row) for row in _list(raw) if isinstance(row, dict)]


def _finding_id(finding: dict[str, Any]) -> str:
    return _text(
        finding.get("finding_id")
        or finding.get("id")
        or finding.get("canonical_defect_id")
    )


def _is_ui_finding(
    finding: dict[str, Any],
    observer_ids: set[str],
) -> bool:
    if not finding:
        return False
    labels = " ".join(
        _text(finding.get(key)).lower()
        for key in (
            "surface",
            "channel",
            "category",
            "risk_family",
            "finding_type",
        )
    )
    if any(token in labels for token in ("ui", "ux", "visual", "dom", "page")):
        return True
    return any(
        observer_id.startswith(("ui_", "visual_", "dom_", "page_"))
        for observer_id in observer_ids
    )


def _build_graph(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    experiment_id = _text(row.get("experiment_id"))
    obligation_id = _text(row.get("obligation_id"))
    status = _text(row.get("status"))
    reason_code = _text(row.get("reason_code"))
    execution_node_id = _stable_id(
        "evidence_execution",
        {
            "experiment_id": experiment_id,
            "obligation_id": obligation_id,
            "status": status,
            "reason_code": reason_code,
        },
    )
    nodes: list[dict[str, Any]] = [{
        "node_id": execution_node_id,
        "node_type": "experiment_execution",
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "status": status,
        "reason_code": reason_code,
        "elapsed_ms": int(row.get("elapsed_ms") or 0),
    }]
    edges: list[dict[str, str]] = []

    step_ids: list[str] = []
    for index, step in enumerate(_list(row.get("steps"))):
        if not isinstance(step, dict):
            continue
        step_id = _text(step.get("step_id") or step.get("id")) or f"step_{index + 1}"
        node_id = _stable_id(
            "evidence_step",
            {
                "experiment_id": experiment_id,
                "step_id": step_id,
                "operation_ref": _text(step.get("operation_ref")),
                "method": _text(step.get("method")),
                "path": _text(step.get("path")),
                "status_code": int(step.get("status_code") or 0),
            },
        )
        step_ids.append(node_id)
        nodes.append({
            "node_id": node_id,
            "node_type": "execution_step",
            "step_id": step_id,
            "phase": _text(step.get("phase")),
            "operation_ref": _text(step.get("operation_ref")),
            "actor_ref": _text(step.get("actor_ref")),
            "method": _text(step.get("method")).upper(),
            "path": _text(step.get("path")),
            "status_code": int(step.get("status_code") or 0),
        })
        edges.append({
            "edge_type": "contains_step",
            "from_ref": execution_node_id,
            "to_ref": node_id,
        })

    observer_ids: set[str] = set()
    observer_node_ids: list[str] = []
    for receipt in _list(row.get("observer_receipts")):
        if not isinstance(receipt, dict):
            continue
        observer_id = _text(receipt.get("observer_id"))
        receipt_id = _text(receipt.get("receipt_id"))
        if observer_id:
            observer_ids.add(observer_id)
        node_id = receipt_id or _stable_id(
            "evidence_observer",
            {
                "experiment_id": experiment_id,
                "observer_id": observer_id,
                "status": _text(receipt.get("status")),
                "reason_code": _text(receipt.get("reason_code")),
            },
        )
        observer_node_ids.append(node_id)
        nodes.append({
            "node_id": node_id,
            "node_type": "observer_receipt",
            "observer_id": observer_id,
            "status": _text(receipt.get("status")),
            "reason_code": _text(receipt.get("reason_code")),
            "campaign_id": _text(receipt.get("campaign_id")),
            "execution_id": _text(receipt.get("execution_id")),
        })
        edges.append({
            "edge_type": "observed_by",
            "from_ref": execution_node_id,
            "to_ref": node_id,
        })

    contract_node_ids: list[str] = []
    for receipt in _list(row.get("contract_evidence_receipts")):
        if not isinstance(receipt, dict):
            continue
        receipt_id = _text(receipt.get("receipt_id") or receipt.get("id"))
        node_id = receipt_id or _stable_id(
            "evidence_contract",
            {
                "experiment_id": experiment_id,
                "schema_version": _text(receipt.get("schema_version")),
                "status": _text(receipt.get("status")),
                "reason_code": _text(receipt.get("reason_code")),
            },
        )
        contract_node_ids.append(node_id)
        nodes.append({
            "node_id": node_id,
            "node_type": "contract_evidence_receipt",
            "schema_version": _text(receipt.get("schema_version")),
            "status": _text(receipt.get("status")),
            "reason_code": _text(receipt.get("reason_code")),
        })
        edges.append({
            "edge_type": "contract_evidence",
            "from_ref": execution_node_id,
            "to_ref": node_id,
        })

    verdict = _dict(row.get("oracle_verdict"))
    oracle_node_id = ""
    if verdict:
        oracle_node_id = _stable_id(
            "evidence_oracle",
            {
                "experiment_id": experiment_id,
                "status": _text(verdict.get("status")),
                "verdict": _text(verdict.get("verdict")),
                "reason_codes": [
                    _text(value)
                    for value in _list(verdict.get("reason_codes"))
                    if _text(value)
                ],
            },
        )
        nodes.append({
            "node_id": oracle_node_id,
            "node_type": "contract_oracle_verdict",
            "status": _text(verdict.get("status")),
            "verdict": _text(verdict.get("verdict")),
            "reason_codes": [
                _text(value)
                for value in _list(verdict.get("reason_codes"))
                if _text(value)
            ],
            "assertion_count": len([
                value for value in _list(verdict.get("assertions"))
                if isinstance(value, dict)
            ]),
        })
        edges.append({
            "edge_type": "evaluated_by",
            "from_ref": execution_node_id,
            "to_ref": oracle_node_id,
        })
        for observer_node_id in observer_node_ids:
            edges.append({
                "edge_type": "supports_oracle",
                "from_ref": observer_node_id,
                "to_ref": oracle_node_id,
            })
        for contract_node_id in contract_node_ids:
            edges.append({
                "edge_type": "supports_oracle",
                "from_ref": contract_node_id,
                "to_ref": oracle_node_id,
            })

    finding = _dict(row.get("finding"))
    projected_ui_finding: dict[str, Any] | None = None
    finding_id = _finding_id(finding)
    if finding and finding_id:
        finding_node_id = _stable_id(
            "evidence_finding",
            {"finding_id": finding_id, "experiment_id": experiment_id},
        )
        nodes.append({
            "node_id": finding_node_id,
            "node_type": "existing_finding",
            "finding_id": finding_id,
            "canonical_defect_id": _text(finding.get("canonical_defect_id")),
            "title": _text(finding.get("title")),
            "category": _text(finding.get("category")),
            "risk_family": _text(finding.get("risk_family")),
            "severity": _text(finding.get("severity")),
            "status": _text(finding.get("status")),
        })
        edges.append({
            "edge_type": "supports_existing_finding",
            "from_ref": oracle_node_id or execution_node_id,
            "to_ref": finding_node_id,
        })
        if _is_ui_finding(finding, observer_ids):
            projected_ui_finding = deepcopy(finding)

    graph_identity = {
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "execution_status": status,
        "node_ids": sorted(_text(node.get("node_id")) for node in nodes),
    }
    graph = {
        "schema_version": SCHEMA_VERSION,
        "graph_id": _stable_id("evidence_graph", graph_identity),
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "execution_status": status,
        "nodes": nodes,
        "edges": edges,
        "coverage": {
            "step_count": len(step_ids),
            "observer_receipt_count": len(observer_node_ids),
            "contract_evidence_receipt_count": len(contract_node_ids),
            "oracle_present": bool(oracle_node_id),
            "existing_finding_present": bool(finding_id),
        },
        "redaction_contract": {
            "raw_payloads_included": False,
            "credentials_included": False,
            "database_rows_included": False,
        },
    }
    summary = {
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "status": status,
        "reason_code": reason_code,
        "elapsed_ms": int(row.get("elapsed_ms") or 0),
        "step_count": len(step_ids),
        "observer_receipt_count": len(observer_node_ids),
        "observed_observer_count": sum(
            1
            for receipt in _list(row.get("observer_receipts"))
            if isinstance(receipt, dict)
            and _text(receipt.get("status")) == "OBSERVED"
        ),
        "oracle_status": _text(verdict.get("status")),
        "oracle_verdict": _text(verdict.get("verdict")),
        "finding_id": finding_id,
    }
    return graph, summary, projected_ui_finding


def project_formal_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Fill formal evidence surfaces from actual execution receipts only."""

    if not isinstance(result, dict):
        raise TypeError("discovery_result_not_object")
    projected = deepcopy(result)
    graphs: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    ui_findings: list[dict[str, Any]] = []
    seen_ui_ids: set[str] = set()
    for row in _execution_rows(projected):
        graph, summary, ui_finding = _build_graph(row)
        graphs.append(graph)
        summaries.append(summary)
        if ui_finding:
            finding_id = _finding_id(ui_finding)
            if finding_id and finding_id not in seen_ui_ids:
                seen_ui_ids.add(finding_id)
                ui_findings.append(ui_finding)

    projected["evidence_graphs"] = graphs
    projected["execution_trace_summaries"] = summaries
    projected["ui_findings"] = ui_findings
    projection_receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "PROJECTED" if graphs else "NO_EXECUTION_RESULTS",
        "evidence_graph_count": len(graphs),
        "execution_trace_summary_count": len(summaries),
        "existing_ui_finding_count": len(ui_findings),
        "new_findings_created": 0,
        "raw_payloads_included": False,
    }
    projection_receipt["receipt_id"] = _stable_id(
        "evidence_projection_receipt",
        projection_receipt,
    )
    projected["formal_evidence_projection_receipt"] = projection_receipt
    return projected


def run_experiment_candidate(inputs: Any, campaign_handle: Any, plan: Any) -> dict[str, Any]:
    """Run the authority, then project its real receipts into formal evidence."""

    result = _run_experiment_candidate(inputs, campaign_handle, plan)
    return project_formal_evidence(result)


__all__ = [
    "project_formal_evidence",
    "run_experiment_candidate",
]
