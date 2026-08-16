"""Flow-data execution contract facade with producer-identity authority.

The established executor-capability proof lives in
``_flow_data_execution_contract_mechanics``. This boundary prevents set/dict
flattening from turning ambiguous or invalid producers into apparently available
flow values.

Before the historical contract can be FROZEN it now proves:
* step ids and graph node ids are unique;
* one canonical output field in one step has one response source path;
* one consumer target has one producer/output identity;
* precondition identity-output contracts are themselves FROZEN and complete;
* if several earlier identity-output steps can produce one required target, the
  consuming step explicitly selects one producer through identity_input_binding.
"""
from __future__ import annotations

from typing import Any

from . import _flow_data_execution_contract_mechanics as _core

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)

_original_freeze_flow_data_execution_contract = _core.freeze_flow_data_execution_contract


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_identity_output(value: Any) -> tuple[bool, list[str]]:
    row = _dict(value)
    targets = sorted(
        {
            _text(item)
            for item in [
                *_list(row.get("consumer_targets")),
                *_list(row.get("alias_targets")),
            ]
            if _text(item)
        }
    )
    valid = bool(
        _text(row.get("schema_version")) == "qualibug.identity-output-binding.v1"
        and _text(row.get("status")).upper() == _core.STATUS_FROZEN
        and _text(row.get("source_identity_field"))
        and _text(row.get("source_path"))
        and _text(row.get("source_authority"))
        and targets
    )
    return valid, targets


def _ambiguity_issues(
    experiment: dict[str, Any],
    requirement: dict[str, Any],
) -> list[dict[str, Any]]:
    exp = _dict(experiment)
    req = _dict(requirement)
    issues: list[dict[str, Any]] = []
    steps = _core._steps(exp)

    # Step identity is part of every producer/input reference. Duplicate ids make
    # the historical produced_by_step dict source-order dependent.
    step_locations: dict[str, list[str]] = {}
    for phase, step in steps:
        step_id = _text(step.get("step_id") or step.get("id"))
        if step_id:
            step_locations.setdefault(step_id, []).append(phase)
    for step_id, phases in sorted(step_locations.items()):
        if len(phases) > 1:
            issues.append(
                {
                    "kind": "STEP_IDENTITY_AMBIGUOUS",
                    "step_id": step_id,
                    "phases": list(phases),
                }
            )

    requirements_by_step = {
        _text(row.get("step_id")): _dict(row)
        for row in _list(req.get("step_requirements"))
        if isinstance(row, dict) and _text(row.get("step_id"))
    }
    # Identity producers are namespaced by the entity they establish. Two
    # different entities (address, order) each emit the bare alias ``id``; a
    # consumer scoped to the order entity must resolve only order's producer,
    # never be reported ambiguous against the address entity's id.
    prior_identity_producers: dict[tuple[str, str], set[str]] = {}

    for phase, step in steps:
        step_id = _text(step.get("step_id") or step.get("id"))
        graph = _dict(step.get("_execution_graph"))

        node_counts: dict[str, int] = {}
        for raw_node in _list(graph.get("nodes")):
            node = _dict(raw_node)
            node_id = _text(node.get("node_id") or node.get("step_id"))
            if node_id:
                node_counts[node_id] = node_counts.get(node_id, 0) + 1
        duplicate_nodes = sorted(
            node_id for node_id, count in node_counts.items() if count > 1
        )
        if duplicate_nodes:
            issues.append(
                {
                    "kind": "GRAPH_NODE_IDENTITY_AMBIGUOUS",
                    "phase": phase,
                    "step_id": step_id,
                    "node_ids": duplicate_nodes,
                }
            )

        output_sources: dict[str, set[str]] = {}
        for spec in _core._output_specs(step):
            canonical, source_path = _core._output_identity(_dict(spec))
            if canonical:
                output_sources.setdefault(canonical, set()).add(source_path)
        for canonical, source_paths in sorted(output_sources.items()):
            nonempty = {path for path in source_paths if path}
            if len(nonempty) > 1:
                issues.append(
                    {
                        "kind": "OUTPUT_BINDING_SOURCE_AMBIGUOUS",
                        "phase": phase,
                        "step_id": step_id,
                        "canonical_field_id": canonical,
                        "source_paths": sorted(nonempty),
                    }
                )

        input_producers: dict[str, set[tuple[str, str]]] = {}
        for ref in _core._input_refs(step):
            producer, source_field, target = _core._input_identity(_dict(ref))
            if target:
                input_producers.setdefault(target, set()).add(
                    (producer, source_field)
                )
        for target, producers in sorted(input_producers.items()):
            concrete = {
                pair for pair in producers if pair[0] and pair[1]
            }
            if len(concrete) > 1:
                issues.append(
                    {
                        "kind": "INPUT_BINDING_PRODUCER_AMBIGUOUS",
                        "phase": phase,
                        "step_id": step_id,
                        "consumer_target": target,
                        "producers": [
                            {
                                "producer_node_id": producer,
                                "producer_output_field": field,
                            }
                            for producer, field in sorted(concrete)
                        ],
                    }
                )

        requirement_row = _dict(requirements_by_step.get(step_id))
        required_targets = {
            _text(value)
            for value in _list(requirement_row.get("required_binding_targets"))
            if _text(value)
        }
        required_targets.update(_core._tokens(step.get("query")))
        identity_input = _dict(step.get("identity_input_binding"))
        selected_producer = _text(identity_input.get("producer_step_id"))
        selected_targets = {
            _text(value)
            for value in _list(identity_input.get("consumer_targets"))
            if _text(value)
        }
        for target in sorted(required_targets):
            # A consuming step scopes its identity consumption with
            # ``subject_entity_ref`` (the entity whose identity field its path/
            # body placeholder names). Scoped, it resolves within that entity's
            # namespace; unscoped, every entity's producer for the bare target is
            # a candidate, so cross-entity collisions stay ambiguous (fail closed).
            consumer_entity_ref = _text(step.get("subject_entity_ref"))
            if consumer_entity_ref:
                producers = set(
                    prior_identity_producers.get((consumer_entity_ref, target))
                    or set()
                )
            else:
                producers = set()
                for (entity_ref, candidate_target), step_ids in (
                    prior_identity_producers.items()
                ):
                    if candidate_target == target:
                        producers.update(step_ids)
            if len(producers) <= 1:
                continue
            if not (
                target in selected_targets
                and selected_producer in producers
                and _text(identity_input.get("status")).upper()
                == _core.STATUS_FROZEN
            ):
                issues.append(
                    {
                        "kind": "SEQUENTIAL_IDENTITY_PRODUCER_AMBIGUOUS",
                        "phase": phase,
                        "step_id": step_id,
                        "consumer_target": target,
                        "producer_step_ids": sorted(producers),
                    }
                )

        identity_output = _dict(step.get("identity_output_binding"))
        if identity_output:
            valid_output, output_targets = _valid_identity_output(identity_output)
            if not valid_output:
                issues.append(
                    {
                        "kind": "IDENTITY_OUTPUT_CONTRACT_INVALID",
                        "phase": phase,
                        "step_id": step_id,
                        "targets": output_targets,
                    }
                )
            if phase == "precondition" and valid_output:
                entity_ref = _text(identity_output.get("entity_ref"))
                for target in output_targets:
                    prior_identity_producers.setdefault(
                        (entity_ref, target), set()
                    ).add(step_id)

    return issues


