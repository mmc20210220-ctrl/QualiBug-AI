from __future__ import annotations

"""Universal API Doc Parser — auto-detect and normalize all major API formats.

Supported formats (auto-detected):
- OpenAPI 3.x (JSON/YAML)
- Swagger 2.0 (JSON/YAML)
- Postman Collection v2.x
- GraphQL Schema (SDL)
- gRPC / Protocol Buffers (.proto)
- HAR (HTTP Archive) — delegates to har_importer

All formats are normalized to an OpenAPI 3.x-compatible internal dict with
``paths``, ``components.schemas``, and ``info`` keys, so downstream code
(_schema_value, _schema_for_endpoint) works unchanged.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


# ── Format detection ──────────────────────────────────────────────────────

def detect_format(text: str, filename: str = "") -> str:
    """Auto-detect the API doc format.

    Returns one of: openapi3, swagger2, postman, graphql, grpc, har, unknown
    """
    text_stripped = text.strip()
    if not text_stripped:
        return "unknown"

    # HAR: standard HTTP Archive format
    if _try_json(text_stripped) and '"log"' in text_stripped[:200] and '"entries"' in text_stripped:
        return "har"

    # Postman: has "info" with "schema" containing "postman"
    data = _try_json(text_stripped)
    if isinstance(data, dict):
        info = data.get("info", {})
        if isinstance(info, dict):
            schema = str(info.get("schema", "")).lower()
            if "postman" in schema:
                return "postman"
            if data.get("item") and isinstance(data.get("item"), list):
                return "postman"

        # OpenAPI 3.x: has "openapi" key
        if "openapi" in data and isinstance(data.get("paths"), dict):
            return "openapi3"

        # Swagger 2.0: has "swagger" key
        if "swagger" in data and str(data.get("swagger", "")).startswith("2."):
            return "swagger2"

    # GraphQL SDL: starts with type/input/enum/schema/interface/union/scalar/directive
    if re.match(r'\s*(type|input|enum|schema|interface|union|scalar|directive|extend|fragment|query|mutation|subscription)\s+\w+', text_stripped):
        return "graphql"

    # gRPC .proto: contains "syntax = "proto3"" or "service ... {"
    if 'syntax = "proto3"' in text_stripped or 'syntax = "proto2"' in text_stripped:
        return "grpc"
    if re.search(r'\bservice\s+\w+\s*\{', text_stripped):
        return "grpc"

    # Fallback: try YAML parse for OpenAPI/Swagger
    try:
        import yaml
        yaml_data = yaml.safe_load(text_stripped)
        if isinstance(yaml_data, dict):
            if "openapi" in yaml_data:
                return "openapi3"
            if "swagger" in yaml_data:
                return "swagger2"
    except Exception:
        pass

    # Markdown API doc: heading-style endpoints like "### POST /api/xxx"
    if re.search(r'^#{1,6}\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/', text_stripped, re.MULTILINE | re.IGNORECASE):
        return "markdown_api"

    # Markdown API doc: table-style like "| GET | /api/xxx | description |"
    if re.search(r'\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|', text_stripped[:3000], re.IGNORECASE):
        return "markdown_api"

    return "unknown"


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Format normalizers → OpenAPI 3.x compatible ──────────────────────────

def parse_to_openapi(text_or_path: str | Path) -> dict[str, Any]:
    """Main entry point: parse any API doc to OpenAPI 3.x-compatible dict.

    Args:
        text_or_path: File path to an API doc, or raw text content

    Returns:
        dict with keys: openapi, info, paths, components (schemas)
    """
    text = ""
    filename = ""
    if isinstance(text_or_path, Path) or (isinstance(text_or_path, str) and "\n" not in text_or_path and Path(text_or_path).exists()):
        p = Path(text_or_path)
        filename = p.name
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        text = str(text_or_path)
        filename = ""

    fmt = detect_format(text, filename)
    try:
        print(f"  [INFO] universal_api_parser: detected format={fmt}", flush=True)
    except OSError:
        pass

    if fmt == "openapi3":
        return _normalize_openapi3(text)
    elif fmt == "swagger2":
        return _convert_swagger2(text)
    elif fmt == "postman":
        return _convert_postman(text)
    elif fmt == "graphql":
        return _convert_graphql(text)
    elif fmt == "grpc":
        return _convert_grpc(text)
    elif fmt == "har":
        return _convert_har(text_or_path if (isinstance(text_or_path, Path) or (isinstance(text_or_path, str) and Path(text_or_path).exists())) else text)
    elif fmt == "markdown_api":
        return _convert_markdown_api(text)
    else:
        try:
            print(f"  [WARN] universal_api_parser: unknown format, returning empty spec", flush=True, file=sys.stderr)
        except OSError:
            pass
        return _empty_spec()


def _resolve_schema_refs(value: Any, spec: dict[str, Any], _depth: int = 0) -> Any:
    """Inline ``$ref`` schemas against the document's own ``components``.

    OpenAPI ``requestBody`` schemas are frequently bare ``$ref`` pointers into
    ``components.schemas``. Downstream request-body construction resolves
    properties only from inline schema objects, so a bare ref made
    ``POST /sales-orders`` send ``{}`` and the target's 422 ``customer_id
    required`` was misread as a defect. Resolution is document-local and
    follows the JSON pointer exactly; unresolvable or cyclic refs stay as-is
    (fail-open, never synthesized).
    """
    if not isinstance(value, dict) or _depth > 12:
        return value
    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref[len("#/components/schemas/"):]
        schema = _dict_value(_dict_value(spec.get("components")).get("schemas")).get(name)
        if isinstance(schema, dict):
            resolved = {
                key: _resolve_schema_refs(item, spec, _depth + 1)
                for key, item in schema.items()
                if key != "$ref"
            }
            resolved.setdefault("title", name)
            return resolved
        return value
    return {
        key: _resolve_schema_refs(item, spec, _depth + 1)
        for key, item in value.items()
    }


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_api_operations_from_text(
    api_spec_text: str,
    *,
    submitted_source_text: str = "",
) -> list[dict[str, Any]]:
    """Normalize source API material into the planner's operation records."""

    text = str(api_spec_text or "").strip()
    if not text:
        raise ValueError("api_spec_text_missing")
    operations: list[dict[str, Any]] = []
    source_documents = [("api_spec", text)]
    submitted = str(submitted_source_text or "").strip()
    if submitted and submitted != text:
        source_documents.append(("submitted_api_spec", submitted))

    for source_id, source_text in source_documents:
        spec = parse_to_openapi(source_text)
        if not isinstance(spec, dict):
            raise ValueError(f"api_spec_parse_result_invalid:{source_id}")
        paths = spec.get("paths")
        if not isinstance(paths, dict):
            raise ValueError(f"api_spec_paths_missing:{source_id}")
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, raw_operation in methods.items():
                normalized_method = str(method or "").strip().upper()
                if normalized_method not in {
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                }:
                    continue
                operation = raw_operation if isinstance(raw_operation, dict) else {}
                # The operation's access contract: source-declared required
                # roles (x-required-roles, the OpenAPI vendor extension the
                # contract authors use for 权限：管理员 declarations). Roles
                # stay exactly as declared — the parser never translates
                # them; the role vocabulary is the source's own.
                required_roles = [
                    str(role).strip()
                    for role in (operation.get("x-required-roles") or [])
                    if str(role or "").strip()
                ]
                operations.append({
                    "method": normalized_method,
                    "path": str(path or "").strip(),
                    "operation_id": str(
                        operation.get("operationId") or ""
                    ).strip() or (
                        f"{normalized_method.lower()}:{str(path or '').strip()}"
                    ),
                    "source_id": source_id,
                    "summary": str(operation.get("summary") or "").strip(),
                    "description": str(operation.get("description") or "").strip(),
                    "tags": list(operation.get("tags") or []),
                    "parameters": list(operation.get("parameters") or []),
                    "required_roles": required_roles,
                    "request_schema": _resolve_schema_refs(
                        operation.get("requestBody")
                        if isinstance(operation.get("requestBody"), dict)
                        else {},
                        spec,
                    ),
                    "response_schema": (
                        operation.get("responses")
                        if isinstance(operation.get("responses"), dict)
                        else {}
                    ),
                })
    if not operations:
        for match in re.finditer(
            r"(?im)^(?:\s*#{1,6}\s*)?(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
            submitted or text,
        ):
            method = match.group(1).upper()
            path = match.group(2).strip().rstrip("`").rstrip(",").rstrip(")")
            operations.append({
                "method": method,
                "path": path,
                "operation_id": f"{method.lower()}:{path}",
                "source_id": "api_spec",
                "summary": "",
                "description": "",
                "tags": [],
                "parameters": [],
                "request_schema": {},
                "response_schema": {},
            })
    if not operations:
        raise ValueError("api_spec_operations_missing")
    # Markdown field tables (字段/类型/必填) are source contracts too. The
    # heading→OpenAPI converter only lifts fenced JSON examples; enrich empty
    # request schemas from the knowledge-center markdown operation parser so
    # required body fields reach Behavior IR without inventing example values.
    _enrich_operations_from_markdown_field_tables(operations, text, submitted)
    return operations


