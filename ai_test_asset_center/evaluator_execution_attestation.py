from __future__ import annotations

"""Evaluator-owned proof that runtime receipts correspond to trusted I/O.

Runtime hashes prove internal consistency, not that a request reached a target.
This contract therefore requires one observation from an evaluator-controlled
gateway for every request-bearing attempt. Product runtime never receives the
signing key and cannot author this artifact.
"""

import hashlib
import json
import time
from typing import Any

from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from .evaluator_receipt_auth import (
    EvaluatorReceiptAuthError,
    seal_evaluator_artifact,
    verify_evaluator_artifact,
)
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)


EXECUTION_ATTESTATION_SCHEMA = "qualibug.evaluator-execution-attestation.v1"
PROCESS_BOUNDARY_SCHEMA = "qualibug.observed-product-process-boundary.v1"
ATTESTATION_FINGERPRINT_FIELD = "attestation_fingerprint"
ATTESTATION_AUTHENTICATION_FIELD = "attestation_authentication"

TRUSTED_OBSERVATION_SOURCES = frozenset({
    "browser_network_proxy",
    "database_audit_gateway",
    "evaluator_http_proxy",
    "external_observability_gateway",
    "governed_sandbox_executor",
})


class ExecutionAttestationError(ValueError):
    """Trusted I/O evidence is absent, forged, or mismatched."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    text = _text(value).lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ExecutionAttestationError(f"{field}_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionAttestationError(f"{field}_invalid") from exc
    if parsed < 0:
        raise ExecutionAttestationError(f"{field}_invalid")
    return parsed


def _validated_boundary(value: dict[str, Any]) -> dict[str, Any]:
    row = dict(_dict(value))
    expected_fields = {
        "schema_version",
        "isolation",
        "worker_protocol_schema",
        "evaluator_secrets_removed",
        "request_fingerprint",
        "result_fingerprint",
        "exit_code",
    }
    if set(row) != expected_fields:
        raise ExecutionAttestationError("process_boundary_fields_invalid")
    if row.get("schema_version") != PROCESS_BOUNDARY_SCHEMA:
        raise ExecutionAttestationError("process_boundary_schema_invalid")
    if row.get("isolation") != "isolated_subprocess":
        raise ExecutionAttestationError("process_boundary_isolation_invalid")
    if row.get("evaluator_secrets_removed") is not True:
        raise ExecutionAttestationError("process_boundary_secret_isolation_invalid")
    if row.get("exit_code") != 0:
        raise ExecutionAttestationError("process_boundary_exit_invalid")
    for field in ("worker_protocol_schema", "request_fingerprint", "result_fingerprint"):
        if not _text(row.get(field)):
            raise ExecutionAttestationError(f"process_boundary_{field}_missing")
    for field in ("request_fingerprint", "result_fingerprint"):
        if not _is_sha256(row.get(field)):
            raise ExecutionAttestationError(f"process_boundary_{field}_invalid")
    return row


def _validated_policy_identity(value: dict[str, Any]) -> dict[str, str]:
    row = _dict(value)
    if set(row) != {"policy_id", "policy_version", "strategy_fingerprint"}:
        raise ExecutionAttestationError("execution_policy_identity_fields_invalid")
    normalized = {field: _text(row.get(field)) for field in row}
    if not all(normalized.values()):
        raise ExecutionAttestationError("execution_policy_identity_missing")
    if not _is_sha256(normalized["strategy_fingerprint"]):
        raise ExecutionAttestationError("execution_strategy_fingerprint_invalid")
    return normalized


def _expected_request_attempts(
    ledger: dict[str, Any],
    *,
    additional_request_attempts: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    attempts = [
        raw
        for raw in _list(ledger.get("attempts"))
        if isinstance(raw, dict)
    ]
    attempts.extend(
        raw
        for raw in (additional_request_attempts or [])
        if isinstance(raw, dict)
    )
    for raw in attempts:
        attempt = _dict(raw)
        operational = _dict(attempt.get("operational_receipt"))
        selected_attempt_id = _text(attempt.get("obligation_id"))
        attempt_id = (
            _text(attempt.get("executed_obligation_id"))
            or selected_attempt_id
        )
        execution_id = _text(attempt.get("execution_id"))
        terminal_stage = _text(attempt.get("terminal_stage")).lower()
        if not operational:
            if not execution_id and terminal_stage == "compile":
                continue
            raise ExecutionAttestationError(
                f"operational_receipt_missing:{selected_attempt_id or 'MISSING'}"
            )
        request_count = _non_negative_int(
            operational.get("http_request_attempt_count"),
            "operational_http_request_attempt_count",
        )
        if request_count == 0:
            continue
        if (
            not selected_attempt_id
            or not attempt_id
            or not execution_id
            or attempt_id in expected
        ):
            raise ExecutionAttestationError("execution_attempt_identity_invalid")
        expected[attempt_id] = {
            "execution_id": execution_id,
            "target_request_count": request_count,
            "write_count": _non_negative_int(
                operational.get("write_request_attempt_count"),
                "operational_write_request_attempt_count",
            ),
            "production_request_count": _non_negative_int(
                operational.get("production_http_request_count"),
                "operational_production_http_request_count",
            ),
        }
    return expected


def _validated_observations(
    values: list[dict[str, Any]],
    *,
    ledger: dict[str, Any],
    additional_request_attempts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    expected = _expected_request_attempts(
        ledger,
        additional_request_attempts=additional_request_attempts,
    )
    observed: dict[str, dict[str, Any]] = {}
    fields = {
        "obligation_id",
        "execution_id",
        "source_kind",
        "source_receipt_id",
        "source_fingerprint",
        "target_request_count",
        "write_count",
        "production_request_count",
        "audit_receipt_ids",
    }
    for raw in values:
        row = dict(_dict(raw))
        if set(row) != fields:
            raise ExecutionAttestationError("trusted_observation_fields_invalid")
        attempt_id = _text(row.get("obligation_id"))
        if not attempt_id or attempt_id in observed:
            raise ExecutionAttestationError("trusted_observation_attempt_duplicate")
        expected_row = expected.get(attempt_id)
        if expected_row is None:
            raise ExecutionAttestationError(
                f"trusted_observation_attempt_unknown:{attempt_id}"
            )
        source_kind = _text(row.get("source_kind"))
        if source_kind not in TRUSTED_OBSERVATION_SOURCES:
            raise ExecutionAttestationError("trusted_observation_source_invalid")
        if not _text(row.get("source_receipt_id")) or not _is_sha256(
            row.get("source_fingerprint")
        ):
            raise ExecutionAttestationError("trusted_observation_source_receipt_invalid")
        normalized = {
            **row,
            "obligation_id": attempt_id,
            "execution_id": _text(row.get("execution_id")),
            "source_kind": source_kind,
            "source_receipt_id": _text(row.get("source_receipt_id")),
            "source_fingerprint": _text(row.get("source_fingerprint")),
            "target_request_count": _non_negative_int(
                row.get("target_request_count"), "trusted_target_request_count"
            ),
            "write_count": _non_negative_int(
                row.get("write_count"), "trusted_write_count"
            ),
            "production_request_count": _non_negative_int(
                row.get("production_request_count"),
                "trusted_production_request_count",
            ),
            "audit_receipt_ids": sorted({
                _text(value)
                for value in _list(row.get("audit_receipt_ids"))
                if _text(value)
            }),
        }
        for field in (
            "execution_id",
            "target_request_count",
            "write_count",
            "production_request_count",
        ):
            if normalized[field] != expected_row[field]:
                raise ExecutionAttestationError(
                    f"trusted_observation_{field}_mismatch:"
                    f"obligation={attempt_id}:"
                    f"expected={expected_row[field]}:"
                    f"observed={normalized[field]}"
                )
        if normalized["production_request_count"]:
            raise ExecutionAttestationError(
                "trusted_observation_production_request_forbidden"
            )
        if normalized["write_count"] and not normalized["audit_receipt_ids"]:
            raise ExecutionAttestationError(
                "trusted_observation_write_audit_missing"
            )
        observed[attempt_id] = normalized
    if set(observed) != set(expected):
        raise ExecutionAttestationError("trusted_observation_coverage_incomplete")
    if not observed:
        raise ExecutionAttestationError("trusted_target_request_observation_missing")
    return [observed[key] for key in sorted(observed)]


def _validated_inputs(
    *,
    mainline_run: dict[str, Any],
    obligation_attempt_ledger: dict[str, Any],
    policy_identity: dict[str, Any],
    fixture_governance: dict[str, Any],
    process_boundary: dict[str, Any],
    trusted_observations: list[dict[str, Any]],
    additional_request_attempts: list[dict[str, Any]] | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
]:
    try:
        mainline = validate_mainline_run_contract(mainline_run)
        ledger = validate_obligation_attempt_ledger(obligation_attempt_ledger)
    except (MainlineContractError, ObligationAttemptLedgerError) as exc:
        raise ExecutionAttestationError(
            f"execution_authority_invalid:{exc}"
        ) from exc
    if ledger.get("run_id") != mainline["run_id"] or ledger.get(
        "campaign_id"
    ) != mainline["campaign_id"]:
        raise ExecutionAttestationError("execution_authority_identity_mismatch")
    policy = _validated_policy_identity(policy_identity)
    if policy["policy_version"] != mainline["policy_version"]:
        raise ExecutionAttestationError("execution_policy_version_mismatch")
    governance = dict(_dict(fixture_governance))
    if (
        governance.get("cleanup_status") != "SUCCEEDED"
        or governance.get("dirty_environment") is not False
        or not _text(governance.get("prepare_receipt_fingerprint"))
        or not _text(governance.get("cleanup_receipt_fingerprint"))
    ):
        raise ExecutionAttestationError("fixture_governance_not_clean")
    boundary = _validated_boundary(process_boundary)
    observations = _validated_observations(
        trusted_observations,
        ledger=ledger,
        additional_request_attempts=additional_request_attempts,
    )
    return mainline, ledger, policy, governance, boundary, observations


def build_execution_attestation(
    *,
    mainline_run: dict[str, Any],
    obligation_attempt_ledger: dict[str, Any],
    policy_identity: dict[str, Any],
    fixture_governance: dict[str, Any],
    process_boundary: dict[str, Any],
    trusted_observations: list[dict[str, Any]],
    additional_request_attempts: list[dict[str, Any]] | None = None,
    signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Seal independently observed I/O after scan and cleanup complete."""

    mainline, ledger, policy, governance, boundary, observations = (
        _validated_inputs(
            mainline_run=mainline_run,
            obligation_attempt_ledger=obligation_attempt_ledger,
            policy_identity=policy_identity,
            fixture_governance=fixture_governance,
            process_boundary=process_boundary,
            trusted_observations=trusted_observations,
            additional_request_attempts=additional_request_attempts,
        )
    )
    payload = {
        "schema_version": EXECUTION_ATTESTATION_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "VERIFIED",
        "trust_boundary": "evaluator_owned_io_gateway",
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "target_id": mainline["target_id"],
        "environment_id": mainline["environment_id"],
        "evaluation_mode": mainline["evaluation_mode"],
        "policy_identity": policy,
        "mainline_contract_fingerprint": mainline["contract_fingerprint"],
        "attempt_ledger_fingerprint": ledger["ledger_fingerprint"],
        "fixture_governance_fingerprint": _fingerprint(governance),
        "process_boundary_fingerprint": _fingerprint(boundary),
        "observed_attempt_count": len(observations),
        "target_request_count": sum(
            row["target_request_count"] for row in observations
        ),
        "write_count": sum(row["write_count"] for row in observations),
        "observations": observations,
    }
    try:
        return seal_evaluator_artifact(
            payload,
            signing_key=signing_key,
            domain=EXECUTION_ATTESTATION_SCHEMA,
            fingerprint_field=ATTESTATION_FINGERPRINT_FIELD,
            authentication_field=ATTESTATION_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise ExecutionAttestationError(
            f"execution_attestation_authentication_failed:{exc}"
        ) from exc


def validate_execution_attestation(
    attestation: dict[str, Any],
    *,
    mainline_run: dict[str, Any],
    obligation_attempt_ledger: dict[str, Any],
    policy_identity: dict[str, Any],
    fixture_governance: dict[str, Any],
    process_boundary: dict[str, Any],
    additional_request_attempts: list[dict[str, Any]] | None = None,
    signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Verify HMAC, trusted-source coverage, and every bound authority."""

    try:
        verified = verify_evaluator_artifact(
            attestation,
            signing_key=signing_key,
            domain=EXECUTION_ATTESTATION_SCHEMA,
            fingerprint_field=ATTESTATION_FINGERPRINT_FIELD,
            authentication_field=ATTESTATION_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise ExecutionAttestationError(
            f"execution_attestation_authentication_invalid:{exc}"
        ) from exc
    mainline, ledger, policy, governance, boundary, observations = (
        _validated_inputs(
            mainline_run=mainline_run,
            obligation_attempt_ledger=obligation_attempt_ledger,
            policy_identity=policy_identity,
            fixture_governance=fixture_governance,
            process_boundary=process_boundary,
            trusted_observations=[
                dict(row)
                for row in _list(verified.get("observations"))
                if isinstance(row, dict)
            ],
            additional_request_attempts=additional_request_attempts,
        )
    )
    expected = {
        "schema_version": EXECUTION_ATTESTATION_SCHEMA,
        "status": "VERIFIED",
        "trust_boundary": "evaluator_owned_io_gateway",
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "target_id": mainline["target_id"],
        "environment_id": mainline["environment_id"],
        "evaluation_mode": mainline["evaluation_mode"],
        "policy_identity": policy,
        "mainline_contract_fingerprint": mainline["contract_fingerprint"],
        "attempt_ledger_fingerprint": ledger["ledger_fingerprint"],
        "fixture_governance_fingerprint": _fingerprint(governance),
        "process_boundary_fingerprint": _fingerprint(boundary),
        "observed_attempt_count": len(observations),
        "target_request_count": sum(
            row["target_request_count"] for row in observations
        ),
        "write_count": sum(row["write_count"] for row in observations),
        "observations": observations,
    }
    for field, value in expected.items():
        if verified.get(field) != value:
            raise ExecutionAttestationError(
                f"execution_attestation_{field}_mismatch"
            )
    if not _text(verified.get("created_at_utc")):
        raise ExecutionAttestationError("execution_attestation_created_at_missing")
    return dict(verified)
