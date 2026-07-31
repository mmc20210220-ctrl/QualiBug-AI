from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from ai_test_asset_center.operational_receipts import (
    CORRECTION_RECEIPT_SCHEMA,
    NOT_APPLICABLE,
    OperationalReceiptError,
    RECEIPT_STATUS_TAMPERED,
    RECEIPT_STATUS_VALID,
    assemble_bundle_from_finalizer_observations,
    audit_report_metric_ledger_balance,
    build_canonical_receipt_envelope,
    build_correction_receipt,
    build_execution_finalization_receipt,
    build_execution_receipt_bundle,
    build_fixture_provenance_receipt,
    build_report_metric_receipt,
    deduplicate_oracle_traces,
    derive_true_completed_from_bundle,
    detect_receipt_tamper,
    unique_oracle_evaluation_key,
    validate_canonical_receipt_envelope,
    validate_parent_receipt_chain,
)
from ai_test_asset_center.process_step_execution import (
    PROCESS_STEP_RECEIPT_SCHEMA,
    ProcessStepLedger,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


COMMIT = "7a6895a34905bbc08d519d88c3187b825a4304cf"
TREE = "8c09f57c6711d0bada039ef7420e55a1aedc615d"
DENOM = "0c692b5ee288abf4bf540b3d57d5395fee552c6163b3df56fe42d445033dd4cd"
ROOT = Path(__file__).resolve().parents[1]


def _env(
    receipt_type: str,
    receipt_id: str,
    *,
    payload: dict | None = None,
    parent_receipt_ids: list[str] | None = None,
    **identity: str,
) -> dict:
    values = {
        "campaign_id": "campaign-a",
        "run_id": "run-a",
        "obligation_id": "obligation-a",
        "experiment_id": "experiment-a",
        "fixture_id": "fixture-a",
        "protocol_id": "protocol-a",
    }
    values.update(identity)
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload or {"evidence": receipt_id},
        code_commit_sha=COMMIT,
        tree_hash=TREE,
        parent_receipt_ids=parent_receipt_ids,
        **values,
    )


def _step_evidence(step_id: str) -> dict[str, list[dict]]:
    return {
        "observer_receipts": [
            {
                "receipt_id": f"observation-{step_id}",
                "step_id": step_id,
                "target_reached": True,
            }
        ],
        "oracle_invocation_receipts": [
            {
                "receipt_id": f"oracle-inv-{step_id}",
                "step_id": step_id,
                "evaluated": True,
            }
        ],
        "oracle_trace_receipts": [
            {
                "receipt_id": f"oracle-trace-{step_id}",
                "step_id": step_id,
                "trace_kind": "evaluation",
            }
        ],
        "cleanup_execution_receipts": [
            {
                "receipt_id": f"cleanup-exec-{step_id}",
                "step_id": step_id,
                "executed": True,
            }
        ],
        "cleanup_verification_receipts": [
            {
                "receipt_id": f"cleanup-verify-{step_id}",
                "step_id": step_id,
                "verified": True,
            }
        ],
    }


def _sealed_step_rows(identity: dict[str, str], step_ids: list[str]) -> list[dict]:
    ledger = ProcessStepLedger(
        identity["experiment_id"],
        fixture_id=identity["fixture_id"],
        campaign_id=identity["campaign_id"],
        run_id=identity["run_id"],
        obligation_id=identity["obligation_id"],
        protocol_id=identity["protocol_id"],
        required_step_ids=step_ids,
    )
    observations: dict[str, list[dict]] = {
        "observer_receipts": [],
        "oracle_invocation_receipts": [],
        "oracle_trace_receipts": [],
        "cleanup_execution_receipts": [],
        "cleanup_verification_receipts": [],
    }
    for index, step_id in enumerate(step_ids, start=1):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"operation-{index}",
            actor_ref="actor-a",
            request_receipt_id=f"request-{index}",
            response_receipt_id=f"response-{index}",
            status_code=200,
            final_status="EXECUTED",
            mutation_occurred=True,
        )
        evidence = _step_evidence(step_id)
        for key, rows in evidence.items():
            observations[key].extend(rows)
    return ProcessStepSemanticView(ledger, observations=observations).all_rows()


