from __future__ import annotations

import copy

import pytest

from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from ai_test_asset_center.evaluator_execution_attestation import (
    ExecutionAttestationError,
    PROCESS_BOUNDARY_SCHEMA,
    _expected_request_attempts,
    build_execution_attestation,
    validate_execution_attestation,
)
from tests.phase3_gate_support import build_formal_evaluation_scope


SIGNING_KEY = "execution-attestation-test-key-0123456789abcdef"


def _authority() -> tuple[dict, dict]:
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-attested",
        campaign_id="campaign-attested",
        target_id="target-attested",
        environment_id="http://127.0.0.1:8011",
        policy_version="v1",
        evaluation_mode="replay",
    )
    _, ledger = build_formal_evaluation_scope(
        [{"finding_id": "finding-attested"}],
        run_id=mainline["run_id"],
        campaign_id=mainline["campaign_id"],
        target_id=mainline["target_id"],
        environment_id=mainline["environment_id"],
        policy_version=mainline["policy_version"],
        evaluation_mode=mainline["evaluation_mode"],
    )
    return mainline, ledger


def _inputs() -> dict:
    mainline, ledger = _authority()
    attempt = ledger["attempts"][0]
    operational = attempt["operational_receipt"]
    return {
        "mainline_run": mainline,
        "obligation_attempt_ledger": ledger,
        "policy_identity": {
            "policy_id": "policy-1",
            "policy_version": "v1",
            "strategy_fingerprint": "a" * 64,
        },
        "fixture_governance": {
            "cleanup_status": "SUCCEEDED",
            "dirty_environment": False,
            "prepare_receipt_fingerprint": "prepare-1",
            "cleanup_receipt_fingerprint": "cleanup-1",
        },
        "process_boundary": {
            "schema_version": PROCESS_BOUNDARY_SCHEMA,
            "isolation": "isolated_subprocess",
            "worker_protocol_schema": (
                "qualibug.observed-product-scan-worker-request.v1"
            ),
            "evaluator_secrets_removed": True,
            "request_fingerprint": "b" * 64,
            "result_fingerprint": "c" * 64,
            "exit_code": 0,
        },
        "trusted_observations": [{
            "obligation_id": attempt["obligation_id"],
            "execution_id": attempt["execution_id"],
            "source_kind": "evaluator_http_proxy",
            "source_receipt_id": "proxy-receipt-1",
            "source_fingerprint": "d" * 64,
            "target_request_count": operational[
                "http_request_attempt_count"
            ],
            "write_count": operational["accepted_write_count"],
            "production_request_count": operational[
                "production_http_request_count"
            ],
            "audit_receipt_ids": [],
        }],
    }


def test_attestation_binds_independent_gateway_to_every_request_attempt() -> None:
    inputs = _inputs()
    attestation = build_execution_attestation(
        **inputs,
        signing_key=SIGNING_KEY,
    )

    validated = validate_execution_attestation(
        attestation,
        **{
            key: value
            for key, value in inputs.items()
            if key != "trusted_observations"
        },
        signing_key=SIGNING_KEY,
    )

    assert validated["status"] == "VERIFIED"
    assert validated["target_request_count"] > 0
    assert validated["trust_boundary"] == "evaluator_owned_io_gateway"


def test_attestation_binds_runtime_surface_attempts_outside_business_ledger() -> None:
    inputs = _inputs()
    surface_attempts = [{
        "obligation_id": "surfobl-1",
        "execution_id": "surfexec-1",
        "terminal_stage": "surface_discovery",
        "terminal_status": "EXECUTED",
        "operational_receipt": {
            "http_request_attempt_count": 1,
            "write_request_attempt_count": 0,
            "production_http_request_count": 0,
        },
    }]
    inputs["trusted_observations"].append({
        "obligation_id": "surfobl-1",
        "execution_id": "surfexec-1",
        "source_kind": "evaluator_http_proxy",
        "source_receipt_id": "surface-proxy-receipt-1",
        "source_fingerprint": "e" * 64,
        "target_request_count": 1,
        "write_count": 0,
        "production_request_count": 0,
        "audit_receipt_ids": [],
    })

    attestation = build_execution_attestation(
        **inputs,
        additional_request_attempts=surface_attempts,
        signing_key=SIGNING_KEY,
    )

    validated = validate_execution_attestation(
        attestation,
        **{
            key: value
            for key, value in inputs.items()
            if key != "trusted_observations"
        },
        additional_request_attempts=surface_attempts,
        signing_key=SIGNING_KEY,
    )

    assert validated["observed_attempt_count"] == 2
    assert validated["target_request_count"] == (
        inputs["trusted_observations"][0]["target_request_count"] + 1
    )


