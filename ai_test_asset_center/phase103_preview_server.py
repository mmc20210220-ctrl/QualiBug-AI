from __future__ import annotations

"""Phase103V: local preview server for the Enterprise Command Center.

This module wraps the Phase103 demo seed data, static frontend exporter, and
framework-free API facade in a small stdlib-only preview server.  It gives
product, sales, implementation, and frontend teams a one-command way to open a
complete Enterprise Quality Command Center prototype while also exercising the
same V1 JSON endpoints that future web clients will consume.

The server is intentionally dependency-free.  It uses ``http.server`` rather
than FastAPI/Flask so it can run in locked-down customer or field-demo laptops.
All static files and API payloads are generated through the existing redaction
path; token, cookie, password, session, and client_secret raw values are never
served.
"""

import argparse
import json
import mimetypes
import posixpath
import tempfile
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse

from ai_test_asset_center.phase103_command_center_api import EnterpriseCommandCenterAPI, api_error_response, api_response
from ai_test_asset_center.phase103_demo_runner import seed_demo_project
from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase103_static_frontend_exporter import export_static_frontend_bundle

PHASE103V_VERSION = "phase103v-preview-server-v1"


@dataclass(frozen=True)
class PreviewResponse:
    """Small transport object returned by the route layer and HTTP handler."""

    status: int
    headers: dict[str, str]
    body: bytes

    @classmethod
    def json(cls, payload: Mapping[str, Any] | list[Any], *, status: int = 200) -> "PreviewResponse":
        body = json.dumps(redact_value(payload), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        return cls(status=status, headers={"Content-Type": "application/json; charset=utf-8"}, body=body)

    @classmethod
    def text(cls, text: str, *, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> "PreviewResponse":
        return cls(status=status, headers={"Content-Type": content_type}, body=text.encode("utf-8"))

    def json_body(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def _not_found(path: str) -> PreviewResponse:
    return PreviewResponse.json(
        api_error_response(
            RuntimeError("not found"),
            request_id="phase103v_not_found",
        )
        | {
            "error": {
                "code": "NOT_FOUND",
                "message": "未找到请求的预览资源或 API 路由。",
                "status": 404,
                "details": {"path": path},
            }
        },
        status=404,
    )


def _method_not_allowed(method: str) -> PreviewResponse:
    return PreviewResponse.json(
        {
            "success": False,
            "data": None,
            "error": {
                "code": "METHOD_NOT_ALLOWED",
                "message": "Phase103 预览服务当前只开放只读 GET 路由。",
                "status": 405,
                "details": {"method": method},
            },
            "meta": {"version": PHASE103V_VERSION},
        },
        status=405,
    )


def _safe_relative_path(raw_path: str) -> str | None:
    parsed = urlparse(raw_path)
    path = unquote(parsed.path or "/")
    if path == "/":
        return "index.html"
    normalised = posixpath.normpath(path.lstrip("/"))
    if normalised.startswith("../") or normalised == ".." or normalised.startswith("/"):
        return None
    return normalised


class Phase103PreviewSite:
    """In-memory preview site combining static pages and V1 API endpoints."""

    def __init__(self, *, scenario: str = "manufacturing", static_dir: str | Path | None = None) -> None:
        self.scenario = scenario
        self.static_dir = Path(static_dir) if static_dir is not None else Path(tempfile.mkdtemp(prefix="phase103_preview_"))
        self.api = EnterpriseCommandCenterAPI()
        self.bundle = seed_demo_project(self.api, scenario=scenario)
        self.project_id = str(self.bundle["project_id"])
        self.static_manifest = export_static_frontend_bundle(self.bundle, self.static_dir)
        self.preview_manifest = self._build_preview_manifest()

    def _build_preview_manifest(self) -> dict[str, Any]:
        base_api = f"/api/v1/projects/{self.project_id}"
        api_routes = {
            "health": "/api/v1/preview/health",
            "manifest": "/api/v1/preview/manifest",
            "projects": "/api/v1/projects",
            "project": base_api,
            "onboarding": f"{base_api}/onboarding",
            "business_model": f"{base_api}/business-model",
            "environment": f"{base_api}/environment/readiness",
            "test_plan": f"{base_api}/test-plan",
            "command_center": f"{base_api}/command-center",
            "live_map": f"{base_api}/live-map",
            "risks": f"{base_api}/risks",
            "value_metrics": f"{base_api}/value-metrics",
            "executive_report": f"{base_api}/reports/executive",
        }
        return {
            "version": PHASE103V_VERSION,
            "scenario": self.scenario,
            "project_id": self.project_id,
            "entrypoint": "/index.html",
            "static_dir": str(self.static_dir),
            "static_manifest": self.static_manifest,
            "api_routes": api_routes,
            "redaction_status": "safe",
        }

    # ------------------------------------------------------------------
    # Pure route layer, easy to unit-test without opening a socket.
    # ------------------------------------------------------------------
    def route(self, raw_path: str, *, method: str = "GET") -> PreviewResponse:
        if method.upper() != "GET":
            return _method_not_allowed(method)
        parsed = urlparse(raw_path)
        path = parsed.path or "/"
        if path.startswith("/api/"):
            return self._route_api(path, parse_qs(parsed.query))
        return self._route_static(raw_path)

    def _route_api(self, path: str, query: Mapping[str, Sequence[str]]) -> PreviewResponse:
        try:
            if path == "/api/v1/preview/health":
                return PreviewResponse.json(
                    api_response(
                        {
                            "status": "ok",
                            "version": PHASE103V_VERSION,
                            "scenario": self.scenario,
                            "project_id": self.project_id,
                            "redaction_status": "safe",
                        }
                    )
                )
            if path == "/api/v1/preview/manifest":
                return PreviewResponse.json(api_response(self.preview_manifest))
            if path == "/api/v1/projects":
                return PreviewResponse.json(self.api.list_projects())

            prefix = "/api/v1/projects/"
            if not path.startswith(prefix):
                return _not_found(path)
            remainder = path[len(prefix) :]
            parts = [part for part in remainder.split("/") if part]
            if not parts:
                return _not_found(path)
            project_id = parts[0]
            tail = parts[1:]
            if project_id != self.project_id:
                return _not_found(path)
            if not tail:
                return PreviewResponse.json(self.api.get_project(project_id))
            if tail == ["onboarding"]:
                return PreviewResponse.json(self.api.get_onboarding(project_id))
            if tail == ["business-model"]:
                return PreviewResponse.json(self.api.get_business_model(project_id))
            if tail == ["environment", "readiness"]:
                return PreviewResponse.json(self.api.get_environment_readiness(project_id))
            if tail == ["test-plan"]:
                return PreviewResponse.json(self.api.get_test_plan(project_id))
            if tail == ["command-center"]:
                return PreviewResponse.json(self.api.get_command_center(project_id))
            if tail == ["live-map"]:
                return PreviewResponse.json(self.api.get_live_map(project_id))
            if tail == ["live-map", "events"]:
                events = self.api.get_live_map(project_id).get("data", {}).get("events", [])
                since = (query.get("since") or [None])[0]
                if since:
                    events = [event for event in events if str(event.get("timestamp", "")) >= str(since)]
                return PreviewResponse.json(api_response(events))
            if tail == ["risks"]:
                return PreviewResponse.json(self.api.list_risks(project_id))
            if len(tail) == 2 and tail[0] == "risks":
                return PreviewResponse.json(self.api.get_risk_detail(project_id, tail[1]))
            if tail == ["value-metrics"]:
                return PreviewResponse.json(self.api.get_value_metrics(project_id))
            if tail == ["reports", "executive"]:
                return PreviewResponse.json(self.api.get_report(project_id))
            return _not_found(path)
        except Exception as exc:  # pragma: no cover - defensive envelope path
            return PreviewResponse.json(api_error_response(exc), status=500)

    def _route_static(self, raw_path: str) -> PreviewResponse:
        rel = _safe_relative_path(raw_path)
        if rel is None:
            return _not_found(raw_path)
        path = (self.static_dir / rel).resolve()
        root = self.static_dir.resolve()
        if root not in path.parents and path != root:
            return _not_found(raw_path)
        if not path.exists() or not path.is_file():
            return _not_found(raw_path)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if content_type.startswith("text/") or path.suffix in {".js", ".json", ".css", ".html", ".md"}:
            content_type = f"{content_type}; charset=utf-8"
        return PreviewResponse(status=200, headers={"Content-Type": content_type}, body=path.read_bytes())


def make_preview_handler(site: Phase103PreviewSite) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to a preview site instance."""

    class Phase103PreviewRequestHandler(BaseHTTPRequestHandler):
        server_version = "Phase103PreviewServer/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            self._send(site.route(self.path, method="GET"))

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            self._send(_method_not_allowed("POST"))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            # Keep demos and tests quiet.  Users still see the startup URL.
            return

        def _send(self, response: PreviewResponse) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

    return Phase103PreviewRequestHandler


def serve_preview(
    *,
    scenario: str = "manufacturing",
    output_dir: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = False,
    serve_forever: bool = True,
) -> dict[str, Any]:
    """Build a preview site and optionally serve it through a local HTTP server."""
    site = Phase103PreviewSite(scenario=scenario, static_dir=output_dir)
    url = f"http://{host}:{port}/"
    manifest = {**site.preview_manifest, "url": url}
    if not serve_forever:
        return manifest
    handler = make_preview_handler(site)
    server = ThreadingHTTPServer((host, int(port)), handler)
    if open_browser:
        webbrowser.open(url)
    print(f"Phase103 preview server: {site.scenario} -> {url}")
    print(json.dumps({"project_id": site.project_id, "api_routes": site.preview_manifest["api_routes"]}, ensure_ascii=False, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - CLI convenience
        print("\nPhase103 preview server stopped.")
    finally:
        server.server_close()
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a local Phase103 Enterprise Command Center preview site and API.")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"], help="Demo scenario to seed.")
    parser.add_argument("--output-dir", default="outputs/phase103_preview_site", help="Directory for generated static files.")
    parser.add_argument("--host", default="127.0.0.1", help="Preview server host.")
    parser.add_argument("--port", type=int, default=8787, help="Preview server port.")
    parser.add_argument("--open-browser", action="store_true", help="Open the preview URL in the default browser.")
    parser.add_argument("--check", action="store_true", help="Only build the preview site and print the manifest without starting a server.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = serve_preview(
        scenario=args.scenario,
        output_dir=args.output_dir,
        host=args.host,
        port=args.port,
        open_browser=args.open_browser,
        serve_forever=not args.check,
    )
    if args.check:
        print("Phase103 preview site generated")
        print(json.dumps({"url": manifest["url"], "entrypoint": manifest["entrypoint"], "project_id": manifest["project_id"]}, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "PHASE103V_VERSION",
    "Phase103PreviewSite",
    "PreviewResponse",
    "main",
    "make_preview_handler",
    "serve_preview",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
