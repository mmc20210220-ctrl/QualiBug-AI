"""Adapter-neutral direct broker read contract for process-graph event waits.

The module defines the boundary between broker-specific plugins and the existing
process-graph wait authority. It owns no scheduler, network client, consumer,
registry, ledger, Oracle, or finalizer. A runtime plugin performs a read-only
snapshot and returns one canonical receipt; this module validates that receipt,
proves the read was non-destructive, and normalizes records for the existing
event transition evaluator.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


CONTRACT_SCHEMA_VERSION = "qualibug.process-graph-broker-read-adapter.v1"
RECEIPT_SCHEMA_VERSION = "qualibug.process-graph-broker-read-adapter-receipt.v1"

STATUS_OBSERVED = "OBSERVED"
STATUS_BLOCKED = "BLOCKED"

BROKER_ADAPTER_INVALID = "PROCESS_GRAPH_BROKER_ADAPTER_INVALID"
BROKER_ADAPTER_UNAVAILABLE = "PROCESS_GRAPH_BROKER_ADAPTER_UNAVAILABLE"
BROKER_ADAPTER_RECEIPT_INVALID = "PROCESS_GRAPH_BROKER_ADAPTER_RECEIPT_INVALID"
BROKER_ADAPTER_CAPABILITY_MISSING = (
    "PROCESS_GRAPH_BROKER_ADAPTER_CAPABILITY_MISSING"
)
BROKER_ADAPTER_SCOPE_MISMATCH = "PROCESS_GRAPH_BROKER_ADAPTER_SCOPE_MISMATCH"
BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION = (
    "PROCESS_GRAPH_BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION"
)
BROKER_ADAPTER_RECORD_LIMIT_EXCEEDED = (
    "PROCESS_GRAPH_BROKER_ADAPTER_RECORD_LIMIT_EXCEEDED"
)
BROKER_ADAPTER_RECORD_INVALID = "PROCESS_GRAPH_BROKER_ADAPTER_RECORD_INVALID"

_SUPPORTED_ADAPTER_KINDS = frozenset({"kafka", "rabbitmq", "rocketmq", "custom"})
_SUPPORTED_CAPABILITIES = frozenset(
    {
        "records",
        "checkpoints",
        "delivery_confirmations",
        "dlq",
        "rebalances",
    }
)
_OBSERVATION_MODES = frozenset({"read_only_snapshot"})
_CONSUMER_ISOLATION_MODES = frozenset(
    {"dedicated_observer", "broker_admin_snapshot"}
)
_MAX_RECORDS_PER_POLL = 500
_MAX_AUXILIARY_RECEIPTS_PER_POLL = 2000
_BROKER_FIELD_NAMES = (
    "topic_field",
    "partition_field",
    "offset_field",
    "checkpoint_field",
    "consumer_group_field",
    "delivery_state_field",
    "dead_letter_topic_field",
    "ordering_key_field",
    "sequence_field",
    "consumer_epoch_field",
    "deduplication_key_field",
    "effect_applied_field",
)


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


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _adapter_spec(event: dict[str, Any]) -> dict[str, Any]:
    source = _dict(event)
    return deepcopy(
        _dict(
            source.get("direct_broker_adapter")
            or source.get("broker_read_adapter")
            or source.get("broker_adapter")
        )
    )


def has_direct_broker_adapter(event: dict[str, Any]) -> bool:
    return bool(_adapter_spec(event))


def direct_broker_adapter_ref(event: dict[str, Any]) -> str:
    return _text(_adapter_spec(event).get("adapter_ref"))


def _source_refs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        deepcopy(row)
        for row in _list(raw.get("source_refs"))
        if isinstance(row, dict)
    ]


def _required_capabilities(
    raw: dict[str, Any],
    broker_contract: dict[str, Any],
) -> tuple[list[str], str]:
    declared = [
        _text(value).lower()
        for value in _list(raw.get("required_capabilities"))
        if _text(value)
    ]
    invalid = sorted(set(declared) - _SUPPORTED_CAPABILITIES)
    if invalid:
        return [], "broker_adapter_capability_invalid:" + ",".join(invalid)

    required = set(declared)
    required.add("records")
    if broker_contract:
        required.update({"checkpoints", "dlq"})
    if raw.get("require_delivery_confirmation_receipts") is True:
        required.add("delivery_confirmations")
    if raw.get("require_rebalance_receipts") is True:
        required.add("rebalances")
    return sorted(required), ""


def _canonical_envelope_valid(
    event: dict[str, Any],
    broker_contract: dict[str, Any],
) -> str:
    if _text(event.get("events_path")) != "$.items":
        return "broker_adapter_requires_events_path_items"
    for field_name in _BROKER_FIELD_NAMES:
        path = _text(_dict(broker_contract).get(field_name))
        if path and not path.startswith("$.broker."):
            return f"broker_adapter_field_not_canonical:{field_name}"
    return ""


def compile_broker_read_adapter_contract(
    event: dict[str, Any],
    *,
    broker_contract: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Compile an optional direct broker adapter contract."""
    raw = _adapter_spec(event)
    if not raw:
        return {}, ""

    adapter_kind = _text(raw.get("adapter_kind")).lower()
    if adapter_kind not in _SUPPORTED_ADAPTER_KINDS:
        return {}, f"broker_adapter_kind_invalid:{adapter_kind or '<empty>'}"

    adapter_ref = _text(raw.get("adapter_ref"))
    capability_ref = _text(raw.get("runtime_capability_ref"))
    if not adapter_ref or not capability_ref:
        return {}, "broker_adapter_ref_or_runtime_capability_ref_missing"

    observation_mode = _text(raw.get("observation_mode")).lower()
    if observation_mode not in _OBSERVATION_MODES:
        return {}, (
            "broker_adapter_observation_mode_invalid:"
            f"{observation_mode or '<empty>'}"
        )
    isolation = _text(raw.get("consumer_isolation")).lower()
    if isolation not in _CONSUMER_ISOLATION_MODES:
        return {}, (
            "broker_adapter_consumer_isolation_invalid:"
            f"{isolation or '<empty>'}"
        )
    commit_mode = _text(raw.get("commit_mode")).lower()
    acknowledgment_mode = _text(raw.get("acknowledgment_mode")).lower()
    if commit_mode != "none" or acknowledgment_mode != "none":
        return {}, "broker_adapter_must_not_commit_or_ack"

    try:
        max_records = int(raw.get("max_records_per_poll"))
    except (TypeError, ValueError):
        return {}, "broker_adapter_max_records_not_integer"
    if max_records < 1 or max_records > _MAX_RECORDS_PER_POLL:
        return {}, "broker_adapter_max_records_out_of_range"

    required_capabilities, capability_error = _required_capabilities(
        raw,
        broker_contract,
    )
    if capability_error:
        return {}, capability_error

    source_refs = _source_refs(raw)
    if not source_refs:
        return {}, "broker_adapter_source_refs_missing"

    envelope_error = _canonical_envelope_valid(event, broker_contract)
    if envelope_error:
        return {}, envelope_error

    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "adapter_kind": adapter_kind,
        "adapter_ref": adapter_ref,
        "runtime_capability_ref": capability_ref,
        "observation_mode": observation_mode,
        "consumer_isolation": isolation,
        "commit_mode": commit_mode,
        "acknowledgment_mode": acknowledgment_mode,
        "max_records_per_poll": max_records,
        "required_capabilities": required_capabilities,
        "require_delivery_confirmation_receipts": (
            raw.get("require_delivery_confirmation_receipts") is True
        ),
        "require_rebalance_receipts": (
            raw.get("require_rebalance_receipts") is True
        ),
        "source_refs": source_refs,
    }
    contract["contract_fingerprint"] = _fingerprint(contract)
    return contract, ""


