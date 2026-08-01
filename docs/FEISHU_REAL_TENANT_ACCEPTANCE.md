# 飞书真实租户验收

本工具用于判断已配置的飞书连接器是否达到试点准入标准。它复用现有受控同步链路，固定采用只读访问和内部 `RETAIN` 生命周期策略，不建立第二套同步实现。

## 执行

```bash
python -m ai_test_asset_center.feishu_tenant_acceptance \
  --project PROJECT_ID \
  --connector CONNECTOR_ID \
  --profile pilot
```

可选 Profile：

| Profile | 连续同步 | 最少资料 | 最低覆盖率 | 最大不支持比例 | 单次最长耗时 |
|---|---:|---:|---:|---:|---:|
| smoke | 2 | 1 | 80% | 20% | 300 秒 |
| pilot | 2 | 20 | 95% | 5% | 900 秒 |
| enterprise | 3 | 200 | 98% | 2% | 1800 秒 |

这些数值是 QualiBug 内部准入门槛，不是外部平台的性能承诺。命令允许覆盖门槛，实际值会写入报告。

## 阻断条件

每次同步必须同时满足：

- 连接可用并证明为只读；
- 远端枚举完整；
- 支持类型全部完成处理；
- 未知资料缺口为零；
- 同步失败数为零；
- checkpoint 完成提交；
- 客户资料修改标记为 false；
- 收据不保存客户正文；
- 覆盖率、不支持比例和耗时达到 Profile。

当连续两次远端快照指纹相同，第二次必须避免重新导出未变化资料。如果验收期间远端资料确实变化，报告会记录该变化，不会误判为增量同步失败。

## 报告

报告写入：

```text
platform_workspace/PROJECT_ID/enterprise_knowledge_center/
connector_acceptance_reports/CONNECTOR_ID/TIMESTAMP_ID.json
```

报告仅保存计数、耗时、状态、哈希和同步收据相对路径，不保存客户正文、下载字节、连接凭据或原始同步游标。

只有以下结果可以作为连接器技术准入凭证：

```json
{
  "verdict": "PASS",
  "acceptance_ready": true,
  "summary": {
    "blocker_failure_count": 0
  }
}
```

该结论只覆盖在线资料接入，不代表业务理解、测试生成或真实缺陷发现能力已经通过端到端验收。
