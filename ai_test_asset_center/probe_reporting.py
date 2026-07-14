"""Markdown rendering functions for probe execution reports.
Extracted from grounded_probe_executor.py.
All functions are pure: data in, markdown string out.
"""
from __future__ import annotations

import json
from typing import Any

from .probe_http import _redact, _safe_payload_summary


def _powershell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _shell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _render_runtime_evidence_carry_forward_markdown(carry: dict[str, Any]) -> str:
    """Render carry-forward evidence as markdown."""
    lines = [
        "# Runtime Evidence Carry-Forward",
        "",
        f"- engine: `{carry.get('engine')}`",
        f"- project: `{carry.get('project_id')}`",
        f"- packages carried forward: {carry.get('carried_forward_count', 0)}",
        f"- candidates skipped: {carry.get('skipped_candidate_count', 0)}",
        "",
    ]
    if carry.get("packages"):
        lines.append("| Candidate ID | Finding ID | Carry Status |")
        lines.append("|---|---|---|")
        for pkg in carry.get("packages", [])[:50]:
            if not isinstance(pkg, dict):
                continue
            lines.append(
                f"| {pkg.get('candidate_id', '-')} "
                f"| {pkg.get('finding_id', '-')} "
                f"| {pkg.get('carry_status', '-')} |"
            )
        lines.append("")
    return "\n".join(lines)


def _render_runtime_evidence_progress_delta_markdown(delta: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Progress Delta",
        "",
        f"- engine: `{delta.get('engine')}`",
        f"- project: `{delta.get('project_id')}`",
        f"- previous run: `{delta.get('previous_run_id')}`",
        f"- current run: `{delta.get('current_run_id')}`",
        "",
        "| Metric | Previous | Current | Delta |",
        "|---|---|---|---|",
    ]
    for item in delta.get("metrics", [])[:30]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| {item.get('label', '-')} "
            f"| {item.get('previous', '-')} "
            f"| {item.get('current', '-')} "
            f"| {item.get('delta', '-')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_runtime_evidence_promotion_gate_markdown(gate: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Promotion Gate",
        "",
        f"- engine: `{gate.get('engine')}`",
        f"- project: `{gate.get('project_id')}`",
        f"- promotion allowed: `{gate.get('promotion_allowed')}`",
        f"- reason: {gate.get('reason', '-')}",
        "",
    ]
    checks = gate.get("checks", {})
    if checks:
        lines.append("| Check | Status |")
        lines.append("|---|---|")
        for name, passed in checks.items():
            lines.append(f"| {name} | {'pass' if passed else 'fail'} |")
        lines.append("")
    return "\n".join(lines)


def _render_runtime_evidence_customer_delivery_manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Runtime Customer Delivery Manifest",
        "",
        f"- engine: `{manifest.get('engine')}`",
        f"- project: `{manifest.get('project_id')}`",
        f"- status: `{manifest.get('status')}`",
        f"- findings: {manifest.get('finding_count', 0)}",
        f"- customer-ready: {manifest.get('customer_ready_count', 0)}",
        "",
    ]
    findings = manifest.get("findings", [])
    if findings:
        lines.append("| Finding | Candidate | Readiness |")
        lines.append("|---|---|---|")
        for f in findings[:50]:
            if not isinstance(f, dict):
                continue
            lines.append(
                f"| {f.get('finding_id', '-')} "
                f"| {f.get('candidate_id', '-')} "
                f"| {f.get('readiness_level', '-')} |"
            )
        lines.append("")
    return "\n".join(lines)


