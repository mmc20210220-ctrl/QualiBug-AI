from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")
DEFAULT_OUTPUT = Path("platform_outputs/enterprise_shop/defect_discovery")
DEFAULT_ROI = Path("benchmark_outputs/probe_roi/probe_roi_scorecard.json")
DEFAULT_OUT = Path("benchmark_outputs/execution_profiler")


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


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 2)
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(ordered[int(k)], 2)
    return round(ordered[f] * (c - k) + ordered[c] * (k - f), 2)


def probe_id_of(row: dict[str, Any]) -> str:
    return str(row.get("probe", {}).get("probe_id") or row.get("probe_id") or "")


def source_of(row: dict[str, Any]) -> str:
    return str(row.get("probe", {}).get("source") or row.get("source") or "unknown")


def template_of(row: dict[str, Any]) -> str:
    probe = row.get("probe", row)
    return str(probe.get("predicted_template_id") or probe.get("probe_id") or "UNKNOWN")


def risk_type_of(row: dict[str, Any]) -> str:
    probe = row.get("probe", row)
    return str(probe.get("risk_type") or "unknown")


def duration_of(row: dict[str, Any]) -> float:
    if "duration_ms" in row:
        try:
            return float(row.get("duration_ms") or 0)
        except Exception:
            return 0.0
    response = row.get("response") or {}
    if isinstance(response, dict):
        try:
            return float(response.get("duration_ms") or 0)
        except Exception:
            return 0.0
    return 0.0


def discovered_probe_ids(discovered_payload: dict[str, Any]) -> set[str]:
    return {str(item.get("probe_id")) for item in discovered_payload.get("bugs", []) or [] if item.get("probe_id")}


def load_roi_by_probe(path: Path) -> dict[str, float]:
    payload = read_json(path, {})
    rows = payload.get("probe_rows", []) if isinstance(payload, dict) else []
    out: dict[str, float] = {}
    for row in rows:
        pid = str(row.get("probe_id") or "")
        if pid:
            try:
                out[pid] = float(row.get("roi_score") or 0)
            except Exception:
                out[pid] = 0.0
    return out


def build_execution_profile(
    execution: list[dict[str, Any]],
    discovered_payload: dict[str, Any],
    roi_by_probe: dict[str, float] | None = None,
) -> dict[str, Any]:
    roi_by_probe = roi_by_probe or {}
    discovered_ids = discovered_probe_ids(discovered_payload)
    rows: list[dict[str, Any]] = []
    by_source: dict[str, list[float]] = defaultdict(list)
    by_template: dict[str, list[float]] = defaultdict(list)
    by_risk: dict[str, list[float]] = defaultdict(list)

    for item in execution:
        probe = item.get("probe", {})
        pid = probe_id_of(item)
        duration_ms = duration_of(item)
        row = {
            "probe_id": pid,
            "source": source_of(item),
            "predicted_template_id": template_of(item),
            "risk_type": risk_type_of(item),
            "severity": probe.get("severity"),
            "duration_ms": round(duration_ms, 2),
            "assertion_result": item.get("assertion_result"),
            "discovered": pid in discovered_ids,
            "roi_score": round(float(roi_by_probe.get(pid, 0.0)), 4),
        }
        rows.append(row)
        by_source[row["source"]].append(duration_ms)
        by_template[row["predicted_template_id"]].append(duration_ms)
        by_risk[row["risk_type"]].append(duration_ms)

    durations = [r["duration_ms"] for r in rows]

    def summarize(bucket: dict[str, list[float]]) -> list[dict[str, Any]]:
        data = []
        for name, vals in bucket.items():
            data.append({
                "name": name,
                "probe_count": len(vals),
                "total_ms": round(sum(vals), 2),
                "avg_ms": round(sum(vals) / max(len(vals), 1), 2),
                "p50_ms": percentile(vals, 0.50),
                "p90_ms": percentile(vals, 0.90),
                "p95_ms": percentile(vals, 0.95),
                "max_ms": round(max(vals) if vals else 0, 2),
            })
        return sorted(data, key=lambda x: (x["total_ms"], x["avg_ms"]), reverse=True)

    threshold = max(100.0, percentile(durations, 0.90)) if durations else 0.0
    slow_rows = sorted([r for r in rows if r["duration_ms"] >= threshold], key=lambda x: x["duration_ms"], reverse=True)
    total_ms = sum(durations)
    return {
        "phase": "phase11_execution_time_profiler_parallel_probe_runner",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "executed_probe_count": len(rows),
            "discovered_probe_count": sum(1 for r in rows if r["discovered"]),
            "total_duration_ms_observed": round(total_ms, 2),
            "avg_duration_ms": round(total_ms / max(len(rows), 1), 2),
            "p50_ms": percentile(durations, 0.50),
            "p90_ms": percentile(durations, 0.90),
            "p95_ms": percentile(durations, 0.95),
            "max_ms": round(max(durations) if durations else 0, 2),
            "slow_probe_threshold_ms": threshold,
            "slow_probe_count": len(slow_rows),
        },
        "by_source": summarize(by_source),
        "by_template": summarize(by_template),
        "by_risk_type": summarize(by_risk),
        "slow_probes": slow_rows[:50],
        "probe_execution_rows": sorted(rows, key=lambda x: x["duration_ms"], reverse=True),
    }