def _normalize_api_path(path: str) -> str:
    text = str(path or "").strip()
    text = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", text)
    return text.rstrip("/") or "/"


def _enrich_operations_from_markdown_field_tables(
    operations: list[dict[str, Any]],
    *source_texts: str,
) -> None:
    try:
        from .enterprise_knowledge_center._parsing import _markdown_api_operations
    except Exception:
        return
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for source_text in source_texts:
        if not str(source_text or "").strip():
            continue
        for row in _markdown_api_operations(str(source_text), "api_spec"):
            if not isinstance(row, dict):
                continue
            method = str(row.get("method") or "").upper()
            path = _normalize_api_path(str(row.get("path") or ""))
            if method and path:
                by_key[(method, path)] = row
    if not by_key:
        return
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        method = str(operation.get("method") or "").upper()
        path = _normalize_api_path(str(operation.get("path") or ""))
        md = by_key.get((method, path))
        if not md:
            continue
        existing = operation.get("request_schema")
        if isinstance(existing, dict) and existing:
            continue
        schema = md.get("request_schema")
        if isinstance(schema, dict) and schema:
            operation["request_schema"] = dict(schema)
        example = md.get("request_example")
        if isinstance(example, dict) and example and not operation.get("request_example"):
            operation["request_example"] = dict(example)


