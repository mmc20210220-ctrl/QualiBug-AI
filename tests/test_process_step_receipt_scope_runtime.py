from ai_test_asset_center.experiment_lifecycle_runtime import (
    attach_lifecycle_ledger,
    record_stage_event,
)
from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_receipt_scope import (
    extract_receipt_step_scope,
    synchronize_scoped_receipts_from_observations,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="experiment-1",
        fixture_id="fixture-1",
        campaign_id="campaign-1",
        run_id="run-1",
        obligation_id="obligation-1",
        protocol_id="protocol-1",
        required_step_ids=["step-1", "step-2"],
    )
    for ordinal in (1, 2):
        ledger.record_step_execution(
            step_id=f"step-{ordinal}",
            phase="treatment",
            operation_ref=f"operation-{ordinal}",
            actor_ref="actor-1",
            request_receipt_id=f"request-{ordinal}",
            response_receipt_id=f"response-{ordinal}",
            status_code=200,
            final_status="EXECUTED",
            mutation_occurred=True,
        )
    return ledger


def test_scope_extractor_requires_one_explicit_scalar_step() -> None:
    assert extract_receipt_step_scope({"receipt_id": "missing"})["status"] == (
        "STEP_SCOPE_MISSING"
    )
    assert extract_receipt_step_scope(
        {"receipt_id": "multi", "step_ids": ["step-1", "step-2"]}
    )["status"] == "MULTI_STEP_SCOPE_FORBIDDEN"
    assert extract_receipt_step_scope(
        {"receipt_id": "exact", "payload": {"step_id": "step-1"}},
        known_step_ids=["step-1", "step-2"],
    )["status"] == "EXACT"
    assert extract_receipt_step_scope(
        {"receipt_id": "unknown", "step_id": "step-404"},
        known_step_ids=["step-1", "step-2"],
    )["status"] == "STEP_SCOPE_UNKNOWN"


def test_finalizer_inputs_are_wrapped_by_semantic_view() -> None:
    ledger = _ledger()
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "observation-1",
                "step_id": "step-1",
                "target_reached": True,
            },
            {
                "receipt_id": "observation-2",
                "step_id": "step-2",
                "target_reached": True,
            },
        ]
    }
    record_stage_event(
        ledger,
        phase="finalizer",
        step_id="finalizer",
        status="READY",
        receipt_id="finalizer_inputs_sealed",
    )

    attach_lifecycle_ledger(observations, ledger)

    assert isinstance(observations["process_step_ledger"], ProcessStepSemanticView)
    assert observations["process_step_ledger_view"] == "semantic_completion"


def test_legacy_total_oracle_broadcast_attempt_cannot_bind_any_step() -> None:
    ledger = _ledger()
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "observation-1",
                "step_id": "step-1",
                "target_reached": True,
            },
            {
                "receipt_id": "observation-2",
                "step_id": "step-2",
                "target_reached": True,
            },
        ],
        "oracle_invocation_receipts": [
            {
                "receipt_id": "oracle-total",
                "step_ids": ["step-1", "step-2"],
                "evaluated": True,
            }
        ],
    }
    view = ProcessStepSemanticView(ledger, observations)

    for step_id in ("step-1", "step-2"):
        assert view.append_receipt_ref(
            step_id,
            "oracle_receipt_ids",
            "oracle-total",
        ) is False

    rows = {row["step_id"]: row for row in view.all_rows()}
    assert rows["step-1"]["scoped_oracle_receipt_ids"] == []
    assert rows["step-2"]["scoped_oracle_receipt_ids"] == []
    audit = observations["process_step_receipt_scope_binding"]
    assert audit["oracle"]["bound"] == []
    assert audit["oracle"]["unbound"][0]["status"] == (
        "MULTI_STEP_SCOPE_FORBIDDEN"
    )
    assert audit["broadcast_fallback_forbidden"] is True


def test_exact_oracle_and_cleanup_receipts_bind_only_their_declared_steps() -> None:
    ledger = _ledger()
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "observation-1",
                "step_id": "step-1",
                "target_reached": True,
            },
            {
                "receipt_id": "observation-2",
                "step_id": "step-2",
                "target_reached": True,
            },
        ],
        "oracle_invocation_receipts": [
            {
                "receipt_id": "oracle-1",
                "step_id": "step-1",
                "evaluated": True,
            },
            {
                "receipt_id": "oracle-2",
                "step_id": "step-2",
                "evaluated": True,
            },
        ],
        "cleanup_execution_receipts": [
            {
                "receipt_id": "cleanup-1",
                "step_id": "step-1",
                "executed": True,
            },
            {
                "receipt_id": "cleanup-2",
                "step_id": "step-2",
                "executed": True,
            },
        ],
    }
    rows = {
        row["step_id"]: row
        for row in ProcessStepSemanticView(ledger, observations).all_rows()
    }

    assert rows["step-1"]["scoped_oracle_receipt_ids"] == ["oracle-1"]
    assert rows["step-2"]["scoped_oracle_receipt_ids"] == ["oracle-2"]
    assert rows["step-1"]["scoped_cleanup_receipt_ids"] == ["cleanup-1"]
    assert rows["step-2"]["scoped_cleanup_receipt_ids"] == ["cleanup-2"]
    assert observations["process_step_receipt_scope_binding"]["complete"] is True


def test_identified_raw_oracle_trace_is_promoted_and_bound() -> None:
    ledger = _ledger()
    trace = {
        "receipt_id": "trace-1",
        "step_id": "step-1",
        "trace_kind": "evaluation",
    }
    observations = {"oracle_trace": [trace]}

    audit = synchronize_scoped_receipts_from_observations(ledger, observations)
    rows = {row["step_id"]: row for row in ledger.all_rows()}

    assert observations["oracle_trace_receipts"] == [trace]
    assert rows["step-1"]["scoped_oracle_receipt_ids"] == ["trace-1"]
    assert audit["oracle"]["bound"] == [
        {
            "receipt_id": "trace-1",
            "step_id": "step-1",
            "evidence_kind": "oracle",
        }
    ]


def test_anonymous_raw_oracle_trace_stays_diagnostic_only() -> None:
    ledger = _ledger()
    trace = {"step_id": "step-1", "trace_kind": "diagnostic"}
    observations = {"oracle_trace": [trace]}

    audit = synchronize_scoped_receipts_from_observations(ledger, observations)
    rows = {row["step_id"]: row for row in ledger.all_rows()}

    assert observations["oracle_trace"] == [trace]
    assert observations["oracle_trace_receipts"] == []
    assert rows["step-1"]["scoped_oracle_receipt_ids"] == []
    assert audit["oracle"]["bound"] == []
    assert audit["oracle"]["unbound"] == []


def test_formal_oracle_trace_without_receipt_id_fails_closed() -> None:
    ledger = _ledger()
    observations = {
        "oracle_trace_receipts": [
            {"step_id": "step-1", "trace_kind": "evaluation"}
        ]
    }

    audit = synchronize_scoped_receipts_from_observations(ledger, observations)

    assert audit["complete"] is False
    assert audit["oracle"]["unbound"][0]["status"] == "RECEIPT_ID_MISSING"
