"""Plan governed fixture chains that establish money-family subject state.

Money-family obligations (idempotency, conservation) compile write experiments
whose control arm must succeed against a subject entity in a source-declared
state — a payment requires an order in ``PENDING_PAYMENT``, a repeat-payment
test requires the order to exist before the first pay, and a balance payment
needs the payer's balance to cover the order. When the subject entity does not
exist in the environment, the control arm fails with
``BLOCKED_CONTROL_ARM_NOT_PROVEN`` and the whole experiment dies before the
rule is tested.

This module plans a *subject-establishment precondition chain*: a fixture-phase
governed write that creates the subject entity through its source-declared
create operation, binds the created identity into the request body reference
field (``orderId``), and optionally advances the entity through source-declared
state transitions when the obligation's property declares a required pre-state
(``from_state``).

SOURCE-DECLARED ONLY
====================
- The subject entity is resolved from the operation's request example
  reference fields (``orderId`` -> entity ``order``) using the same structural
  suffix rule as the existing reference-field resolver. No industry terms, no
  field-name tables.
- The create operation must be a source-declared POST on the entity's
  collection with a request example, an executable actor, and declared
  cleanup — the same authority ``_declared_fixture_setup`` enforces. No
  synthesized create is invented.
- State advancement uses the existing transition graph / reachability
  authority; absent or unreachable target states fail closed as
  ``MONEY_PRECONDITION_STATE_UNREACHABLE``.
- A plan that cannot be built returns ``NOT_APPLICABLE`` — the caller keeps
  its existing fallback (observed-body projection) instead of blocking.
"""
from __future__ import annotations

import logging
from typing import Any

from .runtime_binding_graph import (
    _declared_cleanup_operations as _declared_cleanup_operations,
    _declared_fixture_actor_refs as _declared_fixture_actor_refs,
)
from .target_policy import is_nonproduction_environment
from .validation_read_side_protocol import is_ownership_key as _is_ownership_key

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "qualibug.money-precondition-chain.v1"

PLANNED = "PLANNED"
NOT_APPLICABLE = "NOT_APPLICABLE"
BLOCKED = "BLOCKED"

REASON_NO_SUBJECT_ENTITY = "MONEY_PRECONDITION_SUBJECT_ENTITY_UNRESOLVED"
REASON_NO_CREATE_OPERATION = "MONEY_PRECONDITION_CREATE_OPERATION_MISSING"
REASON_NO_ACTOR = "MONEY_PRECONDITION_ACTOR_UNRESOLVED"
REASON_NO_CLEANUP = "MONEY_PRECONDITION_CLEANUP_MISSING"
REASON_STATE_UNREACHABLE = "MONEY_PRECONDITION_STATE_UNREACHABLE"
REASON_NO_READBACK = "MONEY_PRECONDITION_READBACK_MISSING"

# Money families whose write experiments consume a subject entity that must
# exist before the measured window. Structural signal only: these families
# compile control/treatment writes whose bodies carry entity reference
# fields; a read-only family never needs subject establishment.
MONEY_PRECONDITION_FAMILIES = frozenset({"conservation", "idempotency"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(operation).get("request_example")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    schema = _dict(operation.get("request_schema"))
    for media in _list(_dict(schema.get("content")).values()):
        example = media.get("example")
        if isinstance(example, dict) and example:
            return dict(example)
        for row in _list(_dict(media.get("examples")).values()):
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return dict(value)
    return {}


def _is_reference_field(field: Any) -> bool:
    """True when a body field name is a foreign-key reference slot.

    Mirrors the compiler's reference-field rule (word-boundary identity
    suffix) so subject resolution agrees with observed-body deferral.
    """
    import re

    name = _text(field)
    if not name:
        return False
    if re.search(r"(?:^|_)(?:id|ref|uuid|key)$", name, re.IGNORECASE):
        return True
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return bool(
        re.search(r"(?:^|_)(?:id|ref|uuid|key)$", snake, re.IGNORECASE)
    )


def _identity_suffix_candidate(field: str) -> str:
    import re

    key = _text(field).lower()
    for suffix in ("_id", "id", "_ref", "ref", "_uuid", "uuid", "_key", "key"):
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)].rstrip("_")
    return key