def _convert_markdown_api(text: str) -> dict[str, Any]:
    """Convert Markdown API doc (heading or table format) to OpenAPI 3.x compatible dict."""
    import re as _re
    paths: dict[str, dict] = {}
    tags_set: set[str] = set()
    
    # Pattern A: Table-style "| GET | /api/xxx | description |"
    table_re = _re.compile(r'^\|\s*(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*\|\s*(?P<path>/[^\s|]+)', _re.IGNORECASE | _re.MULTILINE)
    table_matches = list(table_re.finditer(text))
    
    if table_matches:
        for match in table_matches:
            method = match.group("method").lower()
            path = match.group("path").strip()
            segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{") and not s.startswith(":")]
            tag = segments[0] if segments else "api"
            tags_set.add(tag)
            if path not in paths:
                paths[path] = {}
            paths[path][method] = {
                "operationId": f"{method}_{path.strip('/').replace('/','_').replace('{','').replace('}','') or 'root'}",
                "summary": f"{method.upper()} {path}",
                "tags": [tag],
                "responses": {"200": {"description": "OK"}},
            }
        if paths:
            return {
                "openapi": "3.0.0", "info": {"title": "Markdown API", "version": "1.0.0"},
                "paths": paths, "components": {"schemas": {}},
                "tags": [{"name": t} for t in sorted(tags_set)],
            }
    
    # Pattern B: Heading-style "### METHOD /path"
    endpoint_re = _re.compile(
        r'^#{1,6}\s*(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(?P<path>/[^\s\n`]+)',
        _re.IGNORECASE | _re.MULTILINE,
    )
    paths: dict[str, dict] = {}
    tags_set: set[str] = set()
    
    for match in endpoint_re.finditer(text):
        method = match.group("method").lower()
        path = match.group("path").strip().rstrip("`")
        # Get section text until next heading or end
        section_start = match.end()
        next_heading = _re.search(r'^#{1,6}\s', text[section_start:], _re.MULTILINE)
        section_end = section_start + next_heading.start() if next_heading else len(text)
        section = text[section_start:section_end]
        
        # Extract JSON request body example if present
        request_body = None
        json_match = _re.search(r'```json\s*\n(.*?)```', section, _re.DOTALL)
        if json_match:
            try:
                import json as _json
                body_schema = {"properties": {}}
                example = _json.loads(json_match.group(1))
                if isinstance(example, dict):
                    for k, v in example.items():
                        property_schema = _infer_schema_from_value(v)
                        property_schema["example"] = v
                        body_schema["properties"][k] = property_schema
                    request_body = {"content": {"application/json": {"schema": body_schema, "example": example}}}
            except Exception:
                pass
        
        # Build tag from first path segment
        segments = [s for s in path.strip("/").split("/") if s and not s.startswith(":")]
        tag = segments[0] if segments else "api"
        tags_set.add(tag)
        
        op_spec: dict = {
            "operationId": f"{method}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '') or 'root'}",
            "summary": section.split("\n")[0].strip("# ").strip()[:200] if section.strip() else f"{method.upper()} {path}",
            "tags": [tag],
            "responses": {"200": {"description": "OK"}},
        }
        if request_body:
            op_spec["requestBody"] = request_body
        
        if path not in paths:
            paths[path] = {}
        paths[path][method] = op_spec
    
    return {
        "openapi": "3.0.0",
        "info": {"title": "Markdown API", "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": {}},
        "tags": [{"name": t} for t in sorted(tags_set)],
    }


def _empty_spec() -> dict[str, Any]:
    return {"openapi": "3.0.0", "info": {"title": "unknown"}, "paths": {}, "components": {"schemas": {}}}


# ── OpenAPI 3.x normalizer ───────────────────────────────────────────────

def _normalize_openapi3(text: str) -> dict[str, Any]:
    data = _try_json(text)
    if data is None:
        try:
            import yaml
            data = yaml.safe_load(text)
        except Exception:
            return _empty_spec()
    if not isinstance(data, dict):
        return _empty_spec()
    # Ensure components.schemas exists
    data.setdefault("components", {}).setdefault("schemas", {})
    data.setdefault("paths", {})
    return data


# ── Swagger 2.0 → OpenAPI 3.x converter ──────────────────────────────────

def _convert_swagger2(text: str) -> dict[str, Any]:
    data = _try_json(text)
    if data is None:
        try:
            import yaml
            data = yaml.safe_load(text)
        except Exception:
            return _empty_spec()
    if not isinstance(data, dict):
        return _empty_spec()

    result: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": data.get("info", {}),
        "paths": {},
        "components": {"schemas": {}},
    }

    # ── definitions → components.schemas ──
    definitions = data.get("definitions", {})
    if isinstance(definitions, dict):
        result["components"]["schemas"] = dict(definitions)

    # ── paths (Swagger 2.0 uses basePath) ──
    base_path = data.get("basePath", "").rstrip("/")
    swagger_paths = data.get("paths", {})
    if isinstance(swagger_paths, dict):
        for path, methods in swagger_paths.items():
            full_path = base_path + path if base_path and not path.startswith(base_path) else path
            if not isinstance(methods, dict):
                continue
            normalized_methods: dict[str, Any] = {}
            for method, op in methods.items():
                if method.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                    continue
                if not isinstance(op, dict):
                    continue
                # Convert parameters (Swagger 2.0 puts body in parameters)
                normalized_methods[method.lower()] = _convert_swagger2_operation(op, result)
            result["paths"][full_path] = normalized_methods

    # ── securityDefinitions → components.securitySchemes ──
    sec_defs = data.get("securityDefinitions", {})
    if isinstance(sec_defs, dict):
        result["components"]["securitySchemes"] = {}
        for name, sec in sec_defs.items():
            if isinstance(sec, dict):
                result["components"]["securitySchemes"][name] = {
                    "type": sec.get("type", "apiKey"),
                    "name": sec.get("name", ""),
                    "in": sec.get("in", "header"),
                    "description": sec.get("description", ""),
                }

    return result


