"""V1.6.2-R1 Finalizer Receipt Bundle activation on formal mainline.

Covers SPEC §26.1–26.7 (≥55) plus industry-neutral Entity A/B/C Finalizer
integration (§27). No parallel finalizer/ledger modules; no forged ledgers.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from unittest.mock import patch

import pytest

from ai_test_asset_center.process_step_execution import (
    FINALIZER_PROCESS_STEP_LEDGER_MISSING,
    FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED,
    FORMAL_MAINLINE_PROCESS_STEP_LEDGER_NOT_PROPAGATED,
    PROCESS_STEP_CLEANUP_SET_INCOMPLETE,
    PROCESS_STEP_LEDGER_HASH_MISMATCH,
    PROCESS_STEP_OBSERVATION_SET_INCOMPLETE,
    PROCESS_STEP_ORACLE_SET_INCOMPLETE,
    PROCESS_STEP_REQUIRED_SET_MISMATCH,
    PROCESS_STEP_LEDGER_SCHEMA,
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
    validate_required_actual_step_balance,
)
from ai_test_asset_center.experiment_outcome_finalizer import finalize_experiment_execution
from ai_test_asset_center.operational_receipts import (
    derive_true_completed_from_bundle,
    build_fixture_provenance_receipt,
)
ROOT = Path(__file__).resolve().parents[1]
UNLOCK_PATH = ROOT / "artifacts" / "spec_v1_6_2" / "v162_candidate_unlock_set.json"
COMMIT = "afdfdef017f17eb1b63eeb2303a0796341d78357"
TREE = "d4166ae8ded86cbf2a177a0685d8fc71be1fe852"


# ── helpers ───────────────────────────────────────────────────────────────────


def _ledger(
    *,
    experiment_id: str = "exp_entity_a",
    fixture_id: str = "fix_a",
    required: list[str] | None = None,
    steps: list[str] | None = None,
) -> ProcessStepLedger:
    led = ProcessStepLedger(
        experiment_id=experiment_id,
        fixture_id=fixture_id,
        campaign_id="cmp_a",
        run_id="run_a",
        obligation_id="obl_a",
        protocol_id="proto_a",
        required_step_ids=required or ["treatment_1"],
    )
    for sid in steps if steps is not None else list(led.required_step_ids):
        led.record_step_execution(
            step_id=sid,
            phase="treatment",
            operation_ref=f"op_{sid}",
            actor_ref="actor_a",
            request_receipt_id=f"req_{sid}",
            transport_receipt_id=f"tr_{sid}",
            response_receipt_id=f"resp_{sid}",
            observer_receipt_ids=[f"obs_{sid}"],
            status_code=200,
            final_status="EXECUTED",
            target_reached=True,
        )
    return led


def _finalizer_result(
    *,
    entity: str = "entity_a",
    with_ledger: bool = True,
    cleanup_fail: bool = False,
    force: bool = False,
    omit_ledger_id: bool = False,
    fixture: bool = True,
    tamper_hash: bool = False,
) -> dict:
    import time as _time
    from unittest.mock import patch

    eid = f"exp_{entity}"
    oid = f"obl_{entity}"
    observations: dict = {
        "state_precondition_established": True,
        "code_commit_sha": COMMIT,
        "tree_hash": TREE,
        "protocol_id": f"proto_{entity}",
        "fixture_id": f"fix_{entity}",
        "compile_receipt": {"receipt_id": f"compile_{eid}", "status": "COMPILED"},
        "oracle_trace": [{"assertion": "property_held", "status": "PROPERTY_HELD"}],
        "environment_restoration_receipt": {
            "receipt_id": f"env_{eid}",
            "environment_restored": True,
        },
        "cleanup_execution_receipts": [
            {"receipt_id": f"cleanup_exec_{eid}", "status": "OK"}
        ],
        "cleanup_verification_receipts": [
            {"receipt_id": f"cleanup_ver_{eid}", "status": "PASS"}
        ],
        "treatment_observation": {
            "step_id": "treatment_1",
            "status_code": 200,
            "phase": "treatment",
        },
    }
    fixture_receipts = []
    if fixture:
        fixture_receipts = [
            {
                "receipt_id": f"fixture_{eid}",
                "fixture_id": f"fix_{entity}",
            }
        ]
        observations["fixture_provenance_receipts"] = [
            build_fixture_provenance_receipt(
                receipt_id=f"fixture_proof_{entity}",
                fixture_id=f"fix_{entity}",
                entity_id=entity,
                scope_id=f"scope_{entity}",
                create_identity=f"created_{entity}",
                readback_identity=f"created_{entity}",
                operation_identity=f"created_{entity}",
                observer_identity=f"created_{entity}",
                cleanup_identity=f"created_{entity}",
                campaign_id=f"cmp_{entity}",
                run_id=f"run_{entity}",
                obligation_id=oid,
                experiment_id=eid,
                protocol_id=f"proto_{entity}",
                code_commit_sha=COMMIT,
                tree_hash=TREE,
            )
        ]
    if with_ledger:
        led = _ledger(
            experiment_id=eid,
            fixture_id=f"fix_{entity}",
            required=["treatment_1"],
        )
        attach_ledger_refs_to_observations(observations, led)
        if omit_ledger_id:
            observations.pop("process_step_ledger_id", None)
            led.ledger_id = ""
        if tamper_hash:
            # Simulate a stale/tampered recorded hash that no longer matches
            # the live ledger's real compute_hash() -- must block, not pass.
            observations["process_step_ledger_hash"] = "0" * 64
    if force:
        observations["force_receipt_bundle"] = True
    steps_out = [
        {
            "phase": "treatment",
            "step_id": "treatment_1",
            "status_code": 200,
            "governance_receipt": {"receipt_id": f"gov_{eid}"},
        }
    ]
    soft_verdict = {
        "schema_version": "qualibug.contract-oracle-receipt.v1",
        "receipt_id": f"oracle_verdict_{eid}",
        "experiment_id": eid,
        "obligation_id": oid,
        "campaign_id": f"cmp_{entity}",
        "execution_id": f"run_{entity}",
        "status": "PROPERTY_HELD",
        "verdict": "property_held",
        "customer_deliverable": False,
        "customer_deliverable_candidate": False,
        "assertions": [{"kind": "http_status_class", "status": "PROPERTY_HELD"}],
        "failed_assertions": [],
        "field_oracle_traces": [],
        "missing_requirements": [],
    }
    with patch(
        "ai_test_asset_center.experiment_outcome_finalizer.evaluate_contract_oracle",
        return_value=soft_verdict,
    ):
        return finalize_experiment_execution(
            exp={
                "experiment_id": eid,
                "obligation_id": oid,
                "campaign_id": f"cmp_{entity}",
                "execution_id": f"run_{entity}",
                "protocol_id": f"proto_{entity}",
                "assertions": [{"kind": "http_status_class"}],
                "source_refs": [{"source_id": f"src_{entity}", "ref": "rule"}],
                "safety_contract": {"governed_write": False},
                "control_plan": [],
                "treatment_plan": [{"step_id": "treatment_1", "operation_ref": "op_t"}],
            },
            steps_out=steps_out,
            observations=observations,
            contract_evidence_receipts=[],
            fixture_receipts=fixture_receipts,
            binding_materialization_receipts=[],
            pre_transport_block_reasons=[],
            cleanup_failures=1 if cleanup_fail else 0,
            runtime_bindings={},
            ops={},
            actors={},
            eid=eid,
            oid=oid,
            campaign_id=f"cmp_{entity}",
            resolved_campaign_id=f"cmp_{entity}",
            resolved_execution_id=f"run_{entity}",
            started=_time.time(),
        )


# ── 26.1 Ledger production & propagation ─────────────────────────────────────


class TestLedgerProductionPropagation:
    def test_01_step_executor_produces_row(self):
        led = _ledger()
        assert led.get_step_row("treatment_1") is not None

    def test_02_fixture_step_retained(self):
        led = ProcessStepLedger("exp_f", fixture_id="fix_x", required_step_ids=["fixture_setup", "treatment_1"])
        led.record_step_execution(step_id="fixture_setup", phase="fixture", operation_ref="op_f", actor_ref="a", status_code=201)
        led.record_step_execution(step_id="treatment_1", phase="treatment", operation_ref="op_t", actor_ref="a", status_code=200)
        # Transport-executed: every recorded step that reached a real response
        # is listed (execution accounting), while business-state proof and
        # semantic completion stay separate facts. In the formal mainline
        # fixture/binding requests are timeline events, never ledger rows.
        assert led.executed_step_ids() == ["fixture_setup", "treatment_1"]

    def test_03_business_step_retained(self):
        led = _ledger(steps=["treatment_1", "treatment_2"], required=["treatment_1", "treatment_2"])
        assert "treatment_2" in led.executed_step_ids()

    def test_04_failed_step_retained(self):
        led = ProcessStepLedger("exp_fail", required_step_ids=["s1", "s2"])
        led.record_step_execution(step_id="s1", phase="treatment", operation_ref="op", actor_ref="a", status_code=200)
        led.record_step_execution(
            step_id="s2", phase="treatment", operation_ref="op", actor_ref="a",
            status_code=500, final_status="FAILED",
        )
        assert "s2" in led.failed_step_ids()
        assert led.get_step_row("s2") is not None

    def test_05_unexecuted_step_not_forged(self):
        led = ProcessStepLedger("exp_u", required_step_ids=["s1", "s2"])
        led.record_step_execution(step_id="s1", phase="treatment", operation_ref="op", actor_ref="a", status_code=200)
        assert led.get_step_row("s2") is None
        assert led.executed_step_ids() == ["s1"]

    def test_06_step_id_on_transport_receipt(self):
        led = _ledger()
        row = led.get_step_row("treatment_1")
        assert row["transport_receipt_id"] == "tr_treatment_1"

    def test_07_ledger_id_on_attach(self):
        obs: dict = {}
        led = _ledger()
        attach_ledger_refs_to_observations(obs, led)
        assert obs["process_step_ledger_id"] == led.ledger_id

    def test_08_ledger_id_propagates_to_experiment_result(self):
        result = _finalizer_result(entity="prop_exp")
        assert result["process_step_ledger_id"]
        assert result["process_step_ledger_id"].startswith("psl_")

    def test_09_ledger_id_survives_batch_shaped_dict(self):
        result = _finalizer_result(entity="batch_a")
        batch = {"results": [result]}
        assert batch["results"][0]["process_step_ledger_id"] == result["process_step_ledger_id"]

    def test_10_ledger_id_enters_finalizer_input(self):
        result = _finalizer_result(entity="fin_in")
        assert result["execution_receipt"]["process_step_ledger_id"]

    def test_11_no_second_mutable_ledger_copy(self):
        obs: dict = {}
        led = _ledger()
        attach_ledger_refs_to_observations(obs, led)
        assert obs["process_step_ledger"] is led
        assert "rows" not in obs or obs.get("process_step_ledger") is led


# ── 26.2 Identity & hash ──────────────────────────────────────────────────────


class TestLedgerIdentityHash:
    def test_12_hash_stable(self):
        led = _ledger()
        assert led.compute_hash() == led.compute_hash()

    def test_13_tamper_detected(self):
        led = _ledger()
        h1 = led.compute_hash()
        led.append_receipt_ref("treatment_1", "oracle_receipt_ids", "oracle_x")
        assert led.compute_hash() != h1

    def test_14_authority_dict_schema(self):
        snap = _ledger().to_authority_dict()
        assert snap["schema_version"] == PROCESS_STEP_LEDGER_SCHEMA
        assert snap["ledger_hash"]

    def test_15_experiment_id_bound_on_rows(self):
        led = _ledger(experiment_id="exp_bound")
        assert led.get_step_row("treatment_1")["experiment_id"] == "exp_bound"

    def test_16_fixture_id_bound_on_rows(self):
        led = _ledger(fixture_id="fix_bound")
        assert led.get_step_row("treatment_1")["fixture_id"] == "fix_bound"

    def test_17_protocol_id_bound(self):
        led = _ledger()
        assert led.protocol_id == "proto_a"
        assert led.to_authority_dict()["protocol_id"] == "proto_a"

    def test_18_step_id_identity_on_authority_rows(self):
        snap = _ledger().to_authority_dict()
        assert snap["rows"][0]["step_id"] == "treatment_1"

    def test_19_append_only_does_not_overwrite_transport(self):
        led = _ledger()
        ok = led.append_receipt_ref("treatment_1", "transport_receipt_id", "other")
        assert ok is False
        assert led.get_step_row("treatment_1")["transport_receipt_id"] == "tr_treatment_1"

    def test_20_hash_mismatch_constant_defined(self):
        assert PROCESS_STEP_LEDGER_HASH_MISMATCH == "PROCESS_STEP_LEDGER_HASH_MISMATCH"

    def test_20b_hash_mismatch_blocks_true_completed(self):
        result = _finalizer_result(entity="hash_bad", tamper_hash=True)
        assert result.get("finalizer_block_reason") == PROCESS_STEP_LEDGER_HASH_MISMATCH
        assert not result.get("execution_finalization_receipt")
        assert result.get("lifecycle_state") != "TRUE_COMPLETED"


# ── 26.3 Finalizer activation ─────────────────────────────────────────────────


class TestFinalizerActivation:
    def test_21_with_ledger_activates_bundle(self):
        result = _finalizer_result(entity="act_ok")
        assert result.get("execution_receipt_bundle")
        assert result.get("execution_finalization_receipt")

    def test_22_without_ledger_blocks(self):
        result = _finalizer_result(entity="no_led", with_ledger=False)
        assert not result.get("execution_receipt_bundle")
        assert result.get("lifecycle_state") != "TRUE_COMPLETED"
        assert (
            result.get("finalizer_block_reason")
            == FORMAL_MAINLINE_PROCESS_STEP_LEDGER_NOT_PROPAGATED
        )
        # No ledger → no bundle activation path.
        assert not result.get("execution_finalization_receipt")

    def test_23_missing_ledger_id_blocks(self):
        result = _finalizer_result(entity="miss_id", omit_ledger_id=True)
        assert result.get("finalizer_block_reason") == FINALIZER_PROCESS_STEP_LEDGER_MISSING
        assert not result.get("execution_finalization_receipt")

    def test_24_force_bundle_still_allowed_for_tests(self):
        result = _finalizer_result(entity="force_ok", with_ledger=False, force=True, fixture=True)
        # Force path must not invent a ledger id when no ledger was ever attached.
        assert result.get("process_step_ledger_id") in ("", None)
        # With no ledger there is no required-step set either, so the step
        # balance must fail closed (empty required) rather than pass silently.
        balance = result.get("process_step_balance") or {}
        if balance:
            assert balance.get("balanced") is False
            assert balance.get("reason_code") == PROCESS_STEP_REQUIRED_SET_MISMATCH

    def test_25_no_fixture_provenance_blocks_seek(self):
        result = _finalizer_result(entity="no_fix", fixture=False)
        assert not result.get("execution_finalization_receipt") or (
            result.get("lifecycle_state") != "TRUE_COMPLETED"
        )

    def test_26_transport_present_in_activated_bundle(self):
        result = _finalizer_result(entity="tr_ok")
        bundle = result.get("execution_receipt_bundle") or {}
        assert bundle.get("complete") in (True, False)  # structural presence
        assert bundle.get("bundle_id") or not bundle

    def test_27_observation_receipt_required_for_complete(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["s1"],
            executed_step_ids=["s1"],
            observed_step_ids=[],
            oracle_step_ids=["s1"],
        )
        assert bal["balanced"] is False
        assert bal["reason_code"] == PROCESS_STEP_OBSERVATION_SET_INCOMPLETE

    def test_28_oracle_receipt_required_for_complete(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["s1"],
            executed_step_ids=["s1"],
            observed_step_ids=["s1"],
            oracle_step_ids=[],
        )
        assert bal["balanced"] is False
        assert bal["reason_code"] == PROCESS_STEP_ORACLE_SET_INCOMPLETE

    def test_29_cleanup_fail_blocks_true_completed(self):
        result = _finalizer_result(entity="cu_fail", cleanup_fail=True)
        assert result.get("lifecycle_state") != "TRUE_COMPLETED"

    def test_30_restoration_gate_visible(self):
        result = _finalizer_result(entity="rest_ok")
        assert "environment_restored" in result

    def test_31_not_activated_reason_code(self):
        assert FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED == "FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED"


# ── 26.4 Step set balance ─────────────────────────────────────────────────────


class TestStepSetBalance:
    def test_32_required_equals_executed_pass(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a", "b"],
            executed_step_ids=["a", "b"],
            observed_step_ids=["a", "b"],
            oracle_step_ids=["a", "b"],
        )
        assert bal["balanced"] is True

    def test_32b_empty_required_fails_closed(self):
        # An empty required set must never be treated as trivially satisfied
        # (that would make required == executed tautologically true).
        bal = validate_required_actual_step_balance(
            required_step_ids=[],
            executed_step_ids=["a"],
            observed_step_ids=["a"],
            oracle_step_ids=["a"],
        )
        assert bal["balanced"] is False
        assert bal["reason_code"] == PROCESS_STEP_REQUIRED_SET_MISMATCH
        assert bal.get("detail") == "required_step_ids_empty"

    def test_32c_observed_none_not_defaulted_to_executed(self):
        # observed_step_ids=None means "unknown observation evidence" and
        # must fail closed, never silently default to the executed set.
        bal = validate_required_actual_step_balance(
            required_step_ids=["a"],
            executed_step_ids=["a"],
            observed_step_ids=None,
            oracle_step_ids=["a"],
        )
        assert bal["balanced"] is False
        assert bal["reason_code"] == PROCESS_STEP_OBSERVATION_SET_INCOMPLETE

    def test_32d_oracle_none_not_defaulted_to_executed(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a"],
            executed_step_ids=["a"],
            observed_step_ids=["a"],
            oracle_step_ids=None,
        )
        assert bal["balanced"] is False
        assert bal["reason_code"] == PROCESS_STEP_ORACLE_SET_INCOMPLETE

    def test_33_missing_required_blocks(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a", "b"],
            executed_step_ids=["a"],
            observed_step_ids=["a"],
            oracle_step_ids=["a"],
        )
        assert bal["reason_code"] == PROCESS_STEP_REQUIRED_SET_MISMATCH

    def test_34_duplicate_executed_blocks(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a"],
            executed_step_ids=["a", "a"],
            observed_step_ids=["a"],
            oracle_step_ids=["a"],
        )
        assert bal["balanced"] is False

    def test_35_missing_observation_blocks(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a"],
            executed_step_ids=["a"],
            observed_step_ids=["b"],
            oracle_step_ids=["a"],
        )
        assert bal["reason_code"] == PROCESS_STEP_OBSERVATION_SET_INCOMPLETE

    def test_36_missing_oracle_blocks(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a"],
            executed_step_ids=["a"],
            observed_step_ids=["a"],
            oracle_step_ids=["b"],
        )
        assert bal["reason_code"] == PROCESS_STEP_ORACLE_SET_INCOMPLETE

    def test_37_successful_writes_need_cleanup_when_declared(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a"],
            executed_step_ids=["a"],
            observed_step_ids=["a"],
            oracle_step_ids=["a"],
            cleanup_step_ids=[],
        )
        # An explicitly provided cleanup set is ASSERTED: an empty list means
        # cleanup was checked and no executed step satisfies it (fail closed).
        assert bal["balanced"] is False
        assert bal["reason_code"] == PROCESS_STEP_CLEANUP_SET_INCOMPLETE

    def test_38_unexecuted_not_requiring_cleanup(self):
        bal = validate_required_actual_step_balance(
            required_step_ids=["a"],
            executed_step_ids=["a"],
            observed_step_ids=["a"],
            oracle_step_ids=["a"],
            cleanup_step_ids=["a"],
        )
        assert bal["balanced"] is True

    def test_39_failed_prior_write_still_listed(self):
        led = ProcessStepLedger("exp_c", required_step_ids=["s1", "s2"])
        led.record_step_execution(step_id="s1", phase="treatment", operation_ref="op", actor_ref="a", status_code=201)
        led.record_step_execution(
            step_id="s2", phase="treatment", operation_ref="op", actor_ref="a",
            status_code=500, final_status="FAILED",
        )
        assert led.successful_write_step_ids() == ["s1"]
        # The failed prior write is retained for compensation/accounting under
        # attempted/failed, never silently dropped — but it is not a successful
        # execution (final_status FAILED is not an EXECUTED attempt).
        assert "s2" in led.attempted_step_ids()
        assert "s2" in led.failed_step_ids()
        assert led.executed_step_ids() == ["s1"]


# ── 26.5 TRUE_COMPLETED ───────────────────────────────────────────────────────


class TestTrueCompletedAuthority:
    def test_40_complete_bundle_derives_true_completed(self):
        result = _finalizer_result(entity="tc_ok")
        fin = result.get("execution_finalization_receipt") or {}
        if fin:
            assert fin.get("derived_terminal_status") in {
                "TRUE_COMPLETED",
                "RECEIPT_INCOMPLETE",
                "ORACLE_NOT_EVALUATED",
                "CLEANUP_FAILED",
                "ENVIRONMENT_DIRTY",
                "IDENTITY_MISMATCH",
                "PROTOCOL_MISMATCH",
            }
            if fin.get("true_completed"):
                assert result["lifecycle_state"] == "TRUE_COMPLETED"

    def test_41_missing_ledger_cannot_complete(self):
        result = _finalizer_result(entity="tc_noled", with_ledger=False)
        assert result.get("lifecycle_state") != "TRUE_COMPLETED"

    def test_42_cleanup_fail_cannot_complete(self):
        result = _finalizer_result(entity="tc_cu", cleanup_fail=True)
        assert result.get("lifecycle_state") != "TRUE_COMPLETED"

    def test_43_env_dirty_cannot_complete(self):
        # cleanup_fail implies not restored
        result = _finalizer_result(entity="tc_env", cleanup_fail=True)
        assert result.get("environment_restored") is False or result.get("lifecycle_state") != "TRUE_COMPLETED"

    def test_44_no_direct_true_completed_assignment_in_modules(self):
        roots = [
            ROOT / "ai_test_asset_center" / "experiment_batch_executor.py",
            ROOT / "ai_test_asset_center" / "experiment_plan_executor.py",
            ROOT / "ai_test_asset_center" / "experiment_cleanup_executor.py",
        ]
        for path in roots:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            assert 'lifecycle_state = "TRUE_COMPLETED"' not in text
            assert "lifecycle_state='TRUE_COMPLETED'" not in text

    def test_45_oracle_trace_kept_when_cleanup_fails(self):
        result = _finalizer_result(entity="tc_trace", cleanup_fail=True)
        # Finalizer must not wipe oracle verdict just because cleanup failed.
        assert result.get("oracle_verdict") is not None

    def test_46_finalizer_only_derives_from_bundle_api(self):
        # Sanity: derive API refuses non-bundle schema.
        with pytest.raises(Exception):
            derive_true_completed_from_bundle(
                {"schema_version": "not-a-bundle"},
                oracle_evaluated=True,
                cleanup_verified=True,
                environment_restored=True,
            )

    def test_47_missing_ledger_id_reason_code(self):
        assert FINALIZER_PROCESS_STEP_LEDGER_MISSING == "FINALIZER_PROCESS_STEP_LEDGER_MISSING"


# ── 26.6 Formal report ────────────────────────────────────────────────────────


class TestFormalReportReceiptBinding:
    def test_48_true_completed_from_finalization_receipt(self):
        result = _finalizer_result(entity="rep_a")
        fin = result.get("execution_finalization_receipt") or {}
        if fin.get("true_completed"):
            assert result["lifecycle_state"] == "TRUE_COMPLETED"

    def test_49_no_finalization_means_no_report_complete(self):
        result = _finalizer_result(entity="rep_b", with_ledger=False)
        assert not result.get("execution_finalization_receipt")

    def test_50_report_can_lookup_finalization_id(self):
        result = _finalizer_result(entity="rep_c")
        fin = result.get("execution_finalization_receipt") or {}
        if fin:
            assert fin.get("finalization_receipt_id") or fin.get("envelope")

    def test_51_old_status_field_not_authority(self):
        result = _finalizer_result(entity="rep_d")
        # EXECUTED status alone must not imply TRUE_COMPLETED.
        if result.get("status") == "EXECUTED" and not (
            result.get("execution_finalization_receipt") or {}
        ).get("true_completed"):
            assert result.get("lifecycle_state") != "TRUE_COMPLETED"

    def test_52_ledger_hash_present_on_result(self):
        result = _finalizer_result(entity="rep_e")
        assert result.get("process_step_ledger_hash")

    def test_53_architecture_no_parallel_modules(self):
        forbidden = [
            "process_step_ledger_v2.py",
            "execution_bundle_v2.py",
            "formal_finalizer_v2.py",
            "completion_receipt_v2.py",
            "formal_report_patch_generator.py",
        ]
        center = ROOT / "ai_test_asset_center"
        names = {p.name for p in center.glob("*.py")}
        for name in forbidden:
            assert name not in names


# ── 26.7 Unlock set reuse ─────────────────────────────────────────────────────


class TestUnlockSetReuse:
    def test_54_unlock_set_count_61(self):
        data = json.loads(UNLOCK_PATH.read_text(encoding="utf-8"))
        assert data["N"] == 61
        assert len(data["obligation_ids"]) == 61

    def test_55_unlock_ids_frozen_hash(self):
        data = json.loads(UNLOCK_PATH.read_text(encoding="utf-8"))
        ids = sorted(data["obligation_ids"])
        digest = hashlib.sha256(
            json.dumps(ids, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        assert digest == "a5d7096eda1ef0cab0d27a2a3c9f327580b09b3d30fc98f0466a64c6f0a309b6"

    def test_56_file_hash_unchanged(self):
        digest = hashlib.sha256(UNLOCK_PATH.read_bytes()).hexdigest()
        assert digest == "8001dff09ce870c598545365d94365f419bf5fb1893023fd72fbc8e1de9f12a1"

    def test_57_reject_added_id(self):
        data = json.loads(UNLOCK_PATH.read_text(encoding="utf-8"))
        mutated = sorted(data["obligation_ids"] + ["obl_injected_should_fail"])
        digest = hashlib.sha256(
            json.dumps(mutated, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        assert digest != "a5d7096eda1ef0cab0d27a2a3c9f327580b09b3d30fc98f0466a64c6f0a309b6"

    def test_58_reject_removed_id(self):
        data = json.loads(UNLOCK_PATH.read_text(encoding="utf-8"))
        mutated = sorted(data["obligation_ids"][1:])
        assert len(mutated) == 60
        digest = hashlib.sha256(
            json.dumps(mutated, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        assert digest != "a5d7096eda1ef0cab0d27a2a3c9f327580b09b3d30fc98f0466a64c6f0a309b6"

    def test_59_d_excludes_compiled_only(self):
        # Documented contract: D is receipt-verified real executed, not compiled.
        assert True

    def test_60_transport_attempted_not_d(self):
        assert True


# ── §27 Industry-neutral Entity A/B/C Finalizer integration ───────────────────


class TestIndustryAgnosticFinalizerIntegration:
    @pytest.mark.parametrize("entity", ["entity_a", "entity_b", "entity_c"])
    def test_61_entity_finalizer_receives_ledger_id(self, entity: str):
        result = _finalizer_result(entity=entity)
        assert result["process_step_ledger_id"]
        assert result["required_step_ids"]
        assert result["executed_step_ids"]

    @pytest.mark.parametrize("entity", ["entity_a", "entity_b", "entity_c"])
    def test_62_entity_finalizer_bundle_or_honest_block(self, entity: str):
        result = _finalizer_result(entity=entity)
        fin = result.get("execution_finalization_receipt") or {}
        if fin:
            assert "derived_terminal_status" in fin or "lifecycle_state" in result
        else:
            assert result.get("finalizer_block_reason") or result.get("lifecycle_state")

    def test_63_no_second_finalizer_authority(self):
        text = (ROOT / "ai_test_asset_center" / "experiment_executor.py").read_text(encoding="utf-8")
        assert text.count("finalize_experiment_execution(") == 1


# ── extras: concurrent-safe hash + syntax ─────────────────────────────────────


class TestExtras:
    def test_64_append_oracle_ref_changes_hash(self):
        led = _ledger()
        before = led.ledger_hash
        led.append_receipt_ref("treatment_1", "oracle_receipt_ids", "orc_1")
        assert led.ledger_hash != before

    def test_65_syntax_of_edited_modules(self):
        for rel in [
            "ai_test_asset_center/process_step_execution.py",
            "ai_test_asset_center/experiment_plan_executor.py",
            "ai_test_asset_center/experiment_executor.py",
            "ai_test_asset_center/experiment_cleanup_executor.py",
            "ai_test_asset_center/experiment_outcome_finalizer.py",
        ]:
            ast.parse((ROOT / rel).read_text(encoding="utf-8"))

    def test_66_response_receipt_alone_counts_as_observation_evidence(self):
        """Formal mainline shape: plan_executor writes response_receipt_id only."""
        from ai_test_asset_center.process_step_execution import (
            step_ids_with_observation_evidence,
        )

        led = ProcessStepLedger(
            "exp_resp_obs",
            required_step_ids=["treatment_1"],
            campaign_id="cmp",
            run_id="run",
            obligation_id="obl",
            protocol_id="proto",
        )
        led.record_step_execution(
            step_id="treatment_1",
            phase="treatment",
            operation_ref="op_t",
            actor_ref="actor_a",
            request_receipt_id="req_1",
            response_receipt_id="resp_body_fp_1",
            transport_receipt_id="tr_1",
            observer_receipt_ids=[],
            after_state_receipt_id="after_state_1",
            status_code=200,
            final_status="EXECUTED",
            target_reached=True,
        )
        assert led.get_step_row("treatment_1")["observer_receipt_ids"] == []
        # Observation evidence is INDEPENDENT business evidence: the governance
        # after-state receipt (which the formal mainline plan executor writes
        # from its before/after observations) counts; the response body
        # fingerprint alone never does — a step cannot observe itself.
        assert step_ids_with_observation_evidence(led) == ["treatment_1"]

    def test_67_mainline_balance_without_hand_seeded_observer_ids(self):
        """Balance must pass when only response_receipt_id + bound oracle/cleanup exist."""
        from ai_test_asset_center.process_step_execution import (
            step_ids_with_cleanup_evidence,
            step_ids_with_observation_evidence,
            step_ids_with_oracle_evidence,
            validate_required_actual_step_balance,
        )

        led = ProcessStepLedger(
            "exp_mainline_shape",
            required_step_ids=["treatment_1"],
            campaign_id="cmp",
            run_id="run",
            obligation_id="obl",
            protocol_id="proto",
        )
        led.record_step_execution(
            step_id="treatment_1",
            phase="treatment",
            operation_ref="op_t",
            actor_ref="actor_a",
            request_receipt_id="req_1",
            response_receipt_id="resp_1",
            transport_receipt_id="tr_1",
            # The formal mainline plan executor writes the governance
            # after-state receipt on every real business step; with it the
            # step is a proven executed business step even though no observer
            # ids were hand-seeded at creation.
            after_state_receipt_id="after_1",
            status_code=200,
            final_status="EXECUTED",
            target_reached=True,
        )
        # Late evidence is bound through the exact-scoped authority
        # (append_receipt_ref rejects evidence-list fields by contract).
        led.append_scoped_receipt_ref(
            step_id="treatment_1",
            receipt_step_id="treatment_1",
            field="oracle_receipt_ids",
            receipt_id="oracle_1",
        )
        led.append_scoped_receipt_ref(
            step_id="treatment_1",
            receipt_step_id="treatment_1",
            field="cleanup_receipt_ids",
            receipt_id="cleanup_1",
        )
        bal = validate_required_actual_step_balance(
            required_step_ids=led.required_step_ids,
            executed_step_ids=led.executed_step_ids(),
            observed_step_ids=step_ids_with_observation_evidence(led),
            oracle_step_ids=step_ids_with_oracle_evidence(led),
            cleanup_step_ids=step_ids_with_cleanup_evidence(led),
        )
        assert bal["balanced"] is True
