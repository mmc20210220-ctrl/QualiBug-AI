from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _load_json,
    _safe_project_id,
    _write_json,
    load_real_project_config,
)
from .business_flow_execution import load_business_flow_execution_result, run_business_flow_execution
from .cognitive_memory_graph import CognitiveMemoryGraph

PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}
SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
    "jwt",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "client_secret",
}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
REPLAY_MODES = {"evidence_only", "safe_replay", "full_replay"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sandbox_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    return {
        "workspace_dir": root / "platform_workspace" / project / "replay_evidence_sandbox",
        "defect_workspace_dir": root / "platform_workspace" / project / "defect_discovery",
        "output_dir": root / "platform_outputs" / project / "replay_evidence_sandbox",
    }


def _normalize_replay_mode(raw: Any) -> str:
    mode = str(raw or "evidence_only").strip().lower().replace("-", "_")
    return mode if mode in REPLAY_MODES else "evidence_only"


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _redact_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        count = 0
        for k, v in value.items():
            key_l = str(k).lower()
            if key_l in SENSITIVE_KEYS or any(marker in key_l for marker in ("token", "password", "secret", "cookie", "authorization")):
                redacted[str(k)] = "<REDACTED>"
                count += 1
            else:
                rv, rc = _redact_value(v)
                redacted[str(k)] = rv
                count += rc
        return redacted, count
    if isinstance(value, list):
        rows = []
        count = 0
        for item in value[:200]:
            rv, rc = _redact_value(item)
            rows.append(rv)
            count += rc
        return rows, count
    if isinstance(value, str):
        text = value
        before = text
        text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{12,}", r"\1<REDACTED>", text)
        text = re.sub(r"(?i)(authorization\s*[:=]\s*)([^\s,;\n]{6,})", r"\1<REDACTED>", text)
        text = re.sub(r"(?i)(access[_-]?token|refresh[_-]?token|api[_-]?key|secret|password)\s*[:=]\s*['\"]?[^'\"\s,;}]{4,}", r"\1=<REDACTED>", text)
        for marker in PRIVATE_MARKERS:
            text = re.sub(re.escape(marker), "<PRIVATE_MARKER_REDACTED>", text, flags=re.I)
        return text[:6000], 1 if text != before else 0
    return value, 0


def _redact(value: Any) -> tuple[Any, int]:
    return _redact_value(value)


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = _safe_json(data).lower()
    leaks = sorted([m for m in PRIVATE_MARKERS if m.lower() in text])
    return {"passed": not leaks, "leak_terms": leaks}


def load_replay_evidence_sandbox(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    candidates = [
        root / "platform_workspace" / project / "replay_evidence_sandbox" / "replay_evidence_sandbox.json",
        root / "platform_outputs" / project / "replay_evidence_sandbox" / "replay_evidence_sandbox.json",
    ]
    for path in candidates:
        if path.exists():
            data = _load_json(path, {})
            return data if isinstance(data, dict) else None
    return None


def _step_ref(step: dict[str, Any]) -> str:
    return f"{str(step.get('method') or 'GET').upper()} {step.get('path') or '/'}"


def _can_replay_step(step: dict[str, Any], mode: str, cfg: dict[str, Any], options: dict[str, Any]) -> tuple[bool, str]:
    if mode == "evidence_only":
        return False, "evidence_only_never_replays_live_requests"
    method = str(step.get("method") or "GET").upper()
    if method == "GET":
        return True, "safe_replay_get_allowed"
    if mode == "safe_replay":
        return False, "safe_replay_blocks_write_step"
    allow_write = bool(cfg.get("allow_destructive_tests")) and bool(options.get("allow_write_replay", False))
    if mode == "full_replay" and allow_write:
        return True, "full_replay_write_allowed_by_project_policy"
    return False, "write_replay_requires_full_replay_and_allow_write_replay"


def _snapshot_resource_for_step(step: dict[str, Any], execution: dict[str, Any]) -> str:
    text = f"{step.get('stage') or ''} {step.get('path') or ''} {execution.get('risk_type') or ''}".lower()
    if "inventory" in text or "stock" in text or "sku" in text:
        return "inventory"
    if "payment" in text or "pay" in text or "ledger" in text or "balance" in text:
        return "money_ledger"
    if "refund" in text:
        return "refund"
    if "approval" in text or "approve" in text:
        return "approval"
    if "order" in text or "checkout" in text:
        return "order"
    if "coupon" in text or "voucher" in text:
        return "coupon"
    return "business_object"


def _build_snapshot_blueprints(execution: dict[str, Any]) -> list[dict[str, Any]]:
    blueprints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    steps = execution.get("steps") or []
    for step in steps:
        resource = _snapshot_resource_for_step(step, execution)
        for timing in ("before", "after"):
            key = (resource, timing)
            if key in seen:
                continue
            seen.add(key)
            source_step = _step_ref(step)
            blueprints.append({
                "snapshot_id": f"SNAP_{execution.get('execution_id')}_{len(blueprints)+1:02d}",
                "timing": timing,
                "resource_type": resource,
                "source_step": source_step,
                "capture_method": "GET/state_query_or_db_readonly_view",
                "fields_to_capture": _snapshot_fields(resource),
                "safety": "read_only_snapshot_blueprint",
            })
    return blueprints[:12]


def _snapshot_fields(resource: str) -> list[str]:
    return {
        "order": ["order_id", "owner_id", "status", "amount", "paid_amount", "coupon_amount", "updated_at"],
        "money_ledger": ["order_id", "payment_id", "transaction_id", "amount", "balance", "ledger_direction", "idempotency_key"],
        "refund": ["refund_id", "order_id", "payment_id", "refund_amount", "refund_status", "idempotency_key"],
        "inventory": ["sku", "available", "locked", "sold", "version", "updated_at"],
        "approval": ["application_id", "applicant_id", "approver_id", "status", "amount", "approval_node"],
        "coupon": ["coupon_id", "owner_id", "used_count", "discount_amount", "order_id"],
    }.get(resource, ["id", "owner_id", "status", "amount", "updated_at"])


def _build_replay_commands(execution: dict[str, Any], mode: str, cfg: dict[str, Any], options: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    commands: list[dict[str, Any]] = []
    redacted_count = 0
    for step in execution.get("steps") or []:
        can_replay, reason = _can_replay_step(step, mode, cfg, options)
        body, c1 = _redact(step.get("body_blueprint"))
        excerpt, c2 = _redact(step.get("response_excerpt") or "")
        redacted_count += c1 + c2
        commands.append({
            "command_id": f"REPLAY_{execution.get('execution_id')}_{len(commands)+1:02d}",
            "step": step.get("step"),
            "stage": step.get("stage"),
            "actor": step.get("actor"),
            "method": str(step.get("method") or "GET").upper(),
            "path": step.get("path"),
            "request_body": body,
            "original_status_code": step.get("status_code"),
            "original_response_excerpt": excerpt,
            "can_replay_live": can_replay,
            "safety_gate_reason": reason,
            "curl_blueprint": _curl_blueprint(step, body, can_replay),
        })
    return commands, redacted_count


def _curl_blueprint(step: dict[str, Any], body: Any, can_replay: bool) -> str:
    method = str(step.get("method") or "GET").upper()
    path = str(step.get("path") or "/")
    prefix = "# live replay allowed by current mode\n" if can_replay else "# planned only; review safety gate before live replay\n"
    cmd = f"curl -X {method} '${{BASE_URL}}{path}' -H 'Authorization: Bearer <TOKEN>' -H 'Accept: application/json'"
    if body is not None:
        cmd += " -H 'Content-Type: application/json' --data '<REQUEST_BODY_JSON>'"
    return prefix + cmd


def _assertion_score(assertion: dict[str, Any]) -> float:
    status = str(assertion.get("status") or "")
    sev = str(assertion.get("severity") or "P2")
    base = {"failed": 0.9, "needs_replay": 0.66, "needs_evidence": 0.58, "passed": 0.22}.get(status, 0.3)
    bonus = {"P0": 0.08, "P1": 0.04, "P2": 0.0, "P3": -0.04}.get(sev, 0.0)
    return round(max(0.05, min(1.0, base + bonus)), 3)


def _build_packet(issue: dict[str, Any], assertion: dict[str, Any] | None, execution: dict[str, Any], replay_commands: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    relevant_steps = [cmd for cmd in replay_commands if str(cmd.get("step")) in {str(x) for x in (assertion or {}).get("evidence_refs", [])}] if assertion else replay_commands[:5]
    if not relevant_steps:
        relevant_steps = replay_commands[:5]
    request_response, redacted_count = _redact(relevant_steps)
    reproduction_steps = [
        "确认测试账号、测试租户和沙箱数据已准备完成。",
        "按 replay_commands 顺序执行请求；默认先使用 evidence_only / safe_replay。",
        "在关键写步骤前后采集 snapshot_blueprints 中定义的只读状态快照。",
        "比对 assertion.expected 与 assertion.actual；若状态/金额/库存/权限边界不一致，提交缺陷。",
    ]
    status = str((assertion or {}).get("status") or issue.get("status") or "needs_human_review")
    completeness = 0.94 if status == "failed" else (0.78 if status == "needs_replay" else 0.68)
    packet_key = str(issue.get("issue_id") or ((assertion or {}).get("assertion_id")) or "issue")
    digest = hashlib.sha1(packet_key.encode("utf-8", errors="ignore")).hexdigest()[:10]
    packet = {
        "packet_id": f"EVP_{execution.get('execution_id')}_{digest}",
        "execution_id": execution.get("execution_id"),
        "flow_id": execution.get("flow_id"),
        "probe_id": execution.get("probe_id"),
        "issue_id": issue.get("issue_id"),
        "assertion_id": (assertion or {}).get("assertion_id"),
        "title": issue.get("title") or f"链路证据包：{execution.get('title')}",
        "risk_type": issue.get("risk_type") or execution.get("risk_type"),
        "severity": issue.get("severity") or (assertion or {}).get("severity") or "P2",
        "confidence": issue.get("confidence") or (_assertion_score(assertion or {}) if assertion else 0.5),
        "evidence_completeness": round(completeness, 2),
        "reproduction_steps": reproduction_steps,
        "request_response_evidence": request_response,
        "snapshot_blueprints": snapshots,
        "assertion": assertion or {},
        "expected": issue.get("expected") or (assertion or {}).get("expected"),
        "actual": issue.get("actual") or (assertion or {}).get("actual"),
        "bug_signal": issue.get("bug_signal") or (assertion or {}).get("bug_signal"),
        "sanitized": True,
    }
    return packet, redacted_count


def _issue_key(issue: dict[str, Any]) -> tuple[str, str, str]:
    return (str(issue.get("probe_id") or ""), str(issue.get("assertion_id") or ""), str(issue.get("flow_id") or ""))


def _find_assertion_for_issue(issue: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any] | None:
    assertion_id = str(issue.get("assertion_id") or "")
    for assertion in execution.get("assertions") or []:
        if assertion_id and str(assertion.get("assertion_id") or "") == assertion_id:
            return assertion
    return None


def _build_enhanced_issue(issue: dict[str, Any], packet: dict[str, Any], session_id: str) -> dict[str, Any]:
    return {
        **issue,
        "evidence_packet_id": packet.get("packet_id"),
        "evidence_completeness": packet.get("evidence_completeness"),
        "replay_session_id": session_id,
        "reproduction_steps": packet.get("reproduction_steps"),
        "evidence_artifacts": {
            "request_response_count": len(packet.get("request_response_evidence") or []),
            "snapshot_blueprint_count": len(packet.get("snapshot_blueprints") or []),
            "sanitized": True,
        },
    }


def build_replay_evidence_sandbox(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = dict(options or {})
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    mode = _normalize_replay_mode(options.get("replay_mode") or cfg.get("replay_evidence_mode") or "evidence_only")
    flow_execution = load_business_flow_execution_result(project, root)
    if not flow_execution or options.get("refresh_flow_execution"):
        flow_execution = run_business_flow_execution(project, root, options={
            "execution_mode": options.get("execution_mode") or "dry_run",
            "max_probe_count": int(options.get("max_probe_count") or 20),
            "allow_destructive_execution": False,
        })
    max_packets = int(options.get("max_packet_count") or 80)
    candidate_issues = [dict(x) for x in (flow_execution.get("candidate_issues") or [])]
    issue_by_key = {_issue_key(issue): issue for issue in candidate_issues}
    sessions: list[dict[str, Any]] = []
    packets: list[dict[str, Any]] = []
    enhanced_issues: list[dict[str, Any]] = []
    redacted_count = 0

    for execution in flow_execution.get("executions") or []:
        replay_commands, rc = _build_replay_commands(execution, mode, cfg, options)
        redacted_count += rc
        snapshots = _build_snapshot_blueprints(execution)
        session_id = f"RPL_{len(sessions)+1:04d}"
        session_packet_ids: list[str] = []
        exec_issues = [issue for issue in candidate_issues if str(issue.get("probe_id") or "") == str(execution.get("probe_id") or "")]
        if not exec_issues:
            # Build packets for failed/replay-needed assertions even when the candidate issue list was not populated.
            for assertion in execution.get("assertions") or []:
                if str(assertion.get("status") or "") in {"failed", "needs_replay", "needs_evidence"}:
                    exec_issues.append({
                        "issue_id": f"FLOW_ASSERT_{execution.get('probe_id')}_{assertion.get('assertion_id')}",
                        "title": f"链路一致性断言证据：{execution.get('title')} · {assertion.get('assertion_type')}",
                        "risk_type": execution.get("risk_type"),
                        "severity": assertion.get("severity"),
                        "confidence": _assertion_score(assertion),
                        "assertion_id": assertion.get("assertion_id"),
                        "probe_id": execution.get("probe_id"),
                        "flow_id": execution.get("flow_id"),
                        "expected": assertion.get("expected"),
                        "actual": assertion.get("actual"),
                        "bug_signal": assertion.get("bug_signal"),
                    })
        for issue in exec_issues:
            if len(packets) >= max_packets:
                break
            assertion = _find_assertion_for_issue(issue, execution)
            packet, pc = _build_packet(issue, assertion, execution, replay_commands, snapshots)
            redacted_count += pc
            packets.append(packet)
            session_packet_ids.append(str(packet.get("packet_id")))
            enhanced_issues.append(_build_enhanced_issue(issue, packet, session_id))
        sessions.append({
            "replay_session_id": session_id,
            "execution_id": execution.get("execution_id"),
            "flow_id": execution.get("flow_id"),
            "probe_id": execution.get("probe_id"),
            "risk_type": execution.get("risk_type"),
            "replay_mode": mode,
            "safety_gate": {
                "default_mode": "evidence_only",
                "safe_replay_executes_get_only": True,
                "write_replay_requires_full_replay_and_allow_write_replay": True,
                "allow_write_replay": bool(options.get("allow_write_replay", False)),
                "project_allow_destructive_tests": bool(cfg.get("allow_destructive_tests")),
            },
            "snapshot_blueprints": snapshots,
            "replay_commands": replay_commands,
            "evidence_packet_ids": session_packet_ids,
        })
        if len(packets) >= max_packets:
            break

    status_dist: dict[str, int] = {}
    severity_dist: dict[str, int] = {}
    for p in packets:
        status = str((p.get("assertion") or {}).get("status") or "issue")
        sev = str(p.get("severity") or "P2")
        status_dist[status] = status_dist.get(status, 0) + 1
        severity_dist[sev] = severity_dist.get(sev, 0) + 1

    summary = {
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "replay_mode": mode,
        "source_execution_mode": (flow_execution.get("summary") or {}).get("execution_mode"),
        "execution_count": len(flow_execution.get("executions") or []),
        "replay_session_count": len(sessions),
        "replay_command_count": sum(len(s.get("replay_commands") or []) for s in sessions),
        "live_replay_allowed_command_count": sum(1 for s in sessions for c in s.get("replay_commands") or [] if c.get("can_replay_live")),
        "snapshot_blueprint_count": sum(len(s.get("snapshot_blueprints") or []) for s in sessions),
        "evidence_packet_count": len(packets),
        "enhanced_candidate_issue_count": len(enhanced_issues),
        "failed_assertion_packet_count": sum(1 for p in packets if (p.get("assertion") or {}).get("status") == "failed"),
        "needs_replay_packet_count": sum(1 for p in packets if (p.get("assertion") or {}).get("status") == "needs_replay"),
        "average_evidence_completeness": round(sum(float(p.get("evidence_completeness") or 0) for p in packets) / max(1, len(packets)), 3),
        "redacted_field_count": redacted_count,
        "status_distribution": status_dist,
        "severity_distribution": severity_dist,
        "generated_at_utc": _now(),
    }
    result = {
        "phase": "phase40_replay_evidence_sandbox",
        "summary": summary,
        "source_business_flow_execution_summary": flow_execution.get("summary", {}),
        "replay_sessions": sessions,
        "evidence_packets": packets,
        "candidate_issues_enhanced": enhanced_issues,
        "governance": {
            "real_project_mode": True,
            "default_replay_mode": "evidence_only",
            "safe_replay_executes_get_only": True,
            "full_replay_write_steps_require_project_allow_destructive_and_user_flag": True,
            "evidence_is_sanitized": True,
            "uses_no_benchmark_answer_files": True,
            "inputs": ["business_flow_execution_result", "real_project_config", "sanitized_request_response_excerpts", "state_snapshot_blueprints"],
        },
    }
    # Phase91: replay packets enrich only the typed evidence graph.  They remain
    # evidence-captured candidates and never become confirmed findings here.
    try:
        environment_id = str(options.get("environment_id") or options.get("environment") or "test")
        graph = CognitiveMemoryGraph(project, environment_id, root)
        graph_update = graph.record_replay_packets(packets, run_id=str(summary.get("generated_at_utc") or ""))
        result["cognitive_graph"] = {"mode": "shadow", **graph_update, "stats": graph.stats(), "packets_remain_evidence_only": True}
    except Exception as exc:
        result["cognitive_graph"] = {"mode": "off", "error": f"{type(exc).__name__}: {exc}", "packets_remain_evidence_only": True}
    leak = _private_leak_check(result)
    result["private_leak_check"] = leak
    paths = _sandbox_paths(project, root)
    for key in ("workspace_dir", "defect_workspace_dir", "output_dir"):
        paths[key].mkdir(parents=True, exist_ok=True)
    _write_json(paths["workspace_dir"] / "replay_evidence_sandbox.json", result)
    _write_json(paths["defect_workspace_dir"] / "replay_evidence_sandbox.json", result)
    _write_json(paths["output_dir"] / "replay_evidence_sandbox.json", result)
    _write_json(paths["output_dir"] / "replay_evidence_sandbox_summary.json", {"summary": summary, "private_leak_check": leak})
    _write_text(paths["output_dir"] / "replay_evidence_sandbox_report.html", render_replay_evidence_sandbox_report(result))
    _write_text(paths["output_dir"] / "evidence_packets.md", render_evidence_packets_markdown(result))
    return result


def render_evidence_packets_markdown(result: dict[str, Any]) -> str:
    packets = result.get("evidence_packets") or []
    if not packets:
        return "# Phase40 自动证据包\n\n暂无证据包。\n"
    parts = ["# Phase40 自动证据包\n", f"项目：{(result.get('summary') or {}).get('project_id')}\n"]
    for p in packets[:80]:
        parts.append(f"\n## {p.get('severity')} {p.get('title')}\n")
        parts.append(f"- Packet：{p.get('packet_id')}\n- Flow：{p.get('flow_id')}\n- 风险：{p.get('risk_type')}\n- 置信度：{p.get('confidence')}\n- 证据完整度：{p.get('evidence_completeness')}\n- 期望：{p.get('expected')}\n- 实际：{p.get('actual')}\n- Bug Signal：{p.get('bug_signal')}\n")
        parts.append("\n复现步骤：\n")
        for idx, step in enumerate(p.get("reproduction_steps") or [], 1):
            parts.append(f"{idx}. {step}\n")
        parts.append("\n关键请求：\n")
        for cmd in (p.get("request_response_evidence") or [])[:4]:
            parts.append(f"- {cmd.get('method')} {cmd.get('path')} · gate={cmd.get('safety_gate_reason')} · status={cmd.get('original_status_code')}\n")
    return "".join(parts)


def render_replay_evidence_sandbox_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items() if k not in {"project_id", "project_name", "status_distribution", "severity_distribution"})
    packet_rows = []
    for p in (result.get("evidence_packets") or [])[:120]:
        assertion = p.get("assertion") or {}
        packet_rows.append(f"<tr><td>{_html_escape(p.get('packet_id'))}</td><td>{_html_escape(p.get('severity'))}</td><td>{_html_escape(p.get('risk_type'))}</td><td>{_html_escape(assertion.get('status') or 'issue')}</td><td>{_html_escape(p.get('evidence_completeness'))}</td><td>{_html_escape(p.get('title'))}</td><td>{_html_escape(p.get('actual'))}</td></tr>")
    session_rows = []
    for s in (result.get("replay_sessions") or [])[:80]:
        session_rows.append(f"<tr><td>{_html_escape(s.get('replay_session_id'))}</td><td>{_html_escape(s.get('flow_id'))}</td><td>{_html_escape(s.get('risk_type'))}</td><td>{_html_escape(len(s.get('replay_commands') or []))}</td><td>{_html_escape(len(s.get('snapshot_blueprints') or []))}</td><td>{_html_escape(len(s.get('evidence_packet_ids') or []))}</td></tr>")
    leak = result.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Replay Evidence Sandbox</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#fef3c7;color:#92400e}}</style></head><body>
<section class='hero'><span class='badge'>Phase40</span><h1>真实环境安全回放沙箱 + 自动证据包增强</h1><p>把链路执行断言沉淀为可回放命令、只读状态快照蓝图、复现步骤和脱敏请求/响应证据包。默认 evidence_only，不直接执行破坏性请求。</p><p>私有数据泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>证据概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>回放会话</h2><table><thead><tr><th>Session</th><th>Flow</th><th>风险</th><th>回放命令</th><th>快照蓝图</th><th>证据包</th></tr></thead><tbody>{''.join(session_rows) or '<tr><td colspan="6">暂无回放会话</td></tr>'}</tbody></table></section>
<section class='panel'><h2>自动证据包</h2><table><thead><tr><th>Packet</th><th>等级</th><th>风险</th><th>断言状态</th><th>完整度</th><th>标题</th><th>实际</th></tr></thead><tbody>{''.join(packet_rows) or '<tr><td colspan="7">暂无证据包</td></tr>'}</tbody></table></section>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    mode = os.environ.get("REPLAY_EVIDENCE_MODE") or (argv[1] if len(argv) > 1 else "evidence_only")
    result = build_replay_evidence_sandbox(project, options={"replay_mode": mode})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
