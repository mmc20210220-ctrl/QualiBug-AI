"""Plan multi-level dependency establishment chains for governed writes.

Real enterprise systems are multi-interface topologies: a write operation's
request body carries reference fields (``addressId``, ``items[].sku``) whose
referenced entities are themselves created through other write operations
whose bodies carry further references (``user -> address -> order ->
payment``). A fixture-phase chain that stops at one level still dies on a
fresh target with ``BLOCKED_PRECONDITION_BINDING_INCOMPLETE`` or a 500 from an
invalid foreign-key reference.

This module plans the *full dependency DAG* of a subject entity establishment:

* every reference field of the create operation is resolved structurally to a
  referenced entity (word-boundary identity suffix, same authority as the
  existing reference-field resolver);
* the referenced entity's own source-declared create operation is resolved
  (collection POST + request example + executable actor + declared cleanup —
  nothing synthesized);
* recursion continues until every dependency is either planned or visibly
  unresolvable;
* the DAG is emitted as a leaves-first step sequence (post-order DFS) so each
  created identity is captured into ``runtime_bindings`` before the consuming
  create materializes its body (the existing precondition executor already
  captures identities step-by-step);
* dependency cycles are detected by entity-identity visited tracking and fail
  closed with a NAMED reason (``MULTI_LEVEL_DEPENDENCY_CYCLE``) — recursion
  can never spin;
* depth is bounded by an explicit, receipted cap
  (``MULTI_LEVEL_DEPENDENCY_TOO_DEEP``), never silently truncated.

RUNTIME-DATA-FIRST (observe-first)
==================================
Every planned step additionally declares ``observation_resolvers`` (the
source-declared collection reads of the entity it creates) and
``skip_if_observed_target``. Real environment rows are the preferred subject:
the fixture layer / precondition executor bind an observed real identity when
the collection has data, and only fall back to the governed create when
observation is empty. Reads are read-only GET/HEAD on the declared target;
writes remain governed-sandboxed and cleanup-complete.

SOURCE-DECLARED ONLY
====================
No industry terms, no field-name tables, no entity/table names: everything
resolves from the Behavior IR (entities, operations, relations) and the
documented request examples. A dependency whose entity has no establishable
create is left as an unresolved placeholder (the existing
``BLOCKED_PRECONDITION_BINDING_INCOMPLETE`` gate stays the visible witness)
and is reported in ``detail.unresolved_nested_references`` — never silently
dropped, never guessed.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .money_precondition_chain import (
    _create_operation_for_entity,
    _is_reference_field,
    _readback_contract_for_entity,
)
from .runtime_binding_graph import (
    _declared_cleanup_operations as _declared_cleanup_operations,
    _declared_fixture_actor_refs as _declared_fixture_actor_refs,
    _declared_reads_for_paths as _declared_reads_for_paths,
    _request_example as _tokenized_request_example,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "qualibug.multi-level-dependency-chain.v1"

PLANNED = "PLANNED"
NOT_APPLICABLE = "NOT_APPLICABLE"
BLOCKED = "BLOCKED"

# Explicit, receipted recursion budget. Eight levels cover realistic
# enterprise chains (tenant -> org -> user -> address -> order -> payment)
# with headroom; deeper chains fail closed with a named reason instead of
# unbounded recursion.
MAX_DEPENDENCY_DEPTH = 8

REASON_NO_ENTITY = "MULTI_LEVEL_DEPENDENCY_SUBJECT_ENTITY_UNRESOLVED"
REASON_NO_CREATE = "MULTI_LEVEL_DEPENDENCY_CREATE_MISSING"
REASON_NO_ACTOR = "MULTI_LEVEL_DEPENDENCY_ACTOR_UNRESOLVED"
REASON_NO_CLEANUP = "MULTI_LEVEL_DEPENDENCY_CLEANUP_MISSING"
REASON_CYCLE = "MULTI_LEVEL_DEPENDENCY_CYCLE"
REASON_TOO_DEEP = "MULTI_LEVEL_DEPENDENCY_TOO_DEEP"
REASON_IDENTITY_MISSING = "MULTI_LEVEL_DEPENDENCY_IDENTITY_SOURCE_MISSING"
REASON_IDENTITY_AMBIGUOUS = "MULTI_LEVEL_DEPENDENCY_IDENTITY_SOURCE_AMBIGUOUS"

# Establishment step intents (shared with the money chain).
INTENT_ESTABLISHMENT = "multi_level_dependency_establishment"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _blocked(reason_code: str, **detail: Any) -> dict[str, Any]:
    return {
        "status": BLOCKED,
        "reason_code": reason_code,
        "steps": [],
        "identity_binding_target": "",
        "create_operation_ref": "",
        "entity_ref": "",
        "observation_resolvers": [],
        "detail": dict(detail),
    }


def _entity_by_id(entities: list[Any], entity_id: str) -> dict[str, Any]:
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if _text(entity.get("id")) == entity_id:
            return entity
    return {}


def _declared_entity_identity_fields(entity: dict[str, Any]) -> list[str]:
    """Return only identity fields explicitly projected into Behavior IR.

    A conventional ``id`` is not an authority.  The field must be present in
    ``identity_fields`` or in an explicit ``identity_keys[].columns`` row;
    otherwise response capture cannot know which create-response value names
    the established entity.
    """
    fields = [
        _text(value)
        for value in _list(entity.get("identity_fields"))
        if _text(value)
    ]
    for raw_key in _list(entity.get("identity_keys")):
        key = _dict(raw_key)
        fields.extend(
            _text(value)
            for value in _list(key.get("columns"))
            if _text(value)
        )
    return list(dict.fromkeys(fields))


def _collection_observation_resolvers(
    behavior_ir: dict[str, Any],
    create_path: str,
) -> list[dict[str, str]]:
    """Source-declared GET/HEAD reads of the created entity's collection.

    These are the observe-first channel: when the environment already holds
    real rows of the entity, the fixture layer binds a real identity instead
    of creating a disposable one. Structural only — no invented paths.
    """
    return _declared_reads_for_paths(
        [create_path],
        behavior_ir=behavior_ir,
        max_candidates=2,
    )


def _resolve_create_operation(
    behavior_ir: dict[str, Any],
    entity: dict[str, Any],
    reference_field: str,
) -> dict[str, Any]:
    """Resolve the source-declared create POST for an entity.

    Three structural authorities, in order:
    1. the shared entity-based resolver (declared collection paths +
       entity-name final segment);
    2. candidate collections derived from the PARENT reference field through
       the shared body-field collection authority (``addressId`` ->
       ``/api/addresses``), so singular/irregular pluralizations
       (address -> addresses) resolve identically across industries;
    3. Behavior IR data-flow edges: a ``produces`` relation whose ``to_ref``
       is the entity and whose ``from_ref`` is a source-declared POST with a
       request example (e.g. a registration endpoint that produces the user
       entity at a non-collection path). The produced create still must pass
       the cleanup authority downstream — an undeclared compensator refuses
       the step, never invents one.
    The candidate POST must carry a request example and an operation id —
    nothing is synthesized.
    """
    create_op = _create_operation_for_entity(behavior_ir, entity)
    if create_op:
        return create_op
    from .experiment_runtime_support import normalize_path_placeholders
    from .real_id_resolver_base import body_field_collection_paths

    entity_id = _text(entity.get("id"))
    candidates = body_field_collection_paths(_text(reference_field))
    for candidate in candidates:
        normalized = normalize_path_placeholders(candidate).rstrip("/")
        if not normalized or "{" in normalized or ":" in normalized:
            continue
        for op in _list(behavior_ir.get("operations")):
            if not isinstance(op, dict):
                continue
            if _text(op.get("method")).upper() != "POST":
                continue
            op_path = normalize_path_placeholders(
                _text(op.get("path") or op.get("raw_path"))
            )
            if "{" in op_path or ":" in op_path:
                continue
            if op_path.rstrip("/") != normalized:
                continue
            if not _text(op.get("id")):
                continue
            if not _tokenized_request_example(op):
                continue
            return op
    # IR data-flow edge: a source-declared operation that PRODUCES the entity
    # (relation from_ref=operation, to_ref=entity) is the entity's create
    # when its path is not the collection (register -> users).
    for relation in _list(behavior_ir.get("relations")):
        if not isinstance(relation, dict):
            continue
        if _text(relation.get("relation_type")) != "produces":
            continue
        if _text(relation.get("to_ref")) != entity_id:
            continue
        op_id = _text(relation.get("operation_ref") or relation.get("from_ref"))
        if not op_id:
            continue
        for op in _list(behavior_ir.get("operations")):
            if not isinstance(op, dict):
                continue
            if _text(op.get("id")) != op_id:
                continue
            if _text(op.get("method")).upper() != "POST":
                continue
            if not _tokenized_request_example(op):
                continue
            return op
    return {}


def _entity_candidates(field: str) -> list[str]:
    """Structural entity-name candidates for a reference field.

    ``billingAddressId`` -> ``billingaddress`` then progressively stripped
    qualifier prefixes (``address``) so qualifier-prefixed reference fields
    (billingAddressId, shippingAddressId, ownerUserId) resolve to the same
    entity the plain form does. Pure word-boundary splitting on the original
    spelling (camelCase survives before lowercasing) — no industry
    vocabulary.
    """
    import re

    raw = _text(field)
    key = raw.lower()
    stem = key
    for suffix in ("_id", "id", "_ref", "ref", "_uuid", "uuid", "_key", "key"):
        if key.endswith(suffix) and len(key) > len(suffix):
            stem = key[: -len(suffix)].rstrip("_")
            break
    if not stem:
        return []
    candidates = [stem]
    words = re.findall(
        r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+",
        raw,
    )
    words = [word.lower() for word in words if word]
    words = [
        word
        for word in words
        if word not in {"id", "ids", "ref", "refs", "uuid", "uuids", "key", "keys"}
    ]
    if len(words) > 1:
        for start in range(1, len(words)):
            stripped = "".join(words[start:])
            if stripped and stripped != stem and stripped not in candidates:
                candidates.append(stripped)
    return candidates


def _subject_pairs_from_example(
    example: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[tuple[str, str]]:
    """Reference-field -> referenced-entity pairs (structural, qualifier-aware).

    Wraps the shared reference-field resolver and adds qualifier stripping
    (``billingAddressId`` -> ``address``), so diamonds that consume one entity
    through several parent field names resolve to ONE create step binding all
    parent fields.
    """
    entities = _list(behavior_ir.get("entities"))
    by_name: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = _text(entity.get("name"))
        if name:
            by_name.setdefault(name.lower(), entity)
        for alias in _list(entity.get("source_entity_names")):
            if _text(alias):
                by_name.setdefault(_text(alias).lower(), entity)
    # Structural singularization: strip one trailing "s" (orders -> order)
    # AND the "es" form (addresses -> address), so singular reference fields
    # resolve to plural-named entities across English spellings. Language
    # morphology only — never industry vocabulary.
    for key in list(by_name):
        singular = key[:-1] if key.endswith("s") and len(key) > 1 else ""
        if singular and singular not in by_name:
            by_name.setdefault(singular, by_name[key])
        if key.endswith("es") and len(key) > 2:
            es_singular = key[:-2]
            if es_singular and es_singular not in by_name:
                by_name.setdefault(es_singular, by_name[key])

    resolved: list[tuple[str, str]] = []
    for field, value in example.items():
        if not isinstance(field, str):
            continue
        if not _is_reference_field(field):
            continue
        if not (isinstance(value, str) and _text(value)):
            continue
        entity = None
        for candidate in _entity_candidates(field):
            entity = by_name.get(candidate)
            if entity is not None:
                break
        if entity is None:
            continue
        entity_id = _text(entity.get("id"))
        if entity_id:
            resolved.append((entity_id, field))
    return resolved


def _plan_level(
    *,
    behavior_ir: dict[str, Any],
    entity_id: str,
    reference_fields: list[str],
    actor_refs: list[str],
    depth: int,
    visited: list[str],
    planned_entities: set[str],
    steps_out: list[dict[str, Any]],
    unresolved_nested: list[dict[str, Any]],
) -> dict[str, Any]:
    """Plan establishment for one entity; ``ok`` or a named fail-closed reason.

    Post-order DFS: dependencies are planned (and appended to ``steps_out``)
    before the entity's own create step, so identity capture happens in
    dependency order when the plan executes. Returns
    ``{"status": "ok"}`` or ``{"status": "blocked", "reason_code": ...,
    "detail": ...}``.
    """
    entities = _list(behavior_ir.get("entities"))
    entity = _entity_by_id(entities, entity_id)
    primary_field = _text(reference_fields[0] if reference_fields else "")
    if not entity:
        return {
            "status": "blocked",
            "reason_code": REASON_NO_ENTITY,
            "detail": {"entity_ref": entity_id, "reference_field": primary_field},
        }
    identity_fields = _declared_entity_identity_fields(entity)
    if not identity_fields:
        return {
            "status": "blocked",
            "reason_code": REASON_IDENTITY_MISSING,
            "detail": {
                "entity_ref": entity_id,
                "reference_field": primary_field,
                "identity_fields": [],
                "source_authority": "behavior_ir.entities.identity_fields",
            },
        }
    if len(identity_fields) > 1:
        # A row may declare several identity fields (schema PRIMARY KEY plus
        # UNIQUE business keys — orders: id + order_no). The primary key is
        # the structural authority: it identifies exactly the row the create
        # response mints. Business keys are legitimate alternatives but never
        # a reason to block the whole dependency chain, so the primary key
        # wins and the rest stay recorded as alternates. Only when NO
        # structural key exists among the declarations (every candidate is a
        # business key) does the ambiguity remain fail-closed.
        structural = [
            field
            for field in identity_fields
            if re.sub(r"[^a-z0-9]+", "", field.lower())
            in {"id", "uuid", "pk", "key", "uid", "guid"}
        ]
        if structural:
            identity_fields = [structural[0]]
        else:
            return {
                "status": "blocked",
                "reason_code": REASON_IDENTITY_AMBIGUOUS,
                "detail": {
                    "entity_ref": entity_id,
                    "reference_field": primary_field,
                    "identity_fields": identity_fields,
                    "source_authority": "behavior_ir.entities.identity_fields",
                },
            }
    if entity_id in visited:
        chain = "->".join([*visited, entity_id])
        return {
            "status": "blocked",
            "reason_code": REASON_CYCLE,
            "detail": {"chain": chain, "entity_ref": entity_id},
        }
    if depth > MAX_DEPENDENCY_DEPTH:
        return {
            "status": "blocked",
            "reason_code": REASON_TOO_DEEP,
            "detail": {
                "entity_ref": entity_id,
                "depth": depth,
                "max_depth": MAX_DEPENDENCY_DEPTH,
            },
        }

    create_op = _resolve_create_operation(
        behavior_ir, entity, primary_field
    )
    create_op_id = _text(create_op.get("id"))
    if not create_op_id:
        return {
            "status": "blocked",
            "reason_code": REASON_NO_CREATE,
            "detail": {"entity_ref": entity_id, "reference_field": primary_field},
        }

    # Actor: prefer the caller-provided executable actor set; fall back to the
    # source-declared fixture actor authority of the create operation. Never
    # invents an actor.
    actors = _index_actors(_list(behavior_ir.get("actors")))
    available = [actor_id for actor_id in actor_refs if actor_id in actors]
    if not available:
        declared = _declared_fixture_actor_refs(
            create_op, behavior_ir=behavior_ir
        )
        available = [actor_id for actor_id in declared if actor_id in actors]
    if not available:
        return {
            "status": "blocked",
            "reason_code": REASON_NO_ACTOR,
            "detail": {"entity_ref": entity_id, "create_operation_ref": create_op_id},
        }

    create_path = _text(create_op.get("path") or create_op.get("raw_path"))
    cleanup = _declared_cleanup_operations(create_path, behavior_ir=behavior_ir)
    if not cleanup:
        return {
            "status": "blocked",
            "reason_code": REASON_NO_CLEANUP,
            "detail": {"entity_ref": entity_id, "create_operation_ref": create_op_id},
        }

    # ── Nested references of the create example (recursive dependency) ──
    example = _tokenized_request_example(create_op)
    child_groups: dict[str, list[str]] = {}
    for child_entity_id, child_field in _subject_pairs_from_example(
        example, behavior_ir
    ):
        if child_entity_id == entity_id:
            # Self-reference (parent id echoed back) is a data-passing
            # convention, not a creation dependency: skip, never a cycle.
            continue
        child_groups.setdefault(child_entity_id, []).append(_text(child_field))
    next_visited = [*visited, entity_id]
    for child_entity_id, child_fields in child_groups.items():
        if child_entity_id in next_visited:
            # Dependency cycle: the child is an ancestor of the current level.
            # Named fail-closed — recursion can never spin.
            chain = "->".join([*next_visited, child_entity_id])
            return {
                "status": "blocked",
                "reason_code": REASON_CYCLE,
                "detail": {
                    "chain": chain,
                    "entity_ref": child_entity_id,
                    "reference_field": _text(
                        child_fields[0] if child_fields else ""
                    ),
                },
            }
        if child_entity_id in planned_entities:
            # Diamond dependency: the child was already planned under another
            # parent. Register this parent's reference field onto the existing
            # child step so the captured identity binds BOTH parent fields.
            _attach_reference_field(
                steps_out, child_entity_id, child_fields
            )
            continue
        planned_entities.add(child_entity_id)
        child_result = _plan_level(
            behavior_ir=behavior_ir,
            entity_id=child_entity_id,
            reference_fields=child_fields,
            actor_refs=actor_refs,
            depth=depth + 1,
            visited=next_visited,
            planned_entities=planned_entities,
            steps_out=steps_out,
            unresolved_nested=unresolved_nested,
        )
        if _text(child_result.get("status")) != "ok":
            if (
                _text(child_result.get("reason_code"))
                in {REASON_CYCLE, REASON_TOO_DEEP}
            ):
                # Structural hazards: a cycle or unbounded depth must fail the
                # whole chain loudly, never degrade into a placeholder.
                return child_result
            # Unestablishable nested dependency (no create/actor/cleanup):
            # leave the parent's placeholder for the existing binding gate and
            # report the gap — never invent the referenced entity.
            unresolved_nested.append({
                "entity_ref": child_entity_id,
                "reference_field": _text(child_fields[0] if child_fields else ""),
                "reason_code": _text(child_result.get("reason_code")),
            })
            continue

    # ── Own create step (leaves-first: appended after all dependencies) ──
    entity_name = _text(entity.get("name"))
    entry_state = _text(
        entity.get("entry_state") or entity.get("initial_state")
    )
    readback_contract = _readback_contract_for_entity(
        behavior_ir, create_path, entity
    )
    step: dict[str, Any] = {
        "step_id": f"multi_level_create_{entity_name or entity_id}",
        "phase": "fixture",
        "actor_ref": available[0],
        "operation_ref": create_op_id,
        "intent": INTENT_ESTABLISHMENT,
        "protocol_step": "precondition_write",
        "identity_binding_target": primary_field,
        "identity_binding_targets": list(dict.fromkeys(reference_fields)),
        "observe_response_body": True,
        "skip_if_observed_target": primary_field,
        "observation_resolvers": _collection_observation_resolvers(
            behavior_ir, create_path
        ),
        "dependency_level": depth,
        "creates_entity_ref": entity_id,
        "method": "POST",
        "path": create_path,
        "identity_binding_aliases": list(
            dict.fromkeys([*reference_fields, identity_fields[0]])
        ),
        "identity_output_binding": {
            "schema_version": "qualibug.identity-output-binding.v1",
            "status": "FROZEN",
            "entity_ref": entity_id,
            "source_identity_field": identity_fields[0],
            "source_path": identity_fields[0],
            "consumer_targets": list(dict.fromkeys(reference_fields)),
            "alias_targets": list(
                dict.fromkeys([*reference_fields, identity_fields[0]])
            ),
            "source_authority": "behavior_ir.entities.identity_fields",
        },
    }
    if entry_state:
        step["to_state"] = entry_state
        step["state_field"] = _text(
            entity.get("state_field") or entity.get("status_field") or "status"
        )
    elif readback_contract:
        step["to_state"] = _text(entity.get("entry_state"))
        step["state_field"] = _text(readback_contract.get("state_field"))
    steps_out.append(step)
    return {"status": "ok"}


def _attach_reference_field(
    steps_out: list[dict[str, Any]],
    entity_id: str,
    reference_fields: list[str],
) -> None:
    """Register extra parent reference fields on an already planned step."""
    for step in steps_out:
        if not isinstance(step, dict):
            continue
        if _text(step.get("creates_entity_ref")) != entity_id:
            continue
        existing = [
            _text(field)
            for field in _list(step.get("identity_binding_targets"))
            if _text(field)
        ]
        merged = list(dict.fromkeys([*existing, *reference_fields]))
        step["identity_binding_targets"] = merged
        output_binding = _dict(step.get("identity_output_binding"))
        source_field = _text(output_binding.get("source_identity_field"))
        if output_binding:
            output_binding["consumer_targets"] = merged
            output_binding["alias_targets"] = list(
                dict.fromkeys([*merged, source_field])
            )
            step["identity_output_binding"] = output_binding
            step["identity_binding_aliases"] = list(
                output_binding["alias_targets"]
            )
        if not _text(step.get("identity_binding_target")):
            step["identity_binding_target"] = merged[0] if merged else ""
            step["skip_if_observed_target"] = merged[0] if merged else ""
        return


def plan_multi_level_dependency_chain(
    *,
    behavior_ir: dict[str, Any],
    entity_id: str,
    reference_field: str,
    actor_refs: list[str],
    family: str = "",
    multi_service_contract_count: int = 0,
) -> dict[str, Any]:
    """Plan the full dependency DAG establishing ``entity_id``.

    Returns ``{status: PLANNED, steps: [...], identity_binding_target,
    create_operation_ref, entity_ref, observation_resolvers, detail}`` or a
    fail-closed ``{status: BLOCKED, reason_code, ...}``. Steps are ordered
    leaves-first so each created identity binds into ``runtime_bindings``
    before the consuming create materializes.
    """
    ir = _dict(behavior_ir)
    entity = _entity_by_id(_list(ir.get("entities")), _text(entity_id))
    if not entity:
        return _blocked(
            REASON_NO_ENTITY,
            entity_ref=_text(entity_id),
            reference_field=_text(reference_field),
        )
    steps_out: list[dict[str, Any]] = []
    unresolved_nested: list[dict[str, Any]] = []
    planned_entities: set[str] = {_text(entity_id)}
    result = _plan_level(
        behavior_ir=ir,
        entity_id=_text(entity_id),
        reference_fields=[_text(reference_field)],
        actor_refs=[_text(actor) for actor in _list(actor_refs) if _text(actor)],
        depth=1,
        visited=[],
        planned_entities=planned_entities,
        steps_out=steps_out,
        unresolved_nested=unresolved_nested,
    )
    if _text(result.get("status")) != "ok":
        detail = _dict(result.get("detail"))
        return _blocked(
            _text(result.get("reason_code")) or REASON_NO_CREATE,
            **{key: value for key, value in detail.items()},
        )
    if not steps_out:
        return _blocked(REASON_NO_CREATE, entity_ref=_text(entity_id))

    for index, step in enumerate(steps_out, start=1):
        step["step_ordinal"] = index

    subject_step = steps_out[-1]
    return {
        "status": PLANNED,
        "schema_version": SCHEMA_VERSION,
        "steps": steps_out,
        "identity_binding_target": _text(
            subject_step.get("identity_binding_target")
        ),
        "create_operation_ref": _text(subject_step.get("operation_ref")),
        "entity_ref": _text(entity_id),
        "observation_resolvers": _list(
            subject_step.get("observation_resolvers")
        ),
        "reason_code": "",
        "detail": {
            "subject_entity": _text(entity_id),
            "reference_field": _text(reference_field),
            "level_count": max(
                int(_dict(step).get("dependency_level") or 0)
                for step in steps_out
                if isinstance(step, dict)
            ),
            "entity_count": len(steps_out),
            "steps_ordered_leaves_first": True,
            "family": _text(family),
            "unresolved_nested_references": unresolved_nested,
            "multi_service_contract_count": int(multi_service_contract_count or 0),
            "cross_service_transaction_verification": (
                "NOT_MEASURED"
                if int(multi_service_contract_count or 0) > 1
                else "single_service_scope"
            ),
        },
    }


def _index_actors(rows: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and _text(row.get("id"))
    }


__all__ = [
    "BLOCKED",
    "INTENT_ESTABLISHMENT",
    "MAX_DEPENDENCY_DEPTH",
    "NOT_APPLICABLE",
    "PLANNED",
    "REASON_CYCLE",
    "REASON_IDENTITY_AMBIGUOUS",
    "REASON_IDENTITY_MISSING",
    "REASON_NO_ACTOR",
    "REASON_NO_CLEANUP",
    "REASON_NO_CREATE",
    "REASON_NO_ENTITY",
    "REASON_TOO_DEEP",
    "SCHEMA_VERSION",
    "plan_multi_level_dependency_chain",
]
