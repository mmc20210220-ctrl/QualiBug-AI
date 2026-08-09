"""Admit explicit message-chain contracts and runtime event surfaces.

A message chain is the cross-service topology the single-hop event observer
cannot express: an event produced by one operation is consumed downstream and
must advance a target entity's state (payment callback -> order status PAID ->
inventory notice). This overlay is an admission layer only -- it never creates
obligations, executes the target or produces findings. It normalizes two
material-driven rows:

* ``message_chain_contracts``: source-bound chain contracts (event name,
  trigger source, consumers and expected effects) -- every missing leg becomes
  a visible gap, mirroring ``scan_event_contract_overlay``.
* ``runtime_event_surfaces``: operator-declared runtime observation surfaces
  with NO business semantics (no source refs, no expected event type, no
  count/state claims unless the operator declares them). This is the
  degradation channel: when no written event contract exists, the surface
  still makes the event face observable at runtime (AGENTS.md: runtime
  observation is a first-class evidence source; degradation is receipted,
  never silent).

Both rows flow through the same strict channel as the existing event overlay:
explicit external-signal rows typed as chain contracts / runtime surfaces are
admitted alongside first-class scan-context fields, and anything else stays a
visible coverage gap.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
from typing import Any

CHAIN_SCHEMA = "qualibug.formal-message-chain-contract.v1"
RUNTIME_SURFACE_SCHEMA = "qualibug.runtime-event-surface.v1"
OVERLAY_SCHEMA = "qualibug.message-chain-contract-overlay.v1"
CHAIN_SIGNAL_TYPES = frozenset({"message_chain_contract", "event_chain_contract"})
RUNTIME_SIGNAL_TYPES = frozenset({
    "runtime_event_surface",
    "runtime_event_observation",
})

MESSAGE_CHAIN_CONTRACT_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("qualibug_message_chain_contract_context", default=None)
)

_MAX_WINDOW_MS = 30_000
_MAX_EVENTS = 200
_MAX_CONSUMERS = 8

_CHAIN_OBSERVER_FIELDS = (
    "observer_path",
    "events_path",
    "event_id_field",
    "event_type_field",
    "correlation_field",
    "correlation_query_parameter",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stable_id(prefix: str, *parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def bind_message_chain_contract_context(
    context: dict[str, Any] | None,
) -> contextvars.Token:
    return MESSAGE_CHAIN_CONTRACT_CONTEXT.set(dict(context or {}))


def reset_message_chain_contract_context(token: contextvars.Token) -> None:
    MESSAGE_CHAIN_CONTRACT_CONTEXT.reset(token)


def current_message_chain_contract_context() -> dict[str, Any]:
    return dict(MESSAGE_CHAIN_CONTRACT_CONTEXT.get() or {})


def _source_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        copy.deepcopy(item)
        for item in _list(row.get("source_refs"))
        if isinstance(item, dict) and _text(item.get("source_id"))
    ]
    if refs:
        return refs
    source_id = _text(row.get("source_id"))
    if not source_id:
        return []
    return [{
        "source_id": source_id,
        "version": _text(row.get("source_version")),
        "locator": _text(row.get("source_locator")),
        "kind": "formal_message_chain_contract",
        "quote_hash": _text(row.get("quote_hash")),
    }]


def _valid_relative_path(path: str) -> bool:
    value = _text(path)
    return bool(
        value.startswith("/")
        and not value.startswith("//")
        and "://" not in value
        and "#" not in value
        and ".." not in value.split("?")[0].split("/")
    )


def _gap(
    contract_id: str,
    reason_code: str,
    *,
    refs: list[dict[str, Any]] | None = None,
    gap_type: str = "message_chain_contract_not_source_bound",
) -> dict[str, Any]:
    return {
        "gap_type": gap_type,
        "reason_code": reason_code,
        "contract_id": contract_id,
        "source_id": _text(_dict((refs or [{}])[0]).get("source_id")) if refs else "",
        "description": "Message-chain contract could not enter the formal chain",
        "status": "unsupported",
    }


def _normalize_observer_fields(
    row: dict[str, Any],
    contract_id: str,
    *,
    refs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    gaps: list[dict[str, Any]] = []
    missing = [
        field
        for field in _CHAIN_OBSERVER_FIELDS
        if not _text(row.get(field))
    ]
    if missing:
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_FIELDS_MISSING:" + ",".join(missing),
            refs=refs,
        )]
    observer_path = _text(row.get("observer_path"))
    if not _valid_relative_path(observer_path):
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_OBSERVER_PATH_INVALID",
            refs=refs,
        )]
    return observer_path, gaps


def _normalize_trigger_identity(
    row: dict[str, Any],
    contract_id: str,
    *,
    refs: list[dict[str, Any]],
) -> tuple[tuple[str, str] | None, list[dict[str, Any]]]:
    """Return (operation_ref or method+operation_path, actor_ref or actor_role)."""
    operation_ref = _text(row.get("operation_ref") or row.get("operation_id"))
    method = _text(row.get("method") or row.get("http_method")).upper()
    operation_path = _text(
        row.get("operation_path") or row.get("api_path") or row.get("endpoint")
    )
    actor_ref = _text(row.get("actor_ref") or row.get("actor_id"))
    actor_role = _text(row.get("actor_role") or row.get("role"))
    if not operation_ref and not (method and operation_path):
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_OPERATION_IDENTITY_MISSING",
            refs=refs,
        )]
    if not actor_ref and not actor_role:
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_ACTOR_IDENTITY_MISSING",
            refs=refs,
        )]
    return (operation_ref, actor_ref), []


def _normalize_correlation(
    row: dict[str, Any],
    contract_id: str,
    *,
    refs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    correlation_source = _dict(row.get("correlation_source"))
    if row.get("correlation_value") is None and not (
        _text(correlation_source.get("location"))
        and _text(correlation_source.get("path"))
    ):
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_CORRELATION_SOURCE_MISSING",
            refs=refs,
        )]
    return correlation_source, []


def _normalize_delivery_window(
    row: dict[str, Any],
    contract_id: str,
    *,
    refs: list[dict[str, Any]],
) -> tuple[tuple[int, int | None, int] | None, list[dict[str, Any]]]:
    minimum = _int(row.get("expected_min_count"), -1)
    maximum = _int(row.get("expected_max_count"), -1)
    window_ms = _int(row.get("observation_window_ms"), 0)
    gaps: list[dict[str, Any]] = []
    if minimum < 0 or window_ms <= 0:
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_COUNT_OR_WINDOW_INVALID",
            refs=refs,
        )]
    if maximum < 0:
        maximum = None
    if maximum is not None and (maximum < minimum or maximum > _MAX_EVENTS):
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_COUNT_RANGE_INVALID",
            refs=refs,
        )]
    if window_ms > _MAX_WINDOW_MS:
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_WINDOW_INVALID",
            refs=refs,
        )]
    return (minimum, maximum, window_ms), []


def _normalize_consumers(
    row: dict[str, Any],
    contract_id: str,
    *,
    refs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gaps: list[dict[str, Any]] = []
    consumers: list[dict[str, Any]] = []
    raw = [item for item in _list(row.get("consumers")) if isinstance(item, dict)]
    for consumer in raw[:_MAX_CONSUMERS]:
        effect = _dict(consumer.get("effect"))
        readback = _dict(effect.get("readback"))
        if not readback:
            gaps.append(_gap(
                contract_id,
                "FORMAL_CHAIN_EFFECT_READBACK_MISSING",
                refs=refs,
            ))
            continue
        readback_path = _text(readback.get("path"))
        query_parameter = _text(readback.get("query_parameter"))
        state_field = _text(readback.get("state_field"))
        expected_state = _text(readback.get("expected_state"))
        if not state_field or not expected_state:
            gaps.append(_gap(
                contract_id,
                "FORMAL_CHAIN_EFFECT_READBACK_FIELDS_MISSING",
                refs=refs,
            ))
            continue
        if not readback_path and not query_parameter:
            gaps.append(_gap(
                contract_id,
                "FORMAL_CHAIN_EFFECT_READBACK_TARGET_MISSING",
                refs=refs,
            ))
            continue
        if readback_path and not _valid_relative_path(readback_path):
            gaps.append(_gap(
                contract_id,
                "FORMAL_CHAIN_EFFECT_READBACK_PATH_INVALID",
                refs=refs,
            ))
            continue
        poll_until = _int(readback.get("poll_until_ms"), 0)
        if poll_until <= 0 or poll_until > _MAX_WINDOW_MS:
            gaps.append(_gap(
                contract_id,
                "FORMAL_CHAIN_EFFECT_READBACK_WINDOW_INVALID",
                refs=refs,
            ))
            continue
        consumers.append({
            "consumer_ref": _text(consumer.get("consumer_ref")),
            "surface": _text(effect.get("surface")) or "http_state",
            "readback": {
                "path": readback_path,
                "query_parameter": query_parameter,
                "state_field": state_field,
                "expected_state": expected_state,
                "previous_state": _text(readback.get("previous_state")),
                "actor_ref": _text(readback.get("actor_ref")),
                "poll_until_ms": poll_until,
                "poll_interval_ms": max(
                    100,
                    min(5_000, _int(readback.get("poll_interval_ms"), 500)),
                ),
            },
        })
    return consumers, gaps


def _normalize_ordering(
    row: dict[str, Any],
    contract_id: str,
    *,
    refs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    ordering = _dict(row.get("ordering"))
    if not ordering:
        return None, []
    sequence_field = _text(ordering.get("sequence_field"))
    timestamp_field = _text(ordering.get("timestamp_field"))
    expected_types = [
        _text(value)
        for value in _list(ordering.get("expected_types"))
        if _text(value)
    ]
    if not (sequence_field or timestamp_field or expected_types):
        return None, [_gap(
            contract_id,
            "FORMAL_CHAIN_ORDERING_SPEC_MISSING",
            refs=refs,
        )]
    return {
        "sequence_field": sequence_field,
        "timestamp_field": timestamp_field,
        "expected_types": expected_types,
    }, []


def normalize_message_chain_contract(
    raw: dict[str, Any],
    *,
    index: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Normalize one source-bound message-chain contract, or return its gaps."""
    row = copy.deepcopy(_dict(raw))
    contract_id = _text(row.get("contract_id") or row.get("id")) or _stable_id(
        "message_chain", index, row
    )
    refs = _source_refs(row)
    if not refs:
        return None, [_gap(contract_id, "FORMAL_CHAIN_SOURCE_REF_MISSING")]

    trigger, gaps = _normalize_trigger_identity(
        row, contract_id, refs=refs
    )
    if trigger is None:
        return None, gaps
    observer_path, observer_gaps = _normalize_observer_fields(
        row, contract_id, refs=refs
    )
    if observer_path is None:
        return None, gaps + observer_gaps
    correlation, correlation_gaps = _normalize_correlation(
        row, contract_id, refs=refs
    )
    if correlation is None:
        return None, gaps + observer_gaps + correlation_gaps
    window, window_gaps = _normalize_delivery_window(
        row, contract_id, refs=refs
    )
    if window is None:
        return None, gaps + observer_gaps + correlation_gaps + window_gaps
    consumers, consumer_gaps = _normalize_consumers(
        row, contract_id, refs=refs
    )
    ordering, ordering_gaps = _normalize_ordering(row, contract_id, refs=refs)
    all_gaps = (
        gaps + observer_gaps + correlation_gaps + window_gaps + ordering_gaps
    )
    if all_gaps:
        return None, all_gaps
    if not _text(row.get("event_name")):
        return None, all_gaps + [_gap(
            contract_id,
            "FORMAL_CHAIN_EVENT_NAME_MISSING",
            refs=refs,
        )]
    if consumer_gaps:
        return None, all_gaps + consumer_gaps
    duplicate_mode = _text(row.get("duplicate_mode")) or "log"
    if duplicate_mode not in {"log", "queue"}:
        return None, all_gaps + [_gap(
            contract_id,
            "FORMAL_CHAIN_DUPLICATE_MODE_INVALID",
            refs=refs,
        )]

    operation_ref, actor_ref = trigger
    minimum, maximum, window_ms = window
    normalized = {
        **row,
        "schema_version": CHAIN_SCHEMA,
        "contract_id": contract_id,
        "source_refs": refs,
        "source_id": _text(refs[0].get("source_id")),
        "source_locator": _text(refs[0].get("locator")),
        "event_name": _text(row.get("event_name")),
        "observer_path": observer_path,
        "expected_min_count": minimum,
        "expected_max_count": maximum,
        "observation_window_ms": window_ms,
        "poll_interval_ms": max(100, min(5_000, _int(row.get("poll_interval_ms"), 500))),
        "consumers": consumers,
        "ordering": ordering,
        "status": "accepted",
        "derivation": "explicit",
        "channel": "source_contract",
        "confidence": 1.0,
    }
    normalized["duplicate_mode"] = duplicate_mode
    if operation_ref:
        normalized["operation_ref"] = operation_ref
    else:
        normalized["method"] = _text(row.get("method") or row.get("http_method")).upper()
        normalized["operation_path"] = _text(
            row.get("operation_path") or row.get("api_path") or row.get("endpoint")
        )
    if actor_ref:
        normalized["actor_ref"] = actor_ref
    else:
        normalized["actor_role"] = _text(row.get("actor_role") or row.get("role"))
    return normalized, []


