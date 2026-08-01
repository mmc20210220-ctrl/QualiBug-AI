"""Multi-occurrence authority over the stable obligation attempt ledger.

One selected obligation still owns exactly one execution attempt and one terminal status.
A successful execution may, however, contain several independently gated delivery
occurrences. The primary occurrence remains the compatibility projection used by the v1
ledger mechanics; every additional occurrence is validated and sealed inside the same
attempt fingerprint.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import _obligation_attempt_ledger_single_occurrence_mechanics as _core
from ._obligation_attempt_ledger_single_occurrence_mechanics import *  # noqa: F401,F403
from .customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    validate_customer_delivery_gate_bundle,
)

_original_build_obligation_attempt_ledger = _core.build_obligation_attempt_ledger
_original_validate_obligation_attempt_ledger = _core.validate_obligation_attempt_ledger
_original_reseal_obligation_attempt_ledger = _core.reseal_obligation_attempt_ledger


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _occurrence_bundle(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding": dict(_dict(row.get("finding"))) or None,
        "execution_receipt": dict(_dict(row.get("delivery_execution_receipt"))),
        "contract_evidence_receipts": [
            dict(item)
            for item in _list(row.get("contract_evidence_receipts"))
            if isinstance(item, dict)
        ],
        "observer_receipts": [
            dict(item)
            for item in _list(row.get("observer_receipts"))
            if isinstance(item, dict)
        ],
        "oracle_receipt": dict(_dict(row.get("oracle_receipt"))),
        "reproduction_receipt": dict(_dict(row.get("reproduction_receipt"))),
    }


def _validate_occurrence_row(
    row: dict[str, Any],
    *,
    attempt: dict[str, Any],
) -> dict[str, Any]:
    finding_id = _text(row.get("finding_id"))
    outcome_ref = _text(row.get("outcome_ref"))
    gate = _dict(row.get("gate_receipt"))
    if not finding_id or not outcome_ref:
        raise _core.ObligationAttemptLedgerError(
            "delivery_occurrence_identity_missing"
        )
    if gate.get("schema_version") != CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
        raise _core.ObligationAttemptLedgerError(
            f"delivery_occurrence_gate_schema_invalid:{finding_id}"
        )
    if _text(gate.get("status")) != "DELIVERABLE":
        raise _core.ObligationAttemptLedgerError(
            f"delivery_occurrence_not_deliverable:{finding_id}"
        )
    bundle = _occurrence_bundle(row)
    validated = validate_customer_delivery_gate_bundle(gate, **bundle)
    identity = _dict(validated.get("identity"))
    expected = {
        "finding_id": finding_id,
        "obligation_id": _text(attempt.get("executed_obligation_id")),
        "experiment_id": _text(attempt.get("experiment_id")),
        "execution_id": _text(attempt.get("execution_id")),
    }
    for field, value in expected.items():
        if not value or _text(identity.get(field)) != value:
            raise _core.ObligationAttemptLedgerError(
                f"delivery_occurrence_lineage_mismatch:{finding_id}:{field}"
            )
    if _text(validated.get("outcome_ref")) != outcome_ref:
        raise _core.ObligationAttemptLedgerError(
            f"delivery_occurrence_outcome_ref_mismatch:{finding_id}"
        )
    oracle = _dict(bundle.get("oracle_receipt"))
    if _text(oracle.get("primary_violation_outcome_ref")) != outcome_ref:
        raise _core.ObligationAttemptLedgerError(
            f"delivery_occurrence_oracle_outcome_ref_mismatch:{finding_id}"
        )
    parent = _dict(row.get("parent_oracle_receipt"))
    parent_id = _text(oracle.get("parent_oracle_receipt_id"))
    if parent_id:
        if _text(parent.get("receipt_id")) != parent_id:
            raise _core.ObligationAttemptLedgerError(
                f"delivery_occurrence_parent_oracle_missing:{finding_id}"
            )
        if outcome_ref not in {
            _text(value)
            for value in _list(parent.get("violation_outcome_refs"))
            if _text(value)
        }:
            raise _core.ObligationAttemptLedgerError(
                f"delivery_occurrence_parent_outcome_missing:{finding_id}"
            )
    return {
        "finding_id": finding_id,
        "outcome_ref": outcome_ref,
        "gate_receipt_id": _text(validated.get("gate_receipt_id")),
        "gate_output_fingerprint": _text(validated.get("output_fingerprint")),
        "gate_receipt": dict(validated),
        "delivery_evidence_bundle": bundle,
        "parent_oracle_receipt": dict(parent),
    }


def delivery_occurrence_views(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one synthetic attempt view per independently gated occurrence."""
    row = _dict(attempt)
    occurrences = [
        _dict(item)
        for item in _list(row.get("delivery_occurrences"))
        if isinstance(item, dict)
    ]
    if not occurrences:
        return [dict(row)] if _text(row.get("finding_id")) else []
    views: list[dict[str, Any]] = []
    for occurrence in occurrences:
        view = {
            key: value
            for key, value in row.items()
            if key not in {
                "delivery_occurrences",
                "delivery_occurrence_count",
                "delivery_occurrence_finding_ids",
            }
        }
        view.update(
            {
                "finding_id": _text(occurrence.get("finding_id")),
                "outcome_ref": _text(occurrence.get("outcome_ref")),
                "gate_receipt_id": _text(occurrence.get("gate_receipt_id")),
                "output_fingerprint": _text(
                    occurrence.get("gate_output_fingerprint")
                ),
                "gate_receipt": dict(_dict(occurrence.get("gate_receipt"))),
                "delivery_evidence_bundle": dict(
                    _dict(occurrence.get("delivery_evidence_bundle"))
                ),
            }
        )
        views.append(view)
    return views


