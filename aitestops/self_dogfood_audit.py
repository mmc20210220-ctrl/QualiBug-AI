from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ai_test_asset_center.private_pilot_service import run_private_pilot_service


PROJECT_ID = "self_dogfood_demo"
SECRET_SENTINEL = "__QUALIBUG_DOGFOOD_SECRET_SENTINEL__"
OWNER_HEADERS = {
    "Content-Type": "application/json",
    "X-QualiBug-Actor": "owner",
    "X-QualiBug-Role": "project_owner",
}
QA_HEADERS = {
    "Content-Type": "application/json",
    "X-QualiBug-Actor": "qa",
    "X-QualiBug-Role": "qa_engineer",
}
ADMIN_HEADERS = {
    "Content-Type": "application/json",
    "X-QualiBug-Actor": "admin",
    "X-QualiBug-Role": "admin",
}


@dataclass
class AuditFinding:
    severity: str
    title: str
    actual: str
    expected: str
    repro_steps: list[str]
    recommendation: str
    evidence: dict[str, Any] = field(default_factory=dict)
    risk_type: str = "self_dogfood"

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "risk_type": self.risk_type,
            "title": self.title,
            "repro_steps": self.repro_steps,
            "actual": self.actual,
            "expected": self.expected,
            "recommendation": self.recommendation,
            "evidence": self.evidence,
        }


