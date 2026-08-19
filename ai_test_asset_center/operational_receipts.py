"""Receipt-backed operational accounting for discovery execution attempts.

Only explicit HTTP-step and governed-write receipts are counted. Canonical
Receipt Envelope, Execution Receipt Bundle, lifecycle reduction, and
Finalization Receipt derivation live on this existing authority spine.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .process_step_bundle_audit import audit_process_step_receipt_bundle


EXECUTION_OPERATIONAL_RECEIPT_SCHEMA = "qualibug.execution-operational-receipt.v1"
EXECUTION_OPERATIONAL_SUMMARY_SCHEMA = "qualibug.execution-operational-summary.v1"
CANONICAL_RECEIPT_ENVELOPE_SCHEMA = "qualibug.canonical-receipt-envelope.v1"
EXECUTION_RECEIPT_BUNDLE_SCHEMA = "qualibug.execution-receipt-bundle.v1"
EXECUTION_FINALIZATION_RECEIPT_SCHEMA = "qualibug.execution-finalization-receipt.v1"
EXECUTION_LIFECYCLE_DERIVATION_SCHEMA = "qualibug.execution-lifecycle-derivation.v1"
FIXTURE_PROVENANCE_RECEIPT_SCHEMA = "qualibug.fixture-provenance-receipt.v1"
REPORT_METRIC_RECEIPT_SCHEMA = "qualibug.report-metric-receipt.v1"
CORRECTION_RECEIPT_SCHEMA = "qualibug.correction-receipt.v1"

NOT_APPLICABLE = "NOT_APPLICABLE"
RECEIPT_STATUS_VALID = "VALID"
RECEIPT_STATUS_INVALID = "INVALID"
RECEIPT_STATUS_INCOMPLETE = "INCOMPLETE"
RECEIPT_STATUS_TAMPERED = "TAMPERED"
_RECEIPT_STATUSES = frozenset(
    {
        RECEIPT_STATUS_VALID,
        RECEIPT_STATUS_INVALID,
        RECEIPT_STATUS_INCOMPLETE,
        RECEIPT_STATUS_TAMPERED,
    }
)

_IDENTITY_FIELDS = (
    "campaign_id",
    "run_id",
    "obligation_id",
    "experiment_id",
    "fixture_id",
    "protocol_id",
)

REQUIRED_FORMAL_RECEIPT_TYPES = (
    "qualibug.compile-receipt.v1",
    "qualibug.fixture-contract-receipt.v1",
    "qualibug.fixture-materialization-receipt.v1",
    "qualibug.fixture-identity-receipt.v1",
    "qualibug.fixture-scope-receipt.v1",
    "qualibug.process-step-receipt.v1",
    "qualibug.transport-receipt.v1",
    "qualibug.observation-receipt.v1",
    "qualibug.oracle-invocation-receipt.v1",
    "qualibug.oracle-trace-receipt.v1",
    "qualibug.cleanup-execution-receipt.v1",
    "qualibug.cleanup-verification-receipt.v1",
    "qualibug.environment-restoration-receipt.v1",
    "qualibug.execution-finalization-receipt.v1",
    "qualibug.finding-delivery-gate-receipt.v1",
    "qualibug.report-metric-receipt.v1",
)

_TRUE_COMPLETED_REQUIRED_GROUPS = (
    "compile_receipt_id",
    "fixture_provenance_receipt_ids",
    "required_step_receipt_ids",
    "transport_receipt_ids",
    "observation_receipt_ids",
    "oracle_invocation_receipt_ids",
    "cleanup_execution_receipt_ids",
    "cleanup_verification_receipt_ids",
    "environment_restoration_receipt_id",
)

DERIVATION_VERSION = "v1.6.2-receipt-bundle-finalizer"
LIFECYCLE_DERIVATION_VERSION = "v1.8.0-ledger-balanced-reducer"

LIFECYCLE_PLANNED = "PLANNED"
LIFECYCLE_COMPILED = "COMPILED"
LIFECYCLE_FIXTURE_MATERIALIZED = "FIXTURE_MATERIALIZED"
LIFECYCLE_PRECONDITION_ESTABLISHED = "PRECONDITION_ESTABLISHED"
LIFECYCLE_BUSINESS_STEPS_EXECUTED = "BUSINESS_STEPS_EXECUTED"
LIFECYCLE_OBSERVATION_COMPLETED = "OBSERVATION_COMPLETED"
LIFECYCLE_ORACLE_EVALUATED = "ORACLE_EVALUATED"
LIFECYCLE_CLEANUP_EXECUTED = "CLEANUP_EXECUTED"
LIFECYCLE_CLEANUP_VERIFIED = "CLEANUP_VERIFIED"
LIFECYCLE_ENVIRONMENT_RESTORED = "ENVIRONMENT_RESTORED"
LIFECYCLE_TRUE_COMPLETED = "TRUE_COMPLETED"

LIFECYCLE_COMPILE_BLOCKED = "COMPILE_BLOCKED"
LIFECYCLE_FIXTURE_BLOCKED = "FIXTURE_BLOCKED"
LIFECYCLE_PRECONDITION_UNREACHABLE = "PRECONDITION_UNREACHABLE"
LIFECYCLE_PARTIAL_EXECUTION = "PARTIAL_EXECUTION"
LIFECYCLE_HARNESS_FAILED = "HARNESS_FAILED"
LIFECYCLE_ORACLE_INDETERMINATE = "ORACLE_INDETERMINATE"
LIFECYCLE_CLEANUP_FAILED = "CLEANUP_FAILED"
LIFECYCLE_ENVIRONMENT_DIRTY = "ENVIRONMENT_DIRTY"
LIFECYCLE_RECEIPT_INCOMPLETE = "RECEIPT_INCOMPLETE"
LIFECYCLE_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
LIFECYCLE_PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"

TERMINAL_LIFECYCLE_STATES = frozenset(
    {
        LIFECYCLE_TRUE_COMPLETED,
        LIFECYCLE_COMPILE_BLOCKED,
        LIFECYCLE_FIXTURE_BLOCKED,
        LIFECYCLE_PRECONDITION_UNREACHABLE,
        LIFECYCLE_PARTIAL_EXECUTION,
        LIFECYCLE_HARNESS_FAILED,
        LIFECYCLE_ORACLE_INDETERMINATE,
        LIFECYCLE_CLEANUP_FAILED,
        LIFECYCLE_ENVIRONMENT_DIRTY,
        LIFECYCLE_RECEIPT_INCOMPLETE,
        LIFECYCLE_IDENTITY_MISMATCH,
        LIFECYCLE_PROTOCOL_MISMATCH,
    }
)

_EXECUTION_STATUSES = frozenset(
    {
        "EXECUTED",
        "BLOCKED",
        "DEFERRED",
        "HARNESS_FAILURE",
        "HARNESS_FAILED",
        "DELIVERABLE",
        "EXECUTED_BUT_NOT_RESTORED",
    }
)
_CLEANUP_PHASES = frozenset({"cleanup", "fixture_cleanup"})
_CLEANUP_STATUSES = frozenset({"COMPLETED", "FAILED", "NOT_REQUIRED"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OperationalReceiptError(ValueError):
    """An execution receipt is missing or internally contradictory."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise OperationalReceiptError(f"{field}_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OperationalReceiptError(f"{field}_invalid") from exc
    if parsed < 0:
        raise OperationalReceiptError(f"{field}_invalid")
    return parsed


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    return _fingerprint(value)


def _normalize_id_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _list(value):
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out


def _require_identity_value(value: Any, field: str) -> str:
    text = _text(value)
    if not text:
        raise OperationalReceiptError(f"{field}_missing_use_NOT_APPLICABLE")
    return text


# ---------------------------------------------------------------------------
# Explicit operational accounting
# ---------------------------------------------------------------------------


