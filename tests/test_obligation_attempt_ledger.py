from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign
from ai_test_asset_center.obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    build_obligation_attempt_ledger,
    derive_campaign_terminal_status,
)
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)


def _mainline_run() -> dict[str, str]:
    return {"run_id": "RUN-1", "campaign_id": "CMP-1"}


def test_every_selected_obligation_has_one_terminal_attempt() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[
            {"obligation_id": "obl-1", "candidate_id": "cand-1"},
            {"obligation_id": "obl-2", "candidate_id": "cand-2"},
        ],
        compile_results={
            "obl-1": {"status": "COMPILED", "experiment_id": "exp-1"},
            "obl-2": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "receipt_id": "compile-receipt-2",
            },
        },
        execution_results={
            "obl-1": {
                "status": "EXECUTED",
                "execution_id": "exec-1",
                "observation_receipt_ids": ["obs-1"],
                "oracle_receipt_id": "oracle-1",
                "elapsed_ms": 12,
            },
        },
        gate_results={
            "obl-1": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
                "gate_receipt_id": "gate-1",
            },
        },
    )

    assert ledger["schema_version"] == "qualibug.obligation-attempt-ledger.v1"
    assert ledger["selected_count"] == 2
    assert ledger["terminal_count"] == 2
    assert ledger["complete"] is True
    assert [row["obligation_id"] for row in ledger["attempts"]] == ["obl-1", "obl-2"]
    first = ledger["attempts"][0]
    assert first["experiment_id"] == "exp-1"
    assert first["execution_id"] == "exec-1"
    assert first["observation_receipt_ids"] == ["obs-1"]
    assert first["oracle_receipt_id"] == "oracle-1"
    assert first["gate_receipt_id"] == "gate-1"
    assert first["terminal_status"] == "REJECTED"
    assert first["cost_coverage_status"] == "UNKNOWN"
    assert ledger["ledger_fingerprint"]


def test_ledger_accounts_for_deferred_formal_obligations_without_counting_them_selected() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[
            {"obligation_id": "obl-selected", "selection_status": "SELECTED"},
            {
                "obligation_id": "obl-deferred",
                "selection_status": "DEFERRED_NOT_SELECTED",
            },
        ],
        compile_results={
            "obl-selected": {"status": "COMPILED", "experiment_id": "exp-1"},
            "obl-deferred": {
                "status": "DEFERRED",
                "reason_code": "OBLIGATION_NOT_IN_PLAN",
                "reason_detail": "BUDGET_EXHAUSTED",
            },
        },
        execution_results={
            "obl-selected": {
                "status": "EXECUTED",
                "execution_id": "exec-1",
                "observation_receipt_ids": ["obs-1"],
            },
        },
        gate_results={
            "obl-selected": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
            },
        },
    )

    assert ledger["selected_count"] == 1
    assert ledger["accounted_count"] == 2
    assert ledger["terminal_count"] == 2
    assert ledger["complete"] is True
    assert ledger["selection_status_counts"] == {
        "DEFERRED_NOT_SELECTED": 1,
        "SELECTED": 1,
    }


def test_attempt_ledger_preserves_validated_operational_terminal_receipt() -> None:
    operational_receipt = build_execution_operational_receipt(
        receipt_id="operational-exec-1",
        execution_status="EXECUTED",
        steps=[{"method": "GET", "path": "/resources", "status_code": 200}],
        cleanup_failures=0,
    )

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "COMPILED"}},
        execution_results={
            "obl-1": {
                "status": "EXECUTED",
                "operational_receipt": operational_receipt,
            }
        },
        gate_results={
            "obl-1": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
            }
        },
    )

    assert ledger["attempts"][0]["operational_receipt"] == operational_receipt


def test_attempt_ledger_preserves_terminal_reason_detail_for_blockers() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1", "operation_refs": ["op-pay"]}],
        compile_results={
            "obl-1": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OBSERVER",
                "detail": "GET /payments/order/{orderId}",
            }
        },
        execution_results={},
        gate_results={},
    )

    attempt = ledger["attempts"][0]
    assert attempt["reason_code"] == "BLOCKED_MISSING_OBSERVER"
    assert attempt["reason_detail"] == "GET /payments/order/{orderId}"
    assert attempt["stages"][0]["reason_detail"] == "GET /payments/order/{orderId}"


