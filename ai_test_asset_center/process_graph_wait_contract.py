"""Public source-authoritative process-graph async wait authority.

The mature bounded polling compiler/runtime remains in
``process_graph_wait_contract_core``. This facade proves that a wait references
one declared read operation and, when the source declares message/callback
semantics, freezes an event transition contract behind that same wait entry.

There is still one scheduler and one pre-transport gate. State convergence
continues through the core; event delivery delegates to the source-declared
event transition evaluator and returns the same wait receipt status vocabulary.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_event_transition as _event
from . import process_graph_wait_contract_core as _core
from .real_id_resolver import normalize_path_placeholders


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

EVENT_TRANSITION_INVALID = _event.EVENT_TRANSITION_INVALID
EVENT_CORRELATION_UNRESOLVED = _event.EVENT_CORRELATION_UNRESOLVED
EVENT_OBSERVATION_INCOMPLETE = _event.EVENT_OBSERVATION_INCOMPLETE
EVENT_DELIVERY_COUNT_BELOW_MINIMUM = (
    _event.EVENT_DELIVERY_COUNT_BELOW_MINIMUM
)
EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM = (
    _event.EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM
)
EVENT_ID_REUSE_CONFLICT = _event.EVENT_ID_REUSE_CONFLICT
EVENT_IDEMPOTENCY_KEY_MISMATCH = _event.EVENT_IDEMPOTENCY_KEY_MISMATCH
EVENT_RETRY_LIMIT_EXCEEDED = _event.EVENT_RETRY_LIMIT_EXCEEDED


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _operations(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): dict(row)
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _nodes(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("node_id") or row.get("step_id")): dict(row)
        for row in _list(_dict(graph).get("nodes"))
        if isinstance(row, dict)
        and _text(row.get("node_id") or row.get("step_id"))
    }


def _operation_system(operation: dict[str, Any]) -> str:
    return _text(
        operation.get("system_ref")
        or operation.get("target_system_ref")
        or operation.get("service_ref")
    )


def _wait_id(raw: dict[str, Any], index: int) -> str:
    return _text(raw.get("wait_id") or raw.get("contract_id")) or (
        f"wait_{index + 1}"
    )


def _edge_scope(
    graph: dict[str, Any],
    *,
    source_node_id: str,
    target_node_id: str,
) -> tuple[dict[str, Any], str]:
    matches = [
        deepcopy(_dict(edge))
        for edge in _list(graph.get("edges"))
        if isinstance(edge, dict)
        and _text(edge.get("source_node_id")) == source_node_id
        and _text(edge.get("target_node_id")) == target_node_id
    ]
    if len(matches) != 1:
        return {}, (
            "event_edge_identity_ambiguous:"
            f"{source_node_id}->{target_node_id}:count={len(matches)}"
        )
    edge = matches[0]
    if not _text(edge.get("edge_id")) or not _text(edge.get("relation_type")):
        return {}, "event_edge_identity_incomplete"
    return edge, ""


def _event_contract_fingerprint_valid(contract: dict[str, Any]) -> bool:
    row = deepcopy(_dict(contract))
    attached = _text(row.pop("contract_fingerprint", ""))
    return bool(attached and attached == _event._fingerprint(row))


def compile_process_graph_wait_contracts(
    graph: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze declared read transport, waits and optional event semantics."""
    source = deepcopy(_dict(graph))
    operations = _operations(behavior_ir)
    nodes = _nodes(source)
    issues: list[str] = []
    raw_by_wait_id: dict[str, dict[str, Any]] = {}

    for index, raw_value in enumerate(_list(source.get("wait_contracts"))):
        raw = deepcopy(_dict(raw_value))
        wait_id = _wait_id(raw, index)
        raw["wait_id"] = wait_id
        raw_by_wait_id[wait_id] = deepcopy(raw)
        target_node_id = _text(
            raw.get("target_node_id")
            or raw.get("before_node_id")
            or raw.get("consumer_node_id")
        )
        target_node = _dict(nodes.get(target_node_id))
        observer_ref = _text(
            raw.get("observer_operation_ref")
            or raw.get("read_operation_ref")
            or raw.get("operation_ref")
        )
        operation = _dict(operations.get(observer_ref))
        if not operation:
            source["wait_contracts"][index] = raw
            continue

        declared_method = _text(operation.get("method")).upper()
        requested_method = _text(raw.get("method")).upper()
        if requested_method and requested_method != declared_method:
            issues.append(
                f"{wait_id}:observer_method_override_forbidden:"
                f"{requested_method}!={declared_method}"
            )

        declared_path = normalize_path_placeholders(
            _text(
                operation.get("path")
                or operation.get("raw_path")
                or operation.get("path_template")
            )
        )
        requested_path = normalize_path_placeholders(
            _text(raw.get("path") or raw.get("path_template"))
        )
        if requested_path and requested_path != declared_path:
            issues.append(
                f"{wait_id}:observer_path_override_forbidden:"
                f"{requested_path}!={declared_path}"
            )

        target_system = _text(target_node.get("system_ref"))
        operation_system = _operation_system(operation)
        if target_system:
            if not operation_system:
                issues.append(
                    f"{wait_id}:observer_operation_system_unbound:"
                    f"target={target_system}"
                )
            elif operation_system != target_system:
                issues.append(
                    f"{wait_id}:observer_operation_system_mismatch:"
                    f"{operation_system}!={target_system}"
                )
        elif operation_system:
            issues.append(
                f"{wait_id}:observer_operation_system_mismatch:"
                f"{operation_system}!=primary"
            )

        raw["method"] = declared_method
        raw["path"] = declared_path
        raw.pop("path_template", None)
        source["wait_contracts"][index] = raw
        raw_by_wait_id[wait_id] = deepcopy(raw)

    if issues:
        return {
            "status": _core.STATUS_BLOCKED,
            "reason_code": _core.WAIT_CONTRACT_INVALID,
            "detail": ";".join(issues[:16]),
            "issues": issues,
        }

    result = _core.compile_process_graph_wait_contracts(
        source,
        behavior_ir=behavior_ir,
    )
    if _text(result.get("status")) != _core.STATUS_COMPILED:
        return result

    compiled_graph = deepcopy(_dict(result.get("graph")))
    compiled_waits = [
        deepcopy(row)
        for row in _list(compiled_graph.get("wait_contracts"))
        if isinstance(row, dict)
    ]
    event_fingerprints: list[str] = []
    event_issues: list[str] = []
    for row in compiled_waits:
        wait_id = _text(row.get("wait_id"))
        raw = _dict(raw_by_wait_id.get(wait_id))
        if not _event.has_event_transition(raw):
            continue
        event_spec = _event._event_spec(raw)
        event_source_refs = [
            deepcopy(value)
            for value in _list(event_spec.get("source_refs"))
            if isinstance(value, dict)
        ]
        if not event_source_refs:
            event_issues.append(f"{wait_id}:event_source_refs_missing")
            continue
        edge, edge_error = _edge_scope(
            compiled_graph,
            source_node_id=_text(row.get("source_node_id")),
            target_node_id=_text(row.get("target_node_id")),
        )
        if edge_error:
            event_issues.append(f"{wait_id}:{edge_error}")
            continue
        base_wait_fingerprint = _text(row.get("contract_fingerprint"))
        event_contract, event_error = _event.compile_event_transition_contract(
            raw_wait=raw,
            compiled_wait=row,
            relation_type=_text(edge.get("relation_type")),
        )
        if event_error:
            event_issues.append(f"{wait_id}:{event_error}")
            continue
        event_contract.update(
            {
                "edge_id": _text(edge.get("edge_id")),
                "wait_contract_fingerprint": base_wait_fingerprint,
                "source_refs": event_source_refs,
            }
        )
        event_contract.pop("contract_fingerprint", None)
        event_contract["contract_fingerprint"] = _event._fingerprint(
            event_contract
        )
        row["transition_kind"] = "event_delivery"
        row["event_transition_contract"] = event_contract
        row["contract_fingerprint"] = _core._fingerprint(row)
        event_fingerprints.append(
            _text(event_contract.get("contract_fingerprint"))
        )

    if event_issues:
        return {
            "status": _core.STATUS_BLOCKED,
            "reason_code": EVENT_TRANSITION_INVALID,
            "detail": ";".join(event_issues[:16]),
            "issues": event_issues,
        }

    compiled_graph["wait_contracts"] = compiled_waits
    compiled_graph["wait_contracts_by_target"] = {
        _text(row.get("target_node_id")): row for row in compiled_waits
    }
    runtime = deepcopy(_dict(compiled_graph.get("wait_runtime_contract")))
    runtime.update(
        {
            "contract_fingerprints": [
                _text(row.get("contract_fingerprint"))
                for row in compiled_waits
            ],
            "event_transition_count": len(event_fingerprints),
            "event_transition_fingerprints": event_fingerprints,
        }
    )
    compiled_graph["wait_runtime_contract"] = runtime
    return {
        **result,
        "graph": compiled_graph,
        "wait_contracts": compiled_waits,
    }


