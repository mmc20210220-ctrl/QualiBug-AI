# Phase65：财务账务双分录与账期滚动守恒 Oracle

## 目标

Phase65 不新建财务运行时、账务模型、数据仓库或界面。它扩展已有的
`business_causality_conservation` 引擎，把两类企业最昂贵、却常被单接口
测试遗漏的账务事实变成可执行的只读 Oracle：

1. **凭证级双分录平衡**：同一凭证、同一币种下，借方金额之和必须等于贷方金额之和。
2. **跨账期余额连续性**：已声明的相邻账期内，同一科目、币种的期末余额必须等于下一账期期初余额；需要时还验证期末余额是否正确滚动自期初与本期借贷发生额。

它针对的是“接口都 200、交易也成功，但总账已失真”的真实业务事故，而不是把
`debit` / `credit` 字段名猜成财务事实。

## 可发现的高价值缺陷

- 一张凭证缺少贷方或借方分录，导致借贷不平。
- 同一凭证因重试、消息重复或异步落库只生成了半边分录。
- 科目期末余额未正确带入下一账期，导致跨月、关账、审计和报表失真。
- 期初余额、本期借方、本期贷方和期末余额之间的显式滚动公式被破坏。

## 显式配置优先

账务口径、科目方向、借贷方向和账期语义均由企业决定，因此 Phase65 **只执行显式配置**
的财务契约。即使 OpenAPI 中存在 `debit` / `credit` 字段，也不会自动把它视为可发布的财务结论。

```json
{
  "business_causality_execution_mode": "safe_live",
  "business_causality_conservation": {
    "contracts": [
      {
        "type": "journal_balance",
        "source_path": "/accounting/journal-lines",
        "journal_group_fields": ["voucher_id"],
        "debit_field": "debit_amount",
        "credit_field": "credit_amount",
        "currency_field": "currency",
        "tolerance": 0.005
      },
      {
        "type": "period_rollforward",
        "source_path": "/accounting/trial-balances",
        "account_field": "account_code",
        "currency_field": "currency",
        "period_field": "accounting_period",
        "opening_balance_field": "opening_balance",
        "closing_balance_field": "closing_balance",
        "debit_field": "debit_amount",
        "credit_field": "credit_amount",
        "period_sequence": ["2026-05", "2026-06"],
        "movement_sign": "debit_minus_credit",
        "verify_movement_formula": true,
        "tolerance": 0.005
      }
    ]
  }
}
```

`period_sequence` 是强制的显式业务边界：系统不会自行猜测哪两个期间应连续，避免
把季度、年末结转或不同账套误判为缺陷。`movement_sign` 默认
`debit_minus_credit`；如企业科目口径为相反方向，可显式设置为 `credit_minus_debit`。

## 误报与安全边界

- 仅接受 OpenAPI 已声明的集合型 `GET` 路径；不访问详情写接口、不生成凭证、不执行关账或冲销。
- 只有完整分页快照时才断言双分录与账期关系；响应不完整时记录跳过原因而不是生成缺陷。
- 双分录按“凭证标识 + 币种”聚合，防止多币种凭证被错误相抵。
- 账期连续性只验证配置中的相邻 `period_sequence`，并跳过缺失期间或重复的科目-期间观察，避免用不完整事实制造结论。
- 证据只保存字段名、聚合金额、容差、期间标签和经过哈希的凭证/科目身份；不保存原始凭证号、科目号、业务行、令牌或请求头。
- `safe_live` 仅能发送 GET，且必须先通过共享 `execution_safety_verdict`；生产环境、未声明环境或可疑目标在任何网络请求前阻断。
- LLM 只可产生 `unverified_hypothesis`，不能直接进入 `findings` 或缺陷队列。

## 主链路接入

Phase65 复用 `business_causality_conservation` 的现有契约画像、风险计划、执行记录、
证据指纹、确认学习和真实项目发现链。双分录与账期反例以 P0
`financial_double_entry_balance` 或 `financial_period_rollforward` 风险进入既有发布风险候选，
但仍必须经人工确认后才可升级为企业确认缺陷。