def test_duplicate_or_missing_terminal_receipt_fails_fast() -> None:
    with pytest.raises(ObligationAttemptLedgerError, match="duplicate_terminal_receipt:obl-1"):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}],
            compile_results={
                "obl-1": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING"}
            },
            execution_results={},
            gate_results={
                "obl-1": {"status": "REJECTED", "reason_code": "ORACLE_NOT_VIOLATED"}
            },
        )

    with pytest.raises(ObligationAttemptLedgerError, match="terminal_receipt_missing:obl-1"):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}],
            compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
            execution_results={"obl-1": {"status": "EXECUTED", "execution_id": "exec-1"}},
            gate_results={},
        )


def test_stage_receipts_must_follow_compile_execution_gate_order() -> None:
    with pytest.raises(
        ObligationAttemptLedgerError,
        match="execution_without_compiled_obligation:obl-1",
    ):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}],
            compile_results={},
            execution_results={
                "obl-1": {"status": "EXECUTED", "execution_id": "exec-1"}
            },
            gate_results={
                "obl-1": {"status": "REJECTED", "reason_code": "ORACLE_NOT_VIOLATED"}
            },
        )

    with pytest.raises(
        ObligationAttemptLedgerError,
        match="gate_without_executed_obligation:obl-1",
    ):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}],
            compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
            execution_results={},
            gate_results={
                "obl-1": {"status": "REJECTED", "reason_code": "ORACLE_NOT_VIOLATED"}
            },
        )


def test_selected_identity_and_foreign_receipts_fail_fast() -> None:
    with pytest.raises(ObligationAttemptLedgerError, match="selected_obligation_identity_invalid"):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}, {"obligation_id": "obl-1"}],
            compile_results={},
            execution_results={},
            gate_results={},
        )

    with pytest.raises(ObligationAttemptLedgerError, match="foreign_obligation_receipt:obl-other"):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}],
            compile_results={
                "obl-1": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING"},
                "obl-other": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING"},
            },
            execution_results={},
            gate_results={},
        )


def test_deliverable_requires_finding_identity_and_nonterminal_does_not_keep_one() -> None:
    with pytest.raises(ObligationAttemptLedgerError, match="formal_gate_v2_required:obl-1"):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}],
            compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
            execution_results={"obl-1": {"status": "EXECUTED", "execution_id": "exec-1"}},
            gate_results={"obl-1": {"status": "DELIVERABLE", "gate_receipt_id": "gate-1"}},
        )

    with pytest.raises(ObligationAttemptLedgerError, match="nondeliverable_finding_id_present:obl-1"):
        build_obligation_attempt_ledger(
            mainline_run=_mainline_run(),
            selected=[{"obligation_id": "obl-1"}],
            compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
            execution_results={"obl-1": {"status": "EXECUTED", "execution_id": "exec-1"}},
            gate_results={
                "obl-1": {
                    "status": "REJECTED",
                    "reason_code": "ORACLE_NOT_VIOLATED",
                    "finding_id": "finding-should-not-survive",
                }
            },
        )


def test_ledger_join_does_not_mutate_stage_receipts() -> None:
    compile_results = {"obl-1": {"status": "BLOCKED", "reason_code": "BLOCKED_POLICY"}}
    before = deepcopy(compile_results)

    build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results=compile_results,
        execution_results={},
        gate_results={},
    )

    assert compile_results == before


def test_ledger_facade_does_not_replace_core_validator() -> None:
    import ai_test_asset_center._obligation_attempt_ledger_single_occurrence_mechanics as core
    import ai_test_asset_center.obligation_attempt_ledger as facade

    assert core.validate_obligation_attempt_ledger is not facade.validate_obligation_attempt_ledger


def test_campaign_completion_is_derived_from_attempt_ledger_and_cannot_be_overwritten_by_slice_cycle() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "BLOCKED", "reason_code": "BLOCKED_POLICY"}},
        execution_results={},
        gate_results={},
    )
    # All-blocked remains visibly blocked — not a defect-free "completed" run.
    assert derive_campaign_terminal_status(ledger) == "blocked"

    campaign = EnterpriseCampaign.create(
        project_id="PROJECT-1",
        scope_id="scope-1",
        environment_ref="ENV-1",
        snapshot="snapshot-1",
    )
    campaign.campaign_id = "CMP-1"
    campaign.record_obligation_attempt_ledger(ledger)
    assert campaign.status == "blocked"
    assert campaign.public_contract()["completion_authority"] == "obligation_attempt_ledger"
    assert campaign.public_contract()["completion_is_formal"] is True

    campaign.record_cycle(
        round_number=1,
        selection={
            "selected_slice_ids": ["legacy-slice"],
            "remaining_slice_count": 1,
            "stop_reason": "configured_round_limit_reached",
            "next_round": None,
        },
        findings=[],
        coverage_gap_count=1,
        execution_status="completed",
        attempted_slice_ids=["legacy-slice"],
    )
    assert campaign.status == "blocked"
    assert campaign.audit_events[-1]["event"] == "legacy_cycle_projection"

    tampered = deepcopy(ledger)
    tampered["attempts"][0]["reason_code"] = "TAMPERED"
    with pytest.raises(
        ObligationAttemptLedgerError,
        match="obligation_attempt_ledger_fingerprint_mismatch",
    ):
        campaign.record_obligation_attempt_ledger(tampered)


