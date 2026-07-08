from __future__ import annotations

"""Structured context extraction from PRD, OpenAPI, and DB schema.

Extracts the structural building blocks needed by the Bug Ontology Registry
and Behavior Slice Generator:

- roles         — actors / personas from security schemes + PRD descriptions
- entities      — domain objects from schemas + PRD noun phrases
- states        — status values from PRD descriptions + OpenAPI enums
- transitions   — state flows from PRD + API method sequences
- actions       — operations from OpenAPI operationIds + PRD verb phrases
- endpoints     — API paths from OpenAPI
- fields        — schema properties with classification
  - ownership_fields
  - tenant_fields
  - money_fields
  - status_fields
  - unique_fields
  - audit_fields

Design contract:
  - No per-project hardcoding. All detection uses configurable heuristics.
  - Lexicon-based detection with fallback patterns.
  - Every extracted item carries a confidence score.
"""

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Configurable field-classification lexicons ──────────────────────────
# These are data-driven defaults. Deployments can override via
# ``policies/field_lexicon.json`` placed next to ``semantic_lexicon.json``.

_OWNERSHIP_FIELD_TOKENS: set[str] = {
    "user_id", "owner_id", "created_by", "updated_by", "creator_id",
    "buyer_id", "seller_id", "customer_id", "assignee_id", "author_id",
    "operator_id", "modifier_id", "belongs_to", "owned_by",
    "用户", "归属", "所属",
}

_TENANT_FIELD_TOKENS: set[str] = {
    "tenant_id", "organization_id", "org_id", "company_id", "workspace_id",
    "租户", "组织", "公司", "工作空间",
}

_MONEY_FIELD_TOKENS: set[str] = {
    "amount", "price", "balance", "total", "subtotal", "fee", "cost",
    "revenue", "payment", "refund", "charge", "discount", "tax",
    "wallet", "credit", "debit", "deposit", "withdrawal",
    "金额", "价格", "余额", "费用", "支付", "退款", "折扣",
}

_QUANTITY_FIELD_TOKENS: set[str] = {
    "quantity", "count", "stock", "inventory", "qty", "volume",
    "weight", "number", "pieces", "units",
    "数量", "库存", "件数",
}

_STATUS_FIELD_TOKENS: set[str] = {
    "status", "state", "phase", "stage", "condition",
    "状态", "阶段", "环节",
}

_UNIQUE_FIELD_TOKENS: set[str] = {
    "id", "uuid", "code", "sku", "barcode", "serial_number",
    "phone", "email", "username", "account",
}

_AUDIT_FIELD_TOKENS: set[str] = {
    "created_at", "updated_at", "deleted_at", "created_time",
    "updated_time", "create_time", "update_time", "timestamp",
    "version", "revision", "modified_by", "modified_at",
    "创建时间", "更新时间", "删除时间",
}

# State-value lexicon for identifying state-like values in PRD text
_STATE_VALUE_PATTERNS: dict[str, list[str]] = {
    "CREATED": ["created", "draft", "new", "新建", "草稿", "创建", "初始"],
    "PENDING": ["pending", "submitted", "waiting", "待处理", "已提交", "待审核"],
    "ACTIVE": ["active", "in_progress", "processing", "进行中", "处理中", "执行中"],
    "COMPLETED": ["completed", "finished", "done", "success", "已完成", "成功", "结束"],
    "CANCELLED": ["cancelled", "canceled", "aborted", "已取消", "取消", "终止"],
    "FAILED": ["failed", "error", "rejected", "失败", "拒绝", "驳回"],
    "PAID": ["paid", "已支付", "支付成功"],
    "REFUNDED": ["refunded", "已退款", "退款成功"],
    "SHIPPED": ["shipped", "delivered", "已发货", "已交付"],
    "APPROVED": ["approved", "accepted", "已审批", "已通过", "批准"],
    "REJECTED": ["rejected", "denied", "已拒绝", "驳回", "不通过"],
    "EXPIRED": ["expired", "已过期", "失效"],
}


