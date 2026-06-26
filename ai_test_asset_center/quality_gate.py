from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AB_SCORECARD = Path("benchmark_outputs/policy_ab/policy_ab_scorecard.json")
DEFAULT_HISTORY_DIR = Path("benchmark_outputs/policy_history")
DEFAULT_OUTPUT_DIR = Path("benchmark_outputs/quality_gate")

DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "min_instance_recall": 0.30,
    "min_template_recall": 0.60,
    "min_p0_p1_template_recall": 0.60,
    "min_precision": 0.90,
    "max_false_positive_rate": 0.05,
    "max_benchmark_compat_probe_count": 0,
    "max_probe_count": 300,
    "max_probe_growth_without_recall_gain": 120,
    "min_recall_gain_for_probe_growth": 0.02,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
        return int(value)
    except Exception:
        return default


def latest_history_run(history_dir: Path) -> dict[str, Any]:
    history = read_json(history_dir / "policy_history.json", {"runs": []})
    runs = history.get("runs", []) if isinstance(history, dict) else []
    if not runs:
        return {}
    return runs[-1] if isinstance(runs[-1], dict) else {}


def load_regression_alerts(history_dir: Path) -> list[dict[str, Any]]:
    alerts = read_json(history_dir / "policy_regression_alerts.json", [])
    return alerts if isinstance(alerts, list) else []


def recommended_policy(ab_scorecard: dict[str, Any]) -> dict[str, Any]:
    rec = ab_scorecard.get("recommended_policy", {}) if isinstance(ab_scorecard, dict) else {}
    if isinstance(rec, dict) and rec:
        return rec
    ranked = ab_scorecard.get("ranked_results", []) if isinstance(ab_scorecard, dict) else []
    if ranked and isinstance(ranked[0], dict):
        return ranked[0]
    return {}


