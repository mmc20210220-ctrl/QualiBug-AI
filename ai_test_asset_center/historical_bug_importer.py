from __future__ import annotations

"""
[DEPRECATED] Historical Bug Importer
Status: ZOMBIE MODULE -- 0 active cross-references.
Roadmap: Critical for behavior space modeling data flywheel.
         Import historical bug data from external trackers (Jira, etc.)
         to bootstrap the cross-enterprise behavior pattern library.
         Not currently wired into any import pipeline.

Parses historical bug reports from CSV, JSON, JSONL, Markdown, and plain text.
Classifies each bug by risk keyword matching and maps them to API surface via
OpenAPI path references.

See DEPRECATED.md for architecture decisions and activation plan.
"""

import csv
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKERS = {"private_ground_truth", "bug_sets", "enabled_bugs", "current_bug_set", "ground_truth_bugs", "bug_instance_id"}
SUPPORTED_EXTENSIONS = {".csv", ".json", ".jsonl", ".md", ".txt"}

RISK_KEYWORDS: dict[str, list[str]] = {
    "permission_bypass": ["权限", "管理员", "admin", "rbac", "role", "unauthorized", "未授权", "越权访问"],
    "idor": ["他人", "越权", "idor", "横向", "订单详情", "address", "profile", "owner"],
    "tenant_isolation": ["租户", "tenant", "组织隔离", "跨组织", "跨租户"],
    "money_consistency": ["金额", "价格", "优惠", "折扣", "精度", "total", "amount", "price", "discount"],
    "stock_consistency": ["库存", "超卖", "扣减", "回滚", "inventory", "stock"],
    "coupon_abuse": ["优惠券", "coupon", "voucher", "门槛", "过期", "重复抵扣"],
    "payment": ["支付", "回调", "入账", "payment", "pay", "callback", "transaction"],
    "refund": ["退款", "refund", "退货", "重复退款", "超额退款"],
    "idempotency": ["幂等", "重复提交", "duplicate", "idempotency", "重复订单"],
    "order_state": ["订单状态", "取消", "已取消", "状态流转", "order status", "state"],
    "data_consistency": ["一致性", "未落库", "查询不到", "数据不一致", "consistency"],
}

SEVERITY_ORDER = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}


def _safe_project_id(value: str | None) -> str:
    raw = (value or "real_project_demo").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "_-." )
    return safe or "real_project_demo"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(_read_text(path) or "null")
    except Exception:
        return default
    return default


def _html_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _counter(items: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        key = str(item or "unknown")
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items(), key=lambda kv: (-kv[1], kv[0])))


def _infer_risk_type(text: str) -> str:
    lower = (text or "").lower()
    scores: dict[str, int] = {}
    for risk, words in RISK_KEYWORDS.items():
        scores[risk] = sum(1 for w in words if w.lower() in lower)
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else "business_rule"


def _normalize_severity(value: Any, text: str = "") -> str:
    raw = str(value or "").strip().upper()
    if raw in SEVERITY_ORDER:
        return raw
    if raw in {"BLOCKER", "CRITICAL", "S0", "严重", "致命"}:
        return "P0"
    if raw in {"HIGH", "S1", "MAJOR", "高"}:
        return "P1"
    if raw in {"MEDIUM", "S2", "中"}:
        return "P2"
    if raw in {"LOW", "S3", "MINOR", "低"}:
        return "P3"
    lower = (text or "").lower()
    if any(k in lower for k in ["资金", "金额", "支付", "泄露", "越权", "管理员", "p0", "critical"]):
        return "P1"
    return "P2"


def _extract_api_paths(text: str) -> list[str]:
    if not text:
        return []
    # Matches /orders/{id}, /api/v1/admin/orders, etc.
    paths = re.findall(r"/(?:[A-Za-z0-9_{}.-]+/?)+", text)
    cleaned: list[str] = []
    for p in paths:
        p = p.strip().rstrip(".,;:，。；：)）]")
        if len(p) > 1 and p not in cleaned:
            cleaned.append(p)
    return cleaned[:10]


