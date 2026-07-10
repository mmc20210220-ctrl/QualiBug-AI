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
        "audit_or_operational_data": ["audit", "审计", "log", "trace", "record", "history"],
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


def extract_validated_findings(result: dict[str, Any], *, base_url: str = "http://127.0.0.1:8088") -> list[dict[str, Any]]:
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
    base_url: str = "http://127.0.0.1:8088",
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

    # ── Customer Reproduction Pack (5-step: setup→before→target→after→cleanup) ──
    repro_pack_path = output / "customer_reproduction_pack.json"
    repro_pack_md_path = output / "customer_reproduction_pack.md"
    repro_pack = _build_customer_repro_pack(findings, base_url, report["project_id"])
    repro_pack_path.write_text(json.dumps(repro_pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    repro_pack_md_path.write_text(_repro_pack_markdown(repro_pack), encoding="utf-8")

    return {
        "json": str(json_path),
        "customer_reproduction_pack_json": str(repro_pack_path),
        "customer_reproduction_pack_md": str(repro_pack_md_path),
        "markdown": str(md_path),
        "repro_ps1": str(ps1_path),
        "regression_pytest": str(pytest_path),
        "finding_count": len(findings),
    }


# ── 5-Step Customer Reproduction Pack Builder ──────────────────────────

def _build_customer_repro_pack(findings: list[dict], base_url: str, project_id: str) -> dict:
    """Build customer-ready 5-step reproduction pack.

    Each finding gets: setup → before → target → after → cleanup
    with curl templates, expected results, and evidence references.
    """
    packages = []
    for f in findings[:50]:
        evidence = f.get("evidence", {}) if isinstance(f.get("evidence"), dict) else {}
        title = f.get("title") or f.get("bug_title") or ""
        severity = f.get("severity") or "P1"
        host = base_url.rstrip("/") + "/"

        # Detect multi-step business flows from evidence
        calls = evidence.get("calls", []) or []
        flow_steps = evidence.get("flow_steps") or evidence.get("execution_trace") or []
        execution = evidence.get("execution") or {}
        journey_steps = execution.get("steps") or evidence.get("journey_steps") or []

        # ── Multi-step business flow detection ──
        if (len(calls) >= 3 or flow_steps or journey_steps):
            steps = _build_multi_step_flow(calls, flow_steps, journey_steps, evidence, f, host)
        else:
            steps = _build_single_step(f, evidence, host)

        # Detects multi-module / multi-service scenario
        modules = _detect_modules(calls, evidence, host)
        is_cross_module = len(modules) > 1

        packages.append({
            "finding_id": f.get("hypothesis_id") or f.get("issue_id") or f"F_{len(packages):03d}",
            "title": title,
            "severity": severity,
            "is_multi_step": len(calls) >= 3 or bool(flow_steps or journey_steps),
            "is_cross_module": is_cross_module,
            "modules_involved": modules if is_cross_module else [],
            "reproduction_trace": steps,
            "estimated_duration": "10-15分钟" if is_cross_module else "5-10分钟" if len(steps) > 5 else "3-5分钟",
            "tools_needed": ["curl", "浏览器"] + (["多模块数据库访问权限", "各模块API Token"] if is_cross_module else []),
            "preparation_notes": _cross_module_prep_notes(modules) if is_cross_module else [],
        })

    return {
        "engine": "customer_reproduction_pack_v2",
        "project_id": project_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repro_pack_count": len(packages),
        "standard": "5-step (setup→before→target→after→cleanup)",
        "packages": packages,
    }


def _build_single_step(finding: dict, evidence: dict, host: str) -> list[dict]:
    """Build standard 5-step for single-operation findings."""
    method = finding.get("repro_method") or finding.get("method") or "GET"
    path = finding.get("repro_path") or finding.get("path") or ""
    expected = finding.get("expected_behavior") or finding.get("expected") or ""
    actual = finding.get("actual_behavior") or finding.get("actual") or ""
    title = finding.get("title") or finding.get("bug_title") or ""
    body = evidence.get("request_body") or finding.get("request_body")
    full_url = urljoin(host, path.lstrip("/"))

    steps = [
        {"sequence": 1, "phase": "setup",
         "purpose": "验证目标服务可达", "method": "GET", "path": path,
         "curl_template": f'curl -s -o /dev/null -w "%{{http_code}}" {full_url}',
         "expected": "HTTP 2xx", "accepted": True},
        {"sequence": 2, "phase": "before",
         "purpose": "记录操作前数据快照", "method": "GET", "path": path,
         "curl_template": f'curl -s -i "{full_url}{"?page=1&limit=5" if method.upper() == "GET" else ""}"' if not method.upper() == "GET" else f'curl -s -i "{full_url}?page=1&limit=5"',
         "expected": "HTTP 200 + 业务数据", "accepted": True},
        {"sequence": 3, "phase": "target", "purpose": f"执行: {title[:80]}",
         "method": method.upper(), "path": path,
         "curl_template": _target_curl(method, body, full_url),
         "expected": expected[:200], "actual": actual[:200] if actual else "需验证",
         "accepted": True},
        {"sequence": 4, "phase": "after",
         "purpose": "记录操作后数据状态", "method": "GET", "path": path,
         "curl_template": f'curl -s -i "{full_url}"',
         "expected": "数据状态应与预期一致", "accepted": True},
        {"sequence": 5, "phase": "cleanup",
         "purpose": "清理测试数据", "method": "MANUAL", "path": path,
         "curl_template": f"# 清理: DELETE {full_url}/{{created_id}}",
         "expected": "恢复至操作前状态", "accepted": False},
    ]
    return steps


def _build_multi_step_flow(calls: list, flow_steps: list, journey_steps: list,
                          evidence: dict, finding: dict, host: str) -> list[dict]:
    """Build multi-step business flow reproduction — universal across all industries.

    No hardcoded assumptions about business domain. Relies entirely on:
    - HTTP method+path from actual execution evidence
    - Response status/body for expected behavior inference
    - Variable extraction from step responses for context passing

    Works for any industry: ecommerce, healthcare, banking, logistics, education, etc.
    """
    steps = []
    seq = 1
    title = finding.get("title", "") or finding.get("bug_title", "")
    expected = finding.get("expected_behavior") or finding.get("expected") or ""

    # Step 1: SETUP — environment + auth readiness
    auth_header = evidence.get("auth_header") or evidence.get("token") or ""
    auth_note = ""
    if auth_header:
        auth_note = f"\n# 认证: Bearer {auth_header[:20]}..." if isinstance(auth_header, str) else ""
    steps.append({"sequence": seq, "phase": "setup",
                  "purpose": "验证环境可用性及认证准备",
                  "curl_template": f'curl -s -o /dev/null -w "%{{http_code}}" {host}{auth_note}',
                  "expected": "HTTP 2xx", "accepted": True})
    seq += 1

    # Determine flow source
    flow_items = calls if calls else journey_steps or flow_steps

    extracted_vars: dict[str, str] = {}
    for i, item in enumerate(flow_items[:15]):
        # Parse step info from whatever format (call dict, flow step dict, journey step)
        if isinstance(item, dict):
            if "call" in item:
                call_str = item["call"]
                parts = call_str.split(" ", 1)
                step_method = parts[0] if parts else "GET"
                step_path = parts[1] if len(parts) > 1 else "/"
                results = item.get("results", {})
            else:
                step_method = item.get("method") or item.get("action") or "GET"
                step_path = item.get("path") or item.get("endpoint") or "/"
                results = item
        elif isinstance(item, str):
            parts = item.split(" ", 1)
            step_method = parts[0] if parts else "GET"
            step_path = parts[1] if len(parts) > 1 else item
            results = {}
        else:
            continue

        # Substitute variables from previous steps
        path = str(step_path)
        for var, val in extracted_vars.items():
            path = path.replace(var, str(val)).replace(f"{{{var}}}", str(val))

        full_url = urljoin(host, path.lstrip("/"))
        method = step_method.upper()

        # Extract variables from THIS step's response for NEXT steps
        if isinstance(item, dict):
            resolved = _resolve_results(results)
            if isinstance(resolved, dict):
                resp_body = resolved.get("body", {})
                # Flat body extraction
                if isinstance(resp_body, dict):
                    _extract_vars_from_dict(resp_body, seq, extracted_vars)
                    # Nested wrappers: data, result, response
                    for wrapper in ("data", "result", "response"):
                        inner = resp_body.get(wrapper, {})
                        if isinstance(inner, dict):
                            _extract_vars_from_dict(inner, seq, extracted_vars)
                # Array: first item extraction
                if isinstance(resp_body, list) and len(resp_body) > 0 and isinstance(resp_body[0], dict):
                    _extract_vars_from_dict(resp_body[0], seq, extracted_vars)
                # Location header (HTTP 201 → /api/orders/42)
                headers = resolved.get("headers", {})
                if isinstance(headers, dict):
                    loc = headers.get("Location") or headers.get("location", "")
                    if loc:
                        loc_id = loc.rstrip("/").split("/")[-1] if "/" in loc else loc
                        extracted_vars[str(loc_id)] = str(loc_id)

        # Substitute variables into BOTH path and body
        body = item.get("body") or item.get("request_body") if isinstance(item, dict) else None
        if body and isinstance(body, dict) and extracted_vars:
            body_str = json.dumps(body)
            for var, val in extracted_vars.items():
                body_str = body_str.replace(str(var), str(val)).replace("{" + str(var) + "}", str(val))
            try:
                body = json.loads(body_str)
            except json.JSONDecodeError:
                pass  # Keep original if substitution breaks JSON

        curl = _target_curl(method, body, full_url)

        # Phase inferred from position in flow — no industry assumptions
        total = len(flow_items)
        if i == 0:
            phase = "step_observe"      # first step: observe current state
        elif i == total - 1:
            phase = "target"            # last step: the defect trigger
        else:
            phase = f"step_transition"  # middle steps: state transitions

        # Purpose from actual HTTP method semantics
        method_purposes = {
            "GET": "查询/获取数据",
            "POST": "创建/提交",
            "PUT": "更新/修改",
            "PATCH": "部分更新",
            "DELETE": "删除/移除",
        }
        method_purpose = method_purposes.get(method, f"{method}操作")

        purpose = f"业务流步骤{i+1}/{total}: {method_purpose} {path.split('/')[-1][:30]}"
        if i == total - 1:
            purpose = f"触发缺陷: {title[:60]}" if title else f"最终验证步骤"

        step = {
            "sequence": seq, "phase": phase,
            "purpose": purpose, "method": method, "path": path,
            "curl_template": curl,
            "expected": expected[:200] if i == total - 1 else "HTTP 2xx",
            "accepted": True,
        }
        if body and isinstance(body, dict):
            step["request_body"] = body
        if extracted_vars:
            step["context_variables"] = {k: v for k, v in list(extracted_vars.items())[-5:]}
        steps.append(step)
        seq += 1

    # FINAL state verification
    last_path = flow_items[-1].get("path", "/") if isinstance(flow_items[-1], dict) else "/"
    if isinstance(flow_items[0], dict):
        last_path = flow_items[0].get("path", last_path) if "call" in flow_items[0] else last_path
    steps.append({"sequence": seq, "phase": "after",
                  "purpose": "验证业务流程最终状态", "method": "GET",
                  "path": last_path,
                  "curl_template": f"curl -s -i \"{urljoin(host, str(last_path).lstrip('/'))}\"",
                  "expected": expected[:200], "accepted": True})
    seq += 1

    steps.append({"sequence": seq, "phase": "cleanup",
                  "purpose": "按业务流创建顺序反向清理测试数据", "method": "MANUAL",
                  "curl_template": "# 反向清理: 从最后一步创建的实体开始，逐级删除回退",
                  "expected": "恢复至业务流程执行前状态", "accepted": False})

    return steps


def _resolve_results(results: dict) -> dict:
    """Resolve multi-role results to a single result dict (prefer admin)."""
    if not isinstance(results, dict):
        return {}
    for role in ("admin", "viewer", "no_auth"):
        role_result = results.get(role, {})
        if isinstance(role_result, dict) and role_result.get("status"):
            return role_result
    return results


def _extract_vars_from_dict(d: dict, step_seq: int, extracted: dict) -> None:
    """Extract all non-null scalar values as context variables for cross-step passing."""
    for key, val in d.items():
        if val is None or not str(val).strip():
            continue
        if isinstance(val, (str, int, float, bool)):
            extracted[str(val)] = str(val)
            extracted[f"${{STEP_{step_seq}_{key.upper()}}}"] = str(val)


def _target_curl(method: str, body: Any, full_url: str) -> str:
    if body and isinstance(body, dict):
        body_json = json.dumps(body, ensure_ascii=False)
        return f'curl -s -i -X {method} "{full_url}" -H "Content-Type: application/json" -d \'{body_json}\''
    return f'curl -s -i -X {method} "{full_url}"'


def _repro_pack_markdown(pack: dict) -> str:
    lines = ["# QualiBug Customer Reproduction Pack", "",
             f"**项目**: `{pack['project_id']}`",
             f"**生成时间**: {pack['created_at']}",
             f"**标准**: {pack.get('standard', '5-step')}",
             f"**复现包数量**: {pack['repro_pack_count']}", ""]
    for pkg in pack.get("packages", []):
        lines.append(f"## [{pkg['severity']}] {pkg['title'][:100]}")
        lines.append(f"**预计耗时**: {pkg['estimated_duration']}  |  **工具**: {', '.join(pkg['tools_needed'])}")
        lines.append("")
        for step in pkg["reproduction_trace"]:
            icon = "✅" if step["accepted"] else "⚠️"
            lines.append(f"### {icon} Step {step['sequence']}: {step['phase'].upper()}")
            lines.append(f"**目的**: {step['purpose']}")
            lines.append(f"```bash")
            lines.append(step["curl_template"])
            lines.append(f"```")
            if step.get("expected"):
                lines.append(f"**预期**: {step['expected']}")
            if step.get("actual"):
                lines.append(f"**实际**: {step['actual']}")
            lines.append("")
        lines.append("---")
    return "\n".join(lines)


# ── Multi-module cross-service detection ──────────────────────────────

def _detect_modules(calls: list, evidence: dict, default_host: str) -> list[dict]:
    """Detect distinct API modules/services from evidence URLs."""
    hosts: dict[str, set[str]] = {}
    for c in calls:
        if isinstance(c, dict):
            url = c.get("url", "") or ""
            path = c.get("path", "") or c.get("call", "").split(" ", 1)[1] if isinstance(c.get("call"), str) and " " in c.get("call", "") else ""
            full = url or path
            if not full:
                continue
            # Extract host and module
            if "://" in full:
                host = full.split("/")[2]
            else:
                host = default_host.split("://")[-1].split("/")[0] if "://" in default_host else default_host
            module = _infer_module(full)
            hosts.setdefault(str(host), set()).add(module)

    if not hosts:
        return [{"module": "目标系统", "host": default_host, "db_hint": "API对应DB", "auth_hint": "API Token"}]

    modules = []
    for host, mod_set in hosts.items():
        for mod in sorted(mod_set):
            sn = mod.upper().replace("-", "_").replace(" ", "_")
            modules.append({
                "module": mod, "host": host,
                "db_hint": f"{mod.replace(' ', '_')}_db",
                "auth_hint": f"Bearer Token / 环境变量 QUALIBUG_SVC_{sn}_BEARER_TOKEN",
                "env_var_prefix": f"QUALIBUG_SVC_{sn}",
                "config_path": f"multi_service_config.json → services[?name={mod}].auth",
            })
    return modules


def _infer_module(url: str) -> str:
    """Extract module name from URL path, stripping protocol, host, and API prefix verbiage."""
    # Remove protocol and host
    path = url.split("://", 1)[-1] if "://" in url else url
    path = path.split("/", 1)[-1] if "/" in path else path
    # Skip API versioning noise
    parts = [p for p in path.split("?")[0].split("/") if p and p not in ("api", "v1", "v2", "v3", "v4", "rest", "public")]
    return parts[0].replace("-", " ").replace("_", " ") if parts else "目标系统"


def _cross_module_prep_notes(modules: list[dict]) -> list[str]:
    notes = []
    for m in modules[:5]:
        sn = m.get("env_var_prefix", "")
        env_hint = f"export {sn}_BEARER_TOKEN=<token>" if sn else "配置 API Token"
        notes.append(
            f"准备 [{m['module']}] 模块: 连接 {m['host']}, "
            f"DB={m['db_hint']}, 认证={env_hint}"
        )
    if len(modules) > 1:
        deps = " → ".join(m["module"] for m in modules[:5])
        notes.append(f"跨模块数据流: {deps} (确保每个模块有独立的 API Token)")
    if modules:
        notes.append(
            "配置方式: 1) multi_service_config.json 中每个 service 的 auth 字段 "
            "2) 环境变量 QUALIBUG_SVC_<NAME>_BEARER_TOKEN"
        )
    return notes
