from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_quality_projection import (
    attach_quality_projection_to_scan_result,
    build_run_delivery_readiness_projection,
)
from ai_test_asset_center.discovery_mainline_contract import MainlineContractError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_committed_full_artifact_cannot_claim_release_ready_when_pipeline_is_degraded() -> None:
    payload = json.loads(
        (REPOSITORY_ROOT / "_funnel_runs" / "full.json").read_text(encoding="utf-8")
    )

    projected = attach_quality_projection_to_scan_result(payload["full_result"])
    readiness = projected["run_delivery_readiness"]

    assert readiness["scope"] == "current_run_formal_finding_publication"
    assert readiness["status"] == "NOT_READY"
    assert readiness["release_ready"] is False
    assert set(readiness["reason_codes"]).issuperset(
        {"PIPELINE_DEGRADED", "PARTIAL_OBLIGATION_EXECUTION", "COVERAGE_GAPS_REMAIN"}
    )
    assert readiness["coverage_gap_projection"]["by_stage"] == {
        "behavior_ir": 77,
        "test_obligations": 143,
    }
    assert readiness["coverage_gap_projection"]["total"] == 220
    assert readiness["eligible_formal_deliverable_count"] == 20
    assert readiness["published_formal_deliverable_count"] == 0
    assert projected["release_gate"]["verdict"] != "pass"
    assert projected["release_gate"]["status"] != "release_ready"

    commercial = projected["commercial_readiness"]
    assert commercial["scope"] == "commercial_product_readiness"
    assert commercial["status"] == "NOT_MEASURED"
    assert commercial["reason_code"] == "EXTERNAL_EVALUATOR_RECEIPT_REQUIRED"


def test_all_blocked_zero_execution_and_cleanup_failure_are_machine_readable() -> None:
    projection = build_run_delivery_readiness_projection(
        {
            "campaign": {"campaign_id": "campaign-1"},
            "mainline_run": {
                "run_id": "run-1",
                "campaign_id": "campaign-1",
                "contract_fingerprint": "mainline-fingerprint",
                "mainline_authority": "experiment_candidate",
                "customer_outputs_published": True,
            },
            "pipeline_health": {
                "status": "BLOCKED",
                "selected_obligation_count": 2,
                "terminal_obligation_count": 2,
                "executed_obligation_count": 0,
                "blocked_obligation_count": 2,
                "cleanup_failure_count": 1,
            },
            "obligation_attempt_ledger": {
                "schema_version": "qualibug.obligation-attempt-ledger.v1",
                "run_id": "run-1",
                "campaign_id": "campaign-1",
                "mainline_contract_fingerprint": "mainline-fingerprint",
                "selected_count": 2,
                "terminal_count": 2,
                "complete": True,
                "ledger_fingerprint": "ledger-fingerprint",
            },
        },
        formal_count_projection={
            "schema_version": "qualibug.discovery-quality-projection.v2",
            "authority_status": "VERIFIED",
            "formal_customer_deliverable_count": 1,
        },
    )

    assert projection["release_ready"] is False
    assert set(projection["reason_codes"]).issuperset(
        {"PIPELINE_BLOCKED", "NO_REAL_EXECUTION", "ALL_OBLIGATIONS_BLOCKED", "CLEANUP_FAILURE"}
    )
    assert projection["published_formal_deliverable_count"] == 0
    assert projection["identities"] == {
        "campaign_id": "campaign-1",
        "run_id": "run-1",
        "mainline_authority": "experiment_candidate",
        "mainline_contract_fingerprint": "mainline-fingerprint",
        "attempt_ledger_fingerprint": "ledger-fingerprint",
    }


def test_pipeline_and_attempt_ledger_counter_contradiction_fails_with_identities() -> None:
    with pytest.raises(MainlineContractError) as error:
        build_run_delivery_readiness_projection(
            {
                "campaign": {"campaign_id": "campaign-contradiction"},
                "mainline_run": {
                    "run_id": "run-contradiction",
                    "campaign_id": "campaign-contradiction",
                    "contract_fingerprint": "authority-fingerprint",
                    "mainline_authority": "legacy_champion",
                    "customer_outputs_published": True,
                },
                "pipeline_health": {
                    "status": "OK",
                    "selected_obligation_count": 1,
                    "terminal_obligation_count": 1,
                    "executed_obligation_count": 1,
                    "blocked_obligation_count": 0,
                    "cleanup_failure_count": 0,
                },
                "obligation_attempt_ledger": {
                    "schema_version": "qualibug.obligation-attempt-ledger.v1",
                    "run_id": "run-contradiction",
                    "campaign_id": "campaign-contradiction",
                    "mainline_contract_fingerprint": "authority-fingerprint",
                    "selected_count": 2,
                    "terminal_count": 2,
                    "complete": True,
                    "ledger_fingerprint": "ledger-contradiction",
                },
            },
            formal_count_projection={
                "schema_version": "qualibug.discovery-quality-projection.v2",
                "authority_status": "VERIFIED",
                "formal_customer_deliverable_count": 0,
            },
        )

    message = str(error.value)
    assert "run_delivery_projection_contradiction" in message
    assert "campaign-contradiction" in message
    assert "run-contradiction" in message
    assert "ledger-contradiction" in message
