from __future__ import annotations

"""
API Capability Mapper — OpenAPI → APICapability mapping

Parses an OpenAPI 3.x spec and produces a list of APICapability objects.
Every path+method combination is classified into a business capability type
(read, list, create, update, delete, action, unknown) using heuristics
on path structure, HTTP methods, and schema content.

No industry-specific paths are hardcoded.  The mapper relies entirely on:
  - Path segment structure (noun vs parameter vs action-verb)
  - HTTP method semantics
  - Schema field names and JSON Schema annotations

Design goals
------------
- Deterministic classification based on structure, not vocabulary.
- Entity extraction from path templates without a dictionary of known entities.
- Observer-candidate and action-candidate flags so the discovery engine can
  auto-wire state snapshots and mutator probes.
- Multi-tenancy / correlation detection from common header/body field names.
- Pagination and filtering detection from standard query-parameter patterns.
"""

import re
from dataclasses import fields
from typing import Any

from .project_context_compiler import APICapability


# ---------------------------------------------------------------------------
# Constants — heuristic tokens, not industry-specific vocabulary
# ---------------------------------------------------------------------------

# Query parameter names that indicate pagination (standard across OpenAPI ecosystems)
_PAGINATION_PARAMS: frozenset[str] = frozenset({
    "offset", "limit", "page", "page_size", "pageSize", "per_page", "perPage",
    "cursor", "after", "before", "starting_after", "ending_before",
    "start", "end", "skip", "take", "top", "skipToken",
})

# Query parameter names that indicate filtering / search
_FILTER_PARAMS: frozenset[str] = frozenset({
    "filter", "filters", "q", "query", "search", "keyword", "term",
    "status", "state", "type", "category", "sort", "order", "order_by", "orderBy",
    "sort_by", "sortBy", "sort_dir", "sortDir", "sort_direction",
    "fields", "include", "exclude", "expand", "select",
    "created_after", "created_before", "updated_after", "updated_before",
    "from_date", "to_date", "date_from", "date_to",
})

# Header / body field names that signal correlation / tenant routing
_CORRELATION_ID_FIELDS: frozenset[str] = frozenset({
    "correlation_id", "correlationId", "correlation-id",
    "x-correlation-id", "x-request-id", "request_id", "requestId",
    "trace_id", "traceId", "span_id", "spanId",
})

_TENANT_ID_FIELDS: frozenset[str] = frozenset({
    "tenant_id", "tenantId", "tenant-id", "x-tenant-id",
    "org_id", "orgId", "organization_id", "organizationId",
    "account_id", "accountId", "workspace_id", "workspaceId",
})

# Fields in a response schema that strongly hint at an entity ID
_ENTITY_ID_RESPONSE_FIELDS: frozenset[str] = frozenset({
    "id", "uid", "uuid", "guid",
    "entity_id", "entityId",
})

# HTTP methods that mutate state → action candidates when extra path segments exist
_MUTATION_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# HTTP methods that read state → observer candidates
_READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_parameter_segment(segment: str) -> bool:
    """True when `segment` is an OpenAPI path parameter, e.g. ``{order_id}``."""
    return bool(re.fullmatch(r"\{[^}]+\}", segment))


def _unwrap_param_name(segment: str) -> str:
    """Strip curly braces from ``{order_id}`` → ``order_id``."""
    return segment.strip("{}")


def _looks_like_identifier_param(name: str) -> bool:
    """Heuristic: does the parameter name look like an entity identifier?

    Matches names ending in ``_id``, ``Id``, ``-id``, or the bare token ``id``.
    Case-insensitive on the suffix.
    """
    low = name.lower()
    if low == "id":
        return True
    for suffix in ("_id", "-id", "id"):
        if low.endswith(suffix) and len(low) > len(suffix):
            return True
    return False


def _safe_get(d: Any, *keys: str | int, default: Any = None) -> Any:
    """Deep-dict access without KeyError / TypeError."""
    cur = d
    for k in keys:
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return default
    return cur


# ---------------------------------------------------------------------------
# APICapabilityMapper
# ---------------------------------------------------------------------------

