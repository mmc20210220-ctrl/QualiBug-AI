from __future__ import annotations

from ai_test_asset_center.adaptive_behavior_ir_expansion import (
    expand_behavior_ir_from_runtime_observations,
)
from ai_test_asset_center.runtime_fact_candidate import (
    CANDIDATE_SCHEMA,
    LEDGER_SCHEMA,
    candidates_as_observation_receipts,
    grade_runtime_fact_candidates,
    project_runtime_fact_candidates,
    related_blocked_obligation_ids,
    reproject_experimentability_with_candidates,
)


def test_project_candidates_from_observation_and_execution() -> None:
    ledger = project_runtime_fact_candidates(
        observation_receipts=[
            {
                "receipt_id": "obs_1",
                "method": "GET",
                "path": "/api/users/addresses",
                "status_code": 200,
                "response_fingerprint": "a" * 64,
            }
        ],
        execution_results={
            "obl_1": {
                "observer_receipts": [
                    {
                        "receipt_id": "or_1",
                        "observer_id": "after_state",
                        "status": "OBSERVED",
                        "evidence": {"observation_path": "/api/orders/1"},
                    }
                ],
                "contract_evidence_receipts": [
                    {
                        "receipt_id": "cl_1",
                        "kind": "cleanup",
                        "status": "COMPLETED",
                        "evidence": {
                            "method": "DELETE",
                            "path": "/api/orders/1",
                        },
                    }
                ],
            }
        },
        campaign_id="camp_1",
    )
    assert ledger["schema_version"] == LEDGER_SCHEMA
    assert ledger["candidate_count"] >= 3
    kinds = {row["kind"] for row in ledger["candidates"]}
    assert "runtime_operation" in kinds
    assert "runtime_observation_path" in kinds
    assert "runtime_cleanup_capability" in kinds
    assert all(row["schema_version"] == CANDIDATE_SCHEMA for row in ledger["candidates"])
    assert all(row["authority_grade"] == "RUNTIME_OBSERVED" for row in ledger["candidates"])


def test_grade_never_promotes_high_authority() -> None:
    ledger = project_runtime_fact_candidates(
        observation_receipts=[
            {
                "receipt_id": "obs_1",
                "method": "GET",
                "path": "/api/resources",
                "status_code": 200,
                "response_fingerprint": "b" * 64,
            }
        ],
        campaign_id="camp_1",
    )
    asset = {
        "enterprise_understanding_model": {
            "operations": [{"method": "GET", "path": "/api/resources"}],
            "facts": [],
        }
    }
    graded = grade_runtime_fact_candidates(asset, ledger)
    assert graded["high_authority_promotions"] == 0
    assert graded["candidates"][0]["status"] == "NEEDS_AUTHORITY"


def test_related_blocked_includes_abstract_and_feedback_gaps() -> None:
    obligations = [
        {"obligation_id": "obl_body"},
        {"obligation_id": "obl_observer"},
        {"obligation_id": "obl_other"},
    ]
    experiments = {
        "obl_body": {
            "compile_receipt": {
                "status": "ABSTRACT",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "BODY_PARAMETER_NOT_SOURCE_BOUND:address_id",
            }
        },
        "obl_observer": {
            "compile_receipt": {
                "status": "ABSTRACT",
                "reason_code": "BLOCKED_MISSING_OBSERVER",
                "detail": "write_observer",
            }
        },
        "obl_other": {
            "compile_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_UNSUPPORTED_ADAPTER",
                "detail": "ui",
            }
        },
    }
    empty = project_runtime_fact_candidates(campaign_id="c")
    assert related_blocked_obligation_ids(
        obligations=obligations,
        experiments_by_obligation=experiments,
        ledger=empty,
    ) == {"obl_body"}

    with_ops = project_runtime_fact_candidates(
        observation_receipts=[
            {
                "receipt_id": "obs_1",
                "method": "GET",
                "path": "/api/orders/1",
                "status_code": 200,
                "response_fingerprint": "c" * 64,
            }
        ],
        campaign_id="c",
    )
    related = related_blocked_obligation_ids(
        obligations=obligations,
        experiments_by_obligation=experiments,
        ledger=with_ops,
    )
    assert "obl_body" in related
    assert "obl_observer" in related
    assert "obl_other" not in related


