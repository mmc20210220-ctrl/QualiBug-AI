from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .real_project_onboarding import ROOT, _html_escape, _load_json, _read_text, _safe_project_id, _write_json, config_paths, load_real_project_config
from .business_adaptation_layer import build_business_adaptation_profile, load_business_adaptation_profile
from .enterprise_strategy_learning import load_enterprise_strategy_learning, load_strategy_feedback

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-/{}/]+|[\u4e00-\u9fff]+")
MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

RISK_DEFAULTS: dict[str, dict[str, Any]] = {
    "permission_bypass": {"severity": "P1", "expected_status": 403, "expected": "低权限或未授权角色不能访问高权限接口", "bug_signal": "普通用户/匿名用户访问成功或返回敏感字段"},
    "idor": {"severity": "P1", "expected_status": 403, "expected": "资源详情和写操作必须校验归属", "bug_signal": "替换资源 ID 后仍可读取或修改他人资源"},
    "tenant_isolation": {"severity": "P0", "expected_status": 403, "expected": "跨租户/跨组织数据必须隔离", "bug_signal": "非本租户账号可读取或操作其他租户数据"},
    "money_consistency": {"severity": "P0", "expected_status": 409, "expected": "金额、余额、流水、订单状态必须守恒一致", "bug_signal": "接口成功但金额、流水或状态出现不一致"},
    "payment": {"severity": "P0", "expected_status": 409, "expected": "支付金额、状态流转和回调幂等必须一致", "bug_signal": "重复回调、金额篡改或状态错乱仍成功"},
    "refund": {"severity": "P0", "expected_status": 409, "expected": "退款金额、订单状态和退款幂等必须受控", "bug_signal": "重复退款、超额退款或非法状态退款成功"},
    "coupon_abuse": {"severity": "P1", "expected_status": 409, "expected": "优惠券归属、门槛、有效期、次数必须校验", "bug_signal": "重复抵扣、越权使用或绕过门槛成功"},
    "stock_consistency": {"severity": "P1", "expected_status": 409, "expected": "库存扣减、锁定、回滚和订单状态必须一致", "bug_signal": "超卖、库存未扣减或回滚失败"},
    "idempotency": {"severity": "P1", "expected_status": 409, "expected": "重复提交不得产生重复业务结果", "bug_signal": "重复创建、重复扣减、重复入账或重复审批"},
    "order_state": {"severity": "P1", "expected_status": 409, "expected": "订单/业务对象状态必须按状态机流转", "bug_signal": "非法跳转、回退或重复状态推进成功"},
    "approval_bypass": {"severity": "P0", "expected_status": 403, "expected": "审批必须按角色、顺序、金额阈值和状态机推进", "bug_signal": "申请人自审、跳过节点、越级审批成功"},
    "privacy_leak": {"severity": "P0", "expected_status": 403, "expected": "敏感资料只能被授权角色访问", "bug_signal": "未授权角色读取患者、客户、学生等敏感信息"},
    "state_transition": {"severity": "P1", "expected_status": 409, "expected": "核心对象状态只能按 PRD 允许路径流转", "bug_signal": "非法状态跳转或重复提交仍成功"},
    "business_rule": {"severity": "P2", "expected_status": 409, "expected": "后端必须强制校验 PRD 定义的业务规则", "bug_signal": "接口成功但业务不变量被破坏"},
}

