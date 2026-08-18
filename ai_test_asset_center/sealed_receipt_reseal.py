"""Reseal content-addressed receipts after authorized persistence redaction.

Artifact redaction may rewrite secret-bearing strings inside sealed receipts.
Callers must reseal children before parents (with ID remapping) so reload
validation remains fail-closed without false fingerprint mismatches.
"""
from __future__ import annotations

from typing import Any

from .assertion_dsl_base import _assertion_receipt
from .contract_oracles import (
    ACTIVATION_RECEIPT_SCHEMA,
    CONTRACT_EVIDENCE_RECEIPT_SCHEMA,
    CONTRACT_ORACLE_RECEIPT_SCHEMA,
    _content_receipt,
    _restore_pre_gate_oracle,
    build_contract_evidence_receipt,
)
from ._contract_oracles_mechanics import CONTRACT_ORACLE_POST_HOC_FIELDS
from .customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    DELIVERY_EXECUTION_RECEIPT_SCHEMA,
    REPRODUCTION_RECEIPT_SCHEMA,
    _seal,
    build_customer_delivery_gate_receipt_v2,
)
from .observer_contracts_base import SCHEMA_VERSION as OBSERVER_RECEIPT_SCHEMA
from .observer_contracts_base import build_observer_receipt
from .operational_receipts import (
    EXECUTION_OPERATIONAL_RECEIPT_SCHEMA,
    _fingerprint as _operational_fingerprint,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _map_id(value: Any, id_map: dict[str, str]) -> str:
    text = _text(value)
    if not text:
        return ""
    return id_map.get(text, text)


def _map_ids(values: Any, id_map: dict[str, str]) -> list[str]:
    return [_map_id(item, id_map) for item in _list(values) if _text(item)]


def reseal_observer_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    return build_observer_receipt(
        observer_id=_text(row.get("observer_id")),
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        evidence=dict(_dict(row.get("evidence"))),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )


def reseal_contract_evidence_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    return build_contract_evidence_receipt(
        kind=_text(row.get("kind")),
        experiment_id=_text(row.get("experiment_id")),
        obligation_id=_text(row.get("obligation_id")),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
        subject_id=_text(row.get("subject_id")),
        status=_text(row.get("status")),
        evidence=dict(_dict(row.get("evidence"))),
    )


def reseal_execution_operational_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = dict(_dict(receipt))
    unsigned = {
        key: value
        for key, value in row.items()
        if key != "receipt_fingerprint"
    }
    unsigned["receipt_fingerprint"] = _operational_fingerprint(unsigned)
    return unsigned


def reseal_assertion_receipt(
    receipt: dict[str, Any],
    *,
    id_map: dict[str, str],
) -> dict[str, Any]:
    row = _dict(receipt)
    resealed = _assertion_receipt(
        assertion_id=_text(row.get("assertion_id")),
        kind=_text(row.get("kind")),
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        expected=row.get("expected"),
        actual=row.get("actual"),
        error=_text(row.get("error")),
        observer_receipt_ids=_map_ids(row.get("observer_receipt_ids"), id_map),
        source_refs=[
            dict(item) for item in _list(row.get("source_refs")) if isinstance(item, dict)
        ],
        harness_error=bool(row.get("harness_error")),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )
    # V1.6.1: field_oracle_trace is appended AFTER the assertion receipt_id was
    # computed (the validator recomputes the fingerprint over the base fields
    # only and re-attaches the trace). Dropping it during reseal made rebuilt
    # oracle receipts lose their trace, so the delivery gate failed the
    # otherwise identical experiment as FIELD_ORACLE_TRACE_MISSING.
    if isinstance(row.get("field_oracle_trace"), dict):
        resealed["field_oracle_trace"] = dict(row["field_oracle_trace"])
    return resealed


def reseal_activation_receipt(
    receipt: dict[str, Any],
    *,
    id_map: dict[str, str],
) -> dict[str, Any]:
    row = dict(_dict(receipt))
    verified = {
        key: _map_ids(values, id_map)
        for key, values in _dict(row.get("verified_receipt_ids")).items()
    }
    row["verified_receipt_ids"] = verified
    payload = {key: value for key, value in row.items() if key != "receipt_id"}
    return _content_receipt("activation_", payload)


def reseal_oracle_receipt(
    receipt: dict[str, Any],
    *,
    id_map: dict[str, str],
) -> dict[str, Any]:
    row = dict(_dict(receipt))
    activation = reseal_activation_receipt(
        _dict(row.get("activation_receipt")),
        id_map=id_map,
    )
    id_map[_text(_dict(row.get("activation_receipt")).get("receipt_id"))] = _text(
        activation.get("receipt_id")
    )
    assertions: list[dict[str, Any]] = []
    for item in _list(row.get("assertions")):
        if not isinstance(item, dict):
            continue
        old_id = _text(item.get("receipt_id"))
        resealed = reseal_assertion_receipt(item, id_map=id_map)
        if old_id:
            id_map[old_id] = _text(resealed.get("receipt_id"))
        assertions.append(resealed)
    violations = [item for item in assertions if item.get("status") == "VIOLATION"]
    indeterminate = [
        item for item in assertions if item.get("status") == "INDETERMINATE"
    ]
    row["activation_receipt"] = activation
    row["activation_receipt_id"] = _text(activation.get("receipt_id"))
    row["assertions"] = assertions
    row["assertion_receipt_ids"] = [_text(item.get("receipt_id")) for item in assertions]
    row["violation_assertion_receipt_ids"] = [
        _text(item.get("receipt_id")) for item in violations
    ]
    row["indeterminate_assertion_receipt_ids"] = [
        _text(item.get("receipt_id")) for item in indeterminate
    ]
    row["failed_assertions"] = [dict(item) for item in violations]
    # V1.7: authorization causality / delivery gates enrich the oracle receipt
    # AFTER its receipt_id was computed. Validation strips those fields when
    # recomputing the fingerprint, so the reseal must do the same or the
    # resealed receipt fails contract_oracle_receipt_fingerprint_invalid.
    payload = {
        key: value
        for key, value in row.items()
        if key != "receipt_id" and key not in CONTRACT_ORACLE_POST_HOC_FIELDS
    }
    # The strict post-hoc validator restores the oracle to its PRE-gate
    # semantics (the snapshots captured before the causality/validity gates
    # demoted it) and requires the restored base to carry the row identity.
    # The resealed id must therefore be computed over the restored base
    # payload, not over the gate-demoted fields: a reseal that remaps child
    # receipts otherwise produces an id whose base hash can never match
    # (contract_oracle_posthoc_base_identity_mismatch at scan persist).
    # The identity payload is a private copy: the resealed ROW keeps its
    # gate-demoted status/verdict semantics, only the content-addressed id
    # follows the pre-gate base.
    def _remap_failed_assertions(items: Any) -> list[Any]:
        remapped: list[Any] = []
        for item in _list(items):
            if not isinstance(item, dict):
                continue
            row_item = dict(item)
            old_id = _text(row_item.get("receipt_id"))
            if old_id:
                row_item["receipt_id"] = id_map.get(old_id, old_id)
            remapped.append(row_item)
        return remapped

    identity_payload = dict(payload)
    # The strict post-hoc validator restores the oracle to its PRE-gate
    # semantics (the snapshots captured before the causality/validity gates
    # demoted it) and requires the restored base to carry the row identity.
    # The resealed id must therefore always be computed over the restored
    # base payload, not over the gate-demoted fields, otherwise the base hash
    # can never match (contract_oracle_posthoc_base_identity_mismatch at scan
    # persist).  When the reseal remaps child receipts the pre-gate snapshots
    # first follow the remap, so the restored base reflects the resealed
    # children exactly as the strict validator will rebuild it.
    def _remap_failed_assertions(items: Any) -> list[Any]:
        remapped: list[Any] = []
        for item in _list(items):
            if not isinstance(item, dict):
                continue
            row_item = dict(item)
            old_id = _text(row_item.get("receipt_id"))
            if old_id:
                row_item["receipt_id"] = id_map.get(old_id, old_id)
            remapped.append(row_item)
        return remapped

    _prepared = dict(row)
    for _snapshot_field in (
        "pre_validity_oracle_verdict",
        "pre_causality_oracle_verdict",
    ):
        _snap = dict(_dict(_prepared.get(_snapshot_field)))
        if _snap and isinstance(_snap.get("failed_assertions"), list):
            _snap = dict(_snap)
            _snap["failed_assertions"] = _remap_failed_assertions(
                _snap["failed_assertions"]
            )
            _prepared[_snapshot_field] = _snap
    _base = _restore_pre_gate_oracle(_prepared)
    identity_payload = {
        key: value for key, value in _base.items() if key != "receipt_id"
    }
    # The row keeps its gate-demoted semantics; only the content-addressed
    # identity follows the pre-gate base.
    resealed = dict(payload)
    resealed["receipt_id"] = _content_receipt(
        "oracle_", identity_payload
    )["receipt_id"]
    for field in CONTRACT_ORACLE_POST_HOC_FIELDS:
        if field in row:
            resealed[field] = row[field]
    # The pre-gate snapshots captured the ORIGINAL oracle identity before the
    # causality/validity gates demoted the row.  When the reseal remaps child
    # receipts the resealed oracle receives a NEW receipt_id; the snapshots
    # must follow it, otherwise the strict validator's pre-gate restore
    # rebuilds a base whose identity differs from the resealed row and raises
    # contract_oracle_posthoc_base_identity_mismatch at scan persist (the
    # snapshots are post-hoc fields, so this remap never changes the resealed
    # oracle's own content-addressed identity).
    new_oracle_id = _text(resealed.get("receipt_id"))
    new_activation_id = _text(resealed.get("activation_receipt_id"))
    for snapshot_field in (
        "pre_causality_oracle_verdict",
        "pre_validity_oracle_verdict",
    ):
        snapshot = dict(_dict(resealed.get(snapshot_field)))
        if not snapshot:
            continue
        updated = dict(snapshot)
        if _text(updated.get("receipt_id")):
            updated["receipt_id"] = new_oracle_id
        if _text(updated.get("activation_receipt_id")):
            updated["activation_receipt_id"] = new_activation_id
        if isinstance(updated.get("failed_assertions"), list):
            updated["failed_assertions"] = _remap_failed_assertions(
                updated["failed_assertions"]
            )
        resealed[snapshot_field] = updated
    return resealed


def reseal_delivery_execution_receipt(
    receipt: dict[str, Any],
    *,
    id_map: dict[str, str],
    operational: dict[str, Any],
) -> dict[str, Any]:
    row = dict(_dict(receipt))
    row["observation_receipt_ids"] = sorted(
        set(_map_ids(row.get("observation_receipt_ids"), id_map))
    )
    row["oracle_receipt_id"] = _map_id(row.get("oracle_receipt_id"), id_map)
    row["operational_receipt"] = dict(operational)
    row["operational_receipt_id"] = _text(operational.get("receipt_id"))
    row["operational_receipt_fingerprint"] = _text(
        operational.get("receipt_fingerprint")
    )
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    return _seal(
        unsigned,
        prefix="delivery_exec_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )


def reseal_reproduction_receipt(
    receipt: dict[str, Any],
    *,
    id_map: dict[str, str],
) -> dict[str, Any]:
    row = dict(_dict(receipt))
    row["oracle_receipt_id"] = _map_id(row.get("oracle_receipt_id"), id_map)
    steps: list[dict[str, Any]] = []
    for step in _list(row.get("step_observations")):
        if not isinstance(step, dict):
            continue
        item = dict(step)
        item["observation_receipt_id"] = _map_id(
            item.get("observation_receipt_id"),
            id_map,
        )
        steps.append(item)
    row["step_observations"] = steps
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    return _seal(
        unsigned,
        prefix="reproduction_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )


def reseal_delivery_evidence_bundle(
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Reseal nested gate evidence and rebuild the gate receipt.

    Returns (updated_bundle, rebuilt_gate, id_map).
    """

    value = dict(_dict(bundle))
    id_map: dict[str, str] = {}

    observers: list[dict[str, Any]] = []
    for item in _list(value.get("observer_receipts")):
        if not isinstance(item, dict):
            continue
        old_id = _text(item.get("receipt_id"))
        resealed = reseal_observer_receipt(item)
        if old_id:
            id_map[old_id] = _text(resealed.get("receipt_id"))
        observers.append(resealed)

    contracts: list[dict[str, Any]] = []
    for item in _list(value.get("contract_evidence_receipts")):
        if not isinstance(item, dict):
            continue
        old_id = _text(item.get("receipt_id"))
        resealed = reseal_contract_evidence_receipt(item)
        if old_id:
            id_map[old_id] = _text(resealed.get("receipt_id"))
        contracts.append(resealed)

    oracle_row = _dict(value.get("oracle_receipt"))
    old_oracle_id = _text(oracle_row.get("receipt_id"))
    oracle = reseal_oracle_receipt(oracle_row, id_map=id_map)
    if old_oracle_id:
        id_map[old_oracle_id] = _text(oracle.get("receipt_id"))

    execution_row = _dict(value.get("execution_receipt"))
    operational = reseal_execution_operational_receipt(
        _dict(execution_row.get("operational_receipt"))
    )
    old_exec_id = _text(execution_row.get("receipt_id"))
    execution = reseal_delivery_execution_receipt(
        execution_row,
        id_map=id_map,
        operational=operational,
    )
    if old_exec_id:
        id_map[old_exec_id] = _text(execution.get("receipt_id"))

    reproduction_row = _dict(value.get("reproduction_receipt"))
    old_repro_id = _text(reproduction_row.get("receipt_id"))
    reproduction = reseal_reproduction_receipt(reproduction_row, id_map=id_map)
    if old_repro_id:
        id_map[old_repro_id] = _text(reproduction.get("receipt_id"))

    finding = (
        dict(value.get("finding"))
        if isinstance(value.get("finding"), dict)
        else None
    )
    gate = build_customer_delivery_gate_receipt_v2(
        finding=finding,
        execution_receipt=execution,
        contract_evidence_receipts=contracts,
        observer_receipts=observers,
        oracle_receipt=oracle,
        reproduction_receipt=reproduction,
    )
    old_gate_id = _text(_dict(value.get("gate_receipt")).get("gate_receipt_id"))
    if not old_gate_id:
        # gate lives on the attempt; map from rebuilt id only when known later
        pass
    new_gate_id = _text(gate.get("gate_receipt_id"))
    if old_gate_id and new_gate_id:
        id_map[old_gate_id] = new_gate_id

    updated = {
        "finding": finding,
        "execution_receipt": execution,
        "contract_evidence_receipts": contracts,
        "observer_receipts": observers,
        "oracle_receipt": oracle,
        "reproduction_receipt": reproduction,
    }
    return updated, gate, id_map


def reseal_obligation_attempt_nested_receipts(attempt: dict[str, Any]) -> dict[str, Any]:
    """Reseal nested sealed receipts on one attempt before attempt fingerprinting."""

    row = dict(_dict(attempt))
    if isinstance(row.get("operational_receipt"), dict):
        row["operational_receipt"] = reseal_execution_operational_receipt(
            row["operational_receipt"]
        )

    bundle = row.get("delivery_evidence_bundle")
    gate_receipt = row.get("gate_receipt")
    if not (
        isinstance(bundle, dict)
        and isinstance(gate_receipt, dict)
        and gate_receipt.get("schema_version") == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
    ):
        return row

    # Preserve prior gate id for id_map when bundle does not embed it.
    bundle_with_gate = dict(bundle)
    bundle_with_gate["gate_receipt"] = gate_receipt
    updated_bundle, rebuilt_gate, id_map = reseal_delivery_evidence_bundle(
        bundle_with_gate
    )
    old_gate_id = _text(gate_receipt.get("gate_receipt_id"))
    new_gate_id = _text(rebuilt_gate.get("gate_receipt_id"))
    if old_gate_id and new_gate_id:
        id_map[old_gate_id] = new_gate_id

    row["delivery_evidence_bundle"] = updated_bundle
    row["gate_receipt"] = rebuilt_gate
    row["gate_receipt_id"] = new_gate_id
    row["output_fingerprint"] = _text(rebuilt_gate.get("output_fingerprint"))
    row["oracle_receipt_id"] = _map_id(row.get("oracle_receipt_id"), id_map)
    row["observation_receipt_ids"] = _map_ids(
        row.get("observation_receipt_ids"),
        id_map,
    )
    if isinstance(row.get("operational_receipt"), dict):
        # Prefer the resealed operational nested under execution when present.
        nested_op = _dict(updated_bundle.get("execution_receipt")).get(
            "operational_receipt"
        )
        if isinstance(nested_op, dict):
            row["operational_receipt"] = dict(nested_op)

    stages: list[dict[str, Any]] = []
    for stage in _list(row.get("stages")):
        if not isinstance(stage, dict):
            continue
        item = dict(stage)
        stage_name = _text(item.get("stage"))
        item["receipt_id"] = _map_id(item.get("receipt_id"), id_map)
        if stage_name == "gate":
            item["receipt_id"] = new_gate_id
            item["input_fingerprint"] = _text(rebuilt_gate.get("input_fingerprint"))
            item["output_fingerprint"] = _text(rebuilt_gate.get("output_fingerprint"))
        elif stage_name == "execution":
            exec_receipt = _dict(updated_bundle.get("execution_receipt"))
            item["receipt_id"] = _text(exec_receipt.get("receipt_id"))
            item["output_fingerprint"] = _text(exec_receipt.get("receipt_fingerprint"))
        stages.append(item)
    if stages:
        row["stages"] = stages
        row["receipt_refs"] = {
            _text(item.get("stage")): _text(item.get("receipt_id"))
            for item in stages
            if _text(item.get("stage")) and _text(item.get("receipt_id"))
        }
    return row


# Schema constants retained for dispatcher documentation / tests.
_RESEALABLE_SCHEMAS = frozenset({
    OBSERVER_RECEIPT_SCHEMA,
    CONTRACT_EVIDENCE_RECEIPT_SCHEMA,
    ACTIVATION_RECEIPT_SCHEMA,
    CONTRACT_ORACLE_RECEIPT_SCHEMA,
    EXECUTION_OPERATIONAL_RECEIPT_SCHEMA,
    DELIVERY_EXECUTION_RECEIPT_SCHEMA,
    REPRODUCTION_RECEIPT_SCHEMA,
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
})
