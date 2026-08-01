"""Authorization remediation receipts retain every current delivery occurrence."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ai_test_asset_center import historical_authorization_rerun_consumer as consumer
from ai_test_asset_center.historical_authorization_rerun_consumer import (
    consume_historical_authorization_rerun_plan,
)


def _plan() -> dict:
    binding = {
        "scope_id": "scope:current",
        "environment_ref": "env:staging",
        "environment_type": "staging",
        "target_base_url": "https://staging.example.test",
        "execution_mode": "safe_read_only",
        "write_execution_allowed": False,
        "source_binding_status": "RESOLVED",
        "source_id": "source:api",
        "source_hash": "a" * 64,
        "source_candidate_count": 1,
        "runtime_status": "RESOLVED",
        "missing_runtime_bindings": [],
        "reason": "",
    }
    request = {
        "status": "READY_FOR_CONTROLLED_RECOMPILE",
        "project_id": "alpha",
        "predecessor": {
            "authority_scope_id": "ledger:" + "b" * 64,
            "run_id": "run:old",
            "campaign_id": "campaign:old",
            "finding_id": "finding:old",
            "obligation_id": "obl:auth",
            "experiment_id": "exp:old",
            "quarantine_receipt_id": "auth_quarantine:1",
        },
        "current_runtime_binding": binding,
        "approval": {
            "status": "CURRENT_APPROVAL_FOUND",
            "approval_id": "eap_current",
            "code": "",
        },
        "request_id": "auth_rerun_multi",
        "request_fingerprint": "c" * 64,
    }
    return {
        "plan_fingerprint": "d" * 64,
        "projects": [{"project_id": "alpha", "requests": [request]}],
    }


def test_current_multi_occurrence_delivery_is_preserved(
    monkeypatch,
    tmp_path: Path,
) -> None:
    plan = _plan()
    request = plan["projects"][0]["requests"][0]
    monkeypatch.setattr(
        consumer,
        "validate_historical_authorization_rerun_plan",
        lambda value: deepcopy(value),
    )
    monkeypatch.setattr(
        consumer,
        "_fresh_authority",
        lambda project_id, root, request: (
            deepcopy(request["current_runtime_binding"]),
            deepcopy(request["approval"]),
            [],
        ),
    )
    monkeypatch.setattr(
        consumer,
        "validate_mainline_run_contract",
        lambda value: {
            "run_id": "run:new",
            "campaign_id": "campaign:new",
            "contract_fingerprint": "e" * 64,
        },
    )
    monkeypatch.setattr(
        consumer,
        "validate_obligation_attempt_ledger",
        lambda value: {
            "selected_count": 1,
            "terminal_count": 1,
            "ledger_fingerprint": "f" * 64,
            "attempts": [{
                "obligation_id": "obl:auth",
                "experiment_id": "exp:new",
                "execution_id": "execution:new",
                "terminal_stage": "gate",
                "terminal_status": "DELIVERABLE",
                "reason_code": "",
                "gate_receipt_id": "gate:primary",
                "finding_id": "finding:new:a",
                "delivery_occurrence_count": 2,
                "delivery_occurrence_finding_ids": [
                    "finding:new:a",
                    "finding:new:b",
                ],
                "gate_receipt": {
                    "schema_version":
                        consumer.CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
                },
            }],
        },
    )
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: {
            "mainline_run": {},
            "obligation_attempt_ledger": {},
            "formal_count_projection": {
                "delivery_occurrence_finding_ids": [
                    "finding:new:a",
                    "finding:new:b",
                ],
            },
            "canonical_defect_registry": {
                "canonical_defect_ids": ["defect:a", "defect:b"],
            },
        },
    )

    report = consume_historical_authorization_rerun_plan(
        plan,
        root=tmp_path,
        execute=True,
    )

    receipt = report["receipts"][0]
    assert receipt["status"] == "CURRENT_DEFECT_REPRODUCED"
    assert receipt["successor"]["finding_id"] == "finding:new:a"
    assert receipt["successor"]["delivery_occurrence_finding_ids"] == [
        "finding:new:a",
        "finding:new:b",
    ]
    assert receipt["successor"]["canonical_defect_ids"] == [
        "defect:a",
        "defect:b",
    ]
    assert receipt["historical_finding_republication_allowed"] is False