def _render_runtime_evidence_delivery_manifest_verification_markdown(verification: dict[str, Any]) -> str:
    lines = [
        "# Runtime Delivery Manifest Verification",
        "",
        f"- engine: `{verification.get('engine')}`",
        f"- project: `{verification.get('project_id')}`",
        f"- verified: `{verification.get('verified')}`",
        f"- artifact count: {verification.get('artifact_count', 0)}",
        "",
    ]
    results = verification.get("results", [])
    if results:
        lines.append("| Artifact | Status | SHA256 |")
        lines.append("|---|---|---|")
        for r in results[:50]:
            if not isinstance(r, dict):
                continue
            lines.append(
                f"| {r.get('key', '-')} "
                f"| {r.get('status', '-')} "
                f"| {r.get('sha256', '-')[:16]} |"
            )
        lines.append("")
    return "\n".join(lines)


def _render_runtime_evidence_probe_ledger_markdown(ledger: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Probe Ledger",
        "",
        f"- engine: `{ledger.get('engine')}`",
        f"- project: `{ledger.get('project_id')}`",
        f"- entries: {ledger.get('entry_count', 0)}",
        "",
        "| Candidate | Readiness | Verdict | Gaps |",
        "|---|---|---|---|",
    ]
    for entry in ledger.get("entries", [])[:50]:
        if not isinstance(entry, dict):
            continue
        gaps = ", ".join(entry.get("gap_types", [])[:3])
        lines.append(
            f"| {entry.get('candidate_id', '-')} "
            f"| {entry.get('readiness_level', '-')} "
            f"| {entry.get('verdict', '-')} "
            f"| {gaps or '-'} |"
        )
    if len(ledger.get("entries", [])) > 50:
        lines.append(f"\n_Only the first 50 entries are shown; see JSON for all {len(ledger.get('entries', []))} probes._")
    lines.append("")
    return "\n".join(lines)


def _render_runtime_evidence_remediation_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Remediation Plan",
        "",
        f"- engine: `{plan.get('engine')}`",
        f"- project: `{plan.get('project_id')}`",
        f"- status: `{plan.get('status')}`",
        f"- remediation groups: {plan.get('remediation_group_count')}",
        f"- P0 groups: {plan.get('p0_group_count')}",
        "",
    ]
    groups = plan.get("priority_groups", [])
    if groups:
        lines.append("| Priority | Gap | Candidates | Fix |")
        lines.append("|---|---|---|---|")
        for g in groups[:50]:
            if not isinstance(g, dict):
                continue
            lines.append(
                f"| {g.get('priority', '-')} "
                f"| {g.get('gap_type', '-')} "
                f"| {g.get('candidate_count', 0)} "
                f"| {str(g.get('recommended_fix', '-'))[:80]} |"
            )
        lines.append("")
    return "\n".join(lines)


def _render_runtime_customer_reproduction_pack_markdown(pack: dict[str, Any]) -> str:
    lines = [
        "# Runtime Customer Reproduction Pack",
        "",
        f"- engine: `{pack.get('engine')}`",
        f"- project: `{pack.get('project_id')}`",
        f"- status: `{pack.get('status')}`",
        f"- findings: {pack.get('finding_count', 0)}",
        f"- customer-ready: {pack.get('customer_ready_reproduction_count', 0)}",
        f"- blocked: {pack.get('blocked_reproduction_count', 0)}",
        "",
    ]
    packages = pack.get("packages", [])
    for item in packages[:20]:
        if not isinstance(item, dict):
            continue
        lines.append(f"## {item.get('finding_id', '-')} — {item.get('title', '-')}")
        lines.append(f"- candidate: `{item.get('candidate_id')}`")
        lines.append(f"- readiness: `{item.get('readiness_level')}` / customer-ready `{item.get('customer_ready')}`")
        lines.append("")
        lines.append("| # | Phase | Method | Path | HTTP |")
        lines.append("|---:|---|---|---:|---|")
        for step in item.get("reproduction_trace", [])[:20]:
            if not isinstance(step, dict):
                continue
            lines.append(
                f"| {step.get('sequence', '-')} "
                f"| {step.get('phase', '-')} "
                f"| {step.get('method', '-')} "
                f"| {str(step.get('path', '-'))[:60]} "
                f"| {step.get('status_code', '-')} |"
            )
        lines.append("")
    return "\n".join(lines)


