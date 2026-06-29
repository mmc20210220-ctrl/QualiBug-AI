# Tasks
- [x] Task 1: 盘点企业知识中心当前输入适配链路与真实短板
  - [x] SubTask 1.1: 梳理 `enterprise_knowledge_center` 的分类、解析、资产构建和上传链路
  - [x] SubTask 1.2: 记录 `QualiBug_ECommerce_Benchmark` 各类输入的当前表现与失败模式
  - [x] SubTask 1.3: 明确哪些问题属于分类误判，哪些属于结构化 extractor 缺失，哪些属于展示口径失真

- [x] Task 2: 修正 source type 分类逻辑
  - [x] SubTask 2.1: 调整 `PRD/openapi` 等类型的分类优先级与判定条件
  - [x] SubTask 2.2: 为 benchmark 里的 `PRD.md`、`API_DOCS.md`、`DATABASE_DESIGN.md`、`field_dictionary.md` 建立稳定分类回归
  - [x] SubTask 2.3: 确保分类修正不破坏标准 OpenAPI/SQL/CSV 等已有强支持路径

- [x] Task 3: 增加 markdown 接口文档结构化提取
  - [x] SubTask 3.1: 为 `API_DOCS.md` 这类 markdown 文档提取 `method/path` 接口条目
  - [x] SubTask 3.2: 尽可能提取鉴权、状态码、请求参数与响应摘要
  - [x] SubTask 3.3: 让知识资产 `interfaces` 与 `generated_probe_count` 能基于 markdown API 文档获得增益

- [x] Task 4: 增强数据库设计与字段字典适配
  - [x] SubTask 4.1: 从 `DATABASE_DESIGN.md` 中提取业务对象、实体关系或约束线索
  - [x] SubTask 4.2: 从 `field_dictionary.md` 中提取字段语义、别名、约束或枚举线索
  - [x] SubTask 4.3: 让这些信息补充到知识资产，而不是停留在零结构化输出

- [x] Task 5: 增强 UIUX 文档与 SVG 设计资料适配
  - [x] SubTask 5.1: 从 `UIUX_SPEC.md` 提取页面、状态、交互与流程线索
  - [x] SubTask 5.2: 从 `uiux/*.svg` 提取标题、文本节点、状态流转或页面标识
  - [x] SubTask 5.3: 把设计线索沉淀到 design oracle manifest 或等价中间结构，供下游消费

- [x] Task 6: 收敛上传链路与格式支持口径
  - [x] SubTask 6.1: 评估并修正二进制上传处理，避免把未稳定支持的格式错误宣传为已可用
  - [x] SubTask 6.2: 明确“强支持 / 浅支持 / 仅 ingest / 不支持”的格式分层
  - [x] SubTask 6.3: 让页面/API 的格式说明与后端真实能力一致

- [x] Task 7: 增加回归测试与真实 benchmark 验证
  - [x] SubTask 7.1: 为分类、markdown API 提取、字段字典、UIUX 线索提取增加针对性测试
  - [x] SubTask 7.2: 用 `QualiBug_ECommerce_Benchmark` 重新 ingest 并记录关键指标
  - [x] SubTask 7.3: 对比改造前后 `interface_count`、`data_table_count`、`rule_count`、`oracle_count`、`generated_probe_count`

- [x] Task 8: 提交、推送并做最终项目复测
  - [x] SubTask 8.1: 在验证通过后整理本轮代码改动并形成单一清晰提交
  - [x] SubTask 8.2: 推送到远端 `origin/main`
  - [x] SubTask 8.3: 推送后再跑 benchmark 项目复测，并汇总最终数据变化

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 1
- Task 5 depends on Task 1
- Task 6 depends on Task 1
- Task 7 depends on Task 2
- Task 7 depends on Task 3
- Task 7 depends on Task 4
- Task 7 depends on Task 5
- Task 7 depends on Task 6
- Task 8 depends on Task 7
