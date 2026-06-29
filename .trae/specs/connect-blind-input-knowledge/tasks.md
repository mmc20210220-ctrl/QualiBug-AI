# Tasks
- [x] Task 1: 盘点 blind input-only 链与知识中心主链的断层点
  - [x] SubTask 1.1: 梳理 `blind_project_runner`、`input_grounded_candidate_compiler` 与 `enterprise_knowledge_center` 的当前调用关系
  - [x] SubTask 1.2: 确认 blind 报告中 `api_count/endpoint_count/candidate_count` 为 0 的具体断层位置
  - [x] SubTask 1.3: 明确 strict no-peek 边界下哪些 input 文件允许进入知识资产

- [x] Task 2: 在 input-only 流程中接入知识中心资产构建
  - [x] SubTask 2.1: 让 `bug-engine-input-only` 基于允许的 `input/` 文件构建项目级知识资产
  - [x] SubTask 2.2: 保持不读取 `oracle/ground_truth/answer/bug_matrix` 等受限资料
  - [x] SubTask 2.3: 让 blind 产物中显式记录知识资产构建摘要

- [x] Task 3: 让 blind 项目上下文消费结构化接口与实体
  - [x] SubTask 3.1: 将知识资产中的 `interfaces` 接入 blind `api_count/endpoint_count`
  - [x] SubTask 3.2: 将 `data_tables/business_objects/field_dictionary` 接入 blind `entity_count` 与上下文摘要
  - [x] SubTask 3.3: 保证 markdown API 文档与字段字典在 blind 报告里有可见体现

- [x] Task 4: 用知识资产增强 grounded candidate 生成
  - [x] SubTask 4.1: 让 `oracle_library`、`interfaces`、`data_dependencies`、`ui_design_specs` 进入 grounded candidate 编译输入
  - [x] SubTask 4.2: 保持旧编译链兼容，避免破坏已有强支持路径
  - [x] SubTask 4.3: 输出更有信息量的 grounded candidate / probe plan 摘要

- [x] Task 5: 收敛 seed 与边界文件规则
  - [x] SubTask 5.1: 审核 `seed.sql` 等边界文件在 strict no-peek 下的处理规则
  - [x] SubTask 5.2: 明确“允许入知识资产”与“禁止作为答案源”的区分
  - [x] SubTask 5.3: 让 blind 报告清楚记录阻断或降级原因

- [x] Task 6: 增加测试与 blind benchmark 回归
  - [x] SubTask 6.1: 增加 blind 链消费知识资产的定向测试
  - [x] SubTask 6.2: 回归 `QualiBug_ECommerce_Benchmark`，记录 `api_count/endpoint_count/candidate_count/issue_count`
  - [x] SubTask 6.3: 对比旧 blind 基线与新 blind 结果，并总结剩余边界

- [x] Task 7: 提交、推送并输出最终 blind 复测结论
  - [x] SubTask 7.1: 仅提交本轮 blind 链相关改动
  - [x] SubTask 7.2: 推送到远端 `origin/main`
  - [x] SubTask 7.3: 给出 blind 与知识中心两条链的最终状态结论

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2
- Task 4 depends on Task 3
- Task 5 depends on Task 1
- Task 6 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 6