def metric(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    return safe_float(row.get(key), default)


def build_gate_checks(
    policy: dict[str, Any],
    ab_scorecard: dict[str, Any],
    history_dir: Path,
    thresholds: dict[str, float | int] | None = None,
) -> list[dict[str, Any]]:
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    anti = ab_scorecard.get("anti_cheat", {}) if isinstance(ab_scorecard, dict) else {}
    alerts = load_regression_alerts(history_dir)
    latest_run = latest_history_run(history_dir)
    latest_metrics = latest_run.get("recommended_metrics", {}) if latest_run else {}
    # Prefer live A/B recommendation metrics; history metrics are context only.
    instance_recall = metric(policy, "instance_recall")
    template_recall = metric(policy, "template_recall")
    p0p1_template_recall = metric(policy, "p0_p1_template_recall")
    precision = metric(policy, "precision")
    fpr = metric(policy, "false_positive_rate")
    probe_count = safe_int(policy.get("probe_count"))
    compat_count = safe_int(policy.get("benchmark_compat_probe_count"))

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

    add("blind_mode", ab_scorecard.get("discovery_mode") == "blind", "critical", ab_scorecard.get("discovery_mode"), "blind", "Formal promotion must use blind mode.")
    add("no_benchmark_compat", compat_count <= int(t["max_benchmark_compat_probe_count"]), "critical", compat_count, t["max_benchmark_compat_probe_count"], "Promoted policy must not depend on benchmark_compat probes.")
    add("anti_cheat_private_ground_truth", anti.get("private_ground_truth_visible_to_discovery") is False, "critical", anti.get("private_ground_truth_visible_to_discovery"), False, "Discovery platform must not see private ground truth.")
    add("precision", precision >= float(t["min_precision"]), "critical", precision, t["min_precision"], "Precision must stay high before promotion.")
    add("false_positive_rate", fpr <= float(t["max_false_positive_rate"]), "critical", fpr, t["max_false_positive_rate"], "False positive rate must remain controlled.")
    add("instance_recall", instance_recall >= float(t["min_instance_recall"]), "warning", instance_recall, t["min_instance_recall"], "Instance recall should meet the current release bar.")
    add("template_recall", template_recall >= float(t["min_template_recall"]), "warning", template_recall, t["min_template_recall"], "Template recall should cover enough high-value bug patterns.")
    add("p0_p1_template_recall", p0p1_template_recall >= float(t["min_p0_p1_template_recall"]), "critical", p0p1_template_recall, t["min_p0_p1_template_recall"], "P0/P1 template recall is a release-blocking quality signal.")
    add("probe_count_budget", probe_count <= int(t["max_probe_count"]), "warning", probe_count, t["max_probe_count"], "Probe count should not grow without governance.")

    warning_alerts = [a for a in alerts if str(a.get("severity", "")).lower() in {"warning", "critical"}]
    add("policy_regression_alerts", len(warning_alerts) == 0, "warning", len(warning_alerts), 0, "Policy history should not contain unresolved regression warnings.")

    if latest_metrics:
        # Contextual efficiency check: avoid promoting a strategy that grows probes heavily without recall improvement.
        latest_probe_count = safe_int(latest_metrics.get("probe_count"))
        latest_instance_recall = safe_float(latest_metrics.get("instance_recall"))
        probe_growth = probe_count - latest_probe_count
        recall_gain = instance_recall - latest_instance_recall
        excessive_growth = probe_growth > int(t["max_probe_growth_without_recall_gain"]) and recall_gain < float(t["min_recall_gain_for_probe_growth"])
        add("probe_efficiency_vs_history", not excessive_growth, "warning", {"probe_growth": probe_growth, "recall_gain": round(recall_gain, 6)}, {"max_probe_growth": t["max_probe_growth_without_recall_gain"], "min_recall_gain": t["min_recall_gain_for_probe_growth"]}, "New policy should not add many probes without recall gain.")
    else:
        add("policy_history_available", False, "info", "missing", "recommended", "No policy history was found; promotion can proceed but trend checks are limited.")
    return checks


def decide_promotion(policy: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed_critical = [c for c in checks if not c.get("passed") and c.get("severity") == "critical"]
    failed_warning = [c for c in checks if not c.get("passed") and c.get("severity") == "warning"]
    if failed_critical:
        decision = "reject"
        reason = "Critical quality gate checks failed. Do not promote this probe policy."
    elif failed_warning:
        decision = "hold_for_review"
        reason = "No critical failure, but warning checks require review before promotion."
    else:
        decision = "promote"
        reason = "All blocking and warning checks passed. Policy can be promoted."
    return {
        "decision": decision,
        "policy_profile": policy.get("policy_profile"),
        "reason": reason,
        "failed_critical_checks": [c["name"] for c in failed_critical],
        "failed_warning_checks": [c["name"] for c in failed_warning],
        "promotion_allowed": decision == "promote",
    }


def build_quality_gate_report(result: dict[str, Any]) -> str:
    checks_rows = "\n".join(
        f"<tr><td>{c.get('name')}</td><td class='{ 'pass' if c.get('passed') else 'fail' }'>{'PASS' if c.get('passed') else 'FAIL'}</td><td>{c.get('severity')}</td><td><code>{json.dumps(c.get('actual'), ensure_ascii=False)}</code></td><td><code>{json.dumps(c.get('threshold'), ensure_ascii=False)}</code></td><td>{c.get('reason')}</td></tr>"
        for c in result.get("checks", [])
    )
    decision = result.get("decision", {})
    metrics = result.get("recommended_metrics", {})
    badge_class = "pass" if decision.get("decision") == "promote" else ("warn" if decision.get("decision") == "hold_for_review" else "fail")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Phase8 Quality Gate</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.card{{border:1px solid #d8dee9;border-radius:10px;padding:16px;background:#f8fafc;margin:14px 0}}table{{border-collapse:collapse;width:100%;margin-top:14px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.pass{{color:#0f766e;font-weight:bold}}.fail{{color:#b91c1c;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}code{{white-space:pre-wrap}}</style></head><body><h1>Phase8 CI Quality Gate + Strategy Regression Guard</h1><div class="card"><b>Promotion decision：</b><span class="{badge_class}">{decision.get('decision')}</span><br><b>Policy：</b>{decision.get('policy_profile')}<br><b>Reason：</b>{decision.get('reason')}<br><b>Generated：</b>{result.get('generated_at_utc')}</div><div class="card"><h2>Recommended Policy Metrics</h2><ul><li>Instance recall: {metrics.get('instance_recall')}</li><li>Template recall: {metrics.get('template_recall')}</li><li>P0/P1 template recall: {metrics.get('p0_p1_template_recall')}</li><li>Precision: {metrics.get('precision')}</li><li>False positive rate: {metrics.get('false_positive_rate')}</li><li>Probe count: {metrics.get('probe_count')}</li><li>Benchmark compat probes: {metrics.get('benchmark_compat_probe_count')}</li></ul></div><h2>Gate Checks</h2><table><tr><th>Check</th><th>Status</th><th>Severity</th><th>Actual</th><th>Threshold</th><th>Reason</th></tr>{checks_rows}</table><h2>Governance Notes</h2><ul><li>正式策略升级必须使用 blind mode。</li><li>benchmark_compat 探针必须为 0。</li><li>如果 precision、false positive rate 或 P0/P1 recall 退化，应阻断或人工复核。</li><li>该门禁用于策略上线，不是对外宣传指标。</li></ul></body></html>"""


def run_quality_gate(
    ab_scorecard_path: Path = DEFAULT_AB_SCORECARD,
    history_dir: Path = DEFAULT_HISTORY_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    thresholds: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    ab = read_json(ab_scorecard_path, {})
    if not ab:
        raise FileNotFoundError(f"A/B scorecard not found or invalid: {ab_scorecard_path}")
    policy = recommended_policy(ab)
    if not policy:
        raise ValueError("No recommended policy found in A/B scorecard.")
    checks = build_gate_checks(policy, ab, history_dir, thresholds)
    decision = decide_promotion(policy, checks)
    metrics = {
        "quality_score": policy.get("quality_score"),
        "instance_recall": policy.get("instance_recall"),
        "template_recall": policy.get("template_recall"),
        "p0_p1_template_recall": policy.get("p0_p1_template_recall"),
        "precision": policy.get("precision"),
        "false_positive_rate": policy.get("false_positive_rate"),
        "probe_count": policy.get("probe_count"),
        "benchmark_compat_probe_count": policy.get("benchmark_compat_probe_count"),
    }
    result = {
        "phase": "phase8_ci_quality_gate_strategy_regression_guard",
        "generated_at_utc": now_utc(),
        "ab_scorecard_path": str(ab_scorecard_path),
        "history_dir": str(history_dir),
        "recommended_policy": policy,
        "recommended_metrics": metrics,
        "thresholds": {**DEFAULT_THRESHOLDS, **(thresholds or {})},
        "checks": checks,
        "decision": decision,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "quality_gate_result.json", result)
    write_json(output_dir / "policy_promotion_decision.json", decision)
    (output_dir / "quality_gate_report.html").write_text(build_quality_gate_report(result), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ab-scorecard", default=str(DEFAULT_AB_SCORECARD))
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--min-instance-recall", type=float, default=DEFAULT_THRESHOLDS["min_instance_recall"])
    parser.add_argument("--min-template-recall", type=float, default=DEFAULT_THRESHOLDS["min_template_recall"])
    parser.add_argument("--min-p0-p1-template-recall", type=float, default=DEFAULT_THRESHOLDS["min_p0_p1_template_recall"])
    parser.add_argument("--min-precision", type=float, default=DEFAULT_THRESHOLDS["min_precision"])
    parser.add_argument("--max-fpr", type=float, default=DEFAULT_THRESHOLDS["max_false_positive_rate"])
    parser.add_argument("--max-probes", type=int, default=DEFAULT_THRESHOLDS["max_probe_count"])
    args = parser.parse_args()
    thresholds = {
        "min_instance_recall": args.min_instance_recall,
        "min_template_recall": args.min_template_recall,
        "min_p0_p1_template_recall": args.min_p0_p1_template_recall,
        "min_precision": args.min_precision,
        "max_false_positive_rate": args.max_fpr,
        "max_probe_count": args.max_probes,
    }
    result = run_quality_gate(Path(args.ab_scorecard), Path(args.history_dir), Path(args.out), thresholds)
    print(json.dumps({"decision": result["decision"], "out": args.out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
