from __future__ import annotations

"""Phase104A: mutation-capable local HTTP API for the Enterprise Command Center.

Phase103 delivered the product-facing objects, a framework-free API facade,
static frontend exports, preview server, and delivery gates.  This module is the
next step toward a real V1 backend: it exposes the Phase103S facade through a
small stdlib-only HTTP application with the same route contract used by the PRD.

The implementation intentionally avoids FastAPI/Flask so field teams can run it
on locked-down customer laptops.  The app can still be embedded by tests or a
future web framework because all routing is available through ``handle`` without
opening a socket.

Security posture:
* all responses pass through the Phase103 redaction path;
* malformed JSON returns customer-safe errors;
* only explicitly whitelisted API routes are supported;
* CORS preflight is supported for local frontend development;
* raw token/cookie/password/session/client_secret values are never returned.
"""

import argparse
import json
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from ai_test_asset_center.phase103_command_center_api import (
    CommandCenterAPIError,
    EnterpriseCommandCenterAPI,
    api_error_response,
    api_response,
)
from ai_test_asset_center.phase103_demo_runner import seed_demo_project
from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.display_ready_formatter import (
    _runtime_identity_mismatch_reasons,
    _runtime_observation_supports_finding,
)
from ai_test_asset_center.real_project_onboarding import ROOT, _safe_project_id

PHASE104A_VERSION = "phase104a-command-center-http-api-v1"
EVIDENCE_RELEVANCE_FAILURE = "运行时响应与当前缺陷描述不匹配，已拒绝作为复现证据"


@dataclass(frozen=True)
class HttpAPIResponse:
    """Transport response returned by the route layer and HTTP handler."""

    status: int
    headers: dict[str, str]
    body: bytes

    @classmethod
    def json(cls, payload: Mapping[str, Any] | list[Any], *, status: int = 200) -> "HttpAPIResponse":
        body = json.dumps(redact_value(payload), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        return cls(
            status=status,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
            body=body,
        )

    @classmethod
    def empty(cls, *, status: int = 204) -> "HttpAPIResponse":
        return cls(
            status=status,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
            body=b"",
        )

    def json_body(self) -> Any:
        return json.loads(self.body.decode("utf-8") or "{}")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_error(code: str, message: str, *, status: int = 400, details: Mapping[str, Any] | None = None) -> HttpAPIResponse:
    return HttpAPIResponse.json(
        {
            "success": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "status": status,
                "details": redact_value(dict(details or {})),
            },
            "meta": {"generated_at": _now(), "version": PHASE104A_VERSION},
        },
        status=status,
    )


def _status_from_envelope(envelope: Mapping[str, Any], *, default: int = 200) -> int:
    if envelope.get("success") is True:
        return default
    error = envelope.get("error") if isinstance(envelope.get("error"), Mapping) else {}
    try:
        return int(error.get("status") or 500)
    except Exception:
        return 500


