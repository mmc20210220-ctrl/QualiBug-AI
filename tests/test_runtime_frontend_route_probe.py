"""Runtime frontend route probing: bundle route extraction and merge helper."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from ai_test_asset_center.runtime_frontend_route_probe import (
    _extract_routes,
    _route_inventory_document,
    probe_frontend_routes,
)
from ai_test_asset_center.scan_impl_prepare import _merge_route_inventory


class _SpaServer:
    """Tiny local SPA: an index page plus script bundles."""

    def __init__(self, routes: dict[str, tuple[str, str]]) -> None:
        self.routes = routes
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body, ctype = routes.get(self.path, ("not found", "text/plain"))
                payload = body.encode("utf-8")
                self.send_response(200 if self.path in routes else 404)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        return Handler

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _index(bundle_srcs: list[str]) -> str:
    scripts = "".join(f'<script src="{src}"></script>' for src in bundle_srcs)
    return f"<!doctype html><html><head>{scripts}</head><body>spa</body></html>"


def test_extracts_plain_and_template_routes_from_bundle() -> None:
    bundle = (
        'const a = axios.get("/api/orders");'
        'const b = axios.post("/api/orders");'
        'const c = fetch("/api/users/admin/search");'
        'const d = `/api/cart/items/${id}`;'
        'const e = "/api/products/admin/:sku";'
        'const f = "not-an-api";'
    )
    routes = _extract_routes(bundle)
    assert "/api/orders" in routes
    assert "/api/users/admin/search" in routes
    assert "/api/products/admin/:sku" in routes
    assert "not-an-api" not in routes


def test_probe_finds_routes_through_index_and_bundle() -> None:
    bundle = 'fetch("/api/cart/items"); fetch(`/api/refunds/${id}`);'
    server = _SpaServer({
        "/": (_index(["/assets/app.js"]), "text/html"),
        "/assets/app.js": (bundle, "application/javascript"),
    })
    try:
        receipt = probe_frontend_routes(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "found"
    assert receipt["routes"] == ["/api/cart/items", "/api/refunds"]
    assert "/api/cart/items" in receipt["document_text"]


def test_probe_not_found_when_bundle_has_no_routes() -> None:
    server = _SpaServer({
        "/": (_index(["/assets/app.js"]), "text/html"),
        "/assets/app.js": ("console.log('nothing');", "application/javascript"),
    })
    try:
        receipt = probe_frontend_routes(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "not_found"


def test_probe_failed_when_entry_unreachable() -> None:
    server = _SpaServer({})
    try:
        receipt = probe_frontend_routes(f"{server.base_url}/missing")
    finally:
        server.stop()

    assert receipt["status"] in {"failed", "not_found"}


def test_inventory_merges_into_existing_openapi_without_replacing() -> None:
    existing = json.dumps({
        "openapi": "3.0.0",
        "paths": {"/api/orders": {"get": {"summary": "declared"}}},
    })
    inventory = _route_inventory_document(["/api/orders", "/api/cart/items"])
    merged = json.loads(_merge_route_inventory(existing, inventory))

    assert merged["paths"]["/api/orders"]["get"]["summary"] == "declared"
    assert "/api/cart/items" in merged["paths"]


def test_inventory_becomes_machine_contract_when_doc_is_markdown() -> None:
    markdown = "# API_SPEC\n\nGET /api/products\n"
    inventory = _route_inventory_document(["/api/products", "/api/cart"])
    merged = _merge_route_inventory(markdown, inventory)

    parsed = json.loads(merged)
    assert parsed["openapi"] == "3.0.0"
    assert "/api/cart" in parsed["paths"]
