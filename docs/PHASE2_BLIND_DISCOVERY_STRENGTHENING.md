# Phase2 Blind Discovery Strengthening

本阶段目标不是扩大 Bug 数量，而是提升真实盲测模式下的缺陷发现能力，同时保持 clean baseline 低误报。

## 本次优化

1. 新增 High Value Pattern Library 探针
   - 只基于公开 PRD、OpenAPI、账号语义生成。
   - 不读取 bug_set、enabled_bugs、ground_truth。
   - 覆盖权限、未授权、锁定账号、IDOR、租户隔离、库存、优惠券、支付、支付回调、退款、幂等、状态一致性。

2. 强化 Pattern Library 执行器
   - 从单接口状态码判断升级为业务链路验证。
   - 支持前置状态、后置状态、重复提交、金额比对、库存前后对比、订单创建后查询、取消后状态复核、退款后库存复核。

3. 修正 Bug Factory clean 行为
   - clean mode 下支付回调不能把已取消订单改成已支付。
   - 避免干净系统误报。

4. 修正 Ground Truth API 映射
   - AUTH_LOCKED_LOGIN 等模板改成匹配真实触发 API。
   - 提升训练样本和评测数据质量。

5. 调整发现结果去重策略
   - Pattern Library 的不同业务 oracle 可以在同一 API 上分别报告。
   - Generic probes 不再覆盖 Pattern Library 已经发现的同类问题。
   - 降低重复泛化报告造成的误报。

6. 保持 anti-cheat 原则
   - AI 测试平台仍只能读取 public_artifacts。
   - 不读取 private_ground_truth、bug_sets、enabled_bugs、current_bug_set。

## 我方验证结果

### clean mode

```text
known_bugs: 0
discovered_bugs: 0
false_positive_rate: 0
clean_mode_false_positive_rate: 0
```

### bug_set_50 blind mode

```text
known_bugs: 50
discovered_bugs: 26
matched_true_positives: 26
exact_matches: 23
partial_matches: 3
false_positives: 0
recall: 0.52
precision: 1.0
p0_p1_recall: 0.5306
```

Phase1 blind recall 约 0.28，本阶段提升到约 0.52，同时 clean mode 仍然 0 误报。

## 下一阶段建议

不要立即扩到百万级。下一阶段建议做：

1. Template-level metrics：区分“缺陷模式发现率”和“缺陷实例发现率”。
2. 让 bug_set_50 里的重复模板变成真正不同数据变体，而不是相同模板重复。
3. 增加 Pattern Library 命中率统计和自动权重调整。
4. 扩展到 bug_set_200，再评估 clean / blind / random hidden 三种模式。
