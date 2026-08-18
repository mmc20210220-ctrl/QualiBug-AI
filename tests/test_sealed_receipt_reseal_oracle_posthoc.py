"""Regression: reseal must not break oracle receipts enriched after fingerprinting.

V1.7 authorization causality / delivery gates append enrichment fields to a
contract oracle receipt AFTER its ``receipt_id`` was computed. The validator
recomputes the fingerprint over the original payload only (excluding those
post-hoc fields). ``sealed_receipt_reseal.reseal_oracle_receipt`` must apply
the same exclusion, otherwise the redaction reseal path rebuilds a receipt_id
over the enriched payload and validation fails with
``contract_oracle_receipt_fingerprint_invalid`` at the final scan persist step.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center._contract_oracles_mechanics import (
    CONTRACT_ORACLE_POST_HOC_FIELDS,
    _content_receipt,
    _contract_oracle_receipt,
    validate_contract_oracle_receipt,
)
from ai_test_asset_center.assertion_dsl_base import _assertion_receipt
from ai_test_asset_center.sealed_receipt_reseal import reseal_oracle_receipt


EXPERIMENT = {
    "experiment_id": "exp_reseal_test",
    "obligation_id": "obl_reseal_test",
    "campaign_id": "cmp_reseal_test",
    "execution_id": "exec_reseal_test",
}


def _active_activation() -> dict:
    required = {
        "control": ["control-1"],
        "treatment": ["treatment-1"],
        "actor": ["actor-1"],
        "fixture": ["fixture-1"],
        "observer": ["observer-1"],
        "cleanup": ["cleanup-1"],
    }
    verified = {
        key: [f"{key}-verified"]
        for key in required
    }
    payload = {
        "schema_version": "qualibug.contract-oracle-activation-receipt.v1",
        "experiment_id": EXPERIMENT["experiment_id"],
        "obligation_id": EXPERIMENT["obligation_id"],
        "campaign_id": EXPERIMENT["campaign_id"],
        "execution_id": EXPERIMENT["execution_id"],
        "status": "ACTIVE",
        "reason_codes": [],
        "required": required,
        "verified_receipt_ids": verified,
        "source_refs": [{"kind": "api_operation", "locator": "POST /v1/entities/{id}/act"}],
    }
    return _content_receipt("activation_", payload)


def _assertion(status: str, assertion_id: str) -> dict:
    return _assertion_receipt(
        assertion_id=assertion_id,
        kind="authorization",
        status=status,
        reason_code="" if status == "PASS" else "ACCESS_GRANTED",
        expected="denied",
        actual="allowed" if status == "VIOLATION" else "denied",
        error="",
        observer_receipt_ids=[f"obs_{assertion_id}"],
        source_refs=[],
        harness_error=False,
        campaign_id=EXPERIMENT["campaign_id"],
        execution_id=EXPERIMENT["execution_id"],
    )


def _oracle_receipt() -> dict:
    return _contract_oracle_receipt(
        experiment=EXPERIMENT,
        status="VIOLATION",
        verdict="customer_deliverable_defect_candidate",
        activation=_active_activation(),
        assertions=[_assertion("VIOLATION", "a_violation")],
        missing_requirements=[],
        demotion_reason="",
    )


def test_reseal_preserves_passed_causality_enrichment_and_validates() -> None:
    receipt = _oracle_receipt()
    # Post-hoc enrichment appended after receipt_id (authorization causality
    # PASSED path). The original receipt must still validate.
    receipt["authorization_causality_gate"] = "PASSED"
    receipt["authorization_causality_receipt_id"] = "auth_causality_0001"
    validate_contract_oracle_receipt(dict(receipt))

    resealed = reseal_oracle_receipt(dict(receipt), id_map={})
    validated = validate_contract_oracle_receipt(resealed)

    assert validated["authorization_causality_gate"] == "PASSED"
    assert validated["authorization_causality_receipt_id"] == "auth_causality_0001"
    expected_payload = {
        key: value
        for key, value in resealed.items()
        if key != "receipt_id" and key not in CONTRACT_ORACLE_POST_HOC_FIELDS
    }
    assert resealed["receipt_id"] == _content_receipt("oracle_", expected_payload)["receipt_id"]


def test_reseal_preserves_indeterminate_causality_enrichment_and_validates() -> None:
    from ai_test_asset_center import contract_oracles as _facade_oracles

    receipt = _oracle_receipt()
    original_verdict = {
        "status": receipt["status"],
        "verdict": receipt["verdict"],
        "receipt_id": receipt["receipt_id"],
        "activation_receipt_id": receipt["activation_receipt_id"],
        "failed_assertions": receipt["failed_assertions"],
    }
    receipt["status"] = "INDETERMINATE"
    receipt["verdict"] = "blocked_experiment"
    receipt["customer_deliverable_candidate"] = False
    receipt["authorization_causality_gate"] = "INDETERMINATE"
    receipt["authorization_causality_receipt_id"] = "auth_causality_0002"
    receipt["authorization_causality_reason_codes"] = ["CAUSAL_PROOF_INSUFFICIENT"]
    receipt["pre_causality_oracle_verdict"] = original_verdict

    resealed = reseal_oracle_receipt(dict(receipt), id_map={})
    # The strict facade is the real scan-persist path: it restores the
    # pre-gate base and demands identity preservation.  The reseal computes
    # the identity over the restored pre-gate base, so with an empty id_map
    # the identity is unchanged and the snapshot keeps it.
    validated = _facade_oracles.validate_contract_oracle_receipt(resealed)

    assert validated["status"] == "INDETERMINATE"
    assert validated["authorization_causality_gate"] == "INDETERMINATE"
    assert validated["pre_causality_oracle_verdict"]["receipt_id"] == receipt["receipt_id"]


def test_reseal_receipt_id_excludes_post_hoc_fields() -> None:
    receipt = _oracle_receipt()
    receipt["authorization_causality_gate"] = "PASSED"
    receipt["authorization_causality_receipt_id"] = "auth_causality_0003"
    resealed = reseal_oracle_receipt(dict(receipt), id_map={})
    # The resealed id must equal the hash of the payload WITHOUT the post-hoc
    # fields, matching the validator's fingerprint contract.
    with_post_hoc = _content_receipt(
        "oracle_",
        {key: value for key, value in resealed.items() if key != "receipt_id"},
    )["receipt_id"]
    without_post_hoc = _content_receipt(
        "oracle_",
        {
            key: value
            for key, value in resealed.items()
            if key != "receipt_id" and key not in CONTRACT_ORACLE_POST_HOC_FIELDS
        },
    )["receipt_id"]
    assert resealed["receipt_id"] == without_post_hoc
    assert resealed["receipt_id"] != with_post_hoc


def test_reseal_plain_oracle_receipt_is_unchanged_semantics() -> None:
    receipt = _oracle_receipt()
    resealed = reseal_oracle_receipt(dict(receipt), id_map={})
    validated = validate_contract_oracle_receipt(resealed)
    assert validated["status"] == "VIOLATION"
    assert validated["customer_deliverable_candidate"] is True
    assert not set(CONTRACT_ORACLE_POST_HOC_FIELDS).intersection(validated)


def test_reseal_preserves_field_oracle_trace_on_assertion_and_oracle() -> None:
    from ai_test_asset_center.assertion_dsl_base import (
        _assertion_receipt,
        validate_assertion_receipt,
    )
    from ai_test_asset_center.sealed_receipt_reseal import (
        reseal_assertion_receipt,
    )

    assertion = _assertion_receipt(
        assertion_id="a_field_trace",
        kind="state_transition",
        status="VIOLATION",
        reason_code="FORBIDDEN_TRANSITION",
        expected="LOCKED",
        actual="OPEN",
        error="",
        observer_receipt_ids=["obs_trace_1"],
        source_refs=[],
        harness_error=False,
        campaign_id=EXPERIMENT["campaign_id"],
        execution_id=EXPERIMENT["execution_id"],
    )
    # V1.6.1: the trace is attached AFTER the receipt_id was computed.
    assertion["field_oracle_trace"] = {
        "schema_version": "qualibug.field-oracle-trace.v1",
        "assertion_id": "a_field_trace",
        "kind": "state_transition",
        "rule_id": "rule_state_x",
        "expected": "LOCKED",
        "actual": "OPEN",
        "before_values": {"state": "LOCKED"},
        "after_values": {"state": "OPEN"},
        "status": "VIOLATION",
        "reason_code": "FORBIDDEN_TRANSITION",
    }
    resealed_assertion = reseal_assertion_receipt(
        dict(assertion),
        id_map={},
    )
    validated_assertion = validate_assertion_receipt(resealed_assertion)
    assert validated_assertion["field_oracle_trace"]["status"] == "VIOLATION"

    # The resealed oracle must carry the trace on its failed assertions so the
    # delivery gate does not demote the experiment to FIELD_ORACLE_TRACE_MISSING.
    oracle = _contract_oracle_receipt(
        experiment=EXPERIMENT,
        status="VIOLATION",
        verdict="customer_deliverable_defect_candidate",
        activation=_active_activation(),
        assertions=[resealed_assertion],
        missing_requirements=[],
        demotion_reason="",
    )
    resealed_oracle = reseal_oracle_receipt(dict(oracle), id_map={})
    validated_oracle = validate_contract_oracle_receipt(resealed_oracle)
    failed = validated_oracle["failed_assertions"]
    assert failed and failed[0]["field_oracle_trace"]["status"] == "VIOLATION"


def test_reseal_remaps_snapshot_identity_when_children_resealed() -> None:
    """Regression: a reseal that remaps child receipts must also remap the
    pre-gate snapshot's nested identity.

    The real scan reseal chain reseals activation/assertion receipts first
    (old→new id_map), then recomputes the oracle receipt_id over the remapped
    payload.  The post-hoc causality/validity snapshots captured the ORIGINAL
    oracle identity; if they are re-attached verbatim, the strict validator's
    restore produces a base whose receipt_id differs from the row and raises
    contract_oracle_posthoc_base_identity_mismatch at scan persist.
    """
    from ai_test_asset_center import contract_oracles as _facade_oracles
    from ai_test_asset_center.sealed_receipt_reseal import (
        reseal_activation_receipt,
        reseal_assertion_receipt,
    )

    receipt = _oracle_receipt()
    # Children resealed first, exactly as the real reseal chain does.
    resealed_activation = reseal_activation_receipt(
        dict(receipt["activation_receipt"]), id_map={}
    )
    resealed_assertions = [
        reseal_assertion_receipt(dict(item), id_map={})
        for item in receipt["assertions"]
    ]
    id_map = {
        receipt["activation_receipt"]["receipt_id"]: resealed_activation[
            "receipt_id"
        ],
        receipt["assertions"][0]["receipt_id"]: resealed_assertions[0][
            "receipt_id"
        ],
    }
    original_verdict = {
        "status": receipt["status"],
        "verdict": receipt["verdict"],
        "receipt_id": receipt["receipt_id"],
        "activation_receipt_id": receipt["activation_receipt_id"],
        "failed_assertions": receipt["failed_assertions"],
    }
    enriched = dict(receipt)
    enriched["activation_receipt"] = resealed_activation
    enriched["activation_receipt_id"] = resealed_activation["receipt_id"]
    enriched["assertions"] = resealed_assertions
    enriched["status"] = "INDETERMINATE"
    enriched["verdict"] = "blocked_experiment"
    enriched["customer_deliverable_candidate"] = False
    enriched["authorization_causality_gate"] = "INDETERMINATE"
    enriched["authorization_causality_receipt_id"] = "auth_causality_0004"
    enriched["authorization_causality_reason_codes"] = [
        "CAUSAL_PROOF_INSUFFICIENT"
    ]
    enriched["pre_causality_oracle_verdict"] = original_verdict

    resealed = reseal_oracle_receipt(dict(enriched), id_map=id_map)
    # The strict facade validator is the real scan-persist path; it restores
    # the pre-gate base and demands identity preservation.
    validated = _facade_oracles.validate_contract_oracle_receipt(resealed)

    assert validated["status"] == "INDETERMINATE"
    assert validated["authorization_causality_gate"] == "INDETERMINATE"
    # The snapshot identity must follow the resealed oracle so the strict
    # validator's pre-gate restore preserves the row identity.
    assert (
        validated["pre_causality_oracle_verdict"]["receipt_id"]
        == resealed["receipt_id"]
    )
    assert (
        validated["pre_causality_oracle_verdict"]["activation_receipt_id"]
        == resealed["activation_receipt_id"]
    )
    # Failed-assertion identities inside the snapshot follow the remap too.
    snapshot_failed = validated["pre_causality_oracle_verdict"][
        "failed_assertions"
    ]
    assert snapshot_failed and snapshot_failed[0]["receipt_id"] == id_map[
        receipt["assertions"][0]["receipt_id"]
    ]
