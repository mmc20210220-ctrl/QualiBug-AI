"""Source-declared event delivery contracts for process-graph async edges.

This module owns neither scheduling nor target selection. The existing graph
wait gate invokes it for a compile-frozen message/callback transition. Runtime
observes the entire bounded window, separates observation completeness from the
business verdict, and emits only counts and content fingerprints.

Optional partitioned-log semantics are delegated to
``process_graph_broker_delivery``. That semantic subcontract does not create a
second scheduler, transport, ledger, observer registry, or finalizer.
"""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Callable
from urllib.parse import urlencode

from . import process_graph_broker_delivery as _broker
from .async_readback_executor import normalize_async_policy
from .experiment_runtime_support import _resolve_token, _run_http_step
from .real_id_resolver import path_has_placeholders
from .runtime_binding_materializer import materialize_path


CONTRACT_SCHEMA_VERSION = "qualibug.process-graph-event-transition.v1"
RECEIPT_SCHEMA_VERSION = "qualibug.process-graph-event-transition-receipt.v1"
STATUS_COMPILED = "COMPILED"
STATUS_CONVERGED = "CONVERGED"
STATUS_BLOCKED = "BLOCKED"

EVENT_TRANSITION_INVALID = "PROCESS_GRAPH_EVENT_TRANSITION_INVALID"
EVENT_CORRELATION_UNRESOLVED = "PROCESS_GRAPH_EVENT_CORRELATION_UNRESOLVED"
EVENT_OBSERVER_ACTOR_UNRESOLVED = "PROCESS_GRAPH_EVENT_OBSERVER_ACTOR_UNRESOLVED"
EVENT_OBSERVER_BINDING_UNRESOLVED = "PROCESS_GRAPH_EVENT_OBSERVER_BINDING_UNRESOLVED"
EVENT_OBSERVATION_INCOMPLETE = "PROCESS_GRAPH_EVENT_OBSERVATION_INCOMPLETE"
EVENT_COLLECTION_INVALID = "PROCESS_GRAPH_EVENT_COLLECTION_INVALID"
EVENT_DELIVERY_COUNT_BELOW_MINIMUM = "PROCESS_GRAPH_EVENT_DELIVERY_COUNT_BELOW_MINIMUM"
EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM = "PROCESS_GRAPH_EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM"
EVENT_ID_REUSE_CONFLICT = "PROCESS_GRAPH_EVENT_ID_REUSE_CONFLICT"
EVENT_IDEMPOTENCY_KEY_MISMATCH = "PROCESS_GRAPH_EVENT_IDEMPOTENCY_KEY_MISMATCH"
EVENT_CORRELATION_IDENTITY_MISMATCH = (
    "PROCESS_GRAPH_EVENT_CORRELATION_IDENTITY_MISMATCH"
)
EVENT_IDENTITY_TYPE_CONFLICT = "PROCESS_GRAPH_EVENT_IDENTITY_TYPE_CONFLICT"
EVENT_RETRY_LIMIT_EXCEEDED = "PROCESS_GRAPH_EVENT_RETRY_LIMIT_EXCEEDED"

BROKER_DELIVERY_INVALID = _broker.BROKER_DELIVERY_INVALID
BROKER_EXPECTATION_BINDING_UNRESOLVED = (
    _broker.BROKER_EXPECTATION_BINDING_UNRESOLVED
)
BROKER_METADATA_INCOMPLETE = _broker.BROKER_METADATA_INCOMPLETE
BROKER_TOPIC_MISMATCH = _broker.BROKER_TOPIC_MISMATCH
BROKER_CONSUMER_GROUP_MISMATCH = _broker.BROKER_CONSUMER_GROUP_MISMATCH
BROKER_PARTITION_OFFSET_CONFLICT = _broker.BROKER_PARTITION_OFFSET_CONFLICT
BROKER_CHECKPOINT_CONFLICT = _broker.BROKER_CHECKPOINT_CONFLICT
BROKER_CHECKPOINT_REGRESSION = _broker.BROKER_CHECKPOINT_REGRESSION
BROKER_CHECKPOINT_BEHIND_OBSERVED = _broker.BROKER_CHECKPOINT_BEHIND_OBSERVED
BROKER_DLQ_DELIVERY_UNEXPECTED = _broker.BROKER_DLQ_DELIVERY_UNEXPECTED
BROKER_SEQUENCE_ORDER_VIOLATION = _broker.BROKER_SEQUENCE_ORDER_VIOLATION
BROKER_RESTART_DEDUPLICATION_VIOLATION = (
    _broker.BROKER_RESTART_DEDUPLICATION_VIOLATION
)