def test_harness_failure_degrades_campaign_status() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
        execution_results={
            "obl-1": {"status": "HARNESS_FAILED", "reason_code": "ORACLE_EXCEPTION"}
        },
        gate_results={},
    )

    assert derive_campaign_terminal_status(ledger) == "degraded"


def test_zero_selected_obligations_block_campaign_completion() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-EMPTY", "campaign_id": "CMP-EMPTY"},
        selected=[],
        compile_results={},
        execution_results={},
        gate_results={},
    )

    assert derive_campaign_terminal_status(ledger) == "blocked"


def test_campaign_can_retry_only_when_prior_attempts_never_executed() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={
            "obl-1": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_RUNTIME_TARGET",
            }
        },
        execution_results={},
        gate_results={},
    )
    campaign = EnterpriseCampaign.create(
        project_id="PROJECT-1",
        scope_id="scope-1",
        environment_ref="ENV-1",
        snapshot="snapshot-1",
    )
    campaign.campaign_id = "CMP-1"
    campaign.record_obligation_attempt_ledger(ledger)

    assert campaign.reopen_for_unexecuted_attempt_retry(
        reason="runtime_contract_now_approved"
    ) is True
    assert campaign.status == "active"
    assert campaign.obligation_attempt_ledger_fingerprint == ""
    assert campaign.audit_events[-1]["event"] == "obligation_attempt_retry"


def test_campaign_never_retries_after_an_execution_receipt() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "COMPILED"}},
        execution_results={"obl-1": {"status": "EXECUTED"}},
        gate_results={
            "obl-1": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
            }
        },
    )
    campaign = EnterpriseCampaign.create(
        project_id="PROJECT-1",
        scope_id="scope-1",
        environment_ref="ENV-1",
        snapshot="snapshot-1",
    )
    campaign.campaign_id = "CMP-1"
    campaign.record_obligation_attempt_ledger(ledger)

    assert campaign.reopen_for_unexecuted_attempt_retry(
        reason="runtime_contract_now_approved"
    ) is False
    assert campaign.obligation_attempt_ledger_fingerprint


def test_campaign_never_retries_after_an_observation_receipt() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "COMPILED"}},
        execution_results={
            "obl-1": {
                "status": "BLOCKED",
                "reason_code": "POST_REQUEST_PRECONDITION_FAILED",
                "observation_receipt_ids": ["observation-1"],
            }
        },
        gate_results={},
    )
    campaign = EnterpriseCampaign.create(
        project_id="PROJECT-1",
        scope_id="scope-1",
        environment_ref="ENV-1",
        snapshot="snapshot-1",
    )
    campaign.campaign_id = "CMP-1"
    campaign.record_obligation_attempt_ledger(ledger)

    assert campaign.reopen_for_unexecuted_attempt_retry(
        reason="runtime_contract_now_approved"
    ) is False
    assert campaign.obligation_attempt_ledger_fingerprint


def test_artifact_redaction_reseals_obligation_attempt_ledger() -> None:
    """Secret redaction rewrites sealed strings; persistence must reseal."""

    from ai_test_asset_center.artifact_redactor import redact_and_validate
    from ai_test_asset_center.obligation_attempt_ledger import (
        validate_obligation_attempt_ledger,
    )
    from ai_test_asset_center.observer_contracts_base import build_observer_receipt
    from ai_test_asset_center.sealed_receipt_reseal import reseal_observer_receipt

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={
            "obl-1": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "reason_detail": (
                    "token Bearer "
                    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa.bbbbccccdddd"
                ),
            }
        },
        execution_results={},
        gate_results={},
    )
    validate_obligation_attempt_ledger(ledger)

    redacted, _ = redact_and_validate({"obligation_attempt_ledger": ledger})
    sealed = redacted["obligation_attempt_ledger"]
    assert "Bearer <REDACTED_JWT>" in sealed["attempts"][0]["reason_detail"]
    validate_obligation_attempt_ledger(sealed)

    observer = build_observer_receipt(
        observer_id="http_status",
        status="OBSERVED",
        evidence={
            "note": (
                "token Bearer "
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa.bbbbccccdddd"
            )
        },
        campaign_id="CMP-1",
        execution_id="exec-1",
    )
    redacted_obs, _ = redact_and_validate(observer)
    assert "Bearer <REDACTED_JWT>" in redacted_obs["evidence"]["note"]
    # Leaf reseal restores content-addressed identity after redaction.
    from ai_test_asset_center.observer_contracts_base import validate_observer_receipt

    validate_observer_receipt(reseal_observer_receipt(redacted_obs))


