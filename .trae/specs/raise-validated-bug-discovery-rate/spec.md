# 提升真实 Bug 发现率 Spec

## Why
当前 bug 引擎在部分链路上存在“候选多、真实可确认 Bug 少、发现率低”的问题，且不同阶段的漏损点不够透明。用户明确要求只有具备严格证据、可稳定复现、可解释归因的发现才算真实 Bug，因此需要把引擎优化目标从“放大候选量”收敛为“提升已验证真实 Bug 产出率”。

## What Changes
- 建立统一的真实 Bug 计数口径，仅把具备严格验证结论、可复现步骤和完整证据包的发现计入正式结果。
- 为候选生成、Probe 选择、运行执行、Verifier 判定、去重归并五个环节增加漏斗指标与阻断原因输出。
- 将引擎优化目标从“候选规模优先”调整为“已验证真实 Bug 产出优先”，显式衡量 discovery rate、repro success rate 和 evidence completeness。
- 增加低产出项目/路径/风险族的诊断能力，输出“为什么没有发现真实 Bug”而不是只输出 0 结果。
- 为 benchmark 回归补充真实 Bug 口径报表，区分候选信号、待确认发现、已验证真实 Bug。

## Impact
- Affected specs: blind input-only 发现链、runtime probe 选择链、runtime verifier/evidence 链、benchmark 报告口径
- Affected code: `input_grounded_candidate_compiler.py`、`runtime_probe_selection.py`、`grounded_probe_executor.py`、发现结果汇总与 benchmark 报表相关模块、对应 tests

## ADDED Requirements
### Requirement: 真实 Bug 严格计数
系统 SHALL 仅将同时满足“严格验证通过、存在可执行复现步骤、存在完整证据引用”的发现计入正式真实 Bug 总数。

#### Scenario: 发现满足正式计数门槛
- **WHEN** 一条发现已经通过 verifier 严格判定
- **AND** 发现包含可复现的请求/响应或操作轨迹
- **AND** 发现绑定到可追溯的 evidence refs / reproduction pack
- **THEN** 系统将其标记为已验证真实 Bug
- **AND** 该发现计入正式 bug count、family count 和 discovery rate 分母/分子统计

#### Scenario: 发现缺少关键证据
- **WHEN** 一条发现只有启发式信号、模糊响应或不可复现现象
- **THEN** 系统不得将其计入正式真实 Bug 总数
- **AND** 系统将其归类为候选信号或待确认发现
- **AND** 结果中必须明确缺失的是 verifier、repro 还是 evidence

### Requirement: 阶段漏斗可观测
系统 SHALL 对候选生成、Probe 选入、实际执行、验证通过、正式记账五个阶段输出可追踪漏斗指标与主要漏损原因。

#### Scenario: 项目发现率偏低
- **WHEN** 某项目最终真实 Bug 数偏低或 discovery rate 异常偏低
- **THEN** 系统输出每个阶段的输入量、产出量、转化率
- **AND** 输出 Top 阻断原因，例如 grounding 不足、路径未命中、环境失败、证据不完整、verifier 拒绝、重复折叠过强

### Requirement: Probe 选择以真实产出优先
系统 SHALL 在预算有限时优先选择更可能产出“已验证真实 Bug”的 Probe，而不是单纯放大候选覆盖。

#### Scenario: 预算受限时的选择
- **WHEN** 系统需要在有限 probe budget 下选择执行集
- **THEN** 选择逻辑优先考虑历史 validated yield、业务关键路径、风险族覆盖、复现成功率与证据可得性
- **AND** 不得因为追求表面候选量而系统性压缩高质量 Probe 的执行预算

### Requirement: 低产出结果必须可解释
系统 SHALL 对 0 发现或低发现结果输出可操作的解释，而不是仅返回空结果。

#### Scenario: 某条链路没有发现真实 Bug
- **WHEN** 项目、路径或风险族最终未产出真实 Bug
- **THEN** 系统必须说明是“没有生成候选”“候选未入选”“执行失败”“验证不通过”还是“证据不足”
- **AND** 输出下一步可优化方向，例如补知识资产、放宽路径对齐、强化 verifier 规则、补充复现采样

## MODIFIED Requirements
### Requirement: 发现结果分层展示
系统 SHALL 将发现结果严格分为候选信号、待确认发现、已验证真实 Bug 三层，并在所有汇总、报表和对外结论中默认使用“已验证真实 Bug”作为正式口径。

#### Scenario: 生成 benchmark 汇总
- **WHEN** 系统输出 benchmark 或项目级总结
- **THEN** 报表分别展示 candidate count、pending finding count、validated bug count
- **AND** discovery rate 默认基于 validated bug count 计算
- **AND** 对外展示不得把 candidate 或 pending finding 夸大为真实 Bug
