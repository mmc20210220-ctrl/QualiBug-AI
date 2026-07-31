"""Finalize source-backed write safety for process execution graphs.

The process graph remains the topology authority. This module joins that graph
with the existing Behavior IR operation authority, declared effect observers,
explicit compensation relations, cleanup coverage validation, and the existing
WriteReversibilityProof.

The executable scope is intentionally narrow: every graph write must have one
unique source-declared readback operation and a distinct, source-declared
compensator connected by a formal compensates/inverse relation. Same-operation
snapshot/delta restoration remains blocked until those authorities share one
mode vocabulary. No target, method, path, identity, field, body, or cleanup
scope is guessed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .cleanup_plan_validator import validate_cleanup_plan
from .real_id_resolver import infer_path_params, normalize_path_placeholders
from .runtime_binding_graph import declared_effect_observers

GRAPH_WRITE_CONTRACT_SCHEMA = "qualibug.process-graph-write-contract.v1"
GRAPH_WRITE_CONTRACT_INVALID = "BLOCKED_INVALID_CLEANUP_PLAN"
GRAPH_WRITE_OBSERVER_UNRESOLVED = "BLOCKED_MISSING_OBSERVER"
GRAPH_WRITE_COMPENSATION_UNRESOLVED = "BLOCKED_NON_REVERSIBLE_WRITE"

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_RELATION_KINDS = frozenset({"compensates", "inverse", "compensation"})


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


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _extract_graph(experiment: dict[str, Any]) -> tuple[dict[str, Any], str]:
    graphs = [
        _dict(step.get("_execution_graph"))
        for step in _list(experiment.get("treatment_plan"))
        if isinstance(step, dict) and _dict(step.get("_execution_graph"))
    ]
    if not graphs:
        return {}, ""
    if len({_fingerprint(graph) for graph in graphs}) != 1:
        return {}, "treatment_steps_carry_different_execution_graphs"
    return deepcopy(graphs[0]), ""


def _blocked(
    experiment: dict[str, Any], reason_code: str, detail: str
) -> dict[str, Any]:
    result = deepcopy(experiment)
    result["control_plan"] = []
    result["treatment_plan"] = []
    result["cleanup_plan"] = []
    result["compile_receipt"] = {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
    }
    return result


def _observer_contract(
    *,
    operation_ref: str,
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    candidates = declared_effect_observers(
        operation,
        behavior_ir=behavior_ir,
        max_candidates=3,
    )
    unique = {
        (
            _text(row.get("operation_ref")),
            _text(row.get("method")).upper(),
            normalize_path_placeholders(_text(row.get("path"))),
        )
        for row in candidates
        if isinstance(row, dict)
        and _text(row.get("operation_ref"))
        and _text(row.get("method")).upper() in {"GET", "HEAD"}
        and normalize_path_placeholders(_text(row.get("path"))).startswith("/")
    }
    if len(unique) != 1:
        return {}, f"{operation_ref}:effect_observer_count:{len(unique)}"
    observer_ref, method, path = next(iter(unique))
    return {
        "operation_ref": observer_ref,
        "method": method,
        "path": path,
        "source_declared": True,
    }, ""


def _relation_matches(
    relation: dict[str, Any],
    *,
    write_ref: str,
    compensator_ref: str,
) -> bool:
    kind = _text(relation.get("kind") or relation.get("relation_type")).lower()
    if kind not in _RELATION_KINDS:
        return False
    pairs = {
        (
            _text(relation.get("source") or relation.get("from")),
            _text(relation.get("target") or relation.get("to")),
        ),
        (
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
        ),
        (
            _text(relation.get("source_operation_ref")),
            _text(relation.get("target_operation_ref")),
        ),
        (
            _text(relation.get("operation_ref")),
            _text(relation.get("to_ref") or relation.get("to")),
        ),
    }
    return (compensator_ref, write_ref) in pairs or (
        write_ref,
        compensator_ref,
    ) in pairs


def _compensation_relation(
    behavior_ir: dict[str, Any],
    *,
    write_ref: str,
    compensator_ref: str,
) -> dict[str, Any]:
    matches = [
        dict(row)
        for row in _list(_dict(behavior_ir).get("relations"))
        if isinstance(row, dict)
        and _relation_matches(
            row,
            write_ref=write_ref,
            compensator_ref=compensator_ref,
        )
    ]
    return matches[0] if len(matches) == 1 else {}


def _output_specs(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _list(node.get("output_binding_specs")):
        if not isinstance(row, dict):
            continue
        canonical = _text(
            row.get("canonical_field_id")
            or row.get("output_field")
            or row.get("field")
        )
        source_path = _text(
            row.get("json_path")
            or row.get("source_path")
            or row.get("response_field")
        )
        if canonical and source_path:
            result[canonical] = {
                "canonical_field_id": canonical,
                "source_path": source_path,
                "source": "write_response",
            }
    return result


def _cleanup_binding_specs(
    *,
    node: dict[str, Any],
    cleanup_path: str,
) -> tuple[list[dict[str, Any]], str]:
    explicit = [
        dict(row)
        for row in _list(node.get("cleanup_binding_specs"))
        if isinstance(row, dict)
    ]
    placeholders = infer_path_params(cleanup_path)
    if not placeholders:
        return explicit, ""

    by_target = {
        _text(row.get("target") or row.get("target_field")): row
        for row in explicit
        if _text(row.get("target") or row.get("target_field"))
    }
    outputs = _output_specs(node)
    specs: list[dict[str, Any]] = []
    missing: list[str] = []
    for target in placeholders:
        declared = _dict(by_target.get(target))
        if declared:
            source = _text(declared.get("source")) or "write_response"
            source_path = _text(
                declared.get("source_path") or declared.get("json_path")
            )
            source_field = _text(
                declared.get("canonical_field_id")
                or declared.get("source_field")
            )
            if source not in {
                "write_response",
                "original_request",
                "runtime_binding",
            }:
                missing.append(target)
                continue
            if source == "runtime_binding":
                source_field = source_field or target
            elif not source_path and source_field in outputs:
                source_path = _text(outputs[source_field].get("source_path"))
            if source in {"write_response", "original_request"} and not source_path:
                missing.append(target)
                continue
            specs.append(
                {
                    "target": target,
                    "source": source,
                    "source_path": source_path,
                    "canonical_field_id": source_field or target,
                }
            )
            continue

        output = _dict(outputs.get(target))
        if not output:
            missing.append(target)
            continue
        specs.append({"target": target, **output})

    if missing:
        return [], "cleanup_path_bindings_unresolved:" + ",".join(sorted(missing))
    return specs, ""


def _operation_transport(
    node: dict[str, Any], operation: dict[str, Any]
) -> tuple[str, str]:
    method = _text(node.get("method") or operation.get("method")).upper()
    path = normalize_path_placeholders(
        _text(
            node.get("path")
            or operation.get("path")
            or operation.get("raw_path")
            or operation.get("path_template")
        )
    )
    return method, path


def _cleanup_row(
    *,
    node_id: str,
    node: dict[str, Any],
    operation_ref: str,
    compensator_ref: str,
    compensator: dict[str, Any],
    observer: dict[str, Any],
    binding_specs: list[dict[str, Any]],
    relation: dict[str, Any],
) -> dict[str, Any]:
    method = _text(compensator.get("method")).upper()
    path = normalize_path_placeholders(
        _text(
            compensator.get("path")
            or compensator.get("raw_path")
            or compensator.get("path_template")
        )
    )
    return {
        "step_id": f"cleanup_{node_id}",
        "source_step_id": node_id,
        "source_operation_ref": operation_ref,
        "compensates_operation_ref": operation_ref,
        "operation_ref": compensator_ref,
        "actor_ref": _text(node.get("actor_ref")),
        "system_ref": _text(node.get("system_ref")),
        "method": method,
        "path": path,
        "action": "source_declared_compensation",
        "mode": "compensating_transition",
        "source_declared": True,
        "binding_specs": deepcopy(binding_specs),
        "observer_operation": deepcopy(observer),
        "body": deepcopy(node.get("compensation_body")),
        "body_from_original_request": (
            node.get("compensation_body_from_original_request") is True
        ),
        "authority_relation_ref": _text(relation.get("id")) or (
            f"relation:{operation_ref}:{compensator_ref}"
        ),
    }


def _canonicalize_graph(
    graph: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    operations = _operation_index(behavior_ir)
    nodes = [
        dict(row)
        for row in _list(graph.get("nodes"))
        if isinstance(row, dict)
    ]
    order = [
        _text(value)
        for value in _list(graph.get("topological_order"))
        if _text(value)
    ]
    by_id = {
        _text(row.get("node_id") or row.get("step_id")): row
        for row in nodes
        if _text(row.get("node_id") or row.get("step_id"))
    }
    if not nodes or set(order) != set(by_id) or len(order) != len(by_id):
        return {}, {}, GRAPH_WRITE_CONTRACT_INVALID, "graph_order_identity_mismatch"

    write_contracts: dict[str, dict[str, Any]] = {}
    cleanup_by_source: dict[str, dict[str, Any]] = {}
    for node_id in order:
        node = by_id[node_id]
        operation_ref = _text(node.get("operation_ref"))
        operation = _dict(operations.get(operation_ref))
        if not operation:
            return (
                {},
                {},
                GRAPH_WRITE_CONTRACT_INVALID,
                f"{node_id}:operation_not_in_ir:{operation_ref}",
            )
        method, path = _operation_transport(node, operation)
        if not method or not path.startswith("/"):
            return (
                {},
                {},
                GRAPH_WRITE_CONTRACT_INVALID,
                f"{node_id}:operation_transport_unresolved",
            )
        node["method"] = method
        node["path"] = path
        if method not in _WRITE_METHODS:
            continue

        observer, detail = _observer_contract(
            operation_ref=operation_ref,
            operation=operation,
            behavior_ir=behavior_ir,
        )
        if detail:
            return {}, {}, GRAPH_WRITE_OBSERVER_UNRESOLVED, f"{node_id}:{detail}"

        compensator_ref = _text(node.get("compensation_operation_ref"))
        compensator = _dict(operations.get(compensator_ref))
        if not compensator_ref or not compensator:
            return (
                {},
                {},
                GRAPH_WRITE_COMPENSATION_UNRESOLVED,
                f"{node_id}:compensation_operation_unresolved",
            )
        if compensator_ref == operation_ref:
            return (
                {},
                {},
                GRAPH_WRITE_COMPENSATION_UNRESOLVED,
                f"{node_id}:same_operation_restore_not_supported_by_graph_contract",
            )
        compensation_method, compensation_path = _operation_transport({}, compensator)
        if (
            compensation_method not in _WRITE_METHODS
            or not compensation_path.startswith("/")
        ):
            return (
                {},
                {},
                GRAPH_WRITE_COMPENSATION_UNRESOLVED,
                f"{node_id}:compensation_transport_unresolved:{compensator_ref}",
            )

        relation = _compensation_relation(
            behavior_ir,
            write_ref=operation_ref,
            compensator_ref=compensator_ref,
        )
        if not relation:
            return (
                {},
                {},
                GRAPH_WRITE_COMPENSATION_UNRESOLVED,
                f"{node_id}:explicit_compensator_relation_unresolved:{operation_ref}:{compensator_ref}",
            )

        system_ref = _text(node.get("system_ref"))
        compensation_system = _text(
            compensator.get("system_ref")
            or compensator.get("target_system_ref")
            or compensator.get("approved_target_ref")
        )
        if compensation_system and compensation_system != system_ref:
            return (
                {},
                {},
                GRAPH_WRITE_COMPENSATION_UNRESOLVED,
                f"{node_id}:compensation_system_mismatch:{system_ref}:{compensation_system}",
            )

        binding_specs, detail = _cleanup_binding_specs(
            node=node,
            cleanup_path=compensation_path,
        )
        if detail:
            return (
                {},
                {},
                GRAPH_WRITE_COMPENSATION_UNRESOLVED,
                f"{node_id}:{detail}",
            )

        cleanup = _cleanup_row(
            node_id=node_id,
            node=node,
            operation_ref=operation_ref,
            compensator_ref=compensator_ref,
            compensator=compensator,
            observer=observer,
            binding_specs=binding_specs,
            relation=relation,
        )
        contract = {
            "source_step_id": node_id,
            "operation_ref": operation_ref,
            "actor_ref": _text(node.get("actor_ref")),
            "system_ref": system_ref,
            "method": method,
            "path": path,
            "effect_observer": deepcopy(observer),
            "cleanup_step_id": cleanup["step_id"],
            "compensation_operation_ref": compensator_ref,
            "authority_relation_ref": cleanup["authority_relation_ref"],
        }
        write_contracts[node_id] = contract
        cleanup_by_source[node_id] = cleanup
        node["write_contract"] = deepcopy(contract)
        node["effect_observer_operations"] = [deepcopy(observer)]

    cleanup_steps = [
        cleanup_by_source[node_id]
        for node_id in reversed(order)
        if node_id in cleanup_by_source
    ]
    canonical_graph = deepcopy(graph)
    canonical_graph["nodes"] = [by_id[node_id] for node_id in order]
    canonical_graph["write_contracts_by_node"] = deepcopy(write_contracts)
    contract = {
        "schema_version": GRAPH_WRITE_CONTRACT_SCHEMA,
        "execution_graph_id": _text(
            graph.get("execution_graph_id") or graph.get("process_id")
        ),
        "status": "RESOLVED",
        "write_step_ids": [
            node_id for node_id in order if node_id in write_contracts
        ],
        "write_contracts_by_node": deepcopy(write_contracts),
        "cleanup_steps": deepcopy(cleanup_steps),
        "cleanup_order": [row["source_step_id"] for row in cleanup_steps],
        "target_authority": "runtime_contract.approved_targets",
        "credential_authority": "per_target_actor_token_keys",
        "identity_authority": "declared_output_and_cleanup_binding_specs",
        "compensation_authority": "behavior_ir.explicit_compensates_relation",
    }
    contract["contract_id"] = "pgwc_" + _fingerprint(contract)[:24]
    return canonical_graph, contract, "", ""


def _merge_graph_observers(
    experiment: dict[str, Any],
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    observers = [
        deepcopy(row)
        for row in _list(experiment.get("observers"))
        if isinstance(row, dict)
    ]
    effect_rows = [
        deepcopy(_dict(row).get("effect_observer"))
        for row in _dict(contract.get("write_contracts_by_node")).values()
        if _dict(row).get("effect_observer")
    ]
    if not effect_rows:
        return observers
    after_state = next(
        (
            row
            for row in observers
            if _text(row.get("observer_id")) == "after_state"
        ),
        None,
    )
    if after_state is None:
        after_state = {
            "observer_id": "after_state",
            "surface": "http_api",
            "resolver_operations": [],
        }
        observers.append(after_state)
    existing = {
        (
            _text(row.get("operation_ref")),
            _text(row.get("method")).upper(),
            _text(row.get("path")),
        )
        for row in _list(after_state.get("resolver_operations"))
        if isinstance(row, dict)
    }
    for row in effect_rows:
        key = (
            _text(row.get("operation_ref")),
            _text(row.get("method")).upper(),
            _text(row.get("path")),
        )
        if all(key) and key not in existing:
            after_state.setdefault("resolver_operations", []).append(row)
            existing.add(key)
    after_state["process_graph_write_contract_id"] = _text(
        contract.get("contract_id")
    )
    return observers


def finalize_process_graph_write_contract(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze graph write safety onto the final compiled experiment."""
    exp = deepcopy(experiment)
    if _text(_dict(exp.get("compile_receipt")).get("status")) != "COMPILED":
        return exp
    graph, detail = _extract_graph(exp)
    if detail:
        return _blocked(exp, GRAPH_WRITE_CONTRACT_INVALID, detail)
    if not graph:
        return exp

    canonical_graph, contract, reason, detail = _canonicalize_graph(
        graph,
        behavior_ir,
    )
    if reason:
        return _blocked(exp, reason, detail)

    exp["execution_graph"] = canonical_graph
    exp["process_graph_write_contract"] = contract
    nodes_by_id = {
        _text(row.get("node_id")): row
        for row in _list(canonical_graph.get("nodes"))
        if isinstance(row, dict) and _text(row.get("node_id"))
    }
    for step in _list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        node_id = _text(step.get("step_id"))
        node = _dict(nodes_by_id.get(node_id))
        if not node:
            continue
        step.update(
            {
                "method": _text(node.get("method")),
                "path": _text(node.get("path")),
                "effect_observer_operations": deepcopy(
                    _list(node.get("effect_observer_operations"))
                ),
                "_execution_graph": deepcopy(canonical_graph),
                "_graph_write_contract_id": _text(contract.get("contract_id")),
            }
        )

    write_step_ids = _list(contract.get("write_step_ids"))
    if not write_step_ids:
        return exp

    exp["cleanup_plan"] = deepcopy(_list(contract.get("cleanup_steps")))
    exp["observers"] = _merge_graph_observers(exp, contract)
    safety = dict(_dict(exp.get("safety_contract")))
    safety.update(
        {
            "governed_write": True,
            "cleanup_not_required": False,
            "cleanup_authority": "process_graph_write_contract",
            "process_graph_write_contract_id": _text(contract.get("contract_id")),
        }
    )
    exp["safety_contract"] = safety
    receipt = dict(_dict(exp.get("compile_receipt")))
    receipt.update(
        {
            "cleanup_present": True,
            "process_graph_write_contract_id": _text(contract.get("contract_id")),
            "graph_write_step_count": len(write_step_ids),
        }
    )
    exp["compile_receipt"] = receipt

    validation = validate_cleanup_plan(exp, behavior_ir, phase="compile")
    if not validation.get("valid"):
        return _blocked(
            exp,
            _text(validation.get("reason_code"))
            or GRAPH_WRITE_CONTRACT_INVALID,
            _text(validation.get("detail"))
            or "graph_cleanup_validation_failed",
        )
    proof = _dict(validation.get("proof"))
    exp["write_reversibility_proof"] = proof
    exp["cleanup_coverage_contract"] = _dict(validation.get("coverage"))
    exp["compile_receipt"].update(
        {
            "write_reversibility_proof_id": _text(proof.get("proof_id")),
            "write_reversibility_fingerprint": _text(proof.get("fingerprint")),
            "cleanup_semantic_validated": True,
        }
    )
    return exp


__all__ = [
    "GRAPH_WRITE_CONTRACT_SCHEMA",
    "GRAPH_WRITE_CONTRACT_INVALID",
    "GRAPH_WRITE_OBSERVER_UNRESOLVED",
    "GRAPH_WRITE_COMPENSATION_UNRESOLVED",
    "finalize_process_graph_write_contract",
]
