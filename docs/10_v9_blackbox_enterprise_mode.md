# V9 黑盒企业集成模式

## 为什么要做 V9

企业真实场景里，AI Test Asset Center 不应该把失败日志、测试输出、缺陷草稿写进被测业务系统目录。

正确边界是：

```text
被测业务系统 / SUT
  只负责提供服务、OpenAPI、环境地址、CI 构建包、运行日志或接口响应

AI Test Asset Center / 测试平台
  负责生成测试资产、执行测试、保存失败日志、证据包、归因报告、缺陷草稿、回归计划
```

这意味着测试平台应该把所有输出保存到自己的 workspace：

```text
ai_test_asset_center/
├── workspaces/
│   └── enterprise_shop/
│       ├── inputs/          # 从外部系统导入的 PRD/OpenAPI/Diff/失败日志
│       ├── evidence/        # 测试失败证据包
│       ├── generated/       # 生成的测试资产
│       └── reports/         # AI 归因、回归计划、管理报告
```

## 企业集成来源

测试平台一般通过这些方式拿输入，不直接侵入业务系统代码目录：

| 来源 | 企业方式 | 测试平台保存位置 |
|---|---|---|
| PRD / 用户故事 | Jira / Confluence / 飞书文档导出 | workspace inputs |
| OpenAPI | `/openapi.json` URL、网关文档、CI artifact | workspace inputs |
| Git Diff | GitLab/GitHub Merge Request API | workspace inputs |
| 失败日志 | Jenkins/GitLab CI artifacts、测试 runner stdout | workspace evidence |
| 接口响应 | 测试 runner 捕获 | workspace evidence |
| Trace/截图/视频 | Playwright/Test runner artifact | workspace evidence |
| 缺陷草稿 | Jira/禅道/飞书 webhook | 测试平台生成后推送 |

## V9 的演示方式

为了本地演示，`enterprise_shop_demo` 仍然可以作为被测系统。但 AI Test Asset Center 不再把结果写进电商目录，而是执行导入：

```text
enterprise_shop_demo/ai_test_inputs
        ↓ import only
ai_test_asset_center/workspaces/enterprise_shop/inputs
        ↓ generate/analyze
ai_test_asset_center/outputs/blackbox_enterprise_shop
```

这样更接近企业真实落地：被测系统和测试平台解耦。

## 面试表达

> 我在 V9 中把方案从 Demo 目录耦合改造成黑盒企业集成模式。测试平台不要求接触被测业务系统源码，也不会把失败日志写进业务系统目录。它通过 OpenAPI、CI Artifact、Git Diff、失败日志和接口响应作为输入，在自己的 workspace 中生成测试资产、证据包、失败归因、缺陷草稿和精准回归计划。这更符合企业权限边界和数据治理要求。
