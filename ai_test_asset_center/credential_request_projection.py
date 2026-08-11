"""Project declared actor credential references into request bodies.

The binding graph may prove that one credential-shaped placeholder belongs to
one exact declared actor. The compile artifact must carry only the opaque
``credential_secret_ref`` coordinate, never the password itself. The existing
step-kernel credential resolver dereferences that coordinate immediately before
transport and its evidence boundary redacts credential fields.

Projection is driven by the binding plan's exact ``body_template_paths``. This
covers source redaction spellings such as ``<PASSWORD>`` without guessing token
case or replacing a concrete business value. Once every use is projected, the
credential target is removed from the experiment-global binding plan and any
runtime-read binding DAG node for that target is pruned. A credential secret is
not FlowData and must never re-enter fixture/runtime materialization after it has
become a request-local secret reference.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .runtime_binding_graph import _request_example

SCHEMA_VERSION = "qualibug.credential-request-projection.v1"
_PLACEHOLDER_VALUE_RE = re.compile(
    r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$"
)
_PATH_TOKEN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]")


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
            if not isinstance(value, dict):
                continue
            row = dict(value)
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


def _credential_bindings(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    rows, _shape = _binding_rows(experiment)
    return [
        row
        for row in rows
        if _text(row.get("source_priority")) == "actor_credential_secret"
    ]


def _path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for match in _PATH_TOKEN_RE.finditer(_text(path)):
        field, index = match.groups()
        if field:
            tokens.append(field)
        elif index:
            tokens.append(int(index))
    return tokens


def _set_placeholder_at_path(
    value: Any,
    path: str,
    secret_ref: str,
) -> tuple[Any, bool]:
    """Replace one existing placeholder scalar at an exact declared body path."""

    tokens = _path_tokens(path)
    if not tokens:
        return value, False
    root = deepcopy(value)
    current: Any = root
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return value, False
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return value, False
            current = current[token]

    final = tokens[-1]
    if isinstance(final, int):
        if not isinstance(current, list) or final >= len(current):
            return value, False
        existing = current[final]
    else:
        if not isinstance(current, dict) or final not in current:
            return value, False
        existing = current[final]

    if not isinstance(existing, str) or not _PLACEHOLDER_VALUE_RE.match(existing):
        return value, False

    current[final] = secret_ref
    return root, True


def _replace_exact_token(value: Any, target: str, secret_ref: str) -> tuple[Any, int]:
    """Compatibility fallback for old binding rows without body path metadata."""

    count = 0
    governed_target = _text(target).casefold()

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, dict):
            return {key: walk(child) for key, child in node.items()}
        if isinstance(node, list):
            return [walk(child) for child in node]
        if isinstance(node, str):
            match = _PLACEHOLDER_VALUE_RE.match(node)
            if match and _text(match.group(1)).casefold() == governed_target:
                count += 1
                return secret_ref
        return node

    return walk(value), count


def _project_binding_into_body(
    body: Any,
    binding: dict[str, Any],
    *,
    target: str,
    secret_ref: str,
) -> tuple[Any, int]:
    paths = [
        _text(value)
        for value in _list(binding.get("body_template_paths"))
        if _text(value)
    ]
    projected = deepcopy(body)
    count = 0
    for path in list(dict.fromkeys(paths)):
        projected, replaced = _set_placeholder_at_path(
            projected,
            path,
            secret_ref,
        )
        count += int(replaced)
    if count or paths:
        return projected, count
    return _replace_exact_token(projected, target, secret_ref)


def _contains_target_placeholder(value: Any, target: str) -> bool:
    wanted = _text(target).casefold()
    if isinstance(value, dict):
        return any(_contains_target_placeholder(child, target) for child in value.values())
    if isinstance(value, list):
        return any(_contains_target_placeholder(child, target) for child in value)
    if not isinstance(value, str):
        return False
    match = _PLACEHOLDER_VALUE_RE.match(value)
    return bool(match and _text(match.group(1)).casefold() == wanted)


def _target_still_used_as_placeholder(experiment: dict[str, Any], target: str) -> bool:
    for phase in ("precondition", "control", "treatment"):
        for raw in _list(experiment.get(f"{phase}_plan")):
            if not isinstance(raw, dict):
                continue
            step = _dict(raw)
            if _contains_target_placeholder(step.get("body"), target):
                return True
            if _contains_target_placeholder(step.get("query"), target):
                return True
            if _contains_target_placeholder(step.get("path"), target):
                return True
    return False


def _prune_runtime_binding_nodes(
    experiment: dict[str, Any],
    targets: set[str],
) -> dict[str, list[str]]:
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
            if not (isinstance(node, dict) and _text(node.get("node_id")) in removed_ids)
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
        governed["credential_request_binding_nodes_pruned"] = sorted(removed_ids)
        governed["credential_request_binding_targets"] = sorted(targets)
        experiment[field] = governed
        pruned[field] = sorted(removed_ids)
    return pruned


def project_declared_credential_refs(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace proven credential placeholders with opaque secret references."""

    result = deepcopy(_dict(experiment))
    bindings = _credential_bindings(result)
    existing_receipt = _dict(result.get("credential_request_projection_receipt"))
    if not bindings and _text(existing_receipt.get("schema_version")) == SCHEMA_VERSION:
        return result, dict(existing_receipt)

    operations = _operation_index(behavior_ir)
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    removable_targets: set[str] = set()

    for binding in bindings:
        target = _text(binding.get("target"))
        actor_ref = _text(binding.get("actor_ref"))
        secret_ref = _text(binding.get("credential_secret_ref"))
        status = _text(binding.get("status")).lower()
        authority = _text(binding.get("credential_actor_authority"))
        audit = {
            "target": target,
            "actor_ref": actor_ref,
            "credential_secret_ref": secret_ref,
            "credential_actor_authority": authority,
            "body_template_paths": [
                _text(value)
                for value in _list(binding.get("body_template_paths"))
                if _text(value)
            ],
            "projected_step_ids": [],
            "replacement_count": 0,
            "secret_value_persisted": False,
        }
        if not (
            target
            and status == "runtime_resolvable"
            and actor_ref
            and secret_ref
            and authority
        ):
            issue = {
                **audit,
                "status": "BLOCKED",
                "reason_code": "CREDENTIAL_BINDING_COORDINATE_INCOMPLETE",
            }
            issues.append(issue)
            rows.append(issue)
            continue

        for phase in ("precondition", "control", "treatment"):
            projected_plan: list[Any] = []
            for raw_step in _list(result.get(f"{phase}_plan")):
                if not isinstance(raw_step, dict):
                    projected_plan.append(raw_step)
                    continue
                step = deepcopy(raw_step)
                op_ref = _text(step.get("operation_ref"))
                operation = _dict(operations.get(op_ref))
                body = step.get("body") if "body" in step else _request_example(operation)
                projected_body, replacement_count = _project_binding_into_body(
                    body,
                    binding,
                    target=target,
                    secret_ref=secret_ref,
                )
                if replacement_count:
                    step_actor = _text(step.get("actor_ref"))
                    if step_actor and step_actor != actor_ref:
                        issue = {
                            **audit,
                            "status": "BLOCKED",
                            "reason_code": "CREDENTIAL_BINDING_STEP_ACTOR_MISMATCH",
                            "step_id": _text(step.get("step_id") or step.get("id")),
                            "step_actor_ref": step_actor,
                        }
                        issues.append(issue)
                        rows.append(issue)
                        projected_plan.append(step)
                        continue
                    step["body"] = projected_body
                    step["credential_request_projection"] = {
                        "schema_version": SCHEMA_VERSION,
                        "target": target,
                        "actor_ref": actor_ref,
                        "credential_secret_ref": secret_ref,
                        "secret_value_persisted": False,
                    }
                    audit["replacement_count"] += replacement_count
                    audit["projected_step_ids"].append(
                        _text(step.get("step_id") or step.get("id"))
                    )
                projected_plan.append(step)
            result[f"{phase}_plan"] = projected_plan

        if _target_still_used_as_placeholder(result, target):
            issue = {
                **audit,
                "status": "BLOCKED",
                "reason_code": "CREDENTIAL_BINDING_PROJECTION_INCOMPLETE",
            }
            issues.append(issue)
            rows.append(issue)
            continue

        removable_targets.add(target)
        audit.update(
            {
                "status": "PROJECTED" if audit["replacement_count"] else "NOT_USED",
                "reason_code": "",
                "global_binding_required": False,
            }
        )
        rows.append(audit)

    all_bindings, shape = _binding_rows(result)
    governed_bindings = [
        row
        for row in all_bindings
        if not (
            _text(row.get("source_priority")) == "actor_credential_secret"
            and _text(row.get("target")) in removable_targets
        )
    ]
    _restore_binding_shape(result, governed_bindings, shape)
    pruned_nodes = _prune_runtime_binding_nodes(result, removable_targets)

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED" if issues else "PROJECTED",
        "binding_count": len(bindings),
        "projected_binding_count": sum(
            1 for row in rows if _text(row.get("status")) == "PROJECTED"
        ),
        "removed_global_binding_targets": sorted(removable_targets),
        "pruned_fixture_dag_nodes": pruned_nodes,
        "rows": rows,
        "issues": issues,
        "secret_value_persisted": False,
    }
    result["credential_request_projection_receipt"] = receipt
    return result, receipt


__all__ = ["SCHEMA_VERSION", "project_declared_credential_refs"]
