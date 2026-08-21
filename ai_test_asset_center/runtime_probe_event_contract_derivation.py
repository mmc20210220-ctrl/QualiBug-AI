"""档位 D — runtime-probe event-contract derivation.

Lets ``event_delivery_consistency`` become reachable on an unfamiliar system
that *exposes* (but does not declare) an event / audit / outbox listing
surface, without the customer hand-writing the event contract.

Root cause this closes
----------------------
``formal_event_surface`` (the event-delivery observer) requires a formal event
contract carrying concrete observation coordinates (``events_path``,
``event_id_field``, ``event_type_field``, ``correlation_field``,
``expected_event_type``, ``expected_min/max_count``, ``observation_window_ms``).
档位 C (``contract_auto_derivation``) only populates ``event_formal_contracts``
from *source text* (PRD / API spec regex).  For a fully unfamiliar system with
no PRD / API spec those coordinates are absent, so ``event_delivery_consistency``
is structurally unreachable even though the system may actually expose an event
log the tester could observe.

This module derives the *same* ``event_formal_contracts`` rows 档位 C produces,
but learns the coordinates from the governed runtime-interface probe's own
observed response schema instead of source text:

* field names (id / type / time shapes) are read from the probe's captured
  response schema — never hardcoded business paths or terms;
* the type taxonomy (``observed_event_types``) is the system's own event-type
  codes, captured bounded (schema-level observation, not payload data);
* ``expected_min/max_count`` and ``observation_window_ms`` are methodology
  defaults (relative), never an absolute SLA;
* when no event-shaped listing surface is observed, the producer reports
  ``NO_CONTRACTS_DERIVED`` and the caller marks a coverage gap — no fabricated
  contract, no invented business semantics (原则6 / 原则12).

The rows flow through the existing ``bind_source_event_contracts`` binder →
``formal_event_surface`` compiles an ``event_delivery_consistency`` protocol,
exactly like 档位 C's source-derived rows.  No new wheel: the row builder is
``contract_auto_derivation._event_row``.

Detection discipline
--------------------
A read-only GET that returns a JSON *listing* (array of objects) whose field
shapes include an id-like, a type-like, and a time-like field is treated as an
event/audit surface.  This is purely structural; no industry vocabulary is
matched.  Single-object responses and auth-gated / missing endpoints are NOT
treated as event surfaces.
"""

from __future__ import annotations

import copy
from typing import Any

from .contract_auto_derivation import _event_row

DERIVATION_SCHEMA = "qualibug.runtime-probe-event-contract-derivation.v1"

# Methodology defaults (relative bounds, product-owned — never business SLAs).
_EVENT_EXPECTED_MIN_COUNT = 1
_EVENT_EXPECTED_MAX_COUNT = 50          # formal_event_surface caps at _MAX_EVENTS=200
_EVENT_OBSERVATION_WINDOW_MS = 10_000   # formal_event_surface caps at _MAX_WINDOW_MS=30000


def _text(value: Any) -> str:
    return str(value or "").strip()


def _field_roles(fields: list[str]) -> tuple[str | None, str | None, str | None]:
    """Structural classification of response field names into event roles.

    No business vocabulary: only universal id / type / time shapes are matched.
    """
    id_field = next((f for f in fields if f == "id" or f.endswith("_id")), None)
    type_field = next(
        (f for f in fields if f in ("type", "kind", "event_type")
         or f.endswith("_type") or f.endswith("_event")),
        None,
    )
    time_field = next(
        (f for f in fields if f.endswith("_at") or f.endswith("_time")
         or "timestamp" in f or f in ("created", "updated")),
        None,
    )
    return id_field, type_field, time_field


def _looks_like_event_surface(observed: dict[str, Any]) -> bool:
    if not observed.get("is_listing_response"):
        return False
    fields = observed.get("observed_fields") or []
    if not fields:
        return False
    id_field, type_field, time_field = _field_roles(fields)
    # Structural: a listing with id + type + time resembles an event / audit log.
    return bool(id_field and type_field and time_field)


def _actor_role_for_probe(runtime_actors: list[dict[str, Any]] | None) -> str:
    for actor in runtime_actors or []:
        role = _text(actor.get("role") or actor.get("role_key"))
        if role:
            return role.casefold()
    return "anonymous"


