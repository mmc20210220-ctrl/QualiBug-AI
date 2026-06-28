"""
[DEPRECATED] RAG Quality Gate
Status: NEAR-ZOMBIE -- 0 active cross-references.
Roadmap: Quality gate for RAG pipeline outputs.
         Wire into rag_probe_generator.py pipeline.
See DEPRECATED.md for architecture decisions.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RAG_AB_SCORECARD = Path("benchmark_outputs/rag_ab/rag_ab_scorecard.json")
DEFAULT_OUTPUT_DIR = Path("benchmark_outputs/rag_quality_gate")
DEFAULT_TRAINING_CARD = Path("benchmark_outputs/training_data/phase14_training_data_card.json")
DEFAULT_RECOMMENDED_RAG_POLICY = Path("benchmark_outputs/rag_ab/recommended_rag_policy.json")
DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "min_precision": 0.90,
    "max_false_positive_rate": 0.05,
    "min_instance_recall_gain": 0.0,
    "min_template_recall_gain": -0.02,
    "min_quality_score_gain": -0.01,
    "max_probe_growth_ratio": 2.50,
    "max_rag_probe_count": 250,
    "max_rag_top_k": 8,
    "min_avg_retrieval_score": 0.05,
    "max_benchmark_compat_probe_count": 0,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _rows(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    rows = scorecard.get("ranked_results") or scorecard.get("results") or []
    return rows if isinstance(rows, list) else []


def recommended_policy(scorecard: dict[str, Any]) -> dict[str, Any]:
    policy = scorecard.get("recommended_rag_policy") or {}
    if isinstance(policy, dict) and policy:
        return dict(policy)
    rows = _rows(scorecard)
    if rows:
        return dict(rows[0])
    return {}


def baseline_policy(scorecard: dict[str, Any]) -> dict[str, Any]:
    for row in _rows(scorecard):
        if row.get("variant") == "no_rag":
            return dict(row)
    rows = _rows(scorecard)
    return dict(rows[0]) if rows else {}


def _anti_cheat(scorecard: dict[str, Any]) -> dict[str, Any]:
    anti = scorecard.get("anti_cheat") or {}
    if not isinstance(anti, dict):
        anti = {}
    return anti


def _training_card_summary(training_card: Path) -> dict[str, Any]:
    data = read_json(training_card, {})
    if not isinstance(data, dict):
        return {"available": False}
    return {
        "available": bool(data),
        "hidden_test_used_for_training": bool(data.get("hidden_test_used_for_training", False)),
        "private_leak_check": data.get("private_leak_check", "unknown"),
        "template_patterns": data.get("template_patterns") or data.get("template_pattern_count"),
        "rag_documents": data.get("rag_documents") or data.get("rag_document_count"),
    }


def build_gate_checks(
    policy: dict[str, Any],
    baseline: dict[str, Any],
    scorecard: dict[str, Any],
    training_card: Path = DEFAULT_TRAINING_CARD,
    thresholds: dict[str, float | int] | None = None,
) -> list[dict[str, Any]]:
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    anti = _anti_cheat(scorecard)
    train_summary = _training_card_summary(training_card)

    precision = safe_float(policy.get("precision"))
    fpr = safe_float(policy.get("false_positive_rate"))
    instance_recall = safe_float(policy.get("instance_recall"))
    template_recall = safe_float(policy.get("template_recall"))
    quality = safe_float(policy.get("quality_score"))
    probe_count = safe_int(policy.get("probe_count"))
    rag_probe_count = safe_int(policy.get("rag_probe_count"))
    rag_top_k = safe_int(policy.get("rag_top_k"))
    avg_retrieval_score = safe_float(policy.get("avg_retrieval_score"))
    compat = safe_int(policy.get("benchmark_compat_probe_count"))

    base_instance = safe_float(baseline.get("instance_recall"))
    base_template = safe_float(baseline.get("template_recall"))
    base_quality = safe_float(baseline.get("quality_score"))
    base_probe_count = max(1, safe_int(baseline.get("probe_count"), 1))
    instance_gain = instance_recall - base_instance
    template_gain = template_recall - base_template
    quality_gain = quality - base_quality
    probe_growth_ratio = round(probe_count / base_probe_count, 4) if base_probe_count else 0

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, severity: str, actual: Any, threshold: Any, reason: str) -> None:
        checks.append({
            "name": name,
            "passed": bool(passed),
            "severity": severity,
            "actual": actual,
            "threshold": threshold,
            "reason": reason,
        })

    add("blind_mode", scorecard.get("discovery_mode") == "blind", "critical", scorecard.get("discovery_mode"), "blind", "Formal RAG promotion must be evaluated in blind mode.")
    add("no_benchmark_compat", compat <= int(t["max_benchmark_compat_probe_count"]), "critical", compat, t["max_benchmark_compat_probe_count"], "RAG policy must not depend on benchmark_compat probes.")
    add("anti_cheat_private_ground_truth", anti.get("private_ground_truth_visible_to_discovery") is False, "critical", anti.get("private_ground_truth_visible_to_discovery"), False, "Discovery must not see private ground truth.")
    add("anti_cheat_benchmark_compat_disabled", anti.get("benchmark_compat_allowed") is False, "critical", anti.get("benchmark_compat_allowed"), False, "Formal RAG A/B must keep benchmark_compat disabled.")
    if train_summary.get("available"):
        add("hidden_test_not_used_for_training", train_summary.get("hidden_test_used_for_training") is False, "critical", train_summary.get("hidden_test_used_for_training"), False, "Hidden test split must not be used for RAG training.")
        add("training_private_leak_check", str(train_summary.get("private_leak_check")).lower() == "passed", "critical", train_summary.get("private_leak_check"), "passed", "Training/RAG assets must pass private leak checks.")
    else:
        add("training_card_available", False, "warning", "missing", "recommended", "Training data card not found; promotion can proceed only with review.")

    add("precision", precision >= float(t["min_precision"]), "critical", precision, t["min_precision"], "RAG policy must keep precision high.")
    add("false_positive_rate", fpr <= float(t["max_false_positive_rate"]), "critical", fpr, t["max_false_positive_rate"], "RAG policy must keep false positive rate controlled.")
    add("instance_recall_gain_vs_no_rag", instance_gain >= float(t["min_instance_recall_gain"]), "warning", round(instance_gain, 6), t["min_instance_recall_gain"], "RAG should improve or at least not regress instance recall versus no_rag.")
    add("template_recall_gain_vs_no_rag", template_gain >= float(t["min_template_recall_gain"]), "warning", round(template_gain, 6), t["min_template_recall_gain"], "RAG should not meaningfully regress template recall.")
    add("quality_score_gain_vs_no_rag", quality_gain >= float(t["min_quality_score_gain"]), "warning", round(quality_gain, 6), t["min_quality_score_gain"], "RAG quality score should not regress versus no_rag.")
    add("probe_growth_budget", probe_growth_ratio <= float(t["max_probe_growth_ratio"]), "warning", probe_growth_ratio, t["max_probe_growth_ratio"], "RAG probe growth must stay within governance budget.")
    add("rag_probe_count_budget", rag_probe_count <= int(t["max_rag_probe_count"]), "warning", rag_probe_count, t["max_rag_probe_count"], "RAG probe count should not grow without budget control.")
    add("rag_top_k_budget", rag_top_k <= int(t["max_rag_top_k"]), "warning", rag_top_k, t["max_rag_top_k"], "RAG top_k should remain bounded for execution cost and precision.")
    if rag_probe_count > 0:
        add("retrieval_score_floor", avg_retrieval_score >= float(t["min_avg_retrieval_score"]), "warning", avg_retrieval_score, t["min_avg_retrieval_score"], "Average retrieval score should be high enough to justify RAG probes.")
    else:
        add("rag_probe_present", False, "warning", rag_probe_count, "> 0", "Recommended policy contains no RAG probes; it may be no_rag baseline.")

    return checks


def decide_promotion(policy: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed_critical = [c for c in checks if not c.get("passed") and c.get("severity") == "critical"]
    failed_warning = [c for c in checks if not c.get("passed") and c.get("severity") == "warning"]
    if failed_critical:
        decision = "reject"
        reason = "Critical RAG quality gate checks failed. Do not promote this RAG policy."
    elif failed_warning:
        decision = "hold_for_review"
        reason = "No critical failure, but warning checks require review before promoting this RAG policy."
    else:
        decision = "promote"
        reason = "All blocking and warning checks passed. RAG policy can be promoted."
    return {
        "decision": decision,
        "rag_policy_variant": policy.get("variant"),
        "rag_top_k": policy.get("rag_top_k"),
        "reason": reason,
        "failed_critical_checks": [c["name"] for c in failed_critical],
        "failed_warning_checks": [c["name"] for c in failed_warning],
        "promotion_allowed": decision == "promote",
    }


def _metrics(policy: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "variant": policy.get("variant"),
        "rag_top_k": policy.get("rag_top_k"),
        "quality_score": policy.get("quality_score"),
        "instance_recall": policy.get("instance_recall"),
        "template_recall": policy.get("template_recall"),
        "p0_p1_template_recall": policy.get("p0_p1_template_recall"),
        "precision": policy.get("precision"),
        "false_positive_rate": policy.get("false_positive_rate"),
        "probe_count": policy.get("probe_count"),
        "rag_probe_count": policy.get("rag_probe_count"),
        "avg_retrieval_score": policy.get("avg_retrieval_score"),
        "benchmark_compat_probe_count": policy.get("benchmark_compat_probe_count"),
        "instance_recall_gain_vs_no_rag": round(safe_float(policy.get("instance_recall")) - safe_float(baseline.get("instance_recall")), 6),
        "template_recall_gain_vs_no_rag": round(safe_float(policy.get("template_recall")) - safe_float(baseline.get("template_recall")), 6),
        "quality_score_gain_vs_no_rag": round(safe_float(policy.get("quality_score")) - safe_float(baseline.get("quality_score")), 6),
    }


def build_html_report(result: dict[str, Any]) -> str:
    decision = result.get("decision", {})
    metrics = result.get("recommended_metrics", {})
    checks_rows = "\n".join(
        f"<tr><td>{c.get('name')}</td><td class='{ 'pass' if c.get('passed') else 'fail' }'>{'PASS' if c.get('passed') else 'FAIL'}</td><td>{c.get('severity')}</td><td><code>{json.dumps(c.get('actual'), ensure_ascii=False)}</code></td><td><code>{json.dumps(c.get('threshold'), ensure_ascii=False)}</code></td><td>{c.get('reason')}</td></tr>"
        for c in result.get("checks", [])
    )
    badge = "pass" if decision.get("decision") == "promote" else ("warn" if decision.get("decision") == "hold_for_review" else "fail")
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase17 RAG Quality Gate</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.card{{border:1px solid #d8dee9;border-radius:10px;padding:16px;background:#f8fafc;margin:14px 0}}table{{border-collapse:collapse;width:100%;margin-top:14px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.pass{{color:#0f766e;font-weight:bold}}.fail{{color:#b91c1c;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}code{{white-space:pre-wrap}}</style></head><body><h1>Phase17 RAG Quality Gate + Policy Promotion</h1><div class=\"card\"><b>Promotion decision：</b><span class=\"{badge}\">{decision.get('decision')}</span><br><b>RAG policy：</b>{decision.get('rag_policy_variant')}<br><b>Top K：</b>{decision.get('rag_top_k')}<br><b>Reason：</b>{decision.get('reason')}<br><b>Generated：</b>{result.get('generated_at_utc')}</div><div class=\"card\"><h2>Recommended RAG Metrics</h2><ul><li>Instance recall: {metrics.get('instance_recall')}</li><li>Template recall: {metrics.get('template_recall')}</li><li>Precision: {metrics.get('precision')}</li><li>False positive rate: {metrics.get('false_positive_rate')}</li><li>Probe count: {metrics.get('probe_count')}</li><li>RAG probes: {metrics.get('rag_probe_count')}</li><li>Avg retrieval score: {metrics.get('avg_retrieval_score')}</li><li>Instance recall gain vs no_rag: {metrics.get('instance_recall_gain_vs_no_rag')}</li></ul></div><h2>Gate Checks</h2><table><tr><th>Check</th><th>Status</th><th>Severity</th><th>Actual</th><th>Threshold</th><th>Reason</th></tr>{checks_rows}</table><h2>Governance Notes</h2><ul><li>RAG 策略必须在 blind mode 下评估。</li><li>benchmark_compat_probe_count 必须为 0。</li><li>hidden_test 不能用于训练。</li><li>RAG 策略升级必须同时关注 recall、precision、false positive、top_k 和执行成本。</li></ul></body></html>"""


