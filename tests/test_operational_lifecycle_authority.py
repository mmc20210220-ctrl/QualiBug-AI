from ai_test_asset_center.operational_receipts import (
    EXECUTION_RECEIPT_BUNDLE_SCHEMA,
    build_execution_finalization_receipt,
    derive_execution_lifecycle,
    derive_true_completed_from_bundle,
)


def _bundle(**overrides):
    value = {
        "schema_version": EXECUTION_RECEIPT_BUNDLE_SCHEMA,
        "bundle_id": "bundle-1",
        "campaign_id": "campaign-1",
        "run_id": "run-1",
        "obligation_id": "obligation-1",
        "experiment_id": "experiment-1",
        "fixture_id": "fixture-1",
        "protocol_id": "protocol-1",
        "complete": True,
        "missing_receipt_ids": [],
        "invalid_receipt_ids": [],
        "identity_mismatch_receipt_ids": [],
        "protocol_mismatch_receipt_ids": [],
        "process_step_audit": {"complete": True},
        "process_step_ledger_identity_mismatch_receipt_ids": [],
        "process_step_ledger_hash_mismatch_receipt_ids": [],
        "process_step_fact_hash_mismatch_receipt_ids": [],
        "process_step_set_mismatch_fields": [],
        "process_step_invariant_errors": [],
    }
    value.update(overrides)
    return value


def _facts(**overrides):
    value = {
        "execution_status": "EXECUTED",
        "compile_succeeded": True,
        "fixture_required": True,
        "fixture_materialized": True,
        "state_precondition_required": True,
        "state_precondition_established": True,
        "required_steps_declared": True,
        "all_required_steps_executed": True,
        "observation_completed": True,
        "oracle_evaluated": True,
        "oracle_indeterminate": False,
        "cleanup_required": True,
        "cleanup_executed": True,
        "cleanup_verified": True,
        "environment_restored": True,
        "receipt_bundle": _bundle(),
    }
    value.update(overrides)
    return value


def _derive(**overrides):
    return derive_execution_lifecycle(**_facts(**overrides))


def test_true_completed_requires_all_facts_and_valid_bundle():
    result = _derive()
    assert result["lifecycle_state"] == "TRUE_COMPLETED"
    assert result["true_completed"] is True
    assert result["authority_module"] == "ai_test_asset_center.operational_receipts"


def test_compile_block_has_first_precedence():
    result = _derive(
        compile_succeeded=False,
        fixture_materialized=False,
        all_required_steps_executed=False,
    )
    assert result["lifecycle_state"] == "COMPILE_BLOCKED"


def test_fixture_block_precedes_process_state():
    result = _derive(fixture_materialized=False, all_required_steps_executed=False)
    assert result["lifecycle_state"] == "FIXTURE_BLOCKED"


def test_precondition_unreachable_precedes_execution():
    result = _derive(
        state_precondition_established=False,
        all_required_steps_executed=False,
    )
    assert result["lifecycle_state"] == "PRECONDITION_UNREACHABLE"


def test_harness_failure_is_not_partial_execution():
    result = _derive(execution_status="HARNESS_FAILURE")
    assert result["lifecycle_state"] == "HARNESS_FAILED"


def test_required_step_gap_is_partial_execution():
    result = _derive(all_required_steps_executed=False)
    assert result["lifecycle_state"] == "PARTIAL_EXECUTION"


def test_missing_observation_is_receipt_incomplete():
    result = _derive(observation_completed=False)
    assert result["lifecycle_state"] == "RECEIPT_INCOMPLETE"


def test_oracle_indeterminate_is_explicit():
    result = _derive(oracle_indeterminate=True)
    assert result["lifecycle_state"] == "ORACLE_INDETERMINATE"


def test_cleanup_failure_precedes_environment_dirty():
    result = _derive(cleanup_verified=False, environment_restored=False)
    assert result["lifecycle_state"] == "CLEANUP_FAILED"


def test_environment_dirty_precedes_bundle_gate():
    result = _derive(environment_restored=False, receipt_bundle=_bundle(complete=False))
    assert result["lifecycle_state"] == "ENVIRONMENT_DIRTY"


