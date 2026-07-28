"""Projection layer for formal UI contract outcomes.

The Delivery Gate seals UI findings in ``ui_formal_surface``. This module does not
re-adjudicate or mutate that sealed payload. It projects only receipt identities and
statuses into the product evidence graph and adds a separate UI conversion extension to
the discovery loss funnel.

Raw screenshots, console text, network URLs/bodies and provider clues are intentionally
absent from every projection here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .ui_formal_runtime import formalize_browser_ui_contracts_strict

SCHEMA_VERSION = "qualibug.formal-ui-projection.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt_node(receipt: dict[str, Any], kind: str) -> dict[str, Any] | None:
    row = _dict(receipt)
    receipt_id = _text(
        row.get("receipt_id")
        or row.get("gate_receipt_id")
        or row.get("reproduction_receipt_id")
    )
    if not receipt_id:
        return None
    return {
        "node_id": receipt_id,
        "node_type": kind,
        "schema_version": _text(row.get("schema_version")),
        "status": _text(row.get("status") or row.get("verdict")),
        "reason_code": _text(row.get("reason_code")),
    }


def project_ui_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Append sanitized graph and trace rows for formal UI outcomes."""
    if not isinstance(result, dict):
        raise TypeError("formal_ui_result_not_object")
    updated = dict(result)
    formal = _dict(updated.get("formal_ui_contracts"))
    outcomes = [dict(row) for row in _list(formal.get("outcomes")) if isinstance(row, dict)]

    graphs: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for outcome in outcomes:
        contract_id = _text(outcome.get("contract_id"))
        observer_receipts = [
            dict(row)
            for row in _list(outcome.get("observer_receipts"))
            if isinstance(row, dict)
        ]
        oracle = _dict(outcome.get("oracle_receipt"))
        reproduction = _dict(outcome.get("reproduction_receipt"))
        gate = _dict(outcome.get("delivery_gate_receipt"))
        finding = _dict(outcome.get("finding"))

        nodes: list[dict[str, Any]] = []
        for receipt in observer_receipts:
            node = _receipt_node(receipt, "observer_receipt")
            if node:
                node["observer_id"] = _text(receipt.get("observer_id"))
                nodes.append(node)
        for receipt, kind in (
            (oracle, "oracle_receipt"),
            (reproduction, "reproduction_receipt"),
            (gate, "delivery_gate_receipt"),
        ):
            node = _receipt_node(receipt, kind)
            if node:
                nodes.append(node)
        finding_id = _text(finding.get("finding_id") or finding.get("id"))
        if finding_id:
            nodes.append({
                "node_id": finding_id,
                "node_type": "formal_finding",
                "status": _text(finding.get("customer_delivery_status") or "defect"),
                "surface": "UI",
            })

        node_ids = [row["node_id"] for row in nodes if _text(row.get("node_id"))]
        edges = [
            {
                "from": node_ids[index],
                "to": node_ids[index + 1],
                "relation": "supports",
            }
            for index in range(max(0, len(node_ids) - 1))
        ]
        if nodes:
            graph_id = "ui_graph_" + _fingerprint({
                "contract_id": contract_id,
                "nodes": node_ids,
            })[:24]
            graphs.append({
                "schema_version": "qualibug.formal-ui-evidence-graph.v1",
                "graph_id": graph_id,
                "surface": "UI",
                "contract_id": contract_id,
                "outcome_status": _text(outcome.get("status")),
                "reason_codes": [
                    _text(value) for value in _list(outcome.get("reason_codes")) if _text(value)
                ],
                "nodes": nodes,
                "edges": edges,
                "coverage": {
                    "observer_receipt_count": len(observer_receipts),
                    "oracle_present": bool(oracle),
                    "reproduction_present": bool(reproduction),
                    "delivery_gate_present": bool(gate),
                    "formal_finding_present": bool(finding_id),
                },
                "raw_payloads_included": False,
            })

        traces.append({
            "schema_version": "qualibug.formal-ui-trace-summary.v1",
            "contract_id": contract_id,
            "status": _text(outcome.get("status")),
            "reason_codes": [
                _text(value) for value in _list(outcome.get("reason_codes")) if _text(value)
            ],
            "observer_receipt_ids": [
                _text(row.get("receipt_id"))
                for row in observer_receipts
                if _text(row.get("receipt_id"))
            ],
            "oracle_receipt_id": _text(oracle.get("receipt_id")),
            "reproduction_receipt_id": _text(reproduction.get("receipt_id")),
            "delivery_gate_receipt_id": _text(
                gate.get("gate_receipt_id") or gate.get("receipt_id")
            ),
            "finding_id": finding_id,
            "raw_payloads_included": False,
        })

    existing_graphs = [
        dict(row) for row in _list(updated.get("evidence_graphs")) if isinstance(row, dict)
    ]
    known_graph_ids = {_text(row.get("graph_id")) for row in existing_graphs}
    updated["evidence_graphs"] = [
        *existing_graphs,
        *[row for row in graphs if _text(row.get("graph_id")) not in known_graph_ids],
    ]

    existing_traces = [
        dict(row)
        for row in _list(updated.get("execution_trace_summaries"))
        if isinstance(row, dict)
    ]
    known_trace_contracts = {
        _text(row.get("contract_id"))
        for row in existing_traces
        if _text(row.get("schema_version")) == "qualibug.formal-ui-trace-summary.v1"
    }
    updated["execution_trace_summaries"] = [
        *existing_traces,
        *[row for row in traces if _text(row.get("contract_id")) not in known_trace_contracts],
    ]
    updated["formal_ui_projection_receipt"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PROJECTED" if outcomes else "NOT_REQUESTED",
        "outcome_count": len(outcomes),
        "evidence_graph_count": len(graphs),
        "trace_summary_count": len(traces),
        "new_findings_created": 0,
        "raw_payloads_included": False,
    }
    return updated


