from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

import ai_test_asset_center.customer_delivery_gate_v2 as gate
from tests import phase3_gate_support as support


_ADAPTER = "database"
_OPERATION_LOCATOR = "resource:read-resource"
_INVOCATION_OUTCOME = "completed"


def _fingerprint(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _non_http_request_semantics_fingerprint(*, phase: str) -> str:
    return _fingerprint({
        "adapter": _ADAPTER,
        "operation_ref": "read-resource",
        "operation_locator": _OPERATION_LOCATOR,
        "invocation_outcome": _INVOCATION_OUTCOME,
        "mutation_class": (
            "positive_control"
            if phase == "control"
            else "actor_relation_treatment"
        ),
        "mutation_selector": "",
        "mutation_operator": "",
        "request_body_fingerprint": "c" * 64,
    })


def _reseal_reproduction(receipt: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(receipt)
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    fingerprint = _fingerprint(unsigned)
    row["receipt_id"] = f"reproduction_{fingerprint[:32]}"
    row["receipt_fingerprint"] = fingerprint
    return row


def _build_non_http_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    original_builder = gate.build_reproduction_receipt

    def build_non_http_reproduction_receipt(
        *,
        execution_receipt: dict[str, Any],
        steps: list[dict[str, Any]],
        oracle_receipt: dict[str, Any],
        source_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        non_http_steps = []
        for raw_step in steps:
            step = dict(raw_step)
            step.update({
                "adapter": _ADAPTER,
                "operation_locator": _OPERATION_LOCATOR,
                "invocation_outcome": _INVOCATION_OUTCOME,
            })
            non_http_steps.append(step)
        return original_builder(
            execution_receipt=execution_receipt,
            steps=non_http_steps,
            oracle_receipt=oracle_receipt,
            source_refs=source_refs,
        )

    monkeypatch.setattr(
        support,
        "_request_semantics_fingerprint",
        _non_http_request_semantics_fingerprint,
    )
    monkeypatch.setattr(
        support,
        "build_reproduction_receipt",
        build_non_http_reproduction_receipt,
    )
    findings, ledger = support.build_formal_evaluation_scope(
        [{"id": "finding-non-http-reproduction"}],
        run_id="run-non-http-reproduction",
        campaign_id="campaign-non-http-reproduction",
        target_id="target-non-http-reproduction",
        environment_id="environment-test",
        policy_version="policy-test",
        evaluation_mode="operational",
    )
    assert ledger is not None
    return findings, ledger


def test_non_http_reproduction_reaches_delivery_gate_and_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings, ledger = _build_non_http_scope(monkeypatch)

    assert len(findings) == 1
    attempt = ledger["attempts"][0]
    assert attempt["terminal_status"] == "DELIVERABLE"
    bundle = attempt["delivery_evidence_bundle"]
    reproduction = bundle["reproduction_receipt"]

    validated = gate.validate_reproduction_receipt(reproduction)
    assert {
        step["adapter"]
        for step in validated["step_observations"]
    } == {_ADAPTER}
    assert {
        step["operation_locator"]
        for step in validated["step_observations"]
    } == {_OPERATION_LOCATOR}
    assert {
        step["invocation_outcome"]
        for step in validated["step_observations"]
    } == {_INVOCATION_OUTCOME}

    validated_gate = gate.validate_customer_delivery_gate_bundle(
        attempt["gate_receipt"],
        **bundle,
    )
    assert validated_gate["status"] == "DELIVERABLE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_locator", "resource:tampered"),
        ("invocation_outcome", "tampered"),
        ("request_semantics_fingerprint", "0" * 64),
    ],
)
def test_non_http_reproduction_semantics_tampering_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    _, ledger = _build_non_http_scope(monkeypatch)
    reproduction = copy.deepcopy(
        ledger["attempts"][0]["delivery_evidence_bundle"][
            "reproduction_receipt"
        ]
    )
    reproduction["step_observations"][0][field] = value
    reproduction = _reseal_reproduction(reproduction)

    with pytest.raises(
        gate.DeliveryGateV2Error,
        match="reproduction_request_semantics_fingerprint_invalid",
    ):
        gate.validate_reproduction_receipt(reproduction)


def test_http_reproduction_cannot_claim_non_http_schema() -> None:
    findings, ledger = support.build_formal_evaluation_scope(
        [{"id": "finding-http-reproduction"}],
        run_id="run-http-reproduction",
        campaign_id="campaign-http-reproduction",
        target_id="target-http-reproduction",
        environment_id="environment-test",
        policy_version="policy-test",
        evaluation_mode="operational",
    )
    assert findings
    assert ledger is not None
    reproduction = copy.deepcopy(
        ledger["attempts"][0]["delivery_evidence_bundle"][
            "reproduction_receipt"
        ]
    )
    reproduction["step_observations"][0].update({
        "adapter": "http_api",
        "operation_locator": "/resources/{resourceId}",
        "invocation_outcome": "completed",
    })
    reproduction = _reseal_reproduction(reproduction)

    with pytest.raises(
        gate.DeliveryGateV2Error,
        match="reproduction_request_semantics_invalid",
    ):
        gate.validate_reproduction_receipt(reproduction)
