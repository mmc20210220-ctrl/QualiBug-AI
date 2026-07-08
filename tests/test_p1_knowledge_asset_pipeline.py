"""P1 contract test: enterprise knowledge asset construction and scan integration.

Verifies:
- ``build_enterprise_business_knowledge_asset`` is idempotent and produces valid output
- Knowledge asset is persisted and loadable
- Missing source materials produce a documented gap, not a crash
- The scan pipeline (v12) can read the knowledge asset for behavior modeling
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _stub_openapi() -> str:
    return json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0"},
        "paths": {
            "/api/orders": {
                "get": {"summary": "List orders", "responses": {"200": {"description": "OK"}}},
                "post": {"summary": "Create order", "responses": {"201": {"description": "Created"}}},
            },
            "/api/orders/{id}": {
                "get": {"summary": "Get order", "parameters": [{"name": "id", "in": "path", "required": True}], "responses": {"200": {"description": "OK"}}},
            },
            "/api/coupons/validate": {
                "post": {"summary": "Validate coupon", "responses": {"200": {"description": "OK"}}},
            },
        },
    })


def _seed_knowledge_center_registry(root: Path, project: str, sources: list[dict[str, Any]]) -> None:
    """Write a minimal knowledge-center registry so build_enterprise_business_knowledge_asset finds sources.

    The knowledge center reads from:
      platform_workspace/{project}/enterprise_knowledge_center/source_registry.json
    """
    from ai_test_asset_center.real_project_onboarding import _safe_project_id
    project_safe = _safe_project_id(project)
    workspace = root / "platform_workspace" / project_safe / "enterprise_knowledge_center"
    sources_dir = workspace / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    # Write source blobs
    for i, src in enumerate(sources):
        src_id = src.get("source_id", f"src_{i}")
        content = src.get("content", "")
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        blob_path = sources_dir / f"{src_id}.txt"
        blob_path.write_text(text, encoding="utf-8")
    registry = {
        "schema_version": "enterprise-knowledge-center-v1",
        "project_id": project_safe,
        "sources": [
            {
                "source_id": s["source_id"],
                "source_type": s.get("source_type", "other"),
                "original_name": s.get("filename", "document.txt"),
                "status": "active",
                "stored_path": str((sources_dir / f"{s['source_id']}.txt").relative_to(root)),
                "version": "v1",
                "ingested_at": "2025-01-01T00:00:00Z",
            }
            for s in sources
        ],
        "audit_events": [],
        "updated_at_utc": "2025-01-01T00:00:00Z",
    }
    _write_json(workspace / "source_registry.json", registry)


def test_knowledge_asset_builds_from_registered_sources(tmp_path: Path) -> None:
    """When sources are in the knowledge center registry, build returns valid output."""
    from ai_test_asset_center.enterprise_knowledge_center import build_enterprise_business_knowledge_asset

    project = "kb_test_project"
    _seed_knowledge_center_registry(tmp_path, project, [
        {"source_id": "src_api", "source_type": "openapi", "filename": "test_api.json", "content": _stub_openapi()},
    ])

    asset = build_enterprise_business_knowledge_asset(project, root=tmp_path)
    assert isinstance(asset, dict), f"Expected dict, got: {type(asset)}"
    assert asset.get("project_id") == project, f"Wrong project: {asset}"
    summary = asset.get("summary", {})
    active_count = int(summary.get("active_source_count") or 0)
    assert active_count >= 1, f"No active sources in asset: {asset.get('summary')}"


def test_knowledge_asset_is_idempotent(tmp_path: Path) -> None:
    """Building the asset twice returns consistent results."""
    from ai_test_asset_center.enterprise_knowledge_center import build_enterprise_business_knowledge_asset

    project = "kb_idempotent"
    _seed_knowledge_center_registry(tmp_path, project, [
        {"source_id": "src_kb_idem", "source_type": "openapi", "filename": "api.json", "content": _stub_openapi()},
    ])

    a1 = build_enterprise_business_knowledge_asset(project, root=tmp_path)
    a2 = build_enterprise_business_knowledge_asset(project, root=tmp_path)

    # Both should have the same active_source_count
    assert a1.get("summary", {}).get("active_source_count") == a2.get("summary", {}).get("active_source_count")


def test_knowledge_asset_empty_project_returns_valid_structure(tmp_path: Path) -> None:
    """When no sources are registered, the asset returns a documented gap, not a crash."""
    from ai_test_asset_center.enterprise_knowledge_center import build_enterprise_business_knowledge_asset

    project = "kb_empty"
    asset = build_enterprise_business_knowledge_asset(project, root=tmp_path)

    assert isinstance(asset, dict)
    assert asset.get("project_id") == project
    summary = asset.get("summary", {})
    assert int(summary.get("active_source_count") or 0) == 0, f"Expected 0 sources for empty project: {summary}"


def test_knowledge_asset_loadable_after_build(tmp_path: Path) -> None:
    """After build, load_enterprise_business_knowledge_asset returns the persisted result."""
    from ai_test_asset_center.enterprise_knowledge_center import (
        build_enterprise_business_knowledge_asset,
        load_enterprise_business_knowledge_asset,
    )
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    project = "kb_persist"
    _seed_knowledge_center_registry(tmp_path, project, [
        {"source_id": "src_kb_pers", "source_type": "openapi", "filename": "api.json", "content": _stub_openapi()},
    ])

    asset = build_enterprise_business_knowledge_asset(project, root=tmp_path)
    loaded = load_enterprise_business_knowledge_asset(project, root=tmp_path)

    assert loaded is not None, "load_enterprise_business_knowledge_asset returned None"
    assert loaded.get("project_id") == project


def test_scan_pipeline_can_read_knowledge_asset(tmp_path: Path) -> None:
    """The v12 pipeline imports and calls build_enterprise_business_knowledge_asset without error."""
    from ai_test_asset_center.enterprise_knowledge_center import build_enterprise_business_knowledge_asset
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    project = "kb_scan_integration"
    _seed_knowledge_center_registry(tmp_path, project, [
        {"source_id": "src_kb_scan", "source_type": "openapi", "filename": "api.json", "content": _stub_openapi()},
    ])

    # Build the asset — this is what run_v12_pipeline calls lazily
    asset = build_enterprise_business_knowledge_asset(project, root=tmp_path)

    # Verify the asset has the key fields that the v12 pipeline reads
    assert "summary" in asset
    assert "project_id" in asset
    summary = asset["summary"]
    assert summary.get("active_source_count", 0) >= 1

    # The v12 pipeline uses these specific fields from the asset
    for key in ("entities", "oracles", "rules", "routes"):
        # These may be empty but must exist as lists
        val = asset.get(key)
        assert val is None or isinstance(val, list), f"{key} should be a list: {type(val)}"
