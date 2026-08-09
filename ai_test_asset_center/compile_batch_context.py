"""Per-batch Behavior-IR index bundle (SPEC-11 4.2).

The compile chain rebuilt IR-derived indexes per obligation (actor/operation/
conflict scoping, relation mention scans, observer joins): O(obligations × IR).
This module precomputes the indexes ONCE per compile batch and hands them to
the chain through a ``contextvars.ContextVar``. Consumers consult the bundle
and fall back to their original on-the-fly construction when no batch context
is active (direct per-obligation calls, tests), so every path stays
byte-identical — only the repeated index construction is removed.

Thread safety: the bundle is an immutable frozen object; ``ContextVar`` is
per-context, so concurrent workers inherit the caller's bundle at submit time
and cannot observe each other's state.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping

from .behavior_ir_core import _text as _text  # noqa: F401  (re-exported helper)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return str(value or "").strip()


_RELATION_MENTION_KEYS = (
    "operation_ref",
    "op_ref",
    "source",
    "target",
    "source_ref",
    "target_ref",
    "from_ref",
    "to_ref",
    "source_operation_ref",
    "target_operation_ref",
    "from",
    "to",
)


@dataclass(frozen=True)
class CompileBatchIndexes:
    """Immutable IR-derived indexes for one compile batch.

    Every member mirrors EXACTLY the per-call construction it replaces (same
    keys, same normalization), so consumers switching to the bundle keep
    byte-identical outputs. All containers are read-only by contract.
    """

    operations_by_id: Mapping[str, dict[str, Any]]
    actors_by_id: Mapping[str, dict[str, Any]]
    entities_by_id: Mapping[str, dict[str, Any]]
    actor_roles_by_id: Mapping[str, str]
    actor_ids: frozenset[str]
    operation_ids: frozenset[str]
    entity_ids: frozenset[str]
    conflicts: tuple[dict[str, Any], ...]
    conflict_refs: tuple[frozenset[str], ...]
    relations: tuple[dict[str, Any], ...]
    relation_mentions: tuple[frozenset[str], ...]
    relations_by_operation: Mapping[str, tuple[dict[str, Any], ...]]
    related_relations_by_ref: Mapping[str, tuple[dict[str, Any], ...]]
    observes_relations_by_entity: Mapping[str, tuple[dict[str, Any], ...]]
    relation_pairs: frozenset[tuple[str, str]]
    relation_operation_refs: frozenset[str]


def _relation_mention_set(relation: dict[str, Any]) -> frozenset[str]:
    """All ref values under the keys ``_relation_mentions_operation`` scans
    (write_reversibility_contract), so a later membership test against an
    operation-ref set is equivalent to that function's per-key scan."""
    values: set[str] = set()
    for key in _RELATION_MENTION_KEYS:
        value = relation.get(key)
        if isinstance(value, list):
            values.update(_str(item) for item in value if _str(item))
        elif value is not None:
            text = _str(value)
            if text:
                values.add(text)
    return frozenset(values)


def _conflict_ref_set(conflict: dict[str, Any]) -> frozenset[str]:
    """Refs of a conflict row under the same key scan
    ``experiment_compiler._refs_from_mapping`` applies (explicit *_ref keys and
    *_ref/*_refs suffixes), minus source_refs/secret_refs."""
    from .experiment_compiler import _refs_from_mapping

    return frozenset(_refs_from_mapping(conflict))