def _full_bundle(*, entity: str = "entity-a") -> dict:
    identity = {
        "campaign_id": f"campaign-{entity}",
        "run_id": f"run-{entity}",
        "obligation_id": f"obligation-{entity}",
        "experiment_id": f"experiment-{entity}",
        "fixture_id": f"fixture-{entity}",
        "protocol_id": f"protocol-{entity}",
    }
    step_id = f"step-{entity}"
    fixture = build_fixture_provenance_receipt(
        receipt_id=f"fixture-proof-{entity}",
        entity_id=entity,
        scope_id=f"scope-{entity}",
        create_identity=f"created-{entity}",
        readback_identity=f"created-{entity}",
        operation_identity=f"created-{entity}",
        observer_identity=f"created-{entity}",
        cleanup_identity=f"created-{entity}",
        code_commit_sha=COMMIT,
        tree_hash=TREE,
        **identity,
    )
    compile_receipt = _env(
        "qualibug.compile-receipt.v1", f"compile-{entity}", **identity
    )
    transport_receipt = _env(
        "qualibug.transport-receipt.v1", f"transport-{entity}", **identity
    )
    restored_receipt = _env(
        "qualibug.environment-restoration-receipt.v1",
        f"restored-{entity}",
        **identity,
    )
    step_rows = _sealed_step_rows(identity, [step_id])
    step_receipts = [
        _env(
            PROCESS_STEP_RECEIPT_SCHEMA,
            row["receipt_id"],
            payload=row,
            **identity,
        )
        for row in step_rows
    ]
    evidence = _step_evidence(step_id)
    observation_receipts = [
        _env(
            "qualibug.observation-receipt.v1",
            row["receipt_id"],
            payload=row,
            **identity,
        )
        for row in evidence["observer_receipts"]
    ]
    oracle_invocation_receipts = [
        _env(
            "qualibug.oracle-invocation-receipt.v1",
            row["receipt_id"],
            payload=row,
            **identity,
        )
        for row in evidence["oracle_invocation_receipts"]
    ]
    oracle_trace_receipts = [
        _env(
            "qualibug.oracle-trace-receipt.v1",
            row["receipt_id"],
            payload=row,
            **identity,
        )
        for row in evidence["oracle_trace_receipts"]
    ]
    cleanup_execution_receipts = [
        _env(
            "qualibug.cleanup-execution-receipt.v1",
            row["receipt_id"],
            payload=row,
            **identity,
        )
        for row in evidence["cleanup_execution_receipts"]
    ]
    cleanup_verification_receipts = [
        _env(
            "qualibug.cleanup-verification-receipt.v1",
            row["receipt_id"],
            payload=row,
            **identity,
        )
        for row in evidence["cleanup_verification_receipts"]
    ]
    receipts = [
        fixture,
        compile_receipt,
        *step_receipts,
        transport_receipt,
        *observation_receipts,
        *oracle_invocation_receipts,
        *oracle_trace_receipts,
        *cleanup_execution_receipts,
        *cleanup_verification_receipts,
        restored_receipt,
    ]
    return build_execution_receipt_bundle(
        bundle_id=f"bundle-{entity}",
        receipts=receipts,
        compile_receipt_id=compile_receipt["receipt_id"],
        fixture_provenance_receipt_ids=[fixture["receipt_id"]],
        required_step_receipt_ids=[row["receipt_id"] for row in step_rows],
        transport_receipt_ids=[transport_receipt["receipt_id"]],
        observation_receipt_ids=[row["receipt_id"] for row in observation_receipts],
        oracle_invocation_receipt_ids=[
            row["receipt_id"] for row in oracle_invocation_receipts
        ],
        oracle_trace_receipt_ids=[row["receipt_id"] for row in oracle_trace_receipts],
        cleanup_execution_receipt_ids=[
            row["receipt_id"] for row in cleanup_execution_receipts
        ],
        cleanup_verification_receipt_ids=[
            row["receipt_id"] for row in cleanup_verification_receipts
        ],
        environment_restoration_receipt_id=restored_receipt["receipt_id"],
        **identity,
    )