def contract_fingerprint_valid(contract: dict[str, Any]) -> bool:
    value = deepcopy(_dict(contract))
    attached = _text(value.pop("contract_fingerprint", ""))
    return bool(attached and attached == _fingerprint(value))


def _receipt_list(
    receipt: dict[str, Any],
    field: str,
) -> tuple[list[dict[str, Any]], str]:
    value = receipt.get(field, [])
    if not isinstance(value, list):
        return [], f"{field}_not_list"
    if len(value) > _MAX_AUXILIARY_RECEIPTS_PER_POLL:
        return [], f"{field}_limit_exceeded"
    rows = [deepcopy(row) for row in value if isinstance(row, dict)]
    if len(rows) != len(value):
        return [], f"{field}_contains_non_object"
    return rows, ""


def _confirmation_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ack_count": 0, "nack_count": 0, "requeue_count": 0}
    for row in rows:
        state = _text(
            row.get("state")
            or row.get("confirmation_state")
            or row.get("ack_state")
        ).upper()
        if state in {"ACK", "ACKED", "COMMITTED"}:
            counts["ack_count"] += 1
        elif state in {"NACK", "NACKED", "REJECTED"}:
            counts["nack_count"] += 1
        if state in {"REQUEUE", "REQUEUED"} or row.get("requeue") is True:
            counts["requeue_count"] += 1
    return counts