def _subject_entities_from_example(
    example: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[tuple[str, str]]:
    """Resolve all ``(entity_id, reference_field)`` pairs from the example.

    Structural rule: a body field whose name starts with the entity name
    (case-insensitive, singular inflection) and ends in an identity suffix
    names that entity's identity slot. Order follows the documented example
    field order; the caller picks the first field whose entity has an
    establishable create chain.
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
    for key in list(by_name):
        singular = key[:-1] if key.endswith("s") and len(key) > 1 else ""
        if singular and singular not in by_name:
            by_name.setdefault(singular, by_name[key])
        # Structural "es" singularization (addresses -> address) so singular
        # reference fields resolve to plural-named entities. Language
        # morphology only — never industry vocabulary.
        if key.endswith("es") and len(key) > 2:
            es_singular = key[:-2]
            if es_singular and es_singular not in by_name:
                by_name.setdefault(es_singular, by_name[key])

    resolved: list[tuple[str, str]] = []
    for field, value in example.items():
        if not _is_reference_field(field):
            continue
        # Caller-scoped ownership identity fields (userId/ownerId/fromUserId/
        # accountId …) resolve from the arm actor's runtime-observed identity
        # (the ownership_identity_param channel), never from a fixture-phase
        # create.  Treating them as establishable subjects makes the chain try
        # to create a user account (which has no source-declared POST) and
        # block with MONEY_PRECONDITION_CREATE_OPERATION_MISSING — the exact
        # first-loss observed on the refund/balance write examples.
        if _is_ownership_key(field):
            continue
        candidate = _identity_suffix_candidate(field)
        entity = by_name.get(candidate)
        if entity is None:
            continue
        entity_id = _text(entity.get("id"))
        if not entity_id:
            continue
        # The example value must be a placeholder (documented example never
        # carries a real identity): a concrete UUID in the example is a
        # documentation fixture, not a runtime identity.
        if isinstance(value, str) and _text(value):
            resolved.append((entity_id, _text(field)))
    return resolved


def _create_operation_for_entity(
    behavior_ir: dict[str, Any],
    entity: dict[str, Any],
) -> dict[str, Any]:
    """Find the source-declared create (POST collection) for an entity.

    The collection path is derived from the entity's own declared fields /
    HTTP surface: a POST whose path equals the entity's collection (no path
    placeholders), that carries a request example and an operation id, is the
    create. Nothing is synthesized.
    """
    entity_name = _text(entity.get("name"))
    if not entity_name:
        return {}
    from .experiment_runtime_support import normalize_path_placeholders

    collection = ""
    # Prefer an explicitly declared collection path on the entity.
    for key in ("collection_path", "http_collection", "collection"):
        value = _text(entity.get(key))
        if value:
            collection = normalize_path_placeholders(value)
            break
    candidates: list[dict[str, Any]] = []
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
        if collection and op_path != collection:
            continue
        if not _text(op.get("id")):
            continue
        if not _request_example(op):
            continue
        if not collection:
            # No declared collection: the POST must name the entity in its
            # final path segment (orders -> /api/orders).
            segments = [s for s in op_path.strip("/").split("/") if s]
            if not segments or _text(segments[-1]).lower() not in {
                entity_name.lower(),
                entity_name.lower() + "s",
            }:
                continue
        candidates.append(op)
    if not candidates:
        return {}
    candidates.sort(key=lambda row: _text(row.get("path") or row.get("raw_path")))
    return candidates[0]


def _state_goal_from_property(property_spec: dict[str, Any]) -> str:
    """The subject pre-state the obligation's property requires, if any."""
    from .assertion_dsl_base import _state_token as normalize

    goal = _text(property_spec.get("from_state"))
    if not goal:
        goal = _text(
            _dict(_dict(property_spec.get("expression")).get("operands") and
                  _dict(_dict(property_spec.get("expression")).get("operands")[0]).get(
                      "from_state"))
        )
    normalized = normalize(goal)
    if normalized in ("", "unknown_state", "unknown"):
        return ""
    return normalized


