"""Canonical outcome-aware customer Delivery Gate v2 authority.

The historical receipt and cleanup mechanics remain in the private compatibility module.
This facade removes implicit multi-violation selection and requires the finding, violated
assertion, observer receipts, Oracle receipt, and Gate receipt to agree on one ``outcome_ref``.
"""
from __future__ import annotations

from typing import Any

from . import assertion_dsl as _assertions
from . import contract_oracles as _oracles
from . import observer_contracts as _observers
from . import _customer_delivery_gate_v2_mechanics as _core
from ._customer_delivery_gate_v2_mechanics import *  # noqa: F401,F403

_original_validate_active_chain = _core._validate_active_chain
_original_build_customer_delivery_gate_receipt_v2 = (
    _core.build_customer_delivery_gate_receipt_v2
)
_original_validate_customer_delivery_gate_receipt_v2 = (
    _core.validate_customer_delivery_gate_receipt_v2
)

_CANONICAL_FIELDS = (
    "canonical_outcome_identity_required",
    "outcome_ref",
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _receipt_outcome_ref(receipt: dict[str, Any]) -> str:
    return _text(
        receipt.get("outcome_ref")
        or _dict(_dict(receipt.get("evidence")).get("canonical_outcome_identity")).get(
            "outcome_ref"
        )
    )


def _validate_active_chain(
    *,
    execution: dict[str, Any],
    contracts: list[dict[str, Any]],
    observers: list[dict[str, Any]],
    oracle: dict[str, Any],
    reproduction: dict[str, Any],
) -> tuple[str, list[str]]:
    status, reasons = _original_validate_active_chain(
        execution=execution,
        contracts=contracts,
        observers=observers,
        oracle=oracle,
        reproduction=reproduction,
    )
    if status != "DELIVERABLE" or not bool(
        oracle.get("canonical_outcome_identity_required")
    ):
        return status, reasons
    primary = _text(oracle.get("primary_violation_outcome_ref"))
    if not primary:
        return "BLOCKED", ["CANONICAL_VIOLATION_OUTCOME_REF_MISSING"]
    violations = [
        _assertions.validate_assertion_receipt(_dict(row))
        for row in _list(oracle.get("assertions"))
        if isinstance(row, dict) and _text(_dict(row).get("status")) == "VIOLATION"
    ]
    matching = [row for row in violations if _text(row.get("outcome_ref")) == primary]
    if len(violations) != 1 or len(matching) != 1:
        return "BLOCKED", ["AMBIGUOUS_MULTI_OUTCOME_OCCURRENCE"]
    assertion = matching[0]
    observers_by_id = {
        _text(row.get("receipt_id")): row
        for row in observers
        if _text(row.get("receipt_id"))
    }
    referenced = [
        observers_by_id.get(_text(receipt_id))
        for receipt_id in _list(assertion.get("observer_receipt_ids"))
        if _text(receipt_id)
    ]
    if any(row is None for row in referenced):
        return "BLOCKED", ["OUTCOME_OBSERVER_RECEIPT_REFERENCE_MISSING"]
    bound = [
        row for row in referenced if _receipt_outcome_ref(_dict(row)) == primary
    ]
    foreign = [
        row
        for row in referenced
        if _receipt_outcome_ref(_dict(row))
        and _receipt_outcome_ref(_dict(row)) != primary
    ]
    if not bound:
        return "BLOCKED", ["OUTCOME_OBSERVER_RECEIPT_MISSING"]
    if foreign:
        return "BLOCKED", ["FOREIGN_OUTCOME_OBSERVER_RECEIPT_REFERENCED"]
    return status, reasons


def _seal_gate(base: dict[str, Any], *, strict: bool, outcome_ref: str) -> dict[str, Any]:
    payload = {
        key: value for key, value in dict(base).items()
        if key not in {"gate_receipt_id", "output_fingerprint"}
    }
    payload.update(
        {
            "canonical_outcome_identity_required": bool(strict),
            "outcome_ref": _text(outcome_ref),
        }
    )
    return _core._seal(
        payload,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )


def build_customer_delivery_gate_receipt_v2(
    *,
    finding: dict[str, Any] | None,
    execution_receipt: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    observer_receipts: list[dict[str, Any]],
    oracle_receipt: dict[str, Any],
    reproduction_receipt: dict[str, Any],
) -> dict[str, Any]:
    oracle = _oracles.validate_contract_oracle_receipt(_dict(oracle_receipt))
    strict = bool(oracle.get("canonical_outcome_identity_required"))
    primary = _text(oracle.get("primary_violation_outcome_ref"))
    finding_row = _dict(finding)
    finding_ref = _text(finding_row.get("outcome_ref"))
    mismatch_reason = ""
    supplied_finding: dict[str, Any] | None = finding
    if strict and _text(oracle.get("status")) == "VIOLATION":
        if not primary:
            mismatch_reason = "CANONICAL_VIOLATION_OUTCOME_REF_MISSING"
        elif not finding_ref:
            mismatch_reason = "FINDING_OUTCOME_REF_MISSING"
        elif finding_ref != primary:
            mismatch_reason = "FINDING_OUTCOME_REF_MISMATCH"
        if mismatch_reason:
            supplied_finding = None

    base = _original_build_customer_delivery_gate_receipt_v2(
        finding=supplied_finding,
        execution_receipt=execution_receipt,
        contract_evidence_receipts=contract_evidence_receipts,
        observer_receipts=observer_receipts,
        oracle_receipt=oracle,
        reproduction_receipt=reproduction_receipt,
    )
    if not strict:
        return base
    governed = dict(base)
    if mismatch_reason:
        governed.update(
            {
                "status": "BLOCKED",
                "reason_code": mismatch_reason,
                "reason_codes": [mismatch_reason],
                "finding_payload_fingerprint": "",
            }
        )
    sealed = _seal_gate(governed, strict=True, outcome_ref=primary)
    return validate_customer_delivery_gate_receipt_v2(
        sealed,
        finding=finding_row
        if sealed.get("status") == "DELIVERABLE"
        else None,
    )


def validate_customer_delivery_gate_receipt_v2(
    receipt: dict[str, Any],
    *,
    finding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _dict(receipt)
    if not set(_CANONICAL_FIELDS).intersection(row):
        return _original_validate_customer_delivery_gate_receipt_v2(
            row, finding=finding
        )
    if not set(_CANONICAL_FIELDS).issubset(row):
        raise _core.DeliveryGateV2Error("delivery_gate_outcome_fields_invalid")
    strict = bool(row.get("canonical_outcome_identity_required"))
    outcome_ref = _text(row.get("outcome_ref"))
    if not strict:
        raise _core.DeliveryGateV2Error("delivery_gate_outcome_authority_invalid")
    deliverable = _text(row.get("status")) == "DELIVERABLE"
    if deliverable and not outcome_ref:
        raise _core.DeliveryGateV2Error("delivery_gate_outcome_ref_missing")
    if deliverable and finding is not None and _text(
        _dict(finding).get("outcome_ref")
    ) != outcome_ref:
        raise _core.DeliveryGateV2Error("finding_outcome_ref_mismatch")

    base_payload = {
        key: value
        for key, value in row.items()
        if key not in set(_CANONICAL_FIELDS)
        | {"gate_receipt_id", "output_fingerprint"}
    }
    base = _core._seal(
        base_payload,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )
    _original_validate_customer_delivery_gate_receipt_v2(
        base,
        finding=finding if deliverable else None,
    )
    expected = _seal_gate(base, strict=True, outcome_ref=outcome_ref)
    if row != expected:
        raise _core.DeliveryGateV2Error(
            "delivery_gate_output_fingerprint_invalid"
        )
    return dict(expected)


def validate_customer_delivery_gate_bundle(
    gate_receipt: dict[str, Any],
    *,
    finding: dict[str, Any] | None,
    execution_receipt: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    observer_receipts: list[dict[str, Any]],
    oracle_receipt: dict[str, Any],
    reproduction_receipt: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_customer_delivery_gate_receipt_v2(
        gate_receipt,
        finding=finding
        if _text(_dict(gate_receipt).get("status")) == "DELIVERABLE"
        else None,
    )
    rebuilt = build_customer_delivery_gate_receipt_v2(
        finding=finding,
        execution_receipt=execution_receipt,
        contract_evidence_receipts=contract_evidence_receipts,
        observer_receipts=observer_receipts,
        oracle_receipt=oracle_receipt,
        reproduction_receipt=reproduction_receipt,
    )
    if validated != rebuilt:
        # ``reason_detail`` is diagnostic enrichment, not a new adjudication
        # input.  Receipts emitted before the enrichment was introduced remain
        # immutable and must continue to validate against the same evidence
        # bundle.  Accept only the exact old sealed payload; any other drift
        # remains a hard bundle mismatch.
        if (
            "reason_detail" not in validated
            and "reason_detail" in rebuilt
            and validated
            == _core._seal(
                {
                    key: value
                    for key, value in rebuilt.items()
                    if key not in {
                        "reason_detail",
                        "gate_receipt_id",
                        "output_fingerprint",
                    }
                },
                prefix="gate_",
                id_field="gate_receipt_id",
                fingerprint_field="output_fingerprint",
            )
        ):
            return validated
        raise _core.DeliveryGateV2Error("delivery_gate_bundle_mismatch")
    return validated


# Patch mechanics globals used by the original builder and bundle validator.
_core.validate_assertion_receipt = _assertions.validate_assertion_receipt
_core.validate_observer_receipt = _observers.validate_observer_receipt
_core.validate_contract_oracle_receipt = _oracles.validate_contract_oracle_receipt
_core._validate_active_chain = _validate_active_chain
_core.validate_customer_delivery_gate_receipt_v2 = (
    validate_customer_delivery_gate_receipt_v2
)

__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name not in {"_core", "_assertions", "_observers", "_oracles"}
)
