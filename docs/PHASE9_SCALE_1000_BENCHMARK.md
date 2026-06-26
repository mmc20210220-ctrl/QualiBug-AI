# Phase9：Scaling to 1000 Bug Benchmark

Phase9 的目标不是简单堆 Bug 数量，而是把缺陷基准从 200 扩展到 1000，同时控制：

- template coverage
- domain distribution
- severity distribution
- bug instance 去重
- variant signature 去重
- probe ROI
- scaling readiness

新增命令：

```cmd
RUN_GENERATE_BUG_SET_1000.cmd
RUN_BUG_FACTORY_1000.cmd
RUN_SCALE_1000_REPORT.cmd
RUN_PHASE9_VERIFY.cmd
```

推荐流程：

```cmd
RUN_GENERATE_BUG_SET_1000.cmd
RUN_BUG_FACTORY_1000.cmd
RUN_ADAPTIVE_OPTIMIZER.cmd
RUN_DEFECT_DISCOVERY_BLIND.cmd
RUN_BENCHMARK_EVALUATION.cmd
RUN_SCALE_1000_REPORT.cmd
```

输出：

```text
enterprise_bug_factory/bug_sets/bug_set_1000.json
enterprise_bug_factory/private_ground_truth/ground_truth_bugs.json
benchmark_outputs/scale_1000/scale_1000_scorecard.json
benchmark_outputs/scale_1000/scale_1000_report.html
```

注意：AI Test Asset Center 仍然不能读取 bug_sets、private_ground_truth、current_bug_set。正式指标仍然以 blind mode 为准。
