"""Automatic runtime binding resolution for experiment execution.

Resolves path placeholders (e.g. {id}, {orderId}) by calling GET list
endpoints declared in Behavior IR. Fully data-driven and industry-neutral:
only consumes operations declared in the Behavior IR graph.

Schema: qualibug.runtime-binding-resolver.v1
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
import urllib.parse
from typing import Any

_SCHEMA = "qualibug.runtime-binding-resolver.v1"
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_COLON_PARAM_RE = re.compile(r"(?<=/):([a-zA-Z_]\w*)\b")

# Timeout for binding resolution HTTP calls (seconds)
_BINDING_TIMEOUT = 10


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_placeholders(path: str) -> list[str]:
    """Extract placeholder names from a path template ({param} and :param)."""
    results = _PLACEHOLDER_RE.findall(path or "")
    results.extend(_COLON_PARAM_RE.findall(path or ""))
    return results


def collection_segment_for_placeholder(path: str, placeholder_name: str) -> str:
    """Return the path segment that owns ``placeholder_name``.

    ``/api/orders/{id}/confirm`` → ``orders``
    ``/api/cart/items/{id}`` → ``items``
    ``/api/orders/:orderId/ship`` → ``orders``

    Empty when the placeholder is absent or has no owning collection segment.
    Never invents a collection: the segment is taken verbatim from the path.
    """
    normalized = _text(path)
    if not normalized.startswith("/"):
        return ""
    ph = _text(placeholder_name)
    if not ph:
        return ""
    markers = {f"{{{ph}}}", f":{ph}"}
    segments = [segment for segment in normalized.strip("/").split("/") if segment]
    for index, segment in enumerate(segments):
        if segment in markers:
            if index == 0:
                return ""
            return segments[index - 1].lower()
    return ""


def collection_path_for_placeholder(path: str, placeholder_name: str) -> str:
    """Return the exact static collection prefix owning a path placeholder."""
    normalized = _text(path)
    placeholder = _text(placeholder_name)
    if not normalized.startswith("/") or not placeholder:
        return ""
    markers = {f"{{{placeholder}}}", f":{placeholder}"}
    segments = [segment for segment in normalized.strip("/").split("/") if segment]
    for index, segment in enumerate(segments):
        if segment in markers and index > 0:
            prefix = segments[:index]
            if any(_extract_placeholders("/" + item) for item in prefix):
                return ""
            return "/" + "/".join(prefix)
    return ""


def declared_identity_read_operations(
    operations: list[dict[str, Any]],
    *,
    collection_path: str,
) -> list[dict[str, Any]]:
    """Find exact source-declared GET/HEAD operations for one collection entity.

    A collection list response is not enough to prove that its identifier can be
    observed on an entity route. The proof route must exist in Behavior IR and
    must be exactly ``<collection>/<one placeholder>``; action routes and paths
    with additional unresolved parameters are rejected.
    """
    collection_segments = [
        segment
        for segment in _text(collection_path).strip("/").split("/")
        if segment
    ]
    if not collection_segments:
        return []
    candidates: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if _text(operation.get("method")).upper() not in {"GET", "HEAD"}:
            continue
        path = _text(operation.get("path") or operation.get("raw_path"))
        if not path.startswith("/"):
            continue
        segments = [segment for segment in path.strip("/").split("/") if segment]
        placeholders = _extract_placeholders(path)
        if (
            len(placeholders) == 1
            and len(segments) == len(collection_segments) + 1
            and segments[:-1] == collection_segments
            and segments[-1] in {
                f"{{{placeholders[0]}}}",
                f":{placeholders[0]}",
            }
        ):
            candidates.append(operation)
    return candidates


def materialize_declared_identity_read(
    operation: dict[str, Any],
    resource_id: Any,
) -> str:
    """Materialize one exact declared entity read without deriving a new route."""
    path = _text(operation.get("path") or operation.get("raw_path"))
    value = _text(resource_id)
    placeholders = _extract_placeholders(path)
    if not value or len(placeholders) != 1:
        return ""
    if (
        value in {".", ".."}
        or any(char in value for char in ("/", "\\", "?", "#", "\r", "\n"))
    ):
        return ""
    encoded_value = urllib.parse.quote(value, safe="")
    placeholder = placeholders[0]
    materialized = path.replace(f"{{{placeholder}}}", encoded_value)
    materialized = re.sub(
        rf"(?<=/):{re.escape(placeholder)}\b",
        lambda _match: encoded_value,
        materialized,
    )
    if _extract_placeholders(materialized):
        return ""
    return materialized


def _entity_hint(placeholder_name: str) -> str:
    ph_lower = _text(placeholder_name).lower()
    return re.sub(r"(id|_id|Id)$", "", ph_lower).strip("_")


def _find_list_endpoints_for_entity(
    behavior_ir: dict[str, Any],
    placeholder_name: str,
    *,
    collection_hints: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Find GET endpoints that can resolve a placeholder.

    Strategy: match placeholder name and/or owning collection segment(s) from
    the write paths that need the binding. Generic ``{id}`` must never match
    every list endpoint — that cross-binds cart item ids into order confirms.
    """
    operations = _list(behavior_ir.get("operations"))
    candidates: list[dict[str, Any]] = []
    entity_hint = _entity_hint(placeholder_name)
    hints = {
        _text(hint).lower()
        for hint in (collection_hints or set())
        if _text(hint)
    }
    path_hints = {hint.rstrip("/") for hint in hints if hint.startswith("/")}
    segment_hints = hints - path_hints

    for op in operations:
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        # Collection binding needs a response body. A declared HEAD operation
        # cannot authorize an undeclared GET against the same route.
        if method != "GET":
            continue
        path = _text(op.get("path") or op.get("raw_path"))
        if not path:
            continue
        # Skip paths with unresolved placeholders (can't call them)
        if _PLACEHOLDER_RE.search(path) or _COLON_PARAM_RE.search(path):
            continue
        path_lower = path.lower()
        path_segments = {
            segment.lower()
            for segment in path.strip("/").split("/")
            if segment
        }
        if path_hints:
            if path_lower.rstrip("/") not in path_hints:
                continue
            candidates.append(op)
            continue
        if segment_hints:
            if not (segment_hints & path_segments):
                continue
            candidates.append(op)
            continue
        # No collection context: only named placeholders (orderId → order)
        # may match by entity hint. Bare {id} stays unresolved so the
        # per-experiment path-scoped materializer can bind correctly.
        if entity_hint and entity_hint in path_lower:
            candidates.append(op)

    return candidates


