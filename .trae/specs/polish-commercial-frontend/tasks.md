# Tasks
- [x] Task 1: 盘点现有前端的商用化缺口并冻结本次打磨边界
  - [x] SubTask 1.1: 复核首页、环境诊断、执行、风险、价值、商业演示页的当前信息架构与品牌一致性
  - [x] SubTask 1.2: 标记已具备能力、明显短板和必须保留的现有交互，避免重复建设
  - [x] SubTask 1.3: 将“商用化打磨”范围限定为显示层、状态系统、交互反馈、可用性与验收门禁，不扩展后端核心逻辑

- [x] Task 2: 统一全站产品壳与页面首屏结构
  - [x] SubTask 2.1: 统一页面标题、摘要、主指标区、动作区和辅助信息区的结构范式
  - [x] SubTask 2.2: 收口导航、顶部状态、页面内标签和按钮命名，保证跨页面语言一致
  - [x] SubTask 2.3: 让首页与关键页面首屏优先展示当前状态、阻断项、建议动作和主要入口

- [x] Task 3: 建立完整且可信的状态系统
  - [x] SubTask 3.1: 为核心页面补齐 `loading`、`empty`、`error`、`offline`、`unverified`、`blocked`、`ready`、`running`、`completed`、`partial` 等状态表达
  - [x] SubTask 3.2: 明确区分真实数据、模拟数据、历史快照和待验证状态，并在全局与页面级持续可见
  - [x] SubTask 3.3: 为异常状态提供重试、降级说明、恢复路径和影响范围提示

- [x] Task 4: 收口关键动作反馈与恢复闭环
  - [x] SubTask 4.1: 为刷新、启动、复制、导出、切换模式、打开证据等高频动作补齐处理中、成功、失败与阻断反馈
  - [x] SubTask 4.2: 为高风险动作增加确认或保护机制，避免误触发
  - [x] SubTask 4.3: 统一 Toast、内联反馈、按钮禁用态和结果摘要的表达方式

- [x] Task 5: 强化价值呈现与交付表达
  - [x] SubTask 5.1: 打磨价值页、商业演示页和首页摘要区，让风险、收益、成熟度和客户待配合项以业务语言表达
  - [x] SubTask 5.2: 区分已验证发现、待确认信号和历史结果，避免夸大结论
  - [x] SubTask 5.3: 确保关键页面可以自然承接“客户演示”“领导汇报”“交付复盘”三种使用场景

- [x] Task 6: 建立企业级可用性与验收门禁
  - [x] SubTask 6.1: 校验主要断点下的布局稳定性、关键信息可见性和操作可达性
  - [x] SubTask 6.2: 补齐必要的键盘可达性、focus 表达、非纯颜色状态提示和脱敏检查
  - [x] SubTask 6.3: 更新关键路径回归验证，覆盖页面首屏、状态切换、主要点击路径、脱敏与构建门禁

- [ ] Task 7: 清理 Next 基线类型与构建阻断
  - [ ] SubTask 7.1: 修复 `src/pages/*` 与 Next 类型校验的默认导出兼容问题
  - [ ] SubTask 7.2: 让 `npm run typecheck` 与 `npm run build` 在当前前端基线上通过
  - [ ] SubTask 7.3: 复核 CI 门禁与回归脚本，确认构建阻断被真实消除

- [ ] Task 8: 清理前端 lint 阻断并恢复完整 CI 门禁
  - [ ] SubTask 8.1: 修复 `roi/page.tsx`、`LongActionCard.tsx`、`lib/realtime/command-center.ts` 等当前改动链路上的 hooks lint 违规
  - [ ] SubTask 8.2: 修复 `src/pages/*` 与 legacy 组件中的高优先级 ESLint 错误，至少清空当前 `npm run lint` 阻断项
  - [ ] SubTask 8.3: 重新执行 `npm run ci:gate`，确认 lint、构建、回归与脱敏检查整体通过

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2
- Task 4 depends on Task 3
- Task 5 depends on Task 2
- Task 5 depends on Task 3
- Task 6 depends on Task 3
- Task 6 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 6
- Task 8 depends on Task 7
