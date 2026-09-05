"""Behavior IR public facade with account-role and cross-revision identity.

The immutable IR builder remains in :mod:`behavior_ir_core`. This facade preserves
runtime actor identity and adds stable behavioral identity on top of the same IR
objects used by production planning. It does not create a second Behavior IR.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from . import behavior_ir_core as _core
from .runtime_actor_role_identity import resolve_runtime_actor_roles

for _name, _value in vars(_core).items():
    if _name.startswith("__") or _name == "build_behavior_ir_from_knowledge_asset":
        continue
    globals().setdefault(_name, _value)

_original_build_behavior_ir = _core.build_behavior_ir_from_knowledge_asset

_IDENTITY_COLLECTIONS = (
    "entities",
    "operations",
    "actors",
    "states",
    "invariants",
    "relations",
)
_IDENTITY_METADATA_FIELDS = {
    "id",
    "logical_key",
    "source_refs",
    "confidence",
    "derivation",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _hash_key(prefix: str, *parts: Any) -> str:
    raw = "|".join(_text(part).casefold() for part in parts if _text(part))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _ref_key(value: Any, ref_keys: dict[str, str]) -> str:
    token = _text(value)
    return ref_keys.get(token, token)


def _behavior_node_logical_key(
    collection: str,
    node: dict[str, Any],
    *,
    ref_keys: dict[str, str] | None = None,
) -> str:
    """Return a semantic identity that survives source/content revisions.

    Identity intentionally excludes source hashes, source locators, confidence and
    mutable descriptive payloads. Those values may change the revision fingerprint,
    but they must not turn one logical behavior into a remove+add pair.
    """
    refs = ref_keys or {}
    if collection == "operations":
        service = _text(
            node.get("service")
            or node.get("_service_name")
            or node.get("service_name")
        )
        method = _text(node.get("method") or node.get("http_method") or "GET").upper()
        path = _core._path_shape(
            node.get("path") or node.get("raw_path") or node.get("endpoint")
        )
        return _hash_key("birlk_operation", service, method, path)
    if collection == "entities":
        return _hash_key(
            "birlk_entity",
            node.get("service") or node.get("service_name"),
            node.get("canonical_name") or node.get("name") or node.get("entity_name"),
        )
    if collection == "actors":
        return _hash_key(
            "birlk_actor",
            node.get("role_key") or node.get("role") or node.get("name"),
            node.get("account_ref") or node.get("credential_identity_ref"),
        )
    if collection == "states":
        return _hash_key(
            "birlk_state",
            _ref_key(node.get("entity_ref"), refs),
            node.get("name") or node.get("state") or node.get("value") or node.get("label"),
        )
    if collection == "invariants":
        return _hash_key(
            "birlk_invariant",
            node.get("invariant_type") or node.get("kind") or node.get("risk_family"),
            node.get("rule_id") or node.get("contract_id") or node.get("name"),
            _ref_key(node.get("operation_ref"), refs),
            _ref_key(node.get("entity_ref"), refs),
            _ref_key(node.get("actor_ref"), refs),
        )
    if collection == "relations":
        return _hash_key(
            "birlk_relation",
            node.get("relation_type"),
            _ref_key(node.get("from_ref"), refs),
            _ref_key(node.get("to_ref"), refs),
            _ref_key(node.get("operation_ref"), refs),
            _ref_key(node.get("actor_ref"), refs),
        )
    return _hash_key(
        f"birlk_{collection.rstrip('s')}",
        node.get("canonical_id") or node.get("name") or node.get("id"),
    )


def _revision_payload(node: dict[str, Any]) -> dict[str, Any]:
    """Return behavior content used to classify a stable identity as changed."""
    return {
        key: deepcopy(value)
        for key, value in node.items()
        if key not in _IDENTITY_METADATA_FIELDS
    }


def attach_stable_behavior_identity(model: dict[str, Any]) -> dict[str, Any]:
    """Attach logical keys and revision identity to the existing Behavior IR."""
    if not isinstance(model, dict):
        raise TypeError("behavior_ir_model_must_be_object")

    ref_keys: dict[str, str] = {}
    for collection in _IDENTITY_COLLECTIONS[:-1]:
        for node in _list(model.get(collection)):
            if not isinstance(node, dict):
                continue
            logical_key = _behavior_node_logical_key(
                collection,
                node,
                ref_keys=ref_keys,
            )
            node["logical_key"] = logical_key
            node_id = _text(node.get("id"))
            if node_id:
                ref_keys[node_id] = logical_key

    for node in _list(model.get("relations")):
        if not isinstance(node, dict):
            continue
        logical_key = _behavior_node_logical_key(
            "relations",
            node,
            ref_keys=ref_keys,
        )
        node["logical_key"] = logical_key
        node_id = _text(node.get("id"))
        if node_id:
            ref_keys[node_id] = logical_key

    project_key = _text(model.get("project_id")) or "opaque-project-id"
    model["logical_key"] = _hash_key("birlk_model", "behavior_ir", project_key)
    revision_basis = {
        "logical_key": model["logical_key"],
        "source_snapshot_hash": _text(model.get("source_snapshot_hash")),
        "collections": {
            collection: [
                {
                    "logical_key": _text(node.get("logical_key")),
                    "payload": _revision_payload(node),
                }
                for node in _list(model.get(collection))
                if isinstance(node, dict)
            ]
            for collection in _IDENTITY_COLLECTIONS
        },
    }
    blob = json.dumps(
        revision_basis,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    model["revision_id"] = (
        "birrev_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
    )
    model["revision_identity_schema"] = "qualibug.behavior-ir-revision.v1"
    return model


def match_behavior_ir_revisions(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    """Match the same logical behavior across two source revisions."""
    prior = attach_stable_behavior_identity(deepcopy(previous))
    present = attach_stable_behavior_identity(deepcopy(current))
    result: dict[str, list[dict[str, str]]] = {}
    for collection in _IDENTITY_COLLECTIONS:
        before = {
            _text(node.get("logical_key")): node
            for node in _list(prior.get(collection))
            if isinstance(node, dict) and _text(node.get("logical_key"))
        }
        after = {
            _text(node.get("logical_key")): node
            for node in _list(present.get(collection))
            if isinstance(node, dict) and _text(node.get("logical_key"))
        }
        result[collection] = [
            {
                "logical_key": logical_key,
                "previous_id": _text(before[logical_key].get("id")),
                "current_id": _text(after[logical_key].get("id")),
            }
            for logical_key in sorted(set(before) & set(after))
        ]
    return result


def build_minimum_ir_delta(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Build the minimum cross-revision IRDelta required by Architecture v1.3."""
    prior = attach_stable_behavior_identity(deepcopy(previous))
    present = attach_stable_behavior_identity(deepcopy(current))
    collections: dict[str, dict[str, list[str]]] = {}
    totals = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}

    for collection in _IDENTITY_COLLECTIONS:
        before = {
            _text(node.get("logical_key")): node
            for node in _list(prior.get(collection))
            if isinstance(node, dict) and _text(node.get("logical_key"))
        }
        after = {
            _text(node.get("logical_key")): node
            for node in _list(present.get(collection))
            if isinstance(node, dict) and _text(node.get("logical_key"))
        }
        common = set(before) & set(after)
        changed = sorted(
            key
            for key in common
            if _revision_payload(before[key]) != _revision_payload(after[key])
        )
        unchanged = sorted(common - set(changed))
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        collections[collection] = {
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
        }
        totals["added"] += len(added)
        totals["removed"] += len(removed)
        totals["changed"] += len(changed)
        totals["unchanged"] += len(unchanged)

    return {
        "schema_version": "qualibug.behavior-ir-delta.v1",
        "behavior_logical_key": _text(present.get("logical_key")),
        "from_revision_id": _text(prior.get("revision_id")),
        "to_revision_id": _text(present.get("revision_id")),
        "source_snapshot_changed": (
            _text(prior.get("source_snapshot_hash"))
            != _text(present.get("source_snapshot_hash"))
        ),
        "collections": collections,
        "summary": totals,
    }