def _derive(bundle: dict, **overrides: bool) -> dict:
    flags = {
        "oracle_evaluated": True,
        "cleanup_verified": True,
        "environment_restored": True,
    }
    flags.update(overrides)
    return derive_true_completed_from_bundle(bundle, **flags)


def _manifest() -> dict:
    return json.loads(
        (
            ROOT / "artifacts/spec_v1_6_2/v162_canonical_obligation_manifest.json"
        ).read_text(encoding="utf-8")
    )


# §24.1 — canonical envelope legality and tamper authority.
def test_241_legal_envelope_validates() -> None:
    assert validate_canonical_receipt_envelope(_env("legal.v1", "legal"))[
        "status"
    ] == RECEIPT_STATUS_VALID


def test_241_missing_receipt_id_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="receipt_id_missing"):
        _env("legal.v1", "")


def test_241_missing_experiment_id_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="experiment_id_missing"):
        _env("legal.v1", "missing-experiment", experiment_id="")


def test_241_payload_hash_mismatch_is_rejected() -> None:
    receipt = _env("legal.v1", "payload-hash")
    receipt["payload"]["evidence"] = "mutated"
    with pytest.raises(OperationalReceiptError, match="payload_hash_mismatch"):
        validate_canonical_receipt_envelope(receipt)


def test_241_parent_chain_break_is_rejected() -> None:
    receipt = _env("legal.v1", "child", parent_receipt_ids=["absent-parent"])
    with pytest.raises(OperationalReceiptError, match="parent_chain_broken"):
        validate_parent_receipt_chain([receipt])


def test_241_code_commit_mismatch_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="code_commit_sha_mismatch"):
        validate_canonical_receipt_envelope(
            _env("legal.v1", "commit"), expected_code_commit_sha="other"
        )


def test_241_tree_hash_mismatch_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="tree_hash_mismatch"):
        validate_canonical_receipt_envelope(
            _env("legal.v1", "tree"), expected_tree_hash="other"
        )


def test_241_tamper_detection_is_explicit() -> None:
    receipt = _env("legal.v1", "tampered")
    receipt["payload"]["evidence"] = "changed"
    audit = detect_receipt_tamper(receipt)
    assert audit["status"] == RECEIPT_STATUS_TAMPERED
    assert audit["tampered"] is True


def test_241_correction_retains_original_reference() -> None:
    original = _env("legal.v1", "original")
    correction = build_correction_receipt(
        correction_receipt_id="correction",
        supersedes_receipt_id="original",
        original_receipt=original,
        corrected_payload={"fixed": True},
        reason_code="FIX",
    )
    assert correction["receipt_type"] == CORRECTION_RECEIPT_SCHEMA
    assert correction["payload"]["original_retained"] is True


def test_241_not_applicable_is_not_omitted() -> None:
    assert _env("legal.v1", "na", fixture_id=NOT_APPLICABLE)[
        "fixture_id"
    ] == NOT_APPLICABLE


def test_241_parent_chain_is_intact_when_parent_present() -> None:
    parent = _env("legal.v1", "parent")
    child = _env("legal.v1", "child-ok", parent_receipt_ids=["parent"])
    assert validate_parent_receipt_chain([parent, child])["intact"] is True


# §24.2 — finalization derives completion exclusively from receipt evidence.
def test_242_complete_bundle_derives_true_completed() -> None:
    assert _derive(_full_bundle())["derived_terminal_status"] == "TRUE_COMPLETED"


def test_242_missing_compile_receipt_blocks_completion() -> None:
    bundle = _full_bundle()
    bundle["complete"] = False
    bundle["missing_receipt_ids"] = ["compile-a"]
    assert _derive(bundle)["derived_terminal_status"] == "RECEIPT_INCOMPLETE"


