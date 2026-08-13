from __future__ import annotations

"""Document-grounded bug candidate compiler for input-only enterprise runs.

This module is intentionally *not* an industry/static bug template runner.  It
uses only files inside ``projects/<project>/input`` (or the copied
``platform_inputs/<project>`` directory) and turns the customer's own PRD,
business rules, API document, OpenAPI and schema into candidate bug hypotheses
and executable probe obligations.

The output is candidate/probe planning, not runtime confirmation.  Hidden
oracle/ground-truth files are never needed and should never be passed here.
"""

import json
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SourceRef:
    file: str
    section: str
    quote: str
    kind: str = "document"


@dataclass
class ApiEndpoint:
    path: str
    method: str
    capability_code: str = ""
    capability: str = ""
    actors: list[str] | None = None
    checks: list[str] | None = None
    failure_statuses: list[str] | None = None
    summary: str = ""
    source_refs: list[SourceRef] | None = None


@dataclass
class BusinessRule:
    code: str
    title: str
    rule_text: str
    source_ref: SourceRef
    source_type: str = ""
    tokens: list[str] | None = None


@dataclass
class GroundedCandidate:
    candidate_id: str
    title: str
    status: str
    risk_type: str
    severity: str
    confidence: float
    endpoint: dict[str, str]
    affected_entities: list[str]
    actors: list[str]
    expected_behavior: str
    suspected_failure_pattern: str
    probe_plan: dict[str, Any]
    execution_policy: str
    required_evidence: list[str]
    source_refs: list[dict[str, str]]
    grounding_basis: dict[str, Any]
    rationale: str
    evidence_source: str = ""  # P0-6: explicit evidence source marker
    rule_category: str = ""    # P0-6: standardized rule category


# ────────────────────────────────────────────────────────────────────────────
# P0-6: Standardized rule category taxonomy (industry-neutral)
# ────────────────────────────────────────────────────────────────────────────

RULE_CATEGORY_TAXONOMY = (
    "FIELD_INVARIANT",           # Field value constraints (range, format, uniqueness)
    "CAUSAL_POSTCONDITION",      # After action X, condition Y must hold
    "STATE_TRANSITION",          # Valid state transitions and guards
    "CONSERVATION",              # Resource conservation (balance, quantity, amount)
    "CROSS_ENTITY_CONSISTENCY",  # Consistency between related entities
    "IDEMPOTENCY",               # Repeated operations produce same result
    "AUTHORIZATION",             # Access control boundaries
    "ISOLATION",                 # Tenant/owner data isolation
    "TEMPORAL",                  # Time-based constraints (expiry, ordering)
    "REFERENTIAL_INTEGRITY",     # FK relationships must be valid
)

# Map internal risk_type to standardized rule category
_RISK_TYPE_TO_RULE_CATEGORY: dict[str, str] = {
    "business_rule_probe": "FIELD_INVARIANT",
    "read_consistency_probe": "CROSS_ENTITY_CONSISTENCY",
    "auth_boundary_probe": "AUTHORIZATION",
    "ownership_scope_probe": "ISOLATION",
    "idempotency_replay_probe": "IDEMPOTENCY",
    "state_transition_probe": "STATE_TRANSITION",
    "conservation_probe": "CONSERVATION",
    "audit_privacy_probe": "AUTHORIZATION",
    "async_external_event_probe": "CAUSAL_POSTCONDITION",
}


def rule_category_for_risk_type(risk_type: str) -> str:
    """Map internal risk_type to standardized rule category."""
    return _RISK_TYPE_TO_RULE_CATEGORY.get(risk_type, "FIELD_INVARIANT")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


_ROLE_ALIASES = {
    "admin": "admin",
    "administrator": "admin",
    "管理员": "admin",
    "approver": "approver",
    "审批人": "approver",
    "审核员": "approver",
    "operator": "operator",
    "ops": "operator",
    "运营": "operator",
    "操作员": "operator",
    "buyer": "buyer",
    "customer": "buyer",
    "user": "buyer",
    "member": "buyer",
    "买家": "buyer",
    "用户": "buyer",
    "会员": "buyer",
    "merchant": "merchant",
    "seller": "merchant",
    "商家": "merchant",
    "customer_service": "customer_service",
    "support": "customer_service",
    "客服": "customer_service",
    "finance": "finance_manager",
    "finance_manager": "finance_manager",
    "财务": "finance_manager",
    "auditor": "auditor",
    "anonymous": "anonymous",
    "guest": "anonymous",
    "游客": "anonymous",
    "匿名": "anonymous",
}

_ROLE_TEXT_PATTERNS = (
    r"\b(?:admin(?:istrator)?|approver|operator|buyer|customer_service|customer|user|member|merchant|seller|finance(?:_manager)?|auditor)\b",
    r"(?:管理员|审批人|审核员|操作员|运营|买家|用户|会员|商家|客服|财务)",
)

_ACTOR_LABELS = {
    "admin": "管理员",
    "approver": "审批人",
    "operator": "运营",
    "buyer": "买家",
    "merchant": "商家",
    "customer_service": "客服",
    "finance_manager": "财务",
    "auditor": "审计",
    "anonymous": "匿名用户",
}

_SEMANTIC_MATCH_STOP_WORDS = {
    "api", "v1", "v2", "v3", "legacy", "resource", "resources", "item", "items",
    "get", "post", "put", "patch", "delete", "create", "read", "update", "remove",
    "request", "response", "endpoint", "operation", "service", "system", "client",
    "admin", "anonymous", "buyer", "customer", "member", "operator", "seller", "user",
    "userid", "user_id", "account", "actor", "role", "token", "bearer", "auth",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _source(file: str, section: str, quote: str, limit: int = 260, kind: str = "document") -> SourceRef:
    q = _clean(quote)
    if len(q) > limit:
        q = q[: limit - 1] + "…"
    return SourceRef(file=file, section=section, quote=q, kind=kind)


def _split_sections(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "document"
    current_lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = line.strip("# ").strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))
    return sections


def _normalize_actor_values(values: list[str], *, limit: int = 12) -> list[str]:
    actors: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").replace("\n", " ")
        for piece in re.split(r"[/,，、|]+", text):
            token = _clean(piece).strip("`\"'[](){}<>.:;。，")
            if not token or len(token) > 40:
                continue
            if any(marker in token for marker in ("##", "###", "{", "}", "|")):
                continue
            key = token.lower().replace("-", "_").replace(" ", "_")
            canonical = _ROLE_ALIASES.get(token) or _ROLE_ALIASES.get(key)
            if not canonical and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{1,31}", key):
                if key in _ROLE_ALIASES:
                    canonical = _ROLE_ALIASES[key]
            if not canonical:
                continue
            if canonical in seen:
                continue
            seen.add(canonical)
            actors.append(canonical)
            if len(actors) >= limit:
                return actors
    return actors


def _actor_variants(values: list[str], *, limit: int = 3) -> list[list[str]]:
    actors = [actor for actor in _normalize_actor_values(values, limit=limit + 2) if actor != "anonymous"]
    variants = [[actor] for actor in actors[:limit]]
    return variants or [[]]


def _actor_title_prefix(actors: list[str]) -> str:
    if len(actors) != 1:
        return ""
    return f"{_ACTOR_LABELS.get(actors[0], actors[0])}"


def _business_groups(text: str, extra_tokens: list[str] | None = None) -> set[str]:
    """Return source-derived semantic terms without an industry taxonomy."""

    values = [str(text or "")] + [str(token or "") for token in (extra_tokens or [])]
    matched: set[str] = set()
    for value in values:
        cleaned = _clean(value)
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", cleaned.lower()):
            pieces = [token] + [part for part in re.split(r"[_-]+", token) if len(part) >= 3]
            for piece in pieces:
                normalized = piece[:-1] if piece.endswith("s") and len(piece) > 4 else piece
                if normalized not in _SEMANTIC_MATCH_STOP_WORDS:
                    matched.add(normalized)
        for token in re.findall(r"[\u4e00-\u9fff]{3,12}", cleaned):
            matched.add(token)
    for token in extra_tokens or []:
        normalized = _clean(str(token or "")).lower()
        if len(normalized) >= 3 and normalized not in _SEMANTIC_MATCH_STOP_WORDS:
            matched.add(normalized)
    return matched


_ENGLISH_STATE_TOKENS = {
    "active",
    "applied",
    "approved",
    "archived",
    "cancelled",
    "canceled",
    "closed",
    "complete",
    "completed",
    "confirmed",
    "created",
    "deleted",
    "delivered",
    "disabled",
    "done",
    "draft",
    "enabled",
    "expired",
    "failed",
    "finished",
    "inactive",
    "init",
    "new",
    "paid",
    "pending",
    "processing",
    "received",
    "refunded",
    "refunding",
    "rejected",
    "returned",
    "returning",
    "settled",
    "shipped",
    "submitted",
    "success",
    "void",
    "wait_return",
}
_CHINESE_STATE_HINTS = (
    "待",
    "已",
    "审核",
    "审批",
    "通过",
    "拒绝",
    "驳回",
    "支付",
    "付款",
    "发货",
    "收货",
    "完成",
    "取消",
    "关闭",
    "退款",
    "退货",
    "归档",
    "成功",
    "失败",
    "处理中",
    "创建",
    "新建",
    "提交",
    "受理",
    "配送",
    "草稿",
    "启用",
    "停用",
)


def _normalize_state_token(value: Any) -> str:
    token = _clean(str(value or "")).strip("`\"'[](){}<>.:;，。")
    if not token or len(token) > 24 or any(ch.isspace() for ch in token):
        return ""
    low = token.lower()
    if any(marker in low for marker in ("http", "www", ".com", "/", "\\", "px", "rem", "em", "%")):
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?", low):
        return ""
    if re.search(r"\d", token) and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}", token):
        return ""
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}", token):
        return token
    if re.fullmatch(r"[a-z][a-z0-9_]{1,23}", low):
        return token if low in _ENGLISH_STATE_TOKENS else ""
    if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", token):
        return token if any(marker in token for marker in _CHINESE_STATE_HINTS) else ""
    return ""


def _sanitize_state_values(values: list[Any], *, limit: int) -> list[str]:
    states: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = _normalize_state_token(raw)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        states.append(token)
        if len(states) >= limit:
            break
    return states


def _contains_state_token(text: str, states: list[str]) -> bool:
    haystack = str(text or "")
    low = haystack.lower()
    for token in states:
        norm = _normalize_state_token(token)
        if not norm:
            continue
        token_low = norm.lower()
        if re.fullmatch(r"[a-z0-9_]+", token_low):
            if re.search(rf"\b{re.escape(token_low)}\b", low):
                return True
            continue
        if norm in haystack:
            return True
    return False


