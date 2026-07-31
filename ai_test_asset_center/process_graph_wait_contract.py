"""Public source-authoritative process-graph wait contract facade.

The existing bounded polling compiler/runtime remains unchanged in
``process_graph_wait_contract_core``. Before delegating, this facade proves that
a wait contract only REFERENCES the declared observer operation: it cannot
replace that operation's HTTP method, path or target system.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_wait_contract_core as _core
from .real_id_resolver import normalize_path_placeholders


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


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


def compile_process_graph_wait_contracts(
    graph: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Reject wait transport drift, then invoke the existing compiler."""
    source = deepcopy(_dict(graph))
    operations = _operations(behavior_ir)
    nodes = _nodes(source)
    issues: list[str] = []

    for index, raw_value in enumerate(_list(source.get("wait_contracts"))):
        raw = _dict(raw_value)
        wait_id = _text(raw.get("wait_id") or raw.get("contract_id")) or (
            f"wait_{index + 1}"
        )
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
            # The mature core emits the canonical unresolved-operation detail.
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

        # Pass the declared transport shape to the mature compiler, never the
        # caller-provided aliases.
        raw["method"] = declared_method
        raw["path"] = declared_path
        raw.pop("path_template", None)
        source["wait_contracts"][index] = raw

    if issues:
        return {
            "status": _core.STATUS_BLOCKED,
            "reason_code": _core.WAIT_CONTRACT_INVALID,
            "detail": ";".join(issues[:16]),
            "issues": issues,
        }
    return _core.compile_process_graph_wait_contracts(
        source,
        behavior_ir=behavior_ir,
    )


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
