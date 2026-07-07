from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _expect_text_steps(success_criteria: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    raw_items: list[Any] = []
    for key in ("text", "texts", "expect_text", "expect_texts"):
        value = success_criteria.get(key)
        if isinstance(value, list):
            raw_items.extend(value)
        elif value not in (None, ""):
            raw_items.append(value)
    for item in raw_items:
        if isinstance(item, dict):
            text = _text(item.get("text"))
            if not text:
                continue
            steps.append(
                {
                    "action": "expect_text",
                    "selector": _text(item.get("selector")) or "body",
                    "text": text,
                    "timeout_ms": int(item.get("timeout_ms") or 10_000),
                }
            )
            continue
        text = _text(item)
        if text:
            steps.append({"action": "expect_text", "selector": "body", "text": text, "timeout_ms": 10_000})
    return steps


class PageAgentBridgeApp:
    def __init__(self, *, root: Path, mode: str = "stub_page_agent") -> None:
        self.root = Path(root)
        self.mode = mode

    def handle(self, method: str, path: str, body: bytes = b"") -> tuple[int, dict[str, str], bytes]:
        if method == "OPTIONS":
            return self._response(204, {})
        if method == "GET" and path == "/health":
            return self._response(
                200,
                {
                    "ok": True,
                    "service": "page_agent_bridge",
                    "mode": self.mode,
                    "root": str(self.root),
                },
            )
        if method == "POST" and path == "/execute":
            try:
                payload = json.loads(body.decode("utf-8") or "null")
            except json.JSONDecodeError:
                return self._response(400, {"status": "failed", "reason": "INVALID_JSON"})
            if not isinstance(payload, dict):
                return self._response(400, {"status": "failed", "reason": "INVALID_PAYLOAD"})
            result = self._execute(payload)
            status_code = 200 if str(result.get("status") or "") != "failed" else 500
            return self._response(status_code, result)
        return self._response(404, {"status": "failed", "reason": "NOT_FOUND"})

    def _execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = _text(payload.get("metadata", {}).get("bridge_mode")) or self.mode
        if mode == "page_agent_browser_plan":
            return self._execute_page_agent_browser_plan(payload)
        if mode == "playwright_browser_plan":
            return self._execute_playwright_proxy(payload)
        return self._execute_stub_page_agent(payload)

    def _build_page_agent_browser_plan(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        runtime_contract = _as_dict(payload.get("runtime_contract"))
        execution_mode = _text(payload.get("execution_mode")) or _text(runtime_contract.get("execution_mode")) or "safe_read_only"
        metadata = _as_dict(payload.get("metadata"))
        explicit_plan = next(
            (
                candidate
                for candidate in (
                    _as_dict(payload.get("browser_plan")),
                    _as_dict(metadata.get("page_agent_browser_plan")),
                    _as_dict(metadata.get("browser_plan")),
                )
                if candidate
            ),
            {},
        )
        if explicit_plan:
            plan = dict(explicit_plan)
            plan.setdefault("execution_mode", execution_mode)
            if plan["execution_mode"] == "approved_sandbox_write":
                plan["write_approved"] = plan.get("write_approved") is True
            return plan, "explicit_browser_plan"

        start_url = _text(payload.get("start_url")) or _text(runtime_contract.get("approved_base_url"))
        if not start_url:
            return None, "START_URL_MISSING"
        if execution_mode == "approved_sandbox_write":
            return None, "PAGE_AGENT_ACTION_PLAN_MISSING"

        success_criteria = _as_dict(payload.get("success_criteria"))
        steps: list[dict[str, Any]] = [
            {"action": "goto", "url": start_url, "wait_until": "networkidle"},
            {"action": "wait_for_load", "state": "networkidle"},
        ]
        expected_url = _text(
            success_criteria.get("url_pattern")
            or success_criteria.get("expect_url")
            or success_criteria.get("pattern")
            or success_criteria.get("url")
        )
        if expected_url:
            steps.append({"action": "expect_url", "pattern": expected_url, "timeout_ms": 10_000})
        steps.extend(_expect_text_steps(success_criteria))
        steps.append({"action": "screenshot", "full_page": True})
        return {
            "execution_mode": execution_mode,
            "write_approved": False,
            "steps": steps,
        }, "derived_observation_plan"

    def _execute_page_agent_browser_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = _text(payload.get("project_id")) or "unscoped"
        request_id = _text(payload.get("request_id")) or f"bridge_{int(time.time() * 1000)}"
        runtime_contract = _as_dict(payload.get("runtime_contract"))
        approved_base_url = _text(runtime_contract.get("approved_base_url"))
        start_url = _text(payload.get("start_url")) or approved_base_url
        plan, plan_source = self._build_page_agent_browser_plan(payload)
        if not plan:
            return {
                "status": "blocked",
                "reason": plan_source,
                "execution_status": "not_executed",
                "confirmation_status": "blocked",
                "current_url": start_url,
                "history": [
                    {
                        "type": "blocked",
                        "tool": "page_agent_browser_plan",
                        "input": {"start_url": start_url},
                        "output": plan_source,
                    }
                ],
                "console": [],
                "network": [],
                "artifacts": [],
                "findings": [],
                "duration_ms": 0,
            }
        try:
            from .browser_execution import execute_browser_plan

            browser_result = execute_browser_plan(
                project_id,
                plan,
                runtime_contract,
                root=self.root,
                run_id=f"bridge_{request_id}",
            )
        except Exception as exc:
            return {"status": "failed", "reason": f"PAGE_AGENT_BROWSER_PLAN_ERROR:{type(exc).__name__}"}
        metadata = _as_dict(payload.get("metadata"))
        created_data = next(
            (
                candidate
                for candidate in (
                    _as_dict(metadata.get("created_data")),
                    _as_dict(metadata.get("page_agent_created_data")),
                    _as_dict(metadata.get("stub_created_data")),
                )
                if candidate
            ),
            {},
        )
        return {
            "status": "executed" if str(browser_result.get("status") or "") == "executed" else str(browser_result.get("status") or "failed"),
            "reason": str(browser_result.get("reason") or ""),
            "execution_status": str(browser_result.get("execution_status") or "failed"),
            "confirmation_status": str(browser_result.get("confirmation_status") or "candidate"),
            "current_url": start_url,
            "history": [
                {"type": "thinking", "message": f"page_agent_browser_plan accepted task:{_text(payload.get('task'), 160)}"},
                {
                    "type": "executed",
                    "tool": "page_agent_browser_plan",
                    "input": {"start_url": start_url, "plan_source": plan_source},
                    "output": f"browser_execution:{browser_result.get('status')}",
                },
            ],
            "console": _as_list(browser_result.get("console")),
            "network": _as_list(browser_result.get("network")),
            "artifacts": [
                {"artifact_type": "trace", "ref": _text(browser_result.get("trace_ref"))},
                {"artifact_type": "screenshot", "ref": _text(browser_result.get("screenshot_ref"))},
            ],
            "findings": [],
            "created_data": created_data,
            "duration_ms": int(browser_result.get("duration_ms") or 0),
        }

    def _execute_playwright_proxy(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = _text(payload.get("project_id")) or "unscoped"
        request_id = _text(payload.get("request_id")) or f"bridge_{int(time.time() * 1000)}"
        runtime_contract = _as_dict(payload.get("runtime_contract"))
        approved_base_url = _text(runtime_contract.get("approved_base_url"))
        start_url = _text(payload.get("start_url")) or approved_base_url
        if not start_url:
            return {"status": "failed", "reason": "START_URL_MISSING"}
        plan = {
            "execution_mode": _text(payload.get("execution_mode")) or "safe_read_only",
            "steps": [
                {"action": "goto", "url": start_url, "wait_until": "networkidle"},
                {"action": "wait_for_load", "state": "networkidle"},
                {"action": "screenshot", "full_page": True},
            ],
            "write_approved": _text(payload.get("execution_mode")) == "approved_sandbox_write",
        }
        try:
            from .browser_execution import execute_browser_plan

            browser_result = execute_browser_plan(
                project_id,
                plan,
                {
                    "status": "approved",
                    "approved_base_url": approved_base_url or start_url,
                },
                root=self.root,
                run_id=f"bridge_{request_id}",
            )
        except Exception as exc:
            return {"status": "failed", "reason": f"PLAYWRIGHT_PROXY_ERROR:{type(exc).__name__}"}
        return {
            "status": "executed" if str(browser_result.get("status") or "") == "executed" else "failed",
            "execution_status": str(browser_result.get("execution_status") or "failed"),
            "confirmation_status": "candidate",
            "current_url": start_url,
            "history": [
                {
                    "type": "executed",
                    "tool": "playwright_browser_plan",
                    "input": {"url": start_url},
                    "output": f"bridge_proxy:{browser_result.get('status')}",
                }
            ],
            "console": _as_list(browser_result.get("console")),
            "network": _as_list(browser_result.get("network")),
            "artifacts": [
                {"artifact_type": "trace", "ref": _text(browser_result.get("trace_ref"))},
                {"artifact_type": "screenshot", "ref": _text(browser_result.get("screenshot_ref"))},
            ],
            "findings": [],
            "duration_ms": int(browser_result.get("duration_ms") or 0),
        }

    def _execute_stub_page_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = _text(payload.get("task"))
        start_url = _text(payload.get("start_url"))
        request_id = _text(payload.get("request_id"))
        page_hints = [str(item)[:300] for item in _as_list(payload.get("page_hints"))]
        created_data = _as_dict(_as_dict(payload.get("metadata")).get("stub_created_data"))
        return {
            "status": "executed",
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "current_url": start_url,
            "history": [
                {"type": "thinking", "message": f"stub_page_agent accepted task:{task[:160]}"},
                {"type": "executed", "tool": "stub_page_agent", "input": {"start_url": start_url, "page_hints": page_hints}, "output": "stub_completed"},
            ],
            "console": [],
            "network": [],
            "artifacts": [],
            "findings": [],
            "created_data": created_data,
            "duration_ms": 10,
            "reason": "",
            "request_id": request_id,
        }

    def _response(self, status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        body = _json_bytes(payload)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Content-Length": str(len(body)),
        }
        return status, headers, body


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "QualiBugPageAgentBridge/0.1"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(*self.server.app.handle("OPTIONS", self.path))  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        self._send(*self.server.app.handle("GET", self.path))  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        self._send(*self.server.app.handle("POST", self.path, body))  # type: ignore[attr-defined]

    def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return None


class PageAgentBridgeHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: PageAgentBridgeApp) -> None:
        self.app = app
        super().__init__(server_address, _BridgeHandler)


def serve_page_agent_bridge(*, root: Path, host: str = "127.0.0.1", port: int = 8797, mode: str = "stub_page_agent") -> PageAgentBridgeHTTPServer:
    app = PageAgentBridgeApp(root=root, mode=mode)
    server = PageAgentBridgeHTTPServer((host, int(port)), app)
    print(f"QualiBug page-agent bridge listening on http://{host}:{port}")
    print(json.dumps({"health": f"http://{host}:{port}/health", "execute": f"http://{host}:{port}/execute", "mode": mode}, ensure_ascii=False, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping page-agent bridge server...")
    finally:
        server.server_close()
    return server


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the QualiBug page-agent bridge server.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="Workspace root path.")
    parser.add_argument("--host", default=os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_PORT", "8797")))
    parser.add_argument(
        "--mode",
        choices=["stub_page_agent", "playwright_browser_plan", "page_agent_browser_plan"],
        default=os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_MODE", "stub_page_agent"),
        help="stub_page_agent returns a deterministic stub result; playwright_browser_plan proxies to the existing Playwright runner; page_agent_browser_plan executes explicit or observation-derived plans through the Playwright runner.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    serve_page_agent_bridge(root=Path(args.root), host=args.host, port=args.port, mode=args.mode)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
