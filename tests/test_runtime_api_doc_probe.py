"""Runtime API document probing: standard document endpoints on approved targets."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from ai_test_asset_center.runtime_api_doc_probe import (
    _MAX_DOC_BYTES,
    probe_runtime_api_document,
)


class _ProbeServer:
    """Tiny local target exposing configurable document endpoints."""

    def __init__(self, routes: dict[str, tuple[int, str, str]] | None = None) -> None:
        self.routes = routes or {}
        self.requests: list[str] = []
        routes_ref = self.routes
        recorded = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                recorded.append(self.path)
                status, ctype, body = routes_ref.get(
                    self.path, (404, "text/plain", "not found")
                )
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _openapi_document(paths: dict) -> str:
    return json.dumps({"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
                       "paths": paths})


def test_finds_openapi_on_standard_endpoint() -> None:
    doc = _openapi_document({"/api/orders": {"get": {}}, "/api/users": {"post": {}}})
    server = _ProbeServer({"/openapi.json": (200, "application/json", doc)})
    try:
        receipt = probe_runtime_api_document(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "found"
    assert receipt["source_path"] == "/openapi.json"
    assert "/api/orders" in receipt["document_text"]


def test_scans_later_paths_when_first_paths_miss() -> None:
    doc = _openapi_document({"/api/orders": {"get": {}}})
    server = _ProbeServer({"/v2/api-docs": (200, "application/json", doc)})
    try:
        receipt = probe_runtime_api_document(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "found"
    assert receipt["source_path"] == "/v2/api-docs"


def test_not_found_when_all_endpoints_404() -> None:
    server = _ProbeServer()
    try:
        receipt = probe_runtime_api_document(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "not_found"
    assert receipt["attempts"]
    assert all(row["outcome"] in {"http_error", "network_error"} for row in receipt["attempts"])


def test_html_response_is_skipped() -> None:
    server = _ProbeServer({"/openapi.json": (200, "text/html", "<html><body>ui</body></html>")})
    try:
        receipt = probe_runtime_api_document(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "not_found"
    assert receipt["attempts"][0]["outcome"] == "html_response_skipped"


def test_non_contract_json_is_rejected() -> None:
    server = _ProbeServer({"/openapi.json": (200, "application/json", '{"hello": "world"}')})
    try:
        receipt = probe_runtime_api_document(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "not_found"
    assert receipt["attempts"][0]["outcome"] == "not_an_api_contract"


def test_yaml_shaped_contract_is_accepted() -> None:
    yaml_doc = "openapi: 3.0.0\ninfo:\n  title: t\n  version: '1'\npaths:\n  /api/orders:\n    get: {}\n"
    server = _ProbeServer({"/openapi.yaml": (200, "application/yaml", yaml_doc)})
    try:
        receipt = probe_runtime_api_document(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "found"
    assert "/api/orders" in receipt["document_text"]


def test_empty_base_url_is_skipped() -> None:
    receipt = probe_runtime_api_document("")
    assert receipt["status"] == "skipped"
    assert receipt["reason"] == "empty_base_url"


def test_oversize_response_is_rejected() -> None:
    doc = _openapi_document({"/api/big": {"get": {}}})
    padded = doc[:-1] + (" " * (_MAX_DOC_BYTES + 10)) + "}"
    server = _ProbeServer({"/openapi.json": (200, "application/json", padded)})
    try:
        receipt = probe_runtime_api_document(server.base_url)
    finally:
        server.stop()

    assert receipt["status"] == "not_found"
    assert receipt["attempts"][0]["outcome"] == "oversize_response"
