"""V12 source-grounded behavior pipeline with enterprise Campaign governance.

A Campaign is an auditable project scope, environment reference and source
snapshot. Planning can proceed without a target; any runtime traffic additionally
requires a valid source contract and a time-bounded execution approval issued for
the resolved Campaign.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _reset_pipeline_har_entries() -> None:
    """Reset thread-local HAR storage at pipeline start.

    Avoids module-level import (circular) — v12_compat_helpers may import us.
    """
    from .v12_compat_helpers import _reset_v12_har_entries

    _reset_v12_har_entries()


from .enterprise_campaign import (
    EnterpriseCampaign,
    EnterpriseCampaignStore,
    has_real_confirmation_receipt,
    source_snapshot_hash,
)
from .real_id_resolver import (
    alternate_collection_paths,
    bind_entity_fields,
    bind_path_params_from_documented_body,
    collection_path,
    infer_path_params,
    normalize_path_placeholders,
    param_field_candidates,
    path_has_placeholders,
)
from .enterprise_project_config import (
    match_production_data_exclusion,
    _load_execution_safety_boundary,
)
from .disposable_identity_materializer import (
    disposable_identity_nonce,
    materialize_disposable_identity_fields,
)
from .target_policy import build_target_policy_decision

_SENSITIVE = {"authorization", "token", "password", "secret", "cookie", "api_key", "apikey"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AUTH_ACCEPTANCE_KEY_TOKENS = (
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "jwt",
    "token",
    "session",
    "session_id",
    "sessionid",
    "bearer",
)
_AUTH_SUCCESS_BOOL_KEYS = {
    "authenticated",
    "authorized",
    "logged_in",
    "login_success",
    "success",
    "ok",
}
_AUTH_PRINCIPAL_KEYS = {"user", "account", "principal", "profile", "identity"}
_AUTH_ACCEPTANCE_HEADER_TOKENS = {"authorization", "set-cookie", "x-auth-token", "x-session-id"}
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")

# Canonical SSOTs — explicit imports required because ``import *`` skips ``_`` names.
from .pipeline_runtime import (  # noqa: F401
    _active_policy_version,
    _confirmed_findings_path,
    _dict,
    _evidence_chain_path,
    _persist_evidence_chain,
    _runtime_contract,
    _source_manifest_details,
    _source_text,
    _text,
    source_snapshot_hash,
)
from .pipeline_db import (  # noqa: F401
    _db_dialect_from_dsn,
    _dsn_from_text,
    _list_relation_columns,
    _list_relation_names,
    _map_schema_columns,
    _profile_database_dsn,
)
from .pipeline_fuzzer import (  # noqa: F401
    _build_parameter_fuzzer_governed_write_executor,
    _parameter_fuzzer_trace_result,
    _prepare_parameter_fuzzer_catalog,
    _runtime_contract_allows_parameter_fuzzer_writes,
)
from .pipeline_slices import (  # noqa: F401
    _ABS_MAX_ROUND_LIMIT,
    _ABS_MAX_SLICE_BUDGET,
    _as_int,
    _auto_scale_round_limit,
    _auto_scale_slice_budget,
    _behavior_slice_settings,
    _derive_slice_status,
    _load_persisted_slice_history,
    _persist_slice_ledger,
    _slice_ledger_path,
)


def _auth_value_present(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return value not in (None, "", [], {})


def _auth_acceptance_observed(body: Any, headers: dict[str, Any] | None = None, *, _depth: int = 0) -> bool:
    """True when an auth response actually accepts the login, not just HTTP 200."""

    if _depth == 0:
        for key, value in (headers or {}).items():
            key_l = str(key or "").strip().lower()
            if key_l in _AUTH_ACCEPTANCE_HEADER_TOKENS and str(value or "").strip():
                return True
    if _depth > 8:
        return False
    if isinstance(body, dict):
        for key, value in body.items():
            key_l = str(key or "").strip().lower().replace("-", "_")
            if any(token in key_l for token in _AUTH_ACCEPTANCE_KEY_TOKENS) and _auth_value_present(value):
                return True
            if key_l in _AUTH_SUCCESS_BOOL_KEYS and value is True:
                return True
            if key_l in _AUTH_PRINCIPAL_KEYS and isinstance(value, dict) and bool(value):
                return True
            if _auth_acceptance_observed(value, None, _depth=_depth + 1):
                return True
        return False
    if isinstance(body, list):
        return any(_auth_acceptance_observed(item, None, _depth=_depth + 1) for item in body[:20])
    if isinstance(body, str):
        return bool(_JWT_RE.search(body))
    return False


# Absolute safety clamps — the auto-scaler and any env override are bounded by
# these so a pathological pool size can never explode API cost without bound.

from .v12_legacy_schedule import (  # noqa: F401
    _POOL_COLLAPSE_KINDS,
    _POOL_ORIGIN_KEEP_RANK,
    _POOL_PROTECTED_KINDS,
    _SELECTION_KIND_RANK,
    _SELECTION_RESERVED_KINDS,
    _diverse_slice_batch_core,
    _entity_primary_slice_rank,
    _history_item_counts_as_attempted,
    _normalize_selection_family,
    _optimize_behavior_slice_pool,
    _prioritize_confirmed_state_variants,
    _rank_behavior_slices_for_selection,
    _scenario_selection_score,
    _schedule_behavior_slices,
    _selection_kind_rank,
    _selection_result,
    _slice_has_actor_credentials,
    _slice_has_source_executable_route,
    _slice_history,
    _slice_hypothesis_origin,
    _slice_is_pool_protected,
    _slice_is_selection_reserved,
    _slice_llm_invariant_collapse_key,
    _slice_pool_keep_score,
    _slice_route_collapse_key,
    _slice_selection_entity,
    _slice_selection_family,
    _take_diverse_slice_batch,
    clear_slice_reorder_hook,
    register_slice_reorder_hook,
)


def _campaign_identity_defaults(project: str, root: Path) -> dict[str, str]:
    from .enterprise_pilot_runtime import load_connector_registry

    registry = load_connector_registry(project, root)
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


def _campaign_candidate(project: str, prd_text: str, api_spec_text: str, db_schema_text: str, base_url: str, settings: dict[str, int], context: dict[str, Any], root: Path, submitted_api_spec_text: Any) -> EnterpriseCampaign:
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
    return EnterpriseCampaign.create(
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


def _campaign_context(project: str, prd_text: str, api_spec_text: str, db_schema_text: str, base_url: str, settings: dict[str, int], context: dict[str, Any], root: Path, submitted_api_spec_text: Any) -> tuple[EnterpriseCampaign, EnterpriseCampaignStore, str]:
    candidate = _campaign_candidate(
        project,
        prd_text,
        api_spec_text,
        db_schema_text,
        base_url,
        settings,
        context,
        root,
        submitted_api_spec_text,
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
    }


def _run_legacy_champion(
    inputs: Any,
    campaign_handle: Any,
    plan: Any,
) -> dict[str, Any]:
    """Fail closed: the frozen legacy implementation is not installed."""
    from .discovery_mainline_contract import MainlineContractError

    raise MainlineContractError("mainline_runner_unavailable:legacy_champion")


def _run_legacy_champion_domain(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Fail closed without redirecting legacy authority into candidate code."""
    from .discovery_mainline_contract import MainlineContractError

    raise MainlineContractError("mainline_runner_unavailable:legacy_champion")