def build_parallel_plan(profile: dict[str, Any], max_workers: int = 4, timeout_ms: int = 8000) -> dict[str, Any]:
    rows = profile.get("probe_execution_rows", [])
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        # Keep probes from the same high-risk template in the same affinity bucket.
        # This avoids over-parallelizing scenarios that mutate the same business object.
        buckets[str(row.get("predicted_template_id") or "UNKNOWN")].append(row)
    bucket_items = []
    for template, items in buckets.items():
        bucket_items.append({
            "affinity_key": template,
            "probe_count": len(items),
            "estimated_ms": round(sum(float(i.get("duration_ms") or 0) for i in items), 2),
            "probe_ids": [i.get("probe_id") for i in sorted(items, key=lambda x: float(x.get("duration_ms") or 0), reverse=True)],
        })
    bucket_items.sort(key=lambda x: x["estimated_ms"], reverse=True)
    estimated_total = sum(b["estimated_ms"] for b in bucket_items)
    estimated_parallel = estimated_total / max(1, min(max_workers, len(bucket_items) or 1))
    return {
        "phase": "phase11_parallel_probe_runner_plan",
        "max_workers": max_workers,
        "probe_timeout_ms": timeout_ms,
        "parallel_mode": "affinity_bucketed",
        "reason": "Avoid running probes that mutate the same business template in parallel; distribute independent templates across workers.",
        "estimated_sequential_ms": round(estimated_total, 2),
        "estimated_parallel_ms": round(estimated_parallel, 2),
        "estimated_speedup": round(estimated_total / max(estimated_parallel, 1), 2) if estimated_total else 0,
        "buckets": bucket_items,
        "anti_cheat": {
            "uses_private_ground_truth": False,
            "uses_enabled_bugs": False,
            "uses_benchmark_compat": False,
        },
    }


def build_scheduler_policy(
    profile: dict[str, Any],
    roi_by_probe: dict[str, float],
    max_probe_count: int = 120,
    max_total_ms: int = 180000,
    min_roi_score: float = 0.1,
) -> dict[str, Any]:
    rows = []
    for row in profile.get("probe_execution_rows", []):
        pid = str(row.get("probe_id") or "")
        roi_score = float(roi_by_probe.get(pid, row.get("roi_score") or 0))
        duration_ms = float(row.get("duration_ms") or 0)
        value_per_second = roi_score / max(duration_ms / 1000.0, 0.05)
        if roi_score < min_roi_score and str(row.get("severity")) != "P0":
            continue
        rows.append({**row, "roi_score": round(roi_score, 4), "value_per_second": round(value_per_second, 4)})
    rows.sort(key=lambda x: (float(x.get("value_per_second") or 0), float(x.get("roi_score") or 0), str(x.get("severity")) == "P0"), reverse=True)
    selected = []
    used_ms = 0.0
    for row in rows:
        if len(selected) >= max_probe_count:
            break
        dur = max(float(row.get("duration_ms") or 0), 1.0)
        if used_ms + dur > max_total_ms and selected:
            continue
        selected.append(row)
        used_ms += dur
    return {
        "phase": "phase11_roi_time_budget_scheduler",
        "policy_type": "roi_per_second_top_k",
        "max_probe_count": max_probe_count,
        "max_total_ms": max_total_ms,
        "min_roi_score": min_roi_score,
        "selected_probe_count": len(selected),
        "estimated_total_ms": round(used_ms, 2),
        "selected_probe_ids": [r["probe_id"] for r in selected],
        "selected_by_source": dict(Counter(str(r.get("source")) for r in selected)),
        "selected_by_template": dict(Counter(str(r.get("predicted_template_id")) for r in selected)),
        "score_by_probe_id": {r["probe_id"]: r["value_per_second"] for r in selected},
        "blocked_sources": ["benchmark_compat"],
    }


