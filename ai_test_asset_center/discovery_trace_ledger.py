"""Redacted, obligation-attempt keyed discovery trace ledger."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .artifact_redactor import redact_and_validate
from .obligation_attempt_ledger import validate_obligation_attempt_ledger


TRACE_LEDGER_SCHEMA = "qualibug.discovery-trace-ledger.v3"
TRACE_LEDGER_V1_SCHEMA = "qualibug.discovery-trace-ledger.v1"


class DiscoveryTraceError(ValueError):
    """Observed trace artifacts are structurally invalid."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required(value: Any, name: str) -> str:
    text = _text(value)
    if not text:
        raise DiscoveryTraceError(f"missing required trace context: {name}")
    return text


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{_fingerprint([_text(part) for part in parts])[:24]}"


def _opaque_ref(value: Any) -> str:
    """Keep opaque IDs; hash path-, URL-, query-, or whitespace-shaped references."""

    text = _text(value)
    if not text:
        return ""
    if re.search(r"[\\/\s?#]", text) or "://" in text:
        return "ref_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    return text


def _source_kinds(source_refs: list[Any]) -> list[str]:
    return sorted({
        _text(_dict(item).get("source_type") or _dict(item).get("kind"))
        for item in source_refs
        if _text(_dict(item).get("source_type") or _dict(item).get("kind"))
    })


def _source_ref_ids(source_refs: list[Any]) -> list[str]:
    ids: set[str] = set()
    for value in source_refs:
        row = _dict(value)
        identity = _text(
            row.get("source_id")
            or row.get("document_id")
            or row.get("section_id")
            or row.get("relation_id")
        )
        if identity:
            ids.add(_opaque_ref(identity))
    return sorted(ids)


def _stage(attempt: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        (
            _dict(item)
            for item in _list(attempt.get("stages"))
            if _text(_dict(item).get("stage")) == name
        ),
        {},
    )


def _failure_signatures(attempt: dict[str, Any]) -> list[str]:
    terminal = _text(attempt.get("terminal_status")).upper()
    terminal_stage = _text(attempt.get("terminal_stage")).lower()
    reasons = {
        _text(attempt.get("reason_code")).upper(),
        _text(_stage(attempt, "compile").get("reason_code")).upper(),
        _text(_stage(attempt, "execution").get("reason_code")).upper(),
        _text(_stage(attempt, "gate").get("reason_code")).upper(),
    }
    reasons.discard("")
    signatures: set[str] = set()
    if terminal == "BLOCKED" and terminal_stage == "compile":
        signatures.add("EXPERIMENT_COMPILE_BLOCKED")
    if terminal == "DEFERRED":
        signatures.add("CANDIDATE_NOT_SELECTED")
    if terminal == "HARNESS_FAILED":
        signatures.add("EXECUTION_ERROR")
    for reason in reasons:
        if "BINDING" in reason or "PLACEHOLDER" in reason:
            signatures.add("OBLIGATION_BINDING_MISSING")
        if "MISSING_OPERATION" in reason:
            signatures.add("ENDPOINT_BINDING_MISSING")
        if "MISSING_ACTOR" in reason:
            signatures.add("SOURCE_GROUNDING_MISSING")
        if "MISSING_FIXTURE" in reason or "PRECONDITION" in reason:
            signatures.add("PRECONDITION_NOT_MET")
        if "MISSING_OBSERVER" in reason or "ORACLE_NOT_READY" in reason:
            signatures.add("CONTRACT_ORACLE_ACTIVATION_MISSING")
        if "CLEANUP" in reason and (
            "FAIL" in reason or "INCOMPLETE" in reason or "COMPENSATION" in reason
        ):
            signatures.add("CLEANUP_FAILED")
        if "NOT_REVERSIBLE" in reason or "NON_REVERSIBLE" in reason:
            signatures.add("CLEANUP_NOT_REVERSIBLE")
        if "CLEANUP_RECEIPT" in reason:
            signatures.add("CLEANUP_RECEIPT_MISSING")
        if reason == "MULTI_WRITE_AUDIT_INCOMPLETE":
            signatures.add(reason)
        if reason in {
            "CLEANUP_EVIDENCE_MISSING",
            "EVIDENCE_QUALITY_NOT_VALIDATED",
            "BUSINESS_EVIDENCE_NOT_VALIDATED",
            "MISSING_REAL_REPLAY_ASSET",
            "MISSING_CUSTOMER_FACING_HARD_EVIDENCE",
        }:
            signatures.add(
                "EVIDENCE_GATE_INCOMPLETE"
                if reason in {
                    "EVIDENCE_QUALITY_NOT_VALIDATED",
                    "BUSINESS_EVIDENCE_NOT_VALIDATED",
                }
                else "REPLAY_EVIDENCE_MISSING"
                if reason.startswith("MISSING_")
                else reason
            )
    return sorted(signatures)


