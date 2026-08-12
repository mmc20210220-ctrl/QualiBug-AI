"""Contract Oracle facade with strict post-hoc gate integrity.

Outcome-aware Oracle evaluation remains in
``_contract_oracles_outcome_mechanics``.  Authorization causality, Oracle
validity and authorization-delivery gates are deliberately allowed to *demote*
a sealed Oracle after evaluation, but their annotations are not a substitute
for the original content-addressed receipt.

This facade therefore reconstructs the pre-gate Oracle, validates its complete
activation/assertion/lineage/fingerprint contract, and separately enforces that
post-hoc mutations are one-way demotions.  Merely adding a gate-shaped field to
an arbitrary or tampered Oracle can no longer bypass receipt validation.
"""
from __future__ import annotations

from typing import Any

from . import _contract_oracles_outcome_mechanics as _outcome
from ._contract_oracles_outcome_mechanics import *  # noqa: F401,F403

_validate_sealed_outcome_oracle = _outcome.validate_contract_oracle_receipt

_CAUSALITY_FIELDS = frozenset({
    "authorization_causality_gate",
    "authorization_causality_receipt_id",
    "authorization_causality_reason_codes",
    "pre_causality_oracle_verdict",
})
_VALIDITY_FIELDS = frozenset({
    "oracle_validity_gate",
    "oracle_validity_receipt_id",
    "oracle_validity_reason_codes",
    "pre_validity_oracle_verdict",
    "effect_observation_graph_receipt_id",
    "effect_observation_graph_status",
    "effect_observation_graph_fingerprint",
})
_DELIVERY_FIELDS = frozenset({
    "authorization_delivery_gate",
    "authorization_delivery_reason",
})


def __getattr__(name: str) -> Any:
    return getattr(_outcome, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_outcome)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _posthoc_gate_present(row: dict[str, Any]) -> bool:
    return bool(
        _text(row.get("authorization_causality_gate"))
        or _dict(row.get("pre_causality_oracle_verdict"))
        or _text(row.get("oracle_validity_gate"))
        or _dict(row.get("pre_validity_oracle_verdict"))
        or _text(row.get("authorization_delivery_gate"))
    )


def _canonical_reason_codes(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"contract_oracle_{field}_invalid")
    normalized = sorted({_text(item) for item in value if _text(item)})
    if value != normalized:
        raise ValueError(f"contract_oracle_{field}_not_canonical")
    return normalized


def _validate_posthoc_semantics(row: dict[str, Any]) -> None:
    """Post-hoc gates may demote or annotate; they may never upgrade truth."""

    causality_gate = _text(row.get("authorization_causality_gate")).upper()
    causality_pre = _dict(row.get("pre_causality_oracle_verdict"))
    if causality_gate:
        receipt_id = _text(row.get("authorization_causality_receipt_id"))
        if causality_gate not in {"PASSED", "INDETERMINATE", "NOT_APPLICABLE"}:
            raise ValueError("contract_oracle_causality_gate_invalid")
        if not receipt_id.startswith("auth_causality_"):
            raise ValueError("contract_oracle_causality_receipt_ref_invalid")
        reasons = _canonical_reason_codes(
            row.get("authorization_causality_reason_codes", []),
            field="causality_reason_codes",
        )
        if causality_gate == "INDETERMINATE":
            if not causality_pre or not reasons:
                raise ValueError("contract_oracle_causality_demotion_proof_missing")
            if any(
                (
                    _text(row.get("status")).upper() != "INDETERMINATE",
                    _text(row.get("verdict")) != "blocked_experiment",
                    row.get("customer_deliverable_candidate") is not False,
                )
            ):
                raise ValueError("contract_oracle_causality_demotion_invalid")
        elif causality_pre:
            raise ValueError("contract_oracle_causality_preverdict_unexpected")

    validity_gate = _text(row.get("oracle_validity_gate")).upper()
    validity_pre = _dict(row.get("pre_validity_oracle_verdict"))
    if validity_gate:
        receipt_id = _text(row.get("oracle_validity_receipt_id"))
        if validity_gate not in {"PASSED", "INDETERMINATE", "NOT_APPLICABLE"}:
            raise ValueError("contract_oracle_validity_gate_invalid")
        if not receipt_id.startswith("ovg_"):
            raise ValueError("contract_oracle_validity_receipt_ref_invalid")
        reasons = _canonical_reason_codes(
            row.get("oracle_validity_reason_codes", []),
            field="validity_reason_codes",
        )
        if validity_gate == "INDETERMINATE":
            if not validity_pre or not reasons:
                raise ValueError("contract_oracle_validity_demotion_proof_missing")
            if any(
                (
                    _text(row.get("status")).upper() != "INDETERMINATE",
                    _text(row.get("verdict")) != "indeterminate",
                    row.get("customer_deliverable_candidate") is not False,
                )
            ):
                raise ValueError("contract_oracle_validity_demotion_invalid")
        elif validity_pre:
            raise ValueError("contract_oracle_validity_preverdict_unexpected")

    delivery_gate = _text(row.get("authorization_delivery_gate")).upper()
    if delivery_gate:
        if delivery_gate != "INDETERMINATE":
            raise ValueError("contract_oracle_delivery_gate_invalid")
        if not _text(row.get("authorization_delivery_reason")):
            raise ValueError("contract_oracle_delivery_reason_missing")
        if any(
            (
                _text(row.get("status")).upper() != "INDETERMINATE",
                _text(row.get("verdict")) != "blocked_experiment",
                row.get("customer_deliverable_candidate") is not False,
            )
        ):
            raise ValueError("contract_oracle_delivery_demotion_invalid")


