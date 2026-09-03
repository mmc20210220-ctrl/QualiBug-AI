from __future__ import annotations

"""HTTP product-catalog composition without importing Bug Discovery authorities."""

from urllib.parse import urlparse

from products.catalog import get_product_catalog


class ProductCatalogHttpMixin:
    """Expose platform product surfaces before legacy product-specific routing."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/v1/products":
            return super().do_GET()

        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None

        return self._json(
            {
                "ok": True,
                "products": list(get_product_catalog()),
            }
        )
