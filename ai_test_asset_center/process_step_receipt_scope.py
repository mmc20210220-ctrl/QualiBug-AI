"""Exact per-step scope binding for observation, oracle, and cleanup receipts.

A receipt can satisfy one process-step evidence gate only when it carries one
explicit step identity that resolves to a recorded ledger step. Missing scope,
multi-step scope, unknown scope, and reused receipt ids remain unbound. There is
no fallback to all executed steps.
"""
from __future__ import annotations

from typing import Any

from .process_step_execution import ProcessStepLedger

PROCESS_STEP_RECEIPT_SCOPE_BINDING_SCHEMA = (
    "qualibug.process-step-receipt-scope-binding.v1"
)

_STEP_ID_FIELDS = (
    "step_id",
    "source_step_id",
    "subject_step_id",
    "receipt_step_id",
)
_STEP_ID_LIST_FIELDS = (
    "step_ids",
    "source_step_ids",
    "subject_step_ids",
    "receipt_step_ids",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in out:
            out.append(text)
    return out


def receipt_id(receipt: dict[str, Any]) -> str:
    value = _dict(receipt)
    payload = _dict(value.get("payload"))
    return _text(
        value.get("receipt_id")
        or value.get("id")
        or value.get("verification_id")
        or payload.get("receipt_id")
        or payload.get("id")
        or payload.get("verification_id")
    )


def _scope_sources(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    value = _dict(receipt)
    payload = _dict(value.get("payload"))
    sources = [
        value,
        payload,
        _dict(value.get("evidence")),
        _dict(payload.get("evidence")),
        _dict(value.get("verdict")),
        _dict(payload.get("verdict")),
        _dict(value.get("cleanup_result")),
        _dict(payload.get("cleanup_result")),
    ]
    return [source for source in sources if source]


def extract_receipt_step_scope(
    receipt: dict[str, Any],
    *,
    known_step_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve one explicit step identity without inferring from execution sets."""

    scalar_ids: list[str] = []
    list_ids: list[str] = []
    for source in _scope_sources(receipt):
        scalar_ids.extend(_text(source.get(field)) for field in _STEP_ID_FIELDS)
        for field in _STEP_ID_LIST_FIELDS:
            list_ids.extend(_text(item) for item in _list(source.get(field)))

    scalar = _unique(scalar_ids)
    listed = _unique(list_ids)
    declared = _unique(scalar + listed)
    known = set(_unique(list(known_step_ids or [])))

    if listed or len(declared) > 1:
        status = "MULTI_STEP_SCOPE_FORBIDDEN"
        step_id = ""
    elif not declared:
        status = "STEP_SCOPE_MISSING"
        step_id = ""
    else:
        step_id = declared[0]
        status = "EXACT"
        if known and step_id not in known:
            status = "STEP_SCOPE_UNKNOWN"

    return {
        "receipt_id": receipt_id(receipt),
        "status": status,
        "step_id": step_id,
        "declared_step_ids": declared,
        "explicit_scalar_step_ids": scalar,
        "explicit_list_step_ids": listed,
    }


def _deduplicate_receipts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        value = _dict(row)
        if not value:
            continue
        rid = receipt_id(value)
        key = rid or f"anonymous:{len(out)}"
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _rows(observations: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in keys:
        value = observations.get(key)
        if isinstance(value, dict):
            out.append(value)
        else:
            out.extend(row for row in _list(value) if isinstance(row, dict))
    return _deduplicate_receipts(out)


def _bind_group(
    *,
    ledger: ProcessStepLedger,
    rows: list[dict[str, Any]],
    field: str,
    evidence_kind: str,
) -> dict[str, Any]:
    known = list(ledger.recorded_step_ids())
    bound: list[dict[str, str]] = []
    unbound: list[dict[str, Any]] = []
    duplicate_receipt_ids: list[str] = []
    bound_receipt_owner: dict[str, str] = {}

    for row in rows:
        scope = extract_receipt_step_scope(row, known_step_ids=known)
        rid = _text(scope.get("receipt_id"))
        step_id = _text(scope.get("step_id"))
        if not rid:
            unbound.append(
                {
                    **scope,
                    "evidence_kind": evidence_kind,
                    "status": "RECEIPT_ID_MISSING",
                }
            )
            continue
        previous_owner = bound_receipt_owner.get(rid)
        if previous_owner and previous_owner != step_id:
            duplicate_receipt_ids.append(rid)
            unbound.append(
                {
                    **scope,
                    "evidence_kind": evidence_kind,
                    "status": "RECEIPT_REUSED_ACROSS_STEPS",
                    "previous_step_id": previous_owner,
                }
            )
            continue
        if scope.get("status") != "EXACT":
            unbound.append({**scope, "evidence_kind": evidence_kind})
            continue
        if not ledger.append_scoped_receipt_ref(
            step_id=step_id,
            receipt_step_id=step_id,
            field=field,
            receipt_id=rid,
        ):
            unbound.append(
                {
                    **scope,
                    "evidence_kind": evidence_kind,
                    "status": "LEDGER_SCOPE_BINDING_REJECTED",
                }
            )
            continue
        bound_receipt_owner[rid] = step_id
        bound.append(
            {
                "receipt_id": rid,
                "step_id": step_id,
                "evidence_kind": evidence_kind,
            }
        )

    return {
        "evidence_kind": evidence_kind,
        "bound": bound,
        "unbound": unbound,
        "duplicate_receipt_ids": sorted(set(duplicate_receipt_ids)),
        "complete": not unbound and not duplicate_receipt_ids,
    }


def synchronize_scoped_receipts_from_observations(
    ledger: ProcessStepLedger,
    observations: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind only exact-scoped evidence receipts already present in observations."""

    source = observations if isinstance(observations, dict) else {}
    observation_rows = _rows(
        source,
        (
            "observer_receipts",
            "observation_receipts",
            "process_step_observation_receipts",
        ),
    )
    oracle_rows = _rows(
        source,
        (
            "process_step_oracle_receipts",
            "oracle_invocation_receipts",
            "oracle_trace_receipts",
            "oracle_receipts",
            "assertion_receipts",
            "oracle_verdict",
        ),
    )
    cleanup_rows = _rows(
        source,
        (
            "cleanup_execution_receipts",
            "cleanup_execution_receipt",
            "cleanup_verification_receipts",
            "cleanup_verification",
            "cleanup_equivalence_receipt",
        ),
    )

    observation_audit = _bind_group(
        ledger=ledger,
        rows=observation_rows,
        field="observation_receipt_ids",
        evidence_kind="observation",
    )
    oracle_audit = _bind_group(
        ledger=ledger,
        rows=oracle_rows,
        field="oracle_receipt_ids",
        evidence_kind="oracle",
    )
    cleanup_audit = _bind_group(
        ledger=ledger,
        rows=cleanup_rows,
        field="cleanup_receipt_ids",
        evidence_kind="cleanup",
    )
    unbound = [
        *observation_audit["unbound"],
        *oracle_audit["unbound"],
        *cleanup_audit["unbound"],
    ]
    audit = {
        "schema_version": PROCESS_STEP_RECEIPT_SCOPE_BINDING_SCHEMA,
        "ledger_id": ledger.ledger_id,
        "known_step_ids": list(ledger.recorded_step_ids()),
        "observation": observation_audit,
        "oracle": oracle_audit,
        "cleanup": cleanup_audit,
        "unbound_receipts": unbound,
        "complete": (
            observation_audit["complete"]
            and oracle_audit["complete"]
            and cleanup_audit["complete"]
        ),
        "broadcast_fallback_forbidden": True,
    }
    if isinstance(observations, dict):
        observations["process_step_receipt_scope_binding"] = audit
        observations["unbound_process_step_receipts"] = unbound
    return audit
