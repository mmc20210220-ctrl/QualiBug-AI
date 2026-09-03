from __future__ import annotations

"""HTTP product composition without importing Bug Discovery authorities."""

from urllib.parse import unquote, urlparse

from products.catalog import get_product_catalog
from products.requirement_intelligence import analyze_knowledge_asset


def _requirement_analysis_project(path: str) -> str:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == "requirement-intelligence"
    ):
        return parts[3].strip()
    return ""


class ProductCatalogHttpMixin:
    """Expose product catalog and Requirement Intelligence before legacy routing."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        requirement_project = _requirement_analysis_project(parsed.path)
        is_catalog = parsed.path == "/api/v1/products"
        if not is_catalog and not requirement_project:
            return super().do_GET()

        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None

        if is_catalog:
            return self._json(
                {
                    "ok": True,
                    "products": list(get_product_catalog()),
                }
            )

        if not self._require_project_scope(requirement_project):
            return None
        if not self._require_known_project(requirement_project, root):
            return None

        asset = self._load_merged_knowledge_asset(requirement_project, root, actor)
        analysis = analyze_knowledge_asset(asset)
        analysis["project_id"] = requirement_project
        return self._json({"ok": True, "data": analysis})
