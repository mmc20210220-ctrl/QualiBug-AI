from __future__ import annotations

"""
Cross-Organization Saga Verification — Third-Party Integration Testing.

Enterprise systems don't exist in a vacuum. They integrate with external
systems: logistics providers (J&T, SF Express), ecommerce platforms (Pinduoduo,
Taobao), payment gateways (Alipay, WeChat Pay), ERP systems, government APIs...

This module models these integrations as cross-organization business sagas:
1. Define the data flow (who sends what to whom, in what order)
2. Define invariants (what must hold at each step)
3. Verify end-to-end consistency (did the data arrive correctly on the other side?)
4. Detect cross-org bugs (field mapping errors, timeout, state mismatch)

The key insight: you can't control the external system, but you CAN verify
that what you sent matches what you received back, and that the state
transitions make business sense.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _safe_project_id, _write_json, _load_json


# ---------------------------------------------------------------------------
# Cross-organization saga definition
# ---------------------------------------------------------------------------

class CrossOrgSaga:
    """Models a business process that spans organizational boundaries."""

    def __init__(self, saga_id: str, name: str, description: str = ""):
        self.saga_id = saga_id
        self.name = name
        self.description = description
        self.steps: list[dict[str, Any]] = []
        self.invariants: list[dict[str, Any]] = []
        self.external_systems: list[str] = []

    def add_step(self, step_id: str, actor: str, action: str, direction: str,
                 endpoint: str = "", expected_response: str = "",
                 timeout_seconds: int = 30) -> "CrossOrgSaga":
        """Add a step to the saga.

        Args:
            actor: "our_system" or external system name (e.g. "jt-express")
            action: what happens (e.g. "create_waybill", "callback_status")
            direction: "outbound" (we send), "inbound" (they send), "callback" (they call us)
            endpoint: the API endpoint involved
            expected_response: what we expect back
            timeout_seconds: max time for this step to complete
        """
        self.steps.append({
            "step_id": step_id,
            "actor": actor,
            "action": action,
            "direction": direction,
            "endpoint": endpoint,
            "expected_response": expected_response,
            "timeout_seconds": timeout_seconds,
        })
        if actor not in ("our_system",) and actor not in self.external_systems:
            self.external_systems.append(actor)
        return self

    def add_invariant(self, invariant_id: str, description: str,
                      severity: str = "P1", verification: str = "") -> "CrossOrgSaga":
        self.invariants.append({
            "invariant_id": invariant_id,
            "description": description,
            "severity": severity,
            "verification": verification,
        })
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "saga_id": self.saga_id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "invariants": self.invariants,
            "external_systems": self.external_systems,
            "step_count": len(self.steps),
            "invariant_count": len(self.invariants),
        }


# ---------------------------------------------------------------------------
# Pre-built cross-org sagas for common integration patterns
# ---------------------------------------------------------------------------

def logistics_outbound_saga(
    our_service: str = "logistics-service",
    logistics_provider: str = "jt-express",
) -> CrossOrgSaga:
    """Standard outbound logistics integration:
    We create order → push to provider → provider returns tracking → provider updates status.
    """
    return (CrossOrgSaga(
        f"{our_service}_to_{logistics_provider}",
        f"{our_service} → {logistics_provider} 物流单流转",
        "我方创建物流单推送到第三方物流商，物流商返回运单号并持续回传状态",
    )
    .add_step("create_local", "our_system", "create_waybill", "internal",
              expected_response="运单创建成功，状态=pending")
    .add_step("push_to_provider", "our_system", "push_order", "outbound",
              endpoint=f"POST /api/external/{logistics_provider}/orders",
              expected_response="HTTP 200, 返回运单号 tracking_number",
              timeout_seconds=30)
    .add_step("provider_accept", logistics_provider, "accept_order", "inbound",
              endpoint=f"POST /api/callback/{logistics_provider}/accept",
              expected_response="运单号已生成，状态=accepted")
    .add_step("provider_status_update", logistics_provider, "update_status", "callback",
              endpoint=f"POST /api/callback/{logistics_provider}/status",
              expected_response="状态更新: picked_up → in_transit → delivered")
    .add_invariant("tracking_number_exists",
                   "推送后30秒内必须收到运单号", "P0",
                   "检查：推送时间与回调时间的差值 ≤ 30秒")
    .add_invariant("status_no_skip",
                   "状态流转不允许跳步（pending→accepted→picked_up→in_transit→delivered）", "P1",
                   "检查：每次状态变更只允许向前一步")
    .add_invariant("one_to_one_binding",
                   "我方物流单号与外部运单号一对一绑定", "P0",
                   "检查：一个内部运单对应唯一外部运单号，且不能被复用")
    .add_invariant("field_mapping_accuracy",
                   "推送的收件人地址、重量、件数必须与外部回传一致", "P1",
                   "检查：对比推送payload与回传确认中的关键字段")
    )


def ecommerce_inbound_saga(
    our_service: str = "order-service",
    platform: str = "pinduoduo",
) -> CrossOrgSaga:
    """Standard inbound ecommerce platform integration:
    Platform pushes orders → we receive → we process → we report status back.
    """
    return (CrossOrgSaga(
        f"{platform}_to_{our_service}",
        f"{platform} → {our_service} 订单同步",
        "电商平台推送订单到我方系统，我方处理后回传状态",
    )
    .add_step("platform_push", platform, "push_order", "inbound",
              endpoint=f"POST /api/external/{platform}/orders",
              expected_response="我方接收成功，返回内部订单号")
    .add_step("local_process", "our_system", "process_order", "internal",
              expected_response="订单处理完成，状态流转")
    .add_step("report_back", "our_system", "report_status", "outbound",
              endpoint=f"PUT /api/external/{platform}/orders/{{id}}/status",
              expected_response="HTTP 200, 平台确认收到")
    .add_invariant("field_consistency",
                   "平台推送的关键字段（金额、商品、地址）必须在本地完整保留", "P0",
                   "检查：对比推送payload与本地存储，逐一比对关键字段")
    .add_invariant("status_report_timeliness",
                   "状态回传必须在平台规定的时效内完成", "P1",
                   "检查：状态变更时间与回传时间的差值 ≤ 平台SLA")
    .add_invariant("no_duplicate_orders",
                   "同一平台订单号不能重复创建本地订单", "P0",
                   "检查：platform_order_id 必须唯一约束")
    )


def payment_gateway_saga(
    our_service: str = "payment-service",
    gateway: str = "alipay",
) -> CrossOrgSaga:
    """Standard payment gateway integration."""
    return (CrossOrgSaga(
        f"{our_service}_to_{gateway}",
        f"{our_service} → {gateway} 支付网关",
        "我方创建支付单→跳转网关→网关回调支付结果",
    )
    .add_step("create_payment", "our_system", "create_payment_order", "internal",
              expected_response="支付单创建，状态=pending")
    .add_step("redirect_to_gateway", "our_system", "redirect", "outbound",
              endpoint=f"POST /api/external/{gateway}/pay",
              expected_response="网关返回支付页面URL")
    .add_step("gateway_callback", gateway, "payment_result", "callback",
              endpoint=f"POST /api/callback/{gateway}/result",
              expected_response="支付成功/失败")
    .add_invariant("amount_match",
                   "我方支付金额必须等于网关回调金额", "P0",
                   "检查：payment.amount == callback.amount")
    .add_invariant("no_double_charge",
                   "同一支付单不能重复扣款", "P0",
                   "检查：同一 payment_id 只能有一次成功的 callback")
    .add_invariant("callback_signature",
                   "网关回调签名必须验证通过", "P0",
                   "检查：回调请求的签名 = HMAC(body, secret_key)")
    )


# ---------------------------------------------------------------------------
# Saga verification engine
# ---------------------------------------------------------------------------

def verify_cross_org_saga(
    saga: CrossOrgSaga,
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a cross-organization saga against live data.

    This is a plan-only verification by default — actual execution against
    external systems requires explicit opt-in and sandbox mode.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = options or {}
    mode = str(options.get("execution_mode") or "plan_only")

    saga_dict = saga.to_dict()
    findings: list[dict[str, Any]] = []

    # Verify invariants
    for inv in saga_dict["invariants"]:
        finding = {
            "saga_id": saga.saga_id,
            "invariant_id": inv["invariant_id"],
            "description": inv["description"],
            "severity": inv["severity"],
            "verification": inv["verification"],
            "status": "planned" if mode == "plan_only" else "needs_live_data",
            "requires": {
                "access_to_our_system": True,
                "access_to_external_system": False,  # We never directly access external systems
                "our_logs_or_api": True,               # We check our own logs/APIs
            },
        }
        findings.append(finding)

    result = {
        "saga_id": saga.saga_id,
        "name": saga.name,
        "mode": mode,
        "step_count": saga_dict["step_count"],
        "invariant_count": saga_dict["invariant_count"],
        "external_systems": saga_dict["external_systems"],
        "findings": findings,
        "governance": {
            "never_accesses_external_systems_directly": True,
            "verification_uses_our_logs_and_apis_only": True,
            "external_credentials_never_stored": True,
            "production_write_requires_sandbox_approval": True,
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Write results
    output_dir = root / "platform_outputs" / project / "cross_org_sagas"
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / f"{saga.saga_id}_verification.json", result)

    return result


# ---------------------------------------------------------------------------
# Pre-built saga registry
# ---------------------------------------------------------------------------

SAGA_REGISTRY: dict[str, CrossOrgSaga] = {
    "logistics_outbound": logistics_outbound_saga(),
    "ecommerce_inbound": ecommerce_inbound_saga(),
    "payment_gateway": payment_gateway_saga(),
}