def compiled_wait_runtime_ready(graph: dict[str, Any]) -> tuple[bool, str]:
    ready, detail = _core.compiled_wait_runtime_ready(graph)
    if not ready:
        return ready, detail
    waits = [
        _dict(row)
        for row in _list(_dict(graph).get("wait_contracts"))
        if isinstance(row, dict)
    ]
    event_contracts = [
        _dict(row.get("event_transition_contract"))
        for row in waits
        if _text(row.get("transition_kind")) == "event_delivery"
    ]
    if any(
        _text(contract.get("schema_version"))
        != _event.CONTRACT_SCHEMA_VERSION
        or _text(contract.get("status")) != _event.STATUS_COMPILED
        or not _text(contract.get("edge_id"))
        or not _text(contract.get("wait_contract_fingerprint"))
        or not _list(contract.get("source_refs"))
        or not _event_contract_fingerprint_valid(contract)
        for contract in event_contracts
    ):
        return False, "event_transition_contract_drift"
    runtime = _dict(_dict(graph).get("wait_runtime_contract"))
    if int(runtime.get("event_transition_count") or 0) != len(event_contracts):
        return False, "event_transition_count_mismatch"
    if list(runtime.get("event_transition_fingerprints") or []) != [
        _text(row.get("contract_fingerprint")) for row in event_contracts
    ]:
        return False, "event_transition_fingerprint_scope_mismatch"
    return True, ""


def execute_process_graph_wait(
    *,
    graph: dict[str, Any],
    step: dict[str, Any],
    context: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    read_once: Any = None,
    sleep: Any = None,
    monotonic: Any = None,
) -> dict[str, Any]:
    """Execute state convergence or event delivery through one wait gate."""
    step_id = _text(_dict(step).get("step_id"))
    contract = _dict(
        _dict(_dict(graph).get("wait_contracts_by_target")).get(step_id)
    )
    event_contract = _dict(contract.get("event_transition_contract"))
    if event_contract:
        kwargs: dict[str, Any] = {
            "contract": event_contract,
            "context": context,
            "actors": actors,
            "tokens": tokens,
            "read_once": read_once,
        }
        if sleep is not None:
            kwargs["sleep"] = sleep
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        receipt = _event.execute_event_transition(**kwargs)
        receipt.setdefault("step_id", step_id)
        return receipt

    kwargs = {
        "graph": graph,
        "step": step,
        "context": context,
        "actors": actors,
        "tokens": tokens,
        "read_once": read_once,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return _core.execute_process_graph_wait(**kwargs)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name not in {"_core", "_event", "_name"}
)
