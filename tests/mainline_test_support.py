"""Helpers for test doubles that cross the attempt-authoritative V12 boundary."""
from __future__ import annotations

import hashlib
from functools import wraps
from typing import Any

from ai_test_asset_center.customer_delivery_gate import is_customer_deliverable_defect
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.discovery_quality_projection import (
    build_formal_count_projection,
    build_formal_id_consistency,
)
from ai_test_asset_center.obligation_attempt_ledger import (
    build_obligation_attempt_ledger,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable(prefix: str, *parts: Any) -> str:
    material = "|".join(_text(value) for value in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def attach_attempt_authority(
    payload: dict[str, Any],
    campaign_context: dict[str, Any],
) -> dict[str, Any]:
    """Make a mocked V12 result satisfy the same immutable receipt contract."""

    result = dict(payload)
    campaign = result.get("campaign")
    if not isinstance(campaign, dict) or not _text(campaign.get("campaign_id")):
        raise ValueError("test V12 payload requires campaign_id")
    campaign_id = _text(campaign["campaign_id"])
    contract = build_mainline_run_contract(
        mainline_authority=_text(campaign_context.get("mainline_authority")),
        run_id=_text(campaign_context.get("run_id")),
        campaign_id=campaign_id,
        target_id=_text(campaign_context.get("target_id")),
        environment_id=_text(campaign_context.get("environment_id")),
        policy_version=_text(campaign_context.get("policy_version")),
        evaluation_mode=_text(campaign_context.get("evaluation_mode")),
    )
    fingerprint = contract["contract_fingerprint"]
    findings = [
        dict(row)
        for row in result.get("findings", [])
        if isinstance(row, dict)
    ]
    candidates = [
        dict(row)
        for row in result.get("candidate_findings", [])
        if isinstance(row, dict)
    ]
    for row in findings + candidates:
        row["mainline_run"] = {"contract_fingerprint": fingerprint}
    result["findings"] = findings
    if "candidate_findings" in result or candidates:
        result["candidate_findings"] = candidates

    formal_findings = [row for row in findings if is_customer_deliverable_defect(row)]
    selected: list[dict[str, Any]] = []
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(formal_findings):
        finding_id = _text(
            finding.get("finding_id") or finding.get("id") or finding.get("bug_id")
        )
        if not finding_id:
            raise ValueError("formal mocked finding requires finding_id")
        obligation_id = _stable("obligation", campaign_id, finding_id, index)
        experiment_id = _stable("experiment", obligation_id)
        execution_id = _stable("execution", obligation_id)
        selected.append({
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "candidate_id": _text(finding.get("candidate_id")),
        })
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "experiment_id": experiment_id,
            "cost_coverage_status": "MEASURED",
        }
        execution_results[obligation_id] = {
            "status": "EXECUTED",
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "observation_receipt_ids": [_stable("observation", execution_id)],
            "oracle_receipt_id": _stable("oracle", execution_id),
            "cost_coverage_status": "MEASURED",
        }
        gate_results[obligation_id] = {
            "status": "DELIVERABLE",
            "finding_id": finding_id,
            "gate_receipt_id": _stable("gate", execution_id),
            "cost_coverage_status": "MEASURED",
        }

    phase_execution = (
        result.get("phases", {}).get("execution", {})
        if isinstance(result.get("phases"), dict)
        else {}
    )
    if not selected and (
        _text(phase_execution.get("status")).lower() == "completed"
        and int(phase_execution.get("executed") or 0) > 0
    ):
        obligation_id = _stable("obligation", campaign_id, "no_internal_finding")
        experiment_id = _stable("experiment", obligation_id)
        execution_id = _stable("execution", obligation_id)
        selected.append({
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
        })
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "experiment_id": experiment_id,
            "cost_coverage_status": "MEASURED",
        }
        execution_results[obligation_id] = {
            "status": "EXECUTED",
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "observation_receipt_ids": [_stable("observation", execution_id)],
            "oracle_receipt_id": _stable("oracle", execution_id),
            "cost_coverage_status": "MEASURED",
        }
        gate_results[obligation_id] = {
            "status": "REJECTED",
            "reason_code": "ORACLE_NOT_VIOLATED",
            "gate_receipt_id": _stable("gate", execution_id),
            "cost_coverage_status": "MEASURED",
        }

    ledger = build_obligation_attempt_ledger(
        mainline_run=contract,
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    formal = build_formal_count_projection(
        findings=formal_findings,
        candidate_findings=candidates,
    )
    formal_ids = list(formal["formal_finding_ids"])
    result.update({
        "mainline_run": contract,
        "obligation_attempt_ledger": ledger,
        "formal_count_projection": formal,
        "formal_id_consistency": build_formal_id_consistency(
            delivery_gate_ids=formal_ids,
            formal_projection_ids=formal_ids,
            product_projection_ids=formal_ids,
        ),
    })
    return result


def authoritative_v12_double(fake):
    """Wrap a V12 test double so its output crosses the real receipt boundary."""

    @wraps(fake)
    def wrapped(*args, **kwargs):
        result = fake(*args, **kwargs)
        context = kwargs.get("campaign_context")
        if not isinstance(context, dict):
            raise ValueError("mocked V12 call requires campaign_context")
        return attach_attempt_authority(result, context)

    return wrapped
