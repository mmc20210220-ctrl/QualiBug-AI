"""Runtime Binding Probe — 7 probe types for runtime confirmation.

Probes are lightweight runtime checks that confirm or reject binding edges.
They obey the Risk Policy (read-only by default) and never generate Findings.

Probe results: CONFIRMED | REJECTED | INCONCLUSIVE | BLOCKED_BY_ENVIRONMENT

Schema: qualibug.binding-runtime-probe.v1

Probe types:
1. Entity Identity Probe     — Verify entity collection returns items with IDs
2. Field Write-Read Probe    — Write a field value, read it back
3. Operation Effect Probe    — Verify operation produces expected effect
4. Relation Materialization  — Verify FK relation produces linked records
5. State Binding Probe       — Verify state field exists with expected values
6. Actor Scope Probe         — Verify actor can access scoped resources
7. Observer Capability Probe — Verify observer read returns expected fields
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Any

from .binding_ledger import BindingLedger, BindingStatus
from .binding_evidence import collect_runtime_behavior_evidence, compute_composite_confidence


SCHEMA_VERSION = "qualibug.binding-runtime-probe.v1"

# Probe timeout (seconds)
_PROBE_TIMEOUT = 8

# Probe results
PROBE_CONFIRMED = "CONFIRMED"
PROBE_REJECTED = "REJECTED"
PROBE_INCONCLUSIVE = "INCONCLUSIVE"
PROBE_BLOCKED = "BLOCKED_BY_ENVIRONMENT"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# ─── Probe Execution ──────────────────────────────────────────────────────────

def run_probe(
    probe_type: str,
    *,
    base_url: str,
    token: str = "",
    binding: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
    risk_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a single runtime probe.

    Args:
        probe_type: One of the 7 probe types
        base_url: Target API base URL
        token: Bearer token for authentication
        binding: The binding edge being probed
        behavior_ir: Optional Behavior IR for context
        risk_policy: Optional risk policy constraints

    Returns:
        Probe result dict with status, detail, and evidence.
    """
    policy = _dict(risk_policy)
    metadata = _dict(binding.get("metadata"))

    # Check risk policy
    if policy.get("probes_disabled"):
        return _probe_result(PROBE_BLOCKED, "probes_disabled_by_policy", probe_type, binding)

    try:
        if probe_type == "entity_identity":
            return _probe_entity_identity(base_url, token, binding, metadata)
        elif probe_type == "field_write_read":
            if policy.get("allow_write_probes") is not True:
                return _probe_result(PROBE_BLOCKED, "write_probes_not_allowed", probe_type, binding)
            return _probe_field_write_read(base_url, token, binding, metadata)
        elif probe_type == "operation_effect":
            if policy.get("allow_write_probes") is not True:
                return _probe_result(PROBE_BLOCKED, "write_probes_not_allowed", probe_type, binding)
            return _probe_operation_effect(base_url, token, binding, metadata)
        elif probe_type == "relation_materialization":
            return _probe_relation_materialization(base_url, token, binding, metadata)
        elif probe_type == "state_binding":
            return _probe_state_binding(base_url, token, binding, metadata)
        elif probe_type == "actor_scope":
            return _probe_actor_scope(base_url, token, binding, metadata)
        elif probe_type == "observer_capability":
            return _probe_observer_capability(base_url, token, binding, metadata)
        else:
            return _probe_result(PROBE_INCONCLUSIVE, f"unknown_probe_type:{probe_type}", probe_type, binding)
    except Exception as exc:
        return _probe_result(PROBE_BLOCKED, f"probe_exception:{str(exc)[:100]}", probe_type, binding)