def _restore_pre_gate_oracle(row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the exact sealed Oracle underneath ordered post-hoc gates."""

    base = dict(row)

    # Authorization delivery is the last execution-boundary gate.  It only
    # runs for a previously created defect candidate; its failure helper did
    # not historically store a pre-verdict snapshot, so restore the unique
    # candidate shape that can enter this gate.
    delivery_gate = _text(base.get("authorization_delivery_gate")).upper()
    if delivery_gate:
        for field in _DELIVERY_FIELDS:
            base.pop(field, None)
        if delivery_gate == "INDETERMINATE":
            base["status"] = "VIOLATION"
            base["verdict"] = "customer_deliverable_defect_candidate"
            base["customer_deliverable_candidate"] = True

    # Oracle validity runs after authorization causality.  Its pre-verdict
    # snapshot contains the sealed semantic fields that it changed.  Restore
    # only fields present in that snapshot, then remove validity annotations.
    validity_pre = dict(_dict(base.get("pre_validity_oracle_verdict")))
    for field in _VALIDITY_FIELDS:
        base.pop(field, None)
    if validity_pre:
        for field, value in validity_pre.items():
            base[field] = value

    # Authorization causality runs first among these post-hoc gates.
    causality_pre = dict(_dict(base.get("pre_causality_oracle_verdict")))
    for field in _CAUSALITY_FIELDS:
        base.pop(field, None)
    if causality_pre:
        for field, value in causality_pre.items():
            base[field] = value
        # The causality snapshot seals only the semantic fields it changed
        # (status/verdict/receipt_id/…); customer_deliverable_candidate is not
        # captured, so copying only snapshot fields left the restored VIOLATION
        # base carrying the demoted candidate=False. The strict validator then
        # saw a VIOLATION row with candidate=False and the override check on
        # the gate-stripped base raised contract_oracle_semantics_invalid
        # (run34: 73 receipts rejected, experiment groups lost). Restore the
        # VIOLATION delivery-candidate semantics explicitly, exactly as the
        # authorization-delivery restore below does.
        if _text(causality_pre.get("status")).upper() == "VIOLATION":
            base["customer_deliverable_candidate"] = True
            base["customer_deliverable"] = False

    return base


def validate_contract_oracle_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    if not _posthoc_gate_present(row):
        return _validate_sealed_outcome_oracle(row)

    if not _text(row.get("receipt_id")):
        raise ValueError("contract_oracle_receipt_fingerprint_invalid")
    _validate_posthoc_semantics(row)
    base = _restore_pre_gate_oracle(row)
    if _text(base.get("receipt_id")) != _text(row.get("receipt_id")):
        raise ValueError("contract_oracle_posthoc_base_identity_mismatch")

    # This call traverses the complete pre-existing strict validator: activation
    # receipt identity, assertion receipt validation, campaign/execution lineage,
    # canonical outcome projection and content-address fingerprint.
    validated_base = _validate_sealed_outcome_oracle(base)
    if _text(validated_base.get("receipt_id")) != _text(row.get("receipt_id")):
        raise ValueError("contract_oracle_posthoc_fingerprint_mismatch")
    return dict(row)


# Outcome projection and historical mechanics resolve the validator from their
# module globals at call time.  Point both at the strict public authority so no
# internal path can retain the old annotation-shaped bypass.
_outcome.validate_contract_oracle_receipt = validate_contract_oracle_receipt
_outcome._core.validate_contract_oracle_receipt = validate_contract_oracle_receipt

__all__ = sorted(
    {
        *[
            name
            for name in dir(_outcome)
            if not name.startswith("__")
        ],
        "validate_contract_oracle_receipt",
    }
)
