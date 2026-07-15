"""Time-bounded approvals for enterprise runtime execution targets.

An approval binds a Campaign scope, immutable source hash, target origin and
execution mode. It contains no credentials and does not itself execute traffic.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ALLOWED_EXECUTION_MODES = {"safe_read_only", "approved_sandbox_write"}


class ExecutionApprovalError(ValueError):
    """An execution approval cannot be issued or verified safely."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_project(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "unscoped"


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _hash(value: Any, length: int = 64) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _origin(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ExecutionApprovalError("execution_target_origin_invalid")
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _paths(root: Path, project_id: str) -> dict[str, Path]:
    base = Path(root) / "platform_workspace" / _safe_project(project_id) / "execution_approvals"
    return {"base": base, "registry": base / "approvals.json", "audit": base / "audit.jsonl"}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_registry(root: Path, project_id: str) -> dict[str, Any]:
    path = _paths(root, project_id)["registry"]
    if not path.exists():
        return {"schema_version": "qualibug-execution-approvals-v1", "project_id": _safe_project(project_id), "approvals": {}, "updated_at_utc": _now()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionApprovalError("execution_approval_registry_unreadable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("approvals"), dict):
        raise ExecutionApprovalError("execution_approval_registry_invalid")
    return payload


def _parse_expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionApprovalError("execution_approval_expiry_invalid") from exc
    if parsed.tzinfo is None:
        raise ExecutionApprovalError("execution_approval_expiry_timezone_required")
    return parsed.astimezone(timezone.utc)


def issue_execution_approval(
    project_id: str,
    *,
    root: Path,
    campaign_id: str,
    scope_id: str,
    environment_ref: str,
    source_hash: str,
    target_base_url: str,
    execution_mode: str,
    expires_at_utc: str,
    actor: dict[str, Any] | None,
) -> dict[str, Any]:
    """Issue an immutable, time-bounded approval for a single target origin."""
    mode = _text(execution_mode, 80)
    if mode not in ALLOWED_EXECUTION_MODES:
        raise ExecutionApprovalError("execution_approval_mode_invalid")
    campaign = _text(campaign_id, 160)
    scope = _text(scope_id, 160)
    environment = _text(environment_ref, 160)
    source = _text(source_hash, 128).lower()
    if not campaign or not scope or not environment or not re.fullmatch(r"[0-9a-f]{64}", source):
        raise ExecutionApprovalError("execution_approval_binding_missing")
    expiry = _parse_expiry(expires_at_utc)
    if expiry <= datetime.now(timezone.utc):
        raise ExecutionApprovalError("execution_approval_already_expired")
    record = {
        "campaign_id": campaign,
        "scope_id": scope,
        "environment_ref": environment,
        "source_hash": source,
        "target_origin": _origin(target_base_url),
        "execution_mode": mode,
        "expires_at_utc": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": {
            "name": _text((actor or {}).get("name") or (actor or {}).get("actor") or "system", 120),
            "role": _text((actor or {}).get("role") or "system", 64),
        },
        "issued_at_utc": _now(),
    }
    approval_id = "eap_" + _hash(record, 24)
    approval = {"approval_id": approval_id, **record, "approval_hash": ""}
    approval["approval_hash"] = _hash({key: value for key, value in approval.items() if key != "approval_hash"})
    registry = _read_registry(Path(root), project_id)
    existing = registry["approvals"].get(approval_id)
    if existing:
        if existing.get("approval_hash") != approval["approval_hash"]:
            raise ExecutionApprovalError("execution_approval_collision")
        return dict(existing)
    registry["approvals"][approval_id] = approval
    registry["updated_at_utc"] = _now()
    _atomic_json(_paths(Path(root), project_id)["registry"], registry)
    return approval


def verify_execution_approval(
    project_id: str,
    approval_id: str,
    *,
    root: Path,
    campaign_id: str,
    scope_id: str,
    environment_ref: str,
    source_hash: str,
    target_base_url: str,
    execution_mode: str,
) -> dict[str, Any]:
    """Verify target, expiry and all immutable approval bindings."""
    registry = _read_registry(Path(root), project_id)
    approval = registry["approvals"].get(_text(approval_id, 160))
    if not isinstance(approval, dict):
        return {"valid": False, "code": "EXECUTION_APPROVAL_NOT_FOUND"}
    expected_hash = _hash({key: value for key, value in approval.items() if key != "approval_hash"})
    if str(approval.get("approval_hash") or "") != expected_hash:
        return {"valid": False, "code": "EXECUTION_APPROVAL_HASH_MISMATCH"}
    expected = {
        "campaign_id": _text(campaign_id, 160),
        "scope_id": _text(scope_id, 160),
        "environment_ref": _text(environment_ref, 160),
        "source_hash": _text(source_hash, 128).lower(),
        "target_origin": _origin(target_base_url),
        "execution_mode": _text(execution_mode, 80),
    }
    for key, value in expected.items():
        if str(approval.get(key) or "") != value:
            return {"valid": False, "code": f"EXECUTION_APPROVAL_{key.upper()}_MISMATCH"}
    if _parse_expiry(str(approval.get("expires_at_utc") or "")) <= datetime.now(timezone.utc):
        return {"valid": False, "code": "EXECUTION_APPROVAL_EXPIRED"}
    return {"valid": True, "approval": dict(approval)}


def resolve_execution_approval_for_campaign(
    project_id: str,
    *,
    root: Path,
    scope_id: str,
    environment_ref: str,
    source_hash: str,
    target_base_url: str,
    execution_mode: str = "",
) -> dict[str, Any]:
    """Resolve a registered, time-valid approval matching the campaign bindings.

    A scan may omit an explicit ``execution_approval_id`` yet still be covered by
    a pre-issued approval that binds the same immutable campaign identity
    (scope, environment, source hash, target origin). Matching ignores the
    ``campaign_id`` because each scan run receives a fresh campaign identity;
    what matters is the stable binding tuple. ``execution_mode`` is NOT used as a
    hard filter: the stored approval's own ``execution_mode`` is authoritative
    and is surfaced to the caller.

    Returns ``{"found": True, "approval": {...}}`` or
    ``{"found": False, "code": "EXECUTION_APPROVAL_NOT_FOUND"}``.
    """
    try:
        registry = _read_registry(Path(root), project_id)
    except ExecutionApprovalError as exc:
        return {"found": False, "code": f"EXECUTION_APPROVAL_REGISTRY_UNREADABLE:{exc}"}
    scope = _text(scope_id, 160)
    environment = _text(environment_ref, 160)
    source = _text(source_hash, 128).lower()
    try:
        target_origin = _origin(target_base_url)
    except ExecutionApprovalError:
        return {"found": False, "code": "EXECUTION_APPROVAL_TARGET_INVALID"}
    mode = _text(execution_mode, 80)
    candidates: list[dict[str, Any]] = []
    for approval in (registry.get("approvals") or {}).values():
        if not isinstance(approval, dict):
            continue
        if scope and _text(approval.get("scope_id"), 160) != scope:
            continue
        if environment and _text(approval.get("environment_ref"), 160) != environment:
            continue
        if source and _text(approval.get("source_hash"), 128).lower() != source:
            continue
        if _text(approval.get("target_origin"), 200) != target_origin:
            continue
        if mode and _text(approval.get("execution_mode"), 80) != mode:
            continue
        expected_hash = _hash({key: value for key, value in approval.items() if key != "approval_hash"})
        if str(approval.get("approval_hash") or "") != expected_hash:
            continue
        try:
            if _parse_expiry(str(approval.get("expires_at_utc") or "")) <= datetime.now(timezone.utc):
                continue
        except ValueError:
            continue
        candidates.append(dict(approval))
    if not candidates:
        return {"found": False, "code": "EXECUTION_APPROVAL_NOT_FOUND"}
    candidates.sort(key=lambda a: str(a.get("issued_at_utc") or ""), reverse=True)
    return {"found": True, "approval": candidates[0]}
