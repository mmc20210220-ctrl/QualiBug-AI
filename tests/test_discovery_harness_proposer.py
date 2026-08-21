from __future__ import annotations

from dataclasses import asdict

import pytest

from ai_test_asset_center.discovery_harness_proposer import (
    HARNESS_PROPOSAL_SCHEMA,
    HarnessProposalError,
    apply_bounded_harness_edit,
    materialize_policy_candidate,
    propose_harness_candidates,
    validate_strategy_guardrails,
)
from ai_test_asset_center.discovery_weakness_miner import WEAKNESS_REPORT_SCHEMA
from ai_test_asset_center.policy_registry import PolicyRecord, StrategyBundle


def _weakness_report() -> dict:
    patterns = [
        {
            "pattern_id": "WEAK-CLEANUP",
            "failure_signature": "CLEANUP_FAILED",
            "harness_surface": "sandbox_write_policy",
            "failure_mechanism": "cleanup fails",
            "observed_count": 4,
            "affected_run_count": 2,
            "affected_industry_count": 2,
            "example_trace_ids": ["TRACE-1", "TRACE-2"],
            "preserved_good_trace_ids": ["TRACE-GOOD"],
            "proposal_eligible": True,
        },
        {
            "pattern_id": "WEAK-BINDING",
            "failure_signature": "RUNTIME_PATH_BINDING_MISSING",
            "harness_surface": "runtime_binding",
            "failure_mechanism": "runtime id missing",
            "observed_count": 6,
            "affected_run_count": 2,
            "affected_industry_count": 2,
            "example_trace_ids": ["TRACE-3"],
            "preserved_good_trace_ids": [],
            "proposal_eligible": True,
        },
        {
            "pattern_id": "WEAK-FROZEN",
            "failure_signature": "ORACLE_CONFIRMED_NON_EXECUTION",
            "harness_surface": "verifier_orchestration",
            "failure_mechanism": "non-execution oracle vote",
            "observed_count": 1,
            "affected_run_count": 1,
            "affected_industry_count": 1,
            "example_trace_ids": ["TRACE-4"],
            "preserved_good_trace_ids": [],
            "proposal_eligible": True,
        },
    ]
    return {
        "schema_version": WEAKNESS_REPORT_SCHEMA,
        "source_run_ids": ["RUN-1", "RUN-2"],
        "source_policy_ids": ["policy-parent"],
        "patterns": patterns,
        "selected_patterns_for_proposal": [item["pattern_id"] for item in patterns],
    }


def _parent() -> PolicyRecord:
    return PolicyRecord(
        policy_id="policy-parent",
        policy_version="v1.0.0",
        parent_policy_version="",
        project_scope="global",
        status="active",
        created_reason="test parent",
        strategy=StrategyBundle(),
    )


def test_proposals_are_minimal_evidence_bound_and_guarded() -> None:
    parent = _parent()
    report = propose_harness_candidates(_weakness_report(), parent.strategy, max_proposals=20)

    assert report["schema_version"] == HARNESS_PROPOSAL_SCHEMA
    assert report["proposal_count"] >= 4
    assert report["editable_surface_contract"]["arbitrary_code_edits_allowed"] is False
    assert report["editable_surface_contract"]["ground_truth_access_allowed"] is False
    assert any(item["failure_signature"] == "ORACLE_CONFIRMED_NON_EXECUTION" for item in report["blocked_patterns"])
    assert len({item["candidate_strategy_signature"] for item in report["proposals"]}) == report["proposal_count"]
    for proposal in report["proposals"]:
        assert proposal["minimal_edit_count"] == 1
        assert proposal["source_pattern_id"]
        assert proposal["evidence"]["example_trace_ids"]
        assert proposal["guardrails"]["passed"] is True
        assert len(proposal["regression_obligations"]) >= 5
        assert proposal["candidate_strategy_signature"] != proposal["parent_strategy_signature"]


def test_frozen_safety_and_model_guardrail_paths_cannot_be_proposed() -> None:
    strategy = StrategyBundle()
    for path, value in (
        ("verification.verifier_relaxed", True),
        ("reasoner.timeout_seconds", 1),
        ("execution.require_cleanup_receipt", False),
    ):
        with pytest.raises(HarnessProposalError, match="frozen"):
            apply_bounded_harness_edit(
                strategy,
                {"path": path, "operation": "set_integer", "value": value},
            )


def test_materialized_policy_remains_candidate_until_real_evaluation() -> None:
    parent = _parent()
    report = propose_harness_candidates(_weakness_report(), parent.strategy, max_proposals=1)
    proposal = report["proposals"][0]

    candidate = materialize_policy_candidate(proposal, parent)

    assert candidate.status == "candidate"
    assert candidate.parent_policy_version == parent.policy_version
    assert candidate.evaluation_summary["status"] == "awaiting_paired_replay_shadow"
    assert candidate.strategy != parent.strategy
    assert validate_strategy_guardrails(candidate.strategy)["passed"] is True
    assert candidate.strategy.reasoner.timeout_seconds >= 300
    assert candidate.strategy.reasoner.max_tokens >= 32768
    assert candidate.strategy.reasoner.max_workers <= 4
    assert candidate.strategy.reasoner.max_hypotheses_per_engine == 64


def test_tampered_candidate_strategy_cannot_be_materialized() -> None:
    parent = _parent()
    report = propose_harness_candidates(_weakness_report(), parent.strategy, max_proposals=1)
    proposal = dict(report["proposals"][0])
    proposal["candidate_strategy_signature"] = "tampered"

    with pytest.raises(HarnessProposalError, match="fingerprint"):
        materialize_policy_candidate(proposal, parent)


def test_strategy_serialization_preserves_new_generic_surfaces() -> None:
    strategy = StrategyBundle()
    payload = asdict(strategy)

    assert payload["discovery"]["require_documented_endpoint"] is True
    assert "source_operation_id" in payload["discovery"]["endpoint_binding_strategy"]
    assert payload["verification"]["reject_non_execution_oracle_votes"] is True
    assert payload["execution"]["require_cleanup_receipt"] is True
    assert payload["execution"]["persist_cross_round_traces"] is True
