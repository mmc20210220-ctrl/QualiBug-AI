"""Runtime binding graph with traceable source priority."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .obligation_compiler_base import _ownership_params_declared_on_operation
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
    "actor_credential_secret",
    "runtime_actor_secret_ref",
    "disposable_fixture_receipt",
    "api_doc_example",
    "source_declared_path_example",
    "source_declared_body_example",
)

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_BODY_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")
_REDACTION_PLACEHOLDER_TOKENS = frozenset({
    "REDACTED",
    "FILL",
    "TODO",
    "REPLACE",
    "SANDBOX",
})
_CREDENTIAL_FIELD_TOKENS = frozenset({
    "password",
    "passwd",
    "passphrase",
    "newpassword",
    "oldpassword",
    "currentpassword",
    "secret",
    "clientsecret",
    "apikey",
})
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
    segments = [segment for segment in target_path.strip("/").split("/") if segment]
    # Identity-bound action writes (e.g. POST /resources/{id}/confirm) must
    # prefer the source-declared entity GET (/resources/{id}) before the
    # collection GET. collection_path truncates at the first placeholder, so
    # without this ordering the collection always outranks the entity observer
    # and state changes become invisible (false ACCEPTED_WRITE_STATE_UNCHANGED).
    placeholder_positions = [
        index
        for index, segment in enumerate(segments)
        if _PLACEHOLDER_RE.fullmatch(segment)
    ]
    if placeholder_positions and placeholder_positions[-1] < len(segments) - 1:
        parent_resource = "/" + "/".join(
            segments[: placeholder_positions[-1] + 1]
        )
        if parent_resource.startswith("/") and parent_resource not in candidate_paths:
            candidate_paths.append(parent_resource)
    collection = normalize_path_placeholders(collection_path(target_path))
    if collection.startswith("/") and collection not in candidate_paths:
        candidate_paths.append(collection)
    for alternate in alternate_collection_paths(target_path):
        normalized_alternate = normalize_path_placeholders(alternate)
        if normalized_alternate.startswith("/") and normalized_alternate not in candidate_paths:
            candidate_paths.append(normalized_alternate)
    for index in range(2, len(segments)):
        prefix = "/" + "/".join(segments[:index])
        if (
            prefix.startswith("/")
            and not path_has_placeholders(prefix)
            and prefix not in candidate_paths
        ):
            candidate_paths.append(prefix)

    # Source-declared observes/produces/consumes joins may name an effect-read
    # operation even when path heuristics miss the sibling GET.
    operation_ref = _text(operation.get("id"))
    operations_by_id = {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    entity_ids = {
        _text(row.get("id"))
        for row in _list(_dict(behavior_ir).get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    # Entity ids produced/consumed/scoped by this write — join observers of the
    # same entity (produces X + observes X) without inventing paths.
    produced_entity_ids: set[str] = set()
    for relation in _list(_dict(behavior_ir).get("relations")):
        if not isinstance(relation, dict):
            continue
        relation_type = _text(relation.get("relation_type"))
        if relation_type not in {
            "observes",
            "produces",
            "consumes",
            "scopes",
        }:
            continue
        related_refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
            _text(relation.get("entity_ref")),
        }
        if operation_ref and operation_ref not in related_refs:
            continue
        for ref in related_refs:
            if not ref or ref == operation_ref:
                continue
            candidate = _dict(operations_by_id.get(ref))
            method = _text(candidate.get("method")).upper()
            path = normalize_path_placeholders(
                _text(candidate.get("path") or candidate.get("raw_path"))
            )
            if method in {"GET", "HEAD"} and path.startswith("/"):
                if path not in candidate_paths:
                    candidate_paths.append(path)
            elif (
                relation_type in {"produces", "consumes", "scopes"}
                and ref in entity_ids
            ):
                produced_entity_ids.add(ref)

    if produced_entity_ids:
        for relation in _list(_dict(behavior_ir).get("relations")):
            if not isinstance(relation, dict):
                continue
            if _text(relation.get("relation_type")) not in {
                "observes",
                "scopes",
            }:
                continue
            entity_ref = _text(
                relation.get("entity_ref") or relation.get("to_ref")
            )
            if entity_ref not in produced_entity_ids:
                continue
            observer_refs = {
                _text(relation.get("operation_ref")),
                _text(relation.get("from_ref")),
            }
            for ref in observer_refs:
                if not ref or ref == operation_ref or ref == entity_ref:
                    continue
                candidate = _dict(operations_by_id.get(ref))
                method = _text(candidate.get("method")).upper()
                path = normalize_path_placeholders(
                    _text(candidate.get("path") or candidate.get("raw_path"))
                )
                if method in {"GET", "HEAD"} and path.startswith("/"):
                    if path not in candidate_paths:
                        candidate_paths.append(path)

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
            # Collection create (register/…): a concrete same-parent GET can
            # observe side effects when no identity sibling list exists.
            domain_sibling_create_match = (
                _text(operation.get("method")).upper() == "POST"
                and not path_has_placeholders(target_path)
                and not candidate_placeholders
                and path.rstrip("/").rsplit("/", 1)[0]
                == target_path.rstrip("/").rsplit("/", 1)[0]
                and _shares_source_domain(target_path, path)
            )
            relation_declared_match = path == wanted and method in {"GET", "HEAD"}
            if (
                method not in {"GET", "HEAD"}
                or not (
                    exact_match
                    or body_bound_collection_match
                    or body_bound_domain_lookup_match
                    or response_bound_create_match
                    or domain_sibling_create_match
                    or relation_declared_match
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
    return {}


# Placeholder-form identity literals found in documentation examples: UUIDs
# whose first four groups are all zero and whose last group starts with zero
# (00000000-0000-0000-0000-000000001002 and nil/near-nil variants) plus the
# explicit unresolved marker. Legal UUIDs carry nonzero version/variant bits
# in groups 3-4, so this shape is unambiguous example filler. Sending it to a
# real target yields 500/404 (invalid reference), never a meaningful result.
_ZERO_NIL_UUID_RE = re.compile(
    r"^0{8}-0{4}-0{4}-0{4}-0[0-9a-f]{11}$",
    re.IGNORECASE,
)


def _is_placeholder_identity_literal(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or text == "QUALIBUG_UNRESOLVED_ID":
        return True
    return bool(_ZERO_NIL_UUID_RE.match(text))


def _tokenize_placeholder_identity_values(value: Any) -> Any:
    """Turn placeholder identity literals in example bodies into ``{field}``
    tokens so the runtime binding machinery resolves them from observed rows.

    Only identity-shaped fields with a resolvable entity collection are
    tokenized (userId -> users reads); a bare ``id``/``key`` with no entity
    prefix stays literal — it names the resource the body itself creates or
    issues (a JWT subject), not a foreign-key reference. Scalar business
    values (amount, reason) also stay as documented. A token with no
    resolvable read fails the binding gate visibly instead of reaching
    transport as an invalid reference."""
    from .real_id_resolver_base import body_field_collection_paths
    if isinstance(value, dict):
        return {
            str(key): (
                f"{{{str(key)}}}"
                if _field_token(key).endswith("id")
                and _is_placeholder_identity_literal(child)
                and bool(body_field_collection_paths(str(key)))
                else _tokenize_placeholder_identity_values(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_tokenize_placeholder_identity_values(child) for child in value]
    return value


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
            token = _text(match.group(1))
            if token.upper() in _REDACTION_PLACEHOLDER_TOKENS:
                leaf = path.split(".")[-1].split("[")[0]
                token = leaf or token
            rows.append({
                "target": path,
                "template_token": token,
            })
    return rows


def _is_credential_field_token(name: str) -> bool:
    token = _field_token(name)
    return token in _CREDENTIAL_FIELD_TOKENS or token.endswith("password")


def _source_declared_body_example_bindings(
    operation: dict[str, Any],
    unresolved: list[str],
    body_placeholder_paths: dict[str, list[str]],
) -> dict[str, Any] | None:
    """Source-declared example fallback for unresolved body placeholders.

    Mirrors the path-parameter example fallback (7ed1d394): when neither a
    read resolver, a fixture, nor a credential source exists for a body
    placeholder, the operation's own request-body schema property
    ``example``/``default`` is the last source-grounded value available — it
    is part of the operation contract the customer declared, never an
    invented id. A blocked placeholder without a declared example stays
    visibly BLOCKED.
    """
    examples: dict[str, Any] = {}
    request_schema = _dict(operation.get("request_schema"))
    content = _dict(request_schema.get("content"))
    schema = _dict(_dict(content.get("application/json") or {}).get("schema"))
    if not schema:
        for media in content.values():
            candidate = _dict(_dict(media).get("schema"))
            if candidate:
                schema = candidate
                break
    properties = _dict(schema.get("properties"))

    def _walk(props: dict[str, Any]) -> None:
        for field_name, field_schema in props.items():
            field = _dict(field_schema)
            value = field.get("example") or field.get("default")
            if value not in (None, ""):
                examples[_field_token(field_name)] = value
            nested = _dict(field.get("properties"))
            if nested:
                _walk(nested)
            items = _dict(field.get("items"))
            nested_items = _dict(items.get("properties"))
            if nested_items:
                _walk(nested_items)

    _walk(properties)
    if not examples:
        return None
    bindings: dict[str, Any] = {}
    for name in unresolved:
        leafs = {_field_token(name)}
        for body_path in body_placeholder_paths.get(name, []):
            leaf = body_path.split(".")[-1].split("[")[0]
            if leaf:
                leafs.add(_field_token(leaf))
        hit = next(
            (examples[key] for key in leafs if key in examples),
            None,
        )
        if hit is None:
            continue
        bindings[name] = {
            "target": name,
            "target_path": f"/{{{name}}}",
            "status": "bound",
            "materialized_value": str(hit),
            "source_priority": "source_declared_body_example",
            "value_fingerprint": "",
        }
    return bindings or None


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
    from .real_id_resolver_base import _LOOKUP_VERB_SEGMENTS
    for path in wanted:
        for operation in _list(_dict(behavior_ir).get("operations")):
            if not isinstance(operation, dict):
                continue
            declared_path = _normalize(
                _text(operation.get("path") or operation.get("raw_path"))
            )
            method = _text(operation.get("method")).upper()
            if (
                method not in {"GET", "HEAD"}
                or path_has_placeholders(normalize_path_placeholders(
                    _text(operation.get("path") or operation.get("raw_path"))
                ))
                or not _text(operation.get("id"))
            ):
                continue
            matches = declared_path == path
            if not matches:
                # Entity-scoped lookup reads: wanted=/api/users matches
                # GET /api/users/admin/search — the trailing segments close
                # with a generic lookup verb. Health/status endpoints (whose
                # final segment is not a lookup verb) stay excluded: they do
                # not return entity rows.
                prefix = path.rstrip("/") + "/"
                if declared_path.startswith(prefix):
                    tail = declared_path[len(prefix):].strip("/").split("/")
                    matches = bool(tail and tail[-1] in _LOOKUP_VERB_SEGMENTS)
            if not matches:
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
    create_refs = {
        _text(operation.get("id"))
        for operation in _list(_dict(behavior_ir).get("operations"))
        if isinstance(operation, dict)
        and _text(operation.get("method")).upper() == "POST"
        and normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        ).rstrip("/") == created_collection
        and _text(operation.get("id"))
    }
    explicit_compensators = {
        _text(relation.get("operation_ref") or relation.get("from_ref"))
        for relation in _list(_dict(behavior_ir).get("relations"))
        if isinstance(relation, dict)
        and _text(relation.get("relation_type")) == "compensates"
        and _text(relation.get("to_ref")) in create_refs
        and _text(relation.get("status")) not in {"conflicting", "unsupported"}
    }
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
        operation_ref = _text(operation.get("id"))
        is_delete = method == "DELETE"
        is_explicit_compensation = operation_ref in explicit_compensators
        if not is_delete and not is_explicit_compensation:
            continue
        candidate = {
            "operation_ref": operation_ref,
            "method": method,
            "path": path,
        }
        if not is_delete and is_explicit_compensation and len(create_refs) == 1:
            candidate["compensates_operation_ref"] = sorted(create_refs)[0]
        candidates.append((0 if is_delete else 1, candidate))
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


def declared_action_compensators(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> list[dict[str, str]]:
    """Return compensators linked to this write by an explicit source relation."""
    source_id = _text(_dict(operation).get("id"))
    operations = {
        _text(candidate.get("id")): candidate
        for candidate in _list(_dict(behavior_ir).get("operations"))
        if isinstance(candidate, dict) and _text(candidate.get("id"))
    }
    candidates: list[dict[str, str]] = []
    for relation in _list(_dict(behavior_ir).get("relations")):
        if (
            not isinstance(relation, dict)
            or _text(relation.get("relation_type") or relation.get("kind"))
            not in {"compensates", "inverse", "compensation"}
            or not _list(relation.get("source_refs"))
            or _text(relation.get("status")) in {"conflicting", "unsupported"}
        ):
            continue
        standard_cleanup = _text(
            relation.get("operation_ref") or relation.get("from_ref")
        )
        standard_primary = _text(relation.get("to_ref"))
        legacy_primary = _text(
            relation.get("source") or relation.get("source_operation_ref")
        )
        legacy_cleanup = _text(
            relation.get("target") or relation.get("target_operation_ref")
        )
        cleanup_ref = (
            standard_cleanup
            if standard_primary == source_id
            else legacy_cleanup
            if legacy_primary == source_id
            else ""
        )
        candidate = operations.get(cleanup_ref) or {}
        method = _text(candidate.get("method")).upper()
        path = normalize_path_placeholders(
            _text(candidate.get("path") or candidate.get("raw_path"))
        )
        if method not in {"POST", "PUT", "PATCH", "DELETE"} or not path:
            continue
        candidates.append({
            "operation_ref": cleanup_ref,
            "method": method,
            "path": path,
        })
    return candidates


def declared_action_recreate_primaries(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> list[dict[str, str]]:
    """Return writes explicitly named as the inverse of this cleanup action."""
    cleanup_id = _text(_dict(operation).get("id"))
    operations = {
        _text(candidate.get("id")): candidate
        for candidate in _list(_dict(behavior_ir).get("operations"))
        if isinstance(candidate, dict) and _text(candidate.get("id"))
    }
    candidates: list[dict[str, str]] = []
    for relation in _list(_dict(behavior_ir).get("relations")):
        if (
            not isinstance(relation, dict)
            or _text(relation.get("relation_type") or relation.get("kind"))
            not in {"compensates", "inverse", "compensation"}
            or not _list(relation.get("source_refs"))
            or _text(relation.get("status")) in {"conflicting", "unsupported"}
        ):
            continue
        standard_cleanup = _text(
            relation.get("operation_ref") or relation.get("from_ref")
        )
        standard_primary = _text(relation.get("to_ref"))
        legacy_primary = _text(
            relation.get("source") or relation.get("source_operation_ref")
        )
        legacy_cleanup = _text(
            relation.get("target") or relation.get("target_operation_ref")
        )
        primary_ref = (
            standard_primary
            if standard_cleanup == cleanup_id
            else legacy_primary
            if legacy_cleanup == cleanup_id
            else ""
        )
        candidate = operations.get(primary_ref) or {}
        method = _text(candidate.get("method")).upper()
        path = normalize_path_placeholders(
            _text(candidate.get("path") or candidate.get("raw_path"))
        )
        if method not in {"POST", "PUT", "PATCH"} or not path:
            continue
        candidates.append({
            "operation_ref": primary_ref,
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
    return list(dict.fromkeys(explicit))


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
    _h25_reject: list[dict[str, Any]] = []
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
            _h25_reject.append({"collection": collection, "reason": "no_post_create"})
            continue
        body_template = _request_example(create, sibling_ops=operations)
        if not body_template:
            _h25_reject.append({"collection": collection, "reason": "missing_request_example"})
            continue
        body_bindings: list[dict[str, Any]] = []
        unresolved_body = False
        unresolved_field = ""
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
                unresolved_field = field or token
                break
            body_bindings.append({
                "target": _text(row.get("target")),
                "template_token": token,
                "resolver_operations": resolvers,
            })
        if unresolved_body:
            _h25_reject.append({
                "collection": collection,
                "reason": f"unresolved_body_dependency:{unresolved_field}",
            })
            continue
        cleanup_operations = _declared_cleanup_operations(
            normalize_path_placeholders(collection),
            behavior_ir=behavior_ir,
        )
        actor_refs = _declared_fixture_actor_refs(create, behavior_ir=behavior_ir)
        if not cleanup_operations or not actor_refs:
            _h25_reject.append({
                "collection": collection,
                "reason": (
                    "missing_cleanup"
                    if not cleanup_operations
                    else "missing_fixture_actor"
                ),
                "create_ref": _text(create.get("id")),
            })
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
    path_placeholders = set(extract_placeholders(
        op.get("path"),
        op.get("operation_id"),
        *[str(p) for p in _list(op.get("parameters"))],
    ))
    placeholders = list(path_placeholders)
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
    # Ownership identity params declared on the operation (fromUserId/ownerId/
    # sellerId…, including description-driven own-scope fields) resolve from
    # runtime-observed actor identities in the isolation/validation
    # identity-binding stage. Their schema example is a placeholder-shaped
    # identity literal, never a real account — exempt them from every
    # source-example fallback so a fabricated owner never reaches transport.
    _operation_declared_ownership_params = set(
        _ownership_params_declared_on_operation(op)
    )
    for name in ordered:
        existing = _dict(values.get(name))
        source = _text(existing.get("source") or existing.get("source_priority"))
        if source and source not in BINDING_PRIORITY:
            raise ValueError(f"binding_source_priority_invalid:{source}")
        if existing.get("value") is not None and source:
            plan.append({
                "target": name,
                "status": "bound",
                "source_priority": source,
                "value_fingerprint": _fingerprint(existing.get("value")),
                "previous_fingerprint": _text(existing.get("previous_fingerprint")),
            })
        else:
            # Body ownership identity params (userId/fromUserId/ownerId/…) on
            # a same-resource authorization write (require_same_resource:
            # treatment reuses the control actor's resource) resolve from the
            # arm actors' login-observed identities — never from a list read
            # of another entity's collection. A list read (GET users/search)
            # returns whichever row sorts first, silently re-pointing the
            # write at an unrelated account whose business data (cart/orders)
            # may be empty — the control arm then fails before the rule under
            # test is observed. The materializer resolves the control arm's
            # observed identity and both arms share it (same-resource
            # semantics). Isolation-family cross-user writes stay on their
            # own arm-distinct identity channel.
            if (
                name in _operation_declared_ownership_params
                and name not in path_placeholders
                and _text(obl.get("risk_family")) == "authorization"
                and _dict(obl.get("property")).get("require_same_resource") is True
            ):
                plan.append({
                    "target": name,
                    "target_path": f"/{{{name}}}",
                    "status": "runtime_resolvable",
                    "source_priority": "ownership_identity_param",
                    "body_template_paths": list(dict.fromkeys(
                        body_placeholder_paths.get(name, [])
                    )),
                    "value_fingerprint": "",
                })
                continue
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
            if name in path_placeholders:
                # Path placeholders belong to the operation's own resource, so
                # the operation's collection reads are the correct resolvers.
                candidate_resolvers: list[dict[str, str]] = [
                    *path_resolvers,
                    *body_resolvers,
                ]
            else:
                # Body placeholders belong to the FIELD's entity (an order
                # body's addressId is an address, not an order). The operation's
                # own collection reads must never be reused for them. When the
                # field-derived collection has no declared read, fall back to
                # the entity-hint matcher so a runtime-discovered route such as
                # GET /api/users/addresses can resolve addressId.
                if not body_resolvers:
                    from .runtime_binding_resolver import (
                        _find_list_endpoints_for_entity,
                    )

                    for candidate in _find_list_endpoints_for_entity(
                        _dict(behavior_ir),
                        name,
                        collection_hints=set(body_placeholder_paths.get(name, [])),
                    ):
                        candidate_ref = _text(candidate.get("id"))
                        candidate_path = normalize_path_placeholders(
                            _text(candidate.get("path") or candidate.get("raw_path"))
                        )
                        if candidate_ref and candidate_path:
                            body_resolvers.append({
                                "operation_ref": candidate_ref,
                                "method": "GET",
                                "path": candidate_path,
                            })
                candidate_resolvers = body_resolvers
            resolvers: list[dict[str, str]] = []
            seen_resolvers: set[tuple[str, str, str]] = set()
            for resolver in candidate_resolvers:
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
            if _is_credential_field_token(name):
                credential_actor = next(
                    (
                        actor
                        for actor in (actors or [])
                        if isinstance(actor, dict)
                        and _text(
                            actor.get("credential_secret_ref") or actor.get("secret_ref")
                        )
                        and not _text(
                            actor.get("credential_secret_ref") or actor.get("secret_ref")
                        )
                        .lower()
                        .startswith("secret_ref:actor:")
                    ),
                    None,
                )
                if credential_actor is not None:
                    secret_ref = _text(
                        credential_actor.get("credential_secret_ref")
                        or credential_actor.get("secret_ref")
                    )
                    actor_ref = _text(credential_actor.get("id"))
                    plan.append({
                        "target": name,
                        "status": "runtime_resolvable",
                        "source_priority": "actor_credential_secret",
                        "actor_ref": actor_ref,
                        "credential_secret_ref": secret_ref,
                        "resolver_operations": [],
                        "fixture_setup": {
                            "kind": "actor_credential_field",
                            "field": name,
                            "actor_ref": actor_ref,
                            "credential_secret_ref": secret_ref,
                        },
                        "body_template_paths": list(dict.fromkeys(
                            body_placeholder_paths.get(name, [])
                        )),
                        "value_fingerprint": "",
                    })
                    continue
            # Source-declared body example fallback (mirrors the path-parameter
            # example fallback): no resolver, fixture, or credential source —
            # the operation's own request-body schema example/default is the
            # last source-grounded value. Without one the placeholder stays
            # visibly blocked; never invent enterprise data. Ownership
            # identity params are exempt: their schema example is a
            # placeholder-shaped identity literal, never a real account, so
            # binding it would fire a fabricated owner at the target. They
            # stay blocked here and resolve through the isolation/
            # validation identity-binding stage (ownership_identity_param).
            if name not in _operation_declared_ownership_params:
                _body_example_bindings = _source_declared_body_example_bindings(
                    op,
                    [name],
                    body_placeholder_paths,
                )
                if _body_example_bindings and name in _body_example_bindings:
                    plan.append(_body_example_bindings[name])
                    continue
            # An unbound placeholder is never a license to invent enterprise
            # data. Both path and body values must remain visibly blocked.
            is_path_param = name in path_placeholders
            if is_path_param:
                plan.append({
                    "target": name,
                    "status": "blocked",
                    "source_priority": "path_placeholder_unresolvable",
                    "resolver_operations": [],
                    "value_fingerprint": "",
                    "blocked_reason": "PLACEHOLDER_PATH_PARAMETER_NOT_RESOLVED",
                })
            else:
                plan.append({
                    "target": name,
                    "status": "blocked",
                    "source_priority": "body_placeholder_unresolvable",
                    "resolver_operations": [],
                    "value_fingerprint": "",
                    "blocked_reason": "BODY_PARAMETER_NOT_SOURCE_BOUND",
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
            # ── Owned-resource proof without a pre-bound fixture ──
            # A path-target write's placeholder binding resolves through a
            # collection GET read (same_actor_list_read), which cannot prove
            # ownership: the read returns rows the runtime cannot attribute to
            # the control actor. When no binding already carries a
            # fixture_setup, build the owned-resource fixture from the
            # collection create so the owner's resource exists to aim
            # control/treatment at. This is the generic path for
            # isolation/visibility path-target writes (DELETE
            # /api/users/addresses/{id} on an owned collection); without it
            # the obligation compiles to a visible BLOCKED_MISSING_FIXTURE and
            # the ownership boundary is never tested.
            _owned_target = ""
            _owned_path = _text(op.get("path") or op.get("raw_path"))
            if path_has_placeholders(_owned_path):
                from .real_id_resolver import infer_path_params as _infer_path_params

                _owned_target = next(iter(_infer_path_params(_owned_path)), "")
            _target_binding = next((
                item
                for item in plan
                if isinstance(item, dict)
                and _text(item.get("target")) == _owned_target
            ), None) if _owned_target else None
            _owned_setup: dict[str, Any] = {}
            if _owned_target and isinstance(_target_binding, dict):
                _owned_setup = _declared_fixture_setup(
                    operation=op,
                    target=_owned_target,
                    behavior_ir=behavior_ir,
                )
            if _owned_setup and isinstance(_target_binding, dict):
                _target_binding["fixture_setup"] = _owned_setup
                _target_binding["force_fixture_setup"] = True
                _target_binding["required_fixture_id"] = name
                _target_binding["fixture_owner_actor_ref"] = owner_actor_ref
                plan.append({
                    "target": f"fixture:{name}",
                    "fixture_id": name,
                    "status": "fixture_proof",
                    "source_priority": "owned_resource_create_fixture",
                    "binding_target": _text(_target_binding.get("target")),
                    "owner_actor_ref": owner_actor_ref,
                    "create_operation_ref": _text(_owned_setup.get("operation_ref")),
                    "create_path": _text(_owned_setup.get("path")),
                    "proof_operation_ref": _text(op.get("id")),
                    "cleanup_operations": [
                        dict(row)
                        for row in _list(_owned_setup.get("cleanup_operations"))
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
    if source_priority not in BINDING_PRIORITY:
        raise ValueError(f"binding_source_priority_invalid:{source_priority}")
    new_plan = [dict(item) for item in plan]
    priority_rank = {name: index for index, name in enumerate(BINDING_PRIORITY)}
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
        or _text(item.get("status")) == "blocked"  # blocked is terminal, not unresolved
        or (
            _text(item.get("status")) == "runtime_resolvable"
            and (
                bool(_list(item.get("resolver_operations")))
                or bool(_dict(item.get("fixture_setup")))
                # ownership_identity_param: body ownership identity fields
                # (fromUserId/ownerId/…) resolve through the isolation/
                # validation identity-binding stage from runtime-observed
                # actor identities, not through a list read.
                or _text(item.get("source_priority")) == "ownership_identity_param"
            )
        )
    }
    return [name for name in needed if name not in bound]


def blocked_binding_reasons(plan: list[dict[str, Any]]) -> list[str]:
    """Return list of blocked_reason from binding plan items with status='blocked'."""
    return [
        _text(item.get("blocked_reason")) or f"blocked:{_text(item.get('target'))}"
        for item in plan
        if isinstance(item, dict) and _text(item.get("status")) == "blocked"
    ]


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
