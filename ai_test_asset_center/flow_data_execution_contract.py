"""Compile-time execution capability contract for frozen flow data.

``FlowDataRequirement`` identifies values a flow needs. This module proves that
the existing executors can actually supply them:

- initial values must come from the frozen binding plan;
- query placeholders are data requirements too;
- cross-step outputs are legal only on graph-backed treatment nodes;
- each dynamic consumption must explicitly name producer, output field and
  consumer target, matching the graph runtime binding-ledger contract;
- output fields must declare the exact response source path the graph runtime
  will extract.

It does not discover bindings or execute requests.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = "qualibug.flow-data-execution-contract.v1"
STATUS_FROZEN = "FROZEN"
STATUS_BLOCKED = "BLOCKED"
BLOCKED_FLOW_DATA_EXECUTION_CONTRACT_INCOMPLETE = (
    "BLOCKED_FLOW_DATA_EXECUTION_CONTRACT_INCOMPLETE"
)

_TOKEN_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tokens(value: Any) -> list[str]:
    targets: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for child in node.values():
                walk(child)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, str):
            return
        match = _TOKEN_RE.match(node)
        target = _text(match.group(1)) if match else ""
        if target and target not in targets:
            targets.append(target)

    walk(value)
    return targets


def _steps(experiment: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for phase in ("precondition", "control", "treatment"):
        rows.extend(
            (phase, dict(row))
            for row in _list(experiment.get(f"{phase}_plan"))
            if isinstance(row, dict)
        )
    return rows


def _graph(step: dict[str, Any]) -> dict[str, Any]:
    return _dict(step.get("_execution_graph"))


def _graph_node(step: dict[str, Any]) -> dict[str, Any]:
    step_id = _text(step.get("step_id") or step.get("id"))
    for raw in _list(_graph(step).get("nodes")):
        node = _dict(raw)
        if _text(node.get("node_id") or node.get("step_id")) == step_id:
            return node
    return {}


def _output_specs(step: dict[str, Any]) -> list[dict[str, Any]]:
    node = _graph_node(step)
    source = _list(step.get("output_binding_specs")) or _list(
        node.get("output_binding_specs")
    )
    return [dict(row) for row in source if isinstance(row, dict)]


def _input_refs(step: dict[str, Any]) -> list[dict[str, Any]]:
    node = _graph_node(step)
    refs = [
        dict(row)
        for row in (
            _list(step.get("input_binding_refs"))
            or _list(node.get("input_binding_refs"))
        )
        if isinstance(row, dict)
    ]
    step_id = _text(step.get("step_id") or step.get("id"))
    for edge in _list(_graph(step).get("edges")):
        edge_row = _dict(edge)
        if _text(edge_row.get("target_node_id")) != step_id:
            continue
        refs.extend(
            dict(row)
            for row in _list(edge_row.get("binding_refs"))
            if isinstance(row, dict)
        )
    return refs


def _output_identity(spec: dict[str, Any]) -> tuple[str, str]:
    canonical = _text(
        spec.get("canonical_field_id")
        or spec.get("output_field")
        or spec.get("field")
    )
    source_path = _text(
        spec.get("json_path")
        or spec.get("source_path")
        or spec.get("response_field")
    )
    return canonical, source_path


def _input_identity(ref: dict[str, Any]) -> tuple[str, str, str]:
    producer = _text(
        ref.get("producer_node_id")
        or ref.get("source_node_id")
        or ref.get("producer_step_id")
    )
    source_field = _text(
        ref.get("producer_output_field")
        or ref.get("source_field")
        or ref.get("canonical_field_id")
    )
    target = _text(
        ref.get("target")
        or ref.get("consumer_target")
        or ref.get("target_location")
    )
    return producer, source_field, target


def freeze_flow_data_execution_contract(
    experiment: dict[str, Any],
    requirement: dict[str, Any],
) -> dict[str, Any]:
    """Prove every frozen data dependency is executable by current runtimes."""
    exp = _dict(experiment)
    req = _dict(requirement)
    initial_targets = {
        _text(value)
        for value in _list(req.get("binding_targets"))
        if _text(value)
    }
    requirements_by_step = {
        _text(row.get("step_id")): _dict(row)
        for row in _list(req.get("step_requirements"))
        if isinstance(row, dict) and _text(row.get("step_id"))
    }
    produced_by_step: dict[str, set[str]] = {}
    issues: list[dict[str, Any]] = []
    step_contracts: list[dict[str, Any]] = []

    for phase, step in _steps(exp):
        step_id = _text(step.get("step_id") or step.get("id"))
        graph_backed = bool(_graph(step)) and phase == "treatment"
        output_specs = _output_specs(step)
        produced_fields: set[str] = set()
        for index, spec in enumerate(output_specs):
            canonical, source_path = _output_identity(spec)
            if not graph_backed:
                issues.append(
                    {
                        "kind": "OUTPUT_BINDING_EXECUTOR_UNAVAILABLE",
                        "phase": phase,
                        "step_id": step_id,
                        "output_index": index,
                    }
                )
                continue
            if not canonical or not source_path:
                issues.append(
                    {
                        "kind": "OUTPUT_BINDING_IDENTITY_INCOMPLETE",
                        "phase": phase,
                        "step_id": step_id,
                        "output_index": index,
                        "canonical_field_id": canonical,
                        "source_path": source_path,
                    }
                )
                continue
            produced_fields.add(canonical)
        produced_by_step[step_id] = produced_fields

        consumed_targets: set[str] = set()
        input_contracts: list[dict[str, str]] = []
        for index, ref in enumerate(_input_refs(step)):
            producer, source_field, target = _input_identity(ref)
            valid = bool(
                producer
                and source_field
                and target
                and source_field in produced_by_step.get(producer, set())
            )
            input_contracts.append(
                {
                    "producer_node_id": producer,
                    "producer_output_field": source_field,
                    "consumer_target": target,
                    "status": "RESOLVED" if valid else "BLOCKED",
                }
            )
            if valid:
                consumed_targets.add(target)
            else:
                issues.append(
                    {
                        "kind": "INPUT_BINDING_REFERENCE_UNRESOLVED",
                        "phase": phase,
                        "step_id": step_id,
                        "input_index": index,
                        "producer_node_id": producer,
                        "producer_output_field": source_field,
                        "consumer_target": target,
                    }
                )

        requirement_row = _dict(requirements_by_step.get(step_id))
        query_targets = _tokens(step.get("query"))
        required_targets = set(
            _text(value)
            for value in _list(requirement_row.get("required_binding_targets"))
            if _text(value)
        )
        required_targets.update(query_targets)
        available_targets = initial_targets | consumed_targets
        missing_targets = sorted(required_targets - available_targets)
        if missing_targets:
            issues.append(
                {
                    "kind": "STEP_BINDING_EXECUTION_SOURCE_MISSING",
                    "phase": phase,
                    "step_id": step_id,
                    "targets": missing_targets,
                }
            )
        step_contracts.append(
            {
                "phase": phase,
                "step_id": step_id,
                "graph_backed": graph_backed,
                "initial_binding_targets": sorted(
                    required_targets & initial_targets
                ),
                "query_binding_targets": query_targets,
                "input_bindings": input_contracts,
                "produced_output_fields": sorted(produced_fields),
                "required_targets": sorted(required_targets),
                "available_targets": sorted(available_targets),
                "missing_targets": missing_targets,
            }
        )

    payload = {
        "flow_data_requirement_id": _text(req.get("requirement_id")),
        "flow_data_requirement_fingerprint": _text(
            req.get("requirement_fingerprint")
        ),
        "step_contracts": step_contracts,
        "issues": issues,
        "executor_capabilities": {
            "initial_bindings": "experiment_fixture_materializer_core",
            "cross_step_bindings": "process_graph_binding_ledger",
            "sequential_output_bindings_supported": False,
        },
    }
    fingerprint = _fingerprint(payload)
    if issues:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": BLOCKED_FLOW_DATA_EXECUTION_CONTRACT_INCOMPLETE,
            "detail": ";".join(
                f"{_text(row.get('kind'))}:{_text(row.get('step_id'))}"
                for row in issues[:12]
            ),
            "contract_fingerprint": fingerprint,
            **payload,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_FROZEN,
        "contract_fingerprint": fingerprint,
        **payload,
    }


__all__ = [
    "BLOCKED_FLOW_DATA_EXECUTION_CONTRACT_INCOMPLETE",
    "SCHEMA_VERSION",
    "STATUS_BLOCKED",
    "STATUS_FROZEN",
    "freeze_flow_data_execution_contract",
]