RISK_HINTS: dict[str, list[str]] = {
    "permission_bypass": ["admin", "manage", "manager", "后台", "管理员", "权限", "role", "rbac", "approve"],
    "idor": ["{id}", "order", "profile", "address", "user", "customer", "详情", "他人", "归属"],
    "tenant_isolation": ["tenant", "org", "organization", "租户", "组织"],
    "money_consistency": ["amount", "balance", "ledger", "price", "wallet", "金额", "余额", "流水", "价格"],
    "payment": ["payment", "pay", "callback", "transaction", "支付", "回调", "入账"],
    "refund": ["refund", "return", "退款", "退货"],
    "coupon_abuse": ["coupon", "voucher", "discount", "优惠", "折扣"],
    "stock_consistency": ["stock", "inventory", "sku", "库存", "扣减"],
    "idempotency": ["create", "submit", "pay", "callback", "approve", "book", "publish", "创建", "提交", "支付", "审批", "预约"],
    "approval_bypass": ["approval", "approve", "workflow", "review", "expense", "审批", "审核", "报销"],
    "privacy_leak": ["patient", "medical", "record", "customer", "student", "患者", "病历", "客户", "学生"],
    "state_transition": ["status", "state", "cancel", "close", "publish", "状态", "流转", "取消", "发布"],
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _safe_doc_text(value: Any) -> str:
    text = _normalize_text(value).replace("\x00", " ")
    lower = text.lower()
    if any(marker.lower() in lower for marker in PRIVATE_MARKERS):
        return ""
    return text[:5000]


def _tokenize(text: str) -> list[str]:
    tokens = [m.group(0).lower() for m in TOKEN_RE.finditer(text or "")]
    return [t for t in tokens if len(t) > 1 or any("\u4e00" <= c <= "\u9fff" for c in t)]


def _counter(items: Iterable[str]) -> dict[str, int]:
    c: dict[str, int] = {}
    for item in items:
        key = str(item or "unknown")
        c[key] = c.get(key, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _openapi_operations(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            spec = spec if isinstance(spec, dict) else {}
            text = " ".join([
                method_u,
                str(path),
                str(spec.get("operationId") or ""),
                str(spec.get("summary") or ""),
                str(spec.get("description") or ""),
                _normalize_text(spec.get("tags") or []),
                _normalize_text(spec.get("parameters") or []),
                _normalize_text(spec.get("requestBody") or {}),
            ])
            rows.append({"method": method_u, "path": str(path), "summary": spec.get("summary") or "", "operation_id": spec.get("operationId"), "text": text})
    return rows


def _load_openapi(project: str, root: Path) -> dict[str, Any]:
    paths = config_paths(project, root)
    data = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {})
    if not isinstance(data, dict) or not data.get("paths"):
        data = _load_json(paths["input_dir"] / "openapi.json", {})
    return data if isinstance(data, dict) else {}


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 160) -> list[str]:
    clean = _safe_doc_text(text)
    if not clean.strip():
        return []
    chunks: list[str] = []
    i = 0
    while i < len(clean) and len(chunks) < 80:
        chunks.append(clean[i : i + max_chars])
        i += max(1, max_chars - overlap)
    return chunks


def _endpoint_from_any(value: Any) -> str:
    text = str(value or "")
    m = re.search(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s,，;；]+)", text, re.I)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    m2 = re.search(r"(/(?:[A-Za-z0-9_{}.-]+/?)+)", text)
    if m2:
        return m2.group(1)
    return ""


def _module_from_endpoint(endpoint: str) -> str:
    path = endpoint.split(" ", 1)[-1] if " " in endpoint else endpoint
    bits = [b for b in path.strip("/").split("/") if b and not b.startswith("{")]
    return bits[0].lower() if bits else "root"


def _add_doc(docs: list[dict[str, Any]], *, doc_id: str, source: str, doc_type: str, text: Any, risk_type: str = "", business_domain: str = "", endpoint: str = "", module: str = "", severity: str = "P2", weight: float = 1.0, metadata: dict[str, Any] | None = None) -> None:
    body = _safe_doc_text(text)
    if not body.strip():
        return
    docs.append({
        "doc_id": doc_id,
        "source": source,
        "doc_type": doc_type,
        "text": body,
        "risk_type": risk_type or "business_rule",
        "business_domain": business_domain or "unknown",
        "endpoint": endpoint or "",
        "module": module or _module_from_endpoint(endpoint),
        "severity": severity or "P2",
        "weight": round(float(weight or 1.0), 6),
        "metadata": metadata or {},
        "tokens": sorted(set(_tokenize(body + " " + risk_type + " " + endpoint)))[:120],
    })


def _weight_lookup(strategy: dict[str, Any] | None, key: str, field: str) -> float:
    if not isinstance(strategy, dict):
        return 1.0
    rows = strategy.get(field) or []
    key_name = {
        "risk_type_weights": "risk_type",
        "endpoint_weights": "endpoint",
        "business_domain_weights": "business_domain",
        "module_weights": "module",
    }.get(field, "")
    for row in rows:
        if isinstance(row, dict) and str(row.get(key_name)) == str(key):
            try:
                return float(row.get("weight") or 1.0)
            except Exception:
                return 1.0
    return 1.0


