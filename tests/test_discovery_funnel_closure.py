from __future__ import annotations

from ai_test_asset_center.blocker_attribution import profile_reason_code
from ai_test_asset_center.discovery_funnel import (
    _reason_details,
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
    bind_stage_receipt_identity,
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


def _stage_identity(obligation_id: str) -> dict[str, str]:
    return {
        "obligation_id": obligation_id,
        "run_id": "RUN-CLOSURE",
        "campaign_id": "CMP-CLOSURE",
        "target_id": "TARGET-CLOSURE",
        "environment_id": "ENV-CLOSURE",
        "policy_version": "POLICY-CLOSURE",
        "evaluation_mode": "replay",
        "source_snapshot_hash": "sha256:source-closure",
        "mainline_contract_fingerprint": "contract-closure",
    }


def _stage_identity_fields(
    obligation_id: str,
    include_stage_identity: bool,
) -> dict[str, str]:
    return _stage_identity(obligation_id) if include_stage_identity else {}


def _result(
    *,
    include_stage_identity: bool = True,
    gate_status: str = "REJECTED",
    gate_reason: str = "ORACLE_NOT_VIOLATED",
) -> dict:
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
            "obl-1": {
                "status": "COMPILED",
                "experiment_id": "exp-1",
                **_stage_identity_fields("obl-1", include_stage_identity),
            },
            "obl-2": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OBSERVER",
                **_stage_identity_fields("obl-2", include_stage_identity),
            },
        },
        execution_results={
            "obl-1": {
                "status": "EXECUTED",
                "execution_id": "exec-1",
                "observation_receipt_ids": ["obs-1"],
                "oracle_receipt_id": "oracle-1",
                **_stage_identity_fields("obl-1", include_stage_identity),
            }
        },
        gate_results={
            "obl-1": {
                "status": gate_status,
                "reason_code": gate_reason,
                "gate_receipt_id": "gate-1",
                **_stage_identity_fields("obl-1", include_stage_identity),
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


def test_funnel_conservation_counts_only_receipted_oracle_violations() -> None:
    result = _result()
    assert build_funnel_conservation(result)["oracle_violation_count"] == 0

    non_violation_result = _result(
        gate_status="REJECTED",
        gate_reason="ASSERTION_NOT_VIOLATED",
    )
    assert build_funnel_conservation(non_violation_result)["oracle_violation_count"] == 0

    violation_result = _result(
        gate_status="REJECTED",
        gate_reason="BLOCKED_MISSING_OBSERVER",
    )
    assert build_funnel_conservation(violation_result)["oracle_violation_count"] == 1


def test_funnel_fails_safe_on_duplicate_formal_obligation_rows() -> None:
    result = _result()
    result["test_obligations"]["obligations"].append({"obligation_id": "obl-1"})

    conservation = build_funnel_conservation(result)

    assert conservation["status"] == "FAILED_SAFE"
    assert conservation["generated_row_count"] == 3
    assert conservation["generated_count"] == 2
    assert conservation["generated_duplicate_count"] == 1
    assert conservation["generated_duplicate_ids"] == ["obl-1"]
    assert any(
        check["name"] == "formal_obligation_identity_unique"
        and check["status"] == "FAILED_SAFE"
        for check in conservation["checks"]
    )
    report = build_funnel_report(result)
    assert report["report_status"] == "FAILED_SAFE"
    assert report["metrics"]["generated_count"] == 2


def test_funnel_exposes_missing_stage_identity_without_rederiving_it() -> None:
    result = _result(include_stage_identity=False)

    conservation = build_funnel_conservation(result)

    assert conservation["status"] == "INCOMPLETE"
    assert conservation["complete"] is False
    assert conservation["identity_status"] == "INCOMPLETE"
    assert conservation["identity_stage_gaps"]
    assert conservation["identity_stage_gaps"][0]["status"] == "INCOMPLETE"


def test_mainline_binds_immutable_identity_before_sealing_stage_receipts() -> None:
    compile_results, execution_results, gate_results = bind_stage_receipt_identity(
        mainline_run=_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={
            "obl-1": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
            }
        },
        execution_results={},
        gate_results={},
    )

    ledger = build_obligation_attempt_ledger(
        mainline_run=_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )

    stage_identity = ledger["attempts"][0]["stages"][0]["identity"]
    assert stage_identity["status"] == "COMPLETE"
    assert stage_identity["obligation_id"] == "obl-1"
    assert stage_identity["observation_source"] == "mainline_contract_binding"


def test_mainline_binds_sealed_gate_stage_identity_outside_gate_payload() -> None:
    from ai_test_asset_center._obligation_attempt_ledger_single_occurrence_mechanics import (
        _run_identity,
        _stage_identity,
    )
    from ai_test_asset_center.customer_delivery_gate_v2 import (
        CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    )

    gate = {
        "schema_version": CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
        "status": "REJECTED",
        "reason_code": "ORACLE_NOT_VIOLATED",
    }
    _, execution_results, gate_results = bind_stage_receipt_identity(
        mainline_run=_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={},
        execution_results={
            "obl-1": {
                "status": "EXECUTED",
                "executed_obligation_id": "obl-1",
            }
        },
        gate_results={"obl-1": gate},
    )

    bound_gate = gate_results["obl-1"]
    assert "identity" not in bound_gate
    assert bound_gate["stage_identity_receipt"]["policy_version"] == "POLICY-CLOSURE"
    stage_identity = _stage_identity(
        stage="gate",
        obligation_id="obl-1",
        receipt=bound_gate,
        identity=_run_identity(_run(), execution_results["obl-1"], bound_gate),
    )
    assert stage_identity["status"] == "COMPLETE"
    assert stage_identity["observation_source"] == "mainline_contract_binding"


def test_runner_exception_is_terminal_harness_failure_without_request_claim() -> None:
    from types import SimpleNamespace

    from ai_test_asset_center.discovery_mainline import (
        _build_runner_exception_ledger,
    )
    from ai_test_asset_center.discovery_mainline_contract import (
        build_mainline_run_contract,
    )

    contract = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-EXCEPTION",
        campaign_id="CMP-EXCEPTION",
        target_id="TARGET-EXCEPTION",
        environment_id="ENV-EXCEPTION",
        policy_version="POLICY-EXCEPTION",
        evaluation_mode="replay",
        source_snapshot_hash="sha256:source-exception",
    )
    plan = SimpleNamespace(
        mainline_run=contract,
        experiments={
            "obligation_plan": {
                "selected": [{"obligation_id": "obl-exception"}]
            },
            "by_obligation": {
                "obl-exception": {
                    "experiment_id": "exp-exception",
                    "compile_receipt": {"status": "COMPILED"},
                }
            },
        },
    )

    ledger = _build_runner_exception_ledger(
        plan,
        RuntimeError("transport failure"),
    )
    attempt = ledger["attempts"][0]

    assert ledger["complete"] is True
    assert attempt["terminal_status"] == "HARNESS_FAILED"
    assert attempt["reason_code"] == "MAINLINE_RUNTIME_EXCEPTION"
    assert attempt["reason_registry_status"] == "REGISTERED"
    assert attempt["stages"][1]["identity"]["status"] == "COMPLETE"


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