def _call_get_endpoint(
    base_url: str,
    path: str,
    token: str,
    timeout: int = _BINDING_TIMEOUT,
) -> dict[str, Any] | None:
    """Call a GET endpoint and return parsed JSON response."""
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def _call_read_status(
    base_url: str,
    path: str,
    token: str,
    *,
    method: str,
    timeout: int = _BINDING_TIMEOUT,
) -> int:
    """Return status for an exact declared GET/HEAD, or 0 on transport failure."""
    read_method = _text(method).upper()
    if read_method not in {"GET", "HEAD"}:
        raise ValueError(f"unsupported_identity_read_method:{read_method}")
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers, method=read_method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError):
        return 0


def _call_get_status(
    base_url: str,
    path: str,
    token: str,
    timeout: int = _BINDING_TIMEOUT,
) -> int:
    """Compatibility wrapper for declared GET identity observers."""
    return _call_read_status(
        base_url,
        path,
        token,
        method="GET",
        timeout=timeout,
    )


def _extract_id_from_response(response: Any, placeholder_name: str) -> str:
    """Extract a resource ID from a GET list response.

    Handles common response shapes:
    - [ {id: "...", ...}, ... ]
    - { data: [ {id: "...", ...} ] }
    - { items: [ ... ] }
    - { results: [ ... ] }
    - { content: [ ... ] }
    """
    items: list[Any] = []
    if isinstance(response, list):
        items = response
    elif isinstance(response, dict):
        # Try common wrapper keys
        for key in ("data", "items", "results", "content", "records", "rows", "list"):
            val = response.get(key)
            if isinstance(val, list):
                items = val
                break
        if not items and isinstance(response.get("data"), dict):
            # Nested: {data: {items: [...]}}
            nested = response["data"]
            for key in ("items", "results", "content", "records", "rows", "list"):
                val = nested.get(key)
                if isinstance(val, list):
                    items = val
                    break

    if not items:
        return ""

    # Get first item's ID
    first = items[0] if items else {}
    if not isinstance(first, dict):
        return ""

    # Try common ID field names
    ph_lower = placeholder_name.lower()
    entity_hint = _entity_hint(placeholder_name)

    # Exact match first
    for field in (placeholder_name, ph_lower, f"{entity_hint}_id", f"{entity_hint}Id"):
        val = first.get(field)
        if val is not None and str(val).strip():
            return str(val).strip()

    # Generic ID fields
    for field in ("id", "ID", "Id", "_id", "uuid", "key"):
        val = first.get(field)
        if val is not None and str(val).strip():
            return str(val).strip()

    return ""


