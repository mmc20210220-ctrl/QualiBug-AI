"""P1 contract test: preflight distinguishes "has sources" from "has executable API spec".

Before this round the preflight only reported NO_SOURCE when zero sources existed,
but silently claimed "ready" when the customer had uploaded a PRD without any
OpenAPI / Swagger / Postman — the scan would then load the PRD as api_doc_text,
producing zero executable probes with no explanation.

After the fix:
- ``NO_API_SPEC`` is surfaced when sources exist but none are API specs.
- ``_load_registered_source`` prefers OpenAPI-type sources for api_doc_text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _mini_registry(root: Path, project: str, assets: list[dict[str, Any]]) -> None:
    """Write a minimal source registry so list_source_assets returns known data."""
    _safe = project.replace("/", "_")
    base = root / "platform_workspace" / _safe / "source_registry"
    blobs = base / "blobs"
    assets_dict: dict[str, dict[str, Any]] = {}
    for a in assets:
        aid = a["source_id"]
        content = a.get("content", f"mock content for {aid}")
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        sh = __import__("hashlib").sha256(text.encode()).hexdigest()
        assets_dict[aid] = {
            "source_id": aid,
            "source_type": a.get("source_type", "other"),
            "latest_source_hash": sh,
            "latest_version_id": f"srcv_{sh[:24]}",
            "versions": [{
                "version_id": f"srcv_{sh[:24]}",
                "source_hash": sh,
                "byte_count": len(text.encode()),
                "source_type": a.get("source_type", "other"),
                "source_origin": "test",
                "filename": f"{aid}.txt",
                "registered_at_utc": "2025-01-01T00:00:00Z",
                "registered_by": {"name": "test", "role": "test"},
                "blob_ref": f"platform_workspace/{_safe}/source_registry/blobs/{sh}.txt",
            }],
            "updated_at_utc": "2025-01-01T00:00:00Z",
        }
        # Write blob
        (blobs / f"{sh}.txt").parent.mkdir(parents=True, exist_ok=True)
        (blobs / f"{sh}.txt").write_text(text, encoding="utf-8")
    registry = {
        "schema_version": "enterprise-source-registry-v1",
        "project_id": _safe,
        "assets": assets_dict,
        "updated_at_utc": "2025-01-01T00:00:00Z",
    }
    _write_json(base / "registry.json", registry)


def test_preflight_reports_no_api_spec_when_only_prd_exists(tmp_path: Path) -> None:
    """When sources exist but none are OpenAPI-type, preflight must surface NO_API_SPEC."""
    project = "prd_only_project"
    _mini_registry(tmp_path, project, [
        {"source_id": "prd_v1", "source_type": "prd",
         "content": "订单必须只能查看本人数据，支付金额必须等于订单金额"},
    ])

    from ai_test_asset_center.private_pilot_service import PrivatePilotHandler
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}
    # Simulate _handle_scan_preflight without the full HTTP stack
    handler._json = lambda payload, status=200: payload  # type: ignore[assignment]

    handler.wfile = type("FakeWfile", (), {"write": lambda self, data: None})()
    handler.send_response = lambda code: None
    handler.send_header = lambda k, v: None
    handler.end_headers = lambda: None

    # Call via the internal method directly
    import io
    handler.wfile = io.BytesIO()

    from ai_test_asset_center.private_pilot_service import PrivatePilotHandler as _PH
    # Use the module-level function pattern: build a fake request context
    # and call _handle_scan_preflight directly
    inst = _PH.__new__(_PH)
    inst.headers = {}

    # Monkey-patch _json to capture the payload
    _captured: dict[str, Any] = {}
    def _fake_json(payload: Any, status: int = 200) -> None:
        nonlocal _captured
        _captured = payload
    inst._json = _fake_json  # type: ignore[assignment]
    inst.wfile = io.BytesIO()
    inst.send_response = lambda code: None
    inst.send_header = lambda k, v: None
    inst.end_headers = lambda: None

    inst._handle_scan_preflight(project, tmp_path)

    assert _captured.get("ok") is True
    assert _captured.get("ready") is False
    reasons = _captured.get("reasons", [])
    codes = {r["code"] for r in reasons}
    assert "NO_API_SPEC" in codes, f"Expected NO_API_SPEC in reasons, got: {codes}"
    # Should still have NO_CREDENTIALS and NO_TARGET
    assert "NO_CREDENTIALS" in codes
    assert "NO_TARGET" in codes


def test_preflight_ready_when_openapi_exists(tmp_path: Path) -> None:
    """When an OpenAPI source exists, preflight should NOT report NO_API_SPEC."""
    project = "with_openapi"
    _mini_registry(tmp_path, project, [
        {"source_id": "prd_v1", "source_type": "prd",
         "content": "订单必须只能查看本人数据"},
        {"source_id": "openapi_v1", "source_type": "openapi",
         "content": '{"openapi":"3.0.0","paths":{"/api/orders":{"get":{}}}}'},
    ])

    from ai_test_asset_center.private_pilot_service import PrivatePilotHandler
    import io
    inst = PrivatePilotHandler.__new__(PrivatePilotHandler)
    inst.headers = {}
    _captured: dict[str, Any] = {}
    def _fake_json(payload: Any, status: int = 200) -> None:
        nonlocal _captured
        _captured = payload
    inst._json = _fake_json  # type: ignore[assignment]
    inst.wfile = io.BytesIO()
    inst.send_response = lambda code: None
    inst.send_header = lambda k, v: None
    inst.end_headers = lambda: None

    inst._handle_scan_preflight(project, tmp_path)

    reasons = _captured.get("reasons", [])
    codes = {r["code"] for r in reasons}
    assert "NO_API_SPEC" not in codes, f"Should NOT have NO_API_SPEC when OpenAPI exists, got: {codes}"
    # Still expect NO_CREDENTIALS and NO_TARGET (we didn't set those up)
    assert "NO_CREDENTIALS" in codes
    assert "NO_TARGET" in codes


def test_load_registered_source_prefers_openapi_over_prd(tmp_path: Path) -> None:
    """_load_registered_source must pick OpenAPI over PRD when both exist."""
    project = "mixed_sources"
    prd_content = "订单必须只能查看本人数据"
    openapi_content = '{"openapi":"3.0.0","paths":{"/api/orders":{"get":{}}}}'

    _mini_registry(tmp_path, project, [
        {"source_id": "prd_v1", "source_type": "prd", "content": prd_content},
        {"source_id": "openapi_v1", "source_type": "openapi", "content": openapi_content},
    ])

    from ai_test_asset_center.__main__ import _load_registered_source

    context: dict[str, Any] = {}
    result = _load_registered_source(project, tmp_path, context)

    # Must return the OpenAPI content, not the PRD
    assert "openapi" in result.lower() or '"paths"' in result, (
        f"Expected OpenAPI content, got: {result[:200]}"
    )
    # Verify the manifest was updated in context
    manifest = context.get("source_manifest", {})
    assert manifest.get("source_id") == "openapi_v1", (
        f"Expected source_id 'openapi_v1', got: {manifest}"
    )


def test_load_registered_source_falls_back_to_any_source(tmp_path: Path) -> None:
    """When no OpenAPI exists, fall back to any registered source (e.g. PRD)."""
    project = "prd_fallback"
    prd_content = "订单必须只能查看本人数据，支付金额必须等于订单金额"

    _mini_registry(tmp_path, project, [
        {"source_id": "prd_v1", "source_type": "prd", "content": prd_content},
    ])

    from ai_test_asset_center.__main__ import _load_registered_source

    context: dict[str, Any] = {}
    result = _load_registered_source(project, tmp_path, context)

    assert len(result) > 0, "Should return PRD content as fallback when no OpenAPI exists"
    assert "订单" in result
