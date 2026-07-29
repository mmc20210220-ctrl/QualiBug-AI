"""Hydrate approved UI upload scenarios into private-pilot scan contracts.

Callers submit only ``ui_upload_scenario_ids``. The registry is re-read for every
manual or continuous scan; approved scenarios become source-bound
``ui_execution_requests`` and their fixture bindings are merged into the existing
upload-fixture authority. Caller-authored requests are preserved, never replaced.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from . import pipeline_runtime as _pipeline_runtime
from . import private_pilot_scan_context_contract as _scan_context
from . import private_pilot_scan_prep as _scan_prep
from .ui_upload_scenario_registry import (
    MAX_SCENARIOS_PER_RUN,
    materialize_upload_scenarios,
)

_INSTALL_MARKER = "_qualibug_ui_upload_scenario_runtime_binding_installed"
_ORIGINAL_PREPARE = "_qualibug_scan_prepare_before_upload_scenario_binding"
_ORIGINAL_CAMPAIGN_PREPARE = (
    "_qualibug_campaign_prepare_before_upload_scenario_binding"
)
_ORIGINAL_CONTEXT = "_qualibug_campaign_context_before_upload_scenario_binding"
_ORIGINAL_RUNTIME = "_qualibug_runtime_contract_before_upload_scenario_binding"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _scenario_identities(body: dict[str, Any]) -> list[str]:
    if "ui_upload_scenario_ids" not in body:
        return []
    raw = body.get("ui_upload_scenario_ids")
    if not isinstance(raw, list):
        raise ValueError("ui_upload_scenario_ids_not_list")
    values: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise ValueError(f"ui_upload_scenario_identity_not_string:{index}")
        identity = _text(value, limit=160)
        if not identity:
            raise ValueError(f"ui_upload_scenario_identity_empty:{index}")
        if identity not in values:
            values.append(identity)
    if len(values) > MAX_SCENARIOS_PER_RUN:
        raise ValueError("ui_upload_scenario_run_limit_exceeded")
    return values


def _request_identity(request: dict[str, Any]) -> str:
    return _text(request.get("request_id") or request.get("id"), limit=160)


def _hydrate_scenarios(
    project: str,
    root: Path,
    body: dict[str, Any],
) -> dict[str, Any]:
    prepared = copy.deepcopy(_dict(body))
    identities = _scenario_identities(prepared)
    if not identities:
        prepared.pop("ui_upload_scenario_binding_summary", None)
        return prepared
    materialized = materialize_upload_scenarios(
        project, identities, root=Path(root)
    )
    existing_requests = [
        copy.deepcopy(row)
        for row in _list(prepared.get("ui_execution_requests"))
        if isinstance(row, dict)
    ]
    requests_by_id = {
        _request_identity(row): row
        for row in existing_requests
        if _request_identity(row)
    }
    fixture_refs = [
        _text(value, limit=160)
        for value in _list(prepared.get("ui_upload_fixture_ids"))
        if _text(value, limit=160)
    ]
    scenario_refs: list[str] = []
    source_ids: list[str] = []
    scenario_request_ids: list[str] = []
    for row in materialized:
        request = copy.deepcopy(_dict(row.get("ui_execution_request")))
        request_id = _request_identity(request)
        if not request_id:
            raise RuntimeError("ui_upload_scenario_request_identity_missing")
        existing = requests_by_id.get(request_id)
        if existing is not None and existing != request:
            raise ValueError("ui_upload_scenario_request_identity_conflict")
        if existing is None:
            existing_requests.append(request)
            requests_by_id[request_id] = request
        scenario_request_ids.append(request_id)
        for fixture_ref in _list(row.get("fixture_binding_refs")):
            ref = _text(fixture_ref, limit=160)
            if ref and ref not in fixture_refs:
                fixture_refs.append(ref)
        scenario_ref = _text(row.get("scenario_ref"), limit=160)
        if scenario_ref:
            scenario_refs.append(scenario_ref)
        source_id = _text(row.get("source_id"), limit=160)
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
    prepared["ui_execution_requests"] = existing_requests
    prepared["ui_upload_fixture_ids"] = fixture_refs
    prepared["ui_upload_scenario_binding_summary"] = {
        "schema_version": "qualibug.ui-upload-scenario-binding-summary.v1",
        "scenario_count": len(materialized),
        "scenario_refs": sorted(scenario_refs),
        "request_ids": sorted(set(scenario_request_ids)),
        "source_ids": sorted(source_ids),
        "fixture_binding_count": len(fixture_refs),
        "registry_derived": True,
        "raw_fixture_paths_included": False,
        "raw_fixture_content_included": False,
    }
    return prepared


def install_ui_upload_scenario_runtime_binding() -> None:
    if getattr(_scan_prep, _INSTALL_MARKER, False):
        return
    original_prepare = getattr(
        _scan_prep,
        _ORIGINAL_PREPARE,
        _scan_prep._prepare_v12_scan_body,
    )
    original_campaign_prepare = getattr(
        _scan_context,
        _ORIGINAL_CAMPAIGN_PREPARE,
        _scan_context.prepare_scan_body_for_campaign,
    )
    original_context = getattr(
        _scan_context,
        _ORIGINAL_CONTEXT,
        _scan_context.build_campaign_context_from_scan_body,
    )
    original_runtime = getattr(
        _pipeline_runtime,
        _ORIGINAL_RUNTIME,
        _pipeline_runtime._runtime_contract,
    )
    setattr(_scan_prep, _ORIGINAL_PREPARE, original_prepare)
    setattr(
        _scan_context,
        _ORIGINAL_CAMPAIGN_PREPARE,
        original_campaign_prepare,
    )
    setattr(_scan_context, _ORIGINAL_CONTEXT, original_context)
    setattr(_pipeline_runtime, _ORIGINAL_RUNTIME, original_runtime)

    def prepare_with_upload_scenarios(
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
        *,
        local_dev_mode: bool,
    ) -> dict[str, Any]:
        prepared = original_prepare(
            project,
            root,
            actor,
            body,
            local_dev_mode=local_dev_mode,
        )
        return _hydrate_scenarios(project, Path(root), prepared)

    def campaign_prepare_with_upload_scenarios(
        project: str,
        root: Path,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = original_campaign_prepare(project, root, body)
        return _hydrate_scenarios(project, Path(root), prepared)

    def campaign_context_with_upload_scenarios(
        body: dict[str, Any],
    ) -> dict[str, Any]:
        context = copy.deepcopy(original_context(body))
        summary = _dict(body.get("ui_upload_scenario_binding_summary"))
        if summary:
            context["ui_upload_scenario_binding_summary"] = copy.deepcopy(summary)
        return context

    def runtime_contract_with_upload_scenarios(
        context: dict[str, Any],
        base_url: str,
        source_text: Any,
    ) -> dict[str, Any]:
        contract = copy.deepcopy(original_runtime(context, base_url, source_text))
        summary = _dict(context.get("ui_upload_scenario_binding_summary"))
        if summary:
            contract["ui_upload_scenario_binding_summary"] = copy.deepcopy(summary)
        return contract

    _scan_prep._prepare_v12_scan_body = prepare_with_upload_scenarios
    _scan_context.prepare_scan_body_for_campaign = (
        campaign_prepare_with_upload_scenarios
    )
    _scan_context.build_campaign_context_from_scan_body = (
        campaign_context_with_upload_scenarios
    )
    _pipeline_runtime._runtime_contract = runtime_contract_with_upload_scenarios

    scan_handlers = sys.modules.get("ai_test_asset_center.private_pilot_scan_handlers")
    if scan_handlers is not None and getattr(
        scan_handlers,
        "_prepare_v12_scan_body",
        None,
    ) is original_prepare:
        scan_handlers._prepare_v12_scan_body = prepare_with_upload_scenarios

    continuous_handlers = sys.modules.get(
        "ai_test_asset_center.private_pilot_continuous_handlers"
    )
    if continuous_handlers is not None and getattr(
        continuous_handlers,
        "prepare_scan_body_for_campaign",
        None,
    ) is original_campaign_prepare:
        continuous_handlers.prepare_scan_body_for_campaign = (
            campaign_prepare_with_upload_scenarios
        )

    pipeline = sys.modules.get("ai_test_asset_center.v12_pipeline")
    if pipeline is not None and getattr(
        pipeline,
        "_runtime_contract",
        None,
    ) is original_runtime:
        pipeline._runtime_contract = runtime_contract_with_upload_scenarios

    setattr(_scan_prep, _INSTALL_MARKER, True)


__all__ = [
    "install_ui_upload_scenario_runtime_binding",
]