def test_report_projects_captured_oracle_detail_for_legacy_gate_receipts() -> None:
    blockers, unregistered = _reason_details([
        {
            "obligation_id": "obl-harness",
            "reason_code": "CONTRACT_ORACLE_HARNESS_FAILED",
            "delivery_evidence_bundle": {
                "oracle_receipt": {
                    "activation_receipt": {
                        "reason_codes": [
                            "CLEANUP_RECEIPT_FAILED:cleanup:cleanup-1",
                        ],
                    },
                },
            },
        },
    ])

    assert not unregistered
    assert blockers[0]["examples"][0]["reason_detail"] == (
        "CLEANUP_RECEIPT_FAILED:cleanup:cleanup-1"
    )


def test_unknown_reason_is_visible_and_never_inferred_from_detail() -> None:
    profile = profile_reason_code("NEW_REASON_FROM_UNREGISTERED_EMITTER")

    assert profile["registry_status"] == "UNREGISTERED"
    assert profile["reason_family"] == "UNREGISTERED"
    assert profile["is_blocking"] is True


def test_cleanup_gate_reason_codes_are_registered() -> None:
    for reason_code in (
        "CLEANUP_EVIDENCE_INCOMPLETE",
        "CLEANUP_WRITE_COVERAGE_MISMATCH",
    ):
        profile = profile_reason_code(reason_code)

        assert profile["registry_status"] == "REGISTERED"
        assert profile["reason_family"] == "CLEANUP_CAPABILITY_GAP"
        assert profile["is_blocking"] is True


def test_comparison_does_not_invent_a_missing_candidate() -> None:
    report = build_funnel_comparison_report(_result())

    assert report["status"] == "NOT_MEASURED"
    assert report["candidate"]["quality"]["basis"] == "candidate_receipt_missing"
    assert report["delta"]["metrics"] == "NOT_MEASURED"
    assert report["quality_boundary"]["recall"] == "NOT_MEASURED"


def _result_with_run_conditions(**overrides: object) -> dict:
    result = _result()
    result["mainline_run"] = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-CLOSURE",
        campaign_id="CMP-CLOSURE",
        target_id="TARGET-CLOSURE",
        environment_id="ENV-CLOSURE",
        policy_version="POLICY-CLOSURE",
        evaluation_mode="replay",
        source_snapshot_hash="sha256:source-closure",
    )
    result["run_conditions"] = {
        "execution_mode": "approved_sandbox_write",
        "budget_configured": 20,
        "budget_effective": 20,
        "model_provider": "provider-closure",
        "model_id": "model-closure",
        **overrides,
    }
    return result


def test_comparison_requires_budget_model_and_execution_conditions() -> None:
    report = build_funnel_comparison_report(_result(), _result())

    assert report["condition_check"]["status"] == "NOT_MEASURED"
    assert {
        "execution_mode",
        "budget_configured",
        "budget_effective",
        "model_provider",
        "model_id",
    }.issubset(set(report["condition_check"]["missing_fields"]))


def test_comparison_matches_only_identical_explicit_run_conditions() -> None:
    baseline = _result_with_run_conditions()
    candidate = _result_with_run_conditions()

    report = build_funnel_comparison_report(baseline, candidate)

    assert report["condition_check"]["status"] == "MATCH"
    assert report["condition_check"]["missing_fields"] == []


def test_comparison_exposes_budget_model_and_mode_mismatches() -> None:
    baseline = _result_with_run_conditions()
    candidate = _result_with_run_conditions(
        execution_mode="safe_read_only",
        budget_effective=21,
        model_id="model-other",
    )

    report = build_funnel_comparison_report(baseline, candidate)

    assert report["condition_check"]["status"] == "MISMATCH"
    assert {
        "execution_mode",
        "budget_effective",
        "model_id",
    }.issubset({
        row["field"] for row in report["condition_check"]["mismatches"]
    })


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
