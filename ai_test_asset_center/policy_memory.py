from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_DIR = Path("benchmark_outputs/policy_history")
DEFAULT_AB_SCORECARD = Path("benchmark_outputs/policy_ab/policy_ab_scorecard.json")
DEFAULT_HISTORY_PATH = DEFAULT_HISTORY_DIR / "policy_history.json"
DEFAULT_REGRESSION_REPORT = DEFAULT_HISTORY_DIR / "strategy_regression_report.html"
DEFAULT_WEAK_TRENDS = DEFAULT_HISTORY_DIR / "weak_template_trends.json"
DEFAULT_ALERTS = DEFAULT_HISTORY_DIR / "policy_regression_alerts.json"


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


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def load_policy_history(history_path: Path = DEFAULT_HISTORY_PATH) -> dict[str, Any]:
    payload = read_json(history_path, {"phase": "phase7_policy_memory", "runs": []})
    if not isinstance(payload, dict):
        payload = {"phase": "phase7_policy_memory", "runs": []}
    payload.setdefault("phase", "phase7_policy_memory")
    payload.setdefault("runs", [])
    return payload


def row_key_metrics(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_score": safe_float(row.get("quality_score")),
        "instance_recall": safe_float(row.get("instance_recall")),
        "template_recall": safe_float(row.get("template_recall")),
        "p0_p1_template_recall": safe_float(row.get("p0_p1_template_recall")),
        "precision": safe_float(row.get("precision")),
        "false_positive_rate": safe_float(row.get("false_positive_rate")),
        "known_bug_instances": safe_int(row.get("known_bug_instances")),
        "discovered_bugs": safe_int(row.get("discovered_bugs")),
        "probe_count": safe_int(row.get("probe_count")),
        "benchmark_compat_probe_count": safe_int(row.get("benchmark_compat_probe_count")),
    }


def collect_missed_templates_from_scorecards(ab_scorecard: dict[str, Any]) -> dict[str, int]:
    """Aggregate missed template counts from individual policy scorecards.

    The A/B scorecard only stores summaries, while each policy scorecard stores
    missed_templates. This function reads those scorecards from their recorded
    paths. It intentionally uses evaluator outputs, not private Bug Factory
    internals, so it is safe for governance reporting.
    """
    aggregate: dict[str, int] = {}
    for row in ab_scorecard.get("ranked_results", []):
        scorecard_path = Path(str(row.get("scorecard_path") or ""))
        scorecard = read_json(scorecard_path, {})
        missed = scorecard.get("missed_templates", {}) if isinstance(scorecard, dict) else {}
        if not isinstance(missed, dict):
            continue
        for template, count in missed.items():
            aggregate[str(template)] = aggregate.get(str(template), 0) + safe_int(count)
    return dict(sorted(aggregate.items(), key=lambda item: item[1], reverse=True))


