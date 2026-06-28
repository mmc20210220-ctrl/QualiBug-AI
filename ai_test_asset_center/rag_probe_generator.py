"""
[DEPRECATED] RAG Probe Generator
Status: NEAR-ZOMBIE -- 1 active cross-reference.
Roadmap: Generate probes targeting RAG/KB endpoints.
         Verify single reference is active; activate full pipeline or prune.
See DEPRECATED.md for architecture decisions.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_KB = Path("platform_workspace/enterprise_shop/defect_discovery/rag_knowledge_base.json")
FALLBACK_KB = Path("benchmark_outputs/training_data/rag_knowledge_base.json")
DEFAULT_INDEX = Path("platform_workspace/enterprise_shop/defect_discovery/rag_index.json")
FALLBACK_INDEX = Path("benchmark_outputs/training_data/rag_index.json")
DEFAULT_REPORT_OUT = Path("benchmark_outputs/rag_probe/rag_probe_report.html")
DEFAULT_SCORECARD_OUT = Path("benchmark_outputs/rag_probe/rag_probe_scorecard.json")
DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
    "bug_sets/",
    "bug_sets\\",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-/{}/]+|[\u4e00-\u9fff]+")


def _safe_path(path: Path) -> Path:
    text = str(path).replace("\\", "/").lower()
    if any(marker.lower() in text for marker in PRIVATE_MARKERS):
        raise PermissionError(f"RAG discovery cannot read private benchmark artifact: {path}")
    return path


def _read_json(path: Path) -> Any:
    _safe_path(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def tokenize(text: str) -> list[str]:
    tokens = [m.group(0).lower() for m in TOKEN_RE.finditer(text or "")]
    return [t for t in tokens if len(t) > 1 or any("\u4e00" <= c <= "\u9fff" for c in t)]


def load_rag_payload(kb_path: Path | None = None, index_path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    kb = kb_path or DEFAULT_KB
    idx = index_path or DEFAULT_INDEX
    if not kb.exists() and FALLBACK_KB.exists():
        kb = FALLBACK_KB
    if not idx.exists() and FALLBACK_INDEX.exists():
        idx = FALLBACK_INDEX
    if not kb.exists():
        return [], {"document_count": 0, "token_count": 0, "reason": "rag_knowledge_base_missing"}
    payload = _read_json(kb)
    docs = payload.get("documents", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    index = _read_json(idx) if idx.exists() else {"document_count": len(docs), "token_count": 0}
    safe_docs = []
    for doc in docs:
        text = json.dumps(doc, ensure_ascii=False).lower()
        if any(marker.lower() in text for marker in PRIVATE_MARKERS):
            # Drop unsafe docs rather than failing the whole run.
            continue
        if (doc.get("metadata") or {}).get("safe_for_discovery", True) is False:
            continue
        safe_docs.append(doc)
    return safe_docs, index


def operation_query(op: dict[str, Any], prd: str = "") -> str:
    fields = [op.get("method"), op.get("path"), op.get("summary"), op.get("resource"), op.get("operation"), " ".join(op.get("risk_hints", []) or [])]
    return " ".join(str(x or "") for x in fields) + " " + (prd or "")[:1000]


def retrieve_rag_docs(query: str, docs: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    q_tokens = Counter(tokenize(query))
    if not q_tokens:
        return []
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for doc in docs:
        text = str(doc.get("text") or "") + " " + json.dumps(doc.get("related_api") or {}, ensure_ascii=False)
        d_tokens = Counter(tokenize(text))
        overlap = set(q_tokens) & set(d_tokens)
        if not overlap:
            continue
        weighted = sum(min(q_tokens[t], d_tokens[t]) for t in overlap)
        # Prefer exact path/risk matches but do not require them.
        score = weighted / max(1, len(set(q_tokens)))
        scored.append((score, doc, sorted(overlap)[:12]))
    scored.sort(key=lambda x: (x[0], str(x[1].get("severity") == "P0"), x[1].get("template_id", "")), reverse=True)
    return [dict(item[1], retrieval_score=round(item[0], 4), matched_tokens=item[2]) for item in scored[:top_k]]


def _materialize_path(path: str) -> str:
    replacements = {
        "{product_id}": "p100",
        "{order_id}": "o900",
        "{address_id}": "addr_bob",
        "{user_id}": "u_bob",
        "{tenant_id}": "tenant_b",
        "{refund_id}": "r900",
        "{payment_id}": "pay900",
        "{id}": "o900",
    }
    out = path or "/"
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def _expected_status_for(doc: dict[str, Any]) -> int:
    template = str(doc.get("template_id") or "").upper()
    risk = str(doc.get("risk_type") or "").lower()
    text = str(doc.get("text") or "").lower()
    if "unauth" in template or "auth_bypass" in risk:
        return 401
    if any(x in template for x in ["BYPASS", "IDOR", "TENANT", "OWNERSHIP"]):
        return 403
    if any(x in template for x in ["OVERSELL", "DUPLICATE", "NOT_DECREASED", "ROLLBACK", "PAYMENT", "REFUND", "IDEMPOTENCY", "STATUS"]):
        return 409
    m = re.search(r"返回\s*(\d{3})", text)
    if m:
        return int(m.group(1))
    return 409


def _actor_for(doc: dict[str, Any]) -> str:
    template = str(doc.get("template_id") or "").upper()
    risk = str(doc.get("risk_type") or "").lower()
    if "UNAUTH" in template or risk == "auth_bypass":
        return "anonymous"
    if "CALLBACK" in template:
        return "system"
    if "LOCKED" in template:
        return "locked_user"
    return "normal_user"


def _probe_type_for(doc: dict[str, Any]) -> str:
    risk = str(doc.get("risk_type") or "unknown")
    template = str(doc.get("template_id") or "UNKNOWN")
    if "IDOR" in template:
        return "idor_probe"
    if "TENANT" in template:
        return "tenant_probe"
    if "COUPON" in template:
        return "coupon_probe"
    if "PAYMENT" in template or "PAY_" in template:
        return "payment_probe"
    if "REFUND" in template:
        return "refund_probe"
    if "STOCK" in template:
        return "stock_probe"
    if "IDEMPOTENCY" in template:
        return "idempotency_probe"
    if "AUTH" in template:
        return "permission_probe"
    return f"{risk}_probe"


def _api_ref(doc: dict[str, Any]) -> tuple[str, str, str]:
    api = doc.get("related_api") or {}
    method = str(api.get("method") or "GET").upper()
    raw_path = str(api.get("path") or "/")
    path = _materialize_path(raw_path)
    return method, path, f"{method} {raw_path}"


def generate_rag_enhanced_probes(business_model: dict[str, Any], prd: str = "", kb_path: Path | None = None, index_path: Path | None = None, top_k_per_operation: int | None = None) -> list[dict[str, Any]]:
    """Generate blind-mode probes using Phase14 pattern-level RAG knowledge.

    The generator only consumes safe pattern documents and public PRD/OpenAPI-derived
    business_model. It never reads bug_sets, enabled_bugs or private ground truth.
    """
    if top_k_per_operation is None:
        try:
            top_k_per_operation = int(os.environ.get("RAG_TOP_K_PER_OPERATION", "3") or "3")
        except Exception:
            top_k_per_operation = 3
    top_k_per_operation = max(0, int(top_k_per_operation or 0))
    if top_k_per_operation <= 0:
        return []
    docs, index = load_rag_payload(kb_path, index_path)
    if not docs:
        return []
    operations = business_model.get("operations", []) or []
    open_paths = {op.get("path") for op in operations}
    probes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for op in operations:
        matches = retrieve_rag_docs(operation_query(op, prd), docs, top_k=top_k_per_operation)
        for doc in matches:
            method, path, api_template = _api_ref(doc)
            raw_path = str((doc.get("related_api") or {}).get("path") or "")
            # Only generate if the public OpenAPI exposes that endpoint, unless it is a generic same-resource match.
            if raw_path and raw_path not in open_paths:
                continue
            template = str(doc.get("template_id") or "UNKNOWN_TEMPLATE")
            key = (template, api_template)
            if key in seen:
                continue
            seen.add(key)
            score = float(doc.get("retrieval_score") or 0.0)
            severity = str(doc.get("severity") or "P1")
            risk_type = str(doc.get("risk_type") or "unknown")
            probes.append({
                "probe_id": f"RAG_{template}_{len(probes)+1:03d}",
                "title": f"RAG增强：{doc.get('title') or template}",
                "probe_type": _probe_type_for(doc),
                "risk_type": risk_type,
                "severity": severity,
                "actor": _actor_for(doc),
                "method": method,
                "path": path,
                "api_template": api_template,
                "source": "rag_enhanced",
                "expected_status": _expected_status_for(doc),
                "expected": f"HTTP {_expected_status_for(doc)} 或业务状态保持一致",
                "bug_signal": doc.get("bug_signal") or "RAG匹配的高价值业务不变量被破坏",
                "evidence_required": ["actor_role", "request", "response_status", "response_body", "rag_doc_id", "retrieval_score"],
                "predicted_template_id": template,
                "rag_doc_id": doc.get("doc_id"),
                "rag_matched_template": template,
                "retrieval_score": round(score, 4),
                "matched_tokens": doc.get("matched_tokens", []),
                "rag_source": "phase14_pattern_knowledge_base",
                "rag_top_k_per_operation": top_k_per_operation,
            })
    return probes


def summarize_rag_probes(probes: list[dict[str, Any]]) -> dict[str, Any]:
    rag = [p for p in probes if p.get("source") == "rag_enhanced"]
    by_template = Counter(str(p.get("predicted_template_id") or "UNKNOWN") for p in rag)
    by_risk = Counter(str(p.get("risk_type") or "unknown") for p in rag)
    return {
        "rag_probe_count": len(rag),
        "rag_template_count": len(by_template),
        "top_templates": by_template.most_common(20),
        "risk_distribution": dict(by_risk),
        "avg_retrieval_score": round(sum(float(p.get("retrieval_score") or 0) for p in rag) / len(rag), 4) if rag else 0,
        "private_leak_check": "passed" if all(not any(m in json.dumps(p, ensure_ascii=False).lower() for m in PRIVATE_MARKERS) for p in rag) else "failed",
    }


def build_rag_probe_report(workspace: Path = DEFAULT_WORKSPACE, out_html: Path = DEFAULT_REPORT_OUT, out_json: Path = DEFAULT_SCORECARD_OUT) -> dict[str, Any]:
    probes_path = workspace / "defect_probes.json"
    strategy_path = workspace / "probe_generation_strategy.json"
    if not probes_path.exists():
        summary = {"status": "missing_defect_probes", "rag_probe_count": 0}
    else:
        probes = json.loads(probes_path.read_text(encoding="utf-8"))
        summary = summarize_rag_probes(probes)
        summary["status"] = "ok"
    if strategy_path.exists():
        try:
            strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
            summary["probe_policy_profile"] = strategy.get("probe_policy_profile")
            summary["discovery_mode"] = strategy.get("discovery_mode")
            summary["benchmark_compat_probe_count"] = strategy.get("benchmark_compat_probe_count")
        except Exception:
            pass
    _write_json(out_json, summary)
    rows = "".join(f"<tr><td>{html.escape(str(t))}</td><td>{c}</td></tr>" for t, c in summary.get("top_templates", []))
    html_text = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>RAG 增强探针报告</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.kpi{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;border-radius:8px;padding:14px}}table{{border-collapse:collapse;width:100%;margin-top:18px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.ok{{color:#087f5b}}</style></head><body><h1>Phase15 RAG 增强探针报告</h1><p>基于 Phase14 的 pattern-level RAG 知识库生成 blind-mode 探针，不读取 private ground truth / bug_set / enabled_bugs。</p><div class=\"kpi\"><div class=\"card\">RAG 探针<br><b>{summary.get('rag_probe_count',0)}</b></div><div class=\"card\">覆盖模板<br><b>{summary.get('rag_template_count',0)}</b></div><div class=\"card\">平均检索分<br><b>{summary.get('avg_retrieval_score',0)}</b></div><div class=\"card\">泄露检查<br><b class=\"ok\">{summary.get('private_leak_check','unknown')}</b></div></div><h2>Top RAG Templates</h2><table><tr><th>Template</th><th>Probe Count</th></tr>{rows}</table></body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html_text, encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Build Phase15 RAG probe report from latest defect probes")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--out-html", default=str(DEFAULT_REPORT_OUT))
    parser.add_argument("--out-json", default=str(DEFAULT_SCORECARD_OUT))
    args = parser.parse_args(argv)
    summary = build_rag_probe_report(Path(args.workspace), Path(args.out_html), Path(args.out_json))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