def _collect_documents(project: str, root: Path, openapi: dict[str, Any], profile: dict[str, Any], strategy: dict[str, Any] | None) -> list[dict[str, Any]]:
    paths = config_paths(project, root)
    docs: list[dict[str, Any]] = []
    prd = "\n".join(_read_text(paths["input_dir"] / name) for name in ["prd.md", "requirements.md", "business_rules.md"])
    for idx, chunk in enumerate(_chunk_text(prd), start=1):
        _add_doc(docs, doc_id=f"prd::{idx:03d}", source="prd", doc_type="requirement_chunk", text=chunk, weight=1.0, metadata={"chunk_index": idx})

    for idx, op in enumerate(_openapi_operations(openapi), start=1):
        endpoint = f"{op['method']} {op['path']}"
        guessed_risk = _infer_risks_for_text(op.get("text") or endpoint)[:1]
        _add_doc(docs, doc_id=f"openapi::{idx:04d}", source="openapi", doc_type="api_operation", text=op.get("text") or endpoint, risk_type=(guessed_risk[0] if guessed_risk else "business_rule"), endpoint=endpoint, module=_module_from_endpoint(endpoint), severity="P2", weight=1.05, metadata={"operation_id": op.get("operation_id"), "summary": op.get("summary")})

    selected_domains = profile.get("selected_domains") or []
    for idx, domain in enumerate(selected_domains, start=1):
        if not isinstance(domain, dict):
            continue
        d = str(domain.get("domain") or "unknown")
        _add_doc(docs, doc_id=f"business_domain::{d}", source="business_adaptation", doc_type="business_domain", text=domain, business_domain=d, weight=_weight_lookup(strategy, d, "business_domain_weights"), metadata={"domain": d})
    for idx, item in enumerate(profile.get("adaptive_risk_matrix") or [], start=1):
        if not isinstance(item, dict):
            continue
        risks = item.get("risk_types") or item.get("risks") or []
        if isinstance(risks, str):
            risks = [risks]
        endpoint = f"{str(item.get('method') or 'GET').upper()} {item.get('path') or item.get('endpoint') or '/'}"
        for risk in (risks or ["business_rule"]):
            weight = max(_weight_lookup(strategy, str(risk), "risk_type_weights"), _weight_lookup(strategy, endpoint, "endpoint_weights"))
            _add_doc(docs, doc_id=f"business_risk::{idx:04d}::{risk}", source="business_adaptation", doc_type="business_risk_playbook", text=item, risk_type=str(risk), business_domain=str(item.get("business_domain") or "unknown"), endpoint=endpoint, module=_module_from_endpoint(endpoint), severity=str(item.get("severity") or RISK_DEFAULTS.get(str(risk), {}).get("severity") or "P2"), weight=weight, metadata={"matrix_index": idx})

    hist = _load_json(root / "platform_workspace" / project / "historical_bugs" / "normalized_historical_bugs.json", {})
    for idx, bug in enumerate((hist.get("items") if isinstance(hist, dict) else []) or [], start=1):
        if not isinstance(bug, dict):
            continue
        endpoint = _endpoint_from_any(" ".join(bug.get("related_apis") or []))
        risk = str(bug.get("risk_type") or "business_rule")
        _add_doc(docs, doc_id=f"historical_bug::{bug.get('historical_bug_id') or idx}", source="historical_bug", doc_type="historical_bug", text=bug, risk_type=risk, endpoint=endpoint, module=str(bug.get("module") or _module_from_endpoint(endpoint)), severity=str(bug.get("severity") or "P2"), weight=1.18 * _weight_lookup(strategy, risk, "risk_type_weights"), metadata={"historical_bug_id": bug.get("historical_bug_id"), "title": bug.get("title")})

    patterns = _load_json(root / "platform_workspace" / project / "defect_discovery" / "enterprise_bug_pattern_library.json", {})
    for idx, pattern in enumerate((patterns.get("items") if isinstance(patterns, dict) else []) or [], start=1):
        if not isinstance(pattern, dict):
            continue
        risk = str(pattern.get("risk_type") or "business_rule")
        apis = pattern.get("related_apis") or []
        endpoint = _endpoint_from_any(" ".join(apis))
        confidence = float(pattern.get("confidence_prior") or 0.6)
        _add_doc(docs, doc_id=f"historical_pattern::{pattern.get('pattern_id') or idx}", source="historical_pattern", doc_type="bug_pattern", text=pattern, risk_type=risk, endpoint=endpoint, module=str(pattern.get("module") or _module_from_endpoint(endpoint)), severity=str(pattern.get("severity") or "P2"), weight=(1.1 + confidence * 0.3) * _weight_lookup(strategy, risk, "risk_type_weights"), metadata={"pattern_id": pattern.get("pattern_id"), "historical_count": pattern.get("historical_count")})

    feedback = load_strategy_feedback(project, root)
    for idx, row in enumerate(feedback[:300], start=1):
        if not isinstance(row, dict):
            continue
        risk = str(row.get("risk_type") or "business_rule")
        endpoint = str(row.get("endpoint") or "")
        if row.get("is_false_positive") is True or row.get("is_valid_bug") is False:
            w = 0.72
        elif row.get("is_missed_bug") is True:
            w = 1.32
        elif row.get("is_valid_bug") is True and row.get("is_high_value") is True:
            w = 1.28
        elif row.get("is_valid_bug") is True:
            w = 1.12
        else:
            w = 0.95
        _add_doc(docs, doc_id=f"qa_feedback::{row.get('feedback_id') or idx}", source="qa_feedback", doc_type="qa_feedback", text=row, risk_type=risk, business_domain=str(row.get("business_domain") or "unknown"), endpoint=endpoint, module=_module_from_endpoint(endpoint), severity=str(row.get("human_severity") or "P2"), weight=w * _weight_lookup(strategy, risk, "risk_type_weights"), metadata={"feedback_id": row.get("feedback_id"), "is_missed_bug": row.get("is_missed_bug"), "is_false_positive": row.get("is_false_positive")})

    for field, key_name, doc_type in [
        ("risk_type_weights", "risk_type", "strategy_risk_weight"),
        ("endpoint_weights", "endpoint", "strategy_endpoint_weight"),
        ("module_weights", "module", "strategy_module_weight"),
    ]:
        for idx, row in enumerate(((strategy or {}).get(field) or [])[:120], start=1):
            if not isinstance(row, dict):
                continue
            key = str(row.get(key_name) or "")
            risk = key if key_name == "risk_type" else str(row.get("risk_type") or "business_rule")
            endpoint = key if key_name == "endpoint" else ""
            _add_doc(docs, doc_id=f"strategy_weight::{field}::{idx}", source="strategy_learning", doc_type=doc_type, text=row, risk_type=risk, endpoint=endpoint, module=(key if key_name == "module" else _module_from_endpoint(endpoint)), severity="P2", weight=float(row.get("weight") or 1.0), metadata={"status": row.get("status"), "recommendation": row.get("recommendation")})

    # Keep the KB bounded and stable. Prefer high-weight and structured docs.
    docs.sort(key=lambda d: (float(d.get("weight") or 1.0), d.get("source") in {"historical_bug", "historical_pattern", "qa_feedback", "business_adaptation"}), reverse=True)
    return docs[:800]


