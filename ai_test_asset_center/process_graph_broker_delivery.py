"""Source-declared broker delivery semantics for process-graph event transitions.

This module is a semantic subcontract of ``process_graph_event_transition``.
It owns no scheduler, transport, observer registry, ledger, or finalizer. The
existing bounded event wait supplies correlated event rows; this module freezes
partitioned-log identity and evaluates topic, consumer group, partition/offset,
checkpoint, DLQ, source ordering, and restart deduplication without exposing
raw broker identities in runtime receipts.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Callable


CONTRACT_SCHEMA_VERSION = "qualibug.process-graph-broker-delivery.v1"

STATUS_PASS = "PASS"
STATUS_VIOLATION = "VIOLATION"
STATUS_INDETERMINATE = "INDETERMINATE"

BROKER_DELIVERY_INVALID = "PROCESS_GRAPH_BROKER_DELIVERY_INVALID"
BROKER_EXPECTATION_BINDING_UNRESOLVED = (
    "PROCESS_GRAPH_BROKER_EXPECTATION_BINDING_UNRESOLVED"
)
BROKER_METADATA_INCOMPLETE = "PROCESS_GRAPH_BROKER_METADATA_INCOMPLETE"
BROKER_TOPIC_MISMATCH = "PROCESS_GRAPH_BROKER_TOPIC_MISMATCH"
BROKER_CONSUMER_GROUP_MISMATCH = "PROCESS_GRAPH_BROKER_CONSUMER_GROUP_MISMATCH"
BROKER_PARTITION_OFFSET_CONFLICT = (
    "PROCESS_GRAPH_BROKER_PARTITION_OFFSET_CONFLICT"
)
BROKER_CHECKPOINT_CONFLICT = "PROCESS_GRAPH_BROKER_CHECKPOINT_CONFLICT"
BROKER_CHECKPOINT_REGRESSION = "PROCESS_GRAPH_BROKER_CHECKPOINT_REGRESSION"
BROKER_CHECKPOINT_BEHIND_OBSERVED = (
    "PROCESS_GRAPH_BROKER_CHECKPOINT_BEHIND_OBSERVED"
)
BROKER_DLQ_DELIVERY_UNEXPECTED = "PROCESS_GRAPH_BROKER_DLQ_DELIVERY_UNEXPECTED"
BROKER_SEQUENCE_ORDER_VIOLATION = (
    "PROCESS_GRAPH_BROKER_SEQUENCE_ORDER_VIOLATION"
)
BROKER_RESTART_DEDUPLICATION_VIOLATION = (
    "PROCESS_GRAPH_BROKER_RESTART_DEDUPLICATION_VIOLATION"
)

_BROKER_MODEL = "partitioned_log"
_CHECKPOINT_POLICIES = frozenset({"monotonic_only", "must_cover_observed"})
_DLQ_POLICIES = frozenset({"forbidden", "allowed"})
_ORDERING_POLICIES = frozenset({"partition_offset", "source_sequence"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _scalar(value: Any) -> bool:
    return value is not None and isinstance(value, (str, int, float, bool))


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


def _exclusive_expectation(
    raw: dict[str, Any],
    *,
    literal_field: str,
    binding_field: str,
) -> tuple[str, str, str]:
    literal = _text(raw.get(literal_field))
    binding = _text(raw.get(binding_field))
    if bool(literal) == bool(binding):
        return "", "", (
            f"broker_{literal_field}_or_{binding_field}_requires_exactly_one"
        )
    return literal, binding, ""


def compile_broker_delivery_contract(
    event: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Compile the optional broker semantic section of an event contract."""
    source = _dict(event)
    raw = deepcopy(
        _dict(
            source.get("broker_delivery")
            or source.get("broker_contract")
            or source.get("broker_semantics")
        )
    )
    if not raw:
        return {}, ""

    model = _text(raw.get("broker_model") or raw.get("model")).lower()
    if model != _BROKER_MODEL:
        return {}, f"broker_model_invalid:{model or '<empty>'}"

    required = {
        "topic_field": _text(raw.get("topic_field")),
        "partition_field": _text(raw.get("partition_field")),
        "offset_field": _text(raw.get("offset_field")),
        "checkpoint_field": _text(raw.get("checkpoint_field")),
        "consumer_group_field": _text(raw.get("consumer_group_field")),
        "delivery_state_field": _text(raw.get("delivery_state_field")),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return {}, "broker_fields_missing:" + ",".join(missing)

    expected_topic, topic_binding, error = _exclusive_expectation(
        raw,
        literal_field="expected_topic",
        binding_field="topic_binding",
    )
    if error:
        return {}, error
    expected_group, group_binding, error = _exclusive_expectation(
        raw,
        literal_field="expected_consumer_group",
        binding_field="consumer_group_binding",
    )
    if error:
        return {}, error

    checkpoint_policy = _text(raw.get("checkpoint_policy")).lower()
    if checkpoint_policy not in _CHECKPOINT_POLICIES:
        return {}, (
            "broker_checkpoint_policy_invalid:"
            f"{checkpoint_policy or '<empty>'}"
        )
    dlq_policy = _text(raw.get("dlq_policy")).lower()
    if dlq_policy not in _DLQ_POLICIES:
        return {}, f"broker_dlq_policy_invalid:{dlq_policy or '<empty>'}"
    dead_letter_states = [
        _text(value)
        for value in _list(raw.get("dead_letter_states"))
        if _text(value)
    ]
    if not dead_letter_states:
        return {}, "broker_dead_letter_states_missing"

    ordering_policy = _text(raw.get("ordering_policy")).lower()
    if ordering_policy not in _ORDERING_POLICIES:
        return {}, (
            "broker_ordering_policy_invalid:"
            f"{ordering_policy or '<empty>'}"
        )
    ordering_key_field = _text(raw.get("ordering_key_field"))
    sequence_field = _text(raw.get("sequence_field"))
    if ordering_policy == "source_sequence" and not (
        ordering_key_field and sequence_field
    ):
        return {}, "broker_source_sequence_fields_missing"
    if ordering_policy != "source_sequence" and (
        ordering_key_field or sequence_field
    ):
        return {}, "broker_source_sequence_fields_not_applicable"

    restart_required = raw.get("restart_deduplication_required") is True
    consumer_epoch_field = _text(raw.get("consumer_epoch_field"))
    deduplication_key_field = _text(raw.get("deduplication_key_field"))
    effect_applied_field = _text(raw.get("effect_applied_field"))
    restart_fields = (
        consumer_epoch_field,
        deduplication_key_field,
        effect_applied_field,
    )
    if restart_required and not all(restart_fields):
        return {}, "broker_restart_deduplication_fields_missing"
    if not restart_required and any(restart_fields):
        return {}, "broker_restart_deduplication_not_declared"

    dead_letter_topic_field = _text(raw.get("dead_letter_topic_field"))
    source_refs = [
        deepcopy(row)
        for row in _list(raw.get("source_refs"))
        if isinstance(row, dict)
    ]
    if not source_refs:
        return {}, "broker_source_refs_missing"

    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "broker_model": model,
        **required,
        "expected_topic": expected_topic,
        "topic_binding": topic_binding,
        "expected_consumer_group": expected_group,
        "consumer_group_binding": group_binding,
        "checkpoint_policy": checkpoint_policy,
        "dlq_policy": dlq_policy,
        "dead_letter_states": list(dict.fromkeys(dead_letter_states)),
        "dead_letter_topic_field": dead_letter_topic_field,
        "ordering_policy": ordering_policy,
        "ordering_key_field": ordering_key_field,
        "sequence_field": sequence_field,
        "restart_deduplication_required": restart_required,
        "consumer_epoch_field": consumer_epoch_field,
        "deduplication_key_field": deduplication_key_field,
        "effect_applied_field": effect_applied_field,
        "source_refs": source_refs,
    }
    contract["contract_fingerprint"] = _fingerprint(contract)
    return contract, ""


