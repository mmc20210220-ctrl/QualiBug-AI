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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


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
    roles: list[str] = []
    for text in (prd_text, api_text):
        for role in re.findall(r"`([^`]+)`", text or ""):
            token = role.strip()
            if token and re.search(r"(admin|buyer|user|manager|rep|doctor|patient|merchant|tenant|staff|finance|auditor|teacher|student|operator|nurse|warehouse|planner|qc|member|agent|approver)", token, re.I):
                roles.append(token)
    return sorted(dict.fromkeys(roles))


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
    return {"states": states, "terminal_states": terminals}

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
            ))
    return rules


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
            actors=actors,
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
    # endpoint by canonical suffix instead of emitting duplicates. If API.md did
    # not state a method and we inferred the wrong one, the OpenAPI method wins.
    merged: dict[tuple[str, str], ApiEndpoint] = {}
    suffix_index: dict[tuple[str, str], tuple[str, str]] = {}
    suffix_any_index: dict[str, tuple[str, str] | None] = {}

    def absorb(target: ApiEndpoint, src: ApiEndpoint) -> None:
        target.capability_code = target.capability_code or src.capability_code
        target.capability = target.capability or src.capability
        target.summary = target.summary or src.summary
        target.actors = sorted(dict.fromkeys((target.actors or []) + (src.actors or [])))
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
        suffix_any_index[suffix] = key if suffix not in suffix_any_index else None

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
        any_key = suffix_any_index.get(suffix)
        if any_key and any_key in merged:
            target = merged.pop(any_key)
            target.method = ep.method
            new_key = (target.method, target.path)
            absorb(target, ep)
            merged[new_key] = target
            suffix_index[(target.method, suffix)] = new_key
            suffix_any_index[suffix] = new_key
            continue
        key = (ep.method, ep.path)
        merged[key] = ep
        suffix_index[suffix_key] = key
        suffix_any_index[suffix] = key if suffix not in suffix_any_index else None

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


