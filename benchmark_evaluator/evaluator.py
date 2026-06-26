from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from benchmark_evaluator.matcher import match_bug
from benchmark_evaluator.metrics import compute_metrics
from benchmark_evaluator.report_builder import build_report, missed_by_template, missed_by_type


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_optional_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def evaluate(discovered_path: Path, ground_truth_path: Path, output_dir: Path) -> dict:
    discovered_payload = read_json(discovered_path)
    discovered = discovered_payload.get("bugs", [])
    truth_payload = read_json(ground_truth_path)
    truth = truth_payload.get("bugs", [])
    used: set[str] = set()
    matches: list[dict] = []
    false_positives: list[dict] = []
    for bug in discovered:
        gt = match_bug(bug, truth, used)
        if gt:
            gt_id = str(gt.get("bug_id") or gt.get("bug_instance_id"))
            used.add(gt_id)
            match_type = gt.pop("__match_type", "template_match")
            match_score = gt.pop("__match_score", 0)
            matches.append({"match_type": match_type, "match_score": match_score, "discovered": bug, "ground_truth": gt})
        else:
            false_positives.append(bug)
    improvement_plan = build_probe_improvement_plan(truth, used)
    strategy = read_optional_json(Path("platform_workspace/enterprise_shop/defect_discovery/probe_generation_strategy.json"))
    adaptive_policy = read_optional_json(Path("platform_workspace/enterprise_shop/defect_discovery/learned_probe_policy.json"))
    scorecard = {
        "discovery_mode": discovered_payload.get("discovery_mode", "unknown"),
        "benchmark_compat_enabled": discovered_payload.get("benchmark_compat_enabled", None),
        "ground_truth_mode": truth_payload.get("mode", "unknown"),
        "ground_truth_seed": truth_payload.get("seed"),
        "metrics": compute_metrics(truth, discovered, matches),
        "matches": matches,
        "false_positives": false_positives,
        "false_positive_reasons": [{"discovered_bug_id": b.get("discovered_bug_id"), "reason": "No strict multi-factor match in hidden ground truth"} for b in false_positives],
        "missed_summary": missed_by_type(truth, used),
        "missed_templates": missed_by_template(truth, used),
        "probe_improvement_plan": improvement_plan,
        "adaptive_policy_summary": {
            "policy_version": adaptive_policy.get("policy_version"),
            "template_policies": len(adaptive_policy.get("template_policies", [])),
            "adaptive_policy_probe_count": strategy.get("adaptive_policy_probe_count", 0),
            "feedback_learning_probe_count": strategy.get("feedback_learning_probe_count", 0),
            "benchmark_compat_probe_count": strategy.get("benchmark_compat_probe_count", 0),
            "probe_count": strategy.get("probe_count", 0),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "benchmark_scorecard.json").write_text(json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "benchmark_report.html").write_text(build_report(scorecard), encoding="utf-8")
    (output_dir / "training_samples.jsonl").write_text(build_training_samples(matches, false_positives), encoding="utf-8")
    (output_dir / "missed_bug_analysis.md").write_text(build_missed_analysis(scorecard), encoding="utf-8")
    (output_dir / "probe_improvement_plan.json").write_text(json.dumps(improvement_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return scorecard


def build_probe_improvement_plan(truth: list[dict], used: set[str]) -> list[dict]:
    missed = [b for b in truth if b.get("bug_id") not in used]
    grouped: dict[str, list[dict]] = {}
    for bug in missed:
        grouped.setdefault(str(bug.get("template_id", "UNKNOWN_TEMPLATE")), []).append(bug)
    rows = []
    for template, items in sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True):
        sample = items[0]
        rows.append({
            "missed_template": template,
            "missed_count": len(items),
            "severity": sample.get("severity"),
            "risk_type": sample.get("risk_type"),
            "related_apis": sample.get("related_apis", []),
            "likely_reason": likely_reason(sample),
            "suggested_probe": suggested_probe(sample),
            "suggested_oracle": suggested_oracle(sample),
            "priority": sample.get("severity", "P2"),
        })
    return rows[:30]


def likely_reason(bug: dict) -> str:
    risk = bug.get("risk_type")
    if risk in {"payment_callback", "idempotency"}:
        return "缺少重复提交/回调幂等探针，或探针没有校验二次状态变化。"
    if risk in {"stock_consistency", "state_consistency"}:
        return "缺少前置状态与后置状态一致性校验。"
    if risk in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation"}:
        return "缺少角色/租户/资源归属维度的越权探针。"
    if risk in {"money_consistency", "coupon_abuse", "refund_abuse"}:
        return "缺少金额、优惠、退款边界和复核探针。"
    return "当前 Pattern Library 未覆盖该业务不变量。"


def suggested_probe(bug: dict) -> str:
    api = ", ".join(bug.get("related_apis", []))
    return f"针对 {bug.get('template_id')} 生成探针：执行 {api}，校验 {bug.get('expected_behavior')}，若 {bug.get('oracle', {}).get('bug_signal')} 则报 Bug。"


def suggested_oracle(bug: dict) -> str:
    oracle = bug.get("oracle", {})
    return f"oracle_type={oracle.get('type')} expected_status={oracle.get('expected_status')} bug_signal={oracle.get('bug_signal')}"


def build_missed_analysis(scorecard: dict) -> str:
    lines = ["# 漏检分析报告", "", "## 漏检模板排行", ""]
    for item in scorecard.get("probe_improvement_plan", []):
        lines += [
            f"### {item['missed_template']}",
            f"- 漏检数量：{item['missed_count']}",
            f"- 严重等级：{item['severity']}",
            f"- 风险类型：{item['risk_type']}",
            f"- 可能原因：{item['likely_reason']}",
            f"- 建议探针：{item['suggested_probe']}",
            f"- 建议 Oracle：{item['suggested_oracle']}",
            "",
        ]
    return "\n".join(lines)


def build_training_samples(matches: list[dict], false_positives: list[dict]) -> str:
    rows = []
    for item in matches:
        bug = item["discovered"]
        gt = item["ground_truth"]
        rows.append(json.dumps({"input": {"openapi_paths": gt["related_apis"], "platform_probe": bug["probe_id"], "business_context": gt["domain"], "predicted_template_id": bug.get("predicted_template_id")}, "expected_output": {"business_rule": gt["expected_behavior"], "defect_probe": bug["title"], "expected_behavior": gt["expected_behavior"], "bug_signal": gt["oracle"]["bug_signal"], "severity": gt["severity"], "template_id": gt.get("template_id")}, "execution_feedback": {"found_bug": True, "matched_ground_truth": True, "match_type": item.get("match_type"), "match_score": item.get("match_score"), "is_high_value": gt["severity"] in {"P0", "P1"}, "false_positive": False}}, ensure_ascii=False))
    for bug in false_positives:
        rows.append(json.dumps({"input": {"openapi_paths": bug.get("related_apis", []), "platform_probe": bug.get("probe_id"), "business_context": bug.get("risk_type"), "predicted_template_id": bug.get("predicted_template_id")}, "expected_output": {"defect_probe": bug.get("title"), "severity": bug.get("severity")}, "execution_feedback": {"found_bug": True, "matched_ground_truth": False, "is_high_value": False, "false_positive": True}}, ensure_ascii=False))
    return "\n".join(rows) + ("\n" if rows else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovered", default="platform_outputs/enterprise_shop/defect_discovery/discovered_bugs.json")
    parser.add_argument("--ground-truth", default="enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json")
    parser.add_argument("--out", default="benchmark_outputs")
    args = parser.parse_args()
    scorecard = evaluate(Path(args.discovered), Path(args.ground_truth), Path(args.out))
    print(json.dumps(scorecard["metrics"], ensure_ascii=False, indent=2))
    print(f"report={Path(args.out) / 'benchmark_report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