def _outcome(attempt: dict[str, Any], signatures: list[str]) -> str:
    terminal = _text(attempt.get("terminal_status")).upper()
    reason = _text(attempt.get("reason_code")).upper()
    if terminal == "DELIVERABLE":
        return "customer_deliverable_defect"
    if terminal == "REJECTED" and reason == "ORACLE_NOT_VIOLATED":
        return "valid_success_control"
    if terminal == "REJECTED":
        return "valid_violation_held_back"
    if terminal in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"} or signatures:
        return "harness_or_execution_failure"
    return "unresolved"


def _trace_attempt(
    attempt: dict[str, Any],
    *,
    run_id: str,
    policy_id: str,
    target_id: str,
    project_id: str,
    industry: str,
    evaluation_mode: str,
    campaign_id: str,
) -> dict[str, Any]:
    obligation_id = _required(attempt.get("obligation_id"), "obligation_id")
    compile_stage = _stage(attempt, "compile")
    execution_stage = _stage(attempt, "execution")
    gate_stage = _stage(attempt, "gate")
    signatures = _failure_signatures(attempt)
    source_refs = _list(attempt.get("source_refs"))
    operation_refs = sorted({
        _opaque_ref(value)
        for value in _list(attempt.get("operation_refs"))
        if _text(value)
    })
    return {
        "attempt_id": _stable_id(
            "ATTEMPT",
            run_id,
            obligation_id,
            attempt.get("attempt_fingerprint"),
        ),
        "run_id": run_id,
        "policy_id": policy_id,
        "target_id": target_id,
        "project_id": project_id,
        "industry": industry,
        "evaluation_mode": evaluation_mode,
        "campaign_id": campaign_id,
        "candidate_id": _text(attempt.get("candidate_id")),
        "obligation_id": obligation_id,
        "experiment_id": _text(attempt.get("experiment_id")),
        "execution_id": _text(attempt.get("execution_id")),
        "finding_id": _text(attempt.get("finding_id")),
        "behavior_slice_id": _text(attempt.get("behavior_slice_id")),
        "risk_family": _text(attempt.get("risk_family")) or "unclassified",
        "operation_refs": operation_refs,
        "adapter": _text(attempt.get("adapter")) or "unclassified",
        "source_kinds": _source_kinds(source_refs),
        "source_ref_ids": _source_ref_ids(source_refs),
        "compile_status": _text(compile_stage.get("status")).upper(),
        "compile_reason_code": _text(compile_stage.get("reason_code")),
        "execution_status": _text(execution_stage.get("status")).upper(),
        "execution_reason_code": _text(execution_stage.get("reason_code")),
        "oracle_receipt_id": _opaque_ref(attempt.get("oracle_receipt_id")),
        "oracle_reason_code": _text(attempt.get("oracle_reason_code")),
        "gate_status": _text(gate_stage.get("status")).upper(),
        "gate_reason_code": _text(gate_stage.get("reason_code")),
        "terminal_stage": _text(attempt.get("terminal_stage")),
        "terminal_status": _text(attempt.get("terminal_status")).upper(),
        "reason_code": _text(attempt.get("reason_code")),
        "observation_receipt_ids": [
            _opaque_ref(value)
            for value in _list(attempt.get("observation_receipt_ids"))
            if _text(value)
        ],
        "gate_receipt_id": _opaque_ref(attempt.get("gate_receipt_id")),
        "receipt_refs": {
            _text(key): _opaque_ref(value)
            for key, value in _dict(attempt.get("receipt_refs")).items()
            if _text(key) and _opaque_ref(value)
        },
        "input_fingerprint": _opaque_ref(attempt.get("input_fingerprint")),
        "output_fingerprint": _opaque_ref(attempt.get("output_fingerprint")),
        "elapsed_ms": attempt.get("elapsed_ms"),
        "cost_coverage_status": _text(attempt.get("cost_coverage_status")) or "UNKNOWN",
        "failure_signatures": signatures,
        "outcome": _outcome(attempt, signatures),
    }