class APICapabilityMapper:
    """Parses an OpenAPI 3.x spec dict and produces :class:`APICapability` objects.

    Usage::

        mapper = APICapabilityMapper()
        capabilities = mapper.map_from_openapi(spec_dict)

        # Inspect a single endpoint
        cap_type = mapper.classify_capability("GET", "/orders/{order_id}", {})
        entity  = mapper.extract_entity_from_path("/orders/{order_id}")
    """

    # ── Public API ────────────────────────────────────────────────────

    def map_from_openapi(self, openapi_spec: dict[str, Any]) -> list[APICapability]:
        """Parse an OpenAPI 3.x spec and return one ``APICapability`` per operation.

        Args:
            openapi_spec: Parsed OpenAPI JSON/YAML dict.

        Returns:
            List of APICapability, one per (path, method) pair in the spec.
        """
        capabilities: list[APICapability] = []

        paths: dict[str, Any] = openapi_spec.get("paths", {})
        if not isinstance(paths, dict):
            return capabilities

        for path_template, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            for method in ("get", "put", "post", "delete", "options", "head",
                           "patch", "trace"):
                operation: dict[str, Any] | None = path_item.get(method)
                if not isinstance(operation, dict):
                    continue

                cap = self._build_capability(
                    method=method.upper(),
                    path=path_template,
                    operation=operation,
                    openapi_spec=openapi_spec,
                )
                capabilities.append(cap)

        return capabilities

    def classify_capability(
        self,
        method: str,
        path: str,
        schema: dict[str, Any] | None = None,
    ) -> str:
        """Classify a single endpoint into a capability type.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE, …).
            path: OpenAPI path template (e.g. ``/orders/{order_id}``).
            schema: Operation-level schema dict (parameters, requestBody, etc.).

        Returns:
            One of ``"read"``, ``"list"``, ``"create"``, ``"update"``,
            ``"delete"``, ``"action"``, or ``"unknown"``.
        """
        method = method.upper()
        segments = _path_segments(path)
        param_count = sum(1 for s in segments if _is_parameter_segment(s))

        if method == "GET":
            if param_count == 0:
                return "list"
            # One or more params: distinguish read vs list-with-filter
            # If exactly one param that looks like an ID → read; else list
            id_params = [
                s for s in segments
                if _is_parameter_segment(s) and _looks_like_identifier_param(_unwrap_param_name(s))
            ]
            if len(id_params) == 1 and param_count == 1:
                return "read"
            # Multiple params or non-id param → still list (filtered list)
            return "list"

        if method == "POST":
            if param_count == 0:
                return "create"
            # Has path parameters — could be action or sub-resource create
            # Check if the last segment is non-id action word
            last_seg = segments[-1] if segments else ""
            if (_is_parameter_segment(last_seg)
                    and _looks_like_identifier_param(_unwrap_param_name(last_seg))):
                # POST /orders/{id}/cancel → action
                return "action"
            # POST /orders/{id}/items → sub-resource create (but classify as action)
            if param_count >= 1:
                return "action"
            return "create"

        if method in ("PUT", "PATCH"):
            if param_count == 0:
                return "create"   # PUT /entity (upsert semantics)
            # One ID param → update; more → likely action
            id_params = [
                s for s in segments
                if _is_parameter_segment(s) and _looks_like_identifier_param(_unwrap_param_name(s))
            ]
            if len(id_params) >= 1:
                remaining_params = param_count - len(id_params)
                if remaining_params == 0:
                    return "update"
            return "action" if param_count >= 2 else "update"

        if method == "DELETE":
            if param_count == 0:
                return "unknown"   # DELETE /entity is unusual (bulk delete)
            return "delete"

        return "unknown"

    def extract_entity_from_path(self, path: str) -> str:
        """Extract a human-readable entity name from a path template.

        Heuristic: the rightmost non-parameter segment that is not a single
        letter and does not look like an action verb (short word).  Singularised
        when the collection form is a common English plural pattern.

        Args:
            path: OpenAPI path template (e.g. ``/api/v1/orders/{order_id}/items``).

        Returns:
            Entity name string, e.g. ``"order_item"``.  Returns ``""`` if no
            plausible entity segment is found.
        """
        segments = _path_segments(path)
        # Walk segments right-to-left, picking the first plausible entity
        for seg in reversed(segments):
            if _is_parameter_segment(seg):
                continue
            # Skip obvious non-entity prefixes
            if seg.lower() in _NON_ENTITY_PREFIXES:
                continue
            # Skip very short segments (action verbs like "me", "id")
            if len(seg) <= 2:
                continue
            # Skip version segments like v1, v2
            if re.fullmatch(r"v\d+", seg, re.IGNORECASE):
                continue
            # Good candidate — singularise if plural
            return _singularise(seg)
        return ""

    # ── Internal builders ─────────────────────────────────────────────

    def _build_capability(
        self,
        method: str,
        path: str,
        operation: dict[str, Any],
        openapi_spec: dict[str, Any],
    ) -> APICapability:
        """Construct a complete APICapability from one OpenAPI operation."""

        # ── Classification ──────────────────────────────────────────
        cap_type = self.classify_capability(method, path, operation)
        entity = self.extract_entity_from_path(path)

        # ── Parameters ──────────────────────────────────────────────
        parameters: list[dict[str, Any]] = _safe_get(operation, "parameters", default=[])
        if not isinstance(parameters, list):
            parameters = []

        path_params: list[dict[str, Any]] = []
        query_params: list[dict[str, Any]] = []
        header_params: list[dict[str, Any]] = []

        for p in parameters:
            if not isinstance(p, dict):
                continue
            loc = p.get("in", "")
            if loc == "path":
                path_params.append(p)
            elif loc == "query":
                query_params.append(p)
            elif loc == "header":
                header_params.append(p)

        # ── Body schema ─────────────────────────────────────────────
        body_schema: dict[str, Any] | None = None
        request_body: dict[str, Any] | None = _safe_get(operation, "requestBody")
        if isinstance(request_body, dict):
            body_schema = _safe_get(
                request_body, "content", "application/json", "schema",
            ) or _safe_get(
                request_body, "content", "*/*", "schema",
            )

        # ── Response schema (first 2xx) ─────────────────────────────
        response_schema: dict[str, Any] | None = None
        responses: dict[str, Any] | None = _safe_get(operation, "responses")
        if isinstance(responses, dict):
            for status, resp in responses.items():
                if not isinstance(resp, dict):
                    continue
                if status.startswith("2"):
                    response_schema = _safe_get(
                        resp, "content", "application/json", "schema",
                    )
                    if response_schema is not None:
                        break

        # ── Resolve $ref in body / response schemas where possible ──
        body_schema = _resolve_ref(body_schema, openapi_spec)
        response_schema = _resolve_ref(response_schema, openapi_spec)

        # ── Entity ID detection ─────────────────────────────────────
        entity_id_param = ""
        has_entity_id_in_path = False
        for pp in path_params:
            name = pp.get("name", "")
            if _looks_like_identifier_param(name):
                entity_id_param = name
                has_entity_id_in_path = True
                break

        has_entity_id_in_response = _schema_has_field(
            response_schema, _ENTITY_ID_RESPONSE_FIELDS
        )

        # ── Observer / action candidates ────────────────────────────
        is_observer_candidate = (
            method in _READ_METHODS
            and response_schema is not None
            and (
                has_entity_id_in_response
                or has_entity_id_in_path
            )
        )

        is_action_candidate = (
            method in _MUTATION_METHODS
            and (
                has_entity_id_in_path
                or cap_type in ("create", "update", "delete", "action")
            )
        )

        # ── Correlation / tenant detection ──────────────────────────
        has_correlation_id = _detect_field(
            header_params=header_params,
            body_schema=body_schema,
            response_schema=response_schema,
            target_names=_CORRELATION_ID_FIELDS,
        )
        has_tenant_id = _detect_field(
            header_params=header_params,
            body_schema=body_schema,
            response_schema=response_schema,
            target_names=_TENANT_ID_FIELDS,
        )

        # ── Pagination / filtering ──────────────────────────────────
        query_param_names = {qp.get("name", "").lower() for qp in query_params}
        supports_pagination = bool(query_param_names & _PAGINATION_PARAMS)
        supports_filtering = bool(query_param_names & _FILTER_PARAMS)

        # ── Assemble ────────────────────────────────────────────────
        return APICapability(
            capability=cap_type,
            method=method,
            path=path,
            operation_id=operation.get("operationId", ""),
            summary=operation.get("summary", ""),
            description=operation.get("description", ""),
            tags=list(operation.get("tags", []) or []),
            entity=entity,
            entity_id_param=entity_id_param,
            has_entity_id_in_path=has_entity_id_in_path,
            has_entity_id_in_response=has_entity_id_in_response,
            path_params=path_params,
            query_params=query_params,
            header_params=header_params,
            body_schema=body_schema,
            response_schema=response_schema,
            is_observer_candidate=is_observer_candidate,
            is_action_candidate=is_action_candidate,
            has_correlation_id=has_correlation_id,
            has_tenant_id=has_tenant_id,
            supports_pagination=supports_pagination,
            supports_filtering=supports_filtering,
            security=list(operation.get("security", []) or []),
            deprecated=bool(operation.get("deprecated", False)),
            request_body_required=_safe_get(
                operation, "requestBody", "required", default=False
            ),
        )


