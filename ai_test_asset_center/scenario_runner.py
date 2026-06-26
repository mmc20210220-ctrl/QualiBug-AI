from __future__ import annotations

"""
QualiBug Scenario Runner — 业务流程编排引擎

编排多步骤业务流程，在每个步骤后验证业务不变量。
解锁 15 个需要状态设置才能验证的 Oracle Bug。
"""

import base64, json, time, urllib.error, urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ScenarioResult:
    oracle_ids: list[str]
    scenario_name: str
    verdict: str = "falsified"     # confirmed | falsified | blocked
    steps: list[dict] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


class ScenarioRunner:
    """业务流程编排 + 不变量验证"""

    def __init__(self, base_url="http://127.0.0.1:8000/api"):
        self.base = base_url
        self.A = base64.b64encode(b"admin:ADMIN").decode()
        self.P = base64.b64encode(b"planner:PLANNER").decode()
        self.O = base64.b64encode(b"operator:OPERATOR").decode()
        self.W = base64.b64encode(b"warehouse:WAREHOUSE").decode()
        self.Q = base64.b64encode(b"quality:QUALITY").decode()
        self.M = base64.b64encode(b"maint:MAINT").decode()
        self.results: list[ScenarioResult] = []

    def _api(self, method, path, data=None, role="admin"):
        url = f"{self.base}{path}"
        headers = {"Content-Type": "application/json"}
        token = getattr(self, role[0].upper(), self.A)
        headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return {"ok": True, "status": r.status, **json.loads(r.read())}
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": e.code, "error": e.read().decode()[:300]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _step(self, name, result, check=None):
        s = {"step": name, "ok": result.get("ok"), "detail": str(result)[:200]}
        if check:
            s["check"] = check
            s["check_pass"] = check(result)
        self._current_scenario.steps.append(s)
        return result

    def _items(self, result):
        """Extract items list from API response"""
        data = result.get("data", result)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items", data.get("data", []))
        return []

    # ==================================================================
    # Scenario 1: 下达→重复下达→检查预留 (PROD-032, PROD-033, PROD-034)
    # ==================================================================

    def scenario_production_release(self):
        s = ScenarioResult(
            oracle_ids=["MES-PROD-032", "MES-PROD-033", "MES-PROD-034"],
            scenario_name="生产订单下达→重复→预留验证"
        )
        self._current_scenario = s
        ts = int(time.time())

        # 1. 获取库存快照
        inv_before = self._api("GET", "/warehouse/inventory?materialCode=RM-001", role="planner")
        self._step("获取RM-001库存", inv_before)
        avail_before = sum(i.get("available_qty", i.get("availableQty", 0))
                          for i in self._items(inv_before))

        # 2. 创建生产订单（量大到超出可用库存）
        order = self._api("POST", "/production/orders", {
            "materialCode": "FG-100", "planQty": 9999,  # 远超库存
            "plannedStart": "2026-06-21 08:00:00", "plannedEnd": "2026-06-21 17:00:00",
            "priority": "HIGH"
        }, role="planner")
        self._step("创建超大订单", order)
        order_no = order.get("data", {}).get("orderNo", order.get("order_no", ""))

        # 3. 下达
        r1 = self._api("POST", f"/production/orders/{order_no}/release", role="planner")
        self._step("第1次下达", r1)

        # 4. 重复下达 ← Bug: 不应成功
        r2 = self._api("POST", f"/production/orders/{order_no}/release", role="planner")
        self._step("第2次下达(重复)", r2,
                   check=lambda r: not r.get("ok") or r.get("success") == False)

        # 5. 检查预留是否重复
        inv_after = self._api("GET", "/warehouse/inventory?materialCode=RM-001", role="planner")
        self._step("检查预留后库存", inv_after)

        # 判断
        if r1.get("ok") and r2.get("ok"):
            s.findings.append("PROD-032: 重复下达成功 — 可能重复预留")
        if r1.get("ok"):
            s.findings.append(f"PROD-034: 可用量{avail_before}但订单planQty=9999 — 未校验库存即下达")
        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)
        return {"order_no": order_no}

    # ==================================================================
    # Scenario 2: 绕前序开工 + 维修设备 (PROD-037, PROD-038, PROD-039)
    # ==================================================================

    def scenario_operation_constraints(self, order_no=None):
        s = ScenarioResult(
            oracle_ids=["MES-PROD-037", "MES-PROD-038"],
            scenario_name="工序约束→绕前序→维修设备验证"
        )
        self._current_scenario = s

        if not order_no:
            # 获取一个已下达订单
            orders = self._api("GET", "/production/orders?status=RELEASED", role="planner")
            items = orders.get("data", [])
            if not items:
                s.verdict = "blocked"
                s.findings.append("无RELEASED订单可测试")
                self.results.append(s)
                return
            order_no = items[0].get("orderNo", items[0].get("order_no"))

        # 1. 获取工序任务
        wos = self._api("GET", f"/production/orders/{order_no}/work-orders", role="operator")
        wo_list = wos.get("data", [])
        self._step("获取工序列表", wos)

        if len(wo_list) < 2:
            s.verdict = "blocked"
            s.findings.append("工序数<2，无法测试绕前序")
            self.results.append(s)
            return

        # 2. 尝试直接开工 OP20（应失败 — OP10 未完成）
        op20 = next((w for w in wo_list if w.get("operationNo", w.get("operation_no")) == 20), wo_list[-1])
        wo20 = op20.get("workOrderNo", op20.get("work_order_no", ""))
        r = self._api("POST", f"/production/work-orders/{wo20}/start", role="operator")
        self._step("绕前序→直接开工OP20", r,
                   check=lambda r: not r.get("ok") or r.get("success") == False)

        if r.get("ok") and r.get("success"):
            s.findings.append("PROD-037: 绕前序工序直接开工成功")

        # 3. 维修设备上开工
        machines = self._api("GET", "/equipment/machines?status=MAINTENANCE", role="operator")
        maint_machines = machines.get("data", [])
        if maint_machines:
            # 尝试在维修设备上开工
            op10 = wo_list[0]
            wo10 = op10.get("workOrderNo", op10.get("work_order_no", ""))
            r = self._api("POST", f"/production/work-orders/{wo10}/start", role="operator")
            self._step("维修设备上开工OP10", r,
                       check=lambda r: not r.get("ok"))

            if r.get("ok") and r.get("success"):
                s.findings.append("PROD-038: 维修中设备仍然可以开工")

        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)
        return {"order_no": order_no}

    # ==================================================================
    # Scenario 3: 报工超量+负报工+幂等 (PROD-040, PROD-041, PROD-042)
    # ==================================================================

    def scenario_reporting_validation(self, order_no=None):
        s = ScenarioResult(
            oracle_ids=["MES-PROD-040", "MES-PROD-041", "MES-PROD-042"],
            scenario_name="报工验证→负量→超量→幂等"
        )
        self._current_scenario = s

        if not order_no:
            orders = self._api("GET", "/production/orders?status=IN_PROGRESS", role="operator")
            items = orders.get("data", [])
            if not items:
                # 尝试从RELEASED里找一个并开工
                orders = self._api("GET", "/production/orders?status=RELEASED", role="planner")
                items = orders.get("data", [])
                if items:
                    order_no = items[0].get("orderNo", items[0].get("order_no"))
                    # 开工OP10
                    wos = self._api("GET", f"/production/orders/{order_no}/work-orders", role="operator")
                    wo_list = wos.get("data", [])
                    if wo_list:
                        wo10 = wo_list[0].get("workOrderNo", wo_list[0].get("work_order_no", ""))
                        # 将设备设为可用
                        self._api("PUT", "/equipment/machines/MC-01/status?status=RUNNING", role="maintenance")
                        self._api("POST", f"/production/work-orders/{wo10}/start", role="operator")
                else:
                    s.verdict = "blocked"
                    s.findings.append("无可测试订单")
                    self.results.append(s)
                    return
            else:
                order_no = items[0].get("orderNo", items[0].get("order_no"))

        # 获取工序
        wos = self._api("GET", f"/production/orders/{order_no}/work-orders", role="operator")
        wo_list = wos.get("data", [])
        if not wo_list:
            s.verdict = "blocked"
            s.findings.append("无工序任务")
            self.results.append(s)
            return

        wo = wo_list[0]
        wo_no = wo.get("workOrderNo", wo.get("work_order_no", ""))

        # 1. 负报工
        r = self._api("POST", f"/production/work-orders/{wo_no}/complete", {
            "quantity": -5, "lotNo": "LOT-NEG-SCENARIO",
            "idempotencyKey": f"scenario-neg-{int(time.time())}"
        }, role="operator")
        self._step("负报工 quantity=-5", r,
                   check=lambda r: not r.get("ok") or r.get("success") == False)
        if r.get("ok") and r.get("success"):
            s.findings.append("PROD-040: 负报工成功")

        # 2. 超计划报工
        r = self._api("POST", f"/production/work-orders/{wo_no}/complete", {
            "quantity": 99999, "lotNo": "LOT-OVER-SCENARIO",
            "idempotencyKey": f"scenario-over-{int(time.time())}"
        }, role="operator")
        self._step("超量报工 quantity=99999", r,
                   check=lambda r: not r.get("ok") or r.get("success") == False)
        if r.get("ok") and r.get("success"):
            s.findings.append("PROD-041: 超计划报工成功")

        # 3. 幂等键测试
        key = f"scenario-idem-{int(time.time())}"
        r1 = self._api("POST", f"/production/work-orders/{wo_no}/complete", {
            "quantity": 1, "lotNo": "LOT-IDEM-SCENARIO",
            "idempotencyKey": key
        }, role="operator")
        r2 = self._api("POST", f"/production/work-orders/{wo_no}/complete", {
            "quantity": 1, "lotNo": "LOT-IDEM-SCENARIO",
            "idempotencyKey": key
        }, role="operator")
        self._step("幂等键测试(相同)", r2,
                   check=lambda r: not r.get("ok") or r.get("success") == False)
        if r1.get("ok") and r2.get("ok") and r1.get("success") and r2.get("success"):
            s.findings.append("PROD-042: 幂等键未去重 — 相同请求两次都成功")

        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)

    # ==================================================================
    # Scenario 4: 订单关闭绕过检查 (PROD-049, PROD-050, PROD-051)
    # ==================================================================

    def scenario_order_close_bypass(self, order_no=None):
        s = ScenarioResult(
            oracle_ids=["MES-PROD-049", "MES-PROD-050", "MES-PROD-051"],
            scenario_name="订单关闭→取消→状态跳转验证"
        )
        self._current_scenario = s

        if not order_no:
            orders = self._api("GET", "/production/orders?status=IN_PROGRESS", role="planner")
            items = orders.get("data", [])
            if not items:
                orders = self._api("GET", "/production/orders?status=RELEASED", role="planner")
                items = orders.get("data", [])
            if not items:
                s.verdict = "blocked"
                s.findings.append("无可用订单")
                self.results.append(s)
                return
            order_no = items[0].get("orderNo", items[0].get("order_no"))

        # 1. 尝试直接关闭（工序未完成）
        r = self._api("POST", f"/production/orders/{order_no}/close", role="planner")
        self._step("关闭订单(工序未完成)", r,
                   check=lambda r: not r.get("ok") or r.get("success") == False)
        if r.get("ok") and r.get("success"):
            s.findings.append("PROD-049: 工序未完成订单被成功关闭")

        # 2. 状态跳转
        r = self._api("PUT", f"/production/orders/{order_no}/status",
                      {"status": "CLOSED"}, role="operator")
        self._step("OPERATOR直接跳CLOSED", r,
                   check=lambda r: not r.get("ok"))
        if r.get("ok") and r.get("success"):
            s.findings.append("PROD-051: 任意状态跳转成功")

        # 3. 取消→检查预留释放
        inv_before = self._api("GET", "/warehouse/inventory?materialCode=RM-001", role="planner")
        reserved_before = sum(i.get("reserved_qty", i.get("reservedQty", 0))
                             for i in self._items(inv_before))
        r = self._api("POST", f"/production/orders/{order_no}/cancel", role="planner")
        inv_after = self._api("GET", "/warehouse/inventory?materialCode=RM-001", role="planner")
        reserved_after = sum(i.get("reserved_qty", i.get("reservedQty", 0))
                            for i in self._items(inv_after))
        self._step(f"取消订单→预留 {reserved_before}→{reserved_after}", r)
        if reserved_after >= reserved_before and reserved_before > 0:
            s.findings.append("PROD-050: 取消订单未释放预留")

        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)

    # ==================================================================
    # Scenario 5: 质量闭环 (QLT-069, INV-058)
    # ==================================================================

    def scenario_quality_closure(self):
        s = ScenarioResult(
            oracle_ids=["MES-QLT-069", "MES-INV-058"],
            scenario_name="质量闭环→失败冻结→领料验证"
        )
        self._current_scenario = s

        # 1. 创建检验单并设为FAIL
        insp = self._api("POST", "/quality/inspections", {
            "materialCode": "FG-100", "lotNo": "LOT-FG-NEW",
            "prodOrderNo": "MO-202606-001", "inspectionType": "FINAL",
            "sampleQty": 10, "inspector": "quality"
        }, role="quality")
        insp_no = insp.get("data", {}).get("inspectionNo", insp.get("inspection_no", ""))
        self._step("创建检验单", insp)

        if insp_no:
            r = self._api("POST", f"/quality/inspections/{insp_no}/result", {
                "passQty": 0, "failQty": 10, "status": "FAIL",
                "measurements": [{"name": "外观", "value": 999, "lower": 0, "upper": 1, "unit": "OK"}]
            }, role="quality")
            self._step("检验设为FAIL", r)

        # 2. 尝试领料FAIL的批次
        r = self._api("POST", "/warehouse/issues", {
            "materialCode": "FG-100", "warehouseCode": "WH-FG",
            "locationCode": "F-02", "lotNo": "LOT-FG-NEW",
            "quantity": 5, "uom": "EA"
        }, role="warehouse")
        self._step("领料FAIL批次", r,
                   check=lambda r: not r.get("ok") or r.get("success") == False)
        if r.get("ok") and r.get("success"):
            s.findings.append("MES-QLT-069 + INV-058: FAIL批次仍可领料 — 质量门失效")

        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)

    # ==================================================================
    # Scenario 6: 调拨原子性 (INV-059, INV-060, INV-061)
    # ==================================================================

    def scenario_transfer_atomicity(self):
        s = ScenarioResult(
            oracle_ids=["MES-INV-059", "MES-INV-060", "MES-INV-061"],
            scenario_name="调拨原子性→不校验→失败后库存验证"
        )
        self._current_scenario = s

        # 1. 超额调拨
        r = self._api("POST", "/warehouse/transfers", {
            "materialCode": "RM-001", "lotNo": "LOT-RM-B",
            "sourceWarehouse": "WH-A", "sourceLocation": "A-02",
            "targetWarehouse": "WH-B", "targetLocation": "B-01",
            "quantity": 99999  # 远超可用量
        }, role="warehouse")
        self._step("超额调拨 quantity=99999", r,
                   check=lambda r: not r.get("ok") or r.get("success") == False)
        if r.get("ok") and r.get("success"):
            s.findings.append("INV-059: 超额调拨成功 — 未校验来源可用量")

        # 2. 调拨到不存在的目标
        r = self._api("POST", "/warehouse/transfers", {
            "materialCode": "RM-001", "lotNo": "LOT-RM-B",
            "sourceWarehouse": "WH-A", "sourceLocation": "A-02",
            "targetWarehouse": "NONEXIST-WH", "targetLocation": "NONEXIST-LOC",
            "quantity": 1
        }, role="warehouse")
        # 检查来源库存是否被错误扣减
        inv = self._api("GET", "/warehouse/inventory?materialCode=RM-001&warehouseCode=WH-A", role="warehouse")
        source_inv = next((i for i in self._items(inv) if i.get("location_code", i.get("locationCode")) == "A-02"), {})
        source_qty = source_inv.get("qty", source_inv.get("qty", 0))
        self._step("无效目标调拨→检查来源库存", inv)
        if r.get("ok") and r.get("success"):
            s.findings.append("INV-060: 调拨到无效目标成功 — 原子性破坏")
        if not r.get("ok"):
            s.findings.append(f"INV-060: 调拨被拒绝(正确), 来源库存qty={source_qty}")

        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)

    # ==================================================================
    # Scenario 7: OEE + 报表计算验证 (RPT-076-079)
    # ==================================================================

    def scenario_report_calculations(self):
        s = ScenarioResult(
            oracle_ids=["MES-RPT-076", "MES-RPT-077", "MES-RPT-078", "MES-RPT-079"],
            scenario_name="OEE/报表计算验证"
        )
        self._current_scenario = s

        # 1. OEE 检查
        oee = self._api("GET", "/reports/oee?machineCode=MC-01&workDate=2026-06-21", role="planner")
        oee_data = oee.get("data", {})
        self._step("获取OEE数据", oee)

        # 检查是否固定8小时
        planned_hours = oee_data.get("plannedProductionTime", oee_data.get("plannedHours", 0))
        if planned_hours == 8 or planned_hours == 480:
            s.findings.append("RPT-078: OEE使用固定8小时计划时间(非实际班次)")
        if planned_hours == 0:
            s.findings.append("RPT-078: OEE计划时间为0(未正确计算)")

        # 2. 生产报表
        report = self._api("GET", "/reports/production?startDate=2026-06-01&endDate=2026-06-30", role="planner")
        self._step("生产报表 6月", report)

        # 3. 仪表盘
        dash = self._api("GET", "/dashboard/summary", role="planner")
        dash_data = dash.get("data", {})
        self._step("运营仪表盘", dash)
        # 检查待处理订单是否漏计 IN_PROGRESS
        pending = dash_data.get("pendingOrders", dash_data.get("pending_orders", 0))
        if pending is not None and pending >= 0:
            orders = self._api("GET", "/production/orders?status=IN_PROGRESS", role="planner")
            in_progress = len(orders.get("data", []))
            if in_progress > 0 and (pending == 0 or pending < in_progress):
                s.findings.append(f"RPT-076: 仪表盘待处理={pending}但IN_PROGRESS={in_progress}")

        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)

    # ==================================================================
    # Scenario 8: 批次追溯完整性 (RPT-080)
    # ==================================================================

    def scenario_traceability(self):
        s = ScenarioResult(
            oracle_ids=["MES-RPT-080"],
            scenario_name="批次追溯完整性"
        )
        self._current_scenario = s

        trace = self._api("GET", "/trace/lots/LOT-RM-A", role="planner")
        trace_data = trace.get("data", {})
        self._step("批次追溯 LOT-RM-A", trace)

        upstream = trace_data.get("upstreamLots", trace_data.get("upstream_lots", []))
        downstream = trace_data.get("downstreamLots", trace_data.get("downstream_lots", []))
        transactions = trace_data.get("transactions", trace_data.get("inventoryTransactions", []))

        if not upstream and not downstream:
            s.findings.append("RPT-080: 批次追溯无上下游数据")
        if len(transactions) <= 1:
            s.findings.append(f"RPT-080: 追溯仅返回{len(transactions)}条事务(应有完整历史)")

        s.verdict = "confirmed" if s.findings else "falsified"
        self.results.append(s)

    # ==================================================================
    # 全量编排执行
    # ==================================================================

    def run_all(self):
        self.results = []
        print("=== Scenario Runner ===\n")

        # Phase 1: 创建订单并下达
        print("--- Phase 1: 生产下达 ---")
        out = self.scenario_production_release()
        order_no = out.get("order_no", "")

        # Phase 2: 工序约束
        print("\n--- Phase 2: 工序约束 ---")
        self.scenario_operation_constraints(order_no)

        # Phase 3: 报工验证
        print("\n--- Phase 3: 报工验证 ---")
        self.scenario_reporting_validation(order_no)

        # Phase 4: 关闭/取消
        print("\n--- Phase 4: 关闭/取消 ---")
        self.scenario_order_close_bypass(order_no)

        # Phase 5: 质量闭环
        print("\n--- Phase 5: 质量闭环 ---")
        self.scenario_quality_closure()

        # Phase 6: 调拨原子性
        print("\n--- Phase 6: 调拨原子性 ---")
        self.scenario_transfer_atomicity()

        # Phase 7: 报表
        print("\n--- Phase 7: 报表验证 ---")
        self.scenario_report_calculations()

        # Phase 8: 追溯
        print("\n--- Phase 8: 批次追溯 ---")
        self.scenario_traceability()

        return self.results

    def summary(self):
        confirmed = sum(1 for r in self.results if r.verdict == "confirmed")
        total_findings = sum(len(r.findings) for r in self.results)
        return {
            "scenarios": len(self.results),
            "scenarios_confirmed": confirmed,
            "total_bugs_found": total_findings,
            "bugs": [f for r in self.results for f in r.findings],
        }


if __name__ == "__main__":
    runner = ScenarioRunner()
    runner.run_all()
    print(f"\n{'='*60}")
    s = runner.summary()
    print(f"Scenarios: {s['scenarios']} | Confirmed: {s['scenarios_confirmed']} | Bugs: {s['total_bugs_found']}")
    for bug in s["bugs"]:
        print(f"  [BUG] {bug}")