_EVENT_RELATIONS = frozenset(
    {"AWAITS", "NOTIFIES", "TRIGGERS", "MESSAGE", "ASYNC_MESSAGE"}
)
_DELIVERY_KINDS = frozenset({"message", "callback", "notification", "event"})
_DELIVERY_SEMANTICS = frozenset({"at_least_once", "exactly_once"})
_MAX_EVENTS_PER_POLL = 500


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


def _identity(value: Any) -> str:
    """Return a type-sensitive scalar identity without leaking raw values."""
    return _canonical(value)


def _extract(value: Any, path: str) -> tuple[bool, Any]:
    token = _text(path)
    if token in {"", "$"}:
        return True, value
    if token.startswith("$."):
        token = token[2:]
    current = value
    for part in [item for item in token.split(".") if item]:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _delete_path(value: Any, path: str) -> None:
    token = _text(path)
    if token.startswith("$."):
        token = token[2:]
    parts = [item for item in token.split(".") if item]
    if not parts or not isinstance(value, dict):
        return
    current = value
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _event_spec(raw_wait: dict[str, Any]) -> dict[str, Any]:
    source = _dict(raw_wait)
    return deepcopy(
        _dict(
            source.get("event_transition")
            or source.get("event_contract")
            or source.get("delivery_contract")
        )
    )


def has_event_transition(raw_wait: dict[str, Any]) -> bool:
    return bool(_event_spec(raw_wait))


