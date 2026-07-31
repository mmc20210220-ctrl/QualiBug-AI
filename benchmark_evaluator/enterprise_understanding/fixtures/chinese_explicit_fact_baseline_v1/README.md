# 中文显式业务事实基线 v1

该基线用于测量 QualiBug 现有企业理解主链对中文显式业务事实的真实提取能力，不是产品内置答案，也不是模型自标注数据。

## 冻结内容

- `source_rules.md`：公开给产品阶段的中文企业规则语料。
- `ground_truth.json`：只在产品子进程退出、来源身份校验完成后由 Evaluator 加载的人工来源化槽位标注。

Ground Truth 已封闭为 **16 条显式事实、10 个正式 Document Structure IR 业务块**，覆盖权限、AND 条件、状态变化、多副作用原子化、禁止与例外、时间窗口、对象关系、基数、公式、所有权、限定对象权限、IF/ELSE 分支和精确证据地址。`scope_complete=false` 表示该语料只证明显式事实子域，不代表完整企业理解范围；`explicit_fact_scope_complete=true` 表示该冻结语料中的显式事实宇宙已经人工封闭。

证据地址不使用测试手写的段落编号。冻结语料通过产品正式 `GenericTextDocumentAdapter` 生成 Document Structure IR，人工真值使用跨工作区稳定的：

```text
source_rules.md#line=N;chars=start-end
```

`explicit_fact_scope_locators` 明确列出全部10个受审计业务块。事实缺失、候选歧义和重复候选都保留在证据与Precision分母中，不能通过只统计成功匹配的事实、缩小范围或自动挑选候选美化结果。

## 运行

```bash
python -m benchmark_evaluator.enterprise_understanding.chinese_explicit_fact_baseline \
  --workspace-root .qualibug/benchmark-workspace/chinese-explicit-fact-v1 \
  --output .qualibug/benchmark-results/chinese-explicit-fact-v1
```

产品阶段只接收 `source_rules.md` 和运行时生成的来源清单。`ground_truth.json` 不进入产品命令、环境变量或工作区；产品子进程运行期间原 Ground Truth 路径不存在，原始字节只保存在 Evaluator 父进程内存中。

## 输出

- `chinese_explicit_fact_baseline_summary.json`
- `evaluation/business_fact_slot_measurement.json`
- `evaluation/business_fact_slot_alignments.json`
- `evaluation/business_fact_false_accepted.json`
- `evaluation/explicit_fact_first_loss_analysis.json`
- 原 source-backed workflow 的产品资产、来源身份回执及 Evaluator 报告

## 质量门槛

完整来源接入基线必须同时达到：

- 显式事实召回率 `>= 95%`
- 槽位精确准确率 `>= 92%`
- P0 显式事实精确召回率 `>= 95%`
- 精确证据地址准确率 `>= 98%`
- 完整范围内 ACCEPTED 事实 Precision `>= 98%`，即误接受率 `<= 2%`

误接受事实包括：完整 locator 范围内未被任一唯一 Ground Truth 对齐选中的 ACCEPTED 事实、重复候选和歧义候选。Evaluator 不允许自动选择“最像”的候选。

“完成测量”不等于“质量通过”。命令退出码是正式 CI 权威：

- `0`：产品阶段完成、Evaluator 完成，并且全部质量门槛通过；
- `3`：完成了真实测量，但至少一个质量指标低于门槛；
- `2`：产品接入、来源隔离或测量阶段被阻断，未形成有效质量结论。

GitHub Actions 使用固定 Commit Status context：

```text
qualibug/chinese-explicit-fact-baseline
```

状态描述直接显示 `recall`、`slots`、`p0`、`evidence`、`precision` 和 `false`。即使退出码为 `2` 或 `3`，工作流也必须打印摘要并上传已有工件，以便从 `highest_impact_first_loss` 定位首次丢失阶段。误接受事实进入 `FACT_DISCOVERY_FALSE_ACCEPTANCE` 首失阶段，不会被普通召回率掩盖。

修复始终从 `highest_impact_first_loss` 指向的现有主链模块开始，禁止在下游伪造结果、缩小证据或Precision分母、把 `MEASURED` 解释为成功，或新增第二套事实/评测权威。
