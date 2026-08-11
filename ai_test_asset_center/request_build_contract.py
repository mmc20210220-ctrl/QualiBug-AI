"""Compile-time request-build authority for HTTP experiment steps.

A COMPILED experiment must not reach runtime only to discover a request shape
that was already known to be impossible.  This contract mirrors the existing
step-kernel request gates without inventing values:

* path placeholders require an executable binding/flow materialization channel;
* source-required query parameters must be present and either concrete,
  step-actor scoped, or backed by an executable binding;
* source-required custom headers are blocked because the current transport has
  no arbitrary per-step header channel (Authorization/Accept/Content-Type are
  supplied by existing transport authorities);
* top-level source-required body fields must be present, intentionally removed
  by a declared required-field mutation, or covered by a proven runtime body
  projection;
* every placeholder in a body must have an executable binding channel.

The statuses are ``READY``, ``DEFERRED_RUNTIME`` and ``BLOCKED``.  A runtime
channel is recorded, not treated as a compile-time value.  No defaults, UUIDs,
field-name inference, or synthetic request data are produced here.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .binding_target_materialization_authority import (
    resolve_binding_target_materialization,
)
from .real_id_resolver import infer_path_params, normalize_path_placeholders

SCHEMA_VERSION = "qualibug.request-build-contract.v1"
STATUS_READY = "READY"
STATUS_DEFERRED = "DEFERRED_RUNTIME"
STATUS_BLOCKED = "BLOCKED"
_ACTOR_IDENTITY_REF_PREFIX = "actor_identity_ref:"
_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")
_TRANSPORT_HEADER_AUTHORITIES = {
    "authorization": "actor_bearer_token",
    "accept": "http_transport_default",
    "content-type": "json_body_transport",
    "contenttype": "json_body_transport",
}


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
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _binding_rows(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _dict(experiment).get("binding_plan")
    if isinstance(raw, dict):
        return [
            {**_dict(value), "target": _text(_dict(value).get("target") or key)}
            for key, value in raw.items()
            if isinstance(value, dict)
        ]
    return [dict(row) for row in _list(raw) if isinstance(row, dict)]


def _source_parameters(operation: dict[str, Any], location: str) -> list[dict[str, Any]]:
    wanted = _text(location).lower()
    rows: list[dict[str, Any]] = []
    for key in ("parameters", "request_parameters", "params"):
        for raw in _list(operation.get(key)):
            row = _dict(raw)
            loc = _text(
                row.get("in") or row.get("location") or row.get("parameter_in")
            ).lower()
            name = _text(row.get("name") or row.get("field"))
            if loc == wanted and name:
                rows.append(row)
    # Preserve source order only as presentation; identity is the parameter name.
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(_text(row.get("name") or row.get("field")), row)
    return list(unique.values())


def _required_parameters(operation: dict[str, Any], location: str) -> list[dict[str, Any]]:
    return [
        row
        for row in _source_parameters(operation, location)
        if row.get("required") is True
        or _text(row.get("required")).lower() in {"true", "yes", "1", "required"}
    ]


def _request_schema(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        schema = _dict(media.get("schema")) or schema
    return schema


def _required_body_fields(operation: dict[str, Any]) -> list[str]:
    return [
        _text(value)
        for value in _list(_request_schema(operation).get("required"))
        if _text(value)
    ]


def _body_placeholder_targets(value: Any) -> list[str]:
    found: list[str] = []

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
        match = _PLACEHOLDER_RE.match(node)
        if match:
            target = _text(match.group(1))
            if target and target not in found:
                found.append(target)

    walk(value)
    return found


def _step_body(step: dict[str, Any], operation: dict[str, Any]) -> Any:
    if "body" in step:
        return step.get("body")
    example = operation.get("request_example")
    return example if isinstance(example, dict) else None


def _required_field_removals(step: dict[str, Any]) -> set[str]:
    return {
        _text(value)
        for value in _list(step.get("required_field_removal"))
        if _text(value)
    }


def _observed_body_projection_fields(experiment: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for binding in _binding_rows(experiment):
        source = _text(
            binding.get("source_kind") or binding.get("source_priority")
        ).lower()
        target = _text(binding.get("target"))
        if not (
            target == "__observed_body"
            or "observed" in source and "body" in source
        ):
            continue
        if _text(binding.get("status")).lower() not in {
            "runtime_resolvable",
            "bound",
        }:
            continue
        for field in _list(
            binding.get("body_projection_fields")
            or binding.get("projection_fields")
        ):
            if _text(field):
                fields.add(_text(field))
    return fields


def _target_receipt(
    target: str,
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    return resolve_binding_target_materialization(
        target,
        experiment=experiment,
        behavior_ir=behavior_ir,
        flow_execution_contract=flow_execution_contract,
    )


def _path_contract(
    step: dict[str, Any],
    operation: dict[str, Any],
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    path = normalize_path_placeholders(
        _text(
            step.get("path")
            or operation.get("path")
            or operation.get("raw_path")
        )
    )
    targets = [_text(value) for value in infer_path_params(path) if _text(value)]
    target_receipts: list[dict[str, Any]] = []
    blocked: list[str] = []
    deferred = False
    for target in targets:
        receipt = _target_receipt(
            target,
            experiment=experiment,
            behavior_ir=behavior_ir,
            flow_execution_contract=flow_execution_contract,
        )
        target_receipts.append(receipt)
        if _text(receipt.get("status")) != "RESOLVED":
            blocked.append(target)
        elif _text(receipt.get("authority")) != "sealed_materialized_value":
            deferred = True
    return {
        "component": "path",
        "status": STATUS_BLOCKED if blocked else STATUS_DEFERRED if deferred else STATUS_READY,
        "path_template": path,
        "targets": targets,
        "blocked_targets": blocked,
        "target_receipts": target_receipts,
        "reason_code": "REQUEST_PATH_BINDING_UNPROVEN" if blocked else "",
    }


def _query_contract(
    step: dict[str, Any],
    operation: dict[str, Any],
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    required = _required_parameters(operation, "query")
    query = _dict(step.get("query"))
    rows: list[dict[str, Any]] = []
    blocked = False
    deferred = False
    for parameter in required:
        name = _text(parameter.get("name") or parameter.get("field"))
        value = query.get(name)
        row: dict[str, Any] = {"name": name, "status": STATUS_READY, "authority": "step_query_value"}
        if value in (None, "", [], {}):
            row.update(
                {
                    "status": STATUS_BLOCKED,
                    "reason_code": "REQUEST_REQUIRED_QUERY_MISSING",
                    "authority": "",
                }
            )
            blocked = True
        elif isinstance(value, str) and _text(value).startswith(_ACTOR_IDENTITY_REF_PREFIX):
            row.update(
                {
                    "status": STATUS_DEFERRED,
                    "authority": "step_actor_identity_ref",
                }
            )
            deferred = True
        elif isinstance(value, str) and _PLACEHOLDER_RE.match(value):
            target = _text(_PLACEHOLDER_RE.match(value).group(1))
            receipt = _target_receipt(
                target,
                experiment=experiment,
                behavior_ir=behavior_ir,
                flow_execution_contract=flow_execution_contract,
            )
            row["target"] = target
            row["target_receipt"] = receipt
            if _text(receipt.get("status")) != "RESOLVED":
                row.update(
                    {
                        "status": STATUS_BLOCKED,
                        "reason_code": "REQUEST_REQUIRED_QUERY_BINDING_UNPROVEN",
                        "authority": "",
                    }
                )
                blocked = True
            else:
                row.update(
                    {
                        "status": STATUS_DEFERRED,
                        "authority": _text(receipt.get("authority")),
                    }
                )
                deferred = True
        rows.append(row)
    return {
        "component": "query",
        "status": STATUS_BLOCKED if blocked else STATUS_DEFERRED if deferred else STATUS_READY,
        "required": rows,
        "reason_code": "REQUEST_REQUIRED_QUERY_UNBUILDABLE" if blocked else "",
    }


def _header_contract(operation: dict[str, Any]) -> dict[str, Any]:
    required = _required_parameters(operation, "header")
    rows: list[dict[str, Any]] = []
    blocked = False
    for parameter in required:
        name = _text(parameter.get("name") or parameter.get("field"))
        authority = _TRANSPORT_HEADER_AUTHORITIES.get(name.lower().replace("_", "-"), "")
        if authority:
            rows.append(
                {
                    "name": name,
                    "status": STATUS_DEFERRED,
                    "authority": authority,
                    "reason_code": "",
                }
            )
        else:
            blocked = True
            rows.append(
                {
                    "name": name,
                    "status": STATUS_BLOCKED,
                    "authority": "",
                    "reason_code": "REQUEST_REQUIRED_HEADER_TRANSPORT_UNSUPPORTED",
                }
            )
    return {
        "component": "header",
        "status": STATUS_BLOCKED if blocked else STATUS_DEFERRED if rows else STATUS_READY,
        "required": rows,
        "reason_code": "REQUEST_REQUIRED_HEADER_UNBUILDABLE" if blocked else "",
    }


def _body_contract(
    step: dict[str, Any],
    operation: dict[str, Any],
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    method = _text(operation.get("method")).upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return {"component": "body", "status": STATUS_READY, "required": [], "placeholders": []}

    body = _step_body(step, operation)
    body_dict = body if isinstance(body, dict) else {}
    removals = _required_field_removals(step)
    observed_projection = _observed_body_projection_fields(experiment)
    required_rows: list[dict[str, Any]] = []
    blocked = False
    deferred = False
    for field in _required_body_fields(operation):
        value = body_dict.get(field)
        row: dict[str, Any] = {"field": field, "status": STATUS_READY, "authority": "source_or_step_body"}
        if field in removals:
            row.update(
                {
                    "status": STATUS_READY,
                    "authority": "declared_required_field_removal_mutation",
                    "intentional_absence": True,
                }
            )
        elif value in (None, "", [], {}):
            if field in observed_projection:
                row.update(
                    {
                        "status": STATUS_DEFERRED,
                        "authority": "observed_body_projection",
                    }
                )
                deferred = True
            else:
                row.update(
                    {
                        "status": STATUS_BLOCKED,
                        "authority": "",
                        "reason_code": "REQUEST_REQUIRED_BODY_FIELD_MISSING",
                    }
                )
                blocked = True
        required_rows.append(row)

    placeholder_rows: list[dict[str, Any]] = []
    for target in _body_placeholder_targets(body):
        receipt = _target_receipt(
            target,
            experiment=experiment,
            behavior_ir=behavior_ir,
            flow_execution_contract=flow_execution_contract,
        )
        row = {
            "target": target,
            "target_receipt": receipt,
            "status": STATUS_DEFERRED,
            "authority": _text(receipt.get("authority")),
            "reason_code": "",
        }
        if _text(receipt.get("status")) != "RESOLVED":
            row.update(
                {
                    "status": STATUS_BLOCKED,
                    "authority": "",
                    "reason_code": "REQUEST_BODY_BINDING_UNPROVEN",
                }
            )
            blocked = True
        else:
            deferred = True
        placeholder_rows.append(row)

    return {
        "component": "body",
        "status": STATUS_BLOCKED if blocked else STATUS_DEFERRED if deferred else STATUS_READY,
        "required": required_rows,
        "placeholders": placeholder_rows,
        "observed_projection_fields": sorted(observed_projection),
        "reason_code": "REQUEST_BODY_UNBUILDABLE" if blocked else "",
    }


def build_request_build_contract(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    exp = _dict(experiment)
    operations = _operation_index(behavior_ir)
    step_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    deferred_count = 0

    for phase in ("precondition", "control", "treatment"):
        for raw in _list(exp.get(f"{phase}_plan")):
            step = _dict(raw)
            if not step or _text(step.get("protocol_step")) == "ui_open":
                continue
            step_id = _text(step.get("step_id") or step.get("id"))
            operation_ref = _text(step.get("operation_ref"))
            operation = _dict(operations.get(operation_ref))
            if not operation:
                issue = {
                    "phase": phase,
                    "step_id": step_id,
                    "operation_ref": operation_ref,
                    "status": STATUS_BLOCKED,
                    "reason_code": "REQUEST_OPERATION_IDENTITY_UNRESOLVED",
                }
                issues.append(issue)
                step_rows.append(issue)
                continue

            components = [
                _path_contract(
                    step,
                    operation,
                    experiment=exp,
                    behavior_ir=behavior_ir,
                    flow_execution_contract=flow_execution_contract,
                ),
                _query_contract(
                    step,
                    operation,
                    experiment=exp,
                    behavior_ir=behavior_ir,
                    flow_execution_contract=flow_execution_contract,
                ),
                _header_contract(operation),
                _body_contract(
                    step,
                    operation,
                    experiment=exp,
                    behavior_ir=behavior_ir,
                    flow_execution_contract=flow_execution_contract,
                ),
            ]
            blocked_components = [
                row for row in components if _text(row.get("status")) == STATUS_BLOCKED
            ]
            deferred_components = [
                row for row in components if _text(row.get("status")) == STATUS_DEFERRED
            ]
            deferred_count += len(deferred_components)
            row = {
                "phase": phase,
                "step_id": step_id,
                "operation_ref": operation_ref,
                "method": _text(operation.get("method")).upper(),
                "status": STATUS_BLOCKED if blocked_components else STATUS_DEFERRED if deferred_components else STATUS_READY,
                "components": components,
                "blocked_components": [
                    _text(item.get("component")) for item in blocked_components
                ],
                "deferred_components": [
                    _text(item.get("component")) for item in deferred_components
                ],
            }
            step_rows.append(row)
            if blocked_components:
                issues.append(row)

    semantic = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": _text(exp.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id")),
        "status": STATUS_BLOCKED if issues else STATUS_DEFERRED if deferred_count else STATUS_READY,
        "steps": step_rows,
        "issue_count": len(issues),
        "deferred_component_count": deferred_count,
        "source_order_selection_allowed": False,
        "synthetic_request_values_allowed": False,
    }
    semantic["contract_fingerprint"] = _fingerprint(semantic)
    return semantic


def validate_request_build_contract(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild and compare a stored contract at runtime for artifact drift."""

    stored = _dict(_dict(experiment).get("request_build_contract"))
    flow = _dict(_dict(experiment).get("flow_data_execution_contract"))
    if not stored:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": "REQUEST_BUILD_CONTRACT_MISSING",
            "stored_fingerprint": "",
            "current_fingerprint": "",
        }
    current = build_request_build_contract(
        experiment,
        behavior_ir=behavior_ir,
        flow_execution_contract=flow,
    )
    stored_fp = _text(stored.get("contract_fingerprint"))
    current_fp = _text(current.get("contract_fingerprint"))
    if stored_fp != current_fp:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": "REQUEST_BUILD_CONTRACT_DRIFT",
            "stored_fingerprint": stored_fp,
            "current_fingerprint": current_fp,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": _text(current.get("status")),
        "reason_code": "" if _text(current.get("status")) != STATUS_BLOCKED else "REQUEST_BUILD_CONTRACT_BLOCKED",
        "stored_fingerprint": stored_fp,
        "current_fingerprint": current_fp,
    }


__all__ = [
    "SCHEMA_VERSION",
    "STATUS_READY",
    "STATUS_DEFERRED",
    "STATUS_BLOCKED",
    "build_request_build_contract",
    "validate_request_build_contract",
]