def _extract_required(
    raw: dict[str, Any],
    path: str,
    *,
    extractor: Callable[[Any, str], tuple[bool, Any]],
    kind: str,
) -> tuple[Any, str]:
    found, value = extractor(raw, path)
    if not found:
        return None, f"{kind}_missing"
    if kind in {"partition", "offset", "checkpoint", "sequence"}:
        if isinstance(value, bool):
            return None, f"{kind}_not_integer"
        try:
            return int(value), ""
        except (TypeError, ValueError):
            return None, f"{kind}_not_integer"
    if kind == "effect_applied":
        if not isinstance(value, bool):
            return None, "effect_applied_not_boolean"
        return value, ""
    if not _scalar(value):
        return None, f"{kind}_not_scalar"
    return value, ""


def extract_broker_metadata(
    raw_event: dict[str, Any],
    contract: dict[str, Any],
    *,
    extractor: Callable[[Any, str], tuple[bool, Any]],
) -> tuple[dict[str, Any], str]:
    """Extract exact broker metadata from one already-correlated event row."""
    spec = _dict(contract)
    if not spec:
        return {}, ""

    values: dict[str, Any] = {}
    errors: list[str] = []
    field_kinds = (
        ("topic", "topic_field"),
        ("partition", "partition_field"),
        ("offset", "offset_field"),
        ("checkpoint", "checkpoint_field"),
        ("consumer_group", "consumer_group_field"),
        ("delivery_state", "delivery_state_field"),
    )
    for kind, field_name in field_kinds:
        value, error = _extract_required(
            raw_event,
            _text(spec.get(field_name)),
            extractor=extractor,
            kind=kind,
        )
        if error:
            errors.append(error)
        else:
            values[kind] = value

    optional_kinds = (
        ("dead_letter_topic", "dead_letter_topic_field"),
        ("ordering_key", "ordering_key_field"),
        ("sequence", "sequence_field"),
        ("consumer_epoch", "consumer_epoch_field"),
        ("deduplication_key", "deduplication_key_field"),
        ("effect_applied", "effect_applied_field"),
    )
    for kind, field_name in optional_kinds:
        path = _text(spec.get(field_name))
        if not path:
            continue
        value, error = _extract_required(
            raw_event,
            path,
            extractor=extractor,
            kind=kind,
        )
        if error:
            errors.append(error)
        else:
            values[kind] = value

    if errors:
        return {}, ",".join(sorted(set(errors)))
    values["metadata_fingerprint"] = _fingerprint(values)
    return values, ""


