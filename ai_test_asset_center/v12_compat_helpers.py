"""Legacy V12 compatibility helpers outside the discovery mainline.

Re-exported from ``v12_pipeline`` for tests and private-pilot patches.
Not on the ``experiment_candidate`` delivery path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .enterprise_campaign import (
    EnterpriseCampaign,
    EnterpriseCampaignStore,
    has_real_confirmation_receipt,
    source_snapshot_hash,
)
from .discovery_runtime_execution_support import _governed_write_block_reason
from .pipeline_runtime import _dict, _confirmed_findings_path
from .pipeline_slices import _behavior_slice_settings
from .target_policy import build_target_policy_decision

logger = logging.getLogger(__name__)

# Thread-safe HAR storage using threading.local() so concurrent scans
# (e.g. multithreaded server, background continuous discovery) never
# contaminate each other's entry lists.
_har_store = threading.local()

def _ensure_har_list() -> list[dict[str, Any]]:
    """Return the per-thread HAR entry list, initialising it on first access."""
    try:
        return _har_store.entries
    except AttributeError:
        _har_store.entries = []
        return _har_store.entries

def _reset_v12_har_entries() -> None:
    """Discard all per-thread HAR entries.

    Called at the start of every pipeline run so stale traffic from a
    previous scan never leaks into the current report.
    """
    _har_store.entries = []

def _record_v12_har(method: str, url: str, status: int, body: Any, actor: str = "", elapsed_ms: float = 0.0) -> None:
    _ensure_har_list().append({
        "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "time": elapsed_ms,
        "request": {"method": method, "url": url},
        "response": {"status": status, "content": {"mimeType": "application/json", "text": str(body)[:5000]}},
        "_actor": actor,
    })


def _v12_har_report() -> dict[str, Any]:
    entries = _ensure_har_list()
    if not entries:
        return {"status": "no_traffic"}
    counts: dict[int, int] = {}
    result: list[dict[str, Any]] = []
    for item in entries:
        status = int(item.get("response", {}).get("status") or 0)
        counts[status] = counts.get(status, 0) + 1
        content = item.get("response", {}).get("content", {})
        result.append({
            "startedDateTime": item.get("startedDateTime"),
            "time": item.get("time"),
            "request": item.get("request"),
            "response": {"status": status, "body": str(content.get("text") if isinstance(content, dict) else content)[:2000]},
            "_actor": item.get("_actor", ""),
        })
    return {
        "status": "captured",
        "total_calls": len(result),
        "error_responses": sum(count for status, count in counts.items() if status >= 400),
        "status_distribution": counts,
        "entries": result,
    }

_SENSITIVE = {"authorization", "token", "password", "secret", "cookie", "api_key", "apikey"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def is_v12_enabled() -> bool:
    return os.environ.get("ENABLE_V12_STATE_GRAPH_ENGINE", "false").lower() in {"1", "true", "yes", "on"}


def _scenario_executable(scenario: Any) -> bool:
    return bool(getattr(scenario, "steps", []) or []) and str(getattr(scenario, "execution_policy", "") or "") in {
        "safe_read_only",
        "approved_test_write",
        "approved_sandbox_write",
        "runtime_approved",
    }


def _is_test_write_allowed() -> bool:
    """Check if test-environment write operations are approved.

    ``QUALIBUG_ALLOW_TEST_WRITE=1`` means the target is a customer test
    environment where write operations are safe.  The system will still
    enforce DELETE safety guards and fixture data isolation.
    """
    return os.environ.get("QUALIBUG_ALLOW_TEST_WRITE", "").strip().lower() in ("1", "true", "yes")


def _test_write_fixture_prefix() -> str:
    return os.environ.get("QUALIBUG_TEST_FIXTURE_PREFIX", "qualibug_test_").strip() or "qualibug_test_"


def _append_runtime_contract_scenarios(
    generated: list[Any],
    context: dict[str, Any],
    *,
    discovery_round: int,
) -> list[Any]:
    from .runtime_scenario_contract_gate import compile_runtime_scenarios

    runtime_scenarios = compile_runtime_scenarios(
        context,
        discovery_round=discovery_round,
    )
    combined = list(generated)
    seen = {
        f"{getattr(item, 'behavior_slice_id', '')}|{getattr(item, 'id', '')}"
        for item in combined
    }
    for item in runtime_scenarios:
        identity = (
            f"{getattr(item, 'behavior_slice_id', '')}|{getattr(item, 'id', '')}"
        )
        if identity not in seen:
            combined.append(item)
            seen.add(identity)
    return combined


def _execution_approval_contract(context: dict[str, Any], campaign: EnterpriseCampaign, base_url: str, root: Path) -> dict[str, Any]:
    """Enforce the environment boundary after Campaign identity is known.

    A source-bound non-production campaign is authorized for automatic reads
    and writes without per-probe approval. Production and unknown environments
    are fail-closed for every write mode. ``execution_approval_id`` remains
    accepted as audit metadata, but is not a runtime prerequisite.
    """
    if not base_url:
        return {"status": "not_required", "reason": "runtime_target_missing"}
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip()
    if execution_mode == "safe_read_only":
        return {"status": "approved", "execution_mode": execution_mode, "write_allowed": False}
    from .sandbox_write_executor import (
        resolve_environment_kind,
    )

    environment_ref = str(campaign.environment_ref or context.get("environment_ref") or "").strip()
    environment_kind = str(
        context.get("environment_kind")
        or context.get("environment_type")
        or context.get("environment_class")
        or ""
    ).strip()
    if not environment_kind:
        # Project configuration is an explicit declaration. environment_ref is
        # never interpreted as an environment class.
        environment_kind = resolve_environment_kind(
            root,
            str(getattr(campaign, "project_id", "") or context.get("project") or ""),
            dict(context or {}),
        )
    decision = build_target_policy_decision(
        requested_base_url=base_url,
        approved_base_url=str(context.get("approved_base_url") or base_url),
        environment_type=environment_kind,
        environment_ref=environment_ref,
        execution_mode=execution_mode,
        runtime_status="approved",
    )
    if not decision.get("write_allowed"):
        codes = list(decision.get("blocking_codes") or [])
        return {
            "status": "blocked",
            "code": str(codes[0] if codes else "TARGET_POLICY_BLOCKED"),
            "blocking_codes": codes,
            "execution_mode": execution_mode,
            "target_policy_decision": decision,
        }
    approval_id = str(context.get("execution_approval_id") or "").strip()
    return {
        "status": "approved",
        "approval_id": approval_id,
        "execution_mode": execution_mode,
        "environment_ref": environment_ref,
        "environment_kind": environment_kind,
        "authorization_basis": "source_bound_nonproduction_campaign",
        "write_allowed": True,
        "target_policy_decision": decision,
    }


def _persist_confirmed_findings(root: Path, project: str, findings: list[dict[str, Any]]) -> int:
    """主链 9 Gap B1: persist every *deliverable* confirmed defect
    (``customer_delivery_status == "defect"``, must carry a stable ``evidence_id``)
    into ``defect_discovery/confirmed_findings.json`` keyed by evidence_id, so the
    regression runner can replay its reproduction reliably and tell resolved from
    regression.

    Only real defects are persisted — findings blocked by the 主链 1 production-data
    safety boundary (``blocked_safety_boundary``) are deliberately excluded, exactly
    as they are excluded from the customer delivery gate. Returns the number saved.
    """
    if not isinstance(findings, list):
        return 0
    ledger: dict[str, dict[str, Any]] = {}
    path = _confirmed_findings_path(root, project)
    if path.exists():
        try:
            _loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(_loaded, dict):
                ledger = _loaded
        except Exception:
            ledger = {}
    saved = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        if str(f.get("customer_delivery_status") or "") != "defect":
            continue
        evidence_id = str(f.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
        raw_evidence = f.get("raw_evidence") if isinstance(f.get("raw_evidence"), dict) else {}
        response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
        try:
            buggy_status_code = int(response_raw.get("status_code") or 0)
        except (TypeError, ValueError):
            buggy_status_code = 0
        # ── System Behavior Space contract forwarding ──
        # Preserve system promise metadata through the confirmed-findings ledger
        # so regression suite builder, regression runner, and risk clue pool all
        # inherit the contract without re-reading fragile two-step patches.
        _system_promise_id = str(f.get("system_promise_id") or "").strip()
        _regression_contract = f.get("regression_contract") if isinstance(f.get("regression_contract"), dict) else {}
        _sb_evidence = f.get("system_behavior_space_evidence") if isinstance(f.get("system_behavior_space_evidence"), dict) else {}
        _sb_dimensions = f.get("system_behavior_dimensions") if isinstance(f.get("system_behavior_dimensions"), list) else []
        _sb_surface_plan = f.get("system_behavior_surface_plan") if isinstance(f.get("system_behavior_surface_plan"), list) else []
        _sb_required_assets = f.get("system_behavior_required_assets") if isinstance(f.get("system_behavior_required_assets"), list) else []
        _sb_source_family = str(f.get("system_behavior_source_family") or "").strip()
        _learning_signal = f.get("learning_signal") if isinstance(f.get("learning_signal"), dict) else {}

        entry: dict[str, Any] = {
            "evidence_id": evidence_id,
            "title": str(f.get("title") or ""),
            "severity": str(f.get("severity") or "P2"),
            "confirmation_status": str(f.get("confirmation_status") or ""),
            "bug_status": str(f.get("bug_status") or ""),
            "customer_delivery_status": "defect",
            "expected": str(f.get("expected") or ""),
            "actual": str(f.get("actual") or ""),
            "buggy_status_code": buggy_status_code,
            "behavior_slice_id": str(f.get("behavior_slice_id") or ""),
            "discovery_round": f.get("discovery_round"),
            "campaign_id": str(f.get("campaign_id") or ""),
            "timestamp": str(f.get("timestamp") or ""),
            "reproduction": {
                "request": str(ev.get("request") or ""),
                "target": str(ev.get("target") or ""),
                "method": str((ev.get("request") or "").split(" ", 1)[0].strip()),
                "path": "/" + str((ev.get("request") or "").split(" ", 1)[-1].lstrip("/")) if " " in str(ev.get("request") or "") else "",
                "actor": str(ev.get("actor") or ""),
                "reproduction_steps": list(ev.get("reproduction_steps") or []),
            },
        }
        if _system_promise_id:
            entry["system_promise_id"] = _system_promise_id
        if _regression_contract:
            entry["regression_contract"] = _regression_contract
        if _sb_evidence:
            entry["system_behavior_space_evidence"] = _sb_evidence
        if _sb_dimensions:
            entry["system_behavior_dimensions"] = [str(item) for item in _sb_dimensions if str(item)]
        if _sb_surface_plan:
            entry["system_behavior_surface_plan"] = [str(item) for item in _sb_surface_plan if str(item)]
        if _sb_required_assets:
            entry["system_behavior_required_assets"] = [str(item) for item in _sb_required_assets if str(item)]
        if _sb_source_family:
            entry["system_behavior_source_family"] = _sb_source_family
        if _learning_signal:
            entry["learning_signal"] = _learning_signal
        ledger[evidence_id] = entry
        saved += 1
    if saved:
        try:
            from .artifact_redactor import write_json_redacted

            write_json_redacted(path, ledger)
        except Exception as exc:
            raise RuntimeError(f"CONFIRMED_FINDING_PERSIST_FAILED:{type(exc).__name__}") from exc
    return saved


def _maybe_start_behavior_contract_rerun(
    project: str,
    prd_text: str,
    api_spec_text: str,
    db_schema_text: str,
    base_url: str,
    settings: dict[str, int],
    context: dict[str, Any],
    root: Path,
    submitted_api_spec_text: Any,
    behavior_contract: dict[str, Any],
    campaign: EnterpriseCampaign,
    campaign_store: EnterpriseCampaignStore,
    campaign_mode: str,
) -> tuple[EnterpriseCampaign, EnterpriseCampaignStore, str]:
    # Deferred import: v12_pipeline imports this module at load time.
    from .v12_pipeline import _behavior_contract_rerun_key, _campaign_context

    explicit_rerun_key = str(context.get("campaign_rerun_key") or context.get("campaign_restart_key") or "").strip()
    if explicit_rerun_key or campaign.status != "completed":
        return campaign, campaign_store, campaign_mode
    derived_rerun_key = _behavior_contract_rerun_key(behavior_contract)
    if not derived_rerun_key or campaign.rerun_key == derived_rerun_key:
        return campaign, campaign_store, campaign_mode
    rerun_context = dict(context)
    rerun_context["campaign_rerun_key"] = derived_rerun_key
    rerun_context.setdefault("campaign_rerun_reason", "re-evaluate current behavior contract")
    rerun_campaign, rerun_store, rerun_mode = _campaign_context(
        project,
        prd_text,
        api_spec_text,
        db_schema_text,
        base_url,
        settings,
        rerun_context,
        root,
        submitted_api_spec_text,
    )
    return rerun_campaign, rerun_store, rerun_mode


def _recover_stale_campaign_state(campaign: EnterpriseCampaign, slices: list[dict[str, Any]] | None = None) -> bool:
    if campaign.status != "coverage_deferred":
        return False
    deferred_reason = str(campaign.coverage_deferred_reason or "").strip()
    recoverable_reasons = {
        "all_pending_slices_attempted_needs_new_evidence_or_policy",
        "configured_round_limit_reached",
        "slice_budget_reached",
        "automatic_campaign_budget_exhausted",
        "remaining_unattempted_slices_not_source_executable",
    }
    if deferred_reason and deferred_reason not in recoverable_reasons:
        return False
    current_slice_ids = {
        str(item.get("slice_id") or "")
        for item in (slices or [])
        if isinstance(item, dict) and str(item.get("slice_id") or "")
    }
    confirmed = set(campaign.confirmation_receipts)
    unattempted_current = current_slice_ids.difference(campaign.attempted_slice_ids).difference(confirmed)
    if not current_slice_ids or not unattempted_current:
        return False
    campaign.status = "active"
    campaign.round_count = 0
    campaign.coverage_deferred_reason = ""
    campaign.next_campaign_reason = ""
    return True


def _knowledge_asset_planning_text(asset: dict[str, Any]) -> str:
    """Flatten the structured enterprise knowledge asset into a planning text
    block the behavior-graph builder can parse, so uploaded docs (business
    rules, permission matrix, historical-bug patterns) drive the test plan —
    主链 2: parsed knowledge must feed test planning, not just sit in a report.

    Fully generic: no industry/endpoint/field hardcoding — only the customer's
    own parsed statements are surfaced.
    """
    parts: list[str] = []

    rules = asset.get("rule_library") or []
    if isinstance(rules, list):
        lines = []
        for r in rules[:300]:
            if not isinstance(r, dict):
                continue
            stmt = str(r.get("statement") or r.get("expression") or "").strip()
            if stmt:
                lines.append(f"- {stmt}")
        if lines:
            parts.append("## 企业资料解析出的业务规则（驱动数据一致性/金额/库存/状态类测试）\n" + "\n".join(lines))

    perms = asset.get("permission_matrix") or []
    if isinstance(perms, list):
        lines = []
        for p in perms[:300]:
            if not isinstance(p, dict):
                continue
            # Permission entries use role/resource/actions/scope, not statement.
            role = str(p.get("role") or "").strip()
            resource = str(p.get("resource") or "").strip()
            actions = p.get("actions") or []
            scope = str(p.get("scope") or "").strip()
            if role and resource:
                action_text = ", ".join(str(a) for a in actions) if isinstance(actions, list) else str(actions)
                line = f"- {role} 对 {resource} 具有 {action_text or 'access'} 权限"
                if scope and scope not in ("unspecified", ""):
                    line += f" (scope: {scope})"
                lines.append(line)
        if lines:
            parts.append("## 企业资料解析出的权限矩阵（驱动越权/未授权访问测试）\n" + "\n".join(lines))

    risks = asset.get("risk_domains") or []
    if isinstance(risks, list):
        lines = []
        for r in risks[:300]:
            if not isinstance(r, dict):
                continue
            # Risk domain entries use title/expected/risk_type, not statement.
            title = str(r.get("title") or "").strip()
            expected = str(r.get("expected") or "").strip()
            risk_type = str(r.get("risk_type") or "").strip()
            stmt = title or expected or risk_type
            if stmt:
                lines.append(f"- {stmt}")
        if lines:
            parts.append("## 企业资料解析出的历史风险/历史Bug模式（驱动回归测试）\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _redacted_trace_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    path = re.sub(r"^https?://[^/]+", "", path, flags=re.IGNORECASE)
    path = path.split("?", 1)[0]
    path = re.sub(r"/[0-9]+(?=/|$)", "/{id}", path)
    path = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?=/|$)",
        "/{id}",
        path,
        flags=re.IGNORECASE,
    )
    return path


def _redacted_execution_error(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("failed_after_retries:"):
        nested = _governed_write_block_reason(text.split(":", 1)[1])
        if nested:
            return nested.split(":", 1)[0]
    if "write_cleanup_operation_not_declared" in text:
        return "write_cleanup_operation_not_declared"
    if "missing_runtime_path_binding" in text:
        return "missing_runtime_path_binding"
    if "precondition" in text:
        return "precondition_not_met"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "connection" in text or "unreachable" in text:
        return "connection_failure"
    if "auth" in text or "token" in text or "credential" in text:
        return "authentication_failure"
    if "validation" in text or "invalid input" in text:
        return "invalid_test_input"
    return "execution_error"


def _redacted_trace_status(step: dict[str, Any]) -> int:
    response = _dict(step.get("response"))
    try:
        return max(0, int(step.get("status") or response.get("status_code") or 0))
    except (TypeError, ValueError):
        return 0


def _execution_trace_identity(scenario: Any, trace: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(getattr(scenario, "behavior_slice_id", "") or "").strip(),
        str(trace.get("scenario_id") or getattr(scenario, "id", "") or "").strip(),
        str(trace.get("actor_role") or "").strip(),
    )


def _redacted_execution_trace_graph(
    scenario: Any,
    trace: dict[str, Any],
    *,
    discovery_round: int,
) -> dict[str, Any]:
    """Keep stage evidence for every attempt without persisting payloads."""

    steps: list[dict[str, Any]] = []
    for raw_step in trace.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        steps.append(
            {
                "method": str(raw_step.get("method") or "").strip().upper(),
                "path": _redacted_trace_path(raw_step.get("path")),
                "action": str(raw_step.get("action") or "")[:80],
                "status": _redacted_trace_status(raw_step),
                "skipped_reason": _redacted_execution_error(raw_step.get("skipped_reason"))
                if str(raw_step.get("skipped_reason") or "").strip()
                else "",
            }
        )

    sandbox_write = _dict(trace.get("sandbox_write"))
    cleanup = _dict(sandbox_write.get("cleanup"))
    audit_records = [
        item for item in sandbox_write.get("audit_records") or []
        if isinstance(item, dict)
    ]
    accepted_write_records = [
        item for item in audit_records if item.get("operation_accepted") is True
    ]
    accepted_non_cleanup_write_count = len(accepted_write_records)
    cleanup_status = str(cleanup.get("status") or "").strip().lower()
    explicit_cleanup_statuses = [
        str(item.get("cleanup_status") or "").strip().lower()
        for item in accepted_write_records
        if str(item.get("cleanup_status") or "").strip()
    ]
    cleanup_attempted_count = len(accepted_write_records)
    cleanup_completed_count = sum(
        1
        for status in explicit_cleanup_statuses
        if status in {"completed", "verified", "not_required"}
    )
    cleanup_failure_count = (
        len(explicit_cleanup_statuses) - cleanup_completed_count
        if explicit_cleanup_statuses
        else int(
            accepted_non_cleanup_write_count > 0
            and cleanup_status
            not in {"completed", "verified", "not_required", "not_applicable"}
        )
    )
    execution_trace = {
        "scenario_id": str(trace.get("scenario_id") or getattr(scenario, "id", "") or ""),
        "actor_role": str(trace.get("actor_role") or "")[:80],
        "steps": steps,
        "errors": [
            _redacted_execution_error(item)
            for item in trace.get("errors") or []
            if str(item or "").strip()
        ],
        "precondition_not_met": [
            {}
            for item in trace.get("precondition_not_met") or []
            if item is not None
        ],
        "sandbox_write": {
            "status": str(sandbox_write.get("status") or ""),
            "cleanup": {
                "status": str(cleanup.get("status") or ""),
                "receipt_ref": "present" if cleanup.get("receipt_ref") else "",
            },
            "audit_path": "present" if sandbox_write.get("audit_path") else "",
            "audit_record_count": len(audit_records),
        },
        "operational_receipt": {
            "scenario_attempt_count": 1,
            "http_request_attempt_count": sum(
                1
                for item in steps
                if item.get("method")
                and item.get("path")
                and not item.get("skipped_reason")
            ),
            "production_http_request_count": sum(
                1
                for item in audit_records
                if str(item.get("environment_kind") or "").strip().lower()
                in {"production", "prod", "live"}
            ),
            "accepted_write_count": (
                accepted_non_cleanup_write_count + cleanup_completed_count
            ),
            "accepted_non_cleanup_write_count": accepted_non_cleanup_write_count,
            "accepted_cleanup_write_count": cleanup_completed_count,
            "cleanup_attempted_count": cleanup_attempted_count,
            "cleanup_completed_count": cleanup_completed_count,
            "cleanup_failure_count": cleanup_failure_count,
        },
    }
    return {
        "scenario": {
            "id": str(trace.get("scenario_id") or getattr(scenario, "id", "") or ""),
            "behavior_slice_id": str(
                getattr(scenario, "behavior_slice_id", "") or ""
            ),
            "discovery_round": int(discovery_round or 0),
        },
        "execution_trace": execution_trace,
        "oracle_results": [],
        "layers_triggered": [],
        "redaction_contract": {
            "request_body_persisted": False,
            "response_body_persisted": False,
            "query_string_persisted": False,
            "credentials_persisted": False,
        },
    }


def _publish_behavior_contract_snapshot(
    result: dict[str, Any],
    behavior_contract: dict[str, Any],
    slices: list[dict[str, Any]],
) -> int:
    """Expose grounded candidates before preview, scheduling, or execution.

    Later stages may fail on one customer document, fixture, or provider.  The
    already-built candidate pool must remain observable so a failed run reports
    where conversion stopped instead of collapsing the entire funnel to zero.
    """

    preserved = [dict(item) for item in slices if isinstance(item, dict)]
    summary = _dict(behavior_contract.get("summary"))
    coverage_gaps = [
        dict(item)
        for item in behavior_contract.get("coverage_gaps", [])
        if isinstance(item, dict)
    ]
    result["behavior_slices"] = preserved
    result["behavior_contract"] = {
        "summary": {**summary, "total_slices": len(preserved)},
        "coverage_gaps": coverage_gaps,
    }
    return len(preserved)


def _record_pipeline_failure(result: dict[str, Any], exc: Exception) -> None:
    """Fail safe while preserving every candidate produced before the error."""

    detail = str(exc)[:500]
    slices = [
        item for item in result.get("behavior_slices", [])
        if isinstance(item, dict)
    ]
    result["error"] = detail
    phases = result.setdefault("phases", {})
    phases["pipeline"] = {
        "status": "FAILED_SAFE",
        "reason": "pipeline_exception",
        "error_type": type(exc).__name__,
        "detail": detail,
        "preserved_slice_count": len(slices),
    }
    stage_status = result.setdefault("stage_status", {})
    if isinstance(stage_status, dict):
        stage_status["pipeline"] = "FAILED_SAFE"
    stage_failures = result.setdefault("stage_failures", [])
    failure_marker = f"pipeline:{type(exc).__name__}:{detail}"
    if isinstance(stage_failures, list) and failure_marker not in stage_failures:
        stage_failures.append(failure_marker)
    ledger = _dict(result.get("behavior_slice_ledger"))
    if slices and not ledger:
        pending_ids = [
            str(item.get("slice_id") or "")
            for item in slices
            if str(item.get("slice_id") or "").strip()
        ]
        result["behavior_slice_ledger"] = {
            "total_slices": len(slices),
            "selected_slice_ids": [],
            "attempted_slice_ids": [],
            "confirmed_slice_ids": [],
            "pending_slice_ids": pending_ids,
            "stop_reason": "pipeline_failed_before_selection",
        }


def _extract_api_operations_for_ir(api_spec_text: str) -> list[dict[str, Any]]:
    """Compatibility wrapper — SSOT is discovery_runtime._api_operations."""
    text = str(api_spec_text or "")
    if not text.strip():
        return []
    from .discovery_mainline_contract import MainlineContractError
    from .discovery_runtime import _api_operations

    try:
        return _api_operations(text, submitted_source_text="")[:500]
    except MainlineContractError:
        return []


def _jwt_claim_identity(token: str) -> str:
    """Read the account identity claim from a JWT payload, structurally.

    Only the payload segment is decoded (no signature check — the target owns
    the secret, mirroring the credential refresher). The identity claim is
    read from the standard claims ``id``/``sub``/``user_id`` when present. A
    malformed segment or a non-JWT bearer (opaque token, API key) yields an
    empty string so callers leave the identity absent instead of guessing.
    """
    import base64 as _b64

    token = str(token or "").strip()
    parts = token.split(".")
    if len(parts) != 3:
        return ""
    try:
        segment = parts[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(_b64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8"))
    except Exception:
        return ""
    if not isinstance(claims, dict):
        return ""
    for key in ("id", "sub", "user_id", "userId"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _extract_runtime_actors_for_ir(root: Path, project: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Load declared test actors as secret_ref-only IR actors."""
    actors: list[dict[str, Any]] = []
    accounts_path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    payload: Any = {}
    if accounts_path.exists():
        try:
            payload = json.loads(accounts_path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            payload = {}
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("accounts") or payload.get("actors") or payload.get("users") or []
        if not rows and payload:
            # mapping of role -> account object
            rows = [
                {**(value if isinstance(value, dict) else {"name": key}), "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict) and key not in {"schema", "schema_version", "meta"}
            ]
    elif isinstance(payload, list):
        rows = payload
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or row.get("name") or row.get("id") or "").strip()
        if not role:
            continue
        account_ref = str(row.get("account_ref") or row.get("email") or row.get("username") or row.get("id") or role).strip()
        actor: dict[str, Any] = {
            "role": role,
            "account_ref": account_ref,
            "tenant": row.get("tenant") or row.get("scope"),
            "secret_ref": f"secret_ref:test_accounts:{account_ref}",
            "status": str(row.get("status") or "active"),
        }
        # The account row may carry an observed bearer token (login-response
        # identity). A JWT declares the account identity inside its own
        # payload (id/sub/user_id); surface it as account_id so read-side
        # ownership protocols can bind "own identity" parameters from
        # runtime-observed material. Unparseable or non-JWT tokens leave
        # account_id absent — never guessed.
        _account_id = _jwt_claim_identity(_text(row.get("token") or row.get("access_token") or row.get("jwt")))
        if _account_id:
            actor["account_id"] = _account_id
        actors.append(actor)
    # Context-declared actor
    scenario = _dict(context.get("runtime_scenario_contract"))
    declared = _dict(scenario.get("actor"))
    role = str(declared.get("role") or declared.get("name") or declared.get("id") or "").strip()
    if role and not any(a.get("role") == role for a in actors):
        actors.append({
            "role": role,
            "secret_ref": f"secret_ref:context:{role}",
            "status": "active",
        })
    return actors