def compile_event_transition_contract(
    *,
    raw_wait: dict[str, Any],
    compiled_wait: dict[str, Any],
    relation_type: str,
) -> tuple[dict[str, Any], str]:
    event = _event_spec(raw_wait)
    if not event:
        return {}, ""
    relation = _text(relation_type).upper()
    if relation not in _EVENT_RELATIONS:
        return {}, f"event_relation_invalid:{relation or '<empty>'}"
    kind = _text(
        event.get("delivery_kind")
        or event.get("transition_kind")
        or _dict(raw_wait).get("transition_kind")
        or "event"
    ).lower()
    if kind not in _DELIVERY_KINDS:
        return {}, f"event_delivery_kind_invalid:{kind}"
    semantics = _text(
        event.get("delivery_semantics") or event.get("semantics")
    ).lower()
    if semantics not in _DELIVERY_SEMANTICS:
        return {}, f"event_delivery_semantics_invalid:{semantics or '<empty>'}"

    required = {
        "events_path": _text(event.get("events_path")),
        "event_id_field": _text(event.get("event_id_field")),
        "event_type_field": _text(event.get("event_type_field")),
        "correlation_field": _text(event.get("correlation_field")),
        "correlation_binding": _text(
            event.get("correlation_binding")
            or _dict(event.get("correlation_source")).get("binding")
        ),
        "correlation_query_parameter": _text(
            event.get("correlation_query_parameter")
        ),
        "expected_event_type": _text(event.get("expected_event_type")),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return {}, "event_fields_missing:" + ",".join(missing)
    try:
        minimum = int(event.get("expected_min_count"))
        maximum = int(event.get("expected_max_count"))
    except (TypeError, ValueError):
        return {}, "event_expected_count_not_integer"
    if minimum < 0 or maximum < minimum or maximum > _MAX_EVENTS_PER_POLL:
        return {}, "event_expected_count_range_invalid"
    if semantics == "exactly_once" and (minimum != 1 or maximum != 1):
        return {}, "exactly_once_requires_expected_count_one"

    policy = normalize_async_policy(compiled_wait.get("async_policy"))
    if policy.get("enabled") is not True or int(policy.get("max_attempts") or 0) < 2:
        return {}, "event_observation_requires_enabled_bounded_policy"

    idempotency_binding = _text(event.get("idempotency_key_binding"))
    idempotency_field = _text(event.get("idempotency_key_field"))
    if bool(idempotency_binding) != bool(idempotency_field):
        return {}, "event_idempotency_binding_and_field_must_pair"

    retry_field = _text(event.get("delivery_attempt_field"))
    retry_limit_raw = event.get("expected_max_delivery_attempt")
    retry_limit = 0
    if retry_field or retry_limit_raw not in (None, ""):
        if not retry_field:
            return {}, "event_delivery_attempt_field_missing"
        try:
            retry_limit = int(retry_limit_raw)
        except (TypeError, ValueError):
            return {}, "event_delivery_attempt_limit_not_integer"
        if retry_limit < 1 or retry_limit > 100:
            return {}, "event_delivery_attempt_limit_invalid"

    broker_contract, broker_error = _broker.compile_broker_delivery_contract(event)
    if broker_error:
        return {}, f"broker_delivery_invalid:{broker_error}"

    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "status": STATUS_COMPILED,
        "wait_id": _text(compiled_wait.get("wait_id")),
        "source_node_id": _text(compiled_wait.get("source_node_id")),
        "target_node_id": _text(compiled_wait.get("target_node_id")),
        "relation_type": relation,
        "delivery_kind": kind,
        "delivery_semantics": semantics,
        "observer_operation_ref": _text(compiled_wait.get("observer_operation_ref")),
        "observer_method": _text(compiled_wait.get("method")).upper(),
        "observer_path_template": _text(compiled_wait.get("path_template")),
        "actor_ref": _text(compiled_wait.get("actor_ref")),
        "system_ref": _text(compiled_wait.get("system_ref")),
        **required,
        "expected_min_count": minimum,
        "expected_max_count": maximum,
        "idempotency_key_binding": idempotency_binding,
        "idempotency_key_field": idempotency_field,
        "delivery_attempt_field": retry_field,
        "expected_max_delivery_attempt": retry_limit,
        "broker_delivery_contract": broker_contract,
        "async_policy": policy,
        "source_refs": _list(event.get("source_refs")),
    }
    contract["contract_fingerprint"] = _fingerprint(contract)
    return contract, ""


def _payload_fingerprint(
    raw: dict[str, Any],
    contract: dict[str, Any],
) -> str:
    payload = deepcopy(raw)
    mutable_paths = [_text(contract.get("delivery_attempt_field"))]
    broker = _dict(contract.get("broker_delivery_contract"))
    for field_name in (
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
    ):
        mutable_paths.append(_text(broker.get(field_name)))
    for path in mutable_paths:
        if path:
            _delete_path(payload, path)
    return _fingerprint(payload)


