from __future__ import annotations

"""Phase105M: local preview server for the frontend delivery bundle.

Phase105L creates a customer-demo-ready frontend delivery bundle.  Phase105M
wraps that bundle in a dependency-free local preview service so sales,
implementation, and frontend teams can open one URL and also inspect read-only
metadata endpoints such as health, manifest, page inventory, acceptance status,
handoff docs, and checksums.

The route layer is intentionally pure and unit-testable.  Starting an actual
socket is optional and only used by the CLI when ``--check`` is not supplied.
All payloads go through the existing redaction path, and the static file router
blocks directory traversal.
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
from urllib.parse import unquote, urlparse

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_delivery_bundle import (
    FRONTEND_DELIVERY_CHECKSUMS,
    FRONTEND_DELIVERY_MANIFEST_JSON,
    FRONTEND_DELIVERY_REPORT_JSON,
    FRONTEND_DELIVERY_REPORT_MD,
    FRONTEND_DELIVERY_ZIP,
    HANDOFF_DIR,
    HUB_DIR,
    INTERACTION_ACCEPTANCE_DIR,
    build_frontend_delivery_bundle,
    scan_frontend_delivery_for_secret_leaks,
    validate_frontend_delivery_bundle,
)

PHASE105M_VERSION = "phase105m-frontend-preview-server-v1"

PREVIEW_MANIFEST_JSON = "frontend_preview_server_manifest.json"
PREVIEW_MANIFEST_MD = "frontend_preview_server_manifest.md"

PREVIEW_API_PREFIX = "/api/v1/frontend-preview"

PAGE_ALIAS_PREFIXES: tuple[str, ...] = (
    "assets/",
    "data/",
    "pages/",
)

FORBIDDEN_PREVIEW_PATTERNS: tuple[str, ...] = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "Traceback (most recent call last)",
)


@dataclass(frozen=True)
class FrontendPreviewResponse:
    """Small transport object returned by the pure route layer."""

    status: int
    headers: dict[str, str]
    body: bytes

    @classmethod
    def json(cls, payload: Mapping[str, Any] | list[Any], *, status: int = 200) -> "FrontendPreviewResponse":
        body = json.dumps(redact_value(payload), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        return cls(status=status, headers={"Content-Type": "application/json; charset=utf-8"}, body=body)

    @classmethod
    def text(
        cls,
        text: str,
        *,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
    ) -> "FrontendPreviewResponse":
        return cls(status=status, headers={"Content-Type": content_type}, body=text.encode("utf-8"))

    def json_body(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def _api_response(data: Any, *, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return redact_value(
        {
            "success": True,
            "data": data,
            "error": None,
            "meta": {"version": PHASE105M_VERSION, **dict(meta or {})},
        }
    )


def _api_error(code: str, message: str, *, status: int, details: Mapping[str, Any] | None = None) -> FrontendPreviewResponse:
    return FrontendPreviewResponse.json(
        {
            "success": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "status": status,
                "details": redact_value(dict(details or {})),
            },
            "meta": {"version": PHASE105M_VERSION},
        },
        status=status,
    )


def _not_found(path: str) -> FrontendPreviewResponse:
    return _api_error(
        "NOT_FOUND",
        "未找到请求的前端预览资源或只读 API 路由。",
        status=404,
        details={"path": path},
    )


def _method_not_allowed(method: str) -> FrontendPreviewResponse:
    return _api_error(
        "METHOD_NOT_ALLOWED",
        "Phase105M 前端预览服务当前只开放只读 GET/HEAD/OPTIONS 路由。",
        status=405,
        details={"method": method},
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_text(path: Path, *, limit: int = 60_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return text[:limit]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact_value(dict(payload)), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_relative_path(raw_path: str) -> str | None:
    parsed = urlparse(raw_path)
    path = unquote(parsed.path or "/")
    if path in {"", "/", "/index.html", "/hub", "/hub/"}:
        return f"{HUB_DIR}/index.html"
    stripped = path.lstrip("/")
    normalised = posixpath.normpath(stripped)
    if normalised.startswith("../") or normalised == ".." or normalised.startswith("/"):
        return None
    if normalised == "hub_v2":
        return f"{HUB_DIR}/index.html"
    if normalised.startswith(f"{HUB_DIR}/") or normalised.startswith(f"{HANDOFF_DIR}/") or normalised.startswith(
        f"{INTERACTION_ACCEPTANCE_DIR}/"
    ):
        return normalised
    if normalised in {
        FRONTEND_DELIVERY_MANIFEST_JSON,
        FRONTEND_DELIVERY_REPORT_JSON,
        FRONTEND_DELIVERY_REPORT_MD,
        FRONTEND_DELIVERY_CHECKSUMS,
        FRONTEND_DELIVERY_ZIP,
        PREVIEW_MANIFEST_JSON,
        PREVIEW_MANIFEST_MD,
    }:
        return normalised
    if any(normalised.startswith(prefix) for prefix in PAGE_ALIAS_PREFIXES):
        return f"{HUB_DIR}/{normalised}"
    return normalised


def _content_type(path: Path) -> str:
    guessed = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if guessed.startswith("text/") or path.suffix.lower() in {".js", ".json", ".css", ".html", ".md"}:
        return f"{guessed}; charset=utf-8"
    return guessed


def _checksums_summary(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / FRONTEND_DELIVERY_CHECKSUMS
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    entries: list[dict[str, str]] = []
    for line in lines:
        if not line.strip() or "  " not in line:
            continue
        digest, rel = line.split("  ", 1)
        entries.append({"path": rel, "sha256": digest})
    return {"count": len(entries), "entries": entries[:200], "truncated": len(entries) > 200}


class Phase105FrontendPreviewSite:
    """Preview site combining the static delivery bundle and read-only APIs."""

    def __init__(
        self,
        *,
        scenario: str = "manufacturing",
        bundle_dir: str | Path | None = None,
        build_bundle: bool = True,
    ) -> None:
        self.scenario = scenario
        self.bundle_dir = Path(bundle_dir) if bundle_dir is not None else Path(tempfile.mkdtemp(prefix="phase105_frontend_preview_"))
        if build_bundle:
            self.build_result = build_frontend_delivery_bundle(self.bundle_dir, scenario=scenario)
        else:
            self.build_result = {"passed": True, "scenario": scenario, "bundle_dir": str(self.bundle_dir)}
        self.delivery_report = validate_frontend_delivery_bundle(self.bundle_dir)
        self.delivery_manifest = _read_json(self.bundle_dir / FRONTEND_DELIVERY_MANIFEST_JSON)
        self.hub_manifest = _read_json(self.bundle_dir / HUB_DIR / "frontend_experience_hub_v2_manifest.json")
        self.interaction_acceptance = _read_json(
            self.bundle_dir / INTERACTION_ACCEPTANCE_DIR / "frontend_interaction_acceptance_report.json"
        )
        self.preview_manifest = self._build_preview_manifest()
        self._write_preview_manifest()

    def _build_preview_manifest(self) -> dict[str, Any]:
        pages = self.delivery_manifest.get("pages") or self.hub_manifest.get("pages") or []
        api_routes = {
            "health": f"{PREVIEW_API_PREFIX}/health",
            "manifest": f"{PREVIEW_API_PREFIX}/manifest",
            "pages": f"{PREVIEW_API_PREFIX}/pages",
            "acceptance": f"{PREVIEW_API_PREFIX}/acceptance",
            "delivery": f"{PREVIEW_API_PREFIX}/delivery",
            "handoff": f"{PREVIEW_API_PREFIX}/handoff",
            "checksums": f"{PREVIEW_API_PREFIX}/checksums",
        }
        return redact_value(
            {
                "version": PHASE105M_VERSION,
                "scenario": self.scenario,
                "bundle_dir": str(self.bundle_dir),
                "entrypoint": "/index.html",
                "static_entrypoint": f"{HUB_DIR}/index.html",
                "page_count": len(pages),
                "pages": pages,
                "delivery_passed": bool(self.delivery_report.passed),
                "delivery_score": int(self.delivery_report.score),
                "interaction_acceptance_passed": bool(self.interaction_acceptance.get("passed")),
                "api_routes": api_routes,
                "static_routes": {
                    "root": "/",
                    "hub": f"/{HUB_DIR}/index.html",
                    "test_execution": "/pages/test_execution/test_execution.html",
                    "risk_evidence": "/pages/risk_evidence/risk_evidence.html",
                    "report_roi": "/pages/report_roi/report_roi.html",
                    "delivery_manifest": f"/{FRONTEND_DELIVERY_MANIFEST_JSON}",
                    "delivery_report": f"/{FRONTEND_DELIVERY_REPORT_MD}",
                },
                "redaction_status": "safe" if not scan_frontend_delivery_for_secret_leaks(self.bundle_dir) else "needs_review",
            }
        )

    def _write_preview_manifest(self) -> None:
        _write_json(self.bundle_dir / PREVIEW_MANIFEST_JSON, self.preview_manifest)
        routes = self.preview_manifest["api_routes"]
        text = "\n".join(
            [
                "# Phase105M 前端预览服务清单",
                "",
                f"- version: `{PHASE105M_VERSION}`",
                f"- entrypoint: `{self.preview_manifest['entrypoint']}`",
                f"- delivery_score: `{self.preview_manifest['delivery_score']}`",
                f"- delivery_passed: `{self.preview_manifest['delivery_passed']}`",
                "",
                "## 只读 API",
                *[f"- `{name}`: `{path}`" for name, path in routes.items()],
                "",
                "## 页面入口",
                *[f"- {page.get('label', page.get('key'))}: `/{page.get('url')}`" for page in self.preview_manifest.get("pages", [])],
                "",
            ]
        )
        _write_text(self.bundle_dir / PREVIEW_MANIFEST_MD, text)

    def route(self, raw_path: str, *, method: str = "GET") -> FrontendPreviewResponse:
        method_upper = method.upper()
        if method_upper == "OPTIONS":
            return FrontendPreviewResponse.json(_api_response({"allowed_methods": ["GET", "HEAD", "OPTIONS"]}))
        if method_upper not in {"GET", "HEAD"}:
            return _method_not_allowed(method)
        parsed = urlparse(raw_path)
        path = parsed.path or "/"
        if path.startswith("/api/"):
            return self._route_api(path)
        return self._route_static(raw_path)

    def _route_api(self, path: str) -> FrontendPreviewResponse:
        if path == f"{PREVIEW_API_PREFIX}/health":
            leaks = scan_frontend_delivery_for_secret_leaks(self.bundle_dir)
            return FrontendPreviewResponse.json(
                _api_response(
                    {
                        "status": "ok" if self.delivery_report.passed and not leaks else "needs_review",
                        "version": PHASE105M_VERSION,
                        "scenario": self.scenario,
                        "bundle_dir": str(self.bundle_dir),
                        "entrypoint": "/index.html",
                        "delivery_passed": bool(self.delivery_report.passed),
                        "delivery_score": int(self.delivery_report.score),
                        "page_count": int(self.preview_manifest.get("page_count", 0)),
                        "redaction_status": "safe" if not leaks else "needs_review",
                    }
                )
            )
        if path == f"{PREVIEW_API_PREFIX}/manifest":
            return FrontendPreviewResponse.json(_api_response(self.preview_manifest))
        if path == f"{PREVIEW_API_PREFIX}/pages":
            return FrontendPreviewResponse.json(
                _api_response(
                    {
                        "count": int(self.preview_manifest.get("page_count", 0)),
                        "pages": self.preview_manifest.get("pages", []),
                    }
                )
            )
        if path == f"{PREVIEW_API_PREFIX}/acceptance":
            return FrontendPreviewResponse.json(
                _api_response(
                    {
                        "delivery_report": self.delivery_report.to_dict(),
                        "interaction_acceptance": self.interaction_acceptance,
                    }
                )
            )
        if path == f"{PREVIEW_API_PREFIX}/delivery":
            return FrontendPreviewResponse.json(
                _api_response(
                    {
                        "delivery_manifest": self.delivery_manifest,
                        "zip_archive": str(self.bundle_dir / FRONTEND_DELIVERY_ZIP),
                        "preview_manifest": self.preview_manifest,
                    }
                )
            )
        if path == f"{PREVIEW_API_PREFIX}/handoff":
            handoff_root = self.bundle_dir / HANDOFF_DIR
            docs = []
            for doc_name in ["README_FRONTEND_DELIVERY.md", "DEMO_RUNBOOK.md", "CUSTOMER_WALKTHROUGH_SCRIPT.md", "FRONTEND_DELIVERY_CHECKLIST.md"]:
                doc_path = handoff_root / doc_name
                docs.append(
                    {
                        "name": doc_name,
                        "path": f"{HANDOFF_DIR}/{doc_name}",
                        "exists": doc_path.exists(),
                        "preview": _read_text(doc_path, limit=2000),
                    }
                )
            return FrontendPreviewResponse.json(_api_response({"docs": docs}))
        if path == f"{PREVIEW_API_PREFIX}/checksums":
            return FrontendPreviewResponse.json(_api_response(_checksums_summary(self.bundle_dir)))
        return _not_found(path)

    def _route_static(self, raw_path: str) -> FrontendPreviewResponse:
        rel = _safe_relative_path(raw_path)
        if rel is None:
            return _not_found(raw_path)
        path = (self.bundle_dir / rel).resolve()
        root = self.bundle_dir.resolve()
        if path != root and root not in path.parents:
            return _not_found(raw_path)
        if not path.exists() or not path.is_file():
            return _not_found(raw_path)
        return FrontendPreviewResponse(status=200, headers={"Content-Type": _content_type(path)}, body=path.read_bytes())


def make_frontend_preview_handler(site: Phase105FrontendPreviewSite) -> type[BaseHTTPRequestHandler]:
    """Create a stdlib HTTP handler bound to a preview site instance."""

    class Phase105FrontendPreviewRequestHandler(BaseHTTPRequestHandler):
        server_version = "Phase105FrontendPreviewServer/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            self._send(site.route(self.path, method="GET"))

        def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
            response = site.route(self.path, method="HEAD")
            self._send(response, write_body=False)

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            self._send(site.route(self.path, method="OPTIONS"))

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            self._send(_method_not_allowed("POST"))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        def _send(self, response: FrontendPreviewResponse, *, write_body: bool = True) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            # Restrict CORS to localhost for the preview server — this is a
            # local-only static file server, not a public API.
            _origin = self.headers.get("Origin", "") or "http://127.0.0.1"
            if _origin not in ("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost"):
                _origin = "http://127.0.0.1"
            self.send_header("Access-Control-Allow-Origin", _origin)
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(response.body) if write_body else 0))
            self.end_headers()
            if write_body:
                self.wfile.write(response.body)

    return Phase105FrontendPreviewRequestHandler


def serve_frontend_preview(
    *,
    scenario: str = "manufacturing",
    bundle_dir: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8795,
    build_bundle: bool = True,
    open_browser: bool = False,
    serve_forever: bool = True,
) -> dict[str, Any]:
    """Build or load the Phase105L bundle and optionally serve it locally."""
    site = Phase105FrontendPreviewSite(scenario=scenario, bundle_dir=bundle_dir, build_bundle=build_bundle)
    url = f"http://{host}:{port}/"
    manifest = redact_value({**site.preview_manifest, "url": url})
    if not serve_forever:
        return manifest
    handler = make_frontend_preview_handler(site)
    server = ThreadingHTTPServer((host, int(port)), handler)
    if open_browser:
        webbrowser.open(url)
    print(f"Phase105 frontend preview server: {site.scenario} -> {url}")
    print(json.dumps({"entrypoint": url, "api_routes": site.preview_manifest["api_routes"]}, ensure_ascii=False, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - CLI convenience
        print("\nPhase105 frontend preview server stopped.")
    finally:
        server.server_close()
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Phase105 frontend delivery bundle through a local read-only preview server.")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"], help="Demo scenario to build.")
    parser.add_argument("--bundle-dir", default="outputs/phase105_frontend_delivery_bundle", help="Bundle directory to build or load.")
    parser.add_argument("--host", default="127.0.0.1", help="Preview server host.")
    parser.add_argument("--port", type=int, default=8795, help="Preview server port.")
    parser.add_argument("--open-browser", action="store_true", help="Open the preview URL in the default browser.")
    parser.add_argument("--no-build-bundle", action="store_true", help="Use an existing Phase105L bundle instead of building first.")
    parser.add_argument("--check", action="store_true", help="Only build/load the site and print the manifest without starting a server.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = serve_frontend_preview(
        scenario=args.scenario,
        bundle_dir=args.bundle_dir,
        host=args.host,
        port=args.port,
        build_bundle=not args.no_build_bundle,
        open_browser=args.open_browser,
        serve_forever=not args.check,
    )
    if args.check:
        print("Phase105 frontend preview site generated")
        print(json.dumps({"url": manifest["url"], "entrypoint": manifest["entrypoint"], "page_count": manifest["page_count"]}, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "PHASE105M_VERSION",
    "PREVIEW_API_PREFIX",
    "Phase105FrontendPreviewSite",
    "FrontendPreviewResponse",
    "main",
    "make_frontend_preview_handler",
    "serve_frontend_preview",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
