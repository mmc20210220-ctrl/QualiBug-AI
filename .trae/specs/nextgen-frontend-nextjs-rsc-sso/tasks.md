# Tasks
- [x] Task 0: 商业价值信息架构定标（不堆功能）
  - [x] SubTask 0.1: 定义“价值呈现层”最小字段（上线建议/阻断/节省工时/覆盖/下一步动作/证据入口）
  - [x] SubTask 0.2: 将后端能力成果映射到前端信息架构（能力→页面→指标→下钻证据）
  - [x] SubTask 0.3: 为每个核心页面设定“价值验收点”（必须可读、可决策、可行动）

- [x] Task 1: 建立 `frontend/` Next.js 商用前端工程骨架
  - [x] SubTask 1.1: 初始化 `frontend/`（Next.js App Router、TypeScript、现代 lint/test 配置）
  - [x] SubTask 1.2: 建立基础路由结构（登录、项目列表、项目工作区、能力中心、风险证据、执行、报告）
  - [x] SubTask 1.3: 定义设计系统与组件基座（tokens、主题、布局、导航、表格、图表、状态块）

- [x] Task 2: 企业 SSO 与权限模型
  - [x] SubTask 2.1: 接入 SSO（OIDC 优先，SAML 预留），完成登录/登出/回调
  - [x] SubTask 2.2: 建立租户/项目/角色权限模型与路由守卫（含无权限提示与申请入口占位）
  - [x] SubTask 2.3: API 调用携带身份上下文（不伪造可信 actor 头），并做前端脱敏展示策略

- [x] Task 3: 强类型 API 合同与可信健康检查
  - [x] SubTask 3.1: 建立 OpenAPI/Schema → TS 类型生成与版本锁定策略
  - [x] SubTask 3.2: 实现统一 API Client（错误包/网络/超时/重试/取消），并落实“真实健康检查才在线”
  - [x] SubTask 3.3: Demo/Real 模式策略：demo 仅用于演示，real 模式必须可观测 offline/error/unverified

- [x] Task 4: 后端能力成果的产品化信息架构
  - [x] SubTask 4.1: 能力中心/覆盖度：能力清单、覆盖范围、缺口建议与对比视图
  - [x] SubTask 4.2: 风险与证据链：列表/详情/下钻、业务影响、复现、修复建议、关闭准则
  - [x] SubTask 4.3: 执行与任务生命周期：runs/jobs、阶段进度、事件流、失败原因与可重试策略
  - [x] SubTask 4.4: 报告与 ROI：领导层摘要、导出/分享状态、审计轨迹入口

- [x] Task 5: 实时状态与可观测交互
  - [x] SubTask 5.1: 实现可配置实时策略（SSE/WebSocket/轮询兜底），并提供统一连接状态 UI
  - [x] SubTask 5.2: 长链路动作统一交互（进度、取消、危险确认、完成/失败结果归档）

- [x] Task 6: 商用门禁与交付
  - [x] SubTask 6.1: CI 门禁：类型检查、构建、单测、E2E、脱敏扫描、依赖漏洞基线
  - [x] SubTask 6.2: 关键用户旅程 E2E：登录→选项目→看能力→看风险→看执行→出报告
  - [x] SubTask 6.3: 发布与部署约定（环境变量、反向代理、静态资源、私有化部署兼容）

# Task Dependencies
- Task 1 depends on Task 0
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2
- Task 4 depends on Task 3
- Task 5 depends on Task 4
- Task 6 depends on Task 5