def _resolve_expected(
    contract: dict[str, Any],
    bindings: dict[str, Any],
    *,
    literal_field: str,
    binding_field: str,
) -> tuple[Any, str]:
    binding = _text(contract.get(binding_field))
    if binding:
        value = bindings.get(binding)
        if not _scalar(value):
            return None, binding
        return value, ""
    literal = contract.get(literal_field)
    return literal, ""


def _partition_poll_checkpoints(
    rows: list[dict[str, Any]],
) -> tuple[dict[Any, list[tuple[int, int]]], int]:
    grouped: dict[tuple[Any, int], set[int]] = {}
    for row in rows:
        broker = _dict(row.get("broker"))
        if not broker:
            continue
        key = (broker.get("partition"), int(row.get("poll_number") or 0))
        grouped.setdefault(key, set()).add(int(broker.get("checkpoint") or 0))

    conflicts = sum(1 for values in grouped.values() if len(values) > 1)
    by_partition: dict[Any, list[tuple[int, int]]] = {}
    for (partition, poll_number), values in grouped.items():
        if len(values) != 1:
            continue
        by_partition.setdefault(partition, []).append(
            (poll_number, next(iter(values)))
        )
    for values in by_partition.values():
        values.sort(key=lambda item: item[0])
    return by_partition, conflicts