def _ui_funnel_extension(result: dict[str, Any]) -> dict[str, Any]:
    formal = _dict(result.get("formal_ui_contracts"))
    outcomes = [dict(row) for row in _list(formal.get("outcomes")) if isinstance(row, dict)]
    observed = 0
    oracle_evaluated = 0
    reproduced = 0
    gated = 0
    reason_counts: dict[str, int] = {}
    finding_ids: set[str] = set()
    for outcome in outcomes:
        receipts = [
            _dict(row) for row in _list(outcome.get("observer_receipts")) if isinstance(row, dict)
        ]
        if any(_text(row.get("status")).upper() == "OBSERVED" for row in receipts):
            observed += 1
        if _dict(outcome.get("oracle_receipt")):
            oracle_evaluated += 1
        if _dict(outcome.get("reproduction_receipt")):
            reproduced += 1
        if _dict(outcome.get("delivery_gate_receipt")):
            gated += 1
        for reason in _list(outcome.get("reason_codes")):
            token = _text(reason)
            if token:
                reason_counts[token] = reason_counts.get(token, 0) + 1
        finding_id = _text(_dict(outcome.get("finding")).get("finding_id"))
        if finding_id:
            finding_ids.add(finding_id)
    return {
        "surface": "UI",
        "unit": "ui_contract",
        "requested": int(formal.get("requested") or 0),
        "evaluated": int(formal.get("evaluated") or len(outcomes)),
        "observed": observed,
        "oracle_evaluated": oracle_evaluated,
        "reproduction_ready": reproduced,
        "delivery_gate_evaluated": gated,
        "deliverable": int(formal.get("deliverable_count") or 0),
        "canonical_surface_defect_count": len(finding_ids),
        "blocked": int(formal.get("blocked_count") or 0),
        "rejected": int(formal.get("rejected_count") or 0),
        "reason_counts": dict(sorted(reason_counts.items())),
        "provider_findings_promoted": int(formal.get("provider_findings_promoted") or 0),
        "ground_truth_quality_status": "NOT_MEASURED",
    }


def refresh_loss_funnel_with_ui(result: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the base funnel and append the separate UI-contract conversion unit."""
    from .discovery_loss_funnel import build_discovery_loss_funnel

    updated = dict(result)
    funnel = build_discovery_loss_funnel(updated)
    extensions = _dict(funnel.get("surface_extensions"))
    extensions["UI"] = _ui_funnel_extension(updated)
    funnel["surface_extensions"] = extensions
    # Reseal after adding the extension. It intentionally remains separate from the
    # obligation stages because a UI contract is not an obligation-ledger row yet.
    payload = {key: value for key, value in funnel.items() if key != "funnel_fingerprint"}
    funnel["funnel_fingerprint"] = _fingerprint(payload)
    updated["discovery_loss_funnel"] = funnel
    return updated


def formalize_browser_ui_contracts_projected(
    result: dict[str, Any],
    *,
    browser_ui_report: dict[str, Any],
    contracts: Any,
    runtime_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adjudicate formal UI contracts, project receipts, then refresh final metrics."""
    updated = formalize_browser_ui_contracts_strict(
        result,
        browser_ui_report=browser_ui_report,
        contracts=contracts,
        runtime_contract=runtime_contract,
    )
    updated = project_ui_evidence(updated)
    return refresh_loss_funnel_with_ui(updated)


__all__ = [
    "formalize_browser_ui_contracts_projected",
    "project_ui_evidence",
    "refresh_loss_funnel_with_ui",
]