def _convert_swagger2_operation(op: dict, spec_root: dict) -> dict[str, Any]:
    """Convert a single Swagger 2.0 operation to OpenAPI 3.x."""
    result: dict[str, Any] = {
        "summary": op.get("summary", ""),
        "description": op.get("description", ""),
        "operationId": op.get("operationId", ""),
        "parameters": [],
    }

    # Convert parameters
    for param in op.get("parameters", []) or []:
        if not isinstance(param, dict):
            continue
        if param.get("in") == "body":
            schema = param.get("schema", {})
            if isinstance(schema, dict) and "$ref" in schema:
                ref_name = schema["$ref"].split("/")[-1]
                schema = spec_root["components"]["schemas"].get(ref_name, schema)
            result["requestBody"] = {
                "content": {
                    "application/json": {"schema": schema}
                }
            }
        else:
            result["parameters"].append({
                "name": param.get("name", ""),
                "in": param.get("in", "query"),
                "required": param.get("required", False),
                "schema": {
                    "type": param.get("type", "string"),
                    "enum": param.get("enum"),
                    "default": param.get("default"),
                },
                "description": param.get("description", ""),
            })

    # Convert responses
    responses = op.get("responses", {})
    if isinstance(responses, dict):
        result["responses"] = {}
        for status, resp in responses.items():
            if not isinstance(resp, dict):
                continue
            schema = resp.get("schema", {})
            if isinstance(schema, dict) and "$ref" in schema:
                ref_name = schema["$ref"].split("/")[-1]
                schema = spec_root["components"]["schemas"].get(ref_name, schema)
            result["responses"][str(status)] = {
                "description": resp.get("description", ""),
                "content": {
                    "application/json": {"schema": schema}
                } if schema else {},
            }

    return result