def _readback_contract_for_entity(
    behavior_ir: dict[str, Any],
    create_path: str,
    entity: dict[str, Any],
) -> dict[str, Any]:
    """A source-declared readback of the created entity.

    The precondition executor observes the governed create through an
    entity-scoped GET when one is declared (GET /api/orders/{id}), falling
    back to the entity's collection read (GET /api/orders). Structural only:
    path identity is matched by placeholder position, never by invented ids.
    """
    from .experiment_runtime_support import (
        normalize_path_placeholders,
        path_has_placeholders,
    )

    entity_name = _text(entity.get("name"))
    resolvers: list[dict[str, str]] = []
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
            continue
        op_id = _text(op.get("id"))
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        )
        if not op_id or not op_path.startswith("/"):
            continue
        # Entity-scoped read: one placeholder in the final segment and the
        # path prefix matches the create collection (orders/{id}).
        segments = [s for s in op_path.strip("/").split("/") if s]
        if not segments:
            continue
        last = segments[-1]
        is_identity_read = (
            last.startswith("{") and last.endswith("}")
        ) or (last.startswith(":") and len(segments) >= 2)
        if not is_identity_read:
            continue
        prefix = "/" + "/".join(segments[:-1])
        create_collection = normalize_path_placeholders(
            _text(create_path)
        ).rstrip("/")
        if prefix != create_collection:
            continue
        resolvers.append({
            "operation_ref": op_id,
            "method": "GET",
            "path": op_path,
            "binding_semantics": "entity_scoped",
        })
        break
    if not resolvers:
        # Collection read fallback (GET /api/orders) for existence proof.
        for op in _list(behavior_ir.get("operations")):
            if not isinstance(op, dict):
                continue
            if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
                continue
            op_id = _text(op.get("id"))
            op_path = normalize_path_placeholders(
                _text(op.get("path") or op.get("raw_path"))
            )
            if not op_id or not op_path.startswith("/"):
                continue
            if path_has_placeholders(op_path):
                continue
            if op_path != normalize_path_placeholders(_text(create_path)).rstrip("/"):
                continue
            resolvers.append({
                "operation_ref": op_id,
                "method": "GET",
                "path": op_path,
                "binding_semantics": "collection",
            })
            break
    if not resolvers:
        return {}
    return {
        "schema_version": "qualibug.readback-contract.v1",
        "required_fields": [
            {"field": identity} for identity in _entity_identity_fields(entity)
        ],
        "resolver_operations": resolvers,
        "state_field": _text(
            entity.get("state_field") or entity.get("status_field") or "status"
        ),
    }


def _entity_identity_fields(entity: dict[str, Any]) -> list[str]:
    fields = [
        _text(value)
        for value in _list(entity.get("identity_fields"))
        if _text(value)
    ]
    if not fields:
        for raw in _list(entity.get("identity_keys")):
            row = _dict(raw)
            fields.extend(
                _text(value) for value in _list(row.get("columns")) if _text(value)
            )
    return list(dict.fromkeys(fields))


