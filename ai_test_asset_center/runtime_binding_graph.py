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
    for alternate in alternate_collection_paths(target_path):
        normalized_alternate = normalize_path_placeholders(alternate)
        if normalized_alternate.startswith("/") and normalized_alternate not in candidate_paths:
            candidate_paths.append(normalized_alternate)
    segments = [segment for segment in target_path.strip("/").split("/") if segment]
    for index in range(2, len(segments)):
        prefix = "/" + "/".join(segments[:index])
        if (
            prefix.startswith("/")
            and not path_has_placeholders(prefix)
            and prefix not in candidate_paths
        ):
            candidate_paths.append(prefix)
    placeholder_positions = [
        index
        for index, segment in enumerate(segments)
        if _PLACEHOLDER_RE.fullmatch(segment)
    ]
    if placeholder_positions and placeholder_positions[-1] < len(segments) - 1:
        parent_resource = "/" + "/".join(
            segments[:placeholder_positions[-1] + 1]
        )
        if parent_resource not in candidate_paths:
            candidate_paths.append(parent_resource)
    limit = max(1, min(int(max_candidates or 1), 5))
    resolvers: list[dict[str, str]] = []
    seen_resolvers: set[tuple[str, str, str]] = set()
    request_fields = set(_request_example(operation))
    for wanted in candidate_paths:
        for candidate in _list(_dict(behavior_ir).get("operations")):
            if not isinstance(candidate, dict):
                continue
            method = _text(candidate.get("method")).upper()
            path = normalize_path_placeholders(
                _text(candidate.get("path") or candidate.get("raw_path"))
            )
            candidate_placeholders = extract_placeholders(path)
            candidate_collection = normalize_path_placeholders(collection_path(path))
            exact_match = path == wanted
            body_bound_collection_match = (
                bool(candidate_placeholders)
                and candidate_collection == wanted
                and all(name in request_fields for name in candidate_placeholders)
            )
            body_bound_domain_lookup_match = (
                bool(candidate_placeholders)
                and _body_bound_observer_match(
                    target_path=target_path,
                    observer_path=path,
                    request_fields=request_fields,
                )
            )
            response_bound_create_match = (
                _text(operation.get("method")).upper() == "POST"
                and not path_has_placeholders(target_path)
                and len(candidate_placeholders) == 1
                and candidate_collection == target_path
            )
            if (
                method not in {"GET", "HEAD"}
                or not (
                    exact_match
                    or body_bound_collection_match
                    or body_bound_domain_lookup_match
                    or response_bound_create_match
                )
                or not _text(candidate.get("id"))
            ):
                continue
            resolver_key = (_text(candidate.get("id")), method, path)
            if resolver_key in seen_resolvers:
                continue
            seen_resolvers.add(resolver_key)
            resolvers.append({
                "operation_ref": _text(candidate.get("id")),
                "method": method,
                "path": path,
            })
            if len(resolvers) >= limit:
                return resolvers
    return resolvers


def _request_example(operation: dict[str, Any], *, sibling_ops: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
    # Inherit from sibling POST operations sharing the same path prefix
    op_path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    ).rstrip("/")
    op_prefix = op_path.rsplit("/", 1)[0] if "/" in op_path else ""
    # A root API prefix (for example ``/api``) is shared by unrelated
    # resources.  Inheriting a sibling body at that level fabricates path/body
    # placeholders on read operations and later blocks otherwise executable
    # experiments.  Only inherit when the shared prefix names a concrete
    # resource domain.
    op_prefix_parts = [part for part in op_prefix.strip("/").split("/") if part]
    if len(op_prefix_parts) >= 2 and sibling_ops:
        for candidate in sibling_ops:
            if not isinstance(candidate, dict):
                continue
            if _text(candidate.get("method")).upper() != "POST":
                continue
            c_path = normalize_path_placeholders(
                _text(candidate.get("path") or candidate.get("raw_path"))
            ).rstrip("/")
            c_prefix = c_path.rsplit("/", 1)[0] if "/" in c_path else ""
            if c_prefix == op_prefix and c_path != op_path:
                c_example = _dict(candidate.get("request_example"))
                if c_example:
                    return dict(c_example)
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


def _field_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _static_domain_segments(path: str) -> list[str]:
    segments = [
        segment
        for segment in normalize_path_placeholders(path).strip("/").split("/")
        if segment and not _PLACEHOLDER_RE.fullmatch(segment)
    ]
    while segments and (
        segments[0].lower() == "api"
        or re.fullmatch(r"v\d+(?:\.\d+)?", segments[0].lower())
    ):
        segments.pop(0)
    return [segment.lower() for segment in segments]


def _shares_source_domain(target_path: str, observer_path: str) -> bool:
    target_segments = _static_domain_segments(target_path)
    observer_segments = _static_domain_segments(observer_path)
    return bool(
        target_segments
        and observer_segments
        and target_segments[0] == observer_segments[0]
    )


