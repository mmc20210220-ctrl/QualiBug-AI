"""Automatic observer injection for write experiments.

For write operations (POST/PUT/PATCH/DELETE), automatically creates
before/after state comparison observers by finding corresponding GET
read endpoints in the Behavior IR. Fully data-driven and industry-neutral.

Schema: qualibug.auto-observer-injector.v1
"""
from __future__ import annotations

import re
from typing import Any

_SCHEMA = "qualibug.auto-observer-injector.v1"
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_path(path: str) -> str:
    """Normalize a path for comparison (lowercase, strip trailing slash)."""
    return (path or "").lower().rstrip("/")


def _path_entity_prefix(path: str) -> str:
    """Extract the entity collection prefix from a path.

    E.g. /api/v1/orders/{orderId} -> /api/v1/orders
         /api/products/{id}/reviews -> /api/products
    """
    # Remove placeholder segments
    segments = [s for s in path.split("/") if s and not _PLACEHOLDER_RE.fullmatch(s)]
    # Return up to the last non-placeholder segment
    if not segments:
        return ""
    # Find the collection path (before any placeholder)
    result_segments = []
    for seg in path.split("/"):
        if not seg:
            continue
        if _PLACEHOLDER_RE.fullmatch(seg):
            break
        result_segments.append(seg)
    return "/" + "/".join(result_segments) if result_segments else ""


def find_read_endpoint_for_write(
    write_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any] | None:
    """Find a GET endpoint that can observe the effect of a write operation.

    Strategy:
    1. Same path prefix with GET method (e.g. POST /orders -> GET /orders)
    2. Entity-level GET (e.g. POST /orders/{id}/cancel -> GET /orders/{id})
    3. Collection-level GET (e.g. DELETE /orders/{id} -> GET /orders)
    """
    operations = _list(behavior_ir.get("operations"))
    write_path = _text(write_op.get("path") or write_op.get("raw_path"))
    write_method = _text(write_op.get("method")).upper()

    if not write_path or write_method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None

    write_prefix = _path_entity_prefix(write_path)
    if not write_prefix:
        return None

    # Find GET endpoints with matching prefix
    candidates: list[tuple[int, dict[str, Any]]] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in ("GET", "HEAD"):
            continue
        op_path = _text(op.get("path") or op.get("raw_path"))
        if not op_path:
            continue
        op_prefix = _path_entity_prefix(op_path)
        if not op_prefix:
            continue

        # Score by path similarity
        score = 0
        if _normalize_path(op_prefix) == _normalize_path(write_prefix):
            score = 100  # Exact prefix match
        elif _normalize_path(write_prefix).startswith(_normalize_path(op_prefix)):
            score = 50  # Write is under this collection
        elif _normalize_path(op_prefix).startswith(_normalize_path(write_prefix)):
            score = 30  # This collection is under write prefix

        if score > 0:
            candidates.append((score, op))

    if not candidates:
        return None

    # Return best match (highest score, prefer paths without placeholders)
    candidates.sort(key=lambda x: (-x[0], "{" in _text(x[1].get("path"))))
    return candidates[0][1]


def build_http_state_observer(
    write_op: dict[str, Any],
    read_op: dict[str, Any] | None,
    *,
    actor_ref: str = "",
) -> dict[str, Any]:
    """Build an http_state_comparison observer for a write operation.

    The observer performs:
    1. Before: GET read endpoint (if available)
    2. Execute: the write operation
    3. After: GET read endpoint (if available)
    4. Compare: before vs after state
    """
    observer: dict[str, Any] = {
        "observer_type": "http_state_comparison",
        "schema_version": _SCHEMA,
        "write_operation_ref": _text(write_op.get("id")),
        "write_method": _text(write_op.get("method")).upper(),
        "write_path": _text(write_op.get("path") or write_op.get("raw_path")),
        "actor_ref": actor_ref,
    }

    if read_op:
        observer["read_operation_ref"] = _text(read_op.get("id"))
        observer["read_path"] = _text(read_op.get("path") or read_op.get("raw_path"))
        observer["read_method"] = "GET"
        observer["observation_mode"] = "before_after_comparison"
    else:
        # Fallback: use write response as observation
        observer["observation_mode"] = "response_body_only"
        observer["read_operation_ref"] = ""
        observer["read_path"] = ""

    return observer


def inject_observers_for_experiment(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Inject auto-generated observers into an experiment if missing.

    Only injects when:
    - The experiment has write operations in treatment_plan
    - No observers are already declared
    - The experiment is not already compiled with observers

    Returns the modified experiment (may be same reference if no changes).
    """
    exp = _dict(experiment)
    treatment_plan = _list(exp.get("treatment_plan"))
    if not treatment_plan:
        return exp

    # Check if observers already exist
    existing_observers = _list(exp.get("observers"))
    if existing_observers:
        return exp

    # Check if any treatment step is a write operation
    ops_by_id = {
        _text(op.get("id")): op
        for op in _list(behavior_ir.get("operations"))
        if isinstance(op, dict) and _text(op.get("id"))
    }

    has_write = False
    write_ops: list[dict[str, Any]] = []
    for step in treatment_plan:
        if not isinstance(step, dict):
            continue
        op_ref = _text(step.get("operation_ref"))
        op = ops_by_id.get(op_ref, {})
        method = _text(step.get("method") or op.get("method")).upper()
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            has_write = True
            write_ops.append(op)

    if not has_write:
        return exp

    # Generate observers for write operations
    injected_observers: list[dict[str, Any]] = []
    actor_ref = ""
    for step in treatment_plan:
        if isinstance(step, dict) and _text(step.get("actor_ref")):
            actor_ref = _text(step.get("actor_ref"))
            break

    for write_op in write_ops[:3]:  # Limit to 3 observers per experiment
        read_op = find_read_endpoint_for_write(write_op, behavior_ir)
        observer = build_http_state_observer(write_op, read_op, actor_ref=actor_ref)
        injected_observers.append(observer)

    if injected_observers:
        exp = dict(exp)
        exp["observers"] = injected_observers
        exp["_auto_injected_observers"] = True
        exp["_observer_injection_schema"] = _SCHEMA

    return exp


def should_skip_observer_block(experiment: dict[str, Any]) -> bool:
    """Check if an experiment with auto-injected observers should skip the
    BLOCKED_MISSING_OBSERVER gate.

    Returns True if the experiment has auto-injected observers and the
    write operation is not a non-reversible safety concern.
    """
    exp = _dict(experiment)
    if not exp.get("_auto_injected_observers"):
        return False

    # Never skip for non-reversible writes
    for step in _list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        method = _text(step.get("method")).upper()
        path = _text(step.get("path") or "").lower()
        # DELETE is potentially non-reversible
        if method == "DELETE" and "cleanup" not in path and "cancel" not in path:
            return False

    return True
