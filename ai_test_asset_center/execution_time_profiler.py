from __future__ import annotations

import argparse
import json
import math
import os
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


def as_list(payload: Any, key: str | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if key and isinstance(payload, dict):
        value = payload.get(key, [])
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def percentile(values: list[float], p: float) -> float:
    values = sorted(v for v in values if isinstance(v, (int, float)) and v >= 0)
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 2)
    rank = (len(values) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(values[int(rank)], 2)
    return round(values[low] * (high - rank) + values[high] * (rank - low), 2)


def probe_id_from_execution(row: dict[str, Any]) -> str:
    probe = row.get("probe", {}) if isinstance(row.get("probe"), dict) else {}
    return str(probe.get("probe_id") or row.get("probe_id") or "")


def duration_from_execution(row: dict[str, Any]) -> float:
    # Phase11 records execution_duration_ms at row level. Older phases only had
    # response.duration_ms, so keep a backward-compatible fallback.
    for path in [
        ("execution_duration_ms",),
        ("response", "duration_ms"),
        ("timing", "duration_ms"),
    ]:
        obj: Any = row
        ok = True
        for key in path:
            if isinstance(obj, dict) and key in obj:
                obj = obj[key]
            else:
                ok = False
                break
        if ok:
            try:
                return round(float(obj), 2)
            except Exception:
                pass
    return 0.0


def build_rows(probes: list[dict[str, Any]], executions: list[dict[str, Any]], roi_payload: dict[str, Any]) -> list[dict[str, Any]]:
    exec_by_id = {probe_id_from_execution(e): e for e in executions if probe_id_from_execution(e)}
    roi_rows = {str(r.get("probe_id")): r for r in roi_payload.get("probe_rows", []) if isinstance(r, dict) and r.get("probe_id")}
    rows: list[dict[str, Any]] = []
    for probe in probes:
        pid = str(probe.get("probe_id"))
        exe = exec_by_id.get(pid, {})
        roi = roi_rows.get(pid, {})
        duration_ms = duration_from_execution(exe)
        steps = exe.get("journey_steps") or []
        if isinstance(steps, list) and steps:
            step_durations = [duration_from_execution(s) for s in steps if isinstance(s, dict)]
        else:
            step_durations = []
        rows.append({
            "probe_id": pid,
            "source": str(probe.get("source") or "unknown"),
            "predicted_template_id": str(probe.get("predicted_template_id") or probe.get("probe_id") or "UNKNOWN"),
            "risk_type": str(probe.get("risk_type") or "unknown"),
            "severity": str(probe.get("severity") or "P2"),
            "api_template": str(probe.get("api_template") or f"{probe.get('method','')} {str(probe.get('path','')).split('?')[0]}"),
            "actor": probe.get("actor"),
            "executed": bool(exe),
            "assertion_result": exe.get("assertion_result"),
            "discovered": bool(roi.get("discovered")),
            "true_positive": bool(roi.get("true_positive")),
            "false_positive": bool(roi.get("false_positive")),
            "roi_score": float(roi.get("roi_score") or 0.0),
            "duration_ms": duration_ms,
            "step_count": len(step_durations),
            "max_step_duration_ms": max(step_durations) if step_durations else 0.0,
        })
    return rows


def summarize_bucket(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)
    out = []
    for name, items in grouped.items():
        durations = [float(r.get("duration_ms") or 0) for r in items if r.get("executed")]
        tp = sum(1 for r in items if r.get("true_positive"))
        fp = sum(1 for r in items if r.get("false_positive"))
        out.append({
            "name": name,
            "probe_count": len(items),
            "executed_count": sum(1 for r in items if r.get("executed")),
            "true_positive_count": tp,
            "false_positive_count": fp,
            "total_duration_ms": round(sum(durations), 2),
            "avg_duration_ms": round(sum(durations) / max(len(durations), 1), 2),
            "p95_duration_ms": percentile(durations, 0.95),
            "avg_roi_score": round(sum(float(r.get("roi_score") or 0) for r in items) / max(len(items), 1), 4),
            "tp_per_second": round(tp / max(sum(durations) / 1000.0, 0.001), 4),
        })
    return sorted(out, key=lambda x: (x["total_duration_ms"], x["probe_count"]), reverse=True)


def build_execution_schedule(rows: list[dict[str, Any]], max_workers: int = 4, timeout_ms: int = 8000, budget: int | None = None) -> dict[str, Any]:
    # Greedy list scheduling. This is a plan artifact used by the runner and for
    # governance review. It does not read private benchmark answers.
    eligible = [r for r in rows if r.get("source") != "benchmark_compat"]
    eligible.sort(key=lambda r: (float(r.get("roi_score") or 0), r.get("severity") == "P0", -float(r.get("duration_ms") or 0)), reverse=True)
    if budget and budget > 0:
        eligible = eligible[:budget]
    workers = [{"worker_id": i + 1, "estimated_duration_ms": 0.0, "probe_ids": []} for i in range(max(1, max_workers))]
    for row in eligible:
        estimate = float(row.get("duration_ms") or 100.0)
        if estimate <= 0:
            estimate = 100.0
        target = min(workers, key=lambda w: w["estimated_duration_ms"])
        target["probe_ids"].append(row["probe_id"])
        target["estimated_duration_ms"] = round(target["estimated_duration_ms"] + min(estimate, timeout_ms), 2)
    return {
        "max_workers": max_workers,
        "timeout_ms": timeout_ms,
        "budget": budget or len(eligible),
        "scheduled_probe_count": len(eligible),
        "estimated_wall_time_ms": round(max((w["estimated_duration_ms"] for w in workers), default=0.0), 2),
        "estimated_sequential_time_ms": round(sum(float(r.get("duration_ms") or 100.0) for r in eligible), 2),
        "estimated_speedup": round((sum(float(r.get("duration_ms") or 100.0) for r in eligible) / max(max((w["estimated_duration_ms"] for w in workers), default=1.0), 1.0)), 3),
        "workers": workers,
    }


def build_profiler_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    schedule = payload.get("execution_schedule", {})
    slow_rows = "".join(
        f"<tr><td>{r['probe_id']}</td><td>{r['source']}</td><td>{r['predicted_template_id']}</td><td>{r['duration_ms']}</td><td>{r['roi_score']}</td><td>{r['true_positive']}</td></tr>"
        for r in payload.get("slow_probes", [])[:30]
    )
    source_rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['probe_count']}</td><td>{r['total_duration_ms']}</td><td>{r['avg_duration_ms']}</td><td>{r['p95_duration_ms']}</td><td>{r['tp_per_second']}</td></tr>"
        for r in payload.get("source_timing", [])
    )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Phase11 Execution Time Profiler</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;border-radius:10px;padding:14px;background:#f8fafc}}table{{border-collapse:collapse;width:100%;margin:14px 0}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.ok{{color:#0f766e;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}</style></head><body><h1>Phase11 Execution Time Profiler + Parallel Probe Runner</h1><p>目标：统计探针耗时，识别慢探针，并生成并行执行计划，在有限时间预算下优先执行高 ROI 探针。</p><div class=\"grid\"><div class=\"card\"><b>Total probes</b><br>{summary.get('probe_count')}</div><div class=\"card\"><b>Executed probes</b><br>{summary.get('executed_probe_count')}</div><div class=\"card\"><b>Total duration</b><br>{summary.get('total_duration_ms')} ms</div><div class=\"card\"><b>P95 duration</b><br>{summary.get('p95_duration_ms')} ms</div></div><h2>Parallel Schedule</h2><div class=\"card\"><b>Workers:</b> {schedule.get('max_workers')}<br><b>Scheduled probes:</b> {schedule.get('scheduled_probe_count')}<br><b>Estimated wall time:</b> {schedule.get('estimated_wall_time_ms')} ms<br><b>Estimated speedup:</b> {schedule.get('estimated_speedup')}x</div><h2>Source Timing</h2><table><tr><th>Source</th><th>Probes</th><th>Total ms</th><th>Avg ms</th><th>P95 ms</th><th>TP / sec</th></tr>{source_rows}</table><h2>Slow Probes</h2><table><tr><th>Probe</th><th>Source</th><th>Template</th><th>Duration ms</th><th>ROI</th><th>TP</th></tr>{slow_rows}</table><h2>Governance</h2><ul><li>Parallel execution is opt-in via PROBE_PARALLEL_WORKERS.</li><li>When SUT state is shared, keep workers low or use isolated SUT instances per worker.</li><li>Use this report to tune PROBE_EXECUTION_BUDGET, PROBE_TIMEOUT_MS and ROI pruning.</li></ul></body></html>"""


