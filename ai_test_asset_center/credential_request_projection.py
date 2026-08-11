"""Project declared actor credential references into request bodies.

The binding graph may prove that one credential-shaped placeholder belongs to
one exact declared actor.  The compile artifact must carry only the opaque
``credential_secret_ref`` coordinate, never the password itself.  The existing
step-kernel credential resolver dereferences that coordinate immediately before
transport and its evidence boundary redacts credential fields.

This closes the previous compiler/runtime gap where
``actor_credential_secret`` was marked runtime-resolvable but no fixture/read
resolver could ever materialize ``{password}``.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .runtime_binding_graph import _request_example

SCHEMA_VERSION = "qualibug.credential-request-projection.v1"


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


def _credential_bindings(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _dict(experiment).get("binding_plan")
    rows = (
        [
            {**(_dict(value)), "target": _text(_dict(value).get("target") or key)}
            for key, value in raw.items()
            if isinstance(value, dict)
        ]
        if isinstance(raw, dict)
        else [dict(row) for row in _list(raw) if isinstance(row, dict)]
    )
    return [
        row
        for row in rows
        if _text(row.get("source_priority")) == "actor_credential_secret"
    ]


def _replace_exact_token(value: Any, target: str, secret_ref: str) -> tuple[Any, int]:
    count = 0
    brace = "{" + target + "}"
    angle = "<" + target + ">"

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, dict):
            return {key: walk(child) for key, child in node.items()}
        if isinstance(node, list):
            return [walk(child) for child in node]
        if isinstance(node, str) and node.strip() in {brace, angle}:
            count += 1
            return secret_ref
        return node

    return walk(value), count


def project_declared_credential_refs(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace proven credential placeholders with opaque secret references."""

    result = deepcopy(_dict(experiment))
    operations = _operation_index(behavior_ir)
    bindings = _credential_bindings(result)
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

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
            plan = _list(result.get(f"{phase}_plan"))
            projected_plan: list[Any] = []
            for raw_step in plan:
                if not isinstance(raw_step, dict):
                    projected_plan.append(raw_step)
                    continue
                step = deepcopy(raw_step)
                op_ref = _text(step.get("operation_ref"))
                operation = _dict(operations.get(op_ref))
                body = step.get("body") if "body" in step else _request_example(operation)
                projected_body, replacement_count = _replace_exact_token(
                    body,
                    target,
                    secret_ref,
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

        audit.update(
            {
                "status": "PROJECTED" if audit["replacement_count"] else "NOT_USED",
                "reason_code": "",
            }
        )
        rows.append(audit)

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED" if issues else "PROJECTED",
        "binding_count": len(bindings),
        "projected_binding_count": sum(
            1 for row in rows if _text(row.get("status")) == "PROJECTED"
        ),
        "rows": rows,
        "issues": issues,
        "secret_value_persisted": False,
    }
    result["credential_request_projection_receipt"] = receipt
    return result, receipt


__all__ = ["SCHEMA_VERSION", "project_declared_credential_refs"]
