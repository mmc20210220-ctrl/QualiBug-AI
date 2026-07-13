"""Helpers for test doubles that cross the attempt-authoritative V12 boundary."""
from __future__ import annotations

from functools import wraps
from typing import Any

from ai_test_asset_center.customer_delivery_gate import is_customer_deliverable_defect
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    formal_occurrences, ledger = build_formal_evaluation_scope(
        formal_findings,
        run_id=contract["run_id"],
        campaign_id=contract["campaign_id"],
        target_id=contract["target_id"],
        environment_id=contract["environment_id"],
        policy_version=contract["policy_version"],
        evaluation_mode=contract["evaluation_mode"],
        mainline_authority=contract["mainline_authority"],
    )
    formal_scope = build_formal_scope_contract(
        mainline_run=contract,
        findings=formal_occurrences,
        obligation_attempt_ledger=ledger,
    )
    canonical_findings = list(
        formal_scope["formal_count_projection"][
            "canonical_representative_findings"
        ]
    )
    non_formal = [
        row for row in findings if not is_customer_deliverable_defect(row)
    ]
    result.update({
        "mainline_run": contract,
        "obligation_attempt_ledger": ledger,
        "findings": (
            canonical_findings
            if contract["customer_outputs_published"]
            else []
        ),
        "evaluator_canonical_findings": (
            canonical_findings
            if contract["private_evaluator_observation_allowed"]
            else []
        ),
        "candidate_findings": [*candidates, *non_formal],
        **formal_scope,
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