class SelfDogfoodAudit:
    def __init__(self, root: Path, *, mock_llm: bool = True) -> None:
        self.root = root
        self.mock_llm = mock_llm
        self.findings: list[AuditFinding] = []
        self.artifacts: dict[str, str] = {}
        self._server = None
        self._worker: threading.Thread | None = None
        self.base_url = ""
        self._previous_env: dict[str, str | None] = {}
        self._original_chat = None

    def run(self) -> dict[str, Any]:
        start = time.monotonic()
        self._install_env()
        try:
            self._start_server()
            self._check_pages()
            self._check_llm_save_reload_and_restart()
            self._check_ingest_scan_findings_and_exports()
            self._check_permissions()
            self._check_project_scope_isolation()
            self._check_secret_leakage()
        finally:
            self._stop_server()
            self._restore_env()

        report = {
            "ok": not any(f.severity in {"P0", "P1"} for f in self.findings),
            "phase": "self_dogfood_bug_audit",
            "project_id": PROJECT_ID,
            "duration_seconds": round(time.monotonic() - start, 3),
            "finding_count": len(self.findings),
            "findings": [f.as_dict() for f in self.findings],
            "artifacts": self.artifacts,
            "coverage": [
                "service_start",
                "dashboard_settings_knowledge_release_findings_pages",
                "llm_key_save_file_runtime_restart_status",
                "prd_openapi_ingest",
                "scan_run_report_history",
                "findings_page",
                "snapshot_export_surface",
                "role_boundaries",
                "project_scope_isolation",
                "secret_leakage",
            ],
        }
        output = self.root / "platform_outputs" / PROJECT_ID / "self_dogfood_audit.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self.artifacts["self_dogfood_audit"] = str(output)
        report["artifacts"] = self.artifacts
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _install_env(self) -> None:
        keys = [
            "QUALIBUG_PRIVATE_ROOT",
            "QUALIBUG_ENV_LOCAL_PATH",
            "QUALIBUG_LOCAL_DEV_ACTOR",
            "QUALIBUG_ALLOW_PUBLIC_BIND",
            "QUALIBUG_LLM_HEALTH_STATUS",
            "QUALIBUG_LLM_LAST_HEALTH_STATUS",
            "QUALIBUG_LLM_LAST_HEALTH_LABEL",
            "QUALIBUG_LLM_LAST_HEALTH_ERROR",
            "LLM_BASE_URL",
            "LLM_MODEL",
            "LLM_API_KEY",
        ]
        self._previous_env = {key: os.environ.get(key) for key in keys}
        os.environ["QUALIBUG_PRIVATE_ROOT"] = str(self.root)
        os.environ["QUALIBUG_ENV_LOCAL_PATH"] = str(self.root / ".env.local")
        os.environ["QUALIBUG_LOCAL_DEV_ACTOR"] = "1"
        os.environ.pop("QUALIBUG_ALLOW_PUBLIC_BIND", None)
        os.environ.pop("QUALIBUG_LLM_HEALTH_STATUS", None)
        os.environ.pop("QUALIBUG_LLM_LAST_HEALTH_STATUS", None)
        os.environ.pop("QUALIBUG_LLM_LAST_HEALTH_LABEL", None)
        os.environ.pop("QUALIBUG_LLM_LAST_HEALTH_ERROR", None)
        if self.mock_llm:
            from ai_test_asset_center import llm_reasoning

            self._original_chat = llm_reasoning.ReasoningClient._chat
            llm_reasoning.ReasoningClient._chat = lambda _client, _prompt, **_kwargs: '{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def _restore_env(self) -> None:
        if self._original_chat is not None:
            from ai_test_asset_center import llm_reasoning

            llm_reasoning.ReasoningClient._chat = self._original_chat
            self._original_chat = None
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _start_server(self) -> None:
        self._server = run_private_pilot_service(root=self.root, host="127.0.0.1", port=0)
        self._worker = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._worker.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        health = self._json_get("/health", timeout=10)
        if not health.get("ok"):
            self._add("P0", "Private service health check did not pass", str(health), "The service starts and /health returns ok=true.", ["Start the private service", "GET /health"], "Fix service startup or health endpoint behavior.", {"health": health}, "service_start")

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._worker is not None:
            self._worker.join(timeout=5)
            self._worker = None

    def _restart_server(self) -> None:
        self._stop_server()
        self._start_server()

    def _check_pages(self) -> None:
        required = {
            "/dashboard": ["QualiBug", "data-product-ui", "data-download"],
            "/settings": ["LLM", "API Key", "settings-msg"],
            "/knowledge": ["Knowledge", "data-product-ui"],
            "/release": ["data-product-ui", "data-download"],
            "/findings": ["Bug", "data-product-ui"],
            "/onboard": ["data-product-ui", "LLM", "OpenAPI"],
        }
        forbidden = ["X-QualiBug-Actor':'admin", '"X-QualiBug-Actor":"admin"', "X-QualiBug-Role':'admin", '"X-QualiBug-Role":"admin"']
        for route, tokens in required.items():
            html = self._text_get(f"{route}?{urlencode({'project': PROJECT_ID})}")
            missing = [token for token in tokens if token not in html]
            if missing:
                self._add("P1", f"{route} page is missing expected product UI signals", f"Missing tokens: {missing}", "Core pages expose expected text, product shell and controls.", ["Open " + route, "Inspect page text and HTML tokens"], "Render the shared shell and required controls on this route.", {"route": route, "missing": missing}, "page_behavior")
            leaked_headers = [token for token in forbidden if token in html]
            if leaked_headers:
                self._add("P1", f"{route} embeds trusted admin identity headers in frontend JavaScript", f"Found forbidden header tokens: {leaked_headers}", "Frontend must not forge trusted actor/role headers; identity comes from local dev actor or reverse proxy.", ["Open " + route, "Search HTML for X-QualiBug-Actor / X-QualiBug-Role"], "Remove hardcoded trusted identity headers from client JavaScript.", {"route": route, "forbidden_tokens": leaked_headers}, "permission_boundary")
            if 'type="password"' in html or "type='password'" in html:
                if "type=\"password\" id=\"llm-key\" value=\"\"" not in html and "type='password' value=''" not in html:
                    self._add("P1", f"{route} may render a non-empty password field value", "A password input did not clearly render with an empty value.", "Secret inputs are always blank in rendered HTML; use placeholders or status labels instead.", ["Open " + route, "Inspect password input value attributes"], "Render password inputs with value=\"\" and never with masked or partial secrets.", {"route": route}, "secret_leakage")

    def _check_llm_save_reload_and_restart(self) -> None:
        payload = {"project_id": PROJECT_ID, "llm_base_url": "https://api.deepseek.com/v1", "llm_model": "deepseek-chat", "llm_temperature": "0.1", "llm_api_key": SECRET_SENTINEL}
        response = self._json_post("/api/settings/save", payload, OWNER_HEADERS)
        env_local = self.root / ".env.local"
        env_text = env_local.read_text(encoding="utf-8", errors="replace") if env_local.exists() else ""
        if f"LLM_API_KEY={SECRET_SENTINEL}" not in env_text:
            self._add("P0", "LLM key save did not persist to .env.local", "The saved key was not found in the local credentials file.", "Saving LLM settings writes the key to .env.local for local private deployments.", ["POST /api/settings/save as project_owner", "Read .env.local"], "Fix settings persistence and test the file path used by the service.", {"response": self._redact(response), "env_local_exists": env_local.exists()}, "llm_config")
        if not response.get("llm_available") or response.get("llm_status") != "online":
            self._add("P1", "LLM save did not verify online status", f"Response status: {response.get('llm_status')} {response.get('llm_error')}", "Saving LLM settings runs a real connectivity check and returns online or a concrete failure.", ["POST /api/settings/save", "Inspect llm_status"], "Run connectivity verification after save and return the failure reason when unavailable.", {"response": self._redact(response)}, "llm_config")
        health = self._json_get("/health")
        if not health.get("llm_available"):
            self._add("P1", "LLM key is saved but runtime health is not online", str(self._redact(health.get("llm_status", {}))), "Runtime health reflects the saved and verified LLM configuration.", ["Save LLM key", "GET /health"], "Reset cached LLM clients and refresh health state after configuration changes.", {"health": self._redact(health)}, "state_consistency")
        self._restart_server()
        restarted = self._json_get("/health")
        if not restarted.get("llm_available"):
            self._add("P1", "LLM configuration does not survive service restart as online", str(self._redact(restarted.get("llm_status", {}))), "After restart, saved .env.local settings are loaded and verified online.", ["Save LLM key", "Restart private service", "GET /health"], "Load .env.local on startup and trigger/restore LLM health verification.", {"health": self._redact(restarted)}, "state_consistency")
        settings = self._text_get(f"/settings?{urlencode({'project': PROJECT_ID})}")
        if SECRET_SENTINEL in settings:
            self._add("P0", "Settings page leaks the LLM API key", "The secret sentinel appears in rendered settings HTML.", "API keys are never rendered back to the browser.", ["Save LLM key", "Open /settings", "Search HTML for the key"], "Keep the key input blank and only show masked status.", {"route": "/settings"}, "secret_leakage")
        if "Verified online" not in settings and "LLM Online" not in settings:
            self._add("P2", "Settings page does not show verified LLM online status after save", "The page did not contain a clear online/verified label.", "Settings reflects the current verified LLM state.", ["Save LLM key", "Open /settings"], "Render the persisted health state on settings page.", {"route": "/settings"}, "state_consistency")

    def _check_ingest_scan_findings_and_exports(self) -> None:
        prd = """# QualiBug Self Dogfood PRD

The system must require trusted actor headers for privileged writes.
Known Issues: missing auth, missing method checks, missing upload limits, unbounded scan history.
Users must be able to import OpenAPI, run a scan, view Bug details and export evidence.
"""
        openapi = {
            "openapi": "3.0.3",
            "info": {"title": "QualiBug Self Dogfood API", "version": "1.0"},
            "paths": {
                "/api/settings/save": {"post": {"summary": "Save LLM settings", "responses": {"200": {"description": "ok"}}}},
                "/api/scan/run": {"post": {"summary": "Run bug scan", "responses": {"200": {"description": "ok"}}}},
                "/api/knowledge/ingest": {"post": {"summary": "Import knowledge source", "responses": {"200": {"description": "ok"}}}},
                "/health": {"get": {"summary": "Health", "responses": {"200": {"description": "ok"}}}},
            },
        }
        self._ingest("self_dogfood_prd.md", "prd", prd)
        self._ingest("self_dogfood_openapi.json", "openapi", json.dumps(openapi, ensure_ascii=False))
        asset = self._json_get(f"/api/knowledge/asset?{urlencode({'project': PROJECT_ID})}")
        summary = ((asset.get("knowledge_asset") or {}).get("summary") or {})
        source_types = set(((summary.get("source_type_distribution") or {}).keys()))
        if int(summary.get("active_source_count") or 0) < 2 or int(summary.get("interface_count") or 0) < 1:
            self._add("P1", "Knowledge ingestion did not produce enough project context", f"Summary: {summary}", "PRD and OpenAPI ingestion creates active sources and at least one interface.", ["POST /api/knowledge/ingest for PRD", "POST /api/knowledge/ingest for OpenAPI", "GET /api/knowledge/asset"], "Fix ingestion or parsing so OpenAPI interfaces are available to scan engines.", {"summary": summary}, "onboarding")
        if not {"prd", "openapi"}.issubset(source_types):
            self._add("P1", "Knowledge ingestion misclassified PRD or OpenAPI sources", f"Source types: {sorted(source_types)}", "Uploaded PRD remains classified as prd and OpenAPI remains classified as openapi.", ["Upload PRD with type=prd", "Upload OpenAPI with type=openapi", "Inspect knowledge asset summary"], "Pass explicit source_type through upload ingestion and correct content-based fallback classification.", {"summary": summary}, "onboarding")

        scan = self._json_post("/api/scan/run", {"project_id": PROJECT_ID}, OWNER_HEADERS, timeout=60)
        if not scan.get("ok"):
            self._add("P0", "One-click scan failed", str(scan.get("message") or scan.get("error")), "A prepared project can run a scan and return a structured result.", ["Import PRD/OpenAPI", "POST /api/scan/run"], "Fix scan execution path and surface concrete failure reasons.", {"scan": self._redact(scan)}, "scan_execution")
        findings = ((scan.get("stage2_discovery") or {}).get("findings") or [])
        by_severity = ((scan.get("stage2_discovery") or {}).get("by_severity") or {})
        stage2_total = int(((scan.get("stage2_discovery") or {}).get("total_findings") or 0))
        executive_total = int(((scan.get("executive_summary") or {}).get("total_bugs_found") or 0))
        deep_summary = ((scan.get("stage2_discovery") or {}).get("deep_bug_mining") or {})
        validation_queue = ((scan.get("stage2_discovery") or {}).get("validation_queue") or {})
        validation_summary = validation_queue.get("summary") or {}
        validation_execution = ((scan.get("stage2_discovery") or {}).get("validation_execution") or {})
        validation_exec_summary = validation_execution.get("summary") or {}
        deep_findings = [f for f in findings if f.get("source") == "deep_bug_mining"]
        message = str(scan.get("message") or "")
        if not findings and not any(token in message for token in ["OpenAPI", "PRD", "环境", "权限", "引擎", "evidence"]):
            self._add("P1", "Empty scan result does not explain why no meaningful bugs were found", message, "If scan finds nothing, the response explains whether PRD, OpenAPI, environment, permission or engine evidence is missing.", ["Run scan on prepared project", "Inspect result message"], "Add explicit no-finding diagnostics tied to missing inputs or executed engines.", {"message": message}, "scan_explainability")
        if findings and sum(int(v or 0) for v in by_severity.values()) != len(findings):
            self._add("P2", "Finding severity summary is stale or empty", f"by_severity={by_severity}, findings={len(findings)}", "The scan response keeps by_severity consistent with the calibrated findings list.", ["Run scan", "Inspect stage2_discovery.by_severity"], "Recompute severity distribution after health, semantic and validation calibration updates.", {"by_severity": by_severity, "finding_count": len(findings)}, "scan_explainability")
        if findings and (executive_total != len(findings) or stage2_total != len(findings)):
            self._add(
                "P1",
                "Executive summary finding total diverges from detailed findings",
                f"executive_summary.total_bugs_found={executive_total}, stage2.total_findings={stage2_total}, detailed_findings={len(findings)}",
                "The executive summary, stage-two total, and detailed finding list must report the same calibrated count.",
                ["Import PRD/OpenAPI", "POST /api/scan/run", "Compare executive_summary.total_bugs_found with stage2_discovery.findings"],
                "Recompute executive summary totals after health, semantic, and validation enrichment before persisting or rendering scan reports.",
                {"executive_total": executive_total, "stage2_total": stage2_total, "detailed_finding_count": len(findings)},
                "cross_view_reconciliation",
            )
        if not deep_findings or deep_summary.get("status") != "completed":
            self._add("P1", "Deep bug mining did not contribute to scan results", f"summary={deep_summary}, deep_findings={len(deep_findings)}", "Prepared PRD/OpenAPI scans include deep bug mining findings and summary.", ["Import PRD/OpenAPI", "POST /api/scan/run", "Inspect stage2_discovery.deep_bug_mining"], "Wire high-signal PRD/OpenAPI mining into one-click scan.", {"deep_summary": deep_summary}, "scan_execution")
        elif not all(f.get("rank_score") is not None and f.get("verification_level") and f.get("validation_plan") for f in deep_findings):
            self._add("P1", "Deep bug mining findings are missing ranking or validation metadata", "One or more findings lacked rank_score, verification_level, or validation_plan.", "Each mined finding includes rank, verification level and validation plan so users know what to validate first.", ["Run scan", "Inspect deep bug mining findings"], "Annotate and rank mined findings before returning them.", {"sample": self._redact(deep_findings[:3])}, "scan_explainability")
        elif int(deep_summary.get("static_verified_count") or 0) + int(deep_summary.get("live_validation_required_count") or 0) != int(deep_summary.get("finding_count") or 0):
            self._add("P2", "Deep bug mining verification summary is inconsistent", str(deep_summary), "Verification buckets add up to the number of deep mining findings.", ["Run scan", "Inspect deep_bug_mining summary"], "Fix verification bucket accounting.", {"deep_summary": deep_summary}, "scan_explainability")
        if validation_queue.get("status") != "completed" or int(validation_summary.get("total_task_count") or 0) <= 0:
            self._add("P1", "Bug findings are not converted into validation tasks", f"validation_queue={validation_queue}", "One-click scan returns a validation queue that separates static review, safe read-only probes, sandbox-required checks, and human-mapped candidates.", ["Run scan", "Inspect stage2_discovery.validation_queue"], "Build and attach a governed bug validation queue after discovery.", {"validation_queue": validation_queue}, "scan_validation")
        elif not validation_summary.get("by_lane") or not validation_summary.get("by_status"):
            self._add("P2", "Validation queue lacks execution-lane distribution", str(validation_summary), "Validation queue summary shows task distribution by execution lane and status.", ["Run scan", "Inspect validation queue summary"], "Include lane/status counts for triage and safe execution planning.", {"validation_summary": validation_summary}, "scan_validation")
        if validation_execution.get("status") != "completed" or int(validation_exec_summary.get("total_result_count") or 0) <= 0:
            self._add("P1", "Safe validation execution did not produce evidence", f"validation_execution={validation_execution}", "One-click scan executes the safe/static validation subset and persists a redacted evidence report.", ["Run scan", "Inspect stage2_discovery.validation_execution"], "Execute ready_static_review and ready_safe_probe tasks after queue creation.", {"validation_execution": validation_execution}, "scan_validation")
        elif int(validation_exec_summary.get("static_confirmed_count") or 0) <= 0 and int(validation_summary.get("by_lane", {}).get("static_review") or 0) > 0:
            self._add("P2", "Static validation tasks were not confirmed", str(validation_exec_summary), "Static-review tasks produce static confirmation evidence without runtime calls.", ["Run scan", "Inspect validation execution summary"], "Confirm strong static findings in the validation executor.", {"validation_exec_summary": validation_exec_summary}, "scan_validation")
        permission_write_findings = [
            f for f in findings
            if f.get("risk_type") == "permission_boundary" and str(f.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
        ]
        if permission_write_findings and int(validation_exec_summary.get("negative_auth_probe_executed_count") or 0) <= 0:
            self._add("P1", "Permission-boundary write findings were not safely probed", str(validation_exec_summary), "Permission-boundary POST/PUT/PATCH candidates run a no-credential negative-auth probe with empty JSON and no trusted actor fallback.", ["Run scan", "Inspect validation execution summary"], "Execute safe negative-auth probes for permission-boundary write endpoints.", {"validation_exec_summary": validation_exec_summary, "sample": self._redact(permission_write_findings[:3])}, "scan_validation")
        uncalibrated_permission_findings = [
            f for f in permission_write_findings
            if f.get("validation_verdict") == "passed_expectation" and str(f.get("severity")) in {"P0", "P1"}
        ]
        if uncalibrated_permission_findings:
            self._add("P1", "Runtime-rejected permission candidates remain over-severe", "Negative-auth probe passed, but finding remained P0/P1.", "Runtime-rejected no-credential write probes are downgraded to contract gaps instead of confirmed implementation bugs.", ["Run scan", "Inspect calibrated findings"], "Apply validation execution results back onto findings before persistence.", {"sample": self._redact(uncalibrated_permission_findings[:3])}, "scan_validation")
        report_path = self.root / "platform_outputs" / PROJECT_ID / "pipeline_reports" / "latest_pipeline_report.json"
        history_path = self.root / "platform_outputs" / PROJECT_ID / "pipeline_reports" / "scan_history.json"
        if not report_path.exists() or not history_path.exists():
            self._add("P1", "Scan did not persist report and history artifacts", f"report_exists={report_path.exists()}, history_exists={history_path.exists()}", "Scan writes latest report and scan history for later inspection.", ["Run scan", "Check pipeline_reports artifacts"], "Persist scan outputs consistently.", {"report_path": str(report_path), "history_path": str(history_path)}, "scan_history")
        else:
            self.artifacts["latest_pipeline_report"] = str(report_path)
            self.artifacts["scan_history"] = str(history_path)

        findings_page = self._text_get(f"/findings?{urlencode({'project': PROJECT_ID})}")
        if "Bug" not in findings_page or ("暂无" in findings_page and findings):
            self._add("P1", "Findings page does not expose scan results clearly", "The findings page did not contain expected Bug details after scan.", "After scan, /findings shows discovered bugs and impact analysis.", ["Run scan", "Open /findings"], "Render scan findings and not only aggregate cards.", {"finding_count": len(findings)}, "bug_detail")
        dashboard = self._text_get(f"/dashboard?{urlencode({'project': PROJECT_ID})}")
        if "data-download" not in dashboard or "qualibug-payload" not in dashboard:
            self._add("P2", "Snapshot export surface is missing on dashboard", "Could not find data-download or payload script.", "Dashboard exposes a JSON snapshot export action.", ["Open /dashboard", "Inspect export controls"], "Render snapshot export button and payload JSON.", {"route": "/dashboard"}, "report_export")

    def _check_permissions(self) -> None:
        previous = os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND")
        try:
            os.environ["QUALIBUG_ALLOW_PUBLIC_BIND"] = "1"
            no_actor = self._post_expect_http_error("/api/settings/save", {"project_id": PROJECT_ID, "llm_model": "no-actor"}, {"Content-Type": "application/json"})
            if no_actor != 401:
                self._add("P1", "Missing trusted actor can update protected settings", f"HTTP status: {no_actor}", "Without trusted actor headers, protected writes are rejected when public-bind mode is active.", ["Enable public-bind guard", "POST /api/settings/save without actor"], "Require trusted actor headers for protected writes.", {"status": no_actor}, "permission_boundary")
        finally:
            if previous is None:
                os.environ.pop("QUALIBUG_ALLOW_PUBLIC_BIND", None)
            else:
                os.environ["QUALIBUG_ALLOW_PUBLIC_BIND"] = previous
        qa_status = self._post_expect_http_error("/api/settings/save", {"project_id": PROJECT_ID, "llm_model": "qa-denied"}, QA_HEADERS)
        if qa_status != 403:
            self._add("P1", "QA role can update system settings", f"HTTP status: {qa_status}", "QA can request scans but cannot change system settings.", ["POST /api/settings/save as qa_engineer"], "Keep settings updates restricted to project/security/testops/admin roles.", {"status": qa_status}, "permission_boundary")
        owner = self._json_post("/api/settings/save", {"project_id": PROJECT_ID, "llm_model": "deepseek-chat"}, OWNER_HEADERS)
        if not owner.get("ok"):
            self._add("P1", "Project owner cannot update settings", str(owner), "Project owner can maintain local LLM settings.", ["POST /api/settings/save as project_owner"], "Fix allowed roles for settings management.", {"response": self._redact(owner)}, "permission_boundary")

    def _check_project_scope_isolation(self) -> None:
        """Exercise the public-bind project boundary with harmless synthetic data.

        The private service accepts a project selector on several read routes.
        This check proves that a trusted actor scoped to one project cannot use
        that selector to read a different project's persisted scan report.
        It is GET-only after the local fixture file is written.
        """
        foreign_project = "self_dogfood_foreign_project"
        marker = "SELF_DOGFOOD_FOREIGN_PROJECT_MARKER"
        report_path = self.root / "platform_outputs" / foreign_project / "pipeline_reports" / "latest_pipeline_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"project_id": foreign_project, "marker": marker, "stage2_discovery": {"findings": [], "total_findings": 0}, "executive_summary": {"total_bugs_found": 0}}), encoding="utf-8")
        previous = os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND")
        try:
            os.environ["QUALIBUG_ALLOW_PUBLIC_BIND"] = "1"
            headers = {
                "X-QualiBug-Actor": "scope_probe_user",
                "X-QualiBug-Role": "qa_engineer",
                "X-QualiBug-Project-Scopes": PROJECT_ID,
            }
            own_status, _ = self._json_get_with_headers(f"/api/findings?{urlencode({'project': PROJECT_ID})}", headers)
            foreign_status, foreign_payload = self._json_get_with_headers(f"/api/findings?{urlencode({'project': foreign_project})}", headers)
            if own_status != 200:
                self._add("P2", "Project-scoped actor cannot read its allowed project", f"HTTP status: {own_status}", "An actor whose trusted scope contains the requested project receives the declared success response.", ["Enable public-bind guard", "GET /api/findings with own project scope"], "Verify trusted project-scope header parsing and reverse-proxy integration.", {"status": own_status}, "project_scope_access")
            if foreign_status not in {401, 403, 404}:
                leaked_marker = marker in json.dumps(foreign_payload, ensure_ascii=False, default=str)
                self._add("P0", "Authenticated actor can read another project's scan report", f"HTTP status: {foreign_status}; foreign marker returned={leaked_marker}", "An actor scoped to one project is rejected when it selects another project.", ["Enable public-bind guard", "GET /api/findings?project=<foreign> with X-QualiBug-Project-Scopes limited to own project"], "Enforce trusted project scope before any project-scoped read or mutation.", {"foreign_status": foreign_status, "foreign_marker_returned": leaked_marker}, "project_scope_isolation")
        finally:
            if previous is None:
                os.environ.pop("QUALIBUG_ALLOW_PUBLIC_BIND", None)
            else:
                os.environ["QUALIBUG_ALLOW_PUBLIC_BIND"] = previous

    def _check_secret_leakage(self) -> None:
        leak_files: list[str] = []
        checked = 0
        for path in [
            self.root / "platform_outputs",
            self.root / "platform_workspace",
        ]:
            if not path.exists():
                continue
            for file in path.rglob("*"):
                if not file.is_file() or file.suffix.lower() not in {".html", ".json", ".md", ".txt", ".log"}:
                    continue
                checked += 1
                text = file.read_text(encoding="utf-8", errors="replace")
                if SECRET_SENTINEL in text:
                    leak_files.append(str(file))
        for route in ["/dashboard", "/settings", "/knowledge", "/release", "/findings", "/onboard"]:
            html = self._text_get(f"{route}?{urlencode({'project': PROJECT_ID})}")
            if SECRET_SENTINEL in html:
                leak_files.append(f"{route} HTML")
        if leak_files:
            self._add("P0", "LLM API key leaked into customer-visible artifacts", f"Secret sentinel found in {len(leak_files)} locations.", "Secrets may exist in .env.local only, never in pages, reports, logs or exported artifacts.", ["Save sentinel key", "Scan HTML and output artifacts"], "Redact secrets before rendering or persisting reports.", {"leak_locations": leak_files[:20], "checked_file_count": checked}, "secret_leakage")

    def _ingest(self, filename: str, source_type: str, text: str) -> dict[str, Any]:
        payload = {
            "project_id": PROJECT_ID,
            "filename": filename,
            "type": source_type,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
        result = self._json_post("/api/knowledge/ingest", payload, OWNER_HEADERS, timeout=20)
        if not result.get("ok"):
            self._add("P1", f"{source_type} ingestion failed", str(result.get("message") or result.get("error")), f"{source_type} source imports successfully.", [f"POST /api/knowledge/ingest with {filename}"], "Fix ingest endpoint and parser handling.", {"response": self._redact(result)}, "onboarding")
        return result

    def _json_get(self, route: str, *, timeout: int = 20) -> dict[str, Any]:
        with urlopen(self.base_url + route, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _json_get_with_headers(self, route: str, headers: dict[str, str], *, timeout: int = 20) -> tuple[int, dict[str, Any]]:
        request = Request(self.base_url + route, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return int(response.status), payload if isinstance(payload, dict) else {}
        except HTTPError as exc:
            payload: dict[str, Any] = {}
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                payload = parsed if isinstance(parsed, dict) else {}
            except Exception:
                pass
            return int(exc.code), payload

    def _text_get(self, route: str, *, timeout: int = 20) -> str:
        with urlopen(self.base_url + route, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")

    def _json_post(self, route: str, payload: dict[str, Any], headers: dict[str, str], *, timeout: int = 20) -> dict[str, Any]:
        request = Request(self.base_url + route, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_expect_http_error(self, route: str, payload: dict[str, Any], headers: dict[str, str]) -> int:
        try:
            self._json_post(route, payload, headers)
            return 200
        except HTTPError as exc:
            return exc.code

    def _add(self, severity: str, title: str, actual: str, expected: str, repro_steps: list[str], recommendation: str, evidence: dict[str, Any], risk_type: str) -> None:
        self.findings.append(
            AuditFinding(
                severity=severity,
                title=title,
                actual=actual,
                expected=expected,
                repro_steps=repro_steps,
                recommendation=recommendation,
                evidence=evidence,
                risk_type=risk_type,
            )
        )

    def _redact(self, value: Any) -> Any:
        text = json.dumps(value, ensure_ascii=False, default=str)
        text = text.replace(SECRET_SENTINEL, "<redacted>")
        return json.loads(text)


def run_self_dogfood_audit(root: Path | None = None, *, mock_llm: bool = True) -> dict[str, Any]:
    if root is None:
        with tempfile.TemporaryDirectory() as temp:
            return SelfDogfoodAudit(Path(temp), mock_llm=mock_llm).run()
    root.mkdir(parents=True, exist_ok=True)
    return SelfDogfoodAudit(root, mock_llm=mock_llm).run()
