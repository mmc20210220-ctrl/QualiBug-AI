# SPEC:格式无关的企业资料认知能力(实体/字段/权限空间不再为空)

> 文档状态:持续演进（Phase 0–4 已进入现有知识资产主链；Phase 5 增强深层业务语义理解）
> 面向执行者:code agent
> 断点编号:`ENTERPRISE_SOURCE_STRUCTURED_EXTRACTION_EMPTY`
> 上位约束:`AGENTS.md`、`docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`、项目全景文档
> 诊断依据:`docs/AUDIT_2026-07-25_ENTITY_EXTRACTION_BREAKPOINT.md`
> 产品端口:前端 `5174`,后端 `8088`(不得改动)

---

## 0. 为什么这是当前唯一最大断点

跨项目实测(审计报告第二部分):

| 项目 | 实体来源 | IR entities | 历史发现表现 |
|---|---|---|---|
| contractflow_c | openapi.yaml + schema.sql + md 字典 | 22 | Unique TP 57.7% |
| warehouse_e | openapi.yaml | 11 | 4 个深层 Bug |
| ticketsla_d | openapi.yaml | 8 | 盲测 48% |
| **mes_f** | 仅 Markdown | **0** | **盲测 3.1%** |
| **benchmark_mall** | 仅 Markdown | **0** | 131-bug 长期低发现率 |

结论:**当前实体/字段抽取实质依赖客户提供 `openapi.yaml` / `schema.sql` 等机器可读文件。** 只给 Markdown/Word/Excel 的项目,实体空间直接为空,下游绑定、坐标、不变式全部失效。

这与产品定位("全行业、任意企业系统、资料格式必然不一致、自动建立可靠认知")直接冲突,是商业化第一个会暴露的问题。真实客户不会按我们的格式准备资料。

### 现状根因(已定位到代码)

| 编号 | 缺陷 | 位置 |
|---|---|---|
| R1 | 分类是**单标签 + 首个命中即锁定**;`DB_SCHEMA.md` 因不是 `.sql`、正文无 `create table`,落到兜底 `collaboration_document` | `enterprise_knowledge_center/_parsing.py::_classify_source`(约 59 行) |
| R2 | 抽取被分类**门控**:`if source_type in {"db_field_dictionary","database_schema"}` 才跑字段抽取 → 一次误判等于永久零抽取 | `_parsing.py` 约 1209–1212 行 |
| R3 | 字段抽取器只吃 **JSON payload 与 CSV 行**,不解析 **Markdown 表格 / HTML 表格 / Excel sheet** | `_parsing.py::_field_dictionary_entries`(约 285 行) |
| R4 | **不支持 xlsx/xls**(支持集仅 `docx/pdf/har/csv/sql/svg/log/xml/html/htm`),而企业数据字典最常见载体就是 Excel | `_utils.py` 约 79 行 |
| R5 | 抽取层**完全没有 LLM 语义通道**,纯确定性规则,跨行业不可能穷举 | 整个 `_parsing.py` |
| R6 | **静默**:7 个源全部 `parser_status=parsed`、`fidelity=full`、`errors=[]`,而 `tables/fields/permissions` 全 0,`coverage_gaps=0` | `_parser_receipt` 调用链 |

**R6 必须最先修**:不先让失败可见,就无法验证 R1–R5 的修复是否真的生效。

---

## 1. 目标与非目标

### 目标

1. 同一份资料内容,无论以 Markdown / CSV / Excel / Word / HTML / SQL / OpenAPI 哪种格式提供,实体与字段抽取结果**一致**。
2. 结构化产出为空时**必须可见**(coverage gap + 扫描结果 + 命令中心),不得再出现"full fidelity / 0 输出 / 0 缺口"。
3. 确定性抽取覆盖不到的文档,由 LLM 产出**带原文引用的候选**,经工程侧校验后分级晋级,绝不直接当事实。
4. A(benchmark_mall)与 F(mes_f)的 IR entities 从 **0 变正**;C/D/E 不回退。

### 非目标(明确禁止)