def load_input_documents(input_dir: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(input_dir)).replace("\\", "/")
        if path.suffix.lower() in {".md", ".txt", ".sql", ".yaml", ".yml", ".json"}:
            docs[rel] = _read(path)
    return docs


def parse_roles(prd_text: str, api_text: str) -> list[str]:
    role_tokens: list[str] = []
    for text in (prd_text, api_text):
        if not text:
            continue
        role_tokens.extend(match.group(1) for match in re.finditer(r"`([^`\n]{1,40})`", text))
        for pattern in _ROLE_TEXT_PATTERNS:
            role_tokens.extend(match.group(0) for match in re.finditer(pattern, text, re.I))
    return _normalize_actor_values(role_tokens, limit=12)


def parse_entities(prd_text: str, schema_text: str) -> list[str]:
    entities: list[str] = []
    m = re.search(r"##\s*4\.\s*核心领域对象(?P<body>.*?)(?:\n##\s|\Z)", prd_text or "", re.S)
    if m:
        for item in re.findall(r"^\s*-\s*`?([^`\n]+?)`?\s*$", m.group("body"), re.M):
            item = item.strip(" `。，,.;")
            if item:
                entities.append(item)
    for table in re.findall(r"CREATE\s+TABLE\s+([A-Za-z_][\w]*)", schema_text or "", re.I):
        entities.append(table)
    return sorted(dict.fromkeys(entities))[:40]


def parse_state_machine(prd_text: str) -> dict[str, Any]:
    states: list[str] = []
    terminals: list[str] = []
    m = re.search(r"主状态机[：:]\s*`?([^`\n]+)`?", prd_text or "")
    if m:
        states = [s.strip() for s in re.split(r"→|->|=>", m.group(1)) if s.strip()]
    t = re.search(r"终态[：:]\s*`?([^`\n]+)`?", prd_text or "")
    if t:
        terminals = [s.strip() for s in re.split(r"/|、|,|，|\s+", t.group(1)) if s.strip()]
    return {
        "states": _sanitize_state_values(states, limit=24),
        "terminal_states": _sanitize_state_values(terminals, limit=16),
    }

def parse_prd_grounding_refs(prd_text: str) -> dict[str, list[SourceRef]]:
    """Extract reusable PRD evidence snippets that justify probe generation.

    These refs are not bug templates. They are explicit customer statements such
    as "all interfaces must validate ownership" or "stock must never be below 0".
    A candidate must cite at least one such customer-grounding ref or a business
    rule/risk-surface ref before it is emitted in strict mode.
    """
    refs: dict[str, list[SourceRef]] = defaultdict(list)
    for title, body in _split_sections(prd_text or ""):
        section_text = _clean(body)
        bullets = re.findall(r"^\s*-\s*(.+)$", body, re.M) or ([section_text] if section_text else [])
        for raw in bullets:
            text = _clean(raw)
            if not text:
                continue
            low = text.lower()
            if re.search(r"登录态|角色权限|数据归属|跨租户|租户|组织范围|最小权限|权限控制|Bearer", text, re.I):
                refs["auth"].append(_source("PRD.md", title, text, kind="prd_auth_scope"))
                refs["ownership"].append(_source("PRD.md", title, text, kind="prd_auth_scope"))
            if re.search(r"状态机|终态|状态变更|副作用|撤回|驳回|取消|退款|归档|恢复|重放", text, re.I):
                refs["state"].append(_source("PRD.md", title, text, kind="prd_lifecycle"))
            if re.search(r"金额|库存|积分|额度|容量|流水|汇总|守恒|不得小于\s*0|对账", text, re.I):
                refs["conservation"].append(_source("PRD.md", title, text, kind="prd_invariant"))
            if re.search(r"Idempotency-Key|幂等|重复提交|消息 ID|第三方事件号|业务单号|只能产生一次", text, re.I):
                refs["idempotency"].append(_source("PRD.md", title, text, kind="prd_idempotency"))
                refs["async"].append(_source("PRD.md", title, text, kind="prd_idempotency"))
            if re.search(r"审计|隐私|脱敏|敏感字段|导入导出|报表", text, re.I):
                refs["audit"].append(_source("PRD.md", title, text, kind="prd_audit_privacy"))
    return {k: v[:6] for k, v in refs.items()}


def parse_api_global_constraint_refs(api_text: str) -> dict[str, list[SourceRef]]:
    refs: dict[str, list[SourceRef]] = defaultdict(list)
    m = re.search(r"##\s*通用约定(?P<body>.*?)(?:\n##\s|\Z)", api_text or "", re.S)
    body = m.group("body") if m else (api_text or "")[:1200]
    for raw in re.findall(r"^\s*-\s*(.+)$", body, re.M):
        text = _clean(raw)
        if not text:
            continue
        if re.search(r"Bearer Token|登录|鉴权|认证|权限", text, re.I):
            refs["auth"].append(_source("API.md", "通用约定", text, kind="api_global_auth"))
        if re.search(r"租户|组织|权限范围|数据范围|tenant", text, re.I):
            refs["ownership"].append(_source("API.md", "通用约定", text, kind="api_global_scope"))
        if re.search(r"Idempotency-Key|业务唯一键|幂等|重复", text, re.I):
            refs["idempotency"].append(_source("API.md", "通用约定", text, kind="api_global_idempotency"))
        if re.search(r"错误响应|trace_id|details", text, re.I):
            refs["api_contract"].append(_source("API.md", "通用约定", text, kind="api_global_contract"))
    return {k: v[:6] for k, v in refs.items()}


def parse_risk_surface_refs(risk_text: str) -> dict[str, list[SourceRef]]:
    refs: dict[str, list[SourceRef]] = defaultdict(list)
    for raw in re.findall(r"^\s*-\s*(C\d{2}[^\n]+)$", risk_text or "", re.M):
        code_m = re.match(r"(C\d{2})", raw)
        if not code_m:
            continue
        refs[code_m.group(1)].append(_source("RISK_SURFACE_MODEL.md", code_m.group(1), raw, kind="risk_surface"))
    return refs


def _risk_support_keys(risk_type: str) -> list[str]:
    return {
        "business_rule_probe": ["business_rule"],
        "read_consistency_probe": ["business_rule"],
        "auth_boundary_probe": ["auth", "api_contract"],
        "ownership_scope_probe": ["ownership", "auth"],
        "idempotency_replay_probe": ["idempotency"],
        "state_transition_probe": ["state"],
        "conservation_probe": ["conservation"],
        "audit_privacy_probe": ["audit", "ownership", "auth"],
        "async_external_event_probe": ["async", "idempotency"],
    }.get(risk_type, [])


def _refs_by_kind(refs: list[SourceRef]) -> dict[str, int]:
    counts: Counter[str] = Counter(r.kind for r in refs)
    return dict(sorted(counts.items()))


def parse_business_rules(text: str) -> list[BusinessRule]:
    rules: list[BusinessRule] = []
    for title, body in _split_sections(text or ""):
        m = re.match(r"(C\d{2})\s+(.+)", title.strip())
        if not m:
            continue
        code, rule_title = m.group(1), m.group(2).strip()
        bullet_texts = re.findall(r"^\s*-\s*(?:规则\s*\d+\s*[:：]\s*)?(.+)$", body, re.M)
        # Deduplicate repeated benchmark lines; real customer docs often repeat rules in many places.
        for rule_text in dict.fromkeys(_clean(b) for b in bullet_texts if _clean(b)):
            rules.append(BusinessRule(
                code=code,
                title=rule_title,
                rule_text=rule_text,
                source_ref=_source("BUSINESS_RULES.md", title, rule_text, kind="business_rule"),
                source_type="business_rule_document",
                tokens=[],
            ))
    return rules


def _dedupe_strings(values: list[str], *, limit: int | None = None) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = _clean(str(raw or ""))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
        if limit and len(items) >= limit:
            break
    return items


def _merge_state_models(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "states": _sanitize_state_values(list(base.get("states") or []) + list(extra.get("states") or []), limit=24),
        "terminal_states": _sanitize_state_values(list(base.get("terminal_states") or []) + list(extra.get("terminal_states") or []), limit=16),
    }