def validate_execution_operational_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    value = dict(_dict(receipt))
    if value.get("schema_version") != EXECUTION_OPERATIONAL_RECEIPT_SCHEMA:
        raise OperationalReceiptError("operational_receipt_schema_invalid")
    if not _text(value.get("receipt_id")):
        raise OperationalReceiptError("operational_receipt_id_missing")
    execution_status = _text(value.get("execution_status")).upper()
    if execution_status not in _EXECUTION_STATUSES:
        raise OperationalReceiptError("operational_execution_status_invalid")
    value["execution_status"] = execution_status

    for field in (
        "scenario_attempt_count",
        "http_request_attempt_count",
        "write_request_attempt_count",
        "production_http_request_count",
        "accepted_write_count",
        "accepted_non_cleanup_write_count",
        "accepted_cleanup_write_count",
    ):
        value[field] = _non_negative_int(value.get(field), field)

    if value["scenario_attempt_count"] != 1:
        raise OperationalReceiptError("scenario_attempt_count_must_equal_one")
    if value["production_http_request_count"] > value["http_request_attempt_count"]:
        raise OperationalReceiptError("production_http_request_count_exceeds_attempts")
    if value["write_request_attempt_count"] > value["http_request_attempt_count"]:
        raise OperationalReceiptError("write_request_attempt_count_exceeds_attempts")
    if value["accepted_write_count"] > value["write_request_attempt_count"]:
        raise OperationalReceiptError("accepted_write_count_exceeds_write_attempts")
    if (
        value["accepted_non_cleanup_write_count"]
        + value["accepted_cleanup_write_count"]
        != value["accepted_write_count"]
    ):
        raise OperationalReceiptError("accepted_write_count_mismatch")

    cleanup = dict(_dict(value.get("cleanup_outcome")))
    cleanup_status = _text(cleanup.get("status")).upper()
    if cleanup_status not in _CLEANUP_STATUSES:
        raise OperationalReceiptError("cleanup_outcome_status_invalid")
    cleanup["status"] = cleanup_status
    for field in ("attempted_count", "completed_count", "failure_count"):
        cleanup[field] = _non_negative_int(cleanup.get(field), f"cleanup_{field}")
    if cleanup["completed_count"] > cleanup["attempted_count"]:
        raise OperationalReceiptError("cleanup_completed_count_exceeds_attempts")
    if cleanup_status == "FAILED" and cleanup["failure_count"] == 0:
        raise OperationalReceiptError("cleanup_failed_without_failure_count")
    if cleanup_status != "FAILED" and cleanup["failure_count"]:
        raise OperationalReceiptError("cleanup_failure_count_status_mismatch")
    value["cleanup_outcome"] = cleanup

    claimed = _text(value.get("receipt_fingerprint"))
    if not claimed:
        raise OperationalReceiptError("operational_receipt_fingerprint_missing")
    unsigned = {key: item for key, item in value.items() if key != "receipt_fingerprint"}
    if claimed != _fingerprint(unsigned):
        raise OperationalReceiptError("operational_receipt_fingerprint_mismatch")
    return value


def build_execution_operational_receipt(
    *,
    receipt_id: str,
    execution_status: str,
    steps: list[dict[str, Any]],
    cleanup_failures: int,
) -> dict[str, Any]:
    normalized_status = _text(execution_status).upper()
    if normalized_status not in _EXECUTION_STATUSES:
        raise OperationalReceiptError("operational_execution_status_invalid")

    http_attempts = 0
    write_request_attempts = 0
    production_requests = 0
    accepted_non_cleanup = 0
    accepted_cleanup = 0
    cleanup_attempted = 0
    cleanup_completed = 0

    for raw_step in _list(steps):
        step = _dict(raw_step)
        governance = _dict(step.get("governance_receipt"))
        phase = _text(step.get("phase")).lower()
        if governance:
            attempts = _non_negative_int(
                governance.get("http_attempt_count"),
                "governance_http_attempt_count",
            )
            production = _non_negative_int(
                governance.get("production_http_requests"),
                "governance_production_http_requests",
            )
            write_attempts = _non_negative_int(
                governance.get("write_request_attempt_count"),
                "governance_write_request_attempt_count",
            )
            if production > attempts:
                raise OperationalReceiptError(
                    "governance_production_http_requests_exceed_attempts"
                )
            if write_attempts > attempts:
                raise OperationalReceiptError(
                    "governance_write_request_attempts_exceed_http_attempts"
                )
            http_attempts += attempts
            write_request_attempts += write_attempts
            production_requests += production
            accepted = governance.get("accepted") is True
            method = _text(governance.get("method") or step.get("method")).upper()
            is_adapter_cleanup = method.startswith("ADAPTER_")
            if phase in _CLEANUP_PHASES:
                if attempts or is_adapter_cleanup:
                    cleanup_attempted += 1
                if accepted:
                    cleanup_completed += 1
                    if not is_adapter_cleanup:
                        accepted_cleanup += 1
            elif accepted:
                # A body-field-probe reissue (undocumented policy endpoint) is a
                # read-only decision-endpoint observation, not a durable business
                # write: it never creates state that a compensator must restore,
                # so it must not count as an accepted write demanding cleanup
                # coverage at the delivery gate.
                if not _dict(governance.get("_undocumented_field_probe")):
                    accepted_non_cleanup += 1
            continue

        status_code = int(step.get("status_code") or 0)
        if _text(step.get("method")) and _text(step.get("path")) and status_code > 0:
            http_attempts += 1
            if _text(step.get("method")).upper() in _WRITE_METHODS:
                write_request_attempts += 1

    failure_count = _non_negative_int(cleanup_failures, "cleanup_failures")
    # COMPLETED requires at least one accepted cleanup write. Counting mere
    # attempts (including adapter probes that never accepted) previously sealed
    # COMPLETED with completed_count=0 while contract evidence was NOT_REQUIRED
    # (ACCEPTED_WRITE_STATE_UNCHANGED) — 4× CLEANUP_EVIDENCE_INCOMPLETE on T140342Z.
    if failure_count:
        cleanup_status = "FAILED"
    elif cleanup_completed:
        cleanup_status = "COMPLETED"
    else:
        cleanup_status = "NOT_REQUIRED"

    return build_execution_operational_receipt_from_counts(
        receipt_id=receipt_id,
        execution_status=normalized_status,
        scenario_attempt_count=1,
        http_request_attempt_count=http_attempts,
        write_request_attempt_count=write_request_attempts,
        production_http_request_count=production_requests,
        accepted_non_cleanup_write_count=accepted_non_cleanup,
        accepted_cleanup_write_count=accepted_cleanup,
        cleanup_status=cleanup_status,
        cleanup_attempted_count=cleanup_attempted,
        cleanup_completed_count=cleanup_completed,
        cleanup_failure_count=failure_count,
    )


def build_execution_operational_receipt_from_counts(
    *,
    receipt_id: str,
    execution_status: str,
    scenario_attempt_count: int,
    http_request_attempt_count: int,
    write_request_attempt_count: int,
    production_http_request_count: int,
    accepted_non_cleanup_write_count: int,
    accepted_cleanup_write_count: int,
    cleanup_status: str,
    cleanup_attempted_count: int,
    cleanup_completed_count: int,
    cleanup_failure_count: int,
) -> dict[str, Any]:
    accepted_non_cleanup = _non_negative_int(
        accepted_non_cleanup_write_count,
        "accepted_non_cleanup_write_count",
    )
    accepted_cleanup = _non_negative_int(
        accepted_cleanup_write_count,
        "accepted_cleanup_write_count",
    )
    payload: dict[str, Any] = {
        "schema_version": EXECUTION_OPERATIONAL_RECEIPT_SCHEMA,
        "receipt_id": _text(receipt_id),
        "execution_status": _text(execution_status).upper(),
        "scenario_attempt_count": _non_negative_int(
            scenario_attempt_count, "scenario_attempt_count"
        ),
        "http_request_attempt_count": _non_negative_int(
            http_request_attempt_count, "http_request_attempt_count"
        ),
        "write_request_attempt_count": _non_negative_int(
            write_request_attempt_count, "write_request_attempt_count"
        ),
        "production_http_request_count": _non_negative_int(
            production_http_request_count, "production_http_request_count"
        ),
        "accepted_write_count": accepted_non_cleanup + accepted_cleanup,
        "accepted_non_cleanup_write_count": accepted_non_cleanup,
        "accepted_cleanup_write_count": accepted_cleanup,
        "cleanup_outcome": {
            "status": _text(cleanup_status).upper(),
            "attempted_count": _non_negative_int(
                cleanup_attempted_count, "cleanup_attempted_count"
            ),
            "completed_count": _non_negative_int(
                cleanup_completed_count, "cleanup_completed_count"
            ),
            "failure_count": _non_negative_int(
                cleanup_failure_count, "cleanup_failure_count"
            ),
        },
    }
    if not payload["receipt_id"]:
        raise OperationalReceiptError("operational_receipt_id_missing")
    payload["receipt_fingerprint"] = _fingerprint(payload)
    return validate_execution_operational_receipt(payload)


