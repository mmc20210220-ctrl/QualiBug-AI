from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PROJECT = "p2_runtime_current_main"
SCOPE_ID = "orders-runtime-scope"
ENVIRONMENT_REF = "customer-staging"

OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P2 Runtime Current Main API
  version: 1.0.0
paths:
  /api/orders:
    get:
      summary: List orders
      responses:
        '200': {description: ok}
    post:
      summary: Create disposable order
      responses:
        '201': {description: created}
  /api/orders/{orderId}:
    delete:
      summary: Delete disposable order
      responses:
        '204': {description: deleted}
""".strip()

PRD_TEXT = """
客户订单场景：
1. 只允许在客户批准的 staging 环境运行。
2. 读场景只能读取订单列表。
3. 写场景只能创建可清理的 disposable order，并必须具备 cleanup 合同。
""".strip()

DB_SCHEMA = """
CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  tenant_id TEXT NOT NULL
);
""".strip()


class _RuntimeApiHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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

    def do_POST(self) -> None:  # noqa: N802
        type(self).calls.append({"method": "POST", "path": self.path})
        if self.path.startswith("/api/orders"):
            self._json(201, {"id": "ord_disposable_1", "status": "created", "amount_cents": 1899})
            return
        self._json(404, {"error": "not_found"})

    def do_DELETE(self) -> None:  # noqa: N802
        type(self).calls.append({"method": "DELETE", "path": self.path})
        if self.path.startswith("/api/orders/"):
            self._json(204, {})
            return
        self._json(404, {"error": "not_found"})


class _RuntimeApi:
    def __enter__(self) -> str:
        _RuntimeApiHandler.calls = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _RuntimeApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def _prepare_project(tmp_path: Path) -> dict[str, Any]:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    input_dir = tmp_path / "platform_workspace" / PROJECT / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "schema.sql").write_text(DB_SCHEMA, encoding="utf-8")
    return register_source_asset(
        PROJECT,
        "orders-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "customer_qa_lead", "role": "qa_lead"},
    )


def _approval(tmp_path: Path, manifest: dict[str, Any], base_url: str, execution_mode: str) -> dict[str, Any]:
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
    return issue_execution_approval(
        PROJECT,
        root=tmp_path,
        campaign_id=campaign.campaign_id,
        scope_id=SCOPE_ID,
        environment_ref=ENVIRONMENT_REF,
        source_hash=manifest["source_hash"],
        target_base_url=base_url,
        execution_mode=execution_mode,
        expires_at_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        actor={"name": "customer_qa_lead", "role": "qa_lead"},
    )


def _read_contract() -> dict[str, Any]:
    return {
        "execution_policy": "safe_read_only",
        "actor": {"id": "customer_qa_lead"},
        "scenarios": [
            {
                "id": "SCN_P2_READ_ORDERS",
                "entity": "orders",
                "category": "runtime_contract",
                "steps": [{"method": "GET", "path": "/api/orders", "expected_status": 200}],
                "expected_state": "orders_observed",
            }
        ],
    }


def _write_contract(*, cleanup: bool = True, contract_write_approved: bool = True) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "id": "SCN_P2_CREATE_ORDER",
        "entity": "orders",
        "category": "runtime_contract",
        "steps": [
            {
                "method": "POST",
                "path": "/api/orders",
                "expected_status": 201,
                "body": {"sku": "demo-sku", "amount_cents": 1899},
            }
        ],
        "expected_state": "disposable_order_created",
    }
    if cleanup:
        scenario["cleanup_steps"] = [{"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204}]
    contract = {
        "execution_policy": "approved_sandbox_write",
        "actor": {"id": "customer_qa_lead"},
        "scenarios": [scenario],
    }
    if contract_write_approved:
        contract["write_approved"] = True
    return contract


def _base_context(manifest: dict[str, Any], *, execution_mode: str, approval_id: str = "", contract: dict[str, Any], write_approved: bool = False) -> dict[str, Any]:
    context = {
        "source_manifest": manifest,
        "scope_id": SCOPE_ID,
        "environment_ref": ENVIRONMENT_REF,
        "environment_type": "test",
        "execution_mode": execution_mode,
        "test_data_contract": {"strategy": "create_disposable" if write_approved else "synthetic_read_only", "write_approved": write_approved},
        "runtime_scenario_contract": contract,
    }
    if approval_id:
        context["execution_approval_id"] = approval_id
    return context


def test_p2_read_only_runtime_contract_executes_against_local_api(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _prepare_project(tmp_path)
    with _RuntimeApi() as base_url:
        approval = _approval(tmp_path, manifest, base_url, "safe_read_only")
        result = scan(
            PROJECT,
            root=tmp_path,
            prd_text=PRD_TEXT,
            api_doc_text=OPENAPI_TEXT,
            base_url=base_url,
            campaign_context=_base_context(manifest, execution_mode="safe_read_only", approval_id=approval["approval_id"], contract=_read_contract()),
        )

    assert result["success"] is True
    assert result["runtime_contract"]["status"] == "approved"
    assert result["execution_status"] == "completed"
    assert result["auto_har"]["status"] == "captured"
    assert any(call["method"] == "GET" and call["path"].startswith("/api/orders") for call in _RuntimeApiHandler.calls)


def test_p2_write_runtime_contract_is_blocked_without_cleanup_or_write_approval(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _prepare_project(tmp_path)
    with _RuntimeApi() as base_url:
        result = scan(
            PROJECT,
            root=tmp_path,
            prd_text=PRD_TEXT,
            api_doc_text=OPENAPI_TEXT,
            base_url=base_url,
            campaign_context=_base_context(
                manifest,
                execution_mode="approved_sandbox_write",
                contract=_write_contract(cleanup=False, contract_write_approved=False),
                write_approved=False,
            ),
        )

    assert result["success"] is True
    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["reason"] == "runtime_scenario_contract_blocked"
    assert "WRITE_APPROVAL_MISSING" in result["runtime_contract"]["missing_requirements"]
    assert "CLEANUP_CONTRACT_MISSING" in result["runtime_contract"]["missing_requirements"]
    assert result["auto_har"]["status"] == "no_traffic"
    assert not any(call["method"] == "POST" for call in _RuntimeApiHandler.calls)


def test_p2_approved_sandbox_write_runtime_contract_executes_post(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _prepare_project(tmp_path)
    with _RuntimeApi() as base_url:
        approval = _approval(tmp_path, manifest, base_url, "approved_sandbox_write")
        result = scan(
            PROJECT,
            root=tmp_path,
            prd_text=PRD_TEXT,
            api_doc_text=OPENAPI_TEXT,
            base_url=base_url,
            campaign_context=_base_context(
                manifest,
                execution_mode="approved_sandbox_write",
                approval_id=approval["approval_id"],
                contract=_write_contract(cleanup=True, contract_write_approved=True),
                write_approved=True,
            ),
        )

    assert result["success"] is True
    assert result["runtime_contract"]["status"] == "approved"
    assert result["runtime_contract"]["execution_approval"]["execution_mode"] == "approved_sandbox_write"
    assert result["execution_status"] == "completed"
    assert result["auto_har"]["status"] == "captured"
    assert any(call["method"] == "POST" and call["path"].startswith("/api/orders") for call in _RuntimeApiHandler.calls)
