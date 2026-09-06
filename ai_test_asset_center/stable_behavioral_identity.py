"""Stable cross-revision identity for the existing Behavior IR authority.

This module annotates the existing Behavior IR in place. It does not construct or
own a parallel IR. Matching and delta classification are deterministic and never
use an LLM.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

IDENTITY_SCHEMA_VERSION = "qualibug.behavioral-identity.v1"
REVISION_SCHEMA_VERSION = "qualibug.source-revision.v1"
DELTA_SCHEMA_VERSION = "qualibug.ir-delta.v1"

_EXCLUDED_COLLECTIONS = {
    "sources", "source_refs", "conflicts", "gaps", "openapi_servers",
    "text_requirements", "semantic_roles",
}
_VOLATILE_FIELDS = {
    "id", "logical_key", "logical_key_matchable", "logical_key_strategy",
    "revision_fingerprint", "source_refs", "confidence", "derivation",
    "locator", "quote_hash", "version", "ingestion_id", "source_snapshot_hash",
}
_STRONG_KEYS = (
    "requirement_key", "rule_key", "rule_id", "business_behavior_id",
    "behavior_id", "canonical_field_id", "canonical_constraint_id",
    "contract_id", "event_contract_id", "job_contract_id", "permission_key",
    "state_key", "entity_key", "operation_id",
)
_REF_FIELDS = (
    "from_ref", "to_ref", "operation_ref", "entity_ref", "actor_ref",
    "state_ref", "source_state_ref", "target_state_ref", "event_ref",
    "job_ref", "permission_ref", "parent_ref",
)


class StableBehaviorIdentityError(ValueError):
    """Stable identity contract violation."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _hash(prefix: str, payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _path_shape(value: Any) -> str:
    path = _text(value).split("?", 1)[0]
    return re.sub(r"\{[^/{}]+\}", "{}", path).rstrip("/") or "/"