def run_execution_profiler(
    workspace: Path = DEFAULT_WORKSPACE,
    output: Path = DEFAULT_OUTPUT,
    roi_scorecard: Path = DEFAULT_ROI,
    out_dir: Path = DEFAULT_OUT,
    max_workers: int = 4,
    timeout_ms: int = 8000,
    budget: int | None = None,
) -> dict[str, Any]:
    probes = as_list(read_json(workspace / "defect_probes.json", []))
    executions = as_list(read_json(workspace / "probe_execution_result.json", []))
    roi_payload = read_json(roi_scorecard, {})
    rows = build_rows(probes, executions, roi_payload)
    durations = [float(r.get("duration_ms") or 0) for r in rows if r.get("executed")]
    slow_threshold = max(percentile(durations, 0.95), float(timeout_ms) * 0.75 if timeout_ms else 0)
    slow_probes = sorted([r for r in rows if float(r.get("duration_ms") or 0) >= slow_threshold], key=lambda r: r["duration_ms"], reverse=True)
    schedule = build_execution_schedule(rows, max_workers=max_workers, timeout_ms=timeout_ms, budget=budget)
    payload = {
        "phase": "phase11_execution_time_profiler_parallel_probe_runner",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {"workspace": str(workspace), "output": str(output), "roi_scorecard": str(roi_scorecard)},
        "summary": {
            "probe_count": len(rows),
            "executed_probe_count": sum(1 for r in rows if r.get("executed")),
            "total_duration_ms": round(sum(durations), 2),
            "avg_duration_ms": round(sum(durations) / max(len(durations), 1), 2),
            "p50_duration_ms": percentile(durations, 0.50),
            "p90_duration_ms": percentile(durations, 0.90),
            "p95_duration_ms": percentile(durations, 0.95),
            "p99_duration_ms": percentile(durations, 0.99),
            "slow_probe_count": len(slow_probes),
        },
        "probe_timing_rows": rows,
        "source_timing": summarize_bucket(rows, "source"),
        "template_timing": summarize_bucket(rows, "predicted_template_id"),
        "risk_type_timing": summarize_bucket(rows, "risk_type"),
        "slow_probes": slow_probes,
        "execution_schedule": schedule,
        "recommendations": build_recommendations(rows, schedule, slow_probes),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "execution_time_scorecard.json", payload)
    write_json(out_dir / "parallel_execution_plan.json", schedule)
    (out_dir / "slow_probe_report.html").write_text(build_profiler_report(payload), encoding="utf-8")
    # copy lightweight plan to workspace so the runner/CLI can consume it later
    write_json(workspace / "parallel_execution_plan.json", schedule)
    return payload


