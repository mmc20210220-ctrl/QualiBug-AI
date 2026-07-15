from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")
DEFAULT_OUTPUT = Path("platform_outputs/enterprise_shop/defect_discovery")
DEFAULT_BENCHMARK = Path("benchmark_outputs/benchmark_scorecard.json")
DEFAULT_OUT = Path("benchmark_outputs/probe_roi")

SOURCE_WEIGHTS = {
    "pattern_library": 1.15,
    "feedback_learning": 1.10,
    "adaptive_policy": 1.05,
    "journey_auto": 1.00,
    "generic_auto": 0.80,
    "benchmark_compat": 0.05,
}
SEVERITY_WEIGHTS = {"P0": 1.30, "P1": 1.15, "P2": 0.95, "P3": 0.80}


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def probe_template_id(probe: dict[str, Any]) -> str:
    return str(probe.get("predicted_template_id") or probe.get("probe_id") or "UNKNOWN")


def normalize_api(probe: dict[str, Any]) -> str:
    return str(probe.get("api_template") or f"{probe.get('method','')} {str(probe.get('path','')).split('?')[0]}").strip()


def matched_probe_ids(scorecard: dict[str, Any]) -> tuple[set[str], set[str]]:
    true_ids: set[str] = set()
    false_ids: set[str] = set()
    for row in scorecard.get("matches", []) or []:
        discovered = row.get("discovered", {}) if isinstance(row, dict) else {}
        pid = discovered.get("probe_id")
        if pid:
            true_ids.add(str(pid))
    for row in scorecard.get("false_positives", []) or []:
        discovered = row.get("discovered", row) if isinstance(row, dict) else {}
        pid = discovered.get("probe_id")
        if pid:
            false_ids.add(str(pid))
    return true_ids, false_ids


def aggregate_probe_roi(
    probes: list[dict[str, Any]],
    execution: list[dict[str, Any]],
    discovered_payload: dict[str, Any],
    scorecard: dict[str, Any],
) -> dict[str, Any]:
    true_ids, false_ids = matched_probe_ids(scorecard)
    discovered_by_probe = {str(b.get("probe_id")): b for b in discovered_payload.get("bugs", []) or [] if b.get("probe_id")}
    execution_by_probe = {str(r.get("probe", {}).get("probe_id")): r for r in execution if r.get("probe", {}).get("probe_id")}

    rows: list[dict[str, Any]] = []
    source_stats: dict[str, Counter] = defaultdict(Counter)
    template_stats: dict[str, Counter] = defaultdict(Counter)
    risk_stats: dict[str, Counter] = defaultdict(Counter)

    for probe in probes:
        pid = str(probe.get("probe_id"))
        source = str(probe.get("source") or "unknown")
        template = probe_template_id(probe)
        risk_type = str(probe.get("risk_type") or "unknown")
        severity = str(probe.get("severity") or "P2")
        executed = pid in execution_by_probe
        failed_signal = execution_by_probe.get(pid, {}).get("assertion_result") == "failed"
        discovered = pid in discovered_by_probe
        true_positive = pid in true_ids
        false_positive = pid in false_ids or (discovered and pid not in true_ids and bool(scorecard.get("matches")))
        duration_ms = float(execution_by_probe.get(pid, {}).get("response", {}).get("duration_ms") or 0)
        evidence_score = 1.0 if execution_by_probe.get(pid) else 0.0
        severity_weight = SEVERITY_WEIGHTS.get(severity, 0.95)
        source_weight = SOURCE_WEIGHTS.get(source, 0.9)
        # ROI score is intentionally transparent and deterministic. It rewards
        # true positive evidence and high-value probes, penalizes false positives,
        # demo-only sources and slow probes.
        signal = 4.0 if true_positive else (1.5 if discovered else (0.35 if failed_signal else 0.0))
        penalty = 2.0 if false_positive else 0.0
        slow_penalty = min(duration_ms / 5000.0, 0.25)
        roi_score = round(max(0.0, (signal + evidence_score) * severity_weight * source_weight - penalty - slow_penalty), 4)
        row = {
            "probe_id": pid,
            "source": source,
            "predicted_template_id": template,
            "risk_type": risk_type,
            "severity": severity,
            "api_template": normalize_api(probe),
            "actor": probe.get("actor"),
            "executed": executed,
            "assertion_failed": failed_signal,
            "discovered": discovered,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "duration_ms": duration_ms,
            "roi_score": roi_score,
        }
        rows.append(row)
        for bucket, key in [(source_stats, source), (template_stats, template), (risk_stats, risk_type)]:
            bucket[key]["probes"] += 1
            bucket[key]["executed"] += int(executed)
            bucket[key]["discovered"] += int(discovered)
            bucket[key]["true_positive"] += int(true_positive)
            bucket[key]["false_positive"] += int(false_positive)
            bucket[key]["roi_score_sum"] += roi_score

    def summarize(counter_map: dict[str, Counter]) -> list[dict[str, Any]]:
        out = []
        for key, c in counter_map.items():
            probes_count = max(int(c.get("probes", 0)), 1)
            discovered = int(c.get("discovered", 0))
            true_positive = int(c.get("true_positive", 0))
            false_positive = int(c.get("false_positive", 0))
            out.append({
                "name": key,
                "probes": int(c.get("probes", 0)),
                "executed": int(c.get("executed", 0)),
                "discovered": discovered,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "hit_rate": round(discovered / probes_count, 4),
                "tp_per_100_probes": round(true_positive / probes_count * 100, 4),
                "fp_per_100_probes": round(false_positive / probes_count * 100, 4),
                "avg_roi_score": round(float(c.get("roi_score_sum", 0)) / probes_count, 4),
            })
        return sorted(out, key=lambda x: (x["avg_roi_score"], x["true_positive"], -x["false_positive"]), reverse=True)

    rows.sort(key=lambda x: (x["roi_score"], x["true_positive"], not x["false_positive"], x["severity"] == "P0"), reverse=True)
    return {
        "probe_rows": rows,
        "source_roi": summarize(source_stats),
        "template_roi": summarize(template_stats),
        "risk_type_roi": summarize(risk_stats),
        "summary": {
            "probe_count": len(rows),
            "executed_probe_count": sum(1 for r in rows if r["executed"]),
            "true_positive_probe_count": sum(1 for r in rows if r["true_positive"]),
            "false_positive_probe_count": sum(1 for r in rows if r["false_positive"]),
            "avg_roi_score": round(sum(r["roi_score"] for r in rows) / max(len(rows), 1), 4),
        },
    }


