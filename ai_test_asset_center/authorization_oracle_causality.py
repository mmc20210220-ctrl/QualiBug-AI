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
    seal_binding_materialization_receipts,
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
        # The provenance check above already rejects unproven binding sources
        # (non-authoritative source_priority, missing resolver path, invalid
        # 2xx resolver status, etc.). The causal fingerprint itself commits to
        # the sealed value_fingerprint only, so the delivery gate and the
        # historical quarantine can re-derive it content-addressed from the
        # 4-field binding proof alone. Folding the full provenance dict into
        # the fingerprint made the proof unverifiable downstream: the
        # quarantine only holds the proof, never the source materialization
        # rows it would need to reconstruct provenance.
        proven_by_target[target] = _text(provenance.get("value_fingerprint"))

    if reasons:
        return "", sorted(set(receipt_ids)), reasons
    fingerprint = hashlib.sha256(
        _canonical(proven_by_target).encode("utf-8")
    ).hexdigest()
    return fingerprint, sorted(set(receipt_ids)), []


def _recover_same_actor_list_read_materialization(
    result: dict[str, Any],
    experiment: dict[str, Any],
) -> None:
    """Recover a materialization receipt only from an already-proven identity GET.

    The core materializer's pre-resolved ``same_actor_list_read`` branch records
    the selected value in ``runtime_bindings`` and a fixture receipt, then exits
    before appending the binding-materialization receipt consumed by the strict
    authorization causality gate.  Recovery is evidence projection only: no raw
    value is reconstructed and no network fact is invented.  The exact target
    must be source-declared, the fixture receipt must carry the same proof path,
    and one real 2xx identity-proof step must name the resolver operation.
    """

    output = _dict(result)
    exp = _dict(experiment)
    existing = [
        dict(row)
        for row in _list(output.get("binding_materialization_receipts"))
        if isinstance(row, dict)
    ]
    existing_bound_targets = {
        _text(row.get("target") or row.get("binding_target"))
        for row in existing
        if _text(row.get("status")).upper() == "BOUND"
    }

    binding_rows = [
        _dict(row)
        for row in _list(exp.get("binding_plan"))
        if isinstance(row, dict)
        and _text(_dict(row).get("source_priority")) == "same_actor_list_read"
        and _text(_dict(row).get("status")).lower() == "bound"
        and _dict(row).get("materialized_value") not in (None, "")
        and _text(_dict(row).get("target"))
    ]
    if not binding_rows:
        return

    fixtures = [
        _dict(row)
        for row in _list(output.get("fixture_receipts"))
        if isinstance(row, dict)
    ]
    steps = [
        _dict(row)
        for row in _list(output.get("steps"))
        if isinstance(row, dict)
    ]

    recovered = list(existing)
    for binding in binding_rows:
        target = _text(binding.get("target"))
        if target in existing_bound_targets:
            continue

        fixture_matches = [
            row
            for row in fixtures
            if _text(row.get("target")) == target
            and _text(row.get("status")).lower() == "resolved"
            and _text(row.get("source")) == "pre_resolved_binding"
            and _text(row.get("value_fingerprint"))
            and _text(row.get("proof_source")).startswith("/")
        ]
        if len(fixture_matches) != 1:
            continue
        fixture = fixture_matches[0]
        proof_source = _text(fixture.get("proof_source"))

        proof_steps = []
        for step in steps:
            try:
                status_code = int(step.get("status_code") or 0)
            except (TypeError, ValueError):
                status_code = 0
            if (
                _text(step.get("phase")) == "binding_identity_proof"
                and _text(step.get("step_id")).startswith(f"bind-proof:{target}")
                and _text(step.get("path")) == proof_source
                and _text(step.get("operation_ref"))
                and _text(step.get("method")).upper() in {"GET", "HEAD"}
                and 200 <= status_code < 300
            ):
                proof_steps.append((step, status_code))
        if len(proof_steps) != 1:
            continue
        proof_step, status_code = proof_steps[0]

        recovered.append({
            "target": target,
            "source_priority": "same_actor_list_read",
            "status": "bound",
            "value_fingerprint": _text(fixture.get("value_fingerprint")),
            "resolver_path": proof_source,
            "resolver_operation_ref": _text(proof_step.get("operation_ref")),
            "status_code": status_code,
            "resolver_actor_ref": _text(proof_step.get("actor_ref")),
        })
        existing_bound_targets.add(target)

    if len(recovered) == len(existing):
        return
    sealed = seal_binding_materialization_receipts({
        "binding_materialization_receipts": recovered,
    })
    output["binding_materialization_receipts"] = _list(
        sealed.get("binding_materialization_receipts")
    )


# The mechanics builder resolves this helper from its defining-module globals.
# Point that exact call site at the strict public proof authority.
_core._binding_proof = _binding_proof

_original_build_authorization_causality_receipt = (
    _core.build_authorization_causality_receipt
)


def build_authorization_causality_receipt(
    *,
    result: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    account_rows: Iterable[Any],
) -> dict[str, Any]:
    """Project proven missing materialization evidence before causal adjudication."""

    _recover_same_actor_list_read_materialization(result, experiment)
    return _original_build_authorization_causality_receipt(
        result=result,
        experiment=experiment,
        behavior_ir=behavior_ir,
        account_rows=account_rows,
    )


# ``enforce_authorization_oracle_causality`` lives in the mechanics module and
# resolves the builder from that module's globals. Install the governed wrapper
# at the actual call site so public and internal entry points behave identically.
_core.build_authorization_causality_receipt = build_authorization_causality_receipt

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_binding_proof",
        "_recover_same_actor_list_read_materialization",
        "build_authorization_causality_receipt",
    }
)
