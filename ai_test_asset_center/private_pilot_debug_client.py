"""Optional debug-report client for private-pilot diagnostics.

Disabled unless ``QUALIBUG_DEBUG_REPORT=1``. Symbols remain re-exported from
``private_pilot_service`` for call-site compatibility.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

_DBG_ENV_CACHE: tuple[str, str] | None = None


def _truthy_env(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _dbg_env() -> tuple[str, str]:
    global _DBG_ENV_CACHE
    if _DBG_ENV_CACHE is not None:
        return _DBG_ENV_CACHE
    url = str(os.environ.get("QUALIBUG_DEBUG_SERVER_URL") or "").strip()
    session_id = str(os.environ.get("QUALIBUG_DEBUG_SESSION_ID") or "command-center-502").strip() or "command-center-502"
    env_path = Path(__file__).resolve().parents[1] / ".dbg" / f"{session_id}.env"
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            url = next((line.split("=", 1)[1].strip() for line in content.splitlines() if line.startswith("DEBUG_SERVER_URL=")), url)
            session_id = next((line.split("=", 1)[1].strip() for line in content.splitlines() if line.startswith("DEBUG_SESSION_ID=")), session_id)
    except Exception:
        pass
    _DBG_ENV_CACHE = (url, session_id)
    return _DBG_ENV_CACHE


def _dbg_report(*, hypothesis_id: str, msg: str, data: dict[str, Any] | None = None, run_id: str = "pre-fix", trace_id: str = "") -> None:
    # Debug reporting is disabled by default — must be explicitly enabled via
    # QUALIBUG_DEBUG_REPORT=1 to prevent unintended internal-state exfiltration.
    if not _truthy_env("QUALIBUG_DEBUG_REPORT", "0"):
        return
    try:
        url, session_id = _dbg_env()
        if not url:
            return
        payload = {
            "sessionId": session_id,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": "ai_test_asset_center/private_pilot_debug_client.py",
            "msg": msg,
            "data": data or {},
            "traceId": trace_id,
            "ts": int(time.time() * 1000),
        }
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=0.2).read()
    except Exception:
        pass


def _dbg_fingerprint_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    import hashlib

    row = payload if isinstance(payload, dict) else {}
    source_manifest = row.get("source_manifest") if isinstance(row.get("source_manifest"), dict) else {}
    ui_target_resolution = row.get("ui_target_resolution") if isinstance(row.get("ui_target_resolution"), dict) else {}
    prd_text = str(row.get("prd") or "")
    api_doc = str(row.get("api_doc") or row.get("api_doc_text") or "")
    return {
        "project_id": str(row.get("project_id") or row.get("project") or ""),
        "scope_id": str(row.get("scope_id") or ""),
        "environment_ref": str(row.get("environment_ref") or row.get("target_environment") or ""),
        "execution_approval_id": str(row.get("execution_approval_id") or ""),
        "execution_mode": str(row.get("execution_mode") or ""),
        "base_url": str(row.get("base_url") or ""),
        "ui_base_url": str(row.get("ui_base_url") or ""),
        "ui_base_url_source": str(row.get("ui_base_url_source") or ""),
        "ui_target_resolution_status": str(ui_target_resolution.get("status") or ""),
        "ui_target_resolution_reason": str(ui_target_resolution.get("reason") or ""),
        "prd_len": len(prd_text),
        "prd_sha": hashlib.sha256(prd_text.encode("utf-8")).hexdigest() if prd_text else "",
        "api_len": len(api_doc),
        "api_sha": hashlib.sha256(api_doc.encode("utf-8")).hexdigest() if api_doc else "",
        "source_id": str(source_manifest.get("source_id") or ""),
        "source_hash": str(source_manifest.get("source_hash") or ""),
        "source_version_id": str(source_manifest.get("source_version_id") or ""),
    }
