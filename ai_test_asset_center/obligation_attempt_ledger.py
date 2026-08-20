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
from ._obligation_attempt_ledger_single_occurrence_mechanics import (
    OBLIGATION_ATTEMPT_LEDGER_SCHEMA,
    SELECTION_STATUSES,
    TERMINAL_STATUSES,
    ObligationAttemptLedgerError,
)
from .customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    validate_customer_delivery_gate_bundle,
)
from ._delivery_validation_cache import (
    LEDGER_VALIDATION_CACHE,
    _MISSING,
    content_fingerprint,
)

_original_bind_stage_receipt_identity = _core.bind_stage_receipt_identity
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


def _mechanical_execution_gap(receipt: Any) -> bool:
    """Whether a base execution row is only a mainline-generated gap filler."""
    row = _dict(receipt)
    status = _text(row.get("status")).upper()
    reason = _text(row.get("reason_code"))
    detail = _text(row.get("detail"))
    return (
        status in {"BLOCKED", "DEFERRED"}
        and reason in {
            "BLOCKED_EXECUTION",
            "OBLIGATION_NOT_IN_PLAN",
            "OBLIGATION_BUDGET_REACHED",
        }
        and detail in {
            "compiled_obligation_has_no_execution_receipt",
            "compiled_obligation_deferred_by_execution_budget",
        }
    )


def _normalize_variant_selected_identity(
    receipt: dict[str, Any],
    *,
    base_id: str,
    variant_id: str,
    sealed: bool,
) -> dict[str, Any]:
    """Project compiler-local selection onto the formal selected obligation."""
    row = dict(receipt)
    if sealed:
        return row

    declared = _text(row.get("selected_obligation_id"))
    if declared == variant_id:
        row["selected_obligation_id"] = base_id

    nested = row.get("identity")
    if isinstance(nested, dict):
        nested_row = dict(nested)
        if _text(nested_row.get("selected_obligation_id")) == variant_id:
            nested_row["selected_obligation_id"] = base_id
        row["identity"] = nested_row
    return row