# ── Postman Collection → OpenAPI 3.x converter ───────────────────────────

def _convert_postman(text: str) -> dict[str, Any]:
    data = _try_json(text)
    if not isinstance(data, dict):
        return _empty_spec()

    result: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": data.get("info", {}).get("name", "Postman Collection")},
        "paths": {},
        "components": {"schemas": {}},
    }

    items = data.get("item", [])
    if not isinstance(items, list):
        return result

    schema_counter = 0

    def _walk_items(item_list: list, base_path: str = ""):
        nonlocal schema_counter
        for item in item_list:
            if not isinstance(item, dict):
                continue
            # Handle folders (nested items)
            if "item" in item and isinstance(item["item"], list):
                _walk_items(item["item"], base_path)
                continue
            # Handle request items
            request = item.get("request", {})
            if not isinstance(request, dict):
                continue

            url = request.get("url", {})
            if isinstance(url, dict):
                path = "/" + "/".join(url.get("path", []))
                host = url.get("host", [])
            elif isinstance(url, str):
                path = url
            else:
                continue

            method = str(request.get("method", "GET")).lower()
            op: dict[str, Any] = {
                "summary": str(item.get("name", "")),
                "parameters": [],
            }

            # Extract query/path parameters
            for q in url.get("query", []) or []:
                if isinstance(q, dict):
                    op["parameters"].append({
                        "name": q.get("key", ""),
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": str(q.get("description", "")),
                    })
            for v in url.get("variable", []) or []:
                if isinstance(v, dict):
                    op["parameters"].append({
                        "name": v.get("key", ""),
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    })

            # Extract request body schema from Postman body examples
            body = request.get("body", {})
            if isinstance(body, dict):
                mode = body.get("mode", "")
                raw_body = body.get("raw", "")
                if mode == "raw" and raw_body:
                    try:
                        body_json = json.loads(raw_body)
                        if isinstance(body_json, dict):
                            schema_name = f"PostmanBody_{schema_counter}"
                            schema_counter += 1
                            result["components"]["schemas"][schema_name] = _infer_schema_from_value(body_json)
                            op["requestBody"] = {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                                    }
                                }
                            }
                    except json.JSONDecodeError:
                        pass

            result["paths"].setdefault(path, {})[method] = op

    _walk_items(items)
    return result


