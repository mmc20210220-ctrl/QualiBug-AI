# 证据链验证体系规范（Evidence-Chain Verification Spec）

> 状态：生效中（Living Document，随实现演进）
> 上位文档：`DISCOVERY_HARNESS_EVOLUTION_GOAL.md`（Gate 阈值唯一 SSOT，本文不得重定义阈值）
> 关联：根 AGENTS.md 原则13、`AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`

## 0. 核心命题

**能被稳定复现的行为分歧就是 Bug——真值来自证据链的完整性，不来自任何标注者的权威。**

由此推出本体系的总纲：

1. **精度的终极验证是重放，不是对照。** 一条 finding 若附带怀疑者可在自己环境上一键重放的证据包，其真伪不再依赖"信谁"，而依赖"看见什么"。
2. **召回的分母问题用统计与注入解决，不用虚构解决。** 完整 GT 在真实系统上不可得；用真实历史缺陷的精确分母（注入）与双检测器总量估计（标记重捕）替代。
3. **防过拟合必须是结构性而非纪律性。** 静态题库终将被过拟合；评测项自动退役 + 真实 CVE 流水线补充 + 对抗生成器常设化。

## 1. 分层验证仪器栈

| 层 | 仪器 | 回答的问题 | 关键护栏 |
|---|---|---|---|
| L1 | **自证证据包**（P0，见 §2） | "凭什么信这条 bug" | 盲重放：全新进程/乱序/清缓存；判定零内部状态引用 |
| L2 | **版本对差分** | 真实历史漏洞的权威确认 | pin 确切修复 commit；finding↔issue 匹配协议**先注册后跑** |
| L3 | **真实缺陷种子注入**（Magma 范式） | 精确召回率（找到种子/种子总数） | 种子从真实 CVE/issue 语料按风险家族分层抽样；家族轮换防过拟合；登记表 HMAC 密封 evaluator-private |
| L4 | **双异构检测器 + 标记重捕** | 可发现缺陷总量的统计估计 | 用 DAST/SAST/人工三类异构检测器分别估计并报置信区间 |
| L5 | **无 GT 结构下界** | 机械盲复现率；干净目标误报下限 | 复现率分母不含 INDETERMINATE（单列诚实暴露） |
| L6 | **设计伙伴盲测** | "比人工 QA 强多少"（每工程师小时新增确认缺陷） | 客户工程师不知情裁定；同期对照 |
| L7 | **金丝雀轮换 + 对抗生成 + 外部托管真值** | 半年后还作数吗 | 评测项见过 N 次自动退役；新项自真实 CVE feed；GT 托管第三方 |

L2-L4 的落地目标集：≥3 个不同行业真实开源企业系统（ERPNext/Odoo、Saleor/Medusa、GitLab CE/Mattermost 为候选短名单），freeze commit、公开可审计——任何人可重跑评估。

## 2. P0：自证证据包（Self-Proving Evidence Bundle）

### 2.1 现状盘点（2026-08-22 实测）

已存在：
- `customer_delivery_gate_v2.validate_reproduction_receipt`：严格 schema 校验（campaign/obligation/experiment/execution/evidence 五元身份绑定 + receipt_fingerprint + step_observations + source_refs）
- `artifact_store.EVIDENCE_BUNDLE_MANIFEST`：证据工件已入内容寻址库
- 交付门禁已强制不可变 Gate-v2 证据 + attempt ledger

缺口（本 P0 要补的三件事）：
1. **目标环境描述符**（`target_descriptor`）：如何在等价的新鲜副本上到达目标——声明的 base_url/service 列表/environment_type（非生产 fail-closed 写边界照旧适用）；绝不内嵌凭据明文（走既有 secret 引用通道）
2. **重放编译器**：`step_observations` + 断言 DSL → 独立可执行重放脚本；不 import 产品运行时、不读工作空间
3. **外部 CLI**：`tools/discovery_evaluation.py verify <bundle>` → 在指定目标上执行并输出三态判定：`VIOLATION_REPRODUCED` / `NOT_REPRODUCED` / `INDETERMINATE`

### 2.2 验收标准（全部满足才算闭环）

- [ ] Bundle 自包含：目标描述符 + 请求序列 + 断言 + HMAC 封印；经 `artifact_redactor` 合规检查
- [ ] 重放在全新进程、步骤乱序扰动、清缓存条件下进行
- [ ] 判定只依据观测到的行为（status/body/state 差异），零内部回执引用
- [ ] `verify` 对同一 bundle 在未修复目标上稳定输出 `VIOLATION_REPRODUCED`，在官方修复后版本上输出 `NOT_REPRODUCED`（L2 版本对差分即免费获得）
- [ ] 复现率口径 = `VIOLATION_REPRODUCED / (VIOLATION_REPRODUCED + NOT_REPRODUCED)`，直接对接 Gate D 复现率阈值（≥0.9；试点 ≥0.95）
- [ ] 产品运行时零改动：全部落在评估侧（`tools/` + `_private_eval/`），GT 隔离合同不受影响

### 2.3 明确不做

- 不发明新的"真值委员会"；不把 INDETERMINATE 折算进分母；不为提升复现率放宽断言（断言冻结于交付时刻，重放器只执行不解释）

## 3. 实施顺序

| 阶段 | 内容 | 出口判据 |
|---|---|---|
| Phase 0 | 131 held-in 基准正式评估出报告，消除 `evaluation_report_missing` | `goal-status` Gate D 有报告输入（仍 NOT_MEASURED→MEASURED 于 held-in 口径） |
| Phase 1 | P0 三件套（§2.1 缺口）在 1 个 OSS 目标上端到端跑通 | 验收标准全绿 |
| Phase 2 | L2 版本对差分扩展至 ≥3 行业；L3 注入农场上线 | 家族级检测功率矩阵 v1 |
| Phase 3 | L4 总量估计 + L6 设计伙伴盲测；L7 金丝雀机制叠加 | Gate D 全部阈值输入齐备 |

## 4. 修订记录

- 2026-08-22：初版。确立"可复现即真值"为验证第一性原理；P0 定为自证证据包。
- 2026-08-22：**P0 三件套已落地**——`ai_test_asset_center/self_proving_evidence_bundle.py`
  （bundle 编译器 + stdlib 重放执行器 + 三态判定）+ `tools/discovery_evaluation.py`
  新增 `bundle-build` / `verify` 子命令（exit code：REPRODUCED=0 / NOT_REPRODUCED=1 /
  INDETERMINATE=2 / REFUSED=3）。密码学血缘绑定：入包请求体与请求语义指纹逐字节
  复算并比对封存回执，不匹配即 `bundle_request_bytes_lineage_invalid` 拒绝。
  双臂相位判定：control 基线失真→INDETERMINATE；treatment 偏离封存违规→NOT_REPRODUCED。
  验收测试 `tests/test_self_proving_evidence_bundle.py` 13/13 全过
  （含全新进程 CLI 重放、防篡改、HMAC、生产环境 fail-closed、敏感头拒入包、乱序扰动）。
  v1 判定形状基准 = HTTP status-class；同状态码不同响应体的缺陷类不可判别（诚实边界，
  body 断言通道留待后续增量）。附带发现：门禁 `validate_reproduction_receipt` 对步骤
  字段集做精确 http 形状匹配，非 http 步骤回执在消费侧无法通过该校验（第五链路泛化的
  潜在不一致，待独立任务处理）；builder 的非 http 拒绝分支作为纵深防御保留。
