from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P3 Patch API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _seed_defects() -> list[dict]:
    return [
        {
            "id": "BUG_REFUND_UNPAID_ORDER",
            "title": "Unpaid order can be refunded",
            "kind": "should_reject_but_succeeded",
            "method": "POST",
            "path": "/api/refunds",
            "severity": "P0",
        }
    ]


def _observations() -> list[dict]:
    return [
        {
            "method": "POST",
            "path": "/api/refunds",
            "status": 201,
            "body": {"refund_id": "r_1", "order_id": "ord_unpaid"},
        }
    ]


def _manifest(tmp_path: Path) -> dict:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    return register_source_asset(
        "p3_patch_project",
        "p3-patch-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_scan_output_contains_p3_seed_bug_benchmark(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p3_patch_project",
        root=tmp_path,
        api_doc_text=OPENAPI_TEXT,
        campaign_context={
            "source_manifest": manifest,
            "scope_id": "refund-scope",
            "environment_ref": "benchmark",
            "p3_seed_defects": _seed_defects(),
            "p3_http_observations": _observations(),
        },
    )

    benchmark = result["p3_seed_bug_benchmark"]
    assert benchmark["schema_version"] == "p3-seed-bug-benchmark-v1"
    assert benchmark["found_count"] == 1
    assert benchmark["missed_count"] == 0
    assert benchmark["detection_rate"] == 1.0
    assert benchmark["findings"][0]["seed_id"] == "BUG_REFUND_UNPAID_ORDER"


def test_scan_result_file_contains_p3_seed_bug_benchmark(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p3_patch_project",
        root=tmp_path,
        api_doc_text=OPENAPI_TEXT,
        campaign_context={
            "source_manifest": manifest,
            "scope_id": "refund-scope",
            "environment_ref": "benchmark",
            "p3_seed_defects": _seed_defects(),
            "p3_http_observations": _observations(),
        },
    )
    saved = json.loads((tmp_path / "platform_outputs" / "p3_patch_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p3_seed_bug_benchmark"]["found_count"] == 1
    assert saved["p3_seed_bug_benchmark"]["grade"] == "passed"


def test_scan_without_seed_defects_does_not_add_p3_benchmark(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p3_patch_project",
        root=tmp_path,
        api_doc_text=OPENAPI_TEXT,
        campaign_context={
            "source_manifest": manifest,
            "scope_id": "refund-scope",
            "environment_ref": "benchmark",
            "p3_http_observations": _observations(),
        },
    )

    assert "p3_seed_bug_benchmark" not in result
