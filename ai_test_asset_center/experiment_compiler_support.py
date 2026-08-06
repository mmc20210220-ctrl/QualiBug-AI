"""Experiment compile helpers: state context, fixture binding, cleanup specs.

Extracted from ``experiment_compiler_base``. Symbols are re-exported from
``experiment_compiler_base`` for star-import compatibility.
"""
from __future__ import annotations

import re
from typing import Any

from .behavior_ir_core import _singular_token
from .real_id_resolver import collection_path, normalize_path_placeholders
from .real_id_resolver_base import _LOOKUP_VERB_SEGMENTS
from .runtime_binding_graph import _declared_fixture_setup


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _index_by_id(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and _text(node.get("id")):
            out[_text(node.get("id"))] = node
    return out


def _is_unresolvable_actor_secret_ref(secret_ref: str) -> bool:
    ref = _text(secret_ref).lower()
    if ref.startswith("secret_ref:actor:"):
        return True
    # secret_ref:test_accounts:<X> resolves only when X is a real account
    # key. A placeholder actor id (the product stable-id form bir_<hex>)
    # has no credential anywhere; real account keys (emails, user names)
    # contain non-hex characters. Treating the stable-id form as
    # unresolvable keeps permission-matrix actors from being planned for
    # execution and then failing at token resolution.
    if ref.startswith("secret_ref:test_accounts:"):
        account_key = ref[len("secret_ref:test_accounts:"):]
        stripped = re.sub(
            r"^(?:bir_|cf_|obl_|fix_|rel_|node_|sess_)", "", account_key
        )
        if stripped and re.fullmatch(r"[0-9a-f]+", stripped):
            return True
    return False


def _actor_is_executable(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(
        actor.get("credential_secret_ref")
        or actor.get("secret_ref")
    )
    return bool(secret_ref and not _is_unresolvable_actor_secret_ref(secret_ref))


def _state_semantic_value(state: dict[str, Any]) -> str:
    for field in ("value", "name", "state", "status", "label", "code"):
        value = _text(state.get(field))
        if value:
            return value
    return ""


def _state_match_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def _resolve_state_compile_context(
    *,
    behavior_ir: dict[str, Any],
    property_spec: dict[str, Any],
    operation_ref: str,
    required_actors: list[str],
    required_fixtures: list[str],
) -> tuple[dict[str, Any], list[str], list[str], str]:
    """Resolve state refs and one source-permitted runtime actor.

    State obligations are emitted from an explicit transition relation, but the
    generic compiler historically received only state node IDs, no actor, and a
    synthetic ``entity_in_state:*`` fixture that the runtime cannot construct.
    This resolver converts only source-backed IR facts into executable inputs.
    It never invents a state, account, or fixture.
    """

    prop = dict(_dict(property_spec))
    states = _index_by_id(_list(_dict(behavior_ir).get("states")))
    for ref_field, value_field in (
        ("from_state_ref", "from_state"),
        ("to_state_ref", "to_state"),
    ):
        if _text(prop.get(value_field)):
            continue
        state_ref = _text(prop.get(ref_field))
        if not state_ref:
            # Invariant-based state obligations don't have explicit state refs
            prop[value_field] = "unknown_state"
            continue
        state_value = _state_semantic_value(states.get(state_ref) or {})
        if not state_value:
            # Fallback: use the state ref itself as value
            state_value = state_ref.split("_")[-1] if "_" in state_ref else state_ref
            if not state_value or len(state_value) < 2:
                return prop, required_actors, required_fixtures, (
                    f"state_value_unresolved:{state_ref or ref_field}"
                )
        prop[value_field] = state_value

    if _text(prop.get("from_state")).casefold() == _text(prop.get("to_state")).casefold():
        if _text(prop.get("from_state")) != "unknown_state":
            return prop, required_actors, required_fixtures, "state_transition_no_change"

    actors = _index_by_id(_list(_dict(behavior_ir).get("actors")))
    resolved_actors = [
        actor_id
        for actor_id in required_actors
        if actor_id in actors and _actor_is_executable(actors[actor_id])
    ]
    if not resolved_actors:
        permitted_actor_ids = {
            _text(relation.get("actor_ref") or relation.get("from_ref"))
            for relation in _list(_dict(behavior_ir).get("relations"))
            if isinstance(relation, dict)
            and _text(relation.get("relation_type")) == "permits"
            and _text(relation.get("operation_ref")) == _text(operation_ref)
            and _text(relation.get("status")) not in {"conflicting", "unsupported"}
            and _text(relation.get("actor_ref") or relation.get("from_ref"))
        }
        ranked = sorted(
            (
                (
                    0 if _text(actor.get("account_ref")) else 1,
                    0 if actor.get("runtime_bound") is True else 1,
                    actor_id,
                )
                for actor_id, actor in actors.items()
                if actor_id in permitted_actor_ids and _actor_is_executable(actor)
            )
        )
        if ranked:
            resolved_actors = [ranked[0][2]]
    if not resolved_actors:
        return prop, [], required_fixtures, "state_transition_actor_unresolved"

    prop.setdefault("actor_ref", resolved_actors[0])
    effective_fixtures = [
        fixture
        for fixture in required_fixtures
        if not _text(fixture).startswith("entity_in_state:")
    ]
    return prop, resolved_actors, effective_fixtures, ""


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _collection_path_for_member_operation(path: str) -> str:
    """Strip a trailing identity segment: ``/api/cart/items/{id}`` → ``/api/cart/items``."""
    normalized = normalize_path_placeholders(_text(path)).rstrip("/")
    if not normalized:
        return ""
    segments = [part for part in normalized.strip("/").split("/") if part]
    if not segments:
        return ""
    last = segments[-1]
    if (last.startswith("{") and last.endswith("}")) or last.startswith(":"):
        return "/" + "/".join(segments[:-1])
    return normalized


def _source_request_example(
    operation: dict[str, Any],
    *,
    sibling_operations: list[Any] | None = None,
) -> dict[str, Any]:
    from .runtime_binding_graph import _tokenize_placeholder_identity_values
    direct = _dict(operation).get("request_example")
    if isinstance(direct, dict) and direct:
        return _tokenize_placeholder_identity_values(dict(direct))
    request_schema = _dict(_dict(operation).get("request_schema"))
    content = _dict(request_schema.get("content"))
    for media in content.values():
        if not isinstance(media, dict):
            continue
        example = media.get("example")
        if isinstance(example, dict) and example:
            return _tokenize_placeholder_identity_values(dict(example))
        examples = _dict(media.get("examples"))
        for row in examples.values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return _tokenize_placeholder_identity_values(dict(value))
    # Fallback: unique sibling POST on the same collection that already carries
    # a source-attested example. PATCH/PUT docs often omit JSON while the
    # collection create POST documents fields. Ambiguous siblings stay empty.
    siblings = list(sibling_operations or [])
    if not siblings:
        siblings = list(_list(operation.get("_ir_operations")))
    method = _text(operation.get("method")).upper()
    if method not in {"PUT", "PATCH"} or not siblings:
        return {}
    op_collection = _collection_path_for_member_operation(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    if not op_collection:
        return {}
    matches: list[dict[str, Any]] = []
    for candidate in siblings:
        if not isinstance(candidate, dict):
            continue
        if _text(candidate.get("method")).upper() != "POST":
            continue
        c_path = normalize_path_placeholders(
            _text(candidate.get("path") or candidate.get("raw_path"))
        ).rstrip("/")
        if c_path != op_collection:
            continue
        c_example = _dict(candidate.get("request_example"))
        if c_example:
            matches.append(dict(c_example))
    if len(matches) == 1:
        return matches[0]
    return {}


def _operation_entity_refs(
    *,
    behavior_ir: dict[str, Any],
    operation_ref: str,
    relation_types: set[str],
) -> set[str]:
    refs: set[str] = set()
    for relation in _list(_dict(behavior_ir).get("relations")):
        if not isinstance(relation, dict):
            continue
        if _text(relation.get("status")) in {"conflicting", "unsupported"}:
            continue
        if _text(relation.get("operation_ref")) != _text(operation_ref):
            continue
        if _text(relation.get("relation_type")) not in relation_types:
            continue
        for field in ("from_ref", "to_ref"):
            ref = _text(relation.get(field))
            if ref and ref != _text(operation_ref):
                refs.add(ref)
    return refs


def _compensates_create_operation(
    *,
    behavior_ir: dict[str, Any],
    cleanup_ref: str,
    create_ref: str,
) -> bool:
    for relation in _list(_dict(behavior_ir).get("relations")):
        if not isinstance(relation, dict):
            continue
        if _text(relation.get("status")) in {"conflicting", "unsupported"}:
            continue
        if _text(relation.get("relation_type")) != "compensates":
            continue
        if _text(relation.get("operation_ref") or relation.get("from_ref")) != _text(cleanup_ref):
            continue
        if _text(relation.get("to_ref")) == _text(create_ref):
            return True
        for effect in _list(relation.get("effects")):
            if (
                isinstance(effect, dict)
                and _text(effect.get("cleanup_target_operation_ref")) == _text(create_ref)
            ):
                return True
    return False


def _source_declared_control_fixture_binding(
    *,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    if (
        _text(operation.get("method")).upper() not in {"GET", "HEAD"}
        or not control_actor_ref
    ):
        return {}
    collection_path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    ).rstrip("/")
    if (
        not collection_path.startswith("/")
        or "{" in collection_path
        or ":" in collection_path
        or not _list(operation.get("source_refs"))
    ):
        return {}

    read_entities = _operation_entity_refs(
        behavior_ir=behavior_ir,
        operation_ref=operation_ref,
        relation_types={"observes", "scopes"},
    )
    if not read_entities:
        return {}

    cleanup_targets: list[str] = []
    for candidate in _list(_dict(behavior_ir).get("operations")):
        if not isinstance(candidate, dict):
            continue
        candidate_path = normalize_path_placeholders(
            _text(candidate.get("path") or candidate.get("raw_path"))
        ).rstrip("/")
        if not (
            _text(candidate.get("method")).upper() in {"DELETE", "POST", "PATCH", "PUT"}
            and candidate_path.startswith(collection_path + "/")
            and ("{" in candidate_path or ":" in candidate_path)
        ):
            continue
        cleanup_targets.extend(
            re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", candidate_path)
        )
        cleanup_targets.extend(
            re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", candidate_path)
        )
    for target in list(dict.fromkeys([*cleanup_targets, "id"])):
        detail_operation = {
            **operation,
            "path": f"{collection_path}/{{{target}}}",
            "raw_path": f"{collection_path}/{{{target}}}",
        }
        setup = _declared_fixture_setup(
            detail_operation,
            target=target,
            behavior_ir=behavior_ir,
        )
        create_ref = _text(setup.get("operation_ref"))
        if not create_ref or control_actor_ref not in set(_list(setup.get("actor_refs"))):
            continue
        create_entities = _operation_entity_refs(
            behavior_ir=behavior_ir,
            operation_ref=create_ref,
            relation_types={"produces", "transitions"},
        )
        if not (read_entities & create_entities):
            continue
        cleanup_operations = [
            dict(row)
            for row in _list(setup.get("cleanup_operations"))
            if isinstance(row, dict)
        ]
        if not cleanup_operations:
            continue
        if not any(
            _compensates_create_operation(
                behavior_ir=behavior_ir,
                cleanup_ref=_text(row.get("operation_ref")),
                create_ref=create_ref,
            )
            for row in cleanup_operations
        ):
            continue
        return {
            "target": target,
            "target_path": f"/{{{target}}}",
            "status": "runtime_resolvable",
            "source_priority": "source_declared_control_fixture",
            "resolver_operations": [{
                "operation_ref": operation_ref,
                "method": _text(operation.get("method")).upper(),
                "path": collection_path,
            }],
            "fixture_setup": setup,
            "force_fixture_setup": True,
            "required_fixture_id": "control_resource",
            "fixture_owner_actor_ref": control_actor_ref,
            "value_fingerprint": "",
        }
    return {}


def _actor_owns_operation(
    *,
    behavior_ir: dict[str, Any],
    actor_ref: str,
    operation_ref: str,
) -> bool:
    """Source-declared caller-scope proof: an accepted ``owns`` relation must
    tie THIS actor to THIS operation. Relations owned only by other actors
    never qualify — that would cross-contaminate the arm boundary."""
    if not actor_ref or not operation_ref:
        return False
    for relation in _list(_dict(behavior_ir).get("relations")):
        if not isinstance(relation, dict):
            continue
        if _text(relation.get("status")) in {"conflicting", "unsupported"}:
            continue
        if _text(relation.get("relation_type")) != "owns":
            continue
        if actor_ref not in {
            _text(relation.get("from_ref")),
            _text(relation.get("actor_ref")),
        }:
            continue
        if operation_ref in {
            _text(relation.get("to_ref")),
            _text(relation.get("operation_ref")),
        }:
            return True
    return False


def _entity_declares_identity_field(
    entities: list[Any],
    entity_refs: set[str],
    field_keys: set[str],
) -> bool:
    """An observed entity must declare the ownership field in its source
    fields, otherwise reading that field would be an assumption, not
    evidence. Field entries may be plain names or dicts with a ``name``."""
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if (
            _text(entity.get("id")) not in entity_refs
            and _text(entity.get("name")) not in entity_refs
        ):
            continue
        for field in _list(entity.get("fields")):
            name = _text(field.get("name") if isinstance(field, dict) else field)
            if name and _field_key(name) in field_keys:
                return True
    return False


def _owned_entity_identity_resolver(
    *,
    control_actor_ref: str,
    identity_target: str,
    ownership_param: str,
    behavior_ir: dict[str, Any],
    actors: Any,
    preferred_operation_ref: str = "",
) -> dict[str, Any]:
    """Resolve an arm actor's owner identity from a caller-scoped owned read.

    Fallback when the Behavior IR declares no ``*/me`` operation: the
    isolation protocol needs the resource owner's identity, and a collection
    GET that the source ties to the control actor via an ``owns`` relation —
    whose observed entity declares the ownership field — returns rows owned
    by the caller, so the ownership field on those rows IS the caller's own
    identity as observed evidence (never inferred).

    Every validation dimension is fail-closed; any gap returns {} and the
    caller keeps the visible owner_identity_resolver_missing block:
    1. the control actor must be present in the actor registry;
    2. the resolver must be a source-declared GET/HEAD collection operation
       (no path placeholders);
    3. a source-declared ``owns`` relation must tie THIS control actor to
       THIS operation (another actor's owned read is cross-contamination);
    4. an entity observed/scoped by the operation must declare the
       ownership/identity field in its source fields.
    """
    if not control_actor_ref or control_actor_ref not in actors:
        return {}
    field_keys = {
        key
        for key in (_field_key(ownership_param), _field_key(identity_target))
        if key
    }
    if not field_keys:
        return {}
    ir = _dict(behavior_ir)
    entities = _list(ir.get("entities"))
    candidates: list[dict[str, Any]] = []
    for op in _list(ir.get("operations")):
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in {"GET", "HEAD"}:
            continue
        op_id = _text(op.get("id"))
        if not op_id:
            continue
        path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        ).rstrip("/")
        if not path.startswith("/") or "{" in path or ":" in path:
            continue
        if not _list(op.get("source_refs")):
            continue
        if not _actor_owns_operation(
            behavior_ir=ir,
            actor_ref=control_actor_ref,
            operation_ref=op_id,
        ):
            continue
        read_entities = _operation_entity_refs(
            behavior_ir=ir,
            operation_ref=op_id,
            relation_types={"observes", "scopes"},
        )
        if not read_entities:
            continue
        if not _entity_declares_identity_field(entities, read_entities, field_keys):
            continue
        candidates.append({
            "operation_ref": op_id,
            "method": method,
            "path": path,
            "declaring_actor_ref": control_actor_ref,
            "binding_semantics": "caller_scoped",
            "identity_extraction": "owner_field_consensus",
        })
    if not candidates:
        return {}
    # Deterministic choice: the obligation's own operation first (a
    # collection-read isolation probe reads exactly the owned collection
    # under test), then stable id order.
    candidates.sort(
        key=lambda row: (
            row["operation_ref"] != _text(preferred_operation_ref),
            row["operation_ref"],
        )
    )
    return candidates[0]


# Generic lookup verbs that may close an entity-scoped read path
# (GET /api/users/admin/search / export / check) without naming the entity in
# the final segment. Universal system vocabulary — never industry terms.
# Single source of truth: real_id_resolver_base._LOOKUP_VERB_SEGMENTS.


def _reference_field_resolver(
    *,
    behavior_ir: dict[str, Any],
    reference_fields: list[Any],
) -> dict[str, Any]:
    """Resolve foreign-key reference body fields from the referenced
    entity's own caller-scoped collection read.

    A documented request example is a documentation fixture: its scalar
    values are trustworthy, but its reference values (``orderId``,
    ``paymentId``) point at entities that may not exist in the environment
    and are never reliable on a live target. Each reference field names an
    entity structurally (``orderId`` -> entity ``order``); a source-declared
    collection ``GET`` on that entity's collection returns real rows of
    that entity, and the runtime projects the row's identity field into the
    body field (``orderId`` <- ``id``).

    Entity resolution is purely structural — the body field name must start
    with the entity name (case-insensitive) and end in an identity suffix
    (``id``/``ref``/``uuid``/``key``); the identity field comes from the
    entity's own declared identity/IDENTITY-typed field. No industry terms
    and no field-name translation tables are involved, so any system whose
    source material declares an entity and a collection read binds the same
    way. Every validation dimension is fail-closed: a field whose entity
    cannot be resolved, or whose entity has no source-declared collection
    read, contributes no binding and the caller keeps its visible block.
    """
    ir = _dict(behavior_ir)
    entities = _list(ir.get("entities"))
    by_name: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        ent_name = _text(entity.get("name"))
        if ent_name:
            by_name.setdefault(ent_name.lower(), entity)
        for alias in _list(entity.get("source_entity_names")):
            if _text(alias):
                by_name.setdefault(_text(alias).lower(), entity)
    # Singular-form aliases: a body field userId names entity ``user`` while
    # the source entity may be declared as ``users``. The structural suffix
    # rule (identity suffix + candidate) stays authoritative; this only adds
    # the inflection-insensitive lookup (users -> user).
    for key in list(by_name):
        singular = _singular_token(key)
        if singular and singular != key:
            by_name.setdefault(singular, by_name[key])
    identity_suffixes = ("_id", "id", "_ref", "ref", "_uuid", "uuid", "_key", "key")

    resolved: dict[str, Any] = {}
    for field in reference_fields:
        key = _text(field).lower()
        candidate = ""
        suffix = ""
        for s in identity_suffixes:
            if key.endswith(s) and len(key) > len(s):
                candidate = key[: -len(s)].rstrip("_")
                suffix = s
                break
        if not suffix or not candidate:
            continue
        entity = by_name.get(candidate)
        if entity is None:
            continue
        ent_id = _text(entity.get("id"))
        if not ent_id:
            continue
        # The referenced entity's collection read: a source-declared GET/HEAD
        # whose final path segment names the entity itself (orders -> the
        # orders collection). Segment-identity keeps health/status/report
        # endpoints (which also observe the entity in the relation graph but
        # return no entity rows) out of the resolver slot. Entity-scoped
        # lookup verbs (GET /api/users/admin/search — the last ENTITY-NAMED
        # segment is the collection, the final segment is a generic lookup
        # verb) are equally valid row sources when they observe the entity:
        # a target whose collection exposes only search/export/check reads
        # would otherwise leave every reference field unresolved.
        ent_names = {
            _text(alias).lower()
            for alias in [
                *_list(entity.get("source_entity_names")),
                _text(entity.get("name")),
            ]
            if _text(alias)
        }
        resolver_op: dict[str, Any] = {}
        for op in _list(ir.get("operations")):
            if not isinstance(op, dict):
                continue
            method = _text(op.get("method")).upper()
            if method not in {"GET", "HEAD"}:
                continue
            path = normalize_path_placeholders(
                _text(op.get("path") or op.get("raw_path"))
            ).rstrip("/")
            if not path or "{" in path or ":" in path:
                continue
            segments = [segment for segment in path.split("/") if segment]
            if not segments:
                continue
            final_lower = segments[-1].lower()
            if final_lower in ent_names:
                pass  # exact collection read — the canonical slot
            elif final_lower in _LOOKUP_VERB_SEGMENTS and any(
                segment.lower() in ent_names for segment in segments[:-1]
            ):
                pass  # entity-scoped lookup read (users/admin/search)
            else:
                continue
            if not _list(op.get("source_refs")):
                continue
            op_id = _text(op.get("id"))
            read_entities = _operation_entity_refs(
                behavior_ir=ir,
                operation_ref=op_id,
                relation_types={"observes", "scopes"},
            )
            if ent_id not in read_entities and _text(entity.get("name")) not in read_entities:
                continue
            resolver_op = {
                "operation_ref": op_id,
                "method": method,
                "path": path,
            }
            break
        if not resolver_op:
            continue
        # Identity field of the entity: a declared identity field first
        # (id/order_no), then the first IDENTITY-typed field, else ``id``.
        identity = ""
        for f in _list(entity.get("fields")):
            if not isinstance(f, dict):
                continue
            fname = _text(f.get("name"))
            if fname and _field_key(fname) == "id":
                identity = fname
                break
        if not identity:
            for f in _list(entity.get("fields")):
                if isinstance(f, dict) and _text(f.get("semantic_type")) == "IDENTITY":
                    identity = _text(f.get("name"))
                    if identity:
                        break
        if not identity:
            identity = "id"
        resolved[field] = {
            "entity_ref": ent_id,
            "source_field": identity,
        }
        # One resolver read serves one entity; first declared wins.
        if len(resolved) == 1:
            resolved["_resolver"] = resolver_op
    if not resolved or "_resolver" not in resolved:
        return {}
    resolver_op = resolved.pop("_resolver")
    mapped_fields = sorted(resolved.keys())
    return {
        "operation_ref": _text(resolver_op.get("operation_ref")),
        "method": _text(resolver_op.get("method")),
        "path": _text(resolver_op.get("path")),
        "binding_semantics": "caller_scoped",
        "projection_fields": mapped_fields,
        "reference_mapping": resolved,
    }


def _observed_write_body_resolver(
    *,
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    required_fields: list[Any],
    projection_fields: list[Any],
    reference_fields: list[Any] | None = None,
) -> dict[str, Any]:
    """Resolve a write-body template from a caller-scoped collection read.

    Fallback when a write operation declares a request schema but no request
    example: a source-declared collection ``GET``/``HEAD`` on the same
    collection returns real entities of the same resource shape, so the
    runtime can project the schema-declared fields from an observed row into
    the request body — reusing the environment's own test data as observed
    evidence instead of synthesizing values.

    Every validation dimension is fail-closed; any gap returns {} and the
    caller keeps the visible source_declared_request_body_missing block:
    1. the schema must declare at least one property (nothing to project
       otherwise);
    2. the resolver must be a source-declared GET/HEAD on the write
       operation's own collection (no path placeholders);
    3. the resolver must observe/scope an entity whose source-declared
       fields cover every required body field (a read model that cannot
       supply a required field would fail the pre-transport required-field
       gate anyway, so the block stays compile-time and honest).
    """
    fields = [_text(field) for field in projection_fields if _text(field)]
    # Reference-field path first: when the documented example carries a
    # foreign-key reference (orderId) the write's own collection may have no
    # plain collection read at all (refund-service has none) — the reference
    # target's collection is the only source of a real identity value. This
    # path runs before the schema-property gate: reference fields are named
    # by the example itself, which exists even when the schema declares no
    # properties.
    reference_fields = [_text(field) for field in _list(reference_fields) if _text(field)]
    if reference_fields:
        referenced = _reference_field_resolver(
            behavior_ir=behavior_ir,
            reference_fields=reference_fields,
        )
        if referenced:
            return referenced
    if not fields:
        return {}
    required_keys = {
        _field_key(field) for field in required_fields if _field_key(field)
    }
    primary_path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    collection = normalize_path_placeholders(collection_path(primary_path))
    if not collection.startswith("/"):
        return {}
    ir = _dict(behavior_ir)
    entities = _list(ir.get("entities"))
    candidates: list[dict[str, Any]] = []
    for op in _list(ir.get("operations")):
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in {"GET", "HEAD"}:
            continue
        op_id = _text(op.get("id"))
        if not op_id:
            continue
        path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        ).rstrip("/")
        if path != collection.rstrip("/") or "{" in path or ":" in path:
            continue
        if not _list(op.get("source_refs")):
            continue
        read_entities = _operation_entity_refs(
            behavior_ir=ir,
            operation_ref=op_id,
            relation_types={"observes", "scopes"},
        )
        if not read_entities:
            continue
        declared_keys: set[str] = set()
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            if (
                _text(entity.get("id")) not in read_entities
                and _text(entity.get("name")) not in read_entities
            ):
                continue
            for field in _list(entity.get("fields")):
                name = _text(field.get("name") if isinstance(field, dict) else field)
                if name:
                    declared_keys.add(_field_key(name))
        if not declared_keys:
            continue
        if required_keys and not required_keys <= declared_keys:
            continue
        candidates.append({
            "operation_ref": op_id,
            "method": method,
            "path": path,
            "binding_semantics": "caller_scoped",
            "projection_fields": fields,
        })
    if not candidates:
        return {}
    candidates.sort(key=lambda row: row["operation_ref"])
    return candidates[0]


def _post_action_can_restore_named_terminal_field(operation: dict[str, Any]) -> bool:
    if _text(operation.get("method")).upper() != "POST":
        return False
    path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    ).split("?", 1)[0].rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return False
    terminal = segments[-1]
    if terminal.startswith("{") and terminal.endswith("}"):
        return False
    request = _source_request_example(operation)
    if not request:
        return False
    terminal_key = _field_key(terminal)
    body_keys = {
        _field_key(key)
        for key, value in request.items()
        if value not in (None, "", [], {}) and not isinstance(value, (dict, list))
    }
    return bool(terminal_key and terminal_key in body_keys)


def _inverse_delta_cleanup_spec(operation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if _text(operation.get("method")).upper() not in {"POST", "PUT", "PATCH"}:
        return "", {}
    request = _source_request_example(operation)
    if not request:
        return "", {}
    matches = [
        (key, value)
        for key, value in request.items()
        if _field_key(key) == "delta"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ]
    if len(matches) != 1:
        return "", {}
    key, value = matches[0]
    cleanup_body = dict(request)
    cleanup_body[key] = -value
    return _text(key), cleanup_body

