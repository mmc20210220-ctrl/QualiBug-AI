"""Legacy authorization delivery is quarantined, not published or deleted."""
from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import formal_delivery_scope


def _ledger(
    *,
    attempt_risk_family: str,
    finding_risk_family: str | None = None,
) -> dict:
    finding_family = (
        attempt_risk_family
        if finding_risk_family is None
        else finding_risk_family
    )
    return {
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "attempts": [
            {
                "terminal_status": "DELIVERABLE",
                "finding_id": "finding:1",
                "risk_family": attempt_risk_family,
                "attempt_fingerprint": "attempt-fingerprint",
                "gate_receipt": {
                    "schema_version": "qualibug.customer-delivery-gate-receipt.v1",
                    "status": "DELIVERABLE",
                    "finding_id": "finding:1",
                    "receipt_id": "legacy-gate:1",
                },
                "delivery_evidence_bundle": {
                    "finding": {
                        "finding_id": "finding:1",
                        "risk_family": finding_family,
                    },
                    "observer_receipts": [],
                },
            }
        ],
    }


def _install_ledger(monkeypatch, ledger: dict) -> None:
    monkeypatch.setattr(
        formal_delivery_scope,
        "validate_obligation_attempt_ledger",
        lambda value: deepcopy(ledger),
    )


def test_legacy_authorization_deliverable_is_quarantined(monkeypatch) -> None:
    ledger = _ledger(attempt_risk_family="authorization")
    _install_ledger(monkeypatch, ledger)

    assert formal_delivery_scope.validated_deliverable_gate_index(ledger) == {}


def test_legacy_authorization_finding_is_quarantined_when_attempt_family_missing(
    monkeypatch,
) -> None:
    ledger = _ledger(
        attempt_risk_family="",
        finding_risk_family="authorization",
    )
    _install_ledger(monkeypatch, ledger)

    assert formal_delivery_scope.validated_deliverable_gate_index(ledger) == {}


def test_legacy_non_authorization_deliverable_remains_readable(monkeypatch) -> None:
    ledger = _ledger(attempt_risk_family="validation")
    _install_ledger(monkeypatch, ledger)

    index = formal_delivery_scope.validated_deliverable_gate_index(ledger)

    assert index["finding:1"]["status"] == "DELIVERABLE"


def test_mixed_legacy_ledger_quarantines_only_authorization(monkeypatch) -> None:
    authorization = _ledger(attempt_risk_family="authorization")["attempts"][0]
    validation = deepcopy(
        _ledger(attempt_risk_family="validation")["attempts"][0]
    )
    validation["finding_id"] = "finding:validation"
    validation["attempt_fingerprint"] = "attempt-validation"
    validation["gate_receipt"]["finding_id"] = "finding:validation"
    validation["gate_receipt"]["receipt_id"] = "legacy-gate:validation"
    validation["delivery_evidence_bundle"]["finding"][
        "finding_id"
    ] = "finding:validation"
    ledger = {
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "attempts": [authorization, validation],
    }
    _install_ledger(monkeypatch, ledger)

    index = formal_delivery_scope.validated_deliverable_gate_index(ledger)

    assert set(index) == {"finding:validation"}
    assert index["finding:validation"]["status"] == "DELIVERABLE"