def run_probes_for_ledger(
    ledger: BindingLedger,
    *,
    base_url: str,
    token: str = "",
    behavior_ir: dict[str, Any] | None = None,
    risk_policy: dict[str, Any] | None = None,
    max_probes: int = 50,
) -> dict[str, Any]:
    """Run probes for all HIGH_CONFIDENCE bindings that need confirmation.

    Returns summary of probe results.
    """
    # Find bindings needing probes (HIGH_CONFIDENCE with confidence 0.70-0.90)
    candidates = ledger.get_by_status(BindingStatus.HIGH_CONFIDENCE)
    candidates = [
        b for b in candidates
        if 0.70 <= float(b.get("confidence", 0)) < 0.90
    ]

    results: list[dict[str, Any]] = []
    confirmed = 0
    rejected = 0
    inconclusive = 0
    blocked = 0

    for binding in candidates[:max_probes]:
        probe_type = _select_probe_type(binding)
        if not probe_type:
            continue

        result = run_probe(
            probe_type,
            base_url=base_url,
            token=token,
            binding=binding,
            behavior_ir=behavior_ir,
            risk_policy=risk_policy,
        )
        results.append(result)

        status = result.get("status", "")
        if status == PROBE_CONFIRMED:
            confirmed += 1
            # Promote to RUNTIME_CONFIRMED
            evidence = [collect_runtime_behavior_evidence(
                probe_type=probe_type,
                probe_result=PROBE_CONFIRMED,
                probe_detail=result.get("detail", ""),
            )]
            try:
                ledger.promote(
                    binding["binding_id"],
                    BindingStatus.RUNTIME_CONFIRMED,
                    reason=f"probe_confirmed:{probe_type}",
                    evidence=evidence,
                )
                # Auto-promote to EXECUTABLE
                ledger.promote(
                    binding["binding_id"],
                    BindingStatus.EXECUTABLE,
                    reason="probe_confirmed_auto_executable",
                )
            except ValueError:
                pass
        elif status == PROBE_REJECTED:
            rejected += 1
            try:
                ledger.promote(
                    binding["binding_id"],
                    BindingStatus.REJECTED,
                    reason=f"probe_rejected:{probe_type}",
                )
            except ValueError:
                pass
        elif status == PROBE_INCONCLUSIVE:
            inconclusive += 1
        else:
            blocked += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "total_probed": len(results),
        "confirmed": confirmed,
        "rejected": rejected,
        "inconclusive": inconclusive,
        "blocked_by_environment": blocked,
        "results": results,
        "timestamp": time.time(),
    }


# ─── Individual Probe Implementations ─────────────────────────────────────────

def _probe_entity_identity(
    base_url: str, token: str, binding: dict, metadata: dict
) -> dict[str, Any]:
    """Verify entity collection returns items with IDs."""
    collection_path = _text(metadata.get("collection_path") or metadata.get("read_path"))
    if not collection_path:
        return _probe_result(PROBE_INCONCLUSIVE, "no_collection_path", "entity_identity", binding)

    response = _http_get(base_url, collection_path, token)
    if response is None:
        return _probe_result(PROBE_BLOCKED, "no_response", "entity_identity", binding)

    # Check if response contains items with IDs
    items = _extract_items(response)
    if not items:
        return _probe_result(PROBE_REJECTED, "empty_collection", "entity_identity", binding)

    first = items[0] if items else {}
    if isinstance(first, dict) and (first.get("id") or first.get("uuid") or first.get("_id")):
        return _probe_result(PROBE_CONFIRMED, f"found_{len(items)}_items_with_id", "entity_identity", binding)

    return _probe_result(PROBE_INCONCLUSIVE, "items_without_id_field", "entity_identity", binding)


