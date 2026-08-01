"""Observer-backed wait contracts for source-declared process graphs.

This module reuses ``async_readback_executor`` for bounded polling. It owns no
scheduler and discovers nothing: graph compilation must provide an exact target
node, source node, declared GET/HEAD operation, predicate and async policy.
Runtime uses the target node's already-approved base URL and actor context.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Callable

from .async_readback_executor import (
    READBACK_ASYNC_POLICY_INVALID,
    execute_async_readback,
    normalize_async_policy,
)
from .experiment_runtime_support import _resolve_token, _run_http_step
from .real_id_resolver import path_has_placeholders
from .runtime_binding_materializer import materialize_path


SCHEMA_VERSION = "qualibug.process-graph-wait-contract.v1"
RECEIPT_SCHEMA_VERSION = "qualibug.process-graph-wait-receipt.v1"
STATUS_COMPILED = "COMPILED"
STATUS_BLOCKED = "BLOCKED"
STATUS_CONVERGED = "CONVERGED"
STATUS_NOT_REQUIRED = "NOT_REQUIRED"

WAIT_CONTRACT_INVALID = "PROCESS_GRAPH_WAIT_CONTRACT_INVALID"
WAIT_CONTRACT_AMBIGUOUS = "PROCESS_GRAPH_WAIT_CONTRACT_AMBIGUOUS"
WAIT_ASYNC_EDGE_UNCOVERED = "PROCESS_GRAPH_ASYNC_EDGE_WAIT_UNCOVERED"
WAIT_BINDING_UNRESOLVED = "PROCESS_GRAPH_WAIT_BINDING_UNRESOLVED"
WAIT_ACTOR_UNRESOLVED = "PROCESS_GRAPH_WAIT_ACTOR_UNRESOLVED"
WAIT_PREDICATE_NOT_SATISFIED = "PROCESS_GRAPH_WAIT_PREDICATE_NOT_SATISFIED"

_ASYNC_RELATIONS = frozenset(
    {"AWAITS", "NOTIFIES", "TRIGGERS", "MESSAGE", "ASYNC_MESSAGE"}
)
_READ_METHODS = frozenset({"GET", "HEAD"})
_PREDICATE_OPERATORS = frozenset(
    {"equals", "not_equals", "exists", "truthy", "in"}
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _node_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("node_id") or row.get("step_id")): dict(row)
        for row in _list(graph.get("nodes"))
        if isinstance(row, dict)
        and _text(row.get("node_id") or row.get("step_id"))
    }


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): dict(row)
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _predicate(raw: dict[str, Any]) -> tuple[dict[str, Any], str]:
    status_codes = [
        int(value)
        for value in _list(raw.get("status_codes") or raw.get("expected_status_codes"))
        if str(value).strip().isdigit()
    ]
    json_path = _text(raw.get("json_path") or raw.get("field_path"))
    field = _text(raw.get("field"))
    if field and not json_path:
        json_path = "$." + field.lstrip("$.")
    operator = _text(raw.get("operator") or "equals").lower()
    if operator not in _PREDICATE_OPERATORS:
        return {}, f"predicate_operator_unsupported:{operator}"
    has_expected = "expected_value" in raw or "value" in raw
    expected = raw.get("expected_value") if "expected_value" in raw else raw.get("value")
    if not status_codes and not json_path:
        return {}, "predicate_status_or_json_path_required"
    if json_path and operator in {"equals", "not_equals", "in"} and not has_expected:
        return {}, "predicate_expected_value_required"
    if operator == "in" and not isinstance(expected, (list, tuple, set)):
        return {}, "predicate_in_expected_collection_required"
    return {
        "status_codes": sorted(set(status_codes)),
        "json_path": json_path,
        "operator": operator,
        "expected_value": list(expected) if isinstance(expected, (tuple, set)) else expected,
    }, ""


def compile_process_graph_wait_contracts(
    graph: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Validate and freeze every source-declared graph wait."""
    source = deepcopy(_dict(graph))
    nodes = _node_index(source)
    operations = _operation_index(behavior_ir)
    raw_waits = [
        dict(row)
        for row in _list(source.get("wait_contracts"))
        if isinstance(row, dict)
    ]
    compiled: list[dict[str, Any]] = []
    issues: list[str] = []
    targets_seen: set[str] = set()

    for index, raw in enumerate(raw_waits):
        wait_id = _text(raw.get("wait_id") or raw.get("contract_id")) or (
            f"wait_{index + 1}"
        )
        target = _text(
            raw.get("target_node_id")
            or raw.get("before_node_id")
            or raw.get("consumer_node_id")
        )
        source_node = _text(
            raw.get("source_node_id")
            or raw.get("after_node_id")
            or raw.get("producer_node_id")
        )
        observer_ref = _text(
            raw.get("observer_operation_ref")
            or raw.get("read_operation_ref")
            or raw.get("operation_ref")
        )
        node = _dict(nodes.get(target))
        operation = _dict(operations.get(observer_ref))
        method = _text(raw.get("method") or operation.get("method")).upper()
        path = _text(
            raw.get("path")
            or raw.get("path_template")
            or operation.get("path")
            or operation.get("raw_path")
        )
        if not target or target not in nodes:
            issues.append(f"{wait_id}:target_node_unresolved:{target}")
            continue
        if target in targets_seen:
            issues.append(f"{wait_id}:duplicate_target_wait:{target}")
            continue
        targets_seen.add(target)
        if source_node and source_node not in nodes:
            issues.append(f"{wait_id}:source_node_unresolved:{source_node}")
            continue
        if not observer_ref or not operation:
            issues.append(f"{wait_id}:observer_operation_unresolved:{observer_ref}")
            continue
        if method not in _READ_METHODS or not path.startswith("/"):
            issues.append(f"{wait_id}:observer_transport_invalid:{method}:{path}")
            continue
        actor_ref = _text(raw.get("actor_ref")) or _text(node.get("actor_ref"))
        system_ref = _text(raw.get("system_ref")) or _text(node.get("system_ref"))
        if actor_ref != _text(node.get("actor_ref")):
            issues.append(f"{wait_id}:wait_actor_must_match_target_node")
            continue
        if system_ref != _text(node.get("system_ref")):
            issues.append(f"{wait_id}:wait_system_must_match_target_node")
            continue
        predicate, predicate_error = _predicate(
            _dict(raw.get("predicate") or raw.get("terminal_predicate"))
        )
        if predicate_error:
            issues.append(f"{wait_id}:{predicate_error}")
            continue
        try:
            policy = normalize_async_policy(
                _dict(raw.get("async_policy") or raw.get("poll_policy"))
            )
        except ValueError as exc:
            issues.append(f"{wait_id}:{str(exc)}")
            continue
        compiled_row = {
            "schema_version": SCHEMA_VERSION,
            "wait_id": wait_id,
            "source_node_id": source_node,
            "target_node_id": target,
            "observer_operation_ref": observer_ref,
            "method": method,
            "path_template": path,
            "actor_ref": actor_ref,
            "system_ref": system_ref,
            "predicate": predicate,
            "async_policy": policy,
            "source_refs": _list(raw.get("source_refs"))
            or _list(operation.get("source_refs")),
        }
        compiled_row["contract_fingerprint"] = _fingerprint(compiled_row)
        compiled.append(compiled_row)

    async_edges = [
        _dict(edge)
        for edge in _list(source.get("edges"))
        if isinstance(edge, dict)
        and _text(edge.get("relation_type")).upper() in _ASYNC_RELATIONS
    ]
    for edge in async_edges:
        source_node = _text(edge.get("source_node_id"))
        target_node = _text(edge.get("target_node_id"))
        if not any(
            row["source_node_id"] == source_node
            and row["target_node_id"] == target_node
            and row["async_policy"].get("enabled") is True
            for row in compiled
        ):
            issues.append(
                f"async_edge_uncovered:{source_node}->{target_node}"
            )

    if issues:
        # An invalid declaration naturally leaves an async edge uncovered. Keep
        # that derived symptom, but never let it hide the source-contract error.
        has_duplicate = any("duplicate_target_wait" in value for value in issues)
        has_contract_error = any(
            not value.startswith("async_edge_uncovered") for value in issues
        )
        return {
            "status": STATUS_BLOCKED,
            "reason_code": (
                WAIT_CONTRACT_AMBIGUOUS
                if has_duplicate
                else WAIT_CONTRACT_INVALID
                if has_contract_error
                else WAIT_ASYNC_EDGE_UNCOVERED
            ),
            "detail": ";".join(issues[:16]),
            "issues": issues,
        }

    source["wait_contracts"] = compiled
    source["wait_contracts_by_target"] = {
        row["target_node_id"]: row for row in compiled
    }
    source["wait_runtime_contract"] = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_COMPILED,
        "contract_count": len(compiled),
        "contract_fingerprints": [
            row["contract_fingerprint"] for row in compiled
        ],
        "async_edge_count": len(async_edges),
    }
    source["status"] = "COMPILED"
    source.pop("runtime_blockers", None)
    return {
        "status": STATUS_COMPILED,
        "graph": source,
        "wait_contracts": compiled,
    }