def _build_index(docs: list[dict[str, Any]]) -> dict[str, Any]:
    inverted: dict[str, list[str]] = {}
    risk_dist = _counter([str(d.get("risk_type") or "business_rule") for d in docs])
    source_dist = _counter([str(d.get("source") or "unknown") for d in docs])
    module_dist = _counter([str(d.get("module") or "unknown") for d in docs])
    for doc in docs:
        for tok in (doc.get("tokens") or [])[:80]:
            inverted.setdefault(tok, []).append(str(doc.get("doc_id")))
    return {
        "document_count": len(docs),
        "token_count": len(inverted),
        "risk_distribution": risk_dist,
        "source_distribution": source_dist,
        "module_distribution": module_dist,
        "top_tokens": sorted(inverted.keys())[:200],
    }


def retrieve_enterprise_knowledge(query: str, documents: list[dict[str, Any]], top_k: int = 6) -> list[dict[str, Any]]:
    q_tokens = Counter(_tokenize(query))
    if not q_tokens:
        return []
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    q_set = set(q_tokens)
    endpoint_in_query = _endpoint_from_any(query)
    for doc in documents:
        text = " ".join([str(doc.get("text") or ""), str(doc.get("risk_type") or ""), str(doc.get("endpoint") or ""), str(doc.get("module") or "")])
        d_tokens = Counter(_tokenize(text))
        overlap = q_set & set(d_tokens)
        if not overlap:
            continue
        score = sum(min(q_tokens[t], d_tokens[t]) for t in overlap) / max(1, len(q_set))
        if endpoint_in_query and endpoint_in_query == str(doc.get("endpoint")):
            score += 0.45
        elif endpoint_in_query and endpoint_in_query.split(" ")[-1] and endpoint_in_query.split(" ")[-1] in str(doc.get("endpoint")):
            score += 0.25
        if str(doc.get("source")) in {"historical_bug", "historical_pattern", "qa_feedback", "business_adaptation"}:
            score += 0.08
        score *= float(doc.get("weight") or 1.0)
        scored.append((score, doc, sorted(overlap)[:14]))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("doc_id"))))
    out: list[dict[str, Any]] = []
    for score, doc, overlap in scored[: max(0, int(top_k))]:
        slim = {k: v for k, v in doc.items() if k != "tokens"}
        slim["retrieval_score"] = round(score, 6)
        slim["matched_tokens"] = overlap
        out.append(slim)
    return out