- ❌ 不为某个项目/行业加特判分支(不得出现 `mes_f`、`benchmark_mall`、MES、商城等字样)。
- ❌ 不靠继续堆砌文件名正则规则解决——分类可以改进,但**不得再让分类成为抽取的开关**。
- ❌ 不让 LLM 直接产出"事实":LLM 输出一律 `CANDIDATE`,且必须能在原文定位。
- ❌ 不新建第二套知识资产 / 第二套 Behavior IR;全部在 `enterprise_knowledge_center` 与 `behavior_ir` 原位增强。
- ❌ 不修改评测器、不触碰 `_private_eval/_evaluator_private/`、不把 GT 带入运行时。
- ❌ 不改动 5174/8088 端口。

---

## 2. 硬约束

1. `import ai_test_asset_center` 保持无副作用。
2. **Fail loud**:抽取为空必须产出显式 coverage gap,禁止 `except Exception` 吞错后继续(违反 AGENTS.md 第 1 条)。
3. 每次编辑 Python 文件后立即语法检查:
   `python -c "import ast; ast.parse(open('path','r',encoding='utf-8').read()); print('OK')"`
4. 关键配置地板不得移动:`discovery_engine.py` `timeout_seconds >= 300`、`max_tokens >= 32768`;`stage_reason_all_v2.py` `MAX_HYPOTHESES = 64`、`max_workers = 4`。
5. 行业中立:列头/关键词识别只允许"字段定义表"的**通用语义词**(field/column/type/description/constraint/required 及中英文常见变体),不得含任何行业业务词汇。
6. LLM 调用必须走既有 LLM 客户端与预算治理,不得新建旁路调用;prompt 中不得出现行业名、客户名、靶场名、GT 相关内容。
7. 新增第三方依赖须写入 `requirements.txt`;Excel 解析推荐 `openpyxl`,缺库时必须显式报缺口,不得静默跳过。

---

## 3. 实施分期

### Phase 0 — 让失败可见(最优先,独立可验收)

1. **拆分 fidelity 语义**。当前 `fidelity: full` 混淆了"文本解码成功"与"结构化抽取成功"。改为回执同时含:
   - `decode_fidelity`:full / partial / failed(原语义)
   - `extraction_outcome`:`EXTRACTED` / `EMPTY_NO_STRUCTURE_FOUND` / `EMPTY_PARSER_UNSUPPORTED_SHAPE` / `SKIPPED_NOT_APPLICABLE`
2. **零产出必须发缺口**。任何源在被判定为"应含结构化定义"(schema / dictionary / permission 类,或正文检出二维表)却产出 0 行时,追加 coverage gap,`gap_type` 用固定词表:
   `structured_extraction_empty` / `unsupported_document_shape` / `format_decode_unsupported`
3. **资产级空间体检**。知识资产构建结束时,若 `data_tables + business_objects` 为 0 或 `data_fields` 为 0 或 `permission_matrix` 为 0,各发一条资产级 coverage gap(`entity_space_empty` / `field_space_empty` / `permission_space_empty`),并使其在 Behavior IR `coverage_gaps`、扫描结果与命令中心投影中可见。
4. **顺带修复审计红线缺陷**(同属静默失败类):
   - 移除 `discovery_runtime_planning.py` 中 `probe_status` 的硬编码常量(审计 B1),改为真实调用结果或显式"未调用"状态;
   - 移除该文件与 `experiment_executor.py` 中 3 处 `except Exception` 吞错(审计 B5),改为记录并向上暴露。

**Phase 0 验收**:对 mes_f 与 benchmark_mall 构建知识资产,必须出现 `entity_space_empty` 与 `field_space_empty` 缺口;C/D/E 不得出现这两条缺口。

### Phase 1 — 格式归一化层(含 Excel)

1. 新增统一的**文档结构视图**:任何输入 → `{plain_text, tables: [...], key_values: [...], sections: [...]}`。
   - `tables` 中每张表含 `headers`、`rows`、`source_locator`(章节标题 / sheet 名 / 表序号),供后续引用定位。
2. **表格提取器**至少覆盖:Markdown 管道表、CSV/TSV、HTML `<table>`、Excel sheet、Word 表格。
3. **新增 xlsx/xls 支持**(`openpyxl`),并加入受支持后缀集(`_utils.py` 约 79 行)。缺库时发 `format_decode_unsupported` 缺口,不得静默跳过。
4. 该层**不做语义判断**,只回答"这份文档里有哪些二维表和键值结构"。

