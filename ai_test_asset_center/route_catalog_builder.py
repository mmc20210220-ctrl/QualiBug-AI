"""
RouteCatalogBuilder — Unified route catalog from multiple API doc formats.

Supports: OpenAPI JSON/YAML, Swagger, Markdown API docs (table format),
          Postman collections, YApi, Apifox (extensible).

Design principles:
- All parsing happens in-memory from provided text — never fetches from target server.
- Output is a normalized list of RouteEntry objects consumed by Reasoner,
  TestDataGenerator, ValidationQueue, and stage_execute.
- Format auto-detection: tries structured parsers first, falls back to markdown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Normalized route entry ──────────────────────────────────────────


@dataclass
class RouteEntry:
    method: str
    path: str
    operation_id: str = ""
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    path_params: list[str] = field(default_factory=list)
    path_param_formats: dict[str, str] = field(default_factory=dict)  # param_name → schema format (uuid/int64/etc)
    query_params: list[dict[str, str]] = field(default_factory=list)
    request_body_schema: dict[str, Any] = field(default_factory=dict)
    response_schema: dict[str, Any] = field(default_factory=dict)
    auth_requirements: list[str] = field(default_factory=list)
    source_format: str = "unknown"  # openapi | markdown | postman | yapi | apifox

    @property
    def route_key(self) -> str:
        return f"{self.method.upper()} {self.path}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "operation_id": self.operation_id,
            "tags": self.tags,
            "summary": self.summary,
            "path_params": self.path_params,
            "path_param_formats": self.path_param_formats,
            "query_params": self.query_params,
            "request_body_schema": self.request_body_schema,
            "body_properties": self.request_body_schema.get("properties", {}) if isinstance(self.request_body_schema, dict) else {},
            "response_schema": self.response_schema,
            "auth_requirements": self.auth_requirements,
            "source_format": self.source_format,
        }


# ── Main builder ────────────────────────────────────────────────────


class RouteCatalogBuilder:
    """Build a unified route catalog from one or more API document texts."""

    def __init__(self):
        self.routes: list[RouteEntry] = []

    # ── Public API ──────────────────────────────────────────────────

    def build(self, *sources: str) -> list[RouteEntry]:
        """Parse all provided source texts and return unified route catalog."""
        self.routes = []
        for text in sources:
            if not text or not text.strip():
                continue
            text = text.strip()
            fmt = self._detect_format(text)
            parser = self._parser_for(fmt)
            try:
                entries = parser(text)
                for entry in entries:
                    entry.source_format = fmt
                self.routes.extend(entries)
            except Exception:
                # Try fallback parsers
                for fallback_fmt in ["openapi", "markdown"]:
                    if fallback_fmt == fmt:
                        continue
                    try:
                        fallback = self._parser_for(fallback_fmt)
                        entries = fallback(text)
                        for entry in entries:
                            entry.source_format = fallback_fmt
                        self.routes.extend(entries)
                        break
                    except Exception:
                        continue
        # Deduplicate by route_key, keeping first occurrence
        seen = set()
        unique = []
        for r in self.routes:
            if r.route_key not in seen:
                seen.add(r.route_key)
                unique.append(r)
        self.routes = unique
        return self.routes

    def to_route_map(self) -> dict[str, dict[str, Any]]:
        """Convert to the route_map format used by discovery_engine."""
        route_map = {}
        for entry in self.routes:
            route_map[entry.route_key] = {
                "path_pattern": entry.path,
                "method": entry.method,
                "path_params": entry.path_params,
                "path_param_formats": entry.path_param_formats,
                "has_body": bool(entry.request_body_schema),
                "body_properties": entry.request_body_schema.get("properties", {}),
                "tags": entry.tags,
                "auth_requirements": entry.auth_requirements,
            }
        return route_map

    def to_summary(self) -> dict[str, Any]:
        """Return a human-readable summary."""
        methods = {}
        tags = {}
        for r in self.routes:
            methods[r.method] = methods.get(r.method, 0) + 1
            for t in r.tags:
                tags[t] = tags.get(t, 0) + 1
        return {
            "total_routes": len(self.routes),
            "by_method": methods,
            "by_tag": tags,
            "sources": list({r.source_format for r in self.routes}),
        }

    # ── Format detection ────────────────────────────────────────────

    def _detect_format(self, text: str) -> str:
        """Auto-detect API document format from content."""
        t = text.strip()

        # OpenAPI / Swagger JSON
        if t.startswith("{") and ('"openapi"' in t[:500] or '"swagger"' in t[:500]):
            return "openapi"

        # OpenAPI YAML
        if ("openapi:" in t[:200] or "swagger:" in t[:200]) and "paths:" in t[:1000]:
            return "openapi"

        # Postman collection
        if t.startswith("{") and '"info"' in t[:500] and '"item"' in t[:1000] and '"request"' in t[:2000]:
            return "postman"

        # YApi / Apifox (JSON with specific structure)
        if t.startswith("{") and ('"list"' in t[:500] or '"apis"' in t[:500]):
            if '"method"' in t[:1000] and '"path"' in t[:1000]:
                return "yapi"

        # Markdown API doc — table format with Method | Path columns
        if "|" in t[:2000] and re.search(r"\|\s*(方法|Method|METHOD|HTTP)\s*\|", t[:2000], re.IGNORECASE):
            return "markdown"

        # Markdown API doc — section headers with HTTP methods
        if re.search(r"^#{1,4}\s*(GET|POST|PUT|DELETE|PATCH)\s", t[:2000], re.MULTILINE | re.IGNORECASE):
            return "markdown"

        # Fallback: try markdown table extraction
        if "|" in t[:2000]:
            return "markdown"

        return "unknown"

    def _parser_for(self, fmt: str):
        parsers = {
            "openapi": self._parse_openapi,
            "markdown": self._parse_markdown,
            "postman": self._parse_postman,
            "yapi": self._parse_yapi,
        }
        return parsers.get(fmt, self._parse_markdown)

    # ── Format parsers ──────────────────────────────────────────────

    def _parse_openapi(self, text: str) -> list[RouteEntry]:
        """Parse OpenAPI 3.x / Swagger 2.x JSON or YAML."""
        spec = self._load_json_or_yaml(text)
        if not spec or "paths" not in spec:
            return []

        entries = []
        paths = spec.get("paths", {})
        security = spec.get("security", [])
        components = spec.get("components", {}) or spec.get("securityDefinitions", {})

        for path_pattern, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, details in methods.items():
                if method.upper() not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    continue
                if not isinstance(details, dict):
                    continue

                # Path parameters
                path_params = []
                path_param_formats: dict[str, str] = {}
                for p in details.get("parameters", []):
                    if isinstance(p, dict) and p.get("in") == "path":
                        pname = p.get("name", "")
                        path_params.append(pname)
                        # 提取参数的 schema format（uuid/int64/string 等）
                        pschema = p.get("schema", {}) if isinstance(p.get("schema"), dict) else {}
                        pformat = str(pschema.get("format", "") or pschema.get("type", "") or "")
                        if pformat:
                            path_param_formats[pname] = pformat

                # Query parameters
                query_params = []
                for p in details.get("parameters", []):
                    if isinstance(p, dict) and p.get("in") == "query":
                        query_params.append({
                            "name": p.get("name", ""),
                            "required": p.get("required", False),
                            "schema_type": str(p.get("schema", {}).get("type", "string")),
                        })

                # Request body schema
                body_schema = {}
                rb = details.get("requestBody", {})
                if rb:
                    content = rb.get("content", {})
                    json_ct = content.get("application/json", {})
                    body_schema = json_ct.get("schema", {})

                # Response schema (first 2xx)
                resp_schema = {}
                for code, resp in details.get("responses", {}).items():
                    if str(code).startswith("2"):
                        ct = resp.get("content", {}).get("application/json", {})
                        resp_schema = ct.get("schema", {})
                        break

                # Auth requirements
                auth = []
                op_security = details.get("security", security)
                if op_security:
                    for sec in op_security:
                        auth.extend(sec.keys())

                entries.append(RouteEntry(
                    method=method.upper(),
                    path=path_pattern,
                    operation_id=details.get("operationId", ""),
                    tags=details.get("tags", []),
                    summary=details.get("summary", ""),
                    path_params=path_params,
                    path_param_formats=path_param_formats,
                    query_params=query_params,
                    request_body_schema=body_schema,
                    response_schema=resp_schema,
                    auth_requirements=auth,
                ))

        return entries

    def _parse_markdown(self, text: str) -> list[RouteEntry]:
        """Parse Markdown API documentation in table format.

        Expected table format:
        | 方法 | 路径 | 说明 |
        | GET | /api/orders | 查询订单列表 |
        | POST | /api/orders | 创建订单 |

        Also supports section headers:
        ## GET /api/orders
        """
        entries = []
        current_section_path = ""
        current_section_method = ""

        # Pattern 1: Table rows
        # Match: | METHOD | /path | description |
        table_pattern = re.compile(
            r'^\|\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*\|\s*(/[^\s|]+)\s*\|',
            re.IGNORECASE | re.MULTILINE,
        )
        for match in table_pattern.finditer(text):
            method = match.group(1).upper()
            path = match.group(2).strip()
            # Get the rest of the row for summary
            rest = text[match.end():].split("\n")[0]
            summary = rest.split("|")[0].strip() if "|" in rest else rest.strip()

            entries.append(RouteEntry(
                method=method,
                path=path,
                summary=summary,
                tags=self._extract_tags_from_path(path),
            ))

        # Pattern 2: Section headers like "## 基础接口" followed by table
        section_pattern = re.compile(
            r'^#{1,4}\s*(.+?接口|.+?API|Endpoints?)\s*$',
            re.IGNORECASE | re.MULTILINE,
        )
        for section in section_pattern.finditer(text):
            tag = section.group(1).strip()
            # Find the table after this section header
            section_text = text[section.end():]
            next_section = re.search(r'^#{1,4}\s', section_text, re.MULTILINE)
            if next_section:
                section_text = section_text[:next_section.start()]

            for match in table_pattern.finditer(section_text):
                method = match.group(1).upper()
                path = match.group(2).strip()
                rest = section_text[match.end():].split("\n")[0]
                summary = rest.split("|")[0].strip() if "|" in rest else rest.strip()

                # Find existing entry and add tag
                for e in entries:
                    if e.method == method and e.path == path:
                        if tag not in e.tags:
                            e.tags.append(tag)
                        break

        # Pattern 3: Inline HTTP method + path (non-table)
        inline_pattern = re.compile(
            r'(?:^|\n)\s*(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s\n]+)',
            re.IGNORECASE,
        )
        for match in inline_pattern.finditer(text):
            method = match.group(1).upper()
            path = match.group(2).strip()
            # Skip if already captured by table parser
            if not any(e.method == method and e.path == path for e in entries):
                # Get the line as summary
                line = text[match.start():].split("\n")[0]
                summary = line.strip()[:200]
                entries.append(RouteEntry(
                    method=method,
                    path=path,
                    summary=summary,
                    tags=self._extract_tags_from_path(path),
                ))

        # Pattern 4: Heading-style API endpoints (### POST /api/xxx)
        # Match lines like "### POST /api/auth/login" or "### GET /api/products/:sku"
        heading_api_pattern = re.compile(
            r'^#{1,6}\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[^\s\n`]+)',
            re.IGNORECASE | re.MULTILINE,
        )
        for match in heading_api_pattern.finditer(text):
            method = match.group(1).upper()
            path = match.group(2).strip().rstrip('`')
            # Skip if already captured
            if not any(e.method == method and e.path == path for e in entries):
                # Get the whole line as summary
                line = text[match.start():].split("\n")[0]
                summary = line.lstrip('#').strip()[:200]
                entries.append(RouteEntry(
                    method=method,
                    path=path,
                    summary=summary,
                    tags=self._extract_tags_from_path(path),
                ))

        # Enrich: extract path params from paths like /api/orders/{id}
        for entry in entries:
            params = re.findall(r'\{(\w+)\}', entry.path)
            if not params:
                params = re.findall(r':([A-Za-z_]\w*)', entry.path)
            if params:
                entry.path_params = params

        return entries

    def _parse_postman(self, text: str) -> list[RouteEntry]:
        """Parse Postman Collection v2.x JSON."""
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []

        entries = []
        items = data.get("item", [])

        def _walk(items, parent_tags=None):
            if parent_tags is None:
                parent_tags = []
            for item in items:
                if "item" in item:
                    tag = item.get("name", "")
                    _walk(item["item"], parent_tags + ([tag] if tag else []))
                elif "request" in item:
                    req = item["request"]
                    method = req.get("method", "GET").upper()
                    url_raw = req.get("url", {})
                    if isinstance(url_raw, dict):
                        path = "/" + "/".join(url_raw.get("path", []))
                        if not path.startswith("/"):
                            path = "/" + path
                    else:
                        path = str(url_raw)

                    entries.append(RouteEntry(
                        method=method,
                        path=path,
                        operation_id=item.get("name", ""),
                        tags=parent_tags,
                        summary=item.get("name", ""),
                    ))

        _walk(items)
        return entries

    def _parse_yapi(self, text: str) -> list[RouteEntry]:
        """Parse YApi / Apifox export format."""
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []

        entries = []
        apis = data.get("list", data.get("apis", []))
        if isinstance(apis, dict):
            apis = list(apis.values())

        for api in apis:
            if not isinstance(api, dict):
                continue
            method = api.get("method", "GET").upper()
            path = api.get("path", "")
            if not path:
                continue

            entries.append(RouteEntry(
                method=method,
                path=path,
                operation_id=api.get("title", api.get("name", "")),
                tags=[api.get("catname", api.get("tag", ""))] if api.get("catname") else [],
                summary=api.get("title", ""),
                path_params=re.findall(r'\{(\w+)\}', path),
                query_params=[{"name": q.get("name", ""), "required": q.get("required", False)}
                              for q in api.get("req_query", []) if isinstance(q, dict)],
                request_body_schema=api.get("req_body_other", api.get("requestBody", {})),
                response_schema=api.get("res_body", api.get("responseBody", {})),
            ))

        return entries

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _load_json_or_yaml(text: str) -> dict[str, Any] | None:
        """Load JSON or YAML, returning dict or None."""
        if text.strip().startswith("{"):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass
        # Try YAML
        try:
            import yaml
            return yaml.safe_load(text)
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_tags_from_path(path: str) -> list[str]:
        """Extract tags from path segments like /api/orders/{id} -> ['orders', 'api']."""
        segments = [s for s in path.strip("/").split("/") if s and "{" not in s]
        return segments


# ── Singleton convenience ───────────────────────────────────────────

_catalog_cache: dict[str, list[RouteEntry]] = {}


def build_route_catalog(*sources: str, cache_key: str = "") -> list[RouteEntry]:
    """Build route catalog with optional caching."""
    if cache_key and cache_key in _catalog_cache:
        return _catalog_cache[cache_key]

    builder = RouteCatalogBuilder()
    entries = builder.build(*sources)
    if cache_key:
        _catalog_cache[cache_key] = entries
    return entries
