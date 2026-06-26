# Phase64：角色权限与字段级访问边界 Oracle

## 目标

Phase64 不新增运行时、用户界面或并行权限框架。它复用已有的
`consistency_isolation_reasoning`、共享安全边界、上下文凭证脱敏和证据指纹机制，
将“权限声明”变成可执行的只读业务 Oracle。

传统接口测试经常只验证管理员是否能打开页面，却遗漏两个最容易造成企业事故的
事实：不具备权限的角色是否仍然能读取受限数据，以及具备部分查看权限的角色是否
被错误返回了成本、毛利、薪资、银行账户或其他应隐藏字段。

## 可发现的高价值缺陷

1. **路由级越权读取**：明确应被拒绝的测试角色对受限 GET 入口收到 2xx/3xx
   成功响应。
2. **空集合型权限泄露**：企业明确约定“可访问入口但应无可见数据”的角色收到
   非空业务集合。
3. **字段级越权暴露**：合法可读视图包含该角色被明确禁止的非空字段。
4. **授权角色被错误拒绝**：已声明具备读取权限的受控测试角色收到 401、403 或
   404，导致关键岗位无法完成业务工作。

所有检测均建立在企业显式配置的角色上下文、路径与预期之上；系统不会猜测敏感
路由、扫描权限面或尝试权限提升。

## 配置

在 `consistency_isolation_reasoning.access_contracts` 中声明 OpenAPI 已存在的 GET
路径与受控角色上下文。每个上下文必须独立提供 `token_env`、`token` 或 `headers`；
不能静默复用项目级 token。

```json
{
  "consistency_isolation_execution_mode": "safe_live",
  "consistency_isolation_reasoning": {
    "access_contracts": [
      {
        "path": "/finance/reports",
        "sample_query": {"period": "2026-06"},
        "contexts": [
          {
            "name": "finance_admin",
            "expected_access": "allow",
            "token_env": "QUALIBUG_FINANCE_ADMIN_TOKEN"
          },
          {
            "name": "sales_viewer",
            "expected_access": "deny",
            "denied_statuses": [403, 404],
            "token_env": "QUALIBUG_SALES_VIEWER_TOKEN"
          },
          {
            "name": "support_viewer",
            "expected_access": "allow",
            "forbidden_fields": ["gross_margin", "bank_account"],
            "token_env": "QUALIBUG_SUPPORT_VIEWER_TOKEN"
          },
          {
            "name": "limited_viewer",
            "expected_access": "empty",
            "headers": {"X-Test-Role": "limited_viewer"}
          }
        ]
      }
    ]
  }
}
```

`allow` 表示期望明确成功状态（默认 `200`）；`deny` 表示期望
`401/403/404`（可配置）；`empty` 表示入口可达但返回的业务集合必须为空。

## 误报与安全边界

- 只接受显式配置且已映射到 OpenAPI 的 `GET` 入口。
- 只使用显式、隔离的角色上下文。每个上下文的 token/header 仅在内存中使用，
  写入 profile、报告和证据前只保留“已配置”与 header 名称。
- 角色上下文默认**不会**继承项目级 token；只有配置
  `inherit_default_token: true` 才会继承，适用于明确的共享 bearer + role-switch
  header 场景。
- 凭证环境变量未解析、headers 为空时，该上下文会被标记为未执行，而不是把
  401 当成产品权限缺陷。
- `safe_live` 前必须通过共享 `execution_safety_verdict`。生产或未声明环境会在
  **任何 HTTP 请求前**阻断；写入、角色切换、审批和并发验证仍保持
  `sandbox_required`。
- 字段级检查只持久化字段路径与计数，不保存字段值、原始行、token 或 header。
- LLM 只能提出 `unverified_hypothesis`，不能直接进入缺陷队列。

## 主链路接入

Phase64 复用现有风险规划和真实项目发现链。发现会带有
`role_access_control` 或 `field_level_access_control` 风险类型，并以 P0/P2
优先级进入发布风险候选；最终仍需人工确认。