def _event_rows(
    body: Any,
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    found, raw_events = _extract(body, _text(contract.get("events_path")))
    if not found or not isinstance(raw_events, list):
        return [], EVENT_COLLECTION_INVALID
    if len(raw_events) > _MAX_EVENTS_PER_POLL:
        return [], EVENT_COLLECTION_INVALID
    rows: list[dict[str, Any]] = []
    broker_contract = _dict(contract.get("broker_delivery_contract"))
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        id_found, event_id = _extract(raw, _text(contract.get("event_id_field")))
        type_found, event_type = _extract(raw, _text(contract.get("event_type_field")))
        corr_found, correlation = _extract(raw, _text(contract.get("correlation_field")))
        if not (
            id_found
            and type_found
            and corr_found
            and _scalar(event_id)
            and _scalar(event_type)
            and _scalar(correlation)
        ):
            continue
        idempotency_value: Any = None
        idempotency_field = _text(contract.get("idempotency_key_field"))
        if idempotency_field:
            idem_found, idempotency_value = _extract(raw, idempotency_field)
            if not idem_found or not _scalar(idempotency_value):
                idempotency_value = None
        delivery_attempt: int | None = None
        attempt_field = _text(contract.get("delivery_attempt_field"))
        if attempt_field:
            attempt_found, raw_attempt = _extract(raw, attempt_field)
            try:
                delivery_attempt = int(raw_attempt) if attempt_found else None
            except (TypeError, ValueError):
                delivery_attempt = None
        broker_metadata, broker_error = _broker.extract_broker_metadata(
            raw,
            broker_contract,
            extractor=_extract,
        )
        rows.append(
            {
                "event_id": event_id,
                "event_type": event_type,
                "correlation": correlation,
                "idempotency_value": idempotency_value,
                "delivery_attempt": delivery_attempt,
                "broker": broker_metadata,
                "broker_metadata_error": broker_error,
                "payload_fingerprint": _payload_fingerprint(raw, contract),
            }
        )
    return rows, ""


def _blocked_receipt(
    contract: dict[str, Any],
    reason_code: str,
    *,
    detail: str,
) -> dict[str, Any]:
    broker = _dict(contract.get("broker_delivery_contract"))
    idempotency_proof = _dict(
        contract.get("idempotency_binding_contract")
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": STATUS_BLOCKED,
        "semantic_status": "INDETERMINATE",
        "reason_code": reason_code,
        "semantic_reason_codes": [reason_code] if reason_code else [],
        "detail": detail,
        "wait_id": _text(contract.get("wait_id")),
        "source_node_id": _text(contract.get("source_node_id")),
        "target_node_id": _text(contract.get("target_node_id")),
        "contract_fingerprint": _text(contract.get("contract_fingerprint")),
        "idempotency_scope_authority": (
            "source_request_binding_contract"
            if idempotency_proof
            else ""
        ),
        "idempotency_binding_contract_fingerprint": _text(
            idempotency_proof.get("contract_fingerprint")
        ),
        "source_request_contract_fingerprint": _text(
            idempotency_proof.get("source_request_contract_fingerprint")
        ),
        "broker_contract_fingerprint": _text(
            broker.get("contract_fingerprint")
        ),
        "broker_semantic_status": (
            _broker.STATUS_INDETERMINATE if broker else ""
        ),
        "attempt_count": 0,
        "coverage_complete": False,
        "observation_window_completed": False,
        "converged": False,
        "timed_out": False,
    }
    receipt["receipt_id"] = "event_wait_" + _fingerprint(receipt)[:24]
    return receipt


def _broker_receipt_projection(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in _dict(result).items()
        if key
        not in {
            "status",
            "reason_codes",
            "unresolved_binding_fingerprints",
        }
    }


def execute_event_transition(
    *,
    contract: dict[str, Any],
    context: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    read_once: Callable[[], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    spec = _dict(contract)
    bindings = _dict(_dict(context).get("bindings"))
    correlation_key = _text(spec.get("correlation_binding"))
    correlation = bindings.get(correlation_key)
    if not _scalar(correlation):
        return _blocked_receipt(
            spec,
            EVENT_CORRELATION_UNRESOLVED,
            detail=correlation_key or "correlation_binding_missing",
        )
    idempotency_key = _text(spec.get("idempotency_key_binding"))
    expected_idempotency = bindings.get(idempotency_key) if idempotency_key else None
    if idempotency_key and not _scalar(expected_idempotency):
        return _blocked_receipt(
            spec,
            EVENT_CORRELATION_UNRESOLVED,
            detail=f"idempotency_binding_unresolved:{idempotency_key}",
        )

    actor_ref = _text(spec.get("actor_ref"))
    actor = _dict(actors.get(actor_ref))
    token = _resolve_token(actor, tokens)
    if not actor or (
        _text(actor.get("role")).lower() not in {"anonymous", "public"} and not token
    ):
        return _blocked_receipt(
            spec,
            EVENT_OBSERVER_ACTOR_UNRESOLVED,
            detail=actor_ref or "actor_ref_missing",
        )

    path_template = _text(spec.get("observer_path_template"))
    path = materialize_path(path_template, bindings)
    if not path.startswith("/") or path_has_placeholders(path):
        return _blocked_receipt(
            spec,
            EVENT_OBSERVER_BINDING_UNRESOLVED,
            detail=path_template or "observer_path_missing",
        )
    separator = "&" if "?" in path else "?"
    request_path = path + separator + urlencode(
        {_text(spec.get("correlation_query_parameter")): str(correlation)}
    )
    reader = read_once or (
        lambda: _run_http_step(
            base_url=_text(_dict(context).get("base_url")),
            method=_text(spec.get("observer_method")),
            path=request_path,
            token=token,
        )
    )

    policy = normalize_async_policy(spec.get("async_policy"))
    max_attempts = int(policy.get("max_attempts") or 1)
    started = monotonic()
    attempts: list[dict[str, Any]] = []
    events_by_id: dict[str, dict[str, Any]] = {}
    event_id_payloads: dict[str, str] = {}
    event_id_values: dict[str, Any] = {}
    event_id_aliases: dict[str, set[str]] = {}
    correlation_alias_event_ids: set[str] = set()
    out_of_scope_idempotency_event_ids: set[str] = set()
    missing_idempotency_event_ids: set[str] = set()
    broker_observation_rows: list[dict[str, Any]] = []
    event_id_reuse_conflicts = 0
    correlation_identity_mismatches = 0
    event_identity_type_conflicts = 0
    retry_limit_violations = 0
    poll_replay_count = 0
    invalid_collection_count = 0
    successful_polls = 0
    correlated_rows_seen = 0
    matching_rows_seen = 0

    for attempt_number in range(1, max_attempts + 1):
        response = _dict(reader())
        status_code = int(response.get("status_code") or response.get("status") or 0)
        rows: list[dict[str, Any]] = []
        collection_error = ""
        if 200 <= status_code < 300:
            successful_polls += 1
            rows, collection_error = _event_rows(response.get("body"), spec)
            if collection_error:
                invalid_collection_count += 1
        expected_event_type = _text(spec.get("expected_event_type"))
        correlation_identity = _identity(correlation)
        correlated: list[dict[str, Any]] = []
        for row in rows:
            if _text(row.get("event_type")) != expected_event_type:
                continue
            observed_correlation = row.get("correlation")
            observed_identity = _identity(observed_correlation)
            if observed_identity == correlation_identity:
                correlated.append(row)
                continue
            if str(observed_correlation) == str(correlation):
                correlation_alias_event_ids.add(_identity(row.get("event_id")))
        correlation_identity_mismatches = len(correlation_alias_event_ids)
        correlated_rows_seen += len(correlated)

        in_scope: list[dict[str, Any]] = []
        foreign_in_poll = 0
        missing_in_poll = 0
        for row in correlated:
            if not idempotency_key:
                in_scope.append(row)
                continue
            event_identity = _identity(row.get("event_id"))
            idempotency_value = row.get("idempotency_value")
            if not _scalar(idempotency_value):
                missing_idempotency_event_ids.add(event_identity)
                missing_in_poll += 1
                continue
            if _identity(idempotency_value) != _identity(expected_idempotency):
                out_of_scope_idempotency_event_ids.add(event_identity)
                foreign_in_poll += 1
                continue
            in_scope.append(row)

        matching_rows_seen += len(in_scope)
        newly_unique = 0
        for row in in_scope:
            observed_row = {**row, "poll_number": attempt_number}
            broker_observation_rows.append(observed_row)
            raw_event_id = row.get("event_id")
            event_identity = _identity(raw_event_id)
            payload_fp = _text(row.get("payload_fingerprint"))
            if event_identity in events_by_id:
                poll_replay_count += 1
                if event_id_payloads.get(event_identity) != payload_fp:
                    event_id_reuse_conflicts += 1
                continue
            display_identity = str(raw_event_id)
            aliases = event_id_aliases.setdefault(display_identity, set())
            if aliases and event_identity not in aliases:
                event_identity_type_conflicts += 1
            aliases.add(event_identity)
            events_by_id[event_identity] = observed_row
            event_id_payloads[event_identity] = payload_fp
            event_id_values[event_identity] = raw_event_id
            newly_unique += 1
            retry_limit = int(spec.get("expected_max_delivery_attempt") or 0)
            if retry_limit:
                delivery_attempt = row.get("delivery_attempt")
                if not isinstance(delivery_attempt, int) or delivery_attempt > retry_limit:
                    retry_limit_violations += 1
        attempts.append(
            {
                "attempt": attempt_number,
                "elapsed_ms": max(0, int((monotonic() - started) * 1000)),
                "status_code": status_code,
                "collection_valid": not collection_error,
                "correlated_row_count": len(correlated),
                "matching_row_count": len(in_scope),
                "out_of_scope_idempotency_row_count": foreign_in_poll,
                "missing_idempotency_row_count": missing_in_poll,
                "new_unique_event_count": newly_unique,
                "unique_event_count": len(events_by_id),
            }
        )
        if attempt_number < max_attempts:
            sleep(int(policy.get("poll_interval_ms") or 0) / 1000.0)

    unique_count = len(events_by_id)
    minimum = int(spec.get("expected_min_count") or 0)
    maximum = int(spec.get("expected_max_count") or 0)
    idempotency_mismatches = len(missing_idempotency_event_ids)
    if unique_count < minimum:
        idempotency_mismatches += len(out_of_scope_idempotency_event_ids)
    base_coverage_complete = bool(
        len(attempts) == max_attempts
        and successful_polls == len(attempts)
        and invalid_collection_count == 0
    )
    broker_result = _broker.evaluate_broker_delivery_window(
        contract=_dict(spec.get("broker_delivery_contract")),
        rows=broker_observation_rows,
        unique_rows=list(events_by_id.values()),
        bindings=bindings,
    )
    broker_status = _text(broker_result.get("status"))
    broker_reason_codes = [
        _text(value)
        for value in _list(broker_result.get("reason_codes"))
        if _text(value)
    ]

    semantic_status = "PASS"
    reason_codes: list[str] = []
    if not base_coverage_complete:
        semantic_status = "INDETERMINATE"
        reason_codes.append(EVENT_OBSERVATION_INCOMPLETE)
    elif broker_status == _broker.STATUS_INDETERMINATE:
        semantic_status = "INDETERMINATE"
        reason_codes.extend(broker_reason_codes or [BROKER_METADATA_INCOMPLETE])
    else:
        if correlation_identity_mismatches:
            reason_codes.append(EVENT_CORRELATION_IDENTITY_MISMATCH)
        if event_id_reuse_conflicts:
            reason_codes.append(EVENT_ID_REUSE_CONFLICT)
        if event_identity_type_conflicts:
            reason_codes.append(EVENT_IDENTITY_TYPE_CONFLICT)
        if idempotency_mismatches:
            reason_codes.append(EVENT_IDEMPOTENCY_KEY_MISMATCH)
        if retry_limit_violations:
            reason_codes.append(EVENT_RETRY_LIMIT_EXCEEDED)
        if broker_status == _broker.STATUS_VIOLATION:
            reason_codes.extend(broker_reason_codes)
        if unique_count < minimum:
            reason_codes.append(EVENT_DELIVERY_COUNT_BELOW_MINIMUM)
        if unique_count > maximum:
            reason_codes.append(EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM)
        if reason_codes:
            semantic_status = "VIOLATION"

    reason_codes = list(dict.fromkeys(reason_codes))
    reason_code = reason_codes[0] if reason_codes else ""
    observation_complete = semantic_status in {"PASS", "VIOLATION"}
    broker_projection = _broker_receipt_projection(broker_result)
    broker_contract = _dict(spec.get("broker_delivery_contract"))
    idempotency_proof = _dict(spec.get("idempotency_binding_contract"))
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": STATUS_CONVERGED if observation_complete else STATUS_BLOCKED,
        "semantic_status": semantic_status,
        "reason_code": reason_code,
        "semantic_reason_codes": reason_codes,
        "wait_id": _text(spec.get("wait_id")),
        "source_node_id": _text(spec.get("source_node_id")),
        "target_node_id": _text(spec.get("target_node_id")),
        "relation_type": _text(spec.get("relation_type")),
        "delivery_kind": _text(spec.get("delivery_kind")),
        "delivery_semantics": _text(spec.get("delivery_semantics")),
        "contract_fingerprint": _text(spec.get("contract_fingerprint")),
        "observer_operation_ref": _text(spec.get("observer_operation_ref")),
        "observer_method": _text(spec.get("observer_method")),
        "observer_path_template": path_template,
        "actor_ref": actor_ref,
        "system_ref": _text(spec.get("system_ref")),
        "expected_event_type": _text(spec.get("expected_event_type")),
        "expected_min_count": minimum,
        "expected_max_count": maximum,
        "event_scope_mode": (
            "correlation_and_idempotency"
            if idempotency_key
            else "correlation_only"
        ),
        "observed_correlated_row_count": correlated_rows_seen,
        "observed_matching_row_count": matching_rows_seen,
        "observed_unique_event_count": unique_count,
        "poll_replay_count": poll_replay_count,
        "distinct_delivery_overflow_count": max(0, unique_count - maximum),
        "event_id_reuse_conflict_count": event_id_reuse_conflicts,
        "event_identity_type_conflict_count": event_identity_type_conflicts,
        "correlation_identity_mismatch_count": correlation_identity_mismatches,
        "idempotency_mismatch_count": idempotency_mismatches,
        "out_of_scope_idempotency_event_count": len(
            out_of_scope_idempotency_event_ids
        ),
        "missing_idempotency_event_count": len(
            missing_idempotency_event_ids
        ),
        "retry_limit_violation_count": retry_limit_violations,
        "broker_contract_fingerprint": _text(
            broker_contract.get("contract_fingerprint")
        ),
        "broker_semantic_status": broker_status if broker_contract else "",
        "broker_reason_codes": broker_reason_codes,
        "broker_evidence": broker_projection if broker_contract else {},
        "successful_poll_count": successful_polls,
        "attempt_count": len(attempts),
        "coverage_complete": (
            base_coverage_complete
            and broker_status != _broker.STATUS_INDETERMINATE
        ),
        "observation_window_completed": len(attempts) == max_attempts,
        "correlation_fingerprint": _fingerprint(correlation),
        "idempotency_key_fingerprint": (
            _fingerprint(expected_idempotency) if idempotency_key else ""
        ),
        "idempotency_scope_authority": (
            "source_request_binding_contract"
            if idempotency_proof
            else ""
        ),
        "idempotency_binding_contract_fingerprint": _text(
            idempotency_proof.get("contract_fingerprint")
        ),
        "source_request_contract_fingerprint": _text(
            idempotency_proof.get("source_request_contract_fingerprint")
        ),
        "event_id_fingerprints": sorted(
            _fingerprint(value) for value in event_id_values.values()
        ),
        "attempts": attempts,
        "converged": observation_complete,
        "timed_out": semantic_status == "INDETERMINATE",
        "elapsed_ms": max(0, int((monotonic() - started) * 1000)),
    }
    receipt["receipt_id"] = "event_wait_" + _fingerprint(receipt)[:24]
    return receipt


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "STATUS_COMPILED",
    "STATUS_CONVERGED",
    "STATUS_BLOCKED",
    "EVENT_TRANSITION_INVALID",
    "EVENT_CORRELATION_UNRESOLVED",
    "EVENT_OBSERVER_ACTOR_UNRESOLVED",
    "EVENT_OBSERVER_BINDING_UNRESOLVED",
    "EVENT_OBSERVATION_INCOMPLETE",
    "EVENT_COLLECTION_INVALID",
    "EVENT_DELIVERY_COUNT_BELOW_MINIMUM",
    "EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM",
    "EVENT_ID_REUSE_CONFLICT",
    "EVENT_IDEMPOTENCY_KEY_MISMATCH",
    "EVENT_CORRELATION_IDENTITY_MISMATCH",
    "EVENT_IDENTITY_TYPE_CONFLICT",
    "EVENT_RETRY_LIMIT_EXCEEDED",
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
    "has_event_transition",
    "compile_event_transition_contract",
    "execute_event_transition",
]
