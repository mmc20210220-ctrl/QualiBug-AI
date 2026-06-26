# Phase58 · 企业知识统一接入层

## 目标

将企业已拥有的 PRD、MRD、OpenAPI、Postman、数据库表结构、权限矩阵、历史 Bug、工单和协作文档转换为**可追溯测试资产**，而不是要求测试团队手工维护新的行业知识包。

核心链路：

```text
资料导入
  → 内容去重与版本记录
  → 统一解析：模块 / 对象 / 角色 / 状态机 / 接口 / 字段 / 规则 / 风险
  → 关联：资料规则 → 接口 → 数据表 → Oracle → Probe
  → 风险计划、保障覆盖、证据包、缺陷报告与发布风险看板
```

## 设计原则

- **薄接入层**：只负责资料治理、语义归并和关系追溯；复用 Phase56 保障覆盖、Phase57 多行业理解、现有 Probe Planner、Oracle 与报告链路。
- **资料是证据，不是模板**：不会要求客户选择行业包；行业风险只在资料和接口证据达到阈值后启用。
- **避免功能堆叠**：每份资料必须至少贡献规则、接口、数据依赖、权限边界、风险域或可执行验证线索之一。
- **默认安全**：不抓取外部 URL；飞书/Confluence 仅接收导出内容或受控连接器的文本载荷。生产执行默认只读，写入/重放/迁移/状态迁移全部为 `sandbox_required`。

## 支持资料类型

| 类型 | 识别方式 | 主要贡献 |
|---|---|---|
| PRD / MRD | 文件名、文档标题、需求关键词 | 业务规则、角色、状态流、风险点 |
| OpenAPI / Swagger | JSON `openapi/swagger + paths` | 接口、参数、响应契约、模块 |
| Postman | Collection schema / item | 补充接口与调用场景 |
| SQL / 表结构导出 | `CREATE TABLE` / Schema JSON | 数据表、字段、外键、数据依赖 |
| 权限矩阵 | CSV / JSON / 文档 | 角色、资源、动作、数据域范围 |
| 历史 Bug / 工单 | JSON / CSV / 文本 | 高价值回归风险、固定问题模式 |
| 飞书 / Confluence 导出 | 文件名/显式类型/文本 | 规则、流程、审批边界、运行约束 |

## 资产结构

`enterprise_business_knowledge_asset.json` 主要包含：

- `module_tree`
- `business_objects`
- `roles`
- `state_machines`
- `interfaces`
- `data_fields` / `data_tables`
- `rule_library`
- `permission_matrix`
- `data_dependencies`
- `risk_domains`
- `oracle_library`
- `relationships`
- `source_inventory`
- `summary`

关系图至少覆盖：

```text
source → rule / interface / table / permission / state_machine
rule → interface
rule → table
interface → table
risk → probe
```

## 资料治理

- 内容通过 SHA-256 去重。
- 同一逻辑资料名的新内容创建新版本；旧版本状态标记为 `superseded`，不再参与当前资产构建。
- 删除是软删除；可选 `purge_bytes=True` 才会删除项目级原始字节。
- 上传、编辑、删除必须由 `knowledge_admin`、`project_owner`、`qa_lead` 或 `admin` 执行。
- 报告与证据包仅保留来源 ID、版本、内容哈希和脱敏摘要，不嵌入原始资料或凭证。

## 接入 API

```python
from ai_test_asset_center.enterprise_knowledge_center import (
    ingest_enterprise_knowledge_documents,
    ingest_enterprise_knowledge_files,
    build_enterprise_business_knowledge_asset,
    generate_enterprise_business_knowledge_probes,
    operate_enterprise_knowledge_center,
)

ingest_enterprise_knowledge_files(
    project_id="customer_a",
    file_paths=["PRD.md", "openapi.json", "schema.sql", "permission_matrix.csv"],
    actor={"name": "qa_owner", "role": "project_owner"},
)

asset = build_enterprise_business_knowledge_asset("customer_a")
probes = generate_enterprise_business_knowledge_probes({}, {}, "customer_a")

# Local page/controller actions: view | upload | edit | delete | rebuild
view = operate_enterprise_knowledge_center("customer_a", "view", actor={"name": "qa_owner", "role": "project_owner"})
```

可用 CLI：

```bash
python -m ai_test_asset_center.enterprise_knowledge_center \
  --project customer_a \
  --ingest PRD.md openapi.json schema.sql permission_matrix.csv \
  --rebuild --render-center
```

## 页面

构建后会生成本地项目页：

```text
platform_outputs/<project>/enterprise_knowledge_center/
  enterprise_business_knowledge_center.html
  enterprise_business_knowledge_asset.json
  enterprise_business_knowledge_report.html
```

页面展示上传、编辑、删除、查看、重建的受控操作入口说明，并列出资料版本、资产概览、治理规则和来源追溯。实际改变资料的动作仍必须调用受权限控制的接入 API/CLI。

## 验证边界

知识资产提高的是“资料驱动的高价值业务 Bug 发现与证据闭环”，不是对所有业务规则或所有运行路径的绝对保证。系统会输出覆盖缺口和待人工确认项，而不会承诺零缺陷或覆盖全部业务 Bug。
