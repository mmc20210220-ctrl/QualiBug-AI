# Tasks
- [ ] Task 1: 统一真实 Bug、真实证据与 customer-ready 缺陷的单一真相源
  - [ ] SubTask 1.1: 梳理 `bug_status`、`gate_passed`、`delivery_track`、`customer_delivery_status` 的生成与流转链路
  - [ ] SubTask 1.2: 统一 command-center 输出，只保留 `defects/clues` 双轨正式口径
  - [ ] SubTask 1.3: 让汇总字段、合同字段、扫描元数据完全从 `defects/clues` 重算，禁止旁路统计

- [ ] Task 2: 收紧 HAR/日志/DB 证据绑定，杜绝错绑与串案
  - [ ] SubTask 2.1: 审查 `har_bridge.py` 的匹配策略，移除会把无关接口当作证据的模糊匹配
  - [ ] SubTask 2.2: 明确“有效绑定信号”定义，缺少路径/方法/trace/对象键时禁止 customer-facing 证据绑定
  - [ ] SubTask 2.3: 为错绑高风险场景补自动化回归，包括空路径 finding、认证墙 401、相似路径接口和同资源前缀接口

- [ ] Task 3: 禁止生成伪复现资产与样例化证据
  - [ ] SubTask 3.1: 清理 `evidence_enricher_v3.py`、`display_ready_formatter.py` 中的默认命令、默认步骤、默认方法、默认账号和默认 base_url
  - [ ] SubTask 3.2: 统一 synthetic/derived/suggested 语义，让所有下游链路都将其视为不可交付资产
  - [ ] SubTask 3.3: 对无真实复验入口的 finding 只输出缺口说明，不输出会被误当成真实资产的文本

- [ ] Task 4: 统一严格 verifier 与 evidence gate 通过标准
  - [ ] SubTask 4.1: 收紧 `real_project_defect_discovery.py` 中 strict verifier 的 expected/actual/evidence refs 门槛
  - [ ] SubTask 4.2: 对齐 `independent_evidence_verifier.py` 与 `discovery_finding_gate.py` 的 cleanup、runtime evidence 与降级规则
  - [ ] SubTask 4.3: 确保同一 finding 在 verifier、formatter、command-center 里不会出现状态分叉

- [ ] Task 5: 收死前端客户页准入，禁止本地放大或误展示
  - [ ] SubTask 5.1: `Dashboard`、`Findings`、`EvidenceChain`、`Sidebar` 只消费 `defects/clues` 与后端 delivery status
  - [ ] SubTask 5.2: 移除 `risks` 回退、`path+method` 放行、P0 代替总数、evidence_chain 长度代替证据闭环等本地推断
  - [ ] SubTask 5.3: `BehaviorSpace`、`Findings`、`EvidenceChain` 不再展示默认 `GET`、建议性命令或建议性复现入口

- [ ] Task 6: 补齐 API 契约与类型约束，固化前后端一致性
  - [ ] SubTask 6.1: 在 OpenAPI 与前端类型中正式声明 `defects/clues/delivery_track/customer_delivery_status/evidence_quality/reproduction`
  - [ ] SubTask 6.2: 为关键合同字段增加契约测试，防止字段名、语义或默认值再次漂移
  - [ ] SubTask 6.3: 为导出、总览、侧栏、证据链增加同源断言，确保所有数字与状态来自同一数组

- [ ] Task 7: 去除运行验证器中的行业硬编码与样板化探针
  - [ ] SubTask 7.1: 审查 `runtime_verifier.py` 和相关运行时探针，移除固定 base_url、账号、密码、路径和行业样板
  - [ ] SubTask 7.2: 改为从项目配置、知识资产、真实执行结果或探针编译产物读取运行参数
  - [ ] SubTask 7.3: 补跨行业回归样例，证明验证器不会因为样板化输入制造假阳性

- [ ] Task 8: 建立针对“真实 Bug、真实证据、真实可复现”的专项回归
  - [ ] SubTask 8.1: 增加后端回归，覆盖错绑 HAR、synthetic reproduction、状态分叉、cleanup 口径不一致
  - [ ] SubTask 8.2: 增加前端回归，覆盖客户页不展示 clue、不开放假命令、不显示错误方法
  - [ ] SubTask 8.3: 增加端到端验收，验证代表性项目中客户页只出现真实 defect，且证据可复跑

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 1
- Task 5 depends on Task 1
- Task 6 depends on Task 1
- Task 6 depends on Task 5
- Task 7 depends on Task 1
- Task 8 depends on Task 2
- Task 8 depends on Task 3
- Task 8 depends on Task 4
- Task 8 depends on Task 5
- Task 8 depends on Task 6
- Task 8 depends on Task 7