def resolve_state_scoped_bindings(
    experiments: list[dict[str, Any]],
    actor_tokens: dict[str, str],
    base_url: str,
    *,
    max_resolution_attempts: int = 40,
    service_base_urls: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Per-experiment resolution of ``@state=``-scoped path placeholders.

    Batch pre-resolution (``auto_resolve_bindings``) binds one value per
    placeholder, which cannot serve state-machine experiments: each experiment
    requires an entity in its declared source state (a CANCELLED order for the
    cancel step, a PAID order for ship), and different experiments on the same
    placeholder need different states. Resolve per experiment by calling the
    binding's source-declared collection resolver and selecting the first
    entity whose state token matches the binding's required state.

    Returns a mapping obligation_id -> {placeholder: resolved_value}.
    """
    from .runtime_binding_materializer_base import (
        runtime_value_from_response as _state_aware_value,
    )

    out: dict[str, dict[str, str]] = {}
    tokens_to_try: list[str] = []
    for role_key in ("admin", "administrator", "superuser", "root"):
        if role_key in actor_tokens and actor_tokens[role_key]:
            tokens_to_try.append(actor_tokens[role_key])
            break
    for _role, _token_value in dict(actor_tokens).items():
        if _token_value and _token_value not in tokens_to_try:
            tokens_to_try.append(_token_value)
    attempts = 0

    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        oid = _text(exp.get("obligation_id"))
        if not oid:
            continue
        per_exp: dict[str, str] = {}
        for binding in _list(exp.get("binding_plan")):
            if not isinstance(binding, dict) or attempts >= max_resolution_attempts:
                continue
            target_path = _text(binding.get("target_path"))
            if not target_path.startswith("@state="):
                continue
            target = _text(binding.get("target"))
            if not target:
                continue
            # Collection list resolvers only: an entity GET with its own
            # placeholder cannot bootstrap the selection read.
            resolvers = [
                _text(row.get("path"))
                for row in _list(binding.get("resolver_operations"))
                if isinstance(row, dict)
                and _text(row.get("path"))
                and "{" not in _text(row.get("path"))
                and ":" not in _text(row.get("path"))
            ]
            if not resolvers:
                continue
            for resolver_path in resolvers:
                if attempts >= max_resolution_attempts:
                    break
                attempts += 1
                resolver_base_url = base_url
                if service_base_urls:
                    svc = _text(
                        binding.get("_resolver_service_name")
                    )
                    if svc and svc in service_base_urls:
                        resolver_base_url = service_base_urls[svc]
                for candidate_token in tokens_to_try:
                    response = _call_get_endpoint(resolver_base_url, resolver_path, candidate_token)
                    if response is None:
                        continue
                    value = _state_aware_value(response, target, target_path)
                    if value not in (None, "", [], {}):
                        per_exp[target] = str(value)
                        break
                if target in per_exp:
                    break
        if per_exp:
            out[oid] = per_exp
    return out


def auto_resolve_bindings(
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
    base_url: str,
    *,
    required_placeholders: set[str] | None = None,
    placeholder_collection_hints: dict[str, set[str]] | None = None,
    max_resolution_attempts: int = 20,
    service_base_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Automatically resolve path placeholders by calling GET endpoints.

    Args:
        behavior_ir: The Behavior IR v2 model.
        actor_tokens: Map of role/secret_ref -> bearer token.
        base_url: Target base URL.
        required_placeholders: If provided, only resolve these placeholders.
        placeholder_collection_hints: Map of placeholder -> owning collection
            segments derived from the write paths that need the binding. When a
            placeholder maps to multiple collections (orders vs cart items),
            batch resolution leaves it unbound (fail closed) so the
            path-scoped per-experiment materializer can bind correctly.
        max_resolution_attempts: Max GET calls to make.

    Returns:
        {
            "schema_version": ...,
            "bindings": {placeholder_name: resolved_value},
            "receipts": [{placeholder, endpoint, status, value_fingerprint}],
            "attempts": int,
            "resolved_count": int,
            "failed_count": int,
        }
    """
    if not base_url:
        return {
            "schema_version": _SCHEMA,
            "bindings": {},
            "receipts": [],
            "attempts": 0,
            "resolved_count": 0,
            "failed_count": 0,
        }

    # Collect all placeholders from Behavior IR operations (paths) plus body
    # placeholders declared by experiment binding plans. Body values (e.g. an
    # order's addressId) come from owner-scoped list reads, not from operation
    # path templates, so they must be resolvable even though no operation path
    # names them.
    operations = _list(behavior_ir.get("operations"))
    all_placeholders: set[str] = set()
    path_placeholders: set[str] = set()
    for op in operations:
        if not isinstance(op, dict):
            continue
        path = _text(op.get("path") or op.get("raw_path"))
        for ph in _extract_placeholders(path):
            all_placeholders.add(ph)
            path_placeholders.add(ph)
    for ph in _dict(placeholder_collection_hints):
        if _text(ph):
            all_placeholders.add(_text(ph))

    if required_placeholders:
        all_placeholders &= required_placeholders

    if not all_placeholders:
        return {
            "schema_version": _SCHEMA,
            "bindings": {},
            "receipts": [],
            "attempts": 0,
            "resolved_count": 0,
            "failed_count": 0,
        }

    # Pick the best available token (prefer admin, then any)
    token = ""
    for role_key in ("admin", "administrator", "superuser", "root"):
        if role_key in actor_tokens:
            token = actor_tokens[role_key]
            break
    if not token and actor_tokens:
        token = next(iter(actor_tokens.values()), "")
    tokens_to_try = [token] if token else []
    for _role, _token_value in dict(actor_tokens).items():
        if _token_value and _token_value not in tokens_to_try:
            tokens_to_try.append(_token_value)

    hints_by_placeholder = {
        _text(name): {
            _text(hint).lower()
            for hint in (hints or set())
            if _text(hint)
        }
        for name, hints in _dict(placeholder_collection_hints).items()
        if _text(name)
    }

    bindings: dict[str, str] = {}
    receipts: list[dict[str, Any]] = []
    attempts = 0

    for placeholder in sorted(all_placeholders):
        if attempts >= max_resolution_attempts:
            break
        if placeholder in bindings:
            continue

        hints = hints_by_placeholder.get(placeholder) or set()
        # Conflicting collections for the same placeholder name cannot share one
        # batch value — cart item ids must never become order ids.
        if len(hints) > 1:
            receipts.append({
                "placeholder": placeholder,
                "endpoint": "",
                "status": "ambiguous_collection_context",
                "collection_hints": sorted(hints),
                "value_fingerprint": "",
            })
            continue

        candidates = _find_list_endpoints_for_entity(
            behavior_ir,
            placeholder,
            collection_hints=hints or None,
        )
        if not candidates:
            receipts.append({
                "placeholder": placeholder,
                "endpoint": "",
                "status": "unresolved_no_collection_affinity",
                "collection_hints": sorted(hints),
                "value_fingerprint": "",
            })
            continue

        resolved = False
        for endpoint in candidates[:3]:  # Try up to 3 endpoints per placeholder
            if attempts >= max_resolution_attempts:
                break
            attempts += 1

            path = _text(endpoint.get("path") or endpoint.get("raw_path"))
            # Cross-service resolver routing: the resolver endpoint's owning
            # service (scm_trade for GET /scm/purchase-orders) may differ from
            # the scan target service. Route the read to the service's base_url
            # when the caller supplies the map; otherwise keep the target.
            endpoint_base_url = base_url
            if service_base_urls:
                svc = _text(
                    endpoint.get("_service_name") or endpoint.get("service")
                )
                if svc and svc in service_base_urls:
                    endpoint_base_url = service_base_urls[svc]
            value = ""
            used_token = ""
            # Owner-scoped list reads only succeed with the resource owner's
            # token (an admin token sees no addresses). Try every declared
            # actor token until one yields a value.
            for candidate_token in tokens_to_try:
                if attempts >= max_resolution_attempts:
                    break
                attempts += 1
                candidate_response = _call_get_endpoint(
                    endpoint_base_url,
                    path,
                    candidate_token,
                )
                candidate_value = (
                    _extract_id_from_response(candidate_response, placeholder)
                    if candidate_response is not None
                    else ""
                )
                if candidate_value:
                    value = candidate_value
                    used_token = candidate_token
                    break
            if not value:
                receipts.append({
                    "placeholder": placeholder,
                    "endpoint": path,
                    "status": "empty_response",
                    "value_fingerprint": "",
                })
                continue

            identity_operations = declared_identity_read_operations(
                operations,
                collection_path=path,
            )
            if not identity_operations:
                if placeholder in path_placeholders:
                    receipts.append({
                        "placeholder": placeholder,
                        "endpoint": path,
                        "status": "identity_observer_not_declared",
                        "collection_hints": sorted(hints),
                        "value_fingerprint": "",
                    })
                    continue
                # Body placeholder: the owner-scoped list read is the source
                # evidence (e.g. GET /api/users/addresses returns the actor's
                # own addresses); no separate entity-detail route is required.
                bindings[placeholder] = value
                receipts.append({
                    "placeholder": placeholder,
                    "endpoint": path,
                    "status": "resolved_body_from_owner_scoped_list",
                    "value_fingerprint": (
                        value[:8] + "..." if len(value) > 8 else value
                    ),
                })
                resolved = True
                continue

            for identity_operation in identity_operations[:3]:
                if attempts >= max_resolution_attempts:
                    break
                entity_path = materialize_declared_identity_read(
                    identity_operation,
                    value,
                )
                if not entity_path:
                    continue
                attempts += 1
                entity_method = _text(identity_operation.get("method")).upper()
                if entity_method == "GET":
                    entity_status = _call_get_status(
                        base_url,
                        entity_path,
                        used_token or token,
                    )
                else:
                    entity_status = _call_read_status(
                        base_url,
                        entity_path,
                        used_token or token,
                        method=entity_method,
                    )
                if not (200 <= entity_status < 300):
                    receipts.append({
                        "placeholder": placeholder,
                        "endpoint": path,
                        "entity_path": entity_path,
                        "entity_operation_ref": _text(identity_operation.get("id")),
                        "entity_status": entity_status,
                        "status": "identity_unobservable",
                        "value_fingerprint": "",
                    })
                    continue

                bindings[placeholder] = value
                receipts.append({
                    "placeholder": placeholder,
                    "endpoint": path,
                    "entity_path": entity_path,
                    "entity_operation_ref": _text(identity_operation.get("id")),
                    "entity_status": entity_status,
                    "status": "resolved",
                    "value_fingerprint": (
                        value[:8] + "..." if len(value) > 8 else value
                    ),
                })
                resolved = True
                break
            if resolved:
                break

        if not resolved and placeholder not in bindings:
            receipts.append({
                "placeholder": placeholder,
                "endpoint": "",
                "status": "unresolved",
                "value_fingerprint": "",
            })

    resolved_count = len(bindings)
    failed_count = len(all_placeholders) - resolved_count

    return {
        "schema_version": _SCHEMA,
        "bindings": bindings,
        "receipts": receipts,
        "attempts": attempts,
        "resolved_count": resolved_count,
        "failed_count": failed_count,
    }


def collect_required_placeholders(
    experiments: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> set[str]:
    """Collect all unresolved placeholders from a batch of experiments."""
    return set(collect_placeholder_collection_hints(experiments, behavior_ir))


def collect_placeholder_collection_hints(
    experiments: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> dict[str, set[str]]:
    """Map each required placeholder to exact owning collection paths.

    Used so batch pre-resolution cannot bind a cart-item ``id`` into an
    ``/api/orders/{id}/confirm`` write, including when different parents use
    the same terminal collection segment.
    """
    ops_by_id = {
        _text(op.get("id")): op
        for op in _list(behavior_ir.get("operations"))
        if isinstance(op, dict) and _text(op.get("id"))
    }
    hints: dict[str, set[str]] = {}

    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        for plan_key in ("control_plan", "treatment_plan", "setup_plan"):
            for step in _list(exp.get(plan_key)):
                if not isinstance(step, dict):
                    continue
                op_ref = _text(step.get("operation_ref"))
                op = ops_by_id.get(op_ref, {})
                path = _text(step.get("path") or op.get("path") or op.get("raw_path"))
                for ph in _extract_placeholders(path):
                    collection_path = collection_path_for_placeholder(path, ph)
                    bucket = hints.setdefault(ph, set())
                    if collection_path:
                        bucket.add(collection_path.lower().rstrip("/"))
        # Body placeholders are declared on the binding plan with their exact
        # source resolver operations (e.g. addressId -> GET /api/users/addresses).
        # The resolver path is the owning collection evidence for the batch
        # pre-resolution pass.
        for binding in _list(exp.get("binding_plan")):
            if not isinstance(binding, dict):
                continue
            target = _text(binding.get("target"))
            if not target:
                continue
            resolvers = [
                dict(row)
                for row in _list(binding.get("resolver_operations"))
                if isinstance(row, dict)
            ]
            if not resolvers:
                continue
            bucket = hints.setdefault(target, set())
            for resolver in resolvers:
                resolver_path = _text(resolver.get("path"))
                if resolver_path:
                    bucket.add(resolver_path.lower().rstrip("/"))

    return hints
