# Phase55：确认 Bug 数据飞轮（企业回灌闭环）

## 目标

把“系统发现的疑似问题”转成企业可治理的学习资产：

`候选缺陷 → QA 评审 → 独立审批 → 优先级学习 → 回归固化 → 后续版本验证`

本阶段**不新增 Bug 模板**，而是让已经发现并被企业确认的高价值问题，持续提升后续发现的准确性与业务价值。

## 核心规则

1. 只有人工明确标记为 `confirmed` 的候选才会进入待推广队列。
2. 确认者和批准学习的人必须不同，执行“四眼原则”。
3. 未批准的确认 Bug 不改变探针优先级、不进入回归套件。
4. 误报不会压低整类风险。只有精确限定 `risk_type + method + path` 的例外范围，且经审批后才会对该范围降低优先级。
5. 所有评审和审批事件写入哈希链账本。账本篡改会被发现，之后的新增回灌会被阻断。
6. 学习资产不保存原始请求/响应、Token、业务行、评审原文；仅保存脱敏元数据、结构和不可逆摘要。

## 产生的企业资产

位于每个项目的 `platform_workspace/<project>/defect_discovery/`：

- `confirmed_bug_decision_ledger.jsonl`：追加式、哈希链审计账本。
- `confirmed_bug_flywheel_profile.json`：已批准模式、待审批项、治理摘要。
- `confirmed_bug_registry.json`：按业务指纹归并后的确认缺陷登记册。
- `confirmed_bug_promotion_manifest.json`：待审批/已审批的学习推广项。
- `confirmed_bug_exception_registry.json`：精确范围的误报例外规则。
- `confirmed_bug_regression_candidates.json`：由已批准确认缺陷生成的回归候选。
- `confirmed_bug_feedback.jsonl`：仅含脱敏元数据的兼容投影，可供既有多源推理使用。

对应报告：

`platform_outputs/<project>/confirmed_bug_flywheel/confirmed_bug_flywheel_report.html`

## 使用方式

### 1. 记录 QA 评审

```python
from ai_test_asset_center.confirmed_bug_flywheel import record_bug_review

record_bug_review(
    "customer_a",
    candidate={
        "issue_id": "ISSUE_001",
        "risk_type": "business_amount_conservation",
        "oracle_family": "side_effect_amount_conservation",
        "source": "business_causality_conservation",
        "method": "GET",
        "path": "/orders",
        "expected": "订单金额应等于支付金额之和",
        "actual": "发现订单与支付金额不一致",
        "evidence": {"request": {"method": "GET"}, "response": {"status_code": 200}},
    },
    review={
        "decision": "confirmed",
        "reviewer": "qa_alice",
        "reviewer_role": "qa_reviewer",
        "human_severity": "P1",
        "root_cause": "backend",
        "is_high_value": True,
    },
)
```

### 2. 独立审批学习推广

```python
from ai_test_asset_center.confirmed_bug_flywheel import approve_learning_promotion

approve_learning_promotion(
    "customer_a",
    promotion_id="PROMO_LEARNING_AND_REGRESSION_xxx",
    approver="qa_lead_bob",
    approver_role="quality_owner",
)
```

批准后，系统才会：

- 为相同业务 Oracle / 风险 / 接口的探针增加优先级；
- 把只读验证加入 release 回归候选；
- 对写路径生成 `sandbox_required` 回归候选，默认不进入生产验证。

### 3. 检查账本完整性

```bash
python -m ai_test_asset_center.confirmed_bug_flywheel --project customer_a --verify
```

## 误报处理

误报可记录为 `false_positive`。若确实是企业规则允许的例外，必须提供精确范围，例如：

```json
{
  "decision": "false_positive",
  "reviewer": "qa_alice",
  "exception_scope": {
    "risk_type": "business_causality",
    "method": "GET",
    "path": "/orders",
    "source": "business_causality_conservation",
    "expires_at_utc": "2027-12-31T00:00:00Z"
  }
}
```

例外规则同样要求独立审批。它只会降低该精确范围的排序，不会隐藏问题，也不会压制其他订单接口、其他方法或同类风险。

## 与现有引擎的关系

- 风险计划器会读取已批准学习模式，将匹配探针标记为 `confirmed_bug_flywheel_bonus`。
- 主缺陷发现流程会携带当前数据飞轮摘要。
- 回归套件构建器会读取已批准的 `confirmed_bug_regression_candidates.json`。
- Phase46 的确认缺陷记忆仍可读取脱敏投影；Phase55 增加了审批、审计和回归固化治理。
