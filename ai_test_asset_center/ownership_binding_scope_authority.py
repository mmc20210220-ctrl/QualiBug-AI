"""Compile-time ownership binding scope authority.

One target name such as ``userId`` can mean two fundamentally different things:

* QUERY own-scope: each control/treatment step must use *that step actor's* own
  runtime identity. This is not an experiment-global binding at all.
* authorization same-resource BODY scope: both arms intentionally address the
  control owner's same resource. This is one shared binding, but its owner actor
  must be explicit rather than implied by control-plan list order.

This authority runs after the family protocol has fixed step actors and before
FlowData/compile freeze. Query-only ownership placeholders are replaced by an
opaque actor-identity coordinate and removed from the global binding plan. Any
runtime-read-binding nodes already emitted by the raw compiler for those targets
are pruned from both fixture DAG projections by exact target/node identity.
Same-resource body bindings retain their target but are sealed with the exact
control owner actor.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .obligation_compiler_base import (
    _ownership_binder_location,
    _ownership_params_declared_on_operation,
)

SCHEMA_VERSION = "qualibug.ownership-binding-scope-authority.v1"
ACTOR_IDENTITY_REF_PREFIX = "actor_identity_ref:"
_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _binding_rows(experiment: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    raw = _dict(experiment).get("binding_plan")
    if isinstance(raw, dict):
        rows: list[dict[str, Any]] = []
        for key, value in raw.items():
            row = dict(value) if isinstance(value, dict) else {}
            row.setdefault("target", _text(key))
            rows.append(row)
        return rows, "dict"
    return [dict(row) for row in _list(raw) if isinstance(row, dict)], "list"


def _restore_binding_shape(
    experiment: dict[str, Any],
    rows: list[dict[str, Any]],
    shape: str,
) -> None:
    if shape == "dict":
        experiment["binding_plan"] = {
            _text(row.get("target")): row
            for row in rows
            if _text(row.get("target"))
        }
    else:
        experiment["binding_plan"] = rows


def _query_actor_ref(actor_ref: str, target: str) -> str:
    return f"{ACTOR_IDENTITY_REF_PREFIX}{_text(actor_ref)}:{_text(target)}"


def _is_target_placeholder(value: Any, target: str) -> bool:
    if not isinstance(value, str):
        return False
    match = _PLACEHOLDER_RE.match(value)
    return bool(match and _text(match.group(1)) == _text(target))


def _step_uses_target_body(step: dict[str, Any], target: str) -> bool:
    wanted = _text(target)

    def walk(value: Any) -> bool:
        if isinstance(value, dict):
            return any(walk(child) for child in value.values())
        if isinstance(value, list):
            return any(walk(child) for child in value)
        return _is_target_placeholder(value, wanted)

    return walk(step.get("body"))


def _project_query_steps(
    experiment: dict[str, Any],
    *,
    target: str,
    operations: dict[str, dict[str, Any]],
) -> tuple[int, list[dict[str, Any]], bool, bool]:
    """Project exact query placeholders and report query/non-query scope use."""

    projected_count = 0
    rows: list[dict[str, Any]] = []
    non_query_use = False
    query_scope_seen = False
    for phase in ("precondition", "control", "treatment"):
        new_plan: list[Any] = []
        for raw in _list(experiment.get(f"{phase}_plan")):
            if not isinstance(raw, dict):
                new_plan.append(raw)
                continue
            step = deepcopy(raw)
            operation_ref = _text(step.get("operation_ref"))
            operation = _dict(operations.get(operation_ref))
            if not operation or target not in set(
                _ownership_params_declared_on_operation(operation)
            ):
                new_plan.append(step)
                continue
            location = _ownership_binder_location(operation, name=target)
            if location != "query":
                if _step_uses_target_body(step, target):
                    non_query_use = True
                new_plan.append(step)
                continue
            query_scope_seen = True
            query = _dict(step.get("query"))
            actor_ref = _text(step.get("actor_ref"))
            replacements = 0
            new_query = dict(query)
            for key, value in query.items():
                if not _is_target_placeholder(value, target):
                    continue
                if not actor_ref:
                    rows.append(
                        {
                            "phase": phase,
                            "step_id": _text(step.get("step_id") or step.get("id")),
                            "operation_ref": operation_ref,
                            "target": target,
                            "status": "BLOCKED",
                            "reason_code": "OWNERSHIP_QUERY_STEP_ACTOR_MISSING",
                        }
                    )
                    continue
                new_query[key] = _query_actor_ref(actor_ref, target)
                replacements += 1
            if replacements:
                step["query"] = new_query
                step["ownership_query_scope"] = {
                    "schema_version": SCHEMA_VERSION,
                    "scope": "step_actor_query",
                    "actor_ref": actor_ref,
                    "target": target,
                    "identity_value_persisted": False,
                }
                projected_count += replacements
                rows.append(
                    {
                        "phase": phase,
                        "step_id": _text(step.get("step_id") or step.get("id")),
                        "operation_ref": operation_ref,
                        "actor_ref": actor_ref,
                        "target": target,
                        "scope": "step_actor_query",
                        "status": "SEALED",
                        "replacement_count": replacements,
                        "identity_value_persisted": False,
                    }
                )
            new_plan.append(step)
        experiment[f"{phase}_plan"] = new_plan
    return projected_count, rows, non_query_use, query_scope_seen


def _prune_query_binding_dag_nodes(
    experiment: dict[str, Any],
    targets: set[str],
) -> dict[str, list[str]]:
    """Remove only runtime-read nodes for query-local targets from existing DAGs."""

    pruned: dict[str, list[str]] = {}
    if not targets:
        return pruned
    for field in ("fixture_dag", "fixture_dependency_dag"):
        dag = _dict(experiment.get(field))
        if not dag:
            continue
        removed_ids = {
            _text(node.get("node_id"))
            for node in _list(dag.get("nodes"))
            if isinstance(node, dict)
            and _text(node.get("kind")) == "runtime_read_binding"
            and _text(node.get("target")) in targets
            and _text(node.get("node_id"))
        }
        if not removed_ids:
            continue
        governed = deepcopy(dag)
        governed["nodes"] = [
            node
            for node in _list(governed.get("nodes"))
            if not (
                isinstance(node, dict)
                and _text(node.get("node_id")) in removed_ids
            )
        ]
        for order_field in (
            "setup_order",
            "cleanup_order",
            "execution_order",
            "topological_order",
        ):
            if order_field in governed:
                governed[order_field] = [
                    node_id
                    for node_id in _list(governed.get(order_field))
                    if _text(node_id) not in removed_ids
                ]
        if "edges" in governed:
            governed["edges"] = [
                edge
                for edge in _list(governed.get("edges"))
                if not (
                    isinstance(edge, dict)
                    and (
                        _text(edge.get("from")) in removed_ids
                        or _text(edge.get("to")) in removed_ids
                    )
                )
            ]
        governed["query_local_binding_nodes_pruned"] = sorted(removed_ids)
        governed["query_local_binding_targets"] = sorted(targets)
        experiment[field] = governed
        pruned[field] = sorted(removed_ids)
    return pruned


def seal_ownership_binding_scopes(
    experiment: dict[str, Any],
    *,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = deepcopy(_dict(experiment))
    operations = _operation_index(behavior_ir)
    bindings, binding_shape = _binding_rows(result)
    prop = _dict(_dict(obligation).get("property"))
    family = _text(_dict(obligation).get("risk_family"))
    require_same_resource = prop.get("require_same_resource") is True

    governed: list[dict[str, Any]] = []
    receipt_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    removed_query_targets: set[str] = set()

    for raw_binding in bindings:
        binding = dict(raw_binding)
        if _text(binding.get("source_priority")) != "ownership_identity_param":
            governed.append(binding)
            continue
        target = _text(binding.get("target"))
        if not target:
            issue = {
                "status": "BLOCKED",
                "reason_code": "OWNERSHIP_BINDING_TARGET_MISSING",
            }
            issues.append(issue)
            receipt_rows.append(issue)
            governed.append(binding)
            continue

        query_count, query_rows, non_query_use, query_scope_seen = _project_query_steps(
            result,
            target=target,
            operations=operations,
        )
        receipt_rows.extend(query_rows)
        issues.extend(row for row in query_rows if row.get("status") == "BLOCKED")

        # A target used exclusively as arm-local query identity is no longer a
        # global FlowData binding after projection. This is true even if a step
        # already carried a concrete query value: the global ownership row has no
        # remaining consumer. Remove the raw compiler's runtime-binding DAG node
        # as well so activation cannot demand a ghost materialization receipt.
        if query_scope_seen and not non_query_use:
            removed_query_targets.add(target)
            receipt_rows.append(
                {
                    "target": target,
                    "scope": "step_actor_query",
                    "status": "REMOVED_FROM_GLOBAL_BINDING_PLAN",
                    "global_binding_required": False,
                    "projected_placeholder_count": query_count,
                    "identity_value_persisted": False,
                }
            )
            continue

        if family == "authorization" and require_same_resource:
            control_actor_refs = list(
                dict.fromkeys(
                    _text(step.get("actor_ref"))
                    for step in _list(result.get("control_plan"))
                    if isinstance(step, dict)
                    and _text(step.get("actor_ref"))
                    and _text(step.get("operation_ref")) in operations
                    and _ownership_binder_location(
                        _dict(operations.get(_text(step.get("operation_ref")))),
                        name=target,
                    )
                    == "body"
                    and _step_uses_target_body(step, target)
                )
            )
            if len(control_actor_refs) != 1:
                issue = {
                    "target": target,
                    "scope": "shared_control_resource_owner",
                    "status": "BLOCKED",
                    "reason_code": "OWNERSHIP_SAME_RESOURCE_CONTROL_OWNER_NOT_UNIQUE",
                    "candidate_actor_refs": control_actor_refs,
                }
                issues.append(issue)
                receipt_rows.append(issue)
                governed.append(binding)
                continue
            owner_actor_ref = control_actor_refs[0]
            binding.update(
                {
                    "ownership_binding_scope": "shared_control_resource_owner",
                    "owner_actor_ref": owner_actor_ref,
                    "ownership_actor_authority": "compiled_control_step_actor",
                    "source_order_selection_allowed": False,
                }
            )
            receipt_rows.append(
                {
                    "target": target,
                    "scope": "shared_control_resource_owner",
                    "owner_actor_ref": owner_actor_ref,
                    "status": "SEALED",
                    "source_order_selection_allowed": False,
                }
            )
            governed.append(binding)
            continue

        # Remaining body ownership cases need a separately defined semantic
        # channel (for example validation's acting-actor body). Do not invent a
        # global owner coordinate merely because a binding row exists.
        issue = {
            "target": target,
            "scope": "unclassified_ownership_binding",
            "status": "BLOCKED",
            "reason_code": "OWNERSHIP_BINDING_SCOPE_UNPROVEN",
        }
        issues.append(issue)
        receipt_rows.append(issue)
        governed.append(binding)

    _restore_binding_shape(result, governed, binding_shape)
    pruned_nodes = _prune_query_binding_dag_nodes(
        result,
        removed_query_targets,
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED" if issues else "SEALED",
        "rows": receipt_rows,
        "issues": issues,
        "removed_query_targets": sorted(removed_query_targets),
        "pruned_fixture_dag_nodes": pruned_nodes,
        "source_order_selection_allowed": False,
        "identity_value_persisted": False,
    }
    result["ownership_binding_scope_receipt"] = receipt
    return result, receipt


__all__ = [
    "SCHEMA_VERSION",
    "ACTOR_IDENTITY_REF_PREFIX",
    "seal_ownership_binding_scopes",
]