def build_recommendations(rows: list[dict[str, Any]], schedule: dict[str, Any], slow_probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if schedule.get("estimated_speedup", 1) and float(schedule.get("estimated_speedup") or 1) >= 1.5:
        recs.append({"priority": "P1", "area": "parallel_runner", "recommendation": "Enable PROBE_PARALLEL_WORKERS for large benchmark runs after validating SUT isolation."})
    if slow_probes:
        recs.append({"priority": "P1", "area": "slow_probe_pruning", "recommendation": "Review slow probes; split multi-step probes or lower their priority when ROI is low."})
    low_roi_slow = [r for r in slow_probes if float(r.get("roi_score") or 0) < 0.2]
    if low_roi_slow:
        recs.append({"priority": "P2", "area": "budget_policy", "recommendation": f"Consider pruning {len(low_roi_slow)} slow low-ROI probes from budgeted mode."})
    if not recs:
        recs.append({"priority": "P2", "area": "scale_ready", "recommendation": "Current probe timing is healthy; continue measuring at 1000+ benchmark scale."})
    return recs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--roi-scorecard", default=str(DEFAULT_ROI))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--max-workers", type=int, default=int(os.environ.get("PROBE_PARALLEL_WORKERS", "4") or 4))
    parser.add_argument("--timeout-ms", type=int, default=int(os.environ.get("PROBE_TIMEOUT_MS", "8000") or 8000))
    parser.add_argument("--budget", type=int, default=int(os.environ.get("PROBE_EXECUTION_BUDGET", "0") or 0))
    args = parser.parse_args()
    payload = run_execution_profiler(Path(args.workspace), Path(args.output), Path(args.roi_scorecard), Path(args.out), args.max_workers, args.timeout_ms, args.budget or None)
    print(json.dumps({"report": str(Path(args.out) / "slow_probe_report.html"), "scheduled_probe_count": payload["execution_schedule"]["scheduled_probe_count"], "estimated_speedup": payload["execution_schedule"]["estimated_speedup"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