def evaluate_broker_delivery_window(
    *,
    contract: dict[str, Any],
    rows: list[dict[str, Any]],
    unique_rows: list[dict[str, Any]],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate one bounded broker observation window without raw identities."""
    spec = _dict(contract)
    if not spec:
        return {
            "status": STATUS_PASS,
            "reason_codes": [],
            "contract_fingerprint": "",
            "broker_model": "",
        }

    expected_topic, unresolved_topic = _resolve_expected(
        spec,
        bindings,
        literal_field="expected_topic",
        binding_field="topic_binding",
    )
    expected_group, unresolved_group = _resolve_expected(
        spec,
        bindings,
        literal_field="expected_consumer_group",
        binding_field="consumer_group_binding",
    )
    unresolved = [value for value in (unresolved_topic, unresolved_group) if value]
    if unresolved:
        return {
            "status": STATUS_INDETERMINATE,
            "reason_codes": [BROKER_EXPECTATION_BINDING_UNRESOLVED],
            "unresolved_binding_fingerprints": sorted(
                _fingerprint(value) for value in unresolved
            ),
            "contract_fingerprint": _text(spec.get("contract_fingerprint")),
            "broker_model": _text(spec.get("broker_model")),
        }

    metadata_incomplete_count = sum(
        1
        for row in rows
        if _text(row.get("broker_metadata_error"))
        or not _dict(row.get("broker"))
    )
    if metadata_incomplete_count:
        return {
            "status": STATUS_INDETERMINATE,
            "reason_codes": [BROKER_METADATA_INCOMPLETE],
            "metadata_incomplete_count": metadata_incomplete_count,
            "contract_fingerprint": _text(spec.get("contract_fingerprint")),
            "broker_model": _text(spec.get("broker_model")),
        }

    unique = [row for row in unique_rows if _dict(row.get("broker"))]
    brokers = [_dict(row.get("broker")) for row in unique]
    topic_mismatch_count = sum(
        1 for broker in brokers if str(broker.get("topic")) != str(expected_topic)
    )
    group_mismatch_count = sum(
        1
        for broker in brokers
        if str(broker.get("consumer_group")) != str(expected_group)
    )

    offset_owners: dict[tuple[Any, int], tuple[str, str]] = {}
    partition_offset_conflict_count = 0
    for row in unique:
        broker = _dict(row.get("broker"))
        key = (broker.get("partition"), int(broker.get("offset") or 0))
        owner = (
            _text(row.get("event_id")),
            _text(row.get("payload_fingerprint")),
        )
        previous = offset_owners.get(key)
        if previous is not None and previous != owner:
            partition_offset_conflict_count += 1
        else:
            offset_owners[key] = owner

    checkpoint_rows, checkpoint_conflict_count = _partition_poll_checkpoints(rows)
    checkpoint_regression_count = 0
    final_checkpoint_by_partition: dict[Any, int] = {}
    for partition, values in checkpoint_rows.items():
        previous: int | None = None
        for _, checkpoint in values:
            if previous is not None and checkpoint < previous:
                checkpoint_regression_count += 1
            previous = checkpoint
        if values:
            final_checkpoint_by_partition[partition] = values[-1][1]

    max_offset_by_partition: dict[Any, int] = {}
    for broker in brokers:
        partition = broker.get("partition")
        offset = int(broker.get("offset") or 0)
        max_offset_by_partition[partition] = max(
            offset,
            max_offset_by_partition.get(partition, offset),
        )
    checkpoint_behind_count = 0
    if _text(spec.get("checkpoint_policy")) == "must_cover_observed":
        for partition, max_offset in max_offset_by_partition.items():
            final_checkpoint = final_checkpoint_by_partition.get(partition)
            if final_checkpoint is None or final_checkpoint < max_offset:
                checkpoint_behind_count += 1

    dead_letter_states = {
        _text(value) for value in _list(spec.get("dead_letter_states")) if _text(value)
    }
    dlq_delivery_count = 0
    for broker in brokers:
        state = _text(broker.get("delivery_state"))
        dead_letter_topic = _text(broker.get("dead_letter_topic"))
        if state in dead_letter_states or dead_letter_topic:
            dlq_delivery_count += 1
    unexpected_dlq_count = (
        dlq_delivery_count
        if _text(spec.get("dlq_policy")) == "forbidden"
        else 0
    )

    sequence_violation_count = 0
    if _text(spec.get("ordering_policy")) == "source_sequence":
        groups: dict[tuple[Any, Any], list[tuple[int, int]]] = {}
        for broker in brokers:
            key = (broker.get("partition"), broker.get("ordering_key"))
            groups.setdefault(key, []).append(
                (int(broker.get("offset") or 0), int(broker.get("sequence") or 0))
            )
        for values in groups.values():
            values.sort(key=lambda item: item[0])
            previous_sequence: int | None = None
            for _, sequence in values:
                if previous_sequence is not None and sequence <= previous_sequence:
                    sequence_violation_count += 1
                previous_sequence = sequence

    restart_replay_count = 0
    restart_duplicate_effect_count = 0
    if spec.get("restart_deduplication_required") is True:
        groups: dict[Any, list[dict[str, Any]]] = {}
        for broker in brokers:
            groups.setdefault(broker.get("deduplication_key"), []).append(broker)
        for values in groups.values():
            epochs = {value.get("consumer_epoch") for value in values}
            if len(epochs) > 1 and len(values) > 1:
                restart_replay_count += len(values) - 1
            applied_count = sum(
                1 for value in values if value.get("effect_applied") is True
            )
            if applied_count > 1:
                restart_duplicate_effect_count += applied_count - 1

    reason_counts = (
        (BROKER_TOPIC_MISMATCH, topic_mismatch_count),
        (BROKER_CONSUMER_GROUP_MISMATCH, group_mismatch_count),
        (BROKER_PARTITION_OFFSET_CONFLICT, partition_offset_conflict_count),
        (BROKER_CHECKPOINT_CONFLICT, checkpoint_conflict_count),
        (BROKER_CHECKPOINT_REGRESSION, checkpoint_regression_count),
        (BROKER_CHECKPOINT_BEHIND_OBSERVED, checkpoint_behind_count),
        (BROKER_DLQ_DELIVERY_UNEXPECTED, unexpected_dlq_count),
        (BROKER_SEQUENCE_ORDER_VIOLATION, sequence_violation_count),
        (
            BROKER_RESTART_DEDUPLICATION_VIOLATION,
            restart_duplicate_effect_count,
        ),
    )
    reason_codes = [
        reason_code
        for reason_code, count in reason_counts
        if count
    ]
    return {
        "status": STATUS_VIOLATION if reason_codes else STATUS_PASS,
        "reason_codes": reason_codes,
        "contract_fingerprint": _text(spec.get("contract_fingerprint")),
        "broker_model": _text(spec.get("broker_model")),
        "metadata_incomplete_count": 0,
        "topic_mismatch_count": topic_mismatch_count,
        "consumer_group_mismatch_count": group_mismatch_count,
        "partition_count": len({broker.get("partition") for broker in brokers}),
        "observed_offset_count": len(offset_owners),
        "partition_offset_conflict_count": partition_offset_conflict_count,
        "checkpoint_conflict_count": checkpoint_conflict_count,
        "checkpoint_regression_count": checkpoint_regression_count,
        "checkpoint_behind_observed_count": checkpoint_behind_count,
        "dlq_delivery_count": dlq_delivery_count,
        "unexpected_dlq_delivery_count": unexpected_dlq_count,
        "sequence_order_violation_count": sequence_violation_count,
        "restart_replay_count": restart_replay_count,
        "restart_duplicate_effect_count": restart_duplicate_effect_count,
        "topic_fingerprints": sorted(
            {_fingerprint(broker.get("topic")) for broker in brokers}
        ),
        "consumer_group_fingerprints": sorted(
            {_fingerprint(broker.get("consumer_group")) for broker in brokers}
        ),
        "consumer_epoch_fingerprints": sorted(
            {
                _fingerprint(broker.get("consumer_epoch"))
                for broker in brokers
                if broker.get("consumer_epoch") is not None
            }
        ),
        "checkpoint_state_fingerprint": _fingerprint(
            {
                str(partition): checkpoint
                for partition, checkpoint in sorted(
                    final_checkpoint_by_partition.items(),
                    key=lambda item: str(item[0]),
                )
            }
        ),
    }


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "STATUS_PASS",
    "STATUS_VIOLATION",
    "STATUS_INDETERMINATE",
    "BROKER_DELIVERY_INVALID",
    "BROKER_EXPECTATION_BINDING_UNRESOLVED",
    "BROKER_METADATA_INCOMPLETE",
    "BROKER_TOPIC_MISMATCH",
    "BROKER_CONSUMER_GROUP_MISMATCH",
    "BROKER_PARTITION_OFFSET_CONFLICT",
    "BROKER_CHECKPOINT_CONFLICT",
    "BROKER_CHECKPOINT_REGRESSION",
    "BROKER_CHECKPOINT_BEHIND_OBSERVED",
    "BROKER_DLQ_DELIVERY_UNEXPECTED",
    "BROKER_SEQUENCE_ORDER_VIOLATION",
    "BROKER_RESTART_DEDUPLICATION_VIOLATION",
    "compile_broker_delivery_contract",
    "extract_broker_metadata",
    "evaluate_broker_delivery_window",
]