def normalize_runtime_event_surface(
    raw: dict[str, Any],
    *,
    index: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Normalize one operator-declared runtime event surface (degradation channel).

    No source refs are required: the surface is a runtime declaration, not a
    source claim. Business semantics stay absent unless the operator declares
    them (consumers/readback effects, ordering fields); the receipt always
    marks the channel as runtime observation.
    """
    row = copy.deepcopy(_dict(raw))
    surface_id = _text(row.get("surface_id") or row.get("id")) or _stable_id(
        "runtime_event_surface", index, row
    )
    refs: list[dict[str, Any]] = []

    trigger, gaps = _normalize_trigger_identity(
        row, surface_id, refs=refs
    )
    if trigger is None:
        return None, gaps
    observer_path, observer_gaps = _normalize_observer_fields(
        row, surface_id, refs=refs
    )
    if observer_path is None:
        return None, gaps + observer_gaps
    correlation, correlation_gaps = _normalize_correlation(
        row, surface_id, refs=refs
    )
    if correlation is None:
        return None, gaps + observer_gaps + correlation_gaps
    window, window_gaps = _normalize_delivery_window(
        row, surface_id, refs=refs
    )
    if window is None:
        return None, gaps + observer_gaps + correlation_gaps + window_gaps
    consumers, consumer_gaps = _normalize_consumers(
        row, surface_id, refs=refs
    )
    if consumer_gaps:
        return None, (
            gaps + observer_gaps + correlation_gaps + window_gaps + consumer_gaps
        )
    ordering, ordering_gaps = _normalize_ordering(row, surface_id, refs=refs)
    if ordering_gaps:
        return None, (
            gaps + observer_gaps + correlation_gaps + window_gaps + ordering_gaps
        )
    duplicate_mode = _text(row.get("duplicate_mode")) or "log"
    if duplicate_mode not in {"log", "queue"}:
        return None, (
            gaps + observer_gaps + correlation_gaps + window_gaps + [
                _gap(surface_id, "FORMAL_CHAIN_DUPLICATE_MODE_INVALID", refs=refs)
            ]
        )

    operation_ref, actor_ref = trigger
    minimum, maximum, window_ms = window
    normalized = {
        **row,
        "schema_version": RUNTIME_SURFACE_SCHEMA,
        "contract_id": surface_id,
        "surface_id": surface_id,
        "source_refs": refs,
        "event_name": _text(row.get("event_name")) or "",
        "observer_path": observer_path,
        "expected_min_count": minimum,
        "expected_max_count": maximum,
        "observation_window_ms": window_ms,
        "poll_interval_ms": max(100, min(5_000, _int(row.get("poll_interval_ms"), 500))),
        "consumers": consumers,
        "ordering": ordering,
        "duplicate_mode": duplicate_mode,
        "status": "accepted",
        "derivation": "runtime-observed",
        "channel": "runtime_observation",
        "confidence": 0.6,
    }
    if operation_ref:
        normalized["operation_ref"] = operation_ref
    else:
        normalized["method"] = _text(row.get("method") or row.get("http_method")).upper()
        normalized["operation_path"] = _text(
            row.get("operation_path") or row.get("api_path") or row.get("endpoint")
        )
    if actor_ref:
        normalized["actor_ref"] = actor_ref
    else:
        normalized["actor_role"] = _text(row.get("actor_role") or row.get("role"))
    return normalized, []


def _is_typed_row(
    row: dict[str, Any],
    schema_prefix: str,
    signal_types: frozenset[str],
) -> bool:
    schema = _text(row.get("schema_version"))
    explicit_type = _text(
        row.get("signal_type")
        or row.get("contract_type")
        or row.get("kind")
        or row.get("type")
    ).lower()
    return bool(
        schema.startswith(schema_prefix)
        or explicit_type in signal_types
    )


def overlay_message_chain_contracts_with_external_signals(
    asset: dict[str, Any] | None,
    campaign_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Admit chain contracts and runtime surfaces into the knowledge asset."""
    merged = copy.deepcopy(_dict(asset))
    context = _dict(campaign_context) or current_message_chain_contract_context()
    external = [
        copy.deepcopy(row)
        for row in _list(context.get("external_signal_requests"))
        if isinstance(row, dict)
    ]
    raw_chains = [
        copy.deepcopy(row)
        for row in _list(context.get("message_chain_contracts"))
        if isinstance(row, dict)
    ]
    raw_chains.extend(
        row
        for row in external
        if _is_typed_row(row, CHAIN_SCHEMA, CHAIN_SIGNAL_TYPES)
    )
    raw_surfaces = [
        copy.deepcopy(row)
        for row in _list(context.get("runtime_event_surfaces"))
        if isinstance(row, dict)
    ]
    raw_surfaces.extend(
        row
        for row in external
        if _is_typed_row(row, RUNTIME_SURFACE_SCHEMA, RUNTIME_SIGNAL_TYPES)
    )

    existing_chains = {
        _text(row.get("contract_id")): copy.deepcopy(row)
        for row in _list(merged.get("message_chain_contracts"))
        if isinstance(row, dict) and _text(row.get("contract_id"))
    }
    existing_surfaces = {
        _text(row.get("contract_id")): copy.deepcopy(row)
        for row in _list(merged.get("runtime_event_surfaces"))
        if isinstance(row, dict) and _text(row.get("contract_id"))
    }
    gaps: list[dict[str, Any]] = []
    chain_admitted = 0
    surface_admitted = 0
    for index, raw in enumerate(raw_chains, start=1):
        contract, row_gaps = normalize_message_chain_contract(raw, index=index)
        gaps.extend(row_gaps)
        if contract is None:
            continue
        contract_id = _text(contract.get("contract_id"))
        if contract_id in existing_chains:
            gaps.append(_gap(
                contract_id,
                "FORMAL_CHAIN_CONTRACT_ID_DUPLICATE",
                refs=_source_refs(contract),
            ))
            continue
        existing_chains[contract_id] = contract
        chain_admitted += 1
    for index, raw in enumerate(raw_surfaces, start=1):
        surface, row_gaps = normalize_runtime_event_surface(raw, index=index)
        gaps.extend(row_gaps)
        if surface is None:
            continue
        surface_id = _text(surface.get("contract_id"))
        if surface_id in existing_surfaces:
            gaps.append(_gap(
                surface_id,
                "RUNTIME_EVENT_SURFACE_ID_DUPLICATE",
                refs=[],
                gap_type="runtime_event_surface_not_admitted",
            ))
            continue
        existing_surfaces[surface_id] = surface
        surface_admitted += 1

    merged["message_chain_contracts"] = list(existing_chains.values())
    merged["runtime_event_surfaces"] = list(existing_surfaces.values())
    merged["coverage_gaps"] = [
        *[
            copy.deepcopy(row)
            for row in _list(merged.get("coverage_gaps"))
            if isinstance(row, dict)
        ],
        *gaps,
    ]
    receipt = {
        "schema_version": OVERLAY_SCHEMA,
        "status": "OVERLAID"
        if (chain_admitted or surface_admitted)
        else "BLOCKED"
        if (raw_chains or raw_surfaces)
        else "NOT_REQUESTED",
        "message_chain_contract_count": len(raw_chains),
        "message_chain_admitted_count": chain_admitted,
        "runtime_event_surface_count": len(raw_surfaces),
        "runtime_event_surface_admitted_count": surface_admitted,
        "external_signal_request_count": len(external),
        "coverage_gap_count": len(gaps),
        "raw_event_payloads_consumed": False,
    }
    merged["message_chain_contract_overlay_receipt"] = receipt
    return merged, receipt


__all__ = [
    "CHAIN_SCHEMA",
    "OVERLAY_SCHEMA",
    "RUNTIME_SURFACE_SCHEMA",
    "bind_message_chain_contract_context",
    "current_message_chain_contract_context",
    "normalize_message_chain_contract",
    "normalize_runtime_event_surface",
    "overlay_message_chain_contracts_with_external_signals",
    "reset_message_chain_contract_context",
]