def _enrich_attempt_occurrences(
    ledger: dict[str, Any],
    execution_results: Mapping[str, Any],
) -> dict[str, Any]:
    output = dict(ledger)
    execution_by_id = {
        _text(key): _dict(value) for key, value in execution_results.items()
    }
    attempts: list[dict[str, Any]] = []
    for raw_attempt in _list(output.get("attempts")):
        attempt = dict(_dict(raw_attempt))
        execution = execution_by_id.get(_text(attempt.get("obligation_id")), {})
        raw_occurrences = [
            _dict(item)
            for item in _list(execution.get("delivery_occurrences"))
            if isinstance(item, dict)
            and _text(_dict(_dict(item).get("gate_receipt")).get("status"))
            == "DELIVERABLE"
        ]
        if raw_occurrences:
            canonical = [
                _validate_occurrence_row(item, attempt=attempt)
                for item in raw_occurrences
            ]
            canonical.sort(
                key=lambda item: (
                    _text(item.get("outcome_ref")),
                    _text(item.get("finding_id")),
                )
            )
            finding_ids = [_text(item.get("finding_id")) for item in canonical]
            outcome_refs = [_text(item.get("outcome_ref")) for item in canonical]
            if (
                len(finding_ids) != len(set(finding_ids))
                or len(outcome_refs) != len(set(outcome_refs))
            ):
                raise _core.ObligationAttemptLedgerError(
                    "delivery_occurrence_identity_duplicate"
                )
            if _text(attempt.get("finding_id")) not in finding_ids:
                raise _core.ObligationAttemptLedgerError(
                    "primary_delivery_occurrence_missing"
                )
            attempt["delivery_occurrences"] = canonical
            attempt["delivery_occurrence_count"] = len(canonical)
            attempt["delivery_occurrence_finding_ids"] = sorted(finding_ids)
        attempt.pop("attempt_fingerprint", None)
        attempt["attempt_fingerprint"] = _core._fingerprint(attempt)
        attempts.append(attempt)
    output["attempts"] = attempts
    output.pop("ledger_fingerprint", None)
    output["ledger_fingerprint"] = _core._fingerprint(output)
    return output


def build_obligation_attempt_ledger(
    *,
    mainline_run: dict[str, Any],
    selected: list[dict[str, Any]],
    compile_results: dict[str, Any],
    execution_results: dict[str, Any],
    gate_results: dict[str, Any],
) -> dict[str, Any]:
    base = _original_build_obligation_attempt_ledger(
        mainline_run=mainline_run,
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    enriched = _enrich_attempt_occurrences(base, execution_results)
    return validate_obligation_attempt_ledger(enriched)


def validate_obligation_attempt_ledger(
    ledger: dict[str, Any],
) -> dict[str, Any]:
    value = _original_validate_obligation_attempt_ledger(ledger)
    for raw_attempt in _list(value.get("attempts")):
        attempt = _dict(raw_attempt)
        occurrences = [
            _dict(item)
            for item in _list(attempt.get("delivery_occurrences"))
            if isinstance(item, dict)
        ]
        if not occurrences:
            continue
        if _text(attempt.get("terminal_status")) != "DELIVERABLE":
            raise _core.ObligationAttemptLedgerError(
                "nondeliverable_attempt_has_delivery_occurrences"
            )
        validated = [
            _validate_occurrence_row(item, attempt=attempt) for item in occurrences
        ]
        finding_ids = sorted(_text(item.get("finding_id")) for item in validated)
        if int(attempt.get("delivery_occurrence_count") or 0) != len(validated):
            raise _core.ObligationAttemptLedgerError(
                "delivery_occurrence_count_mismatch"
            )
        if _list(attempt.get("delivery_occurrence_finding_ids")) != finding_ids:
            raise _core.ObligationAttemptLedgerError(
                "delivery_occurrence_finding_ids_mismatch"
            )
        if _text(attempt.get("finding_id")) not in finding_ids:
            raise _core.ObligationAttemptLedgerError(
                "primary_delivery_occurrence_missing"
            )
    return value


def reseal_obligation_attempt_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    resealed = _original_reseal_obligation_attempt_ledger(ledger)
    return validate_obligation_attempt_ledger(resealed)


def derive_campaign_terminal_status(ledger: dict[str, Any]) -> str:
    """Derive campaign status through this facade's validated ledger authority."""

    validated = validate_obligation_attempt_ledger(ledger)
    return _core.derive_campaign_terminal_status(validated)

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
