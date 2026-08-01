"""Source resolution, runtime contract, and scan preflight helpers.

Extracted from ``__main__``. Symbols are re-exported for compatibility.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .product_scan_mainline import (
    _as_dict,
    _first_text,
    _gap,
    _safe_project,
    _sha256,
)
from .target_policy import build_target_policy_decision

_SOURCE_EXTENSIONS = {".json", ".yaml", ".yml", ".md", ".txt"}
_MAX_SOURCE_BYTES = 5_000_000
_MAX_SOURCE_FILES = 200
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

def _load_schema_assets(root: Path, project: str) -> str:
    """Load project-scoped database schema for data-layer observation planning.

    Checks, in order:
    1. registered ``database_schema`` source assets (canonical ingest authority)
    2. ``platform_workspace/<project>/input/*.sql`` (legacy workspace)
    3. ``platform_inputs/<project>/schema.sql`` (legacy customer material)
    4. ``platform_inputs/<project>/DB_SCHEMA.md`` (legacy markdown material)

    The HTTP knowledge-ingest path stores uploaded files in the source registry,
    so looking only at legacy filesystem aliases made a successfully parsed
    schema disappear before the mainline planner received it.
    """
    safe = _safe_project(project)
    chunks: list[str] = []
    seen_hashes: set[str] = set()

    def _ingest_text(text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        digest = _sha256(text)
        if digest in seen_hashes:
            return
        seen_hashes.add(digest)
        chunks.append(text[:1_000_000])

    def _ingest(path: Path) -> None:
        if not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return
        _ingest_text(text)

    from .enterprise_source_registry import (
        SourceRegistryError,
        list_source_assets,
        load_source_content,
    )

    for asset in list_source_assets(project, root=root):
        if not isinstance(asset, dict):
            continue
        source_type = str(asset.get("source_type") or "").strip().lower()
        if source_type not in {"database_schema", "db_schema", "db_design", "sql"}:
            continue
        source_hash = str(asset.get("latest_source_hash") or "").strip().lower()
        if not _SHA256_RE.fullmatch(source_hash):
            raise RuntimeError("database_schema_source_hash_invalid")
        try:
            _ingest_text(load_source_content(project, source_hash, root=root))
        except SourceRegistryError as exc:
            raise RuntimeError("database_schema_source_unreadable") from exc

    workspace_sql = root / "platform_workspace" / safe / "input"
    if workspace_sql.exists():
        for path in sorted(workspace_sql.glob("*.sql")):
            _ingest(path)

    inputs_dir = root / "platform_inputs" / safe
    _ingest(inputs_dir / "schema.sql")
    db_schema_md = inputs_dir / "DB_SCHEMA.md"
    if db_schema_md.exists():
        _ingest(db_schema_md)

    return "\n\n".join(chunks)


def _project_requirement_input_dirs(root: Path, project: str) -> list[Path]:
    safe_project = _safe_project(project)
    candidates: list[Path] = [root / "platform_workspace" / safe_project / "input"]

    aliases: set[str] = {safe_project}
    normalized_project = re.sub(r"[^a-z0-9]+", "_", safe_project.lower()).strip("_")
    if normalized_project:
        aliases.add(normalized_project)
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
    except Exception:
        registry = {}
    if isinstance(registry, dict):
        aliases.add(str(registry.get("project_id") or "").strip())
        for connector in registry.get("connectors", []) if isinstance(registry.get("connectors"), list) else []:
            if not isinstance(connector, dict):
                continue
            for key in ("system_name", "service_name", "domain_name", "module_name"):
                aliases.add(str(connector.get(key) or "").strip())
    aliases = {item for item in aliases if item}
    projects_root = root / "projects"
    if projects_root.exists():
        try:
            for entry in sorted(projects_root.iterdir(), key=lambda item: item.name.lower()):
                if not entry.is_dir():
                    continue
                name = entry.name.strip()
                normalized_name = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                if (
                    name in aliases
                    or normalized_name in aliases
                    or any(alias and (alias in normalized_name or normalized_name in alias) for alias in aliases)
                ):
                    candidates.append(entry / "input")
        except OSError:
            pass
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _requirement_doc_score(path: Path) -> int:
    name = path.name.lower()
    if path.suffix.lower() not in {".md", ".txt", ".rst"}:
        return 0
    negative_tokens = (
        "api_spec",
        "openapi",
        "swagger",
        "db_schema",
        "schema",
        "test_accounts",
        "windows_native_start",
        "deployment",
        "historical_bug",
    )
    if any(token in name for token in negative_tokens):
        return 0
    score = 0
    if "prd" in name:
        score += 100
    if "mrd" in name:
        score += 90
    if "business_rules" in name or "business-rules" in name:
        score += 85
    if "requirement" in name:
        score += 80
    if "user_roles" in name or "roles" in name:
        score += 50
    if "spec" in name:
        score += 20
    return score


def _load_project_prd_text(root: Path, project: str) -> str:
    candidates: list[tuple[int, int, Path]] = []
    for directory_index, input_dir in enumerate(_project_requirement_input_dirs(root, project)):
        if not input_dir.exists():
            continue
        try:
            entries = sorted(input_dir.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for path in entries:
            if not path.is_file():
                continue
            score = _requirement_doc_score(path)
            if score <= 0:
                continue
            candidates.append((directory_index, -score, path))
    chunks: list[str] = []
    seen_paths: set[str] = set()
    seen_hashes: set[str] = set()
    for _, _, path in sorted(candidates, key=lambda item: (item[0], item[1], str(item[2]).lower())):
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            text = ""
        if not text:
            continue
        content_hash = _sha256(text)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        chunks.append(f"## {path.name}\n{text}")
    return "\n\n".join(chunks)


def _registry_manifest(root: Path, project: str, api_doc_text: str) -> dict[str, str]:
    try:
        from .enterprise_source_registry import SourceRegistryError, resolve_source_manifest
        manifest = resolve_source_manifest(project, api_doc_text, root=root)
    except (ImportError, OSError, ValueError):
        return {}
    except SourceRegistryError:
        return {}
    if not isinstance(manifest, dict) or not str(manifest.get("source_id") or "").strip() or not str(manifest.get("source_hash") or "").strip():
        return {}
    return {
        "source_id": str(manifest.get("source_id") or "")[:160],
        "source_hash": str(manifest.get("source_hash") or "")[:128],
        "source_version_id": str(manifest.get("source_version_id") or "")[:80],
        "source_origin": str(manifest.get("source_origin") or "registered_source_registry")[:80],
    }


def _load_registered_source(project: str, root: Path, context: dict[str, Any]) -> str:
    """Load the best available registered source as API doc text.

    Prefers OpenAPI / Swagger / Postman type sources.  Falls back to any
    registered source when no API spec is available — the caller can still
    extract partial endpoint hints from PRDs and DB schemas.
    """
    manifest = _as_dict(context.get("source_manifest"))
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    try:
        from .enterprise_source_registry import SourceRegistryError, list_source_assets, load_source_content
        if not _SHA256_RE.fullmatch(source_hash):
            assets = list_source_assets(project, root=root)
            _api_spec_types = {"openapi", "openapi3", "swagger", "postman", "api_spec"}

            def _sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
                """Weight: API-spec sources first, then by recency."""
                _type = str(item.get("source_type") or "").strip().lower()
                _is_api = 0 if _type in _api_spec_types else 1
                return (_is_api, str(item.get("updated_at_utc") or ""), str(item.get("source_id") or ""))

            latest = min(
                (
                    item
                    for item in assets
                    if isinstance(item, dict) and _SHA256_RE.fullmatch(str(item.get("latest_source_hash") or "").strip().lower())
                ),
                key=_sort_key,
                default={},
            )
            source_hash = str(latest.get("latest_source_hash") or "").strip().lower()
            if not _SHA256_RE.fullmatch(source_hash):
                return ""
            context["source_manifest"] = {
                **manifest,
                "source_id": str(latest.get("source_id") or "").strip(),
                "source_hash": source_hash,
                "source_version_id": str(latest.get("latest_version_id") or "").strip(),
                "source_origin": "registered_source_registry",
            }
        return load_source_content(project, source_hash, root=root)
    except (ImportError, OSError, ValueError, SourceRegistryError):
        return ""


def _find_project_asset(root: Path, project: str, content_hash: str) -> dict[str, str]:
    """Migration resolver for an exact project-owned input asset."""
    project_root = root / "platform_workspace" / _safe_project(project) / "input"
    if not project_root.exists() or not project_root.is_dir():
        return {}
    inspected = 0
    try:
        entries = sorted(project_root.rglob("*"))
    except OSError:
        return {}
    for path in entries:
        if inspected >= _MAX_SOURCE_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in _SOURCE_EXTENSIONS:
            continue
        inspected += 1
        try:
            if path.stat().st_size > _MAX_SOURCE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _sha256(content) != content_hash:
            continue
        return {
            "source_id": f"project_asset:{path.relative_to(root).as_posix()}",
            "source_hash": content_hash,
            "source_version_id": f"legacy_{content_hash[:24]}",
            "source_origin": "registered_project_asset",
        }
    return {}


def _source_manifest(root: Path, project: str, context: dict[str, Any], api_doc_path: str, api_doc_text: str) -> dict[str, str]:
    declared = _as_dict(context.get("source_manifest"))
    source_id = str(declared.get("source_id") or "").strip()
    source_hash = str(declared.get("source_hash") or "").strip().lower().removeprefix("sha256:").strip()
    source_version_id = str(declared.get("source_version_id") or "").strip()
    actual_hash = _sha256(api_doc_text)
    source_origin = str(declared.get("source_origin") or "").strip()
    if source_id or source_hash:
        source_origin = source_origin or "declared_manifest"
    else:
        registered = _registry_manifest(root, project, api_doc_text) or _find_project_asset(root, project, actual_hash)
        source_id = registered.get("source_id", "")
        source_hash = registered.get("source_hash", "")
        source_version_id = registered.get("source_version_id", "")
        source_origin = registered.get("source_origin", "external_path_unregistered" if api_doc_path else "inline_unregistered")
    return {
        "source_id": source_id[:160],
        "source_hash": source_hash[:128],
        "source_version_id": source_version_id[:80],
        "actual_hash": actual_hash,
        "source_origin": source_origin[:80],
    }


def _source_contract(manifest: dict[str, str]) -> list[dict[str, str]]:
    if not manifest.get("source_id") or not manifest.get("source_hash"):
        return [_gap("SOURCE_PROVENANCE_MISSING", "Every enterprise scan requires a registered project asset or an explicit source_id and immutable SHA-256 source_hash.")]
    if not _SHA256_RE.fullmatch(manifest["source_hash"]):
        return [_gap("SOURCE_HASH_INVALID", "source_hash must be a lowercase SHA-256 digest for the submitted source content.")]
    if manifest["source_hash"] != manifest["actual_hash"]:
        return [_gap("SOURCE_HASH_MISMATCH", "The source_hash does not match submitted source content.")]
    return []


def _runtime_contract(context: dict[str, Any], base_url: str, manifest: dict[str, str]) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    public_manifest = {
        "source_id": manifest.get("source_id", ""),
        "source_hash": manifest.get("source_hash", ""),
        "source_version_id": manifest.get("source_version_id", ""),
        "source_origin": manifest.get("source_origin", ""),
    }
    if not base_url:
        return "", [], {"status": "plan_only", "reason": "runtime_target_missing", "source_manifest": public_manifest}
    missing: list[dict[str, str]] = []
    if not public_manifest["source_id"] or not public_manifest["source_hash"]:
        missing.append(_gap("SOURCE_PROVENANCE_MISSING", "A registered source is required before runtime probing."))
    if not str(context.get("scope_id") or "").strip():
        missing.append(_gap("CAMPAIGN_SCOPE_MISSING", "An explicit campaign scope_id is required before runtime probing."))
    environment_ref = str(context.get("environment_ref") or context.get("target_environment") or "").strip()
    if not environment_ref:
        missing.append(_gap("ENVIRONMENT_REFERENCE_MISSING", "An approved environment_ref is required before runtime probing."))
    environment_type = _first_text(
        context.get("environment_type"),
        context.get("environment_kind"),
        context.get("environment_class"),
    ).lower()
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip() or "safe_read_only"
    if execution_mode == "approved_sandbox_write" and not environment_type:
        missing.append(_gap("UNKNOWN_ENVIRONMENT", "Write execution requires an explicit non-production environment_type."))
    test_data = _as_dict(context.get("test_data_contract"))
    if test_data.get("strategy") in {"create_disposable", "approved_fixture_setup"} and test_data.get("write_approved") is not True:
        missing.append(_gap("WRITE_APPROVAL_MISSING", "Write-capable test-data strategies require explicit write approval."))
    approved_base_url = _first_text(
        context.get("approved_base_url"),
        _as_dict(context.get("target_policy")).get("approved_base_url"),
        base_url if not missing else "",
    )
    decision = build_target_policy_decision(
        requested_base_url=base_url,
        approved_base_url=approved_base_url,
        environment_type=environment_type,
        environment_ref=environment_ref,
        execution_mode=execution_mode,
        runtime_status="approved" if not missing else "blocked",
    )
    if execution_mode == "approved_sandbox_write" and not decision.get("write_allowed"):
        for code in decision.get("blocking_codes") or []:
            missing.append(_gap(str(code), "Target Policy blocked governed write execution."))
    elif not decision.get("read_allowed"):
        for code in decision.get("blocking_codes") or []:
            missing.append(_gap(str(code), "Target Policy blocked runtime access."))
    if missing:
        return "", missing, {
            "status": "blocked",
            "reason": "runtime_contract_missing",
            "source_manifest": public_manifest,
            "environment_ref": environment_ref,
            "environment_kind": environment_type,
            "execution_mode": execution_mode,
            "target_policy_decision": decision,
        }
    normalized_base = str(decision.get("approved_base_url") or "")
    contract: dict[str, Any] = {
        "status": "approved",
        "reason": "",
        "requested_base_url": str(decision.get("requested_base_url") or ""),
        "approved_base_url": normalized_base,
        "environment_ref": environment_ref,
        "environment_kind": environment_type,
        "execution_mode": execution_mode,
        "source_manifest": public_manifest,
        "target_policy_decision": decision,
    }
    # Propagate validation_phase so downstream budget enforcement respects it.
    _vp = str(context.get("validation_phase") or "").strip().lower()
    if _vp:
        contract["validation_phase"] = _vp
    return normalized_base, [], contract


def _scan_preflight_guide(
    *,
    context: dict[str, Any],
    base_url: str,
    manifest: dict[str, str],
    runtime_contract: dict[str, Any],
    test_data_plan: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    runtime_observed: bool = False,
) -> dict[str, Any]:
    test_data = _as_dict(context.get("test_data_contract"))
    service_credentials = _as_dict(
        (diagnostics or {}).get("service_credentials_readiness")
        or context.get("service_credentials_readiness")
    )
    if not service_credentials and isinstance(context.get("services"), list):
        try:
            from .runtime_onboarding_preflight import _service_credentials_readiness

            service_credentials = _as_dict(_service_credentials_readiness({"services": context.get("services")}))
        except Exception:
            service_credentials = {}
    configured_services = int(service_credentials.get("configured_service_count") or 0)
    unverified_services = service_credentials.get("unverified") if isinstance(service_credentials.get("unverified"), list) else []
    service_credentials_status = (
        "ready"
        if configured_services and bool(service_credentials.get("ok"))
        else ("configured_unverified" if configured_services else "not_configured")
    )
    target_decision = _as_dict(runtime_contract.get("target_policy_decision"))
    if not target_decision and base_url:
        target_decision = build_target_policy_decision(
            requested_base_url=base_url,
            approved_base_url=runtime_contract.get("approved_base_url"),
            environment_type=_first_text(
                runtime_contract.get("environment_kind"),
                context.get("environment_type"),
                context.get("environment_kind"),
                context.get("environment_class"),
            ),
            environment_ref=_first_text(
                runtime_contract.get("environment_ref"),
                context.get("environment_ref"),
                context.get("target_environment"),
            ),
            execution_mode=_first_text(runtime_contract.get("execution_mode"), context.get("execution_mode"), "safe_read_only"),
            runtime_status=runtime_contract.get("status"),
        )
    source_bound_nonproduction = bool(
        target_decision.get("write_allowed")
        and manifest.get("source_id")
        and manifest.get("source_hash")
        and str(context.get("scope_id") or "").strip()
    )
    checks = [
        {
            "key": "source_manifest",
            "label": "immutable_source_manifest",
            "status": "ready" if manifest.get("source_id") and manifest.get("source_hash") else "missing",
            "required": True,
            "detail": manifest.get("source_id") or "register customer materials before scanning",
        },
        {
            "key": "target_base_url",
            "label": "target_environment_url",
            "status": "ready" if target_decision.get("read_allowed") else ("configured_unverified" if base_url else "missing"),
            "required": bool(base_url),
            "detail": base_url or "plan_only_scan_has_no_runtime_target",
        },
        {
            "key": "scope_id",
            "label": "approved_scope",
            "status": "ready" if str(context.get("scope_id") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(context.get("scope_id") or ""),
        },
        {
            "key": "environment_ref",
            "label": "environment_reference",
            "status": "ready" if str(context.get("environment_ref") or context.get("target_environment") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(context.get("environment_ref") or context.get("target_environment") or ""),
        },
        {
            "key": "environment_type",
            "label": "explicit_environment_classification",
            "status": "ready" if str(target_decision.get("environment_type") or "").strip() else "missing",
            "required": bool(base_url and str(context.get("execution_mode") or "") == "approved_sandbox_write"),
            "detail": str(target_decision.get("environment_type") or ""),
        },
        {
            "key": "test_data_strategy",
            "label": "test_data_strategy",
            "status": "ready" if str(test_data.get("strategy") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(test_data.get("strategy") or ""),
        },
        {
            "key": "execution_approval",
            "label": "execution_authorization_basis",
            "status": "ready" if str(context.get("execution_approval_id") or "").strip() else ("not_required" if source_bound_nonproduction or not base_url or str(context.get("execution_mode") or "") == "safe_read_only" else "missing"),
            "required": bool(base_url and str(context.get("execution_mode") or "") == "approved_sandbox_write" and not source_bound_nonproduction),
            "detail": str(context.get("execution_approval_id") or target_decision.get("authorization_basis") or ""),
        },
        {
            "key": "target_policy",
            "label": "target_policy_decision",
            "status": "ready" if target_decision.get("status") == "approved" else "failed",
            "required": bool(base_url),
            "detail": ",".join(str(code) for code in target_decision.get("blocking_codes") or []),
        },
        {
            "key": "actor_credentials",
            "label": "test_actor_or_role_credentials",
            "status": "configured_unverified" if _as_dict(context.get("actor_contract") or context.get("test_actor_contract")) else "not_configured",
            "required": False,
            "detail": "configured actors still require runtime login or token evidence",
        },
        {
            "key": "service_credentials",
            "label": "service_auth_db_credentials",
            "status": service_credentials_status,
            "required": bool(base_url and configured_services),
            "detail": ";".join(str(item) for item in unverified_services) or str(service_credentials.get("message") or ""),
        },
        {
            "key": "url_reachability",
            "label": "url_reachability",
            "status": "ready" if runtime_observed else ("not_checked" if not diagnostics else ("ready" if diagnostics.get("ready") else "failed")),
            "required": bool(base_url),
            "detail": "runtime_traffic_captured" if runtime_observed else str((diagnostics or {}).get("summary") or "no runtime health check was executed"),
        },
    ]
    if test_data_plan:
        checks.append({
            "key": "test_data_contract",
            "label": "test_data_contract",
            "status": str(test_data_plan.get("status") or "missing"),
            "required": bool(base_url),
            "detail": ",".join(str(item) for item in test_data_plan.get("missing_requirements", []) or []),
        })
    missing = [
        item["key"]
        for item in checks
        if item.get("required")
        and (
            item.get("status") in {"missing", "failed", "blocked_with_testability_gap"}
            or (item.get("key") == "service_credentials" and item.get("status") == "configured_unverified")
        )
    ]
    runtime_status = str(runtime_contract.get("status") or "")
    return {
        "status": "ready" if not missing and runtime_status == "approved" else ("plan_only" if not base_url else "blocked"),
        "runtime_contract_status": runtime_status,
        "missing": missing,
        "blocking_codes": sorted(set(str(code) for code in target_decision.get("blocking_codes") or [] if str(code))),
        "target_policy_decision": target_decision,
        "checks": checks,
        "healthy_claim_allowed": not missing and runtime_status == "approved" and bool(target_decision.get("read_allowed") if base_url else True),
    }


def _source_catalog(api_doc: str) -> str:
    labels: set[str] = set()
    for line in str(api_doc or "").splitlines():
        match = re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s|`]+)", line, re.I)
        if not match:
            continue
        parts = [part for part in match.group(1).strip("/").split("/") if part and not part.startswith("{") and part.lower() not in {"api", "v1", "v2", "v3"}]
        if parts:
            labels.add(parts[0])
    return "\n".join(f"# Source asset: {item}" for item in sorted(labels))


