"""Message-chain delivery consistency: the four-link runtime surface.

Single-hop event observation answers "did the trigger emit the declared
event?". Real enterprise message chains are longer: the emitted event is
consumed by another service which must advance a target entity's state
(payment callback -> order status PAID -> inventory notice). This surface adds
the chain semantics on top of the existing formal event chain:

1. Risk family: ``event_delivery_consistency`` (existing) gains a second
   protocol template ``message_chain_verification`` -- one family, two
   executable protocols.
2. Assertion kind ``message_chain_consistency`` states the chain properties:
   no loss (delivery count >= declared minimum), no duplicate delivery (the
   same event id delivered more than once), ordering consistency (sequence /
   timestamp monotonicity and declared type order), and the chain effect (the
   correlated target entity reaches the declared state after the event).
3. Observer ``message_chain_delivery_observer`` measures the evidence: it
   polls the declared relative event GET path WITHOUT collapsing duplicates
   (the single-hop observer dedupes by event id, which hides duplicate
   deliveries), detects ordering violations, and reads the declared target
   state back over a relative GET for every consumer effect.
4. Protocol ``event_delivery_consistency:message_chain_verification``
   executes the trigger operation and routes to the observer.

Degradation channel: when no written chain contract exists, an
operator-declared ``runtime_event_surface`` (admitted by
``message_chain_contract_overlay``) makes the event face observable at
runtime. The obligation is compiled with ``channel=runtime_observation`` and
asserts only the properties the operator declared -- never invented business
rules (AGENTS.md: runtime observation is a first-class evidence source;
degradation is receipted, never silent).

Privacy: raw event payloads never enter evidence; event ids are
fingerprinted; only declared identity/type/state fields are kept.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .formal_event_surface import (
    ADAPTER,
    SURFACE,
    _correlation_value,
    _extract_path,
    _fingerprint,
    _observer_token,
    _scalar,
    _text,
    _validate_observer_path,
)
from .observer_contracts_base import _dict, _list

OBSERVER_ID = "message_chain_delivery_observer"
EVIDENCE_KEY = "message_chain_observation"
ASSERTION_KIND = "message_chain_consistency"
RISK_FAMILY = "event_delivery_consistency"
PROTOCOL_TEMPLATE = "message_chain_verification"
CHAIN_SCHEMA = "qualibug.formal-message-chain-contract.v1"

_MAX_WINDOW_MS = 30_000
_MAX_EVENTS = 200
_MAX_EFFECT_READBACK_ATTEMPTS = 12


def _chain_contract(spec: dict[str, Any]) -> dict[str, Any]:
    row = _dict(spec)
    contract = copy.deepcopy(_dict(row.get("message_chain")))
    if contract:
        return contract
    return copy.deepcopy(_dict(row.get("event_contract")))


def _delivery_window(contract: dict[str, Any]) -> tuple[int, int | None]:
    try:
        minimum = int(contract.get("expected_min_count") or 0)
    except (TypeError, ValueError):
        minimum = 0
    maximum = contract.get("expected_max_count")
    if maximum is None or maximum == "":
        return minimum, None
    try:
        return minimum, int(maximum)
    except (TypeError, ValueError):
        return minimum, None


def _compile_message_chain_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    contract = _chain_contract(property_spec)
    operation = _dict(envelope.get("operation"))
    operation_ref = _text(envelope.get("operation_ref"))
    actor_ref = _text(
        envelope.get("treatment_actor_ref")
        or envelope.get("control_actor_ref")
        or property_spec.get("actor_ref")
    )
    if not contract:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "message_chain_contract_missing",
        }
    if not operation_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "message_chain_trigger_operation_missing",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "message_chain_trigger_actor_missing",
        }
    if not _validate_observer_path(_text(contract.get("observer_path"))):
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OBSERVER",
            "detail": "message_chain_observer_relative_path_invalid",
        }
    method = _text(operation.get("method")).upper()
    trigger_body = copy.deepcopy(contract.get("trigger_body"))
    if trigger_body is None:
        trigger_body = copy.deepcopy(operation.get("request_example"))
    if method in {"POST", "PUT", "PATCH"} and not isinstance(trigger_body, dict):
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "message_chain_trigger_requires_source_request_example",
        }

    step: dict[str, Any] = {
        "step_id": "treatment_1",
        "actor_ref": actor_ref,
        "operation_ref": operation_ref,
        "intent": "trigger_source_declared_event",
        "protocol_step": "event_trigger",
    }
    if isinstance(trigger_body, dict):
        step["body"] = trigger_body
    assertion_property = copy.deepcopy(property_spec)
    assertion_property["message_chain"] = contract
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [step],
        "observers": [{"observer_id": OBSERVER_ID}],
        "assertion": {
            "kind": ASSERTION_KIND,
            "property": assertion_property,
            "invariant_ref": _text(property_spec.get("invariant_ref")),
            "rule_id": _text(property_spec.get("invariant_ref") or property_spec.get("rule_id")),
        },
    }


def _chain_event_rows(
    body: Any,
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Extract every delivery row, preserving duplicates and order."""
    raw_events = _extract_path(body, _text(contract.get("events_path")))
    if not isinstance(raw_events, list):
        return [], "EVENT_CHAIN_COLLECTION_NOT_LIST"
    rows: list[dict[str, Any]] = []
    for raw in raw_events[:_MAX_EVENTS]:
        if not isinstance(raw, dict):
            continue
        event_id = _extract_path(raw, _text(contract.get("event_id_field")))
        event_type = _extract_path(raw, _text(contract.get("event_type_field")))
        correlation = _extract_path(raw, _text(contract.get("correlation_field")))
        sequence = (
            _extract_path(raw, _text(contract.get("sequence_field")))
            if _text(contract.get("sequence_field"))
            else None
        )
        timestamp = (
            _extract_path(raw, _text(contract.get("timestamp_field")))
            if _text(contract.get("timestamp_field"))
            else None
        )
        if not (_scalar(event_id) and _scalar(event_type) and _scalar(correlation)):
            continue
        rows.append({
            "event_id": event_id,
            "event_type": event_type,
            "correlation": correlation,
            "sequence": sequence,
            "timestamp": timestamp,
            "timestamp_present": timestamp not in (None, ""),
        })
    if len(raw_events) > _MAX_EVENTS:
        return rows, "EVENT_CHAIN_COLLECTION_TRUNCATED"
    return rows, ""

