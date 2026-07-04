from __future__ import annotations

"""Lightweight OpenAPI parsing helpers — no silent demo fallbacks."""

import json
import re
import sys
from pathlib import Path
from typing import Any

from .phase104_api_contract_exporter import route_contracts

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
PATH_LINE_RE = re.compile(r"^\s*(/[^:\s]+)\s*:\s*$")
METHOD_LINE_RE = re.compile(r"^\s{2,}(get|post|put|patch|delete|head|options)\s*:\s*$", re.I)
ROUTE_REF_RE = re.compile(
    r"(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?"
    r"(\/[a-zA-Z][\w\-\/{}_.]*(?:\/[a-zA-Z][\w\-\/{}_.]*)*)",
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


def _extract_paths_from_yaml_like_text(text: str) -> dict[str, dict[str, Any]] | None:
    """Try to parse YAML with PyYAML, falling back to regex for bare-keys YAML."""
    try:
        import yaml
        payload = yaml.safe_load(text)
        if isinstance(payload, dict) and payload.get("paths"):
            return _normalize_openapi_dict(payload)["paths"]
    except Exception:
        pass
    # Fallback: regex-based extraction for unstructured path/method listing
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
    return paths if paths else None


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
    """Parse dict/json/path/yaml-like-text OpenAPI input into a normalized dict.

    Returns a dict with at least ``"openapi"`` and ``"paths"`` keys.
    When parsing fails and no paths can be extracted, returns empty paths
    (never silently injects demo routes).
    """
    if isinstance(spec_input, dict):
        return _normalize_openapi_dict(spec_input)

    if isinstance(spec_input, Path):
        # Try universal parser first (handles .har, .proto, .graphql, Postman, etc.)
        try:
            from .universal_api_parser import parse_to_openapi
            result = parse_to_openapi(spec_input)
            if result.get("paths"):
                return result
        except Exception:
            pass
        return parse_openapi_spec(spec_input.read_text(encoding="utf-8", errors="ignore"))

    text = str(spec_input or "").strip()
    if not text:
        print(f"  [WARN] openapi_spec_utils: empty input, returning empty paths", flush=True, file=sys.stderr)
        return {"openapi": "3.0.0", "paths": {}}

    maybe_path = Path(text)
    if "\n" not in text and maybe_path.exists() and maybe_path.is_file():
        return parse_openapi_spec(maybe_path)

    # ── Strategy 1: JSON ──
    try:
        if text.startswith("{"):
            payload = json.loads(text)
            if isinstance(payload, dict):
                normalized = _normalize_openapi_dict(payload)
                if normalized.get("paths"):
                    return normalized
    except Exception as e:
        print(f"  [WARN] openapi_spec_utils: JSON parse failed ({e})", flush=True, file=sys.stderr)

    # ── Strategy 2: YAML (PyYAML) ──
    try:
        import yaml
        payload = yaml.safe_load(text)
        if isinstance(payload, dict):
            normalized = _normalize_openapi_dict(payload)
            if normalized.get("paths"):
                return normalized
    except Exception as e:
        print(f"  [WARN] openapi_spec_utils: YAML parse failed, trying regex fallback ({e})", flush=True, file=sys.stderr)

    # ── Strategy 3: regex fallback for bare YAML-like path lists ──
    yaml_like_paths = _extract_paths_from_yaml_like_text(text)
    if yaml_like_paths:
        print(f"  [WARN] openapi_spec_utils: parsed YAML-like paths via regex; operation-level info may be incomplete", flush=True, file=sys.stderr)
        return {"openapi": "3.0.0", "paths": yaml_like_paths}

    # ── Strategy 4: route reference regex ──
    route_ref_paths = _extract_paths_from_route_refs(text)
    if route_ref_paths:
        return {"openapi": "3.0.0", "paths": route_ref_paths}

    print(f"  [ERROR] openapi_spec_utils: all parsing strategies failed; returning empty paths", flush=True, file=sys.stderr)
    return {"openapi": "3.0.0", "paths": {}}