def _parse_json_body(body: bytes | str | Mapping[str, Any] | None) -> dict[str, Any]:
    if body is None or body == b"" or body == "":
        return {}
    if isinstance(body, Mapping):
        return dict(body)
    if isinstance(body, bytes):
        raw = body.decode("utf-8")
    else:
        raw = str(body)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandCenterAPIError(
            "INVALID_JSON",
            "请求体不是有效 JSON，请检查前端提交的数据格式。",
            status=400,
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        raise CommandCenterAPIError("JSON_OBJECT_REQUIRED", "请求体必须是 JSON 对象。", status=400)
    return dict(parsed)


def _bool_query(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _load_ui_design_oracle_governance(project_id: str) -> dict[str, Any] | None:
    payload = _load_real_project_discovery_payload(project_id)
    if not isinstance(payload, dict):
        return None
    summary = payload.get("risk_based_plan_summary") if isinstance(payload.get("risk_based_plan_summary"), dict) else {}
    if not summary:
        return None
    governance = {str(k): v for k, v in summary.items() if str(k).startswith("ui_design_oracle_")}
    return governance or None


def _load_real_project_discovery_payload(project_id: str) -> dict[str, Any] | None:
    project = _safe_project_id(project_id)
    candidate = ROOT / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    if not candidate.exists():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8") or "null")
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _augment_command_center_snapshot(project_id: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        return dict(envelope) if isinstance(envelope, dict) else {"success": False, "data": None}
    data = envelope.get("data") if isinstance(envelope.get("data"), Mapping) else None
    if not isinstance(data, Mapping):
        return dict(envelope)
    payload = _load_real_project_discovery_payload(project_id)
    updated_data = dict(data)
    if isinstance(payload, dict):
        governance = _load_ui_design_oracle_governance(project_id)
        family_coverage = payload.get("bug_family_coverage") if isinstance(payload.get("bug_family_coverage"), dict) else None
        capability_matrix = payload.get("full_spectrum_capability_matrix") if isinstance(payload.get("full_spectrum_capability_matrix"), dict) else None
        discovery_funnel = payload.get("discovery_funnel") if isinstance(payload.get("discovery_funnel"), dict) else None
        discovery_blocker_summary = payload.get("discovery_blocker_summary") if isinstance(payload.get("discovery_blocker_summary"), dict) else None
        continuous_discovery_campaign = payload.get("continuous_discovery_campaign") if isinstance(payload.get("continuous_discovery_campaign"), dict) else None
        continuous_discovery_metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else None
        if governance:
            updated_data["ui_design_oracle_governance"] = governance
        if family_coverage:
            updated_data["bug_family_coverage"] = family_coverage
        if capability_matrix:
            updated_data["full_spectrum_capability_matrix"] = capability_matrix
        if discovery_funnel:
            updated_data["discovery_funnel"] = discovery_funnel
        if discovery_blocker_summary:
            updated_data["discovery_blocker_summary"] = discovery_blocker_summary
        if continuous_discovery_campaign:
            updated_data["continuous_discovery_campaign"] = continuous_discovery_campaign
        if continuous_discovery_metrics:
            updated_data["continuous_discovery_metrics"] = {
                str(key): value
                for key, value in continuous_discovery_metrics.items()
                if str(key).startswith("continuous_discovery_") or str(key) == "doc_completeness"
            }
    if updated_data == dict(data):
        return dict(envelope)
    updated = dict(envelope)
    updated["data"] = updated_data
    return updated


def _sanitize_customer_evidence_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_customer_evidence_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized = {str(key): _sanitize_customer_evidence_payload(item) for key, item in value.items()}
    if _looks_like_display_risk(sanitized):
        return _sanitize_display_risk(sanitized)
    return sanitized


def _looks_like_display_risk(item: Mapping[str, Any]) -> bool:
    return (
        "title" in item
        and ("raw_evidence" in item or "reproduction" in item or "evidence_quality" in item)
        and ("bug_status" in item or "verdict" in item)
    )


def _sanitize_display_risk(risk: dict[str, Any]) -> dict[str, Any]:
    raw_evidence = risk.get("raw_evidence") if isinstance(risk.get("raw_evidence"), dict) else {}
    response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
    if not response_raw:
        return risk

    request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
    obs = {
        "source": "har",
        "method": request_raw.get("method") or risk.get("repro_method"),
        "path": request_raw.get("path") or risk.get("repro_path"),
        "status_code": response_raw.get("status_code"),
        "body": response_raw.get("body"),
        "request_body": request_raw.get("body"),
        "duration_ms": response_raw.get("duration_ms") or 0,
        "actor": request_raw.get("actor"),
    }
    if _runtime_observation_supports_finding(risk, obs) and not _runtime_identity_mismatch_reasons(risk, obs):
        return risk

    cleaned = dict(risk)
    cleaned["bug_status"] = "not_reproduced"
    cleaned["bug_status_label"] = "未复现"
    cleaned["bug_status_description"] = EVIDENCE_RELEVANCE_FAILURE
    cleaned["verdict"] = "pending"
    cleaned["is_reproducible"] = False
    cleaned["gate_passed"] = False
    failures = [str(item) for item in cleaned.get("gate_failures") or [] if str(item)]
    if EVIDENCE_RELEVANCE_FAILURE not in failures:
        failures.append(EVIDENCE_RELEVANCE_FAILURE)
    cleaned["gate_failures"] = failures
    cleaned["confidence"] = 0.0

    cleaned_raw = dict(raw_evidence)
    cleaned_raw["response_raw"] = {}
    cleaned_raw["has_real_evidence"] = bool(
        cleaned_raw.get("db_snapshot")
        or cleaned_raw.get("logs")
        or cleaned_raw.get("execution_trace")
    )
    cleaned["raw_evidence"] = cleaned_raw

    reproduction = dict(cleaned.get("reproduction") or {})
    reproduction["har_evidence"] = None
    reproduction["is_synthetic"] = True
    reproduction["steps"] = [
        "当前运行响应与缺陷描述不匹配，已拒绝作为复现证据；请重新执行与该缺陷场景一致的复现步骤。"
    ]
    cleaned["reproduction"] = reproduction

    quality = dict(cleaned.get("evidence_quality") or {})
    quality["can_reproduce"] = False
    quality["score"] = min(int(quality.get("score") or 0), 40)
    quality["level"] = "needs_evidence"
    quality["label"] = "风险线索"
    quality["summary"] = EVIDENCE_RELEVANCE_FAILURE
    quality["verified"] = [
        item for item in quality.get("verified") or []
        if "接口响应" not in str(item) and "复现" not in str(item)
    ]
    missing = [str(item) for item in quality.get("missing") or [] if str(item)]
    if "缺少与缺陷描述匹配的真实接口响应" not in missing:
        missing.insert(0, "缺少与缺陷描述匹配的真实接口响应")
    quality["missing"] = missing[:6]
    cleaned["evidence_quality"] = quality

    comparison = dict(cleaned.get("expected_actual_comparison") or {})
    comparison["api_comparison"] = None
    cleaned["expected_actual_comparison"] = comparison
    cleaned["failed_assertions"] = [
        assertion for assertion in cleaned.get("failed_assertions") or []
        if isinstance(assertion, dict) and assertion.get("type") not in {"http_status_error", "response_error_field", "behavior_mismatch"}
    ]
    proof = dict(cleaned.get("proof") or {})
    proof["repro_rate"] = 0
    cleaned["proof"] = proof
    return cleaned


class Phase104CommandCenterHttpApp:
    """Route adapter that exposes EnterpriseCommandCenterAPI over V1 HTTP paths."""

    def __init__(self, *, api: EnterpriseCommandCenterAPI | None = None, seed_scenario: str | None = None) -> None:
        self.api = api or EnterpriseCommandCenterAPI()
        self.seed_bundle: dict[str, Any] | None = None
        if seed_scenario:
            self.seed_bundle = seed_demo_project(self.api, scenario=seed_scenario)

    def handle(self, method: str, path: str, body: bytes | str | Mapping[str, Any] | None = None) -> HttpAPIResponse:
        """Handle one HTTP-like request without requiring a running server."""
        method = method.upper().strip()
        if method == "OPTIONS":
            return HttpAPIResponse.empty()
        parsed = urlparse(path)
        clean_path = (parsed.path or "/").rstrip("/") or "/"
        parts = [part for part in clean_path.split("/") if part]
        query = parse_qs(parsed.query)
        try:
            return self._dispatch(method, parts, query, body)
        except Exception as exc:  # customer-safe envelope
            envelope = api_error_response(exc)
            return HttpAPIResponse.json(envelope, status=_status_from_envelope(envelope, default=500))

    def _dispatch(
        self,
        method: str,
        parts: list[str],
        query: Mapping[str, list[str]],
        body: bytes | str | Mapping[str, Any] | None,
    ) -> HttpAPIResponse:
        if len(parts) < 2 or parts[0] != "api" or parts[1] != "v1":
            return _json_error("NOT_FOUND", "未找到请求的 API 路由。", status=404, details={"path": "/" + "/".join(parts)})

        # /api/v1/health
        if parts == ["api", "v1", "health"] and method == "GET":
            return self._wrap(
                api_response(
                    {
                        "status": "ok",
                        "version": PHASE104A_VERSION,
                        "project_count": len(self.api.projects),
                        "redaction_status": "safe",
                    }
                )
            )

        # /api/v1/industry-templates
        if parts == ["api", "v1", "industry-templates"] and method == "GET":
            return self._wrap(self.api.list_industry_templates())

        # /api/v1/projects
        if parts == ["api", "v1", "projects"]:
            if method == "GET":
                return self._wrap(self.api.list_projects())
            if method == "POST":
                return self._wrap(self.api.create_project(_parse_json_body(body)), status=201)
            return self._method_not_allowed(method)

        # /api/v1/projects/{project_id}/...
        if len(parts) >= 4 and parts[:3] == ["api", "v1", "projects"]:
            project_id = parts[3]
            tail = parts[4:]
            return self._dispatch_project(method, project_id, tail, query, body)

        return _json_error("NOT_FOUND", "未找到请求的 API 路由。", status=404, details={"path": "/" + "/".join(parts)})

    def _dispatch_project(
        self,
        method: str,
        project_id: str,
        tail: list[str],
        query: Mapping[str, list[str]],
        body: bytes | str | Mapping[str, Any] | None,
    ) -> HttpAPIResponse:
        if not tail:
            if method == "GET":
                return self._wrap(self.api.get_project(project_id))
            return self._method_not_allowed(method)

        if tail == ["onboarding"] and method == "GET":
            return self._wrap(self.api.get_onboarding(project_id))

        if tail == ["business-model"]:
            if method == "GET":
                return self._wrap(self.api.get_business_model(project_id))
            if method == "PATCH":
                return self._wrap(self.api.patch_business_model(project_id, _parse_json_body(body)))
            return self._method_not_allowed(method)

        if tail == ["business-model", "apply-template"]:
            if method == "POST":
                return self._wrap(self.api.apply_business_template(project_id, _parse_json_body(body)))
            return self._method_not_allowed(method)

        if tail == ["environment", "config"]:
            if method == "PATCH":
                return self._wrap(self.api.patch_environment_config(project_id, _parse_json_body(body)))
            return self._method_not_allowed(method)

        if tail == ["environment", "preflight"]:
            if method == "POST":
                return self._wrap(self.api.run_environment_preflight(project_id, _parse_json_body(body)))
            return self._method_not_allowed(method)

        if tail == ["environment", "readiness"] and method == "GET":
            return self._wrap(self.api.get_environment_readiness(project_id))

        if tail == ["test-plan", "generate"]:
            if method == "POST":
                return self._wrap(self.api.generate_test_plan(project_id, _parse_json_body(body)))
            return self._method_not_allowed(method)

        if tail == ["test-plan"] and method == "GET":
            return self._wrap(self.api.get_test_plan(project_id))

        if tail == ["test-runs"]:
            if method == "POST":
                return self._wrap(self.api.start_test_run(project_id, _parse_json_body(body)), status=201)
            return self._method_not_allowed(method)

        if len(tail) == 2 and tail[0] == "test-runs" and method == "GET":
            return self._wrap(self.api.get_test_run(project_id, tail[1]))

        if tail == ["command-center"] and method == "GET":
            return self._wrap(_augment_command_center_snapshot(project_id, self.api.get_command_center(project_id)))

        if tail == ["live-map"] and method == "GET":
            return self._wrap(self.api.get_live_map(project_id))

        if tail == ["risks"] and method == "GET":
            filters: dict[str, Any] = {}
            for key in ["severity", "business_flow_id", "status"]:
                if query.get(key):
                    filters[key] = query[key][0]
            if query.get("launch_blocking"):
                filters["launch_blocking"] = _bool_query(query["launch_blocking"][0])
            return self._wrap(self.api.list_risks(project_id, filters))

        if len(tail) == 2 and tail[0] == "risks" and method == "GET":
            return self._wrap(self.api.get_risk_detail(project_id, tail[1]))

        if tail == ["value-metrics"] and method == "GET":
            return self._wrap(self.api.get_value_metrics(project_id))

        if tail == ["reports", "generate"]:
            if method == "POST":
                return self._wrap(self.api.generate_report(project_id))
            return self._method_not_allowed(method)

        if tail == ["reports", "executive"] and method == "GET":
            return self._wrap(self.api.get_report(project_id))

        return _json_error("NOT_FOUND", "未找到请求的项目 API 路由。", status=404, details={"project_id": project_id, "tail": tail})

    def _wrap(self, envelope: Mapping[str, Any], *, status: int = 200) -> HttpAPIResponse:
        sanitized = _sanitize_customer_evidence_payload(envelope)
        return HttpAPIResponse.json(sanitized, status=_status_from_envelope(sanitized, default=status))

    def _method_not_allowed(self, method: str) -> HttpAPIResponse:
        return _json_error(
            "METHOD_NOT_ALLOWED",
            "该 API 路由不支持当前 HTTP 方法。",
            status=405,
            details={"method": method, "allowed": ["GET", "POST", "PATCH", "OPTIONS"]},
        )


class _CommandCenterHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "QualiBugPhase104A/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(self.server.app.handle("OPTIONS", self.path))  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        self._send(self.server.app.handle("GET", self.path))  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802
        self._send(self.server.app.handle("POST", self.path, self._read_body()))  # type: ignore[attr-defined]

    def do_PATCH(self) -> None:  # noqa: N802
        self._send(self.server.app.handle("PATCH", self.path, self._read_body()))  # type: ignore[attr-defined]

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def _send(self, response: HttpAPIResponse) -> None:
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if response.body:
            self.wfile.write(response.body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Keep CLI output clean; future production adapters can wire structured logs.
        return None


class Phase104HTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: Phase104CommandCenterHttpApp) -> None:
        self.app = app
        super().__init__(server_address, _CommandCenterHTTPRequestHandler)


def serve_http_api(*, host: str = "127.0.0.1", port: int = 8088, seed_scenario: str | None = None) -> Phase104HTTPServer:
    app = Phase104CommandCenterHttpApp(seed_scenario=seed_scenario)
    server = Phase104HTTPServer((host, port), app)
    print(f"Phase104A Command Center HTTP API listening on http://{host}:{port}")
    print("Health: /api/v1/health")
    if seed_scenario and app.seed_bundle:
        project_id = app.seed_bundle.get("project_id")
        print(f"Seed scenario: {seed_scenario}")
        print(f"Project API: /api/v1/projects/{project_id}/command-center")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Phase104A HTTP API server...")
    finally:
        server.server_close()
    return server


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Phase104A mutable local Command Center HTTP API.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8088, help="Bind port. Defaults to 8088.")
    parser.add_argument(
        "--seed-scenario",
        choices=["manufacturing", "ecommerce", "saas"],
        default=None,
        help="Optionally seed one full demo project on startup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    serve_http_api(host=args.host, port=args.port, seed_scenario=args.seed_scenario)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "HttpAPIResponse",
    "PHASE104A_VERSION",
    "Phase104CommandCenterHttpApp",
    "Phase104HTTPServer",
    "serve_http_api",
    "main",
]