def _body_bound_observer_match(
    *,
    target_path: str,
    observer_path: str,
    request_fields: set[str],
) -> bool:
    placeholders = extract_placeholders(observer_path)
    if not placeholders:
        return False
    normalized_fields = {_field_token(field) for field in request_fields if _field_token(field)}
    if not normalized_fields:
        return False
    if not all(_field_token(name) in normalized_fields for name in placeholders):
        return False
    return _shares_source_domain(target_path, observer_path)


def _declared_reads_for_paths(
    paths: list[str],
    *,
    behavior_ir: dict[str, Any],
    max_candidates: int = 2,
) -> list[dict[str, str]]:
    def _normalize(p: str) -> str:
        return normalize_path_placeholders(p).rstrip("/").lower()

    wanted = list(dict.fromkeys(
        _normalize(path)
        for path in paths
        if _text(path).startswith("/")
    ))
    resolvers: list[dict[str, str]] = []
    limit = max(1, min(int(max_candidates or 1), 5))
    for path in wanted:
        for operation in _list(_dict(behavior_ir).get("operations")):
            if not isinstance(operation, dict):
                continue
            declared_path = _normalize(
                _text(operation.get("path") or operation.get("raw_path"))
            )
            method = _text(operation.get("method")).upper()
            if (
                declared_path != path
                or method not in {"GET", "HEAD"}
                or path_has_placeholders(normalize_path_placeholders(
                    _text(operation.get("path") or operation.get("raw_path"))
                ))
                or not _text(operation.get("id"))
            ):
                continue
            resolvers.append({
                "operation_ref": _text(operation.get("id")),
                "method": method,
                "path": normalize_path_placeholders(
                    _text(operation.get("path") or operation.get("raw_path"))
                ),
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


def _request_contract_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted(
            (_text(key), _request_contract_shape(child))
            for key, child in value.items()
        ))
    if isinstance(value, list):
        return ("list", tuple(_request_contract_shape(child) for child in value))
    if isinstance(value, str):
        placeholder = _BODY_PLACEHOLDER_RE.match(value)
        return ("placeholder", _text(placeholder.group(1))) if placeholder else "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _source_text_references_action(
    source_operation: dict[str, Any],
    candidate_operation: dict[str, Any],
) -> bool:
    source_path = normalize_path_placeholders(
        _text(source_operation.get("path") or source_operation.get("raw_path"))
    ).rstrip("/")
    source_action = source_path.rsplit("/", 1)[-1].lower()
    source_text = re.sub(
        r"[\W_]+",
        "",
        " ".join([
            _text(source_operation.get("summary")),
            _text(source_operation.get("description")),
        ]).lower(),
    )
    candidate_text = re.sub(
        r"[\W_]+",
        "",
        " ".join([
            _text(candidate_operation.get("summary")),
            _text(candidate_operation.get("description")),
        ]).lower(),
    )
    return bool(
        candidate_text
        and (
            (len(source_text) >= 4 and source_text in candidate_text)
            or (len(source_action) >= 4 and source_action in candidate_text)
        )
    )


