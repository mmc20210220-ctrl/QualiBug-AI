from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ai_test_asset_center.defect_discovery import DefectDiscoveryRunner, DiscoveryConfig, normalize_probe_policy_profile
from benchmark_evaluator.evaluator import evaluate

DEFAULT_POLICIES = ["baseline", "feedback", "adaptive"]


@contextmanager
def patched_env(**values: str | None):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def metric_value(scorecard: dict[str, Any], key: str, default: float = 0.0) -> float:
    metrics = scorecard.get("metrics", {})
    try:
        return float(metrics.get(key, default) or default)
    except Exception:
        return default


def probe_count(scorecard: dict[str, Any]) -> int:
    summary = scorecard.get("adaptive_policy_summary", {})
    try:
        return int(summary.get("probe_count") or 0)
    except Exception:
        return 0


def policy_quality_score(scorecard: dict[str, Any]) -> float:
    """One ranking score for policy A/B comparison.

    The score intentionally rewards recall and precision, while penalizing false
    positives and excessive probe count. It is not a product claim; it is a
    governance signal to select the next policy candidate.
    """
    instance_recall = metric_value(scorecard, "instance_recall", metric_value(scorecard, "recall"))
    template_recall = metric_value(scorecard, "template_recall")
    p0p1_recall = max(metric_value(scorecard, "p0_p1_template_recall"), metric_value(scorecard, "p0_p1_recall"))
    precision = metric_value(scorecard, "precision")
    false_positive_rate = metric_value(scorecard, "false_positive_rate")
    probes = probe_count(scorecard)
    probe_penalty = min(probes / 1000, 0.08)
    return round(
        instance_recall * 0.34
        + template_recall * 0.24
        + p0p1_recall * 0.22
        + precision * 0.18
        - false_positive_rate * 0.18
        - probe_penalty,
        6,
    )


def summarize_policy(profile: str, scorecard: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    metrics = scorecard.get("metrics", {})
    summary = scorecard.get("adaptive_policy_summary", {})
    return {
        "policy_profile": profile,
        "quality_score": policy_quality_score(scorecard),
        "instance_recall": metrics.get("instance_recall", metrics.get("recall", 0)),
        "template_recall": metrics.get("template_recall", 0),
        "p0_p1_template_recall": metrics.get("p0_p1_template_recall", metrics.get("p0_p1_recall", 0)),
        "precision": metrics.get("precision", 0),
        "false_positive_rate": metrics.get("false_positive_rate", 0),
        "known_bug_instances": metrics.get("known_bug_instances", metrics.get("known_bugs", 0)),
        "discovered_bugs": metrics.get("discovered_bugs", 0),
        "probe_count": summary.get("probe_count", 0),
        "generic_probe_count": summary.get("generic_probe_count", 0),
        "feedback_learning_probe_count": summary.get("feedback_learning_probe_count", 0),
        "adaptive_policy_probe_count": summary.get("adaptive_policy_probe_count", 0),
        "benchmark_compat_probe_count": summary.get("benchmark_compat_probe_count", 0),
        "scorecard_path": str(out_dir / "benchmark_scorecard.json"),
        "report_path": str(out_dir / "benchmark_report.html"),
    }


def rank_policy_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: (item.get("quality_score", 0), item.get("precision", 0), item.get("template_recall", 0)), reverse=True)


