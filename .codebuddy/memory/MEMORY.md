# QualiBug AI 项目长期记忆

## 项目概览
- 企业级 AI Bug 挖掘与证据链复现平台，定位全行业适配（禁止硬编码）
- 后端：Python 3.12+，原生 http.server，无 Web 框架
- 前端：React 19 + TypeScript 5 + Vite 8
- 当前版本 95.0.0（Phase106），后端 ~301 模块/8-10万行，前端 45 文件
- 核心架构：后端 display-ready 格式化 + 前端零加工纯渲染 + 复现引擎闭环
- Benchmark：20 行业 6000 Bug

## 关键架构决策
- display-ready 格式化在 `_build_command_center()` 出口层做，不污染 discovery_engine（SoC）
- 证据四层保留：RawRuntime → Semantic → BusinessEvidence → FinalReview
- 双层门控：Runtime Evidence Gate + Business Evidence Gate
- 复现引擎隔离设计，复用 ssrf_guard + enterprise_credential_manager
- 证据链多源数据驱动：后端从 HAR/DB/文档/日志多源填充真实数据，三视角(business/test/dev)预过滤链，6维度证据完备度可视化（2026-07-04）

## 企业资料存储位置（重要，2026-07-05 更正）
前端上传走 `ingest_enterprise_knowledge_documents`，写入**文件系统**：
- `platform_workspace/{project}/enterprise_knowledge_center/source_registry.json`（文档注册表）
- `platform_workspace/{project}/enterprise_knowledge_center/sources/`（原始文档副本）
- `platform_workspace/{project}/input/`（上传原始文件）
- `platform_workspace/{project}/defect_discovery/enterprise_business_knowledge_asset.json`（知识资产）
**不写** SQLite `knowledge_docs` 表（`save_knowledge_doc` 只有定义没有调用处）。

`_load_enterprise_docs` 必须**文件系统优先**（source_registry.json 优先），数据库用真实 tenant_id 作为补充。
之前"先读数据库再读 JSON"是错误的——数据库查不到上传的文档，导致误报"没上传资料"。
数据库 `knowledge_docs` 表目前只有 MJUN科技 的 2 条遗留记录（来源不明，可能是旧代码路径写入）。
所有文档加载路径严格按 project_id 隔离，绝不跨项目/跨客户读取。

## 配置守护值（不可触碰，见 AGENTS.md）
- discovery_engine.py: timeout_seconds ≥ 300, max_tokens ≥ 32768
- stage_reason_all_v2.py: MAX_HYPOTHESES=15, max_workers=4

## 可裁剪模块梳理（2026-07-04 完成）
已执行三档清理，累计删除 40 个文件：
- 第一档：7个死文件（.archived/.bak/桩代码/调试笔记/nul）
- 第二档：15个废弃模块+测试+demo（optimized/enhanced_discovery_engine、phase105 hub v1、4个废弃loop垫片）
- 第三档：2个死配置 + 14个phase103交付链 + 2处依赖清理(flask/werkzeug)
验证：核心模块import OK，删除模块零残留引用。
注意：phase105_frontend_product_shell.py 为git D状态（用户会话前已删），导致6个phase105测试collection error——非本次清理导致。

## 用户偏好
- 重视产品健康验证：配置不等于在线，需真实探活
- 强调全行业通用性，绝对禁止硬编码
- 重视"让企业领导买单"的 demo 体验和销售故事