def compiled_wait_runtime_ready(graph: dict[str, Any]) -> tuple[bool, str]:
    waits = _list(_dict(graph).get("wait_contracts"))
    async_edges = [
        edge
        for edge in _list(_dict(graph).get("edges"))
        if isinstance(edge, dict)
        and _text(edge.get("relation_type")).upper() in _ASYNC_RELATIONS
    ]
    if not waits and not async_edges:
        return True, ""
    runtime = _dict(_dict(graph).get("wait_runtime_contract"))
    if _text(runtime.get("status")) != STATUS_COMPILED:
        return False, "wait_runtime_contract_not_compiled"
    by_target = _dict(_dict(graph).get("wait_contracts_by_target"))
    if len(by_target) != len(waits):
        return False, "wait_contract_target_index_mismatch"
    return True, ""


def _json_path(value: Any, path: str) -> tuple[bool, Any]:
    token = _text(path)
    if token in {"", "$"}:
        return True, value
    if token.startswith("$."):
        token = token[2:]
    current = value
    for part in [segment for segment in token.split(".") if segment]:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def wait_predicate_matches(response: dict[str, Any], predicate: dict[str, Any]) -> bool:
    row = _dict(response)
    spec = _dict(predicate)
    status = int(row.get("status_code") or row.get("status") or 0)
    status_codes = [int(value) for value in _list(spec.get("status_codes"))]
    if status_codes and status not in status_codes:
        return False
    path = _text(spec.get("json_path"))
    if not path:
        return bool(status_codes)
    found, actual = _json_path(row.get("body"), path)
    operator = _text(spec.get("operator")).lower()
    expected = spec.get("expected_value")
    if operator == "exists":
        return found
    if not found:
        return False
    if operator == "truthy":
        return bool(actual)
    if operator == "not_equals":
        return actual != expected
    if operator == "in":
        return actual in _list(expected)
    return actual == expected