def _collections(model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for name, value in model.items():
        if name in _EXCLUDED_COLLECTIONS or not isinstance(value, list):
            continue
        rows = [row for row in value if isinstance(row, dict) and _text(row.get("id"))]
        if rows:
            out[name] = rows
    return out


def _strong_identity(collection: str, node: dict[str, Any]) -> tuple[str, str] | None:
    for field in _STRONG_KEYS:
        value = _text(node.get(field))
        if value:
            return _hash(f"birlk_{collection.rstrip('s')}", [field, value.casefold()]), f"declared:{field}"
    if collection == "operations":
        path = _text(node.get("path") or node.get("raw_path") or node.get("endpoint") or node.get("url"))
        if path:
            service = _text(node.get("service") or node.get("service_name") or node.get("_service_name")).casefold()
            method = _text(node.get("method") or node.get("http_method") or "GET").upper()
            return _hash("birlk_operation", [service, method, _path_shape(path)]), "transport"
    for field in ("canonical_name", "name", "role_key", "role", "state", "value"):
        value = _text(node.get(field))
        if value:
            service = _text(node.get("service") or node.get("service_name")).casefold()
            return _hash(f"birlk_{collection.rstrip('s')}", [service, field, value.casefold()]), f"structural:{field}"
    return None


def _identity_for_node(collection: str, node: dict[str, Any], ref_keys: dict[str, str]) -> tuple[str, bool, str]:
    declared = _strong_identity(collection, node)
    if declared is not None:
        return declared[0], True, declared[1]
    refs = [(field, ref_keys.get(_text(node.get(field)), _text(node.get(field)))) for field in _REF_FIELDS if _text(node.get(field))]
    kinds = [(field, _text(node.get(field)).casefold()) for field in ("kind", "type", "relation_type", "invariant_type", "action", "event", "job") if _text(node.get(field))]
    if refs or kinds:
        return _hash(f"birlk_{collection.rstrip('s')}", [kinds, refs]), True, "structural_refs"
    # Fail closed: keep an addressable key inside this revision, but never claim
    # that an opaque ID establishes cross-revision semantic identity.
    return _hash(f"birlk_{collection.rstrip('s')}", ["opaque", _text(node.get("id"))]), False, "opaque_unmatchable"


def _normalize_refs(value: Any, ref_keys: dict[str, str], field: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _normalize_refs(v, ref_keys, k) for k, v in sorted(value.items()) if k not in _VOLATILE_FIELDS}
    if isinstance(value, list):
        return [_normalize_refs(item, ref_keys, field) for item in value]
    if field.endswith("_ref"):
        return ref_keys.get(_text(value), _text(value))
    if field.endswith("_refs") and field != "source_refs":
        return ref_keys.get(_text(value), _text(value))
    return value


def _revision_fingerprint(node: dict[str, Any], ref_keys: dict[str, str]) -> str:
    payload = {k: _normalize_refs(v, ref_keys, k) for k, v in sorted(node.items()) if k not in _VOLATILE_FIELDS}
    return _hash("birfp", payload)


def derive_revision_identity(model: dict[str, Any]) -> dict[str, Any]:
    collections = _collections(model)
    rows: list[dict[str, Any]] = []
    for name in sorted(collections):
        for node in collections[name]:
            rows.append({
                "collection": name,
                "logical_key": _text(node.get("logical_key")),
                "matchable": bool(node.get("logical_key_matchable")),
                "fingerprint": _text(node.get("revision_fingerprint")),
            })
    source_rows = []
    for source in model.get("sources") or []:
        if isinstance(source, dict):
            source_rows.append({k: source.get(k) for k in ("source_id", "kind", "version", "content_hash", "snapshot_hash") if source.get(k) not in (None, "")})
    basis = {
        "schema_version": REVISION_SCHEMA_VERSION,
        "project_id": _text(model.get("project_id")),
        "source_snapshot_hash": _text(model.get("source_snapshot_hash")),
        "sources": source_rows,
        "nodes": rows,
    }
    fingerprint = _hash("birrevfp", basis)
    return {
        "schema_version": REVISION_SCHEMA_VERSION,
        "revision_id": _hash("birrev", basis),
        "fingerprint": fingerprint,
        "immutable": True,
    }


def attach_stable_behavioral_identity(model: dict[str, Any]) -> dict[str, Any]:
    """Annotate the same Behavior IR object with stable logical/revision identity."""
    if not isinstance(model, dict):
        raise TypeError("behavior_ir_model_must_be_object")
    collections = _collections(model)
    ref_keys: dict[str, str] = {}

    # Pass 1 gives named/declared nodes stable coordinates so relation identities
    # can refer to semantic keys rather than revision-sensitive node IDs.
    for name in sorted(collections):
        for node in collections[name]:
            declared = _strong_identity(name, node)
            if declared is not None:
                node["logical_key"], node["logical_key_matchable"], node["logical_key_strategy"] = declared[0], True, declared[1]
                ref_keys[_text(node.get("id"))] = declared[0]

    # Pass 2 resolves reference-structured nodes; unresolved opaque nodes fail closed.
    for name in sorted(collections):
        for node in collections[name]:
            if not _text(node.get("logical_key")):
                key, matchable, strategy = _identity_for_node(name, node, ref_keys)
                node["logical_key"] = key
                node["logical_key_matchable"] = matchable
                node["logical_key_strategy"] = strategy
            ref_keys[_text(node.get("id"))] = _text(node.get("logical_key"))

    # Collisions are ambiguity, never silent matching.
    counts = Counter(_text(node.get("logical_key")) for rows in collections.values() for node in rows if bool(node.get("logical_key_matchable")))
    collision_keys = {key for key, count in counts.items() if key and count > 1}
    if collision_keys:
        for rows in collections.values():
            for node in rows:
                if _text(node.get("logical_key")) in collision_keys:
                    node["logical_key_matchable"] = False
                    node["logical_key_strategy"] = "collision_unmatchable"

    for rows in collections.values():
        for node in rows:
            node["revision_fingerprint"] = _revision_fingerprint(node, ref_keys)

    revision = derive_revision_identity(model)
    model["logical_key"] = _hash("birlk_model", ["behavior_ir", _text(model.get("project_id")) or "opaque-project"])
    model["revision_id"] = revision["revision_id"]
    model["revision_identity_schema"] = REVISION_SCHEMA_VERSION
    model["behavioral_identity"] = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "revision": revision,
        "node_count": sum(len(rows) for rows in collections.values()),
        "collision_count": len(collision_keys),
        "cross_revision_matching": "deterministic_logical_key_only",
        "llm_change_classification_used": False,
        "parallel_behavior_ir_created": False,
    }
    return model


# Backward-compatible spelling used by the abandoned R38-B draft branch.
attach_stable_behavior_identity = attach_stable_behavioral_identity