### Phase 2 — 通用表格语义抽取(解除分类门控)

1. **新增通用"字段定义表"识别器**:对 Phase 1 产出的每一张表,用**列头语义**判断它是否字段定义表。
   - 识别信号:列头集合中同时出现"名称类"(field/column/name/字段/列名/属性)与"类型或说明类"(type/data type/类型/description/说明/备注/constraint/约束/required/必填)之一以上。
   - 命中则逐行产出字段记录,表名取最近的章节标题 / sheet 名 / 表标题。
2. **解除分类门控**(R2 关键修复):所有源都尝试跑表格抽取与实体抽取,`source_type` **只影响优先级与置信度**,不再决定是否抽取。
   - 确定性机器可读来源(openapi / sql DDL / json schema)置信度最高,保持现有行为不变;
   - 通用表格抽取次之;
   - 分类结果仅作为 `derivation` 标注与排序依据。
3. **分类改为多标签**(R1 修复):一个源可同时带多个 `source_types`(如 DB_SCHEMA.md 既是 collaboration_document 又是 database_schema)。保留原 `source_type` 字段为主标签以维持兼容,新增 `source_types` 列表。
4. Behavior IR 侧确认 `objects/entities/tables/business_objects/data_tables` 五个键的合并逻辑能吃到新产出(`behavior_ir.py` 约 2601 行),必要时补充 `data_fields → entity.fields` 的归并。

**Phase 2 验收**:mes_f 的 `DB_SCHEMA.md` 必须产出 products 等表及其字段;benchmark_mall 同理。且不得改动 C/D/E 的既有抽取结果。

### Phase 3 — LLM 语义抽取层(候选,不产事实)

1. **触发条件**:某源经 Phase 1–2 后仍未产出任何实体/字段/权限,且该源文本非空。逐源触发,不做全量重跑。
2. **输出契约**:LLM 只允许产出候选,每条必须含
   `{kind: entity|field|relation|state|actor, name, source_id, source_locator, verbatim_quote, confidence}`。
3. **工程侧强制校验**(LLM 不是事实裁判):
   - `verbatim_quote` 必须能在原文中**逐字定位**,定位失败即丢弃该候选并计入 `rejected_candidates`;
   - 名称必须出现在原文中,禁止 LLM 发明的同义改写;
   - 校验结果写入回执,拒绝率过高需可见。
4. **状态一律 `CANDIDATE`**,写入知识资产的独立集合(如 `semantic_candidates`),不直接混入 `data_tables`/`data_fields`。
5. 失败必须 fail-fast:LLM 不可用、超时、返回畸形 JSON → 显式记录并发缺口,不得静默当作"没有实体"。

### Phase 4 — 候选校验与晋级

1. 实现全景文档 §3.3 的状态机:
   `CANDIDATE → PENDING_VALIDATION → VALIDATED / CONFLICTED / STALE / REJECTED`
2. **晋级证据**(至少满足其一才可 VALIDATED):
   - 多源交叉一致:候选实体名在 API 路径 / 其他文档 / 状态机中独立出现;
   - 运行时只读探测确认:复用 `binding_runtime_probe.run_probes_for_ledger`(该模块目前从未被调用,正好在此启用),仅在合约已批准的非生产目标上做只读探测。
3. **进入 Behavior IR 的规则**:
   - `VALIDATED` 候选正常进入实体空间;
   - `CANDIDATE` / `PENDING_VALIDATION` 可进入但必须带低置信标记,且**不得单独支撑 formal finding**;
   - `CONFLICTED` 必须作为显式冲突可见,不得静默择一。

### Phase 5 — 来源约束的专家级业务语义理解