def test_runtime_receipts_without_independent_observation_cannot_be_attested() -> None:
    inputs = _inputs()
    inputs["trusted_observations"] = []

    with pytest.raises(
        ExecutionAttestationError,
        match="trusted_observation_coverage_incomplete",
    ):
        build_execution_attestation(**inputs, signing_key=SIGNING_KEY)


def test_unknown_gateway_attempt_reports_exact_execution_identity() -> None:
    inputs = _inputs()
    inputs["trusted_observations"][0]["obligation_id"] = (
        "obligation-unexpected__v_exact"
    )

    with pytest.raises(
        ExecutionAttestationError,
        match=(
            "trusted_observation_attempt_unknown:"
            "obligation-unexpected__v_exact"
        ),
    ):
        build_execution_attestation(**inputs, signing_key=SIGNING_KEY)


def test_compile_blocked_attempt_without_execution_is_not_request_bearing() -> None:
    expected = _expected_request_attempts({
        "attempts": [{
            "obligation_id": "obligation-blocked",
            "execution_id": "",
            "terminal_stage": "compile",
            "terminal_status": "BLOCKED",
            "operational_receipt": {},
        }],
    })

    assert expected == {}


def test_execution_stage_attempt_without_operational_receipt_fails_closed() -> None:
    with pytest.raises(
        ExecutionAttestationError,
        match="operational_receipt_missing",
    ):
        _expected_request_attempts({
            "attempts": [{
                "obligation_id": "obligation-executed",
                "execution_id": "execution-1",
                "terminal_stage": "execution",
                "terminal_status": "BLOCKED",
                "operational_receipt": {},
            }],
        })


def test_attestation_uses_write_attempt_count_not_only_accepted_writes() -> None:
    expected = _expected_request_attempts({
        "attempts": [{
            "obligation_id": "obligation-write",
            "execution_id": "execution-write",
            "terminal_stage": "execution",
            "terminal_status": "BLOCKED",
            "operational_receipt": {
                "http_request_attempt_count": 4,
                "write_request_attempt_count": 2,
                "accepted_write_count": 0,
                "production_http_request_count": 0,
            },
        }],
    })

    assert expected["obligation-write"]["write_count"] == 2


def test_attestation_keys_gateway_requests_by_executed_variant_identity() -> None:
    expected = _expected_request_attempts({
        "attempts": [{
            "obligation_id": "obligation-selected",
            "executed_obligation_id": "obligation-selected__v_exact",
            "execution_id": "execution-variant",
            "terminal_stage": "gate",
            "terminal_status": "DELIVERABLE",
            "operational_receipt": {
                "http_request_attempt_count": 3,
                "write_request_attempt_count": 1,
                "accepted_write_count": 1,
                "production_http_request_count": 0,
            },
        }],
    })

    assert set(expected) == {"obligation-selected__v_exact"}
    assert expected["obligation-selected__v_exact"]["execution_id"] == (
        "execution-variant"
    )


def test_attestation_tampering_is_rejected() -> None:
    inputs = _inputs()
    attestation = build_execution_attestation(
        **inputs,
        signing_key=SIGNING_KEY,
    )
    tampered = copy.deepcopy(attestation)
    tampered["target_request_count"] += 1

    with pytest.raises(
        ExecutionAttestationError,
        match="authentication_invalid",
    ):
        validate_execution_attestation(
            tampered,
            **{
                key: value
                for key, value in inputs.items()
                if key != "trusted_observations"
            },
            signing_key=SIGNING_KEY,
        )
