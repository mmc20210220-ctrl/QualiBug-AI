from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center import assertion_dsl
from ai_test_asset_center import canonical_defect_registry
from ai_test_asset_center import contract_oracles
from ai_test_asset_center import experiment_batch_executor
from ai_test_asset_center import experiment_outcome_finalizer
from ai_test_asset_center import formal_delivery_authority
from ai_test_asset_center import obligation_attempt_ledger
from ai_test_asset_center import observer_contracts


def _observer(outcome_ref: str, observer_id: str) -> dict:
    return observer_contracts.build_observer_receipt(
        observer_id=observer_id,
        status="OBSERVED",
        evidence={"status_code": 500},
        campaign_id="campaign:1",
        execution_id="execution:1",
        semantic_role="MANDATORY_OUTCOME",
        outcome_ref=outcome_ref,
        oracle_template_ref=f"oracle:{outcome_ref}",
        assertion_requirement_ref=f"requirement:{outcome_ref}",
    )


def _assertion(outcome_ref: str, assertion_id: str, expected: int = 200) -> dict:
    return {
        "assertion_id": assertion_id,
        "kind": "http_status",
        "expected": expected,
        "mandatory": True,
        "semantic_role": "MANDATORY_OUTCOME",
        "outcome_ref": outcome_ref,
        "oracle_template_ref": f"oracle:{outcome_ref}",
        "assertion_requirement_ref": f"requirement:{outcome_ref}",
        "canonical_outcome_identity_required": True,
    }


def test_observer_receipt_content_addresses_outcome_identity() -> None:
    receipt = _observer("outcome:state", "entity_state")
    validated = observer_contracts.validate_observer_receipt(receipt)
    assert validated["outcome_ref"] == "outcome:state"
    tampered = deepcopy(validated)
    tampered["outcome_ref"] = "outcome:permission"
    with pytest.raises(ValueError, match="outcome_identity|fingerprint"):
        observer_contracts.validate_observer_receipt(tampered)


def test_assertion_uses_matching_outcome_receipt_only() -> None:
    state = _observer("outcome:state", "entity_state")
    permission = _observer("outcome:permission", "http_response")
    receipt = assertion_dsl.evaluate_assertion(
        _assertion("outcome:state", "assertion:state"),
        observations={
            "status_code": 500,
            "observer_receipts": [state, permission],
        },
        campaign_id="campaign:1",
        execution_id="execution:1",
    )
    validated = assertion_dsl.validate_assertion_receipt(receipt)
    assert validated["status"] == "VIOLATION"
    assert validated["outcome_ref"] == "outcome:state"
    assert validated["observer_receipt_ids"] == [state["receipt_id"]]


def test_multiple_violated_outcomes_are_complete_and_require_fanout() -> None:
    assertions = [
        {"status": "VIOLATION", "outcome_ref": "outcome:permission"},
        {"status": "VIOLATION", "outcome_ref": "outcome:state"},
    ]
    projection = contract_oracles._canonical_projection(
        {
            "canonical_outcome_identity_required": True,
            "mandatory_outcome_refs": ["outcome:permission", "outcome:state"],
        },
        assertions,
    )
    reasons = contract_oracles._identity_reason_codes(projection)
    assert projection["canonical_outcome_identity_complete"] is True
    assert projection["outcome_fanout_required"] is True
    assert projection["violation_occurrence_count"] == 2
    assert projection["primary_violation_outcome_ref"] == ""
    assert reasons == []


