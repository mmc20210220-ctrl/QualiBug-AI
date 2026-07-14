"""Campaign lifecycle management.
Extracted from v12_pipeline.py.
"""
from __future__ import annotations

import hashlib, os, time
from pathlib import Path
from typing import Any

from .pipeline_runtime import _dict, _source_manifest_details, _source_text, source_snapshot_hash
from .pipeline_slices import _behavior_slice_settings
from .enterprise_campaign import EnterpriseCampaign, EnterpriseCampaignStore

def _campaign_identity_defaults(project: str, root: Path) -> dict[str, str]:
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
    except Exception:
        return {}
    profile = registry.get("test_profile") if isinstance(registry, dict) else {}
    if not isinstance(profile, dict):
        return {}
    defaults: dict[str, str] = {}
    scope_id = str(
        profile.get("scope_id")
        or profile.get("deployment_scope_id")
        or profile.get("project_scope_id")
        or ""
    ).strip()
    environment_ref = str(
        profile.get("environment_ref")
        or profile.get("target_environment")
        or profile.get("environment")
        or ""
    ).strip()
    if scope_id:
        defaults["scope_id"] = scope_id[:160]
    if environment_ref:
        defaults["environment_ref"] = environment_ref[:160]
    return defaults


def _campaign_context(project: str, prd_text: str, api_spec_text: str, db_schema_text: str, base_url: str, settings: dict[str, int], context: dict[str, Any], root: Path, submitted_api_spec_text: Any) -> tuple[EnterpriseCampaign, EnterpriseCampaignStore, str]:
    policy_version = str(context.get("policy_version") or _active_policy_version())[:120]
    defaults = _campaign_identity_defaults(project, root)
    scope_id = str(
        context.get("scope_id")
        or defaults.get("scope_id")
        or f"project_scope_{hashlib.sha256(project.encode()).hexdigest()[:12]}"
    )[:160]
    environment_ref = str(
        context.get("environment_ref")
        or context.get("target_environment")
        or defaults.get("environment_ref")
        or (f"target_{hashlib.sha256(base_url.encode()).hexdigest()[:16]}" if base_url else "unbound_environment")
    )[:160]
    rerun_key = str(context.get("campaign_rerun_key") or context.get("campaign_restart_key") or "")[:120]
    rerun_reason = str(context.get("campaign_rerun_reason") or context.get("campaign_restart_reason") or "")[:240]
    snapshot = source_snapshot_hash(prd_text, api_spec_text, db_schema_text, scope_id, environment_ref)
    verification_text = context.get("_source_verification_text", submitted_api_spec_text)
    source_manifest, source_issues = _source_manifest_details(context, verification_text)
    candidate = EnterpriseCampaign.create(
        project,
        scope_id,
        environment_ref,
        snapshot,
        source_id=source_manifest["source_id"] if not source_issues else "",
        source_hash=source_manifest["source_hash"] if not source_issues else "",
        policy_version=policy_version,
        rerun_key=rerun_key,
        rerun_reason=rerun_reason,
        slice_budget=settings["slice_budget"],
        automatic_round_limit=settings["round_limit"],
    )
    store = EnterpriseCampaignStore(root, project)
    campaign, mode = store.open_or_create(candidate)
    # NOTE: the effective per-round budget / round limit are auto-scaled to the
    # discovered candidate-pool size just before scheduling. Here we only align the
    # persisted campaign ceilings with the (possibly env-overridden) starting
    # settings; the auto-scaler may raise them further at scheduling time.
    campaign.slice_budget = min(campaign.slice_budget, settings["slice_budget"])
    campaign.automatic_round_limit = min(campaign.automatic_round_limit, settings["round_limit"])
    return campaign, store, mode


