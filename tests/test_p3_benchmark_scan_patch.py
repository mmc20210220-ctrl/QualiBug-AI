from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: Product Scan Boundary API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _manifest(tmp_path: Path) -> dict[str, str]:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    return register_source_asset(
        "product_scan_boundary",
        "product-scan-boundary-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "p3_seed_defects",
        "seed_bug_defects",
        "seed_defects",
        "p3_http_observations",
    ],
)
def test_product_scan_rejects_evaluator_private_context(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    with pytest.raises(
        ValueError,
        match=f"evaluator_private_context_forbidden:{forbidden_key}",
    ):
        scan(
            "product_scan_boundary",
            root=tmp_path,
            api_doc_text=OPENAPI_TEXT,
            campaign_context={
                "source_manifest": manifest,
                "scope_id": "refund-scope",
                "environment_ref": "staging",
                forbidden_key: [],
            },
        )


def test_package_import_is_side_effect_free() -> None:
    script = """
import importlib

importlib.import_module('ai_test_asset_center')
main = importlib.import_module('ai_test_asset_center.__main__')
assert main.scan.__module__ == 'ai_test_asset_center.__main__'
assert main.scan.__name__ == 'scan'
assert not hasattr(main.scan, '__wrapped__')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