def run_rag_quality_gate(
    rag_ab_scorecard_path: Path = DEFAULT_RAG_AB_SCORECARD,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    training_card: Path = DEFAULT_TRAINING_CARD,
    thresholds: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    scorecard = read_json(rag_ab_scorecard_path, {})
    if not scorecard:
        raise FileNotFoundError(f"RAG A/B scorecard not found or invalid: {rag_ab_scorecard_path}")
    policy = recommended_policy(scorecard)
    if not policy:
        raise ValueError("No recommended RAG policy found in RAG A/B scorecard.")
    baseline = baseline_policy(scorecard)
    checks = build_gate_checks(policy, baseline, scorecard, training_card=training_card, thresholds=thresholds)
    decision = decide_promotion(policy, checks)
    metrics = _metrics(policy, baseline)
    result = {
        "phase": "phase17_rag_quality_gate_policy_promotion",
        "generated_at_utc": now_utc(),
        "rag_ab_scorecard_path": str(rag_ab_scorecard_path),
        "training_card_path": str(training_card),
        "recommended_rag_policy": policy,
        "baseline_policy": baseline,
        "recommended_metrics": metrics,
        "thresholds": {**DEFAULT_THRESHOLDS, **(thresholds or {})},
        "checks": checks,
        "decision": decision,
        "anti_cheat": _anti_cheat(scorecard),
        "training_data_summary": _training_card_summary(training_card),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "rag_quality_gate_result.json", result)
    write_json(output_dir / "rag_policy_promotion_decision.json", decision)
    write_json(output_dir / "promoted_rag_policy_candidate.json", policy if decision.get("promotion_allowed") else {"status": "not_promoted", "candidate": policy})
    (output_dir / "rag_quality_gate_report.html").write_text(build_html_report(result), encoding="utf-8")
    return result


def write_sample_inputs_if_missing(scorecard_path: Path, training_card: Path) -> None:
    if not scorecard_path.exists():
        sample = {
            "phase": "phase16_rag_ab_evaluation_sample",
            "discovery_mode": "blind",
            "recommended_rag_policy": {
                "variant": "rag_top_5",
                "quality_score": 0.48,
                "rag_top_k": 5,
                "instance_recall": 0.12,
                "template_recall": 0.63,
                "p0_p1_template_recall": 0.65,
                "precision": 0.96,
                "false_positive_rate": 0.02,
                "probe_count": 130,
                "rag_probe_count": 22,
                "avg_retrieval_score": 0.31,
                "benchmark_compat_probe_count": 0,
            },
            "ranked_results": [
                {"variant": "rag_top_5", "quality_score": 0.48, "rag_top_k": 5, "instance_recall": 0.12, "template_recall": 0.63, "precision": 0.96, "false_positive_rate": 0.02, "probe_count": 130, "rag_probe_count": 22, "avg_retrieval_score": 0.31, "benchmark_compat_probe_count": 0},
                {"variant": "no_rag", "quality_score": 0.40, "rag_top_k": 0, "instance_recall": 0.10, "template_recall": 0.62, "precision": 0.97, "false_positive_rate": 0.01, "probe_count": 100, "rag_probe_count": 0, "avg_retrieval_score": 0, "benchmark_compat_probe_count": 0},
            ],
            "anti_cheat": {"benchmark_compat_allowed": False, "private_ground_truth_visible_to_discovery": False},
        }
        write_json(scorecard_path, sample)
    if not training_card.exists():
        write_json(training_card, {
            "hidden_test_used_for_training": False,
            "private_leak_check": "passed",
            "template_patterns": 35,
            "rag_documents": 35,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase17 RAG quality gate and policy promotion decision")
    parser.add_argument("--rag-ab-scorecard", default=str(DEFAULT_RAG_AB_SCORECARD))
    parser.add_argument("--training-card", default=str(DEFAULT_TRAINING_CARD))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--min-precision", type=float, default=DEFAULT_THRESHOLDS["min_precision"])
    parser.add_argument("--max-fpr", type=float, default=DEFAULT_THRESHOLDS["max_false_positive_rate"])
    parser.add_argument("--max-rag-probes", type=int, default=DEFAULT_THRESHOLDS["max_rag_probe_count"])
    parser.add_argument("--max-rag-top-k", type=int, default=DEFAULT_THRESHOLDS["max_rag_top_k"])
    args = parser.parse_args()
    thresholds = {
        "min_precision": args.min_precision,
        "max_false_positive_rate": args.max_fpr,
        "max_rag_probe_count": args.max_rag_probes,
        "max_rag_top_k": args.max_rag_top_k,
    }
    if __import__("os").environ.get("RAG_QUALITY_GATE_ALLOW_SAMPLE") == "1":
        write_sample_inputs_if_missing(Path(args.rag_ab_scorecard), Path(args.training_card))
    result = run_rag_quality_gate(Path(args.rag_ab_scorecard), Path(args.out), Path(args.training_card), thresholds)
    print(json.dumps({"decision": result["decision"], "out": args.out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
