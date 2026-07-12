"""Runtime binding graph with traceable source priority."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .real_id_resolver import (
    alternate_collection_paths,
    body_field_collection_paths,
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
_BODY_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")
_CLEANUP_ACTION_RE = re.compile(
    r"(?:cancel|close|void|disable|archive|reject|release|rollback|revoke|remove|"
    r"delete|deactivate|suspend|expire|invalidate|terminate|withdraw|abandon|"
    r"discard|retire|freeze|reset|clear|purge)$",
    re.I,
)


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


def declared_effect_observers(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    max_candidates: int = 2,
) -> list[dict[str, str]]:
    """Return exact source-declared reads for before/after effect observation."""

    target_path = normalize_path_placeholders(_text(_dict(operation).get("path")))
    if not target_path.startswith("/"):
        return []
    candidate_paths = [target_path]
    collection = normalize_path_placeholders(collection_path(target_path))
    if collection.startswith("/") and collection not in candidate_paths:
        candidate_paths.append(collection)
    limit = max(1, min(int(max_candidates or 1), 5))
    resolvers: list[dict[str, str]] = []
    for wanted in candidate_paths:
        for candidate in _list(_dict(behavior_ir).get("operations")):
            if not isinstance(candidate, dict):
                continue
            method = _text(candidate.get("method")).upper()
            path = normalize_path_placeholders(
                _text(candidate.get("path") or candidate.get("raw_path"))
            )
            if (
                method not in {"GET", "HEAD"}
                or path != wanted
                or not _text(candidate.get("id"))
            ):
                continue
            resolvers.append({
                "operation_ref": _text(candidate.get("id")),
                "method": method,
                "path": path,
            })
            if len(resolvers) >= limit:
                return resolvers
    return resolvers


def _request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(operation).get("request_example")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    request_schema = _dict(_dict(operation).get("request_schema"))
    content = _dict(request_schema.get("content"))
    for media in content.values():
        if not isinstance(media, dict):
            continue
        example = media.get("example")
        if isinstance(example, dict) and example:
            return dict(example)
        examples = _dict(media.get("examples"))
        for row in examples.values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return dict(value)
    return {}


def _body_placeholder_rows(value: Any, path: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(_body_placeholder_rows(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_body_placeholder_rows(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        match = _BODY_PLACEHOLDER_RE.match(value)
        if match and path:
            rows.append({
                "target": path,
                "template_token": _text(match.group(1)),
            })
    return rows


def _api_prefix(path: str) -> str:
    parts = [part for part in normalize_path_placeholders(path).split("/") if part]
    return f"/{parts[0]}" if parts else "/api"


def _declared_reads_for_paths(
    paths: list[str],
    *,
    behavior_ir: dict[str, Any],
    max_candidates: int = 2,
) -> list[dict[str, str]]:
    wanted = list(dict.fromkeys(
        normalize_path_placeholders(path)
        for path in paths
        if _text(path).startswith("/")
    ))
    resolvers: list[dict[str, str]] = []
    limit = max(1, min(int(max_candidates or 1), 5))
    for path in wanted:
        for operation in _list(_dict(behavior_ir).get("operations")):
            if not isinstance(operation, dict):
                continue
            declared_path = normalize_path_placeholders(
                _text(operation.get("path") or operation.get("raw_path"))
            )
            method = _text(operation.get("method")).upper()
            if (
                declared_path != path
                or method not in {"GET", "HEAD"}
                or path_has_placeholders(declared_path)
                or not _text(operation.get("id"))
            ):
                continue
            resolvers.append({
                "operation_ref": _text(operation.get("id")),
                "method": method,
                "path": declared_path,
            })
            if len(resolvers) >= limit:
                return resolvers
    return resolvers


def _declared_cleanup_operations(
    create_path: str,
    *,
    behavior_ir: dict[str, Any],
) -> list[dict[str, str]]:
    created_collection = normalize_path_placeholders(create_path).rstrip("/")
    candidates: list[tuple[int, dict[str, str]]] = []
    for operation in _list(_dict(behavior_ir).get("operations")):
        if not isinstance(operation, dict):
            continue
        method = _text(operation.get("method")).upper()
        path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if (
            method not in {"DELETE", "POST", "PATCH", "PUT"}
            or not _text(operation.get("id"))
            or not path_has_placeholders(path)
            or not path.startswith(created_collection + "/")
        ):
            continue
        is_delete = method == "DELETE"
        is_compensation = bool(_CLEANUP_ACTION_RE.search(path.rstrip("/")))
        if not is_delete and not is_compensation:
            continue
        candidates.append((
            0 if is_delete else 1,
            {
                "operation_ref": _text(operation.get("id")),
                "method": method,
                "path": path,
            },
        ))
    ordered = [row for _, row in sorted(
        candidates,
        key=lambda item: (item[0], item[1]["path"].count("/"), item[1]["path"]),
    )]
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in ordered:
        key = (row["operation_ref"], row["method"], row["path"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _declared_fixture_actor_refs(
    create_operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> list[str]:
    create_ref = _text(create_operation.get("id"))
    explicit = [
        _text(relation.get("actor_ref"))
        for relation in _list(_dict(behavior_ir).get("relations"))
        if isinstance(relation, dict)
        and _text(relation.get("relation_type")) == "permits"
        and _text(relation.get("operation_ref")) == create_ref
        and _text(relation.get("actor_ref"))
    ]
    if explicit:
        return list(dict.fromkeys(explicit))

    method = _text(create_operation.get("method")).upper()
    action_tokens = {
        "*", method.lower(), "write", "manage",
        "create", "add", "register", "submit",
    }
    path_tokens = {
        token
        for token in re.findall(
            r"[a-z0-9]+",
            normalize_path_placeholders(_text(create_operation.get("path"))).lower(),
        )
        if token not in {"api", "admin", "v1", "v2", "v3"}
        and not token.startswith("{")
    }
    path_token_forms = set(path_tokens)
    for token in path_tokens:
        if token.endswith("s") and len(token) > 3:
            path_token_forms.add(token[:-1])
        else:
            path_token_forms.add(token + "s")
    ranked: list[tuple[int, int, str]] = []
    for index, actor in enumerate(_list(_dict(behavior_ir).get("actors"))):
        if not isinstance(actor, dict) or not _text(actor.get("id")):
            continue
        actions = {
            _text(action).lower()
            for action in _list(actor.get("allowed_actions"))
            if _text(action)
        }
        resources = set()
        for resource in _list(actor.get("allowed_resources")):
            resources.update(re.findall(r"[a-z0-9]+", _text(resource).lower()))
        resource_forms = set(resources)
        for token in resources:
            if token.endswith("s") and len(token) > 3:
                resource_forms.add(token[:-1])
            else:
                resource_forms.add(token + "s")
        if not actions.intersection(action_tokens) or not resource_forms.intersection(path_token_forms):
            continue
        secret = _text(actor.get("credential_secret_ref"))
        ranked.append((0 if "test_accounts" in secret or "context" in secret else 1, index, _text(actor.get("id"))))
    return [actor_ref for _, _, actor_ref in sorted(ranked)]


def _declared_fixture_setup(
    operation: dict[str, Any],
    *,
    target: str,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    target_path = normalize_path_placeholders(_text(operation.get("path")))
    prefix = _api_prefix(target_path)
    collection_candidates = body_field_collection_paths(target, api_prefix=prefix)
    if not collection_candidates:
        primary = normalize_path_placeholders(collection_path(target_path))
        if primary.startswith("/") and not path_has_placeholders(primary):
            collection_candidates = [primary]
    operations = _list(_dict(behavior_ir).get("operations"))
    for collection in collection_candidates:
        create = next((
            candidate
            for candidate in operations
            if isinstance(candidate, dict)
            and _text(candidate.get("method")).upper() == "POST"
            and normalize_path_placeholders(
                _text(candidate.get("path") or candidate.get("raw_path"))
            ) == normalize_path_placeholders(collection)
            and not path_has_placeholders(normalize_path_placeholders(collection))
            and _text(candidate.get("id"))
        ), None)
        if not isinstance(create, dict):
            continue
        body_template = _request_example(create)
        if not body_template:
            continue
        body_bindings: list[dict[str, Any]] = []
        unresolved_body = False
        for row in _body_placeholder_rows(body_template):
            field = _text(row.get("target")).split(".")[-1].split("[")[0]
            token = _text(row.get("template_token"))
            resolvers = _declared_reads_for_paths(
                body_field_collection_paths(field or token, api_prefix=prefix)
                or body_field_collection_paths(token, api_prefix=prefix),
                behavior_ir=behavior_ir,
            )
            if not resolvers:
                unresolved_body = True
                break
            body_bindings.append({
                "target": _text(row.get("target")),
                "template_token": token,
                "resolver_operations": resolvers,
            })
        cleanup_operations = _declared_cleanup_operations(
            normalize_path_placeholders(collection),
            behavior_ir=behavior_ir,
        )
        actor_refs = _declared_fixture_actor_refs(create, behavior_ir=behavior_ir)
        if unresolved_body or not cleanup_operations or not actor_refs:
            continue
        return {
            "operation_ref": _text(create.get("id")),
            "method": "POST",
            "path": normalize_path_placeholders(collection),
            "actor_refs": actor_refs,
            "body_template": body_template,
            "body_bindings": body_bindings,
            "cleanup_operations": cleanup_operations,
        }
    return {}


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
                binding = {
                    "target": name,
                    "target_path": normalize_path_placeholders(_text(op.get("path"))),
                    "status": "runtime_resolvable",
                    "source_priority": "same_actor_list_read",
                    "resolver_operations": resolvers,
                    "value_fingerprint": "",
                }
                fixture_setup = _declared_fixture_setup(
                    op,
                    target=name,
                    behavior_ir=_dict(behavior_ir),
                )
                if fixture_setup:
                    binding["fixture_setup"] = fixture_setup
                plan.append(binding)
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
        property_spec = _dict(obl.get("property"))
        owner_actor_ref = _text(
            property_spec.get("owner_actor_ref")
            or property_spec.get("control_actor_ref")
        )
        if name == "owned_resource" and property_spec.get("require_ownership_evidence"):
            source_binding = next((
                item
                for item in plan
                if isinstance(item, dict)
                and _text(item.get("status")) == "runtime_resolvable"
                and _dict(item.get("fixture_setup"))
                and owner_actor_ref in {
                    _text(actor_ref)
                    for actor_ref in _list(_dict(item.get("fixture_setup")).get("actor_refs"))
                }
            ), None)
            if isinstance(source_binding, dict):
                setup = _dict(source_binding.get("fixture_setup"))
                source_binding["force_fixture_setup"] = True
                source_binding["required_fixture_id"] = name
                source_binding["fixture_owner_actor_ref"] = owner_actor_ref
                plan.append({
                    "target": f"fixture:{name}",
                    "fixture_id": name,
                    "status": "fixture_proof",
                    "source_priority": "experiment_setup_response",
                    "binding_target": _text(source_binding.get("target")),
                    "owner_actor_ref": owner_actor_ref,
                    "create_operation_ref": _text(setup.get("operation_ref")),
                    "create_path": _text(setup.get("path")),
                    "proof_operation_ref": _text(op.get("id")),
                    "cleanup_operations": [
                        dict(row)
                        for row in _list(setup.get("cleanup_operations"))
                        if isinstance(row, dict)
                    ],
                    "value_fingerprint": "",
                })
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