def build_html_report(profile: dict[str, Any], plan: dict[str, Any], scheduler: dict[str, Any]) -> str:
    s = profile.get("summary", {})
    source_rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['probe_count']}</td><td>{r['total_ms']}</td><td>{r['avg_ms']}</td><td>{r['p90_ms']}</td><td>{r['max_ms']}</td></tr>"
        for r in profile.get("by_source", [])
    )
    slow_rows = "".join(
        f"<tr><td>{r['probe_id']}</td><td>{r['source']}</td><td>{r['predicted_template_id']}</td><td>{r['duration_ms']}</td><td>{r['roi_score']}</td></tr>"
        for r in profile.get("slow_probes", [])[:30]
    )
    bucket_rows = "".join(
        f"<tr><td>{b['affinity_key']}</td><td>{b['probe_count']}</td><td>{b['estimated_ms']}</td></tr>"
        for b in plan.get("buckets", [])[:30]
    )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase11 执行耗时与并行调度报告</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;padding:14px;border-radius:8px;background:#fff}}table{{border-collapse:collapse;width:100%;margin-top:14px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.note{{background:#f8fafc;border:1px solid #d8dee9;padding:14px;border-radius:8px;margin:18px 0}}</style></head><body><h1>Phase11 执行耗时与并行调度报告</h1><div class=\"note\">目标：在不读取 ground truth、不使用 benchmark_compat 的前提下，识别慢探针、生成并行执行计划，并结合 ROI 生成时间预算策略。</div><div class=\"grid\"><div class=\"card\">执行探针<br><b>{s.get('executed_probe_count',0)}</b></div><div class=\"card\">总耗时 ms<br><b>{s.get('total_duration_ms_observed',0)}</b></div><div class=\"card\">P90 ms<br><b>{s.get('p90_ms',0)}</b></div><div class=\"card\">慢探针<br><b>{s.get('slow_probe_count',0)}</b></div><div class=\"card\">并行 workers<br><b>{plan.get('max_workers')}</b></div><div class=\"card\">估算加速<br><b>{plan.get('estimated_speedup')}</b></div><div class=\"card\">预算探针<br><b>{scheduler.get('selected_probe_count')}</b></div><div class=\"card\">预算耗时 ms<br><b>{scheduler.get('estimated_total_ms')}</b></div></div><h2>按来源耗时</h2><table><tr><th>Source</th><th>Count</th><th>Total ms</th><th>Avg ms</th><th>P90 ms</th><th>Max ms</th></tr>{source_rows}</table><h2>慢探针 Top</h2><table><tr><th>Probe</th><th>Source</th><th>Template</th><th>Duration ms</th><th>ROI</th></tr>{slow_rows}</table><h2>并行亲和分组</h2><table><tr><th>Affinity Key</th><th>Probe Count</th><th>Estimated ms</th></tr>{bucket_rows}</table></body></html>"""


