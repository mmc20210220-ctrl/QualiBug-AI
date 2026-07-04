# QualiBug 前端商用化打磨 Spec

## Why
当前 `frontend/` 已经具备项目级工作区、环境门禁、执行剧场、证据回放与 ROI 页面，但整体完成度仍更接近“高能力产品原型”，在信息架构一致性、可信状态表达、空/错/加载体验、品牌化呈现、可交付表达与企业级细节上还有明显收口空间。

本次变更目标不是重做前端，也不是扩展新的业务大模块，而是在现有商用前端基线上进行第二阶段打磨，让产品在客户演示、POC、试点交付和高层汇报场景下达到更稳定、更可信、更完整的商用化产品级别。

## What Changes
- 统一全站信息架构、品牌语言和视觉层级，让首页、环境、执行、风险、价值、商业演示等页面形成一致的企业产品体验。
- 补齐商用产品必需的空态、加载态、失败态、无权限态、离线态、数据缺失态和恢复路径，避免页面在边界条件下只剩技术视角表达。
- 强化首屏与关键页面的“决策叙事”，让用户更快理解当前项目状态、下一步动作、风险等级和可交付结果。
- 收口关键动作反馈，包括运行中、完成、失败、阻断、确认、回退和复制成功等交互闭环。
- 提升商用可信度，明确区分真实数据、模拟数据、历史快照和待验证状态，杜绝“看起来在线但实际未验证”的误导表达。
- 补齐企业级可用性基线，包括响应式断点、键盘可达性、基础可读性、脱敏、性能与关键路径回归门禁。

## Impact
- Affected specs: 前端产品壳、页面级体验、可信状态表达、交付与价值展示、前端验收门禁
- Affected code: `frontend/src/app/(app)/projects/[projectId]/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/environment/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/execution/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/risks/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/value/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/commercial-demo/page.tsx`、`frontend/src/components/layout/*`、`frontend/src/components/project/*`、`frontend/src/components/runtime/*`、`frontend/src/components/risk/*`、`frontend/src/components/value-surface/*`、`frontend/e2e/core-journey.spec.ts`、`frontend/scripts/ci/gate.mjs`

## ADDED Requirements
### Requirement: 全站商用级视觉与信息架构一致性
系统 SHALL 在现有前端工作区基础上统一视觉层级、品牌语言、页面标题结构、页面摘要、关键指标区和主行动区，避免不同页面像不同阶段的产品拼接而成。

#### Scenario: 用户在多个核心页面间切换
- **WHEN** 用户在首页、环境诊断、执行、风险、价值和商业演示页面之间切换
- **THEN** 页面都使用一致的标题层级、说明摘要、主指标区、次级说明区和动作区
- **AND** 页面中的状态文案、按钮语义、标签命名和视觉密度保持一致
- **AND** 用户不需要重新学习每个页面的交互范式

### Requirement: 决策优先的首页与页面首屏
系统 SHALL 让首页和关键页面首屏优先服务“管理决策”和“推进动作”，先回答当前状态、风险级别、是否可推进以及下一步做什么，而不是先堆砌技术细节。

#### Scenario: 首次打开项目工作区
- **WHEN** 用户首次进入某个项目的工作区
- **THEN** 首屏应同时展示当前项目判定、关键门禁状态、主要阻断项、建议动作和主要入口
- **AND** 高价值信息必须在首屏可见，不依赖滚动到深层模块后才获取

### Requirement: 完整且可信的状态系统
系统 SHALL 为所有关键页面和长链路动作提供完整状态系统，覆盖 `loading`、`empty`、`error`、`offline`、`unverified`、`blocked`、`ready`、`running`、`completed`、`partial` 等商用场景常见状态。

#### Scenario: 数据源异常或未完成验证
- **WHEN** 页面依赖的真实 API 不可达、健康检查未通过、SSE 中断、数据为空或返回部分结果
- **THEN** 页面明确展示当前状态及其影响范围
- **AND** 页面提供恢复路径、重试入口或降级说明
- **AND** 系统不得把“配置存在”“历史缓存存在”或“模拟数据存在”误显示为在线成功

### Requirement: 动作反馈与可恢复交互闭环
系统 SHALL 为关键动作提供明确反馈和可恢复路径，避免用户点击后只能依靠猜测判断系统是否生效。

#### Scenario: 用户触发关键动作
- **WHEN** 用户执行启动运行、刷新门禁、复制修复动作、打开证据、生成汇报或切换数据模式等关键操作
- **THEN** 页面展示即时反馈、处理中状态和完成结果
- **AND** 对阻断或失败给出具体原因与下一步建议
- **AND** 对高风险动作保留显式确认或保护机制

### Requirement: 商业化表达与交付可信度
系统 SHALL 在价值页、商业演示页和关键摘要区中，以业务可读方式展示风险影响、交付进度、证据完备度、客户待配合项和下一步建议，而不是只展示技术统计值。

#### Scenario: 高层查看项目结果
- **WHEN** 决策者进入价值页、商业演示页或首页摘要区
- **THEN** 页面展示可汇报的结论、量化指标、阻断风险、交付成熟度和推进建议
- **AND** 页面区分“已经验证的发现”和“待确认的信号”
- **AND** 页面不得做未经证实的夸大性宣称

### Requirement: 企业级可用性与可访问性基线
系统 SHALL 满足基础企业级可用性要求，包括主断点可用、长内容可读、关键控件支持键盘访问、状态色不作为唯一信息来源、敏感信息脱敏以及关键路径可回归验证。

#### Scenario: 不同设备和输入方式访问页面
- **WHEN** 用户使用桌面端、较窄窗口或键盘导航访问核心页面
- **THEN** 页面布局不崩坏、不遮挡关键操作、不丢失主要信息
- **AND** 关键控件具有清晰 focus、label 或等价状态提示
- **AND** 测试与页面输出中不暴露 token、cookie、secret、客户真实 host 或原始敏感日志

## MODIFIED Requirements
### Requirement: 前端产品壳从“可展示”提升为“可交付”
系统现有前端产品壳与页面能力继续保留，但变更后其标准 SHALL 从“具备演示能力”提升为“具备稳定商用演示、试点交付和管理汇报能力”，必须覆盖一致的信息架构、可信状态表达和边界场景体验。

### Requirement: Demo 与真实模式的表达必须被用户一眼识别
系统现有 Demo / Real 数据模式能力继续保留，但变更后 SHALL 在全局和页面级持续标识当前数据来源、可信程度和验证状态，避免用户需要通过细节猜测当前看到的是模拟数据、历史快照还是实时结果。

## REMOVED Requirements
### Requirement: 仅靠技术指标和孤立页面证明产品成熟度
**Reason**: 单页能力存在并不代表整体已达到商用产品级别，缺少统一叙事、边界状态与交付表达时，客户仍会感知为工程原型。
**Migration**: 将验收重心从“页面能打开、模块能展示”迁移到“页面形成连续产品体验、关键状态可信、边界条件可处理、结果可汇报可交付”。
