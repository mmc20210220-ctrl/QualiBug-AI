from __future__ import annotations

"""
[DEPRECATED] RAG A/B Evaluator
Status: ZOMBIE MODULE -- 0 active cross-references.
Roadmap: Future evaluation infrastructure for optimizing RAG-based
         knowledge retrieval in the Enterprise Knowledge Center.
         Compares retrieval strategies (baseline vs candidate) on
         recall, precision, and latency metrics. Not currently wired.

See DEPRECATED.md for architecture decisions and activation plan.
"""

import argparse
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ai_test_asset_center.defect_discovery import DefectDiscoveryRunner, DiscoveryConfig
from benchmark_evaluator.evaluator import evaluate

DEFAULT_VARIANTS = ["no_rag", "rag_top_3", "rag_top_5", "rag_top_8", "rag_with_budget"]
DEFAULT_OUT = Path("benchmark_outputs/rag_ab")
DEFAULT_DISCOVERY_OUT = Path("platform_outputs/enterprise_shop/defect_discovery/discovered_bugs.json")
DEFAULT_RAG_PROBES = Path("platform_workspace/enterprise_shop/defect_discovery/rag_enhanced_probes.json")
DEFAULT_DEFECT_PROBES = Path("platform_workspace/enterprise_shop/defect_discovery/defect_probes.json")

DEPRECATED_STATUS = {
    "status": "deprecated_offline_producer",
    "reason": "No current Hot Path imports this module directly; its scorecard artifact is a benchmark-only input.",
    "next_action": "Either reconnect run_rag_ab_evaluation through a benchmark command with tests or fold it into the active quality gate.",
}