def build_ab_report(payload: dict[str, Any]) -> str:
    rows_html = "\n".join(
        f"<tr><td>{row['policy_profile']}</td><td>{row['quality_score']}</td><td>{row['instance_recall']}</td><td>{row['template_recall']}</td><td>{row['p0_p1_template_recall']}</td><td>{row['precision']}</td><td>{row['false_positive_rate']}</td><td>{row['probe_count']}</td><td><a href='{Path(row['report_path']).name if Path(row['report_path']).parent == Path('.') else row['report_path']}'>report</a></td></tr>"
        for row in payload["ranked_results"]
    )
    best = payload.get("recommended_policy", {})
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Probe Policy A/B Evaluation</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}table{{border-collapse:collapse;width:100%;margin-top:18px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.card{{border:1px solid #d8dee9;padding:14px;border-radius:8px;background:#f8fafc;margin:12px 0}}.ok{{color:#0f766e;font-weight:bold}}</style></head><body><h1>Probe Policy A/B Evaluation</h1><div class="card"><b>推荐策略：</b><span class="ok">{best.get('policy_profile','-')}</span><br><b>质量分：</b>{best.get('quality_score','-')}<br><b>说明：</b>该分数综合 instance recall、template recall、P0/P1 recall、precision、误报率和探针成本，用于选择下一轮策略，不是对外营销指标。</div><table><tr><th>Policy</th><th>Quality</th><th>Instance Recall</th><th>Template Recall</th><th>P0/P1 Template Recall</th><th>Precision</th><th>FPR</th><th>Probes</th><th>Report</th></tr>{rows_html}</table><h2>Governance</h2><ul><li>正式评测固定使用 blind mode。</li><li>benchmark_compat probe count 必须为 0。</li><li>推荐策略会写入 recommended_probe_policy.json，供下一轮缺陷发现使用。</li></ul></body></html>"""


def run_policy_ab_evaluation(
    project: str = "enterprise_shop",
    public_artifacts: Path = Path("enterprise_bug_factory/public_artifacts"),
    ground_truth: Path = Path("enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json"),
    output_dir: Path = Path("benchmark_outputs/policy_ab"),
    policies: list[str] | None = None,
) -> dict[str, Any]:
    policies = [normalize_probe_policy_profile(p, "blind") for p in (policies or DEFAULT_POLICIES)]
    unique_policies: list[str] = []
    for policy in policies:
        if policy not in unique_policies:
            unique_policies.append(policy)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for policy in unique_policies:
        with patched_env(DEFECT_DISCOVERY_MODE="blind", PROBE_POLICY_PROFILE=policy):
            config = DiscoveryConfig(project=project, public_artifacts=public_artifacts, discovery_mode="blind")
            DefectDiscoveryRunner(config).run()
            policy_out = output_dir / policy
            scorecard = evaluate(
                Path(f"platform_outputs/{project}/defect_discovery/discovered_bugs.json"),
                ground_truth,
                policy_out,
            )
            rows.append(summarize_policy(policy, scorecard, policy_out))
    ranked = rank_policy_results(rows)
    recommended = ranked[0] if ranked else {}
    payload = {
        "phase": "phase6_probe_policy_ab_evaluation",
        "project": project,
        "discovery_mode": "blind",
        "policies": unique_policies,
        "ranked_results": ranked,
        "recommended_policy": recommended,
        "anti_cheat": {
            "benchmark_compat_allowed": False,
            "private_ground_truth_visible_to_discovery": False,
            "policy_profiles_are_source_filters_not_answer_keys": True,
        },
    }
    (output_dir / "policy_ab_scorecard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "policy_ab_report.html").write_text(build_ab_report(payload), encoding="utf-8")
    (output_dir / "recommended_probe_policy.json").write_text(json.dumps(recommended, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="enterprise_shop")
    parser.add_argument("--public-artifacts", default="enterprise_bug_factory/public_artifacts")
    parser.add_argument("--ground-truth", default="enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json")
    parser.add_argument("--out", default="benchmark_outputs/policy_ab")
    parser.add_argument("--policies", default=",".join(DEFAULT_POLICIES), help="Comma-separated policy profiles: baseline,feedback,adaptive,conservative,full_blind")
    args = parser.parse_args()
    payload = run_policy_ab_evaluation(
        project=args.project,
        public_artifacts=Path(args.public_artifacts),
        ground_truth=Path(args.ground_truth),
        output_dir=Path(args.out),
        policies=[p.strip() for p in args.policies.split(",") if p.strip()],
    )
    print(json.dumps({"recommended_policy": payload.get("recommended_policy", {}).get("policy_profile"), "out": args.out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
