"""Generate API_SPEC.md in Project B format for QualiBug parser."""
import yaml
import json
from pathlib import Path

INPUT = Path("projects/contractflow_c/input/openapi.yaml")
BR_INPUT = Path("projects/contractflow_c/input/BUSINESS_RULES.md")
OUTPUT = Path("projects/contractflow_c/input/API_SPEC.md")

spec = yaml.safe_load(INPUT.read_text(encoding="utf-8"))
schemas = spec.get("components", {}).get("schemas", {})

# Parse business rules
br_content = BR_INPUT.read_text(encoding="utf-8") if BR_INPUT.exists() else ""

lines = []
lines.append("# API 接口文档")
lines.append("")
lines.append("API Base URL: `http://localhost:8000`")
lines.append("")
lines.append("所有需要登录的接口使用：")
lines.append("")
lines.append("```http")
lines.append("Authorization: Bearer <token>")
lines.append("```")
lines.append("")
lines.append("## 测试账号")
lines.append("")
lines.append("Acme租户测试Token：")
lines.append("- admin: `acme-admin-token`")
lines.append("- legal: `acme-legal-token`")
lines.append("- finance: `acme-finance-token`")
lines.append("- requester: `acme-requester-token`")
lines.append("- project_manager: `acme-manager-token`")
lines.append("- auditor: `acme-auditor-token`")
lines.append("- vendor: `acme-vendor-token`")
lines.append("")
lines.append("Globex租户测试Token：")
lines.append("- admin: `globex-admin-token`")
lines.append("- requester: `globex-requester-token`")
lines.append("- finance: `globex-finance-token`")
lines.append("")

def get_example(schema_name, depth=0):
    """Generate example from schema."""
    if depth > 3:
        return {}
    schema = schemas.get(schema_name, {})
    if "allOf" in schema:
        result = {}
        for sub in schema["allOf"]:
            if "$ref" in sub:
                ref_name = sub["$ref"].split("/")[-1]
                result.update(get_example(ref_name, depth+1))
            elif "properties" in sub:
                for k, v in sub["properties"].items():
                    result[k] = prop_example(v)
        return result
    props = schema.get("properties", {})
    result = {}
    for k, v in props.items():
        result[k] = prop_example(v)
    return result

def prop_example(prop):
    """Get example value for property."""
    if "$ref" in prop:
        return {}
    typ = prop.get("type", "string")
    if isinstance(typ, list):
        typ = typ[0]
    fmt = prop.get("format", "")
    enum = prop.get("enum")
    if enum:
        return enum[0]
    if typ == "string":
        if fmt == "uuid":
            return "550e8400-e29b-41d4-a716-446655440000"
        if fmt == "date":
            return "2025-01-01"
        if fmt == "date-time":
            return "2025-01-01T10:00:00Z"
        if fmt == "email":
            return "user@example.com"
        if fmt == "uri":
            return "http://example.com/evidence"
        return "string"
    if typ == "number":
        return 1000.00
    if typ == "integer":
        return 1
    if typ == "boolean":
        return True
    if typ == "array":
        return []
    return None

def convert_path(path):
    """Convert {param} to :param format and add /api/v1 prefix."""
    import re
    # Convert {param} to :param
    path = re.sub(r'\{(\w+)\}', r':\1', path)
    # Add /api/v1 prefix
    return f"/api/v1{path}"

# Process paths
paths = spec.get("paths", {})

# Group by resource
resources = {
    "Contracts": [],
    "Milestones": [],
    "Finance": [],
    "Audit": [],
    "Reference": [],
    "Auth": []
}

for path, methods in paths.items():
    for method, op in methods.items():
        if method not in ["get", "post", "patch", "put", "delete"]:
            continue
        tags = op.get("tags", ["other"])
        tag = tags[0] if tags else "other"
        
        # Map to resource
        if tag == "contracts":
            resources["Contracts"].append((path, method, op))
        elif tag == "milestones":
            resources["Milestones"].append((path, method, op))
        elif tag == "finance":
            resources["Finance"].append((path, method, op))
        elif tag == "audit":
            resources["Audit"].append((path, method, op))
        elif tag == "reference":
            resources["Reference"].append((path, method, op))
        elif tag == "auth":
            resources["Auth"].append((path, method, op))