def build_run_record(
    ab_scorecard: dict[str, Any],
    run_id: str | None = None,
    bug_set: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    ranked = ab_scorecard.get("ranked_results", [])
    recommended = ab_scorecard.get("recommended_policy", {})
    rows = []
    for row in ranked:
        rows.append({"policy_profile": row.get("policy_profile"), **row_key_metrics(row)})
    recommended_profile = recommended.get("policy_profile") or (rows[0].get("policy_profile") if rows else None)
    best_metrics = row_key_metrics(recommended) if recommended else (row_key_metrics(rows[0]) if rows else {})
    return {
        "run_id": run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S"),
        "timestamp_utc": now_utc(),
        "phase": "phase7_policy_memory",
        "project": ab_scorecard.get("project", "enterprise_shop"),
        "bug_set": bug_set or os.environ.get("BUG_SET") or "unknown",
        "discovery_mode": ab_scorecard.get("discovery_mode", "blind"),
        "recommended_policy_profile": recommended_profile,
        "recommended_metrics": best_metrics,
        "policy_results": rows,
        "missed_template_counts": collect_missed_templates_from_scorecards(ab_scorecard),
        "anti_cheat": ab_scorecard.get("anti_cheat", {}),
        "notes": notes or "",
    }


def append_policy_run(
    ab_scorecard_path: Path = DEFAULT_AB_SCORECARD,
    history_path: Path = DEFAULT_HISTORY_PATH,
    run_id: str | None = None,
    bug_set: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    ab_scorecard = read_json(ab_scorecard_path, {})
    if not ab_scorecard:
        raise FileNotFoundError(f"A/B scorecard not found or invalid: {ab_scorecard_path}")
    history = load_policy_history(history_path)
    record = build_run_record(ab_scorecard, run_id=run_id, bug_set=bug_set, notes=notes)
    # Replace same run_id to make repeated local verification deterministic.
    runs = [r for r in history.get("runs", []) if r.get("run_id") != record["run_id"]]
    runs.append(record)
    history["runs"] = runs
    history["last_updated_utc"] = now_utc()
    history["latest_run_id"] = record["run_id"]
    write_json(history_path, history)
    return history


def metric_delta(current: dict[str, Any], previous: dict[str, Any], key: str) -> float:
    return round(safe_float(current.get(key)) - safe_float(previous.get(key)), 6)


def detect_regressions(history: dict[str, Any]) -> list[dict[str, Any]]:
    runs = history.get("runs", [])
    if len(runs) < 2:
        return []
    prev = runs[-2].get("recommended_metrics", {})
    cur = runs[-1].get("recommended_metrics", {})
    alerts = []
    thresholds = {
        "instance_recall": -0.03,
        "template_recall": -0.03,
        "p0_p1_template_recall": -0.03,
        "precision": -0.03,
    }
    for key, threshold in thresholds.items():
        delta = metric_delta(cur, prev, key)
        if delta < threshold:
            alerts.append({"metric": key, "previous": prev.get(key), "current": cur.get(key), "delta": delta, "severity": "warning", "reason": f"{key} dropped more than {abs(threshold)}"})
    fpr_delta = metric_delta(cur, prev, "false_positive_rate")
    if fpr_delta > 0.03:
        alerts.append({"metric": "false_positive_rate", "previous": prev.get("false_positive_rate"), "current": cur.get("false_positive_rate"), "delta": fpr_delta, "severity": "warning", "reason": "false positive rate increased more than 0.03"})
    probe_delta = metric_delta(cur, prev, "probe_count")
    recall_delta = metric_delta(cur, prev, "instance_recall")
    if probe_delta > 80 and recall_delta < 0.02:
        alerts.append({"metric": "probe_efficiency", "previous_probe_count": prev.get("probe_count"), "current_probe_count": cur.get("probe_count"), "probe_delta": probe_delta, "instance_recall_delta": recall_delta, "severity": "info", "reason": "probe count increased but recall barely improved"})
    return alerts


def build_weak_template_trends(history: dict[str, Any], top_n: int = 20) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    last_seen: dict[str, str] = {}
    appearances: dict[str, int] = {}
    for run in history.get("runs", []):
        run_id = str(run.get("run_id"))
        for template, count in (run.get("missed_template_counts") or {}).items():
            c = safe_int(count)
            totals[template] = totals.get(template, 0) + c
            appearances[template] = appearances.get(template, 0) + 1
            if c > 0:
                last_seen[template] = run_id
    rows = []
    for template, count in totals.items():
        rows.append({
            "template_id": template,
            "total_missed_count": count,
            "appeared_in_runs": appearances.get(template, 0),
            "last_seen_run_id": last_seen.get(template),
            "priority": "P1" if count >= 10 or appearances.get(template, 0) >= 2 else "P2",
            "suggested_action": "Add or strengthen invariant/probe coverage for this template.",
        })
    return sorted(rows, key=lambda item: (item["total_missed_count"], item["appeared_in_runs"]), reverse=True)[:top_n]


def build_regression_report(history: dict[str, Any], alerts: list[dict[str, Any]], weak_templates: list[dict[str, Any]]) -> str:
    runs = history.get("runs", [])
    rows = "\n".join(
        f"<tr><td>{r.get('run_id')}</td><td>{r.get('timestamp_utc')}</td><td>{r.get('bug_set')}</td><td>{r.get('recommended_policy_profile')}</td><td>{r.get('recommended_metrics',{}).get('quality_score')}</td><td>{r.get('recommended_metrics',{}).get('instance_recall')}</td><td>{r.get('recommended_metrics',{}).get('template_recall')}</td><td>{r.get('recommended_metrics',{}).get('precision')}</td><td>{r.get('recommended_metrics',{}).get('false_positive_rate')}</td><td>{r.get('recommended_metrics',{}).get('probe_count')}</td></tr>"
        for r in runs[-30:]
    )
    alert_rows = "\n".join(f"<tr><td>{a.get('severity')}</td><td>{a.get('metric')}</td><td>{a.get('previous')}</td><td>{a.get('current')}</td><td>{a.get('delta', a.get('probe_delta'))}</td><td>{a.get('reason')}</td></tr>" for a in alerts) or "<tr><td colspan='6'>No regression alerts</td></tr>"
    weak_rows = "\n".join(f"<tr><td>{w.get('template_id')}</td><td>{w.get('total_missed_count')}</td><td>{w.get('appeared_in_runs')}</td><td>{w.get('last_seen_run_id')}</td><td>{w.get('priority')}</td><td>{w.get('suggested_action')}</td></tr>" for w in weak_templates) or "<tr><td colspan='6'>No weak templates yet</td></tr>"
    latest = runs[-1] if runs else {}
    m = latest.get("recommended_metrics", {})
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Policy Memory & Regression Trend</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033;background:#f7f9fc}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid #d8dee9;padding:14px;border-radius:10px;background:#fff}}table{{border-collapse:collapse;width:100%;margin-top:16px;background:#fff}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}.note{{background:#fff;border:1px solid #d8dee9;padding:14px;border-radius:8px;margin:18px 0}}.ok{{color:#0f766e;font-weight:bold}}.warn{{color:#b45309;font-weight:bold}}</style></head><body><h1>Policy Memory & Regression Trend</h1><div class=\"note\"><b>定位：</b>记录每轮探针策略 A/B 评测表现，发现策略退化、长期漏检模板和探针 ROI 问题。它只读取 benchmark 输出，不读取 Bug Factory 私有答案。</div><div class=\"grid\"><div class=\"card\">历史运行数<br><b>{len(runs)}</b></div><div class=\"card\">最新推荐策略<br><b>{latest.get('recommended_policy_profile','-')}</b></div><div class=\"card\">Instance Recall<br><b>{m.get('instance_recall','-')}</b></div><div class=\"card\">Template Recall<br><b>{m.get('template_recall','-')}</b></div><div class=\"card\">Precision<br><b>{m.get('precision','-')}</b></div><div class=\"card\">FPR<br><b>{m.get('false_positive_rate','-')}</b></div><div class=\"card\">Probe Count<br><b>{m.get('probe_count','-')}</b></div><div class=\"card\">Regression Alerts<br><b>{len(alerts)}</b></div></div><h2>Policy Trend</h2><table><tr><th>Run</th><th>Time</th><th>Bug Set</th><th>Recommended</th><th>Quality</th><th>Instance Recall</th><th>Template Recall</th><th>Precision</th><th>FPR</th><th>Probes</th></tr>{rows}</table><h2>Regression Alerts</h2><table><tr><th>Severity</th><th>Metric</th><th>Previous</th><th>Current</th><th>Delta</th><th>Reason</th></tr>{alert_rows}</table><h2>Long-term Weak Templates</h2><table><tr><th>Template</th><th>Total Missed</th><th>Appeared In Runs</th><th>Last Seen</th><th>Priority</th><th>Suggested Action</th></tr>{weak_rows}</table><h2>Next Step</h2><div class=\"note\">下一阶段可以基于 weak_template_trends.json 自动生成更细粒度的探针任务，并把策略退化告警加入 CI 质量门禁。</div></body></html>"""


def update_policy_memory(
    ab_scorecard_path: Path = DEFAULT_AB_SCORECARD,
    history_dir: Path = DEFAULT_HISTORY_DIR,
    run_id: str | None = None,
    bug_set: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / "policy_history.json"
    history = append_policy_run(ab_scorecard_path, history_path, run_id=run_id, bug_set=bug_set, notes=notes)
    alerts = detect_regressions(history)
    weak_templates = build_weak_template_trends(history)
    write_json(history_dir / "policy_regression_alerts.json", alerts)
    write_json(history_dir / "weak_template_trends.json", weak_templates)
    (history_dir / "strategy_regression_report.html").write_text(build_regression_report(history, alerts, weak_templates), encoding="utf-8")
    summary = {
        "history_path": str(history_path),
        "report_path": str(history_dir / "strategy_regression_report.html"),
        "runs": len(history.get("runs", [])),
        "latest_run_id": history.get("latest_run_id"),
        "alerts": len(alerts),
        "weak_templates": len(weak_templates),
    }
    write_json(history_dir / "policy_memory_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ab-scorecard", default=str(DEFAULT_AB_SCORECARD))
    parser.add_argument("--out", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--bug-set", default=None)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()
    summary = update_policy_memory(
        ab_scorecard_path=Path(args.ab_scorecard),
        history_dir=Path(args.out),
        run_id=args.run_id,
        bug_set=args.bug_set,
        notes=args.notes,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
