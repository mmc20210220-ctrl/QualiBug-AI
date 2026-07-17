"""UI candidate gating and verification helpers for product scans.

Extracted from ``__main__``. Symbols are re-exported for compatibility.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import urlparse

from .product_scan_mainline import _as_dict, _first_text, _safe_project
from .scan_ui_followup_assets import _normalize_ui_verification_http_path

def _ui_candidate_gate(items: Any) -> list[dict[str, Any]]:
    gated: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        raw = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else {}
        ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
        current_url = str(ui_result.get("current_url") or evidence.get("target") or "").strip()
        artifacts = evidence.get("ui_artifacts") if isinstance(evidence.get("ui_artifacts"), list) else []
        steps = evidence.get("reproduction_steps") if isinstance(evidence.get("reproduction_steps"), list) else []
        status = str(row.get("execution_status") or ui_result.get("status") or "").strip().lower()
        has_real_evidence = raw.get("has_real_evidence") is True
        passes_gate = has_real_evidence and bool(current_url or artifacts) and bool(steps) and status in {"executed", "failed", "blocked"}
        row["ui_candidate_gate"] = {
            "passed": passes_gate,
            "has_real_evidence": has_real_evidence,
            "has_target": bool(current_url),
            "artifact_count": len(artifacts),
            "reproduction_step_count": len(steps),
        }
        if not passes_gate:
            continue
        row.setdefault("execution_status", status or "not_executed")
        row["confirmation_status"] = "candidate"
        row.setdefault("source", "ui_execution_adapter")
        gated.append(row)
    return gated


def _template_string(template: str, values: dict[str, Any]) -> str:
    text = str(template or "")
    for key, value in values.items():
        text = text.replace("{" + str(key) + "}", str(value or ""))
    return text


def _ui_verification_context(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    raw = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    created_data = raw.get("created_data") if isinstance(raw.get("created_data"), dict) else {}
    target = str(ui_result.get("current_url") or evidence.get("target") or "").strip()
    parsed = urlparse(target) if target else None
    artifact_refs = [
        str(item).strip()
        for item in (
            ui_result.get("artifact_refs")
            if isinstance(ui_result.get("artifact_refs"), list)
            else []
        )
        if str(item).strip()
    ]
    reproduction_steps = evidence.get("reproduction_steps") if isinstance(evidence.get("reproduction_steps"), list) else []
    return {
        "current_url": target,
        "path": parsed.path if parsed else "",
        "object_id": str(created_data.get("object_id") or ""),
        "object_type": str(created_data.get("object_type") or ""),
        "data_scope_ref": str(created_data.get("data_scope_ref") or ""),
        "object_url": str(created_data.get("object_url") or ""),
        "request_id": str(ui_result.get("request_id") or ""),
        "artifact_refs": artifact_refs,
        "artifact_count": len(artifact_refs),
        "reproduction_step_count": len(reproduction_steps),
        "execution_status": str(row.get("execution_status") or ui_result.get("status") or "").strip().lower(),
        "bridge_provider": str(ui_result.get("bridge_provider") or ui_result.get("provider") or "").strip(),
    }


def _verify_ui_candidate_http(config: dict[str, Any], values: dict[str, Any], runtime_contract: dict[str, Any]) -> dict[str, Any]:
    base_url = str(runtime_contract.get("approved_base_url") or "").strip().rstrip("/")
    path_template = str(config.get("path") or config.get("url") or "").strip()
    target = _template_string(path_template, values)
    if not target:
        return {"status": "skipped", "reason": "verification_http_target_missing"}
    if target.startswith("/"):
        if not base_url:
            return {"status": "skipped", "reason": "verification_base_url_missing"}
        target = base_url + target
    timeout_ms = int(config.get("timeout_ms") or 5000)
    expected_statuses = {int(x) for x in (config.get("expected_statuses") or [200]) if str(x).strip()}
    try:
        req = urllib_request.Request(target, method="GET", headers={"Accept": "application/json"})
        with urllib_request.urlopen(req, timeout=max(timeout_ms, 1000) / 1000.0) as response:
            body = response.read().decode("utf-8", errors="replace")
            status_code = int(getattr(response, "status", 200) or 200)
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status_code = int(exc.code or 500)
    except Exception as exc:
        return {"status": "failed", "reason": f"verification_http_error:{type(exc).__name__}", "target": target}
    body_json: Any = None
    try:
        body_json = json.loads(body) if body else None
    except Exception:
        body_json = None
    matches = True
    contains = str(config.get("body_contains") or "").strip()
    if contains:
        matches = contains in body
    return {
        "status": "verified" if status_code in expected_statuses and matches else "mismatch",
        "reason": "http_status_and_body_match" if status_code in expected_statuses and matches else "http_expectation_not_met",
        "target": target,
        "status_code": status_code,
        "body_excerpt": body[:500],
        "body_json": body_json if isinstance(body_json, (dict, list)) else None,
    }


def _verify_ui_candidate_sqlite(config: dict[str, Any], values: dict[str, Any], root: Path) -> dict[str, Any]:
    db_path_template = str(config.get("db_path") or "").strip()
    query_template = str(config.get("query") or "").strip()
    if not db_path_template or not query_template:
        return {"status": "skipped", "reason": "verification_sqlite_config_missing"}
    db_path = Path(_template_string(db_path_template, values))
    if not db_path.is_absolute():
        db_path = root / db_path
    if not db_path.exists():
        return {"status": "failed", "reason": "verification_sqlite_db_missing", "db_path": str(db_path)}
    query = _template_string(query_template, values)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
        conn.close()
    except Exception as exc:
        return {"status": "failed", "reason": f"verification_sqlite_error:{type(exc).__name__}", "db_path": str(db_path)}
    min_rows = int(config.get("min_rows") or 1)
    preview = [dict(row) for row in rows[:3]]
    return {
        "status": "verified" if len(rows) >= min_rows else "mismatch",
        "reason": "sqlite_row_match" if len(rows) >= min_rows else "sqlite_row_count_below_threshold",
        "db_path": str(db_path),
        "row_count": len(rows),
        "rows_preview": preview,
    }


def _verify_ui_candidate_execution_evidence(values: dict[str, Any]) -> dict[str, Any]:
    current_url = str(values.get("current_url") or "").strip()
    object_url = str(values.get("object_url") or "").strip()
    object_id = str(values.get("object_id") or "").strip()
    object_type = str(values.get("object_type") or "").strip()
    data_scope_ref = str(values.get("data_scope_ref") or "").strip()
    bridge_provider = str(values.get("bridge_provider") or "").strip()
    status = str(values.get("execution_status") or "").strip().lower()
    artifact_refs = values.get("artifact_refs") if isinstance(values.get("artifact_refs"), list) else []
    artifact_count = int(values.get("artifact_count") or len(artifact_refs) or 0)
    reproduction_step_count = int(values.get("reproduction_step_count") or 0)
    signals: list[str] = []
    if bridge_provider == "page_agent_browser_plan":
        signals.append("page_agent_browser_plan")
    if current_url:
        signals.append("current_url_present")
    if artifact_count > 0:
        signals.append("artifact_present")
    if reproduction_step_count > 0:
        signals.append("reproduction_steps_present")
    if object_url and current_url and object_url == current_url:
        signals.append("current_url_matches_object_url")
    if object_id and current_url and object_id in current_url:
        signals.append("current_url_contains_object_id")
    if object_id and data_scope_ref and object_id in data_scope_ref:
        signals.append("data_scope_ref_contains_object_id")
    object_binding_verified = bool(
        object_id
        and object_type
        and (
            "current_url_matches_object_url" in signals
            or "current_url_contains_object_id" in signals
        )
    )
    if bridge_provider != "page_agent_browser_plan":
        return {"status": "not_requested", "reason": "verification_page_agent_bridge_only"}
    if status != "executed":
        return {"status": "not_requested", "reason": "verification_execution_status_not_executed"}
    if not current_url or artifact_count <= 0 or reproduction_step_count <= 0:
        return {"status": "mismatch", "reason": "page_agent_evidence_incomplete", "signals": signals}
    if not object_binding_verified:
        return {"status": "mismatch", "reason": "page_agent_object_binding_incomplete", "signals": signals}
    return {
        "status": "verified",
        "reason": "page_agent_execution_evidence_consistent",
        "target": current_url,
        "signals": signals,
        "artifact_count": artifact_count,
        "object_type": object_type,
        "object_id": object_id,
        "data_scope_ref": data_scope_ref,
    }


def _verify_ui_candidate_findings(items: Any, *, root: Path, runtime_contract: dict[str, Any]) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        raw = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else {}
        ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
        metadata = ui_result.get("metadata") if isinstance(ui_result.get("metadata"), dict) else {}
        verification_cfg = metadata.get("verification") if isinstance(metadata.get("verification"), dict) else {}
        context_values = _ui_verification_context(row)
        verification_result = {"status": "not_requested", "reason": "verification_not_configured"}
        kind = str(verification_cfg.get("kind") or "").strip().lower()
        if kind == "http_get":
            verification_result = _verify_ui_candidate_http(verification_cfg, context_values, runtime_contract)
        elif kind == "sqlite_query":
            verification_result = _verify_ui_candidate_sqlite(verification_cfg, context_values, root)
        elif not kind:
            verification_result = _verify_ui_candidate_execution_evidence(context_values)
        row["ui_verification"] = verification_result
        if verification_result.get("status") == "verified":
            row["confidence_score"] = max(float(row.get("confidence_score") or 0.0), 0.8)
            row.setdefault("evidence_quality", {})
            if isinstance(row["evidence_quality"], dict):
                quality_level = "cross_verified" if kind in {"http_get", "sqlite_query"} else "runtime_consistent"
                quality_score = 85 if kind in {"http_get", "sqlite_query"} else 80
                row["evidence_quality"]["level"] = quality_level
                row["evidence_quality"]["score"] = max(int(row["evidence_quality"].get("score") or 0), quality_score)
        verified.append(row)
    return verified


def _mark_high_confidence_ui_candidates(items: Any) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        verification = row.get("ui_verification") if isinstance(row.get("ui_verification"), dict) else {}
        quality = row.get("evidence_quality") if isinstance(row.get("evidence_quality"), dict) else {}
        status = str(verification.get("status") or "").strip().lower()
        quality_level = str(quality.get("level") or "").strip().lower()
        quality_score = int(quality.get("score") or 0)
        confidence = float(row.get("confidence_score") or row.get("confidence") or 0.0)
        high_conf = (
            status == "verified"
            and quality_level in {"cross_verified", "validated"}
            and quality_score >= 85
            and confidence >= 0.8
        )
        row["high_confidence_candidate"] = bool(high_conf)
        if high_conf:
            row["candidate_tier"] = "high_confidence_ui_candidate"
            row["customer_evidence_label"] = str(row.get("customer_evidence_label") or "UI 二次验真通过")
            row["verification_badge"] = str(row.get("verification_badge") or "ui_verified")
        else:
            row.setdefault("candidate_tier", "ui_candidate")
        enriched.append(row)
    return enriched


