"""Versioned, source-grounded asset registry for enterprise testing inputs.

This module is deliberately transport-agnostic: file uploads, connector exports,
API specifications and collaboration documents all become immutable project
assets before they influence a Campaign. It stores only project-scoped content
and metadata; secrets belong to connector secret references, not source assets.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


MAX_SOURCE_BYTES = 5_000_000
MAX_SOURCE_ID_LENGTH = 160
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


class SourceRegistryError(ValueError):
    """An asset cannot safely enter the enterprise source registry."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_project(value: Any) -> str:
    text = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._")
    return text or "unscoped"


def _safe_asset_id(value: Any) -> str:
    text = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._")
    if not text:
        raise SourceRegistryError("source_id is required")
    return text[:MAX_SOURCE_ID_LENGTH]


def _canonical_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _paths(root: Path, project_id: str) -> dict[str, Path]:
    project = _safe_project(project_id)
    base = Path(root) / "platform_workspace" / project / "source_registry"
    return {
        "base": base,
        "registry": base / "registry.json",
        "audit": base / "audit.jsonl",
        "blobs": base / "blobs",
        "versions": base / "versions",
    }


def _empty_registry(project_id: str) -> dict[str, Any]:
    return {
        "schema_version": "enterprise-source-registry-v1",
        "project_id": _safe_project(project_id),
        "assets": {},
        "updated_at_utc": _now(),
    }


