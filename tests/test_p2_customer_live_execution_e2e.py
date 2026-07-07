from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tests.test_p2_customer_scenario_e2e import DB_SCHEMA, ENVIRONMENT_REF, OPENAPI_TEXT, PRD_TEXT, PROJECT, SCOPE_ID


class _CustomerApiHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - keep test output quiet
        return

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler method
        type(self).calls.append({"method": "GET", "path": self.path})
        if self.path.startswith("/api/orders"):
            self._write_json(
                200,
                {
                    "orders": [
                        {"id": "ord_1001", "status": "paid", "amount_cents": 1299, "tenant_id": "tenant_demo"}
                    ]
                },
            )
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler method
        type(self).calls.append({"method": "POST", "path": self.path})
        if self.path.startswith("/api/orders"):
            self._write_json(201, {"id": "ord_1002", "status": "created", "amount_cents": 1899})
            return
        self._write_json(404, {"error": "not_found"})


class _LiveCustomerApi:
    def __enter__(self) -> str:
        _CustomerApiHandler.calls = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CustomerApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def _prepare_customer_project(tmp_path: Path) -> dict[str, Any]:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    project_input = tmp_path / "platform_workspace" / PROJECT / "input"
    project_input.mkdir(parents=True, exist_ok=True)
    (project_input / "checkout_schema.sql").write_text(DB_SCHEMA, encoding="utf-8")
    return register_source_asset(
        PROJECT,
        "checkout-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "customer_qa_lead", "role": "qa_lead"},
        metadata={"scenario": "checkout_order_payment_refund"},
    )


def _runtime_scenario_contract() -> dict[str, Any]:
    return {
        "schema_version": "runtime-scenario-contract-v1",
        "execution_policy": "safe_read_only",
        "actor": {"id": "customer_qa_lead"},
        "scenarios": [
            {
                "id": "SCN_LIVE_ORDERS_READ",
                "title": "Live read approved orders",
                "entity": "orders",
                "category": "runtime_contract",
                "severity": "P2",
                "steps": [
                    {
                        "order": 1,
                        "action": "list_orders",
                        "method": "GET",
                        "path": "/api/orders",
                        "expected_status": 200,
                        "actor": "customer_qa_lead",
                    }
                ],
                "expected_state": "orders_observed",
                "oracle_rules": ["HTTP read succeeds"],
            }
        ],
    }


def _issue_live_execution_approval(tmp_path: Path, manifest: dict[str, Any], base_url: str) -> dict[str, Any]:
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval

    snapshot = source_snapshot_hash(PRD_TEXT, OPENAPI_TEXT, DB_SCHEMA, SCOPE_ID, ENVIRONMENT_REF)
    campaign = EnterpriseCampaign.create(
        PROJECT,
        SCOPE_ID,
        ENVIRONMENT_REF,
        snapshot,
        source_id=manifest["source_id"],
        source_hash=manifest["source_hash"],
        policy_version="",
    )
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return issue_execution_approval(
        PROJECT,
        root=tmp_path,
        campaign_id=campaign.campaign_id,
        scope_id=SCOPE_ID,
        environment_ref=ENVIRONMENT_REF,
        source_hash=manifest["source_hash"],
        target_base_url=base_url,
        execution_mode="safe_read_only",
        expires_at_utc=expires_at,
        actor={"name": "customer_qa_lead", "role": "qa_lead"},
    )


def test_p2_live_execution_requires_approval_before_http_traffic(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _prepare_customer_project(tmp_path)
    with _LiveCustomerApi() as base_url:
        result = scan(
            PROJECT,
            root=tmp_path,
            prd_text=PRD_TEXT,
            api_doc_text=OPENAPI_TEXT,
            base_url=base_url,
            campaign_context={
                "source_manifest": manifest,
                "scope_id": SCOPE_ID,
                "environment_ref": ENVIRONMENT_REF,
                "test_data_contract": {"strategy": "synthetic_read_only", "write_approved": False},
                "execution_mode": "safe_read_only",
                "runtime_scenario_contract": _runtime_scenario_contract(),
            },
        )

    assert result["success"] is True
    assert result["runtime_contract"]["status"] == "blocked"
    assert result["runtime_contract"]["reason"] == "execution_approval_required"
    assert "EXECUTION_APPROVAL_MISSING" in result["runtime_contract"].get("missing_requirements", [])
    assert result["execution_status"] == "blocked"
    assert _CustomerApiHandler.calls == []


def test_p2_live_execution_hits_fake_customer_api_with_runtime_contract(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _prepare_customer_project(tmp_path)
    with _LiveCustomerApi() as base_url:
        approval = _issue_live_execution_approval(tmp_path, manifest, base_url)
        result = scan(
            PROJECT,
            root=tmp_path,
            prd_text=PRD_TEXT,
            api_doc_text=OPENAPI_TEXT,
            base_url=base_url,
            campaign_context={
                "source_manifest": manifest,
                "scope_id": SCOPE_ID,
                "environment_ref": ENVIRONMENT_REF,
                "test_data_contract": {"strategy": "synthetic_read_only", "write_approved": False},
                "execution_mode": "safe_read_only",
                "execution_approval_id": approval["approval_id"],
                "runtime_scenario_contract": _runtime_scenario_contract(),
            },
        )

    assert result["success"] is True
    assert result["runtime_contract"]["status"] == "approved"
    assert result["runtime_contract"]["execution_approval"]["status"] == "approved"
    assert result["execution_status"] == "completed"
    assert result["v12"]["phases"]["scenario_generation"]["runtime_scenario_contract_present"] is True
    assert result["v12"]["phases"]["scenario_generation"]["runtime_contract_scenarios"] >= 1
    assert result["v12"]["phases"]["execution"]["executed"] >= 1
    assert result["auto_har"]["status"] == "captured"
    assert result["auto_har"]["total_calls"] >= 1
    assert any(call["method"] == "GET" and call["path"].startswith("/api/orders") for call in _CustomerApiHandler.calls)
    assert Path(result["report_path"]).exists()
    saved = json.loads((tmp_path / "platform_outputs" / PROJECT / "scan_result.json").read_text(encoding="utf-8"))
    assert saved["runtime_contract"]["execution_approval"]["status"] == "approved"
    assert saved["v12"]["phases"]["scenario_generation"]["runtime_scenario_contract_present"] is True
    assert saved["auto_har"]["status"] == "captured"