def execute_process_graph_wait(
    *,
    graph: dict[str, Any],
    step: dict[str, Any],
    context: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    read_once: "Callable[[], dict[str, Any]] | None" = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Execute the target node's compiled wait before its business request."""
    step_id = _text(_dict(step).get("step_id"))
    contract = _dict(
        _dict(_dict(graph).get("wait_contracts_by_target")).get(step_id)
    )
    if not contract:
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "status": STATUS_NOT_REQUIRED,
            "step_id": step_id,
        }
    actor_ref = _text(contract.get("actor_ref"))
    actor = _dict(actors.get(actor_ref))
    token = _resolve_token(actor, tokens)
    if not actor or (
        _text(actor.get("role")).lower() not in {"anonymous", "public"}
        and not token
    ):
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": WAIT_ACTOR_UNRESOLVED,
            "detail": actor_ref or "actor_ref_missing",
            "step_id": step_id,
            "wait_id": _text(contract.get("wait_id")),
        }
    path = materialize_path(
        _text(contract.get("path_template")),
        _dict(context.get("bindings")),
    )
    if not path.startswith("/") or path_has_placeholders(path):
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": WAIT_BINDING_UNRESOLVED,
            "detail": path or "wait_path_missing",
            "step_id": step_id,
            "wait_id": _text(contract.get("wait_id")),
        }

    reader = read_once
    if reader is None:
        reader = lambda: _run_http_step(
            base_url=_text(context.get("base_url")),
            method=_text(contract.get("method")),
            path=path,
            token=token,
        )
    kwargs: dict[str, Any] = {
        "read_once": reader,
        "accept": lambda response: wait_predicate_matches(
            response,
            _dict(contract.get("predicate")),
        ),
        "async_policy": _dict(contract.get("async_policy")),
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    execution = execute_async_readback(**kwargs)
    converged = execution.get("converged") is True
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": STATUS_CONVERGED if converged else STATUS_BLOCKED,
        "reason_code": "" if converged else (
            _text(execution.get("reason_code"))
            or WAIT_PREDICATE_NOT_SATISFIED
        ),
        "step_id": step_id,
        "wait_id": _text(contract.get("wait_id")),
        "contract_fingerprint": _text(contract.get("contract_fingerprint")),
        "observer_operation_ref": _text(
            contract.get("observer_operation_ref")
        ),
        "method": _text(contract.get("method")),
        "path": path,
        "actor_ref": actor_ref,
        "system_ref": _text(contract.get("system_ref")),
        "attempt_count": int(execution.get("attempt_count") or 0),
        "elapsed_ms": int(execution.get("elapsed_ms") or 0),
        "converged": converged,
        "timed_out": execution.get("timed_out") is True,
        "attempts": _list(execution.get("attempts")),
    }
    receipt["receipt_id"] = "wait_" + _fingerprint(receipt)[:24]
    return receipt


__all__ = [
    "RECEIPT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "STATUS_BLOCKED",
    "STATUS_COMPILED",
    "STATUS_CONVERGED",
    "STATUS_NOT_REQUIRED",
    "WAIT_ACTOR_UNRESOLVED",
    "WAIT_ASYNC_EDGE_UNCOVERED",
    "WAIT_BINDING_UNRESOLVED",
    "WAIT_CONTRACT_AMBIGUOUS",
    "WAIT_CONTRACT_INVALID",
    "compile_process_graph_wait_contracts",
    "compiled_wait_runtime_ready",
    "execute_process_graph_wait",
    "wait_predicate_matches",
]