def test_finalizer_fans_out_one_finding_per_violated_outcome(monkeypatch) -> None:
    assertions = [
        {
            "status": "VIOLATION",
            "receipt_id": "assert:permission",
            "kind": "owner_tenant_visibility",
            "outcome_ref": "outcome:permission",
        },
        {
            "status": "VIOLATION",
            "receipt_id": "assert:state",
            "kind": "state_transition",
            "outcome_ref": "outcome:state",
        },
    ]

    def project(parent: dict, ref: str) -> dict:
        assertion = next(item for item in assertions if item["outcome_ref"] == ref)
        return {
            "receipt_id": f"oracle:{ref}",
            "activation_receipt_id": "activation:1",
            "status": "VIOLATION",
            "primary_violation_outcome_ref": ref,
            "parent_oracle_receipt_id": parent["receipt_id"],
            "assertions": [assertion],
        }

    monkeypatch.setattr(
        experiment_outcome_finalizer._outcome_oracles,
        "project_contract_oracle_for_outcome",
        project,
    )
    result = experiment_outcome_finalizer._fanout_finding_outcomes(
        {
            "status": "EXECUTED",
            "oracle_verdict": {
                "receipt_id": "oracle:aggregate",
                "status": "VIOLATION",
                "canonical_outcome_identity_required": True,
                "violation_outcome_refs": [
                    "outcome:permission",
                    "outcome:state",
                ],
                "assertions": assertions,
            },
            "finding": {
                "title": "[ContractOracle] old: actor POST /orders",
                "oracle": {},
                "evidence": {},
                "raw_evidence": {"db_snapshot": {}},
            },
        }
    )
    assert [row["outcome_ref"] for row in result["findings"]] == [
        "outcome:permission",
        "outcome:state",
    ]
    assert result["finding"] == result["findings"][0]
    assert result["oracle_verdict"]["receipt_id"] == "oracle:outcome:permission"
    assert result["aggregate_oracle_verdict"]["receipt_id"] == "oracle:aggregate"
    assert result["findings"][1]["failed_assertions"] == [assertions[1]]


def test_batch_primary_occurrence_prefers_deliverable() -> None:
    primary = experiment_batch_executor._select_primary_occurrence(
        [
            {
                "finding_id": "finding:a",
                "outcome_ref": "outcome:a",
                "gate_receipt": {"status": "BLOCKED"},
            },
            {
                "finding_id": "finding:b",
                "outcome_ref": "outcome:b",
                "gate_receipt": {"status": "DELIVERABLE"},
            },
        ]
    )
    assert primary["finding_id"] == "finding:b"


def test_batch_fans_out_all_occurrences_into_authoritative_collections(monkeypatch) -> None:
    oracles = [
        {
            "receipt_id": "oracle:a",
            "primary_violation_outcome_ref": "outcome:a",
        },
        {
            "receipt_id": "oracle:b",
            "primary_violation_outcome_ref": "outcome:b",
        },
    ]
    monkeypatch.setitem(
        experiment_batch_executor._apply_fanout.__globals__,
        "_occurrence_oracles",
        lambda _outcome: oracles,
    )

    def build_occurrence(*, finding: dict, oracle: dict, **_kwargs) -> dict:
        ref = oracle["primary_violation_outcome_ref"]
        delivered = ref == "outcome:b"
        row = dict(finding)
        row.update(
            {
                "finding_id": f"finding:{ref[-1]}",
                "id": f"finding:{ref[-1]}",
                "outcome_ref": ref,
            }
        )
        return {
            "finding_id": row["finding_id"],
            "outcome_ref": ref,
            "finding": row,
            "delivery_execution_receipt": {
                "receipt_id": f"delivery:{ref}",
                "receipt_fingerprint": f"delivery-fp:{ref}",
            },
            "oracle_receipt": oracle,
            "reproduction_receipt": {"receipt_id": f"repro:{ref}"},
            "gate_receipt": {
                "status": "DELIVERABLE" if delivered else "BLOCKED",
                "gate_receipt_id": f"gate:{ref}",
            },
        }

    monkeypatch.setitem(
        experiment_batch_executor._apply_fanout.__globals__,
        "_build_occurrence",
        build_occurrence,
    )
    result = experiment_batch_executor._apply_fanout(
        {
            "results": [
                {
                    "selected_obligation_id": "obligation:1",
                    "aggregate_oracle_verdict": {"receipt_id": "oracle:aggregate"},
                    "findings": [
                        {"outcome_ref": "outcome:a"},
                        {"outcome_ref": "outcome:b"},
                    ],
                }
            ],
            "execution_results": {"obligation:1": {}},
            "gate_results": {},
            "findings": [],
            "campaign_validation_receipt": {"reasons": []},
        },
        selected=[{"obligation_id": "obligation:1"}],
        experiments_by_obligation={"obligation:1": {}},
        behavior_ir={},
        mainline_run={"run_id": "run:1"},
        campaign_id="campaign:1",
    )
    assert [row["outcome_ref"] for row in result["findings"]] == [
        "outcome:a",
        "outcome:b",
    ]
    execution = result["execution_results"]["obligation:1"]
    assert execution["delivery_occurrence_count"] == 2
    assert execution["finding"]["outcome_ref"] == "outcome:b"
    assert result["gate_results"]["obligation:1"]["status"] == "DELIVERABLE"