def _runtime_actor_coordinates(
    runtime_actors: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    actors = [dict(row) for row in _list(runtime_actors) if isinstance(row, dict)]
    account_roles: dict[str, set[str]] = {}
    account_spellings: dict[str, str] = {}
    for actor in actors:
        role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        account = _text(
            actor.get("account_ref") or actor.get("email")
            or actor.get("username") or actor.get("id")
        )
        if role and account:
            key = account.casefold()
            account_spellings.setdefault(key, account)
            account_roles.setdefault(key, set()).add(role.casefold())

    projection: dict[str, dict[str, Any]] = {}
    prepared: list[dict[str, Any]] = []
    for actor in actors:
        role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        account = _text(
            actor.get("account_ref") or actor.get("email")
            or actor.get("username") or actor.get("id")
        )
        roles = account_roles.get(account.casefold(), set())
        if not account or not role or len(roles) <= 1:
            prepared.append(actor)
            continue
        synthetic = f"urn:qualibug:account-role:{account.casefold()}:{role.casefold()}"
        projected = dict(actor)
        projected["account_ref"] = synthetic
        prepared.append(projected)
        projection[synthetic] = {
            "account_ref": account_spellings.get(account.casefold(), account),
            "role_key": role.casefold(),
            "account_role_count": len(roles),
        }
    return prepared, projection


def build_behavior_ir_from_knowledge_asset(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    available_surfaces: dict[str, bool] | None = None,
    operation_path_scope: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    role_bound_actors, role_identity_receipt = resolve_runtime_actor_roles(
        asset, runtime_actors
    )
    prepared, projection = _runtime_actor_coordinates(role_bound_actors)
    model = _original_build_behavior_ir(
        asset,
        project_id=project_id,
        source_snapshot_hash=source_snapshot_hash,
        api_operations=api_operations,
        runtime_actors=prepared,
        available_surfaces=available_surfaces,
        operation_path_scope=operation_path_scope,
    )
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        coordinate = projection.get(_text(actor.get("account_ref")))
        if not coordinate:
            continue
        account = coordinate["account_ref"]
        role_key = _text(actor.get("role_key") or coordinate.get("role_key"))
        actor["account_ref"] = account
        actor["credential_identity_ref"] = _core._stable_id(
            "credential_identity", account.casefold()
        )
        actor["role_assignment_ref"] = _core._stable_id(
            "role_assignment", account.casefold(), role_key
        )
        actor["account_role_count"] = int(coordinate["account_role_count"])
    _runtime_account_ids = {
        _text(
            item.get("account_ref") or item.get("email") or item.get("username")
        ).casefold(): _text(item.get("account_id"))
        for item in _list(runtime_actors)
        if isinstance(item, dict) and _text(item.get("account_id"))
    }
    if _runtime_account_ids:
        for actor in _list(model.get("actors")):
            if not isinstance(actor, dict) or _text(actor.get("account_id")):
                continue
            _ref = _text(
                actor.get("account_ref") or actor.get("email") or actor.get("username")
            )
            _identity = _runtime_account_ids.get(_ref.casefold())
            if _identity:
                actor["account_id"] = _identity
    if role_identity_receipt.get("actor_count"):
        model["runtime_actor_role_identity_receipt"] = role_identity_receipt
    attach_stable_behavior_identity(model)
    model["model_id"] = _core._content_addressed_id(model)
    return model


__all__ = sorted(
    name for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name", "_value"}
)
