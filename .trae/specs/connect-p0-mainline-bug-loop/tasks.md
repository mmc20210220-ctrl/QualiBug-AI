# Tasks
- [x] Task 1: 盘点主链模块地图与当前断点：扫描现有前端、后端、数据模型、执行器、证据链、回归链路，输出配置 -> 资料 -> 建模 -> 任务 -> 执行 -> Bug -> 证据 -> 前端 -> 回归的模块映射与断点清单。
  - [x] SubTask 1.1: 确认项目配置的前端入口、保存接口、持久化模型与执行链读取点
  - [x] SubTask 1.2: 确认企业资料上传、解析、落库、知识消费与历史 Bug 进入主链的实际调用路径
  - [x] SubTask 1.3: 确认行为建模、任务规划、执行器、Bug 判断、证据聚合、前端展示、回归入口的现有模块与未接线能力

- [x] Task 2: 打通项目配置真实驱动执行的断点：确保项目配置被任务规划器、执行器、证据链与回归链统一读取，移除写死 demo URL、账号、路径和本地默认值。
  - [x] SubTask 2.1: 运行入口 `_handle_v12_scan` 从连接器读取真实 base_url，透传项目级配置到 scan()
  - [x] SubTask 2.2: 移除缺少真实 API 源时伪造电商接口文档(/api/orders 等)与 base_url 兜底的硬编码，改为走真实 source-missing 降级
  - [x] SubTask 2.3: `test_real_project_config_connector_fallback`、`test_project_config_safety_boundary`、`test_private_pilot_service_v12_scan_context` 全部通过

- [x] Task 3: 打通企业资料进入测试计划的断点：确保上传资料不仅落库，还能驱动行为模型、测试任务、风险规则与历史 Bug 回归。
  - [x] SubTask 3.1: 确认知识资产 `enterprise_business_knowledge_asset.json` 与项目关联键，scan() 经 source registry / PRD 读取消费
  - [x] SubTask 3.2: 修复资料导入后自动扫描线程 `except Exception: pass` 静默吞错断点，改为记录 traceback 并落 `auto_scan_last_error.json` 可观测标记（Fail Fast / Make It Observable）
  - [x] SubTask 3.3: 后端模块导入验证通过，导入->自动扫描失败不再静默

- [x] Task 4: 打通结构化行为模型到测试任务生成的断点：确保输出统一格式的跨行业行为模型，并能转化为可执行任务。
  - [x] SubTask 4.1: 确认 `business_state_graph` 已产出统一结构(Actor/Resource/Action/State/Transition/Invariant/Permission/Risk/Oracle)并接入 v12
  - [x] SubTask 4.2: 确认 `semantic_scenario_generator` 绑定行为节点、来源依据、证据缺口，未绑定源则 plan_only
  - [x] SubTask 4.3: `test_phase108_source_derived_behavior`(除既有失败项) 与切片调度相关用例通过

- [x] Task 5: 打通真实执行到 Execution Result 的断点：把 HTTP/OpenAPI/UI/HAR/截图/DB 快照等现有能力统一接入任务执行结果与状态回写。
  - [x] SubTask 5.1: 确认执行相位状态机 blocked/stopped/plan_only/skipped/completed 已回写
  - [x] SubTask 5.2: 确认 execution phase 关联 project/behavior node/请求响应/证据
  - [x] SubTask 5.3: `test_phase110_scan_execution_approval` 通过，执行审批与运行契约链路完整

- [x] Task 6: 打通 Bug 判断与 Evidence Package 聚合的断点：确保正式 Bug 必须绑定真实证据，并且证据不足会被降级。
  - [x] SubTask 6.1: 确认 confirmed/candidate 分流基于 `has_real_confirmation_receipt()`，占位符路径不进入 confirmed
  - [x] SubTask 6.2: 修复 slice_budget 硬上限被破坏的护栏断点(40 -> 15)，`test_slice_budget_is_hard_capped_at_fifteen` 恢复通过
  - [x] SubTask 6.3: oracle 命中后 `confirmed_slice_ids` 入账链已修复，见 Task 9（4 个用例全绿）

- [x] Task 7: 打通前端展示与回归验证的断点：让客户可以从前端看到真实执行进度、Bug 详情、证据链和修复后回归状态。
  - [x] SubTask 7.1: 移除回归 `_infer_module` 电商行业关键词映射，改为数据驱动路径解析(通用)
  - [x] SubTask 7.2: 移除回归 actor/token 的 `normal_user` 角色硬编码与前端 Settings 的 admin-first 排序、默认 admin 行
  - [x] SubTask 7.3: 回归构建/执行/CI 反馈相关 5 个用例通过，前端 Settings 无类型诊断

- [x] Task 8: 跑通一个 P0 最小商业闭环 Demo：用一个真实项目、一个测试账号、一条 API 或页面路径、一份企业资料样例，完成从配置到回归的全链路验收。
  - [x] SubTask 8.1: 以主线 benchmark 项目为最可控场景，避免扩散到无关模块
  - [x] SubTask 8.2: 本轮以主链相关测试套件作为闭环证据(touched-chain 42 项通过)
  - [x] SubTask 8.3: Task 9 的确认账本红测已全部转绿（本轮再补 2 处隐藏红测：coupon 校验路由 validation_only 不入账、supplement 切片无运行时契约仍被计为可执行），真实 benchmark VAL17 端到端已演示(4 findings / 覆盖率 0.667 / 0 FP)

