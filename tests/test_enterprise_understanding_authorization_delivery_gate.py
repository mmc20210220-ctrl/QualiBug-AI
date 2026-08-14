"""Formal authorization delivery requires sealed causality and same-resource replay."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from ai_test_asset_center.authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
    attach_authorization_delivery_evidence,
    validate_authorization_causality_receipt,
    validate_authorization_delivery_finding,
)
from ai_test_asset_center.authorization_oracle_causality import SCHEMA_VERSION
from ai_test_asset_center.customer_delivery_gate_v2 import REPRODUCTION_RECEIPT_SCHEMA
from ai_test_asset_center.discovery_mainline_contract import MainlineContractError
from ai_test_asset_center import formal_delivery_scope


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _causality_receipt() -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASSED",
        "experiment_id": "exp:auth",
        "obligation_id": "obl:auth",
        "campaign_id": "campaign:1",
        "execution_id": "execution:1",
        "reason_codes": [],
        "comparison_dimension": "ROLE_PERMISSION",
        "comparison_contract_fingerprint": _sha({"comparison": "contract"}),
        "compile_binding_graph_fingerprint": _sha({"binding": "graph"}),
        "runtime_resource_identity_fingerprint": _sha(
            {"order_id": "order-42-fingerprint"}
        ),
        "control_target_reached": True,
        "treatment_target_reached": True,
        "single_identity_dimension_proven": True,
        "same_resource_proven": True,
        "verified_receipt_ids": sorted(
            [
                "binding:order-id",
                "contract:control",
                "contract:treatment",
                "observer:authorization",
            ]
        ),
    }
    return {
        **payload,
        "receipt_id": "auth_causality_" + hashlib.sha256(
            _canonical(payload).encode("utf-8")
        ).hexdigest()[:24],
    }


def _step(phase: str, *, path: str = "/orders/order-42") -> dict:
    body_fingerprint = _sha(None)
    semantics = _sha(
        {
            "operation_ref": "op:get-order",
            "method": "GET",
            "path_template": "/orders/{order_id}",
            "mutation_class": f"{phase}_request",
            "mutation_selector": "",
            "mutation_operator": "",
            "request_body_fingerprint": body_fingerprint,
        }
    )
    return {
        "phase": phase,
        "step_id": f"{phase}_1",
        "actor_ref": f"actor:{phase}",
        "operation_ref": "op:get-order",
        "method": "GET",
        "path": path,
        "path_template": "/orders/{order_id}",
        "status_code": 200,
        "observation_receipt_id": f"observation:{phase}",
        "request_body_fingerprint": body_fingerprint,
        "request_semantics_fingerprint": semantics,
        "mutation_class": f"{phase}_request",
        "mutation_selector": "",
        "mutation_operator": "",
        "response_fingerprint": _sha({"id": "order-42"}),
    }


def _reproduction(*, treatment_path: str = "/orders/order-42") -> dict:
    payload = {
        "schema_version": REPRODUCTION_RECEIPT_SCHEMA,
        "campaign_id": "campaign:1",
        "obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
        "execution_id": "execution:1",
        "evidence_id": "evidence:1",
        "status": "REPRODUCED",
        "reason_code": "",
        "oracle_receipt_id": "oracle:1",
        "step_observations": [
            _step("control"),
            _step("treatment", path=treatment_path),
        ],
        "source_refs": [{"kind": "requirement", "locator": "REQ-AUTH-1"}],
    }
    fingerprint = _sha(payload)
    return {
        **payload,
        "receipt_id": "reproduction_" + fingerprint[:32],
        "receipt_fingerprint": fingerprint,
    }


def _finding(*, include_receipt: bool = True) -> dict:
    receipt = _causality_receipt()
    finding = {
        "finding_id": "finding:auth",
        "id": "finding:auth",
        "risk_family": "authorization",
        "campaign_id": "campaign:1",
        "obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
        "execution_id": "execution:1",
        "oracle": {
            "authorization_causality_receipt_id": receipt["receipt_id"],
            "authorization_causality_proven": True,
        },
        "evidence": {
            "authorization_causality_receipt_id": receipt["receipt_id"],
            "runtime_resource_identity_fingerprint": receipt[
                "runtime_resource_identity_fingerprint"
            ],
            "single_identity_dimension_proven": True,
            "same_resource_proven": True,
        },
        "authorization_causality_binding_proofs": [
            {
                "receipt_id": "binding:order-id",
                "target": "order_id",
                "status": "BOUND",
                "value_fingerprint": "order-42-fingerprint",
            }
        ],
    }
    if include_receipt:
        finding["authorization_causality_receipt"] = receipt
    return finding


def _attempt(finding: dict, *, reproduction: dict | None = None) -> dict:
    return {
        "risk_family": "authorization",
        "executed_obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
        "execution_id": "execution:1",
        "delivery_evidence_bundle": {
            "finding": deepcopy(finding),
            "contract_evidence_receipts": [
                {
                    "kind": "control",
                    "receipt_id": "contract:control",
                },
                {
                    "kind": "treatment",
                    "receipt_id": "contract:treatment",
                },
            ],
            "observer_receipts": [
                {
                    "observer_id": "authorization_comparison",
                    "receipt_id": "observer:authorization",
                }
            ],
            "reproduction_receipt": reproduction or _reproduction(),
        },
    }


def test_complete_authorization_delivery_chain_passes() -> None:
    finding = _finding()

    receipt = validate_authorization_delivery_finding(
        finding,
        attempt=_attempt(finding),
        campaign_id="campaign:1",
    )

    assert receipt["status"] == "PASSED"
    assert receipt["same_resource_proven"] is True


def test_missing_full_causality_receipt_blocks_publication() -> None:
    finding = _finding(include_receipt=False)

    with pytest.raises(
        AuthorizationDeliveryGateError,
        match="authorization_causality_receipt_fields_invalid",
    ):
        validate_authorization_delivery_finding(
            finding,
            attempt=_attempt(finding),
            campaign_id="campaign:1",
        )


def test_binding_fingerprint_substitution_blocks_publication() -> None:
    finding = _finding()
    finding["authorization_causality_binding_proofs"][0][
        "value_fingerprint"
    ] = "other-order-fingerprint"

    with pytest.raises(
        AuthorizationDeliveryGateError,
        match="authorization_delivery_binding_fingerprint_mismatch",
    ):
        validate_authorization_delivery_finding(
            finding,
            attempt=_attempt(finding),
            campaign_id="campaign:1",
        )


def test_replay_against_different_resource_blocks_publication() -> None:
    finding = _finding()

    with pytest.raises(
        AuthorizationDeliveryGateError,
        match="authorization_delivery_replay_resource_identity_mismatch",
    ):
        validate_authorization_delivery_finding(
            finding,
            attempt=_attempt(
                finding,
                reproduction=_reproduction(treatment_path="/orders/order-99"),
            ),
            campaign_id="campaign:1",
        )


def test_replay_allows_declared_query_selector_on_same_route() -> None:
    finding = _finding()

    receipt = validate_authorization_delivery_finding(
        finding,
        attempt=_attempt(
            finding,
            reproduction=_reproduction(
                treatment_path="/orders/order-42?owner_id=peer"
            ),
        ),
        campaign_id="campaign:1",
    )

    assert receipt["status"] == "PASSED"


def test_foreign_execution_lineage_blocks_publication() -> None:
    finding = _finding()
    finding["authorization_causality_receipt"]["execution_id"] = "execution:other"

    with pytest.raises(
        AuthorizationDeliveryGateError,
        match="authorization_causality_receipt_fingerprint_invalid",
    ):
        validate_authorization_delivery_finding(
            finding,
            attempt=_attempt(finding),
            campaign_id="campaign:1",
        )


def test_missing_verified_observer_reference_blocks_publication() -> None:
    finding = _finding()
    receipt = finding["authorization_causality_receipt"]
    receipt["verified_receipt_ids"].remove("observer:authorization")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_id"}
    receipt["receipt_id"] = "auth_causality_" + hashlib.sha256(
        _canonical(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    finding["oracle"]["authorization_causality_receipt_id"] = receipt["receipt_id"]
    finding["evidence"]["authorization_causality_receipt_id"] = receipt["receipt_id"]

    with pytest.raises(
        AuthorizationDeliveryGateError,
        match="authorization_delivery_verified_receipt_set_incomplete",
    ):
        validate_authorization_delivery_finding(
            finding,
            attempt=_attempt(finding),
            campaign_id="campaign:1",
        )


def test_execution_packaging_embeds_receipt_and_exact_binding_proof() -> None:
    from ai_test_asset_center.binding_materialization_identity_receipt import (
        seal_binding_materialization_receipts,
    )
    from ai_test_asset_center.authorization_oracle_causality import (
        _binding_parent_provenance,
        _binding_proof,
    )

    # Runtime shape: _finalize_result seals materialization receipts before the
    # causal receipt is built, so attach_authorization_delivery_evidence reads
    # the sealed rows (materialization_identity_receipt + provenance fields).
    sealed = seal_binding_materialization_receipts({
        "binding_materialization_receipts": [
            {
                "target": "order_id",
                "source_priority": "observed_reuse_priority",
                "status": "bound",
                "value_fingerprint": "order-42-fingerprint",
                "resolver_path": "/orders/order-42",
            },
            {
                "target": "customer_id",
                "source_priority": "observed_reuse_priority",
                "status": "bound",
                "value_fingerprint": "customer-7-fingerprint",
                "resolver_path": "/customers/customer-7",
            },
        ],
    })["binding_materialization_receipts"]

    contract = {"resource_identity_binding_targets": ["order_id"]}
    # The causal receipt seals the provenance-aware fingerprint of the selected
    # target only; the unrelated customer_id binding never enters the proof.
    causal_fingerprint, _receipt_ids, _reasons = _binding_proof(contract, sealed)
    receipt = _causality_receipt()
    receipt["runtime_resource_identity_fingerprint"] = causal_fingerprint
    # Re-seal the receipt id so the content address still matches the mutated
    # runtime fingerprint.
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_id"}
    receipt["receipt_id"] = "auth_causality_" + hashlib.sha256(
        _canonical(unsigned).encode("utf-8")
    ).hexdigest()[:24]

    finding = _finding()
    finding["authorization_causality_receipt"] = receipt
    finding["oracle"]["authorization_causality_receipt_id"] = receipt["receipt_id"]
    finding["evidence"]["runtime_resource_identity_fingerprint"] = (
        causal_fingerprint
    )
    finding.pop("authorization_causality_binding_proofs", None)

    result = {
        "finding": finding,
        "authorization_causality_receipt": receipt,
        "binding_materialization_receipts": sealed,
    }
    experiment = {
        "authorization_comparison_contract": {
            "resource_identity_binding_targets": ["order_id"],
        }
    }
    snapshot = deepcopy(result)

    output = attach_authorization_delivery_evidence(
        result,
        experiment=experiment,
    )

    order_row = sealed[0]
    provenance, problem = _binding_parent_provenance(order_row)
    assert problem == ""

    assert result == snapshot
    assert output["finding"]["authorization_causality_receipt"] == receipt
    assert output["finding"]["authorization_causality_binding_proofs"] == [
        {
            "receipt_id": order_row["materialization_receipt_id"],
            "target": "order_id",
            "status": "BOUND",
            "value_fingerprint": "order-42-fingerprint",
        }
    ]


def test_non_authorization_occurrence_is_not_reclassified() -> None:
    finding = {"finding_id": "finding:validation", "risk_family": "validation"}
    attempt = {
        "risk_family": "validation",
        "delivery_evidence_bundle": {"observer_receipts": []},
    }

    assert validate_authorization_delivery_finding(
        finding,
        attempt=attempt,
        campaign_id="campaign:1",
    ) == {"status": "NOT_APPLICABLE", "receipt_id": ""}


def test_formal_scope_quarantines_authorization_gate_without_causal_receipt(
    monkeypatch,
) -> None:
    finding = _finding(include_receipt=False)
    gate = {
        "schema_version": "qualibug.customer-delivery-gate-receipt.v2",
        "status": "DELIVERABLE",
        "identity": {"finding_id": "finding:auth"},
    }
    ledger = {
        "campaign_id": "campaign:1",
        "attempts": [
            {
                "terminal_status": "DELIVERABLE",
                "finding_id": "finding:auth",
                "risk_family": "authorization",
                "executed_obligation_id": "obl:auth",
                "experiment_id": "exp:auth",
                "execution_id": "execution:1",
                "gate_receipt": gate,
                "delivery_evidence_bundle": {
                    **_attempt(finding)["delivery_evidence_bundle"],
                    "finding": finding,
                },
            }
        ],
    }
    monkeypatch.setattr(
        formal_delivery_scope,
        "validate_obligation_attempt_ledger",
        lambda value: deepcopy(ledger),
    )
    monkeypatch.setattr(
        formal_delivery_scope,
        "validate_customer_delivery_gate_receipt_v2",
        lambda value, finding=None: deepcopy(gate),
    )

    assert formal_delivery_scope.validated_deliverable_gate_index(ledger) == {}


def test_causality_receipt_is_content_addressed() -> None:
    receipt = _causality_receipt()
    assert validate_authorization_causality_receipt(receipt) == receipt


def test_collection_observer_proof_is_packaged_and_validated() -> None:
    from ai_test_asset_center.authorization_oracle_causality import (
        build_authorization_observer_binding_proofs,
    )
    from ai_test_asset_center.observer_contracts_base import build_observer_receipt

    observer = build_observer_receipt(
        observer_id="authorization_comparison",
        status="OBSERVED",
        campaign_id="campaign:1",
        execution_id="execution:1",
        evidence={
            "owner_can_access": True,
            "viewer_can_access": True,
            "leak_detected": True,
            "same_resource_proven": True,
            "resource_match_basis": "identity_overlap",
        },
    )
    target = "operation:op:get-orders:observed_resource_identity"
    runtime_fingerprint, expected_proofs = (
        build_authorization_observer_binding_proofs(observer, [target])
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASSED",
        "experiment_id": "exp:auth",
        "obligation_id": "obl:auth",
        "campaign_id": "campaign:1",
        "execution_id": "execution:1",
        "reason_codes": [],
        "comparison_dimension": "ROLE_PERMISSION",
        "comparison_contract_fingerprint": _sha({"comparison": "collection"}),
        "compile_binding_graph_fingerprint": _sha({"binding": "collection"}),
        "runtime_resource_identity_fingerprint": runtime_fingerprint,
        "control_target_reached": True,
        "treatment_target_reached": True,
        "single_identity_dimension_proven": True,
        "same_resource_proven": True,
        "verified_receipt_ids": sorted([
            "contract:control",
            "contract:treatment",
            observer["receipt_id"],
        ]),
    }
    causality = {
        **unsigned,
        "receipt_id": "auth_causality_" + hashlib.sha256(
            _canonical(unsigned).encode("utf-8")
        ).hexdigest()[:24],
    }
    finding = _finding()
    finding["authorization_causality_receipt"] = causality
    finding["oracle"]["authorization_causality_receipt_id"] = causality["receipt_id"]
    finding["evidence"].update({
        "authorization_causality_receipt_id": causality["receipt_id"],
        "runtime_resource_identity_fingerprint": runtime_fingerprint,
        "single_identity_dimension_proven": True,
        "same_resource_proven": True,
    })
    finding.pop("authorization_causality_binding_proofs", None)
    result = {
        "finding": finding,
        "authorization_causality_receipt": causality,
        "observer_receipts": [observer],
        "binding_materialization_receipts": [],
    }
    experiment = {
        "authorization_comparison_contract": {
            "control_operation_ref": "op:get-orders",
            "treatment_operation_ref": "op:get-orders",
            "resource_identity_binding_targets": [],
        }
    }

    packaged = attach_authorization_delivery_evidence(
        result,
        experiment=experiment,
    )
    packaged_finding = packaged["finding"]
    assert packaged_finding["authorization_causality_binding_proofs"] == (
        expected_proofs
    )
    attempt = _attempt(packaged_finding)
    attempt["delivery_evidence_bundle"]["observer_receipts"] = [observer]
    attempt["delivery_evidence_bundle"]["contract_evidence_receipts"] = [
        {"kind": "control", "receipt_id": "contract:control"},
        {"kind": "treatment", "receipt_id": "contract:treatment"},
    ]

    assert validate_authorization_delivery_finding(
        packaged_finding,
        attempt=attempt,
        campaign_id="campaign:1",
    )["status"] == "PASSED"