def run_execution_profiler(
    workspace: Path = DEFAULT_WORKSPACE,
    output: Path = DEFAULT_OUTPUT,
    roi_scorecard: Path = DEFAULT_ROI,
    out_dir: Path = DEFAULT_OUT,
    max_workers: int = 4,
    timeout_ms: int = 8000,
    max_probe_count: int = 120,
    max_total_ms: int = 180000,
) -> dict[str, Any]:
    execution = read_json(workspace / "probe_execution_result.json", [])
    discovered = read_json(output / "discovered_bugs.json", {"bugs": []})
    roi_by_probe = load_roi_by_probe(roi_scorecard)
    profile = build_execution_profile(execution, discovered, roi_by_probe)
    plan = build_parallel_plan(profile, max_workers=max_workers, timeout_ms=timeout_ms)
    scheduler = build_scheduler_policy(profile, roi_by_probe, max_probe_count=max_probe_count, max_total_ms=max_total_ms)
    payload = {
        "phase": "phase11_execution_time_profiler_parallel_probe_runner",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {
            "workspace": str(workspace),
            "output": str(output),
            "roi_scorecard": str(roi_scorecard),
        },
        "execution_time_profile": profile,
        "parallel_execution_plan": plan,
        "scheduler_policy": scheduler,
        "recommendations": build_recommendations(profile, plan, scheduler),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "execution_time_profile.json", profile)
    write_json(out_dir / "parallel_execution_plan.json", plan)
    write_json(out_dir / "execution_budget_scheduler_policy.json", scheduler)
    write_json(out_dir / "phase11_execution_profiler_scorecard.json", payload)
    write_json(workspace / "execution_budget_scheduler_policy.json", scheduler)
    (out_dir / "slow_probe_report.html").write_text(build_html_report(profile, plan, scheduler), encoding="utf-8")
    return payload


def build_recommendations(profile: dict[str, Any], plan: dict[str, Any], scheduler: dict[str, Any]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    summary = profile.get("summary", {})
    if float(summary.get("p90_ms") or 0) > 1000:
        recs.append({"priority": "P1", "area": "slow_probe", "recommendation": "P90 probe duration is high; enable affinity-bucketed parallel execution and review slow probes."})
    if int(scheduler.get("selected_probe_count") or 0) < int(summary.get("executed_probe_count") or 0):
        recs.append({"priority": "P1", "area": "budget_scheduler", "recommendation": "Use ROI-per-second scheduler for large benchmark runs to reduce execution time."})
    if float(plan.get("estimated_speedup") or 0) >= 2:
        recs.append({"priority": "P2", "area": "parallel_runner", "recommendation": "Parallel execution plan indicates meaningful speedup; validate with clean mode before promoting."})
    if not recs:
        recs.append({"priority": "P2", "area": "scale_ready", "recommendation": "Execution time profile is acceptable for current scale; keep collecting history."})
    return recs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--roi-scorecard", default=str(DEFAULT_ROI))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--max-workers", type=int, default=int(os.environ.get("PROBE_PARALLEL_WORKERS", "4")))
    parser.add_argument("--timeout-ms", type=int, default=int(os.environ.get("PROBE_TIMEOUT_MS", "8000")))
    parser.add_argument("--max-probe-count", type=int, default=int(os.environ.get("PROBE_EXECUTION_BUDGET", "120")))
    parser.add_argument("--max-total-ms", type=int, default=int(os.environ.get("PROBE_MAX_TOTAL_MS", "180000")))
    args = parser.parse_args()
    payload = run_execution_profiler(
        workspace=Path(args.workspace),
        output=Path(args.output),
        roi_scorecard=Path(args.roi_scorecard),
        out_dir=Path(args.out),
        max_workers=args.max_workers,
        timeout_ms=args.timeout_ms,
        max_probe_count=args.max_probe_count,
        max_total_ms=args.max_total_ms,
    )
    print(json.dumps({
        "executed_probe_count": payload["execution_time_profile"]["summary"]["executed_probe_count"],
        "slow_probe_count": payload["execution_time_profile"]["summary"]["slow_probe_count"],
        "estimated_speedup": payload["parallel_execution_plan"]["estimated_speedup"],
        "budgeted_probe_count": payload["scheduler_policy"]["selected_probe_count"],
        "report": str(Path(args.out) / "slow_probe_report.html"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