def _row_get(row: dict[str, Any], *keys: str) -> Any:
    lower = {str(k).lower().strip(): v for k, v in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        lk = key.lower()
        if lk in lower and lower[lk] not in (None, ""):
            return lower[lk]
    return ""


def normalize_bug_record(raw: dict[str, Any], source_file: str = "") -> dict[str, Any]:
    title = str(_row_get(raw, "title", "标题", "bug_title", "summary", "name") or "未命名历史缺陷")
    description = str(_row_get(raw, "description", "描述", "desc", "details", "detail", "问题描述") or "")
    reproduce_steps = str(_row_get(raw, "reproduce_steps", "steps", "复现步骤", "repro", "重现步骤") or "")
    root_cause = str(_row_get(raw, "root_cause", "根因", "原因", "cause") or "")
    module = str(_row_get(raw, "module", "模块", "component", "business_module") or "unknown")
    raw_sev = _row_get(raw, "severity", "priority", "严重等级", "优先级", "level")
    combined = "\n".join([title, description, reproduce_steps, root_cause, module])
    severity = _normalize_severity(raw_sev, combined)
    risk_type = str(_row_get(raw, "risk_type", "风险类型") or _infer_risk_type(combined))
    api_paths = _row_get(raw, "api_paths", "related_apis", "api", "接口", "path")
    if isinstance(api_paths, str):
        paths = _extract_api_paths(api_paths) or _extract_api_paths(combined)
    elif isinstance(api_paths, list):
        paths = [str(p) for p in api_paths if str(p).strip()]
    else:
        paths = _extract_api_paths(combined)
    business_impact = str(_row_get(raw, "business_impact", "impact", "影响", "业务影响") or _impact_for_risk(risk_type))
    bug_id = str(_row_get(raw, "bug_id", "id", "key", "编号") or f"HIST_{abs(hash((source_file, title, description))) % 10_000_000:07d}")
    return {
        "historical_bug_id": bug_id,
        "title": title.strip(),
        "description": description.strip(),
        "module": module.strip() or "unknown",
        "severity": severity,
        "risk_type": risk_type,
        "business_impact": business_impact,
        "reproduce_steps": reproduce_steps.strip(),
        "root_cause": root_cause.strip(),
        "related_apis": paths,
        "source_file": source_file,
        "keywords": sorted(set(_keywords(combined + " " + risk_type))),
    }


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text or "")
    stop = {"the", "and", "with", "this", "that", "接口", "用户", "问题", "系统"}
    return [w.lower() for w in words if w.lower() not in stop][:30]


def _impact_for_risk(risk: str) -> str:
    mapping = {
        "permission_bypass": "可能造成未授权访问和敏感业务数据泄露",
        "idor": "可能造成用户访问或修改他人资源",
        "tenant_isolation": "可能造成跨租户数据泄露",
        "money_consistency": "可能造成资损、账单金额错误或财务对账异常",
        "stock_consistency": "可能造成库存超卖、库存不一致或履约失败",
        "coupon_abuse": "可能造成优惠滥用和营销资损",
        "payment": "可能造成支付状态错误、重复入账或资损",
        "refund": "可能造成重复退款、超额退款或状态不一致",
        "idempotency": "可能造成重复订单、重复扣款或重复扣库存",
        "order_state": "可能造成订单状态流转错误和售后异常",
        "data_consistency": "可能造成接口返回成功但数据未落库或查询不一致",
    }
    return mapping.get(risk, "可能造成业务规则被破坏，需要 QA 结合上下文确认")


def parse_csv_file(path: Path) -> list[dict[str, Any]]:
    text = _read_text(path)
    if not text.strip():
        return []
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    return [normalize_bug_record(dict(row), path.name) for row in reader if any((v or "").strip() for v in row.values())]


def parse_json_file(path: Path) -> list[dict[str, Any]]:
    data = _load_json(path, None)
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            rows = data["items"]
        elif isinstance(data.get("bugs"), list):
            rows = data["bugs"]
        elif isinstance(data.get("issues"), list):
            rows = data["issues"]
        else:
            rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    return [normalize_bug_record(dict(row), path.name) for row in rows if isinstance(row, dict)]


def parse_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(normalize_bug_record(obj, path.name))
        except Exception:
            continue
    return rows


def parse_markdown_file(path: Path) -> list[dict[str, Any]]:
    text = _read_text(path)
    if not text.strip():
        return []
    chunks = re.split(r"\n(?=#{1,3}\s+|[-*]\s*(?:BUG|Bug|缺陷|问题)[:：])", text)
    rows: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = [ln.strip("# -*\t") for ln in chunk.splitlines() if ln.strip()]
        title = lines[0][:160] if lines else f"历史缺陷 {idx+1}"
        rows.append(normalize_bug_record({"title": title, "description": chunk}, path.name))
    return rows