def aggregate_execution_operational_receipts(
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [validate_execution_operational_receipt(row) for row in _list(receipts)]
    receipt_ids = [_text(row.get("receipt_id")) for row in rows]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise OperationalReceiptError("operational_receipt_id_duplicate")
    scenario_attempts = sum(row["scenario_attempt_count"] for row in rows)
    executed = sum(
        row["scenario_attempt_count"]
        for row in rows
        if row["execution_status"] == "EXECUTED"
    )
    cleanup_failures = sum(
        _dict(row.get("cleanup_outcome")).get("failure_count", 0) for row in rows
    )
    return {
        "schema_version": EXECUTION_OPERATIONAL_SUMMARY_SCHEMA,
        "receipt_count": len(rows),
        "receipt_ids": receipt_ids,
        "scenario_attempts": scenario_attempts,
        "executed_scenarios": executed,
        "observed_http_request_count": sum(
            row["http_request_attempt_count"] for row in rows
        ),
        "write_request_attempt_count": sum(
            row["write_request_attempt_count"] for row in rows
        ),
        "production_http_requests": sum(
            row["production_http_request_count"] for row in rows
        ),
        "accepted_write_count": sum(row["accepted_write_count"] for row in rows),
        "accepted_non_cleanup_write_count": sum(
            row["accepted_non_cleanup_write_count"] for row in rows
        ),
        "accepted_cleanup_write_count": sum(
            row["accepted_cleanup_write_count"] for row in rows
        ),
        "cleanup_failures": cleanup_failures,
        "execution_success_rate": (
            round(executed / scenario_attempts, 6) if scenario_attempts else 0.0
        ),
    }


# ---------------------------------------------------------------------------
# Canonical receipt envelope and bundle
# ---------------------------------------------------------------------------


def build_canonical_receipt_envelope(
    *,
    receipt_type: str,
    receipt_id: str,
    payload: dict[str, Any],
    campaign_id: str = NOT_APPLICABLE,
    run_id: str = NOT_APPLICABLE,
    obligation_id: str = NOT_APPLICABLE,
    experiment_id: str = NOT_APPLICABLE,
    fixture_id: str = NOT_APPLICABLE,
    protocol_id: str = NOT_APPLICABLE,
    producer_module: str = "",
    producer_version: str = "",
    code_commit_sha: str = NOT_APPLICABLE,
    tree_hash: str = NOT_APPLICABLE,
    parent_receipt_ids: list[str] | None = None,
    source_contract_ids: list[str] | None = None,
    status: str = RECEIPT_STATUS_VALID,
    produced_at: str | None = None,
) -> dict[str, Any]:
    if not _text(receipt_type):
        raise OperationalReceiptError("receipt_type_missing")
    if not _text(receipt_id):
        raise OperationalReceiptError("receipt_id_missing")
    if not isinstance(payload, dict):
        raise OperationalReceiptError("receipt_payload_invalid")
    normalized_status = _text(status).upper() or RECEIPT_STATUS_VALID
    if normalized_status not in _RECEIPT_STATUSES:
        raise OperationalReceiptError("receipt_status_invalid")

    identity = {
        "campaign_id": _require_identity_value(campaign_id, "campaign_id"),
        "run_id": _require_identity_value(run_id, "run_id"),
        "obligation_id": _require_identity_value(obligation_id, "obligation_id"),
        "experiment_id": _require_identity_value(experiment_id, "experiment_id"),
        "fixture_id": _require_identity_value(fixture_id, "fixture_id"),
        "protocol_id": _require_identity_value(protocol_id, "protocol_id"),
    }
    envelope: dict[str, Any] = {
        "schema_version": CANONICAL_RECEIPT_ENVELOPE_SCHEMA,
        "receipt_type": _text(receipt_type),
        "receipt_id": _text(receipt_id),
        **identity,
        "producer_module": _text(producer_module)
        or "ai_test_asset_center.operational_receipts",
        "producer_version": _text(producer_version) or DERIVATION_VERSION,
        "code_commit_sha": _require_identity_value(
            code_commit_sha, "code_commit_sha"
        ),
        "tree_hash": _require_identity_value(tree_hash, "tree_hash"),
        "parent_receipt_ids": _normalize_id_list(parent_receipt_ids),
        "source_contract_ids": _normalize_id_list(source_contract_ids),
        "payload": dict(payload),
        "payload_hash": _stable_hash(payload),
        "produced_at": _text(produced_at)
        or datetime.now(timezone.utc).isoformat(),
        "status": normalized_status,
    }
    envelope["receipt_hash"] = _stable_hash(
        {key: value for key, value in envelope.items() if key != "receipt_hash"}
    )
    return validate_canonical_receipt_envelope(envelope)


def validate_canonical_receipt_envelope(
    receipt: dict[str, Any],
    *,
    expected_code_commit_sha: str | None = None,
    expected_tree_hash: str | None = None,
) -> dict[str, Any]:
    value = dict(_dict(receipt))
    if value.get("schema_version") != CANONICAL_RECEIPT_ENVELOPE_SCHEMA:
        raise OperationalReceiptError("canonical_receipt_schema_invalid")
    if not _text(value.get("receipt_type")):
        raise OperationalReceiptError("receipt_type_missing")
    if not _text(value.get("receipt_id")):
        raise OperationalReceiptError("receipt_id_missing")
    if not isinstance(value.get("payload"), dict):
        raise OperationalReceiptError("receipt_payload_invalid")
    for field in _IDENTITY_FIELDS:
        if not _text(value.get(field)):
            raise OperationalReceiptError(f"{field}_missing_use_NOT_APPLICABLE")
    for field in ("code_commit_sha", "tree_hash"):
        if not _text(value.get(field)):
            raise OperationalReceiptError(f"{field}_missing_use_NOT_APPLICABLE")

    status = _text(value.get("status")).upper()
    if status not in _RECEIPT_STATUSES:
        raise OperationalReceiptError("receipt_status_invalid")
    value["status"] = status
    value["parent_receipt_ids"] = _normalize_id_list(value.get("parent_receipt_ids"))
    value["source_contract_ids"] = _normalize_id_list(value.get("source_contract_ids"))

    claimed_payload_hash = _text(value.get("payload_hash"))
    if not claimed_payload_hash:
        raise OperationalReceiptError("payload_hash_missing")
    if claimed_payload_hash != _stable_hash(value.get("payload")):
        raise OperationalReceiptError("payload_hash_mismatch")

    claimed_receipt_hash = _text(value.get("receipt_hash"))
    if not claimed_receipt_hash:
        raise OperationalReceiptError("receipt_hash_missing")
    unsigned = {key: item for key, item in value.items() if key != "receipt_hash"}
    if claimed_receipt_hash != _stable_hash(unsigned):
        raise OperationalReceiptError("receipt_hash_mismatch")

    if expected_code_commit_sha is not None:
        expected = _text(expected_code_commit_sha) or NOT_APPLICABLE
        if _text(value.get("code_commit_sha")) != expected:
            raise OperationalReceiptError("code_commit_sha_mismatch")
    if expected_tree_hash is not None:
        expected = _text(expected_tree_hash) or NOT_APPLICABLE
        if _text(value.get("tree_hash")) != expected:
            raise OperationalReceiptError("tree_hash_mismatch")
    return value


def validate_parent_receipt_chain(
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [validate_canonical_receipt_envelope(row) for row in _list(receipts)]
    by_id = {_text(row.get("receipt_id")): row for row in rows}
    if len(by_id) != len(rows):
        raise OperationalReceiptError("receipt_id_duplicate_in_chain")
    broken: list[dict[str, str]] = []
    for row in rows:
        for parent_id in _list(row.get("parent_receipt_ids")):
            pid = _text(parent_id)
            if not pid or pid == NOT_APPLICABLE:
                continue
            if pid not in by_id:
                broken.append(
                    {
                        "receipt_id": _text(row.get("receipt_id")),
                        "missing_parent_receipt_id": pid,
                    }
                )
    if broken:
        raise OperationalReceiptError(
            f"parent_chain_broken:{json.dumps(broken, sort_keys=True)}"
        )
    return {
        "schema_version": "qualibug.receipt-parent-chain-audit.v1",
        "receipt_count": len(rows),
        "intact": True,
        "broken_links": [],
    }


def detect_receipt_tamper(receipt: dict[str, Any]) -> dict[str, Any]:
    value = dict(_dict(receipt))
    try:
        validate_canonical_receipt_envelope(value)
        return {
            "receipt_id": _text(value.get("receipt_id")),
            "tampered": False,
            "status": RECEIPT_STATUS_VALID,
            "reason_code": "",
        }
    except OperationalReceiptError as exc:
        reason = str(exc)
        tampered = reason in {"payload_hash_mismatch", "receipt_hash_mismatch"}
        return {
            "receipt_id": _text(value.get("receipt_id")),
            "tampered": tampered or "hash" in reason,
            "status": (
                RECEIPT_STATUS_TAMPERED if tampered else RECEIPT_STATUS_INVALID
            ),
            "reason_code": reason,
        }


def build_correction_receipt(
    *,
    correction_receipt_id: str,
    supersedes_receipt_id: str,
    original_receipt: dict[str, Any],
    corrected_payload: dict[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    original = validate_canonical_receipt_envelope(original_receipt)
    if _text(original.get("receipt_id")) != _text(supersedes_receipt_id):
        raise OperationalReceiptError("correction_supersedes_id_mismatch")
    return build_canonical_receipt_envelope(
        receipt_type=CORRECTION_RECEIPT_SCHEMA,
        receipt_id=correction_receipt_id,
        payload={
            "schema_version": CORRECTION_RECEIPT_SCHEMA,
            "supersedes_receipt_id": _text(supersedes_receipt_id),
            "reason_code": _text(reason_code) or "CORRECTION",
            "corrected_payload": dict(_dict(corrected_payload)),
            "original_receipt_hash": _text(original.get("receipt_hash")),
            "original_retained": True,
        },
        campaign_id=_text(original.get("campaign_id")),
        run_id=_text(original.get("run_id")),
        obligation_id=_text(original.get("obligation_id")),
        experiment_id=_text(original.get("experiment_id")),
        fixture_id=_text(original.get("fixture_id")),
        protocol_id=_text(original.get("protocol_id")),
        producer_module=_text(original.get("producer_module")),
        producer_version=_text(original.get("producer_version")),
        code_commit_sha=_text(original.get("code_commit_sha")),
        tree_hash=_text(original.get("tree_hash")),
        parent_receipt_ids=[_text(supersedes_receipt_id)],
        source_contract_ids=_list(original.get("source_contract_ids")),
        status=RECEIPT_STATUS_VALID,
    )


def _identity_tuple(receipt: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_text(receipt.get(field)) for field in _IDENTITY_FIELDS)


def build_execution_receipt_bundle(
    *,
    bundle_id: str,
    campaign_id: str,
    run_id: str,
    obligation_id: str,
    experiment_id: str,
    fixture_id: str = NOT_APPLICABLE,
    protocol_id: str = NOT_APPLICABLE,
    receipts: list[dict[str, Any]],
    compile_receipt_id: str = "",
    fixture_provenance_receipt_ids: list[str] | None = None,
    required_step_receipt_ids: list[str] | None = None,
    transport_receipt_ids: list[str] | None = None,
    observation_receipt_ids: list[str] | None = None,
    oracle_invocation_receipt_ids: list[str] | None = None,
    oracle_trace_receipt_ids: list[str] | None = None,
    cleanup_execution_receipt_ids: list[str] | None = None,
    cleanup_verification_receipt_ids: list[str] | None = None,
    environment_restoration_receipt_id: str = "",
) -> dict[str, Any]:
    if not _text(bundle_id):
        raise OperationalReceiptError("bundle_id_missing")
    validated = [validate_canonical_receipt_envelope(row) for row in _list(receipts)]
    by_id = {_text(row["receipt_id"]): row for row in validated}
    if len(by_id) != len(validated):
        raise OperationalReceiptError("bundle_receipt_id_duplicate")

    groups = {
        "compile_receipt_id": _text(compile_receipt_id),
        "fixture_provenance_receipt_ids": _normalize_id_list(
            fixture_provenance_receipt_ids
        ),
        "required_step_receipt_ids": _normalize_id_list(required_step_receipt_ids),
        "transport_receipt_ids": _normalize_id_list(transport_receipt_ids),
        "observation_receipt_ids": _normalize_id_list(observation_receipt_ids),
        "oracle_invocation_receipt_ids": _normalize_id_list(
            oracle_invocation_receipt_ids
        ),
        "oracle_trace_receipt_ids": _normalize_id_list(oracle_trace_receipt_ids),
        "cleanup_execution_receipt_ids": _normalize_id_list(
            cleanup_execution_receipt_ids
        ),
        "cleanup_verification_receipt_ids": _normalize_id_list(
            cleanup_verification_receipt_ids
        ),
        "environment_restoration_receipt_id": _text(
            environment_restoration_receipt_id
        ),
    }

    required_ids: list[str] = []
    for key in _TRUE_COMPLETED_REQUIRED_GROUPS:
        value = groups[key]
        if isinstance(value, list):
            required_ids.extend(value)
        elif value:
            required_ids.append(value)
    required_ids = _normalize_id_list(required_ids)
    actual_ids = sorted(by_id.keys())
    required_set_hash = _stable_hash(sorted(required_ids))
    actual_set_hash = _stable_hash(actual_ids)

    validation_errors: list[str] = []
    missing_receipt_ids = [rid for rid in required_ids if rid not in by_id]
    if missing_receipt_ids:
        validation_errors.append("missing_required_receipts")

    expected_identity = (
        _require_identity_value(campaign_id, "campaign_id"),
        _require_identity_value(run_id, "run_id"),
        _require_identity_value(obligation_id, "obligation_id"),
        _require_identity_value(experiment_id, "experiment_id"),
        _require_identity_value(fixture_id, "fixture_id"),
        _require_identity_value(protocol_id, "protocol_id"),
    )
    identity_mismatch_receipt_ids: list[str] = []
    protocol_mismatch_receipt_ids: list[str] = []
    invalid_receipt_ids: list[str] = []

    for rid, row in by_id.items():
        if row.get("status") != RECEIPT_STATUS_VALID:
            invalid_receipt_ids.append(rid)
        row_identity = _identity_tuple(row)
        for idx, field in enumerate(_IDENTITY_FIELDS):
            expected = expected_identity[idx]
            actual = row_identity[idx]
            if expected == NOT_APPLICABLE or actual == NOT_APPLICABLE:
                continue
            if expected != actual:
                identity_mismatch_receipt_ids.append(rid)
                if field == "protocol_id":
                    protocol_mismatch_receipt_ids.append(rid)
                break

    identity_consistent = not identity_mismatch_receipt_ids
    protocol_consistent = not protocol_mismatch_receipt_ids
    if not identity_consistent:
        validation_errors.append("identity_mismatch")
    if not protocol_consistent:
        validation_errors.append("protocol_mismatch")
    if invalid_receipt_ids:
        validation_errors.append("invalid_receipts")

    process_step_audit = audit_process_step_receipt_bundle(
        receipts_by_id=by_id,
        process_step_receipt_ids=groups["required_step_receipt_ids"],
    )
    for error in _list(process_step_audit.get("validation_errors")):
        code = _text(error)
        if code and code not in validation_errors:
            validation_errors.append(code)

    complete = (
        bool(required_ids)
        and not missing_receipt_ids
        and identity_consistent
        and protocol_consistent
        and not invalid_receipt_ids
        and process_step_audit.get("complete") is True
    )
    bundle = {
        "schema_version": EXECUTION_RECEIPT_BUNDLE_SCHEMA,
        "bundle_id": _text(bundle_id),
        "campaign_id": expected_identity[0],
        "run_id": expected_identity[1],
        "obligation_id": expected_identity[2],
        "experiment_id": expected_identity[3],
        "fixture_id": expected_identity[4],
        "protocol_id": expected_identity[5],
        **groups,
        "required_receipt_set_hash": required_set_hash,
        "actual_receipt_set_hash": actual_set_hash,
        "identity_consistent": identity_consistent,
        "protocol_consistent": protocol_consistent,
        "process_step_audit": process_step_audit,
        "process_step_ledger_identity_consistent": bool(
            process_step_audit.get("ledger_identity_consistent")
        ),
        "process_step_ledger_hash_consistent": bool(
            process_step_audit.get("ledger_hash_consistent")
        ),
        "process_step_fact_hashes_valid": bool(
            process_step_audit.get("step_fact_hashes_valid")
        ),
        "process_step_sets_balanced": bool(
            process_step_audit.get("step_sets_balanced")
        ),
        "complete": complete,
        "validation_errors": validation_errors,
        "missing_receipt_ids": missing_receipt_ids,
        "invalid_receipt_ids": sorted(set(invalid_receipt_ids)),
        "identity_mismatch_receipt_ids": sorted(
            set(identity_mismatch_receipt_ids)
        ),
        "protocol_mismatch_receipt_ids": sorted(
            set(protocol_mismatch_receipt_ids)
        ),
        "process_step_ledger_identity_mismatch_receipt_ids": _normalize_id_list(
            process_step_audit.get("ledger_identity_mismatch_receipt_ids")
        ),
        "process_step_ledger_hash_mismatch_receipt_ids": _normalize_id_list(
            process_step_audit.get("ledger_hash_mismatch_receipt_ids")
        ),
        "process_step_fact_hash_mismatch_receipt_ids": _normalize_id_list(
            process_step_audit.get("step_fact_hash_mismatch_receipt_ids")
        ),
        "process_step_declaration_mismatch_receipt_ids": _normalize_id_list(
            process_step_audit.get("declaration_mismatch_receipt_ids")
        ),
        "process_step_set_mismatch_fields": _normalize_id_list(
            process_step_audit.get("set_mismatch_fields")
        ),
        "process_step_invariant_errors": _normalize_id_list(
            process_step_audit.get("invariant_errors")
        ),
        "receipts": validated,
    }
    bundle["bundle_hash"] = _stable_hash(
        {
            key: value
            for key, value in bundle.items()
            if key not in {"receipts", "bundle_hash"}
        }
    )
    return bundle


# ---------------------------------------------------------------------------
# Single lifecycle authority
# ---------------------------------------------------------------------------


def _bundle_facts(bundle: dict[str, Any] | None) -> dict[str, Any]:
    value = dict(_dict(bundle))
    if not value:
        return {
            "provided": False,
            "schema_valid": False,
            "structurally_complete": False,
            "missing_receipt_ids": [],
            "invalid_receipt_ids": [],
            "identity_mismatch_receipt_ids": [],
            "protocol_mismatch_receipt_ids": [],
            "process_step_audit_complete": False,
            "process_step_ledger_identity_mismatch_receipt_ids": [],
            "process_step_ledger_hash_mismatch_receipt_ids": [],
            "process_step_fact_hash_mismatch_receipt_ids": [],
            "process_step_set_mismatch_fields": [],
            "process_step_invariant_errors": [],
        }
    schema_valid = value.get("schema_version") == EXECUTION_RECEIPT_BUNDLE_SCHEMA
    audit = _dict(value.get("process_step_audit"))
    return {
        "provided": True,
        "schema_valid": schema_valid,
        "structurally_complete": bool(value.get("complete")) if schema_valid else False,
        "missing_receipt_ids": _normalize_id_list(value.get("missing_receipt_ids")),
        "invalid_receipt_ids": _normalize_id_list(value.get("invalid_receipt_ids")),
        "identity_mismatch_receipt_ids": _normalize_id_list(
            value.get("identity_mismatch_receipt_ids")
        ),
        "protocol_mismatch_receipt_ids": _normalize_id_list(
            value.get("protocol_mismatch_receipt_ids")
        ),
        "process_step_audit_complete": audit.get("complete") is True,
        "process_step_ledger_identity_mismatch_receipt_ids": _normalize_id_list(
            value.get("process_step_ledger_identity_mismatch_receipt_ids")
        ),
        "process_step_ledger_hash_mismatch_receipt_ids": _normalize_id_list(
            value.get("process_step_ledger_hash_mismatch_receipt_ids")
        ),
        "process_step_fact_hash_mismatch_receipt_ids": _normalize_id_list(
            value.get("process_step_fact_hash_mismatch_receipt_ids")
        ),
        "process_step_set_mismatch_fields": _normalize_id_list(
            value.get("process_step_set_mismatch_fields")
        ),
        "process_step_invariant_errors": _normalize_id_list(
            value.get("process_step_invariant_errors")
        ),
        "process_step_audit": audit,
    }


def derive_execution_lifecycle(
    *,
    execution_status: str,
    compile_succeeded: bool = True,
    fixture_required: bool = True,
    fixture_materialized: bool = False,
    state_precondition_required: bool = True,
    state_precondition_established: bool = False,
    required_steps_declared: bool = False,
    all_required_steps_executed: bool = False,
    observation_completed: bool = False,
    oracle_evaluated: bool = False,
    oracle_indeterminate: bool = False,
    cleanup_required: bool = False,
    cleanup_executed: bool = False,
    cleanup_verified: bool = False,
    environment_restored: bool = False,
    receipt_bundle: dict[str, Any] | None = None,
    finalizer_block_reason: str = "",
) -> dict[str, Any]:
    status = _text(execution_status).upper()
    block_reason = _text(finalizer_block_reason).upper()
    bundle = _bundle_facts(receipt_bundle)
    fixture_ok = (not fixture_required) or bool(fixture_materialized)
    precondition_ok = (
        (not state_precondition_required) or bool(state_precondition_established)
    )
    cleanup_execution_ok = (not cleanup_required) or bool(cleanup_executed)
    cleanup_verification_ok = (not cleanup_required) or bool(cleanup_verified)

    facts = {
        "execution_status": status,
        "compile_succeeded": bool(compile_succeeded),
        "fixture_required": bool(fixture_required),
        "fixture_materialized": bool(fixture_materialized),
        "state_precondition_required": bool(state_precondition_required),
        "state_precondition_established": bool(state_precondition_established),
        "required_steps_declared": bool(required_steps_declared),
        "all_required_steps_executed": bool(all_required_steps_executed),
        "observation_completed": bool(observation_completed),
        "oracle_evaluated": bool(oracle_evaluated),
        "oracle_indeterminate": bool(oracle_indeterminate),
        "cleanup_required": bool(cleanup_required),
        "cleanup_executed": bool(cleanup_executed),
        "cleanup_verified": bool(cleanup_verified),
        "environment_restored": bool(environment_restored),
        "finalizer_block_reason": block_reason,
        "receipt_bundle": bundle,
    }

    lifecycle_state: str
    reason_code: str
    completed_phase = LIFECYCLE_PLANNED

    if not compile_succeeded or "COMPILE" in block_reason:
        lifecycle_state = LIFECYCLE_COMPILE_BLOCKED
        reason_code = block_reason or "COMPILE_NOT_SUCCEEDED"
    else:
        completed_phase = LIFECYCLE_COMPILED
        if not fixture_ok or "FIXTURE" in block_reason:
            lifecycle_state = LIFECYCLE_FIXTURE_BLOCKED
            reason_code = block_reason or "FIXTURE_NOT_MATERIALIZED"
        else:
            completed_phase = LIFECYCLE_FIXTURE_MATERIALIZED
            if not precondition_ok or "PRECONDITION" in block_reason:
                lifecycle_state = LIFECYCLE_PRECONDITION_UNREACHABLE
                reason_code = block_reason or "STATE_PRECONDITION_NOT_ESTABLISHED"
            elif status in {"HARNESS_FAILURE", "HARNESS_FAILED"}:
                lifecycle_state = LIFECYCLE_HARNESS_FAILED
                reason_code = block_reason or status
            else:
                completed_phase = LIFECYCLE_PRECONDITION_ESTABLISHED
                if (
                    not required_steps_declared
                    or not all_required_steps_executed
                    or status in {"BLOCKED", "DEFERRED"}
                ):
                    lifecycle_state = LIFECYCLE_PARTIAL_EXECUTION
                    reason_code = block_reason or (
                        "REQUIRED_STEPS_NOT_DECLARED"
                        if not required_steps_declared
                        else "REQUIRED_STEPS_NOT_EXECUTED"
                    )
                else:
                    completed_phase = LIFECYCLE_BUSINESS_STEPS_EXECUTED
                    if not observation_completed:
                        lifecycle_state = LIFECYCLE_RECEIPT_INCOMPLETE
                        reason_code = block_reason or "OBSERVATION_NOT_COMPLETED"
                    else:
                        completed_phase = LIFECYCLE_OBSERVATION_COMPLETED
                        if oracle_indeterminate or not oracle_evaluated:
                            lifecycle_state = LIFECYCLE_ORACLE_INDETERMINATE
                            reason_code = block_reason or (
                                "ORACLE_INDETERMINATE"
                                if oracle_indeterminate
                                else "ORACLE_NOT_EVALUATED"
                            )
                        else:
                            completed_phase = LIFECYCLE_ORACLE_EVALUATED
                            if not cleanup_execution_ok:
                                lifecycle_state = LIFECYCLE_CLEANUP_FAILED
                                reason_code = block_reason or "CLEANUP_NOT_EXECUTED"
                            elif not cleanup_verification_ok:
                                lifecycle_state = LIFECYCLE_CLEANUP_FAILED
                                reason_code = block_reason or "CLEANUP_NOT_VERIFIED"
                            else:
                                completed_phase = (
                                    LIFECYCLE_CLEANUP_VERIFIED
                                    if cleanup_required
                                    else LIFECYCLE_ORACLE_EVALUATED
                                )
                                if not environment_restored:
                                    lifecycle_state = LIFECYCLE_ENVIRONMENT_DIRTY
                                    reason_code = (
                                        block_reason or "ENVIRONMENT_NOT_RESTORED"
                                    )
                                elif bundle["identity_mismatch_receipt_ids"]:
                                    lifecycle_state = LIFECYCLE_IDENTITY_MISMATCH
                                    reason_code = "PROCESS_RECEIPT_IDENTITY_MISMATCH"
                                elif bundle[
                                    "process_step_ledger_identity_mismatch_receipt_ids"
                                ]:
                                    lifecycle_state = LIFECYCLE_IDENTITY_MISMATCH
                                    reason_code = "PROCESS_STEP_LEDGER_IDENTITY_MISMATCH"
                                elif bundle["protocol_mismatch_receipt_ids"]:
                                    lifecycle_state = LIFECYCLE_PROTOCOL_MISMATCH
                                    reason_code = "PROCESS_RECEIPT_PROTOCOL_MISMATCH"
                                elif (
                                    # A step-set or invariant break is the more
                                    # specific diagnosis: rows were removed,
                                    # reclassified or re-hashed, which also
                                    # changes the ledger hash. Reporting the
                                    # generic HASH_MISMATCH first would hide
                                    # the actual set tampering.
                                    bundle["process_step_set_mismatch_fields"]
                                    or bundle["process_step_invariant_errors"]
                                ):
                                    lifecycle_state = LIFECYCLE_RECEIPT_INCOMPLETE
                                    reason_code = "PROCESS_STEP_SET_MISMATCH"
                                elif bundle[
                                    # A step FACT hash break is more specific
                                    # than a whole-ledger hash drift: a row's
                                    # fact content was tampered (the ledger hash
                                    # changes as a consequence, not as the
                                    # cause).
                                    "process_step_fact_hash_mismatch_receipt_ids"
                                ]:
                                    lifecycle_state = LIFECYCLE_RECEIPT_INCOMPLETE
                                    reason_code = "PROCESS_STEP_FACT_HASH_MISMATCH"
                                elif bundle[
                                    "process_step_ledger_hash_mismatch_receipt_ids"
                                ]:
                                    lifecycle_state = LIFECYCLE_RECEIPT_INCOMPLETE
                                    reason_code = "PROCESS_STEP_LEDGER_HASH_MISMATCH"
                                elif (
                                    not bundle["provided"]
                                    or not bundle["schema_valid"]
                                    or not bundle["structurally_complete"]
                                    or not bundle["process_step_audit_complete"]
                                    or bundle["missing_receipt_ids"]
                                    or bundle["invalid_receipt_ids"]
                                ):
                                    lifecycle_state = LIFECYCLE_RECEIPT_INCOMPLETE
                                    reason_code = block_reason or (
                                        "EXECUTION_RECEIPT_BUNDLE_NOT_ACTIVATED"
                                        if not bundle["provided"]
                                        else "EXECUTION_RECEIPT_BUNDLE_INCOMPLETE"
                                    )
                                else:
                                    completed_phase = LIFECYCLE_ENVIRONMENT_RESTORED
                                    lifecycle_state = LIFECYCLE_TRUE_COMPLETED
                                    reason_code = ""

    return {
        "schema_version": EXECUTION_LIFECYCLE_DERIVATION_SCHEMA,
        "lifecycle_state": lifecycle_state,
        "derived_terminal_status": lifecycle_state,
        "reason_code": reason_code,
        "completed_phase": completed_phase,
        "true_completed": lifecycle_state == LIFECYCLE_TRUE_COMPLETED,
        "terminal": lifecycle_state in TERMINAL_LIFECYCLE_STATES,
        "authority_module": "ai_test_asset_center.operational_receipts",
        "derivation_version": LIFECYCLE_DERIVATION_VERSION,
        "facts": facts,
    }


def derive_true_completed_from_bundle(
    bundle: dict[str, Any],
    *,
    oracle_evaluated: bool,
    cleanup_verified: bool,
    environment_restored: bool,
) -> dict[str, Any]:
    value = dict(_dict(bundle))
    if value.get("schema_version") != EXECUTION_RECEIPT_BUNDLE_SCHEMA:
        raise OperationalReceiptError("execution_receipt_bundle_schema_invalid")

    lifecycle = derive_execution_lifecycle(
        execution_status="EXECUTED",
        compile_succeeded=True,
        fixture_required=False,
        fixture_materialized=True,
        state_precondition_required=False,
        state_precondition_established=True,
        required_steps_declared=True,
        all_required_steps_executed=True,
        observation_completed=True,
        oracle_evaluated=oracle_evaluated,
        oracle_indeterminate=False,
        cleanup_required=True,
        cleanup_executed=True,
        cleanup_verified=cleanup_verified,
        environment_restored=environment_restored,
        receipt_bundle=value,
    )
    bundle_facts = lifecycle["facts"]["receipt_bundle"]
    compatibility_terminal = lifecycle["lifecycle_state"]
    if (
        compatibility_terminal == LIFECYCLE_ORACLE_INDETERMINATE
        and not oracle_evaluated
    ):
        compatibility_terminal = "ORACLE_NOT_EVALUATED"
    return {
        "receipt_bundle_valid": lifecycle["true_completed"],
        "bundle_structurally_complete": bundle_facts["structurally_complete"],
        "missing_receipt_ids": bundle_facts["missing_receipt_ids"],
        "invalid_receipt_ids": bundle_facts["invalid_receipt_ids"],
        "identity_mismatch_receipt_ids": bundle_facts[
            "identity_mismatch_receipt_ids"
        ],
        "protocol_mismatch_receipt_ids": bundle_facts[
            "protocol_mismatch_receipt_ids"
        ],
        "process_step_audit": bundle_facts.get("process_step_audit", {}),
        "oracle_evaluated": bool(oracle_evaluated),
        "cleanup_verified": bool(cleanup_verified),
        "environment_restored": bool(environment_restored),
        "derived_terminal_status": compatibility_terminal,
        "lifecycle_state": compatibility_terminal,
        "reason_code": lifecycle["reason_code"],
        "true_completed": lifecycle["true_completed"],
        "derivation_version": DERIVATION_VERSION,
        "lifecycle_derivation": lifecycle,
    }


def build_execution_finalization_receipt(
    *,
    finalization_receipt_id: str,
    bundle: dict[str, Any],
    oracle_evaluated: bool,
    cleanup_verified: bool,
    environment_restored: bool,
    code_commit_sha: str = NOT_APPLICABLE,
    tree_hash: str = NOT_APPLICABLE,
    lifecycle_facts: dict[str, Any] | None = None,
    execution_status: str = "EXECUTED",
    finalizer_block_reason: str = "",
) -> dict[str, Any]:
    if not _text(finalization_receipt_id):
        raise OperationalReceiptError("finalization_receipt_id_missing")

    facts = dict(_dict(lifecycle_facts))
    if facts:
        derivation = derive_execution_lifecycle(
            execution_status=_text(
                facts.get("execution_status") or execution_status
            ),
            compile_succeeded=facts.get("compile_succeeded", True) is True,
            fixture_required=facts.get("fixture_required", True) is True,
            fixture_materialized=facts.get("fixture_materialized", False) is True,
            state_precondition_required=(
                facts.get("state_precondition_required", True) is True
            ),
            state_precondition_established=(
                facts.get("state_precondition_established", False) is True
            ),
            required_steps_declared=(
                facts.get("required_steps_declared", False) is True
            ),
            all_required_steps_executed=(
                facts.get("all_required_steps_executed", False) is True
            ),
            observation_completed=facts.get("observation_completed", False) is True,
            oracle_evaluated=bool(
                facts.get("oracle_evaluated", oracle_evaluated)
            ),
            oracle_indeterminate=facts.get("oracle_indeterminate", False) is True,
            cleanup_required=facts.get("cleanup_required", False) is True,
            cleanup_executed=facts.get("cleanup_executed", False) is True,
            cleanup_verified=bool(
                facts.get("cleanup_verified", cleanup_verified)
            ),
            environment_restored=bool(
                facts.get("environment_restored", environment_restored)
            ),
            receipt_bundle=bundle,
            finalizer_block_reason=_text(
                facts.get("finalizer_block_reason") or finalizer_block_reason
            ),
        )
        bundle_state = _bundle_facts(bundle)
        compatibility = {
            "receipt_bundle_valid": derivation["true_completed"],
            "bundle_structurally_complete": bundle_state["structurally_complete"],
            "missing_receipt_ids": bundle_state["missing_receipt_ids"],
            "invalid_receipt_ids": bundle_state["invalid_receipt_ids"],
            "identity_mismatch_receipt_ids": bundle_state[
                "identity_mismatch_receipt_ids"
            ],
            "protocol_mismatch_receipt_ids": bundle_state[
                "protocol_mismatch_receipt_ids"
            ],
            "process_step_audit": bundle_state.get("process_step_audit", {}),
            "oracle_evaluated": bool(
                facts.get("oracle_evaluated", oracle_evaluated)
            ),
            "cleanup_verified": bool(
                facts.get("cleanup_verified", cleanup_verified)
            ),
            "environment_restored": bool(
                facts.get("environment_restored", environment_restored)
            ),
            "derived_terminal_status": derivation["lifecycle_state"],
            "lifecycle_state": derivation["lifecycle_state"],
            "reason_code": derivation["reason_code"],
            "true_completed": derivation["true_completed"],
            "derivation_version": DERIVATION_VERSION,
            "lifecycle_derivation": derivation,
        }
    else:
        compatibility = derive_true_completed_from_bundle(
            bundle,
            oracle_evaluated=oracle_evaluated,
            cleanup_verified=cleanup_verified,
            environment_restored=environment_restored,
        )

    payload = {
        "schema_version": EXECUTION_FINALIZATION_RECEIPT_SCHEMA,
        "finalization_receipt_id": _text(finalization_receipt_id),
        "execution_receipt_bundle_id": _text(bundle.get("bundle_id")),
        **compatibility,
    }
    payload["finalization_hash"] = _stable_hash(
        {key: value for key, value in payload.items() if key != "finalization_hash"}
    )
    envelope = build_canonical_receipt_envelope(
        receipt_type=EXECUTION_FINALIZATION_RECEIPT_SCHEMA,
        receipt_id=_text(finalization_receipt_id),
        payload=payload,
        campaign_id=_text(bundle.get("campaign_id")),
        run_id=_text(bundle.get("run_id")),
        obligation_id=_text(bundle.get("obligation_id")),
        experiment_id=_text(bundle.get("experiment_id")),
        fixture_id=_text(bundle.get("fixture_id")),
        protocol_id=_text(bundle.get("protocol_id")),
        producer_module="ai_test_asset_center.experiment_outcome_finalizer",
        producer_version=DERIVATION_VERSION,
        code_commit_sha=code_commit_sha,
        tree_hash=tree_hash,
        parent_receipt_ids=[_text(bundle.get("bundle_id"))]
        if _text(bundle.get("bundle_id"))
        else [],
        source_contract_ids=[],
        status=(
            RECEIPT_STATUS_VALID
            if compatibility["true_completed"]
            else RECEIPT_STATUS_INCOMPLETE
        ),
    )
    return {
        **payload,
        "envelope": envelope,
        "lifecycle_state": compatibility["derived_terminal_status"],
    }


# ---------------------------------------------------------------------------
# Provenance, metrics, and oracle trace utilities
# ---------------------------------------------------------------------------


def build_fixture_provenance_receipt(
    *,
    receipt_id: str,
    fixture_id: str,
    entity_id: str,
    scope_id: str,
    create_identity: str,
    readback_identity: str,
    operation_identity: str,
    observer_identity: str,
    cleanup_identity: str,
    ownership: str = "experiment_disposable",
    campaign_id: str = NOT_APPLICABLE,
    run_id: str = NOT_APPLICABLE,
    obligation_id: str = NOT_APPLICABLE,
    experiment_id: str = NOT_APPLICABLE,
    protocol_id: str = NOT_APPLICABLE,
    parent_receipt_ids: list[str] | None = None,
    code_commit_sha: str = NOT_APPLICABLE,
    tree_hash: str = NOT_APPLICABLE,
) -> dict[str, Any]:
    identities = {
        "create_identity": _text(create_identity),
        "readback_identity": _text(readback_identity),
        "operation_identity": _text(operation_identity),
        "observer_identity": _text(observer_identity),
        "cleanup_identity": _text(cleanup_identity),
    }
    if not all(identities.values()):
        raise OperationalReceiptError("fixture_provenance_identity_incomplete")
    if len(set(identities.values())) != 1:
        raise OperationalReceiptError("fixture_identity_drift")
    ownership_norm = _text(ownership).lower()
    if ownership_norm in {
        "customer",
        "customer_owned",
        "latest_record",
        "max_id",
    }:
        raise OperationalReceiptError(
            "customer_owned_or_heuristic_fixture_forbidden"
        )
    if not _text(scope_id):
        raise OperationalReceiptError("fixture_scope_missing")

    payload = {
        "schema_version": FIXTURE_PROVENANCE_RECEIPT_SCHEMA,
        "fixture_id": _text(fixture_id),
        "entity_id": _text(entity_id),
        "scope_id": _text(scope_id),
        "ownership": ownership_norm or "experiment_disposable",
        "identity_stable": True,
        "scope_stable": True,
        **identities,
    }
    return build_canonical_receipt_envelope(
        receipt_type=FIXTURE_PROVENANCE_RECEIPT_SCHEMA,
        receipt_id=_text(receipt_id),
        payload=payload,
        campaign_id=campaign_id,
        run_id=run_id,
        obligation_id=obligation_id,
        experiment_id=experiment_id,
        fixture_id=_text(fixture_id) or NOT_APPLICABLE,
        protocol_id=protocol_id,
        producer_module="ai_test_asset_center.disposable_fixture_contract",
        producer_version=DERIVATION_VERSION,
        code_commit_sha=code_commit_sha,
        tree_hash=tree_hash,
        parent_receipt_ids=parent_receipt_ids,
        source_contract_ids=[_text(fixture_id)] if _text(fixture_id) else [],
    )


def build_report_metric_receipt(
    *,
    receipt_id: str,
    metric_name: str,
    metric_value: int | float,
    source_receipt_ids: list[str],
    denominator_manifest_hash: str,
    ledger_hash: str,
    campaign_id: str = NOT_APPLICABLE,
    run_id: str = NOT_APPLICABLE,
    code_commit_sha: str = NOT_APPLICABLE,
    tree_hash: str = NOT_APPLICABLE,
) -> dict[str, Any]:
    source_ids = _normalize_id_list(source_receipt_ids)
    if not _text(metric_name):
        raise OperationalReceiptError("report_metric_name_missing")
    if not source_ids:
        raise OperationalReceiptError("report_metric_source_receipts_missing")
    if not _text(denominator_manifest_hash):
        raise OperationalReceiptError("report_metric_denominator_manifest_missing")
    if not _text(ledger_hash):
        raise OperationalReceiptError("report_metric_ledger_hash_missing")

    payload = {
        "schema_version": REPORT_METRIC_RECEIPT_SCHEMA,
        "metric_name": _text(metric_name),
        "metric_value": metric_value,
        "source_receipt_ids": source_ids,
        "denominator_manifest_hash": _text(denominator_manifest_hash),
        "ledger_hash": _text(ledger_hash),
        "id_set_hash": _stable_hash(sorted(source_ids)),
    }
    return build_canonical_receipt_envelope(
        receipt_type=REPORT_METRIC_RECEIPT_SCHEMA,
        receipt_id=_text(receipt_id),
        payload=payload,
        campaign_id=campaign_id,
        run_id=run_id,
        obligation_id=NOT_APPLICABLE,
        experiment_id=NOT_APPLICABLE,
        fixture_id=NOT_APPLICABLE,
        protocol_id=NOT_APPLICABLE,
        producer_module="ai_test_asset_center.discovery_quality_projection",
        producer_version=DERIVATION_VERSION,
        code_commit_sha=code_commit_sha,
        tree_hash=tree_hash,
        parent_receipt_ids=source_ids,
    )


def audit_report_metric_ledger_balance(
    metric_receipts: list[dict[str, Any]],
    *,
    expected_ledger_hash: str,
) -> dict[str, Any]:
    rows = [validate_canonical_receipt_envelope(row) for row in _list(metric_receipts)]
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        payload = _dict(row.get("payload"))
        if _text(payload.get("ledger_hash")) != _text(expected_ledger_hash):
            mismatches.append(
                {
                    "receipt_id": _text(row.get("receipt_id")),
                    "metric_name": _text(payload.get("metric_name")),
                    "observed_ledger_hash": _text(payload.get("ledger_hash")),
                    "expected_ledger_hash": _text(expected_ledger_hash),
                }
            )
    if mismatches:
        raise OperationalReceiptError(
            f"formal_report_receipt_balance_mismatch:{len(mismatches)}"
        )
    return {
        "schema_version": "qualibug.formal-report-receipt-balance.v1",
        "balanced": True,
        "metric_receipt_count": len(rows),
        "expected_ledger_hash": _text(expected_ledger_hash),
        "mismatches": [],
    }


def unique_oracle_evaluation_key(
    *,
    rule_id: str,
    experiment_id: str,
    fixture_id: str,
    assertion_fingerprint: str,
    observation_pair_fingerprint: str,
) -> str:
    payload = {
        "rule_id": _text(rule_id),
        "experiment_id": _text(experiment_id),
        "fixture_id": _text(fixture_id),
        "assertion_fingerprint": _text(assertion_fingerprint),
        "observation_pair_fingerprint": _text(observation_pair_fingerprint),
    }
    if not all(payload.values()):
        raise OperationalReceiptError("oracle_evaluation_key_incomplete")
    return "oeval_" + _stable_hash(payload)[:32]


def deduplicate_oracle_traces(
    traces: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = [dict(_dict(row)) for row in _list(traces)]
    unique: dict[str, dict[str, Any]] = {}
    classified = {
        "evaluation": 0,
        "polling": 0,
        "retry": 0,
        "reproduction": 0,
        "other": 0,
    }
    duplicates: list[str] = []
    for row in raw:
        kind = _text(
            row.get("trace_kind") or row.get("kind") or "evaluation"
        ).lower()
        if kind not in classified:
            kind = "other"
        classified[kind] += 1
        if kind != "evaluation":
            continue
        key = _text(row.get("evaluation_key"))
        if not key:
            key = unique_oracle_evaluation_key(
                rule_id=_text(row.get("rule_id")),
                experiment_id=_text(row.get("experiment_id")),
                fixture_id=_text(row.get("fixture_id") or NOT_APPLICABLE),
                assertion_fingerprint=_text(
                    row.get("assertion_fingerprint") or row.get("assertion_fp")
                ),
                observation_pair_fingerprint=_text(
                    row.get("observation_pair_fingerprint")
                    or row.get("observation_pair_fp")
                ),
            )
        if key in unique:
            duplicates.append(key)
            continue
        unique[key] = row
    return {
        "schema_version": "qualibug.oracle-trace-dedup-audit.v1",
        "raw_trace_count": len(raw),
        "unique_evaluation_count": len(unique),
        "duplicate_evaluation_keys": sorted(set(duplicates)),
        "classification_counts": classified,
        "unique_evaluation_keys": sorted(unique.keys()),
        "raw_traces_do_not_count_as_unique": True,
    }


# ---------------------------------------------------------------------------
# Finalizer adapter: heterogeneous evidence -> canonical bundle
# ---------------------------------------------------------------------------


def assemble_bundle_from_finalizer_observations(
    *,
    bundle_id: str,
    campaign_id: str,
    run_id: str,
    obligation_id: str,
    experiment_id: str,
    fixture_id: str,
    protocol_id: str,
    code_commit_sha: str,
    tree_hash: str,
    compile_receipt: dict[str, Any] | None,
    fixture_provenance_receipts: list[dict[str, Any]],
    process_step_receipts: list[dict[str, Any]],
    transport_receipts: list[dict[str, Any]],
    observation_receipts: list[dict[str, Any]],
    oracle_invocation_receipts: list[dict[str, Any]],
    oracle_trace_receipts: list[dict[str, Any]],
    cleanup_execution_receipts: list[dict[str, Any]],
    cleanup_verification_receipts: list[dict[str, Any]],
    environment_restoration_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    def _ensure_envelope(
        receipt_type: str,
        raw: dict[str, Any],
        fallback_id: str,
    ) -> dict[str, Any]:
        if (
            _text(raw.get("schema_version")) == CANONICAL_RECEIPT_ENVELOPE_SCHEMA
            and _text(raw.get("receipt_id"))
        ):
            return validate_canonical_receipt_envelope(raw)
        rid = _text(raw.get("receipt_id") or raw.get("id") or fallback_id)
        return build_canonical_receipt_envelope(
            receipt_type=receipt_type,
            receipt_id=rid,
            payload=dict(raw),
            campaign_id=campaign_id,
            run_id=run_id,
            obligation_id=obligation_id,
            experiment_id=experiment_id,
            fixture_id=fixture_id,
            protocol_id=protocol_id,
            code_commit_sha=code_commit_sha,
            tree_hash=tree_hash,
            parent_receipt_ids=_normalize_id_list(raw.get("parent_receipt_ids")),
            source_contract_ids=_normalize_id_list(raw.get("source_contract_ids")),
        )

    receipts: list[dict[str, Any]] = []
    compile_id = ""
    if compile_receipt:
        envelope = _ensure_envelope(
            "qualibug.compile-receipt.v1",
            dict(compile_receipt),
            f"compile_{experiment_id}",
        )
        receipts.append(envelope)
        compile_id = _text(envelope.get("receipt_id"))

    def _append_many(
        rows: list[dict[str, Any]],
        *,
        receipt_type: str,
        fallback_prefix: str,
    ) -> list[str]:
        ids: list[str] = []
        for index, row in enumerate(_list(rows)):
            envelope = _ensure_envelope(
                receipt_type,
                dict(_dict(row)),
                f"{fallback_prefix}_{experiment_id}_{index}",
            )
            receipts.append(envelope)
            ids.append(_text(envelope.get("receipt_id")))
        return ids

    fixture_ids: list[str] = []
    for index, row in enumerate(_list(fixture_provenance_receipts)):
        raw = dict(_dict(row))
        receipt_type = (
            FIXTURE_PROVENANCE_RECEIPT_SCHEMA
            if _text(raw.get("receipt_type")).endswith(
                "fixture-provenance-receipt.v1"
            )
            or "fixture" in _text(raw.get("schema_version"))
            else "qualibug.fixture-materialization-receipt.v1"
        )
        envelope = _ensure_envelope(
            receipt_type, raw, f"fixture_{experiment_id}_{index}"
        )
        receipts.append(envelope)
        fixture_ids.append(_text(envelope.get("receipt_id")))

    step_ids = _append_many(
        process_step_receipts,
        receipt_type="qualibug.process-step-receipt.v1",
        fallback_prefix="step",
    )
    transport_ids = _append_many(
        transport_receipts,
        receipt_type="qualibug.transport-receipt.v1",
        fallback_prefix="transport",
    )
    observation_ids = _append_many(
        observation_receipts,
        receipt_type="qualibug.observation-receipt.v1",
        fallback_prefix="obs",
    )
    oracle_inv_ids = _append_many(
        oracle_invocation_receipts,
        receipt_type="qualibug.oracle-invocation-receipt.v1",
        fallback_prefix="oracle_inv",
    )
    oracle_trace_ids = _append_many(
        oracle_trace_receipts,
        receipt_type="qualibug.oracle-trace-receipt.v1",
        fallback_prefix="oracle_trace",
    )
    cleanup_exec_ids = _append_many(
        cleanup_execution_receipts,
        receipt_type="qualibug.cleanup-execution-receipt.v1",
        fallback_prefix="cleanup_exec",
    )
    cleanup_ver_ids = _append_many(
        cleanup_verification_receipts,
        receipt_type="qualibug.cleanup-verification-receipt.v1",
        fallback_prefix="cleanup_ver",
    )

    env_rest_id = ""
    if environment_restoration_receipt:
        envelope = _ensure_envelope(
            "qualibug.environment-restoration-receipt.v1",
            dict(environment_restoration_receipt),
            f"env_restore_{experiment_id}",
        )
        receipts.append(envelope)
        env_rest_id = _text(envelope.get("receipt_id"))

    return build_execution_receipt_bundle(
        bundle_id=bundle_id,
        campaign_id=campaign_id,
        run_id=run_id,
        obligation_id=obligation_id,
        experiment_id=experiment_id,
        fixture_id=fixture_id,
        protocol_id=protocol_id,
        receipts=receipts,
        compile_receipt_id=compile_id,
        fixture_provenance_receipt_ids=fixture_ids,
        required_step_receipt_ids=step_ids,
        transport_receipt_ids=transport_ids,
        observation_receipt_ids=observation_ids,
        oracle_invocation_receipt_ids=oracle_inv_ids,
        oracle_trace_receipt_ids=oracle_trace_ids,
        cleanup_execution_receipt_ids=cleanup_exec_ids,
        cleanup_verification_receipt_ids=cleanup_ver_ids,
        environment_restoration_receipt_id=env_rest_id,
    )