def event_observations_from_receipts(
    observation_receipts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Convert runtime-interface observation receipts into event probe observations.

    Filters to read-only 2xx listings whose captured schema is event-shaped.
    """
    out: list[dict[str, Any]] = []
    for receipt in observation_receipts or []:
        if not isinstance(receipt, dict):
            continue
        op = receipt.get("operation") or {}
        method = _text(op.get("method") or receipt.get("method")).upper()
        if method != "GET":
            continue
        status = int(receipt.get("status_code") or 0)
        if status < 200 or status >= 300:
            continue
        if not receipt.get("is_listing_response"):
            continue
        fields = receipt.get("observed_fields")
        if not isinstance(fields, list) or not fields:
            continue
        out.append({
            "method": "GET",
            "path": _text(op.get("path") or receipt.get("path")),
            "status_code": status,
            "observed_fields": fields,
            "observed_event_types": receipt.get("observed_event_types") or [],
            "is_listing_response": True,
        })
    return out


def derive_runtime_probe_event_contracts(
    asset: dict[str, Any] | None,
    *,
    operations: list[dict[str, Any]] | None = None,
    runtime_observations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive ``event_formal_contracts`` from governed runtime probe observations.

    Returns ``(asset, receipt)``.  The asset is returned unchanged when the pass
    is disabled or finds no event-shaped surface; the receipt always records what
    was attempted, derived, and skipped (with reason codes).
    """
    merged = dict(asset) if isinstance(asset, dict) else {}
    receipt: dict[str, Any] = {
        "schema_version": DERIVATION_SCHEMA,
        "enabled": True if enabled is None else bool(enabled),
        "derived": {"event": 0},
        "skipped": [],
        "methodology_defaults": {
            "event": {
                "expected_min_count": _EVENT_EXPECTED_MIN_COUNT,
                "expected_max_count": _EVENT_EXPECTED_MAX_COUNT,
                "observation_window_ms": _EVENT_OBSERVATION_WINDOW_MS,
                "note": "relative methodology bounds; event schema learned from "
                        "observed response, never from source text or hardcoded terms",
            },
        },
    }
    if enabled is False:
        receipt["status"] = "DISABLED"
        return merged, receipt
    if not runtime_observations:
        receipt["status"] = "NOT_REQUESTED"
        receipt["reason"] = "no_runtime_probe_observations"
        return merged, receipt

    ops_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        ops_by_key[(_text(op.get("method")).upper(), _text(op.get("path")))] = op

    actor_role = _actor_role_for_probe(runtime_actors)
    accepted: list[dict[str, Any]] = []
    existing_keys = {
        (_text(r.get("method")).upper(), _text(r.get("operation_path") or r.get("path")))
        for r in merged.get("event_formal_contracts", []) or []
        if isinstance(r, dict)
    }
    seen: set[tuple[str, str]] = set()

    for obs in runtime_observations:
        if not _looks_like_event_surface(obs):
            continue
        path = _text(obs.get("path"))
        key = ("GET", path)
        if key in existing_keys or key in seen:
            receipt["skipped"].append({
                "kind": "event", "reason": "already_covered", "operation": path,
            })
            continue
        fields = obs["observed_fields"]
        id_field, type_field, time_field = _field_roles(fields)
        # correlation falls back to a secondary id-like / ref field, then the id.
        correlation_field = next(
            (f for f in fields if (f.endswith("_id") and f != id_field)
             or f in ("ref", "correlation_id", "trace_id", "request_id")),
            id_field,
        )
        observed_types = obs.get("observed_event_types") or []
        if not observed_types:
            receipt["skipped"].append({
                "kind": "event",
                "reason": "event_type_not_observed",
                "operation": path,
            })
            continue
        operation = ops_by_key.get(key) or {
            "method": "GET", "path": path, "operation_id": path,
        }
        # correlation_query_parameter: the observer correlates event delivery by
        # querying the listing.  Using the OBSERVED correlation field name as the
        # query parameter is the universal REST convention (param name == field
        # name) and is runtime-observed, never a hardcoded business term.  If the
        # system does not honor that param the observation fails at execution
        # (honest INDETERMINATE), never as a false finding.
        parsed = {
            "quote": "",  # runtime-observed, not source-quoted
            "observer_path": path,
            "events_path": path,
            "event_id_field": id_field,
            "event_type_field": type_field,
            "correlation_field": correlation_field,
            "correlation_query_parameter": correlation_field,
            "expected_event_type": observed_types[0],
            "correlation_source": {"location": "treatment_response", "path": id_field},
            "expected_min_count": _EVENT_EXPECTED_MIN_COUNT,
            "expected_max_count": _EVENT_EXPECTED_MAX_COUNT,
            "observation_window_ms": _EVENT_OBSERVATION_WINDOW_MS,
        }
        row = _event_row(operation, parsed, source_id="runtime_probe", actor_role=actor_role)
        accepted.append(row)
        seen.add(key)

    if accepted:
        merged["event_formal_contracts"] = [
            *(merged.get("event_formal_contracts", []) or []),
            *accepted,
        ]
        receipt["derived"]["event"] = len(accepted)
        receipt["status"] = "CONSUMED"
    else:
        receipt["status"] = "NO_CONTRACTS_DERIVED"
    return merged, receipt