def _infer_risks_for_text(text: str) -> list[str]:
    lower = (text or "").lower()
    scores: dict[str, int] = {}
    for risk, hints in RISK_HINTS.items():
        score = sum(1 for h in hints if h.lower() in lower)
        if risk == "idempotency" and any(w in lower for w in ["post", "put", "patch", "delete"]):
            score += 1
        if score:
            scores[risk] = score
    return [r for r, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def _risk_from_contexts(contexts: list[dict[str, Any]], fallback_text: str) -> list[str]:
    scores: dict[str, float] = {}
    for doc in contexts:
        risk = str(doc.get("risk_type") or "business_rule")
        scores[risk] = scores.get(risk, 0.0) + float(doc.get("retrieval_score") or 0.0) + float(doc.get("weight") or 1.0) * 0.15
    for risk in _infer_risks_for_text(fallback_text):
        scores[risk] = scores.get(risk, 0.0) + 0.35
    if not scores:
        scores["business_rule"] = 0.1
    return [r for r, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])) if r != "unknown"][:3]


def _severity_for_risk(risk: str, contexts: list[dict[str, Any]]) -> str:
    sev_order = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
    values = [str(c.get("severity") or "P2").upper() for c in contexts if str(c.get("risk_type")) == risk]
    values.append(str(RISK_DEFAULTS.get(risk, {}).get("severity") or "P2"))
    return max(values, key=lambda s: sev_order.get(s, 0))


def _destructive_for(risk: str, method: str) -> bool:
    return method.upper() in MUTATION_METHODS or risk in {"money_consistency", "payment", "refund", "stock_consistency", "idempotency", "approval_bypass", "state_transition", "coupon_abuse"}


def _make_probe_id(risk: str, method: str, path: str, idx: int) -> str:
    raw = re.sub(r"[^A-Za-z0-9]+", "_", f"{risk}_{method}_{path}").strip("_").upper()
    return f"EKR_{idx:04d}_{raw[:64]}"


