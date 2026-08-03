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
| 批 4：执行类重复工具退役 | 14 退役 | ✅ 已提交 `008ded26`+`b568585c` |
| 批 5：学习/记忆孤岛退役 | 17 退役 / 5 确认已接入 | ✅ 已提交 `2eb578fb` |
| 核实为实际活着（动态安装/子进程） | 3 | `job_formal_planning_proof`、`observed_product_scan_worker`（子进程）、`_chinese_business_comprehension_extractor_v1` |
| 评测私有（AGENTS.md 契约禁止进运行时） | 10 | 保持评测路径 |
| AGENTS.md 禁止接入 | 2 | `private_pilot_db_audit_patch`（链外 findings）、`deep_experiment_planner`/`deep_experiment_protocol_adapter`（diagnostic-only） |
| 批 7：facade/工具/桥退役 | 14 退役 / 8 测试保护保留 | ✅ 已提交 `0e6e49a4` |
| **不可达剩余** | **108**（193→108，-44%） | 知识中心 11 + 测试保护 8 + 散点 ~89，见路线图 |
| 批 8：stability overlay 闭环 + 剩余 108 处置 | 2 接入 / 40 退役 / 66 保留 | ✅ 已提交 `174277ff`+`0ec57fd9`，见下方章节 |

### 批 8：stability overlay 闭环 + 剩余 108 处置（✅ 已完成）

**1. stability overlay 闭环接入（`174277ff`）** —— 修正 BFS（alias 相对导入解析）后确认：
UI/event/performance 三个 surface 均有「build_discovery_plan 绑定 contextvar + IR 构建前 overlay +
receipt 写入 job_ir」双层接线，唯独 stability 的 `scan_stability_contract_overlay` 从未被调用——
`discovery_stability_loss_projection`（批 1 接入的 hook）消费的 `scan_stability_contract_overlay_receipt`
恒为空。同构补齐：
- `discovery_runtime_semantic_binding.build_discovery_plan`：绑定/重置 stability contextvar
- `build_behavior_ir_with_semantic_operation_bindings`：`overlay_scan_stability_contracts` 进 IR 构建链，
  receipt 写入 `job_ir["scan_stability_contract_overlay_receipt"]`
- `discovery_runtime_quality_projection.project_discovery_quality`：`formal_stability` loss funnel
  从 scan 后钩子提升为主投影一等公民（与 ui/event/performance 同构）
- 连带清理：删除 10 个引用批 4-7 已退役模块的残留测试
  （`multi_surface_adapter`、`feishu_connector_capability_sync`、`feishu_lifecycle_recovery_runtime`、
  `historical_behavior_slices`、`scenario_execution_probe_guard`）

**2. 剩余 108 处置（`0ec57fd9`，修正 BFS 后实为 106）** —— 修正 BFS 全树扫描
（含 `from . import x` alias 解析），与旧快照逐项 diff 收敛一致（差 2 = 本次接入的两个）。
闭包分析（引用方必须在退役集内）+ 全树 AST/字符串/测试引用三重扫描后：

- **退役 40 个散点死代码**（无 import、无 importlib/子进程、无 tools/ CLI、无测试）：
  `_db_auth`、`auto_mobile_setup`、`binding_runtime_probe`、`broad_hypothesis_generator`、
  `bug_validation_queue`、`business_adversarial_validator`、`business_finding_registry`、
  `business_finding_schema_validator`、`change_impact`、`change_impact_cli`、`ci_release_gate`、
  `concurrent_probe_executor`、`deep_bug_mining`、`enterprise_resource_dependency_resolver`、
  `experiment_portfolio`、`finding_deduplicator`、`frontend_task_journey_registry`、
  `hypothesis_prioritizer`、`independent_evidence_verifier`、`industry_auto_inference`、
  `mobile_app_detector`、`multi_layer_tester`、`phase104_ci_quality_gate`、
  `phase104_frontend_integration_workspace`、`phase105_frontend_preview_release_package`、
  `phase105_frontend_release_smoke_demo`、`prd_to_probe_adapter`、`private_pilot_acceptance_smoke`、
  `private_pilot_db_audit_patch`（链外 findings，AGENTS.md 禁止）、`probe_roi_optimizer`、
  `report_exporter`、`rule_reconciliation`、`semantic_diff`、`service_topology`、
  `shared_test_environment`、`systematic_probe_engine`、`target_profiler`、
  `temporal_saga_doc_intel`、`ui_design_oracle_manifest`、`ui_ux_bug_detector`