def _render_runtime_evidence_scoreboard_markdown(scoreboard: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Scoreboard",
        "",
        f"- engine: `{scoreboard.get('engine')}`",
        f"- project: `{scoreboard.get('project_id')}`",
        f"- integrity score: `{scoreboard.get('execution_integrity_score')}`",
        f"- maturity: `{(scoreboard.get('evidence_maturity') or {}).get('level')}`",
        "",
        "## Coverage",
        f"- probes: {scoreboard.get('probe_count', 0)}",
        f"- executed: {scoreboard.get('executed_probe_count', 0)} ({scoreboard.get('execution_coverage_rate', 0)}%)",
        f"- HTTP responses: {scoreboard.get('target_http_response_count', 0)}",
        f"- verdicts: `{json.dumps(scoreboard.get('verdict_counts', {}), ensure_ascii=False)}`",
        "",
        "## Evidence Health",
        f"- fixture setup: {scoreboard.get('fixture_setup_accepted_count', 0)}/{scoreboard.get('fixture_setup_executed_count', 0)}",
        f"- binding: {scoreboard.get('runtime_binding_success_count', 0)}/{scoreboard.get('runtime_binding_event_count', 0)}",
        f"- snapshots: {scoreboard.get('snapshot_accepted_count', 0)}/{scoreboard.get('snapshot_request_count', 0)}",
        f"- cleanup: {scoreboard.get('cleanup_accepted_count', 0)}/{scoreboard.get('cleanup_executed_count', 0)}",
        "",
        "## Findings",
        f"- validated: {scoreboard.get('validated_candidate_count', 0)}",
        f"- protected/falsified: {scoreboard.get('protected_or_falsified_count', 0)}",
        f"- needs evidence: {scoreboard.get('needs_more_evidence_count', 0)}",
        "",
    ]
    return "\n".join(lines)


# ── Main report renderer ────────────────────────────────────────────────

def render_probe_report(report: dict[str, Any]) -> str:
    """Render the complete probe execution report as markdown."""
    lines = [
        "# QualiBug Probe Execution Report",
        "",
        f"- project: `{report.get('project_id')}`",
        f"- generated: {report.get('created_at')}",
        f"- probes: {report.get('probe_count', 0)}",
        f"- findings: {len(report.get('findings', []))}",
        "",
    ]

    findings = report.get("findings", [])
    if findings:
        lines.append("## Findings")
        lines.append("")
        for f in findings[:30]:
            if not isinstance(f, dict):
                continue
            lines.append(f"### [{f.get('severity', '?')}] {f.get('title', '?')}")
            lines.append(f"- category: `{f.get('category', '?')}`")
            lines.append(f"- confidence: {f.get('confidence', '?')}")
            lines.append(f"- evidence: `{f.get('evidence_grade', '?')}`")
            lines.append(f"- reason: {str(f.get('reason', ''))[:200]}")
            lines.append("")
        if len(findings) > 30:
            lines.append(f"_Showing 30 of {len(findings)} findings._")
            lines.append("")

    scoreboard = report.get("runtime_evidence_scoreboard")
    if isinstance(scoreboard, dict):
        lines.append(_render_runtime_evidence_scoreboard_markdown(scoreboard))

    ledger = report.get("runtime_evidence_probe_ledger")
    if isinstance(ledger, dict):
        lines.append(_render_runtime_evidence_probe_ledger_markdown(ledger))

    repro = report.get("runtime_customer_reproduction_pack")
    if isinstance(repro, dict):
        lines.append(_render_runtime_customer_reproduction_pack_markdown(repro))

    remediation = report.get("runtime_evidence_remediation_plan")
    if isinstance(remediation, dict):
        lines.append(_render_runtime_evidence_remediation_plan_markdown(remediation))

    return "\n".join(lines)
