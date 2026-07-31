from copy import deepcopy

from ai_test_asset_center.operational_receipts import (
    build_canonical_receipt_envelope,
    build_execution_receipt_bundle,
    derive_execution_lifecycle,
)
from ai_test_asset_center.process_step_execution import (
    PROCESS_STEP_RECEIPT_SCHEMA,
    ProcessStepLedger,
    _canonical_step_fact,
    _stable_hash,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


IDENTITY = {
    "campaign_id": "campaign-1",
    "run_id": "run-1",
    "obligation_id": "obligation-1",
    "experiment_id": "experiment-1",
    "fixture_id": "fixture-1",
    "protocol_id": "protocol-1",
}


def _envelope(receipt_type: str, receipt_id: str, payload: dict):
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        **IDENTITY,
        code_commit_sha="commit-1",
        tree_hash="tree-1",
    )


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        IDENTITY["experiment_id"],
        fixture_id=IDENTITY["fixture_id"],
        campaign_id=IDENTITY["campaign_id"],
        run_id=IDENTITY["run_id"],
        obligation_id=IDENTITY["obligation_id"],
        protocol_id=IDENTITY["protocol_id"],
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


def _exact_observations() -> dict:
    return {
        "observer_receipts": [
            {
                "receipt_id": f"observation-{ordinal}",
                "step_id": f"step-{ordinal}",
                "target_reached": True,
            }
            for ordinal in (1, 2)
        ],
        "oracle_invocation_receipts": [
            {
                "receipt_id": f"oracle-{ordinal}",
                "step_id": f"step-{ordinal}",
                "evaluated": True,
            }
            for ordinal in (1, 2)
        ],
        "cleanup_execution_receipts": [
            {
                "receipt_id": f"cleanup-exec-{ordinal}",
                "step_id": f"step-{ordinal}",
                "executed": True,
            }
            for ordinal in (1, 2)
        ],
        "cleanup_verification_receipts": [
            {
                "receipt_id": f"cleanup-ver-{ordinal}",
                "step_id": f"step-{ordinal}",
                "verified": True,
            }
            for ordinal in (1, 2)
        ],
    }


def _step_rows(observations: dict | None = None) -> list[dict]:
    return ProcessStepSemanticView(
        _ledger(),
        observations if observations is not None else _exact_observations(),
    ).all_rows()


def _receipt_payloads_from_rows(step_rows: list[dict]) -> dict[str, tuple[str, dict]]:
    payloads: dict[str, tuple[str, dict]] = {}
    for row in step_rows:
        step_id = row["step_id"]
        for rid in row.get("scoped_observation_receipt_ids", []):
            payloads.setdefault(
                rid,
                ("qualibug.observation-receipt.v1", {"step_id": step_id}),
            )
        for rid in row.get("scoped_oracle_receipt_ids", []):
            payloads.setdefault(
                rid,
                ("qualibug.oracle-invocation-receipt.v1", {"step_id": step_id}),
            )
        for rid in row.get("scoped_cleanup_receipt_ids", []):
            receipt_type = (
                "qualibug.cleanup-verification-receipt.v1"
                if rid.startswith("cleanup-ver-")
                else "qualibug.cleanup-execution-receipt.v1"
            )
            payloads.setdefault(rid, (receipt_type, {"step_id": step_id}))
    return payloads


def _bundle(
    step_rows: list[dict],
    *,
    grouped_receipt_ids: list[str] | None = None,
    extra_evidence: list[tuple[str, str, dict]] | None = None,
):
    step_receipts = [
        _envelope(PROCESS_STEP_RECEIPT_SCHEMA, row["receipt_id"], row)
        for row in step_rows
    ]
    compile_receipt = _envelope("qualibug.compile-receipt.v1", "compile-1", {})
    fixture_receipt = _envelope(
        "qualibug.fixture-materialization-receipt.v1", "fixture-r1", {}
    )
    transport_receipt = _envelope(
        "qualibug.transport-receipt.v1", "transport-1", {}
    )
    environment_receipt = _envelope(
        "qualibug.environment-restoration-receipt.v1", "environment-1", {}
    )

    evidence_payloads = _receipt_payloads_from_rows(step_rows)
    for receipt_type, rid, payload in list(extra_evidence or []):
        evidence_payloads[rid] = (receipt_type, payload)
    evidence_receipts = [
        _envelope(receipt_type, rid, payload)
        for rid, (receipt_type, payload) in evidence_payloads.items()
    ]
    observation_ids = sorted(
        rid
        for rid, (receipt_type, _) in evidence_payloads.items()
        if receipt_type == "qualibug.observation-receipt.v1"
    )
    oracle_ids = sorted(
        rid
        for rid, (receipt_type, _) in evidence_payloads.items()
        if receipt_type == "qualibug.oracle-invocation-receipt.v1"
    )
    cleanup_exec_ids = sorted(
        rid
        for rid, (receipt_type, _) in evidence_payloads.items()
        if receipt_type == "qualibug.cleanup-execution-receipt.v1"
    )
    cleanup_ver_ids = sorted(
        rid
        for rid, (receipt_type, _) in evidence_payloads.items()
        if receipt_type == "qualibug.cleanup-verification-receipt.v1"
    )
    receipts = [
        compile_receipt,
        fixture_receipt,
        *step_receipts,
        transport_receipt,
        *evidence_receipts,
        environment_receipt,
    ]
    return build_execution_receipt_bundle(
        bundle_id="bundle-1",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-r1"],
        required_step_receipt_ids=(
            grouped_receipt_ids
            if grouped_receipt_ids is not None
            else [row["receipt_id"] for row in step_rows]
        ),
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=observation_ids,
        oracle_invocation_receipt_ids=oracle_ids,
        cleanup_execution_receipt_ids=cleanup_exec_ids,
        cleanup_verification_receipt_ids=cleanup_ver_ids,
        environment_restoration_receipt_id="environment-1",
        **IDENTITY,
    )


