"""Execute a compiled state-precondition plan before measured business steps.

The planner and compile freezer already decide which operations, actors,
readback surfaces, async policy, target state, and state field are authoritative.
This executor materializes only declared templates, performs governed writes,
and verifies the explicit target field. It never invents an entry state, path,
body value, observer, or compensation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from .assertion_dsl_base import _state_token
from .experiment_runtime_support import (
    _WRITE_METHODS,
    _declared_observation_path,
    _dict,
    _list,
    _request_example,
    _resolve_token,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template,
    materialize_path,
)
from .runtime_binding_materializer_base import (
    runtime_setup_value_from_response as _runtime_setup_value_from_response,
)
from .sandbox_write_executor import execute_governed_control_write


SCHEMA_VERSION = "qualibug.precondition-execution.v1"
BLOCKED_PRECONDITION_STEP_INVALID = "BLOCKED_PRECONDITION_STEP_INVALID"
BLOCKED_PRECONDITION_BINDING_INCOMPLETE = "BLOCKED_PRECONDITION_BINDING_INCOMPLETE"
BLOCKED_PRECONDITION_TRANSPORT = "BLOCKED_PRECONDITION_TRANSPORT"
BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED = "BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED"
BLOCKED_PRECONDITION_TARGET_NOT_REACHED = "BLOCKED_PRECONDITION_TARGET_NOT_REACHED"


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _state_field(step: dict[str, Any]) -> str:
    return _text(
        step.get("state_field")
        or step.get("field")
        or _dict(step.get("readback_contract")).get("state_field")
    )


def _field_values(value: Any, field: str) -> list[Any]:
    """Collect exact key matches for one source-declared state field."""
    result: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if _text(key) == field and not isinstance(child, (dict, list)):
                    result.append(child)
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    unique: list[Any] = []
    markers: set[str] = set()
    for item in result:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in markers:
            markers.add(marker)
            unique.append(item)
    return unique


def _target_verdict(
    *,
    step: dict[str, Any],
    governed: dict[str, Any],
) -> dict[str, Any]:
    target_state = _text(step.get("to_state") or step.get("target_state"))
    state_field = _state_field(step)
    # A subject-establishment create has no prior identity to GET before the
    # write; the create RESPONSE is the observation of the created entity.
    # The planner marks such steps with observe_response_body so the verdict
    # reads the governed write response instead of a post-write readback GET.
    if step.get("observe_response_body") is True:
        after = _dict(governed.get("write"))
    else:
        after = _dict(
            governed.get("response_bound_after")
            or governed.get("after")
        )
    after_status = int(after.get("status") or after.get("status_code") or 0)
    if not target_state or not state_field:
        return {
            "observed": False,
            "reached": False,
            "reason_code": BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED,
            "detail": (
                f"target_or_state_field_missing:target={target_state or 'missing'}:"
                f"field={state_field or 'missing'}"
            ),
            "state_field": state_field,
            "target_state": target_state,
            "observed_values": [],
        }
    if not (200 <= after_status < 300):
        return {
            "observed": False,
            "reached": False,
            "reason_code": BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED,
            "detail": f"readback_status_not_success:{after_status}",
            "state_field": state_field,
            "target_state": target_state,
            "observed_values": [],
        }
    values = _field_values(after.get("body"), state_field)
    if len(values) != 1:
        return {
            "observed": False,
            "reached": False,
            "reason_code": BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED,
            "detail": f"state_field_value_count:{state_field}:{len(values)}",
            "state_field": state_field,
            "target_state": target_state,
            "observed_values": values,
        }
    reached = _state_token(values[0]) == _state_token(target_state)
    return {
        "observed": True,
        "reached": reached,
        "reason_code": "" if reached else BLOCKED_PRECONDITION_TARGET_NOT_REACHED,
        "detail": "" if reached else (
            f"state_mismatch:{state_field}:expected={target_state}:actual={values[0]}"
        ),
        "state_field": state_field,
        "target_state": target_state,
        "observed_values": values,
    }


def execute_precondition_plan(
    *,
    exp: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    runtime_bindings: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
) -> dict[str, Any]:
    """Execute all declared precondition steps in order, fail closed on first gap."""
    plan = [
        dict(step)
        for step in _list(_dict(exp).get("precondition_plan"))
        if isinstance(step, dict)
    ]
    if not plan:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "NOT_REQUIRED",
            "established": True,
            "steps": [],
            "receipts": [],
            "governed_write_steps": [],
            "reason_code": "",
            "detail": "",
        }

    receipts: list[dict[str, Any]] = []
    governed_write_steps: list[dict[str, Any]] = []
    identity_bindings: dict[str, Any] = {}
    for ordinal, step in enumerate(plan, 1):
        step_id = _text(step.get("step_id") or step.get("id"))
        operation_ref = _text(step.get("operation_ref"))
        actor_ref = _text(step.get("actor_ref"))
        operation = _dict(ops.get(operation_ref))
        actor = _dict(actors.get(actor_ref))
        method = _text(step.get("method") or operation.get("method")).upper()
        path_template = _text(
            step.get("path")
            or operation.get("path")
            or operation.get("raw_path")
        )
        token = _resolve_token(actor, tokens)
        if (
            not step_id
            or not operation_ref
            or not operation
            or not actor_ref
            or not actor
            or method not in _WRITE_METHODS
            or not path_template.startswith("/")
        ):
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "BLOCKED",
                "established": False,
                "steps": plan,
                "receipts": receipts,
                "governed_write_steps": governed_write_steps,
                "reason_code": BLOCKED_PRECONDITION_STEP_INVALID,
                "detail": f"invalid_precondition_step:{step_id or ordinal}",
            }

        path = materialize_path(path_template, runtime_bindings)
        body_template = (
            step.get("body")
            if "body" in step
            else _request_example(operation)
        )
        body = materialize_body_template(body_template, runtime_bindings)
        unresolved_path = _unresolved_path_placeholders(path)
        unresolved_body = _unresolved_body_placeholders(body, runtime_bindings)
        if unresolved_path or unresolved_body:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "BLOCKED",
                "established": False,
                "steps": plan,
                "receipts": receipts,
                "governed_write_steps": governed_write_steps,
                "reason_code": BLOCKED_PRECONDITION_BINDING_INCOMPLETE,
                "detail": (
                    f"step={step_id}:path={','.join(unresolved_path)}:"
                    f"body={','.join(unresolved_body)}"
                ),
            }

        observation_path = _declared_observation_path(
            path_template,
            ops,
            runtime_bindings=runtime_bindings,
            request_body=body,
        )
        if step.get("observe_response_body") is True:
            # Subject-establishment create: the create response is the
            # observation of the created entity. No readback GET is possible
            # before the write (no identity yet), so the observation path is
            # the write path itself and the verdict reads the response body.
            observation_path = path_template
        else:
            readback_contract = _dict(step.get("readback_contract"))
            resolver_operations = [
                _dict(row)
                for row in _list(readback_contract.get("resolver_operations"))
                if isinstance(row, dict)
            ]
            if resolver_operations:
                candidate = _text(resolver_operations[0].get("path"))
                if candidate:
                    candidate = materialize_path(candidate, runtime_bindings)
                    if not _unresolved_path_placeholders(candidate):
                        observation_path = candidate
        if not observation_path.startswith("/"):
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "BLOCKED",
                "established": False,
                "steps": plan,
                "receipts": receipts,
                "governed_write_steps": governed_write_steps,
                "reason_code": BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED,
                "detail": f"step={step_id}:observation_path_unresolved",
            }

        governed = execute_governed_control_write(
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase="state_precondition_establishment",
            actor_identity=_text(actor.get("role") or actor_ref),
            actor_token=token,
            method=method,
            path=path,
            body=body,
            observation_path=observation_path,
            # Only pass runtime_body_plan when it is a real mutation plan.
            # The compile freezer adds readback_contract/async_policy metadata
            # which the sandbox executor misinterprets as a mutation directive.
            runtime_body_plan=(
                deepcopy(_dict(step.get("runtime_body_plan")))
                if _text(_dict(step.get("runtime_body_plan")).get("schema_version"))
                == "qualibug.source-observed-mutation-plan.v1"
                else None
            ),
        )
        write = _dict(governed.get("write"))
        status_code = int(write.get("status") or 0)
        accepted = governed.get("accepted") is True and 200 <= status_code < 300
        verdict = _target_verdict(step=step, governed=governed) if accepted else {
            "observed": False,
            "reached": False,
            "reason_code": BLOCKED_PRECONDITION_TRANSPORT,
            "detail": f"step={step_id}:write_status={status_code}",
            "state_field": _state_field(step),
            "target_state": _text(step.get("to_state")),
            "observed_values": [],
        }
        # ── Money-family subject identity capture ──
        # A precondition step that creates the subject entity of a money
        # obligation (order before pay) declares ``identity_binding_target``
        # (e.g. orderId). When the governed write is accepted, capture the
        # created entity identity from the write response body into
        # runtime_bindings so the control/treatment bodies materialize the
        # same created subject (pay the order that was just created).
        # Registration mirrors the fixture materializer's setup capture and
        # is therefore exactly one source: the create response itself.
        _identity_target = _text(step.get("identity_binding_target"))
        if (
            _identity_target
            and accepted
            and verdict.get("reached") is not False
        ):
            _identity_value = _runtime_setup_value_from_response(
                write.get("body"),
                _identity_target,
            )
            if _identity_value in (None, "", [], {}):
                # Final fallback: the response may wrap the created entity
                # (data/result/entity) or name the id differently (order_id
                # vs orderId). The helper's wrapper scan already covers the
                # common shapes; keep the binding unresolved otherwise — the
                # control arm then fails visibly instead of guessing.
                pass
            else:
                runtime_bindings[_identity_target] = _identity_value
                identity_bindings[_identity_target] = _identity_value
                # The request-body placeholder may carry either spelling
                # (``<order_id>`` token form vs ``{orderId}`` field form);
                # register both so body materialization binds either shape.
                import re as _re

                _snake_form = _re.sub(
                    r"(?<=[a-z0-9])(?=[A-Z])", "_", _identity_target
                ).lower()
                if _snake_form and _snake_form != _identity_target:
                    runtime_bindings[_snake_form] = _identity_value
                    identity_bindings[_snake_form] = _identity_value
                receipt_id_identity = "precond_identity_" + _fingerprint(
                    {
                        "step_id": step_id,
                        "target": _identity_target,
                        "value": _identity_value,
                    }
                )[:24]
                receipts.append({
                    "schema_version": SCHEMA_VERSION,
                    "receipt_id": receipt_id_identity,
                    "step_id": step_id,
                    "source_step_id": step_id,
                    "step_ordinal": ordinal,
                    "phase": "precondition_identity",
                    "operation_ref": operation_ref,
                    "actor_ref": actor_ref,
                    "status_code": status_code,
                    "accepted": True,
                    "target": _identity_target,
                    "identity_value": _identity_value,
                    "status": "COMPLETED",
                    "reason_code": "",
                    "detail": "money_subject_identity_captured",
                })
        receipt_id = "precond_" + _fingerprint(
            {
                "step_id": step_id,
                "operation_ref": operation_ref,
                "status_code": status_code,
                "verdict": verdict,
            }
        )[:24]
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": receipt_id,
            "step_id": step_id,
            "source_step_id": step_id,
            "step_ordinal": ordinal,
            "phase": "precondition",
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "status_code": status_code,
            "accepted": accepted,
            "target_reached": verdict["reached"] if verdict["observed"] else None,
            "semantic_verdict_source": "state_observer",
            "state_field": verdict["state_field"],
            "target_state": verdict["target_state"],
            "observed_values": verdict["observed_values"],
            "status": "COMPLETED" if accepted and verdict["reached"] else "FAILED",
            "reason_code": verdict["reason_code"],
            "detail": verdict["detail"],
        }
        receipts.append(receipt)
        governed_write_steps.append(
            {
                "phase": "precondition",
                "step_id": step_id,
                "operation_ref": operation_ref,
                "actor_ref": actor_ref,
                "method": method,
                "path": path,
                "status_code": status_code,
                "governance_receipt": governed,
                "semantic_verdict_receipt": receipt,
            }
        )
        if not accepted or not verdict["reached"]:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "BLOCKED",
                "established": False,
                "steps": plan,
                "receipts": receipts,
                "governed_write_steps": governed_write_steps,
                "reason_code": verdict["reason_code"],
                "detail": verdict["detail"],
            }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETED",
        "established": True,
        "steps": plan,
        "receipts": receipts,
        "governed_write_steps": governed_write_steps,
        "runtime_bindings": runtime_bindings,
        "identity_bindings": identity_bindings,
        "reason_code": "",
        "detail": "",
    }
