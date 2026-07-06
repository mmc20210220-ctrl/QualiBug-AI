"""Source-version change impact for minimal, source-bound regression planning.

This module compares immutable registered source versions. It does not call a
runtime target and it does not classify any defect. For OpenAPI inputs it emits
changed operations; for other text assets it emits an explicit review gap rather
than inventing behavioral meaning.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


class ChangeImpactError(ValueError):
    """Registered source versions cannot be compared safely."""


def _hash(value: Any, length: int = 24) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _as_json(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _openapi_operations(value: str) -> dict[tuple[str, str], dict[str, Any]] | None:
    document = _as_json(value)
    if not document or not isinstance(document.get("paths"), dict):
        return None
    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for path, path_item in document["paths"].items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            normalized = str(method).lower()
            if normalized not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            operations[(normalized.upper(), path)] = operation
    return operations


def _operation_fingerprint(operation: dict[str, Any]) -> str:
    # Omit documentation-only free text to avoid treating wording changes as a runtime change.
    stable = {key: value for key, value in operation.items() if key not in {"summary", "description", "externalDocs"}}
    return _hash(stable, 64)


def compare_source_versions(
    project_id: str,
    *,
    root: Path,
    base_manifest: dict[str, Any],
    head_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Compare two immutable registry manifests and emit regression anchors."""
    base_hash = str(base_manifest.get("source_hash") or "").strip().lower()
    head_hash = str(head_manifest.get("source_hash") or "").strip().lower()
    if not base_hash or not head_hash:
        raise ChangeImpactError("source_hash_required")
    try:
        from .enterprise_source_registry import load_source_content
        base_content = load_source_content(project_id, base_hash, root=root)
        head_content = load_source_content(project_id, head_hash, root=root)
    except Exception as exc:
        raise ChangeImpactError("registered_source_content_unavailable") from exc

    base_operations = _openapi_operations(base_content)
    head_operations = _openapi_operations(head_content)
    common = {
        "schema_version": "qualibug-change-impact-v1",
        "project_id": str(project_id),
        "base_source": {"source_id": str(base_manifest.get("source_id") or ""), "source_hash": base_hash, "source_version_id": str(base_manifest.get("source_version_id") or "")},
        "head_source": {"source_id": str(head_manifest.get("source_id") or ""), "source_hash": head_hash, "source_version_id": str(head_manifest.get("source_version_id") or "")},
        "impacts": [],
        "coverage_gaps": [],
    }
    if base_operations is None or head_operations is None:
        common["coverage_gaps"].append({
            "kind": "CHANGE_REVIEW_GAP",
            "code": "SOURCE_FORMAT_NOT_OPERATIONAL",
            "detail": "Both source versions must be JSON OpenAPI documents to derive source-bound operation changes.",
        })
        return {**common, "summary": {"changed_operation_count": 0, "review_required": True}}

    impacts: list[dict[str, Any]] = []
    all_keys = sorted(set(base_operations) | set(head_operations))
    for method, path in all_keys:
        before = base_operations.get((method, path))
        after = head_operations.get((method, path))
        if before is None:
            change_kind = "operation_added"
        elif after is None:
            change_kind = "operation_removed"
        elif _operation_fingerprint(before) != _operation_fingerprint(after):
            change_kind = "operation_modified"
        else:
            continue
        impact = {
            "impact_id": "IMP_" + _hash({"base": base_hash, "head": head_hash, "method": method, "path": path, "kind": change_kind}),
            "change_kind": change_kind,
            "method": method,
            "path": path,
            "source_refs": [f"{base_hash}:{method} {path}", f"{head_hash}:{method} {path}"],
            "regression_action": "add_or_update_source_bound_coverage" if change_kind != "operation_removed" else "review_removed_operation_contract",
        }
        impacts.append(impact)
    return {
        **common,
        "impacts": impacts,
        "summary": {
            "changed_operation_count": len(impacts),
            "added_operation_count": sum(1 for item in impacts if item["change_kind"] == "operation_added"),
            "modified_operation_count": sum(1 for item in impacts if item["change_kind"] == "operation_modified"),
            "removed_operation_count": sum(1 for item in impacts if item["change_kind"] == "operation_removed"),
            "review_required": bool(impacts),
        },
    }