def _execution_blocked_result(
    *,
    contracts: list[dict[str, Any]] | None = None,
    observers: list[dict[str, Any]] | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mirror the real experiment-execution.v1 payload the finalizer emits.

    The finalizer returns ``steps`` / ``observer_receipts`` /
    ``contract_evidence_receipts`` at the top level of the execution result
    even when the experiment is BLOCKED. The ledger used to discard them for
    any attempt that never reached a delivery gate.
    """

    result: dict[str, Any] = {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_MISSING_BINDING",
    }
    if contracts is not None:
        result["contract_evidence_receipts"] = contracts
    if observers is not None:
        result["observer_receipts"] = observers
    if steps is not None:
        result["steps"] = steps
    return result


def test_execution_diagnostic_bundle_retains_execution_blocked_evidence() -> None:
    """End-to-end: a blocked experiment's evidence must survive on a diagnostic
    channel when no delivery gate exists (regression for the opaque funnel)."""

    from ai_test_asset_center.obligation_attempt_ledger import (
        reseal_obligation_attempt_ledger,
        validate_obligation_attempt_ledger,
    )

    contracts = [
        {
            "obligation_id": "obl-1",
            "evidence": {
                "status_code": 500,
                "control_succeeded": False,
                "response_observed": True,
            },
        },
        {
            "obligation_id": "obl-1",
            "evidence": {
                "status_code": 500,
                "control_succeeded": False,
                "response_observed": True,
            },
        },
    ]
    observers = [
        {"observer_id": "http_status", "status": "OBSERVED"},
        {"observer_id": "business_state", "status": "OBSERVED"},
    ]
    steps = [
        {
            "method": "POST",
            "path": "/coupons/redeem",
            "status_code": 500,
            "step_index": i,
        }
        for i in range(3)
    ]

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
        execution_results={
            "obl-1": _execution_blocked_result(
                contracts=contracts, observers=observers, steps=steps
            )
        },
        gate_results={},
    )

    attempt = ledger["attempts"][0]
    # No delivery gate -> no sealed bundle, but the diagnostic channel must fire.
    assert "delivery_evidence_bundle" not in attempt
    diag = attempt["execution_diagnostic_bundle"]
    assert diag["contract_evidence_receipts"] == contracts
    assert diag["observer_receipts"] == observers
    assert diag["steps"] == steps
    assert "steps_truncated_from" not in diag

    # The new channel must not break validation or redaction reseal.
    validate_obligation_attempt_ledger(ledger)
    resealed = reseal_obligation_attempt_ledger(ledger)
    assert resealed["attempts"][0]["execution_diagnostic_bundle"] == diag


def test_execution_diagnostic_bundle_absent_when_no_execution_evidence() -> None:
    """A compile-stage block with no execution payload must NOT invent evidence."""

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1", "operation_refs": ["op-pay"]}],
        compile_results={
            "obl-1": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OBSERVER",
                "detail": "GET /payments/order/{orderId}",
            }
        },
        execution_results={},
        gate_results={},
    )

    attempt = ledger["attempts"][0]
    assert "execution_diagnostic_bundle" not in attempt
    assert "delivery_evidence_bundle" not in attempt


def test_execution_diagnostic_bundle_truncates_steps_with_visible_marker() -> None:
    """Large step lists are capped, and the cap is marked rather than hidden."""

    steps = [
        {"method": "POST", "path": "/x", "status_code": 500, "step_index": i}
        for i in range(25)
    ]

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
        execution_results={"obl-1": _execution_blocked_result(steps=steps)},
        gate_results={},
    )

    diag = ledger["attempts"][0]["execution_diagnostic_bundle"]
    assert len(diag["steps"]) == 20
    assert diag["steps_truncated_from"] == 25