def test_attempt_ledger_projects_each_delivery_occurrence() -> None:
    attempt = {
        "finding_id": "finding:a",
        "delivery_occurrences": [
            {
                "finding_id": "finding:a",
                "outcome_ref": "outcome:a",
                "gate_receipt_id": "gate:a",
                "gate_output_fingerprint": "fp:a",
                "gate_receipt": {"gate_receipt_id": "gate:a"},
                "delivery_evidence_bundle": {"finding": {"finding_id": "finding:a"}},
            },
            {
                "finding_id": "finding:b",
                "outcome_ref": "outcome:b",
                "gate_receipt_id": "gate:b",
                "gate_output_fingerprint": "fp:b",
                "gate_receipt": {"gate_receipt_id": "gate:b"},
                "delivery_evidence_bundle": {"finding": {"finding_id": "finding:b"}},
            },
        ],
    }
    views = obligation_attempt_ledger.delivery_occurrence_views(attempt)
    assert [row["finding_id"] for row in views] == ["finding:a", "finding:b"]
    assert [row["outcome_ref"] for row in views] == ["outcome:a", "outcome:b"]


def test_formal_authority_allows_multiple_findings_from_same_obligation() -> None:
    entries = [
        {
            "obligation_id": "obligation:1",
            "experiment_id": "experiment:1",
            "execution_id": "execution:1",
            "finding_id": "finding:a",
            "attempt_fingerprint": "attempt:fp",
            "gate_receipt_id": "gate:a",
            "gate_output_fingerprint": "gate-fp:a",
            "finding_payload_fingerprint": "finding-fp:a",
        },
        {
            "obligation_id": "obligation:1",
            "experiment_id": "experiment:1",
            "execution_id": "execution:1",
            "finding_id": "finding:b",
            "attempt_fingerprint": "attempt:fp",
            "gate_receipt_id": "gate:b",
            "gate_output_fingerprint": "gate-fp:b",
            "finding_payload_fingerprint": "finding-fp:b",
        },
    ]
    payload = {
        "schema_version": formal_delivery_authority.FORMAL_DELIVERY_AUTHORITY_SCHEMA,
        "status": "VERIFIED",
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "target_id": "target:1",
        "environment_id": "environment:1",
        "policy_version": "policy:1",
        "evaluation_mode": "formal",
        "mainline_contract_fingerprint": "mainline:fp",
        "attempt_ledger_fingerprint": "ledger:fp",
        "gate_schema_version": "qualibug.customer-delivery-gate-receipt.v2",
        "delivery_occurrence_count": 2,
        "delivery_occurrence_finding_ids": ["finding:a", "finding:b"],
        "deliverable_attempts": entries,
    }
    payload["receipt_fingerprint"] = formal_delivery_authority._fingerprint(payload)
    assert (
        formal_delivery_authority.validate_formal_delivery_authority_receipt(payload)[
            "delivery_occurrence_count"
        ]
        == 2
    )


def test_canonical_registry_maps_all_occurrence_views() -> None:
    attempts = canonical_defect_registry._attempt_by_finding(
        {
            "attempts": [
                {
                    "terminal_status": "DELIVERABLE",
                    "finding_id": "finding:a",
                    "delivery_occurrences": [
                        {
                            "finding_id": "finding:a",
                            "outcome_ref": "outcome:a",
                            "gate_receipt_id": "gate:a",
                            "gate_output_fingerprint": "fp:a",
                            "gate_receipt": {},
                            "delivery_evidence_bundle": {},
                        },
                        {
                            "finding_id": "finding:b",
                            "outcome_ref": "outcome:b",
                            "gate_receipt_id": "gate:b",
                            "gate_output_fingerprint": "fp:b",
                            "gate_receipt": {},
                            "delivery_evidence_bundle": {},
                        },
                    ],
                }
            ]
        }
    )
    assert sorted(attempts) == ["finding:a", "finding:b"]
