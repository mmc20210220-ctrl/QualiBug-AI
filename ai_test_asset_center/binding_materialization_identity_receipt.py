"""Content-addressed identity receipts for final runtime bindings.

The fixture materializer owns resolution and cleanup mutates its receipt status in
place. This authority runs only after governed execution/cleanup has returned and
seals the minimum customer-delivery identity projection: target, final BOUND status,
and the already-redacted value fingerprint. It never stores the bound value, resolves
a binding, or changes execution semantics.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable


SCHEMA_VERSION = "qualibug.binding-materialization-identity-receipt.v1"
_PROOF_FIELDS = {
    "schema_version",
    "receipt_id",
    "target",
    "status",
    "value_fingerprint",
}


class BindingMaterializationIdentityError(ValueError):
    """A runtime binding identity projection is missing or has been altered."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _payload(value: dict[str, Any]) -> dict[str, str]:
    row = _dict(value)
    target = _text(row.get("target") or row.get("binding_target"))
    status = _text(row.get("status")).upper()
    value_fingerprint = _text(row.get("value_fingerprint"))
    if not target:
        raise BindingMaterializationIdentityError(
            "binding_materialization_target_missing"
        )
    if status != "BOUND":
        raise BindingMaterializationIdentityError(
            "binding_materialization_status_not_bound"
        )
    if not value_fingerprint:
        raise BindingMaterializationIdentityError(
            "binding_materialization_value_fingerprint_missing"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "status": status,
        "value_fingerprint": value_fingerprint,
    }


def build_binding_materialization_identity_receipt(
    value: dict[str, Any],
) -> dict[str, str]:
    """Build one receipt without exposing the materialized runtime value."""
    payload = _payload(value)
    return {
        **payload,
        "receipt_id": "binding_materialization_" + hashlib.sha256(
            _canonical(payload).encode("utf-8")
        ).hexdigest()[:24],
    }


def validate_binding_materialization_identity_receipt(
    value: dict[str, Any],
) -> dict[str, str]:
    row = _dict(value)
    if set(row) != _PROOF_FIELDS:
        raise BindingMaterializationIdentityError(
            "binding_materialization_identity_fields_invalid"
        )
    expected = build_binding_materialization_identity_receipt(row)
    if row != expected:
        raise BindingMaterializationIdentityError(
            "binding_materialization_identity_fingerprint_invalid"
        )
    return dict(expected)


def seal_binding_materialization_receipts(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach final identity receipt IDs after cleanup without mutating input."""
    output = deepcopy(_dict(result))
    sealed_rows: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for raw in _list(output.get("binding_materialization_receipts")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if _text(row.get("status")).upper() == "BOUND":
            receipt = build_binding_materialization_identity_receipt(row)
            target = receipt["target"]
            if target in seen_targets:
                raise BindingMaterializationIdentityError(
                    f"binding_materialization_target_ambiguous:{target}"
                )
            seen_targets.add(target)
            row["materialization_receipt_id"] = receipt["receipt_id"]
            row["materialization_identity_receipt"] = receipt
        sealed_rows.append(row)
    output["binding_materialization_receipts"] = sealed_rows
    return output


def binding_identity_proofs_for_targets(
    rows: Iterable[Any],
    targets: Iterable[Any],
) -> list[dict[str, str]]:
    """Project exact validated identity receipts for the requested binding targets."""
    required = {_text(value) for value in targets if _text(value)}
    proofs: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in rows:
        row = _dict(raw)
        target = _text(row.get("target") or row.get("binding_target"))
        if target not in required:
            continue
        proof = validate_binding_materialization_identity_receipt(
            _dict(row.get("materialization_identity_receipt"))
        )
        if _text(row.get("materialization_receipt_id")) != proof["receipt_id"]:
            raise BindingMaterializationIdentityError(
                "binding_materialization_embedded_reference_mismatch"
            )
        if target in seen:
            raise BindingMaterializationIdentityError(
                f"binding_materialization_target_ambiguous:{target}"
            )
        seen.add(target)
        proofs.append(proof)
    if seen != required:
        missing = sorted(required - seen)
        raise BindingMaterializationIdentityError(
            "binding_materialization_identity_missing:"
            + ",".join(missing)
        )
    return sorted(proofs, key=lambda value: value["target"])


__all__ = [
    "BindingMaterializationIdentityError",
    "SCHEMA_VERSION",
    "binding_identity_proofs_for_targets",
    "build_binding_materialization_identity_receipt",
    "seal_binding_materialization_receipts",
    "validate_binding_materialization_identity_receipt",
]
