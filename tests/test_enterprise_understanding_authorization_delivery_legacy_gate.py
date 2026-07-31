"""Legacy delivery receipts cannot publish authorization occurrences."""
from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center import formal_delivery_scope
from ai_test_asset_center.discovery_mainline_contract import MainlineContractError


def _ledger(*, risk_family: str) -> dict:
    return {
        "campaign_id": "campaign:1",
        "attempts": [
            {
                "terminal_status": "DELIVERABLE",
                "finding_id": "finding:1",
                "risk_family": risk_family,
                "gate_receipt": {
                    "schema_version": "qualibug.customer-delivery-gate-receipt.v1",
                    "status": "DELIVERABLE",
                    "finding_id": "finding:1",
                },
                "delivery_evidence_bundle": {
                    "finding": {
                        "finding_id": "finding:1",
                        "risk_family": risk_family,
                    },
                    "observer_receipts": [],
                },
            }
        ],
    }


def test_legacy_authorization_deliverable_is_rejected(monkeypatch) -> None:
    ledger = _ledger(risk_family="authorization")
    monkeypatch.setattr(
        formal_delivery_scope,
        "validate_obligation_attempt_ledger",
        lambda value: deepcopy(ledger),
    )

    with pytest.raises(
        MainlineContractError,
        match="formal_authorization_delivery_v2_required:finding:1",
    ):
        formal_delivery_scope.validated_deliverable_gate_index(ledger)


def test_legacy_non_authorization_deliverable_remains_readable(monkeypatch) -> None:
    ledger = _ledger(risk_family="validation")
    monkeypatch.setattr(
        formal_delivery_scope,
        "validate_obligation_attempt_ledger",
        lambda value: deepcopy(ledger),
    )

    index = formal_delivery_scope.validated_deliverable_gate_index(ledger)

    assert index["finding:1"]["status"] == "DELIVERABLE"