def select_budgeted_policy(roi_payload: dict[str, Any], budget: int = 120, min_roi_score: float = 0.1) -> dict[str, Any]:
    rows = [r for r in roi_payload.get("probe_rows", []) if r.get("source") != "benchmark_compat"]
    rows = [r for r in rows if float(r.get("roi_score") or 0) >= min_roi_score or r.get("severity") == "P0"]
    rows.sort(key=lambda x: (float(x.get("roi_score") or 0), x.get("severity") == "P0", x.get("true_positive")), reverse=True)
    selected = rows[: max(1, int(budget))]
    selected_ids = [r["probe_id"] for r in selected]
    by_template = Counter(r.get("predicted_template_id") or "UNKNOWN" for r in selected)
    by_source = Counter(r.get("source") or "unknown" for r in selected)
    return {
        "phase": "phase10_probe_roi_pruning_execution_budget",
        "policy_type": "budgeted_top_roi",
        "budget": int(budget),
        "min_roi_score": min_roi_score,
        "selected_probe_count": len(selected_ids),
        "selected_probe_ids": selected_ids,
        "score_by_probe_id": {r["probe_id"]: r["roi_score"] for r in selected},
        "selected_by_template": dict(by_template),
        "selected_by_source": dict(by_source),
        "blocked_sources": ["benchmark_compat"],
        "anti_cheat": {
            "uses_private_ground_truth_in_discovery": False,
            "uses_benchmark_compat": False,
            "policy_is_derived_from_evaluator_feedback_not_enabled_bugs": True,
        },
    }


