"""Project FlowDataRequirement into the existing materializer node schema.

The legacy materializer executes nodes shaped as ``node_id/kind/target``. Both
historical fixture DAG formats use different node schemas, so merely selecting
their execution order cannot execute bindings. This module creates a temporary,
deterministic compatibility projection from the frozen FlowDataRequirement and
binding-plan order. It does not become another data authority and never changes
the compiled experiment artifact.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "qualibug.flow-data-materializer-projection.v1"
_RECOGNIZED_KINDS = frozenset(
    {
        "actor_context",
        "runtime_read_binding",
        "ownership_fixture_proof",
        # Every compiled experiment carries a compiler-emitted setup_plan
        # (action=resolve_bindings) whose fixture_dag node must survive the
        # projection: dropping it made the oracle activation reference a node
        # missing from the projected DAG, and the materializer reconciliation
        # blocked every such experiment as BLOCKED_FIXTURE_DAG_DRIFT.
        "setup_step",
    }
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


def _binding_order(experiment: dict[str, Any]) -> list[str]:
    plan = experiment.get("binding_plan")
    rows = (
        [
            {"target": key, **(value if isinstance(value, dict) else {})}
            for key, value in plan.items()
        ]
        if isinstance(plan, dict)
        else [dict(row) for row in _list(plan) if isinstance(row, dict)]
    )
    order: list[str] = []
    for row in rows:
        target = _text(
            row.get("target")
            or row.get("binding_target")
            or row.get("template_token")
        )
        if target and not target.startswith("actor:") and target not in order:
            order.append(target)
    return order


def project_flow_data_materializer_dag(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a runtime-only experiment copy and projection receipt."""
    source = deepcopy(_dict(experiment))
    requirement = _dict(source.get("flow_data_requirement"))
    if _text(requirement.get("status")) != "FROZEN":
        return source, {
            "schema_version": SCHEMA_VERSION,
            "status": "NOT_APPLICABLE",
            "reason": "flow_data_requirement_not_frozen",
            "projected_node_count": 0,
        }

    required_targets = [
        _text(value)
        for value in _list(
            requirement.get("materialized_before_measurement_targets")
        )
        if _text(value)
    ]
    binding_order = _binding_order(source)
    ordered_targets = [
        target for target in binding_order if target in required_targets
    ]
    ordered_targets.extend(
        target for target in required_targets if target not in ordered_targets
    )

    legacy_dag = _dict(source.get("fixture_dag"))
    existing_nodes = [
        deepcopy(row)
        for row in _list(legacy_dag.get("nodes"))
        if isinstance(row, dict)
        and _text(row.get("node_id"))
        and _text(row.get("kind")) in _RECOGNIZED_KINDS
    ]
    existing_by_target = {
        _text(row.get("target")): row
        for row in existing_nodes
        if _text(row.get("kind")) == "runtime_read_binding"
        and _text(row.get("target"))
    }
    nodes = list(existing_nodes)
    generated_node_ids: list[str] = []
    for target in ordered_targets:
        if target in existing_by_target:
            continue
        node_id = f"flow_binding:{target}"
        nodes.append(
            {
                "node_id": node_id,
                "kind": "runtime_read_binding",
                "target": target,
                "authority": "flow_data_requirement",
                "flow_data_requirement_id": _text(
                    requirement.get("requirement_id")
                ),
            }
        )
        generated_node_ids.append(node_id)

    node_ids = {_text(row.get("node_id")) for row in nodes}
    legacy_order = [
        _text(value)
        for value in (
            _list(legacy_dag.get("setup_order"))
            or _list(legacy_dag.get("creation_order"))
        )
        if _text(value) in node_ids
    ]
    target_node_ids = {
        _text(row.get("target")): _text(row.get("node_id"))
        for row in nodes
        if _text(row.get("kind")) == "runtime_read_binding"
        and _text(row.get("target"))
    }
    setup_order = list(dict.fromkeys(legacy_order))
    setup_order.extend(
        target_node_ids[target]
        for target in ordered_targets
        if target in target_node_ids
        and target_node_ids[target] not in setup_order
    )
    setup_order.extend(
        _text(row.get("node_id"))
        for row in nodes
        if _text(row.get("node_id")) not in setup_order
    )

    projected_dag = {
        "schema_version": SCHEMA_VERSION,
        "status": "PROJECTED",
        "nodes": nodes,
        "setup_order": setup_order,
        "creation_order": setup_order,
        "source_requirement_id": _text(requirement.get("requirement_id")),
        "source_requirement_fingerprint": _text(
            requirement.get("requirement_fingerprint")
        ),
    }
    projected_dag["projection_fingerprint"] = _fingerprint(projected_dag)
    source["fixture_dag"] = projected_dag
    # The core currently prefers fixture_dependency_dag.execution_order. Give it
    # the same projected node identities so order and node lookup share a schema.
    source["fixture_dependency_dag"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PROJECTED",
        "nodes": nodes,
        "execution_order": setup_order,
        "fingerprint": projected_dag["projection_fingerprint"],
        "source_fixture_dependency_dag_fingerprint": _text(
            _dict(experiment.get("fixture_dependency_dag")).get("fingerprint")
        ),
    }
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "PROJECTED",
        "requirement_id": _text(requirement.get("requirement_id")),
        "requirement_fingerprint": _text(
            requirement.get("requirement_fingerprint")
        ),
        "required_targets": ordered_targets,
        "projected_node_ids": setup_order,
        "generated_node_ids": generated_node_ids,
        "projected_node_count": len(nodes),
        "projection_fingerprint": projected_dag["projection_fingerprint"],
        "compiled_experiment_mutated": False,
    }
    return source, receipt


__all__ = [
    "SCHEMA_VERSION",
    "project_flow_data_materializer_dag",
]
