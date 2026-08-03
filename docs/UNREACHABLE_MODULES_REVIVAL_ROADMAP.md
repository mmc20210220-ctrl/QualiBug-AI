# 未接入模块复活路线图（Revival Roadmap）

> 目标：把静态分析发现的"有代码但未为主链服务"的模块全部接入主链为产品价值服务。
> 原则：注册式接入优先（主链 15 处扩展点已就位）、SSOT 冲突合并不新建、证据严格性与交付门一致、
> 每批独立提交可回滚、接入不改变既有主链行为。

## 一、审计方法（已收敛为权威结果）

- 静态 import 图（修正相对导入 / `__init__` 包名 / 子模块 alias 解析）
- 正向依赖 BFS：从产品入口（`private_pilot_entrypoint` / `__main__` / `v12_pipeline` /
  `discovery_mainline` / `private_pilot_service` / `continuous_evaluation` / `aitestops.cli`）出发
- 动态导入模式扫描 + 字符串注册核实（`scan_post_hooks` 内置安装器、envelope 钩子等）
- 结论：**1124 个产品模块，931 可达，193 不可达**

## 二、处置状态总表（193）

| 处置 | 数量 | 状态 |
|---|---|---|
| 批 0：自标记废弃退役 | 8 | ✅ 已提交 `15072f35` |
| 批 1：投影/报告接入 scan 后钩子 | 6 | ✅ 已提交 `52516c83` |
| 批 2：坏导入修复 + 观察者/执行验证接入 | 13 接入 / 5 退役 | ✅ 已提交 `16482096` |
| 批 3：坏导入修复 + 规划工具接入 | 21 修复 / 2 接入 | ✅ 已提交 `0d73db04` |
| 核实为实际活着（动态安装） | 1 | `job_formal_planning_proof`（scan_post_hooks 内置） |
| 评测私有（AGENTS.md 契约禁止进运行时） | 10 | 保持评测路径 |
| AGENTS.md 禁止接入 | 2 | `private_pilot_db_audit_patch`（链外 findings）、`deep_experiment_planner`/`deep_experiment_protocol_adapter`（diagnostic-only） |
| 待接入（后续批次） | ~134 | 见下方路线图 |

### 批 0 退役清单（8）

`business_invariant_before_after`（空 stub）、`cross_endpoint_verifier`、`human_feedback_loop` +
`human_feedback_web`（连带）、`model_deployment_gate`、`model_evaluation_harness`、
`rag_probe_generator`、`rag_quality_gate` —— 均 docstring 自标记 DEPRECATED/ZOMBIE/Stub。

### 批 1 接入清单（6，scan_post_hooks 注册式）

| 模块 | 产出键 | 说明 |
|---|---|---|
| `validation_summary` | `validation_summary` | 执行保证汇总（缺失段保持可见缺失） |
| `execution_evidence_report` | `execution_evidence_report` | finding 载体证据支撑率 |
| `performance_baseline` | `performance_baseline` | 性能历史基线 + 回归告警 + 趋势 |
| `discovery_stability_loss_projection` | `discovery_funnel.surface_funnels.formal_stability` | 短窗可靠性损失投影 |
| `behavior_semantic_mapper` | 各 finding 载体（增强） | 业务影响/调查指引（增量，不改分类） |
| `bug_risk_scoring` | `bug_risk_report` + finding 风险分 | 风险评分聚合 |

### 评测私有 10 个（保持，不接入）

`benchmark_bug_factory`、`distributed_benchmark_runner`、`evaluation_run_identity`、
`evaluator_target_readiness`、`identity_benchmark_cli`、`million_dataset_card`、
`p3_seed_bug_benchmark`、`probe_policy_ab_evaluator`、`rag_ab_evaluator`、`scale_benchmark`
—— 读取 GT/基准种子或只服务评测计分，AGENTS.md 契约禁止进入产品运行时。

## 三、后续批次路线图

### 批 2：观察者/断言/风险族注册 ✅ 已完成