def run_v12_pipeline(
    project: str,
    root: Path,
    prd_text: str = "",
    api_spec_text: str = "",
    db_schema_text: str = "",
    base_url: str = "",
    existing_findings: list[dict] | None = None,
    campaign_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility entry point backed by exactly one mainline coordinator."""

    from .discovery_mainline import DiscoveryMainlineInputs, run_discovery_mainline
    from .discovery_mainline_contract import MainlineContractError
    from .discovery_runtime import build_discovery_plan, run_experiment_candidate
    from .system_behavior_space_context import (
        reset_behavior_space_context,
        set_behavior_space_context,
    )

    context_token = set_behavior_space_context(str(project), Path(root))
    try:
        _reset_pipeline_har_entries()
        context = dict(campaign_context or {})
        _require_mainline_identity(context)
        submitted_api_spec_text = str(api_spec_text or "")
        context.setdefault("_source_verification_text", submitted_api_spec_text)
        source_verification_text = context["_source_verification_text"]
        _, source_issues = _source_manifest_details(context, source_verification_text)
        if source_issues:
            raise MainlineContractError(
                "source_identity_invalid:" + ",".join(sorted(source_issues))
            )
        normalized_api_spec_text, normalization = _normalize_executable_api_document(
            submitted_api_spec_text
        )
        normalization_failed = (
            str(normalization.get("status") or "").upper() == "FAILED_SAFE"
        )
        context["_campaign_api_spec_text"] = (
            submitted_api_spec_text if normalization_failed else normalized_api_spec_text
        )
        if normalization_failed:
            raise MainlineContractError(
                "api_document_normalization_failed:"
                f"{normalization.get('error_type') or normalization.get('reason') or 'UNKNOWN'}"
            )
        runtime_contract = _runtime_contract(context, base_url, submitted_api_spec_text)
        context["_runtime_contract"] = runtime_contract
        if not str(context.get("policy_id") or "").strip():
            context["policy_id"] = str(context.get("policy_version") or "").strip()
        if not str(context.get("strategy_fingerprint") or "").strip():
            from .policy_registry import strategy_fingerprint
            from .policy_wiring import get_effective_policy_strategy

            context["strategy_fingerprint"] = strategy_fingerprint(
                get_effective_policy_strategy()
            )
        if "adaptive_planning_history_receipt" not in context:
            from .adaptive_planning_history import (
                load_prior_planning_history_receipt,
            )

            context["adaptive_planning_history_receipt"] = (
                load_prior_planning_history_receipt(Path(root), str(project))
            )
        campaign_candidate = _campaign_candidate(
            str(project),
            str(prd_text or ""),
            str(context["_campaign_api_spec_text"]),
            str(db_schema_text or ""),
            str(runtime_contract.get("approved_base_url") or ""),
            _behavior_slice_settings(),
            context,
            Path(root),
            submitted_api_spec_text,
        )
        submitted_campaign_id = str(context.get("campaign_id") or "").strip()
        if submitted_campaign_id and submitted_campaign_id != campaign_candidate.campaign_id:
            raise MainlineContractError("mainline_campaign_identity_mismatch")
        context["campaign_id"] = campaign_candidate.campaign_id
        inputs = DiscoveryMainlineInputs(
            project=str(project),
            root=Path(root),
            prd_text=str(prd_text or ""),
            api_spec_text=normalized_api_spec_text,
            db_schema_text=str(db_schema_text or ""),
            approved_base_url=str(runtime_contract.get("approved_base_url") or ""),
            campaign_context=context,
            existing_findings=tuple(existing_findings or ()),
        )
        result = run_discovery_mainline(
            inputs,
            build_campaign=_build_mainline_campaign,
            build_plan=build_discovery_plan,
            legacy_runner=None,
            experiment_runner=run_experiment_candidate,
        )
        from .ui_execution_adapter import execute_ui_execution_requests

        ui_execution = execute_ui_execution_requests(
            str(project),
            context.get("ui_execution_requests"),
            _dict(result.get("runtime_contract")),
            root=Path(root),
            run_id=str(_dict(result.get("mainline_run")).get("run_id") or ""),
            execution_context=context,
        )
        if not isinstance(ui_execution, dict):
            raise MainlineContractError("ui_execution_result_not_object")
        discovery_round = int(
            context.get("discovery_round")
            or _behavior_slice_settings()["round_number"]
        )
        normalized_ui_findings, ui_evidence_graphs = (
            _normalize_ui_execution_findings(
                ui_execution,
                campaign_id=str(
                    _dict(result.get("mainline_run")).get("campaign_id") or ""
                ),
                discovery_round=discovery_round,
            )
        )
        result["ui_execution"] = ui_execution
        result["ui_findings"] = normalized_ui_findings
        result.setdefault("evidence_graphs", []).extend(ui_evidence_graphs)
        result.setdefault("phases", {})["ui_execution"] = {
            "status": str(ui_execution.get("status") or "not_requested"),
            "requested": int(ui_execution.get("requested") or 0),
            "executed": int(ui_execution.get("executed") or 0),
            "failed": int(ui_execution.get("failed") or 0),
            "blocked": int(ui_execution.get("blocked") or 0),
            "provider_distribution": dict(
                ui_execution.get("provider_distribution") or {}
            ),
            "findings": len(normalized_ui_findings),
            "duration_ms": int(ui_execution.get("duration_ms") or 0),
        }
        return result
    finally:
        reset_behavior_space_context(context_token)


# NOTE: _load_execution_safety_boundary now lives in enterprise_project_config.py
# (single source of truth shared with regression_runner) and is imported above.


from .v12_legacy_scenario_exec import (  # noqa: F401
    __execute_scenario_once,
    _append_blocked_write_step,
    _bind_runtime_amount_controls,
    _binding_value_for_key,
    _body_has_unbound_placeholders,
    _coerce_runtime_amount,
    _count_by,
    _declared_get_hints,
    _disposable_fixture_nonce,
    _encoded_request_url,
    _entity_candidates_from_response,
    _entity_matches_runtime_binding,
    _execute_scenario,
    _extract,
    _fill_path_aliases,
    _governed_write_block_reason,
    _identity_binding_keys,
    _is_money_control_step,
    _json_or_text,
    _materialize_disposable_identity_fixture_body,
    _observer_collection_fallback_paths,
    _project_bound_observer_entity,
    _read_observer_action,
    _redact,
    _replace,
    _resolve_get_candidates,
    _runtime_amount_binding,
    _summarize_execution_skip_telemetry,
)

from .v12_legacy_oracle_findings import (  # noqa: F401
    _compact_semantic_text,
    _confirmed_oracle_finding,
    _evidence_quality_score,
    _is_harness_support_step,
    _oracle_primary_step_gap,
    _oracle_semantic_signature,
    _runtime_contract_evidence_from_snapshot,
    _semantic_signature_terms,
    _semantic_v12_description,
    _semantic_v12_title,
    _status_confirmation_gap,
    _trace_before_after_snapshot,
    _trace_errors_block_runtime_confirmation,
    _trace_has_valid_success_control,
    _trace_primary_step,
)

from .v12_compat_helpers import (  # noqa: F401
    _append_runtime_contract_scenarios,
    _execution_approval_contract,
    _execution_trace_identity,
    _extract_api_operations_for_ir,
    _extract_runtime_actors_for_ir,
    _is_test_write_allowed,
    _knowledge_asset_planning_text,
    _maybe_start_behavior_contract_rerun,
    _persist_confirmed_findings,
    _publish_behavior_contract_snapshot,
    _record_pipeline_failure,
    _record_v12_har,
    _recover_stale_campaign_state,
    _redacted_execution_error,
    _redacted_execution_trace_graph,
    _redacted_trace_path,
    _redacted_trace_status,
    _reset_v12_har_entries,
    _scenario_executable,
    _test_write_fixture_prefix,
    _v12_har_report,
    is_v12_enabled,
)


def _normalize_ui_execution_findings(
    ui_execution: dict[str, Any] | None,
    *,
    campaign_id: str,
    discovery_round: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    execution = _dict(ui_execution)
    findings: list[dict[str, Any]] = []
    graphs: list[dict[str, Any]] = []
    for result in execution.get("results") if isinstance(execution.get("results"), list) else []:
        if not isinstance(result, dict):
            continue
        bridge_findings = result.get("findings") if isinstance(result.get("findings"), list) else []
        if bridge_findings:
            for item in bridge_findings:
                if not isinstance(item, dict):
                    continue
                normalized = _ui_bridge_finding(
                    item,
                    request_result=result,
                    campaign_id=campaign_id,
                    discovery_round=discovery_round,
                )
                findings.append(normalized)
                graphs.append(_ui_evidence_graph(normalized, result))
            continue
        status = str(result.get("status") or "")
        if status not in {"failed", "blocked"}:
            continue
        normalized = _ui_execution_status_finding(
            result,
            campaign_id=campaign_id,
            discovery_round=discovery_round,
        )
        findings.append(normalized)
        graphs.append(_ui_evidence_graph(normalized, result))
    return findings, graphs


def _normalize_ui_created_data(request_result: dict[str, Any]) -> dict[str, Any]:
    created_data = _dict(request_result.get("created_data"))
    if not created_data:
        return {}
    object_id = str(
        created_data.get("object_id")
        or created_data.get("entity_id")
        or created_data.get("resource_id")
        or created_data.get("id")
        or ""
    ).strip()
    object_type = str(
        created_data.get("object_type")
        or created_data.get("entity")
        or created_data.get("resource_type")
        or created_data.get("type")
        or ""
    ).strip()
    current_url = str(request_result.get("current_url") or request_result.get("start_url") or "").strip()
    object_url = str(created_data.get("object_url") or created_data.get("url") or current_url or "").strip()
    data_scope_ref = str(
        created_data.get("data_scope_ref")
        or created_data.get("scope_ref")
        or (f"{object_type}:{object_id}" if object_type and object_id else "")
    ).strip()
    normalized = dict(created_data)
    if object_id:
        normalized["object_id"] = object_id
    if object_type:
        normalized["object_type"] = object_type
    if data_scope_ref:
        normalized["data_scope_ref"] = data_scope_ref
    if object_url:
        normalized["object_url"] = object_url
    return normalized


def _ui_execution_evidence_payload(request_result: dict[str, Any]) -> dict[str, Any]:
    artifacts = request_result.get("artifacts") if isinstance(request_result.get("artifacts"), list) else []
    artifact_refs: list[str] = []
    artifact_types: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        ref = str(artifact.get("ref") or "").strip()
        if ref and ref not in artifact_refs:
            artifact_refs.append(ref)
        artifact_type = str(artifact.get("artifact_type") or "").strip()
        if artifact_type and artifact_type not in artifact_types:
            artifact_types.append(artifact_type)
    return {
        "request_id": str(request_result.get("request_id") or ""),
        "provider": str(request_result.get("provider") or ""),
        "bridge_provider": str(request_result.get("bridge_provider") or ""),
        "status": str(request_result.get("status") or ""),
        "reason": str(request_result.get("reason") or ""),
        "current_url": str(request_result.get("current_url") or request_result.get("start_url") or ""),
        "artifact_dir": str(request_result.get("artifact_dir") or ""),
        "artifact_refs": artifact_refs,
        "artifact_types": artifact_types,
        "history_count": len(request_result.get("history") if isinstance(request_result.get("history"), list) else []),
        "console_count": len(request_result.get("console") if isinstance(request_result.get("console"), list) else []),
        "network_count": len(request_result.get("network") if isinstance(request_result.get("network"), list) else []),
        "metadata": dict(request_result.get("metadata") or {}) if isinstance(request_result.get("metadata"), dict) else {},
    }


def _ui_bridge_finding(
    item: dict[str, Any],
    *,
    request_result: dict[str, Any],
    campaign_id: str,
    discovery_round: int,
) -> dict[str, Any]:
    finding = dict(item)
    created_data = _normalize_ui_created_data(request_result)
    ui_execution_result = _ui_execution_evidence_payload(request_result)
    finding.setdefault("severity", "P2")
    finding.setdefault("title", f"[UI] {str(request_result.get('title') or request_result.get('request_id') or 'ui_request')[:120]}")
    finding.setdefault("category", "ui_execution")
    finding.setdefault("source", "ui_execution_bridge")
    finding.setdefault("description", str(request_result.get("reason") or "ui_execution_signal"))
    finding.setdefault("confidence_score", 0.7)
    finding.setdefault("campaign_id", campaign_id)
    finding.setdefault("discovery_round", discovery_round)
    finding.setdefault("execution_status", "executed" if str(request_result.get("status") or "") == "executed" else str(request_result.get("status") or "not_executed"))
    finding.setdefault("confirmation_status", "candidate")
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    evidence.setdefault("request", str(request_result.get("task") or request_result.get("title") or "ui_request"))
    evidence.setdefault("response", str(request_result.get("status") or ""))
    evidence.setdefault("target", str(request_result.get("current_url") or request_result.get("start_url") or ""))
    evidence.setdefault("ui_artifacts", request_result.get("artifacts") if isinstance(request_result.get("artifacts"), list) else [])
    evidence.setdefault("reproduction_steps", [str(request_result.get("task") or request_result.get("title") or "ui_request")])
    finding["evidence"] = evidence
    finding.setdefault(
        "raw_evidence",
        {
            "has_real_evidence": bool(
                ui_execution_result.get("artifact_dir")
                or ui_execution_result.get("current_url")
                or ui_execution_result.get("artifact_refs")
                or created_data
            ),
            "ui_execution_result": ui_execution_result,
            "created_data": created_data,
        },
    )
    return finding


def _ui_execution_status_finding(
    request_result: dict[str, Any],
    *,
    campaign_id: str,
    discovery_round: int,
) -> dict[str, Any]:
    status = str(request_result.get("status") or "blocked")
    request_id = str(request_result.get("request_id") or "ui_request")
    title = str(request_result.get("title") or request_id or "ui_request")
    current_url = str(request_result.get("current_url") or request_result.get("start_url") or "")
    severity = "P1" if status == "failed" else "P2"
    created_data = _normalize_ui_created_data(request_result)
    ui_execution_result = _ui_execution_evidence_payload(request_result)
    return {
        "severity": severity,
        "title": f"[UI Execution {status.upper()}] {title[:120]}",
        "category": "ui_execution",
        "source": "ui_execution_adapter",
        "description": str(request_result.get("reason") or f"ui_execution_{status}"),
        "confidence_score": 0.6 if status == "failed" else 0.45,
        "campaign_id": campaign_id,
        "discovery_round": discovery_round,
        "execution_status": status,
        "confirmation_status": "candidate",
        "evidence": {
            "request": str(request_result.get("task") or title),
            "response": status,
            "target": current_url,
            "ui_artifacts": request_result.get("artifacts") if isinstance(request_result.get("artifacts"), list) else [],
            "reproduction_steps": [str(request_result.get("task") or title)],
        },
        "raw_evidence": {
            "has_real_evidence": bool(current_url or ui_execution_result.get("artifact_dir") or ui_execution_result.get("artifact_refs") or created_data),
            "ui_execution_result": ui_execution_result,
            "created_data": created_data,
        },
    }


def _ui_evidence_graph(finding: dict[str, Any], request_result: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request_result.get("request_id") or "ui_request")
    current_url = str(request_result.get("current_url") or request_result.get("start_url") or "")
    history = request_result.get("history") if isinstance(request_result.get("history"), list) else []
    console = request_result.get("console") if isinstance(request_result.get("console"), list) else []
    network = request_result.get("network") if isinstance(request_result.get("network"), list) else []
    return {
        "bug_id": f"UI_{request_id}",
        "title": str(finding.get("title") or request_id),
        "severity": str(finding.get("severity") or "P2"),
        "confidence": float(finding.get("confidence_score") or 0.0),
        "scenario": {
            "id": request_id,
            "category": "ui_execution",
            "title": str(request_result.get("title") or request_id),
            "provider": str(request_result.get("provider") or ""),
            "task": str(request_result.get("task") or ""),
        },
        "request_chain": [{"url": current_url, "task": str(request_result.get("task") or "")}],
        "response_chain": [{"status": str(request_result.get("status") or ""), "reason": str(request_result.get("reason") or "")}],
        "state_diff": {},
        "execution_trace": {
            "current_url": current_url,
            "artifact_dir": str(request_result.get("artifact_dir") or ""),
            "history": history,
            "console": console,
            "network": network,
        },
        "before_snapshot": {},
        "after_snapshot": {},
        "oracle_results": [],
        "reproduction_steps": "\n".join(finding.get("evidence", {}).get("reproduction_steps", []) if isinstance(finding.get("evidence"), dict) else []),
        "evidence_id": str(finding.get("evidence_id") or f"ui_evidence_{request_id}"),
        "layers_triggered": ["UI"],
        "vote_summary": {
            "total_votes": 1,
            "failed_votes": 1,
            "passed_votes": 0,
            "failure_weight": 1.0,
            "total_weight": 1.0,
            "confirmation_threshold_met": False,
        },
    }


