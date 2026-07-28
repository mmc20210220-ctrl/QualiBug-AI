"""Bind approved upload fixtures into the private-pilot runtime contract.

The browser executor consumes ``runtime_contract.ui_file_bindings``.  This module
makes that field registry-derived rather than caller-authored:

* scan callers may submit ``ui_upload_fixture_ids`` containing approved fixture ids
  or binding refs;
* any explicit ``ui_file_bindings`` must exactly match active registry records;
* campaign context and the final pipeline runtime contract retain only canonical
  project-relative bindings;
* loaded aliases are rebound so the patch is effective even when the service
  composition modules imported their helpers earlier.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from . import pipeline_runtime as _pipeline_runtime
from . import private_pilot_scan_context_contract as _scan_context
from . import private_pilot_scan_prep as _scan_prep
from .ui_upload_fixture_registry import (
    MAX_FIXTURES_PER_BINDING_REQUEST,
    approved_upload_fixture_binding,
    materialize_upload_fixture_bindings,
)

_INSTALL_MARKER = "_qualibug_ui_upload_fixture_runtime_binding_installed"
_ORIGINAL_PREPARE = "_qualibug_scan_prepare_before_upload_fixture_binding"
_ORIGINAL_CONTEXT = "_qualibug_campaign_context_before_upload_fixture_binding"
_ORIGINAL_RUNTIME = "_qualibug_runtime_contract_before_upload_fixture_binding"
_CANONICAL_BINDING_FIELDS = frozenset({
    "approved",
    "status",
    "fixture_id",
    "binding_ref",
    "file_path",
    "sha256",
    "size_bytes",
    "content_type",
    "raw_file_content_included",
    "raw_source_path_included",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _fixture_identities(body: dict[str, Any]) -> list[str]:
    if "ui_upload_fixture_ids" not in body:
        return []
    raw = body.get("ui_upload_fixture_ids")
    if not isinstance(raw, list):
        raise ValueError("ui_upload_fixture_ids_not_list")
    values: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise ValueError(f"ui_upload_fixture_identity_not_string:{index}")
        identity = _text(value, limit=160)
        if not identity:
            raise ValueError(f"ui_upload_fixture_identity_empty:{index}")
        if identity not in values:
            values.append(identity)
    if len(values) > MAX_FIXTURES_PER_BINDING_REQUEST:
        raise ValueError("ui_upload_fixture_binding_request_limit_exceeded")
    return values


def _canonical_explicit_bindings(
    project: str,
    root: Path,
    value: Any,
) -> dict[str, dict[str, Any]]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError("ui_file_bindings_not_object")
    canonical: dict[str, dict[str, Any]] = {}
    for submitted_key, raw in value.items():
        key = _text(submitted_key, limit=160)
        row = _dict(raw)
        if not key or not row:
            raise ValueError("ui_file_binding_entry_invalid")
        unknown = set(row) - _CANONICAL_BINDING_FIELDS
        if unknown:
            raise ValueError(
                "ui_file_binding_untrusted_fields:" + ",".join(sorted(unknown))
            )
        identity = _text(row.get("fixture_id") or row.get("binding_ref"), limit=160)
        if not identity:
            raise ValueError("ui_file_binding_registry_identity_missing")
        resolved = approved_upload_fixture_binding(project, identity, root=root)
        binding_ref = _text(resolved.get("binding_ref"), limit=160)
        if key != binding_ref:
            raise ValueError("ui_file_binding_key_must_equal_registry_binding_ref")
        comparable_submitted = {
            field: row.get(field)
            for field in _CANONICAL_BINDING_FIELDS
            if field in row
        }
        comparable_resolved = {
            field: resolved.get(field)
            for field in comparable_submitted
        }
        if comparable_submitted != comparable_resolved:
            raise ValueError("ui_file_binding_registry_identity_mismatch")
        canonical[binding_ref] = resolved
    return canonical


def _hydrate_bindings(project: str, root: Path, body: dict[str, Any]) -> dict[str, Any]:
    prepared = copy.deepcopy(_dict(body))
    identities = _fixture_identities(prepared)
    registry_bindings = materialize_upload_fixture_bindings(
        project,
        identities,
        root=root,
    ) if identities else {}
    explicit_bindings = _canonical_explicit_bindings(
        project,
        root,
        prepared.get("ui_file_bindings"),
    )
    merged = dict(explicit_bindings)
    for key, binding in registry_bindings.items():
        if key in merged and merged[key] != binding:
            raise ValueError("ui_upload_fixture_binding_conflict")
        merged[key] = binding
    if merged:
        prepared["ui_file_bindings"] = merged
        prepared["ui_upload_fixture_binding_summary"] = {
            "schema_version": "qualibug.ui-upload-fixture-binding-summary.v1",
            "binding_count": len(merged),
            "binding_refs": sorted(merged),
            "registry_derived": True,
            "raw_file_content_included": False,
            "absolute_file_paths_included": False,
        }
    else:
        prepared.pop("ui_file_bindings", None)
        prepared.pop("ui_upload_fixture_binding_summary", None)
    return prepared


def install_ui_upload_fixture_runtime_binding() -> None:
    if getattr(_scan_prep, _INSTALL_MARKER, False):
        return
    original_prepare = getattr(
        _scan_prep,
        _ORIGINAL_PREPARE,
        _scan_prep._prepare_v12_scan_body,
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
    setattr(_scan_context, _ORIGINAL_CONTEXT, original_context)
    setattr(_pipeline_runtime, _ORIGINAL_RUNTIME, original_runtime)

    def prepare_with_upload_fixture_bindings(
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
        return _hydrate_bindings(project, Path(root), prepared)

    def campaign_context_with_upload_fixture_bindings(
        body: dict[str, Any],
    ) -> dict[str, Any]:
        context = copy.deepcopy(original_context(body))
        bindings = _dict(body.get("ui_file_bindings"))
        if bindings:
            context["ui_file_bindings"] = copy.deepcopy(bindings)
            context["ui_upload_fixture_binding_summary"] = copy.deepcopy(
                _dict(body.get("ui_upload_fixture_binding_summary"))
            )
        return context

    def runtime_contract_with_upload_fixture_bindings(
        context: dict[str, Any],
        base_url: str,
        source_text: Any,
    ) -> dict[str, Any]:
        contract = copy.deepcopy(original_runtime(context, base_url, source_text))
        bindings = _dict(context.get("ui_file_bindings"))
        if bindings:
            contract["ui_file_bindings"] = copy.deepcopy(bindings)
            contract["ui_upload_fixture_binding_summary"] = copy.deepcopy(
                _dict(context.get("ui_upload_fixture_binding_summary"))
            )
        return contract

    _scan_prep._prepare_v12_scan_body = prepare_with_upload_fixture_bindings
    _scan_context.build_campaign_context_from_scan_body = (
        campaign_context_with_upload_fixture_bindings
    )
    _pipeline_runtime._runtime_contract = runtime_contract_with_upload_fixture_bindings

    scan_handlers = sys.modules.get("ai_test_asset_center.private_pilot_scan_handlers")
    if scan_handlers is not None and getattr(
        scan_handlers,
        "_prepare_v12_scan_body",
        None,
    ) is original_prepare:
        scan_handlers._prepare_v12_scan_body = prepare_with_upload_fixture_bindings

    pipeline = sys.modules.get("ai_test_asset_center.v12_pipeline")
    if pipeline is not None and getattr(pipeline, "_runtime_contract", None) is original_runtime:
        pipeline._runtime_contract = runtime_contract_with_upload_fixture_bindings

    setattr(_scan_prep, _INSTALL_MARKER, True)


__all__ = [
    "install_ui_upload_fixture_runtime_binding",
]
