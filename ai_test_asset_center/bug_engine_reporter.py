"""Customer-facing bug-engine report builder.

The discovery loop keeps rich runtime evidence for internal validation.  This
module turns that result into a redacted, reviewer-ready deliverable:

- validated_bug_report.json: structured, redacted finding inventory
- validated_bug_report.md: human-readable customer report
- validated_bug_repro.ps1: copy/paste reproduction commands for Windows users
- validated_bug_regression_pytest.py: pytest regression assertions for CI

It intentionally does not upgrade verdicts.  It only serializes findings that
have already passed the discovery/evidence gates as validated candidates.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


_SECRET_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|authorization|cookie|session|apikey|api_key|access[_-]?key|credential)",
    re.I,
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _parse_call(call: str) -> tuple[str, str]:
    parts = str(call or "").strip().split(maxsplit=1)
    if not parts:
        return "GET", ""
    method = parts[0].upper()
    path = parts[1] if len(parts) > 1 else ""
    return method, path


def _is_validated(finding: dict[str, Any]) -> bool:
    verdict = str(finding.get("verdict") or "").lower()
    gate_verdict = str(((finding.get("evidence") or {}).get("finding_gate") or {}).get("verdict") or "").lower()
    return verdict in {"validated_candidate", "validated"} or gate_verdict == "validated_candidate"


def _redacted_summary(value: Any, *, depth: int = 0) -> Any:
    """Return a shareable structural summary without raw sensitive values."""
    if depth > 4:
        return {"_truncated": True, "type": type(value).__name__}
    if isinstance(value, dict):
        out: dict[str, Any] = {"_redacted": True, "type": "object", "keys": [str(k) for k in list(value.keys())[:20]]}
        for key in list(value.keys())[:12]:
            skey = str(key)
            if _SECRET_KEY_RE.search(skey):
                out[skey] = "<redacted>"
                continue
            child = value.get(key)
            if isinstance(child, (dict, list)):
                out[skey] = _redacted_summary(child, depth=depth + 1)
            elif child in (None, "", [], {}):
                out[skey] = child
            else:
                out[skey] = {"type": type(child).__name__, "has_value": True}
        return out
    if isinstance(value, list):
        return {
            "_redacted": True,
            "type": "list",
            "size": len(value),
            "sample": [_redacted_summary(v, depth=depth + 1) for v in value[:2]],
        }
    return {"_redacted": True, "type": type(value).__name__, "has_value": value not in (None, "")}


def _extract_calls(finding: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    calls = evidence.get("calls") if isinstance(evidence.get("calls"), list) else []
    contract = evidence.get("business_finding") if isinstance(evidence.get("business_finding"), dict) else {}
    if not calls and isinstance(contract.get("calls"), list):
        calls = contract.get("calls") or []
    result: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        method, path = _parse_call(str(call.get("call") or ""))
        roles: dict[str, Any] = {}
        for role, response in (call.get("results") or {}).items():
            if not isinstance(response, dict):
                continue
            roles[str(role)] = {
                "status": response.get("status"),
                "body_summary": _redacted_summary(response.get("body", {})),
            }
        result.append({"method": method, "path": path, "roles": roles})
    return result


def _first_call(finding: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    calls = _extract_calls(finding)
    if calls:
        return calls[0].get("method", "GET"), calls[0].get("path", ""), calls[0]
    title = str(finding.get("title") or "")
    m = re.search(r"(/api/[\w\-/{}/?=&%.]+)", title)
    return "GET", (m.group(1) if m else ""), {}


def _sensitivity_tags(finding: dict[str, Any], call: dict[str, Any]) -> list[str]:
    text = json.dumps({"title": finding.get("title"), "call": call}, ensure_ascii=False).lower()
    tags: list[str] = []
    for label, needles in {
        "auth_boundary": ["auth", "认证", "permission", "权限", "anonymous", "匿名", "401", "403"],
        "business_data_exposure": ["业务数据", "business", "data", "export", "audit", "users"],
        "audit_or_operational_data": ["audit", "审计", "log", "orders", "inventory", "warehouse"],
    }.items():
        if any(n in text for n in needles):
            tags.append(label)
    return tags or ["business_risk"]


def _root_cause_key(finding: dict[str, Any], method: str, path: str) -> str:
    title = str(finding.get("title") or "").lower()
    if any(t in title for t in ["认证", "权限", "auth", "permission", "匿名", "anonymous"]):
        return "missing_auth_or_authorization_boundary"
    if method and path:
        return f"{method} {path}"
    return "unclassified_business_risk"


def _repro_command(method: str, path: str, base_url: str) -> str:
    if not path:
        return ""
    host = base_url.rstrip("/") + "/"
    url = urljoin(host, path.lstrip("/"))
    method = (method or "GET").upper()
    if method == "GET":
        return f'curl.exe -i "{url}"'
    return f'curl.exe -i -X {method} "{url}"'


def _pytest_name(value: str, fallback: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z_]+", "_", str(value or "").strip("/"))
    slug = re.sub(r"_+", "_", slug).strip("_").lower()
    if not slug:
        slug = fallback
    if slug[0].isdigit():
        slug = "endpoint_" + slug
    return slug[:80]


def _pytest_regression(findings: list[dict[str, Any]], base_url: str) -> str:
    """Generate safe read-only pytest regression tests for validated findings.

    These tests intentionally assert the *fixed* expected behaviour.  A currently
    vulnerable target will fail them, which is exactly what customers need after
    remediation.  Only GET findings are emitted; mutating cases stay out of this
    CI artifact until explicit sandbox support is available.
    """
    lines = [
        '"""Generated by QualiBug. Validates fixed authorization boundaries."""',
        'import os',
        'import requests',
        '',
        f'DEFAULT_BASE_URL = {base_url!r}',
        'BASE_URL = os.environ.get("QUALIBUG_TARGET_BASE_URL", DEFAULT_BASE_URL).rstrip("/")',
        '',
    ]
    emitted = 0
    seen: set[str] = set()
    for idx, finding in enumerate(findings, start=1):
        method = str(finding.get("method") or "GET").upper()
        path = str(finding.get("path") or "")
        if method != "GET" or not path:
            continue
        name = _pytest_name(path, f"finding_{idx}")
        if name in seen:
            name = f"{name}_{idx}"
        seen.add(name)
        emitted += 1
        lines.extend([
            f'def test_{name}_rejects_anonymous_access():',
            f'    path = {path!r}',
            '    resp = requests.get(BASE_URL + path, timeout=10)',
            '    assert resp.status_code in (401, 403), (resp.status_code, resp.text[:500])',
            '',
        ])
    if emitted == 0:
        lines.extend([
            'def test_no_read_only_validated_findings_generated():',
            '    # No GET validated candidates were present when this regression file was generated.',
            '    assert True',
            '',
        ])
    return "\n".join(lines)


def extract_validated_findings(result: dict[str, Any], *, base_url: str = "http://127.0.0.1:8000") -> list[dict[str, Any]]:
    discovery = result.get("discovery_result") if isinstance(result.get("discovery_result"), dict) else result
    findings = discovery.get("findings") if isinstance(discovery.get("findings"), list) else []
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in findings:
        if not isinstance(raw, dict) or not _is_validated(raw):
            continue
        method, path, call = _first_call(raw)
        root_cause = _root_cause_key(raw, method, path)
        dedup_key = (root_cause, method, path)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
        business_finding = evidence.get("business_finding") if isinstance(evidence.get("business_finding"), dict) else {}
        gate = evidence.get("finding_gate") if isinstance(evidence.get("finding_gate"), dict) else {}
        severity = _text(raw.get("severity") or (business_finding.get("business_impact") or {}).get("scope") or "P2", 20)
        item = {
            "id": _text(raw.get("id") or raw.get("hypothesis_id") or business_finding.get("finding_id"), 120),
            "title": _text(raw.get("title") or business_finding.get("title"), 300),
            "severity": severity,
            "verdict": "validated_candidate",
            "confidence": raw.get("confidence") or (business_finding.get("confidence") or {}).get("score") or "medium",
            "risk_type": root_cause,
            "method": method,
            "path": path,
            "expected": _text(raw.get("expected") or business_finding.get("business_intent"), 500),
            "actual": _text(raw.get("actual") or business_finding.get("root_cause_candidate"), 500),
            "business_impact": {
                "summary": "匿名或低权限访问返回业务数据，可能导致企业业务数据泄露或越权读取。"
                if root_cause == "missing_auth_or_authorization_boundary" else _text(raw.get("actual"), 300),
                "tags": _sensitivity_tags(raw, call),
            },
            "evidence_summary": {
                "runtime_gate_status": gate.get("runtime_gate_status") or business_finding.get("runtime_gate_status"),
                "business_gate_status": gate.get("business_gate_status") or business_finding.get("business_gate_status"),
                "call_count": len(_extract_calls(raw)),
                "calls": _extract_calls(raw)[:3],
            },
            "reproduction": {
                "steps": [
                    "确保未携带认证凭证、Cookie 或 Authorization Header。",
                    f"执行只读请求：{method} {path}" if path else "执行报告中的只读请求。",
                    "观察响应状态码和响应结构。",
                    "若返回 HTTP 200 且包含业务数据，而期望为 401/403，则权限边界缺失成立。",
                ],
                "powershell": _repro_command(method, path, base_url),
            },
            "suggested_fix": (
                "在后端路由层强制认证和角色/资源授权校验；默认拒绝匿名请求；避免只依赖前端菜单或页面权限；"
                "为该接口加入回归测试，断言匿名/低权限访问返回 401/403。"
                if root_cause == "missing_auth_or_authorization_boundary"
                else "在后端实现业务不变量校验，并补充最小复现回归测试。"
            ),
        }
        items.append(item)
    return items


def _cluster_summary(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for f in findings:
        key = str(f.get("risk_type") or "unclassified")
        row = clusters.setdefault(key, {"risk_type": key, "count": 0, "severity_max": "P3", "paths": []})
        row["count"] += 1
        sev = str(f.get("severity") or "P3")
        if sev < str(row["severity_max"]):
            row["severity_max"] = sev
        if f.get("path") and f.get("path") not in row["paths"]:
            row["paths"].append(f.get("path"))
    return sorted(clusters.values(), key=lambda r: (str(r.get("severity_max")), -int(r.get("count", 0))))


def _markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# QualiBug Validated Bug Report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Project: `{report['project_id']}`")
    lines.append("")
    s = report["summary"]
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Terminal: `{s.get('terminal', '')}`")
    lines.append(f"- Engine validated candidates: **{s.get('engine_validated_candidates', 0)}**")
    lines.append(f"- Unique report findings after endpoint/root-cause dedup: **{s.get('unique_report_findings', 0)}**")
    lines.append(f"- Raw confirmed signals: {s.get('raw_confirmed_signals', 0)}")
    lines.append(f"- Needs more evidence: {s.get('needs_more_evidence', 0)}")
    lines.append(f"- Unresolved rate: {s.get('inconclusive_rate', 0)}")
    lines.append("")
    lines.append("## Root cause clusters")
    lines.append("")
    for c in report.get("clusters", []):
        lines.append(f"- **{c['risk_type']}**: {c['count']} finding(s), max severity `{c['severity_max']}`, paths: {', '.join(c['paths'][:8])}")
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    for idx, f in enumerate(report.get("findings", []), start=1):
        lines.append(f"### {idx}. {f['title']}")
        lines.append("")
        lines.append(f"- Severity: `{f['severity']}`")
        lines.append(f"- Verdict: `{f['verdict']}`")
        lines.append(f"- Endpoint: `{f['method']} {f['path']}`")
        lines.append(f"- Expected: {f['expected']}")
        lines.append(f"- Actual: {f['actual']}")
        lines.append(f"- Impact: {f['business_impact']['summary']}")
        lines.append(f"- Evidence gate: runtime=`{f['evidence_summary'].get('runtime_gate_status')}`, business=`{f['evidence_summary'].get('business_gate_status')}`")
        if f.get("reproduction", {}).get("powershell"):
            lines.append("- Reproduction:")
            lines.append("")
            lines.append("```powershell")
            lines.append(f["reproduction"]["powershell"])
            lines.append("```")
        lines.append(f"- Suggested fix: {f['suggested_fix']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_customer_bug_report(
    result: dict[str, Any],
    output_dir: str | Path,
    *,
    project_id: str = "real_project_demo",
    base_url: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    discovery = result.get("discovery_result") if isinstance(result.get("discovery_result"), dict) else result
    findings = extract_validated_findings(result, base_url=base_url)
    report = {
        "report_version": "phase92f-customer-evidence-v2",
        "generated_at": _now(),
        "project_id": project_id,
        "summary": {
            "terminal": discovery.get("terminal"),
            "execution_status": discovery.get("execution_status"),
            "validated_candidates": len(findings),
            "engine_validated_candidates": discovery.get("validated_candidates"),
            "unique_report_findings": len(findings),
            "raw_confirmed_signals": discovery.get("raw_confirmed_signals"),
            "needs_more_evidence": discovery.get("needs_more_evidence"),
            "inconclusive_rate": discovery.get("inconclusive_rate"),
        },
        "clusters": _cluster_summary(findings),
        "findings": findings,
        "safety": {
            "raw_response_values_redacted": True,
            "customer_verdict_note": "validated_candidate means runtime and business evidence gates passed; human confirmation is still required before marking confirmed.",
        },
    }
    json_path = output / "validated_bug_report.json"
    md_path = output / "validated_bug_report.md"
    ps1_path = output / "validated_bug_repro.ps1"
    pytest_path = output / "validated_bug_regression_pytest.py"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    commands = [f.get("reproduction", {}).get("powershell", "") for f in findings]
    ps1 = ["# QualiBug validated candidate reproduction commands", "$ErrorActionPreference = 'Continue'", ""]
    for idx, cmd in enumerate([c for c in commands if c], start=1):
        ps1.append(f"Write-Host 'Finding {idx}'")
        ps1.append(cmd)
        ps1.append("")
    ps1_path.write_text("\n".join(ps1), encoding="utf-8")
    pytest_path.write_text(_pytest_regression(findings, base_url), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "repro_ps1": str(ps1_path),
        "regression_pytest": str(pytest_path),
        "finding_count": len(findings),
    }
