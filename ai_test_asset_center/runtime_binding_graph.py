"""Runtime binding graph with traceable source priority."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .real_id_resolver import (
    alternate_collection_paths,
    collection_path,
    normalize_path_placeholders,
    path_has_placeholders,
)


BINDING_PRIORITY = (
    "experiment_setup_response",
    "same_actor_list_read",
    "evaluator_frozen_fixture",
    "disposable_fixture_receipt",
    "api_doc_example",
    "schema_generated",
)

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _fingerprint(value: Any) -> str:
    blob = _text(value)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def extract_placeholders(*texts: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        normalized = normalize_path_placeholders(_text(text))
        for match in _PLACEHOLDER_RE.findall(normalized):
            if match not in seen:
                seen.add(match)
                found.append(match)
    return found


def declared_runtime_read_resolvers(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    max_candidates: int = 2,
) -> list[dict[str, str]]:
    """Return source-declared concrete reads that may bind a target path.

    Candidate paths are derived structurally from the target path, then joined
    to operations already present in Behavior IR. No endpoint or identity value
    is invented here; runtime execution must still emit a successful read and a
    binding receipt before the target operation can run.
    """

    target_path = normalize_path_placeholders(_text(_dict(operation).get("path")))
    if not target_path.startswith("/") or not path_has_placeholders(target_path):
        return []

    candidate_paths: list[str] = []
    primary = normalize_path_placeholders(collection_path(target_path))
    if primary.startswith("/") and not path_has_placeholders(primary):
        candidate_paths.append(primary)
    candidate_paths.extend(
        normalize_path_placeholders(path)
        for path in alternate_collection_paths(target_path)
    )
    ordered_paths = list(dict.fromkeys(
        path
        for path in candidate_paths
        if path.startswith("/") and not path_has_placeholders(path)
    ))

    declared_by_path: dict[str, list[dict[str, Any]]] = {}
    for candidate in _list(_dict(behavior_ir).get("operations")):
        if not isinstance(candidate, dict):
            continue
        method = _text(candidate.get("method")).upper()
        path = normalize_path_placeholders(_text(candidate.get("path") or candidate.get("raw_path")))
        if (
            method not in {"GET", "HEAD"}
            or not _text(candidate.get("id"))
            or not path.startswith("/")
            or path_has_placeholders(path)
        ):
            continue
        declared_by_path.setdefault(path, []).append(candidate)

    resolvers: list[dict[str, str]] = []
    limit = max(1, min(int(max_candidates or 1), 5))
    for path in ordered_paths:
        for candidate in declared_by_path.get(path, []):
            resolvers.append({
                "operation_ref": _text(candidate.get("id")),
                "method": _text(candidate.get("method")).upper(),
                "path": path,
            })
            if len(resolvers) >= limit:
                return resolvers
    return resolvers


def build_binding_plan(
    *,
    operation: dict[str, Any],
    obligation: dict[str, Any],
    actors: list[dict[str, Any]] | None = None,
    available_values: dict[str, dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build binding plan. available_values maps placeholder -> {value, source, priority}."""
    op = _dict(operation)
    obl = _dict(obligation)
    values = dict(available_values or {})
    placeholders = extract_placeholders(
        op.get("path"),
        op.get("operation_id"),
        *[str(p) for p in _list(op.get("parameters"))],
    )
    # Body schema may declare path-like placeholders in examples
    for example in _list(op.get("examples")):
        if isinstance(example, dict):
            placeholders.extend(extract_placeholders(example.get("value"), example.get("body")))
        else:
            placeholders.extend(extract_placeholders(example))
    # Dedupe preserve order
    ordered: list[str] = []
    seen: set[str] = set()
    for name in placeholders:
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    plan: list[dict[str, Any]] = []
    for name in ordered:
        existing = _dict(values.get(name))
        source = _text(existing.get("source") or existing.get("source_priority"))
        if source and source not in BINDING_PRIORITY:
            source = "schema_generated"
        if existing.get("value") is not None and source:
            plan.append({
                "target": name,
                "status": "bound",
                "source_priority": source,
                "value_fingerprint": _fingerprint(existing.get("value")),
                "previous_fingerprint": _text(existing.get("previous_fingerprint")),
            })
        else:
            resolvers = declared_runtime_read_resolvers(
                op,
                behavior_ir=_dict(behavior_ir),
            )
            if resolvers:
                plan.append({
                    "target": name,
                    "target_path": normalize_path_placeholders(_text(op.get("path"))),
                    "status": "runtime_resolvable",
                    "source_priority": "same_actor_list_read",
                    "resolver_operations": resolvers,
                    "value_fingerprint": "",
                })
                continue
            # Prefer disposable fixture for identity-like placeholders
            preferred = "disposable_fixture_receipt" if name.lower().endswith("id") or name.lower() == "id" else "schema_generated"
            plan.append({
                "target": name,
                "status": "unresolved",
                "source_priority": preferred,
                "value_fingerprint": "",
            })

    for actor in actors or []:
        if not isinstance(actor, dict):
            continue
        plan.append({
            "target": f"actor:{_text(actor.get('id'))}",
            "status": "bound" if _text(actor.get("credential_secret_ref")) else "unresolved",
            "source_priority": "runtime_actor_secret_ref",
            "secret_ref": _text(actor.get("credential_secret_ref")),
            "value_fingerprint": _fingerprint(actor.get("credential_secret_ref")),
        })

    for fixture in _list(obl.get("required_fixtures")):
        name = _text(fixture)
        if not name:
            continue
        plan.append({
            "target": f"fixture:{name}",
            "fixture_id": name,
            "status": "required",
            "source_priority": "disposable_fixture_receipt",
            "value_fingerprint": "",
        })
    return plan


def apply_binding(
    plan: list[dict[str, Any]],
    *,
    target: str,
    value: Any,
    source_priority: str,
) -> list[dict[str, Any]]:
    """Apply a binding without allowing low-priority sources to override higher ones."""
    if source_priority not in BINDING_PRIORITY and source_priority != "runtime_actor_secret_ref":
        source_priority = "schema_generated"
    new_plan = [dict(item) for item in plan]
    priority_rank = {name: index for index, name in enumerate(BINDING_PRIORITY)}
    priority_rank["runtime_actor_secret_ref"] = 0
    incoming_rank = priority_rank.get(source_priority, len(BINDING_PRIORITY))
    for item in new_plan:
        if _text(item.get("target")) != _text(target):
            continue
        current = _text(item.get("source_priority"))
        current_rank = priority_rank.get(current, len(BINDING_PRIORITY))
        if item.get("status") == "bound" and incoming_rank > current_rank:
            item["override_rejected"] = {
                "attempted_source": source_priority,
                "kept_source": current,
            }
            return new_plan
        item["previous_fingerprint"] = _text(item.get("value_fingerprint"))
        item["status"] = "bound"
        item["source_priority"] = source_priority
        item["value_fingerprint"] = _fingerprint(value)
        return new_plan
    new_plan.append({
        "target": _text(target),
        "status": "bound",
        "source_priority": source_priority,
        "value_fingerprint": _fingerprint(value),
    })
    return new_plan


def unresolved_placeholders(operation: dict[str, Any], plan: list[dict[str, Any]]) -> list[str]:
    needed = extract_placeholders(_dict(operation).get("path"))
    bound = {
        _text(item.get("target"))
        for item in plan
        if _text(item.get("status")) == "bound"
        or (
            _text(item.get("status")) == "runtime_resolvable"
            and bool(_list(item.get("resolver_operations")))
        )
    }
    return [name for name in needed if name not in bound]
