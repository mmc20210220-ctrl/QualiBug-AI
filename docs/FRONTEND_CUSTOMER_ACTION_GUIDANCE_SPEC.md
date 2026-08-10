# QualiBug 前端客户行动引导 SPEC

状态：已实现  
范围：仅前端展示、导航、交互状态与响应式布局；不改变任何后端检测、Finding、Release Gate 或证据判定逻辑。

## 1. 目标

客户在 Dashboard、运行中心、问题清单、证据中心和发布门禁之间流转时，页面必须根据后端已经返回的真实状态给出一致的下一步，不允许出现以下冲突：

- 页面提示“覆盖未完成”，同时给出“可以发布”；
- 运行前检查已有明确阻断项，但主扫描按钮仍然可以提交；
- 0 个已确认问题时只展示空列表，让客户把“空”误读成“安全”；
- 筛选没有命中与项目本身没有 Finding 使用同一个空态；
- 已确认 P0 因为覆盖递延或链路异常而被降级成普通“待确认”；
- Dashboard 与 ReleaseGate 对同一高风险状态给出不同优先级；
- “没有 Finding”“有 Finding 但证据包缺失”“证据数据读取失败”使用同一个证据空态；
- Evidence 双栏和客户主操作在窄屏下横向溢出。

## 2. 统一发布展示优先级

前端使用 `release-presentation.ts` 作为高风险发布展示优先级解释器。它只组合后端已经返回的事实，不重新计算后端质量结论。

优先级：

1. 已确认 P0：始终优先显示“建议阻断”；
2. 无 P0，但存在与覆盖未完成无关的明确 Release Gate 失败：显示“不建议发布”；
3. 无独立门禁失败，但检测链路 FAILED_SAFE / BLOCKED、Campaign blocked 或 coverage_deferred：显示“待确认”，不得绿色放行；
4. Release Gate 仍有 pending：显示“待处理”；
5. Release Gate 明确 pass：发布页才显示“可以发布”；
6. 无完整 Gate 回执时，发布页不得仅凭 0 Finding 显示绿色放行。

Dashboard 的 P0 / incomplete 高风险状态同样复用该优先级解释器；对完整扫描的普通已确认问题与无问题状态保留 Dashboard 原有“有条件发布 / 可以发布”摘要，并引导用户进入 ReleaseGate 取得正式门禁结论。

`coverage_deferred` 下的 `0 个 P0` 只代表已覆盖范围，前端必须明确说明不能直接推导为系统安全。

## 3. Dashboard 下一步行动

按真实状态只突出一个主要动作：

- 检测链路异常 / BLOCKED：查看运行状态；
- Campaign blocked：处理阻断条件；
- coverage_deferred：继续检测剩余范围；
- 已确认缺陷 > 0：处理问题；
- 检测完整且无已确认缺陷：查看发布门禁。

辅助动作按上下文出现：

- 有已确认缺陷才突出证据与缺陷回归；
- 结果不完整时提供“查看未覆盖范围”；
- 结果完整时提供“再次检测”；
- 结果不完整时导出按钮使用“导出当前报告”，避免暗示报告代表完整安全结论。

## 4. 运行中心启动门禁

运行中心主扫描按钮必须同时满足：

- 运行前检查已通过；
- 当前没有运行中的扫描；
- Fixture / 审批场景必要同步已经结束；
- 审批场景同步没有失败，或用户明确启用了只读熔断路径。

当运行前检查未通过时：

- 主扫描按钮 disabled；
- 按钮直接显示阻断数量或“运行前检查未通过”；
- 页面说明不会提交扫描请求；
- 点击处理入口回到既有 Settings，不创建第二套配置状态。

运行完成 Toast 与运行结果卡必须复用同一 `resultTone()` 判定，避免同一结果一处绿色、一处黄色。

## 5. 问题清单空态

### 真实 0 Finding

当项目确实没有已确认 Finding 时：

- 不展示全是 0 的筛选条；
- 明确说明空列表不等于系统没有问题；
- 提供“继续检测”；
- 提供“查看覆盖范围”；
- 保留“返回价值总览”。