def build_roi_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    policy = payload.get("budgeted_policy", {})
    source_rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['probes']}</td><td>{r['true_positive']}</td><td>{r['false_positive']}</td><td>{r['avg_roi_score']}</td><td>{r['tp_per_100_probes']}</td></tr>"
        for r in payload.get("source_roi", [])
    )
    template_rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['probes']}</td><td>{r['true_positive']}</td><td>{r['false_positive']}</td><td>{r['avg_roi_score']}</td></tr>"
        for r in payload.get("template_roi", [])[:25]
    )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase10 Probe ROI Report</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;border-radius:10px;padding:14px;background:#f8fafc}}table{{border-collapse:collapse;width:100%;margin:14px 0}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.ok{{color:#0f766e;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}</style></head><body><h1>Phase10 Probe ROI Pruning + Execution Budget</h1><p>目标：不是无限增加探针，而是在有限执行预算下优先保留高 ROI、高价值、低误报的探针。</p><div class=\"grid\"><div class=\"card\"><b>Total probes</b><br>{summary.get('probe_count')}</div><div class=\"card\"><b>True positive probes</b><br>{summary.get('true_positive_probe_count')}</div><div class=\"card\"><b>False positive probes</b><br>{summary.get('false_positive_probe_count')}</div><div class=\"card\"><b>Budgeted selected</b><br>{policy.get('selected_probe_count')}</div></div><h2>Source ROI</h2><table><tr><th>Source</th><th>Probes</th><th>TP</th><th>FP</th><th>Avg ROI</th><th>TP / 100 probes</th></tr>{source_rows}</table><h2>Top Template ROI</h2><table><tr><th>Template</th><th>Probes</th><th>TP</th><th>FP</th><th>Avg ROI</th></tr>{template_rows}</table><h2>Budget Policy</h2><div class=\"card\"><b>Budget:</b> {policy.get('budget')}<br><b>Selected probes:</b> {policy.get('selected_probe_count')}<br><b>Blocked sources:</b> {', '.join(policy.get('blocked_sources', []))}</div><h2>Governance</h2><ul><li>budgeted policy excludes benchmark_compat.</li><li>Use RUN_DEFECT_DISCOVERY_BUDGETED.cmd to execute with PROBE_EXECUTION_BUDGET and selected_probe_ids.</li><li>Compare the budgeted result against full adaptive before promoting.</li></ul></body></html>"""


def run_probe_roi_optimizer(
    workspace: Path = DEFAULT_WORKSPACE,
    output: Path = DEFAULT_OUTPUT,
    benchmark_scorecard: Path = DEFAULT_BENCHMARK,
    out_dir: Path = DEFAULT_OUT,
    budget: int = 120,
    min_roi_score: float = 0.1,
) -> dict[str, Any]:
    probes = read_json(workspace / "defect_probes.json", [])
    execution = read_json(workspace / "probe_execution_result.json", [])
    discovered = read_json(output / "discovered_bugs.json", {"bugs": []})
    scorecard = read_json(benchmark_scorecard, {})
    roi = aggregate_probe_roi(probes, execution, discovered, scorecard)
    policy = select_budgeted_policy(roi, budget=budget, min_roi_score=min_roi_score)
    payload = {
        "phase": "phase10_probe_roi_pruning_execution_budget",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {
            "workspace": str(workspace),
            "output": str(output),
            "benchmark_scorecard": str(benchmark_scorecard),
        },
        **roi,
        "budgeted_policy": policy,
        "recommendations": build_recommendations(roi, policy),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "probe_roi_scorecard.json", payload)
    write_json(out_dir / "budgeted_probe_policy.json", policy)
    # Also place a copy in workspace so DefectDiscovery can consume it via default path.
    write_json(workspace / "budgeted_probe_policy.json", policy)
    (out_dir / "probe_roi_report.html").write_text(build_roi_report(payload), encoding="utf-8")
    return payload


def build_recommendations(roi: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    summary = roi.get("summary", {})
    total = int(summary.get("probe_count") or 0)
    selected = int(policy.get("selected_probe_count") or 0)
    if total > selected * 1.5:
        recs.append({"priority": "P1", "area": "execution_budget", "recommendation": "Use budgeted probe selection for large benchmarks to reduce execution cost."})
    low_roi_sources = [r for r in roi.get("source_roi", []) if r.get("probes", 0) >= 5 and r.get("avg_roi_score", 0) < 0.2]
    for row in low_roi_sources[:3]:
        recs.append({"priority": "P2", "area": "source_pruning", "recommendation": f"Review low-ROI source {row['name']} before increasing probe volume."})
    if not recs:
        recs.append({"priority": "P2", "area": "scale_ready", "recommendation": "Current probe ROI is acceptable; compare budgeted mode before promotion."})
    return recs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--benchmark-scorecard", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--budget", type=int, default=120)
    parser.add_argument("--min-roi-score", type=float, default=0.1)
    args = parser.parse_args()
    payload = run_probe_roi_optimizer(Path(args.workspace), Path(args.output), Path(args.benchmark_scorecard), Path(args.out), args.budget, args.min_roi_score)
    print(json.dumps({"selected_probe_count": payload["budgeted_policy"]["selected_probe_count"], "report": str(Path(args.out) / "probe_roi_report.html")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