def _behavior_contract_rerun_key(behavior_contract: dict[str, Any]) -> str:
    slices_payload: list[dict[str, Any]] = []
    for item in behavior_contract.get("slices", []) if isinstance(behavior_contract, dict) else []:
        row = _dict(item)
        slice_id = str(row.get("slice_id") or "").strip()
        if not slice_id:
            continue
        slices_payload.append({
            "slice_id": slice_id,
            "entity": str(row.get("entity") or "").strip(),
            "kind": str(row.get("kind") or "").strip(),
            "states": sorted(str(state or "").strip() for state in row.get("states", []) if str(state or "").strip()),
            "endpoints": sorted(str(path or "").strip() for path in row.get("endpoints", []) if str(path or "").strip()),
            "evidence_gaps": sorted(str(gap or "").strip() for gap in row.get("evidence_gaps", []) if str(gap or "").strip()),
        })
    gap_payload: list[dict[str, str]] = []
    for item in behavior_contract.get("coverage_gaps", []) if isinstance(behavior_contract, dict) else []:
        row = _dict(item)
        gap_payload.append({
            "kind": str(row.get("kind") or "").strip(),
            "title": str(row.get("title") or "").strip(),
            "entity": str(row.get("entity") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
        })
    payload = {
        "schema": "behavior_contract_rerun_v1",
        "slices": sorted(slices_payload, key=lambda item: item["slice_id"]),
        "coverage_gaps": sorted(gap_payload, key=lambda item: (item["kind"], item["entity"], item["title"], item["reason"])),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    return f"behavior_contract:{digest}"


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


def _normalize_executable_api_document(api_document: Any) -> tuple[str, dict[str, Any]]:
    """Compile customer API material into the one executable OpenAPI view.

    The immutable submitted document remains available separately for source
    identity and reasoning. Parser-bound runtime consumers must receive this
    normalized JSON view so a Markdown document is not accidentally parsed as
    YAML again later in the pipeline.
    """

    raw = (
        json.dumps(api_document, ensure_ascii=False, default=str)
        if isinstance(api_document, dict)
        else str(api_document or "")
    )
    if not raw.strip():
        return "", {
            "status": "missing",
            "input_format": "unknown",
            "normalized_path_count": 0,
            "normalized_operation_count": 0,
            "reason": "api_document_missing",
        }

    try:
        from .universal_api_parser import detect_format, parse_to_openapi

        input_format = detect_format(raw)
        normalized = parse_to_openapi(raw)
        if not isinstance(normalized, dict):
            normalized = {}
        paths = normalized.get("paths") if isinstance(normalized.get("paths"), dict) else {}
        normalized = {
            **normalized,
            "openapi": str(normalized.get("openapi") or "3.0.0"),
            "info": (
                normalized.get("info")
                if isinstance(normalized.get("info"), dict)
                else {"title": "normalized customer API"}
            ),
            "paths": paths,
            "components": (
                normalized.get("components")
                if isinstance(normalized.get("components"), dict)
                else {"schemas": {}}
            ),
        }
        normalized.setdefault("components", {}).setdefault("schemas", {})
        operation_count = sum(
            1
            for operations in paths.values()
            if isinstance(operations, dict)
            for method in operations
            if str(method).lower()
            in {"get", "post", "put", "patch", "delete", "head", "options"}
        )
        return json.dumps(normalized, ensure_ascii=False, default=str), {
            "status": "normalized" if paths else "degraded",
            "input_format": input_format,
            "normalized_path_count": len(paths),
            "normalized_operation_count": operation_count,
            "reason": "" if paths else "api_document_has_no_executable_paths",
        }
    except Exception as exc:
        # A malformed source must become an observable, safe empty executable
        # catalog. Keeping the original text here would merely move the same
        # parser exception into preview/scenario generation and erase execution.
        safe_empty = {
            "openapi": "3.0.0",
            "info": {"title": "unparseable customer API"},
            "paths": {},
            "components": {"schemas": {}},
        }
        return json.dumps(safe_empty, ensure_ascii=False), {
            "status": "FAILED_SAFE",
            "input_format": "unknown",
            "normalized_path_count": 0,
            "normalized_operation_count": 0,
            "reason": "api_document_parse_failed",
            "error_type": type(exc).__name__,
            "detail": str(exc)[:300],
        }


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
    """Derive generic operation facts from API docs for Behavior IR.

    Uses existing parsers only; never hardcodes industry paths.
    """
    text = str(api_spec_text or "")
    if not text.strip():
        return []
    operations: list[dict[str, Any]] = []
    try:
        from .universal_api_parser import parse_api_document

        parsed = parse_api_document(text)
        if isinstance(parsed, dict):
            for item in parsed.get("operations") or parsed.get("endpoints") or []:
                if isinstance(item, dict):
                    operations.append(item)
    except Exception:
        operations = []
    if operations:
        return operations[:500]
    import re as _re

    cleaned: list[dict[str, Any]] = []
    for match in _re.finditer(
        r"(?im)^(?:\s*#{1,6}\s*)?(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
        text,
    ):
        method = match.group(1).upper()
        path = match.group(2).strip().rstrip("`").rstrip(",").rstrip(")")
        cleaned.append({
            "method": method,
            "path": path,
            "operation_id": f"{method.lower()}:{path}",
            "source_id": "api_spec_text",
            "side_effect_class": "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read",
        })
    if not cleaned:
        # Also accept OpenAPI paths blocks via parse_to_openapi when available.
        try:
            from .universal_api_parser import parse_to_openapi

            spec = parse_to_openapi(text)
            paths = spec.get("paths") if isinstance(spec, dict) else {}
            if isinstance(paths, dict):
                for path, methods in paths.items():
                    if not isinstance(methods, dict):
                        continue
                    for method, op in methods.items():
                        m = str(method or "").upper()
                        if m not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                            continue
                        op_dict = op if isinstance(op, dict) else {}
                        cleaned.append({
                            "method": m,
                            "path": str(path),
                            "operation_id": str(op_dict.get("operationId") or f"{m.lower()}:{path}"),
                            "source_id": "api_spec_openapi",
                            "summary": str(op_dict.get("summary") or ""),
                            "description": str(op_dict.get("description") or ""),
                            "tags": list(op_dict.get("tags") or []),
                            "side_effect_class": "write" if m in {"POST", "PUT", "PATCH", "DELETE"} else "read",
                            "parameters": list(op_dict.get("parameters") or []),
                            "request_schema": op_dict.get("requestBody"),
                            "response_schema": op_dict.get("responses"),
                        })
        except Exception:
            pass
    return cleaned[:500]


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
        actors.append({
            "role": role,
            "account_ref": account_ref,
            "tenant": row.get("tenant") or row.get("scope"),
            "secret_ref": f"secret_ref:test_accounts:{account_ref}",
            "status": str(row.get("status") or "active"),
        })
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


_MAINLINE_IDENTITY_FIELDS = (
    "mainline_authority",
    "run_id",
    "target_id",
    "environment_id",
    "policy_version",
    "evaluation_mode",
)


def _require_mainline_identity(context: dict[str, Any]) -> None:
    from .discovery_mainline_contract import MainlineContractError

    for field in _MAINLINE_IDENTITY_FIELDS:
        if not str(context.get(field) or "").strip():
            raise MainlineContractError(f"{field}_missing")


def _build_mainline_campaign(inputs: Any) -> dict[str, Any]:
    settings = _behavior_slice_settings()
    campaign_api_spec_text = str(
        inputs.campaign_context.get("_campaign_api_spec_text")
        or inputs.api_spec_text
        or ""
    )
    campaign, store, mode = _campaign_context(
        inputs.project,
        inputs.prd_text,
        campaign_api_spec_text,
        inputs.db_schema_text,
        inputs.approved_base_url,
        settings,
        inputs.campaign_context,
        inputs.root,
        inputs.api_spec_text,
    )
    expected_campaign_id = str(
        inputs.campaign_context.get("campaign_id") or ""
    ).strip()
    if expected_campaign_id and expected_campaign_id != campaign.campaign_id:
        from .discovery_mainline_contract import MainlineContractError

        raise MainlineContractError("mainline_campaign_identity_mismatch")
    return {
        "campaign_id": campaign.campaign_id,
        "campaign": campaign,
        "store": store,
        "mode": mode,