def _project_variant_stage_receipts(
    *,
    selected: list[dict[str, Any]],
    compile_results: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    gate_results: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Project one concrete compiler variant onto each selected formal base.

    Once a variant execution face is chosen, compile and gate stages must come
    from that same variant first. Falling back to a stale direct-base stage can
    splice different experiment lineages into one formal attempt. A real direct
    base execution still has precedence over every variant.
    """
    selected_ids = {
        _text(row.get("obligation_id"))
        for row in selected
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    compile_out = (
        dict(compile_results)
        if isinstance(compile_results, Mapping)
        else compile_results
    )
    execution_out = (
        dict(execution_results)
        if isinstance(execution_results, Mapping)
        else execution_results
    )
    gate_out = (
        dict(gate_results)
        if isinstance(gate_results, Mapping)
        else gate_results
    )
    if not (
        isinstance(compile_out, dict)
        and isinstance(execution_out, dict)
        and isinstance(gate_out, dict)
    ):
        return compile_out, execution_out, gate_out

    chosen_variant_by_base: dict[str, str] = {}
    for raw_id, raw_receipt in list(execution_out.items()):
        variant_id = _text(raw_id)
        base_id = _core._base_obligation_id(variant_id)
        if (
            not variant_id
            or base_id == variant_id
            or base_id not in selected_ids
            or base_id in chosen_variant_by_base
            or not isinstance(raw_receipt, dict)
        ):
            continue
        direct = execution_out.get(base_id)
        if (
            isinstance(direct, dict)
            and direct
            and not _mechanical_execution_gap(direct)
        ):
            continue
        chosen_variant_by_base[base_id] = variant_id

    for base_id, variant_id in chosen_variant_by_base.items():
        variant_execution = execution_out.get(variant_id)
        if not isinstance(variant_execution, dict):
            continue

        execution_face = _normalize_variant_selected_identity(
            variant_execution,
            base_id=base_id,
            variant_id=variant_id,
            sealed=False,
        )
        declared_executed = _text(
            execution_face.get("executed_obligation_id")
        )
        if not declared_executed:
            declared_executed = _text(
                _dict(
                    execution_face.get("delivery_execution_receipt")
                ).get("obligation_id")
            )
        if not declared_executed:
            execution_face["executed_obligation_id"] = variant_id
        execution_out[base_id] = execution_face

        # The execution face decides the lineage. Prefer the matching variant
        # compile receipt; use a base compile row only when that variant stage
        # is genuinely absent.
        compile_source = compile_out.get(variant_id)
        if not isinstance(compile_source, dict) or not compile_source:
            compile_source = compile_out.get(base_id)
        if isinstance(compile_source, dict) and compile_source:
            compile_out[base_id] = _normalize_variant_selected_identity(
                compile_source,
                base_id=base_id,
                variant_id=variant_id,
                sealed=False,
            )

        # Gate-v2 is fingerprint sealed. Re-key the matching variant gate for
        # formal ownership but never rewrite its signed payload. A base gate is
        # only a fallback when the chosen execution face has no gate stage.
        gate_source = gate_out.get(variant_id)
        if not isinstance(gate_source, dict) or not gate_source:
            gate_source = gate_out.get(base_id)
        if isinstance(gate_source, dict) and gate_source:
            is_sealed = (
                gate_source.get("schema_version")
                == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            )
            gate_out[base_id] = _normalize_variant_selected_identity(
                gate_source,
                base_id=base_id,
                variant_id=variant_id,
                sealed=is_sealed,
            )

        for mapping in (compile_out, execution_out, gate_out):
            for raw_key in list(mapping):
                key = _text(raw_key)
                if (
                    key != base_id
                    and _core._base_obligation_id(key) == base_id
                ):
                    mapping.pop(raw_key, None)

    return compile_out, execution_out, gate_out


def bind_stage_receipt_identity(
    *,
    mainline_run: dict[str, Any],
    selected: list[dict[str, Any]],
    compile_results: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    gate_results: Mapping[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Bind mainline identity after losslessly projecting compiler variants."""
    projected_compile, projected_execution, projected_gate = (
        _project_variant_stage_receipts(
            selected=selected,
            compile_results=compile_results,
            execution_results=execution_results,
            gate_results=gate_results,
        )
    )
    return _original_bind_stage_receipt_identity(
        mainline_run=mainline_run,
        selected=selected,
        compile_results=projected_compile,
        execution_results=projected_execution,
        gate_results=projected_gate,
    )


def _occurrence_bundle(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding": dict(_dict(row.get("finding"))) or None,
        "execution_receipt": dict(
            _dict(row.get("delivery_execution_receipt"))
        ),
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
        "reproduction_receipt": dict(
            _dict(row.get("reproduction_receipt"))
        ),
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
        "gate_output_fingerprint": _text(
            validated.get("output_fingerprint")
        ),
        "gate_receipt": dict(validated),
        "delivery_evidence_bundle": bundle,
        "parent_oracle_receipt": dict(parent),
    }


def delivery_occurrence_views(
    attempt: dict[str, Any],
) -> list[dict[str, Any]]:
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
        view.update({
            "finding_id": _text(occurrence.get("finding_id")),
            "outcome_ref": _text(occurrence.get("outcome_ref")),
            "gate_receipt_id": _text(
                occurrence.get("gate_receipt_id")
            ),
            "output_fingerprint": _text(
                occurrence.get("gate_output_fingerprint")
            ),
            "gate_receipt": dict(
                _dict(occurrence.get("gate_receipt"))
            ),
            "delivery_evidence_bundle": dict(
                _dict(occurrence.get("delivery_evidence_bundle"))
            ),
        })
        views.append(view)
    return views


def _enrich_attempt_occurrences(
    ledger: dict[str, Any],
    execution_results: Mapping[str, Any],
) -> dict[str, Any]:
    output = dict(ledger)
    execution_by_id = {
        _text(key): _dict(value)
        for key, value in execution_results.items()
    }
    attempts: list[dict[str, Any]] = []
    for raw_attempt in _list(output.get("attempts")):
        attempt = dict(_dict(raw_attempt))
        execution = execution_by_id.get(
            _text(attempt.get("obligation_id")),
            {},
        )
        raw_occurrences = [
            _dict(item)
            for item in _list(execution.get("delivery_occurrences"))
            if isinstance(item, dict)
            and _text(
                _dict(_dict(item).get("gate_receipt")).get("status")
            )
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
            finding_ids = [
                _text(item.get("finding_id"))
                for item in canonical
            ]
            outcome_refs = [
                _text(item.get("outcome_ref"))
                for item in canonical
            ]
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
            attempt["delivery_occurrence_finding_ids"] = sorted(
                finding_ids
            )
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
    cache_key = content_fingerprint(_dict(ledger))
    cached = LEDGER_VALIDATION_CACHE.get(cache_key)
    if cached is not _MISSING:
        return dict(cached)
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
            _validate_occurrence_row(item, attempt=attempt)
            for item in occurrences
        ]
        finding_ids = sorted(
            _text(item.get("finding_id")) for item in validated
        )
        if int(attempt.get("delivery_occurrence_count") or 0) != len(
            validated
        ):
            raise _core.ObligationAttemptLedgerError(
                "delivery_occurrence_count_mismatch"
            )
        if (
            _list(attempt.get("delivery_occurrence_finding_ids"))
            != finding_ids
        ):
            raise _core.ObligationAttemptLedgerError(
                "delivery_occurrence_finding_ids_mismatch"
            )
        if _text(attempt.get("finding_id")) not in finding_ids:
            raise _core.ObligationAttemptLedgerError(
                "primary_delivery_occurrence_missing"
            )
    LEDGER_VALIDATION_CACHE.put(cache_key, value)
    return value


def reseal_obligation_attempt_ledger(
    ledger: dict[str, Any],
) -> dict[str, Any]:
    resealed = _original_reseal_obligation_attempt_ledger(ledger)
    return validate_obligation_attempt_ledger(resealed)


def derive_campaign_terminal_status(ledger: dict[str, Any]) -> str:
    """Derive campaign status through this facade's validated ledger authority."""
    validated = validate_obligation_attempt_ledger(ledger)
    return _core.derive_campaign_terminal_status(validated)


__all__ = [
    "OBLIGATION_ATTEMPT_LEDGER_SCHEMA",
    "SELECTION_STATUSES",
    "TERMINAL_STATUSES",
    "ObligationAttemptLedgerError",
    "bind_stage_receipt_identity",
    "build_obligation_attempt_ledger",
    "delivery_occurrence_views",
    "derive_campaign_terminal_status",
    "reseal_obligation_attempt_ledger",
    "validate_obligation_attempt_ledger",
]
