"""Frozen data requirements for one executable experiment flow.

This module does not discover fixtures or execute setup requests. It runs after
protocol compilation, when precondition/control/treatment steps and the binding
and fixture dependency plans already exist. Its only job is to freeze which
runtime values every step requires and which existing authority must provide
them.

Authority:
- planning: ``binding_plan`` plus prior step output bindings;
- dependency order: ``fixture_dependency_dag``;
- execution: ``experiment_fixture_materializer_core``.

The older Disposable Fixture Contract is retained only as a compatibility
projection. It may describe one create fixture but cannot claim that an entire
multi-step flow is data-ready.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from .real_id_resolver import infer_path_params, normalize_path_placeholders


SCHEMA_VERSION = "qualibug.flow-data-requirement.v1"
STATUS_FROZEN = "FROZEN"
STATUS_BLOCKED = "BLOCKED"
BLOCKED_FLOW_DATA_BINDING_INCOMPLETE = "BLOCKED_FLOW_DATA_BINDING_INCOMPLETE"
BLOCKED_FLOW_DATA_BINDING_AMBIGUOUS = "BLOCKED_FLOW_DATA_BINDING_AMBIGUOUS"

_TOKEN_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): dict(row)
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _binding_rows(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    raw = experiment.get("binding_plan")
    if isinstance(raw, dict):
        rows = []
        for key, value in raw.items():
            row = dict(value) if isinstance(value, dict) else {}
            row.setdefault("target", _text(key))
            rows.append(row)
        return rows
    return [dict(row) for row in _list(raw) if isinstance(row, dict)]


def _binding_target(row: dict[str, Any]) -> str:
    return _text(
        row.get("target")
        or row.get("binding_target")
        or row.get("template_token")
        or row.get("name")
    )


def _body_tokens(value: Any) -> list[str]:
    result: list[str] = []

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
        if match:
            token = _text(match.group(1))
            if token and token not in result:
                result.append(token)

    walk(value)
    return result


def _declared_input_targets(step: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for raw in _list(step.get("input_binding_refs")):
        value = _text(
            raw.get("target")
            or raw.get("binding_target")
            or raw.get("name")
            if isinstance(raw, dict)
            else raw
        )
        if value and value not in targets:
            targets.append(value)
    return targets


def _identity_alias_targets(
    steps: list[dict[str, Any]],
    binding_targets: set[str],
) -> set[str]:
    """Expand identity bindings into the token spellings they satisfy.

    A subject-establishment step (money_precondition_chain) captures the
    created entity identity into the request reference field (``orderId``)
    and declares ``identity_binding_aliases`` covering the entity's own
    identity field spellings (``id``). Downstream steps address the same
    entity through either spelling — a state-advancement step's path
    ``/api/orders/{id}/cancel`` names the identity field directly. Such a
    token is satisfiable whenever the step's primary identity target is
    already bound, so the freeze check must not report it unresolved.
    """
    alias_targets: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        aliases = [
            _text(value)
            for value in _list(step.get("identity_binding_aliases"))
            if _text(value)
        ]
        if not aliases:
            continue
        primary = _text(
            step.get("identity_binding_target")
            or (
                _list(step.get("identity_binding_targets"))[0]
                if _list(step.get("identity_binding_targets"))
                else ""
            )
        )
        if not primary or primary in binding_targets:
            alias_targets.update(aliases)
    return alias_targets


def _identity_output_binding(step: dict[str, Any]) -> dict[str, Any]:
    return _dict(step.get("identity_output_binding"))


def _identity_output_receipt(
    step: dict[str, Any],
    *,
    phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    binding = _identity_output_binding(step)
    if not binding:
        return {}, {}
    step_id = _text(step.get("step_id") or step.get("id"))
    source_field = _text(binding.get("source_identity_field"))
    source_path = _text(binding.get("source_path"))
    targets = list(
        dict.fromkeys(
            _text(value)
            for value in [
                *_list(binding.get("consumer_targets")),
                *_list(binding.get("alias_targets")),
            ]
            if _text(value)
        )
    )
    authority = _text(binding.get("source_authority"))
    status = _text(binding.get("status"))
    if not (
        status == STATUS_FROZEN
        and source_field
        and source_path
        and targets
        and authority
    ):
        return {}, {
            "phase": phase,
            "step_id": step_id,
            "kind": "IDENTITY_OUTPUT_BINDING_INCOMPLETE",
            "source_identity_field": source_field,
            "source_path": source_path,
            "targets": targets,
            "source_authority": authority,
            "status": status,
        }
    return {
        "producer_step_id": step_id,
        "entity_ref": _text(binding.get("entity_ref")),
        "source_identity_field": source_field,
        "source_path": source_path,
        "produced_targets": targets,
        "source_authority": authority,
        "status": STATUS_FROZEN,
    }, {}


def _declared_output_targets(step: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for key in ("output_binding_specs", "output_bindings", "produces_bindings"):
        for raw in _list(step.get(key)):
            value = _text(
                raw.get("target")
                or raw.get("binding_target")
                or raw.get("name")
                or raw.get("template_token")
                if isinstance(raw, dict)
                else raw
            )
            if value and value not in targets:
                targets.append(value)
    identity_output = _identity_output_binding(step)
    if _text(identity_output.get("status")) == STATUS_FROZEN:
        for raw in [
            *_list(identity_output.get("consumer_targets")),
            *_list(identity_output.get("alias_targets")),
        ]:
            value = _text(raw)
            if value and value not in targets:
                targets.append(value)
    return targets


def _step_body(step: dict[str, Any], operation: dict[str, Any]) -> Any:
    if "body" in step:
        return step.get("body")
    return operation.get("request_example")


def _step_requirement(
    *,
    phase: str,
    step: dict[str, Any],
    operation: dict[str, Any],
    available_before: set[str],
    binding_targets: set[str],
) -> tuple[dict[str, Any], list[str]]:
    step_id = _text(step.get("step_id") or step.get("id"))
    operation_ref = _text(step.get("operation_ref"))
    method = _text(step.get("method") or operation.get("method")).upper()
    path_template = normalize_path_placeholders(
        _text(
            step.get("path")
            or step.get("path_template")
            or operation.get("path")
            or operation.get("raw_path")
            or operation.get("path_template")
        )
    )
    path_targets = [
        value for value in infer_path_params(path_template) if _text(value)
    ]
    body_targets = _body_tokens(_step_body(step, operation))
    declared_inputs = _declared_input_targets(step)
    required_targets = list(
        dict.fromkeys([*path_targets, *body_targets, *declared_inputs])
    )
    produced_targets = _declared_output_targets(step)
    unresolved = [
        target
        for target in required_targets
        if target not in binding_targets
        and target not in available_before
    ]
    requirement = {
        "phase": phase,
        "step_id": step_id,
        "operation_ref": operation_ref,
        "method": method,
        "path_template": path_template,
        "entity_refs": [
            _text(value)
            for value in _list(operation.get("entity_refs"))
            if _text(value)
        ],
        "required_binding_targets": required_targets,
        "path_binding_targets": path_targets,
        "body_binding_targets": body_targets,
        "declared_input_binding_targets": declared_inputs,
        "produced_binding_targets": produced_targets,
        "requires_write_recovery": method in _WRITE_METHODS,
        "materialized_before_step": sorted(
            target
            for target in required_targets
            if target in binding_targets or target in available_before
        ),
        "unresolved_binding_targets": unresolved,
    }
    return requirement, unresolved


def _legacy_projection(
    experiment: dict[str, Any],
    *,
    required_operation_refs: list[str],
    required_entity_refs: list[str],
) -> dict[str, Any]:
    contract = deepcopy(_dict(experiment.get("disposable_fixture_contract")))
    if not contract:
        return {
            "present": False,
            "projection_only": True,
            "coverage_status": "NOT_PRESENT",
            "covered_operation_refs": [],
            "covered_entity_refs": [],
        }
    create_ref = _text(
        contract.get("create_operation_ref")
        or contract.get("create_operation_id")
        or _dict(contract.get("create_operation")).get("operation_ref")
    )
    entity_ref = _text(
        contract.get("entity_ref")
        or contract.get("entity_id")
        or _dict(contract.get("entity")).get("entity_ref")
    )
    covered_operations = [
        create_ref if create_ref in required_operation_refs else ""
    ]
    covered_entities = [
        entity_ref if entity_ref in required_entity_refs else ""
    ]
    covered_operations = [value for value in covered_operations if value]
    covered_entities = [value for value in covered_entities if value]
    fully_covers = bool(required_operation_refs) and set(required_operation_refs).issubset(
        set(covered_operations)
    )
    return {
        "present": True,
        "projection_only": True,
        "fixture_id": _text(contract.get("fixture_id")),
        "covered_operation_refs": covered_operations,
        "covered_entity_refs": covered_entities,
        "coverage_status": "FULL" if fully_covers else "PARTIAL",
        "authority": "flow_data_requirement",
    }


def build_flow_data_requirement(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze exact data dependencies across every executable flow step."""
    source = _dict(experiment)
    operations = _operation_index(behavior_ir)
    bindings = _binding_rows(source)
    binding_by_target: dict[str, list[dict[str, Any]]] = {}
    for row in bindings:
        target = _binding_target(row)
        if target and not target.startswith("actor:"):
            binding_by_target.setdefault(target, []).append(row)

    ambiguous_targets = sorted(
        target
        for target, rows in binding_by_target.items()
        if len({_fingerprint(row) for row in rows}) > 1
    )
    if ambiguous_targets:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": BLOCKED_FLOW_DATA_BINDING_AMBIGUOUS,
            "detail": "binding_targets=" + ",".join(ambiguous_targets),
        }

    binding_targets = set(binding_by_target)
    available_before: set[str] = set()
    step_requirements: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    required_operation_refs: list[str] = []
    required_entity_refs: list[str] = []
    identity_output_binding_receipts: list[dict[str, Any]] = []
    identity_output_issues: list[dict[str, Any]] = []

    for phase in ("precondition", "control", "treatment"):
        for raw in _list(source.get(f"{phase}_plan")):
            if not isinstance(raw, dict):
                continue
            step = dict(raw)
            operation_ref = _text(step.get("operation_ref"))
            operation = _dict(operations.get(operation_ref))
            identity_receipt, identity_issue = _identity_output_receipt(
                step,
                phase=phase,
            )
            if identity_receipt:
                identity_output_binding_receipts.append(identity_receipt)
            if identity_issue:
                identity_output_issues.append(identity_issue)
            requirement, unresolved = _step_requirement(
                phase=phase,
                step=step,
                operation=operation,
                available_before=available_before,
                binding_targets=binding_targets,
            )
            step_requirements.append(requirement)
            if operation_ref and operation_ref not in required_operation_refs:
                required_operation_refs.append(operation_ref)
            for entity_ref in requirement["entity_refs"]:
                if entity_ref and entity_ref not in required_entity_refs:
                    required_entity_refs.append(entity_ref)
            if unresolved:
                unresolved_rows.append(
                    {
                        "phase": phase,
                        "step_id": requirement["step_id"],
                        "targets": unresolved,
                    }
                )
            available_before.update(requirement["produced_binding_targets"])

    if identity_output_issues:
        detail = ";".join(
            f"{row['phase']}:{row['step_id']}:{row['kind']}"
            for row in identity_output_issues
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": BLOCKED_FLOW_DATA_BINDING_INCOMPLETE,
            "detail": detail,
            "identity_output_binding_issues": identity_output_issues,
        }

    if unresolved_rows:
        detail = ";".join(
            f"{row['phase']}:{row['step_id']}:{','.join(row['targets'])}"
            for row in unresolved_rows
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": BLOCKED_FLOW_DATA_BINDING_INCOMPLETE,
            "detail": detail,
            "unresolved_steps": unresolved_rows,
        }

    fixture_dag = _dict(source.get("fixture_dependency_dag"))
    materialized_before_measurement = sorted(
        {
            target
            for row in step_requirements
            for target in row["required_binding_targets"]
            if target in binding_targets
        }
    )
    payload = {
        "required_operation_refs": required_operation_refs,
        "required_entity_refs": required_entity_refs,
        "binding_targets": sorted(binding_targets),
        "materialized_before_measurement_targets": materialized_before_measurement,
        "step_requirements": step_requirements,
        "identity_output_binding_receipts": identity_output_binding_receipts,
        "fixture_dependency_dag_fingerprint": _text(
            fixture_dag.get("fixture_dependency_dag_fingerprint")
            or fixture_dag.get("fingerprint")
            or fixture_dag.get("dag_fingerprint")
        ) or (_fingerprint(fixture_dag) if fixture_dag else ""),
        "materialization_authority": {
            "binding_plan": "binding_plan",
            "dependency_plan": "fixture_dependency_dag",
            "executor": "experiment_fixture_materializer_core",
        },
        "legacy_disposable_fixture_projection": _legacy_projection(
            source,
            required_operation_refs=required_operation_refs,
            required_entity_refs=required_entity_refs,
        ),
    }
    requirement_id = "flow_data_" + _fingerprint(payload)[:20]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_FROZEN,
        "requirement_id": requirement_id,
        "requirement_fingerprint": _fingerprint(payload),
        **payload,
    }


__all__ = [
    "BLOCKED_FLOW_DATA_BINDING_AMBIGUOUS",
    "BLOCKED_FLOW_DATA_BINDING_INCOMPLETE",
    "SCHEMA_VERSION",
    "STATUS_BLOCKED",
    "STATUS_FROZEN",
    "build_flow_data_requirement",
]