def _lifecycle(bundle: dict):
    return derive_execution_lifecycle(
        execution_status="EXECUTED",
        compile_succeeded=True,
        fixture_required=True,
        fixture_materialized=True,
        state_precondition_required=True,
        state_precondition_established=True,
        required_steps_declared=True,
        all_required_steps_executed=True,
        observation_completed=True,
        oracle_evaluated=True,
        oracle_indeterminate=False,
        cleanup_required=True,
        cleanup_executed=True,
        cleanup_verified=True,
        environment_restored=True,
        receipt_bundle=bundle,
    )


def test_balanced_exact_scoped_step_receipts_allow_true_completed() -> None:
    bundle = _bundle(_step_rows())

    audit = bundle["process_step_audit"]
    assert bundle["complete"] is True
    assert audit["complete"] is True
    assert audit["step_evidence_scopes_complete"] is True
    assert audit["evidence_scope_audit"]["complete"] is True
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"


def test_removed_step_is_detected_from_sealed_recorded_set() -> None:
    rows = _step_rows()
    bundle = _bundle(rows[:1])

    assert bundle["complete"] is False
    assert "process_step_set_mismatch" in bundle["validation_errors"]
    assert "ledger_recorded_step_ids" in bundle["process_step_set_mismatch_fields"]
    assert _lifecycle(bundle)["reason_code"] == "PROCESS_STEP_SET_MISMATCH"


def test_ungrouped_step_envelope_cannot_hide_as_optional_extra_receipt() -> None:
    rows = _step_rows()
    bundle = _bundle(rows, grouped_receipt_ids=[rows[0]["receipt_id"]])

    audit = bundle["process_step_audit"]
    assert bundle["complete"] is False
    assert "process_step_receipt_group_mismatch" in bundle["validation_errors"]
    assert audit["group_missing_receipt_ids"] == [rows[1]["receipt_id"]]


def test_mixed_ledger_id_is_identity_mismatch() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["process_step_ledger_id"] = "psl-other"
    bundle = _bundle(rows)

    assert bundle["process_step_ledger_identity_consistent"] is False
    result = _lifecycle(bundle)
    assert result["lifecycle_state"] == "IDENTITY_MISMATCH"
    assert result["reason_code"] == "PROCESS_STEP_LEDGER_IDENTITY_MISMATCH"


def test_mixed_ledger_hash_is_receipt_incomplete() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["process_step_ledger_hash"] = "stale-ledger-hash"
    bundle = _bundle(rows)

    assert bundle["process_step_ledger_hash_consistent"] is False
    assert _lifecycle(bundle)["reason_code"] == "PROCESS_STEP_LEDGER_HASH_MISMATCH"


def test_outer_resign_cannot_hide_step_fact_tamper() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["status_code"] = 503
    bundle = _bundle(rows)

    assert bundle["process_step_fact_hashes_valid"] is False
    assert rows[1]["receipt_id"] in bundle[
        "process_step_fact_hash_mismatch_receipt_ids"
    ]
    assert _lifecycle(bundle)["reason_code"] == "PROCESS_STEP_FACT_HASH_MISMATCH"


def test_reclassified_step_with_recomputed_fact_hash_breaks_set_balance() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["operation_accepted"] = False
    rows[1]["semantic_step_status"] = "OPERATION_REJECTED"
    rows[1]["step_completed"] = False
    rows[1]["step_failed"] = True
    rows[1]["step_fact_hash"] = _stable_hash(_canonical_step_fact(rows[1]))
    bundle = _bundle(rows)

    assert bundle["process_step_fact_hashes_valid"] is True
    assert bundle["process_step_sets_balanced"] is False
    assert "ledger_accepted_step_ids" in bundle["process_step_set_mismatch_fields"]
    assert _lifecycle(bundle)["reason_code"] == "PROCESS_STEP_SET_MISMATCH"


