# V5 失败证据包 + AI 归因增强

V5 目标不是只分析一段失败日志，而是把自动化失败时的多源证据合并成 Evidence Bundle：

- pytest_output.txt：测试失败输出
- api_response.json：实际接口响应
- trace_summary.json：执行链路摘要
- test_case.json：失败用例上下文
- related_diff.diff：相关代码变更
- ci_context.json：CI 环境与分支上下文
- failing_test_source.py：失败脚本片段

然后输出：

- evidence_bundle.json：标准化证据包
- triage_result.json：结构化归因结果
- failure_triage_report.md：失败归因报告
- bug_draft.md：可复制到 Jira/禅道的缺陷草稿
- regression_recommendations.json：防回归建议

企业价值：减少 QA 手动看日志、截图、接口响应、代码 diff 后再整理缺陷的时间。