def normalize_broker_read_receipt(
    receipt: dict[str, Any],
    *,
    contract: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Validate one plugin receipt and normalize its records to ``$.items``.

    Returns ``(body, redacted_evidence, reason_code, detail)``.
    """
    spec = _dict(contract)
    row = _dict(receipt)
    if not spec or not contract_fingerprint_valid(spec):
        return {}, {}, BROKER_ADAPTER_SCOPE_MISMATCH, "adapter_contract_drift"
    if not row:
        return {}, {}, BROKER_ADAPTER_RECEIPT_INVALID, "adapter_receipt_missing"
    if (
        _text(row.get("schema_version")) != RECEIPT_SCHEMA_VERSION
        or _text(row.get("status")).upper() != STATUS_OBSERVED
    ):
        return {}, {}, BROKER_ADAPTER_RECEIPT_INVALID, "adapter_receipt_status_invalid"
    if (
        _text(row.get("adapter_kind")).lower() != _text(spec.get("adapter_kind"))
        or _text(row.get("adapter_ref")) != _text(spec.get("adapter_ref"))
        or _text(row.get("contract_fingerprint"))
        != _text(spec.get("contract_fingerprint"))
    ):
        return {}, {}, BROKER_ADAPTER_SCOPE_MISMATCH, "adapter_receipt_scope_mismatch"

    capabilities = sorted(
        {
            _text(value).lower()
            for value in _list(row.get("capabilities"))
            if _text(value)
        }
    )
    missing = sorted(
        set(_list(spec.get("required_capabilities"))) - set(capabilities)
    )
    if missing:
        return (
            {},
            {},
            BROKER_ADAPTER_CAPABILITY_MISSING,
            "missing_capabilities:" + ",".join(missing),
        )

    if (
        row.get("non_destructive") is not True
        or row.get("commit_performed") is True
        or row.get("ack_performed") is True
        or row.get("nack_performed") is True
    ):
        return (
            {},
            {},
            BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION,
            "adapter_observation_changed_broker_state",
        )

    records = row.get("records")
    if not isinstance(records, list):
        return {}, {}, BROKER_ADAPTER_RECEIPT_INVALID, "records_not_list"
    if len(records) > int(spec.get("max_records_per_poll") or 0):
        return (
            {},
            {},
            BROKER_ADAPTER_RECORD_LIMIT_EXCEEDED,
            "record_limit_exceeded",
        )

    items: list[dict[str, Any]] = []
    record_fingerprints: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            return {}, {}, BROKER_ADAPTER_RECORD_INVALID, "record_not_object"
        event = deepcopy(_dict(record.get("event")))
        broker = deepcopy(_dict(record.get("broker")))
        if not event or not broker or "broker" in event:
            return (
                {},
                {},
                BROKER_ADAPTER_RECORD_INVALID,
                "record_event_or_broker_invalid",
            )
        event["broker"] = broker
        items.append(event)
        record_fingerprints.append(_fingerprint(record))

    auxiliary: dict[str, list[dict[str, Any]]] = {}
    for field in (
        "checkpoint_receipts",
        "delivery_confirmation_receipts",
        "dlq_receipts",
        "rebalance_receipts",
    ):
        values, error = _receipt_list(row, field)
        if error:
            return {}, {}, BROKER_ADAPTER_RECEIPT_INVALID, error
        auxiliary[field] = values

    if (
        spec.get("require_delivery_confirmation_receipts") is True
        and not auxiliary["delivery_confirmation_receipts"]
    ):
        return (
            {},
            {},
            BROKER_ADAPTER_RECEIPT_INVALID,
            "delivery_confirmation_receipts_missing",
        )
    if (
        spec.get("require_rebalance_receipts") is True
        and not auxiliary["rebalance_receipts"]
    ):
        return (
            {},
            {},
            BROKER_ADAPTER_RECEIPT_INVALID,
            "rebalance_receipts_missing",
        )

    confirmation_counts = _confirmation_counts(
        auxiliary["delivery_confirmation_receipts"]
    )
    evidence = {
        "adapter_contract_fingerprint": _text(spec.get("contract_fingerprint")),
        "adapter_kind": _text(spec.get("adapter_kind")),
        "adapter_ref_fingerprint": _fingerprint(spec.get("adapter_ref")),
        "runtime_capability_ref_fingerprint": _fingerprint(
            spec.get("runtime_capability_ref")
        ),
        "adapter_receipt_fingerprint": _fingerprint(row),
        "capability_fingerprints": sorted(
            _fingerprint(value) for value in capabilities
        ),
        "record_count": len(items),
        "record_fingerprints": sorted(record_fingerprints),
        "checkpoint_receipt_count": len(auxiliary["checkpoint_receipts"]),
        "delivery_confirmation_receipt_count": len(
            auxiliary["delivery_confirmation_receipts"]
        ),
        "dlq_receipt_count": len(auxiliary["dlq_receipts"]),
        "rebalance_receipt_count": len(auxiliary["rebalance_receipts"]),
        **confirmation_counts,
        "checkpoint_receipt_fingerprints": sorted(
            _fingerprint(value) for value in auxiliary["checkpoint_receipts"]
        ),
        "delivery_confirmation_receipt_fingerprints": sorted(
            _fingerprint(value)
            for value in auxiliary["delivery_confirmation_receipts"]
        ),
        "dlq_receipt_fingerprints": sorted(
            _fingerprint(value) for value in auxiliary["dlq_receipts"]
        ),
        "rebalance_receipt_fingerprints": sorted(
            _fingerprint(value) for value in auxiliary["rebalance_receipts"]
        ),
    }
    return {"items": items}, evidence, "", ""


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "STATUS_OBSERVED",
    "STATUS_BLOCKED",
    "BROKER_ADAPTER_INVALID",
    "BROKER_ADAPTER_UNAVAILABLE",
    "BROKER_ADAPTER_RECEIPT_INVALID",
    "BROKER_ADAPTER_CAPABILITY_MISSING",
    "BROKER_ADAPTER_SCOPE_MISMATCH",
    "BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION",
    "BROKER_ADAPTER_RECORD_LIMIT_EXCEEDED",
    "BROKER_ADAPTER_RECORD_INVALID",
    "has_direct_broker_adapter",
    "direct_broker_adapter_ref",
    "compile_broker_read_adapter_contract",
    "contract_fingerprint_valid",
    "normalize_broker_read_receipt",
]
