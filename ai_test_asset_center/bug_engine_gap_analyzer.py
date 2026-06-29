"""Evidence-gap analyzer for long-running QualiBug bug-engine cycles.

The customer report deliberately contains only validated candidates.  This
module gives operators and the self-evolution loop a second artifact that
explains why the remaining runtime signals were not reportable yet.

It never upgrades verdicts.  It classifies blocked/needs-more-evidence signals
into actionable buckets so the next optimization can target the real bottleneck
instead of blindly generating more probes.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


_AUTH_WORDS = ("auth", "认证", "permission", "权限", "anonymous", "匿名", "401", "403", "role", "角色")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _gate(finding: dict[str, Any]) -> dict[str, Any]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    gate = evidence.get("finding_gate") if isinstance(evidence.get("finding_gate"), dict) else {}
    return gate


def _verdict(finding: dict[str, Any]) -> str:
    # Prefer the stage/UI verdict.  The gate verdict is still exposed separately
    # because falsified or inconclusive signals may have a NEEDS_MORE_EVIDENCE
    # gate status without being pending customer bugs.
    return str(finding.get("verdict") or "").lower()


def _gate_verdict(finding: dict[str, Any]) -> str:
    return str(_gate(finding).get("verdict") or "").lower()


def _calls(finding: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    calls = evidence.get("calls") if isinstance(evidence.get("calls"), list) else []
    if calls:
        return [c for c in calls if isinstance(c, dict)]
    business = evidence.get("business_finding") if isinstance(evidence.get("business_finding"), dict) else {}
    calls = business.get("calls") if isinstance(business.get("calls"), list) else []
    return [c for c in calls if isinstance(c, dict)]


def _method_path(call: dict[str, Any]) -> tuple[str, str]:
    raw = str(call.get("call") or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].upper(), parts[1]
    if raw.startswith("/"):
        return "GET", raw
    return "", raw


def _has_business_data(body: Any) -> bool:
    if not body:
        return False
    if isinstance(body, dict):
        data = body.get("data", body)
        if isinstance(data, list):
            return len(data) > 0
        if isinstance(data, dict):
            return any(v not in (None, "", [], {}) for v in data.values())
        return data not in (None, "", [], {})
    if isinstance(body, list):
        return len(body) > 0
    return bool(str(body).strip())


def _role_result(call: dict[str, Any], role: str) -> dict[str, Any]:
    results = call.get("results") if isinstance(call.get("results"), dict) else {}
    return results.get(role) if isinstance(results.get(role), dict) else {}


def _endpoint(finding: dict[str, Any]) -> str:
    calls = _calls(finding)
    if calls:
        method, path = _method_path(calls[0])
        return f"{method} {path}".strip()
    title = str(finding.get("title") or "")
    m = re.search(r"(/api/[\w\-/{}/?=&%.]+)", title)
    return f"GET {m.group(1)}" if m else "unknown"


def _classify_gap(finding: dict[str, Any]) -> dict[str, Any]:
    title_expected = f"{finding.get('title', '')} {finding.get('expected', '')}".lower()
    gate = _gate(finding)
    missing = gate.get("business_gate_missing") or []
    if not isinstance(missing, list):
        missing = [str(missing)]
    calls = _calls(finding)
    first = calls[0] if calls else {}
    method, path = _method_path(first)
    no_auth = _role_result(first, "no_auth")
    viewer = _role_result(first, "viewer")
    admin = _role_result(first, "admin")
    no_auth_status = int(no_auth.get("status") or 0)
    viewer_status = int(viewer.get("status") or 0)
    admin_status = int(admin.get("status") or 0)
    no_auth_data = _has_business_data(no_auth.get("body"))
    viewer_data = _has_business_data(viewer.get("body"))
    is_auth = any(w in title_expected for w in _AUTH_WORDS)

    gate_verdict = _gate_verdict(finding)
    if str(_verdict(finding)).lower() in {"schema_invalid"} or gate_verdict == "schema_invalid":
        bucket = "schema_redaction_gap"
        action = "Redact raw sensitive response values before registry validation, then re-run gate."
    elif is_auth and method == "GET" and no_auth_status in {401, 403} and viewer_status in {0, 401, 403}:
        bucket = "healthy_auth_boundary_not_a_bug"
        action = "Do not promote. Treat as falsified/healthy authorization boundary."
    elif is_auth and method == "GET" and no_auth_status == 200 and not no_auth_data:
        bucket = "empty_anonymous_response_not_reportable"
        action = "Do not promote until a populated fixture proves anonymous business-data exposure."
    elif is_auth and method == "GET" and no_auth_status == 200 and no_auth_data and "ENTITY_BINDING_MISSING" in missing:
        bucket = "entity_binding_gap"
        action = "Bind endpoint to a stable business entity/path before promotion."
    elif "ENTITY_BINDING_MISSING" in missing:
        bucket = "entity_binding_gap"
        action = "Add entity alias/type/id from route map, fixture, or response identifier."
    elif "AFTER_SNAPSHOT_MISSING" in missing and method == "GET":
        bucket = "read_only_snapshot_mismatch"
        action = "If this is read-only auth evidence, use auth-boundary matrix; otherwise do not require after snapshot for non-mutating probes."
    elif any("CLEANUP" in m for m in missing):
        bucket = "cleanup_evidence_gap"
        action = "Execute disposable-sandbox cleanup or block write probe from customer report."
    elif missing:
        bucket = "business_evidence_gap"
        action = "Collect missing business evidence: " + ", ".join(str(m) for m in missing[:5])
    else:
        bucket = "non_validated_signal"
        action = "Keep out of customer report until a deterministic oracle promotes it."

    return {
        "id": _text(finding.get("id") or finding.get("hypothesis_id"), 120),
        "title": _text(finding.get("title"), 240),
        "endpoint": _endpoint(finding),
        "verdict": _verdict(finding),
        "gate_verdict": gate_verdict,
        "severity": _text(finding.get("severity"), 20),
        "bucket": bucket,
        "missing_requirements": [str(m) for m in missing],
        "statuses": {"admin": admin_status, "viewer": viewer_status, "no_auth": no_auth_status},
        "data_observed": {"viewer": viewer_data, "no_auth": no_auth_data},
        "recommended_action": action,
    }


def analyze_evidence_gaps(result: dict[str, Any]) -> dict[str, Any]:
    discovery = result.get("discovery_result") if isinstance(result.get("discovery_result"), dict) else result
    findings = discovery.get("findings") if isinstance(discovery.get("findings"), list) else []
    family_coverage = discovery.get("bug_family_coverage") if isinstance(discovery.get("bug_family_coverage"), dict) else {}
    browser_health = discovery.get("browser_ui_health") if isinstance(discovery.get("browser_ui_health"), dict) else {}
    metrics = discovery.get("metrics") if isinstance(discovery.get("metrics"), dict) else {}
    issues = discovery.get("issues") if isinstance(discovery.get("issues"), list) else []
    gaps_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    raw_non_validated = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        verdict = _verdict(finding)
        if verdict in {"validated_candidate", "validated"}:
            continue
        raw_non_validated += 1
        gap = _classify_gap(finding)
        key = (str(gap.get("bucket")), str(gap.get("endpoint")), str(gap.get("verdict")))
        if key in gaps_by_key:
            gaps_by_key[key]["occurrence_count"] = int(gaps_by_key[key].get("occurrence_count", 1) or 1) + 1
            merged_missing = list(gaps_by_key[key].get("missing_requirements") or [])
            for req in gap.get("missing_requirements") or []:
                if req not in merged_missing:
                    merged_missing.append(req)
            gaps_by_key[key]["missing_requirements"] = merged_missing
        else:
            gap["occurrence_count"] = 1
            gaps_by_key[key] = gap
    gaps = list(gaps_by_key.values())
    buckets = Counter(g["bucket"] for g in gaps)
    severity_by_bucket: dict[str, Counter] = defaultdict(Counter)
    for gap in gaps:
        severity_by_bucket[gap["bucket"]][str(gap.get("severity") or "P3")] += 1
    ui_design_oracle_issues = [i for i in issues if isinstance(i, dict) and str(i.get("source") or "") == "ui_design_oracle"]
    return {
        "report_version": "phase92e-evidence-gap-v1",
        "generated_at": _now(),
        "summary": {
            "raw_non_validated_signals": raw_non_validated,
            "unique_non_validated_signals": len(gaps),
            "total_non_validated": len(gaps),
            "buckets": dict(sorted(buckets.items())),
            "severity_by_bucket": {k: dict(v) for k, v in sorted(severity_by_bucket.items())},
            "engine_needs_more_evidence": discovery.get("needs_more_evidence"),
            "engine_inconclusive_rate": discovery.get("inconclusive_rate"),
            "covered_bug_family_count": family_coverage.get("covered_family_count", 0),
            "validated_bug_family_count": family_coverage.get("validated_family_count", 0),
            "missing_bug_families": list(family_coverage.get("missing_validated_families") or []),
            "browser_ui_health": {
                "enabled": bool(browser_health.get("enabled")),
                "reason_code": str(browser_health.get("reason_code") or ""),
                "severity": str(browser_health.get("severity") or ""),
            },
            "browser_ui_blocked_families": list((family_coverage.get("missing_family_reasons") or {}).keys()),
            "ui_design_oracle": {
                "issue_count": int(metrics.get("ui_design_oracle_issue_count", len(ui_design_oracle_issues)) or 0),
                "missing_component_count": int(metrics.get("ui_design_oracle_missing_component_count", 0) or 0),
                "missing_feedback_count": int(metrics.get("ui_design_oracle_missing_feedback_count", 0) or 0),
            },
        },
        "gaps": gaps,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# QualiBug Evidence Gap Report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append("")
    s = report.get("summary", {})
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Unique non-validated signals: **{s.get('unique_non_validated_signals', s.get('total_non_validated', 0))}**")
    lines.append(f"- Raw non-validated signal occurrences: {s.get('raw_non_validated_signals', s.get('total_non_validated', 0))}")
    lines.append(f"- Engine needs_more_evidence: {s.get('engine_needs_more_evidence')}")
    lines.append(f"- Engine inconclusive_rate: {s.get('engine_inconclusive_rate')}")
    lines.append("")
    lines.append("## Buckets")
    lines.append("")
    for bucket, count in (s.get("buckets") or {}).items():
        lines.append(f"- **{bucket}**: {count}")
    lines.append("")
    lines.append("## Gap Details")
    lines.append("")
    for idx, gap in enumerate(report.get("gaps", []), start=1):
        lines.append(f"### {idx}. {gap.get('endpoint', 'unknown')}")
        lines.append("")
        lines.append(f"- Bucket: `{gap.get('bucket')}`")
        lines.append(f"- Verdict: `{gap.get('verdict')}`; gate=`{gap.get('gate_verdict')}`")
        lines.append(f"- Severity: `{gap.get('severity')}`")
        lines.append(f"- Occurrences: {gap.get('occurrence_count', 1)}")
        lines.append(f"- Missing: {', '.join(gap.get('missing_requirements') or []) or 'none'}")
        statuses = gap.get("statuses") or {}
        lines.append(f"- Statuses: admin={statuses.get('admin')} viewer={statuses.get('viewer')} no_auth={statuses.get('no_auth')}")
        data = gap.get("data_observed") or {}
        lines.append(f"- Business data observed: viewer={data.get('viewer')} no_auth={data.get('no_auth')}")
        lines.append(f"- Recommended action: {gap.get('recommended_action')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_evidence_gap_report(result: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = analyze_evidence_gaps(result)
    json_path = output / "evidence_gap_report.json"
    md_path = output / "evidence_gap_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "gap_count": int(report.get("summary", {}).get("total_non_validated", 0) or 0),
        "buckets": report.get("summary", {}).get("buckets", {}),
    }