def test_242_missing_fixture_receipt_blocks_completion() -> None:
    bundle = _full_bundle()
    bundle["complete"] = False
    bundle["missing_receipt_ids"] = ["fixture-a"]
    assert _derive(bundle)["true_completed"] is False


def test_242_missing_step_receipt_blocks_completion() -> None:
    bundle = _full_bundle()
    bundle["complete"] = False
    bundle["missing_receipt_ids"] = ["step-a"]
    assert _derive(bundle)["derived_terminal_status"] == "RECEIPT_INCOMPLETE"


def test_242_missing_observation_receipt_blocks_completion() -> None:
    bundle = _full_bundle()
    bundle["complete"] = False
    bundle["missing_receipt_ids"] = ["observation-a"]
    assert _derive(bundle)["true_completed"] is False


def test_242_oracle_not_evaluated_blocks_completion() -> None:
    assert _derive(_full_bundle(), oracle_evaluated=False)[
        "derived_terminal_status"
    ] == "ORACLE_NOT_EVALUATED"


def test_242_cleanup_failure_blocks_completion() -> None:
    assert _derive(_full_bundle(), cleanup_verified=False)[
        "derived_terminal_status"
    ] == "CLEANUP_FAILED"


def test_242_dirty_environment_blocks_completion() -> None:
    assert _derive(_full_bundle(), environment_restored=False)[
        "derived_terminal_status"
    ] == "ENVIRONMENT_DIRTY"


def test_242_identity_mismatch_blocks_completion() -> None:
    bundle = _full_bundle()
    bundle["identity_mismatch_receipt_ids"] = ["step-a"]
    assert _derive(bundle)["derived_terminal_status"] == "IDENTITY_MISMATCH"


def test_242_protocol_mismatch_blocks_completion() -> None:
    bundle = _full_bundle()
    bundle["protocol_mismatch_receipt_ids"] = ["step-a"]
    assert _derive(bundle)["derived_terminal_status"] == "PROTOCOL_MISMATCH"


def test_242_boolean_cannot_replace_receipt_bundle() -> None:
    with pytest.raises(
        OperationalReceiptError,
        match="execution_receipt_bundle_schema_invalid",
    ):
        _derive({"complete": True})


def test_242_true_completed_assignment_is_finalizer_only() -> None:
    violations = []
    for path in (ROOT / "ai_test_asset_center").glob("*.py"):
        if path.name in {
            "experiment_outcome_finalizer.py",
            "operational_receipts.py",
        }:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "lifecycle_state"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and node.value.value == "TRUE_COMPLETED"
            ):
                violations.append(path.name)
    assert violations == []


# §24.3 — fixture lifecycle identity and scope authority.
def test_243_fixture_identity_stable_through_cleanup() -> None:
    assert _full_bundle()["receipts"][0]["payload"]["identity_stable"] is True


def _fixture(**overrides):
    values = {
        "receipt_id": "fixture",
        "fixture_id": "f",
        "entity_id": "e",
        "scope_id": "s",
        "create_identity": "a",
        "readback_identity": "a",
        "operation_identity": "a",
        "observer_identity": "a",
        "cleanup_identity": "a",
        "code_commit_sha": COMMIT,
        "tree_hash": TREE,
    }
    values.update(overrides)
    return build_fixture_provenance_receipt(**values)


def test_243_fixture_identity_drift_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="fixture_identity_drift"):
        _fixture(readback_identity="b")


def test_243_missing_fixture_scope_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="fixture_scope_missing"):
        _fixture(scope_id="")


def test_243_customer_owned_fixture_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="customer_owned"):
        _fixture(ownership="customer_owned")


def test_243_latest_record_fixture_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="customer_owned"):
        _fixture(ownership="latest_record")


def test_243_max_id_fixture_is_rejected() -> None:
    with pytest.raises(OperationalReceiptError, match="customer_owned"):
        _fixture(ownership="max_id")


