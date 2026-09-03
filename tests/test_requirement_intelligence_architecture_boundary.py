from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ai_test_asset_center.private_pilot_product_catalog import ProductCatalogHttpMixin
from products.catalog import get_product_catalog
from products.requirement_intelligence import get_product_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = REPO_ROOT / "products" / "requirement_intelligence"
SERVICE_PATH = REPO_ROOT / "ai_test_asset_center" / "private_pilot_service.py"


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


def test_product_catalog_demotes_bug_discovery_without_importing_runtime() -> None:
    catalog = {item["product_id"]: item for item in get_product_catalog()}

    assert catalog["requirement_intelligence"]["status"] == "primary"
    assert catalog["requirement_intelligence"]["entry_mode"] == "analysis"
    assert catalog["bug_discovery"]["status"] == "experimental"
    assert catalog["bug_discovery"]["entry_mode"] == "advanced_runtime"


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


def test_product_catalog_mixin_precedes_legacy_http_router_in_composition_root() -> None:
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"), filename=str(SERVICE_PATH))
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivatePilotHandler"
    )
    base_names = [base.id for base in handler.bases if isinstance(base, ast.Name)]

    assert "ProductCatalogHttpMixin" in base_names
    assert "HttpRoutingMixin" in base_names
    assert base_names.index("ProductCatalogHttpMixin") < base_names.index("HttpRoutingMixin")


class _FallbackGet:
    fallback_called = False

    def do_GET(self) -> Any:  # noqa: N802
        self.fallback_called = True
        return "fallback"


class _CatalogHarness(ProductCatalogHttpMixin, _FallbackGet):
    def __init__(self, *, path: str, authenticated: bool = True) -> None:
        self.path = path
        self.authenticated = authenticated
        self.initialized = False
        self.fallback_called = False
        self.response: tuple[dict[str, Any], int] | None = None

    def _init_request_context(self) -> None:
        self.initialized = True

    def _root(self) -> Path:
        return REPO_ROOT

    def _require_actor(self) -> dict[str, str] | None:
        return {"role": "viewer"} if self.authenticated else None

    def _require_tenant(self, root: Path) -> str | None:
        assert root == REPO_ROOT
        return "tenant-test" if self.authenticated else None

    def _json(self, payload: dict[str, Any], status: int = 200, **_: Any) -> dict[str, Any]:
        self.response = (payload, status)
        return payload


def test_product_catalog_route_is_authenticated_and_returns_catalog() -> None:
    handler = _CatalogHarness(path="/api/v1/products")

    payload = handler.do_GET()

    assert handler.initialized is True
    assert handler.fallback_called is False
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    products = {item["product_id"]: item for item in payload["products"]}
    assert products["requirement_intelligence"]["status"] == "primary"
    assert products["bug_discovery"]["status"] == "experimental"


def test_product_catalog_route_rejects_unauthenticated_request() -> None:
    handler = _CatalogHarness(path="/api/v1/products", authenticated=False)

    assert handler.do_GET() is None
    assert handler.fallback_called is False
    assert handler.response is None


def test_product_catalog_mixin_delegates_unrelated_get_routes() -> None:
    handler = _CatalogHarness(path="/api/health")

    assert handler.do_GET() == "fallback"
    assert handler.fallback_called is True