# ── GraphQL Schema → OpenAPI 3.x converter ───────────────────────────────

def _convert_graphql(text: str) -> dict[str, Any]:
    """Convert GraphQL SDL to OpenAPI-compatible structure.

    Maps GraphQL Query/Mutation types to REST-like endpoints.
    """
    result: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "GraphQL Schema"},
        "paths": {},
        "components": {"schemas": {}},
    }

    # Extract type definitions
    type_defs: dict[str, dict[str, Any]] = {}
    input_defs: dict[str, dict[str, Any]] = {}

    # Parse type blocks: type Name { field: Type }
    type_pattern = re.compile(
        r'(type|input)\s+(\w+)\s*(?:implements\s+[\w\s,&]+)?\s*'
        r'\{\s*([^}]*(?:\{[^}]*\}[^}]*)*)\s*\}',
        re.MULTILINE
    )
    # Parse enum blocks
    enum_pattern = re.compile(
        r'enum\s+(\w+)\s*\{\s*([^}]*)\s*\}',
        re.MULTILINE
    )

    for match in type_pattern.finditer(text):
        kind = match.group(1)
        name = match.group(2)
        body = match.group(3)
        fields: dict[str, Any] = {"type": "object", "properties": {}}
        # Parse field lines: fieldName: Type! or fieldName(arg: Type): ReturnType
        field_pattern = re.compile(r'(\w+)\s*(?:\([^)]*\))?\s*:\s*(\[?\w+\]?[!]?)')
        for fm in field_pattern.finditer(body):
            fname = fm.group(1)
            ftype_raw = fm.group(2)
            ftype = _graphql_to_json_type(ftype_raw)
            fields["properties"][fname] = ftype if isinstance(ftype, dict) else {"type": ftype}
        if kind == "input":
            input_defs[name] = fields
        else:
            type_defs[name] = fields

    # Parse enums
    for match in enum_pattern.finditer(text):
        name = match.group(1)
        values = [v.strip() for v in match.group(2).split() if v.strip()]
        type_defs[name] = {"type": "string", "enum": values}

    result["components"]["schemas"] = {**type_defs, **input_defs}

    # Generate REST-like endpoints for Query and Mutation types
    if "Query" in type_defs:
        query_fields = type_defs["Query"].get("properties", {})
        for field_name, field_schema in query_fields.items():
            result["paths"][f"/graphql/query/{field_name}"] = {
                "post": {
                    "summary": f"Query: {field_name}",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {
                                    "query": {"type": "string", "default": f"{{ {field_name} }}"}
                                }}
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": f"Result of {field_name}",
                            "content": {"application/json": {"schema": field_schema}}
                        }
                    }
                }
            }

    if "Mutation" in type_defs:
        mut_fields = type_defs["Mutation"].get("properties", {})
        for field_name, field_schema in mut_fields.items():
            input_schema_name = f"{field_name[0].upper()}{field_name[1:]}Input"
            body_schema = input_defs.get(input_schema_name, {"type": "object"})
            result["paths"][f"/graphql/mutation/{field_name}"] = {
                "post": {
                    "summary": f"Mutation: {field_name}",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "variables": body_schema,
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": f"Result of {field_name}",
                            "content": {"application/json": {"schema": field_schema}}
                        }
                    }
                }
            }

    return result