def build_batch_indexes(behavior_ir: dict[str, Any]) -> CompileBatchIndexes:
    """Compute the index bundle once per batch. O(IR) total instead of
    O(obligations × IR)."""
    ir = _dict(behavior_ir)
    operations = [
        row for row in _list(ir.get("operations")) if isinstance(row, dict)
    ]
    actors = [row for row in _list(ir.get("actors")) if isinstance(row, dict)]
    entities = [row for row in _list(ir.get("entities")) if isinstance(row, dict)]
    relations = [
        row for row in _list(ir.get("relations")) if isinstance(row, dict)
    ]
    conflicts = [
        row for row in _list(ir.get("conflicts")) if isinstance(row, dict)
    ]

    operations_by_id: dict[str, dict[str, Any]] = {}
    operation_ids: set[str] = set()
    for row in operations:
        oid = _str(row.get("id") or row.get("operation_id"))
        if oid:
            operations_by_id[oid] = row
            operation_ids.add(oid)

    actors_by_id: dict[str, dict[str, Any]] = {}
    actor_ids: set[str] = set()
    actor_roles_by_id: dict[str, str] = {}
    for row in actors:
        aid = _str(row.get("id"))
        if aid:
            actors_by_id[aid] = row
            actor_ids.add(aid)
            actor_roles_by_id[aid] = _str(
                row.get("role_key") or row.get("role")
            ).lower()

    entities_by_id: dict[str, dict[str, Any]] = {}
    entity_ids: set[str] = set()
    for row in entities:
        eid = _str(row.get("id"))
        if eid:
            entities_by_id[eid] = row
            entity_ids.add(eid)

    relation_mentions = tuple(_relation_mention_set(row) for row in relations)
    relations_by_operation: dict[str, list[dict[str, Any]]] = {}
    related_relations_by_ref: dict[str, list[dict[str, Any]]] = {}
    observes_relations_by_entity: dict[str, list[dict[str, Any]]] = {}
    relation_pairs: set[tuple[str, str]] = set()
    relation_operation_refs: set[str] = set()
    for row, mentions in zip(relations, relation_mentions):
        for ref in mentions:
            relations_by_operation.setdefault(ref, []).append(row)
        # runtime_binding_graph.declared_effect_observers keys relation joins
        # by {operation_ref, from_ref, to_ref, entity_ref}.
        for key in ("operation_ref", "from_ref", "to_ref", "entity_ref"):
            ref = _str(row.get(key))
            if ref:
                related_relations_by_ref.setdefault(ref, []).append(row)
        rel_type = _str(row.get("relation_type"))
        if rel_type in {"observes", "scopes"}:
            entity_ref = _str(row.get("entity_ref") or row.get("to_ref"))
            if entity_ref:
                observes_relations_by_entity.setdefault(entity_ref, []).append(row)
        from_ref = _str(row.get("from_ref"))
        to_ref = _str(row.get("to_ref"))
        if from_ref and to_ref:
            relation_pairs.add((from_ref, to_ref))
        op_ref = _str(row.get("operation_ref"))
        if op_ref:
            relation_operation_refs.add(op_ref)

    return CompileBatchIndexes(
        operations_by_id=operations_by_id,
        actors_by_id=actors_by_id,
        entities_by_id=entities_by_id,
        actor_roles_by_id=actor_roles_by_id,
        actor_ids=frozenset(actor_ids),
        operation_ids=frozenset(operation_ids),
        entity_ids=frozenset(entity_ids),
        conflicts=tuple(conflicts),
        conflict_refs=tuple(_conflict_ref_set(row) for row in conflicts),
        relations=tuple(relations),
        relation_mentions=relation_mentions,
        relations_by_operation={
            ref: tuple(rows) for ref, rows in relations_by_operation.items()
        },
        related_relations_by_ref={
            ref: tuple(rows) for ref, rows in related_relations_by_ref.items()
        },
        observes_relations_by_entity={
            ref: tuple(rows) for ref, rows in observes_relations_by_entity.items()
        },
        relation_pairs=frozenset(relation_pairs),
        relation_operation_refs=frozenset(relation_operation_refs),
    )


_BATCH_INDEXES: ContextVar[CompileBatchIndexes | None] = ContextVar(
    "qualibug_compile_batch_indexes", default=None
)


def get_batch_indexes() -> CompileBatchIndexes | None:
    """Active bundle for the current context, or None (per-call construction)."""
    return _BATCH_INDEXES.get()


def set_batch_indexes(indexes: CompileBatchIndexes) -> Token:
    """Activate a bundle for this context; returns the reset token."""
    return _BATCH_INDEXES.set(indexes)


def reset_batch_indexes(token: Token) -> None:
    _BATCH_INDEXES.reset(token)


__all__ = [
    "CompileBatchIndexes",
    "build_batch_indexes",
    "get_batch_indexes",
    "reset_batch_indexes",
    "set_batch_indexes",
]
