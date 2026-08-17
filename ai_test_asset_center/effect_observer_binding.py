"""Bind write effects to exact source-declared read observers.

Canonical fields already carry API response bindings produced from source
schemas.  This module joins an invariant's exact canonical field ids to those
bindings and emits a formal ``observes`` relation between the bound write and
the declared GET/HEAD operation.  No path, token, or field-name similarity is
used.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .behavior_ir import (
    BehaviorIRError,
    _content_addressed_id,
    _relation_node,
    validate_behavior_ir,
)
from .enterprise_knowledge_center.enterprise_understanding.chinese_semantic_behavior_ir_adapter import (
    source_process_wait_binding,
)

SCHEMA_VERSION = "qualibug.effect-observer-binding.v1"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})


class EffectObserverBindingError(BehaviorIRError):
    """Canonical field evidence cannot be converted into a valid observer join."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _invariant_field_ids(invariant: dict[str, Any]) -> list[str]:
    refs = [
        _text(value)
        for value in _list(invariant.get("field_ids"))
        if _text(value).startswith("cf_")
    ]
    expression = _dict(invariant.get("expression"))
    for operand in _list(expression.get("operands")):
        row = _dict(operand)
        field_ref = _text(row.get("field_id"))
        if field_ref.startswith("cf_") and field_ref not in refs:
            refs.append(field_ref)
    return refs


