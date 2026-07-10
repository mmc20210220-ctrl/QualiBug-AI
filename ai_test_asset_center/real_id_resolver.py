from __future__ import annotations

import re
from typing import Any, Callable, Iterable

QUALIBUG_UNRESOLVED_ID = "QUALIBUG_UNRESOLVED_ID"

_LIST_FIELDS = ("records", "data", "items", "results", "list", "rows")
_PAGINATION_SUFFIXES = (
    "?page=1&size=1",
    "?pageNum=1&pageSize=1",
    "?offset=0&limit=1",
    "?current=1&pageSize=1",
)
_NORMALIZED_PARAM_RE = re.compile(r"\{([A-Za-z_]\w*)\}")
_PLACEHOLDER_PATTERNS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (
        re.compile(r"\$\{([A-Za-z_]\w*)\}"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
    (
        re.compile(r"<([A-Za-z_]\w*)>"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
    (
        re.compile(r"\{([A-Za-z_]\w*)(?::[^{}]+)?\}"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
    (
        re.compile(r"(?<=/):([A-Za-z_]\w*)\b"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
)

# Generic field aliases for path params — no industry hardcoding of values,
# only common REST naming conventions across systems.
_PARAM_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "uuid", "ID", "pk"),
    "sku": ("sku", "product_sku", "productSku", "code", "product_code", "id"),
    "orderid": ("orderId", "order_id", "order_no", "orderNo", "id"),
    "order_id": ("order_id", "orderId", "order_no", "orderNo", "id"),
    "userid": ("userId", "user_id", "uid", "id"),
    "user_id": ("user_id", "userId", "uid", "id"),
    "addressid": ("addressId", "address_id", "id"),
    "address_id": ("address_id", "addressId", "id"),
    "paymentid": ("paymentId", "payment_id", "id"),
    "refundid": ("refundId", "refund_id", "id"),
    "couponid": ("couponId", "coupon_id", "code", "id"),
    "code": ("code", "coupon_code", "couponCode", "sku", "id"),
}


def normalize_path_placeholders(path: str) -> str:
    normalized = str(path or "")
    for pattern, replacer in _PLACEHOLDER_PATTERNS:
        normalized = pattern.sub(replacer, normalized)
    return normalized


def infer_path_params(path: str, declared: Iterable[str] | None = None) -> list[str]:
    values = [str(item) for item in (declared or []) if str(item)]
    if values:
        return values
    inferred = _NORMALIZED_PARAM_RE.findall(normalize_path_placeholders(path))
    return [str(item) for item in inferred if str(item)]


def path_has_placeholders(path: str) -> bool:
    return bool(_NORMALIZED_PARAM_RE.search(normalize_path_placeholders(path)))


def collection_path(path: str) -> str:
    normalized = normalize_path_placeholders(path).split("?", 1)[0]
    if not normalized.startswith("/"):
        return ""
    placeholder_match = re.search(r"/\{[A-Za-z_]\w*\}", normalized)
    if placeholder_match:
        return normalized[:placeholder_match.start()] or ""
    return normalized


def param_field_candidates(param_name: str) -> list[str]:
    """Return response-field names that can satisfy a path parameter."""
    name = str(param_name or "").strip()
    if not name:
        return ["id"]
    key = re.sub(r"[^a-z0-9_]+", "", name.lower())
    aliases = list(_PARAM_FIELD_ALIASES.get(key, ()))
    # Always include the literal param name and common id fallbacks.
    ordered = [name, *aliases, "id", "uuid", "code", "sku", "business_no", "order_id"]
    seen: set[str] = set()
    result: list[str] = []
    for item in ordered:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def alternate_collection_paths(path: str) -> list[str]:
    """Fallback list endpoints when the structural collection path is not listable.

    Example: ``/api/inventory/{sku}`` has no ``GET /api/inventory``; SKUs are
    typically discoverable from a sibling product catalog endpoint.
    """
    normalized = normalize_path_placeholders(path).split("?", 1)[0]
    params = infer_path_params(normalized)
    if not params:
        return []
    primary = collection_path(normalized)
    parts = [p for p in primary.strip("/").split("/") if p]
    if len(parts) < 2:
        return []
    prefix = "/" + parts[0]  # usually "api"
    resource = parts[-1].lower()
    param_keys = {re.sub(r"[^a-z0-9]+", "", p.lower()) for p in params}
    alternates: list[str] = []
    if "sku" in param_keys or resource in {"inventory", "stock", "warehouse"}:
        for sibling in ("products", "product", "skus", "items", "goods"):
            candidate = f"{prefix}/{sibling}"
            if candidate != primary:
                alternates.append(candidate)
    if any(k in param_keys for k in ("orderid", "order_id")) or resource in {"payments", "payment", "refunds", "refund"}:
        for sibling in ("orders", "order"):
            candidate = f"{prefix}/{sibling}"
            if candidate != primary:
                alternates.append(candidate)
    if any(k in param_keys for k in ("userid", "user_id")):
        for sibling in ("users", "accounts", "customers"):
            candidate = f"{prefix}/{sibling}"
            if candidate != primary:
                alternates.append(candidate)
    return list(dict.fromkeys(alternates))


def extract_fields_for_path(path: str) -> list[str]:
    """Fields a resolve step should extract to satisfy path placeholders."""
    fields: list[str] = []
    for param in infer_path_params(path):
        fields.extend(param_field_candidates(param))
    if not fields:
        fields = ["id", "sku", "code", "uuid"]
    return list(dict.fromkeys(fields))


def extract_first_entity_id(body: Any, param_name: str) -> str | None:
    entities = _extract_entity_candidates(body)
    if entities:
        first = entities[0]
        if isinstance(first, dict):
            for field_name in param_field_candidates(param_name):
                value = first.get(field_name)
                if value not in {None, ""}:
                    return str(value)
    if isinstance(body, dict):
        for field_name in param_field_candidates(param_name):
            value = body.get(field_name)
            if value not in {None, ""}:
                return str(value)
        for value in body.values():
            if isinstance(value, (int, float)) and value > 0:
                return str(int(value))
    return None


def bind_entity_fields(body: Any, path: str = "") -> dict[str, str]:
    """Extract a binding map from a list/detail response for path placeholders."""
    entities = _extract_entity_candidates(body)
    source: dict[str, Any] = {}
    if entities and isinstance(entities[0], dict):
        source = entities[0]
    elif isinstance(body, dict):
        source = body
    if not source:
        return {}
    bindings: dict[str, str] = {}
    params = infer_path_params(path) or ["id"]
    for param in params:
        for field_name in param_field_candidates(param):
            value = source.get(field_name)
            if value not in {None, ""}:
                text = str(value)
                bindings[param] = text
                bindings[field_name] = text
                # Keep a generic id alias when the param is the primary key style.
                if param.lower() in {"id", "sku", "code"} or field_name.lower() in {"id", "sku"}:
                    bindings.setdefault("id", text)
                break
    # Also capture common identity fields even when not in the path.
    for field_name in ("id", "sku", "code", "order_id", "orderId", "user_id", "userId"):
        value = source.get(field_name)
        if value not in {None, ""}:
            bindings.setdefault(field_name, str(value))
    return bindings


def resolve_real_id_from_documented_list(
    path_pattern: str,
    param_name: str,
    try_extract_id: Callable[[str, str], str | None],
) -> str:
    list_path = collection_path(path_pattern)
    candidates = [list_path] if list_path and list_path != path_pattern else []
    candidates.extend(alternate_collection_paths(path_pattern))
    candidates = [item for item in dict.fromkeys(candidates) if item]

    for candidate in candidates:
        result = try_extract_id(candidate, param_name)
        if result is not None:
            return result
        for page_suffix in _PAGINATION_SUFFIXES:
            result = try_extract_id(candidate + page_suffix, param_name)
            if result is not None:
                return result

    if list_path:
        parent_path = re.sub(r"/[^/]+$", "", list_path)
        if parent_path and parent_path != list_path:
            parent_id = try_extract_id(parent_path, "id")
            if parent_id is not None:
                result = try_extract_id(list_path, param_name)
                if result is not None:
                    return result

    return QUALIBUG_UNRESOLVED_ID


def _extract_entity_candidates(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    for field_name in _LIST_FIELDS:
        value = body.get(field_name)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_entity_candidates(value)
            if nested:
                return nested
    for value in body.values():
        if isinstance(value, dict):
            nested = _extract_entity_candidates(value)
            if nested:
                return nested
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            return [item for item in value if isinstance(item, dict)]
    return []