# ---------------------------------------------------------------------------
# Internal helpers — kept at module level for testability
# ---------------------------------------------------------------------------

# Known API prefix segments that should not be treated as entity names
_NON_ENTITY_PREFIXES: frozenset[str] = frozenset({
    "api", "rest", "graphql", "rpc", "web", "public", "internal", "external",
    "auth", "oauth", "oauth2", "login", "logout", "token", "refresh",
    "health", "healthz", "ready", "live", "metrics", "status", "ping",
    "search", "batch", "bulk", "admin", "system", "config", "settings",
    "me", "self", "profile",
})

# Plural → singular suffix mappings (English, heuristic)
_PLURAL_TO_SINGULAR: list[tuple[str, str]] = [
    ("ies", "y"),    # categories → category
    ("ives", "ife"), # lives → life (rare in API paths)
    ("ves", "f"),    # shelves → shelf
    ("ses", "s"),    # addresses → address (when ending in -sses, keep original)
    ("xes", "x"),    # boxes → box
    ("ches", "ch"),  # matches → match
    ("shes", "sh"),  # dishes → dish
    ("ses", "sis"),  # bases → basis, analyses → analysis (complex — kept simple)
    ("men", "man"),  # women → woman
    ("s", ""),       # orders → order (default)
]


def _path_segments(path: str) -> list[str]:
    """Split a path template into non-empty segments, stripping leading/trailing slashes."""
    return [s for s in path.strip("/").split("/") if s]


