"""Runtime binding graph facade with actor, observer, value and fixture authority.

The established source/fixture/read resolver mechanics live in
``_runtime_binding_graph_mechanics``. Formal facts require explicit authority:

* credential placeholders are principal-specific and may not select the first
  actor that happens to have a secret;
* effect observers belong to an exact source operation;
* request-schema examples/defaults may supply ordinary business scalars, but
  never resource identity; and
* fixture creation uses either the target's structural collection or an explicit
  source ``produces`` relation to the operation's entity. A sibling/action POST
  under the same module prefix is never create authority by proximity alone.
"""
from __future__ import annotations

import re
from typing import Any

from . import _runtime_binding_graph_mechanics as _core
from ._runtime_binding_graph_mechanics import *  # noqa: F401,F403

_original_build_binding_plan = _core.build_binding_plan
_original_declared_effect_observers = _core.declared_effect_observers
_original_source_declared_body_example_bindings = (
    _core._source_declared_body_example_bindings
)


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


def _identity_shaped_target(value: Any) -> bool:
    raw = _text(value)
    if not raw:
        return False
    leaf = raw.split(".")[-1].split("[")[0]
    if not leaf:
        return False
    lowered = leaf.lower()
    if lowered in {"id", "uuid", "guid", "key", "ref"}:
        return True
    if re.search(r"(?:_|-)(?:id|uuid|guid|key|ref)$", leaf, re.IGNORECASE):
        return True
    return bool(re.search(r"(?:Id|ID|Uuid|UUID|Guid|GUID|Key|Ref)$", leaf))


def _source_declared_body_example_bindings(
    operation: dict[str, Any],
    unresolved: list[str],
    body_placeholder_paths: dict[str, list[str]],
) -> dict[str, Any] | None:
    """Allow source examples only for non-identity business scalar bindings."""

    governed_unresolved = [
        name for name in unresolved if not _identity_shaped_target(name)
    ]
    if not governed_unresolved:
        return None
    bindings = _original_source_declared_body_example_bindings(
        operation,
        governed_unresolved,
        body_placeholder_paths,
    )
    if not isinstance(bindings, dict):
        return None
    return {
        target: row
        for target, row in bindings.items()
        if not _identity_shaped_target(target)
    } or None


def declared_effect_observers(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    max_candidates: int = 2,
) -> list[dict[str, str]]:
    """Return effect observers only for an exact Behavior IR operation identity."""

    op = _dict(operation)
    operation_ref = _text(op.get("id") or op.get("operation_id"))
    if not operation_ref:
        return []
    indexed = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }
    source_operation = _dict(indexed.get(operation_ref))
    if not source_operation:
        return []

    supplied_path = _text(op.get("path") or op.get("raw_path"))
    source_path = _text(
        source_operation.get("path") or source_operation.get("raw_path")
    )
    supplied_method = _text(op.get("method")).upper()
    source_method = _text(source_operation.get("method")).upper()
    if supplied_path and supplied_path != source_path:
        return []
    if supplied_method and source_method and supplied_method != source_method:
        return []
    return _original_declared_effect_observers(
        source_operation,
        behavior_ir=behavior_ir,
        max_candidates=max_candidates,
    )


def _fixture_structural_collection_paths(
    operation: dict[str, Any],
    target: str,
) -> list[str]:
    target_path = _core.normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    prefix = _core._api_prefix(target_path)
    candidates = _core.body_field_collection_paths(target, api_prefix=prefix)
    if not candidates:
        primary = _core.normalize_path_placeholders(
            _core.collection_path(target_path)
        )
        if primary.startswith("/") and not _core.path_has_placeholders(primary):
            candidates = [primary]
    return list(
        dict.fromkeys(
            _core.normalize_path_placeholders(path).rstrip("/")
            for path in candidates
            if _text(path).startswith("/")
            and not _core.path_has_placeholders(
                _core.normalize_path_placeholders(path)
            )
        )
    )