def _poll_chain_events(
    *,
    base_url: str,
    path: str,
    query_parameter: str,
    correlation_value: Any,
    actor_token: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Poll the declared event path keeping every delivery (no dedupe)."""
    from .sandbox_write_executor import _http_request

    window_ms = min(_MAX_WINDOW_MS, int(contract.get("observation_window_ms") or 0))
    interval_ms = min(5_000, max(100, int(contract.get("poll_interval_ms") or 500)))
    deadline = time.monotonic() + (window_ms / 1000.0)
    deliveries: list[dict[str, Any]] = []
    poll_count = 0
    successful_polls = 0
    status_codes: list[int] = []
    errors: list[str] = []
    truncated = False
    while True:
        poll_count += 1
        separator = "&" if "?" in path else "?"
        url = (
            base_url.rstrip("/")
            + path
            + separator
            + urlencode({query_parameter: str(correlation_value)})
        )
        response = _http_request(
            "GET",
            url,
            token=actor_token,
            timeout=min(5.0, max(0.5, deadline - time.monotonic())),
            max_retries=0,
        )
        status = int(response.get("status") or 0)
        status_codes.append(status)
        if 200 <= status < 300:
            successful_polls += 1
            rows, row_error = _chain_event_rows(response.get("body"), contract)
            if row_error == "EVENT_CHAIN_COLLECTION_TRUNCATED":
                truncated = True
            elif row_error:
                errors.append(row_error)
            for row in rows:
                row["poll_index"] = poll_count
            deliveries.extend(rows)
        else:
            errors.append(_text(response.get("error")) or f"HTTP_{status or 0}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval_ms / 1000.0, remaining))
    return {
        "deliveries": deliveries,
        "poll_count": poll_count,
        "successful_polls": successful_polls,
        "status_codes": status_codes,
        "errors": errors[:10],
        "truncated": truncated,
        "observation_window_completed": True,
    }


def _ordering_violation(
    correlated: list[dict[str, Any]],
    ordering: dict[str, Any],
) -> tuple[str, list[str]]:
    """Return (violation code, observed type sequence) for declared ordering."""
    expected_types = [_text(value) for value in _list(ordering.get("expected_types"))]
    sequence_field = _text(ordering.get("sequence_field"))
    timestamp_field = _text(ordering.get("timestamp_field"))
    observed_types = [_text(row.get("event_type")) for row in correlated]
    if expected_types:
        if observed_types != expected_types:
            return "type_order_mismatch", observed_types
    if sequence_field:
        previous: Any = None
        for row in correlated:
            value = row.get("sequence")
            if not _scalar(value):
                continue
            if previous is not None:
                if value == previous:
                    return "sequence_duplicate", observed_types
                try:
                    decreasing = float(value) < float(previous)
                except (TypeError, ValueError):
                    decreasing = str(value) < str(previous)
                if decreasing:
                    return "sequence_regression", observed_types
            previous = value
    elif timestamp_field:
        previous_ts: Any = None
        for row in correlated:
            value = row.get("timestamp")
            if not _scalar(value):
                continue
            if previous_ts is not None:
                try:
                    decreasing = float(value) < float(previous_ts)
                except (TypeError, ValueError):
                    decreasing = str(value) < str(previous_ts)
                if decreasing:
                    return "timestamp_regression", observed_types
            previous_ts = value
    return "", observed_types


def _consumer_readback(consumer: dict[str, Any]) -> dict[str, Any]:
    """The normalized flat ``readback`` or the raw nested ``effect.readback``."""
    readback = _dict(consumer.get("readback"))
    if readback:
        return readback
    return _dict(_dict(consumer.get("effect")).get("readback"))


def _readback_effect(
    *,
    base_url: str,
    consumer: dict[str, Any],
    correlation_value: Any,
    actor_token: str,
) -> dict[str, Any]:
    """Poll one declared consumer effect readback until the window closes."""
    from .sandbox_write_executor import _http_request

    readback = _consumer_readback(consumer)
    path = _text(readback.get("path"))
    query_parameter = _text(readback.get("query_parameter"))
    correlation_field = _text(readback.get("correlation_field"))
    poll_until_ms = min(_MAX_WINDOW_MS, int(readback.get("poll_until_ms") or 0))
    interval_ms = min(5_000, max(100, int(readback.get("poll_interval_ms") or 500)))
    deadline = time.monotonic() + (poll_until_ms / 1000.0)
    attempts = 0
    errors: list[str] = []
    state_value: Any = None
    while attempts < _MAX_EFFECT_READBACK_ATTEMPTS:
        attempts += 1
        target = path
        if "{correlation}" in target:
            target = target.replace("{correlation}", str(correlation_value))
        elif correlation_field and "{" + correlation_field + "}" in target:
            target = target.replace("{" + correlation_field + "}", str(correlation_value))
        separator = "&" if "?" in target else "?"
        if query_parameter:
            target = target + separator + urlencode({query_parameter: str(correlation_value)})
        response = _http_request(
            "GET",
            target if target.startswith("http") else base_url.rstrip("/") + target,
            token=actor_token,
            timeout=min(5.0, max(0.5, deadline - time.monotonic())),
            max_retries=0,
        )
        status = int(response.get("status") or 0)
        if 200 <= status < 300:
            state_value = _extract_path(response.get("body"), _text(readback.get("state_field")))
            break
        errors.append(_text(response.get("error")) or f"HTTP_{status or 0}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval_ms / 1000.0, remaining))
    expected_state = _text(readback.get("expected_state"))
    previous_state = _text(readback.get("previous_state"))
    observed = _text(state_value)
    if state_value is None and errors:
        state_status = "unobserved"
    elif observed == expected_state:
        state_status = "reached"
    elif previous_state and observed == previous_state:
        state_status = "not_reached"
    elif previous_state:
        state_status = "unexpected"
    else:
        state_status = "not_reached"
    return {
        "consumer_ref": _text(consumer.get("consumer_ref")),
        "state_field": _text(readback.get("state_field")),
        "expected_state": expected_state,
        "previous_state": previous_state,
        "observed_state": observed,
        "state_status": state_status,
        "readback_attempts": attempts,
        "readback_errors": errors[:5],
    }


def _duplicate_analysis(
    correlated: list[dict[str, Any]],
    duplicate_mode: str,
) -> tuple[list[str], int]:
    """Return (duplicate event ids, duplicate delivery units).

    A log stream lists the same event on every poll, so within-batch
    duplicates (one id appearing twice in one poll response) are the
    duplicate-delivery signal; ``duplicate_mode="queue"`` declares consume-on-
    read semantics where an id reappearing in a later poll is a redelivery.
    """
    per_poll: dict[int, dict[str, int]] = {}
    polls_per_id: dict[str, set[int]] = {}
    for row in correlated:
        event_id = _text(row.get("event_id"))
        if not event_id:
            continue
        poll_index = int(row.get("poll_index") or 0)
        bucket = per_poll.setdefault(poll_index, {})
        bucket[event_id] = bucket.get(event_id, 0) + 1
        polls_per_id.setdefault(event_id, set()).add(poll_index)
    if duplicate_mode == "queue":
        duplicate_ids = sorted(
            event_id
            for event_id, polls in polls_per_id.items()
            if len(polls) >= 2
        )
        return duplicate_ids, len(duplicate_ids)
    duplicate_ids = sorted({
        event_id
        for bucket in per_poll.values()
        for event_id, count in bucket.items()
        if count > 1
    })
    duplicate_units = sum(
        max(0, count - 1)
        for bucket in per_poll.values()
        for count in bucket.values()
    )
    return duplicate_ids, duplicate_units


def _unique_correlated(
    correlated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """First-seen unique rows by event id, preserving delivery order."""
    unique: dict[str, dict[str, Any]] = {}
    for row in correlated:
        event_id = _text(row.get("event_id"))
        if not event_id:
            continue
        unique.setdefault(event_id, row)
    return list(unique.values())


def _message_chain_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    exp = _dict(envelope.get("experiment"))
    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    contract = _chain_contract(spec)
    observations = _dict(envelope.get("observations"))
    context = _dict(exp.get("_observer_runtime_context"))
    root_value = _text(context.get("root"))
    project = _text(context.get("project"))
    runtime_contract = _dict(context.get("runtime_contract"))

    def indeterminate(reason_code: str, **detail: Any) -> dict[str, Any]:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence={"detail": detail},
        )

    if not contract:
        return indeterminate("EVENT_CHAIN_CONTRACT_NOT_DECLARED")
    if not (root_value and project and runtime_contract):
        return indeterminate("EVENT_CHAIN_RUNTIME_CONTEXT_MISSING")
    if _text(runtime_contract.get("status")) != "approved":
        return indeterminate("EVENT_CHAIN_RUNTIME_CONTRACT_NOT_APPROVED")
    if ADAPTER not in {
        _text(value) for value in _list(runtime_contract.get("declared_adapters"))
    }:
        return indeterminate("EVENT_CHAIN_OBSERVER_ADAPTER_NOT_DECLARED")
    observer_path = _text(contract.get("observer_path"))
    if not _validate_observer_path(observer_path):
        return indeterminate("EVENT_CHAIN_OBSERVER_PATH_INVALID")
    correlation = _correlation_value(contract, observations)
    if not _scalar(correlation):
        return indeterminate("EVENT_CHAIN_CORRELATION_VALUE_NOT_PROVEN")

    base_url = _text(runtime_contract.get("approved_base_url"))
    actor_ref = _text(spec.get("actor_ref") or contract.get("actor_ref"))
    token = _observer_token(
        root=Path(root_value),
        project=project,
        base_url=base_url,
        actor_ref=actor_ref,
    )
    if contract.get("observer_requires_actor_token") is True and not token:
        return indeterminate("EVENT_CHAIN_OBSERVER_ACTOR_TOKEN_MISSING")

    polled = _poll_chain_events(
        base_url=base_url,
        path=observer_path,
        query_parameter=_text(contract.get("correlation_query_parameter")),
        correlation_value=correlation,
        actor_token=token,
        contract=contract,
    )
    deliveries = [
        row for row in _list(polled.get("deliveries")) if isinstance(row, dict)
    ]
    correlated = [
        row for row in deliveries if str(row.get("correlation")) == str(correlation)
    ]
    unique_correlated = _unique_correlated(correlated)
    event_name = _text(contract.get("event_name"))
    expected_rows = [
        row
        for row in unique_correlated
        if not event_name or _text(row.get("event_type")) == event_name
    ]
    duplicate_mode = _text(contract.get("duplicate_mode")) or "log"
    duplicate_ids, duplicate_units = _duplicate_analysis(
        correlated,
        duplicate_mode,
    )

    ordering_violation = ""
    observed_type_sequence: list[str] = []
    if _dict(contract.get("ordering")):
        ordering_violation, observed_type_sequence = _ordering_violation(
            unique_correlated,
            _dict(contract.get("ordering")),
        )
    else:
        observed_type_sequence = [
            _text(row.get("event_type")) for row in unique_correlated
        ]

    effects: list[dict[str, Any]] = []
    effect_coverage_complete = True
    for consumer in _list(contract.get("consumers")):
        if not isinstance(consumer, dict):
            continue
        readback_actor = _text(_consumer_readback(consumer).get("actor_ref"))
        effect_token = _observer_token(
            root=Path(root_value),
            project=project,
            base_url=base_url,
            actor_ref=readback_actor or actor_ref,
        )
        effect = _readback_effect(
            base_url=base_url,
            consumer=consumer,
            correlation_value=correlation,
            actor_token=effect_token,
        )
        effects.append(effect)
        if effect.get("state_status") == "unobserved":
            effect_coverage_complete = False

    minimum, maximum = _delivery_window(contract)
    coverage_complete = (
        polled.get("observation_window_completed") is True
        and int(polled.get("successful_polls") or 0) > 0
        and polled.get("truncated") is not True
        and not _list(polled.get("errors"))
        and effect_coverage_complete
    )
    evidence = {
        EVIDENCE_KEY: {
            "contract_id": _text(contract.get("contract_id")),
            "channel": _text(contract.get("channel")) or "source_contract",
            "event_name": event_name,
            "expected_min_count": minimum,
            "expected_max_count": maximum,
            "delivery_count": len(expected_rows),
            "correlated_delivery_count": len(unique_correlated),
            "duplicate_mode": duplicate_mode,
            "duplicate_event_id_count": duplicate_units,
            "duplicate_event_id_fingerprints": [
                _fingerprint(value) for value in duplicate_ids
            ],
            "ordering": {
                "sequence_field": _text(_dict(contract.get("ordering")).get("sequence_field")),
                "timestamp_field": _text(_dict(contract.get("ordering")).get("timestamp_field")),
                "expected_types": [
                    _text(value)
                    for value in _list(_dict(contract.get("ordering")).get("expected_types"))
                ],
                "violation": ordering_violation,
                "observed_type_sequence": observed_type_sequence,
            },
            "effects": effects,
            "observed_event_types": sorted({
                _text(row.get("event_type")) for row in unique_correlated
            }),
            "event_id_fingerprints": sorted(
                _fingerprint(row.get("event_id")) for row in unique_correlated
            ),
            "timestamp_present_count": sum(
                1 for row in unique_correlated if row.get("timestamp_present") is True
            ),
            "correlation_fingerprint": _fingerprint(correlation),
            "poll_count": int(polled.get("poll_count") or 0),
            "successful_poll_count": int(polled.get("successful_polls") or 0),
            "status_codes": [int(value or 0) for value in _list(polled.get("status_codes"))],
            "observer_errors": [_text(value) for value in _list(polled.get("errors")) if _text(value)],
            "observation_window_completed": polled.get("observation_window_completed") is True,
            "coverage_complete": coverage_complete,
            "raw_event_payloads_included": False,
        }
    }
    if int(polled.get("successful_polls") or 0) <= 0:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="EVENT_CHAIN_OBSERVER_NOT_REACHED",
            evidence=evidence,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_message_chain(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    expected = {
        "event_name": _text(observation.get("event_name")),
        "min_count": int(observation.get("expected_min_count") or 0),
        "max_count": observation.get("expected_max_count"),
        "duplicate_delivery_allowed": False,
        "ordering_required": bool(
            _dict(observation.get("ordering")).get("sequence_field")
            or _dict(observation.get("ordering")).get("timestamp_field")
            or _dict(observation.get("ordering")).get("expected_types")
        ),
        "effects": list(_list(observation.get("effects"))),
        "full_observation_window_required": True,
    }
    actual = {
        "delivery_count": int(observation.get("delivery_count") or 0),
        "correlated_delivery_count": int(observation.get("correlated_delivery_count") or 0),
        "duplicate_event_id_count": int(observation.get("duplicate_event_id_count") or 0),
        "ordering_violation": _text(_dict(observation.get("ordering")).get("violation")),
        "observed_type_sequence": _list(
            _dict(observation.get("ordering")).get("observed_type_sequence")
        ),
        "effects": list(_list(observation.get("effects"))),
        "observation_window_completed": observation.get("observation_window_completed") is True,
        "coverage_complete": observation.get("coverage_complete") is True,
    }
    if not actual["observation_window_completed"] or not actual["coverage_complete"]:
        return {
            "passed": None,
            "reason_code": "EVENT_CHAIN_OBSERVATION_COVERAGE_INCOMPLETE",
            "expected": expected,
            "actual": actual,
        }
    reasons: list[str] = []
    maximum = observation.get("expected_max_count")
    if actual["duplicate_event_id_count"] > 0:
        reasons.append("EVENT_CHAIN_DUPLICATE_DELIVERY")
    if actual["delivery_count"] < expected["min_count"]:
        reasons.append("EVENT_CHAIN_DELIVERY_BELOW_MINIMUM")
    if maximum is not None and actual["delivery_count"] > int(maximum):
        reasons.append("EVENT_CHAIN_DELIVERY_ABOVE_MAXIMUM")
    if actual["ordering_violation"]:
        reasons.append("EVENT_CHAIN_ORDERING_VIOLATION")
    for effect in actual["effects"]:
        status = _text(effect.get("state_status"))
        if status == "not_reached":
            reasons.append("EVENT_CHAIN_EFFECT_NOT_REACHED")
        elif status == "unexpected":
            reasons.append("EVENT_CHAIN_STATE_UNEXPECTED")
    reasons = list(dict.fromkeys(reasons))
    return {
        "passed": not reasons,
        "reason_code": reasons[0] if reasons else "",
        "expected": expected,
        "actual": actual,
    }


def install_message_chain_surface() -> dict[str, str]:
    """Register chain observer, assertion kind and protocol idempotently."""
    from .adapter_capability import (
        ADAPTER_TO_CAPABILITY,
        ADAPTER_TO_OBSERVATION_SURFACE,
        DECLARATION_REQUIRED,
    )

    DECLARATION_REQUIRED.setdefault(ADAPTER, "runtime_contract.declared_adapters[]")
    ADAPTER_TO_OBSERVATION_SURFACE.setdefault(ADAPTER, SURFACE)
    ADAPTER_TO_CAPABILITY.setdefault(ADAPTER, "event_stream_read")

    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_message_chain_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_message_chain,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["assertion"] = ASSERTION_KIND

    from .experiment_protocol_registry import (
        register_family_protocol,
        registered_family_protocols,
    )

    protocol_id = f"{RISK_FAMILY}:{PROTOCOL_TEMPLATE}"
    if protocol_id not in set(registered_family_protocols()):
        register_family_protocol(
            RISK_FAMILY,
            PROTOCOL_TEMPLATE,
            compiler=_compile_message_chain_protocol,
            observers=(OBSERVER_ID,),
            assertion_kind=ASSERTION_KIND,
            emits_control=False,
            per_step_evidence=False,
        )
    installed["protocol"] = protocol_id
    return installed


_PRE_RECEIPT_KEY = "pre_cleanup_message_chain_observer_receipt"
_INSTALL_MARKER = "_qualibug_message_chain_pre_cleanup_observer_installed"
_BASE_HANDLER_MARKER = "_qualibug_base_message_chain_handler_before_pre_cleanup_registration"
_HOOK_NAME = "message_chain_pre_cleanup_observer"


def _requires_chain_observer(exp: dict[str, Any]) -> bool:
    return any(
        _text(_dict(row).get("observer_id")) == OBSERVER_ID
        for row in _list(_dict(exp).get("observers"))
        if isinstance(row, dict)
    )


def _chain_trigger_step_id(exp: dict[str, Any]) -> str:
    matches = [
        _text(row.get("step_id"))
        for row in _list(_dict(exp).get("treatment_plan"))
        if isinstance(row, dict)
        and _text(row.get("protocol_step")) == "event_trigger"
        and _text(row.get("step_id"))
    ]
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _chain_assertion(exp: dict[str, Any]) -> dict[str, Any]:
    matches = [
        dict(row)
        for row in _list(_dict(exp).get("assertions"))
        if isinstance(row, dict) and _text(row.get("kind")) == ASSERTION_KIND
    ]
    return matches[0] if len(matches) == 1 else {}


def _pre_observe_message_chain(
    *,
    exp: dict[str, Any],
    observations: dict[str, Any],
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any] | None:
    if not _requires_chain_observer(exp):
        return None
    existing = _dict(observations.get(_PRE_RECEIPT_KEY))
    if existing:
        return existing
    step_id = _chain_trigger_step_id(exp)
    assertion = _chain_assertion(exp)
    from .observer_contracts_base import build_observer_receipt

    if not step_id:
        receipt = build_observer_receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="EVENT_CHAIN_TRIGGER_STEP_IDENTITY_NOT_UNIQUE",
            evidence={"matching_event_trigger_step_count": 0},
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
    elif not assertion:
        receipt = build_observer_receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="EVENT_CHAIN_ASSERTION_IDENTITY_NOT_UNIQUE",
            evidence={"matching_assertion_count": 0, "step_id": step_id},
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
    else:
        from . import observer_contracts_base as observers

        try:
            handler = _registered_chain_handler()
            if not callable(handler):
                raise RuntimeError("message_chain_observer_handler_not_registered")
            receipt = handler({
                "observer_id": OBSERVER_ID,
                "experiment": exp,
                "observations": observations,
                "assertion": assertion,
                "property": _dict(assertion.get("property")),
                "control_observation": _dict(observations.get("control_observation")),
                "treatment_observation": _dict(observations.get("treatment_observation")),
                "execution_steps": _list(observations.get("execution_steps")),
                "campaign_id": campaign_id,
                "execution_id": execution_id,
            })
            validated = observers.validate_observer_receipt(_dict(receipt))
            evidence = copy.deepcopy(_dict(validated.get("evidence")))
            chain_evidence = _dict(evidence.get(EVIDENCE_KEY))
            if chain_evidence:
                chain_evidence["observation_phase"] = "pre_cleanup"
                evidence[EVIDENCE_KEY] = chain_evidence
            evidence["step_id"] = step_id
            receipt = observers.build_observer_receipt(
                observer_id=OBSERVER_ID,
                status=_text(validated.get("status")),
                reason_code=_text(validated.get("reason_code")),
                evidence=evidence,
                campaign_id=_text(validated.get("campaign_id")) or campaign_id,
                execution_id=_text(validated.get("execution_id")) or execution_id,
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must still proceed
            receipt = build_observer_receipt(
                observer_id=OBSERVER_ID,
                status="INDETERMINATE",
                reason_code="EVENT_CHAIN_PRE_CLEANUP_OBSERVER_FAILED",
                evidence={
                    "error_type": type(exc).__name__,
                    "step_id": step_id,
                },
                campaign_id=campaign_id,
                execution_id=execution_id,
            )
    receipt = copy.deepcopy(_dict(receipt))
    evidence = _dict(receipt.get("evidence"))
    observations[_PRE_RECEIPT_KEY] = copy.deepcopy(receipt)
    if _text(receipt.get("status")).upper() == "OBSERVED":
        for key, value in evidence.items():
            if key not in observations:
                observations[key] = copy.deepcopy(value)
    return receipt


def _registered_chain_handler() -> Any:
    """Resolve the current composed authority while preserving the test seam.

    Production wrappers are installed in the observer registry. The public
    surface handler normally remains the exact authority captured when
    pre-cleanup was installed. A test may explicitly replace that surface
    handler after installation; honor only that deliberate replacement,
    otherwise use the fully composed registry chain.
    """
    from . import observer_contracts_base as observers

    current_surface = _message_chain_observer_handler
    installed_base = globals().get(_BASE_HANDLER_MARKER, current_surface)
    if current_surface is not installed_base:
        return current_surface
    return observers._REGISTERED_OBSERVER_HANDLERS.get(OBSERVER_ID) or current_surface


def install_message_chain_pre_cleanup_observer() -> None:
    """Run the chain observer after the trigger and before cleanup."""
    from . import experiment_cleanup_executor as cleanup_executor
    from . import observer_contracts_base as observers

    if getattr(cleanup_executor, _INSTALL_MARKER, False):
        return
    base_handler = observers._REGISTERED_OBSERVER_HANDLERS.get(OBSERVER_ID)
    if not callable(base_handler):
        raise RuntimeError("message_chain_observer_not_registered_before_pre_cleanup")
    globals()[_BASE_HANDLER_MARKER] = base_handler

    def handler_reusing_pre_cleanup(envelope: dict[str, Any]) -> dict[str, Any]:
        observations = _dict(_dict(envelope).get("observations"))
        precomputed = _dict(observations.get(_PRE_RECEIPT_KEY))
        if precomputed:
            return copy.deepcopy(precomputed)
        base = globals().get(_BASE_HANDLER_MARKER, base_handler)
        return base(envelope)

    def pre_cleanup_hook(context: dict[str, Any]) -> None:
        _pre_observe_message_chain(
            exp=_dict(context.get("exp")),
            observations=_dict(context.get("observations")),
            campaign_id=_text(
                context.get("resolved_campaign_id") or context.get("campaign_id")
            ),
            execution_id=_text(context.get("resolved_execution_id")),
        )

    observers._REGISTERED_OBSERVER_HANDLERS[OBSERVER_ID] = handler_reusing_pre_cleanup
    cleanup_executor.register_cleanup_pre_hook(_HOOK_NAME, pre_cleanup_hook)
    setattr(cleanup_executor, _INSTALL_MARKER, True)


__all__ = [
    "ADAPTER",
    "ASSERTION_KIND",
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "PROTOCOL_TEMPLATE",
    "RISK_FAMILY",
    "SURFACE",
    "install_message_chain_pre_cleanup_observer",
    "install_message_chain_surface",
]
