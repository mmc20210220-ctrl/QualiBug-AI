"""Receipt-backed operational accounting for discovery execution attempts.

Only explicit HTTP-step and governed-write receipts are counted.  This module
never walks arbitrary result JSON looking for status-like fields.

V1.6.2 Gate A: Canonical Receipt Envelope, Execution Receipt Bundle, and
Finalization Receipt derivation also live here so receipt authority stays on
the existing operational-receipt spine (no parallel *_v2 ledger).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


EXECUTION_OPERATIONAL_RECEIPT_SCHEMA = (
    "qualibug.execution-operational-receipt.v1"
)
EXECUTION_OPERATIONAL_SUMMARY_SCHEMA = (
    "qualibug.execution-operational-summary.v1"
)

# ── V1.6.2 Canonical Receipt Envelope ──────────────────────────────────────
CANONICAL_RECEIPT_ENVELOPE_SCHEMA = "qualibug.canonical-receipt-envelope.v1"
EXECUTION_RECEIPT_BUNDLE_SCHEMA = "qualibug.execution-receipt-bundle.v1"
EXECUTION_FINALIZATION_RECEIPT_SCHEMA = (
    "qualibug.execution-finalization-receipt.v1"
)
FIXTURE_PROVENANCE_RECEIPT_SCHEMA = "qualibug.fixture-provenance-receipt.v1"
REPORT_METRIC_RECEIPT_SCHEMA = "qualibug.report-metric-receipt.v1"
CORRECTION_RECEIPT_SCHEMA = "qualibug.correction-receipt.v1"

NOT_APPLICABLE = "NOT_APPLICABLE"
RECEIPT_STATUS_VALID = "VALID"
RECEIPT_STATUS_INVALID = "INVALID"
RECEIPT_STATUS_INCOMPLETE = "INCOMPLETE"
RECEIPT_STATUS_TAMPERED = "TAMPERED"
_RECEIPT_STATUSES = frozenset({
    RECEIPT_STATUS_VALID,
    RECEIPT_STATUS_INVALID,
    RECEIPT_STATUS_INCOMPLETE,
    RECEIPT_STATUS_TAMPERED,
})

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
_EXECUTION_STATUSES = frozenset({
    "EXECUTED",
    "BLOCKED",
    "DEFERRED",
    "HARNESS_FAILURE",
    "HARNESS_FAILED",
    "DELIVERABLE",
    # V1.6.1: write executed and oracle may have evaluated, but cleanup/restoration
    # did not complete. Must remain a first-class operational status so Field Oracle
    # Traces are not lost behind an OperationalReceiptError abort.
    "EXECUTED_BUT_NOT_RESTORED",
})
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
    unsigned = {
        key: item
        for key, item in value.items()
        if key != "receipt_fingerprint"
    }
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
    """Build one terminal operational receipt from explicit execution steps."""

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
            if production > attempts:
                raise OperationalReceiptError(
                    "governance_production_http_requests_exceed_attempts"
                )
            write_attempts = _non_negative_int(
                governance.get("write_request_attempt_count"),
                "governance_write_request_attempt_count",
            )
            if write_attempts > attempts:
                raise OperationalReceiptError(
                    "governance_write_request_attempts_exceed_http_attempts"
                )
            http_attempts += attempts
            write_request_attempts += write_attempts
            production_requests += production
            accepted = governance.get("accepted") is True
            if phase in _CLEANUP_PHASES:
                if attempts:
                    cleanup_attempted += 1
                if accepted:
                    accepted_cleanup += 1
                    cleanup_completed += 1
            elif accepted:
                accepted_non_cleanup += 1
            continue

        # A direct HTTP adapter step is one request attempt.  A blocked or
        # unresolved plan row has no status_code field and therefore no request.
        status_code = int(step.get("status_code") or 0)
        if _text(step.get("method")) and _text(step.get("path")) and status_code > 0:
            http_attempts += 1
            if _text(step.get("method")).upper() in _WRITE_METHODS:
                write_request_attempts += 1

    failure_count = _non_negative_int(cleanup_failures, "cleanup_failures")
    if failure_count:
        cleanup_status = "FAILED"
    elif accepted_non_cleanup and cleanup_attempted:
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
    """Seal explicit adapter counters without scanning arbitrary result JSON."""

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
            scenario_attempt_count,
            "scenario_attempt_count",
        ),
        "http_request_attempt_count": _non_negative_int(
            http_request_attempt_count,
            "http_request_attempt_count",
        ),
        "write_request_attempt_count": _non_negative_int(
            write_request_attempt_count,
            "write_request_attempt_count",
        ),
        "production_http_request_count": _non_negative_int(
            production_http_request_count,
            "production_http_request_count",
        ),
        "accepted_write_count": accepted_non_cleanup + accepted_cleanup,
        "accepted_non_cleanup_write_count": accepted_non_cleanup,
        "accepted_cleanup_write_count": accepted_cleanup,
        "cleanup_outcome": {
            "status": _text(cleanup_status).upper(),
            "attempted_count": _non_negative_int(
                cleanup_attempted_count,
                "cleanup_attempted_count",
            ),
            "completed_count": _non_negative_int(
                cleanup_completed_count,
                "cleanup_completed_count",
            ),
            "failure_count": _non_negative_int(
                cleanup_failure_count,
                "cleanup_failure_count",
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
    """Aggregate validated terminal receipts without scanning unrelated JSON."""

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
        _dict(row.get("cleanup_outcome")).get("failure_count", 0)
        for row in rows
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


# ═══════════════════════════════════════════════════════════════════════════
# V1.6.2 Gate A — Canonical Receipt Envelope / Bundle / Finalization
# ═══════════════════════════════════════════════════════════════════════════


def _stable_hash(value: Any) -> str:
    return _fingerprint(value)


def _require_identity_value(value: Any, field: str) -> str:
    text = _text(value)
    if not text:
        raise OperationalReceiptError(f"{field}_missing_use_NOT_APPLICABLE")
    return text


def _normalize_id_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _list(value):
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out


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
    """Build a unified Canonical Receipt Envelope (SPEC V1.6.2 §7.1)."""

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
    payload_hash = _stable_hash(payload)
    envelope: dict[str, Any] = {
        "schema_version": CANONICAL_RECEIPT_ENVELOPE_SCHEMA,
        "receipt_type": _text(receipt_type),
        "receipt_id": _text(receipt_id),
        **identity,
        "producer_module": _text(producer_module) or "ai_test_asset_center.operational_receipts",
        "producer_version": _text(producer_version) or DERIVATION_VERSION,
        "code_commit_sha": _require_identity_value(code_commit_sha, "code_commit_sha"),
        "tree_hash": _require_identity_value(tree_hash, "tree_hash"),
        "parent_receipt_ids": _normalize_id_list(parent_receipt_ids),
        "source_contract_ids": _normalize_id_list(source_contract_ids),
        "payload": dict(payload),
        "payload_hash": payload_hash,
        "produced_at": _text(produced_at) or datetime.now(timezone.utc).isoformat(),
        "status": normalized_status,
    }
    envelope["receipt_hash"] = _stable_hash(
        {k: v for k, v in envelope.items() if k != "receipt_hash"}
    )
    return validate_canonical_receipt_envelope(envelope)


def validate_canonical_receipt_envelope(
    receipt: dict[str, Any],
    *,
    expected_code_commit_sha: str | None = None,
    expected_tree_hash: str | None = None,
) -> dict[str, Any]:
    """Validate envelope integrity, hash, and required identity fields."""

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
    actual_payload_hash = _stable_hash(value.get("payload"))
    if not claimed_payload_hash:
        raise OperationalReceiptError("payload_hash_missing")
    if claimed_payload_hash != actual_payload_hash:
        value["status"] = RECEIPT_STATUS_TAMPERED
        raise OperationalReceiptError("payload_hash_mismatch")

    claimed_receipt_hash = _text(value.get("receipt_hash"))
    if not claimed_receipt_hash:
        raise OperationalReceiptError("receipt_hash_missing")
    unsigned = {k: v for k, v in value.items() if k != "receipt_hash"}
    if claimed_receipt_hash != _stable_hash(unsigned):
        value["status"] = RECEIPT_STATUS_TAMPERED
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
    """Fail closed when a parent_receipt_id is not present in the set."""

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
                broken.append({
                    "receipt_id": _text(row.get("receipt_id")),
                    "missing_parent_receipt_id": pid,
                })
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
    """Return a tamper audit row; never silently accept mutated payloads."""

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
            "status": RECEIPT_STATUS_TAMPERED if tampered else RECEIPT_STATUS_INVALID,
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
    """Append-only correction: original receipt must remain retained by caller."""

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
    """Assemble and validate an Execution Receipt Bundle (SPEC §8.2)."""

    if not _text(bundle_id):
        raise OperationalReceiptError("bundle_id_missing")
    validated = [validate_canonical_receipt_envelope(row) for row in _list(receipts)]
    by_id = {_text(row["receipt_id"]): row for row in validated}
    if len(by_id) != len(validated):
        raise OperationalReceiptError("bundle_receipt_id_duplicate")

    groups = {
        "compile_receipt_id": _text(compile_receipt_id),
        "fixture_provenance_receipt_ids": _normalize_id_list(fixture_provenance_receipt_ids),
        "required_step_receipt_ids": _normalize_id_list(required_step_receipt_ids),
        "transport_receipt_ids": _normalize_id_list(transport_receipt_ids),
        "observation_receipt_ids": _normalize_id_list(observation_receipt_ids),
        "oracle_invocation_receipt_ids": _normalize_id_list(oracle_invocation_receipt_ids),
        "oracle_trace_receipt_ids": _normalize_id_list(oracle_trace_receipt_ids),
        "cleanup_execution_receipt_ids": _normalize_id_list(cleanup_execution_receipt_ids),
        "cleanup_verification_receipt_ids": _normalize_id_list(
            cleanup_verification_receipt_ids
        ),
        "environment_restoration_receipt_id": _text(environment_restoration_receipt_id),
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
        # Compare only concrete (non-NOT_APPLICABLE) positions on both sides.
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

    complete = (
        not missing_receipt_ids
        and identity_consistent
        and protocol_consistent
        and not invalid_receipt_ids
        and required_set_hash == actual_set_hash
        and bool(required_ids)
    )
    if required_set_hash != actual_set_hash and not missing_receipt_ids:
        # Extra receipts are allowed only when required set is fully covered;
        # completeness for TRUE_COMPLETED still requires exact required coverage.
        complete = (
            not missing_receipt_ids
            and identity_consistent
            and protocol_consistent
            and not invalid_receipt_ids
            and bool(required_ids)
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
        "complete": complete,
        "validation_errors": validation_errors,
        "missing_receipt_ids": missing_receipt_ids,
        "invalid_receipt_ids": sorted(set(invalid_receipt_ids)),
        "identity_mismatch_receipt_ids": sorted(set(identity_mismatch_receipt_ids)),
        "protocol_mismatch_receipt_ids": sorted(set(protocol_mismatch_receipt_ids)),
        "receipts": validated,
    }
    bundle["bundle_hash"] = _stable_hash(
        {k: v for k, v in bundle.items() if k not in {"receipts", "bundle_hash"}}
    )
    return bundle


def derive_true_completed_from_bundle(
    bundle: dict[str, Any],
    *,
    oracle_evaluated: bool,
    cleanup_verified: bool,
    environment_restored: bool,
) -> dict[str, Any]:
    """Derive terminal status from a validated Execution Receipt Bundle only."""

    value = dict(_dict(bundle))
    if value.get("schema_version") != EXECUTION_RECEIPT_BUNDLE_SCHEMA:
        raise OperationalReceiptError("execution_receipt_bundle_schema_invalid")

    missing = _normalize_id_list(value.get("missing_receipt_ids"))
    invalid = _normalize_id_list(value.get("invalid_receipt_ids"))
    identity_mismatch = _normalize_id_list(value.get("identity_mismatch_receipt_ids"))
    protocol_mismatch = _normalize_id_list(value.get("protocol_mismatch_receipt_ids"))
    bundle_valid = bool(value.get("complete")) and not missing and not invalid

    if identity_mismatch:
        derived = "IDENTITY_MISMATCH"
    elif protocol_mismatch:
        derived = "PROTOCOL_MISMATCH"
    elif not bundle_valid or missing or invalid:
        derived = "RECEIPT_INCOMPLETE"
    elif not oracle_evaluated:
        derived = "ORACLE_NOT_EVALUATED"
    elif not cleanup_verified:
        derived = "CLEANUP_FAILED"
    elif not environment_restored:
        derived = "ENVIRONMENT_DIRTY"
    else:
        derived = "TRUE_COMPLETED"

    return {
        "receipt_bundle_valid": bundle_valid and derived == "TRUE_COMPLETED",
        "bundle_structurally_complete": bool(value.get("complete")),
        "missing_receipt_ids": missing,
        "invalid_receipt_ids": invalid,
        "identity_mismatch_receipt_ids": identity_mismatch,
        "protocol_mismatch_receipt_ids": protocol_mismatch,
        "oracle_evaluated": bool(oracle_evaluated),
        "cleanup_verified": bool(cleanup_verified),
        "environment_restored": bool(environment_restored),
        "derived_terminal_status": derived,
        "true_completed": derived == "TRUE_COMPLETED",
        "derivation_version": DERIVATION_VERSION,
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
) -> dict[str, Any]:
    """Finalizer-only finalization receipt (SPEC §8.3)."""

    if not _text(finalization_receipt_id):
        raise OperationalReceiptError("finalization_receipt_id_missing")
    derivation = derive_true_completed_from_bundle(
        bundle,
        oracle_evaluated=oracle_evaluated,
        cleanup_verified=cleanup_verified,
        environment_restored=environment_restored,
    )
    payload = {
        "schema_version": EXECUTION_FINALIZATION_RECEIPT_SCHEMA,
        "finalization_receipt_id": _text(finalization_receipt_id),
        "execution_receipt_bundle_id": _text(bundle.get("bundle_id")),
        **derivation,
    }
    payload["finalization_hash"] = _stable_hash(
        {k: v for k, v in payload.items() if k != "finalization_hash"}
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
        status=RECEIPT_STATUS_VALID
        if derivation["true_completed"]
        else RECEIPT_STATUS_INCOMPLETE,
    )
    return {
        **payload,
        "envelope": envelope,
        "lifecycle_state": derivation["derived_terminal_status"],
    }


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
    """Lifecycle fixture provenance with identity/scope stability checks."""

    identities = {
        "create_identity": _text(create_identity),
        "readback_identity": _text(readback_identity),
        "operation_identity": _text(operation_identity),
        "observer_identity": _text(observer_identity),
        "cleanup_identity": _text(cleanup_identity),
    }
    if not all(identities.values()):
        raise OperationalReceiptError("fixture_provenance_identity_incomplete")
    unique = set(identities.values())
    identity_stable = len(unique) == 1
    ownership_norm = _text(ownership).lower()
    if ownership_norm in {"customer", "customer_owned", "latest_record", "max_id"}:
        raise OperationalReceiptError("customer_owned_or_heuristic_fixture_forbidden")
    if not identity_stable:
        raise OperationalReceiptError("fixture_identity_drift")
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
    """Formal report metric must bind to receipt IDs + sealed denominator."""

    if not _text(metric_name):
        raise OperationalReceiptError("report_metric_name_missing")
    if not _normalize_id_list(source_receipt_ids):
        raise OperationalReceiptError("report_metric_source_receipts_missing")
    if not _text(denominator_manifest_hash):
        raise OperationalReceiptError("report_metric_denominator_manifest_missing")
    if not _text(ledger_hash):
        raise OperationalReceiptError("report_metric_ledger_hash_missing")
    payload = {
        "schema_version": REPORT_METRIC_RECEIPT_SCHEMA,
        "metric_name": _text(metric_name),
        "metric_value": metric_value,
        "source_receipt_ids": _normalize_id_list(source_receipt_ids),
        "denominator_manifest_hash": _text(denominator_manifest_hash),
        "ledger_hash": _text(ledger_hash),
        "id_set_hash": _stable_hash(sorted(_normalize_id_list(source_receipt_ids))),
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
        parent_receipt_ids=_normalize_id_list(source_receipt_ids),
    )


def audit_report_metric_ledger_balance(
    metric_receipts: list[dict[str, Any]],
    *,
    expected_ledger_hash: str,
) -> dict[str, Any]:
    """Fail closed when formal metric receipts disagree with ledger hash."""

    rows = [validate_canonical_receipt_envelope(row) for row in _list(metric_receipts)]
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        payload = _dict(row.get("payload"))
        if _text(payload.get("ledger_hash")) != _text(expected_ledger_hash):
            mismatches.append({
                "receipt_id": _text(row.get("receipt_id")),
                "metric_name": _text(payload.get("metric_name")),
                "observed_ledger_hash": _text(payload.get("ledger_hash")),
                "expected_ledger_hash": _text(expected_ledger_hash),
            })
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
    """Canonical unique evaluation key for oracle traces (raw traces ≠ unique)."""

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
    """Classify raw traces and count unique evaluations only once."""

    raw = [dict(_dict(row)) for row in _list(traces)]
    unique: dict[str, dict[str, Any]] = {}
    classified = {"evaluation": 0, "polling": 0, "retry": 0, "reproduction": 0, "other": 0}
    duplicates: list[str] = []
    for row in raw:
        kind = _text(row.get("trace_kind") or row.get("kind") or "evaluation").lower()
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
    if duplicates:
        # Detected, not silently collapsed without audit.
        pass
    return {
        "schema_version": "qualibug.oracle-trace-dedup-audit.v1",
        "raw_trace_count": len(raw),
        "unique_evaluation_count": len(unique),
        "duplicate_evaluation_keys": sorted(set(duplicates)),
        "classification_counts": classified,
        "unique_evaluation_keys": sorted(unique.keys()),
        "raw_traces_do_not_count_as_unique": True,
    }


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
    """Wrap heterogeneous finalizer evidence into enveloped receipts + bundle."""

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
        env = _ensure_envelope(
            "qualibug.compile-receipt.v1",
            dict(compile_receipt),
            f"compile_{experiment_id}",
        )
        receipts.append(env)
        compile_id = _text(env.get("receipt_id"))

    fixture_ids: list[str] = []
    for idx, row in enumerate(_list(fixture_provenance_receipts)):
        env = _ensure_envelope(
            FIXTURE_PROVENANCE_RECEIPT_SCHEMA
            if _text(row.get("receipt_type")).endswith("fixture-provenance-receipt.v1")
            or "fixture" in _text(row.get("schema_version"))
            else "qualibug.fixture-materialization-receipt.v1",
            dict(row),
            f"fixture_{experiment_id}_{idx}",
        )
        receipts.append(env)
        fixture_ids.append(_text(env.get("receipt_id")))

    step_ids: list[str] = []
    for idx, row in enumerate(_list(process_step_receipts)):
        env = _ensure_envelope(
            "qualibug.process-step-receipt.v1",
            dict(row),
            f"step_{experiment_id}_{idx}",
        )
        receipts.append(env)
        step_ids.append(_text(env.get("receipt_id")))

    transport_ids: list[str] = []
    for idx, row in enumerate(_list(transport_receipts)):
        env = _ensure_envelope(
            "qualibug.transport-receipt.v1",
            dict(row),
            f"transport_{experiment_id}_{idx}",
        )
        receipts.append(env)
        transport_ids.append(_text(env.get("receipt_id")))

    observation_ids: list[str] = []
    for idx, row in enumerate(_list(observation_receipts)):
        env = _ensure_envelope(
            "qualibug.observation-receipt.v1",
            dict(row),
            f"obs_{experiment_id}_{idx}",
        )
        receipts.append(env)
        observation_ids.append(_text(env.get("receipt_id")))

    oracle_inv_ids: list[str] = []
    for idx, row in enumerate(_list(oracle_invocation_receipts)):
        env = _ensure_envelope(
            "qualibug.oracle-invocation-receipt.v1",
            dict(row),
            f"oracle_inv_{experiment_id}_{idx}",
        )
        receipts.append(env)
        oracle_inv_ids.append(_text(env.get("receipt_id")))

    oracle_trace_ids: list[str] = []
    for idx, row in enumerate(_list(oracle_trace_receipts)):
        env = _ensure_envelope(
            "qualibug.oracle-trace-receipt.v1",
            dict(row),
            f"oracle_trace_{experiment_id}_{idx}",
        )
        receipts.append(env)
        oracle_trace_ids.append(_text(env.get("receipt_id")))

    cleanup_exec_ids: list[str] = []
    for idx, row in enumerate(_list(cleanup_execution_receipts)):
        env = _ensure_envelope(
            "qualibug.cleanup-execution-receipt.v1",
            dict(row),
            f"cleanup_exec_{experiment_id}_{idx}",
        )
        receipts.append(env)
        cleanup_exec_ids.append(_text(env.get("receipt_id")))

    cleanup_ver_ids: list[str] = []
    for idx, row in enumerate(_list(cleanup_verification_receipts)):
        env = _ensure_envelope(
            "qualibug.cleanup-verification-receipt.v1",
            dict(row),
            f"cleanup_ver_{experiment_id}_{idx}",
        )
        receipts.append(env)
        cleanup_ver_ids.append(_text(env.get("receipt_id")))

    env_rest_id = ""
    if environment_restoration_receipt:
        env = _ensure_envelope(
            "qualibug.environment-restoration-receipt.v1",
            dict(environment_restoration_receipt),
            f"env_restore_{experiment_id}",
        )
        receipts.append(env)
        env_rest_id = _text(env.get("receipt_id"))

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
