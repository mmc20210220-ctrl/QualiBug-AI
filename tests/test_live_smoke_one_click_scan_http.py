"""Runtime LIVE smoke test for the one-click scan main-chain.

Unlike the static contract tests, this boots the REAL private pilot HTTP service
(the single backend that serves the SPA and the `/api/v1/scan` endpoint the Run
Center calls) on a real localhost TCP port, points it at a REAL system-under-test
HTTP server, and drives an actual `POST /api/v1/scan` over the wire.

It asserts the exact things the completion verifier flagged as unproven:
  * the endpoint really executes (execution_status == "completed"),
  * it captures REAL runtime evidence (auto_har.status == "captured" with entries),
  * real HTTP traffic actually reached the SUT,
  * the downstream command-center projection (the data source Dashboard / Findings
    refresh from after `emitScanCompleted`) reflects the completed run,
  * scan preflight surfaces real readiness reasons instead of silently passing.

Execution is made deterministic (no LLM dependency, no flakiness) by submitting an
explicit source-bound `runtime_scenario_contract` in the scan body — the same
contract the V12 pipeline already understands. This exercises the real gate: the
run still enforces execution_mode / approval / production-safety downstream.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT = "live_smoke_one_click"
SCOPE_ID = "orders-live-scope"
ENVIRONMENT_REF = "customer-staging"

OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: Live Smoke Orders API
  version: 1.0.0
paths:
  /api/orders:
    get:
      summary: List orders
      responses:
        '200': {description: ok}
""".strip()


class _SutHandler(BaseHTTPRequestHandler):
    """Minimal real HTTP system-under-test. Records every call it receives."""

    calls: list[dict[str, Any]] = []

    def log_message(self, *_args: Any) -> None:  # pragma: no cover - silence
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        type(self).calls.append({"method": "GET", "path": self.path})
        if self.path.startswith("/api/orders"):
            self._json(200, {"orders": [{"id": "ord_1", "status": "paid", "amount_cents": 1299}]})
            return
        self._json(404, {"error": "not_found"})


class _LocalHttpServer:
    def __init__(self, handler: type[BaseHTTPRequestHandler], root: Path | None = None) -> None:
        self._handler = handler
        self._root = root

    def __enter__(self) -> str:
        if self._handler is _SutHandler:
            _SutHandler.calls = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        if self._root is not None:
            self.server.qualibug_private_root = self._root  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def _http(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 120.0) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:  # surface the real body, never swallow
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            payload = {"error": f"HTTP {exc.code}"}
        return exc.code, payload


def _runtime_scenario_contract() -> dict[str, Any]:
    return {
        "execution_policy": "safe_read_only",
        "actor": {"id": "customer_qa_lead"},
        "scenarios": [
            {
                "id": "SCN_LIVE_READ_ORDERS",
                "entity": "orders",
                "category": "runtime_contract",
                "steps": [{"method": "GET", "path": "/api/orders", "expected_status": 200}],
                "expected_state": "orders_observed",
            }
        ],
    }


def test_one_click_scan_http_endpoint_executes_real_traffic(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.private_pilot_service import run_private_pilot_service

    # Seed a real registered source so the scan binds a real source manifest.
    manifest = register_source_asset(
        PROJECT,
        "orders-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "customer_qa_lead", "role": "qa_lead"},
    )

    with _LocalHttpServer(_SutHandler) as sut_base_url:
        server = run_private_pilot_service(root=tmp_path, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        api_base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            # 1) Preflight must read real project state and surface actionable reasons,
            #    not silently claim readiness.
            pf_status, preflight = _http("GET", f"{api_base}/api/v1/scan/preflight?project={PROJECT}")
            assert pf_status == 200, preflight
            assert preflight.get("ok") is True
            assert isinstance(preflight.get("reasons"), list)
            # No connector/credentials configured yet → preflight must report blockers.
            codes = {str(r.get("code")) for r in preflight.get("reasons", [])}
            assert codes, "preflight must surface at least one blocker before config"

            # 2) The one-click scan the Run Center fires. Explicit runtime scenario
            #    contract makes execution deterministic against the live SUT.
            scan_status, result = _http(
                "POST",
                f"{api_base}/api/v1/scan",
                {
                    "project_id": PROJECT,
                    "base_url": sut_base_url,
                    "api_doc": OPENAPI_TEXT,
                    "scope_id": SCOPE_ID,
                    "environment_ref": ENVIRONMENT_REF,
                    "execution_mode": "safe_read_only",
                    "source_manifest": {
                        "source_id": manifest["source_id"],
                        "source_hash": manifest["source_hash"],
                    },
                    "test_data_contract": {"strategy": "safe_read_only"},
                    "runtime_scenario_contract": _runtime_scenario_contract(),
                },
            )
            assert scan_status == 200, result

            # 3) Real execution + real captured runtime evidence (not plan_only/blocked/mock).
            assert result.get("execution_status") == "completed", result
            auto_har = result.get("auto_har") or {}
            assert auto_har.get("status") == "captured", auto_har
            entries = auto_har.get("entries") or []
            assert len(entries) >= 1, auto_har

            # 3b) The HTTP scan envelope must carry the honest run-status fields the
            #     Run Center needs to distinguish executed/blocked/plan_only/partial.
            #     (Regression guard: these were previously dropped from the envelope.)
            for honest_key in ("campaign", "coverage_gaps", "runtime_contract", "test_data_plan", "release_gate"):
                assert honest_key in result, f"scan envelope missing honest field: {honest_key}"

            # 4) Real HTTP traffic actually reached the SUT.
            assert any(
                call["method"] == "GET" and call["path"].startswith("/api/orders")
                for call in _SutHandler.calls
            ), _SutHandler.calls

            # 5) Downstream command-center projection (Dashboard/Findings data source)
            #    reflects the completed run.
            cc_status, command_center = _http(
                "GET", f"{api_base}/api/v1/projects/{PROJECT}/command-center"
            )
            assert cc_status == 200, command_center
            assert command_center.get("ok") is True, command_center
            data = command_center.get("data") or {}
            scan_meta = data.get("scan_meta") or {}
            assert scan_meta, "command-center must carry scan_meta after a completed run"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
