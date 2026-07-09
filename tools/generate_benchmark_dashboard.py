"""P6 Benchmark Report Generator — creates a standalone HTML dashboard from benchmark metrics.

Features:
  - Corrected recall (unique bug-type dedup, avoids recall > 1.0)
  - False negative rate display
  - Auto-refresh via polling backend API (every 60s)
  - Historical trend tracking via benchmark_history.json
  - P3 bug-type coverage matrix (20/20 target)
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

BENCHMARK_JSON = Path(__file__).resolve().parent / "platform_outputs" / "benchmark_metrics.json"
BENCHMARK_HISTORY = Path(__file__).resolve().parent / "platform_outputs" / "benchmark_history.json"
BACKEND_API = os.environ.get("QUALIBUG_API_URL", "http://localhost:8088")


def _save_history(data: dict) -> None:
    """Append current metrics to history for trend tracking."""
    BENCHMARK_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    if BENCHMARK_HISTORY.exists():
        try:
            history = json.loads(BENCHMARK_HISTORY.read_text("utf-8") or "[]")
        except Exception:
            history = []
    if not isinstance(history, list):
        history = []
    entry = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "recall": data.get("corrected_recall", data.get("recall", 0)),
        "precision": data.get("precision", 0),
        "p3_coverage": data.get("p3_coverage", "0/20"),
        "evidence_completeness": data.get("evidence_completeness", 0),
        "false_negative_rate": data.get("false_negative_rate", 0),
    }
    history.append(entry)
    history = history[-90:]  # Keep last 90 runs
    BENCHMARK_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def _no_data_banner(has_real_data: bool) -> str:
    """Return a warning banner if no real data is available."""
    if has_real_data:
        return ""
    return (
        '<div class="no-data-banner">'
        '⚠ No benchmark data available. Run a benchmark scan to populate real metrics. '
        'All values below are placeholders, NOT fabricated data.'
        '</div>'
    )


def _build_coverage_rows(
    bug_type_data: dict,
    risk_family_data: dict,
) -> str:
    """Build the coverage table rows from real data.

    If no data is available, shows a single row indicating no data.
    """
    if not bug_type_data and not risk_family_data:
        return (
            '<table>'
            '<tr><th>#</th><th>Bug Type / Risk Family</th><th>Status</th><th>Details</th></tr>'
            '<tr><td colspan="4" class="no-data">'
            'No coverage data available — run a benchmark scan to populate.'
            '</td></tr>'
            '</table>'
        )

    rows: list[str] = []
    rows.append('<table>')
    rows.append('<tr><th>#</th><th>Bug Type / Risk Family</th><th>Status</th><th>Details</th></tr>')

    # Use risk_family_data if available, otherwise bug_type_data
    source = risk_family_data if risk_family_data else bug_type_data
    idx = 0
    for name, info in sorted(source.items()):
        if not isinstance(info, dict):
            continue
        idx += 1
        total = info.get("total", 0)
        detected = info.get("detected", 0)
        if detected > 0:
            status_cls = "detected"
            status_text = f"✓ Detected ({detected}/{total})"
        elif total > 0:
            status_cls = "undetected"
            status_text = f"✗ Missed (0/{total})"
        else:
            status_cls = "no-data"
            status_text = "— No data"
        rows.append(
            f'<tr><td>{idx}</td>'
            f'<td>{name}</td>'
            f'<td class="{status_cls}">{status_text}</td>'
            f'<td>recall: {info.get("recall", "—")}</td>'
            f'</tr>'
        )

    if idx == 0:
        rows.append('<tr><td colspan="4" class="no-data">No coverage data available.</td></tr>')

    rows.append('</table>')
    return "\n".join(rows)


def generate_report() -> str:
    # Default metrics are EMPTY — no fabricated data.
    # Real metrics come from benchmark_metrics.json (written by benchmark_compute.py).
    data: dict = {}

    if BENCHMARK_JSON.exists():
        try:
            data = json.loads(BENCHMARK_JSON.read_text("utf-8"))
        except Exception:
            data = {}

    has_real_data = bool(data and data.get("benchmark_active"))

    # Read metrics from real data only
    recall = data.get("recall", None)
    precision = data.get("precision", None)
    f1_score = data.get("f1_score", None)
    fnr = data.get("false_negative_rate", None)
    ev = data.get("evidence_completeness_rate", data.get("evidence_completeness", None))
    repro = data.get("reproduction_success_rate", data.get("repro_success", None))
    regr = data.get("regression_success_rate", None)

    # Bug type breakdown from real data
    bug_type_data = data.get("bug_type_breakdown", {})
    risk_family_data = data.get("risk_family_breakdown", {})

    # P3 coverage: computed from real data, not hardcoded
    total_bug_types = len(bug_type_data) if bug_type_data else 0
    detected_bug_types = sum(
        1 for v in bug_type_data.values()
        if isinstance(v, dict) and v.get("detected", 0) > 0
    ) if bug_type_data else 0
    p3_cov = f"{detected_bug_types}/{total_bug_types}" if total_bug_types > 0 else "0/0"

    # Color helpers
    def _safe_val(v, default="—"):
        """Return formatted value or a placeholder when no data is available."""
        if v is None:
            return default
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)

    def _safe_pct(v, default="—"):
        """Return percentage bar width or 0."""
        if v is None:
            return 0
        if isinstance(v, (int, float)):
            return min(abs(v) * 100, 100)
        return 0

    recall_num = recall if isinstance(recall, (int, float)) else 0
    precision_num = precision if isinstance(precision, (int, float)) else 0
    fnr_num = fnr if isinstance(fnr, (int, float)) else 0

    recall_cls = "recall-good" if recall_num >= 0.8 else ("recall-ok" if recall_num >= 0.5 else "recall-low") if recall_num else "recall-low"
    precision_cls = "precision-good" if precision_num >= 0.7 else ("precision-ok" if precision_num >= 0.4 else "precision-low") if precision_num else "precision-low"
    fnr_cls = "recall-good" if fnr_num <= 0.2 else ("recall-ok" if fnr_num <= 0.4 else "recall-low") if fnr_num else "recall-low"

    # Build coverage table rows from real data
    coverage_rows_html = _build_coverage_rows(bug_type_data, risk_family_data)

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>QualiBug Benchmark Metrics</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1e293b,#334155);padding:32px;text-align:center;border-bottom:2px solid #3b82f6}}
.header h1{{font-size:28px;color:#f8fafc}}.header p{{color:#94a3b8;margin-top:8px}}
.no-data-banner{{background:#422006;color:#fbbf24;padding:16px;text-align:center;font-size:13px;border-bottom:1px solid #78350f}}
.refresh{{font-size:11px;color:#64748b;margin-top:6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;padding:24px;max-width:1200px;margin:0 auto}}
.card{{background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155}}
.card h3{{font-size:13px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}}
.card .value{{font-size:36px;font-weight:700;color:#f8fafc}}
.card .sub{{font-size:12px;color:#64748b;margin-top:4px}}
.recall-good{{color:#22c55e}}.recall-ok{{color:#eab308}}.recall-low{{color:#ef4444}}
.bar-container{{margin-top:16px}}.bar-label{{display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px}}
.bar{{height:8px;border-radius:4px;background:#334155;overflow:hidden}}
.bar-fill{{height:100%;border-radius:4px;transition:width 0.5s}}
.bar-recall{{background:linear-gradient(90deg,#3b82f6,#22c55e)}}
.bar-precision{{background:linear-gradient(90deg,#8b5cf6,#3b82f6)}}
.bar-ev{{background:linear-gradient(90deg,#06b6d4,#22c55e)}}
.bar-f1{{background:linear-gradient(90deg,#f59e0b,#ef4444)}}
.bar-repro{{background:linear-gradient(90deg,#f59e0b,#22c55e)}}
.bar-fnr{{background:linear-gradient(90deg,#ef4444,#22c55e)}}
.bar-regr{{background:linear-gradient(90deg,#06b6d4,#8b5cf6)}}
.coverage-table{{padding:24px;max-width:1200px;margin:0 auto}}
.coverage-table table{{width:100%;border-collapse:collapse;font-size:13px}}
.coverage-table th{{background:#1e293b;color:#94a3b8;padding:10px;text-align:left;border-bottom:1px solid #334155}}
.coverage-table td{{padding:10px;border-bottom:1px solid #1e293b}}
.coverage-table .detected{{color:#22c55e;font-weight:600}}
.coverage-table .undetected{{color:#ef4444}}
.coverage-table .no-data{{color:#64748b;font-style:italic}}
.trend-section{{padding:24px;max-width:1200px;margin:0 auto}}
.trend-section h2{{color:#f8fafc;margin-bottom:16px}}
.trend-bar{{display:flex;align-items:flex-end;gap:4px;height:80px;padding:8px;background:#1e293b;border-radius:8px}}
.trend-bar-item{{flex:1;min-width:12px;border-radius:3px 3px 0 0;transition:height 0.3s}}
.footer{{text-align:center;padding:24px;color:#475569;font-size:11px}}
</style>
<script>
const API_URL = '{BACKEND_API}/api/benchmark/metrics';
let refreshCount = 0;
async function refreshMetrics() {{
    refreshCount++;
    try {{
        const resp = await fetch(API_URL, {{signal: AbortSignal.timeout(5000)}});
        if (resp.ok) {{
            const metrics = await resp.json();
            Object.entries(metrics).forEach(([key, val]) => {{
                const el = document.getElementById('metric-' + key);
                if (el) el.textContent = typeof val === 'number' ? val.toFixed(2) : val;
            }});
            document.getElementById('refresh-status').textContent =
                '✓ Auto-refreshed ' + refreshCount + ' times · ' + new Date().toLocaleTimeString('zh-CN');
        }}
    }} catch (e) {{
        document.getElementById('refresh-status').textContent =
            '⚠ Refresh failed (#' + refreshCount + ') · showing cached data';
    }}
}}
setInterval(refreshMetrics, 60000);
window.addEventListener('DOMContentLoaded', () => {{
    document.getElementById('refresh-status').textContent =
        'Waiting for first refresh... · ' + new Date().toLocaleTimeString('zh-CN');
}});
</script>
</head>
<body>
<div class="header"><h1>QualiBug — Benchmark Metrics</h1><p>Metrics from real bug discovery runs · no fabricated data</p><p class="refresh" id="refresh-status">Waiting for first refresh...</p></div>
{_no_data_banner(has_real_data)}
<div class="grid">
<div class="card"><h3>Recall</h3><div class="value {recall_cls}" id="metric-recall">{_safe_val(recall)}</div><div class="sub">Target ≥ 0.80</div>
<div class="bar-container"><div class="bar"><div class="bar-fill bar-recall" style="width:{_safe_pct(recall):.0f}%"></div></div></div></div>
<div class="card"><h3>Precision</h3><div class="value {precision_cls}" id="metric-precision">{_safe_val(precision)}</div><div class="sub">Target ≥ 0.70</div>
<div class="bar-container"><div class="bar"><div class="bar-fill bar-precision" style="width:{_safe_pct(precision):.0f}%"></div></div></div></div>
<div class="card"><h3>F1 Score</h3><div class="value {recall_cls}" id="metric-f1_score">{_safe_val(f1_score)}</div><div class="sub">Harmonic mean of recall & precision</div>
<div class="bar-container"><div class="bar"><div class="bar-fill bar-f1" style="width:{_safe_pct(f1_score):.0f}%"></div></div></div></div>
<div class="card"><h3>False Negative Rate</h3><div class="value {fnr_cls}" id="metric-false_negative_rate">{_safe_val(fnr)}</div><div class="sub">Target ≤ 0.20</div>
<div class="bar-container"><div class="bar"><div class="bar-fill bar-fnr" style="width:{max(0, (1 - fnr_num) * 100):.0f}%"></div></div></div></div>
<div class="card"><h3>Evidence Completeness</h3><div class="value recall-good" id="metric-evidence_completeness_rate">{_safe_val(ev)}</div><div class="sub">Target = 1.00</div>
<div class="bar-container"><div class="bar"><div class="bar-fill bar-ev" style="width:{_safe_pct(ev):.0f}%"></div></div></div></div>
<div class="card"><h3>Reproduction Success</h3><div class="value recall-good" id="metric-reproduction_success_rate">{_safe_val(repro)}</div><div class="sub">Target = 1.00</div>
<div class="bar-container"><div class="bar"><div class="bar-fill bar-repro" style="width:{_safe_pct(repro):.0f}%"></div></div></div></div>
<div class="card"><h3>Regression Success</h3><div class="value recall-good" id="metric-regression_success_rate">{_safe_val(regr)}</div><div class="sub">Target = 1.00</div>
<div class="bar-container"><div class="bar"><div class="bar-fill bar-regr" style="width:{_safe_pct(regr):.0f}%"></div></div></div></div>
</div>
<div class="coverage-table"><h2 style="margin-bottom:16px;color:#f8fafc">Bug Type Detection Matrix ({p3_cov})</h2>
{coverage_rows_html}
</div>
<div class="footer">QualiBug AI · Benchmark Dashboard · Auto-refresh 60s · All metrics from real data only</div>
</body></html>"""
    return html


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "platform_outputs" / "p6_benchmark_dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(generate_report(), encoding="utf-8")
    print(f"Benchmark dashboard generated: {out}")

    # Only write metrics JSON if real data exists (from BENCHMARK_JSON)
    if BENCHMARK_JSON.exists():
        try:
            existing = json.loads(BENCHMARK_JSON.read_text("utf-8"))
        except Exception:
            existing = {}
    else:
        existing = {}

    if existing and existing.get("benchmark_active"):
        # Real data exists — append to history
        BENCHMARK_JSON.parent.mkdir(parents=True, exist_ok=True)
        _save_history(existing)
        print(f"Benchmark history updated: {BENCHMARK_HISTORY}")
    else:
        print("No real benchmark data found — skipping metrics JSON and history write.")
        print("Run a benchmark scan first to populate real metrics.")