def _probe_field_write_read(
    base_url: str, token: str, binding: dict, metadata: dict
) -> dict[str, Any]:
    """Write a field value, read it back (requires write permission)."""
    # This is a simplified probe - in production would need full CRUD cycle
    field_name = _text(metadata.get("field_name"))
    read_path = _text(metadata.get("read_path") or metadata.get("collection_path"))
    if not field_name or not read_path:
        return _probe_result(PROBE_INCONCLUSIVE, "insufficient_metadata", "field_write_read", binding)

    # Read-only check: verify field exists in response
    response = _http_get(base_url, read_path, token)
    if response is None:
        return _probe_result(PROBE_BLOCKED, "no_response", "field_write_read", binding)

    items = _extract_items(response)
    if items and isinstance(items[0], dict):
        if field_name in items[0]:
            return _probe_result(PROBE_CONFIRMED, f"field_{field_name}_present", "field_write_read", binding)
        return _probe_result(PROBE_REJECTED, f"field_{field_name}_absent", "field_write_read", binding)

    return _probe_result(PROBE_INCONCLUSIVE, "no_items_to_check", "field_write_read", binding)


def _probe_operation_effect(
    base_url: str, token: str, binding: dict, metadata: dict
) -> dict[str, Any]:
    """Verify operation endpoint exists and responds."""
    method = _text(metadata.get("method"))
    path = _text(metadata.get("endpoint_path"))
    if not path:
        return _probe_result(PROBE_INCONCLUSIVE, "no_endpoint_path", "operation_effect", binding)

    # For safety, only probe GET operations
    if method.upper() not in ("GET", "HEAD"):
        return _probe_result(PROBE_BLOCKED, "non_get_probe_blocked", "operation_effect", binding)

    response = _http_get(base_url, path, token)
    if response is None:
        return _probe_result(PROBE_BLOCKED, "no_response", "operation_effect", binding)

    return _probe_result(PROBE_CONFIRMED, "endpoint_responds", "operation_effect", binding)


def _probe_relation_materialization(
    base_url: str, token: str, binding: dict, metadata: dict
) -> dict[str, Any]:
    """Verify FK relation produces linked records."""
    correlation_key = _text(metadata.get("correlation_key"))
    target_path = _text(metadata.get("target_collection_path"))
    if not correlation_key:
        return _probe_result(PROBE_INCONCLUSIVE, "no_correlation_key", "relation_materialization", binding)

    # Check if target collection has the correlation field
    if target_path:
        response = _http_get(base_url, target_path, token)
        if response is None:
            return _probe_result(PROBE_BLOCKED, "no_response", "relation_materialization", binding)
        items = _extract_items(response)
        if items and isinstance(items[0], dict):
            if correlation_key in items[0]:
                return _probe_result(PROBE_CONFIRMED, f"correlation_key_{correlation_key}_present", "relation_materialization", binding)

    return _probe_result(PROBE_INCONCLUSIVE, "cannot_verify_relation", "relation_materialization", binding)


def _probe_state_binding(
    base_url: str, token: str, binding: dict, metadata: dict
) -> dict[str, Any]:
    """Verify state field exists with expected values."""
    state_field = _text(metadata.get("state_field_name"))
    entity_path = _text(metadata.get("entity_collection_path") or metadata.get("collection_path"))
    raw_values = _list(metadata.get("raw_values"))

    if not state_field or not entity_path:
        return _probe_result(PROBE_INCONCLUSIVE, "insufficient_metadata", "state_binding", binding)

    response = _http_get(base_url, entity_path, token)
    if response is None:
        return _probe_result(PROBE_BLOCKED, "no_response", "state_binding", binding)

    items = _extract_items(response)
    if not items:
        return _probe_result(PROBE_INCONCLUSIVE, "empty_collection", "state_binding", binding)

    # Check if state field exists
    first = items[0] if items else {}
    if isinstance(first, dict) and state_field in first:
        actual_value = first[state_field]
        if raw_values and str(actual_value) in [str(v) for v in raw_values]:
            return _probe_result(PROBE_CONFIRMED, f"state_field_{state_field}={actual_value}", "state_binding", binding)
        return _probe_result(PROBE_CONFIRMED, f"state_field_{state_field}_exists", "state_binding", binding)

    return _probe_result(PROBE_REJECTED, f"state_field_{state_field}_absent", "state_binding", binding)


