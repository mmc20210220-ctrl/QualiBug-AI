"""Independent exact-scope audit for process-step evidence receipts.

The live ledger binds evidence. This module verifies the sealed result from the
Execution Receipt Bundle. Exact observation, oracle and cleanup receipts must
name one recorded step and be referenced by that same step. Graph aggregate
cleanup receipts are validated as aggregate sets and are never mistaken for
single-step evidence. Typed HTTP observations are also checked against the
Observer subject set sealed by the Compile Receipt.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .process_step_receipt_scope import extract_receipt_step_scope

PROCESS_STEP_EVIDENCE_SCOPE_AUDIT_SCHEMA = (
    "qualibug.process-step-evidence-scope-audit.v1"
)

COMPILE_RECEIPT_TYPE = "qualibug.compile-receipt.v1"
OBSERVATION_RECEIPT_TYPE = "qualibug.observation-receipt.v1"
ORACLE_INVOCATION_RECEIPT_TYPE = "qualibug.oracle-invocation-receipt.v1"
ORACLE_TRACE_RECEIPT_TYPE = "qualibug.oracle-trace-receipt.v1"
CLEANUP_EXECUTION_RECEIPT_TYPE = "qualibug.cleanup-execution-receipt.v1"
CLEANUP_VERIFICATION_RECEIPT_TYPE = "qualibug.cleanup-verification-receipt.v1"
OBSERVER_SUBJECT_BINDING_SCHEMA = "qualibug.observer-subject-binding.v1"
TYPED_OBSERVER_RECEIPT_SCHEMA = "qualibug.observer-receipt.v1"
PROCESS_STEP_ORACLE_INVOCATION_SCHEMA = (
    "qualibug.process-step-oracle-invocation.v1"
)
TYPED_CLEANUP_EXECUTION_RECEIPT_SCHEMA = (
    "qualibug.cleanup-execution-receipt.v1"
)
TYPED_CLEANUP_EQUIVALENCE_RECEIPT_SCHEMA = (
    "qualibug.cleanup-equivalence-receipt.v1"
)
GRAPH_CLEANUP_EXECUTION_SET_SCHEMA = (
    "qualibug.process-graph-cleanup-execution-set.v1"
)
GRAPH_CLEANUP_EQUIVALENCE_SCHEMA = (
    "qualibug.process-graph-cleanup-equivalence-receipt.v1"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ids(value: Any) -> list[str]:
    out: list[str] = []
    for raw in _list(value):
        text = _text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _payload(envelope: dict[str, Any]) -> dict[str, Any]:
    return _dict(_dict(envelope).get("payload"))


def _payload_schema(envelope: dict[str, Any]) -> str:
    return _text(_payload(envelope).get("schema_version"))


def _discover(
    receipts_by_id: dict[str, dict[str, Any]],
    receipt_types: set[str],
) -> list[str]:
    return sorted(
        receipt_id
        for receipt_id, envelope in receipts_by_id.items()
        if _text(_dict(envelope).get("receipt_type")) in receipt_types
    )


def _owners_by_receipt(
    process_step_payloads: list[dict[str, Any]],
    field: str,
) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for payload in process_step_payloads:
        step_id = _text(payload.get("step_id"))
        if not step_id:
            continue
        for rid in _ids(payload.get(field)):
            owners.setdefault(rid, [])
            if step_id not in owners[rid]:
                owners[rid].append(step_id)
    return owners


def _filter_owners(
    owners: dict[str, list[str]],
    allowed_receipt_ids: list[str],
) -> dict[str, list[str]]:
    allowed = set(allowed_receipt_ids)
    return {
        rid: list(step_ids)
        for rid, step_ids in owners.items()
        if rid in allowed
    }


def _cleanup_execution_status_valid(payload: dict[str, Any]) -> bool:
    status = _text(payload.get("status")).upper()
    status_code = _safe_int(payload.get("status_code"))
    if status == "ACCEPTED":
        return bool(
            payload.get("attempted") is True
            and payload.get("transport_reached") is True
            and payload.get("succeeded") is True
            and 200 <= status_code < 300
        )
    if status == "NOT_REQUIRED":
        return bool(
            payload.get("attempted") is False
            and payload.get("transport_reached") is False
            and payload.get("succeeded") is False
            and status_code == 0
            and _text(payload.get("reason_code")).upper()
            in {"CLEANUP_NOT_REQUIRED", "NO_CLEANUP_PLAN"}
        )
    return False


def _cleanup_verification_status_valid(payload: dict[str, Any]) -> bool:
    status = _text(payload.get("equivalence_status")).upper()
    if status == "EQUIVALENT":
        return True
    return bool(
        status == "NOT_APPLICABLE"
        and _text(payload.get("reason_code")).upper()
        == "CLEANUP_NOT_REQUIRED"
    )


def _graph_cleanup_execution_set_valid(payload: dict[str, Any]) -> bool:
    write_ids = _ids(payload.get("write_step_ids"))
    node_receipts = _dict(
        payload.get("step_cleanup_execution_receipts_by_id")
    )
    if (
        _text(payload.get("schema_version"))
        != GRAPH_CLEANUP_EXECUTION_SET_SCHEMA
        or not write_ids
        or len(write_ids) != len(_list(payload.get("write_step_ids")))
        or set(node_receipts) != set(write_ids)
        or _text(payload.get("status")).upper() != "ACCEPTED"
        or payload.get("succeeded") is not True
        or not 200 <= _safe_int(payload.get("status_code")) < 300
    ):
        return False

    expected_receipt_ids: list[str] = []
    for step_id in write_ids:
        row = _dict(node_receipts.get(step_id))
        if (
            _text(row.get("source_step_id")) != step_id
            or not _text(row.get("receipt_id"))
            or _text(row.get("schema_version"))
            != TYPED_CLEANUP_EXECUTION_RECEIPT_SCHEMA
            or not _cleanup_execution_status_valid(row)
        ):
            return False
        expected_receipt_ids.append(_text(row.get("receipt_id")))
    return _ids(payload.get("source_receipt_ids")) == expected_receipt_ids


def _graph_cleanup_equivalence_valid(payload: dict[str, Any]) -> bool:
    write_ids = _ids(payload.get("write_step_ids"))
    node_receipts = _dict(payload.get("step_equivalence_receipts_by_id"))
    if (
        _text(payload.get("schema_version"))
        != GRAPH_CLEANUP_EQUIVALENCE_SCHEMA
        or not write_ids
        or len(write_ids) != len(_list(payload.get("write_step_ids")))
        or set(node_receipts) != set(write_ids)
        or _text(payload.get("equivalence_status")).upper() != "EQUIVALENT"
        or _safe_int(payload.get("not_equivalent_step_count")) != 0
        or _safe_int(payload.get("indeterminate_step_count")) != 0
    ):
        return False

    expected_receipt_ids: list[str] = []
    for step_id in write_ids:
        row = _dict(node_receipts.get(step_id))
        if (
            _text(row.get("source_step_id")) != step_id
            or not _text(row.get("receipt_id"))
            or _text(row.get("schema_version"))
            != TYPED_CLEANUP_EQUIVALENCE_RECEIPT_SCHEMA
            or not _cleanup_verification_status_valid(row)
        ):
            return False
        expected_receipt_ids.append(_text(row.get("receipt_id")))
    return _ids(payload.get("step_equivalence_receipt_ids")) == expected_receipt_ids


def _semantic_status_valid(
    envelope: dict[str, Any],
    *,
    evidence_kind: str,
) -> bool:
    """Validate typed semantic status without constraining generic envelopes."""
    payload = _payload(envelope)
    schema = _text(payload.get("schema_version"))
    if (
        evidence_kind == "observation"
        and schema == TYPED_OBSERVER_RECEIPT_SCHEMA
    ):
        return _text(payload.get("status")).upper() == "OBSERVED"
    if (
        evidence_kind == "oracle_invocation"
        and schema == PROCESS_STEP_ORACLE_INVOCATION_SCHEMA
    ):
        return bool(
            payload.get("evaluated") is True
            and _text(payload.get("oracle_status")).upper()
            in {"PROPERTY_HELD", "VIOLATION"}
            and _text(payload.get("source_oracle_receipt_id"))
        )
    if (
        evidence_kind == "cleanup_execution"
        and schema == TYPED_CLEANUP_EXECUTION_RECEIPT_SCHEMA
    ):
        return _cleanup_execution_status_valid(payload)
    if (
        evidence_kind == "cleanup_verification"
        and schema == TYPED_CLEANUP_EQUIVALENCE_RECEIPT_SCHEMA
    ):
        return _cleanup_verification_status_valid(payload)
    return True


def _audit_group(
    *,
    receipts_by_id: dict[str, dict[str, Any]],
    discovered_receipt_ids: list[str],
    owners: dict[str, list[str]],
    known_step_ids: list[str],
    evidence_kind: str,
) -> dict[str, Any]:
    unscoped: list[str] = []
    multi_step: list[str] = []
    unknown_step: list[str] = []
    unreferenced: list[str] = []
    broadcast: list[str] = []
    owner_mismatch: list[str] = []
    invalid_semantic_status: list[str] = []
    exact_owner_by_receipt: dict[str, str] = {}

    for rid in discovered_receipt_ids:
        envelope = _dict(receipts_by_id.get(rid))
        scope = extract_receipt_step_scope(
            envelope,
            known_step_ids=known_step_ids,
        )
        status = _text(scope.get("status"))
        step_id = _text(scope.get("step_id"))
        receipt_owners = sorted(set(owners.get(rid, [])))
        semantic_valid = _semantic_status_valid(
            envelope,
            evidence_kind=evidence_kind,
        )
        if status == "STEP_SCOPE_MISSING":
            unscoped.append(rid)
        elif status == "MULTI_STEP_SCOPE_FORBIDDEN":
            multi_step.append(rid)
        elif status == "STEP_SCOPE_UNKNOWN":
            unknown_step.append(rid)
        elif status == "EXACT" and semantic_valid:
            exact_owner_by_receipt[rid] = step_id
        elif status == "EXACT":
            invalid_semantic_status.append(rid)

        if not receipt_owners:
            unreferenced.append(rid)
        elif len(receipt_owners) > 1:
            broadcast.append(rid)
        elif status == "EXACT" and receipt_owners[0] != step_id:
            owner_mismatch.append(rid)

    complete = not any(
        (
            unscoped,
            multi_step,
            unknown_step,
            unreferenced,
            broadcast,
            owner_mismatch,
            invalid_semantic_status,
        )
    )
    return {
        "evidence_kind": evidence_kind,
        "complete": complete,
        "discovered_receipt_ids": discovered_receipt_ids,
        "owners_by_receipt": owners,
        "exact_owner_by_receipt": exact_owner_by_receipt,
        "unscoped_receipt_ids": unscoped,
        "multi_step_receipt_ids": multi_step,
        "unknown_step_receipt_ids": unknown_step,
        "unreferenced_receipt_ids": unreferenced,
        "broadcast_receipt_ids": broadcast,
        "owner_mismatch_receipt_ids": owner_mismatch,
        "invalid_semantic_status_receipt_ids": invalid_semantic_status,
    }


def _covered_steps(group: dict[str, Any]) -> set[str]:
    return {
        _text(step_id)
        for step_id in _dict(group.get("exact_owner_by_receipt")).values()
        if _text(step_id)
    }


def _audit_compiled_observer_subjects(
    *,
    receipts_by_id: dict[str, dict[str, Any]],
    observation_receipt_ids: list[str],
    observation_audit: dict[str, Any],
    known_step_ids: list[str],
) -> dict[str, Any]:
    compile_ids = _discover(receipts_by_id, {COMPILE_RECEIPT_TYPE})
    typed_http_receipt_ids = [
        rid
        for rid in observation_receipt_ids
        if (
            _payload_schema(_dict(receipts_by_id.get(rid)))
            == TYPED_OBSERVER_RECEIPT_SCHEMA
            and _text(
                _payload(_dict(receipts_by_id.get(rid))).get("observer_id")
            )
            == "http_response"
        )
    ]
    exact_owners = _dict(observation_audit.get("exact_owner_by_receipt"))
    actual_http_step_ids = sorted(
        {
            _text(exact_owners.get(rid))
            for rid in typed_http_receipt_ids
            if _text(exact_owners.get(rid))
        }
    )

    binding_receipt: dict[str, Any] = {}
    compile_receipt_id = ""
    if len(compile_ids) == 1:
        compile_receipt_id = compile_ids[0]
        binding_receipt = _dict(
            _payload(_dict(receipts_by_id.get(compile_receipt_id))).get(
                "observer_subject_binding_receipt"
            )
        )

    declared = bool(binding_receipt)
    stored_hash = _text(binding_receipt.get("binding_hash"))
    recomputed_hash = ""
    if declared:
        recomputed_hash = _stable_hash(
            {
                key: value
                for key, value in binding_receipt.items()
                if key != "binding_hash"
            }
        )

    bindings = [
        dict(row)
        for row in _list(binding_receipt.get("bindings"))
        if isinstance(row, dict)
    ]
    http_bindings = [
        row
        for row in bindings
        if _text(row.get("observer_id")) == "http_response"
    ]
    expected_http_step_ids = (
        _ids(http_bindings[0].get("subject_step_ids"))
        if len(http_bindings) == 1
        else []
    )
    missing_http_step_ids = sorted(
        set(expected_http_step_ids) - set(actual_http_step_ids)
    )
    unexpected_http_step_ids = sorted(
        set(actual_http_step_ids) - set(expected_http_step_ids)
    )
    unknown_declared_step_ids = sorted(
        set(expected_http_step_ids) - set(known_step_ids)
    )

    declaration_valid = bool(
        declared
        and len(compile_ids) == 1
        and _text(binding_receipt.get("schema_version"))
        == OBSERVER_SUBJECT_BINDING_SCHEMA
        and binding_receipt.get("complete") is True
        and _safe_int(binding_receipt.get("binding_count")) == len(bindings)
        and stored_hash
        and stored_hash == recomputed_hash
        and len(http_bindings) == 1
        and _text(http_bindings[0].get("scope_mode")) == "per_plan_step"
        and expected_http_step_ids
        and not unknown_declared_step_ids
    )
    required = bool(typed_http_receipt_ids or declared)
    complete = bool(
        (not required)
        or (
            declaration_valid
            and not missing_http_step_ids
            and not unexpected_http_step_ids
        )
    )
    return {
        "schema_version": OBSERVER_SUBJECT_BINDING_SCHEMA,
        "complete": complete,
        "required": required,
        "declared": declared,
        "declaration_valid": declaration_valid,
        "compile_receipt_ids": compile_ids,
        "compile_receipt_id": compile_receipt_id,
        "stored_binding_hash": stored_hash,
        "recomputed_binding_hash": recomputed_hash,
        "typed_http_observation_receipt_ids": typed_http_receipt_ids,
        "expected_http_step_ids": expected_http_step_ids,
        "actual_http_step_ids": actual_http_step_ids,
        "missing_http_step_ids": missing_http_step_ids,
        "unexpected_http_step_ids": unexpected_http_step_ids,
        "unknown_declared_step_ids": unknown_declared_step_ids,
        "compile_subject_authority_enforced": required,
    }


def audit_process_step_evidence_scope(
    *,
    receipts_by_id: dict[str, dict[str, Any]],
    process_step_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify compiled Observer scope, aggregate cleanup and exact lineage."""

    payloads = [
        dict(_dict(row))
        for row in process_step_payloads
        if isinstance(row, dict)
    ]
    known_step_ids = [
        _text(row.get("step_id"))
        for row in payloads
        if _text(row.get("step_id"))
    ]
    required_executed_step_ids = sorted(
        {
            _text(row.get("step_id"))
            for row in payloads
            if row.get("required_step") is True
            and row.get("response_received") is True
            and _text(
                row.get("final_step_status") or row.get("final_status")
            ).upper()
            == "EXECUTED"
            and _text(row.get("step_id"))
        }
    )
    cleanup_required_step_ids = sorted(
        {
            _text(row.get("step_id"))
            for row in payloads
            if _text(row.get("step_id"))
            and row.get("operation_accepted") is True
            and (
                row.get("mutation_occurred") is True
                or bool(_text(row.get("cleanup_contract_id")))
            )
        }
    )

    observation_ids = _discover(
        receipts_by_id,
        {OBSERVATION_RECEIPT_TYPE},
    )
    oracle_invocation_ids = _discover(
        receipts_by_id,
        {ORACLE_INVOCATION_RECEIPT_TYPE},
    )
    oracle_trace_ids = _discover(
        receipts_by_id,
        {ORACLE_TRACE_RECEIPT_TYPE},
    )
    all_cleanup_execution_ids = _discover(
        receipts_by_id,
        {CLEANUP_EXECUTION_RECEIPT_TYPE},
    )
    all_cleanup_verification_ids = _discover(
        receipts_by_id,
        {CLEANUP_VERIFICATION_RECEIPT_TYPE},
    )
    aggregate_cleanup_execution_ids = [
        rid
        for rid in all_cleanup_execution_ids
        if _payload_schema(_dict(receipts_by_id.get(rid)))
        == GRAPH_CLEANUP_EXECUTION_SET_SCHEMA
    ]
    aggregate_cleanup_verification_ids = [
        rid
        for rid in all_cleanup_verification_ids
        if _payload_schema(_dict(receipts_by_id.get(rid)))
        == GRAPH_CLEANUP_EQUIVALENCE_SCHEMA
    ]
    cleanup_execution_ids = sorted(
        set(all_cleanup_execution_ids)
        - set(aggregate_cleanup_execution_ids)
    )
    cleanup_verification_ids = sorted(
        set(all_cleanup_verification_ids)
        - set(aggregate_cleanup_verification_ids)
    )

    invalid_aggregate_cleanup_execution_ids = [
        rid
        for rid in aggregate_cleanup_execution_ids
        if not _graph_cleanup_execution_set_valid(
            _payload(_dict(receipts_by_id.get(rid)))
        )
    ]
    invalid_aggregate_cleanup_verification_ids = [
        rid
        for rid in aggregate_cleanup_verification_ids
        if not _graph_cleanup_equivalence_valid(
            _payload(_dict(receipts_by_id.get(rid)))
        )
    ]

    observation_owners_all = _owners_by_receipt(
        payloads,
        "scoped_observation_receipt_ids",
    )
    oracle_owners_all = _owners_by_receipt(
        payloads,
        "scoped_oracle_receipt_ids",
    )
    cleanup_owners_all = _owners_by_receipt(
        payloads,
        "scoped_cleanup_receipt_ids",
    )

    observation = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=observation_ids,
        owners=_filter_owners(
            observation_owners_all,
            observation_ids,
        ),
        known_step_ids=known_step_ids,
        evidence_kind="observation",
    )
    observer_subject_binding = _audit_compiled_observer_subjects(
        receipts_by_id=receipts_by_id,
        observation_receipt_ids=observation_ids,
        observation_audit=observation,
        known_step_ids=known_step_ids,
    )
    oracle_invocation = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=oracle_invocation_ids,
        owners=_filter_owners(
            oracle_owners_all,
            oracle_invocation_ids,
        ),
        known_step_ids=known_step_ids,
        evidence_kind="oracle_invocation",
    )
    oracle_trace = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=oracle_trace_ids,
        owners=_filter_owners(
            oracle_owners_all,
            oracle_trace_ids,
        ),
        known_step_ids=known_step_ids,
        evidence_kind="oracle_trace",
    )
    cleanup_execution = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=cleanup_execution_ids,
        owners=_filter_owners(
            cleanup_owners_all,
            cleanup_execution_ids,
        ),
        known_step_ids=known_step_ids,
        evidence_kind="cleanup_execution",
    )
    cleanup_verification = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=cleanup_verification_ids,
        owners=_filter_owners(
            cleanup_owners_all,
            cleanup_verification_ids,
        ),
        known_step_ids=known_step_ids,
        evidence_kind="cleanup_verification",
    )

    observation_unknown_refs = sorted(
        set(observation_owners_all) - set(observation_ids)
    )
    oracle_unknown_refs = sorted(
        set(oracle_owners_all)
        - set(oracle_invocation_ids)
        - set(oracle_trace_ids)
    )
    cleanup_unknown_refs = sorted(
        set(cleanup_owners_all)
        - set(cleanup_execution_ids)
        - set(cleanup_verification_ids)
    )

    required = set(required_executed_step_ids)
    cleanup_required = set(cleanup_required_step_ids)
    missing_observation_step_ids = sorted(
        required - _covered_steps(observation)
    )
    missing_oracle_step_ids = sorted(
        required - _covered_steps(oracle_invocation)
    )
    missing_cleanup_execution_step_ids = sorted(
        cleanup_required - _covered_steps(cleanup_execution)
    )
    missing_cleanup_verification_step_ids = sorted(
        cleanup_required - _covered_steps(cleanup_verification)
    )

    group_audits = (
        observation,
        oracle_invocation,
        oracle_trace,
        cleanup_execution,
        cleanup_verification,
    )
    broadcast_receipt_ids = sorted(
        {
            rid
            for group in group_audits
            for rid in _ids(group.get("broadcast_receipt_ids"))
        }
    )
    unbound_receipt_ids = sorted(
        {
            *observation_unknown_refs,
            *oracle_unknown_refs,
            *cleanup_unknown_refs,
            *(
                rid
                for group in group_audits
                for field in (
                    "unscoped_receipt_ids",
                    "multi_step_receipt_ids",
                    "unknown_step_receipt_ids",
                    "unreferenced_receipt_ids",
                    "owner_mismatch_receipt_ids",
                )
                for rid in _ids(group.get(field))
            ),
        }
    )
    invalid_semantic_status_receipt_ids = sorted(
        {
            *invalid_aggregate_cleanup_execution_ids,
            *invalid_aggregate_cleanup_verification_ids,
            *(
                rid
                for group in group_audits
                for rid in _ids(
                    group.get("invalid_semantic_status_receipt_ids")
                )
            ),
        }
    )

    validation_errors: list[str] = []
    set_mismatch_fields: list[str] = []
    if broadcast_receipt_ids:
        validation_errors.append(
            "process_step_receipt_scope_broadcast"
        )
        set_mismatch_fields.append("receipt_scope_broadcast")
    if unbound_receipt_ids:
        validation_errors.append(
            "process_step_receipt_scope_unbound"
        )
        set_mismatch_fields.append("receipt_scope_unbound")
    if invalid_semantic_status_receipt_ids:
        validation_errors.append(
            "process_step_evidence_status_invalid"
        )
        set_mismatch_fields.append("evidence_status")
    if not observer_subject_binding.get("complete", True):
        validation_errors.append(
            "compiled_observer_subject_scope_mismatch"
        )
        set_mismatch_fields.append("observer_subject_scope")
    if missing_observation_step_ids:
        validation_errors.append(
            "process_step_observation_scope_incomplete"
        )
        set_mismatch_fields.append("observation_scope")
    if missing_oracle_step_ids:
        validation_errors.append(
            "process_step_oracle_scope_incomplete"
        )
        set_mismatch_fields.append("oracle_scope")
    if missing_cleanup_execution_step_ids:
        validation_errors.append(
            "process_step_cleanup_execution_scope_incomplete"
        )
        set_mismatch_fields.append("cleanup_execution_scope")
    if missing_cleanup_verification_step_ids:
        validation_errors.append(
            "process_step_cleanup_verification_scope_incomplete"
        )
        set_mismatch_fields.append("cleanup_verification_scope")

    complete = not validation_errors
    return {
        "schema_version": PROCESS_STEP_EVIDENCE_SCOPE_AUDIT_SCHEMA,
        "complete": complete,
        "known_step_ids": known_step_ids,
        "required_executed_step_ids": required_executed_step_ids,
        "cleanup_required_step_ids": cleanup_required_step_ids,
        "observation": observation,
        "observer_subject_binding": observer_subject_binding,
        "oracle_invocation": oracle_invocation,
        "oracle_trace": oracle_trace,
        "cleanup_execution": cleanup_execution,
        "cleanup_verification": cleanup_verification,
        "aggregate_cleanup_execution_receipt_ids": (
            aggregate_cleanup_execution_ids
        ),
        "aggregate_cleanup_verification_receipt_ids": (
            aggregate_cleanup_verification_ids
        ),
        "invalid_aggregate_cleanup_execution_receipt_ids": (
            invalid_aggregate_cleanup_execution_ids
        ),
        "invalid_aggregate_cleanup_verification_receipt_ids": (
            invalid_aggregate_cleanup_verification_ids
        ),
        "aggregate_cleanup_receipts_excluded_from_step_scope": True,
        "observation_unknown_reference_ids": observation_unknown_refs,
        "oracle_unknown_reference_ids": oracle_unknown_refs,
        "cleanup_unknown_reference_ids": cleanup_unknown_refs,
        "missing_observation_step_ids": missing_observation_step_ids,
        "missing_oracle_step_ids": missing_oracle_step_ids,
        "missing_cleanup_execution_step_ids": (
            missing_cleanup_execution_step_ids
        ),
        "missing_cleanup_verification_step_ids": (
            missing_cleanup_verification_step_ids
        ),
        "broadcast_receipt_ids": broadcast_receipt_ids,
        "unbound_receipt_ids": unbound_receipt_ids,
        "invalid_semantic_status_receipt_ids": (
            invalid_semantic_status_receipt_ids
        ),
        "validation_errors": validation_errors,
        "set_mismatch_fields": set_mismatch_fields,
        "broadcast_fallback_forbidden": True,
    }