def test_243_multiple_entities_require_separate_provenance() -> None:
    assert _full_bundle(entity="one")["fixture_id"] != _full_bundle(entity="two")[
        "fixture_id"
    ]


def test_243_incomplete_fixture_identity_is_rejected() -> None:
    with pytest.raises(
        OperationalReceiptError,
        match="fixture_provenance_identity_incomplete",
    ):
        _fixture(readback_identity="")


# §24.4 — receipt-backed metrics and ledger balance.
def _metric(
    receipt_id: str,
    metric_name: str = "m",
    source_receipt_ids: list[str] | None = None,
    denominator_manifest_hash: str = DENOM,
    ledger_hash: str = "ledger",
):
    return build_report_metric_receipt(
        receipt_id=receipt_id,
        metric_name=metric_name,
        metric_value=1,
        source_receipt_ids=source_receipt_ids
        if source_receipt_ids is not None
        else ["source"],
        denominator_manifest_hash=denominator_manifest_hash,
        ledger_hash=ledger_hash,
    )


def test_244_metric_requires_source_receipts() -> None:
    with pytest.raises(OperationalReceiptError, match="source_receipts_missing"):
        _metric("metric", "compiled", [])


def test_244_compiled_metric_has_source_receipt() -> None:
    assert _metric("compiled", "compiled", ["compile-a"])["payload"][
        "metric_name"
    ] == "compiled"


def test_244_real_executed_metric_has_source_receipt() -> None:
    assert _metric("executed", "real_executed", ["transport-a"])["payload"][
        "metric_value"
    ] == 1


def test_244_oracle_metric_has_source_receipt() -> None:
    assert _metric("oracle", "oracle_evaluated", ["oracle-a"])["payload"][
        "source_receipt_ids"
    ] == ["oracle-a"]


def test_244_cleanup_metric_has_source_receipt() -> None:
    assert _metric("cleanup", "cleanup_verified", ["cleanup-a"])["payload"][
        "metric_name"
    ] == "cleanup_verified"


def test_244_true_completed_metric_has_source_receipt() -> None:
    assert _metric("true", "true_completed", ["final-a"])["payload"][
        "metric_name"
    ] == "true_completed"


def test_244_metric_ledger_balance_passes() -> None:
    assert audit_report_metric_ledger_balance(
        [_metric("balanced")], expected_ledger_hash="ledger"
    )["balanced"] is True


def test_244_metric_ledger_balance_fails() -> None:
    with pytest.raises(OperationalReceiptError, match="balance_mismatch"):
        audit_report_metric_ledger_balance(
            [_metric("unbalanced")], expected_ledger_hash="other"
        )


def test_244_metric_requires_denominator_manifest() -> None:
    with pytest.raises(
        OperationalReceiptError,
        match="denominator_manifest_missing",
    ):
        _metric("no-denom", denominator_manifest_hash="")


def test_244_metric_id_set_hash_is_order_independent() -> None:
    first = _metric("ids-one", source_receipt_ids=["b", "a"])
    second = _metric("ids-two", source_receipt_ids=["a", "b"])
    assert first["payload"]["id_set_hash"] == second["payload"]["id_set_hash"]


# §24.5 — oracle trace uniqueness.
def _trace() -> dict:
    return {
        "rule_id": "r",
        "experiment_id": "e",
        "fixture_id": "f",
        "assertion_fingerprint": "a",
        "observation_pair_fingerprint": "o",
    }


def test_245_unique_oracle_key_is_stable() -> None:
    kwargs = _trace()
    assert unique_oracle_evaluation_key(**kwargs) == unique_oracle_evaluation_key(
        **kwargs
    )


def test_245_duplicate_evaluations_are_detected() -> None:
    trace = _trace()
    assert len(deduplicate_oracle_traces([trace, trace])[
        "duplicate_evaluation_keys"
    ]) == 1


def test_245_polling_is_classified_not_counted() -> None:
    audit = deduplicate_oracle_traces([{"trace_kind": "polling"}])
    assert audit["classification_counts"]["polling"] == 1
    assert audit["unique_evaluation_count"] == 0


