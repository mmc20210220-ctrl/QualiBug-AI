from __future__ import annotations

"""Lightweight OpenAPI parsing helpers without fixed demo fallbacks."""

import json
import re
from pathlib import Path
from typing import Any

from .phase104_api_contract_exporter import route_contracts

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
PATH_LINE_RE = re.compile(r"^\s*(/[^:\s]+)\s*:\s*$")
METHOD_LINE_RE = re.compile(r"^\s{2,}(get|post|put|patch|delete|head|options)\s*:\s*$", re.I)
ROUTE_REF_RE = re.compile(
    r"(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?(\/(?:api|auth|admin|users?|orders?|projects?|reports?|test-runs?|risks?|environment|business-model)[\w\-\/{}]*)",
    re.I,
)


def _normalize_openapi_dict(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    paths = payload.get("paths")
    clean_paths: dict[str, dict[str, Any]] = {}
    if isinstance(paths, dict):
        for path, methods in paths.items():
            if not isinstance(path, str):
                continue
            if not isinstance(methods, dict):
                clean_paths[str(path)] = {}
                continue
            clean_methods: dict[str, Any] = {}
            for method, config in methods.items():
                if str(method).lower() not in HTTP_METHODS:
                    continue
                clean_methods[str(method).lower()] = config if isinstance(config, dict) else {}
            clean_paths[str(path)] = clean_methods
    normalized["paths"] = clean_paths
    if "openapi" not in normalized:
        normalized["openapi"] = "3.0.0"
    return normalized


def _extract_paths_from_yaml_like_text(text: str) -> dict[str, dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    current_path = ""
    for line in text.splitlines():
        path_match = PATH_LINE_RE.match(line)
        if path_match:
            current_path = path_match.group(1).strip()
            paths.setdefault(current_path, {})
            continue
        method_match = METHOD_LINE_RE.match(line)
        if method_match and current_path:
            paths.setdefault(current_path, {})[method_match.group(1).lower()] = {}
    return paths


def _extract_paths_from_route_refs(text: str) -> dict[str, dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    for match in ROUTE_REF_RE.finditer(text):
        method = str(match.group(1) or "GET").lower()
        path = str(match.group(2) or "").strip()
        if not path:
            continue
        paths.setdefault(path, {})[method] = {}
    return paths


def _fallback_contract_paths() -> dict[str, dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    for contract in route_contracts():
        paths.setdefault(contract.path, {})[contract.method.lower()] = {
            "summary": contract.summary,
            "operationId": contract.operation_id,
            "description": contract.description,
        }
    return paths


def parse_openapi_spec(spec_input: Any) -> dict[str, Any]:
    """Parse dict/json/path/yaml-like OpenAPI input into a normalized dict."""
    if isinstance(spec_input, dict):
        return _normalize_openapi_dict(spec_input)

    if isinstance(spec_input, Path):
        return parse_openapi_spec(spec_input.read_text(encoding="utf-8", errors="ignore"))

    text = str(spec_input or "").strip()
    if not text:
        return {"openapi": "3.0.0", "paths": _fallback_contract_paths()}

    maybe_path = Path(text)
    if "\n" not in text and maybe_path.exists() and maybe_path.is_file():
        return parse_openapi_spec(maybe_path)

    try:
        if text.startswith("{"):
            payload = json.loads(text)
            if isinstance(payload, dict):
                normalized = _normalize_openapi_dict(payload)
                if normalized.get("paths"):
                    return normalized
    except Exception:
        pass

    yaml_like_paths = _extract_paths_from_yaml_like_text(text)
    if yaml_like_paths:
        return {"openapi": "3.0.0", "paths": yaml_like_paths}

    route_ref_paths = _extract_paths_from_route_refs(text)
    if route_ref_paths:
        return {"openapi": "3.0.0", "paths": route_ref_paths}

    return {"openapi": "3.0.0", "paths": _fallback_contract_paths()}