def _graphql_to_json_type(gql_type: str) -> dict | str:
    """Map GraphQL type to JSON Schema type."""
    gql_type = gql_type.replace("!", "").strip()
    is_list = gql_type.startswith("[") and gql_type.endswith("]")
    inner = gql_type.strip("[]")
    type_map = {
        "String": "string", "Int": "integer", "Float": "number",
        "Boolean": "boolean", "ID": "string",
    }
    base = type_map.get(inner, inner.lower())
    if is_list:
        return {"type": "array", "items": {"type": base} if isinstance(base, str) else base}
    return base if isinstance(base, str) else base


# ── gRPC .proto → OpenAPI 3.x converter ─────────────────────────────────

def _convert_grpc(text: str) -> dict[str, Any]:
    """Convert .proto file to OpenAPI-compatible structure."""
    result: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "gRPC Service"},
        "paths": {},
        "components": {"schemas": {}},
    }

    # Extract package
    pkg_match = re.search(r'package\s+([\w.]+)\s*;', text)
    package = pkg_match.group(1) if pkg_match else "default"

    # Extract message definitions
    msg_pattern = re.compile(
        r'message\s+(\w+)\s*\{\s*([^}]*(?:\{[^}]*\}[^}]*)*)\s*\}',
        re.MULTILINE
    )
    for match in msg_pattern.finditer(text):
        name = match.group(1)
        body = match.group(2)
        fields: dict[str, Any] = {"type": "object", "properties": {}}
        # Parse fields: type name = number;
        field_pattern = re.compile(r'(repeated\s+)?(\w+)\s+(\w+)\s*=\s*(\d+)\s*;')
        for fm in field_pattern.finditer(body):
            is_repeated = bool(fm.group(1))
            ftype_raw = fm.group(2)
            fname = fm.group(3)
            ftype = _proto_to_json_type(ftype_raw)
            if is_repeated:
                fields["properties"][fname] = {"type": "array", "items": ftype if isinstance(ftype, dict) else {"type": ftype}}
            else:
                fields["properties"][fname] = ftype if isinstance(ftype, dict) else {"type": ftype}
        result["components"]["schemas"][name] = fields

    # Extract enum definitions
    enum_pattern = re.compile(
        r'enum\s+(\w+)\s*\{\s*([^}]*)\s*\}',
        re.MULTILINE
    )
    for match in enum_pattern.finditer(text):
        name = match.group(1)
        body = match.group(2)
        values = [v.strip().split("=")[0].strip() for v in body.split("\n") if v.strip() and not v.strip().startswith("//")]
        result["components"]["schemas"][name] = {"type": "string", "enum": [v for v in values if v]}

    # Extract service → REST-like endpoints
    service_pattern = re.compile(
        r'service\s+(\w+)\s*\{\s*([^}]*)\s*\}',
        re.MULTILINE
    )
    for match in service_pattern.finditer(text):
        svc_name = match.group(1)
        body = match.group(2)
        # Parse rpc methods: rpc Method(RequestType) returns (ResponseType);
        rpc_pattern = re.compile(
            r'rpc\s+(\w+)\s*\(\s*(?:stream\s+)?(\w+)\s*\)\s*returns\s*\(\s*(?:stream\s+)?(\w+)\s*\)',
            re.MULTILINE
        )
        for rm in rpc_pattern.finditer(body):
            method_name = rm.group(1)
            request_type = rm.group(2)
            response_type = rm.group(3)
            path = f"/{package.replace('.', '/')}.{svc_name}/{method_name}"

            request_schema = {}
            if request_type in result["components"]["schemas"]:
                request_schema = {"$ref": f"#/components/schemas/{request_type}"}

            response_schema = {}
            if response_type in result["components"]["schemas"]:
                response_schema = {"$ref": f"#/components/schemas/{response_type}"}

            result["paths"][path] = {
                "post": {
                    "summary": f"gRPC: {svc_name}.{method_name}",
                    "description": f"RPC method {method_name} (request: {request_type}, response: {response_type})",
                    "requestBody": {
                        "content": {"application/json": {"schema": request_schema}}
                    } if request_schema else {},
                    "responses": {
                        "200": {
                            "description": f"Response of {method_name}",
                            "content": {"application/json": {"schema": response_schema}}
                        } if response_schema else {},
                    }
                }
            }

    return result