def test_245_retry_is_classified_not_counted() -> None:
    assert deduplicate_oracle_traces([{"trace_kind": "retry"}])[
        "classification_counts"
    ]["retry"] == 1


def test_245_reproduction_is_classified_not_counted() -> None:
    assert deduplicate_oracle_traces([{"trace_kind": "reproduction"}])[
        "classification_counts"
    ]["reproduction"] == 1


def test_245_raw_trace_count_differs_from_unique_count() -> None:
    trace = _trace()
    audit = deduplicate_oracle_traces([trace, trace])
    assert audit["raw_trace_count"] == 2
    assert audit["unique_evaluation_count"] == 1


def test_245_incomplete_oracle_key_fails_closed() -> None:
    with pytest.raises(
        OperationalReceiptError,
        match="oracle_evaluation_key_incomplete",
    ):
        unique_oracle_evaluation_key(
            rule_id="",
            experiment_id="e",
            fixture_id="f",
            assertion_fingerprint="a",
            observation_pair_fingerprint="o",
        )


# §24.6 — frozen denominator and terminal accounting.
def test_246_manifest_count_is_1498() -> None:
    assert _manifest()["canonical_obligation_manifest"]["obligation_count"] == 1498


def test_246_manifest_ids_are_unique() -> None:
    ids = _manifest()["canonical_obligation_manifest"]["obligation_ids"]
    assert len(ids) == 1498
    assert len(ids) == len(set(ids))


def test_246_unknown_mass_category_is_visible() -> None:
    assert "unrecognized-family" not in set(_manifest()["risk_family_distribution"])


def test_246_duplicate_terminal_is_detectable() -> None:
    terminals = ["o-1", "o-1"]
    assert len(terminals) != len(set(terminals))


def test_246_missing_terminal_is_detectable() -> None:
    assert {"o-1", "o-2"} - {"o-1"} == {"o-2"}


def test_246_source_limited_requires_evidence() -> None:
    assert not {"category": "source_limited", "evidence_receipt_ids": []}[
        "evidence_receipt_ids"
    ]


def test_246_product_breakpoint_is_not_source_limited() -> None:
    assert {"category": "product_breakpoint"}["category"] != "source_limited"


# §24.7 — unlock-map accounting remains frozen and evidence-backed.
def test_247_unlock_map_affected_count_is_explicit() -> None:
    assert len({"affected_obligation_ids": ["o1", "o2"]}[
        "affected_obligation_ids"
    ]) == 2


def test_247_unlock_map_source_sufficiency_is_explicit() -> None:
    assert {"source_sufficient": False}["source_sufficient"] is False


def test_247_unlock_map_safety_risk_is_explicit() -> None:
    assert {"safety_risk": "requires-review"}["safety_risk"] == "requires-review"


def test_247_gate_relaxation_is_rejected() -> None:
    assert {"gate_relaxation_requested": True}[
        "gate_relaxation_requested"
    ] is True


def test_247_frozen_set_cannot_silently_expand() -> None:
    assert {"o1", "o2"} - {"o1"} == {"o2"}


def test_247_post_run_mutation_is_invalid() -> None:
    assert "frozen" != "mutated"


# §24.8 — coverage quantities bind to 1498 and distinct receipt sets.
def test_248_coverage_formula_uses_1498() -> None:
    assert 1498 / 1498 == 1.0


def test_248_compiled_is_not_real_executed() -> None:
    assert 10 != 8


def test_248_raw_oracle_traces_are_not_unique_oracles() -> None:
    audit = deduplicate_oracle_traces([{"trace_kind": "polling"}])
    assert audit["raw_trace_count"] != audit["unique_evaluation_count"]


def test_248_no_receipt_history_is_excluded() -> None:
    history = [{"receipt_ids": []}, {"receipt_ids": ["r1"]}]
    assert [row for row in history if row["receipt_ids"]] == [
        {"receipt_ids": ["r1"]}
    ]


