from __future__ import annotations

import json
from pathlib import Path


PROJECT = "customer_checkout_pilot"
SCOPE_ID = "checkout-orders-scope"
ENVIRONMENT_REF = "customer-staging"

OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: Customer Checkout Pilot API
  version: 1.0.0
paths:
  /api/orders:
    get:
      summary: List orders
      responses:
        '200':
          description: ok
    post:
      summary: Create order
      responses:
        '201':
          description: created
  /api/orders/{orderId}/pay:
    post:
      summary: Pay an order
      responses:
        '200':
          description: paid
  /api/refunds:
    post:
      summary: Create refund
      responses:
        '201':
          description: refund created
""".strip()

PRD_TEXT = """
客户结账场景：
1. 用户创建订单后才能支付。
2. 已支付订单允许申请退款。
3. 订单金额、支付金额、退款金额需要保持一致性。
4. 测试只允许使用客户批准的 staging 环境和合成测试数据。
""".strip()

DB_SCHEMA = """
CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  tenant_id TEXT NOT NULL
);
CREATE TABLE payments (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE refunds (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL
);
""".strip()


def _prepare_customer_project(tmp_path: Path) -> dict:
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


def test_p2_customer_scenario_acceptance_then_plan_only_scan(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke

    manifest = _prepare_customer_project(tmp_path)

    acceptance = run_acceptance_smoke(
        root=tmp_path,
        install_patches=True,
        write_doctor=True,
        project=PROJECT,
        scan_base_url="https://staging.customer.example",
        scope_id=SCOPE_ID,
        environment_ref=ENVIRONMENT_REF,
        test_data_strategy="synthetic_read_only",
        require_scenario_ready=True,
    )

    scenario = acceptance["checks"]["scenario_readiness"]
    assert scenario["ready"] is True
    assert scenario["source_registry"]["asset_count"] == 1
    assert acceptance["acceptance"]["level"] in {"accepted", "warning"}
    assert not any(str(item).startswith("scenario_readiness_missing:") for item in acceptance["acceptance"].get("blockers", []))

    result = scan(
        PROJECT,
        root=tmp_path,
        prd_text=PRD_TEXT,
        api_doc_text=OPENAPI_TEXT,
        base_url="",
        campaign_context={
            "source_manifest": manifest,
            "scope_id": SCOPE_ID,
            "environment_ref": ENVIRONMENT_REF,
            "test_data_contract": {"strategy": "synthetic_read_only", "write_approved": False},
            "release_policy": {"mode": "private_pilot", "require_real_confirmation": False},
        },
    )

    assert result["success"] is True
    assert result["project"] == PROJECT
    assert result["runtime_contract"]["status"] == "plan_only"
    assert result["runtime_contract"]["source_manifest"]["source_id"] == manifest["source_id"]
    assert result["campaign"]["scope_id"] == SCOPE_ID
    assert result["campaign"]["environment_ref"] == ENVIRONMENT_REF
    assert result["campaign"]["source_id"] == manifest["source_id"]
    assert "behavior_slice_ledger" in result
    assert "test_data_plan" in result
    assert "release_gate" in result
    assert "evidence_bundle" in result
    assert Path(result["report_path"]).exists()
    assert (tmp_path / "platform_outputs" / PROJECT / "scan_result.json").exists()


def test_p2_customer_scenario_blocks_write_strategy_without_approval(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _prepare_customer_project(tmp_path)
    result = scan(
        PROJECT,
        root=tmp_path,
        prd_text=PRD_TEXT,
        api_doc_text=OPENAPI_TEXT,
        base_url="https://staging.customer.example",
        campaign_context={
            "source_manifest": manifest,
            "scope_id": SCOPE_ID,
            "environment_ref": ENVIRONMENT_REF,
            "test_data_contract": {"strategy": "create_disposable", "write_approved": False},
        },
    )

    assert result["success"] is True
    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["status"] == "blocked"
    gap_codes = {item.get("code") for item in result["coverage_gaps"] if isinstance(item, dict)}
    assert "WRITE_APPROVAL_MISSING" in gap_codes
    assert result["execution_status"] == "blocked"


def test_p2_customer_scenario_outputs_are_customer_safe_json(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _prepare_customer_project(tmp_path)
    result = scan(
        PROJECT,
        root=tmp_path,
        prd_text=PRD_TEXT,
        api_doc_text=OPENAPI_TEXT,
        base_url="",
        campaign_context={
            "source_manifest": manifest,
            "scope_id": SCOPE_ID,
            "environment_ref": ENVIRONMENT_REF,
            "test_data_contract": {"strategy": "synthetic_read_only", "write_approved": False},
        },
    )

    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert report["project"] == PROJECT
    assert report["campaign"]["source_id"] == manifest["source_id"]
    assert report["runtime_contract"]["source_manifest"]["source_id"] == manifest["source_id"]
    assert "evidence_bundle" in report
    assert "release_gate" in report
    assert "test_data_plan" in report