def validate_revision_identity(model: dict[str, Any]) -> bool:
    candidate = deepcopy(model)
    attach_stable_behavioral_identity(candidate)
    stored = ((model.get("behavioral_identity") or {}).get("revision") or {})
    actual = ((candidate.get("behavioral_identity") or {}).get("revision") or {})
    if stored.get("revision_id") != actual.get("revision_id") or stored.get("fingerprint") != actual.get("fingerprint"):
        raise StableBehaviorIdentityError("behavior_ir_revision_identity_mutated")
    return True


def _index(model: dict[str, Any]) -> tuple[dict[str, tuple[str, dict[str, Any]]], set[str]]:
    mapping: dict[str, tuple[str, dict[str, Any]]] = {}
    ambiguous: set[str] = set()
    for name, rows in _collections(model).items():
        for node in rows:
            key = _text(node.get("logical_key"))
            if not key or not bool(node.get("logical_key_matchable")):
                continue
            if key in mapping:
                ambiguous.add(key)
            else:
                mapping[key] = (name, node)
    for key in ambiguous:
        mapping.pop(key, None)
    return mapping, ambiguous


def match_behavior_ir_revisions(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    prior = attach_stable_behavioral_identity(deepcopy(previous))
    present = attach_stable_behavioral_identity(deepcopy(current))
    left, left_ambiguous = _index(prior)
    right, right_ambiguous = _index(present)
    keys = sorted(set(left) & set(right))
    return {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "matches": [
            {
                "logical_key": key,
                "collection": right[key][0],
                "previous_id": _text(left[key][1].get("id")),
                "current_id": _text(right[key][1].get("id")),
            }
            for key in keys
        ],
        "ambiguous": sorted(left_ambiguous | right_ambiguous),
        "llm_used": False,
    }


def _adjacency(model: dict[str, Any]) -> dict[str, set[str]]:
    id_to_key = {_text(node.get("id")): _text(node.get("logical_key")) for rows in _collections(model).values() for node in rows}
    graph: dict[str, set[str]] = defaultdict(set)
    for rows in _collections(model).values():
        for node in rows:
            source_key = _text(node.get("logical_key"))
            if not source_key:
                continue
            for field, value in node.items():
                refs: list[Any] = []
                if field.endswith("_ref") and field != "source_ref":
                    refs = [value]
                elif field.endswith("_refs") and field != "source_refs" and isinstance(value, list):
                    refs = value
                for ref in refs:
                    target_key = id_to_key.get(_text(ref), "")
                    if target_key and target_key != source_key:
                        graph[source_key].add(target_key)
                        graph[target_key].add(source_key)
    return graph


def build_ir_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    prior = attach_stable_behavioral_identity(deepcopy(previous))
    present = attach_stable_behavioral_identity(deepcopy(current))
    left, left_ambiguous = _index(prior)
    right, right_ambiguous = _index(present)
    left_keys, right_keys = set(left), set(right)
    added = sorted(right_keys - left_keys)
    removed = sorted(left_keys - right_keys)
    shared = left_keys & right_keys
    modified = sorted(key for key in shared if _text(left[key][1].get("revision_fingerprint")) != _text(right[key][1].get("revision_fingerprint")))
    changed = set(added) | set(removed) | set(modified)
    graph = _adjacency(prior)
    current_graph = _adjacency(present)
    for key, neighbors in current_graph.items():
        graph[key].update(neighbors)
    impacted = sorted({neighbor for key in changed for neighbor in graph.get(key, set()) if neighbor not in changed})

    by_collection: dict[str, dict[str, list[str]]] = {}
    all_collections = sorted(set(name for name, _ in left.values()) | set(name for name, _ in right.values()))
    for name in all_collections:
        by_collection[name] = {
            "added": [k for k in added if right.get(k, (None,))[0] == name],
            "modified": [k for k in modified if right.get(k, left.get(k, (None,)))[0] == name],
            "removed": [k for k in removed if left.get(k, (None,))[0] == name],
            "impacted": [k for k in impacted if right.get(k, left.get(k, (None,)))[0] == name],
        }
    return {
        "schema_version": DELTA_SCHEMA_VERSION,
        "previous_revision_id": _text(prior.get("revision_id")),
        "current_revision_id": _text(present.get("revision_id")),
        "added": added,
        "modified": modified,
        "removed": removed,
        "impacted": impacted,
        "ambiguous": sorted(left_ambiguous | right_ambiguous),
        "by_collection": by_collection,
        "llm_used": False,
    }


# Backward-compatible name from the abandoned draft branch.
build_minimum_ir_delta = build_ir_delta
