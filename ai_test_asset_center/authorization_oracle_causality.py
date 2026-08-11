"""Authorization causality facade with sealed runtime-binding provenance.

The established causality mechanics remain in
``_authorization_oracle_causality_mechanics``.  This facade tightens the
resource-binding proof only: a BOUND row is not evidence merely because it
carries a value fingerprint.  It must carry the existing sealed materialization
identity receipt, that embedded receipt must match the parent row, and the
source-specific runtime provenance must be present.

The causality resource fingerprint commits to both value identity and provenance
(resolver operation/path/status or an already-observed reuse source), so later
code cannot substitute an unproven binding source without changing the causal
receipt.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

from . import _authorization_oracle_causality_mechanics as _core
from ._authorization_oracle_causality_mechanics import *  # noqa: F401,F403
from .binding_materialization_identity_receipt import (
    BindingMaterializationIdentityError,
    validate_binding_materialization_identity_receipt,
)

_ALLOWED_BINDING_AUTHORITIES = frozenset({
    "same_actor_list_read",
    "experiment_setup_response",
    "observed_reuse_priority",
})


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return _core._canonical(value)


def _binding_parent_provenance(
    row: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Validate one materialization row and return signed causal provenance."""

    try:
        proof = validate_binding_materialization_identity_receipt(
            _dict(row.get("materialization_identity_receipt"))
        )
    except BindingMaterializationIdentityError as exc:
        return {}, f"materialization_identity_invalid:{type(exc).__name__}"

    if _text(row.get("materialization_receipt_id")) != _text(
        proof.get("receipt_id")
    ):
        return {}, "materialization_identity_reference_mismatch"
    if _text(row.get("target") or row.get("binding_target")) != _text(
        proof.get("target")
    ):
        return {}, "materialization_identity_target_mismatch"
    if _text(row.get("status")).upper() != _text(proof.get("status")).upper():
        return {}, "materialization_identity_status_mismatch"
    if _text(row.get("value_fingerprint")) != _text(
        proof.get("value_fingerprint")
    ):
        return {}, "materialization_identity_value_mismatch"

    source = _text(row.get("source_priority"))
    if source not in _ALLOWED_BINDING_AUTHORITIES:
        return {}, f"materialization_source_not_authoritative:{source or 'missing'}"

    resolver_path = _text(row.get("resolver_path"))
    resolver_operation_ref = _text(row.get("resolver_operation_ref"))
    try:
        status_code = int(row.get("status_code") or 0)
    except (TypeError, ValueError):
        status_code = 0

    if source in {"same_actor_list_read", "experiment_setup_response"}:
        if not resolver_path.startswith("/"):
            return {}, "materialization_resolver_path_missing"
        if not resolver_operation_ref:
            return {}, "materialization_resolver_operation_missing"
        if not (200 <= status_code < 300):
            return {}, "materialization_resolver_not_observed_2xx"
    elif source == "observed_reuse_priority":
        # Reuse is admitted only when the materializer itself attached the
        # previously observed source path.  It has no new HTTP status because
        # the observation occurred in the precondition/previous materialization
        # leg, so requiring a synthetic 2xx here would invent transport evidence.
        if not resolver_path:
            return {}, "materialization_reuse_source_missing"

    provenance = {
        "materialization_receipt_id": _text(proof.get("receipt_id")),
        "target": _text(proof.get("target")),
        "status": "BOUND",
        "value_fingerprint": _text(proof.get("value_fingerprint")),
        "source_priority": source,
        "resolver_operation_ref": resolver_operation_ref,
        "resolver_path": resolver_path,
        "status_code": status_code,
        "resolver_actor_ref": _text(row.get("resolver_actor_ref")),
        "identity_source": _text(row.get("identity_source")),
    }
    return provenance, ""


def _binding_proof(
    contract: dict[str, Any], rows: Iterable[Any]
) -> tuple[str, list[str], list[str]]:
    targets = [
        _text(value)
        for value in _list(_dict(contract).get("resource_identity_binding_targets"))
        if _text(value)
    ]
    if not targets:
        if _dict(contract).get("same_resource_identity_required") is True:
            return "", [], ["AUTHORIZATION_CAUSAL_RESOURCE_TARGETS_MISSING"]
        return "", [], []

    by_target: dict[str, list[dict[str, Any]]] = {target: [] for target in targets}
    for raw in rows:
        row = _dict(raw)
        target = _text(row.get("target") or row.get("binding_target"))
        if target not in by_target:
            continue
        if (
            _text(row.get("status")).upper() == "BOUND"
            and _text(row.get("value_fingerprint"))
        ):
            by_target[target].append(row)

    reasons: list[str] = []
    receipt_ids: list[str] = []
    proven_by_target: dict[str, str] = {}
    for target in targets:
        matches = by_target[target]
        if len(matches) != 1:
            reasons.append(
                f"AUTHORIZATION_CAUSAL_RESOURCE_BINDING_"
                f"{'MISSING' if not matches else 'AMBIGUOUS'}:{target}"
            )
            continue
        provenance, problem = _binding_parent_provenance(matches[0])
        if problem:
            reasons.append(
                f"AUTHORIZATION_CAUSAL_RESOURCE_BINDING_PROVENANCE_INVALID:"
                f"{target}:{problem}"
            )
            continue
        receipt_id = _text(provenance.get("materialization_receipt_id"))
        if receipt_id:
            receipt_ids.append(receipt_id)
        # The causal resource identity is now value + provenance, not just the
        # value fingerprint.  This remains content-addressed and deterministic.
        proven_by_target[target] = hashlib.sha256(
            _canonical(provenance).encode("utf-8")
        ).hexdigest()

    if reasons:
        return "", sorted(set(receipt_ids)), reasons
    fingerprint = hashlib.sha256(
        _canonical(proven_by_target).encode("utf-8")
    ).hexdigest()
    return fingerprint, sorted(set(receipt_ids)), []


# The mechanics builder resolves this helper from its defining-module globals.
# Point that exact call site at the strict public proof authority.
_core._binding_proof = _binding_proof

# Preserve the established public callables while ensuring their mechanics use
# the governed binding proof above.
build_authorization_causality_receipt = _core.build_authorization_causality_receipt

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_binding_proof",
        "build_authorization_causality_receipt",
    }
)