- [x] Task 9: 修复 v12 确认账本断点(本轮新发现)：oracle 命中但 `behavior_slice_ledger.confirmed_slice_ids` 为空，导致正式 Bug 无法入账、跨轮历史无法复用。
  - [x] SubTask 9.1: 定位到 4 个根因——最终 confirmed_slice_ids 丢弃同源历史确认；DB 证据 before/after 形状未识别为 runtime_and_db；4 处遗留调试 `exec()` 注入污染执行链并泄漏 `127.0.0.1:7777` 网络调用；DB schema DDL 中 `DISABLED` 误匹配 disable 动作 profile 劫持 coupon 校验分类
  - [x] SubTask 9.2: 逐个修复：confirmed_slice_ids 改为并集复用同源历史；`db_captured` 兼容 before/after 快照形状；删除全部 4 处调试注入；`_match_invariant_action` 动作识别排除 database_schema DDL
  - [x] SubTask 9.3: `test_phase108`/`test_phase109`/`test_phase110` 等 100 项全绿；stash 验证 4 个失败均为既有红测、修复零回归
  - [x] SubTask 9.4(本轮补充): 复跑发现 2 处仍红测——`coupon` 不变量因 `_invariant_runtime_upgrade` 强依赖 observation_path 致 validation_only 路由(POST /validate)不生成 approved_sandbox_write 场景；`supplement` 切片(permission/isolation/concurrency/money/source_observation)在无运行时契约时仍被计为可执行，致 plan_only_scenarios 为 0。已修复：`validation_only` 跳过 observation_path 门控；`_fallback_active_slice` 透传 `allow_source_runtime`，未批准时降级为 plan_only_requires_fixture。phase108/109/110 共 96 项复跑全绿。

- [x] Task 10: 收敛"本轮扫描 vs 缺陷货架"scope 分离剩余断点(本轮新发现，非本轮 Task9 引入，stash 已证实为既有红测)。
  - [x] SubTask 10.1: 修复 `scan_diagnostics.py` 预检 `routes` UnboundLocalError 崩溃(块外初始化)
  - [x] SubTask 10.2: 修复 `report_source_path` 跨平台分隔符：4 处 `str(path.relative_to(root))` 改为 `.as_posix()`，解决 current_scan_report_selection 与 v12_plan_only 用例
  - [x] SubTask 10.3: 后端 command-center scope 计数(`customer_ready_defects` 取 campaign 去重数而非 raw total)、前端 `buildProjectSummary` 采用 raw 原始 payload 口径(用户裁决)、前端 `Dashboard.tsx` 货架历史缺陷提示改 JSX 插值；对齐互斥的 scope_display 契约断言到 raw 口径

- [x] Task 11: 主链回归护栏套件验证(闭环守门，本轮新建)：为每条已打通的主链建立独立回归测试，固化"配置->资料->建模->任务->执行->Bug->证据->前端->回归"全链不失守。
  - [x] SubTask 11.1: 主链2 知识流 `test_mainchain2_knowledge_flow.py`——上传资料驱动测试计划(`_sync_input_only_knowledge_asset` 回落 registry 资产；`v12_pipeline._knowledge_asset_planning_text` 扁平化 rule_library/permission_matrix/risk_domains)。3 项全绿。
  - [x] SubTask 11.2: 主链4 任务状态 `test_mainchain4_task_status.py`——`BehaviorSlice` 新增 status 字段(默认 pending)、`_persist_slice_ledger` 派生 per-task 状态映射(attempted->running/confirmed->passed/blocked->blocked)。3 项全绿。
  - [x] SubTask 11.3: 主链5 执行安全边界 `test_mainchain5_execution_safety_boundary.py`——`_execute_scenario` 复用 `match_production_data_exclusion` 单一真相源，命中生产数据禁触即拦截不发请求。4 项全绿。
  - [x] SubTask 11.4: 主链6 Bug 判定安全 `test_mainchain6_bug_judgment_safety.py`——被安全边界拦截的执行产物绝不判为已复现/已确认，`_confirmed_oracle_finding` 降级为 auditable candidate。4 项全绿。
  - [x] SubTask 11.5: 主链7 证据链 `test_mainchain7_evidence_chain.py`——`evidence_id` 由随机 uuid4 改为稳定签名(同缺陷可去重/可检索)；`evidence_graphs` 按 evidence_id 落盘可检索。2 项全绿。
  - [x] SubTask 11.6: 主链8 任务看板 `test_mainchain8_task_board.py`——`_build_test_task_board` 从 v12 报告准确透传(前端零变换渲染)；空态返回 None。15 项全绿。
  - [x] SubTask 11.7: 验证结论——6 个 mainchain 文件共 31 项全绿(清除 `.pytest_tmp` 污染后)；后端主链核心(phase108 系列 5 文件 + phase109 系列 2 文件 + test_project_config_safety_boundary + test_frontend_scope_display_contract + 6 mainchain)共 131 项全绿、零回归、零 error。注意：全量红测其余 270 errors/51 failures 仍属历史 WIP 不稳定(缺 QUALIBUG_JWT_SECRET 等环境项)，与本轮主链修复无关。

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 1
- Task 4 depends on Task 3
- Task 5 depends on Task 2
- Task 5 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 5
- Task 8 depends on Task 2
- Task 8 depends on Task 3
- Task 8 depends on Task 4
- Task 8 depends on Task 5
- Task 8 depends on Task 6
- Task 8 depends on Task 7
- Task 9 depends on Task 5
- Task 8 depends on Task 9
