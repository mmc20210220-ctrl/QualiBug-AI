"""Convert OpenAPI YAML to Markdown API spec format (like Project B)."""
import yaml
from pathlib import Path

INPUT = Path("projects/contractflow_c/input/openapi.yaml")
OUTPUT = Path("projects/contractflow_c/input/API_SPEC.md")

spec = yaml.safe_load(INPUT.read_text(encoding="utf-8"))

lines = []
lines.append("# ContractFlow API 接口文档\n")
lines.append(f"API Base URL: `http://localhost:8000/api/v1`\n")
lines.append("所有需要登录的接口使用：\n")
lines.append("```http")
lines.append("Authorization: Bearer <token>")
lines.append("```\n")

# Process paths
paths = spec.get("paths", {})
schemas = spec.get("components", {}).get("schemas", {})

def schema_to_example(schema_name, depth=0):
    """Generate example JSON from schema."""
    if depth > 3:
        return {}
    schema = schemas.get(schema_name, {})
    if "allOf" in schema:
        result = {}
        for sub in schema["allOf"]:
            if "$ref" in sub:
                ref_name = sub["$ref"].split("/")[-1]
                result.update(schema_to_example(ref_name, depth+1))
            elif "properties" in sub:
                for k, v in sub["properties"].items():
                    result[k] = type_to_example(v)
        return result
    props = schema.get("properties", {})
    result = {}
    for k, v in props.items():
        result[k] = type_to_example(v)
    return result

def type_to_example(prop):
    """Convert property schema to example value."""
    if "$ref" in prop:
        return {}
    typ = prop.get("type", "string")
    if isinstance(typ, list):
        typ = typ[0]
    fmt = prop.get("format", "")
    if typ == "string":
        if fmt == "uuid":
            return "uuid-string"
        if fmt == "date":
            return "2025-01-01"
        if fmt == "date-time":
            return "2025-01-01T10:00:00Z"
        if fmt == "email":
            return "user@example.com"
        if fmt == "uri":
            return "http://example.com"
        enum = prop.get("enum")
        if enum:
            return enum[0]
        return "string"
    if typ == "number":
        return 100.00
    if typ == "integer":
        return 1
    if typ == "boolean":
        return True
    if typ == "array":
        return []
    if typ == "object":
        return {}
    return None

# Group by tags
tagged_ops = {}
for path, methods in paths.items():
    for method, op in methods.items():
        if method in ["get", "post", "patch", "put", "delete"]:
            tags = op.get("tags", ["other"])
            tag = tags[0] if tags else "other"
            if tag not in tagged_ops:
                tagged_ops[tag] = []
            tagged_ops[tag].append((path, method, op))

# Output by tag
for tag, ops in tagged_ops.items():
    lines.append(f"## {tag.capitalize()}\n")
    
    for path, method, op in ops:
        summary = op.get("summary", "")
        desc = op.get("description", "")
        
        lines.append(f"### {method.upper()} {path}\n")
        if summary:
            lines.append(f"{summary}\n")
        if desc:
            lines.append(f"{desc}\n")
        
        # Parameters
        params = op.get("parameters", [])
        if params:
            lines.append("参数：\n")
            for p in params:
                if "$ref" in p:
                    continue
                name = p.get("name", "")
                loc = p.get("in", "")
                req = "必需" if p.get("required") else "可选"
                pdesc = p.get("description", "")
                lines.append(f"- `{name}` ({loc}, {req}): {pdesc}")
            lines.append("")
        
        # Request body
        req_body = op.get("requestBody", {})
        if req_body:
            content = req_body.get("content", {}).get("application/json", {})
            schema = content.get("schema", {})
            if "$ref" in schema:
                schema_name = schema["$ref"].split("/")[-1]
                example = schema_to_example(schema_name)
                lines.append("请求：\n")
                lines.append("```json")
                import json
                lines.append(json.dumps(example, ensure_ascii=False))
                lines.append("```\n")
        
        # Responses
        responses = op.get("responses", {})
        for code, resp in responses.items():
            if code.startswith("2"):
                rdesc = resp.get("description", "")
                content = resp.get("content", {}).get("application/json", {})
                schema = content.get("schema", {})
                if schema:
                    if "$ref" in schema:
                        schema_name = schema["$ref"].split("/")[-1]
                        example = schema_to_example(schema_name)
                    elif schema.get("type") == "array":
                        items = schema.get("items", {})
                        if "$ref" in items:
                            schema_name = items["$ref"].split("/")[-1]
                            example = [schema_to_example(schema_name)]
                        else:
                            example = []
                    else:
                        example = {}
                    lines.append(f"响应 {code}：\n")
                    lines.append("```json")
                    import json
                    lines.append(json.dumps(example, ensure_ascii=False, indent=2))
                    lines.append("```\n")

OUTPUT.write_text("\n".join(lines), encoding="utf-8")
print(f"Generated: {OUTPUT}")
print(f"Size: {OUTPUT.stat().st_size:,} bytes")
print(f"Lines: {len(lines)}")