def compile_grounded_candidates(input_dir: str | Path, *, project_id: str = "", max_candidates: int | None = None) -> dict[str, Any]:
    input_path = Path(input_dir).resolve()
    docs = load_input_documents(input_path)
    prd = docs.get("PRD.md", "") or docs.get("prd.md", "")
    api_md = docs.get("API.md", "") or docs.get("api.md", "")
    rules_text = docs.get("BUSINESS_RULES.md", "") or docs.get("business_rules.md", "")
    schema_text = docs.get("schema.sql", "") or docs.get("DATABASE_DESIGN.md", "")
    risk_text = docs.get("RISK_SURFACE_MODEL.md", "")

    roles = parse_roles(prd, api_md)
    entities = parse_entities(prd, schema_text)
    state_model = parse_state_machine(prd)
    rules = parse_business_rules(rules_text)
    endpoints = merge_endpoints(parse_api_md(api_md), parse_openapi_endpoints(input_path))
    by_rule = _rule_lookup(rules)
    prd_refs = parse_prd_grounding_refs(prd)
    api_global_refs = parse_api_global_constraint_refs(api_md)
    risk_refs_by_code = parse_risk_surface_refs(risk_text)
    strict_document_grounding = os.environ.get("QUALIBUG_STRICT_DOCUMENT_GROUNDING", "1") != "0"

    candidates: list[GroundedCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    discarded_ungrounded_count = 0

    def add(ep: ApiEndpoint, risk_type: str, title: str, expected: str, failure: str, probe: dict[str, Any], policy: str, required_evidence: list[str], rule_codes: list[str], severity: str = "P2", confidence: float = 0.62) -> None:
        nonlocal discarded_ungrounded_count
        key = (ep.method.upper(), ep.path, risk_type)
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
            actors=list(ep.actors or roles[:3]),
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
        ))

    for ep in endpoints:
        code = _capability_code_from_endpoint(ep)
        checks = {c.lower() for c in (ep.checks or [])}
        path_low = ep.path.lower()
        cap_low = (ep.capability or ep.summary or "").lower()
        combined = f"{path_low} {cap_low}"
        actors = ep.actors or roles

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
                [code or "C03", "C03"],
                severity="P1" if not _is_write(ep.method) else "P2",
                confidence=0.66,
            )
        if {"tenant", "org_scope", "object_owner"}.intersection(checks) or "tenant" in path_low or "tenant_id" in path_low:
            add(
                ep,
                "ownership_scope_probe",
                f"跨租户/跨组织/跨归属访问 {ep.method.upper()} {ep.path} 的数据隔离候选",
                "请求必须校验 tenant、org_scope 和 object_owner；跨租户或非归属对象访问必须被拒绝，且错误信息不得泄露对象是否存在。",
                "接口可能只校验登录态，不校验数据归属、组织范围或租户过滤，导致越权读写。",
                {
                    "steps": ["准备 A/B 两个租户或两个 owner 的对象", "使用 A 身份访问/修改 B 对象", "断言 403/404 且无 B 对象字段返回"],
                    "mutations": ["tenant_id", "object_id", "owner_user_id", "org_id"],
                    "expected_status": [403, 404],
                },
                "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                ["actor_matrix", "object_binding", "request_response_pair", "negative_control"],
                [code or "C05", "C05", "C03"],
                severity="P1",
                confidence=0.7,
            )
        if _is_write(ep.method) and ("idempotency" in checks or "idempotency" in combined or "submit" in path_low or "callback" in path_low or "sync" in path_low or "process" in path_low):
            add(
                ep,
                "idempotency_replay_probe",
                f"重复提交/重放 {ep.method.upper()} {ep.path} 的幂等候选",
                "核心写接口必须使用业务唯一键或 Idempotency-Key，同一业务意图或第三方事件只能产生一次副作用。",
                "重复请求可能重复扣减库存/金额、重复创建对象、重复发送通知或重复处理回调。",
                {
                    "steps": ["构造同一业务唯一键或 Idempotency-Key 的写请求", "连续发送两次或并发发送 N 次", "比较业务对象、流水、库存/额度、审计日志数量"],
                    "headers": ["Idempotency-Key"],
                    "oracle": "side_effect_count == 1 and ledger_delta_not_duplicated",
                },
                "disposable_sandbox_required",
                ["before_after_snapshot", "side_effect_count", "idempotency_key", "ledger_or_audit_diff"],
                [code or "C10", "C10", "C11"],
                severity="P1",
                confidence=0.74,
            )
        if _is_write(ep.method) and ("state" in checks or "transition" in path_low or "archive" in path_low or "approve" in path_low or "cancel" in path_low or "refund" in path_low):
            terminals = state_model.get("terminal_states") or []
            add(
                ep,
                "state_transition_probe",
                f"终态/非法状态流转调用 {ep.method.upper()} {ep.path} 的状态机候选",
                "对象只能按 PRD 状态机推进；cancelled/refunded/completed 等终态不得再产生副作用。",
                "接口可能未检查当前状态，允许终态重入、跳跃状态、重复审批或归档后修改。",
                {
                    "steps": ["构造处于终态或非法前置状态的对象", "调用目标写接口", "断言 409/422，且金额/库存/审计/消息无新增副作用"],
                    "state_machine": state_model.get("states") or [],
                    "terminal_states": terminals,
                    "expected_status": [409, 422],
                },
                "disposable_sandbox_required",
                ["pre_state_snapshot", "post_state_snapshot", "side_effect_diff", "state_transition_log"],
                [code or "C06", "C06", "C07"],
                severity="P1",
                confidence=0.76,
            )
        if _is_write(ep.method) and re.search(r"库存|amount|payment|refund|settle|ledger|quota|额度|points|balance|deduct|transactions|billing|invoice|reimburse|credit|capacity", combined, re.I):
            add(
                ep,
                "conservation_probe",
                f"{ep.method.upper()} {ep.path} 对金额/库存/额度/流水守恒的候选",
                "核心资源必须守恒：主表、明细、流水、汇总和报表在事务或补偿后保持一致，数量不得小于 0。",
                "接口可能局部成功、重复扣减、漏写流水、主表与汇总不一致或负库存/负额度。",
                {
                    "steps": ["记录主对象、资源账户、流水和报表快照", "执行目标业务动作或异常路径", "对账主表/明细/流水/汇总差异"],
                    "oracle": "resource_balance_after == resource_balance_before + sum(ledger_delta) and no_negative_quantity",
                },
                "disposable_sandbox_required",
                ["db_snapshot_before_after", "ledger_reconciliation", "negative_quantity_check", "report_consistency"],
                [code or "C08", "C08", "C14", "C23"],
                severity="P1",
                confidence=0.78,
            )
        if ("audit" in checks or re.search(r"export|import|approve|admin|config|rules|audit|privacy|file|download|report", combined, re.I)):
            add(
                ep,
                "audit_privacy_probe",
                f"{ep.method.upper()} {ep.path} 的审计/隐私/导出边界候选",
                "管理员、导入导出、隐私字段和配置变更必须校验权限、脱敏并产生审计日志。",
                "接口可能允许未授权导出、敏感字段未脱敏、缺少审计日志或导出过滤条件缺失租户/角色范围。",
                {
                    "steps": ["以最低权限角色执行或读取目标接口", "检查敏感字段最小化/脱敏", "检查 audit_logs 是否记录 actor/action/object/before-after"],
                    "sensitive_fields": ["email", "phone", "id_card", "amount", "payload_json", "before_json", "after_json"],
                },
                "read_only_safe" if not _is_write(ep.method) else "disposable_sandbox_required",
                ["role_matrix", "response_schema", "sensitive_field_scan", "audit_log_snapshot"],
                [code or "C31", "C22", "C31"],
                severity="P1" if re.search(r"export|privacy|audit", combined, re.I) else "P2",
                confidence=0.7,
            )
        if _is_write(ep.method) and re.search(r"callback|webhook|events|process|sync|retry|message|notify|third|payment|logistics", combined, re.I):
            add(
                ep,
                "async_external_event_probe",
                f"{ep.method.upper()} {ep.path} 的异步/第三方事件幂等与验签候选",
                "第三方回调、消息和异步任务必须验签、幂等、可重试、可死信并防乱序。",
                "接口可能接受伪造回调、重复事件、乱序事件或失败重试导致重复副作用。",
                {
                    "steps": ["构造缺失签名/过期 nonce/重复 external_event_id 的事件", "重放和乱序发送", "断言拒绝或只处理一次且可审计"],
                    "mutations": ["signature", "nonce", "timestamp", "external_event_id", "idempotency_key"],
                },
                "disposable_sandbox_required",
                ["event_id", "signature_result", "retry_log", "side_effect_count", "dead_letter_or_error_record"],
                [code or "C19", "C19", "C20", "C32"],
                severity="P1",
                confidence=0.74,
            )

    max_n = max_candidates or int(os.environ.get("QUALIBUG_INPUT_ONLY_MAX_CANDIDATES", "180") or 180)
    # Prioritize stronger, write-side business risks but keep read-only auth/tenant probes visible.
    order = {
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
        },
        "summary": {
            "candidate_count": len(candidates),
            "endpoint_count": endpoint_count,
            "runtime_confirmed_bugs": 0,
            "needs_human_review": len(candidates),
            "strict_document_grounding": strict_document_grounding,
            "discarded_ungrounded_count": discarded_ungrounded_count,
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


def write_grounded_candidate_outputs(input_dir: str | Path, output_dir: str | Path, *, project_id: str = "", max_candidates: int | None = None) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = compile_grounded_candidates(input_dir, project_id=project_id, max_candidates=max_candidates)
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
