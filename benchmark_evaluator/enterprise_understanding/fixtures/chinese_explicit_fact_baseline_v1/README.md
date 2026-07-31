# 中文显式业务事实基线 v1

该基线用于测量 QualiBug 现有企业理解主链对中文显式业务事实的真实提取能力，不是产品内置答案，也不是模型自标注数据。

## 冻结内容

- `source_rules.md`：公开给产品阶段的中文企业规则语料。
- `ground_truth.json`：只在产品子进程退出、来源身份校验完成后由 Evaluator 加载的人工来源化槽位标注。

Ground Truth 覆盖权限、AND 条件、状态变化、多副作用原子化、禁止与例外、时间窗口、对象关系、基数和公式。`scope_complete=false` 表示该语料只证明显式事实子域，不代表完整企业理解范围。

## 运行

```bash
python -m benchmark_evaluator.enterprise_understanding.chinese_explicit_fact_baseline \
  --workspace-root .qualibug/benchmark-workspace/chinese-explicit-fact-v1 \
  --output .qualibug/benchmark-results/chinese-explicit-fact-v1
```

产品阶段只接收 `source_rules.md` 和运行时生成的来源清单。`ground_truth.json` 不进入产品命令、环境变量或工作区。

## 输出

- `chinese_explicit_fact_baseline_summary.json`
- `evaluation/business_fact_slot_measurement.json`
- `evaluation/explicit_fact_first_loss_analysis.json`
- 原 source-backed workflow 的产品资产、来源身份回执及 Evaluator 报告

初始基线只要求可重复测量，不用低分阻断 CI。达到以下目标后才允许将其升级为发布门禁：

- 显式事实召回率 `>= 95%`
- 槽位精确准确率 `>= 92%`
- P0 显式事实精确召回率 `>= 95%`

修复始终从 `highest_impact_first_loss` 指向的现有主链模块开始，禁止在下游伪造结果或新增第二套事实/评测权威。
