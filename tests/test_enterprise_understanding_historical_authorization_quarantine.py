"""Historical authorization artifacts are quarantined without weakening proof."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from ai_test_asset_center import historical_authorization_quarantine as quarantine
from ai_test_asset_center.historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
    authorization_attempt_requires_causal_delivery,
    build_historical_authorization_quarantine_projection,
    classify_historical_authorization_attempt,
    validate_historical_authorization_quarantine_projection,
    validate_historical_authorization_quarantine_receipt,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _legacy_attempt(
    *,
    finding_id: str = "finding:auth",
    risk_family: str = "authorization",
    finding_risk_family: str | None = None,
) -> dict:
    finding_family = risk_family if finding_risk_family is None else finding_risk_family
    return {
        "terminal_status": "DELIVERABLE",
        "finding_id": finding_id,
        "obligation_id": "obl:auth",
        "executed_obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
        "execution_id": "exec:auth",
        "risk_family": risk_family,
        "attempt_fingerprint": "a" * 64,
        "gate_receipt": {
            "schema_version": "qualibug.customer-delivery-gate-receipt.v1",
            "status": "DELIVERABLE",
            "finding_id": finding_id,
            "receipt_id": "legacy_gate:auth",
        },
        "delivery_evidence_bundle": {
            "finding": {
                "finding_id": finding_id,
                "risk_family": finding_family,
            },
            "observer_receipts": [],
        },
    }


def _causality_receipt(*, receipt_id_override: str = "") -> dict:
    payload = {
        "schema_version": "qualibug.authorization-oracle-causality-receipt.v1",
        "status": "PASSED",
        "experiment_id": "exp:auth",
        "obligation_id": "obl:auth",
        "campaign_id": "campaign:1",
        "execution_id": "exec:auth",
        "reason_codes": [],
        "comparison_dimension": "ROLE_PERMISSION",
        "comparison_contract_fingerprint": "1" * 64,
        "compile_binding_graph_fingerprint": "2" * 64,
        "runtime_resource_identity_fingerprint": "3" * 64,
        "control_target_reached": True,
        "treatment_target_reached": True,
        "single_identity_dimension_proven": True,
        "same_resource_proven": True,
        "verified_receipt_ids": [
            "binding:order",
            "contract:control",
            "contract:treatment",
            "observer:authorization",
        ],
    }
    expected = "auth_causality_" + hashlib.sha256(
        _canonical(payload).encode("utf-8")
    ).hexdigest()[:24]
    return {**payload, "receipt_id": receipt_id_override or expected}


def _early_v2_attempt(*, causal_receipt: dict | None = None) -> dict:
    finding = {
        "finding_id": "finding:auth",
        "risk_family": "authorization",
        "campaign_id": "campaign:1",
        "obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
        "execution_id": "exec:auth",
    }
    if causal_receipt is not None:
        finding["authorization_causality_receipt"] = causal_receipt
    return {
        "terminal_status": "DELIVERABLE",
        "finding_id": "finding:auth",
        "obligation_id": "obl:auth",
        "executed_obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
        "execution_id": "exec:auth",
        "risk_family": "authorization",
        "attempt_fingerprint": "b" * 64,
        "gate_receipt": {
            "schema_version": "qualibug.customer-delivery-gate-receipt.v2",
            "status": "DELIVERABLE",
            "gate_receipt_id": "gate:auth",
        },
        "delivery_evidence_bundle": {
            "finding": finding,
            "observer_receipts": [],
            "contract_evidence_receipts": [],
            "reproduction_receipt": {},
        },
    }


def test_legacy_authorization_receipt_is_deterministic_and_auditable() -> None:
    attempt = _legacy_attempt()

    first = classify_historical_authorization_attempt(
        attempt,
        run_id="run:1",
        campaign_id="campaign:1",
    )
    second = classify_historical_authorization_attempt(
        deepcopy(attempt),
        run_id="run:1",
        campaign_id="campaign:1",
    )

    assert first == second
    assert first["status"] == "QUARANTINED"
    assert first["reason_code"] == "UNVERIFIABLE_LEGACY_AUTHORIZATION"
    assert first["reason_detail"] == "LEGACY_AUTHORIZATION_GATE_V1_NO_CAUSAL_RECEIPT"
    assert first["rerun_status"] == "RERUN_REQUIRED"
    assert validate_historical_authorization_quarantine_receipt(first) == first
    assert "request" not in _canonical(first).lower()
    assert "response" not in _canonical(first).lower()


def test_non_authorization_legacy_attempt_is_not_quarantined() -> None:
    attempt = _legacy_attempt(risk_family="validation")

    assert authorization_attempt_requires_causal_delivery(attempt) is False
    assert classify_historical_authorization_attempt(
        attempt,
        run_id="run:1",
        campaign_id="campaign:1",
    ) == {}


def test_finding_family_detects_authorization_when_attempt_family_is_missing() -> None:
    attempt = _legacy_attempt(
        risk_family="",
        finding_risk_family="authorization",
    )

    assert authorization_attempt_requires_causal_delivery(attempt) is True
    assert classify_historical_authorization_attempt(
        attempt,
        run_id="run:1",
        campaign_id="campaign:1",
    )["status"] == "QUARANTINED"


def test_early_gate_v2_without_causal_receipt_is_quarantined() -> None:
    receipt = classify_historical_authorization_attempt(
        _early_v2_attempt(),
        run_id="run:1",
        campaign_id="campaign:1",
    )

    assert receipt["status"] == "QUARANTINED"
    assert receipt["reason_detail"] == "authorization_causality_receipt_fields_invalid"


def test_present_but_tampered_causal_receipt_is_a_hard_contradiction() -> None:
    attempt = _early_v2_attempt(
        causal_receipt=_causality_receipt(receipt_id_override="auth_causality_tampered")
    )

    with pytest.raises(
        HistoricalAuthorizationQuarantineError,
        match="historical_authorization_contradiction:authorization_causality_receipt_fingerprint_invalid",
    ):
        classify_historical_authorization_attempt(
            attempt,
            run_id="run:1",
            campaign_id="campaign:1",
        )


def test_projection_preserves_ledger_and_builds_minimal_rerun_queue(
    monkeypatch,
) -> None:
    ledger = {
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "ledger_fingerprint": "f" * 64,
        "attempts": [
            _legacy_attempt(),
            _legacy_attempt(
                finding_id="finding:validation",
                risk_family="validation",
            ),
        ],
    }
    snapshot = deepcopy(ledger)
    monkeypatch.setattr(
        quarantine,
        "validate_obligation_attempt_ledger",
        lambda value: deepcopy(ledger),
    )

    projection = build_historical_authorization_quarantine_projection(
        ledger,
        superseded_registry_fingerprint="c" * 64,
    )

    assert ledger == snapshot
    assert projection["status"] == "QUARANTINED"
    assert projection["quarantine_count"] == 1
    assert projection["quarantined_finding_ids"] == ["finding:auth"]
    assert projection["rerun_required_count"] == 1
    assert projection["rerun_queue"] == [
        {
            "finding_id": "finding:auth",
            "obligation_id": "obl:auth",
            "experiment_id": "exp:auth",
            "action": "RERUN_REQUIRED",
            "requirements": [
                "authorization_comparison_contract_v1",
                "authorization_causality_receipt_v1",
                "binding_materialization_identity_receipt_v1",
                "customer_delivery_gate_v2",
            ],
            "quarantine_receipt_id": projection["quarantine_receipts"][0][
                "receipt_id"
            ],
        }
    ]
    assert validate_historical_authorization_quarantine_projection(
        projection
    ) == projection
