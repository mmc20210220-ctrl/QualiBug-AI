# 强化真实 Bug 与证据完整性 Spec

## Why
当前产品的核心目标不是“展示更多线索”，而是“稳定挖掘真实 Bug，并交付真实证据与可复现资产”。现状中仍存在几类会直接伤害产品可信度的问题：无关 HAR 被误绑成运行时证据、复现命令或步骤被推断生成、前后端对 customer-ready 缺陷的准入条件不完全一致、客户页仍可能混入待验证线索或弱证据结果。这些问题会让系统把假证据包装成真 Bug，最终导致客户复跑失败、误判缺陷、信任受损。

## What Changes
- 建立“真实 Bug / 待验证线索 / 内部建议资产”三层统一口径，并以后端交付状态作为单一真相源。
- 收紧运行时证据绑定规则，禁止在缺少有效绑定信号时把 HAR、日志、DB 片段附会到无关 finding。
- 禁止生成或下发会被误认为真实复现资产的合成步骤、合成命令、默认方法、默认接口和默认账号信息。
- 统一后端 verifier、formatter、command-center 和前端客户页准入规则，确保只有真实证据闭环的缺陷才能进入客户视图。
- 为前后端增加契约约束与回归检查，避免再次出现“总览、侧栏、行为验证、证据链”数据打架。

## Impact
- Affected specs: `raise-validated-bug-discovery-rate`、`enable-continuous-validated-discovery`
- Affected backend code:
  - `har_bridge.py`
  - `evidence_enricher_v3.py`
  - `display_ready_formatter.py`
  - `private_pilot_service.py`
  - `real_project_defect_discovery.py`
  - `independent_evidence_verifier.py`
  - `discovery_finding_gate.py`
  - `runtime_verifier.py`
- Affected frontend code:
  - `frontend/src/api/data.ts`
  - `frontend/src/api/client.ts`
  - `frontend/src/types/index.ts`
  - `frontend/src/pages/Dashboard.tsx`
  - `frontend/src/pages/Findings.tsx`
  - `frontend/src/pages/EvidenceChain.tsx`
  - `frontend/src/pages/InternalClues.tsx`
  - `frontend/src/pages/BehaviorSpace.tsx`
  - `frontend/openapi/phase104_api_contract/openapi.json`

## ADDED Requirements
### Requirement: 真实证据绑定必须具备有效目标信号
系统 SHALL 仅在 finding 具备可验证的目标绑定信号时，才允许将运行时 HAR、日志或 DB 证据绑定到该 finding。

#### Scenario: finding 缺少真实接口或操作标识
- **WHEN** 一条 finding 没有明确的接口路径、方法、trace、request id、业务实体主键或等价的绑定信号
- **THEN** 系统不得将任意 HAR、日志或 DB 证据附着到该 finding
- **AND** 系统必须把该 finding 保持为待补证状态
- **AND** 系统只能输出“缺少真实绑定信号”的缺口说明，而不能输出推断出的接口、方法或表

#### Scenario: 绑定信号与候选证据不一致
- **WHEN** finding 的目标路径、方法、业务对象或时序信息与候选 HAR/日志/DB 证据不一致
- **THEN** 系统不得把该候选证据纳入最终证据链
- **AND** 系统应把该命中记录为内部候选，而不是客户可见证据

### Requirement: 复现资产必须来自真实执行
系统 SHALL 仅将真实执行产生的请求、响应、步骤、命令和观察结果下发为复现资产。

#### Scenario: 缺少真实复现资产
- **WHEN** finding 没有真实执行产生的 request/response、HAR 片段、步骤轨迹或原始观测
- **THEN** 系统不得生成或展示默认 cURL、默认步骤、默认 GET/POST 方法、默认账号、默认密码或默认 base_url
- **AND** 前后端只能展示“待补真实复验入口”

#### Scenario: 资产为 synthetic
- **WHEN** 任一 reproduction 资产被标记为 synthetic、derived、suggested 或 fallback
- **THEN** 该资产不得被用于 customer-ready 判定
- **AND** 客户页不得展示该资产为“复制命令”“点击复现”或“可验收入口”

