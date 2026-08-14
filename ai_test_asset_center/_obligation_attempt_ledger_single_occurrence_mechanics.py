"""Authoritative terminal accounting for selected discovery obligations."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Mapping

from .operational_receipts import (
    OperationalReceiptError,
    validate_execution_operational_receipt,
)
from .customer_delivery_gate import LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    DeliveryGateV2Error,
    validate_customer_delivery_gate_bundle,
)
from .blocker_attribution import (
    REASON_CODE_REGISTRY_SCHEMA,
    profile_reason_code,
)


OBLIGATION_ATTEMPT_LEDGER_SCHEMA = "qualibug.obligation-attempt-ledger.v1"
TERMINAL_STATUSES = frozenset({
    "DELIVERABLE",
    "REJECTED",
    "BLOCKED",
    "DEFERRED",
    "HARNESS_FAILED",
})
SELECTION_STATUSES = frozenset({
    "SELECTED",
    "DEFERRED_NOT_SELECTED",
    "COMPILE_BLOCKED",
    "PLAN_BLOCKED",
})
_STAGE_STATUSES = {
    "compile": frozenset({"COMPILED", "BLOCKED", "DEFERRED", "HARNESS_FAILED"}),
    "execution": frozenset({"EXECUTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED", "DELIVERABLE"}),
    "gate": frozenset(TERMINAL_STATUSES),
}
_COST_COVERAGE_STATUSES = frozenset({"MEASURED", "PARTIAL", "UNKNOWN"})
_IDENTITY_FIELDS = (
    "run_id",
    "campaign_id",
    "target_id",
    "environment_id",
    "policy_version",
    "evaluation_mode",
    "source_snapshot_hash",
    "mainline_contract_fingerprint",
)


class ObligationAttemptLedgerError(ValueError):
    """Attempt receipts disagree or cannot prove terminal coverage."""


def _text(value: Any) -> str:
    return str(value or "").strip()


_VARIANT_OBLIGATION_RE = re.compile(r"^(.+?)__v_[a-f0-9]+$")


def _base_obligation_id(obligation_id: str) -> str:
    """Collapse a compiler-expanded variant id to its base obligation id.

    Validation field-constraint expansion and coverage-unit arm derivation
    compile experiments under variant ids (``obl_x__v_<digest>``). The ledger
    account scope selects the base obligation, so the variant is one executable
    face of that base — never a separate, foreign obligation.
    """
    match = _VARIANT_OBLIGATION_RE.match(_text(obligation_id))
    return match.group(1) if match else _text(obligation_id)


def _collapse_variant_receipts(
    by_id: dict[str, dict[str, Any]],
    *,
    selected_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Fold compiler-expanded variant receipts into their selected base.

    Validation field-constraint expansion compiles and executes experiments
    under variant obligation ids while the accounting scope selects the base
    obligation. A variant receipt is therefore one face of the selected base,
    not a foreign row. Fold each selected-base variant into its base (the
    base's own receipt wins; otherwise the first variant in insertion order).
    A variant whose base is not selected is left untouched so the fail-closed
    foreign check still rejects genuinely foreign receipts.
    """
    collapsed: dict[str, dict[str, Any]] = {}
    variants: dict[str, list[dict[str, Any]]] = {}
    for obligation_id, receipt in by_id.items():
        base = _base_obligation_id(obligation_id)
        if base == obligation_id:
            collapsed[obligation_id] = receipt
        elif base in selected_ids:
            variants.setdefault(base, []).append(receipt)
        else:
            collapsed[obligation_id] = receipt
    for base, receipts in variants.items():
        if base not in collapsed:
            collapsed[base] = dict(receipts[0])
    return collapsed