### 筛选后 0 Finding

当项目有 Finding，但筛选 / 搜索没有命中时：

- 文案必须是“没有匹配的问题”；
- 主动作是“清除筛选”；
- 不把该状态描述成项目没有缺陷。

## 6. 发布门禁页面行动

ReleaseGate 必须同时消费：

- 发布门禁 checks / overall；
- 已确认 Finding 与 P0 数量；
- pipeline health；
- Campaign 状态；
- 商业交付守卫。

前端只用于决定信息优先级和 CTA，不改变上述任一后端事实。

主要 CTA：

- 已确认 P0：处理 P0 问题；
- 检测链路异常：查看运行状态；
- Campaign blocked：处理阻断条件；
- coverage_deferred：继续检测剩余范围；
- 明确门禁失败且已有已确认问题：处理已确认问题；
- 无门禁数据：启动检测；
- 其余状态：返回价值总览。

当结果不完整时，发布页必须提供“查看未覆盖范围”。门禁数据为空时必须明确说明“0 条门禁数据 ≠ 可以发布”。

## 7. 证据中心状态分离

证据中心必须区分三种完全不同的状态：

1. **读取失败**：提示数据异常，可重新读取；不得解释为没有证据或没有问题。
2. **真实 0 Finding**：说明当前没有客户可交付证据包，并提供继续检测与查看覆盖范围。
3. **已有确认 Finding，但 0 Evidence Package**：显示为证据尚未形成的异常/待处理状态，主动作返回问题清单核对，不得降级成普通空态。

当存在 Evidence Package 时，默认打开第一条真实证据，避免进入页面后仍停留在空白详情区。

## 8. 客户主链响应式规则

现有模块化 `evidence.css` 使用 `.evidence-split-layout`，而当前证据页面实际 class 为 `.evidence-layout`。为避免重写遗留 `index.css`，新增最后加载的 `customer-responsive.css` 作为客户主链响应式覆盖层。

要求：

- 1024px 以下 Evidence 双栏折叠为单栏；
- 证据列表取消 sticky，并限制高度，详情保持可阅读；
- 720px 以下 Findings / Evidence 页面头部操作区纵向排列；
- Action Bar 标题独占一行，不和按钮争抢宽度；
- 560px 以下客户主操作按钮全宽，满足触控与小窗口操作；
- 长标题、门禁详情、证据文本允许安全换行，不产生页面级横向滚动。

`customer-responsive.css` 必须在遗留 `index.css` 之后加载，确保真实页面 class 的修正规则生效。

## 9. 回归门禁

`test:customer-action-guidance` 必须进入 `npm run ci:gate`，至少锁定：

- coverage_deferred 不得进入 clean release advice；
- 已确认 P0 的展示优先级高于 incomplete coverage；
- Dashboard 高风险发布状态复用统一前端优先级解释器；
- Dashboard 下一步 CTA 来自状态判断而非固定模板；
- incomplete result 暴露 Coverage 入口；
- ReleaseGate 使用统一优先级解释器，0 Gate 数据不显示安全；
- 预检未通过时运行按钮 disabled，handler 同样 fail closed；
- 运行 Toast 与结果卡共用 result tone；
- 真空 Finding 与筛选空结果是两种不同空态；
- 真空 Finding 必须提供继续检测与覆盖入口；
- Evidence 读取失败、0 Finding、Finding 无证据包必须分开；
- Evidence 默认打开第一条真实证据；
- 客户响应式覆盖层必须最后加载并命中真实 `.evidence-layout`；
- 1024 / 720 / 560 三档客户主链响应式规则不能被删除。

## 10. 非目标

本 SPEC 不定义也不修改：

- Bug 如何发现；
- 场景如何生成；
- Oracle / Observer / Experiment 执行；
- Finding 是否成立；
- Release Gate 的后端判定；
- 覆盖率如何计算；
- 后端扫描状态机。

这些能力全部由后端既有合同提供，前端只负责准确消费、表达和交互。