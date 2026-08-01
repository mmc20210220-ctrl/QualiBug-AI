from __future__ import annotations

from ai_test_asset_center.blocker_attribution import profile_reason_code
from ai_test_asset_center.discovery_funnel import (
    build_funnel,
    build_funnel_comparison_report,
    build_funnel_conservation,
    build_funnel_report,
    render_funnel_report_markdown,
)
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
    validate_mainline_run_contract,
)
from ai_test_asset_center.obligation_attempt_ledger import (
    build_obligation_attempt_ledger,
    validate_obligation_attempt_ledger,
)


def _run() -> dict[str, str]:
    return {
        "run_id": "RUN-CLOSURE",
        "campaign_id": "CMP-CLOSURE",
        "target_id": "TARGET-CLOSURE",
        "environment_id": "ENV-CLOSURE",
        "policy_version": "POLICY-CLOSURE",
        "evaluation_mode": "replay",
        "source_snapshot_hash": "sha256:source-closure",
        "contract_fingerprint": "contract-closure",
    }


def _result() -> dict:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_run(),
        selected=[
            {
                "obligation_id": "obl-1",
                "risk_family": "validation",
                "operation_refs": ["op-1"],
                "actor_refs": ["actor-1"],
                "source_refs": [{"source_id": "src-1", "source_hash": "sha256:source-closure"}],
            },
            {
                "obligation_id": "obl-2",
                "risk_family": "state",
                "operation_refs": ["op-2"],
                "source_refs": [{"source_id": "src-1", "source_hash": "sha256:source-closure"}],
            },
        ],
        compile_results={
            "obl-1": {"status": "COMPILED", "experiment_id": "exp-1"},
            "obl-2": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_OBSERVER"},
        },
        execution_results={
            "obl-1": {
                "status": "EXECUTED",
                "execution_id": "exec-1",
                "observation_receipt_ids": ["obs-1"],
                "oracle_receipt_id": "oracle-1",
            }
        },
        gate_results={
            "obl-1": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
                "gate_receipt_id": "gate-1",
            }
        },
    )
    return {
        "obligation_attempt_ledger": ledger,
        "test_obligations": {
            "obligations": [{"obligation_id": "obl-1"}, {"obligation_id": "obl-2"}]
        },
        "formal_count_projection": {
            "schema_version": "qualibug.discovery-quality-projection.v2",
            "formal_customer_deliverable_count": 0,
            "canonical_defect_ids": [],
            "delivery_occurrence_count": 0,
            "delivery_occurrence_finding_ids": [],
        },
    }


def test_attempt_ledger_carries_identity_and_explicit_reason_family() -> None:
    result = _result()
    ledger = result["obligation_attempt_ledger"]
    validate_obligation_attempt_ledger(ledger)

    assert ledger["identity"]["source_snapshot_hash"] == "sha256:source-closure"
    assert ledger["identity"]["status"] == "COMPLETE"
    blocked = ledger["attempts"][1]
    assert blocked["reason_family"] == "OBSERVER_CAPABILITY_GAP"
    assert blocked["reason_registry_status"] == "REGISTERED"
    assert blocked["stages"][0]["identity"]["obligation_id"] == "obl-2"


def test_funnel_conservation_uses_stage_receipts() -> None:
    result = _result()
    conservation = build_funnel_conservation(result)

    assert conservation["status"] == "PASS"
    assert conservation["complete"] is True
    assert conservation["selected_count"] == 2
    assert conservation["execution_count"] == 1
    assert conservation["execution_unresolved_count"] == 0
    assert all(check["status"] == "PASS" for check in conservation["checks"])


def test_report_exposes_top_blockers_without_claiming_external_quality() -> None:
    result = _result()
    funnel = build_funnel(result)
    report = build_funnel_report(result, funnel=funnel)

    assert report["quality"]["status"] == "NOT_MEASURED"
    assert report["quality"]["recall"] == "NOT_MEASURED"
    assert report["top_blocking_reasons"][0]["reason"] == "BLOCKED_MISSING_OBSERVER"
    markdown = render_funnel_report_markdown(report)
    assert "BLOCKED_MISSING_OBSERVER" in markdown
    assert "NOT_MEASURED" in markdown
    assert "stack_trace" not in markdown.lower()


def test_unknown_reason_is_visible_and_never_inferred_from_detail() -> None:
    profile = profile_reason_code("NEW_REASON_FROM_UNREGISTERED_EMITTER")

    assert profile["registry_status"] == "UNREGISTERED"
    assert profile["reason_family"] == "UNREGISTERED"
    assert profile["is_blocking"] is True


def test_comparison_does_not_invent_a_missing_candidate() -> None:
    report = build_funnel_comparison_report(_result())

    assert report["status"] == "NOT_MEASURED"
    assert report["candidate"]["quality"]["basis"] == "candidate_receipt_missing"
    assert report["delta"]["metrics"] == "NOT_MEASURED"
    assert report["quality_boundary"]["recall"] == "NOT_MEASURED"


def test_source_snapshot_hash_stays_in_the_immutable_mainline_identity() -> None:
    contract = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-CLOSURE-CONTRACT",
        campaign_id="CMP-CLOSURE-CONTRACT",
        target_id="TARGET-CLOSURE-CONTRACT",
        environment_id="ENV-CLOSURE-CONTRACT",
        policy_version="POLICY-CLOSURE-CONTRACT",
        evaluation_mode="replay",
        source_snapshot_hash="sha256:closure-contract",
    )

    assert contract["source_snapshot_hash"] == "sha256:closure-contract"
    assert validate_mainline_run_contract(contract) == contract