def freeze_flow_data_execution_contract(
    experiment: dict[str, Any],
    requirement: dict[str, Any],
) -> dict[str, Any]:
    base = _original_freeze_flow_data_execution_contract(experiment, requirement)
    authority_issues = _ambiguity_issues(experiment, requirement)
    if not authority_issues:
        return base

    combined = [
        dict(row)
        for row in _list(_dict(base).get("issues"))
        if isinstance(row, dict)
    ]
    combined.extend(authority_issues)
    output = dict(_dict(base))
    output.update(
        {
            "schema_version": _core.SCHEMA_VERSION,
            "status": _core.STATUS_BLOCKED,
            "reason_code": _core.BLOCKED_FLOW_DATA_EXECUTION_CONTRACT_INCOMPLETE,
            "detail": ";".join(
                f"{_text(row.get('kind'))}:{_text(row.get('step_id'))}"
                for row in combined[:12]
            ),
            "issues": combined,
            "producer_identity_authority": {
                "status": "BLOCKED",
                "source_order_selection_allowed": False,
                "ambiguity_issue_count": len(authority_issues),
            },
        }
    )
    output["contract_fingerprint"] = _core._fingerprint(
        {
            "base_contract_fingerprint": _text(base.get("contract_fingerprint")),
            "issues": combined,
            "producer_identity_authority": output[
                "producer_identity_authority"
            ],
        }
    )
    return output


_core.freeze_flow_data_execution_contract = freeze_flow_data_execution_contract

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "freeze_flow_data_execution_contract",
        "_ambiguity_issues",
    }
)
