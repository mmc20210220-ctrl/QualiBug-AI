from __future__ import annotations

import json
from copy import deepcopy

from ai_test_asset_center.discovery_trace_ledger import build_discovery_trace_ledger
from ai_test_asset_center.discovery_weakness_miner import mine_discovery_weaknesses


def _formal_finding() -> dict:
    return {
        "title": "observed finding",
        "severity": "P1",
        "behavior_slice_id": "BHV_INVALID",
        "evidence_id": "EVID_INVALID",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "expected": "request rejected",
        "actual": "request accepted",
        "timestamp": "2026-07-10T00:00:00Z",
        "evidence_consistency": {"verdict": "confirmed"},
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "CUSTOMER_READY",
            "missing_requirements": [],
        },
        "reproduction": {
            "method": "POST",
            "path": "/api/resources/{id}",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"result": "redacted"}},
        },
        "raw_evidence": {
            "request_raw": {"method": "POST", "path": "/api/resources/{id}"},
            "response_raw": {"status_code": 200, "body": {"result": "redacted"}},
            "timestamp": "2026-07-10T00:00:00Z",
            "has_real_evidence": True,
        },
    }


def _observed_v12() -> dict:
    return {
        "runtime_status": "OK",
        "behavior_slices": [
            {
                "slice_id": "BHV_INVALID",
                "kind": "state_machine",
                "endpoints": ["/api/resources/{id}"],
                "priority": 0.9,
                "source_refs": [{"source_type": "requirement", "quote": "not emitted"}],
                "_hypothesis_origin": "reasoner",
                "_hypothesis_id": "HYP-1",
                "_hypothesis_family": "state_machine",
                "_bound_method": "POST",
                "_bound_path": "/api/resources/{id}",
            },
            {
                "slice_id": "BHV_UNBOUND",
                "kind": "invariant",
                "endpoints": [],
                "priority": 0.2,
                "source_refs": [],
            },
        ],
        "behavior_slice_ledger": {
            "campaign_id": "CMP-1",
            "round": 1,
            "selected_slice_ids": ["BHV_INVALID"],
            "attempted_slice_ids": ["BHV_INVALID"],
        },
        "phases": {
            "scenario_generation": {"selected_slice_ids": ["BHV_INVALID"]},
            "execution": {
                "status": "completed",
                "executed": 0,
                "observability_status": "ok",
                "skip_telemetry": {
                    "blocked_samples": [
                        {
                            "behavior_slice_id": "BHV_INVALID",
                            "reason": "missing_runtime_path_binding:id",
                        }
                    ]
                },
            },
        },
        "findings": [_formal_finding()],
        "evidence_graphs": [
            {
                "scenario": {
                    "id": "SCN-1",
                    "behavior_slice_id": "BHV_INVALID",
                    "discovery_round": 1,
                },
                "evidence_id": "EVID_INVALID",
                "execution_trace": {
                    "scenario_id": "SCN-1",
                    "steps": [
                        {"method": "GET", "path": "/api/resources", "status": 200},
                        {
                            "method": "POST",
                            "path": "/api/resources/{id}",
                            "status": 0,
                            "skipped_reason": "missing_runtime_path_binding:id",
                            "request": {"secret": "must-not-persist"},
                            "response": {"body": "must-not-persist"},
                        },
                    ],
                    "errors": ["missing_runtime_path_binding:id"],
                    "precondition_not_met": [{"missing_path_params": ["id"]}],
                    "sandbox_write": {
                        "status": "cleanup_incomplete",
                        "audit_path": "private-path-not-emitted",
                        "cleanup": {"status": "failed", "receipt_ref": ""},
                    },
                },
                "oracle_results": [
                    {
                        "passed": False,
                        "oracle_name": "HttpStatusOracle",
                        "actual": "HTTP 0",
                    }
                ],
                "layers_triggered": ["L1"],
            }
        ],
        "mainline_unification": {
            "reasoner": {"input": 5, "bound": 2, "dropped_no_endpoint": 3}
        },
    }


def test_trace_ledger_exposes_causal_failure_without_raw_customer_payloads() -> None:
    ledger = build_discovery_trace_ledger(
        _observed_v12(),
        run_id="RUN-1",
        policy_id="POLICY-1",
        target_id="TARGET-1",
        project_id="PROJECT-1",
        industry="industry-a",
        evaluation_mode="replay",
    )

    invalid = next(item for item in ledger["traces"] if item["behavior_slice_id"] == "BHV_INVALID")
    assert invalid["outcome"] == "invalid_promotion_or_verification"
    assert {
        "RUNTIME_PATH_BINDING_MISSING",
        "ZERO_STATUS_NON_EXECUTION",
        "PRECONDITION_NOT_MET",
        "ORACLE_CONFIRMED_NON_EXECUTION",
        "CLEANUP_FAILED",
        "CLEANUP_EVIDENCE_MISSING",
    } <= set(invalid["failure_signatures"])
    unbound = next(item for item in ledger["traces"] if item["behavior_slice_id"] == "BHV_UNBOUND")
    assert {"ENDPOINT_BINDING_MISSING", "SOURCE_GROUNDING_MISSING", "CANDIDATE_NOT_SELECTED"} <= set(
        unbound["failure_signatures"]
    )
    assert ledger["aggregate_stage_events"]["dropped_no_endpoint"] == 3
    serialized = json.dumps(ledger, ensure_ascii=False)
    assert "must-not-persist" not in serialized
    assert "private-path-not-emitted" not in serialized
    assert ledger["redaction_contract"]["ground_truth_persisted"] is False