def test_fully_resigned_forgery_cannot_replace_ledger_hash_authority() -> None:
    rows = [deepcopy(row) for row in _step_rows()]
    rows[1]["operation_accepted"] = False
    rows[1]["semantic_step_status"] = "OPERATION_REJECTED"
    rows[1]["step_completed"] = False
    rows[1]["step_failed"] = True
    rows[1]["step_fact_hash"] = _stable_hash(_canonical_step_fact(rows[1]))
    for row in rows:
        row["ledger_accepted_step_ids"] = ["step-1"]
        row["ledger_completed_step_ids"] = ["step-1"]
        row["ledger_failed_step_ids"] = ["step-2"]
        row["ledger_pending_semantic_step_ids"] = []
        row["process_step_ledger_hash"] = "forged-ledger-hash"

    bundle = _bundle(rows)
    audit = bundle["process_step_audit"]
    assert audit["step_fact_hashes_valid"] is True
    assert audit["ledger_hash_value_consistent"] is True
    assert audit["ledger_hash_content_valid"] is False
    assert _lifecycle(bundle)["reason_code"] == "PROCESS_STEP_LEDGER_HASH_MISMATCH"


def test_total_oracle_receipt_with_step_ids_is_never_broadcast() -> None:
    observations = _exact_observations()
    observations["oracle_invocation_receipts"] = [
        {
            "receipt_id": "oracle-total",
            "step_ids": ["step-1", "step-2"],
            "evaluated": True,
        }
    ]
    rows = _step_rows(observations)
    bundle = _bundle(
        rows,
        extra_evidence=[
            (
                "qualibug.oracle-invocation-receipt.v1",
                "oracle-total",
                {"step_ids": ["step-1", "step-2"]},
            )
        ],
    )

    scope = bundle["process_step_audit"]["evidence_scope_audit"]
    assert bundle["complete"] is False
    assert scope["oracle_invocation"]["multi_step_receipt_ids"] == ["oracle-total"]
    assert scope["missing_oracle_step_ids"] == ["step-1", "step-2"]


def test_unscoped_total_cleanup_receipt_cannot_cover_all_writes() -> None:
    observations = _exact_observations()
    observations["cleanup_execution_receipts"] = [
        {"receipt_id": "cleanup-total", "executed": True}
    ]
    rows = _step_rows(observations)
    bundle = _bundle(
        rows,
        extra_evidence=[
            (
                "qualibug.cleanup-execution-receipt.v1",
                "cleanup-total",
                {},
            )
        ],
    )

    scope = bundle["process_step_audit"]["evidence_scope_audit"]
    assert bundle["complete"] is False
    assert scope["cleanup_execution"]["unscoped_receipt_ids"] == [
        "cleanup-total"
    ]
    assert scope["missing_cleanup_execution_step_ids"] == ["step-1", "step-2"]


def test_unknown_step_observation_is_unbound() -> None:
    observations = _exact_observations()
    observations["observer_receipts"] = [
        {
            "receipt_id": "observation-unknown",
            "step_id": "step-404",
            "target_reached": True,
        }
    ]
    rows = _step_rows(observations)
    bundle = _bundle(
        rows,
        extra_evidence=[
            (
                "qualibug.observation-receipt.v1",
                "observation-unknown",
                {"step_id": "step-404"},
            )
        ],
    )

    scope = bundle["process_step_audit"]["evidence_scope_audit"]
    assert bundle["complete"] is False
    assert scope["observation"]["unknown_step_receipt_ids"] == [
        "observation-unknown"
    ]


def test_same_oracle_receipt_referenced_by_two_steps_is_broadcast_violation() -> None:
    ledger = _ledger()
    observations = _exact_observations()
    observations["oracle_invocation_receipts"] = [
        {"receipt_id": "oracle-shared", "step_id": "step-1", "evaluated": True}
    ]
    ledger.append_scoped_receipt_ref(
        step_id="step-1",
        receipt_step_id="step-1",
        field="oracle_receipt_ids",
        receipt_id="oracle-shared",
    )
    ledger.append_scoped_receipt_ref(
        step_id="step-2",
        receipt_step_id="step-2",
        field="oracle_receipt_ids",
        receipt_id="oracle-shared",
    )
    rows = ProcessStepSemanticView(ledger, observations).all_rows()
    bundle = _bundle(
        rows,
        extra_evidence=[
            (
                "qualibug.oracle-invocation-receipt.v1",
                "oracle-shared",
                {"step_id": "step-1"},
            )
        ],
    )

    scope = bundle["process_step_audit"]["evidence_scope_audit"]
    assert bundle["complete"] is False
    assert scope["broadcast_receipt_ids"] == ["oracle-shared"]
    assert "receipt_scope_broadcast" in bundle["process_step_set_mismatch_fields"]
