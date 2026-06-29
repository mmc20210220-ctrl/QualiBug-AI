# QualiBug Blind 输入链接入知识中心结构化结果 Spec

## Why
当前 `enterprise_knowledge_center` 已经能从无源码企业资料中提取接口、字段字典、数据表、设计线索和 probe 候选，但 `bug-engine-input-only` / blind 候选链仍主要依赖较老的 `input_only` 编译逻辑，导致真实 benchmark 中即便知识中心资产已显著增强，blind 侧仍出现 `api_count=0`、`endpoint_count=0`、`candidate_count=0` 的断层。

本次变更目标是在不引入 oracle/ground-truth 窥探的前提下，把 blind 无源码候选链接入企业知识中心的结构化结果，提升 `api_count`、`endpoint_count`、`candidate_count` 和 grounded candidate 产出质量，并保持严格 no-peek 边界。

## What Changes
- 在 `bug-engine-input-only` / blind runner 中引入企业知识中心资产构建与消费步骤。
- 让 blind 链能够读取知识中心生成的 `interfaces`、`field_dictionary`、`data_tables`、`ui_design_specs` 与 `oracle_library`，并转化为项目上下文、端点清单与 grounded candidates。
- 在不越过 no-peek 边界的前提下，允许 blind 链使用 `input/` 资料生成的结构化资产，而不是只依赖浅层文档扫描。
- 调整 input manifest / leak guard 的规则边界，明确哪些输入可以进入知识资产但不能作为 oracle/答案源参与评分。
- 增加定向测试与 benchmark 回归，验证 blind 报告中的 `api_count`、`endpoint_count`、`candidate_count` 与产物不再为 0。

## Impact
- Affected specs: blind 无源码候选生成、企业知识中心资产消费、strict no-peek 输入边界、benchmark blind 回归验证
- Affected code: `ai_test_asset_center/blind_project_runner.py`、`ai_test_asset_center/input_grounded_candidate_compiler.py`、`ai_test_asset_center/enterprise_knowledge_center.py`、`ai_test_asset_center/blind_benchmark_runner.py`、相关 tests

## ADDED Requirements
### Requirement: Blind 链必须消费 input-only 生成的知识资产
系统 SHALL 在 `bug-engine-input-only` 模式下，仅基于 `projects/<project>/input` 中允许读取的资料构建企业知识资产，并把该资产接入 blind 候选生成链。

#### Scenario: 运行 input-only 项目
- **WHEN** 用户运行 `bug-engine-input-only`
- **THEN** 系统从允许的 `input/` 文件构建知识中心资产
- **AND** blind 候选生成可以消费该资产中的结构化结果
- **AND** 系统不得读取 `oracle`、`ground_truth`、`seed`、`answer`、`bug_matrix` 等受限目录或文件作为候选依据

### Requirement: Blind 项目上下文必须包含结构化接口与实体信息
系统 SHALL 将知识中心资产中的 `interfaces`、`data_tables`、`field_dictionary`、`business_objects` 反映到 blind 项目上下文与报告摘要中。

#### Scenario: 生成 blind 输入报告
- **WHEN** blind 链完成输入解析
- **THEN** 报告中的 `api_count`、`entity_count`、`endpoint_count` 反映知识中心结构化结果
- **AND** 当 markdown API 文档存在时，`api_count` 与 `endpoint_count` 不得继续维持为 0

### Requirement: Blind grounded candidates 必须由结构化资产增益
系统 SHALL 使用知识中心资产中的规则、接口、数据依赖、历史缺陷与 UIUX 线索增强 grounded candidate 生成，而不是仅依赖浅层正则扫描。

#### Scenario: 生成 grounded candidates
- **WHEN** blind 链编译 grounded candidates
- **THEN** 它结合知识中心资产中的 `oracle_library`、`interfaces`、`data_tables`、`ui_design_specs`
- **AND** 输出的 candidate 数量与质量相比仅靠旧编译链应有可见增益

### Requirement: Strict no-peek 边界必须保持成立
系统 SHALL 明确区分“允许进入知识资产的输入资料”和“禁止作为答案源参与候选评分的受限资料”，确保接入知识中心后仍保持 strict no-peek。

#### Scenario: 处理 seed 或其他边界文件
- **WHEN** blind 链遇到 `seed.sql` 或其他边界资料
- **THEN** 系统按显式规则决定其是否可作为普通输入资料进入结构化资产
- **AND** 若禁止参与候选评分，报告明确记录阻断或降级原因
- **AND** 系统不得把受限资料当作 oracle/ground truth 使用

### Requirement: Blind benchmark 回归必须体现结构化增益
系统 SHALL 使用真实 benchmark 无源码资料回归 blind 链，并验证关键指标相对旧链改善。

#### Scenario: 运行 blind benchmark 回归
- **WHEN** 系统对电商 benchmark 运行 blind input-only 回归
- **THEN** 结果至少输出 `api_count`、`endpoint_count`、`candidate_count`、`issue_count`
- **AND** 与旧基线相比，`api_count` 与 `endpoint_count` 应显著提升
- **AND** 若 `issue_count` 仍为 0，系统必须能说明是执行阶段缺失还是候选阶段仍有边界

## MODIFIED Requirements
### Requirement: Blind 无源码链的成功标准从“读到文件”升级为“读到结构化资产”
系统现有 blind 输入链 SHALL 继续坚持 strict no-peek，但其成功标准应从“只完成 input 文件扫描”升级为“能够把 input-only 资料构建成结构化知识资产并消费到 grounded candidate 生成”。

### Requirement: Benchmark blind 报告必须与知识资产能力一致
系统现有 benchmark blind 报告 SHALL 反映知识中心资产已提取出的接口、实体与候选增益，避免出现“知识中心里已有接口与 probe，但 blind 报告仍显示 0”的断层。

## REMOVED Requirements
### Requirement: Blind 链只依赖旧 input compiler 的浅层扫描
**Reason**: 当知识中心已经能从同一批 input 资料中抽出高价值结构化结果时，blind 链继续只依赖浅层扫描会造成能力断层，无法代表系统在无源码输入场景下的真实能力。
**Migration**: 让 blind 链优先消费 input-only 构建出的知识中心资产，并在 strict no-peek 边界内保留必要的旧链兼容逻辑。
