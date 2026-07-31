"""Require a genuine governance transport receipt for operation attribution.

The existing causality runtime verifies the materialized request body and response.
This wrapper closes the fallback where a request-body fingerprint could be exposed
under the name ``transport_receipt_id``. Only the governance receipt emitted by the
actual treatment transport is accepted. The same runtime function is tightened in
place; no second plan executor is created.
"""
from __future__ import annotations

import functools
from typing import Any

from .operation_causality_receipt_integrity import (
    seal_operation_causality_transport_receipt,
)

_INSTALL_MARKER = "__qualibug_operation_causality_transport_authority_v1__"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_status_code(value: Any) -> bool:
    try:
        return int(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _governance_receipt_ids(result: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    output: dict[tuple[str, str], list[str]] = {}
    for raw in _list(result.get("steps")):
        if not isinstance(raw, dict):
            continue
        row = _dict(raw)
        if _text(row.get("phase")).lower() != "treatment":
            continue
        key = (
            _text(row.get("operation_ref")),
            _text(row.get("step_id") or row.get("subject_id")),
        )
        if not all(key):
            continue
        receipt_id = _text(_dict(row.get("governance_receipt")).get("receipt_id"))
        output.setdefault(key, []).append(receipt_id)
    return output


def _tighten_receipt(
    row: dict[str, Any],
    *,
    candidates: list[str],
) -> dict[str, Any]:
    nonempty = [value for value in candidates if _text(value)]
    exact_id = nonempty[0] if len(candidates) == 1 and len(nonempty) == 1 else ""
    claimed_id = _text(row.get("transport_receipt_id"))
    claimed_attributed = _text(row.get("status")) == "ATTRIBUTED"
    authority_valid = bool(exact_id and claimed_id == exact_id)
    surrogate_present = bool(claimed_id and claimed_id != exact_id)
    changed = claimed_id != exact_id

    # Preserve a more fundamental earlier refusal, such as ambiguous treatment
    # selection, when it never claimed a transport receipt. Override only a false
    # ATTRIBUTED claim or an actual surrogate/stale receipt id.
    if not authority_valid and (claimed_attributed or surrogate_present):
        row["status"] = "INDETERMINATE"
        row["reason_code"] = (
            "OPERATION_CAUSAL_GOVERNANCE_TRANSPORT_RECEIPT_MISSING"
            if not exact_id
            else "OPERATION_CAUSAL_GOVERNANCE_TRANSPORT_RECEIPT_MISMATCH"
        )
        row["transport_reached"] = bool(
            exact_id and _positive_status_code(row.get("status_code"))
        )
        changed = True

    # Never retain a body fingerprint or any other surrogate in a field whose
    # semantics are an actual governance transport receipt.
    if claimed_id != exact_id:
        row["transport_receipt_id"] = exact_id
        changed = True

    if changed:
        row.pop("receipt_id", None)
        return seal_operation_causality_transport_receipt(row)
    return row


def install_operation_causality_transport_authority() -> None:
    """Tighten the existing transport finalizer in place."""
    from . import operation_causality_runtime as runtime

    original = getattr(runtime, "finalize_operation_causality_transport", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        result = kwargs.get("result")
        observations = kwargs.get("observations")
        receipts = original(*args, **kwargs)
        if not isinstance(result, dict):
            return receipts
        governance = _governance_receipt_ids(result)
        tightened: list[dict[str, Any]] = []
        for raw in receipts:
            row = _dict(raw)
            key = (
                _text(row.get("operation_ref")),
                _text(row.get("treatment_step_id")),
            )
            tightened.append(
                _tighten_receipt(row, candidates=governance.get(key, []))
            )
        if isinstance(observations, dict):
            observations[runtime.TRANSPORT_KEY] = tightened
        return tightened

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    runtime.finalize_operation_causality_transport = wrapped


__all__ = ["install_operation_causality_transport_authority"]