def generate_enterprise_knowledge_probes(openapi: dict[str, Any], cfg: dict[str, Any] | None = None, project_id: str = "real_project_demo", root: Path | None = None, max_count: int = 160) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    result = load_enterprise_test_knowledge(project, root)
    if not result:
        result = build_enterprise_test_knowledge(project, root, options={"skip_probe_preview": True})
    documents = result.get("documents") or []
    operations = _openapi_operations(openapi or {})
    selected_domains = ((result.get("business_context") or {}).get("selected_domains") or [])
    domain = selected_domains[0].get("domain") if selected_domains and isinstance(selected_domains[0], dict) else "unknown"
    probes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for op in operations:
        endpoint = f"{op['method']} {op['path']}"
        query = " ".join([endpoint, op.get("summary") or "", op.get("text") or ""])
        contexts = retrieve_enterprise_knowledge(query, documents, top_k=6)
        risks = _risk_from_contexts(contexts, query)
        for risk in risks[:2]:
            key = (risk, op["method"], op["path"])
            if key in seen:
                continue
            seen.add(key)
            defaults = RISK_DEFAULTS.get(risk) or RISK_DEFAULTS["business_rule"]
            source_docs = [{"doc_id": c.get("doc_id"), "source": c.get("source"), "doc_type": c.get("doc_type"), "score": c.get("retrieval_score")} for c in contexts[:4]]
            probe_idx = len(probes) + 1
            probes.append({
                "probe_id": _make_probe_id(risk, op["method"], op["path"], probe_idx),
                "title": f"企业知识库RAG：{risk} · {op['method']} {op['path']}",
                "probe_type": f"enterprise_knowledge_{risk}_probe",
                "risk_type": risk,
                "severity": _severity_for_risk(risk, contexts),
                "business_domain": domain,
                "actor": "normal_user" if risk not in {"permission_bypass", "privacy_leak"} else "low_privilege_user",
                "method": op["method"],
                "path": op["path"],
                "source": "enterprise_knowledge_rag",
                "expected_status": defaults.get("expected_status", 409),
                "expected": defaults.get("expected"),
                "bug_signal": defaults.get("bug_signal"),
                "evidence_required": ["actor_role", "request", "response_status", "response_body", "knowledge_doc_ids", "retrieval_score"],
                "knowledge_doc_ids": [str(c.get("doc_id")) for c in contexts[:4]],
                "knowledge_contexts": source_docs,
                "retrieval_score": round(sum(float(c.get("retrieval_score") or 0.0) for c in contexts[:4]), 6),
                "matched_tokens": sorted(set(tok for c in contexts[:4] for tok in (c.get("matched_tokens") or [])))[:20],
                "execution_policy": "candidate_only" if _destructive_for(risk, op["method"]) else "safe_execute",
                "destructive": _destructive_for(risk, op["method"]),
            })
            if len(probes) >= int(max_count):
                return probes
    return probes[: int(max_count)]