def test_weakness_miner_prioritizes_verifier_and_cleanup_failures() -> None:
    first = build_discovery_trace_ledger(
        _observed_v12(),
        run_id="RUN-1",
        policy_id="POLICY-1",
        target_id="TARGET-1",
        project_id="PROJECT-1",
        industry="industry-a",
        evaluation_mode="replay",
    )
    second = build_discovery_trace_ledger(
        _observed_v12(),
        run_id="RUN-2",
        policy_id="POLICY-1",
        target_id="TARGET-2",
        project_id="PROJECT-2",
        industry="industry-b",
        evaluation_mode="shadow",
    )

    report = mine_discovery_weaknesses([first, second])
    patterns = {item["failure_signature"]: item for item in report["patterns"]}

    assert patterns["ORACLE_CONFIRMED_NON_EXECUTION"]["severity"] == "critical"
    assert patterns["ORACLE_CONFIRMED_NON_EXECUTION"]["proposal_eligible"] is True
    assert patterns["ORACLE_CONFIRMED_NON_EXECUTION"]["affected_run_count"] == 2
    assert patterns["ORACLE_CONFIRMED_NON_EXECUTION"]["affected_industry_count"] == 2
    assert patterns["CLEANUP_FAILED"]["harness_surface"] == "sandbox_write_policy"
    assert patterns["ENDPOINT_BINDING_DROPPED_AGGREGATE"]["observed_count"] == 6
    assert report["privacy_contract"]["ground_truth_used"] is False
    serialized = json.dumps(report, ensure_ascii=False)
    assert "/api/resources" not in serialized
    assert "observed finding" not in serialized


def test_non_reversible_cleanup_remains_a_commercial_weakness() -> None:
    observed = deepcopy(_observed_v12())
    sandbox = observed["evidence_graphs"][0]["execution_trace"]["sandbox_write"]
    sandbox["status"] = "cleanup_incomplete"
    sandbox["cleanup"] = {"status": "not_reversible", "receipt_ref": "/api/resources/{id}"}

    ledger = build_discovery_trace_ledger(
        observed,
        run_id="RUN-NONREVERSIBLE",
        policy_id="POLICY-1",
        target_id="TARGET-1",
        project_id="PROJECT-1",
        industry="industry-a",
        evaluation_mode="replay",
    )
    trace = next(item for item in ledger["traces"] if item["behavior_slice_id"] == "BHV_INVALID")
    assert "SANDBOX_WRITE_INCOMPLETE" in trace["failure_signatures"]
    assert "CLEANUP_NOT_REVERSIBLE" in trace["failure_signatures"]

    report = mine_discovery_weaknesses([ledger])
    pattern = next(item for item in report["patterns"] if item["failure_signature"] == "CLEANUP_NOT_REVERSIBLE")
    assert pattern["severity"] == "critical"
    assert pattern["harness_surface"] == "sandbox_write_policy"


def test_multi_write_scenario_requires_one_governed_receipt_per_write() -> None:
    observed = deepcopy(_observed_v12())
    trace = observed["evidence_graphs"][0]["execution_trace"]
    trace["steps"].insert(
        1,
        {
            "action": "bootstrap_create_id",
            "method": "POST",
            "path": "/api/resources",
            "status": 201,
        },
    )
    trace["steps"][2]["status"] = 200
    trace["steps"][2].pop("skipped_reason", None)
    trace["sandbox_write"]["status"] = "completed"
    trace["sandbox_write"]["cleanup"] = {"status": "completed", "receipt_ref": "cleanup-1"}

    ledger = build_discovery_trace_ledger(
        observed,
        run_id="RUN-MULTI-WRITE",
        policy_id="POLICY-1",
        target_id="TARGET-1",
        project_id="PROJECT-1",
        industry="industry-a",
        evaluation_mode="replay",
    )
    item = next(row for row in ledger["traces"] if row["behavior_slice_id"] == "BHV_INVALID")
    assert item["execution"]["write_step_count"] == 2
    assert item["execution"]["governed_write_receipt_count"] == 1
    assert "MULTI_WRITE_AUDIT_INCOMPLETE" in item["failure_signatures"]

    report = mine_discovery_weaknesses([ledger])
    pattern = next(row for row in report["patterns"] if row["failure_signature"] == "MULTI_WRITE_AUDIT_INCOMPLETE")
    assert pattern["severity"] == "critical"
