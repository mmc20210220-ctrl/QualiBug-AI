from __future__ import annotations

"""
QualiBug Runtime Verifier — 自动化运行时探测引擎

将 Oracle 缺陷定义映射为可执行的 API 探针，
对目标系统发起安全探测并对比预期 vs 实际行为。

这是三层架构中 Verifier 层的运行时代码实现。
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 探针定义
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    oracle_id: str
    verdict: str          # confirmed | falsified | inconclusive | blocked
    expected: str
    actual: str
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    blocked_reason: str = ""


DEFAULT_BASE_URL = os.environ.get("QUALIBUG_DEFAULT_BASE_URL", "http://127.0.0.1:8088")


def _default_role_tokens() -> dict[str, str]:
    return {
        "admin": base64.b64encode(b"admin:ADMIN").decode(),
        "planner": base64.b64encode(b"planner:PLANNER").decode(),
        "operator": base64.b64encode(b"operator:OPERATOR").decode(),
        "warehouse": base64.b64encode(b"warehouse:WAREHOUSE").decode(),
        "quality": base64.b64encode(b"quality:QUALITY").decode(),
        "maintenance": base64.b64encode(b"maint:MAINT").decode(),
        "viewer": base64.b64encode(b"viewer:VIEWER").decode(),
        "forged": base64.b64encode(b"viewer:ADMIN").decode(),
    }


def _load_role_tokens(overrides: dict[str, str] | None = None) -> dict[str, str]:
    tokens = _default_role_tokens()
    env_payload = os.environ.get("QUALIBUG_RUNTIME_TOKENS", "").strip()
    if env_payload:
        try:
            loaded = json.loads(env_payload)
        except Exception:
            loaded = {}
        if isinstance(loaded, dict):
            for role, token in loaded.items():
                role_name = str(role or "").strip()
                token_value = str(token or "").strip()
                if role_name and token_value:
                    tokens[role_name] = token_value
    if isinstance(overrides, dict):
        for role, token in overrides.items():
            role_name = str(role or "").strip()
            token_value = str(token or "").strip()
            if role_name and token_value:
                tokens[role_name] = token_value
    return tokens


class RuntimeVerifier:
    """运行时验证器，默认加载外部注入的角色凭证配置。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, role_tokens: dict[str, str] | None = None):
        self.base_url = str(base_url or DEFAULT_BASE_URL)
        self.results: list[ProbeResult] = []

        # Legacy benchmark tokens remain as a compatibility fallback only.
        self.tokens = _load_role_tokens(role_tokens)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _api(self, method: str, path: str, data=None, role: str = "admin",
             extra_headers: dict | None = None, no_auth: bool = False) -> dict:
        """发送 API 请求并返回 JSON"""
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        if not no_auth and role in self.tokens:
            headers["Authorization"] = f"Bearer {self.tokens[role]}"

        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"_http": resp.status, **json.loads(resp.read())}
        except urllib.error.HTTPError as e:
            return {"_http": e.code, "_error": e.read().decode()[:500]}

    def _verify(self, oracle_id: str, expected: str, probe_fn, **kwargs):
        """执行一条探针并记录结果"""
        try:
            result = probe_fn(**kwargs)
            verdict = "confirmed" if result else "falsified"
            self.results.append(ProbeResult(
                oracle_id=oracle_id,
                verdict=verdict,
                expected=expected,
                actual=str(result)[:200],
            ))
        except Exception as e:
            self.results.append(ProbeResult(
                oracle_id=oracle_id,
                verdict="blocked",
                expected=expected,
                actual="",
                blocked_reason=str(e)[:200],
            ))

    # ------------------------------------------------------------------
    # 身份与治理 (GOV)
    # ------------------------------------------------------------------

    def probe_gov_001_password_plaintext(self) -> ProbeResult:
        """明文密码泄露"""
        r = self._api("GET", "/users", role="admin")
        users = r.get("data", [])
        leaked = any("password" in str(u) for u in users)
        sample = next((f"{u['username']}:{u.get('password','?')}" for u in users[:2]), "")
        return ProbeResult(
            oracle_id="MES-GOV-001",
            verdict="confirmed" if leaked else "falsified",
            expected="密码不应出现在 API 响应中",
            actual=f"泄露 {len(users)} 用户密码, 如 {sample}" if leaked else "未泄露",
            confidence=1.0,
        )

    def probe_gov_002_token_forgery(self) -> ProbeResult:
        """令牌可伪造"""
        r = self._api("GET", "/users", role="forged")
        return ProbeResult(
            oracle_id="MES-GOV-002",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="伪造 token 应被拒绝",
            actual=f"伪造 viewer→ADMIN token 访问成功" if r.get("success") else "被拒绝",
            evidence={"response": str(r)[:200]},
        )

    def probe_gov_003_xrole_escalation(self) -> ProbeResult:
        """X-Role 提权"""
        r = self._api("GET", "/production/orders", no_auth=True,
                      extra_headers={"X-Role": "ADMIN"})
        items = len(r.get("data", []))
        return ProbeResult(
            oracle_id="MES-GOV-003",
            verdict="confirmed" if r.get("success") and items > 0 else "falsified",
            expected="X-Role 头不应被信任",
            actual=f"无认证+X-Role=ADMIN 成功访问, {items} 条数据" if r.get("success") else "被拒绝",
        )

    def probe_gov_004_password_in_userlist(self) -> ProbeResult:
        """用户列表含密码字段"""
        r = self._api("GET", "/users", role="admin")
        users = r.get("data", [])
        has_field = any(u.get("password") is not None for u in users)
        return ProbeResult(
            oracle_id="MES-GOV-004",
            verdict="confirmed" if has_field else "falsified",
            expected="GET /users 不应包含 password 字段",
            actual="包含 password 字段" if has_field else "不含",
        )

    def probe_gov_005_noauth_read(self) -> ProbeResult:
        """无认证读取业务数据"""
        endpoints = ["/production/orders", "/warehouse/inventory",
                     "/quality/inspections", "/master/materials"]
        accessible = []
        for ep in endpoints:
            r = self._api("GET", ep, no_auth=True)
            if r.get("success") or r.get("data"):
                accessible.append(ep)
        return ProbeResult(
            oracle_id="MES-GOV-005",
            verdict="confirmed" if accessible else "falsified",
            expected="所有业务端点应要求认证",
            actual=f"{len(accessible)}/{len(endpoints)} 端点无认证可访问: {accessible}" if accessible else "全部要求认证",
            evidence={"accessible": accessible},
        )

    def probe_gov_006_export_noauth(self) -> ProbeResult:
        """导出未限角色"""
        r = self._api("GET", "/export/all", role="viewer")
        return ProbeResult(
            oracle_id="MES-GOV-006",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="仅 ADMIN 可导出",
            actual="VIEWER 成功导出" if r.get("success") else "被拒绝",
        )

    def probe_gov_008_notification_leak(self) -> ProbeResult:
        """通知 userId 越权"""
        r = self._api("GET", "/notifications?userId=1", role="viewer")
        items = len(r.get("data", []))
        return ProbeResult(
            oracle_id="MES-GOV-008",
            verdict="confirmed" if r.get("success") and items > 0 else "inconclusive",
            expected="用户只能看自己的通知",
            actual=f"VIEWER 通过 userId=1 查到 {items} 条" if items > 0 else "无数据/需更多测试",
        )

    def probe_gov_010_http200_errors(self) -> ProbeResult:
        """业务错误返回 200"""
        r = self._api("POST", "/production/orders/NONEXIST/close", data={}, role="admin")
        return ProbeResult(
            oracle_id="MES-GOV-010",
            verdict="confirmed" if r.get("_http") == 200 else "falsified",
            expected="不存在资源操作应返回 4xx",
            actual=f"HTTP {r.get('_http')} (应为 404/409)",
        )

    # ------------------------------------------------------------------
    # 主数据 (MD)
    # ------------------------------------------------------------------

    def probe_md_013_negative_safety_stock(self) -> ProbeResult:
        """负安全库存"""
        r = self._api("POST", "/master/materials", data={
            "code": f"PROBE-NEG-{int(time.time())}", "name": "安全库存测试",
            "spec": "T", "uom": "EA", "safetyStock": -100, "status": "ACTIVE"
        }, role="planner")
        return ProbeResult(
            oracle_id="MES-MD-013",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="safetyStock 应拒绝负数",
            actual="成功创建" if r.get("success") else f"被拒绝: {r.get('message','')[:80]}",
        )

    def probe_md_015_delete_active_material(self) -> ProbeResult:
        """删除在用物料"""
        # 先创建一个测试物料
        code = f"PROBE-DEL-{int(time.time())}"
        self._api("POST", "/master/materials", data={
            "code": code, "name": "待删除", "spec": "T", "uom": "EA",
            "safetyStock": 10, "status": "ACTIVE"
        }, role="planner")
        r = self._api("DELETE", f"/master/materials/{code}", role="admin")
        return ProbeResult(
            oracle_id="MES-MD-015",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="ACTIVE 物料应拒绝删除",
            actual="删除成功（未校验状态）" if r.get("success") else f"正确拒绝: {r.get('message','')[:80]}",
        )

    # ------------------------------------------------------------------
    # 生产执行 (PROD)
    # ------------------------------------------------------------------

    def probe_prod_026_negative_plan_qty(self) -> ProbeResult:
        """负数计划量"""
        r = self._api("POST", "/production/orders", data={
            "materialCode": "FG-100", "planQty": -5
        }, role="planner")
        return ProbeResult(
            oracle_id="MES-PROD-026",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="planQty 应 >0",
            actual=f"planQty=-5 创建{'成功' if r.get('success') else '被拒'}",
        )

    def probe_prod_027_nonexistent_material(self) -> ProbeResult:
        """不存在物料"""
        r = self._api("POST", "/production/orders", data={
            "materialCode": f"NONEXIST-{int(time.time())}", "planQty": 10
        }, role="planner")
        return ProbeResult(
            oracle_id="MES-PROD-027",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="不存在物料应拒绝",
            actual="创建成功" if r.get("success") else "正确拒绝",
        )

    def probe_prod_032_double_release(self) -> ProbeResult:
        """重复下达重复预留"""
        r1 = self._api("POST", "/production/orders/MO-202606-001/release", role="planner")
        r2 = self._api("POST", "/production/orders/MO-202606-001/release", role="planner")
        return ProbeResult(
            oracle_id="MES-PROD-032",
            verdict="confirmed" if r1.get("success") and r2.get("success") else "falsified",
            expected="第二次下达应返回 409 或幂等返回",
            actual=f"两次下达均成功" if r1.get("success") and r2.get("success") else "被拒绝",
        )

    def probe_prod_037_skip_prev_op(self) -> ProbeResult:
        """绕前序开工"""
        r = self._api("POST", "/production/work-orders/MO-202606-001-OP20/start", role="operator")
        return ProbeResult(
            oracle_id="MES-PROD-037",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="前序未完成应拒绝开工",
            actual=f"OP20 直接开工{'成功（绕过了OP10）' if r.get('success') else '被拒'}",
        )

    def probe_prod_040_negative_report(self) -> ProbeResult:
        """负报工量"""
        r = self._api("POST", "/production/work-orders/MO-202606-001-OP10/complete",
                      data={"quantity": -5, "lotNo": "LOT-NEG", "idempotencyKey": f"neg-{int(time.time())}"},
                      role="operator")
        return ProbeResult(
            oracle_id="MES-PROD-040",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="报工数量应 >0",
            actual="负报工成功" if r.get("success") else "被拒绝",
        )

    def probe_prod_041_over_plan_report(self) -> ProbeResult:
        """报工超计划"""
        r = self._api("POST", "/production/work-orders/MO-202606-001-OP10/complete",
                      data={"quantity": 99999, "lotNo": "LOT-OVER", "idempotencyKey": f"over-{int(time.time())}"},
                      role="operator")
        return ProbeResult(
            oracle_id="MES-PROD-041",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="累计报工不应超过计划量",
            actual="超计划报工成功" if r.get("success") else "被拒绝",
        )

    def probe_prod_042_idempotency_fail(self) -> ProbeResult:
        """幂等键未去重"""
        key = f"idem-{int(time.time())}"
        r1 = self._api("POST", "/production/work-orders/MO-202606-001-OP10/complete",
                       data={"quantity": 1, "lotNo": "LOT-IDEM", "idempotencyKey": key}, role="operator")
        r2 = self._api("POST", "/production/work-orders/MO-202606-001-OP10/complete",
                       data={"quantity": 1, "lotNo": "LOT-IDEM", "idempotencyKey": key}, role="operator")
        both_ok = r1.get("success") and r2.get("success")
        return ProbeResult(
            oracle_id="MES-PROD-042",
            verdict="confirmed" if both_ok else "falsified",
            expected="相同幂等键第二次应返回已有结果",
            actual="两次均成功（未去重）" if both_ok else "去重生效",
        )

    # ------------------------------------------------------------------
    # 仓储 (INV)
    # ------------------------------------------------------------------

    def probe_inv_054_negative_receipt(self) -> ProbeResult:
        """负收货"""
        r = self._api("POST", "/warehouse/receipts", data={
            "materialCode": "RM-001", "warehouseCode": "WH-A", "locationCode": "A-01",
            "lotNo": f"LOT-NEG-{int(time.time())}", "quantity": -10, "uom": "EA"
        }, role="warehouse")
        return ProbeResult(
            oracle_id="MES-INV-054",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="收货数量应 >0",
            actual="负收货成功" if r.get("success") else "被拒绝",
        )

    def probe_inv_056_excessive_issue(self) -> ProbeResult:
        """超额领料"""
        r = self._api("POST", "/warehouse/issues", data={
            "materialCode": "RM-001", "warehouseCode": "WH-A", "locationCode": "A-01",
            "lotNo": "LOT-RM-A", "quantity": 99999, "uom": "EA"
        }, role="warehouse")
        return ProbeResult(
            oracle_id="MES-INV-056",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="领料不应超过可用量",
            actual="巨额领料成功（未校验可用量）" if r.get("success") else "正确拒绝",
        )

    def probe_inv_057_negative_issue(self) -> ProbeResult:
        """负数领料增加库存"""
        r = self._api("POST", "/warehouse/issues", data={
            "materialCode": "RM-001", "warehouseCode": "WH-A", "locationCode": "A-01",
            "lotNo": "LOT-RM-A", "quantity": -5, "uom": "EA"
        }, role="warehouse")
        return ProbeResult(
            oracle_id="MES-INV-057",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="领料负数应被拒绝",
            actual="负数领料成功（变相增加库存）" if r.get("success") else "被拒绝",
        )

    # ------------------------------------------------------------------
    # 质量 (QLT)
    # ------------------------------------------------------------------

    def probe_qlt_065_zero_sample(self) -> ProbeResult:
        """零抽样"""
        r = self._api("POST", "/quality/inspections", data={
            "materialCode": "FG-100", "lotNo": "LOT-FG-100-001",
            "prodOrderNo": "MO-202606-001", "inspectionType": "FINAL",
            "sampleQty": 0, "inspector": "quality"
        }, role="quality")
        return ProbeResult(
            oracle_id="MES-QLT-065",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="sampleQty 应 >0",
            actual="零抽样成功" if r.get("success") else "被拒绝",
        )

    # ------------------------------------------------------------------
    # 设备 (EQP)
    # ------------------------------------------------------------------

    def probe_eqp_071_operator_status_change(self) -> ProbeResult:
        """操作员改设备状态"""
        r = self._api("PUT", "/equipment/machines/MC-01/status?status=FAULT", role="operator")
        return ProbeResult(
            oracle_id="MES-EQP-071",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="OPERATOR 不应能改设备状态",
            actual="OPERATOR 成功改为 FAULT" if r.get("success") else "被拒绝",
        )

    def probe_eqp_072_meter_rollback(self) -> ProbeResult:
        """计数器回退"""
        r = self._api("PUT", "/equipment/machines/MC-01/meter", data={"meterHours": 1.0}, role="maintenance")
        return ProbeResult(
            oracle_id="MES-EQP-072",
            verdict="confirmed" if r.get("success") else "falsified",
            expected="计数器应单调递增",
            actual="计数器回退成功" if r.get("success") else "被拒绝",
        )

    # ------------------------------------------------------------------
    # 集成 (INT)
    # ------------------------------------------------------------------

    def probe_int_081_erp_dedup(self) -> ProbeResult:
        """ERP 事件去重"""
        event = {
            "externalRef": f"ERP-DUP-{int(time.time())}",
            "eventType": "GOODS_RECEIPT",
            "payload": {"receiptNo": f"GR-DUP-{int(time.time())}", "materialCode": "RM-001", "qty": 1},
            "sourceSystem": "ERP"
        }
        r1 = self._api("POST", "/integrations/erp/events", data=event, role="planner")
        r2 = self._api("POST", "/integrations/erp/events", data=event, role="planner")
        return ProbeResult(
            oracle_id="MES-INT-081",
            verdict="confirmed" if r1.get("success") and r2.get("success") else "falsified",
            expected="重复事件应返回首次结果",
            actual="两次均成功（未去重）" if r1.get("success") and r2.get("success") else "去重生效",
        )

    # ------------------------------------------------------------------
    # 批量执行
    # ------------------------------------------------------------------

    def run_all(self) -> list[ProbeResult]:
        """执行所有可自动探测的 Oracle"""
        self.results = []

        probes = [
            # GOV
            self.probe_gov_001_password_plaintext,
            self.probe_gov_002_token_forgery,
            self.probe_gov_003_xrole_escalation,
            self.probe_gov_004_password_in_userlist,
            self.probe_gov_005_noauth_read,
            self.probe_gov_006_export_noauth,
            self.probe_gov_008_notification_leak,
            self.probe_gov_010_http200_errors,
            # MD
            self.probe_md_013_negative_safety_stock,
            self.probe_md_015_delete_active_material,
            # PROD
            self.probe_prod_026_negative_plan_qty,
            self.probe_prod_027_nonexistent_material,
            self.probe_prod_032_double_release,
            self.probe_prod_037_skip_prev_op,
            self.probe_prod_040_negative_report,
            self.probe_prod_041_over_plan_report,
            self.probe_prod_042_idempotency_fail,
            # INV
            self.probe_inv_054_negative_receipt,
            self.probe_inv_056_excessive_issue,
            self.probe_inv_057_negative_issue,
            # QLT
            self.probe_qlt_065_zero_sample,
            # EQP
            self.probe_eqp_071_operator_status_change,
            self.probe_eqp_072_meter_rollback,
            # INT
            self.probe_int_081_erp_dedup,
        ]

        for i, probe_fn in enumerate(probes):
            try:
                result = probe_fn()
                self.results.append(result)
                tag = "[PASS]" if result.verdict == "confirmed" else ("[FAIL]" if result.verdict == "falsified" else "[FIX]")
                print(f"{i+1:2d}. {tag} {result.oracle_id}: {result.verdict}")
            except Exception as e:
                self.results.append(ProbeResult(
                    oracle_id="UNKNOWN",
                    verdict="blocked",
                    expected="",
                    actual="",
                    blocked_reason=str(e)[:200],
                ))
                print(f"{i+1:2d}. [ERROR] ERROR: {e}")

        return self.results

    def summary(self) -> dict:
        confirmed = sum(1 for r in self.results if r.verdict == "confirmed")
        falsified = sum(1 for r in self.results if r.verdict == "falsified")
        inconclusive = sum(1 for r in self.results if r.verdict == "inconclusive")
        blocked = sum(1 for r in self.results if r.verdict == "blocked")

        return {
            "total_probes": len(self.results),
            "confirmed": confirmed,
            "falsified": falsified,
            "inconclusive": inconclusive,
            "blocked": blocked,
            "hit_rate": round(confirmed / max(len(self.results), 1) * 100, 1),
            "oracle_reference": os.environ.get("QUALIBUG_RUNTIME_ORACLE_REFERENCE", "legacy_runtime_probe_pack"),
            "note": "剩余 {} 个 Oracle 需要 DB 访问/状态编排/并发测试".format(
                82 - confirmed
            ),
        }


# Backward compatibility for existing imports while removing MES-only naming
# from the primary entrypoint.
MESRuntimeVerifier = RuntimeVerifier


# ---------------------------------------------------------------------------
# 独立运行
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    v = RuntimeVerifier()
    v.run_all()
    print(f"\n{'='*60}")
    print(json.dumps(v.summary(), indent=2, ensure_ascii=False))
