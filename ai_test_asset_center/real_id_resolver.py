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


def extract_first_entity_id(body: Any, param_name: str) -> str | None:
    entities = _extract_entity_candidates(body)
    if entities:
        first = entities[0]
        if isinstance(first, dict):
            for field_name in ("id", "business_no", "order_id", param_name, "ID", "uuid"):
                value = first.get(field_name)
                if value not in {None, ""}:
                    return str(value)
    if isinstance(body, dict):
        for value in body.values():
            if isinstance(value, (int, float)) and value > 0:
                return str(int(value))
    return None


def resolve_real_id_from_documented_list(
    path_pattern: str,
    param_name: str,
    try_extract_id: Callable[[str, str], str | None],
) -> str:
    list_path = collection_path(path_pattern)
    if not list_path or list_path == path_pattern:
        return QUALIBUG_UNRESOLVED_ID

    result = try_extract_id(list_path, param_name)
    if result is not None:
        return result

    for page_suffix in _PAGINATION_SUFFIXES:
        result = try_extract_id(list_path + page_suffix, param_name)
        if result is not None:
            return result

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