# Output by resource
for resource, ops in resources.items():
    if not ops:
        continue
    lines.append(f"## {resource}")
    lines.append("")
    
    for path, method, op in ops:
        full_path = convert_path(path)
        summary = op.get("summary", "")
        desc = op.get("description", "")
        
        lines.append(f"### {method.upper()} {full_path}")
        lines.append("")
        if summary:
            lines.append(f"{summary}。")
            lines.append("")
        if desc:
            lines.append(f"{desc}")
            lines.append("")
        
        # Parameters
        params = op.get("parameters", [])
        path_params = [p for p in params if p.get("in") == "path" and "$ref" not in p]
        header_params = [p for p in params if p.get("in") == "header" and "$ref" not in p]
        
        if header_params:
            for p in header_params:
                name = p.get("name", "")
                pdesc = p.get("description", "")
                lines.append(f"请求头：`{name}` - {pdesc}")
                lines.append("")
        
        # Request body
        req_body = op.get("requestBody", {})
        if req_body:
            content = req_body.get("content", {}).get("application/json", {})
            schema = content.get("schema", {})
            if "$ref" in schema:
                schema_name = schema["$ref"].split("/")[-1]
                example = get_example(schema_name)
                lines.append("请求：")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(example, ensure_ascii=False))
                lines.append("```")
                lines.append("")
        
        # Responses
        responses = op.get("responses", {})
        for code, resp in responses.items():
            if code.startswith("2"):
                content = resp.get("content", {}).get("application/json", {})
                schema = content.get("schema", {})
                if schema:
                    if "$ref" in schema:
                        schema_name = schema["$ref"].split("/")[-1]
                        example = get_example(schema_name)
                    elif schema.get("type") == "array":
                        items = schema.get("items", {})
                        if "$ref" in items:
                            schema_name = items["$ref"].split("/")[-1]
                            example = [get_example(schema_name)]
                        else:
                            example = []
                    else:
                        example = {}
                    lines.append(f"响应 {code}：")
                    lines.append("")
                    lines.append("```json")
                    lines.append(json.dumps(example, ensure_ascii=False))
                    lines.append("```")
                    lines.append("")
        
        # Add business rules for specific endpoints
        if "submit" in path:
            lines.append("前置条件：合同状态必须为DRAFT，至少有一个里程碑且里程碑金额合计等于合同总额。")
            lines.append("")
        elif "legal-approve" in path:
            lines.append("前置条件：只有legal或admin角色可操作，合同状态必须为LEGAL_REVIEW。")
            lines.append("")
        elif "activate" in path:
            lines.append("前置条件：合同状态必须为APPROVED。")
            lines.append("副作用：预算available减少合同金额，reserved增加同额。")
            lines.append("")
        elif "cancel" in path:
            lines.append("副作用：释放未使用预算预留，所有未完成付款申请自动REJECTED。")
            lines.append("")
        elif "accept" in path and "milestone" in path.lower():
            lines.append("前置条件：里程碑状态必须为SUBMITTED。")
            lines.append("幂等性：重复调用不得重复生成验收记录。")
            lines.append("")
        elif "pay" in path:
            lines.append("前置条件：付款申请状态必须为FINANCE_APPROVED。")
            lines.append("幂等性：相同Idempotency-Key重复调用不得重复改变资金。")
            lines.append("副作用：reserved减少、spent增加、contract.paid增加。")
            lines.append("")

OUTPUT.write_text("\n".join(lines), encoding="utf-8")
print(f"Generated: {OUTPUT}")
print(f"Size: {OUTPUT.stat().st_size:,} bytes")
print(f"Lines: {len(lines)}")
