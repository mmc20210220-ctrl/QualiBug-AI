"""Source-declared event delivery contracts for process-graph async edges.

This module owns neither scheduling nor target selection. The existing graph
wait gate invokes it for a compile-frozen message/callback transition. Runtime
observes the entire bounded window, separates observation completeness from the
business verdict, and emits only counts and content fingerprints.
"""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Callable
from urllib.parse import urlencode

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
EVENT_RETRY_LIMIT_EXCEEDED = "PROCESS_GRAPH_EVENT_RETRY_LIMIT_EXCEEDED"

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
        "async_policy": policy,
        "source_refs": _list(event.get("source_refs")),
    }
    contract["contract_fingerprint"] = _fingerprint(contract)
    return contract, ""


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
        rows.append(
            {
                "event_id": event_id,
                "event_type": event_type,
                "correlation": correlation,
                "idempotency_value": idempotency_value,
                "delivery_attempt": delivery_attempt,
                "payload_fingerprint": _fingerprint(raw),
            }
        )
    return rows, ""


def _blocked_receipt(
    contract: dict[str, Any],
    reason_code: str,
    *,
    detail: str,
) -> dict[str, Any]:
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": STATUS_BLOCKED,
        "semantic_status": "INDETERMINATE",
        "reason_code": reason_code,
        "detail": detail,
        "wait_id": _text(contract.get("wait_id")),
        "source_node_id": _text(contract.get("source_node_id")),
        "target_node_id": _text(contract.get("target_node_id")),
        "contract_fingerprint": _text(contract.get("contract_fingerprint")),
        "attempt_count": 0,
        "coverage_complete": False,
        "observation_window_completed": False,
        "converged": False,
        "timed_out": False,
    }
    receipt["receipt_id"] = "event_wait_" + _fingerprint(receipt)[:24]
    return receipt


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
    event_id_reuse_conflicts = 0
    idempotency_mismatches = 0
    retry_limit_violations = 0
    poll_replay_count = 0
    invalid_collection_count = 0
    successful_polls = 0
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
        correlated = [
            row
            for row in rows
            if str(row.get("correlation")) == str(correlation)
            and _text(row.get("event_type")) == _text(spec.get("expected_event_type"))
        ]
        matching_rows_seen += len(correlated)
        newly_unique = 0
        for row in correlated:
            raw_id = str(row.get("event_id"))
            payload_fp = _text(row.get("payload_fingerprint"))
            if raw_id in events_by_id:
                poll_replay_count += 1
                if event_id_payloads.get(raw_id) != payload_fp:
                    event_id_reuse_conflicts += 1
                continue
            events_by_id[raw_id] = row
            event_id_payloads[raw_id] = payload_fp
            newly_unique += 1
            if idempotency_key and str(row.get("idempotency_value")) != str(
                expected_idempotency
            ):
                idempotency_mismatches += 1
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
                "matching_row_count": len(correlated),
                "new_unique_event_count": newly_unique,
                "unique_event_count": len(events_by_id),
            }
        )
        if attempt_number < max_attempts:
            sleep(int(policy.get("poll_interval_ms") or 0) / 1000.0)

    unique_count = len(events_by_id)
    minimum = int(spec.get("expected_min_count") or 0)
    maximum = int(spec.get("expected_max_count") or 0)
    coverage_complete = bool(
        len(attempts) == max_attempts
        and successful_polls == len(attempts)
        and invalid_collection_count == 0
    )
    semantic_status = "PASS"
    reason_code = ""
    if not coverage_complete:
        semantic_status = "INDETERMINATE"
        reason_code = EVENT_OBSERVATION_INCOMPLETE
    elif event_id_reuse_conflicts:
        semantic_status = "VIOLATION"
        reason_code = EVENT_ID_REUSE_CONFLICT
    elif idempotency_mismatches:
        semantic_status = "VIOLATION"
        reason_code = EVENT_IDEMPOTENCY_KEY_MISMATCH
    elif retry_limit_violations:
        semantic_status = "VIOLATION"
        reason_code = EVENT_RETRY_LIMIT_EXCEEDED
    elif unique_count < minimum:
        semantic_status = "VIOLATION"
        reason_code = EVENT_DELIVERY_COUNT_BELOW_MINIMUM
    elif unique_count > maximum:
        semantic_status = "VIOLATION"
        reason_code = EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM

    # A measured violation is a completed observation and must reach the Oracle.
    # Only unresolved/incomplete evidence blocks the target transport.
    observation_complete = semantic_status in {"PASS", "VIOLATION"}
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": STATUS_CONVERGED if observation_complete else STATUS_BLOCKED,
        "semantic_status": semantic_status,
        "reason_code": reason_code,
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
        "observed_matching_row_count": matching_rows_seen,
        "observed_unique_event_count": unique_count,
        "poll_replay_count": poll_replay_count,
        "distinct_delivery_overflow_count": max(0, unique_count - maximum),
        "event_id_reuse_conflict_count": event_id_reuse_conflicts,
        "idempotency_mismatch_count": idempotency_mismatches,
        "retry_limit_violation_count": retry_limit_violations,
        "successful_poll_count": successful_polls,
        "attempt_count": len(attempts),
        "coverage_complete": coverage_complete,
        "observation_window_completed": len(attempts) == max_attempts,
        "correlation_fingerprint": _fingerprint(correlation),
        "idempotency_key_fingerprint": (
            _fingerprint(expected_idempotency) if idempotency_key else ""
        ),
        "event_id_fingerprints": sorted(
            _fingerprint(value) for value in events_by_id
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
    "EVENT_RETRY_LIMIT_EXCEEDED",
    "has_event_transition",
    "compile_event_transition_contract",
    "execute_event_transition",
]
