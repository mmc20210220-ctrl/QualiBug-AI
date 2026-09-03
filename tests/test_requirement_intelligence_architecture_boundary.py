from __future__ import annotations

import ast
from pathlib import Path

from products.requirement_intelligence import get_product_manifest


PRODUCT_ROOT = Path(__file__).resolve().parents[1] / "products" / "requirement_intelligence"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _is_forbidden(module: str) -> bool:
    if module.startswith("products.bug_discovery"):
        return True
    if module == "ai_test_asset_center.v12_pipeline" or module.startswith(
        "ai_test_asset_center.v12_pipeline."
    ):
        return True
    if module.startswith("ai_test_asset_center.private_pilot_") and "_patch" in module:
        return True
    if module.startswith("ai_test_asset_center.discovery_runtime_semantic_binding"):
        return True
    return False


def test_requirement_intelligence_manifest_is_bounded_and_evidence_required() -> None:
    manifest = get_product_manifest()

    assert manifest["product_id"] == "requirement_intelligence"
    assert manifest["status"] == "primary"
    assert manifest["evidence_required"] is True
    assert set(manifest["supported_findings"]) == {
        "requirement_conflict",
        "requirement_missing",
        "requirement_ambiguity",
    }


def test_requirement_intelligence_does_not_import_bug_discovery_authorities() -> None:
    violations: list[str] = []
    for path in sorted(PRODUCT_ROOT.rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if _is_forbidden(module):
                violations.append(f"{path.relative_to(PRODUCT_ROOT)} -> {module}")

    assert violations == [], (
        "Requirement Intelligence must remain upstream of Bug Discovery execution/patch "
        "authorities. Forbidden imports: " + ", ".join(violations)
    )