关键发现：`discovery_engine/_engine.py` 等子包存在 **46 处坏相对导入**（模块移入子包后 `from .X` 指向不存在的路径，运行时 ImportError 被 except 静默吞掉）——批量修正为绝对导入后，Phase78B 语义状态验证、覆盖报告、fixture 自动构造、假设验证、证据规范化、发现门禁等段全部复活，`business_invariant_evaluator`、`state_observer_registry`、`invariant_engine`、`coverage_matrix`、`fixture_auto_constructor`、`hypothesis_schema` 等 13 个模块经 discovery_engine / real_project_defect_discovery / semantic_scenario_generator 路径接入。另接入 `behavior_registry`（scan 后钩子）、`cross_observer_conservation_reconciler`（grounded_probe_executor 验证段）；退役 5 个能力已被主链覆盖的旧 Phase77 编排层模块。

### 批 4：执行类（~20 个，需先定义 executor 扩展点）

**核实结论（已扫描）：全部无坏导入，属真正未接线的执行工具。** 与主链能力对照：
- 重复（主链已有一等公民）：`multi_surface_adapter`（主链 surface 安装器体系）、`frontend_ui_tester`（formal_ui_surface/ui_browser）、`flow_orchestrator`（multi_step_protocol 已注册 process 流协议）、`executor_cleanup`（从 experiment_executor 抽取后主链不再引用）、`db_verifier`/`deep_verifier`/`runtime_verifier`（observer/oracle 链覆盖验证语义）、`execution_profiler`+`execution_time_profiler`（双份互斥）
- 独特能力（需 executor 扩展点）：`concurrent_probe_executor`（并行探针）、`experiment_portfolio`（冻结组合+配额）、`multi_layer_tester`（多层统一评测）
- 模板/声明：`_fixture_materializer_facade_template`（模板文件，核心是活的主链模块）
- 待评估：`mobile_app_detector`（Manifest 静态分析，无设备）、`capability_99_upgrades`、`cross_org_saga`、`replay_evidence_sandbox`、`observed_product_scan_worker`、`auto_mobile_setup`、`full_spectrum_bug_engine`、`deep_security_test_engine`、`execution_adapter`

**接入方式**：需在 `experiment_executor` 定义 surface / 并行 / 剖析 / 组合四个扩展点（架构级改造，单独设计批次）。

经 `register_observer` / `register_assertion_kind` / `register_risk_family` /
`register_family_protocol` 四件套接入（observer 证据键 → kind → family → protocol 互校验链）：

- 状态观察：`state_observer_registry`（规范状态快照）、`state_projection_engine`、`business_invariant_evaluator`（确定性不变量求值）
- 断言：`proof_obligation_compiler`、`invariant_engine`（6 类不变量）
- 证据合并：`observer_response_semantic_joiner`、`cross_observer_conservation_reconciler`
- 注册原语：`behavior_registry`、`experiment_observer_registry`
- 覆盖矩阵：`coverage_matrix`（生产者，patch 已消费——先核实 compute 输入契约）
- 已核实 no-op 的 `registered_observer_evidence_bridge` 走退役而非接入

### 批 3：规划类（~25 个，`discovery_runtime_planning` 消费）

- 假设链：`hypothesis_schema`（严格归一化）、`hypothesis_prioritizer`、`hypothesis_slice_bridge`、`prd_to_probe_adapter`
- 探索：`state_path_exploration`、`space_dimension_registry`、`supplementary_behavior_slices`、`cross_entity_chain_planning`、`idempotency_replay_planning`、`temporal_experiment_planning`、`actor_matrix_planning`、`violation_activation`、`rule_reconciliation`
- 变更/影响：`change_impact`、`semantic_diff`
- 资源/依赖：`enterprise_resource_dependency_resolver`、`binding_runtime_probe`、`project_summary_builder`、`target_profiler`、`risk_based_probe_planner`、`probe_roi_optimizer`
- 深度规划：`deep_experiment_planner`、`deep_experiment_protocol_adapter`（AGENTS.md 标注 diagnostic-only——接入需先确认不违反"heuristic deep plan 不得替代 compiler-blocked experiment"）