VARIANT_CONFIGS: dict[str, dict[str, Any]] = {
    "no_rag": {
        "label": "No RAG baseline",
        "probe_policy_profile": "adaptive",
        "rag_top_k": 0,
        "execution_budget": None,
    },
    "rag_top_3": {
        "label": "RAG top 3",
        "probe_policy_profile": "rag_enhanced",
        "rag_top_k": 3,
        "execution_budget": None,
    },
    "rag_top_5": {
        "label": "RAG top 5",
        "probe_policy_profile": "rag_enhanced",
        "rag_top_k": 5,
        "execution_budget": None,
    },
    "rag_top_8": {
        "label": "RAG top 8",
        "probe_policy_profile": "rag_enhanced",
        "rag_top_k": 8,
        "execution_budget": None,
    },
    "rag_with_budget": {
        "label": "RAG top 8 with execution budget",
        "probe_policy_profile": "rag_enhanced",
        "rag_top_k": 8,
        "execution_budget": 160,
    },
}


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


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _metric(scorecard: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float((scorecard.get("metrics") or {}).get(key, default) or default)
    except Exception:
        return default


def _probe_summary() -> dict[str, Any]:
    rag_payload = _read_json(DEFAULT_RAG_PROBES, {"items": []})
    probes_payload = _read_json(DEFAULT_DEFECT_PROBES, [])
    rag_items = rag_payload.get("items", []) if isinstance(rag_payload, dict) else []
    probes = probes_payload.get("items", probes_payload) if isinstance(probes_payload, dict) else probes_payload
    if not isinstance(probes, list):
        probes = []
    scores = [float(p.get("retrieval_score") or 0) for p in rag_items]
    return {
        "probe_count": len(probes),
        "rag_probe_count": len(rag_items),
        "rag_template_count": len({str(p.get("predicted_template_id") or p.get("rag_matched_template") or "UNKNOWN") for p in rag_items}),
        "avg_retrieval_score": round(sum(scores) / len(scores), 4) if scores else 0,
        "min_retrieval_score": round(min(scores), 4) if scores else 0,
        "max_retrieval_score": round(max(scores), 4) if scores else 0,
        "top_rag_templates": _top_counts([str(p.get("predicted_template_id") or p.get("rag_matched_template") or "UNKNOWN") for p in rag_items], 20),
    }


def _top_counts(items: list[str], limit: int = 10) -> list[list[Any]]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return [[k, v] for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _quality_score(row: dict[str, Any]) -> float:
    # This is an internal governance score for RAG setting selection, not a marketing metric.
    recall = float(row.get("instance_recall") or 0)
    template = float(row.get("template_recall") or 0)
    p0p1 = float(row.get("p0_p1_template_recall") or 0)
    precision = float(row.get("precision") or 0)
    fpr = float(row.get("false_positive_rate") or 0)
    probes = float(row.get("probe_count") or 0)
    rag_probes = float(row.get("rag_probe_count") or 0)
    cost_penalty = min(probes / 3000, 0.08) + min(rag_probes / 1000, 0.04)
    return round(recall * 0.34 + template * 0.22 + p0p1 * 0.20 + precision * 0.18 - fpr * 0.22 - cost_penalty, 6)


def summarize_variant(name: str, config: dict[str, Any], scorecard: dict[str, Any], variant_out: Path) -> dict[str, Any]:
    summary = _probe_summary()
    row = {
        "variant": name,
        "label": config.get("label", name),
        "probe_policy_profile": config.get("probe_policy_profile"),
        "rag_top_k": config.get("rag_top_k", 0),
        "execution_budget": config.get("execution_budget"),
        "instance_recall": _metric(scorecard, "instance_recall", _metric(scorecard, "recall")),
        "template_recall": _metric(scorecard, "template_recall"),
        "p0_p1_template_recall": _metric(scorecard, "p0_p1_template_recall", _metric(scorecard, "p0_p1_recall")),
        "precision": _metric(scorecard, "precision"),
        "false_positive_rate": _metric(scorecard, "false_positive_rate"),
        "known_bug_instances": _metric(scorecard, "known_bug_instances", _metric(scorecard, "known_bugs")),
        "discovered_bugs": _metric(scorecard, "discovered_bugs"),
        "benchmark_compat_probe_count": int((scorecard.get("adaptive_policy_summary") or {}).get("benchmark_compat_probe_count") or 0),
        "scorecard_path": str(variant_out / "benchmark_scorecard.json"),
        "report_path": str(variant_out / "benchmark_report.html"),
        **summary,
    }
    row["quality_score"] = _quality_score(row)
    return row


def rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (r.get("quality_score", 0), r.get("precision", 0), r.get("instance_recall", 0), -float(r.get("probe_count") or 0)), reverse=True)


def build_retrieval_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    best_by_recall = max(rows, key=lambda r: r.get("instance_recall", 0), default={})
    best_by_quality = max(rows, key=lambda r: r.get("quality_score", 0), default={})
    return {
        "phase": "phase16_rag_ab_retrieval_quality",
        "variant_count": len(rows),
        "best_by_instance_recall": best_by_recall.get("variant"),
        "best_by_quality_score": best_by_quality.get("variant"),
        "rag_variants": [
            {
                "variant": r["variant"],
                "rag_top_k": r.get("rag_top_k"),
                "rag_probe_count": r.get("rag_probe_count"),
                "rag_template_count": r.get("rag_template_count"),
                "avg_retrieval_score": r.get("avg_retrieval_score"),
                "instance_recall": r.get("instance_recall"),
                "precision": r.get("precision"),
                "false_positive_rate": r.get("false_positive_rate"),
            }
            for r in rows
        ],
        "governance_notes": [
            "RAG top_k should be selected by blind benchmark quality, not by probe count alone.",
            "Clean-mode false positives and precision must be monitored before promoting any RAG policy.",
            "benchmark_compat probes remain disabled for all formal RAG A/B variants.",
        ],
    }


def build_html_report(payload: dict[str, Any]) -> str:
    rows_html = "\n".join(
        f"<tr><td>{r['variant']}</td><td>{r['quality_score']}</td><td>{r['rag_top_k']}</td><td>{r['instance_recall']}</td><td>{r['template_recall']}</td><td>{r['p0_p1_template_recall']}</td><td>{r['precision']}</td><td>{r['false_positive_rate']}</td><td>{r['probe_count']}</td><td>{r['rag_probe_count']}</td><td>{r['avg_retrieval_score']}</td></tr>"
        for r in payload.get("ranked_results", [])
    )
    best = payload.get("recommended_rag_policy", {})
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>RAG A/B Evaluation</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}table{{border-collapse:collapse;width:100%;margin-top:18px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.card{{border:1px solid #d8dee9;background:#f8fafc;border-radius:8px;padding:14px;margin:12px 0}}.ok{{color:#0f766e;font-weight:bold}}.warn{{color:#b45309}}</style></head><body><h1>Phase16 RAG A/B Evaluation</h1><div class=\"card\"><b>推荐 RAG 策略：</b><span class=\"ok\">{best.get('variant','-')}</span><br><b>质量分：</b>{best.get('quality_score','-')}<br><b>Top K：</b>{best.get('rag_top_k','-')}<br><b>说明：</b>该报告比较 no_rag、不同 top_k RAG 和 budgeted RAG，在 blind mode 下评估发现率、精度、误报和探针成本。</div><table><tr><th>Variant</th><th>Quality</th><th>RAG Top K</th><th>Instance Recall</th><th>Template Recall</th><th>P0/P1 Template Recall</th><th>Precision</th><th>FPR</th><th>Probe Count</th><th>RAG Probes</th><th>Avg Retrieval</th></tr>{rows_html}</table><h2>治理原则</h2><ul><li>正式评测固定使用 blind mode。</li><li>benchmark_compat_probe_count 必须为 0。</li><li>RAG 不应只追求 top_k 更大，而应平衡 recall、precision、false positive 和执行成本。</li><li>推荐策略会写入 recommended_rag_policy.json，供后续质量门禁或默认策略候选使用。</li></ul></body></html>"""


def run_rag_ab_evaluation(
    project: str = "enterprise_shop",
    public_artifacts: Path = Path("enterprise_bug_factory/public_artifacts"),
    ground_truth: Path = Path("enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json"),
    output_dir: Path = DEFAULT_OUT,
    variants: list[str] | None = None,
) -> dict[str, Any]:
    selected = variants or DEFAULT_VARIANTS
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for name in selected:
        if name not in VARIANT_CONFIGS:
            raise ValueError(f"Unknown RAG A/B variant: {name}")
        cfg = dict(VARIANT_CONFIGS[name])
        variant_out = output_dir / name
        variant_out.mkdir(parents=True, exist_ok=True)
        budget = cfg.get("execution_budget")
        with patched_env(
            DEFECT_DISCOVERY_MODE="blind",
            PROBE_POLICY_PROFILE=str(cfg.get("probe_policy_profile")),
            RAG_TOP_K_PER_OPERATION=str(cfg.get("rag_top_k", 0)),
            PROBE_EXECUTION_BUDGET=str(budget) if budget else None,
            # Avoid accidentally loading old budgeted policy unless this variant explicitly budgets by count.
            PROBE_BUDGET_POLICY_PATH="__phase16_no_budget_policy__" if not budget else os.environ.get("PROBE_BUDGET_POLICY_PATH", "__phase16_no_budget_policy__"),
        ):
            DefectDiscoveryRunner(DiscoveryConfig(project=project, public_artifacts=public_artifacts, discovery_mode="blind")).run()
            scorecard = evaluate(DEFAULT_DISCOVERY_OUT, ground_truth, variant_out)
            # Copy probe artifacts for later inspection.
            for artifact in [DEFAULT_RAG_PROBES, DEFAULT_DEFECT_PROBES]:
                if artifact.exists():
                    shutil.copy2(artifact, variant_out / artifact.name)
            rows.append(summarize_variant(name, cfg, scorecard, variant_out))
    ranked = rank_rows(rows)
    payload = {
        "phase": "phase16_rag_ab_evaluation",
        "project": project,
        "discovery_mode": "blind",
        "variants": selected,
        "ranked_results": ranked,
        "recommended_rag_policy": ranked[0] if ranked else {},
        "retrieval_quality": build_retrieval_quality(rows),
        "anti_cheat": {
            "benchmark_compat_allowed": False,
            "private_ground_truth_visible_to_discovery": False,
            "rag_knowledge_base_level": "pattern_level_only",
        },
    }
    _write_json(output_dir / "rag_ab_scorecard.json", payload)
    _write_json(output_dir / "retrieval_quality.json", payload["retrieval_quality"])
    _write_json(output_dir / "recommended_rag_policy.json", payload.get("recommended_rag_policy", {}))
    (output_dir / "rag_ab_report.html").write_text(build_html_report(payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase16 RAG A/B evaluation")
    parser.add_argument("--project", default="enterprise_shop")
    parser.add_argument("--public-artifacts", default="enterprise_bug_factory/public_artifacts")
    parser.add_argument("--ground-truth", default="enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--variants", default=",".join(DEFAULT_VARIANTS))
    args = parser.parse_args()
    payload = run_rag_ab_evaluation(
        project=args.project,
        public_artifacts=Path(args.public_artifacts),
        ground_truth=Path(args.ground_truth),
        output_dir=Path(args.out),
        variants=[v.strip() for v in args.variants.split(",") if v.strip()],
    )
    print(json.dumps({"recommended_rag_policy": payload.get("recommended_rag_policy", {}).get("variant"), "out": args.out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
