from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_GROUND_TRUTH = Path("enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json")
DEFAULT_SCORECARD = Path("benchmark_outputs/benchmark_scorecard.json")
DEFAULT_POLICY_AB = Path("benchmark_outputs/policy_ab/policy_ab_scorecard.json")
DEFAULT_OUT = Path("benchmark_outputs/scale_1000")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def counter_dict(values: list[str]) -> dict[str, int]:
    return dict(Counter(values))


def distribution_summary(bugs: list[dict[str, Any]]) -> dict[str, Any]:
    domains = counter_dict([str(b.get("domain", "unknown")) for b in bugs])
    severities = counter_dict([str(b.get("severity", "unknown")) for b in bugs])
    risk_types = counter_dict([str(b.get("risk_type", "unknown")) for b in bugs])
    templates = counter_dict([str(b.get("template_id", "UNKNOWN_TEMPLATE")) for b in bugs])
    instance_ids = [str(b.get("bug_instance_id", b.get("bug_id", ""))) for b in bugs]
    variant_signatures = []
    for bug in bugs:
        dims = bug.get("variant_dimensions", {}) or {}
        variant_signatures.append("|".join([
            str(bug.get("template_id")),
            str(bug.get("risk_type")),
            ",".join(bug.get("related_apis", [])),
            str(dims.get("actor")),
            str(dims.get("operation")),
            str(dims.get("tenant_scope")),
            str(dims.get("data_condition")),
        ]))
    return {
        "bug_count": len(bugs),
        "domain_distribution": domains,
        "severity_distribution": severities,
        "risk_type_distribution": risk_types,
        "template_count": len(templates),
        "template_distribution": templates,
        "template_distribution_min": min(templates.values()) if templates else 0,
        "template_distribution_max": max(templates.values()) if templates else 0,
        "unique_instance_ids": len(set(instance_ids)),
        "duplicate_instance_ids": len(instance_ids) - len(set(instance_ids)),
        "unique_variant_signatures": len(set(variant_signatures)),
        "duplicate_variant_signatures": len(variant_signatures) - len(set(variant_signatures)),
        "p0_p1_count": severities.get("P0", 0) + severities.get("P1", 0),
        "p0_p1_ratio": round((severities.get("P0", 0) + severities.get("P1", 0)) / max(len(bugs), 1), 4),
    }


def extract_metrics(scorecard: dict[str, Any]) -> dict[str, Any]:
    metrics = scorecard.get("metrics", {}) if isinstance(scorecard, dict) else {}
    adaptive = scorecard.get("adaptive_policy_summary", {}) if isinstance(scorecard, dict) else {}
    return {
        "instance_recall": metrics.get("instance_recall", metrics.get("recall", 0)),
        "template_recall": metrics.get("template_recall", 0),
        "p0_p1_template_recall": metrics.get("p0_p1_template_recall", metrics.get("p0_p1_recall", 0)),
        "precision": metrics.get("precision", 0),
        "false_positive_rate": metrics.get("false_positive_rate", 0),
        "known_bug_instances": metrics.get("known_bug_instances", metrics.get("known_bugs", 0)),
        "discovered_bugs": metrics.get("discovered_bugs", 0),
        "matched_true_positives": metrics.get("matched_true_positives", 0),
        "probe_count": adaptive.get("probe_count", 0),
        "adaptive_policy_probe_count": adaptive.get("adaptive_policy_probe_count", 0),
        "feedback_learning_probe_count": adaptive.get("feedback_learning_probe_count", 0),
        "benchmark_compat_probe_count": adaptive.get("benchmark_compat_probe_count", 0),
    }


def compute_probe_roi(metrics: dict[str, Any]) -> dict[str, Any]:
    probes = float(metrics.get("probe_count") or 0)
    matched = float(metrics.get("matched_true_positives") or 0)
    discovered = float(metrics.get("discovered_bugs") or 0)
    return {
        "bugs_found_per_100_probes": round(matched / max(probes, 1) * 100, 4),
        "discovered_items_per_100_probes": round(discovered / max(probes, 1) * 100, 4),
        "probe_count": int(probes),
        "estimated_execution_cost_bucket": "low" if probes < 250 else ("medium" if probes < 750 else "high"),
        "scaling_warning": probes > 1000,
    }


