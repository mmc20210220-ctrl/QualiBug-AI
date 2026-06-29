# Tasks
- [x] Task 1: 定义 Behavior Space Visualization Schema
  - [x] SubTask 1.1: 设计统一类型：scene、system nodes、behavior paths、probe executions、findings、evidence refs、replay refs、audit refs
  - [x] SubTask 1.2: 建立后端 runtime/environment/probe/reproduction 数据到 schema 的映射器
  - [x] SubTask 1.3: 为 schema 加入“价值摘要字段”，支持首页/报告页直接复用

- [ ] Task 2: 建立 2D 业务流与证据链视图
  - [ ] SubTask 2.1: 用 React Flow + ELK 构建系统拓扑与业务流视图
  - [ ] SubTask 2.2: 在图上叠加覆盖度、风险暴露点、证据入口、阻断项
  - [ ] SubTask 2.3: 支持从节点/边/风险点下钻到风险详情、证据链和回放入口

- [ ] Task 3: 建立 2.5D Behavior Space 沙盘
  - [ ] SubTask 3.1: 用 React Three Fiber + Three.js 搭建基础 2.5D 场景与相机控制
  - [ ] SubTask 3.2: 把系统节点、行为穿行、风险暴露、环境状态映射成 2.5D 图元
  - [ ] SubTask 3.3: 仅为高价值场景提供 2.5D 入口，不替代主操作界面

- [ ] Task 4: 打通回放与审计
  - [ ] SubTask 4.1: 把 reproduction pack 与证据摘要绑定到风险点/行为路径
  - [ ] SubTask 4.2: 把交付、审批、导出、签收等审计信息关联到场景或侧边面板

- [ ] Task 5: 商业价值与门禁
  - [ ] SubTask 5.1: 为行为空间页补齐“是否可上线 / 风险成本 / 下一步动作”价值摘要
  - [ ] SubTask 5.2: 增加回归门禁，防止行为空间页面退化为炫技或原始技术数据
  - [ ] SubTask 5.3: 增加构建/测试/性能基线验证，并验证 2D/2.5D 入口可用

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2
- Task 4 depends on Task 3
- Task 5 depends on Task 4
