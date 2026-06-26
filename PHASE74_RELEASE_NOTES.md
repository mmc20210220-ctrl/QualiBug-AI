# Phase74 Release Notes — Autonomous Business Bug Discovery Agent Loop

## 核心变化

Phase74 将 QualiBug 的核心能力从“多个独立检测器”收敛为一个持久化 Agent Loop 控制面。

新增 `ai_test_asset_center/agent_discovery_loop.py`：

- 项目级 SQLite 单一权威账本；
- 可导出但不可写回的 CSV 电子表格投影；
- 将业务世界模型候选、已确认只读 Oracle、Markdown API/PRD 文档契约、并发 Sandbox 实验和发现候选同步到同一状态机；
- 用风险、未知度、证据强度和安全可执行性选择 `next_best_actions`；
- 运行时证据必须由人类裁决后才确认根因；
- 确认根因自动生成回归守卫；
- 事件哈希链用于篡改检测。

## 产品边界

- 不跟踪“已知 Bug 总数”。任何 benchmark 根因数只用于离线评估，不进入运行时计划。
- 文档、静态源码与 LLM 都只能生成假设或实验，不是 Bug。
- Loop 不直接放开写请求；所有 Sandbox 执行仍由既有执行器和安全边界控制。
- 不新增前端框架或平行业务规则存储。

## CLI

```bash
python -m aitestops.cli agent-loop --project <project_id> --root . --max-actions 12
```

命令输出权威账本位置、当前状态汇总和下一批高信息增益动作。