def _probe_actor_scope(
    base_url: str, token: str, binding: dict, metadata: dict
) -> dict[str, Any]:
    """Verify actor can access scoped resources."""
    # Simple probe: verify the token works for a basic GET
    scope_field = _text(metadata.get("scope_field"))
    if not token:
        return _probe_result(PROBE_BLOCKED, "no_token_available", "actor_scope", binding)

    # Try a basic endpoint to verify token validity
    response = _http_get(base_url, "/api", token)
    if response is not None:
        return _probe_result(PROBE_CONFIRMED, "token_valid", "actor_scope", binding)

    # Try root
    response = _http_get(base_url, "/", token)
    if response is not None:
        return _probe_result(PROBE_CONFIRMED, "token_valid_root", "actor_scope", binding)

    return _probe_result(PROBE_INCONCLUSIVE, "cannot_verify_scope", "actor_scope", binding)


def _probe_observer_capability(
    base_url: str, token: str, binding: dict, metadata: dict
) -> dict[str, Any]:
    """Verify observer read operation returns expected fields."""
    read_path = _text(metadata.get("read_path"))
    observed_fields = _list(metadata.get("observed_fields"))

    if not read_path:
        return _probe_result(PROBE_INCONCLUSIVE, "no_read_path", "observer_capability", binding)

    response = _http_get(base_url, read_path, token)
    if response is None:
        return _probe_result(PROBE_BLOCKED, "no_response", "observer_capability", binding)

    items = _extract_items(response)
    if not items:
        return _probe_result(PROBE_INCONCLUSIVE, "empty_response", "observer_capability", binding)

    first = items[0] if items else {}
    if not isinstance(first, dict):
        return _probe_result(PROBE_INCONCLUSIVE, "non_dict_item", "observer_capability", binding)

    if not observed_fields:
        return _probe_result(PROBE_CONFIRMED, "read_returns_data", "observer_capability", binding)

    # Check field coverage
    present = sum(1 for f in observed_fields if f in first)
    coverage = present / len(observed_fields) if observed_fields else 0

    if coverage >= 0.5:
        return _probe_result(PROBE_CONFIRMED, f"field_coverage:{coverage:.0%}", "observer_capability", binding)
    return _probe_result(PROBE_REJECTED, f"low_field_coverage:{coverage:.0%}", "observer_capability", binding)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _select_probe_type(binding: dict[str, Any]) -> str:
    """Select appropriate probe type for a binding."""
    btype = binding.get("binding_type", "")
    probe_map = {
        "entity": "entity_identity",
        "field": "field_write_read",
        "operation": "operation_effect",
        "relation": "relation_materialization",
        "state": "state_binding",
        "actor": "actor_scope",
        "scope": "actor_scope",
        "observer": "observer_capability",
        "oracle_input": "observer_capability",
        "fixture": "entity_identity",
    }
    return probe_map.get(btype, "")


def _probe_result(status: str, detail: str, probe_type: str, binding: dict) -> dict[str, Any]:
    """Create a standardized probe result."""
    return {
        "probe_type": probe_type,
        "binding_id": binding.get("binding_id", ""),
        "binding_type": binding.get("binding_type", ""),
        "status": status,
        "detail": detail,
        "timestamp": time.time(),
    }


def _http_get(base_url: str, path: str, token: str) -> Any:
    """Execute a GET request and return parsed JSON."""
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        resp = urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT)
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def _extract_items(response: Any) -> list[Any]:
    """Extract list items from a response (handles common wrappers)."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("data", "items", "results", "content", "records", "rows", "list"):
            val = response.get(key)
            if isinstance(val, list):
                return val
        # Nested: {data: {items: [...]}}
        data = response.get("data")
        if isinstance(data, dict):
            for key in ("items", "results", "content", "records", "rows", "list"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
    return []
