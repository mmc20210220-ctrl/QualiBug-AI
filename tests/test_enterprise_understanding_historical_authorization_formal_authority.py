"""Formal authority receipts contain only non-quarantined occurrences."""
from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import formal_delivery_authority as authority
from ai_test_asset_center.formal_delivery_authority import (
    build_formal_delivery_authority_receipt,
    validate_formal_delivery_authority_receipt,
)


def _mainline() -> dict:
    return {
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "target_id": "target:1",
        "environment_id": "environment:1",
        "policy_version": "policy:1",
        "evaluation_mode": "formal",
        "contract_fingerprint": "m" * 64,
    }


def _legacy_attempt(
    *,
    finding_id: str,
    risk_family: str,
    obligation_id: str,
) -> dict:
    return {
        "terminal_status": "DELIVERABLE",
        "finding_id": finding_id,
        "risk_family": risk_family,
        "obligation_id": obligation_id,
        "executed_obligation_id": obligation_id,
        "experiment_id": "exp:" + finding_id,
        "execution_id": "exec:" + finding_id,
        "attempt_fingerprint": ("a" if risk_family == "authorization" else "v") * 64,
        "gate_receipt": {
            "schema_version": "qualibug.customer-delivery-gate-receipt.v1",
            "status": "DELIVERABLE",
            "finding_id": finding_id,
            "receipt_id": "legacy:" + finding_id,
            "gate_receipt_id": "gate:" + finding_id,
            "output_fingerprint": ("g" if risk_family == "authorization" else "h") * 64,
            "finding_payload_fingerprint": ("p" if risk_family == "authorization" else "q") * 64,
        },
        "delivery_evidence_bundle": {
            "finding": {
                "finding_id": finding_id,
                "risk_family": risk_family,
            },
            "observer_receipts": [],
        },
    }


def test_formal_authority_omits_quarantined_authorization_attempt(
    monkeypatch,
) -> None:
    mainline = _mainline()
    valid_finding = {
        "finding_id": "finding:validation",
        "risk_family": "validation",
    }
    ledger = {
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "mainline_contract_fingerprint": mainline["contract_fingerprint"],
        "ledger_fingerprint": "l" * 64,
        "attempts": [
            _legacy_attempt(
                finding_id="finding:auth",
                risk_family="authorization",
                obligation_id="obl:auth",
            ),
            _legacy_attempt(
                finding_id="finding:validation",
                risk_family="validation",
                obligation_id="obl:validation",
            ),
        ],
    }
    monkeypatch.setattr(
        authority,
        "validate_mainline_run_contract",
        lambda value: deepcopy(mainline),
    )
    monkeypatch.setattr(
        authority,
        "validate_obligation_attempt_ledger",
        lambda value: deepcopy(ledger),
    )
    monkeypatch.setattr(
        authority,
        "formal_customer_deliverable_findings",
        lambda findings, obligation_attempt_ledger=None: [deepcopy(valid_finding)],
    )

    receipt = build_formal_delivery_authority_receipt(
        mainline_run=mainline,
        findings=[
            {"finding_id": "finding:auth", "risk_family": "authorization"},
            valid_finding,
        ],
        obligation_attempt_ledger=ledger,
    )

    assert receipt["delivery_occurrence_finding_ids"] == [
        "finding:validation"
    ]
    assert receipt["delivery_occurrence_count"] == 1
    assert len(receipt["deliverable_attempts"]) == 1
    assert receipt["deliverable_attempts"][0]["finding_id"] == (
        "finding:validation"
    )
    assert validate_formal_delivery_authority_receipt(receipt) == receipt
