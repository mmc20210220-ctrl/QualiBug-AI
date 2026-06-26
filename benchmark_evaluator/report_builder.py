from __future__ import annotations

import json
from collections import Counter


def missed_by_type(truth: list[dict], used: set[str]) -> dict:
    counter = Counter()
    for item in truth:
        if item.get("bug_id") not in used:
            counter[item.get("risk_type", "unknown")] += 1
    return dict(counter)


def missed_by_template(truth: list[dict], used: set[str]) -> dict:
    counter = Counter()
    for item in truth:
        if item.get("bug_id") not in used:
            counter[item.get("template_id", "UNKNOWN_TEMPLATE")] += 1
    return dict(counter)


def build_report(scorecard: dict) -> str:
    m = scorecard["metrics"]
    match_rows = "\n".join(
        f"<tr><td>{item.get('match_type')}</td><td>{item.get('match_score')}</td><td>{item['ground_truth'].get('severity')}</td><td>{item['discovered'].get('title')}</td><td>{item['ground_truth'].get('template_id')}</td><td>{item['discovered'].get('bug_value_score')}</td></tr>"
        for item in scorecard.get("matches", [])[:200]
    )
    miss_rows = "\n".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in scorecard.get("missed_templates", {}).items())
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Benchmark 评测报告</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033;background:#f7f9fc}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;padding:14px;border-radius:10px;background:#fff}}table{{border-collapse:collapse;width:100%;margin-top:18px;background:#fff}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.note{{background:#fff;border:1px solid #d8dee9;padding:14px;border-radius:8px;margin:18px 0}}.warn{{color:#b45309}}</style></head><body><h1>Benchmark 评测报告</h1><div class=\"note\"><b>评测模式：</b>discovery_mode={scorecard.get('discovery_mode')}，benchmark_compat_enabled={scorecard.get('benchmark_compat_enabled')}，ground_truth_mode={scorecard.get('ground_truth_mode')}，seed={scorecard.get('ground_truth_seed')}。正式可信指标应优先查看 blind + hidden/random + clean baseline。</div><div class=\"grid\"><div class=\"card\">已知实例<br><b>{m.get('known_bug_instances')}</b></div><div class=\"card\">已知模板<br><b>{m.get('known_bug_templates')}</b></div><div class=\"card\">发现 Bug<br><b>{m.get('discovered_bugs')}</b></div><div class=\"card\">有效发现<br><b>{m.get('matched_true_positives')}</b></div><div class=\"card\">Instance Recall<br><b>{m.get('instance_recall')}</b></div><div class=\"card\">Template Recall<br><b>{m.get('template_recall')}</b></div><div class=\"card\">P0/P1 Instance Recall<br><b>{m.get('p0_p1_instance_recall')}</b></div><div class=\"card\">P0/P1 Template Recall<br><b>{m.get('p0_p1_template_recall')}</b></div><div class=\"card\">Exact Instance<br><b>{m.get('exact_instance_matches')}</b></div><div class=\"card\">Partial Instance<br><b>{m.get('partial_instance_matches')}</b></div><div class=\"card\">Template Match<br><b>{m.get('template_matches')}</b></div><div class=\"card\">误报<br><b>{m.get('false_positives')}</b></div><div class=\"card\">漏检实例<br><b>{m.get('missed_instances')}</b></div><div class=\"card\">漏检模板<br><b>{m.get('missed_templates')}</b></div><div class=\"card\">精确率<br><b>{m.get('precision')}</b></div><div class=\"card\">Clean误报率<br><b>{m.get('clean_mode_false_positive_rate')}</b></div></div><h2>每个发现 Bug 的证据匹配</h2><table><tr><th>匹配类型</th><th>匹配分</th><th>等级</th><th>发现标题</th><th>匹配模板</th><th>价值分</th></tr>{match_rows}</table><h2>漏检模板排行</h2><table><tr><th>模板</th><th>漏检数</th></tr>{miss_rows}</table><h2>探针改进建议</h2><pre>{json.dumps(scorecard.get('probe_improvement_plan', []), ensure_ascii=False, indent=2)}</pre></body></html>"""
