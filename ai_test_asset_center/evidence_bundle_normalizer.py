from __future__ import annotations

"""Normalize real-project evidence bundles for the main enterprise chain.

This module does not invent proof. It only standardizes fields that already exist
in discovery artifacts or can be derived from captured execution receipts.
The goal is to make `evidence_bundle.json` consistently satisfy the strict main
chain contract when the underlying runtime proof is actually present.
"""

import json
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _safe_project_id, _write_json, config_paths


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null")
    except Exception:
        return fallback


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _first_record(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return dict(value)
    return {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _index_by(items: list[dict[str, Any]], *keys: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        for key in keys:
            value = _text(item.get(key))
            if value and value not in result:
                result[value] = item
    return result


def _request_from(item: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    return _first_record(
        item.get("request"),
        item.get("request_raw"),
        item.get("raw_request"),
        item.get("har_request"),
        execution.get("request"),
        execution.get("request_raw"),
        execution.get("raw_request"),
    )


def _response_from(item: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    return _first_record(
        item.get("response"),
        item.get("response_raw"),
        item.get("raw_response"),
        item.get("har_response"),
        execution.get("response"),
        execution.get("response_raw"),
        execution.get("raw_response"),
    )


def _expected_from(item: dict[str, Any], issue: dict[str, Any]) -> Any:
    return item.get("expected") if item.get("expected") is not None else issue.get("expected")


def _actual_from(item: dict[str, Any], issue: dict[str, Any]) -> Any:
    return item.get("actual") if item.get("actual") is not None else issue.get("actual")


def _match_execution(item: dict[str, Any], executions_by_probe: dict[str, dict[str, Any]], executions_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    execution_id = _text(item.get("execution_id") or item.get("trace_id"))
    if execution_id and execution_id in executions_by_id:
        return executions_by_id[execution_id]
    probe_id = _text(item.get("probe_id"))
    if probe_id and probe_id in executions_by_probe:
        return executions_by_probe[probe_id]
    return {}


def _build_reproduction(item: dict[str, Any], request: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    existing = _first_record(item.get("reproduction"), item.get("replay"))
    if existing:
        return existing
    if not request or not execution:
        return {}
    method = request.get("method") or execution.get("method")
    path = request.get("path") or request.get("url") or execution.get("path") or execution.get("url")
    if not method and not path:
        return {}
    return {
        "method": method,
        "path": path,
        "is_synthetic": False,
        "derived_from": "captured_execution_receipt",
    }


def _has_execution_receipt(item: dict[str, Any]) -> bool:
    return any((
        item.get("execution_id"),
        item.get("probe_id"),
        item.get("trace_id"),
        item.get("timestamp"),
        item.get("captured_at"),
        item.get("duration_seconds") is not None,
        item.get("response_status") is not None,
    ))


def _missing_fields(item: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not item.get("issue_id"):
        missing.append("issue_id")
    if not isinstance(item.get("request"), dict):
        missing.append("request")
    if not isinstance(item.get("response"), dict):
        missing.append("response")
    if item.get("expected") is None:
        missing.append("expected")
    if item.get("actual") is None:
        missing.append("actual")
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    replay = item.get("replay") if isinstance(item.get("replay"), dict) else {}
    proof = item.get("proof") if isinstance(item.get("proof"), dict) else {}
    if not reproduction and not replay and proof.get("can_reproduce") is not True:
        missing.append("reproduction_or_replay")
    if not _has_execution_receipt(item):
        missing.append("execution_receipt")
    if item.get("is_synthetic") is True or reproduction.get("is_synthetic") is True or replay.get("is_synthetic") is True or proof.get("is_synthetic") is True:
        missing.append("non_synthetic_evidence")
    return missing


def _normalize_item(item: dict[str, Any], issue: dict[str, Any], execution: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = dict(item)
    request = _request_from(item, execution)
    response = _response_from(item, execution)
    expected = _expected_from(item, issue)
    actual = _actual_from(item, issue)
    reproduction = _build_reproduction(item, request, execution)

    if request:
        normalized["request"] = request
    if response:
        normalized["response"] = response
    if expected is not None:
        normalized["expected"] = expected
    if actual is not None:
        normalized["actual"] = actual
    if reproduction:
        normalized["reproduction"] = reproduction
    if execution:
        for key in ("probe_id", "execution_id", "trace_id", "timestamp", "captured_at", "duration_seconds", "response_status"):
            if normalized.get(key) in (None, "") and execution.get(key) not in (None, ""):
                normalized[key] = execution.get(key)
    if normalized.get("is_synthetic") is None and execution and request and response:
        normalized["is_synthetic"] = False

    missing = _missing_fields(normalized)
    return normalized, {
        "issue_id": _text(normalized.get("issue_id")),
        "probe_id": _text(normalized.get("probe_id")),
        "normalized": len(missing) == 0,
        "missing_fields": missing,
    }


def normalize_evidence_bundle(project_id: str = "real_project_demo", root: Path | None = None, *, persist: bool = True) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    workspace_dir = paths["workspace_dir"]
    output_dir = paths["output_dir"]
    evidence_path = output_dir / "evidence_bundle.json"

    evidence_payload = _read_json(evidence_path, {})
    evidence_items = _items(evidence_payload)
    issues = _items(_read_json(output_dir / "discovered_issues.json", {}))
    executions = _items(_read_json(workspace_dir / "probe_execution_result.json", {}))

    issues_by_id = _index_by(issues, "issue_id", "id")
    executions_by_probe = _index_by(executions, "probe_id")
    executions_by_id = _index_by(executions, "execution_id", "trace_id", "id")

    normalized_items: list[dict[str, Any]] = []
    item_reports: list[dict[str, Any]] = []
    for item in evidence_items:
        issue_id = _text(item.get("issue_id") or item.get("id"))
        issue = issues_by_id.get(issue_id, {})
        execution = _match_execution(item, executions_by_probe, executions_by_id)
        normalized, report = _normalize_item(item, issue, execution)
        normalized_items.append(normalized)
        item_reports.append(report)

    normalized_payload = dict(evidence_payload) if isinstance(evidence_payload, dict) else {}
    normalized_payload["items"] = normalized_items
    normalized_payload["normalization"] = {
        "normalized_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "normalizer": "ai_test_asset_center.evidence_bundle_normalizer",
        "item_count": len(normalized_items),
        "fully_normalized_count": sum(1 for item in item_reports if item["normalized"]),
    }
    report = {
        "phase": "evidence_bundle_normalization",
        "project_id": project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_item_count": len(evidence_items),
        "output_item_count": len(normalized_items),
        "fully_normalized_count": sum(1 for item in item_reports if item["normalized"]),
        "items": item_reports,
        "rules": [
            "preserve existing proof",
            "standardize request/response aliases",
            "copy expected/actual from linked issue when already present there",
            "copy execution receipt fields from matched probe execution",
            "derive replay only from captured execution receipts",
            "do not invent validated evidence",
        ],
    }
    if persist:
        output_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        _write_json(evidence_path, normalized_payload)
        _write_json(output_dir / "evidence_bundle_normalization_report.json", report)
        _write_json(workspace_dir / "evidence_bundle_normalization_report.json", report)
    return report