def declared_action_compensators(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> list[dict[str, str]]:
    """Return unique source-declared action endpoints that can compensate a write.

    Structural compatibility alone is insufficient. A candidate must share the
    same action parent and request contract, be explicitly documented, expose a
    real effect observer, and its own source text must reference the original
    action. Ambiguous candidates are returned to the compiler, which fails
    closed unless exactly one remains.
    """
    source = _dict(operation)
    source_id = _text(source.get("id"))
    source_path = normalize_path_placeholders(
        _text(source.get("path") or source.get("raw_path"))
    ).rstrip("/")
    source_body = _request_example(source)
    if (
        not source_id
        or _text(source.get("method")).upper() not in {"POST", "PUT", "PATCH"}
        or "/" not in source_path
        or not source_body
        or not _list(source.get("source_refs"))
    ):
        return []
    parent_path = source_path.rsplit("/", 1)[0]
    source_shape = _request_contract_shape(source_body)
    candidates: list[dict[str, str]] = []
    for candidate in _list(_dict(behavior_ir).get("operations")):
        if not isinstance(candidate, dict) or _text(candidate.get("id")) == source_id:
            continue
        method = _text(candidate.get("method")).upper()
        path = normalize_path_placeholders(
            _text(candidate.get("path") or candidate.get("raw_path"))
        ).rstrip("/")
        terminal = path.rsplit("/", 1)[-1]
        if (
            method not in {"POST", "PUT", "PATCH"}
            or path.rsplit("/", 1)[0] != parent_path
            or not _CLEANUP_ACTION_RE.search(terminal)
            or not _list(candidate.get("source_refs"))
            or _request_contract_shape(_request_example(candidate)) != source_shape
            or not _source_text_references_action(source, candidate)
            or not declared_effect_observers(candidate, behavior_ir=behavior_ir)
        ):
            continue
        candidates.append({
            "operation_ref": _text(candidate.get("id")),
            "method": method,
            "path": path,
        })
    return candidates


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
    result = [actor_ref for _, _, actor_ref in sorted(ranked)]
    if not result:
        # Fallback: use the first admin actor when no explicit permissions
        # are declared for this operation. Admin role should have universal
        # access in most enterprise systems.
        for actor in _list(_dict(behavior_ir).get("actors")):
            if isinstance(actor, dict) and _text(actor.get("role")).lower() == "admin":
                return [_text(actor.get("id"))]
    return result


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
        body_template = _request_example(create, sibling_ops=operations)
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
        # Allow fixture creation even without cleanup operations or actors.
        # Missing cleanup means the created resource can't be automatically
        # removed after the test — acceptable for non-production targets.
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
    body_placeholder_paths: dict[str, list[str]] = {}
    _ir_ops = _list(_dict(behavior_ir).get("operations"))
    for row in _body_placeholder_rows(_request_example(op, sibling_ops=_ir_ops)):
        token = _text(row.get("template_token"))
        body_path = _text(row.get("target"))
        if token and body_path:
            body_placeholder_paths.setdefault(token, []).append(body_path)
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
    placeholders.extend(body_placeholder_paths)
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
            path_resolvers = declared_runtime_read_resolvers(
                op,
                behavior_ir=_dict(behavior_ir),
            )
            body_resolvers: list[dict[str, str]] = []
            for body_path in body_placeholder_paths.get(name, []):
                field = body_path.split(".")[-1].split("[")[0]
                body_resolvers.extend(_declared_reads_for_paths(
                    body_field_collection_paths(field or name, api_prefix=_api_prefix(_text(op.get("path"))))
                    or body_field_collection_paths(name, api_prefix=_api_prefix(_text(op.get("path")))),
                    behavior_ir=_dict(behavior_ir),
                ))
            resolvers: list[dict[str, str]] = []
            seen_resolvers: set[tuple[str, str, str]] = set()
            for resolver in [*path_resolvers, *body_resolvers]:
                key = (
                    _text(resolver.get("operation_ref")),
                    _text(resolver.get("method")).upper(),
                    _text(resolver.get("path")),
                )
                if key in seen_resolvers:
                    continue
                seen_resolvers.add(key)
                resolvers.append(dict(resolver))
            if resolvers:
                binding = {
                    "target": name,
                    "target_path": (
                        normalize_path_placeholders(_text(op.get("path")))
                        if name in extract_placeholders(op.get("path"))
                        else f"/{{{name}}}"
                    ),
                    "status": "runtime_resolvable",
                    "source_priority": "same_actor_list_read",
                    "resolver_operations": resolvers,
                    "value_fingerprint": "",
                }
                if body_placeholder_paths.get(name):
                    binding["body_template_paths"] = list(dict.fromkeys(
                        body_placeholder_paths[name]
                    ))
                fixture_setup = _declared_fixture_setup(
                    op,
                    target=name,
                    behavior_ir=_dict(behavior_ir),
                )
                if fixture_setup:
                    binding["fixture_setup"] = fixture_setup
                plan.append(binding)
                continue
            # When no read resolvers exist, check if we can create the resource
            _create_only = _declared_fixture_setup(
                op,
                target=name,
                behavior_ir=_dict(behavior_ir),
            )
            if _create_only:
                plan.append({
                    "target": name,
                    "status": "runtime_resolvable",
                    "source_priority": "fixture_create_only",
                    "resolver_operations": [],
                    "fixture_setup": _create_only,
                    "value_fingerprint": "",
                })
                continue
            plan.append({
                "target": name,
                "status": "unresolved",
                "source_priority": "",
                "resolver_operations": [],
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
    # Enrich with cross-entity relation chains before returning
    plan = enrich_binding_plan_with_relation_chain(plan, behavior_ir=behavior_ir)
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
    op = _dict(operation)
    needed = extract_placeholders(op.get("path"))
    needed.extend(
        _text(row.get("template_token"))
        for row in _body_placeholder_rows(_request_example(op))
        if _text(row.get("template_token"))
    )
    needed = list(dict.fromkeys(needed))
    bound = {
        _text(item.get("target"))
        for item in plan
        if _text(item.get("status")) == "bound"
        or (
            _text(item.get("status")) == "runtime_resolvable"
            and (
                bool(_list(item.get("resolver_operations")))
                or bool(_dict(item.get("fixture_setup")))
                or item.get("synthetic_value") is not None
            )
        )
    }
    return [name for name in needed if name not in bound]


# ── Cross-entity relation chain resolver ─────────────────────────────────

def _cross_entity_resolver_chain(
    target_path: str,
    *,
    behavior_ir: dict[str, Any],
    visited: set[str] | None = None,
    depth: int = 0,
) -> list[dict[str, Any]]:
    """Build a multi-hop resolver chain by traversing Behavior IR relations.

    When a path param like ``{resourceId}`` cannot be resolved directly from the
    target endpoint's collection list response, this function walks the
    relation graph (``produces``, ``scopes``, ``owns``, ``consumes``) to find
    parent entities whose collection list can supply the missing identity.

    Returns a list of resolver steps, each with ``operation_ref``, ``path``,
    and ``param`` fields.  The chain is ordered from outermost (first to
    resolve) to innermost (the original target).
    """
    if visited is None:
        visited = set()
    if depth > 4:
        return []

    target_norm = normalize_path_placeholders(target_path).rstrip("/").lower()
    if target_norm in visited:
        return []
    visited.add(target_norm)

    # Find the entity node that maps to this collection path
    entities = _list(_dict(behavior_ir).get("entities"))
    target_entity = None
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_paths = [
            normalize_path_placeholders(_text(p)).rstrip("/").lower()
            for p in (
                [_text(entity.get("collection_path"))]
                + _list(entity.get("alternate_paths"))
            )
            if _text(p)
        ]
        if target_norm in entity_paths:
            target_entity = entity
            break

    if target_entity is None:
        return []

    target_entity_ref = _text(target_entity.get("id"))
    chain: list[dict[str, Any]] = []

    # Walk relations to find parent entities that scope/produce/own this one
    relations = _list(_dict(behavior_ir).get("relations"))
    parent_relations = [
        rel for rel in relations
        if isinstance(rel, dict)
        and _text(rel.get("relation_type")) in {"produces", "scopes", "owns", "consumes"}
        and _text(rel.get("to_ref")) == target_entity_ref
    ]

    for rel in parent_relations:
        parent_ref = _text(rel.get("from_ref"))
        # Find the parent entity
        parent_entity = None
        for entity in entities:
            if isinstance(entity, dict) and _text(entity.get("id")) == parent_ref:
                parent_entity = entity
                break
        if parent_entity is None:
            continue

        # Derive the parent's collection path
        parent_collection = _text(parent_entity.get("collection_path"))
        if not parent_collection:
            parent_collection = _text(parent_entity.get("name")).lower()
            if parent_collection:
                parent_collection = "/api/" + parent_collection.replace(" ", "_") + "s"

        # Recurse to resolve the parent's own dependencies first
        parent_chain = _cross_entity_resolver_chain(
            parent_collection,
            behavior_ir=behavior_ir,
            visited=visited,
            depth=depth + 1,
        )
        chain.extend(parent_chain)

        # Add a resolver step: list the parent collection to get IDs
        parent_reads = _declared_reads_for_paths(
            [parent_collection],
            behavior_ir=behavior_ir,
            max_candidates=1,
        )
        for parent_read in parent_reads:
            chain.append({
                "stage": "cross_entity_parent",
                "depth": depth,
                "relation_type": _text(rel.get("relation_type")),
                "parent_entity_ref": parent_ref,
                "child_entity_ref": target_entity_ref,
                "operation_ref": parent_read["operation_ref"],
                "method": parent_read["method"],
                "path": parent_read["path"],
                "bind_for": _text(rel.get("to_ref")),
                "param": "id",
            })
            # Only take one parent resolver per depth level
            break
        break  # Only use the first viable parent relation

    return chain


def enrich_binding_plan_with_relation_chain(
    plan: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Augment a binding plan with cross-entity resolver chains.

    For each unresolved placeholder in the plan, attempts to build a relation-
    based resolver chain and appends it as additional resolver operations.
    """
    if not plan:
        return plan

    enriched = list(plan)
    for item in enriched:
        if not isinstance(item, dict):
            continue
        if _text(item.get("status")) not in {"unresolved", "runtime_resolvable"}:
            continue

        target_path = normalize_path_placeholders(
            _text(item.get("path") or item.get("target_path"))
        )
        if not target_path:
            continue

        chain = _cross_entity_resolver_chain(
            target_path,
            behavior_ir=behavior_ir,
        )
        if not chain:
            continue

        # Append chain steps as additional resolver operations
        existing_resolvers = _list(item.get("resolver_operations"))
        for step in chain:
            existing_resolvers.append({
                "operation_ref": step["operation_ref"],
                "method": step.get("method", "GET"),
                "path": step["path"],
                "source": f"cross_entity_relation:{step.get('relation_type')}",
            })
        item["resolver_operations"] = existing_resolvers
        item["cross_entity_chain"] = chain
        if existing_resolvers:
            item["status"] = "runtime_resolvable"

    return enriched