def test_recompile_only_expansion_without_new_operations(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_behavior_ir_expansion as expansion

    initial_ir = {"model_id": "model-1", "operations": [{"id": "op-1"}]}
    monkeypatch.setattr(
        expansion,
        "compile_obligations_from_behavior_ir",
        lambda behavior_ir: {
            "obligations": [
                {
                    "obligation_id": "obl_blocked",
                    "risk_family": "validation",
                    "source_refs": [{"source_id": "api_spec", "kind": "rule"}],
                    "required_operations": ["op-1"],
                },
                {
                    "obligation_id": "obl_other",
                    "risk_family": "validation",
                    "source_refs": [{"source_id": "api_spec", "kind": "rule"}],
                },
            ]
        },
    )
    monkeypatch.setattr(
        expansion,
        "compile_experiments",
        lambda obligations, **kwargs: {
            "experiments": [
                {
                    "obligation_id": "obl_blocked",
                    "experiment_id": "exp_blocked",
                    "compile_receipt": {"status": "COMPILED"},
                    "observers": [
                        {"observer_id": "http_response", "adapter": "http_api"}
                    ],
                    "source_refs": [{"source_id": "api_spec", "kind": "rule"}],
                    "control_plan": [],
                    "treatment_plan": [
                        {
                            "operation_ref": "op-1",
                            "method": "GET",
                            "path": "/api/resources",
                            "actor_ref": "actor_a",
                        }
                    ],
                }
            ],
            "blocked_experiments": [],
        },
    )
    monkeypatch.setattr(
        expansion,
        "attach_fixture_dag_to_experiments",
        lambda pack, **kwargs: pack,
    )
    result = expand_behavior_ir_from_runtime_observations(
        initial_behavior_ir=initial_ir,
        existing_obligation_ids={"obl_blocked", "obl_other"},
        recompile_obligation_ids={"obl_blocked"},
        knowledge_asset={"asset_id": "a1"},
        documented_operations=[
            {"method": "GET", "path": "/api/resources", "source_id": "api"}
        ],
        observation_receipts=[],
        project_id="p",
        source_snapshot_hash="hash",
        runtime_actors=[],
        environment_type="test",
        policy_version="v1",
        budget=8,
        planning_round=3,
        planning_context={"project": "p"},
    )
    assert result["status"] == "RECOMPILED"
    assert result["round_receipt"]["stop_reason"] == "recompile_without_new_operations"
    assert result["recompile_obligations"][0]["obligation_id"] == "obl_blocked"
    assert result["delta_obligations"] == []


def test_candidates_as_observation_receipts_filters_non_candidates() -> None:
    ledger = {
        "candidates": [
            {
                "status": "CANDIDATE",
                "kind": "runtime_operation",
                "candidate_id": "rfc_1",
                "method": "GET",
                "path": "/api/x",
                "observation_fingerprint": "d" * 64,
                "source_refs": [],
            },
            {
                "status": "REJECTED",
                "kind": "runtime_operation",
                "candidate_id": "rfc_2",
                "method": "GET",
                "path": "/api/y",
            },
        ]
    }
    rows = candidates_as_observation_receipts(ledger)
    assert len(rows) == 1
    assert rows[0]["path"] == "/api/x"
    assert rows[0]["runtime_fact_candidate"] is True


def test_reproject_attaches_ledger_without_accepted_mutation() -> None:
    asset = {
        "enterprise_understanding_model": {
            "facts": [],
            "operations": [],
            "accepted_facts": [{"fact_id": "fact_1", "kind": "RULE"}],
        }
    }
    ledger = project_runtime_fact_candidates(
        observation_receipts=[
            {
                "receipt_id": "obs_1",
                "method": "GET",
                "path": "/api/z",
                "status_code": 200,
                "response_fingerprint": "e" * 64,
            }
        ],
        campaign_id="c",
    )
    updated_asset, graded = reproject_experimentability_with_candidates(asset, ledger)
    assert graded["high_authority_promotions"] == 0
    assert "runtime_fact_candidate_ledger" in updated_asset
    # Original ACCEPTED facts are not rewritten into candidates.
    assert updated_asset["enterprise_understanding_model"]["accepted_facts"] == [
        {"fact_id": "fact_1", "kind": "RULE"}
    ]