def _singularise(word: str) -> str:
    """Best-effort English plural → singular conversion."""
    low = word.lower()
    for suffix, replacement in _PLURAL_TO_SINGULAR:
        if low.endswith(suffix):
            singular = low[: -len(suffix)] + replacement
            # Preserve original casing pattern
            if word == low:
                return singular
            if word[0].isupper():
                return singular.capitalize()
            return singular
    return word


def _resolve_ref(
    schema: dict[str, Any] | None,
    openapi_spec: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve a ``$ref`` pointer one level deep in the OpenAPI spec.

    Only processes top-level ``$ref`` on the schema dict itself; does not
    recursively walk nested subschemas (keeps the mapper fast).
    """
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    # #/components/schemas/Foo → components → schemas → Foo
    resolved = _safe_get(openapi_spec, *ref.lstrip("#/").split("/"))
    if isinstance(resolved, dict):
        return resolved
    return schema


def _schema_has_field(
    schema: dict[str, Any] | None,
    target_names: frozenset[str],
) -> bool:
    """Check whether a JSON Schema object contains any of the target field names
    in its ``properties`` or top-level keys.

    Handles both object schemas (``properties`` dict) and array schemas
    (``items.properties``).
    """
    if not isinstance(schema, dict):
        return False

    # Direct properties
    properties: dict[str, Any] | None = schema.get("properties")
    if isinstance(properties, dict):
        if any(name in properties for name in target_names):
            return True
        # Check nested objects one level deep
        for prop_schema in properties.values():
            if isinstance(prop_schema, dict):
                nested = prop_schema.get("properties")
                if isinstance(nested, dict) and any(
                    name in nested for name in target_names
                ):
                    return True

    # Array items
    items: dict[str, Any] | None = schema.get("items")
    if isinstance(items, dict):
        return _schema_has_field(items, target_names)

    return False


def _detect_field(
    header_params: list[dict[str, Any]],
    body_schema: dict[str, Any] | None,
    response_schema: dict[str, Any] | None,
    target_names: frozenset[str],
) -> bool:
    """Check for the presence of a named field across headers, body schema,
    and response schema.
    """
    # Headers
    for hp in header_params:
        if hp.get("name", "").lower() in target_names:
            return True

    # Body request schema
    if _schema_has_field(body_schema, target_names):
        return True

    # Response schema
    if _schema_has_field(response_schema, target_names):
        return True

    return False