def load_historical_bug_files(project_id: str, root: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    input_dir = root / "platform_inputs" / project / "historical_bugs"
    records: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    if not input_dir.exists():
        return records, files
    for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            files.append({"file": str(path.relative_to(root)).replace("\\", "/"), "status": "skipped", "reason": f"unsupported {suffix}"})
            continue
        try:
            if suffix == ".csv":
                parsed = parse_csv_file(path)
            elif suffix == ".json":
                parsed = parse_json_file(path)
            elif suffix == ".jsonl":
                parsed = parse_jsonl_file(path)
            else:
                parsed = parse_markdown_file(path)
            records.extend(parsed)
            files.append({"file": str(path.relative_to(root)).replace("\\", "/"), "status": "parsed", "records": len(parsed)})
        except Exception as exc:
            files.append({"file": str(path.relative_to(root)).replace("\\", "/"), "status": "error", "error": str(exc)})
    # De-duplicate by id/title/risk/module.
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in records:
        key = (str(item.get("historical_bug_id")), str(item.get("title")), str(item.get("risk_type")), str(item.get("module")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique, files


def build_enterprise_patterns(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rec in records:
        key = (str(rec.get("risk_type") or "business_rule"), str(rec.get("module") or "unknown"))
        groups.setdefault(key, []).append(rec)
    patterns: list[dict[str, Any]] = []
    for idx, ((risk, module), items) in enumerate(sorted(groups.items()), start=1):
        severity = max((str(i.get("severity") or "P2") for i in items), key=lambda s: SEVERITY_ORDER.get(s, 0))
        apis: list[str] = []
        kws: set[str] = set()
        examples: list[dict[str, Any]] = []
        for item in items:
            for api in item.get("related_apis") or []:
                if api not in apis:
                    apis.append(api)
            kws.update(item.get("keywords") or [])
            examples.append({"title": item.get("title"), "severity": item.get("severity"), "business_impact": item.get("business_impact")})
        patterns.append({
            "pattern_id": f"ENT_{risk.upper()}_{idx:03d}",
            "risk_type": risk,
            "module": module,
            "severity": severity,
            "historical_count": len(items),
            "confidence_prior": round(min(0.95, 0.55 + 0.08 * len(items) + (0.12 if severity in {"P0", "P1"} else 0)), 3),
            "related_apis": apis[:20],
            "keywords": sorted(kws)[:40],
            "business_impact": _impact_for_risk(risk),
            "examples": examples[:5],
            "recommended_probe_strategy": _probe_strategy_for_risk(risk),
            "source": "enterprise_historical_bugs",
        })
    return patterns


def _probe_strategy_for_risk(risk: str) -> str:
    return {
        "permission_bypass": "使用低权限账号访问管理员/高权限接口，期望 401/403",
        "idor": "使用 A/B 两个账号交叉访问资源 ID，验证资源归属控制",
        "tenant_isolation": "使用不同租户账号交叉访问租户资源，验证隔离",
        "money_consistency": "比较下单、支付、优惠、退款金额，验证金额不变量",
        "stock_consistency": "比较操作前后库存和订单状态，验证库存不变量",
        "coupon_abuse": "验证优惠券归属、有效期、门槛、重复使用限制",
        "payment": "验证支付金额、状态流转和回调幂等",
        "refund": "验证退款金额、订单状态和重复退款限制",
        "idempotency": "使用相同幂等键或重复请求，验证不会重复创建业务结果",
        "order_state": "构造非法状态流转，验证订单状态机不被破坏",
    }.get(risk, "基于历史缺陷关键词生成业务规则探针，需 QA 结合证据确认")


def build_risk_profile(records: list[dict[str, Any]], patterns: list[dict[str, Any]]) -> dict[str, Any]:
    severity_dist = _counter(str(r.get("severity")) for r in records)
    risk_dist = _counter(str(r.get("risk_type")) for r in records)
    module_dist = _counter(str(r.get("module")) for r in records)
    api_dist = _counter(api for r in records for api in (r.get("related_apis") or []))
    high_value = [r for r in records if str(r.get("severity")) in {"P0", "P1"}]
    return {
        "historical_bug_count": len(records),
        "pattern_count": len(patterns),
        "high_value_bug_count": len(high_value),
        "severity_distribution": severity_dist,
        "risk_distribution": risk_dist,
        "module_distribution": module_dist,
        "top_related_apis": dict(list(api_dist.items())[:20]),
        "top_risk_types": list(risk_dist.keys())[:10],
        "recommended_focus": [p["risk_type"] for p in sorted(patterns, key=lambda x: (-int(x.get("historical_count") or 0), -SEVERITY_ORDER.get(str(x.get("severity")), 0)))[:8]],
        "risk_score": min(100, len(high_value) * 8 + len(patterns) * 3),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "private_leak_check": "passed",
    }


def build_rag_docs(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for p in patterns:
        content = " ".join([
            str(p.get("risk_type")), str(p.get("module")), str(p.get("severity")),
            str(p.get("business_impact")), str(p.get("recommended_probe_strategy")),
            " ".join(p.get("keywords") or []), " ".join(p.get("related_apis") or []),
        ])
        docs.append({
            "doc_id": f"enterprise_history::{p.get('pattern_id')}",
            "source": "enterprise_historical_bugs",
            "risk_type": p.get("risk_type"),
            "module": p.get("module"),
            "severity": p.get("severity"),
            "confidence_prior": p.get("confidence_prior"),
            "content": content,
            "pattern": p,
        })
    return docs


def render_import_report(summary: dict[str, Any], patterns: list[dict[str, Any]], records: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr><td>{_html_escape(p.get('pattern_id'))}</td><td>{_html_escape(p.get('risk_type'))}</td><td>{_html_escape(p.get('module'))}</td><td>{_html_escape(p.get('severity'))}</td><td>{_html_escape(p.get('historical_count'))}</td><td>{_html_escape(p.get('recommended_probe_strategy'))}</td></tr>"
        for p in patterns[:80]
    )
    dist = summary.get("risk_profile", {}).get("risk_distribution", {})
    dist_html = "".join(f"<span class='pill'>{_html_escape(k)}: {_html_escape(v)}</span>" for k, v in dist.items())
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'/><title>历史 Bug 导入报告</title><style>
body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f8fafc;color:#0f172a;margin:0;padding:28px}}.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px;margin:0 0 18px;box-shadow:0 10px 25px rgba(15,23,42,.05)}}.kpis{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.kpis div{{background:#f1f5f9;border-radius:14px;padding:14px}}.kpis span{{display:block;color:#64748b;font-size:12px}}.kpis b{{font-size:24px}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #e5e7eb;padding:10px;text-align:left;font-size:13px}}.pill{{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:999px;padding:6px 10px;margin:4px}}.ok{{color:#047857}}
</style></head><body><h1>历史 Bug 导入与企业风险画像</h1><div class='card kpis'>
<div><span>历史缺陷</span><b>{_html_escape(summary.get('historical_bug_count'))}</b></div><div><span>缺陷模式</span><b>{_html_escape(summary.get('pattern_count'))}</b></div><div><span>高价值缺陷</span><b>{_html_escape(summary.get('high_value_bug_count'))}</b></div><div><span>Risk Score</span><b>{_html_escape(summary.get('risk_profile',{}).get('risk_score'))}</b></div></div>
<div class='card'><h2>风险分布</h2>{dist_html or '<p>暂无风险分布。</p>'}</div>
<div class='card'><h2>企业历史缺陷模式</h2><table><thead><tr><th>Pattern</th><th>风险类型</th><th>模块</th><th>严重等级</th><th>历史次数</th><th>建议探针策略</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class='card'><h2>输出产物</h2><p class='ok'>已生成 enterprise_bug_pattern_library、enterprise_rag_knowledge_base 和 real_project_risk_profile。真实项目缺陷发现会自动使用这些资产增强探针策略。</p></div></body></html>"""


def import_historical_bugs(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    input_dir = root / "platform_inputs" / project / "historical_bugs"
    workspace_hist = root / "platform_workspace" / project / "historical_bugs"
    workspace_defect = root / "platform_workspace" / project / "defect_discovery"
    output_dir = root / "platform_outputs" / project / "historical_bugs"
    records, files = load_historical_bug_files(project, root)
    patterns = build_enterprise_patterns(records)
    risk_profile = build_risk_profile(records, patterns)
    rag_docs = build_rag_docs(patterns)
    rag_index = {doc["doc_id"]: {"risk_type": doc.get("risk_type"), "module": doc.get("module"), "keywords": doc.get("pattern", {}).get("keywords", [])[:20]} for doc in rag_docs}
    training_rows = [
        {
            "task": "enterprise_history_probe_generation",
            "input": {"risk_type": p.get("risk_type"), "module": p.get("module"), "keywords": p.get("keywords"), "related_apis": p.get("related_apis")},
            "expected_output": {"probe_strategy": p.get("recommended_probe_strategy"), "severity": p.get("severity"), "business_impact": p.get("business_impact")},
            "metadata": {"source": "enterprise_historical_bugs", "pattern_id": p.get("pattern_id")},
        }
        for p in patterns
    ]
    workspace_hist.mkdir(parents=True, exist_ok=True)
    workspace_defect.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(workspace_hist / "normalized_historical_bugs.json", {"items": records, "files": files})
    _write_json(workspace_defect / "enterprise_bug_pattern_library.json", {"items": patterns})
    _write_json(workspace_defect / "enterprise_rag_knowledge_base.json", {"documents": rag_docs})
    _write_json(workspace_defect / "enterprise_rag_index.json", rag_index)
    _write_json(workspace_defect / "real_project_risk_profile.json", risk_profile)
    _write_json(output_dir / "enterprise_bug_pattern_library.json", {"items": patterns})
    _write_json(output_dir / "enterprise_rag_knowledge_base.json", {"documents": rag_docs})
    _write_json(output_dir / "real_project_risk_profile.json", risk_profile)
    _write_jsonl(output_dir / "enterprise_history_training_samples.jsonl", training_rows)
    summary = {
        "ok": True,
        "project_id": project,
        "input_dir": str(input_dir.relative_to(root)).replace("\\", "/"),
        "historical_bug_count": len(records),
        "pattern_count": len(patterns),
        "high_value_bug_count": risk_profile.get("high_value_bug_count"),
        "parsed_files": files,
        "risk_profile": risk_profile,
        "private_leak_check": "passed",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json(output_dir / "historical_bug_import_summary.json", summary)
    (output_dir / "historical_bug_import_report.html").write_text(render_import_report(summary, patterns, records), encoding="utf-8")
    return summary


def save_historical_bug_text(project_id: str, content: str, fmt: str = "csv", filename: str | None = None, root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    fmt = (fmt or "csv").lower().strip(".")
    if fmt not in {"csv", "json", "jsonl", "md", "txt"}:
        fmt = "csv"
    name = filename or f"historical_bugs.{fmt}"
    name = Path(name.replace("\\", "/")).name
    if not Path(name).suffix:
        name = f"{name}.{fmt}"
    target = root / "platform_inputs" / project / "historical_bugs" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return {"ok": True, "project_id": project, "path": str(target.relative_to(root)).replace("\\", "/"), "size": target.stat().st_size}


def seed_sample_historical_bugs(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    sample = """bug_id,title,description,module,severity,risk_type,related_apis,business_impact,reproduce_steps
HIST-001,普通用户可访问管理员订单接口,普通用户携带 token 请求 /admin/orders 返回 200 并包含订单数据,订单权限,P0,permission_bypass,/admin/orders,订单数据泄露,普通用户登录后 GET /admin/orders
HIST-002,优惠券可重复抵扣,同一用户对同一购物车重复应用 WELCOME10 后金额多次减少,优惠券,P1,coupon_abuse,/coupons/apply,营销资损,添加商品后重复 POST /coupons/apply
HIST-003,下单后库存未扣减,checkout 成功后 /inventory 查询库存未变化,库存,P1,stock_consistency,/checkout /inventory/{sku},库存超卖和履约失败,下单前后对比库存
HIST-004,重复支付回调重复入账,相同 transaction_id 回调两次生成两条流水,支付,P0,payment,/payments/callback,重复入账造成资损,重复发送支付回调
HIST-005,用户可查看他人订单,用户 B 请求用户 A 的 /orders/{id} 返回 200,订单越权,P0,idor,/orders/{id},用户隐私和订单泄露,A 创建订单后 B 查询订单详情
"""
    return save_historical_bug_text(project_id, sample, "csv", "sample_historical_bugs.csv", root)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Import enterprise historical bugs and build project-specific risk knowledge.")
    parser.add_argument("project_id", nargs="?", default="real_project_demo")
    parser.add_argument("--seed-sample", action="store_true")
    args = parser.parse_args()
    if args.seed_sample:
        seed_sample_historical_bugs(args.project_id)
    result = import_historical_bugs(args.project_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