def _stage_loss(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for stage in ("compile", "execution", "oracle", "gate", "terminal"):
        if stage == "oracle":
            statuses = ["OBSERVED" if row.get("oracle_receipt_id") else "MISSING" for row in attempts]
            reasons = [row.get("oracle_reason_code") for row in attempts]
        elif stage == "terminal":
            statuses = [row.get("terminal_status") for row in attempts]
            reasons = [row.get("reason_code") for row in attempts]
        else:
            statuses = [row.get(f"{stage}_status") or "NOT_REACHED" for row in attempts]
            reasons = [row.get(f"{stage}_reason_code") for row in attempts]
        reason_counts = Counter(_text(reason) for reason in reasons if _text(reason))
        status_counts = Counter(_text(status) for status in statuses if _text(status))
        result[stage] = {
            "input_count": len(attempts),
            "status_counts": dict(sorted(status_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
        }
    return result


def build_discovery_trace_ledger_v2(
    scan_result: dict[str, Any],
    *,
    run_id: str,
    policy_id: str,
    target_id: str,
    project_id: str,
    industry: str,
    evaluation_mode: str,
) -> dict[str, Any]:
    """Join authoritative attempt receipts into one redacted V2 row per obligation."""

    if not isinstance(scan_result, dict):
        raise DiscoveryTraceError("scan_result must be an object")
    run_id = _required(run_id, "run_id")
    policy_id = _required(policy_id, "policy_id")
    target_id = _required(target_id, "target_id")
    project_id = _required(project_id, "project_id")
    industry = _required(industry, "industry")
    evaluation_mode = _required(evaluation_mode, "evaluation_mode")
    try:
        attempt_ledger = validate_obligation_attempt_ledger(
            _dict(scan_result.get("obligation_attempt_ledger"))
        )
    except ValueError as exc:
        raise DiscoveryTraceError(f"obligation_attempt_ledger_invalid:{exc}") from exc
    if _text(attempt_ledger.get("run_id")) != run_id:
        raise DiscoveryTraceError("obligation_attempt_ledger_run_id_mismatch")
    campaign_id = _required(attempt_ledger.get("campaign_id"), "campaign_id")
    attempts = [
        _trace_attempt(
            _dict(item),
            run_id=run_id,
            policy_id=policy_id,
            target_id=target_id,
            project_id=project_id,
            industry=industry,
            evaluation_mode=evaluation_mode,
            campaign_id=campaign_id,
        )
        for item in _list(attempt_ledger.get("attempts"))
    ]
    if len({row["obligation_id"] for row in attempts}) != len(attempts):
        raise DiscoveryTraceError("duplicate_obligation_attempt")
    formal_projection = _dict(scan_result.get("formal_count_projection"))
    if not isinstance(
        formal_projection.get("delivery_occurrence_finding_ids"), list
    ):
        raise DiscoveryTraceError("delivery_occurrence_finding_ids_missing")
    formal_ids = sorted({
        _text(value)
        for value in formal_projection["delivery_occurrence_finding_ids"]
        if _text(value)
    })
    canonical_ids = sorted({
        _text(value)
        for value in _list(formal_projection.get("canonical_defect_ids"))
        if _text(value)
    })
    deliverable_ids = sorted({
        row["finding_id"]
        for row in attempts
        if row["terminal_status"] == "DELIVERABLE" and row["finding_id"]
    })
    if formal_ids != deliverable_ids:
        raise DiscoveryTraceError("delivery_occurrence_finding_ids_mismatch")
    signature_counts = Counter(
        signature for row in attempts for signature in row["failure_signatures"]
    )
    outcome_counts = Counter(row["outcome"] for row in attempts)
    terminal_counts = Counter(row["terminal_status"] for row in attempts)
    harness_failed = int(terminal_counts.get("HARNESS_FAILED", 0))
    complete = bool(attempt_ledger.get("complete")) and len(attempts) == int(
        attempt_ledger.get("selected_count") or 0
    )
    payload: dict[str, Any] = {
        "schema_version": TRACE_LEDGER_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": run_id,
        "policy_id": policy_id,
        "target_id": target_id,
        "project_id": project_id,
        "industry": industry,
        "evaluation_mode": evaluation_mode,
        "campaign_id": campaign_id,
        "attempt_ledger_fingerprint": _text(attempt_ledger.get("ledger_fingerprint")),
        "attempt_count": len(attempts),
        "trace_count": len(attempts),
        "delivery_occurrence_finding_ids": formal_ids,
        "canonical_defect_ids": canonical_ids,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "terminal_status_counts": dict(sorted(terminal_counts.items())),
        "failure_signature_counts": dict(
            sorted(signature_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "stage_loss": _stage_loss(attempts),
        "pipeline_health": {
            "status": "DEGRADED" if harness_failed or not complete else "OK",
            "execution_status": "completed" if complete else "partial",
            "selected_obligation_count": int(attempt_ledger.get("selected_count") or 0),
            "terminal_obligation_count": len(attempts),
            "harness_failure_count": harness_failed,
        },
        "aggregate_stage_events": {
            "terminal_reason_counts": dict(
                sorted(Counter(row["reason_code"] for row in attempts if row["reason_code"]).items())
            ),
        },
        "redaction_contract": {
            "raw_request_bodies_persisted": False,
            "raw_response_bodies_persisted": False,
            "credentials_persisted": False,
            "ground_truth_persisted": False,
            "target_private_paths_persisted": False,
        },
        "attempts": attempts,
    }
    payload["ledger_fingerprint"] = _fingerprint(payload)
    return validate_trace_ledger(payload)


def build_discovery_trace_ledger(
    scan_result: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility name; runtime semantics are V2 only."""

    return build_discovery_trace_ledger_v2(scan_result, **kwargs)


def validate_trace_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ledger, dict) or ledger.get("schema_version") != TRACE_LEDGER_SCHEMA:
        raise DiscoveryTraceError("trace_ledger_v2_required")
    for field in (
        "run_id",
        "policy_id",
        "target_id",
        "project_id",
        "industry",
        "evaluation_mode",
    ):
        _required(ledger.get(field), field)
    attempts = ledger.get("attempts")
    if not isinstance(attempts, list):
        raise DiscoveryTraceError("trace_ledger_attempts_missing")
    obligation_ids = [_text(_dict(item).get("obligation_id")) for item in attempts]
    if not all(obligation_ids) or len(obligation_ids) != len(set(obligation_ids)):
        raise DiscoveryTraceError("trace_ledger_obligation_identity_invalid")
    if int(ledger.get("attempt_count") or 0) != len(attempts):
        raise DiscoveryTraceError("trace_ledger_attempt_count_mismatch")
    occurrence_ids = ledger.get("delivery_occurrence_finding_ids")
    canonical_ids = ledger.get("canonical_defect_ids")
    if not isinstance(occurrence_ids, list):
        raise DiscoveryTraceError(
            "trace_ledger_delivery_occurrence_finding_ids_missing"
        )
    if not isinstance(canonical_ids, list):
        raise DiscoveryTraceError("trace_ledger_canonical_defect_ids_missing")
    normalized_occurrence_ids = sorted({
        _text(value) for value in occurrence_ids if _text(value)
    })
    normalized_canonical_ids = sorted({
        _text(value) for value in canonical_ids if _text(value)
    })
    if occurrence_ids != normalized_occurrence_ids:
        raise DiscoveryTraceError(
            "trace_ledger_delivery_occurrence_identity_invalid"
        )
    if canonical_ids != normalized_canonical_ids:
        raise DiscoveryTraceError("trace_ledger_canonical_identity_invalid")
    attempt_occurrence_ids = sorted({
        _text(_dict(item).get("finding_id"))
        for item in attempts
        if _text(_dict(item).get("terminal_status")).upper() == "DELIVERABLE"
        and _text(_dict(item).get("finding_id"))
    })
    if normalized_occurrence_ids != attempt_occurrence_ids:
        raise DiscoveryTraceError(
            "trace_ledger_delivery_occurrence_attempt_mismatch"
        )
    redaction = _dict(ledger.get("redaction_contract"))
    for field in (
        "raw_request_bodies_persisted",
        "raw_response_bodies_persisted",
        "credentials_persisted",
        "ground_truth_persisted",
        "target_private_paths_persisted",
    ):
        if redaction.get(field) is not False:
            raise DiscoveryTraceError(f"trace_ledger_redaction_contract_invalid:{field}")
    observed = _text(ledger.get("ledger_fingerprint"))
    expected = _fingerprint({
        key: value for key, value in ledger.items() if key != "ledger_fingerprint"
    })
    if not observed:
        raise DiscoveryTraceError("trace_ledger_fingerprint_missing")
    if observed != expected:
        raise DiscoveryTraceError("trace_ledger_fingerprint_mismatch")
    return dict(ledger)


def migrate_trace_ledger_v1_to_v3(
    v1: dict[str, Any],
    *,
    obligation_map: dict[str, str],
) -> dict[str, Any]:
    """Explicit offline migration; runtime never invokes this automatically."""

    if not isinstance(v1, dict) or v1.get("schema_version") != TRACE_LEDGER_V1_SCHEMA:
        raise DiscoveryTraceError("trace_ledger_v1_required")
    traces = [_dict(item) for item in _list(v1.get("traces"))]
    slice_ids = {_text(item.get("behavior_slice_id")) for item in traces}
    slice_ids.discard("")
    missing = sorted(slice_ids - set(obligation_map))
    if missing:
        raise DiscoveryTraceError(
            "v1_migration_obligation_map_incomplete:" + ",".join(missing)
        )
    attempts: list[dict[str, Any]] = []
    for row in traces:
        slice_id = _text(row.get("behavior_slice_id"))
        obligation_id = _required(obligation_map.get(slice_id), "obligation_id")
        outcome = _text(row.get("outcome"))
        finding_id = _text(row.get("finding_id"))
        if outcome == "customer_deliverable_defect":
            if not finding_id:
                raise DiscoveryTraceError(
                    f"v1_migration_finding_id_missing:{slice_id}"
                )
            terminal_status = "DELIVERABLE"
            reason_code = ""
        elif outcome == "valid_success_control":
            terminal_status = "REJECTED"
            reason_code = "MIGRATED_VALID_SUCCESS"
        elif outcome == "valid_violation_held_back":
            terminal_status = "REJECTED"
            reason_code = "MIGRATED_GATE_REJECTION"
        elif outcome == "unresolved":
            terminal_status = "DEFERRED"
            reason_code = "MIGRATED_UNRESOLVED"
        else:
            terminal_status = "HARNESS_FAILED"
            reason_code = _text(row.get("blocked_reason_code")) or "MIGRATED_HARNESS_FAILURE"
        generation = _dict(row.get("generation"))
        attempts.append({
            "attempt_id": _stable_id("MIGRATED", v1.get("run_id"), obligation_id),
            "run_id": _text(v1.get("run_id")),
            "policy_id": _text(v1.get("policy_id")),
            "target_id": _text(v1.get("target_id")),
            "project_id": _text(v1.get("project_id")),
            "industry": _text(v1.get("industry")),
            "evaluation_mode": _text(v1.get("evaluation_mode")),
            "campaign_id": _text(v1.get("campaign_id")),
            "candidate_id": "",
            "obligation_id": obligation_id,
            "experiment_id": "",
            "execution_id": "",
            "finding_id": finding_id if terminal_status == "DELIVERABLE" else "",
            "behavior_slice_id": slice_id,
            "risk_family": _text(generation.get("family")) or "unclassified",
            "operation_refs": [],
            "adapter": "unclassified",
            "source_kinds": list(_list(generation.get("source_kinds"))),
            "source_ref_ids": [],
            "compile_status": "",
            "compile_reason_code": "",
            "execution_status": "",
            "execution_reason_code": reason_code,
            "oracle_receipt_id": "",
            "oracle_reason_code": "",
            "gate_status": "",
            "gate_reason_code": "",
            "terminal_stage": "migration",
            "terminal_status": terminal_status,
            "reason_code": reason_code,
            "observation_receipt_ids": [],
            "gate_receipt_id": "",
            "receipt_refs": {},
            "input_fingerprint": "",
            "output_fingerprint": "",
            "elapsed_ms": None,
            "cost_coverage_status": "UNKNOWN",
            "failure_signatures": sorted({
                _text(value)
                for value in _list(row.get("failure_signatures"))
                if _text(value)
            }),
            "outcome": outcome or "unresolved",
        })
    redaction = _dict(v1.get("redaction_contract"))
    payload: dict[str, Any] = {
        "schema_version": TRACE_LEDGER_SCHEMA,
        "created_at_utc": _text(v1.get("created_at_utc")),
        "run_id": _required(v1.get("run_id"), "run_id"),
        "policy_id": _required(v1.get("policy_id"), "policy_id"),
        "target_id": _required(v1.get("target_id"), "target_id"),
        "project_id": _required(v1.get("project_id"), "project_id"),
        "industry": _required(v1.get("industry"), "industry"),
        "evaluation_mode": _required(v1.get("evaluation_mode"), "evaluation_mode"),
        "campaign_id": _text(v1.get("campaign_id")),
        "attempt_ledger_fingerprint": "",
        "attempt_count": len(attempts),
        "trace_count": len(attempts),
        "delivery_occurrence_finding_ids": sorted({
            row["finding_id"] for row in attempts if row["finding_id"]
        }),
        "canonical_defect_ids": [],
        "outcome_counts": dict(sorted(Counter(row["outcome"] for row in attempts).items())),
        "terminal_status_counts": dict(
            sorted(Counter(row["terminal_status"] for row in attempts).items())
        ),
        "failure_signature_counts": dict(
            sorted(Counter(
                signature for row in attempts for signature in row["failure_signatures"]
            ).items())
        ),
        "stage_loss": _stage_loss(attempts),
        "pipeline_health": dict(_dict(v1.get("pipeline_health"))),
        "aggregate_stage_events": dict(_dict(v1.get("aggregate_stage_events"))),
        "redaction_contract": {
            "raw_request_bodies_persisted": redaction.get("raw_request_bodies_persisted", False),
            "raw_response_bodies_persisted": redaction.get("raw_response_bodies_persisted", False),
            "credentials_persisted": redaction.get("credentials_persisted", False),
            "ground_truth_persisted": redaction.get("ground_truth_persisted", False),
            "target_private_paths_persisted": False,
        },
        "attempts": attempts,
        "migration": {
            "source_schema": TRACE_LEDGER_V1_SCHEMA,
            "explicit": True,
        },
    }
    payload["ledger_fingerprint"] = _fingerprint(payload)
    return validate_trace_ledger(payload)


def persist_trace_ledger(ledger: dict[str, Any], output_root: Path | str) -> Path:
    value = validate_trace_ledger(ledger)
    redacted, _redaction_receipt = redact_and_validate(value)
    if redacted != value:
        raise DiscoveryTraceError(
            "trace ledger still contained redactable material before persistence"
        )
    safe = re.compile(r"[^A-Za-z0-9_.-]+")
    target_identity = _required(value.get("target_id"), "target_id")
    target = safe.sub("_", target_identity)
    run_id = safe.sub("_", _required(value.get("run_id"), "run_id"))
    path = Path(output_root) / target / f"{run_id}.trace-ledger.json"
    if os.name == "nt" and len(str(path)) > 240:
        # Windows legacy file APIs reject paths near MAX_PATH with a misleading
        # FileNotFoundError even when the parent directory exists.  Keep the
        # full target identity in the redacted ledger, but use a deterministic
        # bounded directory component for the filesystem projection.
        target = "target_" + hashlib.sha256(
            target_identity.encode("utf-8")
        ).hexdigest()[:32]
        path = Path(output_root) / target / f"{run_id}.trace-ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(redacted, ensure_ascii=False, indent=2)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != json.loads(payload):
            raise DiscoveryTraceError(
                f"immutable trace ledger already exists with different content: {path}"
            )
        return path
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)
    return path