@dataclass
class ExtractedContext:
    """Container for all extracted structural context."""

    # ── Actors / Roles ──
    roles: list[dict[str, Any]] = field(default_factory=list)
    # ── Domain Entities ──
    entities: list[dict[str, Any]] = field(default_factory=list)
    # ── States ──
    states: list[dict[str, Any]] = field(default_factory=list)
    # ── Transitions ──
    transitions: list[dict[str, Any]] = field(default_factory=list)
    # ── Actions ──
    actions: list[dict[str, Any]] = field(default_factory=list)
    # ── Endpoints ──
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    # ── Fields ──
    fields: list[dict[str, Any]] = field(default_factory=list)
    # ── Classified field sets ──
    ownership_fields: list[str] = field(default_factory=list)
    tenant_fields: list[str] = field(default_factory=list)
    money_fields: list[str] = field(default_factory=list)
    quantity_fields: list[str] = field(default_factory=list)
    status_fields: list[str] = field(default_factory=list)
    unique_fields: list[str] = field(default_factory=list)
    audit_fields: list[str] = field(default_factory=list)
    # ── Source tracking ──
    sources_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "roles": self.roles,
            "entities": self.entities,
            "states": self.states,
            "transitions": self.transitions,
            "actions": self.actions,
            "endpoints": self.endpoints,
            "fields": self.fields,
            "ownership_fields": self.ownership_fields,
            "tenant_fields": self.tenant_fields,
            "money_fields": self.money_fields,
            "quantity_fields": self.quantity_fields,
            "status_fields": self.status_fields,
            "unique_fields": self.unique_fields,
            "audit_fields": self.audit_fields,
            "sources_used": self.sources_used,
            "summary": {
                "role_count": len(self.roles),
                "entity_count": len(self.entities),
                "state_count": len(self.states),
                "transition_count": len(self.transitions),
                "action_count": len(self.actions),
                "endpoint_count": len(self.endpoints),
                "field_count": len(self.fields),
                "ownership_field_count": len(self.ownership_fields),
                "tenant_field_count": len(self.tenant_fields),
                "money_field_count": len(self.money_fields),
                "status_field_count": len(self.status_fields),
                "unique_field_count": len(self.unique_fields),
                "audit_field_count": len(self.audit_fields),
            },
        }


# ── Public API ───────────────────────────────────────────────────────────

def extract_context(
    prd_text: str = "",
    api_spec_text: str = "",
    db_schema_text: str = "",
    *,
    project_context: dict[str, Any] | None = None,
) -> ExtractedContext:
    """Extract structured context from all available sources.

    Args:
        prd_text: Raw PRD / requirements document text.
        api_spec_text: OpenAPI / Swagger / Postman spec text.
        db_schema_text: SQL DDL or ORM model definitions.
        project_context: Optional pre-extracted context from enterprise pipeline.

    Returns:
        ExtractedContext with all structural building blocks.
    """
    ctx = ExtractedContext()

    # Track which sources contributed
    sources: list[str] = []
    if prd_text.strip():
        sources.append("prd")
    if api_spec_text.strip():
        sources.append("openapi")
    if db_schema_text.strip():
        sources.append("db_schema")
    if project_context:
        sources.append("project_context")
    ctx.sources_used = sources

    # 1. Parse OpenAPI spec → endpoints, fields, schemas
    api_data = _parse_api_spec(api_spec_text)
    if api_data:
        ctx.endpoints = _extract_endpoints(api_data)
        ctx.fields = _extract_fields(api_data, ctx.endpoints)
        ctx.actions = _extract_actions(api_data)

    # 2. Parse PRD → roles, entities, states, transitions
    if prd_text.strip():
        ctx.roles = _extract_roles_from_prd(prd_text, api_data)
        ctx.entities = _extract_entities_from_prd(prd_text, api_data)
        ctx.states = _extract_states_from_prd(prd_text, api_data)
        ctx.transitions = _extract_transitions_from_prd(prd_text, api_data, ctx.endpoints)

    # 3. Parse DB schema → additional fields and entity confirmation
    if db_schema_text.strip():
        db_fields = _extract_fields_from_db_schema(db_schema_text)
        _merge_db_fields(ctx, db_fields)

    # 4. Merge project context if provided
    if project_context:
        _merge_project_context(ctx, project_context)

    # 5. Classify fields into semantic categories
    _classify_fields(ctx)

    # 6. Fill gaps with heuristic defaults when sources are sparse
    _fill_role_defaults(ctx)
    _fill_entity_defaults(ctx, api_data)

    return ctx