1. 规则抽取不再把 Markdown 表格整行当成业务规则。只保留包含规则语义的单元格，避免把邮箱、密码和账号示例带入规则文本或模型 prompt。
2. 每条显式规则生成 `qualibug.business-semantic-frame.v1`，保留来源定位、义务模态（必须/禁止/仅限）、正负极性、前置条件、受约束主体、受约束行为和原文锚点；所有字段必须能回到原始 statement，不能由模型补写。
3. Behavior IR 原位承接并校验 semantic frame。schema、模态、极性、来源锚点或原文定位不一致时 fail-fast，禁止静默降级成普通文本。
4. 既有 `agent_semantic_linker` 在有界批次内读取规则、接口 schema、实体/字段、角色/权限、状态机和关系事实，按“产品专家 + 测试专家”视角形成规则到接口的实验意图。
5. 每条规则必须得到 `LINKED`、`NO_EXECUTABLE_INTERFACE` 或 `AMBIGUOUS` 终态；接受的关系必须引用输入中真实存在的 rule/interface/supporting-fact ID。模型解释仅是 synthetic rationale，不能成为业务事实、Oracle 或 finding 证据。
6. Prompt 进入既有 `artifact_redactor` 边界，不携带 credential value 或 request-example value。缺失评估、虚构 ID、虚构证据、低置信度、重复、超预算和上下文截断都进入可观察回执，不得假装“已理解”。

---

## 4. 验收门禁(全部满足才算完成)

1. **语法与导入**:改动文件 `ast.parse` 通过;`import ai_test_asset_center` 无副作用。
2. **既有测试无新增失败**:与改动前基线逐条比对失败集合(方法见审计报告"审计方法留痕":`git worktree` 检出父提交跑同参数 pytest,导出失败清单比对)。必须提交两份清单与比对结果。
3. **跨项目抽取量化(核心硬门禁)**:提交下表实测前后对比,A 与 F 的 IR entities 必须从 0 变正,C/D/E 不得下降。

   | 项目 | data_tables | data_fields | permission_matrix | IR entities |
   |---|---|---|---|---|
   | benchmark_mall | 0 → **>0** | 0 → **>0** | — | 0 → **>0** |
   | mes_f | 0 → **>0** | 0 → **>0** | 0 → **>0** | 0 → **>0** |
   | contractflow_c | 27 → ≥27 | 27 → ≥27 | 4 → ≥4 | 22 → ≥22 |
   | ticketsla_d | 8 → ≥8 | 8 → ≥8 | 1 → ≥1 | 8 → ≥8 |
   | warehouse_e | 12 → ≥12 | 12 → ≥12 | 0 → ≥0 | 11 → ≥11 |

4. **格式无关性证明**:取同一份表结构内容,分别以 `.md` / `.csv` / `.xlsx` / `.docx` 四种格式提供,抽取出的表名与字段集合必须一致。提交该对照测试。
5. **可观测性证明**:构造一个"无任何结构化定义"的文档,验证产出 `structured_extraction_empty` 缺口且资产级空间缺口可见;并验证 `fidelity: full` 不再与零抽取共存。
6. **纪律审计**:无行业词汇 / 项目名硬编码(复用 `_project_f_anti_hardcoding_audit.py` 口径);LLM prompt 无行业与 GT 内容;无 GT 路径引用;端口未动。
7. **下游联动验证**:mes_f 重跑绑定门禁,通过率必须从 `0/60` 显著上升;空间坐标 entity/actor 维空值率必须从 `85/85` 显著下降(不要求全绑上,要求可量化改善并给出数字)。
8. **提交**:按 Phase 分次提交,每次干净、message 说明 Phase;不得带入运行产物垃圾。

---

## 5. 与既有审计缺陷的关系

审计报告中的 B1(伪造 receipt)与 B5(吞异常)属静默失败红线,**并入本 SPEC Phase 0** 一并修复。
B2/B3/B4/B6/B7/B8(接线质量缺陷)**留待本断点关闭后的下一轮**处理——因为在实体空间为空时,修 Binding 与坐标的边际收益极低。

Project F 盲测 3.1% 仍为封存口径。本 SPEC 的验收只产生工程能力证据,不产生任何新的发现能力宣称;真实能力等 Project G 盲测检验。

## 6. 建议实施顺序

Phase 0(可见性,含红线修复)→ 验收 5 → Phase 1(格式归一化 + Excel)→ Phase 2(通用表格抽取 + 解除门控)→ 验收 3/4 → Phase 3(LLM 候选)→ Phase 4(校验晋级)→ 验收 7。

每个 Phase 独立可验收、可回退,不得跨 Phase 混改。Phase 0–2 是"必须做完"的部分:仅这三期就应当让 A 与 F 的实体空间非空。Phase 3–4 是把能力从"支持常见格式"提升到"支持任意企业资料"的关键,但必须建立在前三期的可观测与确定性基础之上。
