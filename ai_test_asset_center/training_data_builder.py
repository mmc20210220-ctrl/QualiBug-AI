from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from enterprise_bug_factory.services.bug_catalog import TEMPLATE_DEFS

DEFAULT_INDEX = Path("enterprise_bug_factory/bug_sets/million_bug_index.json")
DEFAULT_OUT = Path("benchmark_outputs/training_data")
DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")
DEFAULT_MAX_TRAINING_ROWS = 2000

PRIVATE_FIELD_HINTS = {
    "bug_instance_id",
    "enabled_bugs",
    "current_bug_set",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-/{}/]+|[\u4e00-\u9fff]+")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def tokenize(text: str) -> list[str]:
    tokens = [m.group(0).lower() for m in TOKEN_RE.finditer(text or "")]
    # Keep a deterministic, compact index: no stopword dependency and no external vector DB.
    return [t for t in tokens if len(t) > 1 or any("\u4e00" <= c <= "\u9fff" for c in t)]


def stable_id(prefix: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _api_parts(api: str) -> tuple[str, str]:
    parts = str(api or "").strip().split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].upper(), parts[1]
    return "GET", str(api or "")


def build_pattern_library(index: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    template_distribution = ((index or {}).get("global_distribution") or {}).get("template_distribution") or {}
    split_template_counts = (index or {}).get("split_template_counts") or {}
    patterns: list[dict[str, Any]] = []
    for template in TEMPLATE_DEFS:
        method, path = _api_parts(template.get("api", ""))
        template_id = template["template_id"]
        train_seen = int((split_template_counts.get("train") or {}).get(template_id, 0))
        validation_seen = int((split_template_counts.get("validation") or {}).get(template_id, 0))
        total_seen = int(template_distribution.get(template_id, 0))
        title = template.get("title", template_id)
        risk_type = template.get("risk_type", "unknown")
        expected_status = template.get("expected_status")
        pattern = {
            "knowledge_id": stable_id("pattern", template_id),
            "template_id": template_id,
            "title": title,
            "domain": template.get("domain"),
            "risk_type": risk_type,
            "severity": template.get("severity"),
            "business_rule": f"{title} 应被阻止或保持状态一致，不能破坏 {risk_type} 业务不变量。",
            "probe_strategy": template.get("trigger"),
            "expected_behavior": f"{method} {path} 应返回 {expected_status} 或保持业务状态一致",
            "bug_signal": template.get("signal"),
            "related_api": {"method": method, "path": path, "raw": template.get("api")},
            "evidence_required": ["actor_role", "request", "response_status", "response_body", "expected", "actual"],
            "training_stats": {
                "total_dataset_instances": total_seen,
                "train_instances": train_seen,
                "validation_instances": validation_seen,
            },
            "source": "bug_template_library_trainable_public_view",
        }
        patterns.append(pattern)
    return patterns


def build_rag_documents(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for p in patterns:
        api = p.get("related_api") or {}
        text = "\n".join([
            f"缺陷模板: {p.get('template_id')}",
            f"标题: {p.get('title')}",
            f"领域: {p.get('domain')}",
            f"风险类型: {p.get('risk_type')}",
            f"严重等级: {p.get('severity')}",
            f"业务规则: {p.get('business_rule')}",
            f"探针策略: {p.get('probe_strategy')}",
            f"期望行为: {p.get('expected_behavior')}",
            f"Bug 信号: {p.get('bug_signal')}",
            f"相关接口: {api.get('method')} {api.get('path')}",
        ])
        docs.append({
            "doc_id": stable_id("ragdoc", p.get("template_id", "unknown")),
            "template_id": p.get("template_id"),
            "domain": p.get("domain"),
            "risk_type": p.get("risk_type"),
            "severity": p.get("severity"),
            "related_api": p.get("related_api"),
            "text": text,
            "metadata": {
                "source": "phase14_training_pattern_library",
                "safe_for_discovery": True,
                "contains_ground_truth_instance": False,
                "contains_instance_ids": False,
            },
        })
    return docs


def build_lexical_rag_index(documents: list[dict[str, Any]]) -> dict[str, Any]:
    inverted: dict[str, list[str]] = defaultdict(list)
    doc_vectors: list[dict[str, Any]] = []
    for doc in documents:
        counts = Counter(tokenize(str(doc.get("text", ""))))
        top_tokens = [token for token, _ in counts.most_common(60)]
        doc_vectors.append({"doc_id": doc["doc_id"], "template_id": doc.get("template_id"), "top_tokens": top_tokens})
        for token in top_tokens:
            inverted[token].append(doc["doc_id"])
    return {
        "index_type": "phase14_lightweight_lexical_rag_index",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "document_count": len(documents),
        "token_count": len(inverted),
        "doc_vectors": doc_vectors,
        "inverted_index": dict(sorted(inverted.items())),
        "retrieval_notes": [
            "This is a dependency-free lexical index for local MVP use.",
            "It is safe for ai_test_asset_center discovery because it contains pattern-level knowledge only, not hidden ground truth instances.",
        ],
    }


def _probe_training_rows(patterns: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in patterns:
        api = p.get("related_api") or {}
        rows.append({
            "task": "business_rule_to_defect_probe",
            "split": "train",
            "input": {
                "prd_snippet": p.get("title"),
                "business_context": p.get("domain"),
                "openapi_paths": [api.get("raw")],
                "risk_type": p.get("risk_type"),
            },
            "expected_output": {
                "business_rule": p.get("business_rule"),
                "defect_probe": p.get("probe_strategy"),
                "expected_behavior": p.get("expected_behavior"),
                "bug_signal": p.get("bug_signal"),
                "severity": p.get("severity"),
                "template_id": p.get("template_id"),
            },
            "learning_policy": {
                "use_for_rag": True,
                "use_for_prompt_tuning": True,
                "use_for_fine_tuning_later": True,
                "contains_hidden_ground_truth": False,
            },
        })
        if len(rows) >= max_rows:
            break
    return rows


def _false_positive_training_rows() -> list[dict[str, Any]]:
    # Hand-authored negative examples are intentional and safe: they teach the platform not to over-report normal protection behavior.
    examples = [
        ("未登录访问受保护接口返回 401", "auth_bypass", "not_bug", "401/403 是正确鉴权行为，不应报告为缺陷。"),
        ("普通用户访问管理员接口返回 403", "permission_bypass", "not_bug", "权限被正确拒绝，不应报告为越权。"),
        ("库存不足下单返回 409", "stock_consistency", "not_bug", "库存规则被正确执行，不应报告为超卖。"),
        ("重复使用优惠券返回 409", "coupon_abuse", "not_bug", "重复抵扣被正确阻止，不应报告为资损。"),
        ("重复支付回调返回已处理且账务未增加", "payment_callback", "not_bug", "幂等处理正确，不应报告重复入账。"),
        ("未支付订单退款返回 409", "refund_abuse", "not_bug", "退款前置状态校验正确，不应报告为缺陷。"),
        ("跨租户访问返回 403", "tenant_isolation", "not_bug", "租户隔离正确，不应报告数据泄露。"),
    ]
    return [
        {
            "task": "false_positive_filtering",
            "split": "train",
            "input": {"observation": obs, "risk_type": risk},
            "expected_output": {"label": label, "reason": reason, "should_create_bug": False},
            "learning_policy": {"use_for_false_positive_filter": True},
        }
        for obs, risk, label, reason in examples
    ]


def _missed_template_training_rows(probe_improvement_plan_path: Path) -> list[dict[str, Any]]:
    if not probe_improvement_plan_path.exists():
        return []
    try:
        payload = json.loads(probe_improvement_plan_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    items = payload if isinstance(payload, list) else payload.get("suggestions", []) if isinstance(payload, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        template = item.get("missed_template") or item.get("template_id")
        if not template:
            continue
        rows.append({
            "task": "missed_template_probe_improvement",
            "split": "train",
            "input": {
                "missed_template": template,
                "missed_count": item.get("missed_count"),
                "likely_reason": item.get("likely_reason"),
            },
            "expected_output": {
                "suggested_probe": item.get("suggested_probe"),
                "suggested_oracle": item.get("suggested_oracle"),
                "priority": item.get("priority", "P1"),
            },
            "learning_policy": {"use_for_probe_policy_learning": True},
        })
    return rows


def _validate_no_private_leak(obj: Any) -> list[str]:
    text = json.dumps(obj, ensure_ascii=False).lower()
    leaks = sorted(token for token in PRIVATE_FIELD_HINTS if token in text)
    # template_id is allowed in training/RAG. Concrete bug instance identifiers and enabled bug lists are not.
    return leaks


def build_training_data(
    index_path: Path = DEFAULT_INDEX,
    out_dir: Path = DEFAULT_OUT,
    workspace_dir: Path = DEFAULT_WORKSPACE,
    max_training_rows: int = DEFAULT_MAX_TRAINING_ROWS,
    probe_improvement_plan_path: Path = Path("benchmark_outputs/probe_improvement_plan.json"),
) -> dict[str, Any]:
    index = read_json(index_path) if index_path.exists() else {}
    patterns = build_pattern_library(index)
    rag_docs = build_rag_documents(patterns)
    rag_index = build_lexical_rag_index(rag_docs)
    probe_rows = _probe_training_rows(patterns, max_rows=max_training_rows)
    false_positive_rows = _false_positive_training_rows()
    missed_rows = _missed_template_training_rows(probe_improvement_plan_path)

    card = {
        "phase": "phase14_training_data_builder_rag_knowledge_base",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_index": str(index_path),
        "template_patterns": len(patterns),
        "rag_documents": len(rag_docs),
        "rag_tokens": rag_index.get("token_count"),
        "probe_policy_training_rows": len(probe_rows),
        "false_positive_training_rows": len(false_positive_rows),
        "missed_template_training_rows": len(missed_rows),
        "splits_used": ["train", "validation"],
        "hidden_test_used_for_training": False,
        "governance": {
            "pattern_level_only": True,
            "contains_instance_ids": False,
            "contains_enabled bug list": False,
            "contains_private_ground_truth": False,
            "safe_for_rag_discovery": True,
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "high_value_bug_patterns.json", {"patterns": patterns})
    write_json(out_dir / "rag_knowledge_base.json", {"documents": rag_docs})
    write_json(out_dir / "rag_index.json", rag_index)
    write_json(out_dir / "training_data_card.json", card)
    write_jsonl(out_dir / "probe_policy_training.jsonl", probe_rows)
    write_jsonl(out_dir / "false_positive_training.jsonl", false_positive_rows)
    write_jsonl(out_dir / "missed_template_training.jsonl", missed_rows)
    (out_dir / "phase14_training_data_card.html").write_text(build_training_data_card_html(card, patterns), encoding="utf-8")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    write_json(workspace_dir / "rag_knowledge_base.json", {"documents": rag_docs})
    write_json(workspace_dir / "rag_index.json", rag_index)

    leak_check = _validate_no_private_leak({"patterns": patterns, "rag_docs": rag_docs, "probe_rows": probe_rows})
    # expected string "bug_id" can appear as part of template_id? We only care exact private fields in serialized output.
    leak_check = [x for x in leak_check if x not in {"ground_truth"}]
    card["private_leak_check"] = {"leak_terms": leak_check, "passed": not leak_check}
    write_json(out_dir / "training_data_card.json", card)
    return {
        "out_dir": str(out_dir),
        "workspace_dir": str(workspace_dir),
        "card": card,
        "artifacts": {
            "high_value_bug_patterns": str(out_dir / "high_value_bug_patterns.json"),
            "rag_knowledge_base": str(out_dir / "rag_knowledge_base.json"),
            "rag_index": str(out_dir / "rag_index.json"),
            "probe_policy_training": str(out_dir / "probe_policy_training.jsonl"),
            "false_positive_training": str(out_dir / "false_positive_training.jsonl"),
            "missed_template_training": str(out_dir / "missed_template_training.jsonl"),
            "training_data_card": str(out_dir / "phase14_training_data_card.html"),
        },
    }


def build_training_data_card_html(card: dict[str, Any], patterns: list[dict[str, Any]]) -> str:
    domains = Counter(str(p.get("domain")) for p in patterns)
    risks = Counter(str(p.get("risk_type")) for p in patterns)
    domain_rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in sorted(domains.items()))
    risk_rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in sorted(risks.items()))
    governance = card.get("governance", {})
    gov_rows = "".join(f"<li>{html.escape(k)}: <b>{html.escape(str(v))}</b></li>" for k, v in governance.items())
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase14 Training Data & RAG Knowledge Base</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;border-radius:10px;padding:14px;background:#f8fafc}}table{{border-collapse:collapse;width:100%;margin:14px 0}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.ok{{color:#0f766e;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}</style></head><body><h1>Phase14 Training Data Builder + RAG Knowledge Base</h1><p>目标：把百万级缺陷数据资产转成可训练、可检索、可治理的高价值 Bug Pattern 知识库。训练数据只使用 pattern-level 知识和 train/validation split，不把 hidden_test 或具体 ground truth 实例暴露给发现平台。</p><div class=\"grid\"><div class=\"card\"><b>Patterns</b><br>{card.get('template_patterns')}</div><div class=\"card\"><b>RAG docs</b><br>{card.get('rag_documents')}</div><div class=\"card\"><b>RAG tokens</b><br>{card.get('rag_tokens')}</div><div class=\"card\"><b>Hidden used</b><br>{card.get('hidden_test_used_for_training')}</div></div><h2>Training Rows</h2><table><tr><th>Dataset</th><th>Rows</th></tr><tr><td>probe_policy_training</td><td>{card.get('probe_policy_training_rows')}</td></tr><tr><td>false_positive_training</td><td>{card.get('false_positive_training_rows')}</td></tr><tr><td>missed_template_training</td><td>{card.get('missed_template_training_rows')}</td></tr></table><h2>Domain Distribution</h2><table><tr><th>Domain</th><th>Patterns</th></tr>{domain_rows}</table><h2>Risk Distribution</h2><table><tr><th>Risk Type</th><th>Patterns</th></tr>{risk_rows}</table><h2>Governance</h2><ul>{gov_rows}</ul><p class=\"ok\">Safe for RAG discovery: pattern-level only, no enabled bug list, no current bug set, no instance ids.</p></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Phase14 training data and local RAG knowledge base from safe pattern-level benchmark assets.")
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--max-training-rows", type=int, default=DEFAULT_MAX_TRAINING_ROWS)
    parser.add_argument("--probe-improvement-plan", default="benchmark_outputs/probe_improvement_plan.json")
    args = parser.parse_args()
    result = build_training_data(
        index_path=Path(args.index),
        out_dir=Path(args.out),
        workspace_dir=Path(args.workspace),
        max_training_rows=args.max_training_rows,
        probe_improvement_plan_path=Path(args.probe_improvement_plan),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