def build_enterprise_test_knowledge(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    openapi = _load_openapi(project, root)
    profile = load_business_adaptation_profile(project, root) or build_business_adaptation_profile(project, root)
    strategy = load_enterprise_strategy_learning(project, root)
    documents = _collect_documents(project, root, openapi, profile, strategy)
    index = _build_index(documents)
    operations = _openapi_operations(openapi)
    op_contexts = []
    for op in operations[:120]:
        endpoint = f"{op['method']} {op['path']}"
        contexts = retrieve_enterprise_knowledge(endpoint + " " + (op.get("text") or ""), documents, top_k=int(options.get("top_k", 5) or 5))
        op_contexts.append({
            "endpoint": endpoint,
            "summary": op.get("summary") or "",
            "recommended_risks": _risk_from_contexts(contexts, op.get("text") or endpoint),
            "top_contexts": [{"doc_id": c.get("doc_id"), "source": c.get("source"), "doc_type": c.get("doc_type"), "risk_type": c.get("risk_type"), "retrieval_score": c.get("retrieval_score")} for c in contexts[:5]],
        })
    probes = generate_enterprise_knowledge_probes(openapi, cfg, project, root, max_count=int(options.get("preview_probe_count", 60) or 60)) if not bool(options.get("skip_probe_preview")) else []
    selected_domains = profile.get("selected_domains") or []
    summary = {
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "document_count": len(documents),
        "token_count": index.get("token_count", 0),
        "openapi_operation_count": len(operations),
        "operation_context_count": len(op_contexts),
        "preview_probe_count": len(probes),
        "business_domains": [d.get("domain") for d in selected_domains if isinstance(d, dict)],
        "strategy_learning_enabled": bool(strategy),
        "strategy_learning_feedback_rows": ((strategy or {}).get("summary") or {}).get("feedback_rows", 0),
        "top_risks": list((index.get("risk_distribution") or {}).keys())[:8],
        "top_sources": list((index.get("source_distribution") or {}).keys())[:8],
    }
    result = {
        "phase": "phase37_enterprise_knowledge_rag",
        "summary": summary,
        "documents": documents,
        "index": index,
        "operation_contexts": op_contexts,
        "preview_probes": probes,
        "business_context": {"selected_domains": selected_domains, "adaptive_risk_matrix_count": len(profile.get("adaptive_risk_matrix") or [])},
        "strategy_learning_digest": ((strategy or {}).get("summary") or {}),
        "governance": {
            "uses_only_enterprise_public_inputs_and_qa_feedback": True,
            "uses_no_benchmark_answer_files": True,
            "rag_is_deterministic_local_retrieval": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    out_dir = root / "platform_outputs" / project / "enterprise_knowledge"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "enterprise_test_knowledge.json", result)
    _write_json(out_dir / "enterprise_test_knowledge_summary.json", {"summary": summary, "private_leak_check": result["private_leak_check"]})
    _write_json(out_dir / "enterprise_knowledge_index.json", index)
    _write_json(ws_dir / "enterprise_test_knowledge.json", result)
    _write_json(ws_dir / "enterprise_knowledge_index.json", index)
    (out_dir / "enterprise_test_knowledge_report.html").write_text(render_enterprise_test_knowledge_report(result), encoding="utf-8")
    return result


def load_enterprise_test_knowledge(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    for p in [
        root / "platform_workspace" / project / "defect_discovery" / "enterprise_test_knowledge.json",
        root / "platform_outputs" / project / "enterprise_knowledge" / "enterprise_test_knowledge.json",
    ]:
        data = _load_json(p, {})
        if isinstance(data, dict) and data:
            return data
    return None


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted(m for m in PRIVATE_MARKERS if m.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def render_enterprise_test_knowledge_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    index = result.get("index") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items() if k not in {"business_domains", "top_risks", "top_sources"})
    source_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in (index.get("source_distribution") or {}).items())
    risk_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in (index.get("risk_distribution") or {}).items())
    ctx_rows = ""
    for item in (result.get("operation_contexts") or [])[:80]:
        ctx = "; ".join(f"{c.get('source')}/{c.get('risk_type')}({c.get('retrieval_score')})" for c in (item.get("top_contexts") or [])[:3])
        ctx_rows += f"<tr><td>{_html_escape(item.get('endpoint'))}</td><td>{_html_escape(', '.join(item.get('recommended_risks') or []))}</td><td>{_html_escape(ctx)}</td></tr>"
    probe_rows = ""
    for p in (result.get("preview_probes") or [])[:60]:
        probe_rows += f"<tr><td>{_html_escape(p.get('severity'))}</td><td>{_html_escape(p.get('risk_type'))}</td><td>{_html_escape(p.get('method'))} {_html_escape(p.get('path'))}</td><td>{_html_escape(', '.join(p.get('knowledge_doc_ids') or []))}</td></tr>"
    leak = result.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>企业知识库 RAG</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>Phase37</span><h1>企业知识库 RAG + 策略学习融合</h1><p>把 PRD、OpenAPI、历史 Bug、业务适配画像和 QA 策略学习结果统一成企业专属测试知识库，并生成贴近业务的 RAG 探针。</p><p>私有数据泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>知识库概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>来源分布</h2><table><tbody>{source_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table></section>
<section class='panel'><h2>风险分布</h2><table><tbody>{risk_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table></section>
<section class='panel'><h2>接口 RAG 上下文</h2><table><thead><tr><th>接口</th><th>推荐风险</th><th>Top 上下文</th></tr></thead><tbody>{ctx_rows or '<tr><td colspan="3">暂无接口上下文</td></tr>'}</tbody></table></section>
<section class='panel'><h2>预览探针</h2><table><thead><tr><th>等级</th><th>风险</th><th>接口</th><th>知识来源</th></tr></thead><tbody>{probe_rows or '<tr><td colspan="4">暂无预览探针</td></tr>'}</tbody></table></section>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    result = build_enterprise_test_knowledge(project)
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