def _fixture_create_candidates(
    operation: dict[str, Any],
    *,
    target: str,
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return source POSTs authorized structurally or by explicit produces relation."""

    operations = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }
    structural_paths = set(_fixture_structural_collection_paths(operation, target))
    current_entities = {
        _text(value)
        for value in [
            *_list(_dict(operation).get("entity_refs")),
            _dict(operation).get("entity_ref"),
        ]
        if _text(value)
    }
    explicit_producers: set[str] = set()
    if current_entities:
        for raw in _list(_dict(behavior_ir).get("relations")):
            relation = _dict(raw)
            if (
                _text(relation.get("relation_type")) != "produces"
                or _text(relation.get("status")) in {"conflicting", "unsupported"}
                or not _list(relation.get("source_refs"))
                or _text(relation.get("to_ref")) not in current_entities
            ):
                continue
            producer_ref = _text(
                relation.get("operation_ref") or relation.get("from_ref")
            )
            if producer_ref:
                explicit_producers.add(producer_ref)

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operation_ref, raw in operations.items():
        candidate = _dict(raw)
        path = _core.normalize_path_placeholders(
            _text(candidate.get("path") or candidate.get("raw_path"))
        ).rstrip("/")
        if (
            _text(candidate.get("method")).upper() != "POST"
            or not path.startswith("/")
            or _core.path_has_placeholders(path)
        ):
            continue
        authorities: list[str] = []
        if path in structural_paths:
            authorities.append("structural_collection")
        if operation_ref in explicit_producers:
            authorities.append("explicit_produces_relation")
        if not authorities or operation_ref in seen:
            continue
        seen.add(operation_ref)
        candidates.append(
            {
                "operation": candidate,
                "operation_ref": operation_ref,
                "path": path,
                "authorities": authorities,
            }
        )
    return candidates


def _declared_fixture_setup(
    operation: dict[str, Any],
    *,
    target: str,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build fixture setup only from one fully viable authoritative create."""

    operations = _list(_dict(behavior_ir).get("operations"))
    viable: list[dict[str, Any]] = []
    for candidate in _fixture_create_candidates(
        operation,
        target=target,
        behavior_ir=behavior_ir,
    ):
        create = _dict(candidate.get("operation"))
        create_path = _text(candidate.get("path"))
        body_template = _core._request_example(create, sibling_ops=operations)
        if not body_template:
            continue

        body_bindings: list[dict[str, Any]] = []
        unresolved_body = False
        prefix = _core._api_prefix(create_path)
        for row in _core._body_placeholder_rows(body_template):
            field = _text(row.get("target")).split(".")[-1].split("[")[0]
            token = _text(row.get("template_token"))
            resolvers = _core._declared_reads_for_paths(
                _core.body_field_collection_paths(field or token, api_prefix=prefix)
                or _core.body_field_collection_paths(token, api_prefix=prefix),
                behavior_ir=behavior_ir,
            )
            if not resolvers:
                unresolved_body = True
                break
            body_bindings.append(
                {
                    "target": _text(row.get("target")),
                    "template_token": token,
                    "resolver_operations": resolvers,
                }
            )
        if unresolved_body:
            continue

        cleanup_operations = _core._declared_cleanup_operations(
            create_path,
            behavior_ir=behavior_ir,
        )
        actor_refs = _core._declared_fixture_actor_refs(
            create,
            behavior_ir=behavior_ir,
        )
        if not cleanup_operations or len(actor_refs) != 1:
            continue
        viable.append(
            {
                "operation_ref": _text(candidate.get("operation_ref")),
                "method": "POST",
                "path": create_path,
                "actor_refs": list(actor_refs),
                "body_template": body_template,
                "body_bindings": body_bindings,
                "cleanup_operations": cleanup_operations,
                "create_authorities": list(candidate.get("authorities") or []),
            }
        )

    # Multiple fully viable creates are semantic ambiguity. Do not let source
    # order choose which business object/setup route the experiment will create.
    if len(viable) != 1:
        return {}
    return viable[0]


def _actor_ref(actor: dict[str, Any]) -> str:
    return _text(actor.get("id") or actor.get("actor_id"))


def _actor_secret(actor: dict[str, Any]) -> str:
    secret = _text(
        actor.get("credential_secret_ref")
        or actor.get("secret_ref")
        or actor.get("credential_ref")
    )
    if secret.lower().startswith("secret_ref:actor:"):
        return ""
    return secret


def _credential_actor_authority(
    obligation: dict[str, Any],
    actors: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    candidates = {
        _actor_ref(actor): actor
        for actor in actors
        if isinstance(actor, dict)
        and _actor_ref(actor)
        and _actor_secret(actor)
    }
    if len(candidates) == 1:
        return next(iter(candidates.values())), "unique_required_actor"
    if not candidates:
        return None, "credential_actor_missing"

    obl = _dict(obligation)
    prop = _dict(obl.get("property"))
    explicit_refs = {
        _text(value)
        for value in (
            prop.get("actor_ref"),
            prop.get("control_actor_ref"),
            prop.get("treatment_actor_ref"),
            prop.get("owner_actor_ref"),
            prop.get("resource_owner_actor_id"),
            obl.get("actor_ref"),
        )
        if _text(value)
    }
    explicit_candidates = explicit_refs.intersection(candidates)
    if len(explicit_refs) == 1 and len(explicit_candidates) == 1:
        actor_ref = next(iter(explicit_candidates))
        return candidates[actor_ref], "explicit_actor_consensus"
    return None, "credential_actor_ambiguous"


def _govern_credential_bindings(
    plan: list[dict[str, Any]],
    *,
    obligation: dict[str, Any],
    actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    governed: list[dict[str, Any]] = []
    for raw in plan:
        row = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(row, dict):
            governed.append(row)
            continue
        if _text(row.get("source_priority")) != "actor_credential_secret":
            governed.append(row)
            continue

        actor, authority = _credential_actor_authority(obligation, actors)
        if actor is None:
            row.update(
                {
                    "status": "blocked",
                    "blocked_reason": "CREDENTIAL_BINDING_ACTOR_AMBIGUOUS",
                    "credential_actor_authority": authority,
                    "actor_ref": "",
                    "credential_secret_ref": "",
                    "value_fingerprint": "",
                }
            )
            setup = dict(_dict(row.get("fixture_setup")))
            setup.pop("actor_ref", None)
            setup.pop("credential_secret_ref", None)
            if setup:
                setup["credential_actor_authority"] = authority
                row["fixture_setup"] = setup
            governed.append(row)
            continue

        actor_ref = _actor_ref(actor)
        secret_ref = _actor_secret(actor)
        row.update(
            {
                "status": "runtime_resolvable",
                "actor_ref": actor_ref,
                "credential_secret_ref": secret_ref,
                "credential_actor_authority": authority,
                "value_fingerprint": "",
            }
        )
        setup = dict(_dict(row.get("fixture_setup")))
        if setup:
            setup.update(
                {
                    "actor_ref": actor_ref,
                    "credential_secret_ref": secret_ref,
                    "credential_actor_authority": authority,
                }
            )
            row["fixture_setup"] = setup
        governed.append(row)
    return governed


def build_binding_plan(
    *,
    operation: dict[str, Any],
    obligation: dict[str, Any],
    actors: list[dict[str, Any]] | None = None,
    available_values: dict[str, dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan = _original_build_binding_plan(
        operation=operation,
        obligation=obligation,
        actors=actors,
        available_values=available_values,
        behavior_ir=behavior_ir,
    )
    return _govern_credential_bindings(
        plan,
        obligation=obligation,
        actors=[
            dict(actor)
            for actor in (actors or [])
            if isinstance(actor, dict)
        ],
    )


_core.declared_effect_observers = declared_effect_observers
_core._source_declared_body_example_bindings = (
    _source_declared_body_example_bindings
)
_core._declared_fixture_setup = _declared_fixture_setup

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "build_binding_plan",
        "declared_effect_observers",
        "_source_declared_body_example_bindings",
        "_identity_shaped_target",
        "_fixture_create_candidates",
        "_declared_fixture_setup",
        "_credential_actor_authority",
        "_govern_credential_bindings",
    }
)