def _completion_candidate_index(
    *,
    invariants: list[Any],
    canonical_fields: dict[str, dict[str, Any]],
    operations: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Index exact state-transition completion predicates.

    A candidate exists only when a source-backed postcondition operand names
    one canonical STATE field and that field declares a concrete response JSON
    path on a GET/HEAD operation. Non-field outcomes in the same invariant do
    not hide that state operand. Multiple distinct state operands or readbacks
    remain multiple candidates; callers fail closed instead of selecting by
    order.
    """

    merged: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for invariant_value in invariants:
        invariant = _dict(invariant_value)
        invariant_ref = _text(invariant.get("id"))
        expression = _dict(invariant.get("expression"))
        operands = [
            _dict(value)
            for value in _list(expression.get("operands"))
            if isinstance(value, dict)
        ]
        if (
            not invariant_ref
            or not _list(invariant.get("source_refs"))
            or _text(expression.get("kind")) != "postcondition"
            or _text(expression.get("operator"))
            not in {"must_become", "outcome_contract"}
        ):
            continue
        write_refs = [
            ref
            for ref in (
                _text(value) for value in _list(invariant.get("operation_refs"))
            )
            if ref in operations
            and _text(operations[ref].get("method")).upper() in _WRITE_METHODS
            and _list(operations[ref].get("source_refs"))
        ]
        for operand in operands:
            field_ref = _text(operand.get("field_id"))
            expected_value = operand.get("expected_value")
            if (
                not field_ref
                or not isinstance(expected_value, str)
                or not expected_value.strip()
            ):
                continue
            field = _dict(canonical_fields.get(field_ref))
            if (
                _text(field.get("semantic_type")).upper() != "STATE"
                or not (
                    _list(field.get("source_refs"))
                    or _list(field.get("entity_source_refs"))
                )
            ):
                continue
            read_bindings: dict[tuple[str, str], dict[str, Any]] = {}
            for binding_value in _list(field.get("api_response_bindings")):
                binding = _dict(binding_value)
                observer_ref = _text(binding.get("operation_id"))
                json_path = _text(binding.get("json_path"))
                observer = _dict(operations.get(observer_ref))
                if (
                    observer_ref
                    and json_path
                    and _text(observer.get("method")).upper()
                    in _READ_METHODS
                    and _list(observer.get("source_refs"))
                ):
                    read_bindings[(observer_ref, json_path)] = binding
            for write_ref in write_refs:
                for observer_ref, json_path in sorted(read_bindings):
                    key = (
                        write_ref,
                        _text(expected_value),
                        observer_ref,
                        json_path,
                        field_ref,
                    )
                    candidate = merged.setdefault(
                        key,
                        {
                            "target_operation_ref": write_ref,
                            "target_state": expected_value,
                            "observer_operation_ref": observer_ref,
                            "json_path": json_path,
                            "canonical_field_ref": field_ref,
                            "completion_invariant_refs": [],
                        },
                    )
                    if (
                        invariant_ref
                        not in candidate["completion_invariant_refs"]
                    ):
                        candidate["completion_invariant_refs"].append(
                            invariant_ref
                        )

    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in merged.values():
        candidate["completion_invariant_refs"] = sorted(
            candidate["completion_invariant_refs"]
        )
        indexed.setdefault(
            (
                _text(candidate.get("target_operation_ref")),
                _text(candidate.get("target_state")),
            ),
            [],
        ).append(candidate)
    for candidates in indexed.values():
        candidates.sort(
            key=lambda row: (
                _text(row.get("observer_operation_ref")),
                _text(row.get("json_path")),
                _text(row.get("canonical_field_ref")),
            )
        )
    return indexed


def _bind_timed_wait_completion_observers(
    *,
    process_graphs: list[Any],
    completion_candidates: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[int, dict[str, int]]:
    bound_count = 0
    reason_counts: dict[str, int] = {}

    def record(reason: str) -> None:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    for graph_value in process_graphs:
        graph = _dict(graph_value)
        if _text(graph.get("status")) != "COMPILED" or not (
            _list(graph.get("source_refs")) or _list(graph.get("evidence"))
        ):
            continue
        nodes = {
            _text(row.get("node_id")): row
            for row in _list(graph.get("nodes"))
            if isinstance(row, dict) and _text(row.get("node_id"))
        }
        for wait_value in _list(graph.get("wait_contracts")):
            wait = _dict(wait_value)
            if not (
                _text(wait.get("wait_kind")) == "TIMED_WAIT"
                and _text(wait.get("status")) == "BOUND"
                and wait.get("source_backed") is True
            ):
                continue
            target_node = _dict(nodes.get(_text(wait.get("target_node_id"))))
            target_operation_ref = _text(target_node.get("operation_ref"))
            target_state = _text(target_node.get("to_state"))
            candidates = completion_candidates.get(
                (target_operation_ref, target_state), []
            )
            if not target_operation_ref or not target_state or not candidates:
                record("TEMPORAL_COMPLETION_POSTCONDITION_UNRESOLVED")
                continue
            if len(candidates) != 1:
                record("TEMPORAL_COMPLETION_OBSERVER_AMBIGUOUS")
                continue
            candidate = candidates[0]
            expected_predicate = {
                "json_path": candidate["json_path"],
                "operator": "equals",
                "expected_value": candidate["target_state"],
            }
            declared_observer = _text(
                wait.get("observer_operation_ref")
                or wait.get("read_operation_ref")
            )
            declared_predicate = _dict(
                wait.get("predicate") or wait.get("terminal_predicate")
            )
            if (
                declared_observer
                and declared_observer != candidate["observer_operation_ref"]
            ) or (
                declared_predicate
                and declared_predicate != expected_predicate
            ):
                record("TEMPORAL_COMPLETION_OBSERVER_CONFLICT")
                continue
            wait["observer_operation_ref"] = candidate[
                "observer_operation_ref"
            ]
            wait["predicate"] = expected_predicate
            wait["completion_binding"] = {
                "authority": "state_transition_postcondition_response_binding",
                "canonical_field_ref": candidate["canonical_field_ref"],
                "completion_invariant_refs": candidate[
                    "completion_invariant_refs"
                ],
                "target_operation_ref": target_operation_ref,
                "target_state": candidate["target_state"],
            }
            bound_count += 1
    return bound_count, dict(sorted(reason_counts.items()))


def _bind_temporal_invariants_from_process_waits(
    *,
    invariants: list[Any],
    process_graphs: list[Any],
    operations: dict[str, dict[str, Any]],
) -> tuple[int, dict[str, int]]:
    bound_count = 0
    reason_counts: dict[str, int] = {}

    def resolves(kind: str, ref: str) -> bool:
        return kind == "operation" and ref in operations

    for invariant_value in invariants:
        invariant = _dict(invariant_value)
        expression = _dict(invariant.get("expression"))
        if not (
            _text(expression.get("kind")) == "temporal"
            and _text(expression.get("temporal_semantics"))
            == "action_deadline"
            and _text(expression.get("anchor_grounding_status")) != "BOUND"
        ):
            continue
        operation_refs = sorted(
            {
                _text(value)
                for value in _list(invariant.get("operation_refs"))
                if _text(value) in operations
            }
        )
        if len(operation_refs) != 1:
            reason = "TEMPORAL_PROCESS_WAIT_UNRESOLVED"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue
        constraint = {
            "raw": expression.get("raw"),
            "anchor": expression.get("anchor"),
            "duration": expression.get("duration"),
            "window_ms": expression.get("window_ms"),
            "source_backed": True,
        }
        binding, reason = source_process_wait_binding(
            constraint,
            operation_ref=operation_refs[0],
            process_graphs=process_graphs,
            ref_resolver=resolves,
        )
        if binding:
            expression.update(binding)
            bound_count += 1
        else:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return bound_count, dict(sorted(reason_counts.items()))


def bind_source_effect_observers(
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add exact write→read observer relations from canonical field bindings."""

    if not isinstance(behavior_ir, dict):
        raise EffectObserverBindingError("behavior_ir_not_object")

    enriched = deepcopy(behavior_ir)
    operations = {
        _text(row.get("id")): row
        for row in _list(enriched.get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    canonical_fields: dict[str, dict[str, Any]] = {}
    for entity in _list(enriched.get("entities")):
        if not isinstance(entity, dict):
            continue
        for field in _list(entity.get("fields")):
            row = _dict(field)
            field_ref = _text(row.get("field_id"))
            if field_ref:
                canonical_fields[field_ref] = {
                    **row,
                    "entity_ref": _text(entity.get("id")),
                    "entity_source_refs": _list(entity.get("source_refs")),
                }

    pair_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    missing_field_ids: set[str] = set()
    fields_without_read_binding: set[str] = set()
    invariants_without_write_binding: set[str] = set()

    for invariant in _list(enriched.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        invariant_ref = _text(invariant.get("id"))
        if not invariant_ref or _text(invariant.get("binding_status")) == "umbrella_rule_excluded":
            continue
        field_ids = _invariant_field_ids(invariant)
        if not field_ids:
            continue
        write_refs = [
            operation_ref
            for operation_ref in (
                _text(value)
                for value in _list(invariant.get("operation_refs"))
            )
            if operation_ref in operations
            and _text(operations[operation_ref].get("method")).upper() in _WRITE_METHODS
        ]
        if not write_refs:
            invariants_without_write_binding.add(invariant_ref)
            continue

        for field_ref in field_ids:
            field = _dict(canonical_fields.get(field_ref))
            if not field:
                missing_field_ids.add(field_ref)
                continue
            read_refs = []
            for binding in _list(field.get("api_response_bindings")):
                operation_ref = _text(_dict(binding).get("operation_id"))
                operation = _dict(operations.get(operation_ref))
                if (
                    operation_ref
                    and _text(operation.get("method")).upper() in _READ_METHODS
                    and operation_ref not in read_refs
                ):
                    read_refs.append(operation_ref)
            if not read_refs:
                fields_without_read_binding.add(field_ref)
                continue

            for write_ref in write_refs:
                for read_ref in read_refs:
                    if write_ref == read_ref:
                        continue
                    pair = pair_evidence.setdefault(
                        (write_ref, read_ref),
                        {
                            "field_refs": set(),
                            "invariant_refs": set(),
                            "source_refs": [],
                        },
                    )
                    pair["field_refs"].add(field_ref)
                    pair["invariant_refs"].add(invariant_ref)
                    for source_ref in (
                        _list(invariant.get("source_refs"))
                        + _list(field.get("source_refs"))
                        + _list(field.get("entity_source_refs"))
                    ):
                        if isinstance(source_ref, dict) and source_ref not in pair["source_refs"]:
                            pair["source_refs"].append(source_ref)

    existing_keys = {
        (
            _text(row.get("relation_type")),
            _text(row.get("from_ref")),
            _text(row.get("to_ref")),
            _text(row.get("operation_ref")),
        )
        for row in _list(enriched.get("relations"))
        if isinstance(row, dict)
    }
    added_relations: list[dict[str, Any]] = []
    for (write_ref, read_ref), evidence in sorted(pair_evidence.items()):
        relation_key = ("observes", write_ref, read_ref, read_ref)
        if relation_key in existing_keys:
            continue
        write_operation = _dict(operations.get(write_ref))
        read_operation = _dict(operations.get(read_ref))
        source_refs = (
            _list(evidence.get("source_refs"))
            + _list(write_operation.get("source_refs"))
            + _list(read_operation.get("source_refs"))
        )[:5]
        relation = _relation_node(
            relation_type="observes",
            from_ref=write_ref,
            to_ref=read_ref,
            operation_ref=read_ref,
            effects=[{
                "effect_source_operation_ref": write_ref,
                "observer_operation_ref": read_ref,
                "canonical_field_refs": sorted(evidence["field_refs"]),
                "invariant_refs": sorted(evidence["invariant_refs"]),
            }],
            source_refs=source_refs,
            confidence=min(
                float(write_operation.get("confidence") or 0.7),
                float(read_operation.get("confidence") or 0.7),
            ),
            derivation="schema-derived",
            source_relationship_ref=(
                "canonical_field_response_binding:"
                + _fingerprint({
                    "write_ref": write_ref,
                    "read_ref": read_ref,
                    "field_refs": sorted(evidence["field_refs"]),
                })[:20]
            ),
        )
        added_relations.append(relation)
        existing_keys.add(relation_key)

    enriched["relations"] = [
        *[
            dict(row)
            for row in _list(enriched.get("relations"))
            if isinstance(row, dict)
        ],
        *added_relations,
    ]
    completion_candidates = _completion_candidate_index(
        invariants=_list(enriched.get("invariants")),
        canonical_fields=canonical_fields,
        operations=operations,
    )
    timed_wait_observer_bound_count, timed_wait_binding_reason_counts = (
        _bind_timed_wait_completion_observers(
            process_graphs=_list(enriched.get("process_graphs")),
            completion_candidates=completion_candidates,
        )
    )
    temporal_invariant_bound_count, temporal_binding_reason_counts = (
        _bind_temporal_invariants_from_process_waits(
            invariants=_list(enriched.get("invariants")),
            process_graphs=_list(enriched.get("process_graphs")),
            operations=operations,
        )
    )
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "BOUND_WITH_GAPS"
            if missing_field_ids
            or fields_without_read_binding
            or invariants_without_write_binding
            or timed_wait_binding_reason_counts
            or temporal_binding_reason_counts
            else "BOUND"
            if pair_evidence
            or timed_wait_observer_bound_count
            or temporal_invariant_bound_count
            else "NO_CANONICAL_EFFECT_BINDINGS"
        ),
        "binding_authority": "canonical_field_api_response_binding",
        "timed_wait_binding_authority": (
            "state_transition_postcondition_response_binding"
        ),
        "heuristic_binding_enabled": False,
        "candidate_pair_count": len(pair_evidence),
        "added_relation_count": len(added_relations),
        "missing_field_count": len(missing_field_ids),
        "missing_field_ids": sorted(missing_field_ids),
        "field_without_read_binding_count": len(fields_without_read_binding),
        "field_without_read_binding_ids": sorted(fields_without_read_binding),
        "invariant_without_write_binding_count": len(invariants_without_write_binding),
        "invariant_without_write_binding_ids": sorted(invariants_without_write_binding),
        "timed_wait_completion_candidate_count": sum(
            len(rows) for rows in completion_candidates.values()
        ),
        "timed_wait_observer_bound_count": timed_wait_observer_bound_count,
        "timed_wait_binding_reason_counts": timed_wait_binding_reason_counts,
        "temporal_invariant_bound_count": temporal_invariant_bound_count,
        "temporal_binding_reason_counts": temporal_binding_reason_counts,
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    enriched["effect_observer_binding_receipt"] = receipt
    enriched["model_id"] = _content_addressed_id(enriched)

    errors = validate_behavior_ir(enriched)
    if errors:
        raise EffectObserverBindingError(
            "effect_observer_binding_invalid_ir:" + ",".join(errors)
        )
    return enriched, receipt