### Requirement: customer-ready 缺陷必须满足统一严格门禁
系统 SHALL 使用后端统一生成的 customer delivery status 作为客户页唯一准入依据，并要求真实证据门禁同时成立。

#### Scenario: 缺陷进入客户页
- **WHEN** 一条 finding 被标记为 customer-ready defect
- **THEN** 它必须同时满足：
- **AND** `bug_status = reproduced`
- **AND** `gate_passed = true`
- **AND** 存在真实运行时证据、DB 快照、日志链路或等价原始证据之一
- **AND** 所有复现资产均非 synthetic
- **AND** 前端不得通过本地猜测或 path/method 兜底放行

#### Scenario: finding 仅有线索或弱证据
- **WHEN** 一条 finding 缺少真实原始证据、缺少真实复验入口、或仅有规则/文档/推断性链条
- **THEN** 系统必须将其归类为 `clue`
- **AND** 客户页不得将其计入可交付缺陷、可验收证据包或可复现问题

### Requirement: 客户页与内部页必须物理隔离
系统 SHALL 将客户交付缺陷与内部待验证线索在数据结构、汇总统计和页面展示上完全分离。

#### Scenario: 输出 command-center
- **WHEN** 后端返回 command-center 数据
- **THEN** `data.defects` 仅包含 customer-ready 缺陷
- **AND** `data.clues` 仅包含待验证线索
- **AND** `data.risks` 只能作为兼容别名，不得承载额外候选集合
- **AND** 汇总字段、合同字段、扫描元数据必须与 `defects/clues` 实际数组严格一致

#### Scenario: 前端客户页面消费数据
- **WHEN** Dashboard、Findings、EvidenceChain、Sidebar 消费 command-center
- **THEN** 页面只允许基于 `data.defects` 与 `data.clues` 计算数量、状态与列表
- **AND** 不得回退到旧 `risks` 口径
- **AND** 不得用 P0 数、证据链长度或 path/method 冒充缺陷总数

### Requirement: 证据包必须具备真实验收价值
系统 SHALL 仅将具备真实运行证据或等价硬证据的 finding 计为客户验收证据包。

#### Scenario: finding 仅有规则链或文档链
- **WHEN** finding 只有规则说明、文档引用、推断结论或建议性步骤
- **THEN** 它不得计入 evidence pack
- **AND** 不得计入证据闭环率、可验收证据包数量或客户验收动作

#### Scenario: finding 具备真实原始证据
- **WHEN** finding 具备 HAR 原始请求/响应、DB 快照、日志链路或可重放执行轨迹
- **AND** 这些证据与 finding 的目标对象一致
- **THEN** 系统可以将其计入 evidence pack

## MODIFIED Requirements
### Requirement: 发现结果分层展示
系统 SHALL 将对外结果严格分为 `defect` 与 `clue` 两层；所有客户级汇总、列表和导出默认仅展示 `defect`。

#### Scenario: 输出客户级汇总
- **WHEN** 系统输出风险总览、行为验证、证据链、导出报告或侧栏统计
- **THEN** 所有正式数量默认基于 `defects` 计算
- **AND** 任何 `clue`、`not_reproduced`、`suspected`、`risk_clue` 都不得混入客户正式结果

### Requirement: 严格 verifier 与证据 gate 一致
系统 SHALL 统一 verifier、formatter、gate、independent verifier 的通过标准，不允许同一 finding 在不同链路中出现“一个通过、一个拒绝”的状态分叉。

#### Scenario: cleanup 或 evidence 状态未完成
- **WHEN** finding 的 cleanup、evidence refs、expected/actual、runtime evidence 任一关键条件未完成
- **THEN** 所有 verifier/gate 都必须给出一致的未通过结论
- **AND** finding 必须降级为 clue 或 pending

### Requirement: 运行验证器必须保持跨行业通用
系统 SHALL 禁止在 runtime verifier 与复验探针链路中使用固定 base_url、固定账号、固定密码、固定业务路径或样例行业数据。

#### Scenario: 编译项目级验证探针
- **WHEN** 系统为某项目生成 runtime verifier 或复验探针
- **THEN** 所有接口、账号、环境和样本必须来自项目配置、知识资产或真实运行结果
- **AND** 不得落入任何示例系统、localhost 样板或行业硬编码模板