def _read_registry(root: Path, project_id: str) -> dict[str, Any]:
    path = _paths(root, project_id)["registry"]
    if not path.exists():
        return _empty_registry(project_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceRegistryError("source_registry_unreadable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), dict):
        raise SourceRegistryError("source_registry_invalid")
    return payload


def _append_audit(root: Path, project_id: str, event: str, asset_id: str, source_hash: str, actor: dict[str, Any] | None) -> None:
    path = _paths(root, project_id)["audit"]
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = ""
    if path.exists():
        try:
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                previous = str(json.loads(lines[-1]).get("event_hash") or "")
        except Exception as exc:
            raise SourceRegistryError("source_registry_audit_unreadable") from exc
    entry = {
        "at_utc": _now(),
        "event": event,
        "asset_id": asset_id,
        "source_hash": source_hash,
        "actor": {
            "name": str((actor or {}).get("name") or (actor or {}).get("actor") or "system")[:120],
            "role": str((actor or {}).get("role") or "system")[:64],
        },
        "previous_event_hash": previous,
    }
    entry["event_hash"] = _sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_blob(root: Path, project_id: str, source_hash: str, content: str) -> Path:
    paths = _paths(root, project_id)
    blob = paths["blobs"] / f"{source_hash}.txt"
    if blob.exists():
        existing = blob.read_text(encoding="utf-8", errors="strict")
        if _sha256(existing) != source_hash:
            raise SourceRegistryError("source_blob_hash_mismatch")
        return blob
    blob.parent.mkdir(parents=True, exist_ok=True)
    temporary = blob.with_suffix(".txt.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        if _sha256(temporary.read_text(encoding="utf-8")) != source_hash:
            raise SourceRegistryError("source_blob_write_hash_mismatch")
        temporary.replace(blob)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return blob


def _validate_source_content(content_text: str, source_type: str) -> dict[str, Any]:
    """Basic structural validation of imported source documents.

    Returns {'valid': bool, 'reason': str}. Validation is advisory, not blocking.
    """
    if not content_text or not content_text.strip():
        return {"valid": False, "reason": "Content is empty"}

    source_type_lower = source_type.lower().strip()

    # OpenAPI validation
    if source_type_lower in ("openapi", "openapi3", "swagger", "api_spec"):
        try:
            import json as _json
            spec = _json.loads(content_text)
            if not isinstance(spec, dict):
                return {"valid": False, "reason": "OpenAPI spec is not a JSON object"}
            if "openapi" not in spec and "swagger" not in spec:
                return {"valid": False, "reason": "Missing 'openapi' or 'swagger' version field"}
            if "paths" not in spec or not isinstance(spec.get("paths"), dict):
                return {"valid": False, "reason": "Missing or empty 'paths' — no API endpoints defined"}
            return {"valid": True, "reason": f"OpenAPI {spec.get('openapi', spec.get('swagger', '?'))} with {len(spec['paths'])} paths"}
        except Exception as e:
            return {"valid": False, "reason": f"Invalid JSON: {e}"}

    # PRD validation
    if source_type_lower in ("prd", "requirement", "business_rules"):
        if len(content_text.strip()) < 10:
            return {"valid": False, "reason": "PRD content too short (<10 chars)"}
        return {"valid": True, "reason": f"PRD with {len(content_text)} chars"}

    # DB Schema validation
    if source_type_lower in ("db_design", "database_schema", "sql", "db_schema"):
        if len(content_text.strip()) < 10:
            return {"valid": False, "reason": "DB schema too short (<10 chars)"}
        # Basic SQL check: should contain CREATE or ALTER or table definition
        has_sql = any(kw in content_text.upper() for kw in ("CREATE TABLE", "ALTER TABLE", "CREATE INDEX"))
        if not has_sql:
            return {"valid": True, "reason": "No CREATE/ALTER TABLE found — may be schema description rather than SQL DDL"}
        return {"valid": True, "reason": f"DB schema with {len(content_text)} chars"}

    return {"valid": True, "reason": f"Content accepted ({len(content_text)} chars, type={source_type})"}


def register_source_asset(
    project_id: str,
    source_id: str,
    content: Any,
    *,
    source_type: str,
    root: Path,
    actor: dict[str, Any] | None = None,
    origin: str = "manual_upload",
    filename: str = "",
    external_ref: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register a source version and return its immutable scan manifest.

    A repeated registration of identical content is idempotent. A changed
    payload creates a new version under the same source asset identity.
    """
    project = _safe_project(project_id)
    asset_id = _safe_asset_id(source_id)
    # Convert content to text for validation (handles bytes, str, dict)
    # Only validate if content is already in-memory (not file-like streams)
    content_text = ""
    if isinstance(content, bytes):
        content_text = content.decode("utf-8", errors="replace")
    elif isinstance(content, str):
        content_text = content
    elif isinstance(content, dict):
        import json as _json
        content_text = _json.dumps(content, ensure_ascii=False)

    # Validate content structure based on source_type (only if we have text)
    if content_text:
        validation = _validate_source_content(content_text, source_type)
        if not validation["valid"]:
            import sys
            print(f"[source_registry] Validation warning for {source_id} ({source_type}): {validation['reason']}", file=sys.stderr)
            # Store validation result in metadata for downstream consumers
            metadata = dict(metadata or {})
            metadata["_validation"] = validation
    source_kind = str(source_type or "other_document").strip()[:80]
    if not source_kind:
        raise SourceRegistryError("source_type is required")
    source_origin = str(origin or "manual_upload").strip()[:80]
    if not source_origin:
        raise SourceRegistryError("source_origin is required")
    text = _canonical_text(content)
    byte_count = len(text.encode("utf-8"))
    if not text.strip():
        raise SourceRegistryError("source_content is required")
    if byte_count > MAX_SOURCE_BYTES:
        raise SourceRegistryError("source_content_too_large")
    source_hash = _sha256(text)
    registry = _read_registry(Path(root), project)
    assets = registry["assets"]
    current = assets.get(asset_id)
    if current is not None and not isinstance(current, dict):
        raise SourceRegistryError("source_asset_invalid")
    versions = list((current or {}).get("versions") or [])
    existing = next((row for row in versions if isinstance(row, dict) and row.get("source_hash") == source_hash), None)
    blob = _write_blob(Path(root), project, source_hash, text)
    if existing is None:
        version = {
            "version_id": f"srcv_{source_hash[:24]}",
            "source_hash": source_hash,
            "byte_count": byte_count,
            "source_type": source_kind,
            "source_origin": source_origin,
            "filename": str(filename or "")[:240],
            "external_ref": str(external_ref or "")[:500],
            "registered_at_utc": _now(),
            "registered_by": {
                "name": str((actor or {}).get("name") or (actor or {}).get("actor") or "system")[:120],
                "role": str((actor or {}).get("role") or "system")[:64],
            },
            "metadata": dict(metadata or {}),
            "blob_ref": str(blob.relative_to(Path(root))),
        }
        versions.append(version)
        action = "source_version_registered"
    else:
        version = existing
        action = "source_version_reused"
    assets[asset_id] = {
        "source_id": asset_id,
        "source_type": source_kind,
        "latest_source_hash": source_hash,
        "latest_version_id": version["version_id"],
        "versions": versions[-100:],
        "updated_at_utc": _now(),
    }
    registry["updated_at_utc"] = _now()
    _atomic_json(_paths(Path(root), project)["registry"], registry)
    _append_audit(Path(root), project, action, asset_id, source_hash, actor)
    return {
        "source_id": asset_id,
        "source_hash": source_hash,
        "source_version_id": version["version_id"],
        "source_origin": "registered_source_registry",
        "source_type": source_kind,
        "blob_ref": version["blob_ref"],
    }


def resolve_source_manifest(project_id: str, content: Any, *, root: Path) -> dict[str, str]:
    """Resolve content to a registered immutable manifest; never guesses by name."""
    text = _canonical_text(content)
    source_hash = _sha256(text)
    registry = _read_registry(Path(root), _safe_project(project_id))
    for asset in registry.get("assets", {}).values():
        if not isinstance(asset, dict):
            continue
        for version in asset.get("versions", []):
            if not isinstance(version, dict) or version.get("source_hash") != source_hash:
                continue
            return {
                "source_id": str(asset.get("source_id") or ""),
                "source_hash": source_hash,
                "source_version_id": str(version.get("version_id") or ""),
                "source_origin": "registered_source_registry",
                "source_type": str(version.get("source_type") or asset.get("source_type") or ""),
            }
    return {}


def verify_source_manifest(project_id: str, manifest: dict[str, Any], content: Any, *, root: Path) -> dict[str, Any]:
    """Verify both content integrity and registry ownership of a source manifest."""
    supplied = manifest if isinstance(manifest, dict) else {}
    source_id = _safe_asset_id(supplied.get("source_id")) if supplied.get("source_id") else ""
    source_hash = str(supplied.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    actual_hash = _sha256(_canonical_text(content))
    if not source_id or not source_hash:
        return {"valid": False, "code": "SOURCE_PROVENANCE_MISSING"}
    if not _SHA256_RE.fullmatch(source_hash):
        return {"valid": False, "code": "SOURCE_HASH_INVALID"}
    if source_hash != actual_hash:
        return {"valid": False, "code": "SOURCE_HASH_MISMATCH"}
    resolved = resolve_source_manifest(project_id, content, root=Path(root))
    if not resolved or resolved.get("source_id") != source_id:
        return {"valid": False, "code": "SOURCE_NOT_REGISTERED"}
    return {"valid": True, "manifest": resolved}


def list_source_assets(project_id: str, *, root: Path) -> list[dict[str, Any]]:
    registry = _read_registry(Path(root), _safe_project(project_id))
    result: list[dict[str, Any]] = []
    for asset in registry.get("assets", {}).values():
        if not isinstance(asset, dict):
            continue
        result.append({
            "source_id": str(asset.get("source_id") or ""),
            "source_type": str(asset.get("source_type") or ""),
            "latest_source_hash": str(asset.get("latest_source_hash") or ""),
            "latest_version_id": str(asset.get("latest_version_id") or ""),
            "version_count": len(asset.get("versions") or []),
            "updated_at_utc": str(asset.get("updated_at_utc") or ""),
        })
    return sorted(result, key=lambda item: item["source_id"])


def load_source_content(project_id: str, source_hash: str, *, root: Path) -> str:
    digest = str(source_hash or "").strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise SourceRegistryError("source_hash_invalid")
    path = _paths(Path(root), _safe_project(project_id))["blobs"] / f"{digest}.txt"
    if not path.exists():
        raise SourceRegistryError("source_blob_missing")
    content = path.read_text(encoding="utf-8")
    if _sha256(content) != digest:
        raise SourceRegistryError("source_blob_hash_mismatch")
    return content