def _knowledge_asset_roles(asset: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    for row in asset.get("roles") or []:
        if not isinstance(row, dict):
            continue
        roles.append(str(row.get("role") or row.get("name") or row.get("title") or ""))
    return _normalize_actor_values(roles, limit=12)


def _knowledge_asset_entities(asset: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for row in asset.get("business_objects") or []:
        if isinstance(row, dict):
            names.append(str(row.get("object") or row.get("name") or ""))
    for row in asset.get("data_tables") or []:
        if isinstance(row, dict):
            names.append(str(row.get("name") or row.get("table") or ""))
    for row in asset.get("field_dictionary") or []:
        if isinstance(row, dict):
            names.append(str(row.get("table") or ""))
    return _dedupe_strings(names, limit=40)


def _knowledge_asset_state_model(asset: dict[str, Any]) -> dict[str, Any]:
    states: list[str] = []
    terminals: list[str] = []
    for row in asset.get("state_machines") or []:
        if not isinstance(row, dict):
            continue
        states.extend([str(x) for x in (row.get("states") or []) if str(x).strip()])
        transitions = row.get("transitions") or []
        for item in transitions:
            if isinstance(item, dict):
                states.extend([str(item.get("from") or ""), str(item.get("to") or "")])
        for token in row.get("terminal_states") or []:
            if str(token).strip():
                terminals.append(str(token))
        for token in row.get("states") or []:
            low = str(token).strip().lower()
            if low in {"completed", "complete", "cancelled", "canceled", "closed", "archived", "refunded", "done", "finished", "终态", "已完成", "已取消", "已关闭", "已归档"}:
                terminals.append(str(token))
    return {
        "states": _sanitize_state_values(states, limit=24),
        "terminal_states": _sanitize_state_values(terminals, limit=16),
    }


def _knowledge_rule_code(row: dict[str, Any], index: int) -> str:
    raw = str(row.get("rule_id") or row.get("source_rule_id") or row.get("risk_id") or "").strip()
    if raw:
        return raw.replace(":", "_").replace("/", "_")[:48]
    return f"KA_{index:03d}"


def _knowledge_asset_rules(asset: dict[str, Any]) -> list[BusinessRule]:
    rules: list[BusinessRule] = []
    for index, row in enumerate(asset.get("rule_library") or [], 1):
        if not isinstance(row, dict):
            continue
        statement = _clean(str(row.get("statement") or row.get("expected") or row.get("title") or ""))
        if not statement:
            continue
        source_id = str(row.get("source_id") or "knowledge_asset")
        title = _clean(str(row.get("rule_type") or row.get("risk_type") or "knowledge_rule")) or "knowledge_rule"
        rules.append(BusinessRule(
            code=_knowledge_rule_code(row, index),
            title=title,
            rule_text=statement,
            source_ref=_source(f"knowledge_asset:{source_id}", title, statement, kind="knowledge_rule"),
            source_type=str(row.get("source_type") or ""),
            tokens=[str(token) for token in (row.get("tokens") or []) if str(token).strip()],
        ))
    return rules


def _knowledge_asset_historical_risk_rules(asset: dict[str, Any]) -> list[BusinessRule]:
    rules: list[BusinessRule] = []
    for row in asset.get("risk_domains") or []:
        if not isinstance(row, dict):
            continue
        source_type = str(row.get("source_type") or "")
        oracle_family = str(row.get("oracle_family") or "")
        if source_type != "historical_bug" and oracle_family != "historical_regression_oracle":
            continue
        statement = _clean(str(row.get("expected") or row.get("title") or ""))
        if not statement:
            continue
        source_id = str(row.get("source_id") or "knowledge_asset")
        rules.append(BusinessRule(
            code=_knowledge_rule_code(row, len(rules) + 1),
            title=_clean(str(row.get("risk_type") or row.get("oracle_family") or "historical_risk")),
            rule_text=statement,
            source_ref=_source(f"knowledge_asset:{source_id}", str(row.get("risk_id") or row.get("risk_type") or "historical_risk"), statement, kind="knowledge_risk"),
            source_type=source_type or "historical_bug",
            tokens=[str(token) for token in (row.get("tokens") or []) if str(token).strip()],
        ))
    return rules


def _knowledge_asset_endpoints(asset: dict[str, Any]) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    for row in asset.get("interfaces") or []:
        if not isinstance(row, dict):
            continue
        method = str(row.get("method") or "GET").upper()
        path = str(row.get("path") or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        summary = _clean(str(row.get("summary") or row.get("title") or row.get("operation_id") or f"{method} {path}"))
        parameters = [str(x) for x in (row.get("parameters") or []) if str(x).strip()]
        token_space = " ".join(
            [path, summary, " ".join(parameters), " ".join(str(x) for x in (row.get("tags") or [])), " ".join(str(x) for x in (row.get("tokens") or []))]
        )
        checks: list[str] = []
        if re.search(r"auth|authorization|bearer|token|login|permission|权限|鉴权|认证|管理员|admin|登录态|未登录|角色", token_space, re.I):
            checks.append("auth")
        if re.search(r"tenant|org|owner|ownership|scope|租户|组织|归属|所有者|本人|自己的|仅本人|仅管理员|管理员可|管理员可以|对象归属|越权", token_space, re.I):
            checks.extend(["tenant", "object_owner"])
        if re.search(r"idempotency|幂等|duplicate|retry|重试|external_event_id|event_id|callback|webhook|notify|replay|回调|重放|重复提交|重复支付|重复退款|支付回调|payment|payments|refund|refunds|charge|capture|settle|支付|退款", token_space, re.I):
            checks.append("idempotency")
        if re.search(r"state|transition|approve|cancel|refund|archive|状态|流转|审批|撤回|取消|退款|归档", token_space, re.I):
            checks.append("state")
        if re.search(r"audit|privacy|export|import|report|admin|config|隐私|审计|导出|导入|配置", token_space, re.I):
            checks.append("audit")
        source_id = str(row.get("source_id") or "knowledge_asset")
        section = str(row.get("interface_id") or f"{method} {path}")
        quote = summary or f"{method} {path}"
        endpoints.append(ApiEndpoint(
            path=path,
            method=method,
            capability_code="",
            capability=summary,
            actors=_normalize_actor_values([str(x) for x in (row.get("actors") or [])], limit=6),
            checks=sorted(dict.fromkeys(checks)),
            failure_statuses=[],
            summary=summary,
            source_refs=[_source(f"knowledge_asset:{source_id}", section, quote, kind="knowledge_interface")],
        ))
    return endpoints


def _knowledge_row_to_candidate_risks(row: dict[str, Any], text: str) -> list[str]:
    low = f"{str(row.get('risk_type') or '')} {str(row.get('rule_type') or '')} {text}".lower()
    risks: list[str] = []
    if re.search(r"\bbusiness_rule\b|业务规则", low):
        risks.append("business_rule_probe")
    if re.search(r"list|search|query|page|pagination|filter|sort|列表|搜索|查询|分页|排序|过滤", low):
        risks.append("read_consistency_probe")
    if re.search(r"permission|auth|authorization|登录|鉴权|认证", low):
        risks.append("auth_boundary_probe")
    if re.search(r"tenant|scope|owner|ownership|租户|归属|组织|越权", low):
        risks.append("ownership_scope_probe")
    if re.search(r"async_event|callback|webhook|event|message|notify|queue|sms|back[_ -]?in[_ -]?stock|restock|inventory[_ -]?sync|inventory[_ -]?restore|回调|事件|消息|通知|短信|异步|到货提醒|补货提醒|库存同步|库存恢复|恢复库存|库存回补", low):
        risks.append("async_external_event_probe")
    if re.search(r"idempotency|duplicate|replay|retry|幂等|重复|重试", low):
        risks.extend(["idempotency_replay_probe", "async_external_event_probe"])
    if re.search(r"state|transition|workflow|status|状态|流转|审批|终态|取消|退款|归档", low):
        risks.append("state_transition_probe")
    if re.search(r"conservation|reconciliation|balance|ledger|inventory|amount|quota|fund|库存|金额|余额|账本|额度|积分|对账", low):
        risks.append("conservation_probe")
    if re.search(r"audit|privacy|sensitive|export|import|审计|隐私|敏感|导出|导入", low):
        risks.append("audit_privacy_probe")
    return _dedupe_strings(risks, limit=6)


def _knowledge_asset_support_refs(asset: dict[str, Any]) -> dict[str, list[SourceRef]]:
    refs: dict[str, list[SourceRef]] = defaultdict(list)
    for collection_name in ("rule_library", "risk_domains"):
        for row in asset.get(collection_name) or []:
            if not isinstance(row, dict):
                continue
            quote = _clean(str(row.get("statement") or row.get("expected") or row.get("title") or ""))
            if not quote:
                continue
            source_id = str(row.get("source_id") or row.get("source_rule_id") or "knowledge_asset")
            section = str(row.get("rule_id") or row.get("risk_id") or row.get("rule_type") or row.get("risk_type") or collection_name)
            kind = "knowledge_rule" if collection_name == "rule_library" else "knowledge_risk"
            ref = _source(f"knowledge_asset:{source_id}", section, quote, kind=kind)
            for risk_type in _knowledge_row_to_candidate_risks(row, quote):
                refs[risk_type].append(ref)
    return {key: value[:8] for key, value in refs.items()}


def _infer_risk_types_from_text(text: str) -> list[str]:
    low = _clean(text).lower()
    risks: list[str] = []
    if re.search(r"\bbusiness_rule\b|业务规则", low):
        risks.append("business_rule_probe")
    if re.search(r"list|search|query|page|pagination|filter|sort|列表|搜索|查询|分页|排序|过滤", low):
        risks.append("read_consistency_probe")
    if re.search(r"permission|auth|authorization|登录|鉴权|认证|管理员|admin|未登录|token|bearer", low):
        risks.append("auth_boundary_probe")
    if re.search(r"tenant|scope|owner|ownership|租户|归属|组织|越权|所有者|自己的|本人", low):
        risks.append("ownership_scope_probe")
    if re.search(r"async_event|callback|webhook|event|message|notify|queue|sms|back[_ -]?in[_ -]?stock|restock|inventory[_ -]?sync|inventory[_ -]?restore|回调|支付回调|事件|消息|通知|短信|异步|到货提醒|补货提醒|库存同步|库存恢复|恢复库存|库存回补", low):
        risks.append("async_external_event_probe")
    if re.search(r"idempotency|duplicate|replay|retry|幂等|重复|重试", low):
        risks.extend(["idempotency_replay_probe", "async_external_event_probe"])
    if re.search(r"state|transition|workflow|status|状态|流转|审批|终态|取消|退款|归档", low):
        risks.append("state_transition_probe")
    if re.search(r"conservation|reconciliation|balance|ledger|inventory|amount|quota|fund|库存|金额|余额|账本|额度|积分|对账|应付金额", low):
        risks.append("conservation_probe")
    if re.search(r"audit|privacy|sensitive|export|import|审计|隐私|敏感|导出|导入|日志|密钥", low):
        risks.append("audit_privacy_probe")
    return _dedupe_strings(risks, limit=8)


def _zh_overlap_terms(text: str) -> set[str]:
    terms: set[str] = set()
    stop_words = {
        "返回", "请求", "接口", "当前", "创建", "获取", "用户", "管理员",
        "客户", "角色", "令牌", "认证", "授权", "系统", "服务",
    }
    for token in re.findall(r"[\u4e00-\u9fff]{2,12}", _clean(text)):
        if token in stop_words:
            continue
        terms.add(token)
        if len(token) > 2:
            for size in (2, 3, 4):
                for index in range(0, len(token) - size + 1):
                    piece = token[index:index + size]
                    if piece not in stop_words:
                        terms.add(piece)
    return terms


def _endpoint_rule_matches(ep: ApiEndpoint, rules: list[BusinessRule]) -> dict[str, list[str]]:
    endpoint_text = _clean(" ".join([ep.path, ep.capability or "", ep.summary or ""]))
    endpoint_low = endpoint_text.lower()
    path_terms = {
        token.lower()
        for token in re.findall(r"[A-Za-z_]{3,}", ep.path)
        if token.lower() not in {"api", "get", "post", "put", "patch", "delete", "head", "options", "admin", "auth"}
    }
    zh_terms = _zh_overlap_terms(endpoint_text)
    endpoint_groups = _business_groups(endpoint_text)
    matches: dict[str, list[str]] = defaultdict(list)
    for rule in rules:
        rule_text = _clean(f"{rule.title} {rule.rule_text}")
        rule_low = rule_text.lower()
        rule_groups = _business_groups(rule_text, rule.tokens or [])
        related = False
        if any(term in rule_low for term in path_terms):
            related = True
        if not related and any(term in rule.rule_text for term in zh_terms):
            related = True
        if not related and endpoint_low and endpoint_low in rule_low:
            related = True
        if not related and "business_rule" in rule_low and endpoint_groups and rule_groups and endpoint_groups.intersection(rule_groups):
            related = True
        if (
            not related
            and endpoint_groups
            and rule_groups
            and endpoint_groups.intersection(rule_groups)
            and re.search(r"tenant|scope|owner|ownership|租户|归属|组织|越权|所有者|自己的|本人|仅本人", rule_low, re.I)
        ):
            related = True
        if (
            not related
            and endpoint_groups
            and rule_groups
            and endpoint_groups.intersection(rule_groups)
            and re.search(
                r"callback|webhook|events?|message|notify|third|signature|nonce|timestamp|external_event_id|idempotency|replay|retry|back[_ -]?in[_ -]?stock|restock|inventory[_ -]?sync|inventory[_ -]?restore|幂等|回调|验签|签名|重试|重放|消息|通知|第三方事件号|到货提醒|补货提醒|库存同步|库存恢复|恢复库存|库存回补|settlement|审批回调|支付回调",
                rule_low,
                re.I,
            )
        ):
            related = True
        if not related and str(rule.source_type or "") == "historical_bug" and endpoint_groups and rule_groups and endpoint_groups.intersection(rule_groups):
            related = True
        if not related and str(rule.source_type or "") == "historical_bug":
            historical_tokens = [str(token) for token in (rule.tokens or []) if str(token).strip()]
            if endpoint_groups and _business_groups(" ".join(historical_tokens), historical_tokens).intersection(endpoint_groups):
                related = True
        if not related:
            continue
        for risk_type in _infer_risk_types_from_text(f"{rule.code} {rule_text}"):
            matches[risk_type].append(rule.code)
    return {
        risk_type: _dedupe_strings(codes, limit=8)
        for risk_type, codes in matches.items()
        if codes
    }


def parse_api_md(text: str) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    # Matches sections like: ### 3. /api/v1/ecommerce/订单/{id}
    matches = list(re.finditer(r"^###\s*(?:\d+\.\s*)?([^\n]+)\n(?P<body>.*?)(?=^###\s|\Z)", text or "", re.M | re.S))
    for match in matches:
        path = match.group(1).strip()
        body = match.group("body") or ""
        if not path.startswith("/"):
            continue
        capability = ""
        cap_line = re.search(r"关联能力[：:]\s*([^\n]+)", body)
        if cap_line:
            capability = _clean(cap_line.group(1).strip("。."))
        actors: list[str] = []
        actor_line = re.search(r"请求方[：:]\s*([^\n]+)", body)
        if actor_line:
            actors = [a.strip(" `。./") for a in re.split(r"/|、|,|，", actor_line.group(1)) if a.strip(" `。./")]
        checks: list[str] = []
        checks_line = re.search(r"必须校验[：:]\s*([^\n]+)", body)
        if checks_line:
            checks = [c.strip(" `。.") for c in re.split(r"、|,|，|/", checks_line.group(1)) if c.strip(" `。.")]
        statuses: list[str] = []
        status_line = re.search(r"失败状态码[：:]\s*([^\n]+)", body)
        if status_line:
            statuses = [s for s in re.findall(r"\b\d{3}\b", status_line.group(1))]
        # API.md may not say method. Infer a safe default from endpoint semantics; OpenAPI will override/augment.
        method = "POST" if re.search(r"/(apply|bind|approve|transition|archive|deduct|quote|submit|commit|evaluate|import|run|callback|process|sync|export|redeem|settle|forms)(?:/|$)", path, re.I) else "GET"
        cnum = re.search(r"/(\d{3})(?:/|$)|_(c\d{2})_|\b(C\d{2})\b", path + " " + body, re.I)
        capability_code = ""
        if cnum:
            if cnum.group(1):
                capability_code = f"C{int(cnum.group(1)):02d}"
            elif cnum.group(2):
                capability_code = cnum.group(2).upper()
            elif cnum.group(3):
                capability_code = cnum.group(3).upper()
        endpoints.append(ApiEndpoint(
            path=path,
            method=method,
            capability_code=capability_code,
            capability=capability,
            actors=_normalize_actor_values(actors, limit=6),
            checks=checks,
            failure_statuses=statuses,
            summary=capability,
            source_refs=[_source("API.md", path, match.group(0), kind="endpoint_contract")],
        ))
    return endpoints


def _load_openapi(input_dir: Path) -> dict[str, Any]:
    for name in ("openapi.json", "swagger.json"):
        p = input_dir / name
        if p.exists():
            try:
                return json.loads(_read(p) or "{}")
            except Exception:
                return {}
    for name in ("openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml"):
        p = input_dir / name
        if p.exists():
            try:
                return yaml.safe_load(_read(p) or "{}") or {}
            except Exception:
                return {}
    return {}


def parse_openapi_endpoints(input_dir: Path) -> list[ApiEndpoint]:
    spec = _load_openapi(input_dir)
    out: list[ApiEndpoint] = []
    paths = spec.get("paths") if isinstance(spec, dict) else None
    if not isinstance(paths, dict):
        return out
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if str(method).lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            op = op or {}
            summary = _clean(str(op.get("summary") or op.get("description") or ""))
            statuses = sorted([str(k) for k in (op.get("responses") or {}).keys() if re.fullmatch(r"\d{3}", str(k))])
            checks: list[str] = []
            if op.get("security"):
                checks.append("auth")
            for param in op.get("parameters") or []:
                name = str(param.get("name") or "").lower()
                if "tenant" in name:
                    checks.append("tenant")
                if "idempotency" in name:
                    checks.append("idempotency")
            cnum = re.search(r"_c(\d{2})_|\bC(\d{2})\b|/(\d{3})(?:/|$)", " ".join([str(op.get("operationId") or ""), summary, path]), re.I)
            capability_code = f"C{int(next(g for g in cnum.groups() if g)):02d}" if cnum else ""
            out.append(ApiEndpoint(
                path=str(path),
                method=str(method).upper(),
                capability_code=capability_code,
                capability=summary,
                actors=[],
                checks=sorted(dict.fromkeys(checks)),
                failure_statuses=statuses,
                summary=summary,
                source_refs=[_source("openapi.yaml", f"{method.upper()} {path}", summary or f"{method.upper()} {path}", kind="endpoint_contract")],
            ))
    return out


def _canonical_api_suffix(path: str) -> str:
    p = (path or "").strip()
    # Treat /api/v1/<domain>/foo and /foo as the same business endpoint.
    p = re.sub(r"^/api/v\d+(?:/[^/]+)?", "", p)
    return p or (path or "")


def merge_endpoints(api_md: list[ApiEndpoint], openapi: list[ApiEndpoint]) -> list[ApiEndpoint]:
    # Prefer API.md paths because they usually contain the enterprise base prefix
    # (/api/v1/<domain>). Merge OpenAPI details into the matching API.md
    # endpoint by canonical suffix instead of emitting duplicates. Do not merge
    # across different HTTP methods: same-path GET/POST pairs often represent
    # distinct read/write capabilities, and collapsing them silently removes the
    # write-side bug surface.
    merged: dict[tuple[str, str], ApiEndpoint] = {}
    suffix_index: dict[tuple[str, str], tuple[str, str]] = {}

    def absorb(target: ApiEndpoint, src: ApiEndpoint) -> None:
        target.capability_code = target.capability_code or src.capability_code
        target.capability = target.capability or src.capability
        target.summary = target.summary or src.summary
        target.actors = _normalize_actor_values(list(target.actors or []) + list(src.actors or []), limit=8)
        target.checks = sorted(dict.fromkeys((target.checks or []) + (src.checks or [])))
        target.failure_statuses = sorted(dict.fromkeys((target.failure_statuses or []) + (src.failure_statuses or [])))
        refs = list((target.source_refs or []) + (src.source_refs or []))
        seen_refs: set[tuple[str, str, str]] = set()
        deduped: list[SourceRef] = []
        for ref in refs:
            key = (ref.file, ref.section, ref.quote)
            if key not in seen_refs:
                seen_refs.add(key)
                deduped.append(ref)
        target.source_refs = deduped

    for ep in api_md:
        ep.method = ep.method.upper()
        ep.actors = list(ep.actors or [])
        ep.checks = list(ep.checks or [])
        ep.failure_statuses = list(ep.failure_statuses or [])
        ep.source_refs = list(ep.source_refs or [])
        key = (ep.method, ep.path)
        merged[key] = ep
        suffix = _canonical_api_suffix(ep.path)
        suffix_index[(ep.method, suffix)] = key

    for ep in openapi:
        ep.method = ep.method.upper()
        ep.actors = list(ep.actors or [])
        ep.checks = list(ep.checks or [])
        ep.failure_statuses = list(ep.failure_statuses or [])
        ep.source_refs = list(ep.source_refs or [])
        suffix = _canonical_api_suffix(ep.path)
        suffix_key = (ep.method, suffix)
        target_key = suffix_index.get(suffix_key)
        if target_key and target_key in merged:
            absorb(merged[target_key], ep)
            continue
        key = (ep.method, ep.path)
        merged[key] = ep
        suffix_index[suffix_key] = key

    return sorted(merged.values(), key=lambda e: (_canonical_api_suffix(e.path), e.path, e.method))


def _is_write(method: str) -> bool:
    return method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


def _endpoint_entity(path: str, entities: list[str]) -> list[str]:
    hits: list[str] = []
    low = path.lower()
    for entity in entities:
        e_low = entity.lower()
        if entity in path or e_low in low or e_low.rstrip("s") in low:
            hits.append(entity)
    return hits[:6]


def _rule_lookup(rules: list[BusinessRule]) -> dict[str, list[BusinessRule]]:
    by: dict[str, list[BusinessRule]] = defaultdict(list)
    for rule in rules:
        by[rule.code].append(rule)
    return by


def _rule_quotes(rules: list[BusinessRule], code: str, limit: int = 2) -> list[SourceRef]:
    return [r.source_ref for r in rules if r.code == code][:limit]


def _has_check(ep: ApiEndpoint, check: str) -> bool:
    return check.lower() in {c.lower() for c in (ep.checks or [])}


def _signature_parts(values: list[Any], *, limit: int = 8) -> tuple[str, ...]:
    tokens = sorted({
        _clean(str(value or "")).lower()
        for value in values
        if _clean(str(value or ""))
    })
    return tuple(tokens[:limit])


def _probe_signature_parts(probe: dict[str, Any]) -> tuple[str, ...]:
    values: list[Any] = []
    for key in (
        "headers",
        "negative_headers",
        "mutations",
        "expected_status",
        "terminal_states",
        "sensitive_fields",
        "oracle",
    ):
        raw = probe.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif raw not in (None, ""):
            values.append(raw)
    return _signature_parts(values, limit=12)


def _candidate_dedupe_key(
    ep: ApiEndpoint,
    risk_type: str,
    *,
    actors: list[str] | None = None,
    rule_codes: list[str] | None = None,
    probe: dict[str, Any] | None = None,
    title: str = "",
    expected: str = "",
    failure: str = "",
) -> tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    candidate_actors = _signature_parts(list(actors or ep.actors or []), limit=6)
    scenario_parts = _signature_parts([title, expected, failure], limit=6)
    rule_parts = _signature_parts(list(rule_codes or []), limit=8)
    probe_parts = _probe_signature_parts(probe or {})
    return (
        ep.method.upper(),
        ep.path,
        risk_type,
        candidate_actors,
        scenario_parts + rule_parts,
        probe_parts,
    )


def _capability_code_from_endpoint(ep: ApiEndpoint) -> str:
    if ep.capability_code:
        return ep.capability_code
    path_low = ep.path.lower()
    if "search?q=" in path_low:
        return "C21"
    if "list" in path_low and "page_size" in path_low:
        return "C28"
    if "search?keyword" in path_low or path_low.rstrip("/").endswith("/search"):
        return "C30"
    m = re.search(r"/(\d{3})(?:/|$)", ep.path)
    return f"C{int(m.group(1)):02d}" if m else ""


def _resolve_candidate_limit(max_candidates: int | None, *, endpoint_count: int, role_count: int = 0) -> int:
    if isinstance(max_candidates, int) and max_candidates > 0:
        return max_candidates
    raw_env = (os.environ.get("QUALIBUG_INPUT_ONLY_MAX_CANDIDATES") or "").strip()
    if raw_env:
        return max(1, int(raw_env))
    # Default blind/input-only candidate volume must scale with both API surface
    # and grounded actor variants; otherwise role-level expansion gets silently
    # truncated and lower-priority risk families disappear from the report.
    multiplier = 36 if role_count >= 8 else 30 if role_count >= 4 else 4 if role_count >= 2 else 3
    return max(180, min(5000, endpoint_count * multiplier))


def compile_grounded_candidates(input_dir: str | Path, *, project_id: str = "", max_candidates: int | None = None, knowledge_asset: dict[str, Any] | None = None) -> dict[str, Any]:
    input_path = Path(input_dir).resolve()
    docs = load_input_documents(input_path)
    prd = docs.get("PRD.md", "") or docs.get("prd.md", "")
    api_md = docs.get("API.md", "") or docs.get("api.md", "")
    rules_text = docs.get("BUSINESS_RULES.md", "") or docs.get("business_rules.md", "")
    schema_text = docs.get("schema.sql", "") or docs.get("DATABASE_DESIGN.md", "")
    risk_text = docs.get("RISK_SURFACE_MODEL.md", "")
    knowledge_asset = knowledge_asset if isinstance(knowledge_asset, dict) else {}

    roles = [
        actor
        for actor in _normalize_actor_values(parse_roles(prd, api_md) + _knowledge_asset_roles(knowledge_asset), limit=12)
        if actor != "anonymous"
    ]
    entities = _dedupe_strings(parse_entities(prd, schema_text) + _knowledge_asset_entities(knowledge_asset), limit=40)
    state_model = _merge_state_models(parse_state_machine(prd), _knowledge_asset_state_model(knowledge_asset))
    rules = parse_business_rules(rules_text) + _knowledge_asset_rules(knowledge_asset) + _knowledge_asset_historical_risk_rules(knowledge_asset)
    endpoints = merge_endpoints(parse_api_md(api_md), parse_openapi_endpoints(input_path))
    knowledge_endpoints = _knowledge_asset_endpoints(knowledge_asset)
    if knowledge_endpoints:
        endpoints = merge_endpoints(endpoints, knowledge_endpoints)
    by_rule = _rule_lookup(rules)
    prd_refs = parse_prd_grounding_refs(prd)
    api_global_refs = parse_api_global_constraint_refs(api_md)
    risk_refs_by_code = parse_risk_surface_refs(risk_text)
    knowledge_support_refs = _knowledge_asset_support_refs(knowledge_asset)
    strict_document_grounding = os.environ.get("QUALIBUG_STRICT_DOCUMENT_GROUNDING", "1") != "0"

    candidates: list[GroundedCandidate] = []
    seen: set[tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = set()
    discarded_ungrounded_count = 0

    def add(
        ep: ApiEndpoint,
        risk_type: str,
        title: str,
        expected: str,
        failure: str,
        probe: dict[str, Any],
        policy: str,
        required_evidence: list[str],
        rule_codes: list[str],
        severity: str = "P2",
        confidence: float = 0.62,
        candidate_actors: list[str] | None = None,
    ) -> None:
        nonlocal discarded_ungrounded_count
        actor_set = _normalize_actor_values(list(candidate_actors or ep.actors or roles[:4]), limit=4)
        if not actor_set and risk_type == "auth_boundary_probe":
            actor_set = ["anonymous"]
        if not actor_set:
            actor_set = _normalize_actor_values(roles[:3], limit=3)
        key = _candidate_dedupe_key(
            ep,
            risk_type,
            actors=actor_set,
            rule_codes=rule_codes,
            probe=probe,
            title=title,
            expected=expected,
            failure=failure,
        )
        if key in seen:
            return
        seen.add(key)

        endpoint_refs: list[SourceRef] = list(ep.source_refs or [])[:3]
        support_refs: list[SourceRef] = []
        for code in rule_codes:
            support_refs.extend(_rule_quotes(rules, code, limit=2))
            support_refs.extend((risk_refs_by_code.get(code) or [])[:1])
        for support_key in _risk_support_keys(risk_type):
            support_refs.extend((prd_refs.get(support_key) or [])[:2])
            support_refs.extend((api_global_refs.get(support_key) or [])[:2])
        support_refs.extend((knowledge_support_refs.get(risk_type) or [])[:3])

        seen_ref_keys: set[tuple[str, str, str, str]] = set()
        deduped_endpoint_refs: list[SourceRef] = []
        deduped_support_refs: list[SourceRef] = []
        for bucket, target in ((endpoint_refs, deduped_endpoint_refs), (support_refs, deduped_support_refs)):
            for ref in bucket:
                key_ref = (ref.file, ref.section, ref.quote, ref.kind)
                if key_ref not in seen_ref_keys:
                    seen_ref_keys.add(key_ref)
                    target.append(ref)

        # Strict document-grounded mode: every candidate must cite the endpoint
        # contract AND at least one customer requirement/business rule/risk/API
        # convention.  This prevents fallback to industry/static templates.
        if strict_document_grounding and (not deduped_endpoint_refs or not deduped_support_refs):
            discarded_ungrounded_count += 1
            return

        source_refs = (deduped_endpoint_refs[:3] + deduped_support_refs[:8])
        # Confidence is document-grounding strength, not bug certainty.
        doc_sources = len({s.file for s in source_refs})
        support_kinds = {s.kind for s in deduped_support_refs}
        adj_conf = min(0.88, confidence + 0.04 * max(0, doc_sources - 1) + 0.02 * max(0, len(support_kinds) - 1))
        cid = f"GIC-{len(candidates)+1:04d}"
        candidates.append(GroundedCandidate(
            candidate_id=cid,
            title=title,
            status="document_derived_candidate",
            risk_type=risk_type,
            severity=severity,
            confidence=round(adj_conf, 2),
            endpoint={"method": ep.method.upper(), "path": ep.path, "capability_code": _capability_code_from_endpoint(ep), "capability": ep.capability or ep.summary or ""},
            affected_entities=_endpoint_entity(ep.path + " " + (ep.capability or ""), entities) or entities[:4],
            actors=actor_set,
            expected_behavior=expected,
            suspected_failure_pattern=failure,
            probe_plan=probe,
            execution_policy=policy,
            required_evidence=required_evidence,
            source_refs=[asdict(s) for s in source_refs],
            grounding_basis={
                "strict_document_grounding": strict_document_grounding,
                "endpoint_contract_refs": len(deduped_endpoint_refs),
                "supporting_requirement_refs": len(deduped_support_refs),
                "support_kinds": _refs_by_kind(deduped_support_refs),
                "rule_codes": sorted(dict.fromkeys([c for c in rule_codes if c])),
                "generation_reason": "endpoint_contract_plus_customer_requirement",
            },
            rationale="该候选必须同时引用 input 中的接口契约和客户需求/业务规则/风险面；未读取 oracle/ground_truth/BUG_MATRIX，未使用行业静态模板。",
            evidence_source=f"document_grounded:{risk_type}",
            rule_category=rule_category_for_risk_type(risk_type),
        ))

    for ep in endpoints:
        code = _capability_code_from_endpoint(ep)
        checks = {c.lower() for c in (ep.checks or [])}
        path_low = ep.path.lower()
        capability_text = ep.capability or ep.summary or ""
        cap_low = capability_text.lower()
        combined = f"{path_low} {cap_low}"
        endpoint_text = _clean(" ".join([ep.path, ep.capability or "", ep.summary or ""]))
        endpoint_groups = _business_groups(endpoint_text)
        state_text_signal = bool(re.search(r"状态|终态|流转|生命周期|state transition|terminal state|initial state", capability_text, re.I))
        state_token_signal = _contains_state_token(capability_text, list(state_model.get("states") or []))
        state_endpoint_signal = (
            "state" in checks
            or state_text_signal
            or state_token_signal
            or "transition" in path_low
            or "archive" in path_low
            or "approve" in path_low
            or "cancel" in path_low
            or "payment" in path_low
            or "refund" in path_low
            or "return" in path_low
            or "ship" in path_low
            or "receive" in path_low
        )
        actors = [actor for actor in _normalize_actor_values(list(ep.actors or roles), limit=8) if actor != "anonymous"]
        non_admin_actors = [actor for actor in actors if actor not in {"admin", "approver"}]
        admin_actors = [actor for actor in actors if actor == "admin"]
        base_write_actors = non_admin_actors or actors
        write_actors = _dedupe_strings(base_write_actors + roles, limit=6)
        non_admin_variants = _actor_variants(non_admin_actors or actors[:3], limit=4)
        write_actor_variants = _actor_variants(write_actors, limit=5)
        audit_actor_variants = _actor_variants(_dedupe_strings(actors + roles, limit=6), limit=6)
        matched_rule_codes = _endpoint_rule_matches(ep, rules)
        inferred_rule_risks = set(matched_rule_codes.keys())
        if "auth_boundary_probe" in inferred_rule_risks:
            checks.add("auth")
        if "ownership_scope_probe" in inferred_rule_risks:
            checks.update({"tenant", "object_owner"})
        if "idempotency_replay_probe" in inferred_rule_risks or "async_external_event_probe" in inferred_rule_risks:
            checks.add("idempotency")
        if "state_transition_probe" in inferred_rule_risks:
            checks.add("state")
        if "audit_privacy_probe" in inferred_rule_risks:
            checks.add("audit")

        def rule_codes_for(risk_type: str, defaults: list[str]) -> list[str]:
            return _dedupe_strings(defaults + matched_rule_codes.get(risk_type, []), limit=8)

        def variant_codes_for(risk_type: str, *, limit: int) -> list[str | None]:
            codes = matched_rule_codes.get(risk_type, [])
            if codes:
                return codes[:limit]
            return [None]

        def rule_codes_variant_for(risk_type: str, defaults: list[str], focus_code: str | None) -> list[str]:
            focused = [focus_code] if focus_code else []
            return _dedupe_strings(defaults + focused, limit=8)

        if "business_rule_probe" in inferred_rule_risks:
            for focus_code in variant_codes_for("business_rule_probe", limit=8):
                rule = by_rule.get(str(focus_code or "")) if focus_code else None
                rule_title = _clean(str(getattr(rule, "rule_text", "") or "")) if rule else ""
                if rule_title:
                    rule_title = rule_title[:60] + ("…" if len(rule_title) > 60 else "")
                else:
                    rule_title = "业务规则"
                actor_variants = write_actor_variants if _is_write(ep.method) else non_admin_variants
                for actor_variant in actor_variants[:3]:
                    actor_prefix = _actor_title_prefix(actor_variant)
                    add(
                        ep,
                        "business_rule_probe",
                        f"{actor_prefix}{ep.method.upper()} {ep.path} 的业务规则校验候选：{rule_title}",
                        "接口行为必须满足客户业务规则与约束，返回/副作用应与规则一致。",
                        "接口可能忽略业务规则约束，导致列表过滤缺失、状态约束缺失、越权或业务不一致。",
                        {
                            "steps": ["提取规则中的约束字段与边界条件", "构造满足与违反规则的请求对照组", "对比返回字段/数量/状态变化并断言与规则一致"],
                            "focus_rule": focus_code or "",
                            "rule_text": getattr(rule, "rule_text", "") if rule else "",
                        },
                        "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                        ["request_response_pair", "response_schema", "negative_control"] if not _is_write(ep.method) else ["before_after_snapshot", "side_effect_diff", "request_response_pair"],
                        rule_codes_variant_for("business_rule_probe", [code or "BR00"], focus_code),
                        severity="P1",
                        confidence=0.72,
                        candidate_actors=actor_variant,
                    )

        list_endpoint_signal = ep.method.upper() == "GET" and not re.search(r"\{[^}]+\}", ep.path)
        if list_endpoint_signal and "business_rule_probe" in inferred_rule_risks:
            for focus_code in variant_codes_for("business_rule_probe", limit=8):
                rule = by_rule.get(str(focus_code or "")) if focus_code else None
                rule_title = _clean(str(getattr(rule, "rule_text", "") or "")) if rule else ""
                if rule_title:
                    rule_title = rule_title[:60] + ("…" if len(rule_title) > 60 else "")
                else:
                    rule_title = "业务规则"
                for actor_variant in non_admin_variants[:3]:
                    actor_prefix = _actor_title_prefix(actor_variant)
                    add(
                        ep,
                        "read_consistency_probe",
                        f"{actor_prefix}{ep.method.upper()} {ep.path} 的列表/分页/过滤一致性候选：{rule_title}",
                        "列表/搜索接口必须在分页、过滤、排序、权限与租户边界下保持一致性，返回集合与总数不得泄露越权对象。",
                        "接口可能缺少租户/归属过滤、分页总数不一致、排序/过滤绕过或 cursor/page 参数导致越权对象混入。",
                        {
                            "steps": ["构造分页/过滤/排序的对照组请求", "对比 items/total/count 与边界条件一致性", "检查越权对象是否混入以及总数是否泄露"],
                            "focus_rule": focus_code or "",
                            "rule_text": getattr(rule, "rule_text", "") if rule else "",
                            "controls": ["page", "page_size", "cursor", "sort", "filter"],
                        },
                        "read_only_safe",
                        ["request_response_pair", "pagination_invariants", "filter_invariants", "negative_control"],
                        rule_codes_variant_for("business_rule_probe", [code or "BRL00"], focus_code),
                        severity="P1",
                        confidence=0.7,
                        candidate_actors=actor_variant,
                    )

        if "auth" in checks or {"401", "403"}.intersection(set(ep.failure_statuses or [])):
            add(
                ep,
                "auth_boundary_probe",
                f"未登录/缺失 Bearer Token 访问 {ep.method.upper()} {ep.path} 的认证边界候选",
                "所有接口均需 Bearer Token；未登录请求必须返回 401/403，且不得泄露对象存在性或业务数据。",
                "接口可能仅依赖前端/网关声明，后端未强制登录态或错误信息暴露对象存在性。",
                {
                    "steps": ["构造无 Authorization 请求", "保留其它必填租户/路径参数的最小合法形式", "断言响应为 401/403/404 且不包含业务对象字段"],
                    "negative_headers": ["Authorization"],
                    "expected_status": [401, 403, 404],
                },
                "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                ["request_response_pair", "status_code", "body_redaction_check"],
                rule_codes_for("auth_boundary_probe", [code or "C03", "C03"]),
                severity="P1" if not _is_write(ep.method) else "P2",
                confidence=0.66,
                candidate_actors=["anonymous"],
            )
            if re.search(r"admin|管理员|仅管理员|审批", combined, re.I) and non_admin_actors:
                for actor_variant in non_admin_variants:
                    actor_prefix = _actor_title_prefix(actor_variant)
                    add(
                        ep,
                        "auth_boundary_probe",
                        f"{actor_prefix}已登录访问 {ep.method.upper()} {ep.path} 的授权边界候选",
                        "接口若声明仅管理员/审批角色可访问，则低权限已登录角色必须返回 403/404，且不得看到对象存在性或业务字段。",
                        "接口可能只校验登录态，未继续校验角色边界，导致普通用户、客服或运营以已登录状态访问管理接口。",
                        {
                            "steps": ["使用低权限但已登录的角色访问接口", "保留合法 Authorization 与必填参数", "断言响应为 403/404 且无敏感业务字段"],
                            "headers": ["Authorization"],
                            "expected_status": [403, 404],
                        },
                        "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                        ["request_response_pair", "status_code", "role_matrix", "negative_control"],
                        rule_codes_for("auth_boundary_probe", [code or "C03", "C03"]),
                        severity="P1",
                        confidence=0.7,
                        candidate_actors=actor_variant,
                    )
        if {"tenant", "org_scope", "object_owner"}.intersection(checks) or "tenant" in path_low or "tenant_id" in path_low:
            for actor_variant in non_admin_variants or _actor_variants(actors[:2], limit=2):
                actor_prefix = _actor_title_prefix(actor_variant)
                for focus_code in variant_codes_for("ownership_scope_probe", limit=6):
                    add(
                        ep,
                        "ownership_scope_probe",
                        f"{actor_prefix}跨租户/跨组织/跨归属访问 {ep.method.upper()} {ep.path} 的数据隔离候选",
                        "请求必须校验 tenant、org_scope 和 object_owner；跨租户或非归属对象访问必须被拒绝，且错误信息不得泄露对象是否存在。",
                        "接口可能只校验登录态，不校验数据归属、组织范围或租户过滤，导致越权读写。",
                        {
                            "steps": ["准备 A/B 两个租户或两个 owner 的对象", "使用 A 身份访问/修改 B 对象", "断言 403/404 且无 B 对象字段返回"],
                            "mutations": ["tenant_id", "object_id", "owner_user_id", "org_id"],
                            "expected_status": [403, 404],
                            "focus_rule": focus_code or "",
                        },
                        "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                        ["actor_matrix", "object_binding", "request_response_pair", "negative_control"],
                        rule_codes_variant_for("ownership_scope_probe", [code or "C05", "C05", "C03"], focus_code),
                        severity="P1",
                        confidence=0.7,
                        candidate_actors=actor_variant,
                    )
            if admin_actors and (re.search(r"admin|管理员", combined, re.I) or "ownership_scope_probe" in inferred_rule_risks):
                for focus_code in variant_codes_for("ownership_scope_probe", limit=3):
                    add(
                        ep,
                        "ownership_scope_probe",
                        f"管理员跨组织/跨租户访问 {ep.method.upper()} {ep.path} 的越权候选",
                        "管理员也必须受 tenant、org_scope 和对象归属边界约束，不能把管理员角色当作全局无边界权限。",
                        "接口可能仅判断 is_admin=true，遗漏 tenant/org_scope 过滤，导致管理员跨组织读取或修改无授权对象。",
                        {
                            "steps": ["准备不同 tenant/org 的对象", "使用管理员身份访问非授权 tenant/org 的对象", "断言 403/404 且无越权数据返回"],
                            "mutations": ["tenant_id", "org_id", "object_id"],
                            "expected_status": [403, 404],
                            "focus_rule": focus_code or "",
                        },
                        "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                        ["actor_matrix", "object_binding", "request_response_pair", "negative_control"],
                        rule_codes_variant_for("ownership_scope_probe", [code or "C05", "C05", "C03"], focus_code),
                        severity="P1",
                        confidence=0.74,
                        candidate_actors=admin_actors[:1],
                    )
        idempotency_endpoint_signal = bool(
            _is_write(ep.method)
            and (
                "idempotency" in checks
                or "idempotency" in combined
                or "submit" in path_low
                or "callback" in path_low
                or "sync" in path_low
                or "process" in path_low
                or (
                    bool(endpoint_groups)
                    and (prd_refs.get("idempotency") or "idempotency_replay_probe" in inferred_rule_risks)
                )
            )
        )
        if idempotency_endpoint_signal:
            for focus_code in variant_codes_for("idempotency_replay_probe", limit=8):
                for actor_variant in write_actor_variants:
                    actor_prefix = _actor_title_prefix(actor_variant)
                    add(
                        ep,
                        "idempotency_replay_probe",
                        f"{actor_prefix}顺序重复提交/重放 {ep.method.upper()} {ep.path} 的幂等候选",
                        "核心写接口必须使用业务唯一键或 Idempotency-Key，同一业务意图或第三方事件只能产生一次副作用。",
                        "重复请求可能重复扣减库存/金额、重复创建对象、重复发送通知或重复处理回调。",
                        {
                            "steps": ["构造同一业务唯一键或 Idempotency-Key 的写请求", "连续发送两次或并发发送 N 次", "比较业务对象、流水、库存/额度、审计日志数量"],
                            "headers": ["Idempotency-Key"],
                            "oracle": "side_effect_count == 1 and ledger_delta_not_duplicated",
                            "focus_rule": focus_code or "",
                        },
                        "disposable_sandbox_required",
                        ["before_after_snapshot", "side_effect_count", "idempotency_key", "ledger_or_audit_diff"],
                        rule_codes_variant_for("idempotency_replay_probe", [code or "C10", "C10", "C11"], focus_code),
                        severity="P1",
                        confidence=0.74,
                        candidate_actors=actor_variant,
                    )
                    add(
                        ep,
                        "idempotency_replay_probe",
                        f"{actor_prefix}并发重复提交 {ep.method.upper()} {ep.path} 的幂等竞争窗口候选",
                        "同一业务唯一键在并发压力下也只能产生一次副作用，不能因竞争窗口造成重复扣减、重复创建或重复发送。",
                        "接口可能只在顺序重放下幂等，但并发到达时缺少唯一约束/锁/compare-and-set，导致 side effect 被重复执行。",
                        {
                            "steps": ["构造同一业务唯一键或 Idempotency-Key", "并发发送相同写请求 N 次", "核对对象数量、库存/金额变化、流水与审计日志是否只发生一次"],
                            "headers": ["Idempotency-Key"],
                            "concurrency": 5,
                            "oracle": "side_effect_count == 1 and ledger_delta_not_duplicated",
                            "focus_rule": focus_code or "",
                        },
                        "disposable_sandbox_required",
                        ["before_after_snapshot", "side_effect_count", "idempotency_key", "ledger_or_audit_diff"],
                        rule_codes_variant_for("idempotency_replay_probe", [code or "C10", "C10", "C11"], focus_code),
                        severity="P1",
                        confidence=0.76,
                        candidate_actors=actor_variant,
                    )
        if _is_write(ep.method) and state_endpoint_signal:
            terminals = state_model.get("terminal_states") or []
            if terminals:
                for focus_code in variant_codes_for("state_transition_probe", limit=6):
                    for actor_variant in write_actor_variants:
                        actor_prefix = _actor_title_prefix(actor_variant)
                        add(
                            ep,
                            "state_transition_probe",
                            f"{actor_prefix}终态重入调用 {ep.method.upper()} {ep.path} 的状态机候选",
                            "对象进入 cancelled/refunded/completed 等终态后不得再次执行写入、副作用或重复审批。",
                            "接口可能未检查终态，允许终态重入、重复退款或归档后继续修改。",
                            {
                                "steps": ["构造处于终态的对象", "调用目标写接口", "断言 409/422 且金额/库存/审计/消息无新增副作用"],
                                "state_machine": state_model.get("states") or [],
                                "terminal_states": terminals,
                                "expected_status": [409, 422],
                                "focus_rule": focus_code or "",
                            },
                            "disposable_sandbox_required",
                            ["pre_state_snapshot", "post_state_snapshot", "side_effect_diff", "state_transition_log"],
                            rule_codes_variant_for("state_transition_probe", [code or "C06", "C06", "C07"], focus_code),
                            severity="P1",
                            confidence=0.76,
                            candidate_actors=actor_variant,
                        )
            if len(state_model.get("states") or []) >= 3:
                for focus_code in variant_codes_for("state_transition_probe", limit=6):
                    for actor_variant in write_actor_variants:
                        actor_prefix = _actor_title_prefix(actor_variant)
                        add(
                            ep,
                            "state_transition_probe",
                            f"{actor_prefix}跳跃/非法前置状态调用 {ep.method.upper()} {ep.path} 的状态机候选",
                            "对象只能按 PRD 声明的状态机顺序推进，不能跳过前置状态直接完成审批、发货、退款或归档。",
                            "接口可能只校验目标动作合法，未校验前置状态，导致跳跃状态、越序审批或异常回滚路径漏拦截。",
                            {
                                "steps": ["构造缺失前置状态的对象", "调用目标写接口", "断言 409/422 且状态轨迹与副作用均未错误推进"],
                                "state_machine": state_model.get("states") or [],
                                "expected_status": [409, 422],
                                "focus_rule": focus_code or "",
                            },
                            "disposable_sandbox_required",
                            ["pre_state_snapshot", "post_state_snapshot", "side_effect_diff", "state_transition_log"],
                            rule_codes_variant_for("state_transition_probe", [code or "C06", "C06", "C07"], focus_code),
                            severity="P1",
                            confidence=0.74,
                            candidate_actors=actor_variant,
                        )
        if _is_write(ep.method) and (
            re.search(r"库存|amount|payment|refund|settle|ledger|quota|额度|points|balance|deduct|transactions|billing|invoice|reimburse|credit|capacity", combined, re.I)
            or "conservation_probe" in inferred_rule_risks
        ):
            for actor_variant in write_actor_variants:
                actor_prefix = _actor_title_prefix(actor_variant)
                add(
                    ep,
                    "conservation_probe",
                    f"{actor_prefix}{ep.method.upper()} {ep.path} 的负库存/负金额/额度下溢候选",
                    "核心资源字段必须存在下界保护，库存、金额、积分、额度和余额不得出现负值或超过业务上限。",
                    "接口可能缺少边界校验，导致重复扣减、超额退款、负库存或负额度。",
                    {
                        "steps": ["记录资源字段基线", "构造边界值、超额扣减或超额退款请求", "断言库存/金额/额度不出现负值且错误码稳定"],
                        "oracle": "no_negative_quantity and no_negative_balance and bounded_amount_delta",
                    },
                    "disposable_sandbox_required",
                    ["db_snapshot_before_after", "negative_quantity_check", "request_response_pair", "boundary_value_matrix"],
                    rule_codes_for("conservation_probe", [code or "C08", "C08", "C14", "C23"]),
                    severity="P1",
                    confidence=0.78,
                    candidate_actors=actor_variant,
                )
                add(
                    ep,
                    "conservation_probe",
                    f"{actor_prefix}{ep.method.upper()} {ep.path} 的主表/流水/汇总不一致候选",
                    "核心资源必须守恒：主表、明细、流水、汇总和报表在事务或补偿后保持一致，局部失败也必须可对账。",
                    "接口可能局部成功、漏写流水、补偿不完整或报表延迟未收敛，导致主表与汇总/流水不一致。",
                    {
                        "steps": ["记录主对象、资源账户、流水和报表快照", "执行目标业务动作与异常路径", "对账主表/明细/流水/汇总差异并确认补偿是否收敛"],
                        "oracle": "resource_balance_after == resource_balance_before + sum(ledger_delta) and report_consistent_with_primary_record",
                    },
                    "disposable_sandbox_required",
                    ["db_snapshot_before_after", "ledger_reconciliation", "report_consistency", "side_effect_diff"],
                    rule_codes_for("conservation_probe", [code or "C08", "C08", "C14", "C23"]),
                    severity="P1",
                    confidence=0.8,
                    candidate_actors=actor_variant,
                )
        audit_signal = bool(
            "audit" in checks
            or re.search(r"export|import|approve|admin|config|rules|audit|privacy|file|download|report", combined, re.I)
            or (
                ep.method.upper() == "GET"
                and re.search(
                    r"user|users|member|members|account|accounts|profile|/me\b|admin|audit[_-]?log|logs?|privacy|sensitive|email|phone|id_card|address|手机号|邮箱|身份证|收货地址|隐私|敏感|脱敏|审计",
                    combined,
                    re.I,
                )
            )
        )
        if audit_signal:
            for focus_code in variant_codes_for("audit_privacy_probe", limit=3):
                for actor_variant in audit_actor_variants:
                    actor_prefix = _actor_title_prefix(actor_variant)
                    add(
                        ep,
                        "audit_privacy_probe",
                        f"{actor_prefix}{ep.method.upper()} {ep.path} 的审计/隐私/导出边界候选",
                        "管理员、导入导出、隐私字段和配置变更必须校验权限、脱敏并产生审计日志。",
                        "接口可能允许未授权导出、敏感字段未脱敏、缺少审计日志或导出过滤条件缺失租户/角色范围。",
                        {
                            "steps": ["以最低权限角色执行或读取目标接口", "检查敏感字段最小化/脱敏", "检查 audit_logs 是否记录 actor/action/object/before-after"],
                            "sensitive_fields": ["email", "phone", "id_card", "amount", "payload_json", "before_json", "after_json"],
                            "focus_rule": focus_code or "",
                        },
                        "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                        ["role_matrix", "response_schema", "sensitive_field_scan", "audit_log_snapshot"],
                        rule_codes_variant_for("audit_privacy_probe", [code or "C31", "C22", "C31"], focus_code),
                        severity="P1" if re.search(r"export|privacy|audit", combined, re.I) else "P2",
                        confidence=0.7,
                        candidate_actors=actor_variant,
                    )
        async_endpoint_signal = bool(
            _is_write(ep.method)
            and (
                re.search(r"callback|webhook|events|process|sync|retry|message|notify|third|payment|logistics|settlement|approval", combined, re.I)
                or "idempotency" in checks
                or "async_external_event_probe" in inferred_rule_risks
                or (
                    bool(endpoint_groups)
                    and (prd_refs.get("async") or prd_refs.get("idempotency") or "idempotency_replay_probe" in inferred_rule_risks)
                )
            )
        )
        if async_endpoint_signal:
            for focus_code in variant_codes_for("async_external_event_probe", limit=8):
                for actor_variant in audit_actor_variants:
                    actor_prefix = _actor_title_prefix(actor_variant)
                    add(
                        ep,
                        "async_external_event_probe",
                        f"{actor_prefix}{ep.method.upper()} {ep.path} 的异步/第三方事件幂等与验签候选",
                        "第三方回调、消息和异步任务必须验签、幂等、可重试、可死信并防乱序。",
                        "接口可能接受伪造回调、重复事件、乱序事件或失败重试导致重复副作用。",
                        {
                            "steps": ["构造缺失签名/过期 nonce/重复 external_event_id 的事件", "重放和乱序发送", "断言拒绝或只处理一次且可审计"],
                            "mutations": ["signature", "nonce", "timestamp", "external_event_id", "idempotency_key"],
                            "focus_rule": focus_code or "",
                        },
                        "disposable_sandbox_required",
                        ["event_id", "signature_result", "retry_log", "side_effect_count", "dead_letter_or_error_record"],
                        rule_codes_variant_for("async_external_event_probe", [code or "C19", "C19", "C20", "C32"], focus_code),
                        severity="P1",
                        confidence=0.74,
                        candidate_actors=actor_variant,
                    )

    max_n = _resolve_candidate_limit(max_candidates, endpoint_count=len(endpoints), role_count=len(roles))
    # Prioritize stronger, write-side business risks but keep read-only auth/tenant probes visible.
    order = {
        "business_rule_probe": 0,
        "read_consistency_probe": 0,
        "ownership_scope_probe": 0,
        "auth_boundary_probe": 1,
        "conservation_probe": 2,
        "state_transition_probe": 3,
        "idempotency_replay_probe": 4,
        "async_external_event_probe": 5,
        "audit_privacy_probe": 6,
    }
    candidates = sorted(candidates, key=lambda c: (order.get(c.risk_type, 99), c.endpoint.get("path", ""), -c.confidence))[:max_n]
    # Re-number after sorting/truncation for stable reports.
    for idx, cand in enumerate(candidates, 1):
        cand.candidate_id = f"GIC-{idx:04d}"

    by_risk = Counter(c.risk_type for c in candidates)
    by_policy = Counter(c.execution_policy for c in candidates)
    by_sev = Counter(c.severity for c in candidates)
    endpoint_count = len({(c.endpoint["method"], c.endpoint["path"]) for c in candidates})
    payload = {
        "project_id": project_id,
        "mode": "input_only_document_grounded_candidates",
        "strict_no_peek": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_dir": str(input_path),
        "input_documents_used": sorted(docs.keys()),
        "domain_model": {
            "roles": roles,
            "entities": entities,
            "state_machine": state_model,
            "business_rule_count": len(rules),
            "endpoint_count": len(endpoints),
            "knowledge_asset_attached": bool(knowledge_asset),
        },
        "summary": {
            "candidate_count": len(candidates),
            "endpoint_count": endpoint_count,
            "runtime_confirmed_bugs": 0,
            "needs_human_review": len(candidates),
            "strict_document_grounding": strict_document_grounding,
            "discarded_ungrounded_count": discarded_ungrounded_count,
            "knowledge_asset_rule_count": len(knowledge_asset.get("rule_library") or []) if knowledge_asset else 0,
            "knowledge_asset_interface_count": len(knowledge_asset.get("interfaces") or []) if knowledge_asset else 0,
            "by_risk_type": dict(sorted(by_risk.items())),
            "by_execution_policy": dict(sorted(by_policy.items())),
            "by_severity": dict(sorted(by_sev.items())),
        },
        "candidates": [asdict(c) for c in candidates],
        "note": "Candidates are generated from input documents only and must cite endpoint_contract plus customer_requirement refs. They are not confirmed bugs until executed against a live/disposable target and verified by evidence gates.",
    }
    return payload


def render_grounded_candidates_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    domain = payload.get("domain_model") or {}
    lines = [
        f"# Document-grounded Bug Candidate Plan — {payload.get('project_id') or ''}",
        "",
        "## Guardrail",
        "",
        f"- strict_no_peek: `{payload.get('strict_no_peek')}`",
        "- source: `projects/<project>/input` only",
        "- runtime_confirmed_bugs: `0` before execution",
        "",
        "## Domain model extracted from customer documents",
        "",
        f"- roles: {', '.join(domain.get('roles') or []) or 'none'}",
        f"- entities: {', '.join(domain.get('entities') or []) or 'none'}",
        f"- state machine: {' → '.join((domain.get('state_machine') or {}).get('states') or []) or 'none'}",
        f"- terminal states: {', '.join((domain.get('state_machine') or {}).get('terminal_states') or []) or 'none'}",
        f"- business rules: {domain.get('business_rule_count')}",
        f"- API endpoints: {domain.get('endpoint_count')}",
        "",
        "## Candidate summary",
        "",
        f"- candidates: {summary.get('candidate_count')}",
        f"- covered endpoints: {summary.get('endpoint_count')}",
        f"- strict document grounding: `{summary.get('strict_document_grounding')}`",
        f"- discarded ungrounded candidates: `{summary.get('discarded_ungrounded_count')}`",
        f"- execution policies: `{json.dumps(summary.get('by_execution_policy') or {}, ensure_ascii=False)}`",
        f"- risk types: `{json.dumps(summary.get('by_risk_type') or {}, ensure_ascii=False)}`",
        "",
        "## Top candidates",
        "",
    ]
    for cand in (payload.get("candidates") or [])[:40]:
        ep = cand.get("endpoint") or {}
        refs = cand.get("source_refs") or []
        ref_text = "; ".join(f"{r.get('file')} / {r.get('section')}: {r.get('quote')}" for r in refs[:3])
        lines.extend([
            f"### {cand.get('candidate_id')} — {cand.get('title')}",
            "",
            f"- risk_type: `{cand.get('risk_type')}`",
            f"- severity: `{cand.get('severity')}` / confidence: `{cand.get('confidence')}`",
            f"- endpoint: `{ep.get('method')} {ep.get('path')}`",
            f"- execution_policy: `{cand.get('execution_policy')}`",
            f"- expected: {cand.get('expected_behavior')}",
            f"- suspected failure: {cand.get('suspected_failure_pattern')}",
            f"- required evidence: {', '.join(cand.get('required_evidence') or [])}",
            f"- source refs: {ref_text}",
            f"- grounding basis: `{json.dumps(cand.get('grounding_basis') or {}, ensure_ascii=False)}`",
            "",
        ])
    return "\n".join(lines)


def _validation_priority(candidate: dict[str, Any]) -> dict[str, Any]:
    severity_score = {"P0": 100, "P1": 80, "P2": 45, "P3": 20}.get(str(candidate.get("severity") or "").upper(), 30)
    risk_score = {
        "conservation_probe": 24,
        "state_transition_probe": 22,
        "idempotency_replay_probe": 22,
        "async_external_event_probe": 20,
        "ownership_scope_probe": 18,
        "auth_boundary_probe": 16,
        "audit_privacy_probe": 12,
    }.get(str(candidate.get("risk_type") or ""), 8)
    policy = str(candidate.get("execution_policy") or "")
    execution_score = 10 if policy == "read_only_safe" else 4
    confidence_score = int(float(candidate.get("confidence") or 0) * 20)
    evidence_count = len(candidate.get("required_evidence") or [])
    evidence_score = min(evidence_count, 6) * 2
    endpoint = candidate.get("endpoint") or {}
    code = str(endpoint.get("capability_code") or "")
    category_score = 8 if re.fullmatch(r"C\d{2}", code) else 0
    total = severity_score + risk_score + execution_score + confidence_score + evidence_score + category_score
    lane = "immediate_readonly" if policy == "read_only_safe" else "sandbox_required"
    return {
        "score": total,
        "lane": lane,
        "reasons": {
            "severity_score": severity_score,
            "risk_score": risk_score,
            "execution_score": execution_score,
            "confidence_score": confidence_score,
            "evidence_score": evidence_score,
            "category_score": category_score,
        },
    }


def build_runtime_validation_queue(payload: dict[str, Any], *, limit: int = 80) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for candidate in payload.get("candidates") or []:
        priority = _validation_priority(candidate)
        endpoint = candidate.get("endpoint") or {}
        rows.append({
            "rank": 0,
            "candidate_id": candidate.get("candidate_id"),
            "validation_score": priority["score"],
            "validation_lane": priority["lane"],
            "severity": candidate.get("severity"),
            "confidence": candidate.get("confidence"),
            "risk_type": candidate.get("risk_type"),
            "endpoint": endpoint,
            "execution_policy": candidate.get("execution_policy"),
            "required_evidence": candidate.get("required_evidence") or [],
            "priority_reasons": priority["reasons"],
            "customer_acceptance_gate": {
                "minimum_evidence": ["request_response_pair", "source_refs", "grounding_basis"],
                "runtime_confirmation_required": True,
                "status_before_execution": "candidate_not_customer_signable",
            },
        })
    rows.sort(key=lambda item: (
        item["validation_lane"] != "immediate_readonly",
        -int(item["validation_score"]),
        str(item["risk_type"] or ""),
        str((item.get("endpoint") or {}).get("path") or ""),
    ))
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx
    selected = rows[:limit]
    by_lane = Counter(row["validation_lane"] for row in selected)
    by_risk = Counter(row["risk_type"] for row in selected)
    return {
        "project_id": payload.get("project_id"),
        "mode": "runtime_validation_priority_queue",
        "strict_no_peek": True,
        "candidate_count": len(payload.get("candidates") or []),
        "queue_limit": limit,
        "queued_count": len(selected),
        "summary": {
            "by_lane": dict(sorted(by_lane.items())),
            "by_risk_type": dict(by_risk.most_common()),
            "customer_signable_before_runtime": 0,
            "runtime_confirmation_required": True,
        },
        "queue": selected,
    }


def render_runtime_validation_queue_markdown(queue: dict[str, Any]) -> str:
    summary = queue.get("summary") or {}
    lines = [
        f"# Runtime Validation Queue - {queue.get('project_id') or ''}",
        "",
        "## Guardrail",
        "",
        "- source: document-grounded candidates only",
        "- hidden oracle / ground truth: not read",
        "- customer-signable bugs before runtime: `0`",
        "",
        "## Summary",
        "",
        f"- queued: `{queue.get('queued_count')}` / candidates: `{queue.get('candidate_count')}`",
        f"- by lane: `{json.dumps(summary.get('by_lane') or {}, ensure_ascii=False)}`",
        f"- by risk type: `{json.dumps(summary.get('by_risk_type') or {}, ensure_ascii=False)}`",
        "",
        "## Top validation targets",
        "",
    ]
    for row in queue.get("queue") or []:
        endpoint = row.get("endpoint") or {}
        lines.extend([
            f"### #{row.get('rank')} {row.get('candidate_id')} - {row.get('risk_type')}",
            "",
            f"- score: `{row.get('validation_score')}` / lane: `{row.get('validation_lane')}`",
            f"- severity: `{row.get('severity')}` / confidence: `{row.get('confidence')}`",
            f"- endpoint: `{endpoint.get('method')} {endpoint.get('path')}`",
            f"- evidence: {', '.join(row.get('required_evidence') or [])}",
            f"- acceptance: `{(row.get('customer_acceptance_gate') or {}).get('status_before_execution')}`",
            "",
        ])
    return "\n".join(lines)


def write_grounded_candidate_outputs(input_dir: str | Path, output_dir: str | Path, *, project_id: str = "", max_candidates: int | None = None, knowledge_asset: dict[str, Any] | None = None) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = compile_grounded_candidates(input_dir, project_id=project_id, max_candidates=max_candidates, knowledge_asset=knowledge_asset)
    json_path = output / "grounded_candidates.json"
    md_path = output / "grounded_candidates.md"
    probe_path = output / "grounded_probe_plan.json"
    queue_path = output / "runtime_validation_queue.json"
    queue_md_path = output / "runtime_validation_queue.md"
    validation_queue = build_runtime_validation_queue(payload)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_grounded_candidates_markdown(payload), encoding="utf-8")
    queue_path.write_text(json.dumps(validation_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    queue_md_path.write_text(render_runtime_validation_queue_markdown(validation_queue), encoding="utf-8")
    probe_path.write_text(json.dumps({
        "project_id": payload.get("project_id"),
        "mode": "document_grounded_probe_plan",
        "strict_no_peek": True,
        "created_at": payload.get("created_at"),
        "probes": [
            {
                "candidate_id": c.get("candidate_id"),
                "risk_type": c.get("risk_type"),
                "endpoint": c.get("endpoint"),
                "execution_policy": c.get("execution_policy"),
                "actors": c.get("actors") or [],
                "probe_plan": c.get("probe_plan"),
                "required_evidence": c.get("required_evidence"),
                "source_refs": c.get("source_refs") or [],
                "grounding_basis": c.get("grounding_basis") or {},
                "validation_priority": _validation_priority(c),
            }
            for c in payload.get("candidates") or []
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["outputs"] = {
        "grounded_candidates": str(json_path),
        "grounded_candidates_md": str(md_path),
        "grounded_probe_plan": str(probe_path),
        "runtime_validation_queue": str(queue_path),
        "runtime_validation_queue_md": str(queue_md_path),
    }
    return payload