def build_scale_recommendations(distribution: dict[str, Any], metrics: dict[str, Any], roi: dict[str, Any]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if distribution.get("duplicate_instance_ids", 0) > 0:
        recs.append({"priority": "P0", "area": "dedupe", "recommendation": "Bug instance IDs are duplicated; block this benchmark set before training."})
    if distribution.get("template_count", 0) < 30:
        recs.append({"priority": "P1", "area": "template_coverage", "recommendation": "Template coverage is low; increase template diversity before scaling further."})
    if float(metrics.get("precision") or 0) < 0.85 and metrics.get("known_bug_instances", 0):
        recs.append({"priority": "P0", "area": "precision", "recommendation": "Precision is below enterprise threshold; tighten evidence matching and false-positive filters."})
    if float(metrics.get("template_recall") or 0) < 0.60 and metrics.get("known_bug_instances", 0):
        recs.append({"priority": "P1", "area": "template_recall", "recommendation": "Template recall is weak; prioritize missed template improvement plans before increasing count."})
    if roi.get("scaling_warning"):
        recs.append({"priority": "P1", "area": "probe_cost", "recommendation": "Probe count exceeds 1000; introduce probe sampling, dedupe, and ROI-based pruning."})
    if not recs:
        recs.append({"priority": "P2", "area": "scale_ready", "recommendation": "Benchmark distribution and current policy metrics are acceptable for the next scale step."})
    return recs


def build_html_report(payload: dict[str, Any]) -> str:
    dist = payload["distribution_summary"]
    metrics = payload.get("policy_metrics", {})
    roi = payload.get("probe_roi", {})
    rec_rows = "".join(f"<tr><td>{r['priority']}</td><td>{r['area']}</td><td>{r['recommendation']}</td></tr>" for r in payload.get("recommendations", []))
    domain_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(dist.get("domain_distribution", {}).items()))
    severity_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(dist.get("severity_distribution", {}).items()))
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase9 Scale Benchmark Report</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;border-radius:10px;padding:14px;background:#f8fafc}}table{{border-collapse:collapse;width:100%;margin:14px 0}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.ok{{color:#0f766e;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}</style></head><body><h1>Phase9 Scaling to 1000 Bug Benchmark</h1><p>目标：不是简单堆 Bug 数量，而是控制模板覆盖、业务域分布、严重等级分布、重复率和探针 ROI，为未来 10000 / 百万级 Benchmark 打基础。</p><div class=\"grid\"><div class=\"card\"><b>Bug instances</b><br>{dist.get('bug_count')}</div><div class=\"card\"><b>Templates</b><br>{dist.get('template_count')}</div><div class=\"card\"><b>P0/P1 ratio</b><br>{dist.get('p0_p1_ratio')}</div><div class=\"card\"><b>Duplicate IDs</b><br>{dist.get('duplicate_instance_ids')}</div></div><h2>Policy Metrics</h2><div class=\"grid\"><div class=\"card\"><b>Instance recall</b><br>{metrics.get('instance_recall')}</div><div class=\"card\"><b>Template recall</b><br>{metrics.get('template_recall')}</div><div class=\"card\"><b>Precision</b><br>{metrics.get('precision')}</div><div class=\"card\"><b>False positive rate</b><br>{metrics.get('false_positive_rate')}</div></div><h2>Probe ROI</h2><div class=\"card\">bugs_found_per_100_probes: <b>{roi.get('bugs_found_per_100_probes')}</b><br>probe_count: <b>{roi.get('probe_count')}</b><br>execution_cost_bucket: <b>{roi.get('estimated_execution_cost_bucket')}</b></div><h2>Domain Distribution</h2><table><tr><th>Domain</th><th>Count</th></tr>{domain_rows}</table><h2>Severity Distribution</h2><table><tr><th>Severity</th><th>Count</th></tr>{severity_rows}</table><h2>Scale Recommendations</h2><table><tr><th>Priority</th><th>Area</th><th>Recommendation</th></tr>{rec_rows}</table><h2>Governance</h2><ul><li>AI discovery still must not read private ground truth or bug sets.</li><li>1000-bug results should be judged by blind mode, precision, recall, and probe ROI together.</li><li>Before 10000+ scale, add sampling, dedupe, and execution-time budget control.</li></ul></body></html>"""


def run_scale_benchmark_report(
    ground_truth_path: Path = DEFAULT_GROUND_TRUTH,
    benchmark_scorecard_path: Path = DEFAULT_SCORECARD,
    policy_ab_path: Path = DEFAULT_POLICY_AB,
    output_dir: Path = DEFAULT_OUT,
) -> dict[str, Any]:
    truth_payload = read_json(ground_truth_path, {})
    bugs = truth_payload.get("bugs", []) if isinstance(truth_payload, dict) else []
    scorecard = read_json(benchmark_scorecard_path, {})
    if not scorecard and policy_ab_path.exists():
        policy_ab = read_json(policy_ab_path, {})
        recommended = policy_ab.get("recommended_policy", {}) if isinstance(policy_ab, dict) else {}
        scorecard = {"metrics": recommended, "adaptive_policy_summary": {"probe_count": recommended.get("probe_count", 0)}}
    distribution = distribution_summary(bugs)
    metrics = extract_metrics(scorecard)
    roi = compute_probe_roi(metrics)
    payload = {
        "phase": "phase9_scaling_to_1000_bug_benchmark",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ground_truth_path": str(ground_truth_path),
        "benchmark_scorecard_path": str(benchmark_scorecard_path),
        "policy_ab_path": str(policy_ab_path),
        "distribution_summary": distribution,
        "policy_metrics": metrics,
        "probe_roi": roi,
        "recommendations": build_scale_recommendations(distribution, metrics, roi),
        "next_scale_targets": [1000, 10000, 100000, 1000000],
        "anti_cheat": {
            "ai_discovery_can_read_private_truth": False,
            "public_artifacts_expose_seed_or_enabled_bugs": False,
            "formal_metrics_require_blind_mode": True,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scale_1000_scorecard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "scale_1000_report.html").write_text(build_html_report(payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", default=str(DEFAULT_GROUND_TRUTH))
    parser.add_argument("--benchmark-scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--policy-ab", default=str(DEFAULT_POLICY_AB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    payload = run_scale_benchmark_report(Path(args.ground_truth), Path(args.benchmark_scorecard), Path(args.policy_ab), Path(args.out))
    print(json.dumps({"bug_count": payload["distribution_summary"]["bug_count"], "template_count": payload["distribution_summary"]["template_count"], "report": str(Path(args.out) / "scale_1000_report.html")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