# ── Internal: API Spec Parsing ───────────────────────────────────────────

def _parse_api_spec(text: str) -> dict[str, Any] | None:
    """Parse API spec text into a dict. Tries JSON first, then delegates."""
    text_stripped = text.strip()
    if not text_stripped:
        return None
    try:
        data = json.loads(text_stripped)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Try the universal parser
    try:
        from .universal_api_parser import parse_to_openapi
        return parse_to_openapi(text_stripped)
    except ImportError:
        pass
    return None


def _extract_endpoints(api_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all endpoints from OpenAPI-compatible spec."""
    endpoints: list[dict[str, Any]] = []
    paths = api_data.get("paths", {})
    if not isinstance(paths, dict):
        return endpoints

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, detail in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            if not isinstance(detail, dict):
                continue
            op_id = str(detail.get("operationId", "")).strip()
            summary = str(detail.get("summary", detail.get("description", ""))).strip()
            tags = detail.get("tags", [])
            if isinstance(tags, list):
                tags = [str(t) for t in tags]

            # Extract parameters
            params = []
            for param in detail.get("parameters", []) or []:
                if isinstance(param, dict):
                    params.append({
                        "name": str(param.get("name", "")),
                        "in_": str(param.get("in", "")),
                        "required": bool(param.get("required", False)),
                        "schema_type": str((param.get("schema", {}) or {}).get("type", "")),
                    })

            # Extract security requirements
            security = detail.get("security", [])
            if isinstance(security, list):
                security = [list(s.keys()) if isinstance(s, dict) else [] for s in security]

            # Extract request body schema ref
            request_body = detail.get("requestBody", {})
            if isinstance(request_body, dict):
                content = request_body.get("content", {})
                if isinstance(content, dict):
                    for ct, ct_detail in content.items():
                        if isinstance(ct_detail, dict):
                            schema_ref = ct_detail.get("schema", {})
                            if isinstance(schema_ref, dict):
                                request_body = {
                                    "content_type": ct,
                                    "schema_ref": schema_ref.get("$ref", ""),
                                }
                                break

            # Extract response schemas
            responses = {}
            for status_code, resp in (detail.get("responses", {}) or {}).items():
                if not isinstance(resp, dict):
                    continue
                resp_content = resp.get("content", {})
                if isinstance(resp_content, dict):
                    for ct, ct_detail in resp_content.items():
                        if isinstance(ct_detail, dict):
                            schema_ref = ct_detail.get("schema", {})
                            if isinstance(schema_ref, dict):
                                responses[str(status_code)] = {
                                    "content_type": ct,
                                    "schema_ref": schema_ref.get("$ref", ""),
                                }
                                break

            endpoints.append({
                "path": path,
                "method": method.upper(),
                "operation_id": op_id,
                "summary": summary,
                "tags": tags,
                "parameters": params,
                "security": security,
                "request_body": request_body,
                "responses": responses,
                "confidence": 1.0 if op_id else 0.8,
            })

    return endpoints


def _extract_actions(api_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract actions from OpenAPI operationIds and summaries."""
    actions: list[dict[str, Any]] = []
    paths = api_data.get("paths", {})
    if not isinstance(paths, dict):
        return actions

    seen: set[str] = set()
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, detail in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not isinstance(detail, dict):
                continue
            op_id = str(detail.get("operationId", "")).strip()
            summary = str(detail.get("summary", detail.get("description", ""))).strip()
            action_name = op_id or summary or f"{method} {path}"
            if action_name in seen:
                continue
            seen.add(action_name)

            # Classify action type
            method_upper = method.upper()
            if method_upper == "GET":
                action_type = "read"
            elif method_upper == "DELETE":
                action_type = "delete"
            elif method_upper in {"POST", "PUT", "PATCH"}:
                action_type = "write"
            else:
                action_type = "other"

            # Infer entity from path or operationId
            entity = _infer_entity_from_path(path, op_id)
            # Infer verb from operationId
            verb = _infer_verb_from_operation_id(op_id, summary)

            actions.append({
                "name": action_name,
                "type": action_type,
                "method": method_upper,
                "path": path,
                "entity": entity,
                "verb": verb,
                "confidence": 1.0 if op_id else 0.7,
            })
    return actions


def _extract_fields(
    api_data: dict[str, Any],
    endpoints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract all fields from OpenAPI schema definitions."""
    fields: list[dict[str, Any]] = []
    schemas = api_data.get("components", {}).get("schemas", {})
    if isinstance(schemas, dict):
        for schema_name, schema_def in schemas.items():
            if not isinstance(schema_def, dict):
                continue
            props = schema_def.get("properties", {})
            if isinstance(props, dict):
                for prop_name, prop_def in props.items():
                    if not isinstance(prop_def, dict):
                        continue
                    fields.append({
                        "name": prop_name,
                        "entity": schema_name,
                        "type": str(prop_def.get("type", "")),
                        "format": str(prop_def.get("format", "")),
                        "nullable": bool(prop_def.get("nullable", False)),
                        "enum": prop_def.get("enum"),
                        "description": str(prop_def.get("description", "")),
                        "confidence": 1.0,
                    })

    # Also extract from inline schemas in request/response bodies
    for ep in endpoints:
        for resp_info in (ep.get("responses", {}) or {}).values():
            schema_ref = resp_info.get("schema_ref", "")
            if schema_ref:
                entity_name = schema_ref.split("/")[-1] if "/" in schema_ref else schema_ref
                # Already captured via components.schemas above

    return fields


# ── Internal: PRD Parsing ────────────────────────────────────────────────

def _extract_roles_from_prd(
    prd_text: str,
    api_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract roles/actors from PRD text and OpenAPI security schemes."""
    roles: list[dict[str, Any]] = []
    seen: set[str] = set()

    # From API security schemes
    if api_data:
        security_schemes = (
            (api_data.get("components", {}) or {}).get("securitySchemes", {}) or {}
        )
        if isinstance(security_schemes, dict):
            for scheme_name in security_schemes:
                if scheme_name not in seen:
                    seen.add(scheme_name)
                    roles.append({
                        "name": scheme_name,
                        "source": "openapi_security_scheme",
                        "confidence": 0.9,
                    })

    # From PRD text: look for role/actor descriptions
    # Chinese patterns: "管理员", "普通用户", "游客", "审核员", etc.
    role_patterns = [
        (r'(管理员|系统管理员|超级管理员)', "admin"),
        (r'(普通用户|注册用户|会员用户)', "user"),
        (r'(游客|匿名用户|未登录用户)', "anonymous"),
        (r'(审核员|审批人)', "reviewer"),
        (r'(操作员|运营人员)', "operator"),
        (r'(财务|财务人员)', "finance"),
        (r'(审计员|审计人员)', "auditor"),
        (r'(供应商|商家|商户|卖家)', "vendor"),
        (r'(客户|买家|消费者)', "customer"),
        (r'(代理|代理商)', "agent"),
    ]
    for pattern, role_id in role_patterns:
        matches = re.findall(pattern, prd_text, re.IGNORECASE)
        for match in matches:
            role_name = match if isinstance(match, str) else match[0]
            if role_name not in seen:
                seen.add(role_name)
                roles.append({
                    "name": role_name,
                    "role_id": role_id,
                    "source": "prd_pattern",
                    "confidence": 0.7,
                })

    # English patterns
    en_patterns = [
        (r'\b(admin|administrator|super\s*admin)\b', "admin"),
        (r'\b(viewer|guest|anonymous|public)\b', "anonymous"),
        (r'\b(reviewer|approver|auditor)\b', "reviewer"),
        (r'\b(operator|manager|staff)\b', "operator"),
        (r'\b(user|customer|buyer|client)\b', "user"),
        (r'\b(vendor|supplier|merchant|seller)\b', "vendor"),
    ]
    for pattern, role_id in en_patterns:
        matches = re.findall(pattern, prd_text, re.IGNORECASE)
        for match in matches:
            role_name = match if isinstance(match, str) else match[0]
            if role_name.lower() not in seen:
                seen.add(role_name.lower())
                roles.append({
                    "name": role_name,
                    "role_id": role_id,
                    "source": "prd_en_pattern",
                    "confidence": 0.6,
                })

    return roles


def _extract_entities_from_prd(
    prd_text: str,
    api_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract domain entities from PRD text and API schema names."""
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()

    # From API schemas (highest confidence)
    if api_data:
        schemas = api_data.get("components", {}).get("schemas", {})
        if isinstance(schemas, dict):
            for schema_name in schemas:
                key = schema_name.lower()
                if key not in seen:
                    seen.add(key)
                    entities.append({
                        "name": schema_name,
                        "source": "openapi_schema",
                        "confidence": 1.0,
                    })

    # From endpoint paths
    if api_data:
        paths = api_data.get("paths", {})
        if isinstance(paths, dict):
            for path in paths:
                for segment in path.strip("/").split("/"):
                    segment = segment.strip()
                    if not segment or segment.startswith("{") or segment in {"api", "v1", "v2", "v3"}:
                        continue
                    key = segment.lower()
                    if key not in seen:
                        seen.add(key)
                        entities.append({
                            "name": segment,
                            "source": "openapi_path",
                            "confidence": 0.7,
                        })

    # From PRD text: look for entity-like noun phrases
    # Chinese patterns: "XX管理", "XX模块", "XX表", "XX系统"
    entity_patterns = [
        r'(\w{2,8}(?:管理|模块|系统|中心|平台|服务))',
        r'(\w{2,8}(?:订单|商品|用户|库存|支付|退款|优惠券|账户|报表|审批|流程))',
    ]
    for pattern in entity_patterns:
        for match in re.findall(pattern, prd_text):
            entity_name = match if isinstance(match, str) else match[0]
            key = entity_name.lower()
            if key not in seen:
                seen.add(key)
                entities.append({
                    "name": entity_name,
                    "source": "prd_pattern",
                    "confidence": 0.5,
                })

    return entities


def _extract_states_from_prd(
    prd_text: str,
    api_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract state values from PRD text and API enums."""
    states: list[dict[str, Any]] = []
    seen: set[str] = set()

    # From state value patterns in PRD
    for canonical, aliases in _STATE_VALUE_PATTERNS.items():
        for alias in aliases:
            if alias.lower() in prd_text.lower():
                if canonical not in seen:
                    seen.add(canonical)
                    states.append({
                        "name": canonical,
                        "source": "prd_state_pattern",
                        "confidence": 0.7,
                    })
                break

    # From API enum values in schemas
    if api_data:
        schemas = api_data.get("components", {}).get("schemas", {})
        if isinstance(schemas, dict):
            for schema_name, schema_def in schemas.items():
                if not isinstance(schema_def, dict):
                    continue
                props = schema_def.get("properties", {})
                if isinstance(props, dict):
                    for prop_name, prop_def in props.items():
                        if not isinstance(prop_def, dict):
                            continue
                        # Look for status/state fields with enum values
                        prop_lower = prop_name.lower()
                        if any(tok in prop_lower for tok in _STATUS_FIELD_TOKENS):
                            enum_values = prop_def.get("enum")
                            if isinstance(enum_values, list):
                                for val in enum_values:
                                    val_str = str(val).upper()
                                    if val_str not in seen:
                                        seen.add(val_str)
                                        states.append({
                                            "name": val_str,
                                            "entity": schema_name,
                                            "source": "openapi_enum",
                                            "confidence": 1.0,
                                        })

    return states


def _extract_transitions_from_prd(
    prd_text: str,
    api_data: dict[str, Any] | None,
    endpoints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract state transitions from PRD text and API sequences."""
    transitions: list[dict[str, Any]] = []

    # Heuristic: look for PRD phrases like "从X变为Y", "X→Y", "X后进入Y"
    transition_patterns = [
        r'从\s*(\w+)\s*(?:状态)?\s*(?:变为|转为|变成|流转到|进入)\s*(\w+)\s*(?:状态)?',
        r'(\w+)\s*[→>]\s*(\w+)',
        r'(\w+)\s*(?:之后|然后|下一步)\s*(?:进入|变为|流转到)\s*(\w+)',
        r'once\s+(\w+)\s*(?:is\s+)?(\w+)\s*,\s*(?:the\s+)?(?:status|state)\s*(?:becomes|changes to|transitions to)\s*(\w+)',
    ]
    for pattern in transition_patterns:
        for match in re.findall(pattern, prd_text, re.IGNORECASE):
            groups = match if isinstance(match, tuple) and len(match) >= 2 else (match,)
            if len(groups) >= 2:
                from_state = str(groups[0]).strip().upper()
                to_state = str(groups[1]).strip().upper()
                if from_state and to_state and from_state != to_state:
                    transitions.append({
                        "from_state": from_state,
                        "to_state": to_state,
                        "source": "prd_pattern",
                        "confidence": 0.6,
                    })

    # From API endpoints: POST creates → GET reads → PUT updates → DELETE ends
    # This is a structural heuristic
    entity_endpoints: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ep in endpoints:
        path = ep.get("path", "")
        entity = _infer_entity_from_path(path, ep.get("operation_id", ""))
        entity_endpoints[entity].append(ep)

    for entity, eps in entity_endpoints.items():
        methods = {ep.get("method", "") for ep in eps}
        if "POST" in methods and "GET" in methods:
            transitions.append({
                "from_state": "NONEXISTENT",
                "to_state": "CREATED",
                "entity": entity,
                "source": "api_structure",
                "confidence": 0.8,
            })
        if "PUT" in methods or "PATCH" in methods:
            transitions.append({
                "from_state": "CREATED",
                "to_state": "UPDATED",
                "entity": entity,
                "source": "api_structure",
                "confidence": 0.7,
            })
        if "DELETE" in methods:
            transitions.append({
                "from_state": "ANY",
                "to_state": "DELETED",
                "entity": entity,
                "source": "api_structure",
                "confidence": 0.7,
            })

    return transitions


# ── Internal: DB Schema Parsing ──────────────────────────────────────────

def _extract_fields_from_db_schema(schema_text: str) -> list[dict[str, Any]]:
    """Extract field definitions from SQL DDL or ORM model text."""
    fields: list[dict[str, Any]] = []

    # SQL CREATE TABLE pattern
    create_table_pattern = r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\w`".]+?(\w+)\s*\((.*?)\);'
    for match in re.finditer(create_table_pattern, schema_text, re.IGNORECASE | re.DOTALL):
        table_name = match.group(1)
        body = match.group(2)
        # Extract column definitions
        col_pattern = r'[\w`"]+?\s+(\w+)'
        # Simpler: split by comma and extract first word as column name
        for line in body.split(","):
            line = line.strip()
            parts = line.split()
            if not parts:
                continue
            col_name = parts[0].strip("`\"'[]")
            col_type = parts[1] if len(parts) > 1 else ""
            # Filter out SQL keywords
            if col_name.upper() in {"PRIMARY", "FOREIGN", "UNIQUE", "INDEX", "KEY", "CONSTRAINT", "CHECK"}:
                continue
            fields.append({
                "name": col_name,
                "entity": table_name,
                "type": col_type,
                "source": "db_ddl",
                "confidence": 1.0,
            })

    # ORM model pattern (Python/SQLAlchemy style)
    orm_pattern = r'class\s+(\w+)\s*\(.*?Base.*?\).*?:\s*(.*?)(?=\nclass\s+\w+|$)'
    for match in re.finditer(orm_pattern, schema_text, re.DOTALL):
        class_name = match.group(1)
        body = match.group(2)
        col_def = r'(\w+)\s*=\s*Column\s*\('
        for col_match in re.finditer(col_def, body):
            col_name = col_match.group(1)
            if col_name in {"__tablename__", "id"}:
                continue
            # Try to get type
            type_match = re.search(r'Column\s*\(\s*(\w+)', body[col_match.start():col_match.start() + 100])
            col_type = type_match.group(1) if type_match else ""
            fields.append({
                "name": col_name,
                "entity": class_name,
                "type": col_type,
                "source": "db_orm",
                "confidence": 0.9,
            })

    return fields


def _merge_db_fields(ctx: ExtractedContext, db_fields: list[dict[str, Any]]) -> None:
    """Merge DB-extracted fields into the context, avoiding duplicates."""
    existing = {(f["name"], f.get("entity", "")) for f in ctx.fields}
    for f in db_fields:
        if (f["name"], f.get("entity", "")) not in existing:
            ctx.fields.append(f)


def _merge_project_context(ctx: ExtractedContext, pc: dict[str, Any]) -> None:
    """Merge pre-extracted project context into the context."""
    for key in ("roles", "entities", "states", "transitions", "actions", "endpoints"):
        items = pc.get(key, [])
        if isinstance(items, list):
            existing_list = getattr(ctx, key)
            existing_keys = {
                (item.get("name", ""), item.get("entity", item.get("path", "")))
                for item in existing_list
            }
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_key = (item.get("name", ""), item.get("entity", item.get("path", "")))
                if item_key not in existing_keys:
                    existing_list.append(item)


# ── Internal: Field Classification ───────────────────────────────────────

def _classify_fields(ctx: ExtractedContext) -> None:
    """Classify all extracted fields into semantic categories."""
    for field_info in ctx.fields:
        name = field_info.get("name", "")
        name_lower = name.lower()

        if any(tok == name_lower or tok in name_lower for tok in _OWNERSHIP_FIELD_TOKENS):
            if name not in ctx.ownership_fields:
                ctx.ownership_fields.append(name)

        if any(tok == name_lower or tok in name_lower for tok in _TENANT_FIELD_TOKENS):
            if name not in ctx.tenant_fields:
                ctx.tenant_fields.append(name)

        if any(tok == name_lower or tok in name_lower for tok in _MONEY_FIELD_TOKENS):
            if name not in ctx.money_fields:
                ctx.money_fields.append(name)

        if any(tok == name_lower or tok in name_lower for tok in _QUANTITY_FIELD_TOKENS):
            if name not in ctx.quantity_fields:
                ctx.quantity_fields.append(name)

        if any(tok == name_lower or tok in name_lower for tok in _STATUS_FIELD_TOKENS):
            if name not in ctx.status_fields:
                ctx.status_fields.append(name)

        if any(tok == name_lower or tok in name_lower for tok in _UNIQUE_FIELD_TOKENS):
            if name not in ctx.unique_fields:
                ctx.unique_fields.append(name)

        if any(tok == name_lower or tok in name_lower for tok in _AUDIT_FIELD_TOKENS):
            if name not in ctx.audit_fields:
                ctx.audit_fields.append(name)


# ── Internal: Gap Filling ────────────────────────────────────────────────

def _fill_role_defaults(ctx: ExtractedContext) -> None:
    """If no roles found, provide universal defaults."""
    if ctx.roles:
        return
    seen_names = {r.get("name", "").lower() for r in ctx.roles}
    defaults = [
        {"name": "admin", "role_id": "admin", "source": "default", "confidence": 0.3},
        {"name": "viewer", "role_id": "viewer", "source": "default", "confidence": 0.3},
        {"name": "anonymous", "role_id": "anonymous", "source": "default", "confidence": 0.3},
    ]
    for d in defaults:
        if d["name"] not in seen_names:
            ctx.roles.append(d)


def _fill_entity_defaults(ctx: ExtractedContext, api_data: dict[str, Any] | None) -> None:
    """If no entities found, attempt last-resort extraction from paths."""
    if ctx.entities:
        return
    # Already attempted in _extract_entities_from_prd; no further fallback needed
    pass


# ── Internal: Helpers ────────────────────────────────────────────────────

def _infer_entity_from_path(path: str, operation_id: str = "") -> str:
    """Infer entity name from an API path or operationId."""
    # Try operationId first: e.g., "getOrders" → "orders"
    if operation_id:
        # Strip common verb prefixes
        for prefix in ("get", "list", "create", "update", "delete", "find", "search",
                       "add", "remove", "fetch", "query", "patch", "put", "post"):
            if operation_id.lower().startswith(prefix) and len(operation_id) > len(prefix):
                return operation_id[len(prefix):].lower()

    # From path: /api/orders/{id} → orders
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    # Remove common prefixes
    skip = {"api", "v1", "v2", "v3", "v4", "v5"}
    meaningful = [s for s in segments if s.lower() not in skip]
    return meaningful[-1].lower() if meaningful else "unknown"


def _infer_verb_from_operation_id(op_id: str, summary: str = "") -> str:
    """Infer action verb from operationId or summary."""
    verb_map = {
        "get": "查询", "list": "列表", "create": "创建", "update": "修改",
        "delete": "删除", "patch": "更新", "search": "搜索", "find": "查找",
        "add": "添加", "remove": "移除", "submit": "提交", "approve": "审批",
        "reject": "拒绝", "cancel": "取消", "publish": "发布", "export": "导出",
        "import": "导入", "upload": "上传", "download": "下载",
    }
    if op_id:
        for eng, cn in verb_map.items():
            if op_id.lower().startswith(eng):
                return cn
    return summary or "操作"


# ── Persistence ──────────────────────────────────────────────────────────

def persist_context(
    ctx: ExtractedContext,
    project: str,
    *,
    root: Path | None = None,
) -> Path:
    """Persist extracted context to platform_workspace for reuse."""
    root = Path(root or Path.cwd())
    out_dir = root / "platform_workspace" / project
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "extracted_context.json"
    path.write_text(
        json.dumps(ctx.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def load_context(project: str, *, root: Path | None = None) -> ExtractedContext | None:
    """Load previously persisted extracted context."""
    root = Path(root or Path.cwd())
    path = root / "platform_workspace" / project / "extracted_context.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ctx = ExtractedContext()
        for key in ("roles", "entities", "states", "transitions", "actions", "endpoints", "fields",
                     "ownership_fields", "tenant_fields", "money_fields", "quantity_fields",
                     "status_fields", "unique_fields", "audit_fields", "sources_used"):
            if key in data:
                setattr(ctx, key, data[key])
        return ctx
    except Exception:
        return None