def _proto_to_json_type(proto_type: str) -> dict | str:
    type_map = {
        "string": "string", "int32": "integer", "int64": "integer",
        "uint32": "integer", "uint64": "integer", "sint32": "integer",
        "sint64": "integer", "fixed32": "integer", "fixed64": "integer",
        "sfixed32": "integer", "sfixed64": "integer", "float": "number",
        "double": "number", "bool": "boolean", "bytes": "string",
    }
    return type_map.get(proto_type, proto_type.lower())


# ── HAR → OpenAPI 3.x converter ─────────────────────────────────────────

def _convert_har(text_or_path: Any) -> dict[str, Any]:
    """Convert HAR traffic to OpenAPI-compatible structure via har_importer."""
    try:
        from .har_importer import import_har_endpoints
        endpoints = import_har_endpoints(str(text_or_path) if isinstance(text_or_path, (str, Path)) else "", min_count=1)
    except Exception:
        return _empty_spec()

    result: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "HAR Traffic"},
        "paths": {},
        "components": {"schemas": {}},
    }

    for ep in endpoints:
        path = ep["path"]
        method = ep["method"].lower()
        result["paths"].setdefault(path, {})[method] = {
            "summary": ep.get("summary", ""),
            "parameters": [],
        }

    return result


# ── Schema inference from JSON values ────────────────────────────────────

def _infer_schema_from_value(value: Any) -> dict[str, Any]:
    """Infer a JSON Schema from an example value."""
    if isinstance(value, dict):
        props = {}
        for k, v in value.items():
            props[k] = _infer_schema_from_value(v)
        return {"type": "object", "properties": props}
    elif isinstance(value, list):
        item_schema = _infer_schema_from_value(value[0]) if value else {"type": "string"}
        return {"type": "array", "items": item_schema}
    elif isinstance(value, bool):
        return {"type": "boolean"}
    elif isinstance(value, int):
        return {"type": "integer"}
    elif isinstance(value, float):
        return {"type": "number"}
    else:
        return {"type": "string"}


# ── Quick test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Universal API Doc Parser")
    parser.add_argument("file", help="Path to API doc file")
    args = parser.parse_args()

    result = parse_to_openapi(args.file)
    paths_count = len(result.get("paths", {}))
    schemas_count = len(result.get("components", {}).get("schemas", {}))
    print(f"\nParsed: {paths_count} paths, {schemas_count} schemas")

    if paths_count > 0:
        print("\nEndpoints:")
        for path, methods in sorted(result["paths"].items()):
            for method in methods:
                print(f"  {method.upper():6s} {path}")
    if schemas_count > 0:
        print(f"\nSchemas: {', '.join(result['components']['schemas'].keys())}")