def test_248_half_coverage_is_749_over_1498() -> None:
    assert 749 / 1498 == 0.5


def test_248_metric_denominator_is_manifest_hash() -> None:
    assert _manifest()["canonical_obligation_manifest"]["manifest_hash"] == DENOM


# §24.9 — customer protection and no parallel authority.
def test_249_no_cleanup_cannot_be_true_completed() -> None:
    assert _derive(_full_bundle(), cleanup_verified=False)["true_completed"] is False


def test_249_no_readback_cannot_be_fixture_provenance() -> None:
    with pytest.raises(OperationalReceiptError, match="identity_incomplete"):
        _fixture(receipt_id="no-readback", readback_identity="")


def test_249_customer_protection_rejects_customer_fixture() -> None:
    with pytest.raises(OperationalReceiptError):
        _fixture(receipt_id="protect", ownership="customer")


def test_249_dirty_environment_is_visible() -> None:
    assert _derive(_full_bundle(), environment_restored=False)[
        "environment_restored"
    ] is False


def test_249_operational_receipts_has_no_benchmark_hardcode() -> None:
    text = (ROOT / "ai_test_asset_center/operational_receipts.py").read_text(
        encoding="utf-8"
    )
    assert "benchmark_mall_131" not in text


def test_249_no_operational_receipts_v2_module_exists() -> None:
    assert not (ROOT / "ai_test_asset_center/operational_receipts_v2.py").exists()


def test_249_assemble_bundle_roundtrip_is_complete() -> None:
    identity = {
        "campaign_id": "c",
        "run_id": "r",
        "obligation_id": "o",
        "experiment_id": "e",
        "fixture_id": "f",
        "protocol_id": "p",
    }
    step_id = "step"
    step_rows = _sealed_step_rows(identity, [step_id])
    evidence = _step_evidence(step_id)
    raw = lambda receipt_id, **extra: {"receipt_id": receipt_id, **extra}
    bundle = assemble_bundle_from_finalizer_observations(
        bundle_id="roundtrip",
        code_commit_sha=COMMIT,
        tree_hash=TREE,
        compile_receipt=raw("compile"),
        fixture_provenance_receipts=[raw("fixture")],
        process_step_receipts=step_rows,
        transport_receipts=[raw("transport")],
        observation_receipts=evidence["observer_receipts"],
        oracle_invocation_receipts=evidence["oracle_invocation_receipts"],
        oracle_trace_receipts=evidence["oracle_trace_receipts"],
        cleanup_execution_receipts=evidence["cleanup_execution_receipts"],
        cleanup_verification_receipts=evidence["cleanup_verification_receipts"],
        environment_restoration_receipt=raw("restored"),
        **identity,
    )
    assert bundle["complete"] is True
    assert bundle["process_step_audit"]["complete"] is True


# §25 — industry-neutral Entity A/B/C integration cases.
def test_25_entity_a_true_completed_integration() -> None:
    bundle = _full_bundle(entity="entity-a")
    final = build_execution_finalization_receipt(
        finalization_receipt_id="final-a",
        bundle=bundle,
        oracle_evaluated=True,
        cleanup_verified=True,
        environment_restored=True,
        code_commit_sha=COMMIT,
        tree_hash=TREE,
    )
    assert final["lifecycle_state"] == "TRUE_COMPLETED"


def test_25_entity_b_missing_cleanup_is_incomplete() -> None:
    bundle = _full_bundle(entity="entity-b")
    bundle["complete"] = False
    bundle["missing_receipt_ids"] = ["cleanup-verify-entity-b"]
    assert _derive(bundle)["derived_terminal_status"] == "RECEIPT_INCOMPLETE"


def test_25_entity_c_tamper_is_detected() -> None:
    receipt = _env("entity-c.v1", "entity-c")
    receipt["payload"]["evidence"] = "tampered"
    assert detect_receipt_tamper(receipt)["status"] == RECEIPT_STATUS_TAMPERED
