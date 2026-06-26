from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _safe_project_id, load_real_project_config

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}
DESTRUCTIVE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HIGH_RISK_TYPES = {"permission_bypass", "idor", "tenant_isolation", "payment", "refund", "money", "stock", "order_state", "idempotency", "duplicate_submit"}
MODE_LIMITS = {"smoke": 20, "release": 80, "full": 500}
SEVERITY_WEIGHT = {"P0": 100, "P1": 80, "P2": 45, "P3": 20}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_json_safe(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _safe_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text[:limit]


def _normalize_probe_id(raw: Any, index: int) -> str:
    text = str(raw or f"REGRESSION_{index:04d}")
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return (text or f"REGRESSION_{index:04d}")[:96]


def _infer_module(path: str, risk_type: str, title: str = "") -> str:
    haystack = f"{path} {risk_type} {title}".lower()
    mapping = [
        ("auth", ["auth", "login", "token", "role", "rbac", "permission", "admin", "user", "tenant", "idor"]),
        ("order", ["order", "cart", "checkout", "cancel"]),
        ("payment", ["pay", "payment", "callback", "trade"]),
        ("refund", ["refund", "return"]),
        ("inventory", ["stock", "inventory", "sku"]),
        ("coupon", ["coupon", "discount", "promotion"]),
        ("account", ["account", "profile", "address"]),
    ]
    for module, keys in mapping:
        if any(k in haystack for k in keys):
            return module
    parts = [p for p in path.strip("/").split("/") if p]
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", (parts[0] if parts else "general"))[:48] or "general"


def _is_destructive(method: str, risk_type: str) -> bool:
    return method.upper() in DESTRUCTIVE_METHODS or risk_type.lower() in {"payment", "refund", "idempotency", "duplicate_submit", "concurrency", "delete", "cancel_order"}


def _load_fix_regression_probes(project: str, root: Path) -> list[dict[str, Any]]:
    """Load traditional fix-regression probes plus Phase55 approved confirmations.

    Phase55 candidates are already approval-gated.  They are included alongside
    fix-verification probes so a confirmed customer defect becomes a durable
    regression obligation, without requiring raw business payload persistence.
    """
    project = _safe_project_id(project)
    probes: list[dict[str, Any]] = []
    p = root / "platform_workspace" / project / "defect_discovery" / "fix_regression_probes.json"
    data = _load_json_safe(p, {})
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        probes.extend(i for i in data["items"] if isinstance(i, dict))
    elif isinstance(data, list):
        probes.extend(i for i in data if isinstance(i, dict))

    phase55 = root / "platform_workspace" / project / "defect_discovery" / "confirmed_bug_regression_candidates.json"
    phase55_data = _load_json_safe(phase55, {})
    phase55_items = phase55_data.get("items") if isinstance(phase55_data, dict) else phase55_data
    if isinstance(phase55_items, list):
        probes.extend(
            {**item, "source": item.get("source") or "phase55_confirmed_bug_flywheel"}
            for item in phase55_items
            if isinstance(item, dict) and item.get("approved") is True
        )

    if probes:
        return probes

    # Fallback: infer candidates from fix verification result when probes have
    # not yet been materialized.
    result = _load_json_safe(root / "platform_outputs" / project / "fix_verification" / "fix_verification_result.json", {})
    if isinstance(result, dict):
        for item in result.get("items", []) or []:
            if isinstance(item, dict) and item.get("verification_status") in {"fixed", "still_failing", "needs_review"}:
                probes.append({
                    "regression_probe_id": f"REG_{item.get('verification_id') or item.get('issue_id')}",
                    "issue_id": item.get("issue_id"),
                    "title": item.get("title"),
                    "risk_type": item.get("risk_type") or "business_risk",
                    "severity": item.get("severity") or "P2",
                    "method": item.get("method") or (item.get("evidence", {}).get("request", {}) if isinstance(item.get("evidence"), dict) else {}).get("method") or "GET",
                    "path": item.get("path") or (item.get("evidence", {}).get("request", {}) if isinstance(item.get("evidence"), dict) else {}).get("url") or "/",
                    "actor": (item.get("evidence", {}).get("request", {}) if isinstance(item.get("evidence"), dict) else {}).get("actor") or "normal_user",
                    "expected": "原缺陷信号不应复现。",
                    "source": "fix_verification_result",
                })
    return probes


def _risk_score(probe: dict[str, Any]) -> float:
    severity = str(probe.get("severity") or "P2").upper()
    risk_type = str(probe.get("risk_type") or "business_risk").lower()
    method = str(probe.get("method") or "GET").upper()
    score = float(SEVERITY_WEIGHT.get(severity, 35))
    if risk_type in HIGH_RISK_TYPES:
        score += 18
    if method in DESTRUCTIVE_METHODS:
        score += 10
    if probe.get("source") == "fix_verification_loop":
        score += 10
    if probe.get("source") == "phase55_confirmed_bug_flywheel":
        score += 14
    if str(probe.get("issue_id") or ""):
        score += 4
    return round(score, 2)


def _normalize_probe(probe: dict[str, Any], index: int) -> dict[str, Any]:
    risk_type = _safe_text(probe.get("risk_type") or "business_risk", 100)
    method = _safe_text(probe.get("method") or "GET", 12).upper()
    path = _safe_text(probe.get("path") or probe.get("url") or "/", 300)
    title = _safe_text(probe.get("title") or probe.get("expected") or f"回归探针 {index}", 240)
    severity = _safe_text(probe.get("severity") or "P2", 16).upper()
    normalized = {
        "regression_probe_id": _normalize_probe_id(probe.get("regression_probe_id") or probe.get("probe_id") or probe.get("id"), index),
        "issue_id": _safe_text(probe.get("issue_id"), 120),
        "title": title,
        "module": _safe_text(probe.get("module") or _infer_module(path, risk_type, title), 80),
        "risk_type": risk_type,
        "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
        "method": method,
        "path": path,
        "actor": _safe_text(probe.get("actor") or "normal_user", 80),
        "expected": _safe_text(probe.get("expected") or "原缺陷信号不应复现，业务规则保持正确。", 1200),
        "source": _safe_text(probe.get("source") or "fix_regression_probes", 120),
        "destructive": _is_destructive(method, risk_type),
    }
    normalized["priority_score"] = _risk_score(normalized)
    normalized["tags"] = [normalized["severity"], normalized["risk_type"], normalized["module"]]
    return normalized


def _dedupe_sort(probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for idx, raw in enumerate(probes, start=1):
        p = _normalize_probe(raw, idx)
        key = (p["method"], p["path"], p["risk_type"], p.get("issue_id") or p["regression_probe_id"])
        old = seen.get(key)
        if not old or p["priority_score"] > old["priority_score"]:
            seen[key] = p
    return sorted(seen.values(), key=lambda item: (-float(item.get("priority_score") or 0), str(item.get("module") or ""), str(item.get("path") or "")))


def _select_modes(probes: list[dict[str, Any]], cfg: dict[str, Any], options: dict[str, Any]) -> dict[str, dict[str, Any]]:
    max_smoke = int(options.get("max_smoke") or cfg.get("regression_smoke_max") or MODE_LIMITS["smoke"])
    max_release = int(options.get("max_release") or cfg.get("regression_release_max") or MODE_LIMITS["release"])
    max_full = int(options.get("max_full") or cfg.get("regression_full_max") or MODE_LIMITS["full"])
    safe_only = not bool(options.get("allow_destructive_regression") or cfg.get("allow_destructive_tests"))
    non_destructive = [p for p in probes if not (safe_only and p.get("destructive"))]
    p0p1 = [p for p in non_destructive if p.get("severity") in {"P0", "P1"}]
    high = [p for p in non_destructive if p.get("risk_type") in HIGH_RISK_TYPES]
    smoke_candidates = []
    seen_ids: set[str] = set()
    for p in [*p0p1, *high, *non_destructive]:
        pid = str(p.get("regression_probe_id"))
        if pid not in seen_ids:
            smoke_candidates.append(p)
            seen_ids.add(pid)
    release_candidates = non_destructive
    full_candidates = probes if bool(options.get("allow_destructive_regression") or cfg.get("allow_destructive_tests")) else non_destructive
    return {
        "smoke": {"mode": "smoke", "description": "发布前快速回归，只覆盖 P0/P1、高风险、非破坏性探针。", "items": smoke_candidates[:max_smoke]},
        "release": {"mode": "release", "description": "版本发布回归，覆盖高优先级和历史修复问题，默认跳过破坏性探针。", "items": release_candidates[:max_release]},
        "full": {"mode": "full", "description": "完整回归套件，覆盖所有已沉淀修复回归探针。", "items": full_candidates[:max_full]},
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = [m for m in PRIVATE_MARKERS if m.lower() in text]
    return {"passed": not leaks, "checked": True, "leak_count": len(leaks)}


def _render_report(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    cards = "".join(
        f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>"
        for label, value in {
            "总探针": summary.get("total_probe_count"),
            "Smoke": summary.get("smoke_count"),
            "Release": summary.get("release_count"),
            "Full": summary.get("full_count"),
            "P0/P1": summary.get("p0_p1_count"),
            "CI 建议": summary.get("ci_gate_recommendation"),
        }.items()
    )
    rows = []
    for item in result.get("modes", {}).get("release", {}).get("items", [])[:120]:
        rows.append(
            "<tr>"
            f"<td>{_html_escape(item.get('priority_score'))}</td>"
            f"<td>{_html_escape(item.get('severity'))}</td>"
            f"<td>{_html_escape(item.get('module'))}</td>"
            f"<td>{_html_escape(item.get('risk_type'))}</td>"
            f"<td>{_html_escape(item.get('method'))} {_html_escape(item.get('path'))}</td>"
            f"<td>{_html_escape(item.get('title'))}</td>"
            "</tr>"
        )
    module_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{v}</td></tr>" for k, v in summary.get("module_distribution", {}).items())
    risk_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{v}</td></tr>" for k, v in summary.get("risk_distribution", {}).items())
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>回归套件构建器</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:24px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eff6ff;color:#1d4ed8}}</style></head><body>
<section class='hero'><span class='badge'>Phase31 Regression Suite</span><h1>{_html_escape(summary.get('project_name'))}</h1><p>将修复验证产生的回归探针组织为 smoke / release / full 三种长期运行套件，按模块、风险类型、严重等级排序，并输出 CI 可消费的套件清单。</p><p>生成时间：{_html_escape(summary.get('generated_at'))} · 私有数据隔离：{_html_escape(summary.get('private_leak_check_passed'))}</p></section>
<section class='panel'><h2>套件概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>Release 回归套件 Top 探针</h2><table><thead><tr><th>优先级</th><th>等级</th><th>模块</th><th>风险</th><th>接口</th><th>标题</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">暂无回归探针</td></tr>'}</tbody></table></section>
<section class='panel'><h2>分布</h2><div class='grid'><div><h3>模块</h3><table>{module_rows or '<tr><td>暂无</td><td>0</td></tr>'}</table></div><div><h3>风险类型</h3><table>{risk_rows or '<tr><td>暂无</td><td>0</td></tr>'}</table></div><div><h3>CI 建议</h3><p>{_html_escape(summary.get('ci_gate_recommendation'))}</p><p>如果 release 套件中 P0/P1 回归失败，建议阻断发布；P2 回归失败建议人工复核。</p></div></div></section>
</body></html>"""


def build_regression_suite(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    raw_probes = _load_fix_regression_probes(project, root)
    probes = _dedupe_sort(raw_probes)
    modes = _select_modes(probes, cfg, options)
    p0_p1_count = sum(1 for p in probes if p.get("severity") in {"P0", "P1"})
    destructive_count = sum(1 for p in probes if p.get("destructive"))
    ci_gate_recommendation = "run_release_suite"
    if not probes:
        ci_gate_recommendation = "no_regression_suite_yet"
    elif p0_p1_count >= 1:
        ci_gate_recommendation = "block_on_p0_p1_regression_failure"
    summary = {
        "phase": "phase31_regression_suite_builder",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_probe_count": len(probes),
        "smoke_count": len(modes["smoke"]["items"]),
        "release_count": len(modes["release"]["items"]),
        "full_count": len(modes["full"]["items"]),
        "p0_p1_count": p0_p1_count,
        "destructive_probe_count": destructive_count,
        "allow_destructive_regression": bool(options.get("allow_destructive_regression") or cfg.get("allow_destructive_tests")),
        "module_distribution": _count_by(probes, "module"),
        "risk_distribution": _count_by(probes, "risk_type"),
        "severity_distribution": _count_by(probes, "severity"),
        "ci_gate_recommendation": ci_gate_recommendation,
    }
    ci_gate = {
        "project_id": project,
        "suite": "release",
        "gate_policy": {
            "fail_on_p0_p1_regression": True,
            "manual_review_on_p2_regression": True,
            "skip_destructive_by_default": not summary["allow_destructive_regression"],
        },
        "expected_exit_codes": {
            "passed": 0,
            "manual_review_required": 1,
            "failed": 2,
        },
        "recommendation": ci_gate_recommendation,
    }
    result = {
        "phase": "phase31_regression_suite_builder",
        "project_id": project,
        "summary": summary,
        "modes": modes,
        "ci_gate": ci_gate,
    }
    private_check = _private_leak_check(result)
    summary["private_leak_check_passed"] = private_check["passed"]
    result["private_leak_check"] = private_check

    out_dir = root / "platform_outputs" / project / "regression_suite"
    workspace_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "regression_suite.json", result)
    _write_json(out_dir / "regression_suite_summary.json", summary)
    _write_json(out_dir / "regression_suite_ci_gate.json", ci_gate)
    _write_text(out_dir / "regression_suite_report.html", _render_report(result))
    _write_json(workspace_dir / "regression_suite.json", result)
    _write_json(workspace_dir / "regression_suite_manifest.json", {"summary": summary, "artifacts": {"report_html": str((out_dir / 'regression_suite_report.html').relative_to(root)).replace('\\', '/')}})
    return result


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    allow_destructive = str(os.environ.get("ALLOW_DESTRUCTIVE_REGRESSION", "0")).lower() in {"1", "true", "yes", "on"}
    result = build_regression_suite(project, options={"allow_destructive_regression": allow_destructive})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