def plan_money_family_precondition(
    *,
    behavior_ir: dict[str, Any],
    operation: dict[str, Any],
    actor_refs: list[str],
    property_spec: dict[str, Any] | None = None,
    family: str = "",
    environment_type: str = "",
) -> dict[str, Any]:
    """Plan a fixture-phase subject-establishment chain for a money write.

    Returns ``{status: PLANNED, steps: [...], identity_binding_target,
    create_operation_ref, detail}`` or ``{status: NOT_APPLICABLE}`` when no
    source-declared chain exists. A BLOCKED status is returned only when the
    subject entity IS resolvable but its establishment cannot be built
    (missing create / actor / cleanup / unreachable state) — the caller
    decides whether that blocks the experiment or degrades to its existing
    observed-body fallback.
    """
    ir = _dict(behavior_ir)
    nonproduction = is_nonproduction_environment(environment_type)
    example = _request_example(operation)
    subject_pairs = _subject_entities_from_example(example, ir)
    if not subject_pairs:
        return {
            "status": NOT_APPLICABLE,
            "reason_code": REASON_NO_SUBJECT_ENTITY,
            "steps": [],
            "identity_binding_target": "",
        }

    entities = _list(ir.get("entities"))
    actors = _index_by_id(_list(ir.get("actors")))
    available_actors = [
        actor_id
        for actor_id in actor_refs
        if actor_id in actors
    ]
    # Prefer the first subject whose entity has a source-declared create op,
    # executable actor, and declared cleanup. A request body may carry several
    # reference fields (refund-to-balance: userId + orderId); the subject of
    # the money write is the entity the create chain can establish.
    unresolved_reasons: list[dict[str, Any]] = []
    for entity_id, reference_field in subject_pairs:
        entity = next(
            (
                row
                for row in entities
                if isinstance(row, dict) and _text(row.get("id")) == entity_id
            ),
            {},
        )
        if not entity:
            continue
        create_op = _create_operation_for_entity(ir, entity)
        if not create_op:
            unresolved_reasons.append({
                "entity_ref": entity_id,
                "reference_field": reference_field,
                "reason_code": REASON_NO_CREATE_OPERATION,
            })
            continue
        create_op_id = _text(create_op.get("id"))
        chain_actors = list(available_actors)
        if not chain_actors:
            declared = _declared_fixture_actor_refs(create_op, behavior_ir=ir)
            chain_actors = [
                actor_id for actor_id in declared if actor_id in actors
            ]
        if not chain_actors:
            unresolved_reasons.append({
                "entity_ref": entity_id,
                "reference_field": reference_field,
                "reason_code": REASON_NO_ACTOR,
                "create_operation_ref": create_op_id,
            })
            continue
        cleanup = _declared_cleanup_operations(
            _text(create_op.get("path") or create_op.get("raw_path")),
            behavior_ir=ir,
        )
        if not cleanup and not nonproduction:
            unresolved_reasons.append({
                "entity_ref": entity_id,
                "reference_field": reference_field,
                "reason_code": REASON_NO_CLEANUP,
                "create_operation_ref": create_op_id,
            })
            continue

        create_path = _text(create_op.get("path") or create_op.get("raw_path"))
        # ── Multi-level dependency DAG ──
        # The subject's own create operation may carry further reference
        # fields (order create -> addressId; items[].sku -> products). The
        # shared multi-level planner resolves the full dependency DAG
        # (leaves-first, cycle-detected, depth-capped, observe-first
        # resolvers) instead of this module planning a single create step.
        from .multi_level_dependency_chain import (
            BLOCKED as _ML_CHAIN_BLOCKED,
            PLANNED as _ML_CHAIN_PLANNED,
            plan_multi_level_dependency_chain as _plan_multi_level_chain,
        )

        chain_result = _plan_multi_level_chain(
            behavior_ir=ir,
            entity_id=entity_id,
            reference_field=reference_field,
            actor_refs=chain_actors,
            family=_text(family),
            environment_type=environment_type,
        )
        if _text(chain_result.get("status")) == _ML_CHAIN_BLOCKED:
            return {
                "status": BLOCKED,
                "reason_code": _text(chain_result.get("reason_code"))
                or REASON_NO_CREATE_OPERATION,
                "steps": [],
                "identity_binding_target": reference_field,
                "entity_ref": entity_id,
                "create_operation_ref": create_op_id,
                "chain_detail": _dict(chain_result.get("detail")),
            }
        if _text(chain_result.get("status")) != _ML_CHAIN_PLANNED:
            unresolved_reasons.append({
                "entity_ref": entity_id,
                "reference_field": reference_field,
                "reason_code": REASON_NO_CREATE_OPERATION,
            })
            continue
        steps = [dict(step) for step in _list(chain_result.get("steps"))]
        if not steps:
            unresolved_reasons.append({
                "entity_ref": entity_id,
                "reference_field": reference_field,
                "reason_code": REASON_NO_CREATE_OPERATION,
            })
            continue
        # Backward-compatible subject step identity: the final (subject) step
        # keeps the historical step id / intent so existing compiled
        # experiments and receipts stay stable; nested dependency steps keep
        # their own ids.
        subject_step = steps[-1]
        subject_step["step_id"] = "money_precondition_create"
        subject_step["intent"] = "money_subject_establishment"
        # Identity alias coverage: the subject identity is captured under the
        # request reference field (orderId), but downstream steps address the
        # same entity through the entity's own identity field spellings (a
        # state-advancement step cancels via /api/orders/{id}/cancel). Declare
        # both spellings so the flow-data freeze check and the precondition
        # executor register every token the chain consumes.
        identity_output_binding = _dict(
            subject_step.get("identity_output_binding")
        )
        subject_identity_aliases = [
            _text(value)
            for value in _list(identity_output_binding.get("alias_targets"))
            if _text(value)
        ]
        subject_step["identity_binding_aliases"] = subject_identity_aliases

        # Optional state advancement: when the property declares a required
        # pre-state (e.g. CANCELLED for a pay-after-cancel rule), plan the
        # transition path from the entity's entry state through the source
        # transition graph. Unreachable goals fail closed with a named reason.
        state_goal = _state_goal_from_property(_dict(property_spec))
        if state_goal:
            from .state_precondition_planner import plan_state_precondition

            state_result = plan_state_precondition(
                behavior_ir=ir,
                from_state=state_goal,
                actors=chain_actors,
            )
            if _text(state_result.get("status")) != "PLANNED":
                return {
                    "status": BLOCKED,
                    "reason_code": REASON_STATE_UNREACHABLE,
                    "steps": steps,
                    "identity_binding_target": reference_field,
                    "create_operation_ref": create_op_id,
                    "state_goal": state_goal,
                    "state_reason": _text(state_result.get("reason_code")),
                }
            _state_base = len(steps)
            for index, edge in enumerate(
                _list(state_result.get("steps")), start=_state_base + 1
            ):
                steps.append(
                    {
                        "step_id": f"money_precondition_state_{index - _state_base}",
                        "phase": "fixture",
                        "actor_ref": _text(edge.get("actor_ref")) or chain_actors[0],
                        "operation_ref": _text(edge.get("operation_ref")),
                        "intent": "money_subject_state_advancement",
                        "protocol_step": "precondition_write",
                        "from_state": _text(edge.get("from_state")),
                        "to_state": _text(edge.get("to_state")),
                        "step_ordinal": index,
                        # The advancement step targets the SAME subject entity
                        # (cancel /api/orders/{id}/cancel names the entity's
                        # identity field directly); declare the alias spellings
                        # so the flow-data freeze check and the executor bind
                        # the path placeholder from the captured identity.
                        "identity_binding_aliases": subject_identity_aliases,
                        "identity_input_binding": {
                            "schema_version": "qualibug.identity-input-binding.v1",
                            "status": "FROZEN",
                            "producer_step_id": _text(
                                subject_step.get("step_id")
                            ),
                            "producer_output_field": _text(
                                identity_output_binding.get(
                                    "source_identity_field"
                                )
                            ),
                            "consumer_targets": subject_identity_aliases,
                            "source_authority": _text(
                                identity_output_binding.get(
                                    "source_authority"
                                )
                            ),
                        },
                    }
                )

        chain_detail = _dict(chain_result.get("detail"))
        return {
            "status": PLANNED,
            "schema_version": SCHEMA_VERSION,
            "steps": steps,
            "identity_binding_target": reference_field,
            "create_operation_ref": create_op_id,
            "entity_ref": entity_id,
            "observation_resolvers": _list(
                chain_result.get("observation_resolvers")
            ),
            "reason_code": "",
            "detail": {
                "subject_entity": entity_id,
                "reference_field": reference_field,
                "create_path": create_path,
                "cleanup_operation_count": len(cleanup),
                "family": _text(family),
                "chain_levels": int(chain_detail.get("level_count") or 0),
                "chain_entity_count": int(chain_detail.get("entity_count") or 0),
                "unresolved_nested_references": _list(
                    chain_detail.get("unresolved_nested_references")
                ),
            },
        }

    # No subject field had an establishable chain. Surface the first named
    # gap so the caller sees WHY establishment is impossible instead of a
    # silent NOT_APPLICABLE.
    first_gap = unresolved_reasons[0] if unresolved_reasons else {}
    return {
        "status": BLOCKED,
        "reason_code": _text(first_gap.get("reason_code"))
        or REASON_NO_CREATE_OPERATION,
        "steps": [],
        "identity_binding_target": _text(first_gap.get("reference_field")),
        "entity_ref": _text(first_gap.get("entity_ref")),
        "unresolved_subjects": unresolved_reasons,
    }


def _index_by_id(rows: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and _text(row.get("id"))
    }


__all__ = [
    "BLOCKED",
    "MONEY_PRECONDITION_FAMILIES",
    "NOT_APPLICABLE",
    "PLANNED",
    "SCHEMA_VERSION",
    "plan_money_family_precondition",
]
