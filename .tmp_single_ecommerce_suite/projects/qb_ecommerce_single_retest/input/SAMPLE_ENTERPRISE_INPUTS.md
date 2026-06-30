# 企业输入资料样例

客户在使用测试引擎时可能上传以下资料：

- PRD 文档：`docs/PRD.md`
- 接口文档：`docs/API_DOCS.md`
- 数据库设计：`db/schema.sql` 与 `db/field_dictionary.md`
- 历史问题记录：`docs/HISTORICAL_BUG_RECORDS.csv`
- UI/UX 设计稿：`uiux/*.svg`
- 冒烟用例：`tests/api_smoke.http`
- 代码仓库：`apps/frontend` 与 `apps/backend`

测试引擎应从这些资料中自动抽取业务实体、状态机、字段约束、接口契约、UI 交互约束和历史风险模式，再通过实际运行或静态分析发现问题。