- **恢复 3 个**（初判退役、核实有真实消费者）：`confirmed_bug_gate`（tools/
  `render_confirmed_bug_evidence_report.py` CLI + 测试，docstring 明言为 CLI/CI 复用设计）、
  `experiment_contract`（tools/`build_breakthrough_audit_pack.py` 审计包工具引用其 SCHEMA_VERSION）、
  `invariant_operation_binder`（AGENTS.md 标注 diagnostic-only + 专属测试）
- **保留 66 个**（不再退役，记录为待接线/保护/评测）：
  - 知识中心 18：`enterprise_knowledge_center.*` 8（含 3 个 importlib 动态加载豁免：
    `_chinese_business_comprehension_extractor_v1`、`builder_legacy_v1`、`integration_legacy_v1`）
    + `classifiers`、`document_intelligence`、`semantic_analysis`、`defect_discovery` 主包 + 7 子模块
  - 测试保护 18：`connector_tenant_acceptance`、`enterprise_pilot_runtime_with_chain`、
    `external_signal_adapter`、`loop_watchdog`、`main_chain_contract`、`stability_scan_context_bridge`、
    `sweep_loop`、`historical_authorization_inventory/_rerun_consumer/_rerun_plan`、
    `actor_matrix_planning`、`cross_entity_chain_planning`、`idempotency_replay_planning`、
    `hypothesis_slice_bridge`、`private_pilot_doctor`、`real_project_discovery_with_chain`、
    `violation_activation`、`invariant_operation_binder`（各有专属或集成测试）
  - 已接入但静态不可见 7（scan_post_hooks 字符串安装器）：`behavior_registry`、`bug_risk_scoring`、
    `execution_evidence_report`、`job_formal_planning_proof`、`validation_summary`、
    `performance_baseline`、`behavior_semantic_mapper`
  - 评测/AGENTS 约束：评测私有 10 + `adaptive_probe_optimizer` +
    `deep_experiment_planner`/`deep_experiment_protocol_adapter`（diagnostic-only）+
    `benchmark_runtime_cleanup_assessment`/`benchmark_target_cleanliness`（benchmark_evaluator 引用）
  - 豁免 4：`observed_product_scan_worker`（子进程）、`architecture_inventory`（tools/ 运行时 trace）、
    `_fixture_materializer_facade_template`（模板文件）、`replay_benchmark_runner`（评测基础设施）
  - 依赖保留模块 3：`evidence_bundle_normalizer`（← `real_project_discovery_with_chain`）、
    `state_path_exploration`、`temporal_experiment_planning`（← `deep_experiment_planner`）

**剩余不可达：66**（108→66，-39%）——全部为有消费者/有测试/评测/动态加载模块，无纯死代码残留。

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

### 批 4：执行类 ✅ 已完成（14 退役 / 5 保留待扩展点）

**核实结论：全部无坏导入。** 与主链能力对照：
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

### 批 5：学习/记忆/自进化 ✅ 已完成（17 退役 / 5 确认经批 3 修复接入）

### 批 6：企业知识中心子模块（部分完成，剩余待管线接线）

**核实结论（权威）**：614 个相对导入全部正确（零坏导入）；`enterprise_knowledge_center` 包可达且被主链引用；**3 个子模块经 importlib 动态加载实际活着**（`_chinese_business_comprehension_extractor_v1`、`builder_legacy_v1`、`integration_legacy_v1`——豁免）。已退役 `scenario_execution_probe_guard`（自声明 no longer rewrites + 零引用）。剩余 **11 个未接线组件**（`source_occurrence_ledger`/`source_occurrence_ingestion`/`business_world_model`/`classifiers`/`document_intelligence`/`industry_auto_inference`/`service_topology`/`temporal_saga_doc_intel`/`closure`/`document_structure_gate`/`openapi_path_parameter_projection`）——属"活包内未接线组件"，**接入方式**：在知识中心管线的 ingestion/understanding 调用点接线（需先梳理 `composition.py` 等组合点调用图），单独批次设计。

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
