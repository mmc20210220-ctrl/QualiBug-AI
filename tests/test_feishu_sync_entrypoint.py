from __future__ import annotations

import ast
from pathlib import Path

from ai_test_asset_center.feishu_connector_capability_sync import (
    sync_feishu_connector as capability_sync,
)
from ai_test_asset_center.feishu_connector_sync import sync_feishu_connector


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
LEGACY_MODULE = "feishu_connector_adapter"
CANONICAL_MODULE = "feishu_connector_sync"


def _production_import_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path.name == "feishu_connector_adapter.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = str(node.module or "")
            imports_sync = any(alias.name == "sync_feishu_connector" for alias in node.names)
            if imports_sync and module.endswith(LEGACY_MODULE):
                violations.append(str(path.relative_to(PACKAGE_ROOT.parent)))
    return violations


def test_public_entrypoint_is_capability_aware_implementation() -> None:
    assert sync_feishu_connector is capability_sync
    assert sync_feishu_connector.__module__.endswith(
        "feishu_connector_capability_sync"
    )


def test_product_code_cannot_import_legacy_adapter_sync() -> None:
    assert _production_import_violations() == []


def test_canonical_entrypoint_module_is_explicit() -> None:
    module = __import__(
        f"ai_test_asset_center.{CANONICAL_MODULE}",
        fromlist=["sync_feishu_connector"],
    )
    assert module.sync_feishu_connector is capability_sync