def _object(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ObligationAttemptLedgerError(f"{field}_not_object")
    return dict(value)

def _attempt_row(row: dict[str, Any]) -> dict[str, Any]:
    """Resolve an obligation-attempt row in either persisted shape.

    ``build_obligation_attempt_ledger`` writes FLAT rows (fields directly on
    the row, e.g. ``executed_obligation_id``), while historical rows nest the
    attempt under ``obligation_attempt``. Reseal/validate must accept both —
    the nested shape wins when present, otherwise the row itself is the
    attempt (fingerprint and identity fields live at the same level).
    """
    if not isinstance(row, dict):
        # TEMP-DIAG: non-dict row in attempts (str/None observed in run14)
        import sys as _sys
        print(
            "ATTEMPT_ROW_DIAG type=%s repr=%s"
            % (type(row).__name__, repr(row)[:120]),
            file=_sys.stderr,
            flush=True,
        )
        return {"_invalid_row": row}
    nested = row.get("obligation_attempt")
    if isinstance(nested, dict):
        return nested
    return row



def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_DIAGNOSTIC_STEP_LIMIT = 20


def _diagnostic_rows(value: Any, limit: int | None = None) -> list[dict[str, Any]]:
    rows = [dict(row) for row in (value or []) if isinstance(row, dict)]
    return rows[:limit] if limit is not None else rows


def _execution_diagnostic_bundle(
    execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Retain execution evidence for attempts that never reach the delivery gate.

    ``delivery_evidence_bundle`` is only built when a customer delivery gate
    receipt exists, so every blocked or rejected attempt discarded the very
    receipts and steps that explain *why* it was blocked. That left the
    blocked funnel opaque: the ledger recorded a reason code and fingerprints
    with no observable evidence behind them, and diagnosing any blocked path
    required re-running the whole scan.

    This carries already-captured evidence through verbatim. Nothing is
    synthesised, and the sealed delivery bundle is left untouched.
    """

    contracts = _diagnostic_rows(execution_receipt.get("contract_evidence_receipts"))
    observers = _diagnostic_rows(execution_receipt.get("observer_receipts"))
    all_steps = _diagnostic_rows(execution_receipt.get("steps"))
    steps = all_steps[:_DIAGNOSTIC_STEP_LIMIT]
    if not (contracts or observers or steps):
        return {}

    bundle: dict[str, Any] = {}
    if contracts:
        bundle["contract_evidence_receipts"] = contracts
    if observers:
        bundle["observer_receipts"] = observers
    if steps:
        bundle["steps"] = steps
        if len(all_steps) > len(steps):
            # Keep truncation visible instead of silently presenting a
            # partial step list as if it were complete.
            bundle["steps_truncated_from"] = len(all_steps)
    return bundle


def _reason_detail(receipt: dict[str, Any]) -> str:
    row = _object(receipt, field="reason_detail_receipt")
    for key in ("reason_detail", "detail", "blocked_detail", "error"):
        value = _text(row.get(key))
        if value:
            return value[:500]
    raw_missing = row.get("missing_requirements")
    missing_values = raw_missing if isinstance(raw_missing, list) else [raw_missing]
    missing = [_text(value) for value in missing_values if _text(value)]
    if missing:
        return ",".join(missing)[:500]
    return ""


def _receipt_map(value: Any, *, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise ObligationAttemptLedgerError(f"{field}_not_object")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_receipt in value.items():
        obligation_id = _text(raw_key)
        if not obligation_id:
            raise ObligationAttemptLedgerError(f"{field}_obligation_id_missing")
        normalized[obligation_id] = _object(raw_receipt, field=f"{field}:{obligation_id}")
    return normalized


def _identity_value(field: str, *rows: dict[str, Any]) -> str:
    """Read one identity value without deriving it from free-form detail."""

    values: set[str] = set()
    for row in rows:
        value = _text(row.get(field))
        if value:
            values.add(value)
    if len(values) > 1:
        raise ObligationAttemptLedgerError(
            f"identity_value_conflict:{field}"
        )
    return next(iter(values), "")


def _source_snapshot_hash(*rows: dict[str, Any]) -> str:
    direct = _identity_value("source_snapshot_hash", *rows)
    if direct:
        return direct
    direct = _identity_value("source_hash", *rows)
    if direct:
        return direct
    source_refs: list[dict[str, Any]] = []
    for row in rows:
        source_refs.extend(
            value
            for value in row.get("source_refs", []) or []
            if isinstance(value, dict)
        )
    values = {
        _text(ref.get("source_snapshot_hash") or ref.get("source_hash"))
        for ref in source_refs
        if _text(ref.get("source_snapshot_hash") or ref.get("source_hash"))
    }
    # Multiple source assets are not one snapshot hash.  Keep the missing
    # field visible instead of inventing a composite identity here.
    return next(iter(values)) if len(values) == 1 else ""


def _run_identity(
    run: dict[str, Any],
    *rows: dict[str, Any],
) -> dict[str, Any]:
    source_snapshot_hash = _source_snapshot_hash(run, *rows)
    identity = {
        "run_id": _text(run.get("run_id")),
        "campaign_id": _text(run.get("campaign_id")),
        "target_id": _text(run.get("target_id")),
        "environment_id": _text(run.get("environment_id")),
        "policy_version": _text(run.get("policy_version")),
        "evaluation_mode": _text(run.get("evaluation_mode")),
        "source_snapshot_hash": source_snapshot_hash,
        "mainline_contract_fingerprint": _text(
            run.get("contract_fingerprint")
            or run.get("mainline_contract_fingerprint")
        ),
    }
    identity["missing_fields"] = [
        field
        for field in _IDENTITY_FIELDS
        if not _text(identity.get(field))
    ]
    identity["status"] = "COMPLETE" if not identity["missing_fields"] else "INCOMPLETE"
    selected_obligation_id = next(
        (
            _text(row.get("obligation_id"))
            for row in rows
            if _text(row.get("obligation_id"))
        ),
        "",
    )
    executed_obligation_ids: set[str] = set()
    for row in rows:
        nested_delivery = _object(
            row.get("delivery_execution_receipt"),
            field="delivery_execution_receipt",
        )
        nested_identity = _object(
            row.get("identity"),
            field="stage_identity",
        )
        candidates = (
            _text(row.get("executed_obligation_id")),
            _text(nested_delivery.get("obligation_id")),
            _text(nested_identity.get("executed_obligation_id")),
            _text(nested_identity.get("obligation_id")),
            _text(row.get("obligation_id")),
        )
        for candidate in candidates:
            if candidate and candidate != selected_obligation_id:
                executed_obligation_ids.add(candidate)
    if len(executed_obligation_ids) > 1:
        raise ObligationAttemptLedgerError(
            "identity_value_conflict:executed_obligation_id"
        )
    if executed_obligation_ids:
        identity["executed_obligation_id"] = next(iter(executed_obligation_ids))
    return identity


def bind_stage_receipt_identity(
    *,
    mainline_run: dict[str, Any],
    selected: list[dict[str, Any]],
    compile_results: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    gate_results: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Bind the immutable run identity onto mainline-owned stage receipts.

    Stage executors often return a narrow receipt containing only their local
    execution ID.  The mainline coordinator is the authority that owns the
    immutable run contract, so it adds the missing identity envelope before
    the ledger seals the stage chain.  Existing conflicting values are never
    overwritten; the ledger builder will reject them fail-closed.
    """

    run = _object(mainline_run, field="mainline_run")
    identity = _run_identity(run)
    selected_ids, _ = _selected_rows(selected)
    selected_set = set(selected_ids)

    executed_by_selected: dict[str, str] = {}
    for raw_id, raw_receipt in execution_results.items():
        selected_id = _text(raw_id)
        receipt = _object(
            raw_receipt,
            field=f"execution_receipt:{selected_id or 'MISSING'}",
        )
        declared_selected = _text(receipt.get("selected_obligation_id"))
        if declared_selected and declared_selected != selected_id:
            raise ObligationAttemptLedgerError(
                f"selected_obligation_identity_mismatch:{selected_id}"
            )
        executed_id = _text(receipt.get("executed_obligation_id"))
        if not executed_id:
            executed_id = _text(
                _object(
                    receipt.get("delivery_execution_receipt"),
                    field=f"delivery_execution_receipt:{selected_id or 'MISSING'}",
                ).get("obligation_id")
            )
        if executed_id and executed_id != selected_id:
            prior = executed_by_selected.get(selected_id)
            if prior and prior != executed_id:
                raise ObligationAttemptLedgerError(
                    f"executed_obligation_identity_conflict:{selected_id}"
                )
            executed_by_selected[selected_id] = executed_id

    def _existing_obligation_ids(
        receipt: dict[str, Any],
        nested_identity: dict[str, Any],
    ) -> set[str]:
        return {
            value
            for value in (
                _text(receipt.get("obligation_id")),
                _text(nested_identity.get("obligation_id")),
            )
            if value
        }

    def _declared_selected_ids(
        receipt: dict[str, Any],
        nested_identity: dict[str, Any],
    ) -> set[str]:
        return {
            value
            for value in (
                _text(receipt.get("selected_obligation_id")),
                _text(nested_identity.get("selected_obligation_id")),
            )
            if value
        }

    def bind(
        receipts: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        if not isinstance(receipts, Mapping):
            raise ObligationAttemptLedgerError("stage_receipts_not_mapping")
        for raw_id, raw_receipt in receipts.items():
            obligation_id = _text(raw_id)
            receipt = _object(
                raw_receipt,
                field=f"stage_receipt:{obligation_id or 'MISSING'}",
            )
            if obligation_id not in selected_set:
                output[obligation_id] = receipt
                continue
            nested = receipt.get("identity")
            if nested is not None and not isinstance(nested, dict):
                raise ObligationAttemptLedgerError(
                    f"stage_identity_not_object:{obligation_id}"
                )
            nested_identity = dict(nested) if isinstance(nested, dict) else {}
            declared_selected_ids = _declared_selected_ids(
                receipt,
                nested_identity,
            )
            if declared_selected_ids and declared_selected_ids != {obligation_id}:
                raise ObligationAttemptLedgerError(
                    f"selected_obligation_identity_mismatch:{obligation_id}"
                )
            existing_obligation_ids = _existing_obligation_ids(
                receipt,
                nested_identity,
            )
            expected_executed = executed_by_selected.get(obligation_id, "")
            allowed_obligation_ids = {obligation_id}
            if expected_executed:
                allowed_obligation_ids.add(expected_executed)
            if not existing_obligation_ids.issubset(allowed_obligation_ids):
                raise ObligationAttemptLedgerError(
                    f"stage_obligation_identity_mismatch:{obligation_id}"
                )
            declared_executed = {
                value
                for value in (
                    _text(receipt.get("executed_obligation_id")),
                    _text(nested_identity.get("executed_obligation_id")),
                )
                if value
            }
            if declared_executed and declared_executed != {
                expected_executed or obligation_id
            }:
                raise ObligationAttemptLedgerError(
                    f"executed_obligation_identity_mismatch:{obligation_id}"
                )
            for field in _IDENTITY_FIELDS:
                expected = _text(identity.get(field))
                top_level = _text(receipt.get(field))
                nested_value = _text(nested_identity.get(field))
                if top_level and nested_value and top_level != nested_value:
                    raise ObligationAttemptLedgerError(
                        f"stage_identity_value_conflict:{obligation_id}:{field}"
                    )
                if expected and not top_level and not nested_value:
                    nested_identity[field] = expected
            is_sealed_gate = (
                receipt.get("schema_version") == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            )
            if is_sealed_gate:
                # Gate-v2 receipts are fingerprint-sealed. Their payload cannot
                # accept the normal identity envelope without invalidating the
                # receipt fingerprint, so carry a separately named mainline
                # stage-binding receipt. The ledger consumes it for stage
                # continuity and strips it before validating the sealed gate.
                stage_binding = {
                    field: expected
                    for field in _IDENTITY_FIELDS
                    if (expected := _text(identity.get(field)))
                }
                stage_binding.update(
                    {
                        "obligation_id": obligation_id,
                        "selected_obligation_id": obligation_id,
                        "identity_binding_source": (
                            "immutable_mainline_run_contract"
                        ),
                    }
                )
                if expected_executed:
                    stage_binding["executed_obligation_id"] = expected_executed
                existing_binding = receipt.get("stage_identity_receipt")
                if existing_binding is not None:
                    if not isinstance(existing_binding, dict):
                        raise ObligationAttemptLedgerError(
                            f"stage_identity_receipt_not_object:{obligation_id}:gate"
                        )
                    for field, expected in stage_binding.items():
                        observed = _text(existing_binding.get(field))
                        if observed and observed != _text(expected):
                            raise ObligationAttemptLedgerError(
                                f"stage_identity_value_conflict:{obligation_id}:gate:{field}"
                            )
                receipt["stage_identity_receipt"] = stage_binding
                output[obligation_id] = receipt
                continue
            nested_identity["obligation_id"] = obligation_id
            if expected_executed:
                nested_identity["executed_obligation_id"] = expected_executed
            nested_identity["selected_obligation_id"] = obligation_id
            nested_identity["identity_binding_source"] = (
                "immutable_mainline_run_contract"
            )
            receipt["identity"] = nested_identity
            output[obligation_id] = receipt
        return output

    return bind(compile_results), bind(execution_results), bind(gate_results)


def _stage_identity(
    *,
    stage: str,
    obligation_id: str,
    receipt: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    nested_identity = receipt.get("identity")
    if nested_identity is not None and not isinstance(nested_identity, dict):
        raise ObligationAttemptLedgerError(
            f"stage_identity_not_object:{obligation_id}:{stage}"
        )
    nested_identity = nested_identity if isinstance(nested_identity, dict) else {}
    stage_binding = receipt.get("stage_identity_receipt")
    if stage_binding is not None and not isinstance(stage_binding, dict):
        raise ObligationAttemptLedgerError(
            f"stage_identity_receipt_not_object:{obligation_id}:{stage}"
        )
    stage_binding = stage_binding if isinstance(stage_binding, dict) else {}
    identity_rows = (receipt, nested_identity, stage_binding)

    # A stage identity must describe what the stage receipt actually carried.
    # The immutable run identity remains available as ``expected_identity`` but
    # must not be copied into the observed fields: doing that turns a missing
    # stage binding into a false continuity PASS.
    observed: dict[str, str] = {}
    observed_fields: list[str] = []
    for field in _IDENTITY_FIELDS:
        values = {
            _text(row.get(field))
            for row in identity_rows
            if _text(row.get(field))
        }
        if len(values) > 1:
            raise ObligationAttemptLedgerError(
                f"stage_identity_value_conflict:{obligation_id}:{stage}:{field}"
            )
        value = next(iter(values), "")
        if value:
            observed[field] = value
            observed_fields.append(field)
        expected = _text(identity.get(field))
        if expected and value and expected != value:
            raise ObligationAttemptLedgerError(
                f"stage_identity_mismatch:{obligation_id}:{stage}:{field}"
            )

    declared_selected_ids = {
        value
        for value in (
            _text(receipt.get("selected_obligation_id")),
            _text(nested_identity.get("selected_obligation_id")),
            _text(stage_binding.get("selected_obligation_id")),
        )
        if value
    }
    if declared_selected_ids and declared_selected_ids != {obligation_id}:
        raise ObligationAttemptLedgerError(
            f"selected_obligation_identity_mismatch:{obligation_id}:{stage}"
        )
    expected_executed = _text(identity.get("executed_obligation_id"))
    declared_executed_ids = {
        value
        for value in (
            _text(receipt.get("executed_obligation_id")),
            _text(nested_identity.get("executed_obligation_id")),
            _text(stage_binding.get("executed_obligation_id")),
        )
        if value
    }
    if declared_executed_ids and declared_executed_ids != {
        expected_executed or obligation_id
    }:
        raise ObligationAttemptLedgerError(
            f"executed_obligation_identity_mismatch:{obligation_id}:{stage}"
        )
    observed_obligation_ids = {
        _text(row.get("obligation_id"))
        for row in identity_rows
        if _text(row.get("obligation_id"))
    }
    observed_executed_ids = observed_obligation_ids - {obligation_id}
    if observed_executed_ids and (
        not expected_executed or observed_executed_ids != {expected_executed}
    ):
        raise ObligationAttemptLedgerError(
            f"stage_obligation_identity_mismatch:{obligation_id}:{stage}"
        )

    expected_identity = {
        field: _text(identity.get(field))
        for field in _IDENTITY_FIELDS
        if _text(identity.get(field))
    }
    missing_fields = [
        field
        for field in _IDENTITY_FIELDS
        if not _text(identity.get(field)) or not _text(observed.get(field))
    ]
    result = dict(observed)
    result["obligation_id"] = obligation_id
    if expected_executed:
        result["executed_obligation_id"] = expected_executed
    result["status"] = "COMPLETE" if not missing_fields else "INCOMPLETE"
    result["missing_fields"] = missing_fields
    result["observed_fields"] = sorted(observed_fields)
    result["expected_identity"] = expected_identity
    identity_binding_source = _text(
        nested_identity.get("identity_binding_source")
        or stage_binding.get("identity_binding_source")
    )
    if identity_binding_source:
        result["identity_binding_source"] = identity_binding_source
    result["observation_source"] = (
        "mainline_contract_binding"
        if identity_binding_source
        else "stage_receipt"
        if len(observed_fields) == len(_IDENTITY_FIELDS)
        else "stage_receipt_partial"
        if observed_fields
        else "immutable_run_contract_only"
    )
    for field in ("experiment_id", "execution_id", "receipt_id", "gate_receipt_id"):
        value = _text(receipt.get(field))
        if value:
            result[field] = value
    for field in ("experiment_id", "execution_id", "finding_id"):
        value = _text(nested_identity.get(field))
        if value:
            result[field] = value
    return result


def _validated_gate_bundle(
    *,
    obligation_id: str,
    run: dict[str, Any],
    execution_receipt: dict[str, Any],
    gate_receipt: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate v2 evidence; legacy non-deliverable gates stay diagnostic-only."""

    status = _text(gate_receipt.get("status")).upper()
    if gate_receipt.get("schema_version") != CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
        if status == "DELIVERABLE":
            if (
                _text(run.get("mainline_authority")) == "legacy_champion"
                and gate_receipt.get("schema_version")
                == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            ):
                return dict(gate_receipt), {}
            raise ObligationAttemptLedgerError(
                f"formal_gate_v2_required:{obligation_id}"
            )
        if _text(gate_receipt.get("finding_id")):
            raise ObligationAttemptLedgerError(
                f"legacy_nondeliverable_finding_id_present:{obligation_id}"
            )
        return dict(gate_receipt), {}

    evidence_bundle = {
        "finding": (
            dict(execution_receipt.get("finding"))
            if isinstance(execution_receipt.get("finding"), dict)
            else None
        ),
        "execution_receipt": _object(
            execution_receipt.get("delivery_execution_receipt"),
            field=f"delivery_execution_receipt:{obligation_id}",
        ),
        "contract_evidence_receipts": [
            dict(value)
            for value in execution_receipt.get("contract_evidence_receipts", []) or []
            if isinstance(value, dict)
        ],
        "observer_receipts": [
            dict(value)
            for value in execution_receipt.get("observer_receipts", []) or []
            if isinstance(value, dict)
        ],
        "oracle_receipt": _object(
            execution_receipt.get("oracle_receipt"),
            field=f"oracle_receipt:{obligation_id}",
        ),
        "reproduction_receipt": _object(
            execution_receipt.get("reproduction_receipt"),
            field=f"reproduction_receipt:{obligation_id}",
        ),
    }
    try:
        gate_for_validation = dict(gate_receipt)
        # This is a mainline-owned stage binding for the sealed gate, not part
        # of the gate's signed payload. Keep the signed receipt unchanged while
        # retaining the binding in the ledger stage projection.
        gate_for_validation.pop("stage_identity_receipt", None)
        validated = validate_customer_delivery_gate_bundle(
            gate_for_validation,
            **evidence_bundle,
        )
    except (DeliveryGateV2Error, ValueError) as exc:
        raise ObligationAttemptLedgerError(
            f"delivery_gate_bundle_invalid:{obligation_id}:{exc}"
        ) from exc
    identity = _object(validated.get("identity"), field="delivery_gate_identity")
    delivery_execution_receipt = evidence_bundle["execution_receipt"]
    executed_obligation_id = _text(
        delivery_execution_receipt.get("obligation_id")
    )
    if not executed_obligation_id:
        raise ObligationAttemptLedgerError(
            f"delivery_execution_obligation_id_missing:{obligation_id}"
        )
    declared_selected_obligation_id = _text(
        execution_receipt.get("selected_obligation_id") or obligation_id
    )
    if declared_selected_obligation_id != obligation_id:
        raise ObligationAttemptLedgerError(
            f"selected_obligation_identity_mismatch:{obligation_id}"
        )
    declared_executed_obligation_id = _text(
        execution_receipt.get("executed_obligation_id")
        or executed_obligation_id
    )
    if declared_executed_obligation_id != executed_obligation_id:
        raise ObligationAttemptLedgerError(
            f"executed_obligation_identity_mismatch:{obligation_id}"
        )
    expected_pairs = {
        "run_id": _text(run.get("run_id")),
        "campaign_id": _text(run.get("campaign_id")),
        "mainline_contract_fingerprint": _text(run.get("contract_fingerprint")),
        "obligation_id": executed_obligation_id,
        "execution_id": _text(execution_receipt.get("execution_id")),
        "experiment_id": _text(execution_receipt.get("experiment_id")),
    }
    for field, expected in expected_pairs.items():
        if not expected or _text(identity.get(field)) != expected:
            raise ObligationAttemptLedgerError(
                f"delivery_gate_identity_mismatch:{obligation_id}:{field}"
            )
    return validated, evidence_bundle


def _stage_record(
    stage: str,
    receipt: dict[str, Any],
    *,
    obligation_id: str,
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    if not receipt:
        return None
    status = _text(receipt.get("status")).upper()
    if status not in _STAGE_STATUSES[stage]:
        raise ObligationAttemptLedgerError(
            f"{stage}_status_invalid:{status or 'MISSING'}"
        )
    receipt_id = _text(
        receipt.get(f"{stage}_receipt_id")
        or receipt.get("receipt_id")
        or (receipt.get("gate_receipt_id") if stage == "gate" else "")
    )
    elapsed = receipt.get("elapsed_ms")
    if elapsed is not None:
        try:
            elapsed = max(0, int(elapsed))
        except (TypeError, ValueError) as exc:
            raise ObligationAttemptLedgerError(
                f"{stage}_elapsed_ms_invalid"
            ) from exc
    stage_identity = _stage_identity(
        stage=stage,
        obligation_id=obligation_id,
        receipt=receipt,
        identity=identity,
    )
    return {
        "stage": stage,
        "status": status,
        "identity": stage_identity,
        "identity_status": _text(stage_identity.get("status")),
        "identity_missing_fields": list(stage_identity.get("missing_fields", [])),
        "reason_code": _text(receipt.get("reason_code")),
        "reason_detail": _reason_detail(receipt),
        "receipt_id": receipt_id,
        "input_fingerprint": _text(receipt.get("input_fingerprint")),
        "output_fingerprint": _text(receipt.get("output_fingerprint")),
        "elapsed_ms": elapsed,
    }


def _selected_rows(selected: Any) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(selected, list):
        raise ObligationAttemptLedgerError("selected_obligations_not_list")
    ids: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    for value in selected:
        row = _object(value, field="selected_obligation")
        obligation_id = _text(row.get("obligation_id"))
        ids.append(obligation_id)
        if obligation_id:
            rows[obligation_id] = row
    if not all(ids) or len(ids) != len(set(ids)):
        raise ObligationAttemptLedgerError("selected_obligation_identity_invalid")
    return ids, rows


def build_obligation_attempt_ledger(
    *,
    mainline_run: dict[str, Any],
    selected: list[dict[str, Any]],
    compile_results: dict[str, Any],
    execution_results: dict[str, Any],
    gate_results: dict[str, Any],
) -> dict[str, Any]:
    """Join stage receipts without copying raw request or response payloads."""

    run = _object(mainline_run, field="mainline_run")
    run_id = _text(run.get("run_id"))
    campaign_id = _text(run.get("campaign_id"))
    if not run_id:
        raise ObligationAttemptLedgerError("run_id_missing")
    if not campaign_id:
        raise ObligationAttemptLedgerError("campaign_id_missing")

    selected_ids, selected_by_id = _selected_rows(selected)
    selected_set = set(selected_ids)
    selected_execution_ids = {
        obligation_id
        for obligation_id, row in selected_by_id.items()
        if _text(row.get("selection_status")).upper()
        in {"", "SELECTED"}
    }
    compile_by_id = _collapse_variant_receipts(
        _receipt_map(compile_results, field="compile_results"),
        selected_ids=selected_set,
    )
    execution_by_id = _collapse_variant_receipts(
        _receipt_map(execution_results, field="execution_results"),
        selected_ids=selected_set,
    )
    gate_by_id = _collapse_variant_receipts(
        _receipt_map(gate_results, field="gate_results"),
        selected_ids=selected_set,
    )
    foreign_ids = sorted(
        (set(compile_by_id) | set(execution_by_id) | set(gate_by_id))
        - set(selected_ids)
    )
    if foreign_ids:
        raise ObligationAttemptLedgerError(
            f"foreign_obligation_receipt:{foreign_ids[0]}"
        )

    attempts: list[dict[str, Any]] = []
    for obligation_id in selected_ids:
        selected_row = selected_by_id[obligation_id]
        compile_receipt = compile_by_id.get(obligation_id, {})
        execution_receipt = execution_by_id.get(obligation_id, {})
        gate_receipt = gate_by_id.get(obligation_id, {})
        identity = _run_identity(
            run,
            selected_row,
            compile_receipt,
            execution_receipt,
            gate_receipt,
        )
        stage_records = [
            record
            for record in (
                _stage_record(
                    "compile",
                    compile_receipt,
                    obligation_id=obligation_id,
                    identity=identity,
                ),
                _stage_record(
                    "execution",
                    execution_receipt,
                    obligation_id=obligation_id,
                    identity=identity,
                ),
                _stage_record(
                    "gate",
                    gate_receipt,
                    obligation_id=obligation_id,
                    identity=identity,
                ),
            )
            if record is not None
        ]
        terminals = [
            (stage, receipt)
            for stage, receipt in (
                ("compile", compile_receipt),
                ("execution", execution_receipt),
                ("gate", gate_receipt),
            )
            if _text(receipt.get("status")).upper() in TERMINAL_STATUSES
        ]
        # When a gate receipt exists, execution DELIVERABLE is superseded by
        # the gate decision and must not count as a separate terminal.
        if (
            len(terminals) == 2
            and gate_receipt
            and _text(gate_receipt.get("status")).upper() in TERMINAL_STATUSES
        ):
            terminals = [
                (stage, receipt)
                for stage, receipt in terminals
                if not (
                    stage == "execution"
                    and _text(receipt.get("status")).upper() == "DELIVERABLE"
                )
            ]
        if len(terminals) != 1:
            code = "terminal_receipt_missing" if not terminals else "duplicate_terminal_receipt"
            import sys
            print(f"[LEDGER_DEBUG] {code}:{obligation_id}", file=sys.stderr)
            print(f"  compile_status={_text(compile_receipt.get('status'))}", file=sys.stderr)
            print(f"  execution_status={_text(execution_receipt.get('status'))}", file=sys.stderr)
            print(f"  gate_status={_text(gate_receipt.get('status'))}", file=sys.stderr)
            print(f"  terminals_after_filter={[(s, _text(r.get('status'))) for s, r in terminals]}", file=sys.stderr)
            raise ObligationAttemptLedgerError(f"{code}:{obligation_id}")

        compile_status = _text(compile_receipt.get("status")).upper()
        execution_status = _text(execution_receipt.get("status")).upper()
        if execution_receipt and compile_status != "COMPILED":
            raise ObligationAttemptLedgerError(
                f"execution_without_compiled_obligation:{obligation_id}"
            )
        if gate_receipt and execution_status not in ("EXECUTED", "DELIVERABLE"):
            raise ObligationAttemptLedgerError(
                f"gate_without_executed_obligation:{obligation_id}"
            )

        gate_evidence_bundle: dict[str, Any] = {}
        if gate_receipt:
            gate_receipt, gate_evidence_bundle = _validated_gate_bundle(
                obligation_id=obligation_id,
                run=run,
                execution_receipt=execution_receipt,
                gate_receipt=gate_receipt,
            )

        terminal_stage, terminal_receipt = terminals[0]
        if terminal_stage == "gate":
            terminal_receipt = gate_receipt
        terminal_status = _text(terminal_receipt.get("status")).upper()
        reason_code = _text(terminal_receipt.get("reason_code"))
        finding_id = _text(
            _object(terminal_receipt.get("identity"), field="gate_identity").get(
                "finding_id"
            )
            if terminal_receipt.get("schema_version")
            == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            else terminal_receipt.get("finding_id")
        )
        if terminal_status == "DELIVERABLE" and not finding_id:
            raise ObligationAttemptLedgerError(
                f"deliverable_finding_id_missing:{obligation_id}"
            )
        if terminal_status != "DELIVERABLE" and finding_id:
            raise ObligationAttemptLedgerError(
                f"nondeliverable_finding_id_present:{obligation_id}"
            )
        if terminal_status != "DELIVERABLE" and not reason_code:
            raise ObligationAttemptLedgerError(
                f"terminal_reason_code_missing:{obligation_id}"
            )
        reason_profile = (
            profile_reason_code(reason_code)
            if reason_code
            else {
                "registry_status": "NOT_APPLICABLE",
                "reason_code": "",
                "reason_family": "DELIVERABLE_OUTCOME",
                "recoverability": "NOT_APPLICABLE",
                "is_blocking": False,
                "must_remain_blocked": False,
            }
        )

        cost_coverage_status = _text(
            gate_receipt.get("cost_coverage_status")
            or execution_receipt.get("cost_coverage_status")
            or compile_receipt.get("cost_coverage_status")
            or "UNKNOWN"
        ).upper()
        if cost_coverage_status not in _COST_COVERAGE_STATUSES:
            raise ObligationAttemptLedgerError(
                f"cost_coverage_status_invalid:{obligation_id}"
            )
        elapsed_values = [
            int(record["elapsed_ms"])
            for record in stage_records
            if record["elapsed_ms"] is not None
        ]
        observation_receipt_ids = [
            _text(value)
            for value in execution_receipt.get("observation_receipt_ids", []) or []
            if _text(value)
        ]
        operational_receipt: dict[str, Any] = {}
        if execution_receipt.get("operational_receipt") is not None:
            try:
                operational_receipt = validate_execution_operational_receipt(
                    _object(
                        execution_receipt.get("operational_receipt"),
                        field=f"operational_receipt:{obligation_id}",
                    )
                )
            except OperationalReceiptError as exc:
                raise ObligationAttemptLedgerError(
                    f"operational_receipt_invalid:{obligation_id}:{exc}"
                ) from exc
        attempt: dict[str, Any] = {
            "selection_status": (
                _text(selected_row.get("selection_status")).upper()
                or "SELECTED"
            ),
            "candidate_id": _text(
                selected_row.get("candidate_id")
                or compile_receipt.get("candidate_id")
                or execution_receipt.get("candidate_id")
            ),
            "source_refs": [
                dict(value)
                for value in selected_row.get("source_refs", []) or []
                if isinstance(value, dict)
            ],
            "risk_family": _text(selected_row.get("risk_family")),
            "operation_refs": [
                _text(value)
                for value in (
                    selected_row.get("operation_refs")
                    or selected_row.get("required_operations")
                    or []
                )
                if _text(value)
            ],
            "actor_refs": [
                _text(value)
                for value in (
                    selected_row.get("actor_refs")
                    or selected_row.get("required_actors")
                    or []
                )
                if _text(value)
            ],
            "adapter": _text(
                selected_row.get("adapter")
                or selected_row.get("execution_adapter")
            ),
            "round": _text(
                selected_row.get("planning_round")
                if selected_row.get("planning_round") is not None
                else selected_row.get("round")
            ),
            "behavior_slice_id": _text(
                selected_row.get("behavior_slice_id")
                or selected_row.get("slice_id")
            ),
            "behavior_ir_refs": [
                _text(value)
                for value in selected_row.get("behavior_ir_refs", []) or []
                if _text(value)
            ],
            "obligation_id": obligation_id,
            "identity": identity,
            "executed_obligation_id": _text(
                execution_receipt.get("executed_obligation_id")
                or _object(
                    execution_receipt.get("delivery_execution_receipt"),
                    field=f"delivery_execution_receipt:{obligation_id}",
                ).get("obligation_id")
                or obligation_id
            ),
            "experiment_id": _text(
                compile_receipt.get("experiment_id")
                or execution_receipt.get("experiment_id")
                or selected_row.get("experiment_id")
            ),
            "execution_id": _text(execution_receipt.get("execution_id")),
            "observation_receipt_ids": observation_receipt_ids,
            "oracle_receipt_id": _text(
                execution_receipt.get("oracle_receipt_id")
                or gate_receipt.get("oracle_receipt_id")
            ),
            "oracle_reason_code": _text(
                execution_receipt.get("oracle_reason_code")
                or gate_receipt.get("oracle_reason_code")
            ),
            "gate_receipt_id": _text(
                gate_receipt.get("gate_receipt_id")
                or gate_receipt.get("receipt_id")
            ),
            "finding_id": finding_id,
            "terminal_stage": terminal_stage,
            "terminal_status": terminal_status,
            "reason_code": reason_code,
            "reason_detail": _reason_detail(terminal_receipt),
            "reason_family": reason_profile["reason_family"],
            "reason_registry_status": reason_profile["registry_status"],
            "reason_recoverability": reason_profile["recoverability"],
            "reason_is_blocking": bool(reason_profile["is_blocking"]),
            "reason_must_remain_blocked": bool(
                reason_profile["must_remain_blocked"]
            ),
            "input_fingerprint": _text(
                selected_row.get("input_fingerprint")
                or compile_receipt.get("input_fingerprint")
            ),
            "output_fingerprint": _text(
                gate_receipt.get("output_fingerprint")
                or execution_receipt.get("output_fingerprint")
            ),
            "receipt_refs": {
                record["stage"]: record["receipt_id"]
                for record in stage_records
                if record["receipt_id"]
            },
            "elapsed_ms": sum(elapsed_values) if elapsed_values else None,
            "cost_coverage_status": cost_coverage_status,
            "stages": stage_records,
        }
        if operational_receipt:
            attempt["operational_receipt"] = operational_receipt
        if gate_receipt.get("schema_version") in {
            CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
            LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
        }:
            if (
                gate_receipt.get("schema_version")
                == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
                and _text(run.get("mainline_authority")) != "legacy_champion"
            ):
                raise ObligationAttemptLedgerError(
                    f"legacy_gate_authority_invalid:{obligation_id}"
                )
            attempt["gate_receipt"] = dict(gate_receipt)
            if (
                gate_receipt.get("schema_version")
                == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            ):
                attempt["delivery_evidence_bundle"] = gate_evidence_bundle
        if not attempt.get("delivery_evidence_bundle"):
            # No delivery gate means no sealed bundle, but the execution
            # evidence still exists and is the only record of why this
            # attempt failed. Persist it on a separate diagnostic channel.
            diagnostic_bundle = _execution_diagnostic_bundle(execution_receipt)
            if diagnostic_bundle:
                attempt["execution_diagnostic_bundle"] = diagnostic_bundle
        attempt["attempt_fingerprint"] = _fingerprint(attempt)
        attempts.append(attempt)

    terminal_counts = dict(
        sorted(Counter(row["terminal_status"] for row in attempts).items())
    )
    ledger: dict[str, Any] = {
        "schema_version": OBLIGATION_ATTEMPT_LEDGER_SCHEMA,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "mainline_contract_fingerprint": _text(run.get("contract_fingerprint")),
        # The campaign identity is sourced from the immutable run contract.
        # Obligation rows may reference several source assets and therefore
        # must not be collapsed into a fabricated campaign snapshot hash.
        "identity": _run_identity(run),
        "selected_count": len(selected_execution_ids),
        "terminal_count": len(attempts),
        "accounted_count": len(attempts),
        "selection_status_counts": dict(
            sorted(
                Counter(
                    _text(row.get("selection_status")).upper() or "SELECTED"
                    for row in attempts
                ).items()
            )
        ),
        "complete": len(attempts) == len(selected_ids),
        "terminal_status_counts": terminal_counts,
        "attempts": attempts,
        "reason_registry": {
            "schema_version": REASON_CODE_REGISTRY_SCHEMA,
            "status": (
                "FAILED_SAFE"
                if any(
                    _text(row.get("reason_registry_status")) == "UNREGISTERED"
                    for row in attempts
                )
                else "PASS"
            ),
            "unregistered_reason_codes": sorted({
                _text(row.get("reason_code"))
                for row in attempts
                if _text(row.get("reason_registry_status")) == "UNREGISTERED"
                and _text(row.get("reason_code"))
            }),
        },
    }
    ledger["ledger_fingerprint"] = _fingerprint(ledger)
    return ledger


def reseal_obligation_attempt_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Recompute attempt/ledger fingerprints after authorized payload transforms.

    Persistence redaction may rewrite secret-bearing strings inside sealed
    receipts. Callers must reseal nested content-addressed receipts first, then
    reseal attempt/ledger fingerprints so reload validation remains fail-closed
    on identity without false fingerprint mismatches.
    """

    from .sealed_receipt_reseal import reseal_obligation_attempt_nested_receipts

    value = _object(ledger, field="obligation_attempt_ledger")
    if value.get("schema_version") != OBLIGATION_ATTEMPT_LEDGER_SCHEMA:
        raise ObligationAttemptLedgerError("obligation_attempt_ledger_schema_invalid")
    resealed_attempts: list[dict[str, Any]] = []
    raw_attempts = value.get("attempts")
    if not isinstance(raw_attempts, list):
        raise ObligationAttemptLedgerError("obligation_attempts_not_list")
    for row in raw_attempts:
        attempt = {
            key: item
            for key, item in _attempt_row(row).items()
            if key != "attempt_fingerprint"
        }
        attempt = reseal_obligation_attempt_nested_receipts(attempt)
        attempt["attempt_fingerprint"] = _fingerprint(attempt)
        resealed_attempts.append(attempt)
    resealed = {
        key: item
        for key, item in value.items()
        if key not in {"attempts", "ledger_fingerprint"}
    }
    resealed["attempts"] = resealed_attempts
    resealed["ledger_fingerprint"] = _fingerprint(resealed)
    return resealed


def validate_obligation_attempt_ledger(
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Validate schema plus immutable ledger and per-attempt fingerprints."""

    value = _object(ledger, field="obligation_attempt_ledger")
    required_root_fields = {
        "schema_version",
        "run_id",
        "campaign_id",
        "mainline_contract_fingerprint",
        "selected_count",
        "terminal_count",
        "complete",
        "terminal_status_counts",
        "attempts",
        "ledger_fingerprint",
    }
    optional_root_fields = {
        "identity",
        "reason_registry",
        "accounted_count",
        "selection_status_counts",
    }
    if set(value) - required_root_fields - optional_root_fields:
        raise ObligationAttemptLedgerError(
            "obligation_attempt_ledger_fields_invalid"
        )
    if value.get("schema_version") != OBLIGATION_ATTEMPT_LEDGER_SCHEMA:
        raise ObligationAttemptLedgerError("obligation_attempt_ledger_schema_invalid")
    observed_fingerprint = _text(value.get("ledger_fingerprint"))
    if not observed_fingerprint:
        raise ObligationAttemptLedgerError(
            "obligation_attempt_ledger_fingerprint_missing"
        )
    fingerprint_payload = {
        key: item
        for key, item in value.items()
        if key != "ledger_fingerprint"
    }
    if observed_fingerprint != _fingerprint(fingerprint_payload):
        raise ObligationAttemptLedgerError(
            "obligation_attempt_ledger_fingerprint_mismatch"
        )
    root_identity = _object(value.get("identity"), field="ledger_identity")
    if root_identity:
        for field in ("run_id", "campaign_id"):
            if _text(root_identity.get(field)) != _text(value.get(field)):
                raise ObligationAttemptLedgerError(
                    f"ledger_identity_mismatch:{field}"
                )
        contract_fingerprint = _text(value.get("mainline_contract_fingerprint"))
        identity_fingerprint = _text(
            root_identity.get("mainline_contract_fingerprint")
        )
        if contract_fingerprint and identity_fingerprint != contract_fingerprint:
            raise ObligationAttemptLedgerError(
                "ledger_identity_mismatch:mainline_contract_fingerprint"
            )
    reason_registry = _object(
        value.get("reason_registry"),
        field="ledger_reason_registry",
    )
    if reason_registry:
        if reason_registry.get("schema_version") != REASON_CODE_REGISTRY_SCHEMA:
            raise ObligationAttemptLedgerError("reason_registry_schema_invalid")
        if _text(reason_registry.get("status")) not in {"PASS", "FAILED_SAFE"}:
            raise ObligationAttemptLedgerError("reason_registry_status_invalid")
    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        raise ObligationAttemptLedgerError("obligation_attempts_not_list")
    try:
        selected_count = int(value.get("selected_count"))
        terminal_count = int(value.get("terminal_count"))
        accounted_count = int(value.get("accounted_count", terminal_count))
    except (TypeError, ValueError) as exc:
        raise ObligationAttemptLedgerError(
            "obligation_attempt_counts_invalid"
        ) from exc
    if (
        selected_count < 0
        or terminal_count < 0
        or accounted_count < 0
        or selected_count > accounted_count
        or terminal_count != accounted_count
        or terminal_count != len(attempts)
        or value.get("complete") is not True
    ):
        raise ObligationAttemptLedgerError(
            "obligation_attempt_terminal_coverage_invalid"
        )
    obligation_ids = [
        _text(_attempt_row(row).get("obligation_id"))
        for row in attempts
    ]
    if not all(obligation_ids) or len(obligation_ids) != len(set(obligation_ids)):
        raise ObligationAttemptLedgerError(
            "obligation_attempt_identity_invalid"
        )
    terminal_statuses = [
        _text(_attempt_row(row).get("terminal_status"))
        .upper()
        for row in attempts
    ]
    if any(status not in TERMINAL_STATUSES for status in terminal_statuses):
        raise ObligationAttemptLedgerError(
            "obligation_terminal_status_invalid"
        )
    expected_terminal_counts = dict(sorted(Counter(terminal_statuses).items()))
    if value.get("terminal_status_counts") != expected_terminal_counts:
        raise ObligationAttemptLedgerError(
            "obligation_terminal_status_counts_invalid"
        )
    selection_statuses = [
        _text(_attempt_row(row).get("selection_status")).upper()
        or "SELECTED"
        for row in attempts
    ]
    if any(status not in SELECTION_STATUSES for status in selection_statuses):
        raise ObligationAttemptLedgerError("obligation_selection_status_invalid")
    expected_selection_counts = dict(
        sorted(Counter(selection_statuses).items())
    )
    declared_selection_counts = value.get("selection_status_counts")
    if declared_selection_counts is not None:
        if declared_selection_counts != expected_selection_counts:
            raise ObligationAttemptLedgerError(
                "obligation_selection_status_counts_invalid"
            )
    if int(expected_selection_counts.get("SELECTED", 0)) != selected_count:
        raise ObligationAttemptLedgerError(
            "obligation_selected_count_mismatch"
        )
    for row in attempts:
        attempt = _attempt_row(row)
        obligation_id = _text(attempt.get("obligation_id"))
        attempt_identity = _object(
            attempt.get("identity"),
            field="obligation_attempt_identity",
        )
        if attempt_identity:
            if _text(attempt_identity.get("obligation_id")) not in {"", obligation_id}:
                raise ObligationAttemptLedgerError(
                    f"obligation_attempt_identity_mismatch:{obligation_id}"
                )
            for field in ("run_id", "campaign_id"):
                if _text(attempt_identity.get(field)) != _text(value.get(field)):
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_identity_mismatch:{obligation_id}:{field}"
                    )
            root_contract = _text(value.get("mainline_contract_fingerprint"))
            attempt_contract = _text(
                attempt_identity.get("mainline_contract_fingerprint")
            )
            if root_contract and attempt_contract != root_contract:
                raise ObligationAttemptLedgerError(
                    f"obligation_attempt_identity_mismatch:{obligation_id}:mainline_contract_fingerprint"
                )
        selection_status = (
            _text(attempt.get("selection_status")).upper() or "SELECTED"
        )
        if selection_status not in SELECTION_STATUSES:
            raise ObligationAttemptLedgerError(
                f"obligation_selection_status_invalid:{obligation_id}"
            )
        terminal_status = _text(attempt.get("terminal_status")).upper()
        terminal_stage = _text(attempt.get("terminal_stage"))
        if terminal_stage not in {"compile", "execution", "gate"}:
            raise ObligationAttemptLedgerError(
                f"obligation_terminal_stage_invalid:{_text(attempt.get('obligation_id'))}"
            )
        if terminal_status == "DELIVERABLE" and not _text(
            attempt.get("finding_id")
        ):
            raise ObligationAttemptLedgerError(
                f"deliverable_finding_id_missing:{_text(attempt.get('obligation_id'))}"
            )
        if terminal_status != "DELIVERABLE" and _text(
            attempt.get("finding_id")
        ):
            raise ObligationAttemptLedgerError(
                f"nondeliverable_finding_id_present:{_text(attempt.get('obligation_id'))}"
            )
        stages = attempt.get("stages")
        if not isinstance(stages, list) or not stages:
            raise ObligationAttemptLedgerError(
                f"obligation_attempt_stages_invalid:{_text(attempt.get('obligation_id'))}"
            )
        stage_names = [
            _text(_object(stage, field="obligation_attempt_stage").get("stage"))
            for stage in stages
        ]
        if len(stage_names) != len(set(stage_names)) or terminal_stage not in stage_names:
            raise ObligationAttemptLedgerError(
                f"obligation_attempt_stage_chain_invalid:{_text(attempt.get('obligation_id'))}"
            )
        for stage in stages:
            stage_value = _object(stage, field="obligation_attempt_stage")
            stage_identity = _object(
                stage_value.get("identity"),
                field="obligation_attempt_stage_identity",
            )
            if stage_identity:
                if _text(stage_identity.get("obligation_id")) != obligation_id:
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_stage_identity_mismatch:{obligation_id}"
                    )
                stage_executed_obligation_id = _text(
                    stage_identity.get("executed_obligation_id")
                )
                attempt_executed_obligation_id = _text(
                    attempt.get("executed_obligation_id") or obligation_id
                )
                if (
                    stage_executed_obligation_id
                    and stage_executed_obligation_id
                    != attempt_executed_obligation_id
                ):
                    raise ObligationAttemptLedgerError(
                        "obligation_attempt_stage_executed_identity_mismatch:"
                        f"{obligation_id}"
                    )
                for field in ("run_id", "campaign_id"):
                    if _text(stage_identity.get(field)) and _text(
                        stage_identity.get(field)
                    ) != _text(value.get(field)):
                        raise ObligationAttemptLedgerError(
                            f"obligation_attempt_stage_identity_mismatch:{obligation_id}:{field}"
                        )
                stage_identity_status = _text(
                    stage_identity.get("status")
                ).upper()
                if stage_identity_status and stage_identity_status not in {
                    "COMPLETE",
                    "INCOMPLETE",
                }:
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_stage_identity_status_invalid:{obligation_id}"
                    )
                declared_stage_status = _text(
                    stage_value.get("identity_status")
                ).upper()
                if (
                    declared_stage_status
                    and stage_identity_status
                    and declared_stage_status != stage_identity_status
                ):
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_stage_identity_status_mismatch:{obligation_id}"
                    )
                missing_fields = stage_identity.get("missing_fields")
                if missing_fields is not None and not isinstance(missing_fields, list):
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_stage_identity_missing_fields_invalid:{obligation_id}"
                    )
                if stage_identity_status == "COMPLETE" and (
                    missing_fields
                    or any(
                        not _text(stage_identity.get(field))
                        for field in _IDENTITY_FIELDS
                    )
                ):
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_stage_identity_incomplete:{obligation_id}"
                    )
        reason_code = _text(attempt.get("reason_code"))
        reason_registry_status = _text(attempt.get("reason_registry_status"))
        if reason_code and reason_registry_status:
            profile = profile_reason_code(reason_code)
            if reason_registry_status != _text(profile.get("registry_status")):
                raise ObligationAttemptLedgerError(
                    f"obligation_attempt_reason_registry_mismatch:{obligation_id}"
                )
            if _text(attempt.get("reason_family")) != _text(
                profile.get("reason_family")
            ):
                raise ObligationAttemptLedgerError(
                    f"obligation_attempt_reason_family_mismatch:{obligation_id}"
                )
        observed_attempt_fingerprint = _text(attempt.get("attempt_fingerprint"))
        expected_attempt_fingerprint = _fingerprint({
            key: item
            for key, item in attempt.items()
            if key != "attempt_fingerprint"
        })
        if observed_attempt_fingerprint != expected_attempt_fingerprint:
            obligation_id = _text(attempt.get("obligation_id")) or "MISSING"
            raise ObligationAttemptLedgerError(
                f"obligation_attempt_fingerprint_mismatch:{obligation_id}"
            )
        gate_receipt = _object(
            attempt.get("gate_receipt"),
            field="obligation_attempt_gate_receipt",
        )
        if gate_receipt:
            if (
                gate_receipt.get("schema_version")
                == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            ):
                if (
                    _text(gate_receipt.get("status")).upper()
                    != _text(attempt.get("terminal_status")).upper()
                ):
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_gate_projection_mismatch:{_text(attempt.get('obligation_id'))}"
                    )
                if (
                    _text(attempt.get("terminal_status")).upper() == "DELIVERABLE"
                    and _text(gate_receipt.get("finding_id"))
                    != _text(attempt.get("finding_id"))
                ):
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_gate_projection_mismatch:{_text(attempt.get('obligation_id'))}"
                    )
            elif (
                gate_receipt.get("schema_version")
                == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            ):
                bundle = _object(
                    attempt.get("delivery_evidence_bundle"),
                    field="obligation_attempt_delivery_evidence_bundle",
                )
                try:
                    validated_gate = validate_customer_delivery_gate_bundle(
                        gate_receipt,
                        finding=(
                            dict(bundle.get("finding"))
                            if isinstance(bundle.get("finding"), dict)
                            else None
                        ),
                        execution_receipt=_object(
                            bundle.get("execution_receipt"),
                            field="ledger_delivery_execution_receipt",
                        ),
                        contract_evidence_receipts=[
                            dict(item)
                            for item in bundle.get("contract_evidence_receipts", []) or []
                            if isinstance(item, dict)
                        ],
                        observer_receipts=[
                            dict(item)
                            for item in bundle.get("observer_receipts", []) or []
                            if isinstance(item, dict)
                        ],
                        oracle_receipt=_object(
                            bundle.get("oracle_receipt"),
                            field="ledger_oracle_receipt",
                        ),
                        reproduction_receipt=_object(
                            bundle.get("reproduction_receipt"),
                            field="ledger_reproduction_receipt",
                        ),
                    )
                except (DeliveryGateV2Error, ValueError) as exc:
                    raise ObligationAttemptLedgerError(
                        f"obligation_attempt_gate_bundle_invalid:{_text(attempt.get('obligation_id'))}:{exc}"
                    ) from exc
                identity = _object(
                    validated_gate.get("identity"),
                    field="validated_gate_identity",
                )
                if any((
                    _text(validated_gate.get("status"))
                    != _text(attempt.get("terminal_status")),
                    _text(validated_gate.get("gate_receipt_id"))
                    != _text(attempt.get("gate_receipt_id")),
                    _text(identity.get("finding_id"))
                    != _text(attempt.get("finding_id")),
                )):
                    raise ObligationAttemptLedgerError(
                        "obligation_attempt_gate_projection_mismatch:"
                        f"{_text(attempt.get('obligation_id'))}"
                        f":status={_text(validated_gate.get('status'))}"
                        f"/{_text(attempt.get('terminal_status'))}"
                        f":gate_id={_text(validated_gate.get('gate_receipt_id'))[:16]}"
                        f"/{_text(attempt.get('gate_receipt_id'))[:16]}"
                        f":finding_id={_text(identity.get('finding_id'))[:16]}"
                        f"/{_text(attempt.get('finding_id'))[:16]}"
                    )
            else:
                raise ObligationAttemptLedgerError(
                    f"obligation_attempt_gate_schema_invalid:{_text(attempt.get('obligation_id'))}"
                )
    return value


def derive_campaign_terminal_status(ledger: dict[str, Any]) -> str:
    """Project campaign status only from obligation terminal coverage."""

    value = validate_obligation_attempt_ledger(ledger)
    try:
        selected_count = int(value.get("selected_count"))
        terminal_count = int(value.get("terminal_count"))
    except (TypeError, ValueError) as exc:
        raise ObligationAttemptLedgerError("obligation_attempt_counts_invalid") from exc
    attempts = value["attempts"]
    accounted_count = int(value.get("accounted_count", terminal_count))
    if (
        terminal_count != accounted_count
        or terminal_count != len(attempts)
        or not bool(value.get("complete"))
    ):
        return "active"
    if selected_count == 0:
        return "blocked"
    statuses = [_text(_attempt_row(row).get("terminal_status")).upper() for row in attempts]
    if any(status not in TERMINAL_STATUSES for status in statuses):
        raise ObligationAttemptLedgerError("obligation_terminal_status_invalid")
    if "HARNESS_FAILED" in statuses:
        return "degraded"
    # All-blocked/deferred runs stay visibly BLOCKED — empty findings must never
    # be read as a defect-free completed campaign.
    if statuses and all(status in {"BLOCKED", "DEFERRED"} for status in statuses):
        return "blocked"
    return "completed"
