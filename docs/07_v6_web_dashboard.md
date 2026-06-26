# V6 Web Dashboard

V6 把前面 V2-V5 的命令行能力包装成一个本地 Web 演示平台，方便面试、售前、内部分享时展示完整闭环。

## 包含能力

- V2：需求文档 -> 测试资产 -> Pytest
- V3：OpenAPI/Swagger -> 接口测试资产 -> Pytest
- V4：Git Diff -> 影响面分析 -> 精准回归
- V5：失败证据包 -> AI/规则归因 -> 缺陷草稿

## 启动方式

```cmd
RUN_WEB.cmd
```

默认打开：

```text
http://127.0.0.1:7860
```

## 设计价值

V6 的重点不是做一个复杂平台，而是把 AI TestOps 的企业落地逻辑可视化：

1. 输入来源不是只有测试人员手写用例，而是需求、接口契约、Git Diff、失败证据。
2. AI/规则引擎生成测试资产，但经过 Schema Guard、Semantic Guard、Execution Guard、State Guard。
3. 输出不是自然语言，而是可执行测试、回归计划、证据包、缺陷草稿和治理记录。

面试表达：

> 我把 AI TestOps PoC 做成了可视化演示平台，可以在页面上运行需求生成测试资产、OpenAPI 生成接口测试、Git Diff 精准回归和失败证据归因四个闭环。这样不是单纯展示脚本，而是展示 AI 如何进入企业测试生命周期，降低测试设计、回归选择和失败排查成本。