def test_invalid_bundle_cannot_be_overridden_by_true_facts():
    result = _derive(receipt_bundle=_bundle(complete=False))
    assert result["lifecycle_state"] == "RECEIPT_INCOMPLETE"
    assert result["true_completed"] is False


def test_identity_mismatch_is_terminal():
    result = _derive(
        receipt_bundle=_bundle(identity_mismatch_receipt_ids=["receipt-1"])
    )
    assert result["lifecycle_state"] == "IDENTITY_MISMATCH"


def test_process_ledger_identity_mismatch_is_terminal():
    result = _derive(
        receipt_bundle=_bundle(
            process_step_ledger_identity_mismatch_receipt_ids=["step-1"]
        )
    )
    assert result["lifecycle_state"] == "IDENTITY_MISMATCH"
    assert result["reason_code"] == "PROCESS_STEP_LEDGER_IDENTITY_MISMATCH"


def test_process_ledger_hash_mismatch_is_receipt_incomplete():
    result = _derive(
        receipt_bundle=_bundle(
            complete=False,
            process_step_ledger_hash_mismatch_receipt_ids=["step-1"],
        )
    )
    assert result["lifecycle_state"] == "RECEIPT_INCOMPLETE"
    assert result["reason_code"] == "PROCESS_STEP_LEDGER_HASH_MISMATCH"


def test_process_step_fact_hash_mismatch_is_receipt_incomplete():
    result = _derive(
        receipt_bundle=_bundle(
            complete=False,
            process_step_fact_hash_mismatch_receipt_ids=["step-1"],
        )
    )
    assert result["lifecycle_state"] == "RECEIPT_INCOMPLETE"
    assert result["reason_code"] == "PROCESS_STEP_FACT_HASH_MISMATCH"


def test_process_step_set_mismatch_is_receipt_incomplete():
    result = _derive(
        receipt_bundle=_bundle(
            complete=False,
            process_step_set_mismatch_fields=["ledger_recorded_step_ids"],
        )
    )
    assert result["lifecycle_state"] == "RECEIPT_INCOMPLETE"
    assert result["reason_code"] == "PROCESS_STEP_SET_MISMATCH"


def test_protocol_mismatch_is_terminal():
    result = _derive(
        receipt_bundle=_bundle(protocol_mismatch_receipt_ids=["receipt-1"])
    )
    assert result["lifecycle_state"] == "PROTOCOL_MISMATCH"


def test_backward_bundle_api_keeps_public_oracle_name():
    result = derive_true_completed_from_bundle(
        _bundle(),
        oracle_evaluated=False,
        cleanup_verified=True,
        environment_restored=True,
    )
    assert result["derived_terminal_status"] == "ORACLE_NOT_EVALUATED"
    assert (
        result["lifecycle_derivation"]["lifecycle_state"]
        == "ORACLE_INDETERMINATE"
    )


def test_finalization_receipt_uses_lifecycle_facts_not_legacy_flags():
    result = build_execution_finalization_receipt(
        finalization_receipt_id="final-1",
        bundle=_bundle(),
        oracle_evaluated=True,
        cleanup_verified=True,
        environment_restored=True,
        lifecycle_facts={
            "execution_status": "BLOCKED",
            "compile_succeeded": True,
            "fixture_required": True,
            "fixture_materialized": True,
            "state_precondition_required": True,
            "state_precondition_established": True,
            "required_steps_declared": True,
            "all_required_steps_executed": False,
            "observation_completed": False,
            "oracle_evaluated": False,
            "cleanup_required": False,
            "environment_restored": True,
            "finalizer_block_reason": "PROCESS_STEP_NOT_EXECUTED",
        },
    )
    assert result["lifecycle_state"] == "PARTIAL_EXECUTION"
    assert result["true_completed"] is False
    assert result["reason_code"] == "PROCESS_STEP_NOT_EXECUTED"
    assert result["envelope"]["status"] == "INCOMPLETE"