### 批 4：执行类（~29 个，需先定义 executor 扩展点）

**最大改造批次**：`flow_orchestrator`、`fixture_auto_constructor`、`shared_test_environment`、
`semantic_state_verifier`、`concurrent_probe_executor`、`multi_surface_adapter`、
`executor_cleanup`、`_fixture_materializer_facade_template`、`execution_profiler`/
`execution_time_profiler`（双份——合并）、`experiment_portfolio`、`db_verifier`、
`deep_verifier`、`runtime_verifier`、`mobile_app_detector`、`frontend_ui_tester`、
`defect_discovery._runner/_probes/_scenarios` 等。
先在 `experiment_executor` 定义 surface / 并行 / 剖析 / 组合四个扩展点，再逐个挂入。

### 批 5：学习/记忆/自进化（~17 个）

`adaptive_planning_history` / 自进化触发接口：`discovery_learner`、`enterprise_strategy_learning`、
`feedback_policy_update`、`policy_memory`、`bug_pattern_library`、`bug_knowledge_graph`、
`cross_industry_confirmed_learning`、`historical_bug_importer`、`training_data_builder`、
`model_dataset_exporter`、`regression_guard`、`golden_obligation_set`、`evolution_scheduler`、
`fix_co_pilot`、`local_patcher`、`product_incident`、`phase91_graph_evaluation`。

### 批 6：企业知识中心（~15 个）

`enterprise_knowledge_center` 子包（`business_world_model`、`classifiers`、`document_intelligence`、
`source_occurrence_ledger`、`enterprise_understanding.*`、`defect_discovery._model/_common`、
`industry_auto_inference`、`enterprise_test_knowledge`、`service_topology`、`temporal_saga_doc_intel`）。
知识中心当前是离链资产面——需新开主链钩子（如 scan 后钩子或 planning 钩子）接入。
`_chinese_business_comprehension_extractor_v1` 已动态加载（活着），保持。

### 批 7：连接器/运维/基础设施（~25 个）

- 连接器：`feishu_connector_sync`、`feishu_connector_capability_sync`、`feishu_lifecycle_recovery_runtime`、
  `connector_tenant_acceptance`、`external_signal_adapter`、`external_integration`、`multi_service_discovery`
- 运维/外循环：`loop_watchdog`（Loop 0 监测，接入 continuous 外循环）、`sweep_loop`、`log_analyzer`、
  `observed_product_scan_worker`、`historical_authorization_rerun_consumer/_plan`
- 基础设施：`unified_http_transport`（强制唯一 HTTP 入口）、`safe_cache`、`safe_retry`、
  `_shared_utils`（统一工具源）、`probe_utils`、`optimizations`、`module_loader`、
  `main_chain_contract`、`project_context_artifact`、`enterprise_access_policy`
- 顺序敏感桥（合并为单一组合点）：`formal_contract_scan_context_bridge`、
  `stability_scan_context_bridge`、`scan_stability_contract_overlay`、`enterprise_pilot_runtime_with_chain`

## 四、每批执行标准（strangler 门禁）

1. **接线核实**：静态 BFS 不可达 ≠ 无引用——先查字符串注册/动态导入（如 `job_formal_planning_proof` 教训）
2. **SSOT 检查**：主链已有同等能力则合并/重定向，不建双权威
3. 逐个接入：注册/消费代码 → `ast.parse` 语法检查 → 模拟验证（如批 1 的 apply_scan_post_hooks 验证）→ 回归测试
4. 独立提交，可回滚；每批完成后更新本路线图状态表

## 五、横切原则

- 证据严格性：`business_evidence_enricher`、`evidence_normalizer`、`external_signal_adapter`
  接入时保持与 `discovery_finding_gate` 同级的 receipt 强制校验，防止旁路泄漏
- 导入零副作用：`import ai_test_asset_center` 不得触发注册；接入统一走显式安装点
  （`scan_post_hooks` 内置安装器 / entrypoint install_runtime_components / surface 安装器）
- 评测私有模块永不进入产品运行时（AGENTS.md 契约）
